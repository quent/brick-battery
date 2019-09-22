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

    def __init__(self, host, port=502, timeout=3):
        """
        Create a Modbus TCP asynchronous client using its own event loop.
        """
        self._host = host
        self._port = port
        # Timeout before giving up requests so that run loop doesn't get hung up forever
        self._request_timeout = timeout
        _, self._async_client = ModbusClient(schedulers.ASYNC_IO,
                                             host=self._host,
                                             port=self._port)
        # Power values in W
        self.grid_import = float('NaN')
        self.phase_import = [float('NaN') for _ in range(3)]
        self.pv_generation = float('NaN')
        # Lifetime values in kWh
        self.lifetime_production = float('NaN')
        self.lifetime_export = float('NaN')
        self.lifetime_import = float('NaN')
        self.phase_import = [float('NaN'), float('NaN'), float('NaN')]
        self.phase_voltage = [float('NaN'), float('NaN'), float('NaN')]

    async def check_se_load(self):
        """
        Send the current power flow prepared request to the Solar inverter and
        return a 2-uple with flow to grid and PV generation, both in watts.
        Return NaN, NaN if the Modbus TCP request failed.
        Assumes a meter is connected to the inverter in 'Export + Import' mode (C_Option).
        """
        try:
            # 40206 Total Real Power (sum of active phases)
            # 40207 Phase A AC Real Power
            # 40208 Phase B AC Real Power
            # 40209 Phase C AC Real Power
            # 40210 AC Real Power Scale Factor
            export_request = asyncio.wait_for(
                self._async_client.protocol.read_holding_registers(
                    address=40206, count=5, unit=1),
                timeout=self._request_timeout)
            # 40083 AC Power value
            # 40084 AC Power scale factor
            pv_generation_request = asyncio.wait_for(
                self._async_client.protocol.read_holding_registers(
                    address=40083, count=2, unit=1),
                timeout=self._request_timeout)
            # 40093 AC Lifetime Energy production in Wh uint32
            # 40095 AC Lifetime Energy production scale factor int16
            pv_lifetime_production_request = asyncio.wait_for(
                self._async_client.protocol.read_holding_registers(
                    address=40093, count=3, unit=1),
                timeout=self._request_timeout)
            # 40226 Total Exported Real Energy in Wh uint32
            # 40228 Phase A Exported Real Energy uint32
            # 40230 Phase B Exported Real Energy uint32
            # 40232 Phase C Exported Real Energy uint32
            # 40234 Total Imported Real Energy in Wh uint32
            # 40236 Phase A Imported Real Energy uint32
            # 40238 Phase B Imported Real Energy uint32
            # 40240 Phase C Imported Real Energy uint32
            # 40242 Real Energy Scale Factor int16
            export_import_lifetime_request = asyncio.wait_for(
                self._async_client.protocol.read_holding_registers(
                    address=40226, count=17, unit=1),
                timeout=self._request_timeout)
            # 40195 Line to Neutral AC Voltage (average of active phases)
            # 40196 Phase A to Neutral AC Voltage
            # 40197 Phase B to Neutral AC Voltage
            # 40198 Phase C to Neutral AC Voltage
            # 40199 Line to Line AC Voltage (average of active phases)
            # 40200 Phase A to Phase B AC Voltage
            # 40201 Phase B to Phase B AC Voltage
            # 40202 Phase C to Phase B AC Voltage
            # 40203 AC Voltage Scale Factor
            phase_voltage_request = asyncio.wait_for(
                self._async_client.protocol.read_holding_registers(
                    address=40195, count=9, unit=1),
                timeout=self._request_timeout)

            # Request everything at the same time
            export_result, pv_generation_result, pv_lifetime_production_result, \
                export_import_lifetime_result, phase_voltage_result = \
                await asyncio.gather(export_request, \
                pv_generation_request, pv_lifetime_production_request, \
                export_import_lifetime_request, phase_voltage_request)

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
            # Get lifetime production unitless then scale factor
            pv_lifetime_production_decoder = BinaryPayloadDecoder.fromRegisters(
                pv_lifetime_production_result.registers, byteorder=Endian.Big)
            self.lifetime_production = pv_lifetime_production_decoder.decode_32bit_uint() \
                                       * 10 ** pv_lifetime_production_decoder.decode_16bit_int() \
                                       / 1000
            # Get lifetime export, then import then scale factor
            export_import_lifetime_decoder = BinaryPayloadDecoder.fromRegisters(
                export_import_lifetime_result.registers, byteorder=Endian.Big)
            export_import_lifetime_data = \
                [export_import_lifetime_decoder.decode_32bit_uint() for _ in range(8)]
            export_import_lifetime_data.append(
                export_import_lifetime_decoder.decode_16bit_int())
            self.lifetime_export = export_import_lifetime_data[0] \
                                   * 10 ** export_import_lifetime_data[8] \
                                   / 1000
            self.lifetime_import = export_import_lifetime_data[4] \
                                   * 10 ** export_import_lifetime_data[8] \
                                   / 1000
            phase_voltage_decoder = BinaryPayloadDecoder.fromRegisters(
                phase_voltage_result.registers, byteorder=Endian.Big)
            phase_voltage_data = [phase_voltage_decoder.decode_16bit_int() for _ in range(9)]
            self.phase_voltage = [phase_voltage_data[n] * 10 ** phase_voltage_data[8]
                                  for n in range(1, 4)]
            LOGGER.debug('Import: %dW, Phase 1: %dW, Phase 2: %dW, Phase 3: %dW, Generation: %dW',
                         self.grid_import, self.phase_import[0], self.phase_import[1],
                         self.phase_import[2], self.pv_generation)
            return self.grid_import, self.pv_generation
        except Exception as ex:
            LOGGER.error('Call to SolarEdge inverter via Modbus TCP interface for current power '
                         'flow failed: %s', ex)
            return float('NaN'), float('NaN')
