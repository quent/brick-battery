#!/usr/bin/env python3
from datetime import datetime
import flask
import logging
import math
import uncurl
import time

from daikin_api import Aircon
from requests import Request, Session

MAX_AC_CONSUMPTION = 2500

def main():
    logging.basicConfig(level=logging.INFO)

    curl_file = open('currentPowerFlow.curl')
    curl_command = curl_file.read()
    curl_file.close()

    context = uncurl.parse_context(curl_command)

    session = Session()
    request = Request(method=context.method, url=context.url, headers=context.headers, cookies=context.cookies)
    prepared_request = session.prepare_request(request)

    ac = [Aircon(0, 'http://192.168.1.101'),
          Aircon(1, 'http://192.168.1.102')]

    check_ac_status(ac)
    for unit in ac:
        unit.get_basic_info()
        logging.info(unit)

    while True:
        load = check_se_load(session, prepared_request)
        logging.info(datetime.now().strftime("%d/%m/%Y %H:%M:%S"))
        logging.info('Import from grid is ' + str(load) + 'W')
        check_ac_status(ac)
        ac_consumption = get_ac_consumption(ac)
        logging.info('Estimated combined A/C consumption ' + str(ac_consumption) + 'W')
        target = calculate_target(load, ac_consumption)
        logging.info('Target is ' + str(target) + '\n')
        time.sleep(5)

def calculate_target(load, consumption):
    '''Target here is difference consumption wanted from AC
       We'll investigate using a target absolut AC consumption value
       for models too...
    '''

    if abs(load) < 200:
        # Don't bother touching a thing within a 200W threshold
        return 0
    if load > 0 and consumption > 0:
        return -min(load, consumption)
    if load < 0 and consumption < MAX_AC_CONSUMPTION:
        return min(-load, MAX_AC_CONSUMPTION)

def check_ac_status(ac):
    for unit in ac:
        unit.get_sensor_info()
        unit.get_control_info()

def get_ac_consumption(ac):
    total = 0
    for unit in ac:
        consumption = unit.get_consumption()
        if not math.isnan(consumption):
            total += consumption
    return total

def check_se_load(session, prepared_request):
    response = session.send(prepared_request)
    if response.status_code == 200:
        json = response.json()
        consumption = int(json['siteCurrentPowerFlow']['GRID']['currentPower'] * 1000)
        is_export = {'from': 'Load', 'to': 'GRID'} in json['siteCurrentPowerFlow']['connections']
        if is_export:
            consumption = -consumption
        logging.debug(f'consumption is {consumption}W and exporting: {is_export}')
        return consumption
    else:
        logging.warning(f'Call to SolarEdge API failed, returned HTTP code {response.status_code}')
        return float('NaN')

main()
