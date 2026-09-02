#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
LabExT  Copyright (C) 2021  ETH Zurich and Polariton Technologies AG
This program is free software and comes with ABSOLUTELY NO WARRANTY; for details see LICENSE file.
"""

import time
import uuid

from pandas import DataFrame

from LabExT.Measurements.MeasAPI.Measurement import Measurement
from LabExT.Wafer.Device import Device
from LabExT.Utils import make_filename_compliant

class QueueEntry:
    """Base class for anything that can sit in the experiment's `to_do_list`.

    The queue used to hold only `ToDo`s (device + measurement). Externally loaded
    queues (see `LabExT.Experiments.QueueLoader`) additionally contain explicit
    alignment steps - `MoveEntry` and `SfpEntry` - which `StandardExperiment.run()`
    dispatches on by `entry_type`. `device`/`measurement` default to `None` so
    consumers can cheaply tell an alignment step from a measurement.
    """

    entry_type = "entry"
    device = None
    measurement = None

    def __init__(self):
        self._timestamp = int(time.time() * 1e6)
        # The ToDo table uses get_hash() as the Treeview item id, which must be unique.
        # A timestamp alone is not enough: time.time() only resolves to ~16ms on Windows,
        # so entries built in a loop (as the queue loader does) all share one timestamp,
        # and alignment entries have no measurement id to tell them apart either.
        self._uid = uuid.uuid4().hex

    def get_hash(self):
        """calculate the unique but hardly one-way functional 'hash' of a queue entry"""
        return str(self.entry_type) + str(self.device) + self._uid

    def display_columns(self):
        """Returns the (device id, device type, description) triple shown in the ToDo table."""
        raise NotImplementedError


class ToDo(QueueEntry):

    entry_type = "meas"

    def __init__(self,
                 device: Device,
                 measurement: Measurement,
                 part_of_sweep: bool = False,
                 sweep_parameters: DataFrame = None,
                 dictionary_wrapper: "DictionaryWrapper" = None,
                 auto_align: bool = True):
        """Create a new ToDo

        Args:
            device: A reference to the device this measurement should be run on
            measurement: The measurement that should be run
            part_of_sweep: This should only be `True` if this ToDo is part of a sweep
            sweep_parameters: If the `ToDo` is part of a sweep this argument mustn't be `None`
            dictionary_wrapper: If the `ToDo` is part of a sweep this argument mustn't be `None`
            auto_align: Whether the global "auto move stages"/"execute search for peak" settings
                may align before this measurement. Loaded queues set this to `False`, because they
                carry their own explicit `MoveEntry`/`SfpEntry` steps and would otherwise align twice.
        """
        assert (part_of_sweep and sweep_parameters is not None) or not part_of_sweep
        assert (part_of_sweep and dictionary_wrapper is not None) or not part_of_sweep

        super().__init__()

        self.device = device
        self.measurement = measurement
        self.part_of_sweep = part_of_sweep
        self.sweep_parameters = sweep_parameters
        self.auto_align = auto_align

        self.dictionary_wrapper = dictionary_wrapper
        """This reference is shared between all `ToDo`s which are part of the same sweep."""

    def __getitem__(self, item):
        """ make To-Do class compatible with old code which used (device,measurement) tuples as ToDos """
        if item == 0:
            return self.device
        elif item == 1:
            return self.measurement
        else:
            raise KeyError(item)

    def __str__(self):
        return "<ToDo: " + str(self.measurement.get_name_with_id()) + " on " + str(self.device) + ">"

    def __repr__(self):
        return self.__str__()

    def get_hash(self):
        """calculate the unique but hardly one-way functional 'hash' of a to-do"""
        hash = str(self.device)
        hash += str(self.measurement.get_name_with_id())
        # get_name_with_id() only carries a shortened measurement id, and the timestamp
        # is too coarse to separate to-dos created in the same loop, so the per-entry
        # uid is what actually guarantees the uniqueness the ToDo table's item ids need
        hash += self._uid
        return hash

    def display_columns(self):
        return self.device.id, self.device.type, self.measurement.get_name_with_id()


class MoveEntry(QueueEntry):
    """Queue entry moving the stages to a device, without running anything on it."""

    entry_type = "move"

    def __init__(self, device: Device):
        super().__init__()
        self.device = device

    def __str__(self):
        return "<MoveEntry: move to " + str(self.device) + ">"

    def __repr__(self):
        return self.__str__()

    def display_columns(self):
        return self.device.id, self.device.type, "Move to device"


class SfpEntry(QueueEntry):
    """Queue entry running a Search for Peak at the current position.

    `device` is optional and only used to feed the aligned position back into the stage
    calibration when "refine calibration from search for peak" is enabled.
    """

    entry_type = "sfp"

    def __init__(self, device: Device = None):
        super().__init__()
        self.device = device

    def __str__(self):
        return "<SfpEntry: search for peak" + (" at " + str(self.device) if self.device else "") + ">"

    def __repr__(self):
        return self.__str__()

    def display_columns(self):
        if self.device is None:
            return "", "", "Search for Peak"
        return self.device.id, self.device.type, "Search for Peak"


class DictionaryWrapper:
    """This class wraps a dictionary and a subfolder name (str).
    
    It acts like a pointer, such that many objects can have a view onto the 
    same data, but the data can be changed as a whole after initialization.

    This is needed for measurements belonging to a sweep. They all need to 
    share the dictionary with the summary information of the sweep, however,
    this is only created once the first measurement is run. To be able to 
    update the references the other measurements use, they are given a reference
    to this wrapper class instead, which holds a reference to the final dictionary
    once it's created.
    """

    def __init__(self, dictionary: dict = None) -> None:
        """Initializes a new `DictionaryWrapper`
        
        Args:
            dictionary: The `dict` that should be wrapped or `None`
        """
        
        self._dictionary = dictionary
        self._subfolder_name = ""

    @property
    def available(self) -> bool:
        """Returns `True` if this wrapper contains a dictionary.
        
        A dictionary can be set with `self.wrap(...)`.
        """
        return self._dictionary is not None

    @property
    def get(self) -> dict:
        """Returns a reference to the wrapped dictionary or `None` if `self.available == False`."""
        return self._dictionary

    def wrap(self, dictionary: dict) -> None:
        self._dictionary = dictionary

    @property
    def subfolder_name(self) -> str:
        """Returns the wrapped subfolder name."""
        return self._subfolder_name

    @subfolder_name.setter
    def subfolder_name(self, new_name) -> None:
        """Sets the wrapped subfolder name."""
        self._subfolder_name = make_filename_compliant(new_name)