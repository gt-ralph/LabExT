#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
LabExT  Copyright (C) 2021  ETH Zurich and Polariton Technologies AG
This program is free software and comes with ABSOLUTELY NO WARRANTY;
for details see LICENSE file.
"""

import os
import shutil
import tempfile
import unittest
from unittest.mock import Mock

from LabExT.Experiments.StandardExperiment import StandardExperiment
from LabExT.Experiments.ToDo import MoveEntry, SfpEntry
from LabExT.Wafer.Device import Device

SFP_RESULT = {"search successful": True, "optimized location": [1.0, 2.0]}


class QueuedAlignmentExecutionTest(unittest.TestCase):
    """Execution of the explicit move/sfp steps that a loaded experiment queue carries."""

    def setUp(self):
        self.device = Device(id="4310", type="loopback_left",
                             in_position=[0.0, 0.0], out_position=[0.0, 0.0])
        self.output_path = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.output_path, ignore_errors=True)

    def make_experiment(self, refine_enabled=True):
        """A StandardExperiment with just enough wired up to run alignment entries."""
        experiment = StandardExperiment.__new__(StandardExperiment)  # skip the Tk-coupled ctor
        experiment.logger = Mock()
        experiment._chip = Mock()
        experiment._mover = Mock()
        experiment._experiment_manager = Mock()
        experiment._meas_control_settings = Mock(json_indented=True)
        experiment._labext_vers = ("test", "test")
        experiment._fqdn_of_exp_runner = "testhost"
        experiment.param_chip_name = "TestChip"
        experiment.param_chip_file_path = ""
        experiment.param_output_path = self.output_path
        experiment.exctrl_pause_after_device = False
        experiment.exctrl_auto_move_stages = False
        # the global pre-measurement search is off, which is how a loaded queue is run
        experiment.exctrl_enable_sfp = False
        experiment.exctrl_refine_calibration_with_sfp = refine_enabled
        experiment._last_queued_sfp_result = None
        experiment._last_queued_move_device = None

        experiment._peak_searcher = Mock()
        experiment._peak_searcher.search_for_peak.return_value = SFP_RESULT
        experiment._refine_calibration_from_sfp = Mock(return_value={"applied": True})
        return experiment

    def run_entries(self, experiment, entries):
        experiment.to_do_list = list(entries)
        for _ in range(len(entries)):
            self.assertTrue(experiment._execute_alignment_entry(experiment.to_do_list[0]))

    def test_move_entry_moves_and_pops(self):
        experiment = self.make_experiment()
        self.run_entries(experiment, [MoveEntry(self.device)])
        experiment._mover.move_to_device.assert_called_once_with(experiment._chip, self.device)
        self.assertEqual([], experiment.to_do_list)

    def test_sfp_without_device_refines_against_moved_to_device(self):
        # the queue notebooks emit {"type": "sfp"} with no device_id
        experiment = self.make_experiment(refine_enabled=True)
        self.run_entries(experiment, [MoveEntry(self.device), SfpEntry()])

        experiment._refine_calibration_from_sfp.assert_called_once()
        device, results = experiment._refine_calibration_from_sfp.call_args[0]
        self.assertIs(self.device, device)
        self.assertIs(SFP_RESULT, results)

    def test_sfp_writes_standalone_result_file(self):
        experiment = self.make_experiment()
        self.run_entries(experiment, [MoveEntry(self.device), SfpEntry()])
        written = [f for f in os.listdir(self.output_path) if "_sfp_" in f]
        self.assertTrue(written, "expected a standalone search-for-peak record")

    def test_sfp_result_is_carried_to_following_measurements(self):
        experiment = self.make_experiment()
        self.run_entries(experiment, [SfpEntry()])
        self.assertIs(SFP_RESULT, experiment._last_queued_sfp_result)

    def test_move_clears_previous_alignment_result(self):
        # a move invalidates the alignment, so later measurements must not inherit it
        experiment = self.make_experiment()
        self.run_entries(experiment, [SfpEntry(), MoveEntry(self.device)])
        self.assertIsNone(experiment._last_queued_sfp_result)

    def test_explicit_device_on_sfp_takes_precedence(self):
        other = Device(id="4520", type="L2", in_position=[0.0, 0.0], out_position=[0.0, 0.0])
        experiment = self.make_experiment(refine_enabled=True)
        self.run_entries(experiment, [MoveEntry(self.device), SfpEntry(device=other)])
        self.assertIs(other, experiment._refine_calibration_from_sfp.call_args[0][0])

    def test_no_refinement_when_disabled(self):
        experiment = self.make_experiment(refine_enabled=False)
        self.run_entries(experiment, [MoveEntry(self.device), SfpEntry()])
        experiment._refine_calibration_from_sfp.assert_not_called()

    def test_unattributable_sfp_warns_instead_of_refining(self):
        experiment = self.make_experiment(refine_enabled=True)
        self.run_entries(experiment, [SfpEntry()])
        experiment._refine_calibration_from_sfp.assert_not_called()
        self.assertTrue(experiment.logger.warning.called)

    def test_failed_alignment_keeps_entry_and_pauses(self):
        experiment = self.make_experiment()
        experiment._mover.move_to_device.side_effect = RuntimeError("stage lost")
        entry = MoveEntry(self.device)
        experiment.to_do_list = [entry]

        with unittest.mock.patch("LabExT.Experiments.StandardExperiment.messagebox"):
            self.assertFalse(experiment._execute_alignment_entry(entry))

        self.assertEqual([entry], experiment.to_do_list, "failed entry must stay queued")


if __name__ == "__main__":
    unittest.main()
