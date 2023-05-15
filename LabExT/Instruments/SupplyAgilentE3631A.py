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
    the user manual, which contains programming information:


    * [User Manual](    * [User Manual](https://www.keysight.com/us/en/product/83640L/synthesized-sweptcw-generator-10-mhz-to-40-ghz.html#resources)
    
    

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

    
    #welp spent an hour debuggin why the args are not making it
    #into th einit and couldn't figure it out. Kind of acts like the 
    #args are simply not there. Kind of weird.
    #manually set limits to what I needed at the time
    def __init__(self, *args, **kwargs):
        # print("printing args (from supply):")
        # print(args)
        # print("printing kwargs (from supply):")
        # print(kwargs)
        super().__init__(*args, **kwargs)
        # print("printing args (from supply, after super init):")
        # print(args)
        # print("printing kwargs (from supply, after super init):")
        # print(kwargs)
        # print("This is the self._kwargs:")
        #print(self._kwargs)
        # call Instrument constructor, creates VISA instrument
        print(self._kwargs)
        if(self.channel == 0):
            self.chanstring = 'P6V' #positive 6 v supply channel
            self._voltage_lim = float(self._kwargs.get("voltage_lim_0", 1.6))
            # print("This is the voltage limit:")
            # print(self._voltage_lim)
        elif(self.channel == 1):
            self.chanstring = "P25V" #positive 25V supply channel
            self._voltage_lim = float(self._kwargs.get("voltage_lim_1", 1.6))
            # print("This is the voltage limit:")
            # print(self._voltage_lim)
        elif(self.channel == 2):
            self.chanstring = "N25V"
            self._voltage_lim = float(self._kwargs.get("voltage_lim_2", -25))
            # print("This is the voltage limit:")
            # print(self._voltage_lim)
        elif(self.channel == None):
            self.chanstring = ""
        else:
            raise InstrumentException(f'Provided channel {self.channel} is not valid, must be 0, 1, or 2 corresponding to device channels P6V, P25V, or N25V')

        #probably need to add a condtional for negative voltages
        self._current_lim = self._kwargs.get("current_lim", 0.08)
        # print("This is the current limit")
        # print(self._current_lim)

        #internal voltage variable
        self._voltage = 0
        

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
        # print("about to command")
        # print("using the following current limit:")
        # print(self._current_lim)
        self.command(f'APPL {self.chanstring}, {0}, {self._current_lim}')
        # print("command succeeded")
    
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

    # def set_voltage(self, voltage = 0, current=None ):
    #     """
    #     Sets the voltage of the specified channel
    #     Note the APPL command technically permits no current
    #     as an input argument (it'll use the single number as a voltage)
    #     Valid channels: P6V, P25V, N25V
    #     :return: none
    #     """
    #     #if user didn't specify the current limit, set it to the default
    #     if current == None:
    #         current = self._current_lim
        
    #     #if one of the positive channels
    #     if ((self.channel == 0 or self.channel == 1) and (voltage <= self._voltage_lim and voltage >= 0)):
    #         self.command(f'APPL {self.chanstring}, {voltage}, {current}')
    #         return True
    #     #if the negative channel
    #     elif ((self.channel == 2) and (voltage >= self._voltage_lim and voltage <= 0)):
    #         self.command(f'APPL {self.chanstring}, {voltage}, {current}')
    #         return True
    #     #if ya done goofed
    #     else:
    #         raise InstrumentException(f'voltage exceeded safe limit. It was: {voltage}')

 
#
# get/set properties
#

    @property
    def current(self):
        """
        Returns the voltage of the P6V channel
        Don't need to save the actual current draw as a 
        variable (yet) so I won't program it in
        :return: current in amps
        """

        return float(self.request(f'MEAS:CURR? {self.chanstring}'))
    

    @property
    def voltage(self):
        """
        Returns the voltage of the P6V channel
        :return: voltage in volts
        """
        self._voltage = float(self.request(f'MEAS:VOLT? {self.chanstring}'))
        return self._voltage
    
    @voltage.setter
    def voltage(self, voltage):
        """
        Sets the voltage of the specified channel
        Note the APPL command technically permits no current
        as an input argument (it'll use the single number as a voltage)
        I do use it, but just to set it to the same current limit again.
        Valid channels: P6V, P25V, N25V
        :return: none
        """
        
        current = self._current_lim
        # print("This is the current limit I found:")
        # print(self._current_lim)
        
        #if one of the positive channels
        if ((self.channel == 0 or self.channel == 1) and (voltage <= self._voltage_lim and voltage >= 0)):
            self.command(f'APPL {self.chanstring}, {voltage}, {current}')
            self._voltage = voltage
            return True
        #if the negative channel
        elif ((self.channel == 2) and (voltage >= self._voltage_lim and voltage <= 0)):
            self.command(f'APPL {self.chanstring}, {voltage}, {current}')
            self._voltage = voltage
            return True
        #if ya done goofed
        else:
            raise InstrumentException(f'voltage exceeded safe limit. It was: {voltage}')
        


    @property
    def output(self):
        """
        Returns the output status of the instrument
        :return: 1 representing on, 0 representing false
        """

        return int(self.request(f':OUTPut?'))

#TODO: add an output.setter function eg f':OUTPut 1' but it would be untested so I'll leave it unimplemented
    



