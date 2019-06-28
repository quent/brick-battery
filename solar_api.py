import datetime
import logging
import uncurl

import requests

LOGGER = logging.getLogger(__name__)

class SolarAPI:

    def __init__(self, cpf_curl_filename, site_curl_filename=None):
        """
        cpf_curl_filename text file containing the curl command used to retrieve
                          currentPowerFlow data
        site_curl_filename optional text file containing the curl command used
                           to retrieve the last updated information (optional,
                           only used to verify when data was last updated if the
                           SolarEdge backend or inverter is not processing and
                           dispatching real-time data). currentPowerFlow
                           does not contain any timestamp :((((
        """
        cpf_curl_file = open(cpf_curl_filename)
        cpf_curl_command = cpf_curl_file.read()
        cpf_curl_file.close()

        self.session = requests.Session()
        cpf_context = uncurl.parse_context(cpf_curl_command)
        cpf_request = requests.Request(method=cpf_context.method,
                                       url=cpf_context.url,
                                       headers=cpf_context.headers,
                                       cookies=cpf_context.cookies)
        self.cpf_prepared_request = self.session.prepare_request(cpf_request)

        if not site_curl_filename:
            self.site_prepared_request = None
        else:
            site_curl_file = open(site_curl_filename)
            site_curl_command = site_curl_file.read()
            site_curl_file.close()

            site_context = uncurl.parse_context(site_curl_command)
            site_request = requests.Request(method=site_context.method,
                                            url=site_context.url,
                                            headers=site_context.headers,
                                            cookies=site_context.cookies)
            self.site_prepared_request = self.session.prepare_request(site_request)

    def check_se_load(self):
        """
        Send the current power flow prepared request to the Solar API and
        return a 2-uple with flow to grid and PV generation, both in watts.
        Returns NaN, NaN is the request failed.
        """
        try:
            response = self.session.send(self.cpf_prepared_request)
            response.raise_for_status()
        except Exception as e:
            LOGGER.error('Call to SolarEdge API for current power flow failed: %s', e)
            return float('NaN'), float('NaN')
        if response.status_code == 200:
            json = response.json()
            grid_import = int(json['siteCurrentPowerFlow']['GRID']['currentPower'] * 1000)
            pv = int(json['siteCurrentPowerFlow']['PV']['currentPower'] * 1000)
            is_export = False
            for connection in json['siteCurrentPowerFlow']['connections']:
                for k, v in connection.items():
                    if v.lower() == 'grid':
                        is_export = k.lower() == 'to'
            LOGGER.debug('%s %dW', 'Exporting' if is_export else 'Importing', grid_import)
            return -grid_import if is_export else grid_import, pv
        else:
            return float('NaN'), float('NaN')

    def check_last_updated(self):
        try:
            response = self.session.send(self.site_prepared_request)
            response.raise_for_status()
        except Exception as e:
            LOGGER.error('Call to SolarEdge API for site info failed: %s', e)
            return None
        if response.status_code == 200:
            json = response.json()
            last_updated_string = json['fieldOverview']['fieldOverview']['lastUpdateTime']
            LOGGER.debug('last updated string %s\n', last_updated_string)
            try:
                last_updated_datetime = datetime.datetime.strptime(last_updated_string[:19], '%Y-%m-%d %H:%M:%S')
            except ValueError as ve:
                LOGGER.warning('SolarEdge API for site returned a date that could not be parsed: %s, error: %s', lastUpdated, ve)
                return None
            LOGGER.debug('SolarEdge data last updated on %s', last_updated_datetime)
            return last_updated_datetime
        else:
            return None
