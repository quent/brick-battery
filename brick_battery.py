#!/usr/bin/env python3
"""
The brick-battery module is the entry point to start
the BrickBatteryCharger with Aircon and SolarAPI instances.

It runs a main loop to poll solar generation, energy consumption
and air conditioners settings and sensors.
Air con settings are adjusted to make the household consumption
fit within a pre-set import range.
"""

from datetime import datetime
import logging
import math
import os
import sys
import time
import traceback

from daikin_api import Aircon
from solar_api import SolarAPI
from csv_logger import CSVLogger

LOGGER = logging.getLogger('brick_battery')

def main():
    logging.basicConfig(level=logging.INFO)
    logging.getLogger("urllib3").setLevel(logging.INFO)
    logging.getLogger("requests").setLevel(logging.INFO)

    ac = [Aircon(0, 'http://192.168.1.101'),
          Aircon(1, 'http://192.168.1.102')]

    current_power_flow_file = os.path.join(
        os.path.dirname(os.path.realpath(sys.argv[0])),
        'currentPowerFlow.curl')
    solar = SolarAPI(current_power_flow_file)

    sleep_mode_settings = {
        'pow': '1',
        'mode': '4',
        'stemp': '22.0',
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
    # don't read less often than 3s otherwise SolarEdge drops connection
    bbc = BrickBatteryCharger(ac,
                              solar,
                              read_interval=3,
                              set_interval=60,
                              min_load=200,
                              max_load=700,
                              csv=csv,
                              sleep_mode_settings=sleep_mode_settings,
                              wakeup_threshold=200,
                              dryrun_mode=False)
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
    more than than temperature settings.
    """
    def __init__(self, ac, solar, set_interval, read_interval,
                 min_load, max_load, csv=None, sleep_mode_settings={},
                 wakeup_threshold=200, dryrun_mode=False):
        """
        Args:
            ac a list of Aircon instances
            solar a SolarAPI instance
            csv a CSVLogger instance to write analytics information to, defaults to
                None if no logging is required
            set_interval the time in seconds between sending
                         new commands to the aircons
            read_interval the (minimum) time in seconds between aircon
                          and solar sensors polls
            min_load target minimum load in watts
            max_load target maximum load in watts. For example we want
                     the household to always import from the grid a value
                     within [0, 300] watts. A larger range gives more
                     tolerance and avoid changing settings continuously if
                     household comsumption or PV generation fluctuate often.
            sleep_mode_settings a set of Aircon settings to send to the aircon
                                units when PV generation is stopped in the evening.
            wakeup_threshold the minimum PV generation in watts from the inverter
                             before sleep_mode_settings are ignored and the brick
                             loader starts controlling the aircons
            dry_run set to true to test your system after changes without
                    interfering with the household operation.
        """
        self.ac = ac
        self.solar = solar
        self.csv = csv
        self.set_interval = set_interval
        self.read_interval = read_interval
        self.next_set = 10
        self.csv_save_interval = 120
        self.csv_next_save = self.csv_save_interval
        self.dryrun = dryrun_mode
        self.min_load = min_load
        self.max_load = max_load
        self.is_sleep_mode = False
        self.sleep_mode_settings = sleep_mode_settings
        self.wakeup_threshold = wakeup_threshold

    def charge(self):
        if self.dryrun:
            LOGGER.warning('Running dry-run mode: not sending any commands to aircons')
        if self.csv:
            LOGGER.info('Logging runtime analytics to %s', self.csv.file.name)
        self.load_ac_status()
        grid_import, pv_generation = self.solar.check_se_load()
        if pv_generation == 0:
            LOGGER.info('Inverter off, started in sleep mode')
            self.set_sleep_mode()
        for unit in self.ac:
            unit.get_basic_info()
            LOGGER.info(unit)
        while True:
            try:
                self.read_set_loop()
                time.sleep(self.read_interval)
            except Exception as e:
                LOGGER.error('Something went wrong, skip this run loop call, cause: %s', e)
                LOGGER.error(traceback.format_exc())

    def load_ac_status(self):
        for unit in self.ac:
            unit.get_sensor_info()
            unit.get_control_info()

    def get_ac_consumption(self):
        total = 0
        for unit in self.ac:
            consumption = unit.get_consumption()
            if not math.isnan(consumption):
                total += consumption
        return total

    def set_ac_controls(self, target):
        """The interesting part: adaptative controller to play
        with the aircon buttons and hope that we land as close
        as possible within the target consumption range by the
        next set iteration.
        Input parameters are:
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
                LOGGER.warning('Aircon in ' + unit.name + ' is turned off or irresponsive :(')
            if unit.controls.get('mode', '-') != '4' and unit.controls.get('pow', '-') == '1':
                LOGGER.error('Aircon in ' + unit.name + ' is not in heating mode, better stop here')
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
                    LOGGER.info('Turning humidification on in ' + self.ac[0].name)
                if target > 0 and shum1 == 0:
                    shum1 = 50
                    setting_change = True
                    target -= humidifier_consumption
                    LOGGER.info('Turning humidification on in ' + self.ac[1].name)
            # Recalculate temp increase steps
            steps = math.ceil(abs(target) / step_size)
            for i in range(steps):
                if min(stemp0, stemp1) == 30:
                    LOGGER.info('Both aircons set to max already, can\'t do anything')
                    break
                if stemp0 < stemp1:
                    stemp0 += 1
                    setting_change = True
                    LOGGER.info('Increasing temperature in ' + self.ac[0].name +
                                ' to ' + str(stemp0))
                else:
                    stemp1 += 1
                    setting_change = True
                    LOGGER.info('Increasing temperature in ' + self.ac[1].name +
                                ' to ' + str(stemp1))
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
                    LOGGER.info('Turning humidification off in ' + self.ac[0].name)
                if target < 0 and shum1 > 0:
                    shum1 = 0
                    setting_change = True
                    target += humidifier_consumption
                    LOGGER.info('Turning humidification off in ' + self.ac[1].name)
            # Recalculate temp increase steps
            steps = math.ceil(abs(target) / step_size)
            for i in range(steps):
                if max(stemp0 + 4 - htemp0, stemp1 + 4 - htemp1) <= 0:
                    LOGGER.info('Both aircons set to min temperature, can\'t do anything')
                    break
                if stemp0 + 4 - htemp0 > stemp1 + 4 - htemp1:
                    stemp0 -= 1
                    setting_change = True
                    LOGGER.info('Decreasing temperature in ' + self.ac[0].name +
                                ' to ' + str(stemp0))
                else:
                    stemp1 -= 1
                    setting_change = True
                    LOGGER.info('Decreasing temperature in ' + self.ac[1].name +
                                ' to ' + str(stemp1))
        if not setting_change:
            return
        # Set all changed temperature and humidity values now
        self.ac[0].controls['stemp'] = str(stemp0)
        self.ac[0].controls['shum'] = str(shum0)
        self.ac[1].controls['stemp'] = str(stemp1)
        self.ac[1].controls['shum'] = str(shum1)
        if self.dryrun:
            LOGGER.warning('Running in dry-run mode: no set action sent')
            return
        for unit in self.ac:
            unit.set_control_info()

    def calculate_target(self, load, consumption):
        """
        Target here is difference consumption wanted from AC
        Negative target to decrease consumption, positive to increase it
        """
        avg_load_target = (self.max_load + self.min_load) / 2
        if self.min_load < load < self.max_load:
            # Don't bother touching a thing if importing and
            # less than 200W
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

    def set_sleep_mode(self):
        if self.is_sleep_mode:
            return
        self.is_sleep_mode = True
        self.read_interval *= 10

    def unset_sleep_mode(self):
        if not self.is_sleep_mode:
            return
        self.is_sleep_mode = False
        self.read_interval /= 10

    def read_set_loop(self):
        now = datetime.now().strftime("%d/%m/%Y %H:%M:%S")
        LOGGER.info(now)
        grid_import, pv_generation = self.solar.check_se_load()
        if grid_import < 0:
            LOGGER.info('PV generating %dW Exporting %dW', pv_generation, -grid_import)
        else:
            LOGGER.info('PV generating %dW Importing %dW', pv_generation, grid_import)

        if pv_generation == 0 and not self.is_sleep_mode:
            LOGGER.info('PV generation just stopped, entering sleep mode')
            self.set_sleep_mode()
            for unit in self.ac:
                unit.controls = self.sleep_mode_settings
                unit.set_control_info()
        elif pv_generation >= self.wakeup_threshold and self.is_sleep_mode:
            LOGGER.info('PV generation just starting, leaving sleep mode')
            self.unset_sleep_mode()

        self.load_ac_status()
        ac_consumption = self.get_ac_consumption()
        LOGGER.info('Estimated combined A/C consumption %dW', ac_consumption)
        # Make sure values match the order of CSVLogger.data_to_save
        if self.csv:
            self.csv.write([now,
                            pv_generation,
                            grid_import,
                            ac_consumption,
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
            self.csv_next_save -= self.read_interval
            if self.csv_next_save <= 0:
                self.csv_next_save = self.csv_save_interval
                self.csv.save()
        if not self.is_sleep_mode:
            target = self.calculate_target(grid_import, ac_consumption)
            self.next_set -= self.read_interval
            LOGGER.info('Target is %d (import in [%d, %d]) ' +
                        ('next set in ' + str(self.next_set) + ' seconds \n'
                         if self.next_set > 0
                         else 'setting now \n'),
                        target, self.min_load, self.max_load)
            if (self.next_set <= 0 and target != 0):
                self.next_set = self.set_interval
                LOGGER.info("Setting A/C controls\n")
                self.set_ac_controls(target)

if __name__ == '__main__':
    main()
