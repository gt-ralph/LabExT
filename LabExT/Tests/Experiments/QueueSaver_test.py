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

from LabExT.Experiments.QueueLoader import QueueLoadError, build_entries, load_queue_file
from LabExT.Experiments.QueueSaver import (
    QueueSaveError,
    queue_save_warnings,
    save_queue_file,
    serialize_queue,
)
from LabExT.Experiments.ToDo import MoveEntry, QueueEntry, SfpEntry, ToDo
from LabExT.Tests.Experiments.QueueLoader_test import FakeMeasurement, make_experiment_manager
from LabExT.Wafer.Device import Device

MIXED_QUEUE = [
    {"type": "move", "device_id": "1"},
    {"type": "sfp"},
    {"type": "meas", "device_id": "1", "measurement": "FakeMeasurement",
     "parameters": {"number of points": 42}},
    {"type": "meas", "device_id": "2", "measurement": "FakeMeasurement"},
    {"type": "move", "device_id": "3"},
    {"type": "sfp", "device_id": "3"},
    {"type": "meas", "device_id": "3", "measurement": "FakeMeasurement"},
]


class QueueSaverTest(unittest.TestCase):

    def setUp(self):
        self.device_a = Device(id="1", type="MZI", in_position=[100.0, 200.0], out_position=[150.0, 200.0])
        self.device_a_sibling = Device(id="2", type="MZI", in_position=[100.0, 200.0], out_position=[150.0, 260.0])
        self.device_far = Device(id="3", type="MZI", in_position=[900.0, 200.0], out_position=[950.0, 200.0])
        self.experiment_manager = make_experiment_manager(
            [self.device_a, self.device_a_sibling, self.device_far]
        )
        # the loader fixture registers the measurement under its display name, but the saver
        # writes the class name - which is the key the loader actually looks up. Register it
        # so a round trip resolves, exactly as it does against a real experiment.
        self.experiment_manager.exp.measurements_classes["FakeMeasurement"] = FakeMeasurement
        self.experiment_manager.exp.measurement_list = {"DummyMeas", "FakeMeasurement"}

    def temp_path(self):
        handle = tempfile.NamedTemporaryFile("w", suffix=".json", delete=False)
        handle.close()
        self.addCleanup(lambda: os.path.exists(handle.name) and os.unlink(handle.name))
        return handle.name

    def load(self, entries):
        return build_entries(
            {"labext_queue_version": 1, "chip_name": "TestChip", "entries": entries},
            self.experiment_manager,
        )

    def save_and_read(self, entries):
        path = self.temp_path()
        save_queue_file(path, entries, self.experiment_manager)
        with open(path) as json_file:
            return json.load(json_file)

    def make_todo(self, device, auto_align=False):
        return ToDo(device=device, measurement=FakeMeasurement(), auto_align=auto_align)

    # --- the acceptance test ---------------------------------------------------------

    def test_round_trip_mixed_queue(self):
        entries = self.load(MIXED_QUEUE)
        saved = self.save_and_read(entries)

        self.assertEqual(1, saved["labext_queue_version"])
        self.assertEqual("TestChip", saved["chip_name"])

        reloaded = build_entries(saved, self.experiment_manager)
        self.assertEqual(
            [e.entry_type for e in entries], [e.entry_type for e in reloaded]
        )
        self.assertEqual(
            [getattr(e.device, "id", None) for e in entries],
            [getattr(e.device, "id", None) for e in reloaded],
        )
        self.assertEqual(
            [e.auto_align for e in entries if isinstance(e, ToDo)],
            [e.auto_align for e in reloaded if isinstance(e, ToDo)],
        )
        self.assertEqual(42, reloaded[2].measurement.parameters["number of points"].value)

    def test_saved_file_loads_through_the_public_loader(self):
        path = self.temp_path()
        save_queue_file(path, self.load(MIXED_QUEUE), self.experiment_manager)
        self.assertEqual(7, len(load_queue_file(path, self.experiment_manager)))

    # --- the two shapes that are easy to get wrong ------------------------------------

    def test_writes_measurement_class_name_not_display_name(self):
        todo = self.make_todo(self.device_a)
        todo.measurement.name = "some pretty display name"
        saved = self.save_and_read([todo])
        self.assertEqual("FakeMeasurement", saved["entries"][0]["measurement"])

    def test_parameters_are_written_flat(self):
        todo = self.make_todo(self.device_a)
        todo.measurement.parameters["number of points"].value = 7
        saved = self.save_and_read([todo])
        parameters = saved["entries"][0]["parameters"]
        self.assertEqual(7, parameters["number of points"])
        # not the {"value", "unit"} shape used inside measurement result files
        self.assertNotIsInstance(parameters["number of points"], dict)

    # --- entry shapes -----------------------------------------------------------------

    def test_auto_align_round_trips_both_ways(self):
        entries = [self.make_todo(self.device_a, auto_align=True), self.make_todo(self.device_a)]
        saved = self.save_and_read(entries)
        self.assertEqual([True, False], [e["auto_align"] for e in saved["entries"]])
        self.assertEqual(
            [True, False], [e.auto_align for e in build_entries(saved, self.experiment_manager)]
        )

    def test_sfp_without_device_omits_device_id(self):
        saved = self.save_and_read([MoveEntry(self.device_a), SfpEntry()])
        self.assertNotIn("device_id", saved["entries"][1])
        self.assertEqual("sfp", saved["entries"][1]["type"])

    def test_sfp_with_device_keeps_device_id(self):
        saved = self.save_and_read([SfpEntry(device=self.device_a)])
        self.assertEqual("1", saved["entries"][0]["device_id"])

    def test_instruments_are_written_when_selected(self):
        todo = self.make_todo(self.device_a)
        todo.measurement.selected_instruments = {"Laser": {"class": "L", "visa": "GPIB0::1::INSTR"}}
        saved = self.save_and_read([todo])
        self.assertEqual(
            {"Laser": {"class": "L", "visa": "GPIB0::1::INSTR"}}, saved["entries"][0]["instruments"]
        )

    def test_empty_instrument_selection_is_omitted(self):
        # so the loader falls back to the Experiment Wizard's saved selection, as before
        saved = self.save_and_read([self.make_todo(self.device_a)])
        self.assertNotIn("instruments", saved["entries"][0])

    def test_none_valued_parameter_is_omitted_and_still_reloads(self):
        todo = self.make_todo(self.device_a)
        todo.measurement.parameters["mean"]._value = None
        saved = self.save_and_read([todo])
        self.assertNotIn("mean", saved["entries"][0]["parameters"])
        build_entries(saved, self.experiment_manager)  # must not raise

    # --- refusals ---------------------------------------------------------------------

    def test_invalid_queue_raises_and_writes_nothing(self):
        # two devices at different input locations with no alignment step between them
        entries = [self.make_todo(self.device_a), self.make_todo(self.device_far)]
        path = self.temp_path()
        os.unlink(path)
        with self.assertRaises(QueueSaveError) as ctx:
            save_queue_file(path, entries, self.experiment_manager)
        self.assertIn("does not match device", str(ctx.exception))
        self.assertFalse(os.path.exists(path))

    def test_unsupported_entry_type_is_rejected(self):
        with self.assertRaises(QueueSaveError) as ctx:
            serialize_queue([QueueEntry()])
        self.assertIn("cannot save entry", str(ctx.exception))

    def test_move_without_device_is_rejected(self):
        with self.assertRaises(QueueSaveError):
            serialize_queue([MoveEntry(device=None)])

    def test_unserialisable_parameter_value_is_rejected(self):
        todo = self.make_todo(self.device_a)
        todo.measurement.parameters["mean"]._value = object()
        with self.assertRaises(QueueSaveError) as ctx:
            serialize_queue([todo])
        self.assertIn("mean", str(ctx.exception))

    # --- warnings ---------------------------------------------------------------------

    def test_sweep_entries_warn_and_are_flattened(self):
        todos = [self.make_todo(self.device_a), self.make_todo(self.device_a)]
        for todo in todos:
            todo.part_of_sweep = True
        warnings = queue_save_warnings(todos)
        self.assertEqual(1, len(warnings))
        self.assertIn("sweep", warnings[0])
        self.assertEqual(["meas", "meas"], [e["type"] for e in serialize_queue(todos)["entries"]])

    def test_instrument_credentials_warn(self):
        todo = self.make_todo(self.device_a)
        todo.measurement.selected_instruments = {
            "Switch": {"class": "SwitchDiconGP800", "args": {"hostname": "gp800", "password": "x"}}
        }
        warnings = queue_save_warnings([todo])
        self.assertEqual(1, len(warnings))
        self.assertIn("plain text", warnings[0])

    def test_no_warnings_for_a_plain_queue(self):
        self.assertEqual([], queue_save_warnings(self.load(MIXED_QUEUE)))


