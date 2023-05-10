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
import oscope_scpi
from oscope_scpi import Oscilloscope

from os import environ


class UXR(Instrument):
    """
    ## UXR


    This class provides an interface to a a Keysight UXR. See the following two links for
    the user manual, which contains programming information:

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
        

        # self.networked_instrument_properties.extend([
        #     'atten',
        #     'output'          
        # ])

    def open(self):
        """
        Open the connection to the instrument. Automatically re-uses any old connection if it is already open.

        :return: None
        """
        resource = environ.get('OSCOPE_IP',self._address)
        self._inst = oscope_scpi.UXR(resource,4,0)
        self._inst.open()

        #Go with no authentication for now
        #TODO: add actual authentication
        # authentication = self._inst.query('OUTPUT?')
        # print(int(authentication.strip()))

        # if int(authentication.strip()) != 0 and int(authentication.strip()) != 1:
        #     raise InstrumentException(f'Authentication failed, device returned this thing:{authentication}')
    
    def close(self):
        """
            close the oscope safely
        """
        self._inst.reset()
        self._inst.close()

    #perform a single capture
    def single(self):
        self._inst.modeSingle()

    #return all four waveforms in a kinda gross array
    def get_all_waveforms(self):
        waveform_list = []
        for i in range(1,5):
            waveform_list.append(self._inst.waveformData(channel=i))
        return waveform_list
    
    #return one waveform
    def get_waveform(self, channel_num):
        return self._inst.waveformData(channel=channel_num)




#
# get/set properties
#

    # @property
    # def atten(self):
    #     """
    #     Returns the current attenuation
    #     :return: attenuation in dB
    #     """

    #     return float(self.request(f'INPut:ATTenuation?'))
    
    # @atten.setter
    # def atten(self, value):
    #     """
    #     Sets the attenuation
    #     :input: (double) value: attenuation in dB
    #     :return: none
    #     """
    #     self.command(f"INPut:Attenuation {value}")



