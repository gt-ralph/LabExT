#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
LabExT  Copyright (C) 2021  ETH Zurich and Polariton Technologies AG
This program is free software and comes with ABSOLUTELY NO WARRANTY; for details see LICENSE file.
"""

import threading

import time

import numpy as np

from LabExT.Instruments.InstrumentAPI import Instrument, InstrumentException


class FSUESA(Instrument):
    """
    ## FSUESA

    This class provides an interface to an agilent E3631A. See the following two links for
    the user manual, which contains porgamming information:

    * [User Manual](https://www.keysight.com/us/en/assets/9018-01308/user-manuals/9018-01308.pdf?success=true)
    

    #### Properties

    handbook page refers to: Yokogawa AQ6370C Remote Control Manual (IMAQ6370C-17EN.pdf)

    | property type    | datatype | read/write | page in handbook | unit | description                                                       |
    |------------------|----------|------------|------------------|------|-------------------------------------------------------------------|
    | startwavelength  | float    | rw         | 7-88             | nm   | Sets/queries the measurement start wavelength.                    |

    The sensitivity modes is any of: 'NHLD', 'NAUT', 'MID', 'HIGH1', 'HIGH2', 'HIGH3', 'NORM'.

    #### Methods
    * **run**: triggers a new measurement and waits until the sweep is over
    * **stop**: stops sweeping
    * **get_data**: downloads the wavelength and power data of the last measurement

    """

    

    def __init__(self, *args, **kwargs):
        # call Instrument constructor, creates VISA instrument
        super().__init__(*args, **kwargs)

        self._net_timeout_ms = kwargs.get("net_timeout_ms", 30000)


    def open(self):
        """
        Open the connection to the instrument. Automatically re-uses any old connection if it is already open.

        :return: None
        """
        super().open()
        print("about to query this boi")
        test = self._inst.query("*IDN?")
        print(test)
        self._inst.read_termination = '\n'

    
    def close(self):
        """
            close the power supply safely, returning its outputs to 0
            and deactivating output
        """
        time.sleep(0.25)
        self.command("*RST")
        time.sleep(0.25)
        super().close()

    def set_initial_settings(self):
        time.sleep(0.25)
        self.command("SYSTEM:DISPLAY:UPDATE ON")
        time.sleep(0.25)
        self.command("*RST")
        time.sleep(0.25)
        self.command("INPut1:ATTenuation 0")
        time.sleep(0.25)

        self.command("DISP:WIND:TRAC:Y:RLEV -20dBm")
        time.sleep(0.25)
        self.command("BAND 100Hz")
        time.sleep(0.25)
        self.command("BAND:VID 500Hz")
        time.sleep(0.25)
        self.command("DET RMS")
        time.sleep(0.25)
        self.command("INIT:CONT OFF")

    #
    # sets voltage on the specified port
    #

    def set_frequency_band(self, center,bandwidth):
        left = center - bandwidth
        right = center + bandwidth
        self.command(f"SENSE:FREQ:START {left}")
        self.command(f"SENSE:FREQ:STOP {right}")
        return

    def get_trace(self):
        self.command("INIT;*WAI")
        response = self._inst.query("TRAC? TRACE1")
        data = np.fromstring(response,dtype=float, sep=",")
        return data
 




