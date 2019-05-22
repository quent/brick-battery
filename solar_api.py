#!/usr/bin/env python3
import subprocess
import sys
import flask
import uncurl
import time
from requests import Request, Session

def main():
    curl_file = open('currentPowerFlow.curl')
    curl_command = curl_file.read()
    curl_file.close()

    context = uncurl.parse_context(curl_command)

    session = Session()
    request = Request(method=context.method, url=context.url, headers=context.headers, cookies=context.cookies)
    prepared_request = session.prepare_request(request)

    while True:
        check_se_load()
        time.sleep(3)


def check_se_load():
    response = session.send(prepared_request)
    if response.status_code == 200:
        json = response.json()
        consumption = json['siteCurrentPowerFlow']['GRID']['currentPower']
        is_export = {'from': 'Load', 'to': 'GRID'} in json['siteCurrentPowerFlow']['connections']
        if is_export:
            consumption = -consumption
        print(f'consumption is {consumption} and exporting: {is_export}')
    else:
        print(f'Call to SolarEdge API failed, returned HTTP code {response.status_code}')

main()
