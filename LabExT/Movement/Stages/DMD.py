import pyvisa as visa
from LabExT.Movement.config import Axis
from LabExT.Movement.Stage import Stage, StageError, assert_stage_connected, assert_driver_loaded
from LabExT.Utils import get_configuration_file_path, try_to_lift_window
from LabExT.View.Controls.DriverPathDialog import DriverPathDialog

#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
LabExT  Copyright (C) 2022  ETH Zurich and Polariton Technologies AG
This program is free software and comes with ABSOLUTELY NO WARRANTY; for details see LICENSE file.
"""

from LabExT.Movement.Stage import Stage

"""
Addresses:
1. motors - 10 - newport esp300
2. multimeter - 21 - agilent 34401A lab multimeter (hp)
3. attenuator - 27 - exfoFVA-3100
"""
class DMD(Stage):
    """
    Simple Stage implementation for testing purposes.
    """

    #
    #   Class description and properties
    #

    driver_loaded = True
    driver_specifiable = False
    description = "Dummy Stage Vendor"

    @classmethod
    def find_stage_addresses(cls):
        return [
            'tcp:192.168.0.42:1234',
            'tcp:192.168.0.123:7894'
        ]

    @classmethod
    def load_driver(cls):
        pass

    def __init__(self, address):
        super().__init__(address)
        self.connected = False
        self.id_list = None
        self.connect()
        """
        self._speed_xy = None
        self._speed_z = None
        self._acceleration_xy = None"""

    def __str__(self) -> str:
        return "DMD Stage at {}".format(self.address_string)

    @property
    def address_string(self) -> str:
        return self.address

    @property
    def identifier(self) -> str:
        return self.address_string
    """
    def get_ids(self) -> list:
        if self.connected():
            self.id_list = []
            self.devices = [self.motors,self.multi,self.attenuator]
            query = ['ID ?','*IDN', 'IDN?']
            for device in self.devices:
    
                
        else:
            return [] 
    """
    def connect(self) -> bool:
        if self.connected:
            self._logger.info('All stages are already connected')
            return True
        else:
            try:
                rm = visa.ResourceManager()
                self.motors = rm.open_resource('GPIB0::10::INSTR')
                self.multi = rm.open_resource('GPIB0::21:INSTR')
                self.attenuator = rm.open_resource('GPIB0::27::INSTR')
                self.connected = True
                self._logger.info('All stages connected')
            except:
                self._logger.info('Failed to connect 1 or more stages')
            return self.connected

    def disconnect(self) -> bool:
        self.motors.close()
        self.multi.close()
        self.attenuator.close()
        self.connected = False
        return True

    def set_speed_xy(self, umps: float):
        self._speed_xy = umps

    def set_speed_z(self, umps: float):
        self._speed_z = umps

    def get_speed_xy(self) -> float:
        return self._speed_xy

    def get_speed_z(self) -> float:
        return self._speed_z

    def set_acceleration_xy(self, umps2):
        self._acceleration_xy = umps2

    def get_acceleration_xy(self) -> float:
        return self._acceleration_xy

    def get_status(self) -> tuple:
        return ('STOP', 'STOP', 'STOP')

    @property 
    def is_stopped(self) -> bool:
        return all(s == 'STOP' for s in self.get_status())

    def get_position(self) -> list:
        xPos = motors.query(f'1TP?')
        yPos = motors.query(f'2TP?')
        zPos = motors.query(f'3TP?')
        return [xPos, yPos, zPos]

    def move_absolute(self,
            xPos: float = 0,
            yPos: float = 0,
            zPos: float = 0) -> None:
	    motors.write(f'1PA{xPos}')
	    resp = motors.query(f'{ax}MD?') # information on response from MD---> successful motion or not
	    while resp[0] == '0':
		    resp = motors.query(f'{ax}MD?')
        motors.write(f'2PA{yPos}')
        resp = motors.query(f'{ax}MD?')
	    while resp[0] == '0':
		    resp = motors.query(f'{ax}MD?')
        motors.write(f'3PA{zPos}')
        resp = motors.query(f'{ax}MD?') 
	    while resp[0] == '0':
		    resp = motors.query(f'{ax}MD?')        
    
    def move_relative(
            self,
            xPos: float = 0,
            yPos: float = 0,
            zPos: float = 0) -> None:
        pass00
0