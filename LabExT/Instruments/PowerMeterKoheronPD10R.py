#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
LabExT  Copyright (C) 2021  ETH Zurich and Polariton Technologies AG
This program is free software and comes with ABSOLUTELY NO WARRANTY; for details see LICENSE file.
"""

from LabExT.Instruments.InstrumentAPI import Instrument, InstrumentException
from LabExT.Instruments.LabJack import LabJack

import threading


class PowerMeterKoheronPD10R(Instrument):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.lj = None
        self.lj_port = self._kwargs.get("lj_port", None)


    @Instrument._open.getter  # weird way to override the parent's class property getter
    def _open(self):
        return self.lj

    def open(self):
        self.lj = LabJack()

    def voltage_to_dBm(self, voltage):
        '''
        Use this calibration for the Koheron detectors
        '''
        zero_dbm = 2.11
        volts_per_decade = 0.3
        power_dBm = (voltage - zero_dbm) / (volts_per_decade*0.1)
        return power_dBm

    def fetch_power(self):
        return self.voltage_to_dBm(self.lj.read_from_port(self.lj_port))
    
    def get_instrument_parameter(self):
        return {'idn': self.idn()}
    
    def idn(self):
        return f"LabJack PD {self.lj}"
    
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
        return ""

    def request_channel(self, *args, **kwargs):
        return ""

    def query(self, *args, **kwargs):
        return ""

    def query_channel(self, *args, **kwargs):
        return ""

    def write(self, *args):
        return None

    def write_channel(self, *args, **kwargs):
        return None

    def query_raw_bytes(self, *args, **kwargs):
        return None