class QueueSaverRealFileTest(unittest.TestCase):
    """Round-trips a queue file that is actually used in the lab, not a synthetic one."""

    QUEUE_FILE = os.path.join(
        os.path.dirname(__file__), "..", "..", "..", "queue_notebooks", "test_tile_queue_il.json"
    )

    def test_notebook_queue_survives_a_round_trip(self):
        if not os.path.isfile(self.QUEUE_FILE):
            self.skipTest("queue_notebooks/test_tile_queue_il.json not present")

        with open(self.QUEUE_FILE) as json_file:
            original = json.load(json_file)

        device_ids = {e["device_id"] for e in original["entries"] if "device_id" in e}
        devices = [
            Device(id=i, type="dev", in_position=[0.0, 0.0], out_position=[10.0, 0.0])
            for i in sorted(device_ids)
        ]
        experiment_manager = make_experiment_manager(devices)
        measurement_class = original["entries"][2]["measurement"]
        experiment_manager.exp.measurements_classes = {measurement_class: FakeMeasurement}
        experiment_manager.exp.measurement_list = {measurement_class}

        # the fixture's FakeMeasurement only knows DummyMeas parameters, so the real file's
        # parameter names would be rejected on load - the point here is entry structure
        for entry in original["entries"]:
            entry.pop("parameters", None)

        entries = build_entries(original, experiment_manager)
        saved = serialize_queue(entries, chip_name=original["chip_name"])

        self.assertEqual(
            [e["type"] for e in original["entries"]], [e["type"] for e in saved["entries"]]
        )
        self.assertEqual(
            [e.get("device_id") for e in original["entries"]],
            [e.get("device_id") for e in saved["entries"]],
        )
        self.assertEqual(
            [e.get("instruments") for e in original["entries"] if e["type"] == "meas"],
            [e.get("instruments") for e in saved["entries"] if e["type"] == "meas"],
        )


if __name__ == "__main__":
    unittest.main()
