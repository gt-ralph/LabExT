#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
LabExT  Copyright (C) 2021  ETH Zurich and Polariton Technologies AG
This program is free software and comes with ABSOLUTELY NO WARRANTY; for details see LICENSE file.
"""

from LabExT.Instruments.InstrumentAPI import InstrumentException
from LabExT.Instruments.LabJack import LabJack


class PowerMeterKoheronPD10R(LabJack):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs) 

    def open(self):
        super().open()

    def voltage_to_dBm(self, voltage):
        '''
        Use this calibration for the Koheron detectors
        '''
        zero_dbm = 2.11
        volts_per_decade = 0.3
        power_dBm = (voltage - zero_dbm) / (volts_per_decade*0.1)
        return power_dBm

    def fetch_power(self, port="AIN0"):
        return self.voltage_to_dBm(super().read_from_port(port))
