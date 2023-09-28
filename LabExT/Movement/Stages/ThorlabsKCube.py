#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
LabExT  Copyright (C) 2022  ETH Zurich and Polariton Technologies AG
This program is free software and comes with ABSOLUTELY NO WARRANTY; for details see LICENSE file.
"""
import sys
import json
import time
import numpy as np

from enum import Enum

from pylablib.devices import Thorlabs
# import py_thorlabs_ctrl.kinesis as KCUBE
# from System import Decimal

from LabExT.Movement.config import Axis
from LabExT.Movement.Stage import Stage, StageError, assert_stage_connected, assert_driver_loaded
from LabExT.Utils import get_configuration_file_path, try_to_lift_window
from LabExT.View.Controls.DriverPathDialog import DriverPathDialog

sys_path_changed = False
try:
    settings_path = get_configuration_file_path('kcube_module_path.txt')
    with open(settings_path, 'r') as fp:
        module_path = json.load(fp)
    # sys.path.insert(0, module_path)
    # KCUBE.init(module_path)
    sys_path_changed = True
    # from Thorlabs import KinesisMotor
    # from pylablib.devices import Thorlabs
    KCUBE_LOADED = True
except (ImportError, OSError, FileNotFoundError):
    KCUBE = object()
    KCUBE_LOADED = False
finally:
    if sys_path_changed:
        del sys.path[0]

class MovementType(Enum):
    """Enumerate different movement modes."""
    RELATIVE = 0,
    ABSOLUTE = 1

class ThorlabsKCube(Stage):
    """
    Simple Stage implementation for testing purposes.
    """

    #
    #   Class description and properties
    #

    driver_loaded = KCUBE_LOADED
    driver_path_dialog = None
    driver_specifiable = True
    description = "Thorlabs KCube Stages"

    @classmethod
    def load_driver(cls, parent):
        """
        Loads driver for SmarAct by open a dialog to specifiy the driver path. This method will be invoked by the StageWizard.
        """
        if try_to_lift_window(cls.driver_path_dialog):
            parent.wait_window(cls.driver_path_dialog)
            return cls.driver_path_dialog.path_has_changed

        cls.driver_path_dialog = DriverPathDialog(
            parent,
            settings_file_path="kcube_module_path.txt",
            title="Stage Driver Settings",
            label="Thorlabs driver module path",
            hint="Specify the directory where the Thorlabs blah blah blah")
        parent.wait_window(cls.driver_path_dialog)
        
        return cls.driver_path_dialog.path_has_changed

    @classmethod
    @assert_driver_loaded
    def find_stage_addresses(cls): #TODO
        devices = Thorlabs.list_kinesis_devices()
        return [{"X":devices[0][0], f"Y":devices[1][0], "Z":devices[2][0]}]
    
    class _Channel:
        def __init__(self, serial_number, name='Channel') -> None:
            self.name = name
            self._sn = serial_number
            self._stage = Thorlabs.KinesisMotor(self._sn)
            self._status = None
            self._movement_mode = MovementType.RELATIVE
            self._position = None
            self._sensor = None
            self._speed = 0
            self._acceleration = 0
        
        @property
        def status(self) -> int:
            """Returns current channel status specified by SA_GetStatus_S"""
            self._status = self._stage.get_status()

            return self._status
        
        @property
        def position(self):
            """Returns current position of channel in micrometers specified by SA_GetPosition_S"""
            # position is in steps, One step travels 29 nm
            self._position = self._stage.get_position() * 29e-6

            return self._position
        
        @property
        def speed(self) -> float: #TODO
            """Returns speed setting of channel in micrometers/seconds specified by SA_GetClosedLoopMoveSpeed_S"""
            self._speed = self._stage.get_velocity_parameters()[2] * 1e3

            return self._speed
        
        @speed.setter
        def speed(self, umps: float) -> None:
            self._speed = self._stage.setup_velocity(max_velocity=None, acceleration=None)

        @property
        def acceleration(self) -> float: 
            """Returns acceleration of channel in micrometers/seconds^2 specified by SA_GetClosedLoopMoveAcceleration_S"""
            self._acceleration = self._stage.get_velocity_parameters()[1] * 1e3

            return self._acceleration

        @acceleration.setter
        def acceleration(self, umps2: float, max_velocity=None) -> None:
            """Sets acceleration of channel in micrometers/seconds^2 by calling SA_SetClosedLoopMoveAcceleration_S

            Parameters
            ----------
            umps2 : float
                Acceleration measured in um/s^2
            """
            accel = None if umps2 < 1e-8 else umps2/1e3
            self._acceleration = self._stage.setup_velocity(max_velocity=max_velocity, acceleration=accel)

        # Channel control
        def stop(self) -> None:
            """Stops all movement of this channel"""
            self._stage.stop(immediate=True)

        def is_moving(self) -> None:
            """Stops all movement of this channel"""
            return self._stage.is_moving()

        def move(
                self,
                diff: float,
                mode: MovementType) -> None:
            """Moves the channel with the specified movement type by the value diff

            Parameters
            ----------
            diff : float
                Channel movement measured in micrometers.
            mode : MovementType
                Channel movement type
            """
            self.movement_mode = mode
            if self.movement_mode == MovementType.RELATIVE:
                print('rel: ', diff)
                self._stage.move_by(diff / 29e-3)
                self._stage.wait_for_stop()
            elif self.movement_mode == MovementType.ABSOLUTE:
                print('abs: ', diff)
                self._stage.move_to(diff / 29e-3)
                self._stage.wait_for_stop()


    def __init__(self, address):
        super().__init__(address)
        self.channels = {}
        self.handle = None
        self._speed_z = None
        self._speed_xy = 0
        

    def __str__(self) -> str:
        return "KCUBE Stage at {}".format(self.address_string)

    @property
    def address_string(self) -> str:
        return self.address

    @property
    def identifier(self) -> str:
        return self.address_string

    def connect(self) -> bool:
        if self.connected:
            self._logger.debug('Stage is already connected.')
            return True
        
        axes = [Axis.X, Axis.Y, Axis.Z]
        sns = [27265733, 27265718, 27258551]
        
        for sn, axis in zip(sns, axes):
            self.channels[axis] = self._Channel(serial_number=sn)

            try:
                self.connected = True

                self._logger.info(
                    'PiezoStage at {} initialised successfully.'.format(
                        self.address))

            except Exception as e:
                self.connected = False
                self.handle = None
                self.channels = {}

                raise e
            
        return self.connected

    @assert_driver_loaded
    # @assert_stage_connected
    def disconnect(self) -> bool:
        for ch in self.channels:
            ch._stage.close()
        self.connected = False

    @assert_driver_loaded
    # @assert_stage_connected
    def set_speed_xy(self, umps: float):
        self.channels[Axis.X].speed = umps
        self.channels[Axis.Y].speed = umps
        self._speed_xy = umps

    @assert_driver_loaded
    # @assert_stage_connected
    def set_speed_z(self, umps: float):
        self.channels[Axis.Z].speed = umps
        self._speed_z = umps

    @assert_driver_loaded
    # @assert_stage_connected
    def get_speed_xy(self) -> float:
        x_speed = self.channels[Axis.X].speed
        y_speed = self.channels[Axis.Y].speed
        if (x_speed != y_speed):
            self._logger.info(
                "Speed settings of x and y channel are not equal.")
            
        return x_speed

    @assert_driver_loaded
    # @assert_stage_connected
    def get_speed_z(self) -> float:
        return self._speed_z

    @assert_driver_loaded
    # @assert_stage_connected
    def set_acceleration_xy(self, umps2):
        self.channels[Axis.X].acceleration = umps2
        self.channels[Axis.Y].acceleration = umps2

    @assert_driver_loaded
    # @assert_stage_connected
    def get_acceleration_xy(self) -> float:
        x_acceleration = self.channels[Axis.X].acceleration
        y_acceleration = self.channels[Axis.Y].acceleration

        if (x_acceleration != y_acceleration):
            self._logger.info(
                'Acceleration settings of x and y channel are not equal.')

        return x_acceleration

    @assert_driver_loaded
    # @assert_stage_connected
    def get_status(self) -> tuple: #TODO
        return tuple(ch.status for ch in self.channels.values())

    def is_stopped(self, channel, stop_pos_um) -> bool: #TODO
        return all(not s for s in self.is_moving())

    @assert_driver_loaded
    # @assert_stage_connected
    def get_position(self) -> list:
        return [
            self.channels[Axis.X].position,
            self.channels[Axis.Y].position,
            self.channels[Axis.Z].position,
        ]

    @assert_driver_loaded
    # @assert_stage_connected
    def move_relative(
            self,
            x: float = 0,
            y: float = 0,
            z: float = 0,
            wait_for_stopping: bool = True) -> None:
        
        self._logger.debug(
            'Want to relative move %s to x = %s um, y = %s um and z = %s um',
            self.address,
            x,
            y,
            z)
        
        print("MOVING RELATIVE", x, y, z)
        print(self.get_position())

        stop_pos_um = self.channels[Axis.X].position + x
        self.channels[Axis.X].move(diff=x, mode=MovementType.RELATIVE)
        if wait_for_stopping:
            self._wait_for_stopping(self.channels[Axis.X], stop_pos_um)

        
        stop_pos_um = self.channels[Axis.Y].position + y
        self.channels[Axis.Y].move(diff=y, mode=MovementType.RELATIVE)
        if wait_for_stopping:
            self._wait_for_stopping(self.channels[Axis.Y], stop_pos_um)

        stop_pos_um = self.channels[Axis.Z].position + z
        self.channels[Axis.Z].move(diff=z, mode=MovementType.RELATIVE)
        if wait_for_stopping:
            self._wait_for_stopping(self.channels[Axis.Z], stop_pos_um)

        print(self.get_position())

        pass

    def move_absolute(
            self,
            x: float = None,
            y: float = None,
            z: float = None,
            wait_for_stopping: bool = True) -> None:
        
        self._logger.debug(
            'Want to absolute move %s to x = %s um, y = %s um and z = %s um',
            self.address,
            x,
            y,
            z)

        if x is not None:
            stop_pos_um = x
            self.channels[Axis.X].move(diff=x, mode=MovementType.ABSOLUTE)
            if wait_for_stopping:
                self._wait_for_stopping(self.channels[Axis.X], stop_pos_um)
        if y is not None:
            stop_pos_um = y
            self.channels[Axis.Y].move(diff=y, mode=MovementType.ABSOLUTE)
            if wait_for_stopping:
                self._wait_for_stopping(self.channels[Axis.Y], stop_pos_um)
        if z is not None:
            stop_pos_um = z
            self.channels[Axis.Z].move(diff=z, mode=MovementType.ABSOLUTE)
            if wait_for_stopping:
                self._wait_for_stopping(self.channels[Axis.Z], stop_pos_um)

    def _wait_for_stopping(self, channel, stop_pos: float, delay=0.05):
        """
        Blocks until all channels have 'SA_STOPPED_STATUS' status.
        """
        print('wait for stopping')
        while True:
            time.sleep(delay)

            if self.is_stopped:
                break