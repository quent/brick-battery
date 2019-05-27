#!/usr/bin/env python3
from datetime import datetime
import flask
import logging
import math
import os
import sys
import time

from daikin_api import Aircon
from solar_api import SolarAPI
from requests import Request, Session

logger = logging.getLogger('brick_battery')

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

    # don't read less often than 3s otherwise SolarEdge drops connection
    bbc = BrickBatteryCharger(ac,
                              solar,
                              read_interval=3,
                              set_interval=60,
                              min_load = 200,
                              max_load = 1000,
                              dryrun_mode=False)
    bbc.charge()

class BrickBatteryCharger:

    def __init__(self, ac, solar, set_interval, read_interval,
        dryrun_mode, min_load, max_load):
        self.ac = ac
        self.solar = solar
        self.set_interval = set_interval
        self.read_interval = read_interval
        self.next_set = 10
        self.dryrun = dryrun_mode
        self.min_load = min_load
        self.max_load = max_load

    def charge(self):
        if self.dryrun:
            logger.warning('Running dry-run mode: not sending any commands to aircons')
        self.load_ac_status()
        for unit in self.ac:
            unit.get_basic_info()
            logger.info(unit)
        while True:
            try:
                self.read_set_loop()
            except Exception as e:
                logger.error('Something went wrong, skip this run loop call, cause: %s', e)
         
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
        '''The interesting part: adaptative controller to play
        with the aircon buttons and hope that we land as close
        as possible within the threshold by the next set iteration
        Input parameters are:
        - set_interval duration between each AC set operation,
          for how reactive we want to be, but the aircons can
          take some time to adapt their consumption too
        - step_size for how many temp buttons we want to press
          based on how far we are from the target
        - humidifier_consumption used to estimate its impact to
          get closer to the target
        '''
        # Watts per temperature +/- button press
        step_size = 200
        # Watts when humidifier is turned on
        humidifier_consumption = 200
        setting_change = False

        # Some sanity checks first
        for unit in self.ac:
            if unit.controls.get('pow', '-') != '1':
                logger.warning('Aircon in ' + unit.name + ' is turned off or irresponsive :(')
            if unit.controls.get('mode', '-') != '4' and unit.controls.get('pow', '-') == '1':
                logger.error('Aircon in ' + unit.name + ' is not in heating mode, better stop here')
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
                    logger.info('Turning humidification on in ' + self.ac[0].name)
                if target > 0 and shum1 == 0:
                    shum1 = 50
                    setting_change = True
                    target -= humidifier_consumption
                    logger.info('Turning humidification on in ' + self.ac[1].name)
            # Recalculate temp increase steps
            steps = math.ceil(abs(target) / step_size)
            for i in range(steps):
                if min(stemp0, stemp1) == 30:
                    logger.info('Both aircons set to max already, can\'t do anything')
                    break
                if stemp0 < stemp1:
                    stemp0 += 1
                    setting_change = True
                    logger.info('Increasing temperature in ' + self.ac[0].name + ' to ' + str(stemp0))
                else:
                    stemp1 += 1
                    setting_change = True
                    logger.info('Increasing temperature in ' + self.ac[1].name + ' to ' + str(stemp1))
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
                    logger.info('Turning humidification off in ' + self.ac[0].name)
                if target < 0 and shum1 > 0:
                    shum1 = 0
                    setting_change = True
                    target += humidifier_consumption
                    logger.info('Turning humidification off in ' + self.ac[1].name)
            # Recalculate temp increase steps
            steps = math.ceil(abs(target) / step_size)
            for i in range(steps):
                if max(stemp0 + 4 - htemp0, stemp1 + 4 - htemp1) <= 0:
                    logger.info('Both aircons set to min temperature, can\'t do anything')
                    break
                if stemp0 + 4 - htemp0 > stemp1 + 4 - htemp1:
                    stemp0 -= 1
                    setting_change = True
                    logger.info('Decreasing temperature in ' + self.ac[0].name + ' to ' + str(stemp0))
                else:
                    stemp1 -= 1
                    setting_change = True
                    logger.info('Decreasing temperature in ' + self.ac[1].name + ' to ' + str(stemp1))
        if not setting_change:
            return
        # Set all changed temperature and humidity values now
        self.ac[0].controls['stemp'] = str(stemp0)
        self.ac[0].controls['shum'] = str(shum0)
        self.ac[1].controls['stemp'] = str(stemp1)
        self.ac[1].controls['shum'] = str(shum1)
        if self.dryrun:
            logger.warning('Running in dry-run mode: no set action sent')
            return
        for unit in self.ac:
            unit.set_control_info()

    def calculate_target(self, load, consumption):
        '''Target here is difference consumption wanted from AC
           Negative target to decrease consumption, positive to increase it
        '''
        avg_load_target = (self.max_load + self.min_load) / 2
        if self.min_load < load and load < self.max_load:
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

    def read_set_loop(self):
        logger.info(datetime.now().strftime("%d/%m/%Y %H:%M:%S"))
        grid_import, pv_generation = self.solar.check_se_load()
        if grid_import < 0:
            logger.info('PV generating %dW Exporting %dW', pv_generation, -grid_import)
        else:
            logger.info('PV generating %dW Importing %dW', pv_generation, grid_import)
        self.load_ac_status()
        ac_consumption = self.get_ac_consumption()
        logger.info('Estimated combined A/C consumption %dW', ac_consumption)
        target = self.calculate_target(grid_import, ac_consumption)
        logger.info('Target is %d (import in [%d, %d]) next set in %d seconds \n',
                    target, self.min_load, self.max_load, self.next_set)
        self.next_set -= self.read_interval
        if (self.next_set <= 0 and target != 0):
            self.next_set = self.set_interval
            logger.info("Setting A/C controls\n")
            self.set_ac_controls(target)
        time.sleep(self.read_interval)

if __name__ == '__main__':
    main()
