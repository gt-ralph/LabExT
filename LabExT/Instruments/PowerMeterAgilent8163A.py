#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
LabExT  Copyright (C) 2021  ETH Zurich and Polariton Technologies AG
This program is free software and comes with ABSOLUTELY NO WARRANTY; for details see LICENSE file.
"""

from LabExT.Instruments.InstrumentAPI import Instrument, InstrumentException
from LabExT.Instruments.LabJack import LabJack

import threading


class PowerMeterAgilent8163A(Instrument):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)


        # instrument parameter on network, add to this list all object properties which should get freshly fetched
        # and added to self.instrument_paramters on each get_instrument_parameter() call
        self.networked_instrument_properties.extend([
            'power'
        ])


    @Instrument._open.getter  # weird way to override the parent's class property getter
    def _open(self):
        return self.lj

    def open(self):
        super().open()

    def fetch_power(self):
        return self.power
    
    @property
    def power(self):
        return self.request("FETCH1:Chan1:POW?")

    

