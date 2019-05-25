import logging
import uncurl

import requests

logger = logging.getLogger(__name__)

class SolarAPI:

    def __init__(self, curl_filename):
        curl_file = open(curl_filename)
        curl_command = curl_file.read()
        curl_file.close()

        self.session = requests.Session()
        context = uncurl.parse_context(curl_command)
        request = requests.Request(method=context.method,
                                   url=context.url,
                                   headers=context.headers,
                                   cookies=context.cookies)
        self.prepared_request = self.session.prepare_request(request)

    def check_se_load(self):
        try:
            response = self.session.send(self.prepared_request)
        except Exception as e:
            logger.error('Call to SolarEdge API failed, connection error ' + str(e))
            return float('NaN')
        if response.status_code == 200:
            json = response.json()
            consumption = int(json['siteCurrentPowerFlow']['GRID']['currentPower'] * 1000)
            #Careful, case is flaky and check somewhat brittle
            is_export = {'from': 'LOAD', 'to': 'Grid'} in json['siteCurrentPowerFlow']['connections']
            if is_export:
                consumption = -consumption
            logger.debug('Consumption is ' + str(consumption) + 'W and ' + ('exporting' if is_export else 'importing'))
            return consumption
        else:
            logger.warning('Call to SolarEdge API failed, returned HTTP code ' + str(response.status_code))
            return float('NaN')
