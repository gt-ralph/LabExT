#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
LabExT  Copyright (C) 2021  ETH Zurich and Polariton Technologies AG
This program is free software and comes with ABSOLUTELY NO WARRANTY;
for details see LICENSE file.
"""

import json
import os
import shutil
import tempfile
import unittest
from unittest.mock import Mock, patch

import numpy as np

from LabExT.Experiments.StandardExperiment import StandardExperiment
from LabExT.Movement.Calibration import Calibration
from LabExT.Movement.MoverNew import MoverNew
from LabExT.Movement.Stages.DummyStage import DummyStage
from LabExT.Movement.config import Axis, Direction, DevicePort, Orientation, State
from LabExT.Wafer.Device import Device

SFP_OK = {"search successful": True, "optimized through power": -11.5}
SFP_FAILED = {"search successful": False, "calibration rejection reasons": ["X: no peak found"]}


class RefineCalibrationFromSfpTest(unittest.TestCase):
    """`_refine_calibration_from_sfp` against a real mover/calibration with a dummy stage."""

    def setUp(self):
        self.output_path = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.output_path, ignore_errors=True)
        settings_dir = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, settings_dir, ignore_errors=True)
        for name, file_name in [("CALIBRATIONS_SETTINGS_FILE", "mover_calibrations.json"),
                                ("CHIP_MAPS_DIR", "chip_maps")]:
            patcher = patch.object(MoverNew, name, os.path.join(settings_dir, file_name))
            patcher.start()
            self.addCleanup(patcher.stop)

        self.stage = DummyStage("usb:123")
        self.stage.connect()
        self.mover = MoverNew(None)
        self.calibration = Calibration(self.mover, self.stage, Orientation.LEFT, DevicePort.INPUT)
        for chip_axis, direction, stage_axis in [
                (Axis.X, Direction.POSITIVE, Axis.X),
                (Axis.Y, Direction.POSITIVE, Axis.Y),
                (Axis.Z, Direction.POSITIVE, Axis.Z)]:
            self.calibration.update_axes_rotation(chip_axis, direction, stage_axis)
        self.mover._calibrations[(Orientation.LEFT, DevicePort.INPUT)] = self.calibration

        self.experiment = StandardExperiment.__new__(StandardExperiment)  # skip the Tk-coupled ctor
        self.experiment.logger = Mock()
        self.experiment._mover = self.mover
        self.experiment.param_output_path = self.output_path

    def refine_at(self, device, stage_position, sfp_results=SFP_OK):
        self.stage.get_position = Mock(return_value=list(stage_position))
        return self.experiment._refine_calibration_from_sfp(device, sfp_results)

    @staticmethod
    def stage_of(x, y):
        # stage = chip rotated by 0.5 deg, shifted
        yaw = np.radians(0.5)
        return [np.cos(yaw) * x - np.sin(yaw) * y + 100, np.sin(yaw) * x + np.cos(yaw) * y - 50, 20]

    def test_first_point_fixes_calibration_and_writes_map(self):
        device = Device(id="1010", type="t", in_position=[0, 0], out_position=[0, 0])

        summary = self.refine_at(device, self.stage_of(0, 0))

        self.assertTrue(summary["applied"])
        self.assertEqual(self.calibration.state, State.SINGLE_POINT_FIXED)
        with open(summary["chip map file"]) as fp:
            chip_map_file = json.load(fp)
        points = chip_map_file["stages"][self.calibration.short_str]["points"]
        self.assertEqual(points[0]["device_id"], "1010")
        self.assertEqual(points[0]["sfp_power_dbm"], -11.5)
        self.assertTrue(summary["chip map file"].startswith(os.path.join(self.output_path, "chip_maps")))

    def test_failed_search_is_not_fed_in(self):
        device = Device(id="1010", type="t", in_position=[0, 0], out_position=[0, 0])

        summary = self.refine_at(device, self.stage_of(0, 0), SFP_FAILED)

        self.assertFalse(summary["applied"])
        self.assertFalse(self.calibration.chip_map.is_valid)

    def test_map_builds_up_and_gates_outlier(self):
        for i, (x, y) in enumerate([(0, 0), (1000, 0), (0, -1000)]):
            device = Device(id=str(i), type="t", in_position=[x, y], out_position=[x, y])
            self.assertTrue(self.refine_at(device, self.stage_of(x, y))["applied"])

        self.assertEqual(self.calibration.state, State.FULLY_CALIBRATED)

        outlier = Device(id="9", type="t", in_position=[500, -500], out_position=[500, -500])
        wrong_lobe = np.array(self.stage_of(500, -500)) + [30, 0, 0]
        summary = self.refine_at(outlier, wrong_lobe)

        self.assertFalse(summary["applied"])
        stage_summary = summary["stages"][self.calibration.short_str]
        self.assertFalse(stage_summary["accepted"])
        self.assertAlmostEqual(stage_summary["correction um"], 30, delta=0.5)
        self.assertEqual(len(self.calibration.chip_map.active_points), 3)
        self.assertEqual(len(self.calibration.chip_map.points), 4)


if __name__ == '__main__':
    unittest.main()
