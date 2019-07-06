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
    def __init__(self, port):
        """
        Args:
        port the port number to bind the listening socket to
        """
        self.port = port
        app = web.Application()
        app.add_routes([web.get('/', self.hello),
                        web.get('/status', self.status)])
        self.runner = web.AppRunner(app)

    def register_controller(self, controller):
        """
        When created, the controller should register itself with the server
        instance
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
        site = web.TCPSite(self.runner, 'localhost', self.port)
        await site.start()

    async def status(self, request):
        """Pass all details about current state"""
        ctrl = self.controller
        json = {'dry_run': ctrl.dryrun,
                'last_updated': ctrl.last_updated,
                'read_interval': ctrl.read_interval,
                'min_load': ctrl.min_load,
                'max_load': ctrl.max_load,
                'is_sleep_mode': ctrl.is_sleep_mode,
                'sleep_mode_settings': ctrl.sleep_mode_settings,
                'wakeup_threshold': ctrl.wakeup_threshold,
                'aircons': ctrl.ac,
                'ac_consumption': ctrl.ac_consumption,
                'solar': ctrl.solar,
                'set_interval': ctrl.set_interval,
                'next_set': ctrl.next_set if not ctrl.is_sleep_mode else '-'}
        return web.json_response(safe_json(json))

    async def hello(self, request):
        """Dumb hello welcome handler for server root"""
        return web.Response(text="Hello Brick Battery here!")
 
def safe_json(obj):
    """Return a version of obj that is serialisable and valid JSON"""
    if isinstance(obj, list):
        return [safe_json(elem) for elem in obj]
    elif isinstance(obj, datetime.datetime):
        return obj.isoformat()
    elif hasattr(obj, 'json_dict') and callable(obj.json_dict):
        # call json_dict() function if it exists
        return obj.json_dict()
    elif hasattr(obj, '__dict__'):
        # for any other object we only want explicit attributes
        return safe_json(obj.__dict__)
    elif isinstance(obj, dict):
        # hide explicit attributes that start with an underscore
        return {k:safe_json(v) for k,v in obj.items() if not k.startswith('_')}
    elif obj is None:
        return ''
    elif isinstance(obj, float) and math.isnan(obj):
        return ''
    else:
        return obj
