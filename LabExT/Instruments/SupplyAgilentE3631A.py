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


class SupplyAgilentE3631A(Instrument):
    """
    ## SupplyAgilentE3631A

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
        if(self.channel == 0):
            self.chanstring = 'P6V' #positive 6 v supply channel
        elif(self.channel == 1):
            self.chanstring = "P25V" #positive 25V supply channel
        elif(self.channel == 2):
            self.chanstring = "N25V"
        elif(self.channel == None):
            self.chanstring = ""
        else:
            raise InstrumentException(f'Provided channel {self.channel} is not valid, must be 0, 1, or 2 corresponding to device channels P6V, P25V, or N25V')

        self._net_timeout_ms = kwargs.get("net_timeout_ms", 30000)

        self.networked_instrument_properties.extend([
            'voltage',
            'current'          
        ])

    def open(self):
        """
        Open the connection to the instrument. Automatically re-uses any old connection if it is already open.

        :return: None
        """
        super().open()
        self._inst.read_termination = '\n'

        authentication = self._inst.query('OUTPUT:STATE?')

        if authentication.strip() == '0':
            self.command("OUTPUT:STATE ON")
            authentication_post_init = self._inst.query('OUTPUT:STATE?')
            if authentication_post_init.strip() == '0':
                raise InstrumentException('Authentication failed, device did not enable otuput but returned 0')
        elif authentication.strip() != '1':
            raise InstrumentException(f'Authentication failed, device returned this thing:{authentication}')
    
    def close(self):
        """
            close the power supply safely, returning its outputs to 0
            and deactivating output
        """
        self.command("*RST")
        self.command("OUTPUT:STATE OFF")
        super().close()



    #
    # sets voltage on the port specified by the channel
    #

    def set_voltage(self, voltage = 0, current=1 ):
        """
        Sets the voltage of the specified channel
        Valid channels: P6V, P25V, N25V
        :return: none
        """
        self.command(f'APPL {self.chanstring},{voltage},{current}')
        return 

 
#
# get/set properties
#

    @property
    def current(self):
        """
        Returns the voltage of the P6V channel
        :return: voltage in volts
        """

        return float(self.request(f'MEAS:CURR? {self.chanstring}'))
    
    def get_current(self):
        """
        Returns the voltage of the P6V channel
        :return: current in amps
        """

        return self.current

    @property
    def voltage(self):
        """
        Returns the voltage of the P6V channel
        :return: voltage in volts
        """

        return float(self.request(f'MEAS:VOLT? {self.chanstring}'))
    
    def get_voltage(self):
        """
        Returns the voltage of the P6V channel
        :return: current in amps
        """

        return self.voltage




