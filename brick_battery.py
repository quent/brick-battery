#!/usr/bin/env python3
"""
The brick-battery module is the entry point to start
the BrickBatteryCharger with Aircon and SolarInfo instances.

It runs a main loop to poll solar generation, energy consumption
and air conditioners settings and sensors.
Air con settings are adjusted to make the household consumption
fit within a pre-set import range.
"""

import asyncio
import datetime
import logging
import traceback
from collections import deque
from functools import reduce
from math import isnan
from ruamel.yaml import YAML

from battery_api import BrickBatteryHTTPServer
from daikin_api import Aircon
from solaredge_modbus_tcp import SolarInfo
from csv_logger import CSVLogger
from utils import datetime_now, empty_if_nan

LOGGER = logging.getLogger('brick_battery')

def main():
    """
    Initialise SolarInfo, Aircon and CSVLogger instances with all settings for
    target grid import (or export) range, read frequency, aircon set frequency,
    parameters logged to CSV, sleep mode settings and thresholds.
    Eventually, all this should be read a config file and even be settable via a
    web API.
    """
    logging.basicConfig(level=logging.INFO)
    #logging.getLogger('daikin_api').setLevel(logging.DEBUG)
    #logging.getLogger('solaredge_api').setLevel(logging.DEBUG)
    logging.getLogger('solaredge_modbus_tcp').setLevel(logging.DEBUG)

    config_file = 'config.yaml'
    yaml = YAML()
    with open(config_file, 'r') as stream:
        config = yaml.load(stream)

    # Ensure A/C sleep mode settings are strings
    for key, value in config['sleep_mode_settings'].items():
        config['sleep_mode_settings'][key] = str(value)

    aircons = [Aircon(ac['number'], ac['url']) for ac in config['aircons']]

    csv_logger = None
    if config['csv_file']:
        csv_logger = CSVLogger(config['csv_file'], config['csv_headers'])
    recent_values = None
    if config['recent_values'] and config['recent_values']['max_size']:
        recent_values = {'headers': None,
                         'values': deque(maxlen=config['recent_values']['max_size'])}

    bbc = BrickBatteryCharger(config,
                              aircons,
                              SolarInfo(config['inverter_host']),
                              server=BrickBatteryHTTPServer(config['listen']['interface'],
                                                            config['listen']['port'],
                                                            config_file),
                              csv=csv_logger,
                              recent_values=recent_values)
    bbc.charge()

