#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
LabExT  Copyright (C) 2022  ETH Zurich and Polariton Technologies AG
This program is free software and comes with ABSOLUTELY NO WARRANTY; for details see LICENSE file.
"""
import sys
import json

import py_thorlabs_ctrl.kinesis as KCUBE
# py_thorlabs_ctrl.kinesis.init('C:\Program Files\Thorlabs\Kinesis')

from LabExT.Movement.Stage import Stage
from LabExT.Utils import get_configuration_file_path, try_to_lift_window
from LabExT.View.Controls.DriverPathDialog import DriverPathDialog

sys_path_changed = False
try:
    settings_path = get_configuration_file_path('kcube_module_path.txt')
    with open(settings_path, 'r') as fp:
        module_path = json.load(fp)
    # sys.path.insert(0, module_path)
    KCUBE.init(module_path)
    sys_path_changed = True
    from py_thorlabs_ctrl.kinesis.motor import KCubeDCServo
    KCUBE_LOADED = True
except (ImportError, OSError, FileNotFoundError):
    KCUBE = object()
    KCUBE_LOADED = False
finally:
    if sys_path_changed:
        del sys.path[0]

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
    def find_stage_addresses(cls):
        return [27258584, 27258581]

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

    def __init__(self, address):
        super().__init__(address)

        self._speed_xy = None
        self._speed_z = None
        self._acceleration_xy = None

    def __str__(self) -> str:
        return "Dummy Stage at {}".format(self.address_string)

    @property
    def address_string(self) -> str:
        return self.address

    @property
    def identifier(self) -> str:
        return self.address_string

    def connect(self) -> bool:
        self.connected = True
        return True

    def disconnect(self) -> bool:
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
        return [0, 0, 0]

    def move_relative(
            self,
            x: float = 0,
            y: float = 0,
            z: float = 0,
            wait_for_stopping: bool = True) -> None:
        pass

    def move_absolute(
            self,
            x: float = None,
            y: float = None,
            z: float = None,
            wait_for_stopping: bool = True) -> None:
        pass
