"""
Module for the SolarEdge Modbus TCP interface for LAN connection to the inverted to get
realtime PV generation, load and grid import from the inverter and its attached energy monitor.
"""
import asyncio
import logging
from pymodbus.client import AsyncModbusTcpClient
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
        # Timeout is the number of seconds before giving up a connection and
        # a request so that the run loop doesn't get hung up forever
        self._async_client = AsyncModbusTcpClient(host=host, port=port, timeout=timeout)

        # Power values in W
        self.grid_import = float('NaN')
        self.pv_generation = float('NaN')

        # Import power in W per phase
        self.phase_import = [float('NaN') for _ in range(3)]

        # Voltage per phase in V
        self.phase_voltage = [float('NaN'), float('NaN'), float('NaN')]

        # Lifetime values in kWh
        self.lifetime_production = float('NaN')
        self.lifetime_export = float('NaN')
        self.lifetime_import = float('NaN')

    async def check_se_load(self):
        """
        Send the current power flow prepared request to the Solar inverter and
        return a 2-uple with flow to grid and PV generation, both in watts.
        Return NaN, NaN if the Modbus TCP request failed.
        Assumes a meter is connected to the inverter in 'Export + Import' mode (C_Option).
        """
        try:
            if not self._async_client.connected:
                await self._async_client.connect()

            # The maximum amount of holding registers that can be read at once
            # using Modbus over TCP seems to be 123 (to fit in a 256 byte response).
            # Request the range of needed registers split between inverter and meter registers

            # https://knowledge-center.solaredge.com/sites/kc/files/sunspec-implementation-technical-note.pdf

            # Inverter registers range: 40000 -> 40109
            inverter_request = self._async_client.read_holding_registers(
                    address=40083, count=40096 - 40083)

            # Meter 1 registers range:  40121 -> 40295
            meter_request = self._async_client.read_holding_registers(
                    address=40195, count=40243 - 40195)

            inverter_data, meter_data = await asyncio.gather(inverter_request, meter_request)

            inverter_decoder, meter_decoder = [BinaryPayloadDecoder.fromRegisters(
                data.registers, byteorder=Endian.BIG) for data in [inverter_data, meter_data]]

            # 40083 AC Power value int16
            # 40084 AC Power scale factor int16
            self.pv_generation = inverter_decoder.decode_16bit_int() \
                                 * 10 ** inverter_decoder.decode_16bit_int()

            # 40085 -> 40093
            inverter_decoder.skip_bytes((40093 - 40085) * 2)

            # 40093 AC Lifetime Energy production in Wh uint32
            # 40095 AC Lifetime Energy production scale factor int16
            self.lifetime_production = inverter_decoder.decode_32bit_uint() \
                                       * 10 ** inverter_decoder.decode_16bit_int() \
                                       / 1000

            # 40195 Line to Neutral AC Voltage (average of active phases) int16
            # 40196 Phase A to Neutral AC Voltage int16
            # 40197 Phase B to Neutral AC Voltage int16
            # 40198 Phase C to Neutral AC Voltage int16
            # 40199 Line to Line AC Voltage (average of active phases) int16
            # 40200 Phase A to Phase B AC Voltage int16
            # 40201 Phase B to Phase B AC Voltage int16
            # 40202 Phase C to Phase B AC Voltage int16
            # 40203 AC Voltage Scale Factor (base-10 multiplier) for all int16
            phase_voltage_data = [meter_decoder.decode_16bit_int() for _ in range(9)]

            # Store only line to phase (skip phase to phase)
            self.phase_voltage = [phase_voltage_data[n] * 10 ** phase_voltage_data[8]
                                  for n in range(1, 4)]

            # 40204 -> 40206
            meter_decoder.skip_bytes((40206 - 40204) * 2)

            # 40206 Total Real Power (sum of active phases) int16
            # 40207 Phase A AC Real Power int16
            # 40208 Phase B AC Real Power int16
            # 40209 Phase C AC Real Power int16
            # 40210 AC Real Power Scale Factor base-10 multiplier for all int16
            export_data = [meter_decoder.decode_16bit_int() for _ in range(5)]

            (self.grid_import, self.phase_import[0],
             self.phase_import[1], self.phase_import[2]) = \
                [-export_data[n] * 10 ** export_data[4] for n in range(4)]

            # 40211 -> 40226
            meter_decoder.skip_bytes((40226 - 40211) * 2)

            # 40226 Total Exported Real Energy in Wh uint32
            # 40228 Phase A Exported Real Energy uint32
            # 40230 Phase B Exported Real Energy uint32
            # 40232 Phase C Exported Real Energy uint32
            # 40234 Total Imported Real Energy in Wh uint32
            # 40236 Phase A Imported Real Energy uint32
            # 40238 Phase B Imported Real Energy uint32
            # 40240 Phase C Imported Real Energy uint32
            # 40242 Real Energy Scale Factor int16
            export_import_lifetime_data = \
                [meter_decoder.decode_32bit_uint() for _ in range(8)]

            export_import_lifetime_data.append(
                meter_decoder.decode_16bit_int())

            self.lifetime_export = export_import_lifetime_data[0] \
                                   * 10 ** export_import_lifetime_data[8] \
                                   / 1000

            self.lifetime_import = export_import_lifetime_data[4] \
                                   * 10 ** export_import_lifetime_data[8] \
                                   / 1000

            LOGGER.debug('Import: %dW, Phase 1: %dW, Phase 2: %dW, Phase 3: %dW, Generation: %dW',
                         self.grid_import, self.phase_import[0], self.phase_import[1],
                         self.phase_import[2], self.pv_generation)

            return self.grid_import, self.pv_generation

        except Exception as ex:
            LOGGER.error('Call to SolarEdge inverter via Modbus TCP interface for current power '
                         'flow failed: %s', ex)
            self._async_client.close()

            return float('NaN'), float('NaN')