class BrickBatteryCharger:
    """
    The BrickBatteryCharger holds the logic to adjust the aircons
    settings. The current implementation uses 2 aircon instances
    but could be made more generic to run any number of them.
    It changes settings only every `set_interval` (~1min) while
    it reads data every `read_interval` (~3sec) because it can take
    some time for aircon units to adapt their power regime to the
    new settings.

    Because the Ururu Sarara aircon fan settings cannot be accessed by
    the wifi module (thanks Daikin), this implementation requires that
    the aircons are set to max fan speed using the infrared remote so that
    they can provide their max capacity when PV is producing a lot, fan speed
    to Auto does not achieve the same output. If you're using this code
    with aircons that allow fan speed through the wifi API, the controlling
    logic could be greatly enhanced to adjust power output by using fan speed
    more than the temperature setting itself.
    """
    def __init__(self, config, aircons, solar, server, csv=None, recent_values=None):
        """
        Args:
            config a dictionary containing settings from the yaml config file
            aircons a list of Aircon instances
            solar a SolarInfo instance
            server a BrickBatteryHTTPServer instance to expose controls and
                   status through a web API
            csv an optional CSVLogger instance to write analytics information to,
                defaults to None if no logging is required
            recent_values an optional container of time-series accessed
                through the web API for plotting
        """
        self.config = config
        self.ac = aircons
        self.ac_consumption = float('NaN')
        self.solar = solar
        self.csv = csv
        self.server = server
        self.server.register_controller(self)
        self.last_updated = None
        self.last_set = None
        self.last_check_set_time = None
        self.csv_last_save = datetime_now()
        self.is_sleep_mode = False
        self.recent_values = recent_values

    def charge(self):
        """
        Set up the event loop for the async API polls, do a first poll,
        then run the main loop (using the event loop...)
        """
        loop = asyncio.get_event_loop()
        if not self.config['operation']:
            LOGGER.warning('Operation mode off: not sending any commands to aircons')
        if self.csv:
            LOGGER.info('Logging runtime analytics to %s', self.csv.file.name)
        [(_, pv_generation), *_] = loop.run_until_complete(asyncio.gather(
            self.solar.check_se_load(),
            *[unit.get_basic_info() for unit in self.ac],
            *self.get_load_ac_status_requests()))
        now = datetime_now()
        self.last_updated = now
        if pv_generation <= self.config['sleep_threshold']:
            LOGGER.info('Inverter off, started in sleep mode')
            self.is_sleep_mode = True
        else:
            # Schedule first set in 3 read steps
            self.last_set = now - datetime.timedelta(
                seconds=self.config['set_interval'] - 3 * self.config['read_interval'])
        for unit in self.ac:
            LOGGER.info(unit)
        loop.run_until_complete(self.server.start())
        loop.run_until_complete(self.main_loop())

    async def main_loop(self):
        """
        Run forever the poll SolarInfo and Aircon to see what need to be done.
        The wait is done in parallel so that if the read (and possibly set step)
        took 2 seconds and we want to run every 3 seconds, the wait will only block
        for another extra second.
        """
        while True:
            try:
                await asyncio.gather(
                    self.read_set_step(),
                    asyncio.sleep(self.config['read_interval']))
            except Exception as ex:
                LOGGER.error('Something went wrong, skip this run loop call, cause: %s', ex)
                LOGGER.error(traceback.format_exc())

    def get_load_ac_status_requests(self):
        """
        Just return a list of futures to poll each aircon info and be later
        awaited
        """
        requests = []

        # First see when we last verified that the aircon modules had their time checked last.
        # Add a check and set time if it was too long ago.
        now = datetime_now()
        if self.config['ac_check_set_time_interval'] and \
                (not self.last_check_set_time \
                or (now > self.last_check_set_time +
                datetime.timedelta(seconds=self.config['ac_check_set_time_interval']))):
            self.last_check_set_time = now
            for unit in self.ac:
                requests.append(unit.check_set_time())

        # Now add sensors and controls
        for unit in self.ac:
            requests.append(unit.get_sensor_info())
            requests.append(unit.get_control_info())
            if not unit.name_set:
                requests.append(unit.get_basic_info())
        return requests

    def estimate_ac_consumption(self):
        """
        Calculate the combined estimated consumption from all aircons.
        `Aircon.get_sensor_info()` must have been called first.
        """
        consumptions = list(filter(lambda x: not isnan(x),
                                   map(lambda unit: unit.get_consumption(),
                                       self.ac)))
        if not consumptions:
            self.ac_consumption = float('nan')
        else:
            self.ac_consumption = reduce(lambda x, y: x + y, consumptions)

    async def set_ac_controls(self, target):
        """
        The interesting part: adaptive controller to play
        with the aircon buttons and hope that we land as close
        as possible within the target consumption range by the
        next set iteration.
        Control parameters are:
        - set_interval duration between each AC set operation,
          for how reactive we want to be, but the aircons can
          take some time to adapt their consumption too
        - step_size for how many temp buttons we want to press
          based on how far we are from the target
        - humidifier_consumption used to estimate its impact to
          get closer to the target
        """
        setting_change = False
        # Some sanity checks first
        for unit in self.ac:
            if unit.controls.get('pow', '-') != '1':
                LOGGER.warning('Aircon in %s is turned off or irresponsive :(', unit.name)
            if unit.controls.get('mode', '-') != '4' and unit.controls.get('pow', '-') == '1':
                LOGGER.error('Aircon in %s is not in heating mode, better stop here', unit.name)
                return
        setting_change = self.increase_temp_for_target(target) if target > 0 \
                         else self.decrease_temp_for_target(target)
        if not setting_change:
            return
        if not self.config['operation']:
            LOGGER.warning('Operation mode off: no set action sent')
            return
        asyncio.gather(*[unit.set_control_info() for unit in self.ac])

    def increase_temp_for_target(self, target):
        """
        Modify settings of AC to increase their consumption.
        It only modifies the settings of the ac controls but does not
        send the actual command to the units.

        Settings are modified in sequence to adjust estimated consumption
        and the consumption target is updated after each iteration until
        that target is reached, or cannot be reached because consumption
        is already at a maximum:
        - First increase temperature evenly across all active aircons
        - If all aircons at maximum temperature, turn humidification on

        Return True if settings were actually modified in which case
        the caller may send the commands to aircons.
        """
        setting_change = False
        active_units = [unit for unit in self.ac if
                        'stemp' in unit.controls and 'shum' in unit.controls and
                        'htemp' in unit.sensors and unit.controls.get('pow', '-') == '1']
        stemp = [float(unit.controls['stemp']) for unit in active_units]
        htemp = [float(unit.sensors['htemp']) for unit in active_units]
        shum = [int(unit.controls['shum']) for unit in active_units]
        max_htemp = int(self.config['max_htemp'])
        while target > 0:
            # Find the A/C that needs the most boost to get to max house temperature
            _, min_index = min((st + ht, index)
                               for (index, [ht, st]) in enumerate(zip(htemp, stemp)))
            # Note: stemp, while being a float, only gets int and half-int values through
            # additions and substractions: equals-comparison and modulus are safe
            if stemp[min_index] >= 30:
                # Still check if another A/C is not set to max yet
                min_st, min_st_index = min((st, index)
                                           for (index, st) in enumerate(stemp))
                if min_st >= 30:
                    LOGGER.info('All aircons set to max already')
                    break
                else:
                    min_index = min_st_index
            # If temperature set to a half degree, first try to set it back to integer
            step = 1 if stemp[min_index] % 1 == 0 else 0.5
            stemp[min_index] += step
            target -= step * active_units[min_index].consumption_per_degree
            setting_change = True
            LOGGER.info('Increasing temperature in %s to %d',
                        active_units[min_index].name, stemp[min_index])

        for index, unit in enumerate(active_units):
            if htemp[index] > max_htemp:
                # Now set stemp down from max_temp by every degree htemp is above max_htemp
                # For instance, If max_htemp is 27 and htemp 29, set stemp to 25
                new_stemp = 2 * max_htemp - htemp[index]
                target += abs(stemp[index] - new_stemp) * unit.consumption_per_degree
                stemp[index] = new_stemp
                setting_change = True
                LOGGER.info('Getting a bit warm in %s: %d, reviewing set temperature to %d',
                            unit.name, htemp[index], stemp[index])

        max_shum = int(self.config['max_shum'])
        for index, unit in enumerate(active_units):
            if target <= 0:
                break
            # Increasing temperature not sufficient, time to turn on humidifier
            if shum[index] != max_shum and self.config['control_humidity']:
                shum[index] = max_shum
                setting_change = True
                target -= unit.humidifier_consumption
                LOGGER.info('Setting humidification to %d%%RH in %s', max_shum, unit.name)

        if setting_change:
            for index, unit in enumerate(active_units):
                unit.controls['stemp'] = str(stemp[index])
                unit.controls['shum'] = str(shum[index])
        return setting_change

    def decrease_temp_for_target(self, target):
        """
        Modify settings of AC to decrease their consumption.
        It only modifies the settings of the ac controls but does not
        send the actual command to the units.

        Settings are modified in sequence to adjust estimated consumption
        and the consumption target is updated after each iteration until
        that target is reached, or cannot be reached because consumption
        is already at a minimum:
        - First decrease temperature evenly across all active aircons
        - If all aircons at minimum temperature, turn humidification off

        Return True if settings were actually modified in which case
        the caller may send the commands to aircons.
        """
        setting_change = False
        active_units = [unit for unit in self.ac if
                        'stemp' in unit.controls and 'shum' in unit.controls and
                        'htemp' in unit.sensors and unit.controls.get('pow', '-') == '1']
        stemp = [float(unit.controls['stemp']) for unit in active_units]
        htemp = [float(unit.sensors['htemp']) for unit in active_units]
        shum = [int(unit.controls['shum']) for unit in active_units]
        # Aircon unit shuts at set temp = room temp - 4º but then takes up
        # to 2 minutes to start again, setting at room temp - 3.5º keeps
        # the aircon consuming just a little bit but allows for a much faster restart
        while target < 0:
            max_diff, max_index = max((st - ht + 3.5, index)
                                      for (index, [st, ht]) in enumerate(zip(stemp, htemp)))
            # Note: stemp, while being a float, only gets int and half-int values through
            # additions and substractions: equals-comparison and modulus are safe
            if max_diff <= 0:
                LOGGER.info('All aircons set to min already, can\'t do anything')
                break
            # Decrease by half a degree if temperature if the current set temperature
            # is a half-degree value or if we're just close to the 3.5º difference mark
            step = 1 if stemp[max_index] % 1 == 0 and max_diff >= 1 else 0.5
            stemp[max_index] -= step
            target += step * active_units[max_index].consumption_per_degree
            setting_change = True
            LOGGER.info('Decreasing temperature in %s to %d',
                        active_units[max_index].name, stemp[max_index])

        for index, unit in enumerate(active_units):
            # Temperature low enough, time to turn off humidifier
            # It might take some time for this action to affect consumption
            # so don't count it against the target
            if stemp[index] <= 26 and shum[index] != 0 and self.config['control_humidity']:
                shum[index] = 0
                setting_change = True
                LOGGER.info('Turning humidification off in %s', unit.name)

        if setting_change:
            for index, unit in enumerate(active_units):
                unit.controls['stemp'] = str(stemp[index])
                unit.controls['shum'] = str(shum[index])
        return setting_change

    def calculate_target(self, load, consumption):
        """
        Target here is difference consumption wanted from AC
        Negative target to decrease consumption, positive to increase it
        """
        if isnan(load) or isnan(consumption):
            return float('NaN')
        if self.config['min_load'] < load < self.config['max_load']:
            # Don't bother touching a thing if importing within
            # acceptable load range
            return 0
        avg_load_target = (self.config['max_load'] + self.config['min_load']) / 2
        if load > self.config['max_load'] and consumption > 0:
            # We need to reduce AC consumption
            return -min(load - avg_load_target, consumption)
        max_total_ac_consumption = sum([unit.max_consumption for unit in self.ac])
        if load < self.config['min_load'] and consumption < max_total_ac_consumption:
            # We need to increase AC consumption
            return min(avg_load_target - load, max_total_ac_consumption)
        # We cannot help because either the AC is off and we're over consuming
        # or we're exporting but we've already turned the AC to max capacity
        return 0

    async def read_set_step(self):
        """
        Executed by the main loop to do:
        - Poll all Aircons and SolarInfo asynchronously
        - Manage if solar inverter just went to sleep or woke up
        - Calculate aircons consumption
        - Log analytics to a new CSV line
        - Save CSV file (only at save interval)
        - If not asleep, calculate target aircon consumption and
          set aircons controls for this target (only at set interval)
        """
        start_step_time_string = datetime_now().strftime("%d/%m/%Y %H:%M:%S")
        LOGGER.info(start_step_time_string)
        [(grid_import, pv_generation), *_] = await asyncio.gather(
            self.solar.check_se_load(),
            *self.get_load_ac_status_requests())
        now = datetime_now()
        self.last_updated = now
        if grid_import < 0:
            LOGGER.info('PV generating %.0fW Exporting %.0fW', pv_generation, -grid_import)
        else:
            LOGGER.info('PV generating %.0fW Importing %.0fW', pv_generation, grid_import)

        if pv_generation <= self.config['sleep_threshold'] and not self.is_sleep_mode:
            LOGGER.info('PV generation just stopped, entering sleep mode')
            self.is_sleep_mode = True
            if not self.config['operation']:
                LOGGER.warning('Operation mode off: no set action sent')
            else:
                for unit in self.ac:
                    unit.controls = self.config['sleep_mode_settings']
                asyncio.gather(*[unit.set_control_info() for unit in self.ac])
        elif pv_generation >= self.config['wakeup_threshold'] and self.is_sleep_mode:
            LOGGER.info('PV generation just starting, leaving sleep mode')
            self.is_sleep_mode = False

        self.estimate_ac_consumption()
        LOGGER.info('Estimated combined A/C consumption %.0fW', self.ac_consumption)

        self.save_csv_line(now, pv_generation, grid_import)
        self.add_to_recent_values(now, pv_generation, grid_import, self.ac_consumption)

        if not self.is_sleep_mode:
            target = self.calculate_target(grid_import, self.ac_consumption)
            if self.last_set:
                next_set = (self.last_set
                            + datetime.timedelta(0, self.config['set_interval'])
                            - now).total_seconds()
            else:
                next_set = 0
            LOGGER.info('Target is %.0f (import in [%d, %d]) ',
                        target, self.config['min_load'], self.config['max_load'])
            if next_set > 0:
                LOGGER.info('next set in %.0f seconds \n', next_set)
            elif target != 0 and not isnan(target):
                LOGGER.info('setting now\n')
            else:
                LOGGER.info('setting anytime\n')

            if next_set <= 0 and target != 0 and not isnan(target):
                self.last_set = now
                LOGGER.info("Setting A/C controls\n")
                await self.set_ac_controls(target)

    def add_to_recent_values(self, record_time, pv_generation, grid_import, ac_consumption):
        """
        Add, if configured, a new timeseries entry for the given record time
        with pv_generation, grid_import and aircons consumption.
        """
        if self.recent_values is None:
            return
        if self.recent_values['headers'] is None:
            self.recent_values['headers'] = \
                ['time', 'pv_generation', 'grid_import', 'ac_consumption']
        self.recent_values['values'].append(
            [record_time, pv_generation, grid_import, ac_consumption])

    def save_csv_line(self, record_time, pv_generation, grid_import):
        """
        Save, if configured, a new line data for the given record time
        information about pv_generation, grid_import and aircons statuses.
        It will only commit to disk every csv_save_interval from the config.
        """
        if not self.csv:
            return
        # Make sure values match the order of csv_headers in config
        # NaN are encoded as empty strings (missing values)
        self.csv.write([record_time.replace(microsecond=0).isoformat(),
                        empty_if_nan(pv_generation),
                        empty_if_nan(grid_import),
                        empty_if_nan(self.ac_consumption),
                        self.ac[0].sensors.get('cmpfreq', ''),
                        self.ac[1].sensors.get('cmpfreq', ''),
                        self.ac[0].sensors.get('otemp', ''),
                        self.ac[0].sensors.get('htemp', ''),
                        self.ac[1].sensors.get('htemp', ''),
                        self.ac[0].controls.get('stemp', ''),
                        self.ac[1].controls.get('stemp', ''),
                        self.ac[0].controls.get('shum', ''),
                        self.ac[1].controls.get('shum', ''),
                        ])
        if (record_time - self.csv_last_save
                >= datetime.timedelta(0, self.config['csv_save_interval'])):
            self.csv_last_save = record_time
            self.csv.save()

if __name__ == '__main__':
    main()
