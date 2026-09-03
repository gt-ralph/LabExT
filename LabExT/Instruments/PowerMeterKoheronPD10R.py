#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
LabExT  Copyright (C) 2021  ETH Zurich and Polariton Technologies AG
This program is free software and comes with ABSOLUTELY NO WARRANTY; for details see LICENSE file.
"""

from LabExT.Instruments.InstrumentAPI import Instrument, InstrumentException
from LabExT.Instruments.LabJack import LabJack

import threading


# Every Koheron photodiode hangs off the one LabJack, differing only in which AIN it reads.
# Opening a handle per power meter per measurement left a growing pile of connections to the
# same device - nothing ever closed them - and concurrent connections to one device collide
# on Modbus transaction ids, which surfaces as LJME_TRANSACTION_ID_ERR partway through a run
# rather than at the start. One shared connection for the process instead.
_shared_labjack = None
_shared_labjack_lock = threading.Lock()


def get_shared_labjack() -> LabJack:
    """Returns the process-wide LabJack, opening it on first use."""
    global _shared_labjack
    with _shared_labjack_lock:
        if _shared_labjack is None:
            _shared_labjack = LabJack()
        return _shared_labjack


class PowerMeterKoheronPD10R(Instrument):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.lj = None
        self.lj_port = self._kwargs.get("lj_port", None)


    @Instrument._open.getter  # weird way to override the parent's class property getter
    def _open(self):
        return self.lj

    def open(self):
        self.lj = get_shared_labjack()

    def close(self):
        # the LabJack is shared with every other power meter, so one of them finishing is
        # not a reason to close it. It stays open for the life of the process.
        self.lj = None

    def voltage_to_dBm(self, voltage):
        '''
        Use this calibration for the Koheron detectors
        '''
        zero_dbm = 2.11
        volts_per_decade = 0.3
        power_dBm = (voltage - zero_dbm) / (volts_per_decade*0.1)
        return power_dBm
    
    @property
    def power(self):
        return self.voltage_to_dBm(self.lj.read_from_port(self.lj_port))

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

    def check_instrument_errors(self, context=None):
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
    
    def logging_stop(self):
        return None
