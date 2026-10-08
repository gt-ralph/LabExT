#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
LabExT  Copyright (C) 2021  ETH Zurich and Polariton Technologies AG
This program is free software and comes with ABSOLUTELY NO WARRANTY;
for details see LICENSE file.
"""

import json
import shutil
import tempfile
import unittest
from unittest.mock import Mock, patch

from LabExT.Experiments.QueueLoader import QueueLoadError, build_entries
from LabExT.Experiments.QueueSaver import serialize_queue
from LabExT.Experiments.QueueValidation import split_into_blocks, validate_queue_warnings
from LabExT.Experiments.StandardExperiment import StandardExperiment
from LabExT.Experiments.ToDo import CalibEntry, MoveEntry, SfpEntry
from LabExT.Movement.Calibration import Calibration
from LabExT.Movement.MoverNew import MoverError, MoverNew
from LabExT.Movement.Stages.DummyStage import DummyStage
from LabExT.Movement.Transformations import ChipCoordinate, CoordinatePairing, StageCoordinate
from LabExT.Movement.config import Axis, Direction, DevicePort, Orientation, State
from LabExT.SearchForPeak.PeakSearcher import PeakSearcher
from LabExT.Tests.Experiments.QueueLoader_test import make_experiment_manager
from LabExT.Wafer.Device import Device

CONFIGURED = {
    "Laser": [
        {"visa": "GPIB0::20::INSTR", "class": "LaserSantec", "channels": []},
        {"visa": "GPIB0::21::INSTR", "class": "LaserMainframeKeysight", "channels": [0, 1, 2]},
    ],
    "Switch": [{"visa": "http://switch", "class": "SwitchDiconGP800", "channels": []}],
}


def fake_get_visa_address(role):
    return CONFIGURED[role.rstrip(" 0123456789")]


class SfpAndCalibLoadingTest(unittest.TestCase):

    def setUp(self):
        self.device = Device(id="1", type="t", in_position=[0.0, 0.0], out_position=[0.0, 0.0])
        self.experiment_manager = make_experiment_manager([self.device])
        patcher = patch("LabExT.Experiments.QueueLoader.get_visa_address", side_effect=fake_get_visa_address)
        patcher.start()
        self.addCleanup(patcher.stop)

    def build(self, entries):
        return build_entries({"labext_queue_version": 1, "entries": entries}, self.experiment_manager)

    def test_sfp_parameters_are_validated_and_coerced(self):
        entry, = self.build([{"type": "sfp", "device_id": "1", "parameters": {
            "Switch Flag": True, "M = 1": 3, "M = 2": 4, "Laser power": 2}}])

        self.assertEqual(entry.parameters, {"Switch Flag": True, "M = 1": 3, "M = 2": 4, "Laser power": 2.0})
        self.assertIsInstance(entry.parameters["Laser power"], float)

    def test_unknown_sfp_parameter_is_rejected(self):
        with self.assertRaisesRegex(QueueLoadError, "no parameter 'Laser colour'"):
            self.build([{"type": "sfp", "parameters": {"Laser colour": "red"}}])

    def test_wrongly_typed_sfp_parameter_is_rejected(self):
        with self.assertRaisesRegex(QueueLoadError, "cannot set parameter 'Laser power'"):
            self.build([{"type": "sfp", "parameters": {"Laser power": "high"}}])

    def test_instrument_class_name_resolves_to_descriptor(self):
        entry, = self.build([{"type": "sfp", "instruments": {"Laser": "LaserSantec"}}])

        self.assertEqual(entry.instruments["Laser"]["visa"], "GPIB0::20::INSTR")
        self.assertIsNone(entry.instruments["Laser"]["channel"])

    def test_multi_channel_instrument_needs_channel(self):
        with self.assertRaisesRegex(QueueLoadError, "name one with 'channel'"):
            self.build([{"type": "sfp", "instruments": {"Laser": "LaserMainframeKeysight"}}])

        entry, = self.build([{"type": "sfp", "instruments": {
            "Laser": {"class": "LaserMainframeKeysight", "channel": 1}}}])
        self.assertEqual(entry.instruments["Laser"]["channel"], 1)

    def test_unavailable_channel_is_rejected(self):
        with self.assertRaisesRegex(QueueLoadError, "channel 7 is not available"):
            self.build([{"type": "sfp", "instruments": {
                "Laser": {"class": "LaserMainframeKeysight", "channel": 7}}}])

    def test_unconfigured_instrument_is_rejected(self):
        with self.assertRaisesRegex(QueueLoadError, "0 configured instruments match"):
            self.build([{"type": "sfp", "instruments": {"Laser": "LaserSimulator"}}])

    def test_unknown_instrument_role_is_rejected(self):
        with self.assertRaisesRegex(QueueLoadError, "no instrument role 'OVA'"):
            self.build([{"type": "sfp", "instruments": {"OVA": "Luna"}}])

    def test_full_descriptor_is_used_as_is(self):
        descriptor = {"visa": "GPIB0::99::INSTR", "class": "LaserX", "channels": [5], "channel": 5}
        entry, = self.build([{"type": "sfp", "instruments": {"Laser": descriptor}}])

        self.assertEqual(entry.instruments["Laser"], descriptor)

    def test_calib_entry(self):
        new_map, use_map = self.build([
            {"type": "calib", "action": "new_map", "profile": "4C15_Cband"},
            {"type": "calib", "action": "use_map", "profile": "4C15_Oband"},
        ])

        self.assertIsInstance(new_map, CalibEntry)
        self.assertTrue(new_map.pause)
        self.assertEqual(use_map.profile, "4C15_Oband")
        self.assertFalse(use_map.pause)

    def test_calib_entry_with_file(self):
        with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as handle:
            handle.write("{}")
        self.addCleanup(lambda: __import__("os").unlink(handle.name))

        entry, = self.build([{"type": "calib", "action": "use_map", "profile": "p", "file": handle.name}])
        self.assertEqual(entry.file, handle.name)
        self.assertEqual(serialize_queue([entry])["entries"][0]["file"], handle.name)

        with self.assertRaisesRegex(QueueLoadError, "does not exist"):
            self.build([{"type": "calib", "action": "use_map", "profile": "p", "file": handle.name + ".missing"}])
        with self.assertRaisesRegex(QueueLoadError, "only a 'use_map'"):
            self.build([{"type": "calib", "action": "new_map", "profile": "p", "file": handle.name}])

    def test_invalid_calib_entries(self):
        with self.assertRaisesRegex(QueueLoadError, "unknown calib action"):
            self.build([{"type": "calib", "action": "reset", "profile": "x"}])
        with self.assertRaisesRegex(QueueLoadError, "non-empty profile"):
            self.build([{"type": "calib", "action": "new_map"}])

    def test_round_trip_through_saver(self):
        raw = [
            {"type": "calib", "action": "new_map", "profile": "4C15_Oband", "pause": True},
            {"type": "sfp", "device_id": "1", "parameters": {"Switch Flag": True, "M = 1": 3},
             "instruments": {"Laser": "LaserSantec"}},
        ]
        entries = self.build(raw)
        saved = serialize_queue(entries)["entries"]

        self.assertEqual(saved[0], raw[0])
        self.assertEqual(saved[1]["parameters"], raw[1]["parameters"])
        self.assertEqual(saved[1]["instruments"]["Laser"]["class"], "LaserSantec")
        reloaded = self.build(json.loads(json.dumps(saved)))
        self.assertEqual(reloaded[1].instruments, entries[1].instruments)

    def test_calib_entry_closes_block_and_does_not_count_as_alignment_warning(self):
        entries = [CalibEntry("new_map", "p"), SfpEntry(self.device)]
        self.assertEqual(split_into_blocks(entries), [])
        self.assertEqual(validate_queue_warnings(entries), [])


class FakePeakSearcher:
    """Just the parts of PeakSearcher that queued sfp execution touches."""

    def __init__(self):
        self.parameters = PeakSearcher.get_default_parameter()
        self.selected_instruments = {role: {"class": "Default" + role} for role in PeakSearcher.get_wanted_instrument()}
        self.instruments = {("Laser", "Default"): "default laser"}
        self.seen = None
        self.sigmas = []

    def set_prediction_sigma_um(self, sigma_um):
        self.sigmas.append(sigma_um)

    get_wanted_instrument = staticmethod(PeakSearcher.get_wanted_instrument)

    def update_params_from_savefile(self):
        pass

    def init_instruments(self):
        self.instruments = {("Laser", self.selected_instruments["Laser"]["class"]): "new laser"}

    def search_for_peak(self):
        self.seen = {
            "M = 1": self.parameters["M = 1"].value,
            "laser": self.selected_instruments["Laser"]["class"],
            "instruments": dict(self.instruments),
        }
        return {"search successful": True, "optimized through power": -10.0}


class QueuedChipMapExecutionTest(unittest.TestCase):

    def setUp(self):
        self.output_path = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.output_path, ignore_errors=True)
        settings_dir = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, settings_dir, ignore_errors=True)
        for name, file_name in [("CALIBRATIONS_SETTINGS_FILE", "mover_calibrations.json"),
                                ("CHIP_MAPS_DIR", "chip_maps")]:
            patcher = patch.object(MoverNew, name, f"{settings_dir}/{file_name}")
            patcher.start()
            self.addCleanup(patcher.stop)

        # a failing step would otherwise block the test run on a Tk dialog
        patcher = patch("LabExT.Experiments.StandardExperiment.messagebox")
        self.messagebox = patcher.start()
        self.addCleanup(patcher.stop)

        self.stage = DummyStage("usb:123")
        self.stage.connect()
        self.mover = MoverNew(None)
        self.calibration = Calibration(self.mover, self.stage, Orientation.LEFT, DevicePort.INPUT)
        for chip_axis, stage_axis in [(Axis.X, Axis.X), (Axis.Y, Axis.Y), (Axis.Z, Axis.Z)]:
            self.calibration.update_axes_rotation(chip_axis, Direction.POSITIVE, stage_axis)
        self.mover._calibrations[(Orientation.LEFT, DevicePort.INPUT)] = self.calibration
        self.mover.move_to_device = Mock()

        self.device = Device(id="2010", type="t", in_position=[100.0, -50.0], out_position=[100.0, -50.0])

        experiment = StandardExperiment.__new__(StandardExperiment)  # skip the Tk-coupled ctor
        experiment.logger = Mock()
        experiment._chip = Mock()
        experiment._mover = self.mover
        experiment._experiment_manager = Mock()
        experiment._meas_control_settings = Mock(json_indented=True)
        experiment._labext_vers = ("test", "test")
        experiment._fqdn_of_exp_runner = "testhost"
        experiment.param_chip_name = "TestChip"
        experiment.param_chip_file_path = ""
        experiment.param_output_path = self.output_path
        experiment.exctrl_pause_after_device = True
        experiment.exctrl_auto_move_stages = False
        experiment.exctrl_enable_sfp = False
        experiment.exctrl_inter_measurement_wait_time = 0.0
        experiment.exctrl_refine_calibration_with_sfp = True
        experiment._last_queued_sfp_result = None
        experiment._last_queued_move_device = None
        experiment._manual_alignment_pending = False
        experiment._peak_searcher = FakePeakSearcher()
        experiment.to_do_list = []
        self.experiment = experiment

    def execute(self, entry):
        self.experiment.to_do_list = [entry] + self.experiment.to_do_list
        self.assertTrue(self.experiment._execute_alignment_entry(entry))

    def test_sfp_overrides_apply_for_the_search_only(self):
        entry = SfpEntry(self.device, parameters={"M = 1": 7},
                         instruments={"Laser": {"class": "LaserSantec"}})
        self.execute(entry)

        seen = self.experiment._peak_searcher.seen
        self.assertEqual(seen["M = 1"], 7)
        self.assertEqual(seen["laser"], "LaserSantec")
        self.assertEqual(seen["instruments"], {("Laser", "LaserSantec"): "new laser"})

        peak_searcher = self.experiment._peak_searcher
        self.assertEqual(peak_searcher.parameters["M = 1"].value, 1)
        self.assertEqual(peak_searcher.selected_instruments["Laser"], {"class": "DefaultLaser"})
        self.assertEqual(peak_searcher.instruments, {("Laser", "Default"): "default laser"})

    def test_new_map_entry_starts_empty_profile_without_fallback(self):
        # the default profile still has old pairings; a new band must not use them
        self.calibration.update_single_point_offset(CoordinatePairing(
            self.calibration, StageCoordinate(1, 2, 3), self.device, ChipCoordinate(100, -50, 0)))
        self.assertEqual(self.calibration.state, State.SINGLE_POINT_FIXED)

        self.execute(CalibEntry("new_map", "4C15_Cband"))

        self.assertEqual(self.calibration.active_chip_map_profile, "4C15_Cband")
        self.assertEqual(self.calibration.state, State.COORDINATE_SYSTEM_FIXED)
        self.assertFalse(self.mover.can_move_absolutely)

    def test_first_device_after_pause_is_positioned_by_hand(self):
        calib = CalibEntry("new_map", "4C15_Oband")
        self.experiment.to_do_list = [MoveEntry(self.device)]
        self.execute(calib)
        self.experiment._pause_for_manual_alignment(calib)
        self.messagebox.showinfo.assert_called_once()

        # move is skipped because the map is empty, the sfp adds the first map point
        self.stage.get_position = Mock(return_value=[1000.0, 2000.0, 30.0])
        self.experiment.to_do_list = []
        self.execute(MoveEntry(self.device))
        self.mover.move_to_device.assert_not_called()
        self.execute(SfpEntry())

        self.assertEqual(len(self.calibration.chip_map.active_points), 1)
        self.assertEqual(self.calibration.chip_map.active_points[0].device_id, "2010")
        self.assertEqual(self.calibration.state, State.SINGLE_POINT_FIXED)

        # from now on moves go through the map
        self.execute(MoveEntry(self.device))
        self.mover.move_to_device.assert_called_once()

        # the first search had no map to predict from, the next one gets the map's uncertainty
        self.execute(SfpEntry())
        sigmas = self.experiment._peak_searcher.sigmas
        self.assertIsNone(sigmas[0])
        self.assertGreater(sigmas[1], 0)

    def test_use_map_loads_chip_map_file(self):
        self.calibration.update_chip_map(CoordinatePairing(
            self.calibration, StageCoordinate(1, 2, 3), self.device, ChipCoordinate(100, -50, 0)))
        path = self.mover.dump_chip_map(self.output_path)
        self.calibration.reset_chip_map()

        self.execute(CalibEntry("use_map", "restored", file=path))

        self.assertEqual(self.calibration.active_chip_map_profile, "restored")
        self.assertEqual(len(self.calibration.chip_map.active_points), 1)
        self.assertEqual(self.calibration.state, State.SINGLE_POINT_FIXED)

    def test_chip_map_file_of_other_chip_is_rejected(self):
        chip = Mock()
        chip.name = "TestChip"
        chip.devices = {self.device.id: self.device}
        chip.path = ""
        self.mover._chip = chip
        self.calibration.update_chip_map(CoordinatePairing(
            self.calibration, StageCoordinate(1, 2, 3), self.device, ChipCoordinate(100, -50, 0)))
        path = self.mover.dump_chip_map(self.output_path)
        other_chip = Mock()
        other_chip.name = "OtherChip"
        other_chip.devices = {}
        self.mover._chip = other_chip

        with self.assertRaisesRegex(MoverError, "belongs to chip"):
            self.mover.load_chip_map_file(path)

    def test_use_map_switches_back_to_existing_profile(self):
        self.execute(CalibEntry("new_map", "a"))
        self.calibration.update_chip_map(CoordinatePairing(
            self.calibration, StageCoordinate(1, 2, 3), self.device, ChipCoordinate(100, -50, 0)))
        self.execute(CalibEntry("new_map", "b"))
        self.execute(CalibEntry("use_map", "a"))

        self.assertEqual(len(self.calibration.chip_map.active_points), 1)


class RadiusFromChipMapTest(unittest.TestCase):

    def setUp(self):
        self.peak_searcher = PeakSearcher.__new__(PeakSearcher)  # skip the GUI-coupled ctor
        self.peak_searcher.parameters = PeakSearcher.get_default_parameter()
        self.peak_searcher.parameters['Radius from Chip Map: Enable'].value = True

    def test_disabled_or_unknown_sigma_keeps_configured_radius(self):
        self.assertEqual(self.peak_searcher.first_pass_radius_from_sigma(None, 5.0, 0.5), (5.0, 0.5))
        self.peak_searcher.parameters['Radius from Chip Map: Enable'].value = False
        self.assertEqual(self.peak_searcher.first_pass_radius_from_sigma(4.0, 5.0, 0.5), (5.0, 0.5))

    def test_radius_is_sigma_multiple_with_scaled_step(self):
        radius, step = self.peak_searcher.first_pass_radius_from_sigma(4.0, 5.0, 0.5)
        self.assertAlmostEqual(radius, 12.0)
        self.assertAlmostEqual(step, 1.2)

    def test_radius_is_clamped(self):
        self.assertAlmostEqual(self.peak_searcher.first_pass_radius_from_sigma(0.2, 5.0, 0.5)[0], 3.0)
        self.assertAlmostEqual(self.peak_searcher.first_pass_radius_from_sigma(100.0, 5.0, 0.5)[0], 50.0)


if __name__ == '__main__':
    unittest.main()
