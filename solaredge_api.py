"""
Module for the SolarEdge Web API to get realtime PV generation, load and grid import
pushed by the inverter and energy monitor.
"""
import logging
import uncurl

import aiohttp

LOGGER = logging.getLogger(__name__)

class SolarInfo:
    """
    The SolarInfo class manages a persistent secure HTTP connection to the 
    Solaredge API and allows to retrieve data asynchronously.

    Just store the curl command with parameters used to get the current power flow
    from your site in a text file and pass the filename to the constructor.

    To ensure the connection does not get closed, it is recommended to poll
    about every 3 seconds.
    """

    def __init__(self, curl_filename):
        """
        curl_filename text file containing the curl command used to retrieve
                          currentPowerFlow data
        """
        self._session = None
        with open(curl_filename) as curl_file:
            curl_command = curl_file.read()
            self.cpf_context = uncurl.parse_context(curl_command)

    def _get_session(self):
        """
        Lazy ClientSession initialisation to ensure its life-cycle remains
        within the one of the event loop
        """
        if self._session is None:
            self._session = aiohttp.ClientSession(raise_for_status=True)
        return self._session

    async def check_se_load(self):
        """
        Send the current power flow prepared request to the Solar API and
        return a 2-uple with flow to grid and PV generation, both in watts.
        Return NaN, NaN if the request failed or the JSON could not be parsed.

        Examples of JSON responses (note the capitalisation of names
        in connections depends on whether it is in from or to, yey!):

        * Import with generation:
        {"siteCurrentPowerFlow": {
            "updateRefreshRate": 3,
            "unit": "kW",
            "connections":
                [{"from": "GRID", "to": "Load"},
                 {"from": "PV",   "to": "Load"}],
            "GRID": {"status": "Active", "currentPower": 0.13},
            "LOAD": {"status": "Active", "currentPower": 3.15},
            "PV":   {"status": "Active", "currentPower": 3.02}}}

        * Balanced load and generation:
        {"siteCurrentPowerFlow":{
            "updateRefreshRate":3,
            "unit":"kW",
            "connections":
                [{"from":"PV","to":"Load"}],
            "GRID":{"status":"Active","currentPower":0.0},
            "LOAD":{"status":"Active","currentPower":2.98},
            "PV":{"status":"Active","currentPower":2.98}}}

        * Export:
        {"siteCurrentPowerFlow":{
            "updateRefreshRate":3,
            "unit":"kW",
            "connections":
                [{"from":"LOAD","to":"Grid"},
                 {"from":"PV","to":"Load"}],
            "GRID":{"status":"Active","currentPower":0.19},
            "LOAD":{"status":"Active","currentPower":2.2},
            "PV":{"status":"Active","currentPower":2.39}}}
        """
        try:
            async with await self._get_session().request(
                    method=self.cpf_context.method,
                    url=self.cpf_context.url,
                    headers=self.cpf_context.headers,
                    cookies=self.cpf_context.cookies) as response:

                json = await response.json()
                grid_import = int(json['siteCurrentPowerFlow']['GRID']['currentPower'] * 1000)
                pv_generation = int(json['siteCurrentPowerFlow']['PV']['currentPower'] * 1000)
                is_export = False
                for connection in json['siteCurrentPowerFlow']['connections']:
                    for key, value in connection.items():
                        if key == 'to' and value.lower() == 'grid':
                            is_export = True
                            break
                LOGGER.debug('%s %dW', 'Exporting' if is_export else 'Importing', grid_import)
                return (-grid_import if is_export else grid_import), pv_generation
        except Exception as ex:
            LOGGER.error('Call to SolarEdge API for current power flow failed: %s', ex)
            return float('NaN'), float('NaN')
