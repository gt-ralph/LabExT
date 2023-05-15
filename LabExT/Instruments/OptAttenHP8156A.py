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


class OptAttenHP8156A(Instrument):
    """
    ## OptAttenHP8156A

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
        

        self.networked_instrument_properties.extend([
            'atten',
            'output',
            'wavelength',          
        ])

    def open(self):
        """
        Open the connection to the instrument. Automatically re-uses any old connection if it is already open.

        :return: None
        """
        super().open()
        #self._inst.read_termination = '\n'

        authentication = self._inst.query('OUTPUT?')
        print(int(authentication.strip()))

        if int(authentication.strip()) != 0 and int(authentication.strip()) != 1:
            raise InstrumentException(f'Authentication failed, device returned this thing:{authentication}')
    
    def close(self):
        """
            close the attenuator safely, returning its outputs to 0
            and deactivating output
        """
        self.command("*RST")
        super().close()

#
# get/set properties
#

    @property
    def atten(self):
        """
        Returns the current attenuation
        :return: attenuation in dB
        """

        return float(self.request(f'INPut:ATTenuation?'))
    
    @atten.setter
    def atten(self, value):
        """
        Sets the attenuation
        :input: (double) value: attenuation in dB
        :return: none
        """
        self.command(f"INPut:Attenuation {value}")

    @property
    def wavelength(self):
        """
        returns wavelength in meters
        """
        return float(self.request(f'INPut:WAVelength?'))
    
    @wavelength.setter
    def wavelength(self, value):
        """
        sets wavelength in meters
        """
        self.command(f"INPut:WAVelength {float(value)}")

    @property
    def output(self):
        """
        Returns the outuput status of the instrument
        :return: 1 represnting on, 0 representing false
        """

        return int(self.request(f':OUTPut?'))
    
    @output.setter
    def output(self, value):
        """
        sets the output
        :input: 1 to turn it on, 0 to turn it off (AND NOTHING ELSE)
        :return: none
        """
        float(self.request(f':OUTPut {int(value)}'))



