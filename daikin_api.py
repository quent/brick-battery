import logging
import requests
import urllib.parse

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

    def __init__(self, num, host):
        self.num = num;
        self.name = 'aircon' + str(num);
        self.host = host;
        self.sensors = {};
        self.info = {};
        self.controls = {};
        self.command_to_send = False;

    def api_get(self, path):
        logging.debug(self.host + path)
        response = requests.get(self.host + path, timeout=2)
        return dict(x.split('=') for x in response.text.split(','))

    def api_set(self, path, dic):
        logging.debug(self.host + path)
        response = requests.get(self.host + path, dic, timeout=2)
        ret_dic = dict(x.split('=') for x in response.text.split(','))
        if not 'ret' in ret_dic or ret_dic['ret'] != 'OK':
            logging.error('aircon_api_set returned with ' + response.text)
        return ret_dic

    def get_sensor_info(self):
        self.sensors = self.api_get('/aircon/get_sensor_info')

    def get_control_info(self):
        self.controls = self.api_get('/aircon/get_control_info')

    def set_control_info(self):
        return self.api_set('/aircon/set_control_info', self.controls)

    def get_basic_info(self):
        self.info = self.api_get('/common/basic_info')
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
                'Indoor ' + self.sensors['htemp'] + 'ºC, ' +
                'Outdoor ' + self.sensors['otemp'] + 'ºC, ' +
                'Compressor ' + self.sensors['cmpfreq'] + 'Hz ' +
                Aircon.power[self.controls['pow']] + ' ' + Aircon.mode[self.controls['mode']] + ' ' +
                self.controls['stemp'] + 'ºC, ' +
                self.controls['shum'] + '% RH')

