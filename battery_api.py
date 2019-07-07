"""
Module used to provide controls and status of the BrickBatteryController
via a web API
"""

import datetime
import logging
import math
from aiohttp import web

LOGGER = logging.getLogger('battery_api')

class BrickBatteryHTTPServer:
    """
    An asynchronous HTTP server using aiohttp.
    It runs in the event loop along with battery controller.
    A nice web front-end will let it show power flows and
    control settings such as on/off, target min and max grid load,
    threshold for waking up the controller.
    """
    def __init__(self, host, port):
        """
        Args:
        host the host/IP on which to accept connection
             ('localhost' for local connections only,
              None to accept connection from anywhere)
        port the port number to bind the listening socket to
        """
        self.host = host
        self.port = port
        app = web.Application()
        app.add_routes([web.get('/', self.hello),
                        web.get('/status', self.status),
                        web.get('/controls', self.controls)])
        self.runner = web.AppRunner(app)
        self.controller = None

    def register_controller(self, controller):
        """
        The controller must register itself with the BrickBatteryHTTPServer
        instance during its initialisation
        """
        self.controller = controller

    async def stop(self):
        """If needed, to shut down cleanly"""
        await self.runner.cleanup()

    async def start(self):
        """
        Start the socket listener on the localhost, make sur the controller has
        been registered first
        """
        await self.runner.setup()
        site = web.TCPSite(self.runner, self.host, self.port)
        await site.start()
        LOGGER.info('Web API listening to %s:%s', self.host, self.port)

    async def status(self, request):
        """Pass all details about current state"""
        ctrl = self.controller
        json = {'operation': ctrl.operation,
                'last_updated': ctrl.last_updated,
                'last_set': ctrl.last_set,
                'is_sleep_mode': ctrl.is_sleep_mode,
                'solar': ctrl.solar,
                'ac_consumption': ctrl.ac_consumption,
                'aircons': ctrl.ac}
        return web.json_response(safe_json(json))

    async def controls(self, request):
        """Set controls """
        LOGGER.info("request query dict=%s",
                    ', '.join(['{}: {}'.format(k, v) for k, v in request.query.items()]))
        config, invalid_parameters = self.parse_controls_query(request.query.items())
        if invalid_parameters:
            return web.json_response({'errors': invalid_parameters})
        for key, value in config.items():
            if key.startswith('ac_sleep_'):
                if self.controller.sleep_mode_settings[key[9:]] != value:
                    LOGGER.info('Setting sleep_mode_settings.%s to %s', key[9:], value)
                    self.controller.sleep_mode_settings[key[9:]] = value
            elif self.controller.__getattribute__(key) != value:
                LOGGER.info('Setting %s to %s', key, value)
                self.controller.__setattr__(key, value)
        return web.json_response(config)

    def parse_controls_query(self, query_dict):
        """
        Populate a config dictionary with target values and a dictionary
        of invalid parameters containing their reasons where errors were found
        """
        invalid_parameters = {}
        ctrl = self.controller
        config = {'operation': ctrl.operation,
                  'min_load': ctrl.min_load,
                  'max_load': ctrl.max_load,
                  'wakeup_threshold': ctrl.wakeup_threshold,
                  'sleep_threshold': ctrl.sleep_threshold,
                  'read_interval': ctrl.read_interval,
                  'set_interval': ctrl.set_interval,
                  'ac_sleep_pow': ctrl.sleep_mode_settings['pow'],
                  'ac_sleep_mode': ctrl.sleep_mode_settings['mode'],
                  'ac_sleep_stemp': ctrl.sleep_mode_settings['stemp'],
                  'ac_sleep_shum': ctrl.sleep_mode_settings['shum']}
        for key, value in query_dict:
            if key not in config.keys():
                invalid_parameters[key] = 'invalid key'
            elif key == 'operation':
                parse_onoff(key, value, config, invalid_parameters)
            elif key in ['wakeup_threshold', 'read_interval', 'set_interval']:
                parse_strictly_positive_int(key, value, config, invalid_parameters)
            elif key == 'sleep_threshold':
                parse_positive_int(key, value, config, invalid_parameters)
            elif key in ['min_load', 'max_load']:
                parse_int(key, value, config, invalid_parameters)
            elif key == 'ac_sleep_pow':
                parse_ac_pow(key, value, config, invalid_parameters)
            elif key == 'ac_sleep_mode':
                parse_ac_mode(key, value, config, invalid_parameters)
            elif key == 'ac_sleep_stemp':
                parse_ac_stemp(key, value, config, invalid_parameters)
            elif key == 'ac_sleep_shum':
                parse_ac_shum(key, value, config, invalid_parameters)
        if not config['min_load'] < config['max_load']:
            invalid_parameters['min_load'] = 'min_load must be lower than max_load'
        if not config['sleep_threshold'] < config['wakeup_threshold']:
            invalid_parameters['wakeup_threshold'] = \
                'wakeup_threshold must be greater than sleep_threshold'
        if not config['set_interval'] >= 10:
            invalid_parameters['set_interval'] = 'must be at least 10 seconds'
        return config, invalid_parameters

    async def hello(self, request):
        """Dumb hello welcome handler for server root"""
        return web.Response(text='Hello, Brick Battery here!\n'
                            'Use /status and /controls to have fun')

