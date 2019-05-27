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
            response.raise_for_status()
        except Exception as e:
            logger.error('Call to SolarEdge API failed: %s', e)
            return float('NaN')
        if response.status_code == 200:
            json = response.json()
            grid_import = int(json['siteCurrentPowerFlow']['GRID']['currentPower'] * 1000)
            pv = int(json['siteCurrentPowerFlow']['PV']['currentPower'] * 1000)
            is_export = False
            for connection in json['siteCurrentPowerFlow']['connections']:
                for k, v in connection.items():
                    if v.lower() == 'grid':
                        is_export = k.lower() == 'to'
            logger.debug('%s %dW', 'Exporting' if is_export else 'Importing', grid_import)
            return -grid_import if is_export else grid_import, pv
