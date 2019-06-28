#!/usr/bin/env python3
import logging
from solar_api import SolarAPI

LOGGER = logging.getLogger('brick_battery')

def main():
    logging.basicConfig(level=logging.DEBUG)
    s = SolarAPI('currentPowerFlow.curl', 'sites.curl')
    print('last updated: %s', s.check_last_updated())

if __name__ == '__main__':
    main()
