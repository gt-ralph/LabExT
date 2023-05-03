#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
LabExT  Copyright (C) 2021  ETH Zurich and Polariton Technologies AG
This program is free software and comes with ABSOLUTELY NO WARRANTY; for details see LICENSE file.
"""

import numpy as np
import time

from LabExT.Instruments.InstrumentAPI import Instrument, InstrumentException


class SigGenAgilent83640L(Instrument):
    """
    ## SupplyAgilentE3631A

    This class provides an interface to an agilent E3631A. See the following two links for
    the user manual, which contains porgamming information:

    * [User Manual](https://www.keysight.com/us/en/product/83640L/synthesized-sweptcw-generator-10-mhz-to-40-ghz.html#resources)
    

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

        # self.networked_instrument_properties.extend([
        #     'voltage',
        #     'current'          
        # ])

    def open(self):
        """
        Open the connection to the instrument. Automatically re-uses any old connection if it is already open.

        :return: None
        """
        super().open()
        self._inst.timeout = 25000
        # self._inst.read_termination = '\n'
        # self._inst.write_termination = '\n'

        authentication = self._inst.query('POWER:STATE?')
        print(authentication)

        if authentication.strip() == '0':
            self.command("POWER:STATE 1")
            time.sleep(0.2)
            authentication_post_init = self._inst.query('POWER:STATE?')
            print(authentication_post_init)
            if authentication_post_init.strip() == '0':
                raise InstrumentException('Authentication failed, device did not enable otuput but returned 0')
        elif authentication.strip() != '1':
            raise InstrumentException(f'Authentication failed, device returned this thing:{authentication}')
        time.sleep(0.2)


    def close(self):
        """
            close the power supply safely, returning its outputs to 0
            and deactivating output
        """
        self.command("*RST")
        super().close()


#these should eventually be get/set properties but it's fine
#for now


    #
    # sets power on the port 
    # might need to specify the unit
    #

    def set_power(self, power = 0 ):
        """
        Sets the power
        :return: none
        """
        self.command(f'POW:LEV {power}')
        return 
    
    #
    # sets frequency on the port specified by the channel
    # in MHz
    #

    def set_freq(self, freq = 0 ):
        """
        Sets the frequency
        :return: none
        """
        self.command(f'FREQ:CW {freq} MHz')
        return 
    
    #
    # turns the output on or off
    # could also do "ON" or "OFF" instead of 1 or 0
    #
    def set_output(self, state = 0):
        """
        Sets the output state
        :return: none
        """
        self.command(f'POWER:STATE {state}')
        return
    


 
#
# get/set properties
#

    # @property
    # def current(self):
    #     """
    #     Returns the voltage of the P6V channel
    #     :return: voltage in volts
    #     """

    #     return float(self.request(f'MEAS:CURR? {self.chanstring}'))
    
    # def get_current(self):
    #     """
    #     Returns the voltage of the P6V channel
    #     :return: current in amps
    #     """

    #     return self.current

    # @property
    # def voltage(self):
    #     """
    #     Returns the voltage of the P6V channel
    #     :return: voltage in volts
    #     """

    #     return float(self.request(f'MEAS:VOLT? {self.chanstring}'))
    
    # def get_voltage(self):
    #     """
    #     Returns the voltage of the P6V channel
    #     :return: current in amps
    #     """

    #     return self.voltage

