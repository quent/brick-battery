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
import math
import os
import sys
import traceback

from battery_api import BrickBatteryHTTPServer
from daikin_api import Aircon
from solaredge_api import SolarInfo
from csv_logger import CSVLogger

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
    logging.getLogger("urllib3").setLevel(logging.INFO)
    logging.getLogger("aiohttp").setLevel(logging.INFO)


    aircons = [Aircon(0, 'http://192.168.1.101'),
               Aircon(1, 'http://192.168.1.102')]

    current_power_flow_file = os.path.join(
        os.path.dirname(os.path.realpath(sys.argv[0])),
        'currentPowerFlow.curl')
    solar = SolarInfo(current_power_flow_file)

    sleep_mode_settings = {
        'pow': '1',
        'mode': '4',
        'stemp': '22',
        'shum': '0'
    }

    data_to_save = ['datetime',
                    'PV generation in W',
                    'Grid import in W',
                    'Estimated A/C consumption in W',
                    'Living Room compressor frequency in Hz',
                    'Bedrooms compressor frequency in Hz',
                    'Outdoor temperature in ºC',
                    'Living Room temperature in ºC',
                    'Bedrooms temperature in ºC',
                    'Target Living Room temperature in ºC',
                    'Target Bedrooms temperature in ºC',
                    'Target Living Room humidity in %RH',
                    'Target Bedrooms humidity in %RH',
                    ]
    csv = CSVLogger('energy_data.csv', data_to_save)

    # Use 'localhost' to only accept connections from this host
    server = BrickBatteryHTTPServer('0.0.0.0', 8080)

    # don't read less often than 5s otherwise SolarEdge drops connection
    bbc = BrickBatteryCharger(aircons,
                              solar,
                              read_interval=3,
                              set_interval=60,
                              min_load=0,
                              max_load=400,
                              server=server,
                              csv=csv,
                              sleep_mode_settings=sleep_mode_settings,
                              sleep_threshold=200,
                              wakeup_threshold=500,
                              operation=True)
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
    def __init__(self, aircons, solar, set_interval, read_interval,
                 min_load, max_load, server, csv=None, sleep_mode_settings={},
                 sleep_threshold=0, wakeup_threshold=200, operation=True):
        """
        Args:
            aircons a list of Aircon instances
            solar a SolarInfo instance
            set_interval the time in seconds between sending
                         new commands to the aircons
            read_interval the (minimum) time in seconds between aircon
                          and solar sensors polls
            min_load target minimum load in watts
            max_load target maximum load in watts. For example we want
                     the household to always import from the grid a value
                     within [0, 300] watts. A larger range gives more
                     tolerance and avoids changing settings continuously if
                     household comsumption or PV generation fluctuate often.
            server a BrickBatteryHTTPServer instance to expose controls and
                   status through a web API
            csv an optional CSVLogger instance to write analytics information to,
                defaults to None if no logging is required
            sleep_mode_settings a set of Aircon settings to send to each aircon
                                unit when PV generation is stopped in the evening
            sleep_threshold the PV generation in watts from the inverter at which
                            the brick loader stops controlling the aircons and
                            sleep_mode_settings are set
                            Must be lower than wakeup_threshold
            wakeup_threshold the PV generation in watts from the inverter at which
                             sleep_mode_settings are ignored and the brick loader
                             starts controlling the aircons
                             Must be greater than sleep_threshold
            operation set to False to test your system after changes without
                    interfering with the household operation.
        """
        now = datetime.datetime.now()
        self.ac = aircons
        self.ac_consumption = float('NaN')
        self.solar = solar
        self.csv = csv
        self.server = server
        self.server.register_controller(self)
        self.last_updated = None
        self.set_interval = set_interval
        self.read_interval = read_interval
        self.last_set = None
        self.csv_save_interval = 120
        self.csv_last_save = now
        self.operation = operation
        self.min_load = min_load
        self.max_load = max_load
        self.is_sleep_mode = False
        self.sleep_mode_settings = sleep_mode_settings
        self.wakeup_threshold = wakeup_threshold
        self.sleep_threshold = sleep_threshold

    def charge(self):
        """
        Set up the event loop for the async API polls, do a first poll,
        then run the main loop (using the event loop...)
        """
        loop = asyncio.get_event_loop()
        if not self.operation:
            LOGGER.warning('Operation mode off: not sending any commands to aircons')
        if self.csv:
            LOGGER.info('Logging runtime analytics to %s', self.csv.file.name)
        [(_, pv_generation), *_] = loop.run_until_complete(asyncio.gather(
            self.solar.check_se_load(),
            *[unit.get_basic_info() for unit in self.ac],
            *self.get_load_ac_status_requests()))
        now = datetime.datetime.now()
        self.last_updated = now
        if pv_generation <= self.sleep_threshold:
            LOGGER.info('Inverter off, started in sleep mode')
            self.is_sleep_mode = True
        else:
            # Schedule first set in 3 read steps
            self.last_set = now - datetime.timedelta(
                seconds=self.set_interval - 3 * self.read_interval)
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
                    asyncio.sleep(self.read_interval * (10 if self.is_sleep_mode else 1)))
            except Exception as ex:
                LOGGER.error('Something went wrong, skip this run loop call, cause: %s', ex)
                LOGGER.error(traceback.format_exc())

    def get_load_ac_status_requests(self):
        """
        Just return a list of futures to poll each aircon info and be later
        awaited
        """
        requests = []
        for unit in self.ac:
            requests.append(unit.get_sensor_info())
            requests.append(unit.get_control_info())
        return requests

    def estimate_ac_consumption(self):
        """
        Calculate the combined estimated consumption from all aircons.
        `Aircon.get_sensor_info()` must have been called first.
        """
        total = 0
        for unit in self.ac:
            consumption = unit.get_consumption()
            if not math.isnan(consumption):
                total += consumption
        self.ac_consumption = total

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
        # Watts per temperature +/- button press
        step_size = 200
        # Watts when humidifier is turned on
        humidifier_consumption = 200
        setting_change = False

        # Some sanity checks first
        for unit in self.ac:
            if unit.controls.get('pow', '-') != '1':
                LOGGER.warning('Aircon in %s is turned off or irresponsive :(', unit.name)
            if unit.controls.get('mode', '-') != '4' and unit.controls.get('pow', '-') == '1':
                LOGGER.error('Aircon in %s is not in heating mode, better stop here', unit.name)
                return
        # First rule of thumb for reactivity, action a temperature step per 200W difference
        steps = math.ceil(abs(target) / step_size)
        stemp0 = float(self.ac[0].controls['stemp'])
        stemp1 = float(self.ac[1].controls['stemp'])
        shum0 = int(self.ac[0].controls['shum'])
        shum1 = int(self.ac[1].controls['shum'])
        if target > 0:
            # Increase AC consumption. Sometimes when AC are turned off
            # at the compressor, it can take them a while to get going
            # and then they crank up like mad for another minute before
            # winding down to a steady rate
            temp_potential = (30 - stemp0 + 30 - stemp1)
            # How many times can we still press the temp+ buttons?
            if temp_potential < steps:
                # Increasing temperature won't be sufficient, time to turn on humidifier
                if shum0 == 0:
                    shum0 = 50
                    setting_change = True
                    target -= humidifier_consumption
                    LOGGER.info('Turning humidification on in %s', self.ac[0].name)
                if target > 0 and shum1 == 0:
                    shum1 = 50
                    setting_change = True
                    target -= humidifier_consumption
                    LOGGER.info('Turning humidification on in %s', self.ac[1].name)
            # Recalculate temp increase steps
            steps = math.ceil(abs(target) / step_size)
            for _ in range(steps):
                if min(stemp0, stemp1) == 30:
                    LOGGER.info('Both aircons set to max already, can\'t do anything')
                    break
                if stemp0 < stemp1:
                    stemp0 += 1
                    setting_change = True
                    LOGGER.info('Increasing temperature in %s to %d',
                                self.ac[0].name, stemp0)
                else:
                    stemp1 += 1
                    setting_change = True
                    LOGGER.info('Increasing temperature in %s to %d',
                                self.ac[1].name, stemp1)
        else:
            # Decrease AC consumption
            htemp0 = float(self.ac[0].sensors['htemp'])
            htemp1 = float(self.ac[1].sensors['htemp'])
            temp_potential = max(stemp0 + 4 - htemp0, 0) + max(stemp1 + 4 - htemp1, 0)
            # Temp potentiel is trickier on the way down because the aircon will shut
            # all heating is we set heating about 4ºC less than room temperature
            if temp_potential < steps:
                # Decreasing temperature won't be sufficient, time to turn off humidifier
                if shum0 > 0:
                    shum0 = 0
                    setting_change = True
                    target += humidifier_consumption
                    LOGGER.info('Turning humidification off in %s', self.ac[0].name)
                if target < 0 and shum1 > 0:
                    shum1 = 0
                    setting_change = True
                    target += humidifier_consumption
                    LOGGER.info('Turning humidification off in %s', self.ac[1].name)
            # Recalculate temp increase steps
            steps = math.ceil(abs(target) / step_size)
            for _ in range(steps):
                if max(stemp0 + 4 - htemp0, stemp1 + 4 - htemp1) <= 0:
                    LOGGER.info('Both aircons set to min temperature, can\'t do anything')
                    break
                if stemp0 + 4 - htemp0 > stemp1 + 4 - htemp1:
                    stemp0 -= 1
                    setting_change = True
                    LOGGER.info('Decreasing temperature in %s to %d',
                                self.ac[0].name, stemp0)
                else:
                    stemp1 -= 1
                    setting_change = True
                    LOGGER.info('Decreasing temperature in %s to %d',
                                self.ac[1].name, stemp1)
        if not setting_change:
            return
        # Set all changed temperature and humidity values now
        self.ac[0].controls['stemp'] = str(stemp0)
        self.ac[0].controls['shum'] = str(shum0)
        self.ac[1].controls['stemp'] = str(stemp1)
        self.ac[1].controls['shum'] = str(shum1)
        if not self.operation:
            LOGGER.warning('Operation mode off: no set action sent')
            return
        asyncio.gather(*[unit.set_control_info() for unit in self.ac])

    def calculate_target(self, load, consumption):
        """
        Target here is difference consumption wanted from AC
        Negative target to decrease consumption, positive to increase it
        """
        avg_load_target = (self.max_load + self.min_load) / 2
        if math.isnan(load) or math.isnan(consumption):
            return float('NaN')
        if self.min_load < load < self.max_load:
            # Don't bother touching a thing if importing within
            # acceptable load range
            return 0
        if load > self.max_load and consumption > 0:
            # We need to reduce AC consumption
            return -min(load - avg_load_target, consumption)
        if load < self.min_load and consumption < len(self.ac) * Aircon.MAX_AC_CONSUMPTION:
            # We need to increase AC consumption
            return min(avg_load_target - load, len(self.ac) * Aircon.MAX_AC_CONSUMPTION)
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
        start_step_time_string = datetime.datetime.now().strftime("%d/%m/%Y %H:%M:%S")
        LOGGER.info(start_step_time_string)
        [(grid_import, pv_generation), *_] = await asyncio.gather(
            self.solar.check_se_load(),
            *self.get_load_ac_status_requests())
        now = datetime.datetime.now()
        self.last_updated = now
        if grid_import < 0:
            LOGGER.info('PV generating %.0fW Exporting %.0fW', pv_generation, -grid_import)
        else:
            LOGGER.info('PV generating %.0fW Importing %.0fW', pv_generation, grid_import)

        if pv_generation <= self.sleep_threshold and not self.is_sleep_mode:
            LOGGER.info('PV generation just stopped, entering sleep mode')
            self.is_sleep_mode = True
            if not self.operation:
                LOGGER.warning('Operation mode off: no set action sent')
            else:
                for unit in self.ac:
                    unit.controls = self.sleep_mode_settings
                asyncio.gather(*[unit.set_control_info() for unit in self.ac])
        elif pv_generation >= self.wakeup_threshold and self.is_sleep_mode:
            LOGGER.info('PV generation just starting, leaving sleep mode')
            self.is_sleep_mode = False

        self.estimate_ac_consumption()
        LOGGER.info('Estimated combined A/C consumption %.0fW', self.ac_consumption)
        # Make sure values match the order of CSVLogger.data_to_save
        if self.csv:
            self.csv.write([start_step_time_string,
                            pv_generation,
                            grid_import,
                            self.ac_consumption,
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
            if (now - self.csv_last_save
                    >= datetime.timedelta(0, self.csv_save_interval)):
                self.csv_last_save = now
                self.csv.save()

        if not self.is_sleep_mode:
            target = self.calculate_target(grid_import, self.ac_consumption)
            if self.last_set:
                next_set = (self.last_set
                            + datetime.timedelta(0, self.set_interval)
                            - now).total_seconds()
            else:
                next_set = 0
            LOGGER.info('Target is %.0f (import in [%d, %d]) ',
                        target, self.min_load, self.max_load)
            if next_set > 0:
                LOGGER.info('next set in %.0f seconds \n', next_set)
            elif target != 0 and not math.isnan(target):
                LOGGER.info('setting now\n')
            else:
                LOGGER.info('setting anytime\n')
            if next_set <= 0 and target != 0 and not math.isnan(target):
                self.last_set = now
                LOGGER.info("Setting A/C controls\n")
                await self.set_ac_controls(target)

if __name__ == '__main__':
    main()
