#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
LabExT  Copyright (C) 2021  ETH Zurich and Polariton Technologies AG
This program is free software and comes with ABSOLUTELY NO WARRANTY; for details see LICENSE file.
"""

from LabExT.Instruments.InstrumentAPI import Instrument, InstrumentException
#from LabExT.Instruments.LabJack import LabJack

import threading


class PowerMeterKeithley_2(Instrument):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._net_timeout_ms = kwargs.get("net_timeout_ms", 10000)
        self._net_chunk_size = kwargs.get("net_chunk_size_B", 1024)
        self._always_returns_sweep_in_Watt = kwargs.get("always_returns_sweep_in_Watt", True)

        # instrument parameter on network, add to this list all object properties which should get freshly fetched
        # and added to self.instrument_paramters on each get_instrument_parameter() call
        self.networked_instrument_properties.extend([
            'wavelength',
            'unit',
            'range',
            'autoranging',
            'averagetime'
        ])



    @Instrument._open.getter  # weird way to override the parent's class property getter
    def _open(self):
        return self._inst

    def open(self):
        print("Trying to open:")
        super().open()
        print("haha opened!")
        #:SOURce1:CLEar:AUTO OFF
        self._inst.timeout = self._net_timeout_ms
        self._inst.chunk_size = self._net_chunk_size
    
    def get_vcal(self,wavelength):
        A = -0.8541
        B = 2.6141
        C = 1.5068
        return A*wavelength**2 + B*wavelength + C

    def voltage_to_dBm(self, voltage):
        '''
        Use this calibration for the Newport detectors
        '''
        power_dBm = 20 * (float(voltage)-self.get_vcal(self.wavelength))
        # return voltage
        return power_dBm

    def fetch_power(self):
        print("trying to read")
        r = self.query(':READ?').strip()
        r_float_list = [float(idx) for idx in r.split(",")]
        current_val = r_float_list[1]
        print(r)
        print(type(r))
        # print(r_float_list)
        print("we read bois")
        return current_val
    
    def get_instrument_parameter(self):
        return {'idn': self.idn()}
    
    def idn(self):
        return f"LabJack PD {3}"
    
    def trigger(self, continuous=False):
        return
    
    @Instrument.thread_lock.getter  # weird way to override the parent's class property getter
    def thread_lock(self):
        return threading.Lock()

    def clear(self):
        return None

    def reset(self):
        return None

    def ready_check_sync(self):
        return True

    def ready_check_async_setup(self):
        return None

    def ready_check_async(self):
        return True

    def check_instrument_errors(self):
        return None

    def command(self, *args, **kwargs):
        return None

    def command_channel(self, *args, **kwargs):
        return None

    def request(self, *args, **kwargs):
        return None

    def request_channel(self, *args, **kwargs):
        return None

    def query(self, *args, **kwargs):
        return None

    def query_channel(self, *args, **kwargs):
        return None

    def write(self, *args):
        return None

    def write_channel(self, *args, **kwargs):
        return None

    def query_raw_bytes(self, *args, **kwargs):
        return None
