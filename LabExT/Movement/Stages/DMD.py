import pyvisa as visa
from LabExT.Movement.config import Axis
from LabExT.Movement.Stage import Stage, StageError, assert_stage_connected, assert_driver_loaded
from LabExT.Utils import get_configuration_file_path, try_to_lift_window
from LabExT.View.Controls.DriverPathDialog import DriverPathDialog
import numpy as np
import time

#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
LabExT  Copyright (C) 2022  ETH Zurich and Polariton Technologies AG
This program is free software and comes with ABSOLUTELY NO WARRANTY; for details see LICENSE file.
"""

from LabExT.Movement.Stage import Stage

"""
GPIB Addresses:
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
    description = "DMD Stage"

    @classmethod
    def find_stage_addresses(cls):
        """return [
            'tcp:192.168.0.42:1234',
            'tcp:192.168.0.123:7894'
        ]"""
        pass

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
        if self.connected:
            self.motors.close()
            self.multi.close()
            self.attenuator.close()
            self.connected = False
            return True
        else:
            return False
        
    def resetInstruments(self):
        rm = visa.ResourceManager()
        resources = list(rm.list_resources())
        for resource in resources:
            try:
                resource.close()
                self._logger.info(f'{resource} + closed')
            except:
                self._logger.info(f'{resource} could not be closed')
        self.connect()


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
        xPos = self.motors.query(f'1TP?')
        yPos = self.motors.query(f'2TP?')
        zPos = self.motors.query(f'3TP?')
        return [xPos, yPos, zPos]

    def move_relative(
            self,
            xPos: float = 0,
            yPos: float = 0,
            zPos: float = 0) -> None:
        xCurr = self.motors.query('1TP?')
        yCurr = self.motors.query('2TP?')
        zCurr = self.motors.query('3TP?')
        diffX = float(xCurr) - xPos
        diffY = float(yCurr) - yPos
        diffZ = float(zCurr) - zPos
        if diffX != 0:
            self.motors.write(f'1PR{xPos}')
            resp = self.motors.query(f'1MD?')
            while resp[0] == '0':
                resp = self.motors.query(f'1MD?')
            self.motors.write(f'2PR{yPos}')
        if diffY != 0:
            resp = self.motors.query(f'2MD?')
            while resp[0] == '0':
                resp = self.motors.query(f'2MD?')
            self.motors.write(f'3PR{zPos}')
        if diffZ != 0:
            resp = self.motors.query(f'3MD?') 
            while resp[0] == '0':
                resp = self.motors.query(f'3MD?')
            return None
        
    def move_absolute(self,
            xPos: float = 0,
            yPos: float = 0,
            zPos: float = 0) -> None:
        xCurr = self.motors.query('1TP?')
        yCurr = self.motors.query('2TP?')
        zCurr = self.motors.query('3TP?')
        diffX = float(xCurr) - xPos
        diffY = float(yCurr) - yPos
        diffZ = float(zCurr) - zPos
        if diffX != 0:
            self.motors.write(f'1PA{xPos}')
            resp = self.motors.query(f'1MD?') # information on response from MD---> successful motion or not
            while resp[0] == '0':
                resp = self.motors.query(f'1MD?')
        if diffY != 0:
            self.motors.write(f'2PA{yPos}')
            resp = self.motors.query(f'2MD?')
            while resp[0] == '0':
                resp = self.motors.query(f'2MD?')
        if diffZ != 0:
            self.motors.write(f'3PA{zPos}')
            resp = self.motors.query(f'3MD?') 
            while resp[0] == '0':
                resp = self.motors.query(f'3MD?')
        return None
    
    def move_absolute_by_axis(self, axis, pos):
        self.motors.write(f'{axis}PA{pos}')
        resp = self.motors.query(f'{axis}MD?')
        while resp[0] == '0':
            resp = self.motors.query(f'{axis}MD?')
        return None

    def goHome(self) -> list:
        # Defining home
        self.motors.write(f'1DH0')
        self.motors.write(f'2DH0')
        self.motors.write(f'3DH0')
        # moving home
        self.move_absolute(0,0,0)
        # verify
        x = self.motors.query('1TP')
        y = self.motors.query('2TP')
        z = self.motors.query('3TP')
        return [x,y,z]
    
    def line(self,
            ax: float,
            start: float = 0,
            stop: float = 0,
            step: float = 0) -> None:
        for i in range(start,stop,step):
            if ax == 1:
                self.move_absolute(i,0,0)
            if ax == 2:
                self.move_absolute(0,i,0)
            else:
                self.move_absolute(0,0,i)
    
    # ALTER BEAM PROFILE FUNCTION BASED ON FIBER DIMENSIONS.
    def beam_profile(self) -> list:
        # Horizontal profile
        self.resetInstruments()
        self.motors.write(f'1DH0')
        self.motors.write(f'2DH0')
        self.motors.write(f'3DH0')
        # Initial current (or power?) measurement
        currentInit = self.multi.query('MEASure:CURRent:DC? DEF,DEF')
        #voltageInit = self.multi.query('MEASure:VOLTage:DC? DEF,DEF')
        currentInit = float(currentInit.strip())
        #voltageInit = float(voltageInit.strip())
        #powerInit = currentInit * voltageInit
        x = self.motors.query('1TP')
        y = self.motors.query('2TP')
        z = self.motors.query('3TP')
        print(x,y,z)
        p = []
        # move to init pos
        start = -0.5
        stop = 0.5
        step = 0.001
        self.move_absolute(-0.6,0,0)
        self.line(1,-0.6,start,step)
        # measurement loop
        for i in range(start,stop,step):
            self.move_absolute(i,0,0)
            currentMeas = self.multi.query('MEASure:CURRent:DC? DEF,DEF')
            currentMeas = float(currentMeas.strip())
            p.append(currentMeas)
        
        self.goHome()
        return p
    
    # USING MAX RESOLUTION TO MINIMIZE CHANCES OF REPEATS IN POWER MAXIMUM 
    def center(self) -> list:
        hp, vp = [],  []
        self.resetInstruments()
        # Define movement parameters
        left = -0.01
        right = 0.01
        down = -0.01
        up = 0.01
        step = 0.001
        self.goHome()
        # Vertical profile measurement
        self.move_absolute(0,down,0)
        for i in range(down,up,step):
            self.move_absolute(0,i,0)
            currentMeas = self.multi.query('MEASure:CURRent:DC? DEF,MAX')
            currentMeas = float(currentMeas.strip())
            vp.append(currentMeas)
        self.goHome()
        # Horizontal axis measurement
        self.move_absolute(left,0,0)
        for i in range(left,right,step):
            self.move_absolute(i,0,0)
            currentMeas = self.multi.query('MEASure:CURRent:DC? DEF,MAX')
            currentMeas = float(currentMeas.strip())
            hp.append(currentMeas)
        # identifying center + move to center
        hMax = max(hp)
        hMaxInd = hp.index(hMax)
        vMax = max(vp)
        vMaxInd = vp.index(vMax)
        centerCoords = [left + (hMaxInd - 1)*step, down + (vMaxInd - 1)*step]
        self.move_absolute(centerCoords[0],0,0)
        self.move_absolute(0,centerCoords[1],0)
        return centerCoords

    def edges(self,
            left: float, 
            right: float,
            up: float, 
            down: float) -> dict:
        powerOuts = {}
        epsilon = 0.01
        self.goHome()
        center_current = self.multi.query('MEASure:CURRent:DC? DEF,DEF')
        powerOuts['pcenter'] = center_current
        self.move_absolute(left-epsilon,0,0)
        # left edge
        self.move_absolute(left,0,0)
        left_current = self.multi.query('MEASure:CURRent:DC? DEF,DEF')
        powerOuts['pleft'] = left_current
        # right edge
        self.move_absolute(right,0,0)
        right_current = self.multi.query('MEASure:CURRent:DC? DEF,DEF')
        powerOuts['pright'] = right_current
        # recenter
        self.goHome()
        self.move_absolute(0,down - epsilon,0)
        # down edge
        self.move_absolute(0,down,0)
        down_current = self.multi.query('MEASure:CURRent:DC? DEF,DEF')
        powerOuts['pdown'] = down_current
        # upper edge
        self.move_absolute(0,up,0)
        up_current = self.multi.query('MEASure:CURRent:DC? DEF,DEF')
        powerOuts['pup'] = up_current
        # center2
        self.goHome()
        center2_current = self.multi.query('MEASure:CURRent:DC? DEF,DEF')
        powerOuts['pcenter2'] = center2_current
        return powerOuts

    def fiber_sweep(self) -> np.ndarray:
        self.resetInstruments()
        left = -0.03
        right = 0.03
        up = 0.03
        down = -0.03
        step = 0.003
        dim = len(range(left,right,step))
        dat = np.empty((dim,dim))
        p = dim
        q = 1
        for i in range(down,up,step):
            if i == down:
                self.move_absolute_by_axis(2,down-2*step)
                self.line(2,down-2*step,down,0.001)
            self.move_absolute_by_axis(2,i)
            for j in range(left,right,step):
                if j == left:
                    self.move_absolute_by_axis(1,left-2*step)
                    self.line(1,left-2*step,left,0.001)
                self.move_absolute_by_axis(1,j)
                x = self.motors.query(f'1TP?')
                y = self.motors.query(f'2TP?')
                pow = self.multi.query('MEASure:CURRent:DC? DEF,DEF')
                dat[q][p] = [j,i,x,y,pow]
                q += 1
            q = 1
            p -= 1
        return dat
                



        """left = -0.01
        right = 0.01
        down = -0.01
        up = 0.01
        step = 0.001

        def initializeAxis(self, axis) -> bool:
            self.motors.write(f'{axis}PA0')
            resp = self.motors.query(f'{axis}MD?')
            while resp[0] == '0':
                resp = self.motors.query(f'{axis}MD?')
            return True
        
        def measureAxis(self, 
                    axis: float,
                    start: float = 0, 
                    stop: float = 0,
                    step: float = 0.001) -> list:
            profile = []
            positions = range(start, stop, step)
            self.move_absolute_by_axis(axis, start - 2 * step)
            self.move_absolute_by_axis(axis, start - step)
            for pos in positions:
                self.move_absolute_by_axis(axis, pos)
                p = self.multi.query('MEASure:CURRent:DC? DEF,DEF')
                profile.append(p)
            return profile
        
        initializeAxis(1)
        initializeAxis(2)
        vp = measureAxis(2,down,up,step)
        self.move_absolute_by_axis(2,0)
        hp = measureAxis(1,left,right,step)
        self.move_absolute_by_axis(1,0)"""





        


        
        

