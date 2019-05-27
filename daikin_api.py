import logging
import requests
import urllib.parse

logger = logging.getLogger(__name__)

class Aircon:

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
        self.num = num;
        self.name = 'aircon' + str(num);
        self.host = host;
        self.sensors = {};
        self.info = {};
        self.controls = {};
        self.command_to_send = False;

    def api_get(self, path):
        logger.debug(self.host + path)
        try:
            response = requests.get(self.host + path, timeout=4)
            response.raise_for_status()
        except Exception as e:
            logger.error('aircon api get request error: %s', e)
            return {}
        return dict(x.split('=') for x in response.text.split(','))

    def api_set(self, path, dic):
        logger.debug(self.host + path)
        try:
            response = requests.get(self.host + path, dic, timeout=4)
            response.raise_for_status()
        except Exception as e:
            logger.error('aircon api set request error: %s', e)
            return {}
        ret_dic = dict(x.split('=') for x in response.text.split(','))
        if not 'ret' in ret_dic or ret_dic['ret'].upper() != 'OK':
            logger.error('aircon api set request returned with %s, request was %s',
                         response.text, r.request.path_url)
        return ret_dic

    def get_sensor_info(self):
        self.sensors = self.api_get('/aircon/get_sensor_info')

    def get_control_info(self):
        controls = self.api_get('/aircon/get_control_info')
        if 'shum' in controls and controls['shum'].upper() == 'CONTINUE':
            controls['shum'] = '100'
        self.controls = controls

    def set_control_info(self):
        c = self.controls;
        settings = {'pow': c['pow'], 'mode': c['mode'], 'stemp': c['stemp'], 'shum': c['shum']};
        logger.info('Setting ' + self.name + ' ' +
                    Aircon.power[c['pow']] + ' ' +
                    Aircon.mode[c['mode']] + ' ' +
                    c['stemp'] + 'ºC, ' +
                    c['shum'] + '% RH')
        return self.api_set('/aircon/set_control_info', settings)

    def get_basic_info(self):
        self.info = self.api_get('/common/basic_info')
        if 'name' in self.info:
            self.name = urllib.parse.unquote(self.info['name'])

    def get_consumption(self):
        if not (self.sensors and 'cmpfreq' in self.sensors
                and self.controls and 'shum' in self.controls):
            return float('NaN')
        shum = self.controls['shum']
        shum = int(shum) if shum.isnumeric() else 0
        cmpfreq = int(self.sensors['cmpfreq'])
        return cmpfreq * 20 + (200 if shum > 0 else 0)

    def __str__(self):
        return ('AC ' + self.name + ' ' +
                str(self.get_consumption()) + 'kW ' +
                'Indoor ' + self.sensors.get('htemp', '-') + 'ºC, ' +
                'Outdoor ' + self.sensors.get('otemp', '-') + 'ºC, ' +
                'Compressor ' + self.sensors.get('cmpfreq', '-') + 'Hz ' +
                Aircon.power.get(self.controls.get('pow', '-'), '-') + ' ' +
                Aircon.mode.get(self.controls.get('mode', '-'), '-') + ' ' +
                self.controls.get('stemp', '-') + 'ºC, ' +
                self.controls.get('shum', '-') + '% RH')
