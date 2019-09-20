"""
Module for the SolarEdge Modbus TCP interface for LAN connection to the inverted to get
realtime PV generation, load and grid import from the inverter and its attached energy monitor.
"""
import asyncio
import logging
from pymodbus.client.asynchronous.tcp import AsyncModbusTCPClient as ModbusClient
from pymodbus.client.asynchronous import schedulers
from pymodbus.constants import Endian
from pymodbus.payload import BinaryPayloadDecoder

LOGGER = logging.getLogger(__name__)

class SolarInfo:
    """
    The SolarInfo class manages a persistent Modbus TCP connection to the
    Solaredge inverter and allows to retrieve data asynchronously.

    All you need to get started is to configure your inverter to listen on the Modbus TCP port
    502 by default.
    """

    def __init__(self, host, port=502):
        """
        Create a Modbus TCP asynchronous client using its own event loop.
        """
        self._host = host
        self._port = port
        _, self._async_client = ModbusClient(schedulers.ASYNC_IO,
                                             host=self._host,
                                             port=self._port)
        self.grid_import = float('NaN')
        self.phase_import = [float('NaN') for _ in range(3)]
        self.pv_generation = float('NaN')

    async def check_se_load(self):
        """
        Send the current power flow prepared request to the Solar inverter and
        return a 2-uple with flow to grid and PV generation, both in watts.
        Return NaN, NaN if the Modbus TCP request failed.
        """
        try:
            # 40206 Total Real Power (sum of active phases)
            # 40207 Phase A AC Real Power
            # 40208 Phase B AC Real Power
            # 40209 Phase C AC Real Power
            # 40210 AC Real Power Scale Factor
            export_request = self._async_client.protocol.read_holding_registers(
                address=40206, count=5, unit=1)
            # 40083 AC Power value
            # 40084 AC Power scale factor
            pv_generation_request = self._async_client.protocol.read_holding_registers(
                address=40083, count=2, unit=1)
            export_result, pv_generation_result = \
                await asyncio.gather(export_request, pv_generation_request)
            # Get total Grid export, then Ph1, Ph2 and Ph3 export,
            # then scale factor (base-10 multiplier) for all of them
            export_decoder = BinaryPayloadDecoder.fromRegisters(
                export_result.registers, byteorder=Endian.Big)
            export_data = [export_decoder.decode_16bit_int() for _ in range(5)]
            (self.grid_import, self.phase_import[0],
             self.phase_import[1], self.phase_import[2]) = \
                [-export_data[n] * 10 ** export_data[4] for n in range(4)]
            # Get PV AC Power value unitless, then scale factor
            pv_generation_decoder = BinaryPayloadDecoder.fromRegisters(
                pv_generation_result.registers, byteorder=Endian.Big)
            self.pv_generation = pv_generation_decoder.decode_16bit_int() \
                                 * 10 ** pv_generation_decoder.decode_16bit_int()
            LOGGER.debug('Import: %dW, Phase 1: %dW, Phase 2: %dW, Phase 3: %dW, Generation: %dW',
                         self.grid_import, self.phase_import[0], self.phase_import[1],
                         self.phase_import[2], self.pv_generation)
            return self.grid_import, self.pv_generation
        except Exception as ex:
            LOGGER.error('Call to SolarEdge inverter via Modbus TCP interface for current power '
                         'flow failed: %s', ex)
            return float('NaN'), float('NaN')
