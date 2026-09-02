#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
LabExT  Copyright (C) 2021  ETH Zurich and Polariton Technologies AG
This program is free software and comes with ABSOLUTELY NO WARRANTY;
for details see LICENSE file.
"""

import json
import os
import tempfile
import unittest
from unittest.mock import Mock

from LabExT.Experiments.QueueLoader import QueueLoadError, build_entries, load_queue_file
from LabExT.Experiments.ToDo import MoveEntry, SfpEntry, ToDo
from LabExT.Measurements.DummyMeas import DummyMeas
from LabExT.Wafer.Device import Device


class FakeMeasurement(DummyMeas):
    """DummyMeas without the Measurement base constructor's GUI/experiment coupling."""

    def __init__(self):
        self.name = "DummyMeas"
        self.settings_path = "DummyMeas_settings.json"
        self.selected_instruments = {}
        self.parameters = DummyMeas.get_default_parameter()
        self.initialised_instruments = []

    def init_instruments(self):
        pass


def make_experiment_manager(devices):
    """Minimal ExperimentManager stand-in exposing what the loader touches."""
    chip = Mock()
    chip.name = "TestChip"
    chip.devices = {device.id: device for device in devices}

    experiment = Mock()
    experiment.measurements_classes = {"DummyMeas": FakeMeasurement}
    experiment.measurement_list = {"DummyMeas"}
    experiment.create_measurement_object.side_effect = lambda class_name: FakeMeasurement()

    experiment_manager = Mock()
    experiment_manager.chip = chip
    experiment_manager.exp = experiment
    return experiment_manager


class QueueLoaderTest(unittest.TestCase):

    def setUp(self):
        # two devices sharing an input coupler, one elsewhere
        self.device_a = Device(id="1", type="MZI", in_position=[100.0, 200.0], out_position=[150.0, 200.0])
        self.device_a_sibling = Device(id="2", type="MZI", in_position=[100.0, 200.0], out_position=[150.0, 260.0])
        self.device_far = Device(id="3", type="MZI", in_position=[900.0, 200.0], out_position=[950.0, 200.0])
        self.experiment_manager = make_experiment_manager(
            [self.device_a, self.device_a_sibling, self.device_far]
        )

    def write_queue(self, entries):
        handle = tempfile.NamedTemporaryFile("w", suffix=".json", delete=False)
        json.dump({"labext_queue_version": 1, "chip_name": "TestChip", "entries": entries}, handle)
        handle.close()
        self.addCleanup(os.unlink, handle.name)
        return handle.name

    def test_loads_mixed_queue_in_order(self):
        path = self.write_queue([
            {"type": "move", "device_id": "1"},
            {"type": "sfp"},
            {"type": "meas", "device_id": "1", "measurement": "DummyMeas",
             "parameters": {"number of points": 42}},
            {"type": "meas", "device_id": "2", "measurement": "DummyMeas"},
            {"type": "move", "device_id": "3"},
            {"type": "sfp"},
            {"type": "meas", "device_id": "3", "measurement": "DummyMeas"},
        ])

        entries = load_queue_file(path, self.experiment_manager)

        self.assertEqual(
            ["move", "sfp", "meas", "meas", "move", "sfp", "meas"],
            [entry.entry_type for entry in entries],
        )
        self.assertIsInstance(entries[0], MoveEntry)
        self.assertIsInstance(entries[1], SfpEntry)
        self.assertIsInstance(entries[2], ToDo)
        self.assertEqual("1", entries[0].device.id)
        self.assertEqual(42, entries[2].measurement.parameters["number of points"].value)
        # loaded measurements must not be aligned again by the global checkboxes
        self.assertTrue(all(not e.auto_align for e in entries if isinstance(e, ToDo)))

    def test_mismatched_input_location_in_block_is_rejected(self):
        path = self.write_queue([
            {"type": "move", "device_id": "1"},
            {"type": "sfp"},
            {"type": "meas", "device_id": "1", "measurement": "DummyMeas"},
            {"type": "meas", "device_id": "3", "measurement": "DummyMeas"},
        ])
        with self.assertRaises(QueueLoadError) as ctx:
            load_queue_file(path, self.experiment_manager)
        self.assertIn("does not match device", str(ctx.exception))

    def test_unknown_device_is_rejected(self):
        path = self.write_queue([{"type": "move", "device_id": "999"}])
        with self.assertRaises(QueueLoadError) as ctx:
            load_queue_file(path, self.experiment_manager)
        self.assertIn("is not on chip", str(ctx.exception))

    def test_unknown_measurement_is_rejected(self):
        path = self.write_queue([
            {"type": "meas", "device_id": "1", "measurement": "NotAMeasurement"}
        ])
        with self.assertRaises(QueueLoadError) as ctx:
            load_queue_file(path, self.experiment_manager)
        self.assertIn("unknown measurement", str(ctx.exception))

    def test_unknown_parameter_is_rejected(self):
        path = self.write_queue([
            {"type": "meas", "device_id": "1", "measurement": "DummyMeas",
             "parameters": {"not a real parameter": 1}}
        ])
        with self.assertRaises(QueueLoadError) as ctx:
            load_queue_file(path, self.experiment_manager)
        self.assertIn("has no parameter", str(ctx.exception))

    def test_integer_is_accepted_for_float_parameter(self):
        # JSON has no int/float distinction, but MeasParamFloat insists on a real float
        path = self.write_queue([
            {"type": "meas", "device_id": "1", "measurement": "DummyMeas",
             "parameters": {"mean": 3}}
        ])
        entries = load_queue_file(path, self.experiment_manager)
        value = entries[0].measurement.parameters["mean"].value
        self.assertEqual(3.0, value)
        self.assertIs(float, type(value))

    def test_bad_value_for_parameter_is_rejected(self):
        path = self.write_queue([
            {"type": "meas", "device_id": "1", "measurement": "DummyMeas",
             "parameters": {"number of points": "many"}}
        ])
        with self.assertRaises(QueueLoadError) as ctx:
            load_queue_file(path, self.experiment_manager)
        self.assertIn("cannot set parameter", str(ctx.exception))

    def test_unknown_entry_type_is_rejected(self):
        with self.assertRaises(QueueLoadError) as ctx:
            build_entries(
                {"labext_queue_version": 1, "entries": [{"type": "teleport"}]},
                self.experiment_manager,
            )
        self.assertIn("unknown entry type", str(ctx.exception))

    def test_wrong_version_is_rejected(self):
        with self.assertRaises(QueueLoadError) as ctx:
            build_entries({"labext_queue_version": 99, "entries": []}, self.experiment_manager)
        self.assertIn("unsupported queue file version", str(ctx.exception))


if __name__ == "__main__":
    unittest.main()