def to_int(string):
    """Return parsed string as tuple is_valid, int_value"""
    try:
        num = int(string)
        return True, num
    except ValueError:
        return False, 0

def parse_onoff(key, value, config, invalid_parameters):
    """
    Check value from query and assign the key: value pair to config if valid
    or key: error message to invalid_parameters otherwise
    onoff parses lenient value representation and assigns into a boolean
    """

    if value.lower() in ['0', 'off', 'false']:
        config[key] = False
    elif value.lower() in ['1', 'on', 'true']:
        config[key] = True
    else:
        invalid_parameters[key] = 'invalid value must be on/off/0/1/true/false'

def parse_strictly_positive_int(key, value, config, invalid_parameters):
    """Add key with checked value to config or with error message to invalid_parameters"""
    is_num, num = to_int(value)
    if is_num and num > 0:
        config[key] = num
    else:
        invalid_parameters[key] = 'invalid value, must be an integer greater than zero'

def parse_positive_int(key, value, config, invalid_parameters):
    """Add key with checked value to config or with error message to invalid_parameters"""
    is_num, num = to_int(value)
    if is_num and num >= 0:
        config[key] = num
    else:
        invalid_parameters[key] = 'invalid value, must be an integer '\
            'equal to or greater than zero'

def parse_int(key, value, config, invalid_parameters):
    """Add key with checked value to config or with error message to invalid_parameters"""
    is_num, num = to_int(value)
    if is_num:
        config[key] = num
    else:
        invalid_parameters[key] = 'invalid value, must be an integer'

def parse_ac_pow(key, value, config, invalid_parameters):
    """Add key with checked value to config or with error message to invalid_parameters"""
    if value in ['0', '1']:
        config[key] = value
    else:
        invalid_parameters[key] = 'invalid value must be 0 or 1'

def parse_ac_mode(key, value, config, invalid_parameters):
    """Add key with checked value to config or with error message to invalid_parameters"""
    if value in ['', '1', '2', '3', '4', '6']:
        config[key] = value
    else:
        invalid_parameters[key] = 'invalid value must be (empty) for humidify, '\
            '1 for auto, 2 for dry, 3 for cool, 4 for heat, 6 for fan only'

def parse_ac_stemp(key, value, config, invalid_parameters):
    """Add key with checked value to config or with error message to invalid_parameters"""
    is_num, num = to_int(value)
    if is_num and 10 <= num <= 32:
        config[key] = str(num)
    else:
        invalid_parameters[key] = 'invalid value, must be an integer from 10 to 32'

def parse_ac_shum(key, value, config, invalid_parameters):
    """Add key with checked value to config or with error message to invalid_parameters"""
    is_num, num = to_int(value)
    if is_num and 0 <= num <= 100 and num % 5 ==0:
        config[key] = str(num)
    else:
        invalid_parameters[key] = 'invalid value, must be an integer from 0 to 100 '\
            'by increments of 5'

def safe_json(obj):
    """Return a version of obj that is serialisable and valid JSON"""
    if isinstance(obj, list):
        return [safe_json(elem) for elem in obj]
    if isinstance(obj, datetime.datetime):
        return obj.replace(microsecond=0).isoformat()
    if hasattr(obj, 'json_dict') and callable(obj.json_dict):
        # call json_dict() function if it exists
        return obj.json_dict()
    if hasattr(obj, '__dict__'):
        # for any other object we only want explicit attributes
        return safe_json(obj.__dict__)
    if isinstance(obj, dict):
        # hide explicit attributes that start with an underscore
        return {k:safe_json(v) for k, v in obj.items() if not k.startswith('_')}
    if obj is None:
        return ''
    if isinstance(obj, float) and math.isnan(obj):
        return ''
    return obj