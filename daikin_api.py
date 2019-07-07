"""
Module for the Daikin BRP072A42 WiFi Control unit API
This module controls one split air conditioning unit.
"""

import math
import logging
import urllib.parse
import aiohttp

LOGGER = logging.getLogger(__name__)

class Aircon:
    """
    An Aircon class interacts with one physical instance of a Daikin wifi unit.
    It implements some functionalities specific to the Ururu Sarara (FTXZN) unit
    like humidification settings.
    Unfortunately, this Ururu unit does not implement the fan settings part of the
    API, which means we have no control to increase heating output if the IR remote
    last set it to low airflow.
    The sensor parameter cmpfreq gives us the frequency of the outside compressor
    which drives most of the whole aircon consumption and lets us estimate roughly
    its actual electrical load in watts.
    """

    # The world would be a better place if people used meaningful names rather than a
    # mix of empty values and numbers.
    mode = {'' : 'HUMIDIFY',
            '0': '',
            '1': 'AUTO',
            '2': 'DRY',
            '3': 'COOL',
            '4': 'HEAT',
            '5': '?',
            '6': 'FAN ONLY',
            '7': '?'}

    power = {'0': 'OFF',
             '1': 'ON'}

    MAX_AC_CONSUMPTION = 1400

    def __init__(self, num, host):
        self.num = num
        self.name = 'aircon' + str(num)
        self.host = host
        self.sensors = {}
        self.info = {}
        self.controls = {}
        self.command_to_send = False
        self._session = None

    def _get_session(self):
        """
        Lazy ClientSession initialisation to ensure its life-cycle remains
        within the one of the event loop
        """
        if self._session is None:
            self._session = aiohttp.ClientSession(
                raise_for_status=True,
                conn_timeout=4)
        return self._session

    async def api_get(self, path):
        """
        Send a get command at the specified path to the aircon controller
        and return the values as a dictionary if ok or an empty dictionary if
        any error occurred.
        """
        LOGGER.debug(self.host + path)
        try:
            async with await self._get_session().get(self.host + path) as response:
                text = await response.text()
                return dict(x.split('=') for x in text.split(','))
        except Exception as ex:
            LOGGER.error('aircon api get request error: %s', ex)
            return {}

    async def api_set(self, path, dic):
        """
        Send a set command: an HTTP GET but with request parameters to set controls
        at the specified path to the aircon controller using the parameter in dic
        and return the values as a dictionary if ok or an empty dictionary if
        any transport error occurred, it will return the actual response if
        the protocol command was invalid (check the value of 'ret').
        """
        LOGGER.debug(self.host + path)
        try:
            async with await self._get_session().get(
                    self.host + path + '?' + urllib.parse.urlencode(dic)) as response:
                text = await response.text()
                ret_dic = dict(x.split('=') for x in text.split(','))
                if 'ret' not in ret_dic or ret_dic['ret'].upper() != 'OK':
                    LOGGER.error('aircon api set request returned with %s, request was %s',
                                 response.text, response.request.path_url)
                return ret_dic
        except Exception as ex:
            LOGGER.error('aircon api set request error: %s', ex)
            return {}

    async def get_sensor_info(self):
        """Load sensors information (room and outdoor temperatures, compressor frequency)"""
        self.sensors = await self.api_get('/aircon/get_sensor_info')

    async def get_control_info(self):
        """
        Load controls information (set target room temperature and humidity,
        mode, power on/off)
        """
        controls = await self.api_get('/aircon/get_control_info')
        if 'shum' in controls and controls['shum'].upper() == 'CONTINUE':
            controls['shum'] = '100'
        self.controls = controls

    async def set_control_info(self):
        """
        Sends a command to the aircon to update its controls.
        You need to first update settings in the `controls` dictionary
        then call this function.
        """
        controls = self.controls
        settings = {'pow': controls['pow'],
                    'mode': controls['mode'],
                    'stemp': controls['stemp'],
                    'shum': controls['shum']}
        LOGGER.info('Setting %s %s %s %sºC, %s%% RH',
                    self.name,
                    Aircon.power[controls['pow']],
                    Aircon.mode[controls['mode']],
                    controls['stemp'],
                    controls['shum'])
        return await self.api_set('/aircon/set_control_info', settings)

    async def get_basic_info(self):
        """Load basic information (only aircon name is actually used here)"""
        self.info = await self.api_get('/common/basic_info')
        if 'name' in self.info:
            self.name = urllib.parse.unquote(self.info['name'])

    def get_consumption(self):
        """
        Returns an estimated electrical consumption in watts.
        This modelled value is purely empirical but mostly accurate as the
        compressor generates most of the load and its consumption seems to be
        proportional to its frequency.
        The internal fan speed (which we don't know for the Ururu unit) seems to
        have little effect.
        As for the humidifying function, unfortunately, it seems to vary with the
        internal fan speed.
        """
        if not (self.sensors and 'cmpfreq' in self.sensors
                and self.controls and 'shum' in self.controls):
            return float('NaN')
        shum = self.controls['shum']
        shum = int(shum) if shum.isnumeric() else 0
        cmpfreq = int(self.sensors['cmpfreq'])
        return cmpfreq * 20 + (200 if shum > 0 else 0)

    def __str__(self):
        """One-liner output for nice summary in a console"""
        return ('AC ' + self.name + ' ' +
                str(self.get_consumption()) + 'kW ' +
                'Indoor ' + self.sensors.get('htemp', '-') + 'ºC, ' +
                'Outdoor ' + self.sensors.get('otemp', '-') + 'ºC, ' +
                'Compressor ' + self.sensors.get('cmpfreq', '-') + 'Hz ' +
                Aircon.power.get(self.controls.get('pow', '-'), '-') + ' ' +
                Aircon.mode.get(self.controls.get('mode', '-'), '-') + ' ' +
                self.controls.get('stemp', '-') + 'ºC, ' +
                self.controls.get('shum', '-') + '% RH')

    def json_dict(self):
        """Return dictionary of only useful objects"""
        consumption = self.get_consumption()
        return {
            'name': self.name,
            'consumption': consumption if not math.isnan(consumption) else '-',
            'power': Aircon.power.get(self.controls.get('pow', '-'), '-'),
            'mode': Aircon.mode.get(self.controls.get('mode', '-'), '-'),
            'stemp': self.controls.get('stemp', '-'),
            'shum': self.controls.get('shum', '-'),
            'htemp': self.sensors.get('htemp', '-'),
            'otemp': self.sensors.get('otemp', '-'),
            'cmpfreq': self.sensors.get('cmpfreq', '-')
            }
