#!/usr/bin/env python3
from datetime import datetime
import flask
import logging
import math
import time

from daikin_api import Aircon
from solar_api import SolarAPI
from requests import Request, Session

logger = logging.getLogger('brick_battery')

def main():
    logging.basicConfig(level=logging.INFO)
    logging.getLogger("urllib3").setLevel(logging.INFO)

    ac = [Aircon(0, 'http://192.168.1.101'),
          Aircon(1, 'http://192.168.1.102')]

    solar = SolarAPI('currentPowerFlow.curl')

    bbc = BrickBatteryCharger(ac, solar, read_interval=5, set_interval=60)
    bbc.charge()

class BrickBatteryCharger:

    def __init__(self, ac, solar,
                 set_interval, read_interval):
        self.ac = ac
        self.solar = solar
        self.set_interval = set_interval
        self.read_interval = read_interval
        self.next_set = 10

    def charge(self):
        self.load_ac_status()
        for unit in self.ac:
            unit.get_basic_info()
            logger.info(unit)
        while True:
            self.read_set_loop()
         
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
        Many levers are:
        - set_interval duration for how reactive we
          want to be, but the aircons can't take some time to change
          state too
        - step_size for how many temp buttons we want to press
          based on how far we are from the target
        - humidifier_consumption
        '''
        # Watts per temperature +/- button press
        step_size = 200
        # Watts when humidifier is turned on
        humidifier_consumption = 300
        setting_change = False

        # Some sanity checks first
        for unit in self.ac:
            if unit.controls['pow'] == '0':
                logger.warning('Aircon in ' + unit.name + ' is turned off :(')
            if unit.controls['mode'] != '4':
                logger.error('Aircon in ' + unit.name + ' is not in heating mode, better stop here')
                return
        # First rule of thumb for reactivity, action a temperature step per 200W difference
        steps = math.ceil(abs(target) / step_size)
        stemp0 = float(self.ac[0].controls['stemp'])
        stemp1 = float(self.ac[1].controls['stemp'])
        shum0 = int(self.ac[0].controls['shum'])
        shum1 = int(self.ac[1].controls['shum'])
        if target > 0:
            # Increase AC consumption
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
                    logger.info('Both aircons set to max temperature, can\'t do anything')
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
        # Set all changed temperature and humidity values now
        self.ac[0].controls['stemp'] = str(stemp0)
        self.ac[0].controls['shum'] = str(shum0)
        self.ac[1].controls['stemp'] = str(stemp1)
        self.ac[1].controls['shum'] = str(shum1)
        for unit in self.ac:
            unit.set_control_info()
        return

    def calculate_target(self, load, consumption):
        '''Target here is difference consumption wanted from AC
           Negative target to decrease consumption, positive to increase it
        '''
        if 0 < load and load < 200:
            # Don't bother touching a thing if importing and
            # less than 200W
            return 0
        if load > 0 and consumption > 0:
            # We need to reduce AC consumption
            return -min(load, consumption)
        if load < 0 and consumption < len(self.ac) * Aircon.MAX_AC_CONSUMPTION:
            # We need to increase AC consumption
            return min(-load, len(self.ac) * Aircon.MAX_AC_CONSUMPTION)
        # We cannot help because either the AC is off and we're over consuming
        # or we're exporting but we're already turned the AC to max capacity
        return 0

    def read_set_loop(self):
        logger.info(datetime.now().strftime("%d/%m/%Y %H:%M:%S"))
        load = self.solar.check_se_load()
        logger.info('Import from grid is ' + str(load) + 'W')
        self.load_ac_status()
        ac_consumption = self.get_ac_consumption()
        ac0model = self.ac[0].get_consumption_from_model()
        ac1model = self.ac[1].get_consumption_from_model()
        logger.info('Estimated combined A/C consumption ' + str(ac_consumption) + 'W')
        target = self.calculate_target(load, ac_consumption)
        logger.info('Target is ' + str(target) + '\n')
        self.next_set -= self.read_interval
        if (self.next_set <= 0 and target != 0):
            self.next_set = self.set_interval
            logger.info("Setting A/C controls\n")
            self.set_ac_controls(target)
        time.sleep(self.read_interval)

if __name__ == '__main__':
    main()
