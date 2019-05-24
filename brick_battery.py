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
        self.next_set = set_interval

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
        return

    def calculate_target(self, load, consumption):
        '''Target here is difference consumption wanted from AC
           Negative target to decrease consumption, positive to increase it
        '''
        if abs(load) < 200:
            # Don't bother touching a thing within a 200W threshold
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
        logger.info('Estimated combined A/C consumption from model ' +
                     str(ac0model + ac1model) + 'W' + ': ' + str(ac0model) + 'W + ' + str(ac1model))
        logger.info('stemp-htemp=' + str(float(self.ac[0].controls['stemp']) - float(self.ac[0].sensors['htemp'])) +
                     ' cmpfreq=' + self.ac[0].sensors['cmpfreq'])
        target = self.calculate_target(load, ac_consumption)
        logger.info('Target is ' + str(target) + '\n')
        self.next_set -= self.read_interval
        if (self.next_set <= 0):
            self.next_set = self.set_interval
            if target != 0:
                logger.info("Setting A/C controls\n")
                self.set_ac_controls(target)
        time.sleep(self.read_interval)

if __name__ == '__main__':
    main()
