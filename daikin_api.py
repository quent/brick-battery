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

    MAX_AC_CONSUMPTION = 1200

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
        response = requests.get(self.host + path, timeout=3)
        return dict(x.split('=') for x in response.text.split(','))

    def api_set(self, path, dic):
        logger.debug(self.host + path)
        response = requests.get(self.host + path, dic, timeout=3)
        ret_dic = dict(x.split('=') for x in response.text.split(','))
        if not 'ret' in ret_dic or ret_dic['ret'] != 'OK':
            logger.error('aircon_api_set returned with ' + response.text)
        return ret_dic

    def get_sensor_info(self):
        self.sensors = self.api_get('/aircon/get_sensor_info')

    def get_control_info(self):
        self.controls = self.api_get('/aircon/get_control_info')

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
        self.name = urllib.parse.unquote(self.info['name'])

    def get_consumption_from_model(self):
        '''Here we use a possible estimation of what we could get given
           the indoor and set temperature rather than compressor frequency.
           This assumes fan setting set to maximum air flow (which we can't check).
        '''
        htemp = float(self.sensors['htemp'])
        stemp = float(self.controls['stemp'])
        shum = float(self.controls['shum'])
        # we assume a linear temp / power curve for now
        return max(stemp + 4 - htemp, 0) * 200 + (200 if shum > 0 else 0)

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
                'Indoor ' + self.sensors['htemp'] + 'ºC, ' +
                'Outdoor ' + self.sensors['otemp'] + 'ºC, ' +
                'Compressor ' + self.sensors['cmpfreq'] + 'Hz ' +
                Aircon.power[self.controls['pow']] + ' ' + Aircon.mode[self.controls['mode']] + ' ' +
                self.controls['stemp'] + 'ºC, ' +
                self.controls['shum'] + '% RH')

