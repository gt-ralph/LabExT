#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
LabExT  Copyright (C) 2022  ETH Zurich and Polariton Technologies AG
This program is free software and comes with ABSOLUTELY NO WARRANTY; for details see LICENSE file.
"""

import json
import os
import tempfile
import unittest

import numpy as np

from LabExT.Movement.Calibration import Calibration
from LabExT.Movement.ChipMap import ChipMapTransformation, MapLevel
from LabExT.Movement.MoverNew import MoverNew
from LabExT.Movement.Stages.DummyStage import DummyStage
from LabExT.Movement.Transformations import (
    AxesRotation, ChipCoordinate, CoordinatePairing, StageCoordinate, TransformationError)
from LabExT.Movement.config import Axis, Direction, DevicePort, Orientation, State
from LabExT.Wafer.Device import Device


def make_axes_rotation():
    """Chip x -> stage -y, chip y -> stage x, chip z -> stage z."""
    axes_rotation = AxesRotation()
    axes_rotation.update(Axis.X, Direction.NEGATIVE, Axis.Y)
    axes_rotation.update(Axis.Y, Direction.POSITIVE, Axis.X)
    axes_rotation.update(Axis.Z, Direction.POSITIVE, Axis.Z)
    return axes_rotation


class SyntheticChip:
    """
    Ground truth for a chip mounted with yaw, tilt, an offset and (optionally) stage scale errors.
    """

    def __init__(self, axes_rotation, yaw_deg=0.6, scale_ppm=(0, 0), skew=0.0,
                 offset=(5000., -3000.), z_offset=1200., tilt=(0.003, -0.002), noise_um=0.0, seed=0):
        yaw = np.radians(yaw_deg)
        rotation = np.array([[np.cos(yaw), -np.sin(yaw)], [np.sin(yaw), np.cos(yaw)]])
        scale = np.array([[1 + scale_ppm[0] * 1e-6, skew], [0, 1 + scale_ppm[1] * 1e-6]])
        self.A = rotation @ scale
        self.offset = np.array(offset)
        self.z_offset = z_offset
        self.tilt = np.array(tilt)
        self.R = axes_rotation.matrix
        self.noise_um = noise_um
        self.rng = np.random.default_rng(seed)

    def stage(self, x, y, z=0.0, noisy=True):
        u = np.array([x, y])
        q = np.r_[self.A @ u + self.offset, self.z_offset + self.tilt @ u + z]
        stage = self.R @ q
        if noisy and self.noise_um:
            stage = stage + self.rng.normal(0, self.noise_um, 3)
        return StageCoordinate.from_numpy(stage)


def device(device_id, x, y):
    return Device(id=device_id, type="test", in_position=[x, y], out_position=[x, y])


class ChipMapTransformationTest(unittest.TestCase):
    def setUp(self) -> None:
        self.axes_rotation = make_axes_rotation()
        self.chip_map = ChipMapTransformation(self.axes_rotation)
        self.truth = SyntheticChip(self.axes_rotation)

    def add(self, device_id, x, y, **kwargs):
        return self.chip_map.add_point(
            ChipCoordinate(x, y, 0), self.truth.stage(x, y), device=device(device_id, x, y), **kwargs)

    def assert_predicts(self, x, y, places_um=0.01):
        predicted = self.chip_map.chip_to_stage(ChipCoordinate(x, y, 0)).to_numpy()
        expected = self.truth.stage(x, y, noisy=False).to_numpy()
        self.assertLess(np.linalg.norm(predicted - expected), places_um)

    def test_invalid_without_points(self):
        self.assertFalse(self.chip_map.is_valid)
        self.assertIsNone(self.chip_map.level)
        with self.assertRaises(TransformationError):
            self.chip_map.chip_to_stage(ChipCoordinate(0, 0, 0))

    def test_requires_valid_axes_rotation(self):
        axes_rotation = AxesRotation()
        axes_rotation.update(Axis.X, Direction.POSITIVE, Axis.Y)
        with self.assertRaises(RuntimeError):
            ChipMapTransformation(axes_rotation)

    def test_single_point_is_offset_through_axes_rotation(self):
        self.add(1, 0, 0)

        self.assertTrue(self.chip_map.is_valid)
        self.assertFalse(self.chip_map.is_fully_constrained)
        self.assertEqual(self.chip_map.level, MapLevel.OFFSET)

        # with one point, a chip offset maps through the axes rotation only
        stage_origin = self.truth.stage(0, 0).to_numpy()
        predicted = self.chip_map.chip_to_stage(ChipCoordinate(100, 0, 0)).to_numpy()
        np.testing.assert_allclose(predicted, stage_origin + self.axes_rotation.matrix @ [100, 0, 0])

    def test_two_points_fit_yaw(self):
        self.add(1, 0, 0)
        self.add(2, 1000, 0)

        self.assertEqual(self.chip_map.level, MapLevel.RIGID)
        self.assertAlmostEqual(self.chip_map.get_diagnostics()["yaw_deg"], 0.6, places=6)
        self.assertFalse(self.chip_map.is_fully_constrained)

    def test_two_close_points_stay_offset(self):
        self.add(1, 0, 0)
        self.add(2, 10, 0)

        self.assertEqual(self.chip_map.level, MapLevel.OFFSET)

    def test_three_spread_points_recover_full_pose(self):
        for i, (x, y) in enumerate([(0, 0), (1000, 0), (0, -1000)]):
            self.add(i, x, y)

        self.assertTrue(self.chip_map.is_fully_constrained)
        self.assertEqual(self.chip_map.level, MapLevel.RIGID)
        self.assert_predicts(500, -500)
        self.assert_predicts(-800, 300)

    def test_collinear_points_do_not_invent_perpendicular_tilt(self):
        # devices in one row: tilt along the row is fitted, across the row it is unknown -> 0
        for i, x in enumerate([0, 300, 600, 900]):
            self.add(i, x, 0)

        self.assertFalse(self.chip_map.is_fully_constrained)
        self.assertEqual(self.chip_map.level, MapLevel.RIGID)

        z_on_row = self.chip_map.chip_to_stage(ChipCoordinate(1200, 0, 0)).z
        self.assertAlmostEqual(z_on_row, self.truth.stage(1200, 0).z, places=3)

        z_off_row = self.chip_map.chip_to_stage(ChipCoordinate(0, -1000, 0)).z
        z_at_row_start = self.truth.stage(0, 0).z
        self.assertAlmostEqual(z_off_row, z_at_row_start, places=3)

    def test_affine_chosen_for_stage_scale_errors(self):
        self.truth = SyntheticChip(
            self.axes_rotation, scale_ppm=(3000, -2000), skew=0.002, noise_um=0.2, seed=1)
        rng = np.random.default_rng(2)
        for i, (x, y) in enumerate(rng.uniform([-1000, -1800], [1700, 0], (12, 2))):
            self.add(i, x, y)

        self.assertEqual(self.chip_map.level, MapLevel.AFFINE)
        diagnostics = self.chip_map.get_diagnostics()
        self.assertAlmostEqual(diagnostics["scale_x_ppm"], 3000, delta=300)
        self.assertAlmostEqual(diagnostics["scale_y_ppm"], -2000, delta=300)
        self.assert_predicts(200, -900, places_um=1.0)

    def test_affine_not_chosen_without_scale_errors(self):
        self.truth = SyntheticChip(self.axes_rotation, noise_um=0.3, seed=3)
        rng = np.random.default_rng(4)
        for i, (x, y) in enumerate(rng.uniform([-1000, -1800], [1700, 0], (15, 2))):
            self.add(i, x, y)

        self.assertEqual(self.chip_map.level, MapLevel.RIGID)

    def test_max_level_caps_model(self):
        self.chip_map = ChipMapTransformation(self.axes_rotation, max_level=MapLevel.OFFSET)
        self.add(1, 0, 0)
        self.add(2, 1000, 0)

        self.assertEqual(self.chip_map.level, MapLevel.OFFSET)

    def test_residual_correction_follows_local_deviation(self):
        for i, (x, y) in enumerate([(0, 0), (1000, 0), (0, -1000), (1000, -1000)]):
            self.add(i, x, y)

        # a device found 3 um off the rigid model pulls its neighbourhood, not the far corner
        bump = self.axes_rotation.matrix @ [3.0, 0, 0]
        stage = StageCoordinate.from_numpy(self.truth.stage(500, -500).to_numpy() + bump)
        self.chip_map.add_point(ChipCoordinate(500, -500, 0), stage, device=device(9, 500, -500))

        near = self.chip_map.chip_to_stage(ChipCoordinate(520, -500, 0)).to_numpy()
        far = self.chip_map.chip_to_stage(ChipCoordinate(1000, -1000, 0)).to_numpy()
        near_offset = near - self.truth.stage(520, -500).to_numpy()
        far_offset = far - self.truth.stage(1000, -1000).to_numpy()

        self.assertGreater(near_offset @ bump / np.linalg.norm(bump), 1.5)
        self.assertLess(np.linalg.norm(far_offset), np.linalg.norm(near_offset))

    def test_stage_to_chip_inverts_chip_to_stage(self):
        self.truth = SyntheticChip(self.axes_rotation, scale_ppm=(3000, -2000), noise_um=0.3, seed=5)
        rng = np.random.default_rng(6)
        for i, (x, y) in enumerate(rng.uniform([-1000, -1800], [1700, 0], (10, 2))):
            self.add(i, x, y)

        for chip in [ChipCoordinate(123, -456, 0), ChipCoordinate(-700, -100, 20)]:
            roundtrip = self.chip_map.stage_to_chip(self.chip_map.chip_to_stage(chip))
            np.testing.assert_allclose(roundtrip.to_numpy(), chip.to_numpy(), atol=1e-6)

    def test_remeasurement_supersedes_earlier_point(self):
        self.add(1, 0, 0)
        self.add(2, 1000, 0)
        moved = StageCoordinate.from_numpy(self.truth.stage(0, 0).to_numpy() + [1, 1, 0])
        self.chip_map.add_point(ChipCoordinate(0, 0, 0), moved, device=device(1, 0, 0))

        self.assertEqual(len(self.chip_map.points), 3)
        self.assertEqual(len(self.chip_map.active_points), 2)
        self.assertTrue(self.chip_map.points[0].superseded)

    def test_gate_rejects_outlier_once_established(self):
        for i, (x, y) in enumerate([(0, 0), (1000, 0), (0, -1000)]):
            self.add(i, x, y, gate=True)

        bad = StageCoordinate.from_numpy(self.truth.stage(400, -400).to_numpy() + [40, 0, 0])
        point = self.chip_map.add_point(
            ChipCoordinate(400, -400, 0), bad, device=device(7, 400, -400), gate=True)

        self.assertFalse(point.accepted)
        self.assertIn("predicted position", point.reject_reason)
        self.assertEqual(len(self.chip_map.active_points), 3)
        self.assertAlmostEqual(point.correction_um, 40, delta=0.1)

    def test_gate_keeps_point_without_flag_or_before_established(self):
        self.add(1, 0, 0, gate=True)
        far = StageCoordinate.from_numpy(self.truth.stage(1000, 0).to_numpy() + [20, 0, 0])
        point = self.chip_map.add_point(ChipCoordinate(1000, 0, 0), far, device=device(2, 1000, 0), gate=True)

        self.assertTrue(point.accepted)

    def test_rejected_point_does_not_supersede(self):
        for i, (x, y) in enumerate([(0, 0), (1000, 0), (0, -1000)]):
            self.add(i, x, y, gate=True)

        bad = StageCoordinate.from_numpy(self.truth.stage(0, 0).to_numpy() + [40, 0, 0])
        self.chip_map.add_point(ChipCoordinate(0, 0, 0), bad, device=device(0, 0, 0), gate=True)

        self.assertTrue(self.chip_map.points[0].is_active)

    def test_robust_fit_limits_early_bad_point(self):
        # the 2nd point is a wrong-lobe search accepted before the gate was active
        self.truth = SyntheticChip(self.axes_rotation, noise_um=0.3, seed=10)
        rng = np.random.default_rng(11)
        points = rng.uniform([-1000, -1800], [1700, 0], (15, 2))
        for i, (x, y) in enumerate(points):
            stage = self.truth.stage(x, y).to_numpy()
            if i == 1:
                stage = stage + self.axes_rotation.matrix @ [25, 0, 0]
            self.chip_map.add_point(ChipCoordinate(x, y, 0), StageCoordinate.from_numpy(stage),
                                    device=device(i, x, y), gate=True)

        self.assertGreaterEqual(self.chip_map.get_diagnostics()["downweighted_points"], 1)
        for x, y in rng.uniform([-1000, -1800], [1700, 0], (100, 2)):
            self.assert_predicts(x, y, places_um=2.5)
        # also right next to the bad point
        self.assert_predicts(points[1][0] + 50, points[1][1], places_um=2.5)

    def test_sigma_shrinks_as_map_grows(self):
        self.truth = SyntheticChip(self.axes_rotation, noise_um=0.3, seed=7)
        target = ChipCoordinate(800, -800, 0)

        self.add(1, 0, 0)
        sigma_one = self.chip_map.predict(target)[1]
        self.add(2, 1500, 0)
        self.add(3, 0, -1500)
        self.add(4, 1500, -1500)
        sigma_four = self.chip_map.predict(target)[1]

        self.assertGreater(sigma_one, 5 * sigma_four)
        self.assertGreaterEqual(sigma_four, ChipMapTransformation.POINT_SIGMA_UM)

    def test_sigma_covers_errors_in_simulated_sweep(self):
        self.truth = SyntheticChip(self.axes_rotation, yaw_deg=-0.4, scale_ppm=(1500, -800),
                                   noise_um=0.3, seed=8)
        rng = np.random.default_rng(9)
        ratios = []
        for i, (x, y) in enumerate(rng.uniform([-1000, -1800], [1700, 0], (40, 2))):
            if self.chip_map.is_valid:
                predicted, sigma = self.chip_map.predict(ChipCoordinate(x, y, 0))
                actual = self.truth.stage(x, y, noisy=False).to_numpy()
                ratios.append(np.linalg.norm(predicted.to_numpy() - actual) / sigma)
            self.add(i, x, y, gate=True)

        self.assertLess(max(ratios), 3)
        self.assertEqual(sum(1 for p in self.chip_map.points if not p.accepted), 0)

    def test_refits_when_axes_rotation_changes(self):
        self.add(1, 0, 0)
        before = self.chip_map.chip_to_stage(ChipCoordinate(100, 0, 0)).to_numpy()

        self.axes_rotation.update(Axis.X, Direction.POSITIVE, Axis.Y)
        after = self.chip_map.chip_to_stage(ChipCoordinate(100, 0, 0)).to_numpy()

        self.assertFalse(np.allclose(before, after))

    def test_drift_weighting_favours_recent_points(self):
        self.chip_map = ChipMapTransformation(self.axes_rotation, drift_tau_s=60, residual_correction=False)
        old = self.truth.stage(0, 0).to_numpy()
        self.chip_map.add_point(ChipCoordinate(0, 0, 0), StageCoordinate.from_numpy(old),
                                device=device(1, 0, 0), timestamp=0)
        self.chip_map.add_point(ChipCoordinate(10, 0, 0),
                                StageCoordinate.from_numpy(self.truth.stage(10, 0).to_numpy() + [2, 0, 0]),
                                device=device(2, 10, 0), timestamp=600)

        predicted = self.chip_map.chip_to_stage(ChipCoordinate(0, 0, 0)).to_numpy()
        # offset-only map: the 0.6 deg yaw over the 10 um baseline adds ~0.1 um
        self.assertAlmostEqual(predicted[0] - old[0], 2, delta=0.15)

    def test_dump_and_load_roundtrip(self):
        self.chip_map = ChipMapTransformation(self.axes_rotation, drift_tau_s=3600, GATE_K=4.0)
        for i, (x, y) in enumerate([(0, 0), (1000, 0), (0, -1000)]):
            self.add(i, x, y, sfp_power_dbm=-12.5)
        bad = StageCoordinate.from_numpy(self.truth.stage(400, -400).to_numpy() + [40, 0, 0])
        self.chip_map.add_point(ChipCoordinate(400, -400, 0), bad, device=device(7, 400, -400), gate=True)

        data = json.loads(json.dumps(self.chip_map.dump()))
        loaded = ChipMapTransformation.load(data, axes_rotation=self.axes_rotation)

        self.assertEqual(loaded.GATE_K, 4.0)
        self.assertEqual(loaded.drift_tau_s, 3600)
        self.assertEqual(len(loaded.points), 4)
        self.assertEqual(len(loaded.active_points), 3)
        self.assertEqual(loaded.points[0].sfp_power_dbm, -12.5)
        self.assertEqual(loaded.points[3].reject_reason, self.chip_map.points[3].reject_reason)
        target = ChipCoordinate(300, -200, 0)
        np.testing.assert_allclose(
            loaded.chip_to_stage(target).to_numpy(), self.chip_map.chip_to_stage(target).to_numpy())

    def test_unknown_setting_raises(self):
        with self.assertRaises(ValueError):
            ChipMapTransformation(self.axes_rotation, NOT_A_SETTING=1)


class CalibrationChipMapTest(unittest.TestCase):
    def setUp(self) -> None:
        self.stage = DummyStage('usb:123456789')
        self.stage.connect()
        self.mover = MoverNew(None)
        self.calibration = Calibration(self.mover, self.stage, Orientation.LEFT, DevicePort.INPUT)
        for chip_axis, direction, stage_axis in [
                (Axis.X, Direction.NEGATIVE, Axis.Y),
                (Axis.Y, Direction.POSITIVE, Axis.X),
                (Axis.Z, Direction.POSITIVE, Axis.Z)]:
            self.calibration.update_axes_rotation(chip_axis, direction, stage_axis)
        self.truth = SyntheticChip(self.calibration._axes_rotation)

    def pair(self, device_id, x, y):
        return CoordinatePairing(
            calibration=self.calibration,
            stage_coordinate=self.truth.stage(x, y),
            device=device(device_id, x, y),
            chip_coordinate=ChipCoordinate(x, y, 0))

    def test_state_follows_chip_map(self):
        self.assertEqual(self.calibration.state, State.COORDINATE_SYSTEM_FIXED)

        self.calibration.update_chip_map(self.pair(1, 0, 0))
        self.assertEqual(self.calibration.state, State.SINGLE_POINT_FIXED)

        self.calibration.update_chip_map(self.pair(2, 1000, 0))
        self.assertEqual(self.calibration.state, State.SINGLE_POINT_FIXED)

        self.calibration.update_chip_map(self.pair(3, 0, -1000))
        self.assertEqual(self.calibration.state, State.FULLY_CALIBRATED)

    def test_chip_map_takes_over_transformation(self):
        for i, (x, y) in enumerate([(0, 0), (1000, 0), (0, -1000)]):
            self.calibration.update_chip_map(self.pair(i, x, y))

        predicted = self.calibration.transform_chip_to_stage_coordinate(ChipCoordinate(500, -500, 0))
        np.testing.assert_allclose(predicted.to_numpy(), self.truth.stage(500, -500).to_numpy(), atol=0.01)

        chip = self.calibration.transform_stage_to_chip_coordinate(self.truth.stage(500, -500))
        np.testing.assert_allclose(chip.to_numpy(), [500, -500, 0], atol=0.01)

    def test_profiles_keep_separate_maps(self):
        self.calibration.update_chip_map(self.pair(1, 0, 0))
        self.calibration.use_chip_map_profile("cband")

        self.assertEqual(self.calibration.active_chip_map_profile, "cband")
        self.assertFalse(self.calibration.chip_map.is_valid)
        self.assertEqual(self.calibration.state, State.COORDINATE_SYSTEM_FIXED)

        self.calibration.use_chip_map_profile(Calibration.DEFAULT_CHIP_MAP_PROFILE)
        self.assertEqual(len(self.calibration.chip_map.active_points), 1)
        self.assertEqual(self.calibration.state, State.SINGLE_POINT_FIXED)

    def test_use_profile_with_reset_empties_map(self):
        self.calibration.update_chip_map(self.pair(1, 0, 0))
        self.calibration.use_chip_map_profile(Calibration.DEFAULT_CHIP_MAP_PROFILE, reset=True)

        self.assertFalse(self.calibration.chip_map.is_valid)

    def test_legacy_pairings_seed_chip_map(self):
        for i, (x, y) in enumerate([(0, 0), (1000, 0), (0, -1000)]):
            self.calibration.update_kabsch_rotation(self.pair(i, x, y))

        reloaded = Calibration(
            self.mover, self.stage, Orientation.LEFT, DevicePort.INPUT,
            axes_rotation=self.calibration._axes_rotation,
            kabsch_rotation=self.calibration._kabsch_rotation)

        self.assertEqual(len(reloaded.chip_map.active_points), 3)
        self.assertEqual(reloaded.state, State.FULLY_CALIBRATED)

    def test_dump_and_load_with_chip_maps(self):
        devices = {}
        for i, (x, y) in enumerate([(0, 0), (1000, 0), (0, -1000)]):
            pairing = self.pair(i, x, y)
            devices[str(i)] = pairing.device
            self.calibration.update_chip_map(pairing)
        self.calibration.use_chip_map_profile("cband")
        self.calibration.update_chip_map(self.pair(5, 200, -200))

        chip = type("Chip", (), {"devices": devices})()
        data = json.loads(json.dumps(self.calibration.dump()))
        loaded = Calibration.load(self.mover, self.stage, data, chip=chip)

        self.assertEqual(loaded.active_chip_map_profile, "cband")
        self.assertEqual(set(loaded.chip_maps), {Calibration.DEFAULT_CHIP_MAP_PROFILE, "cband"})
        self.assertEqual(len(loaded.chip_maps[Calibration.DEFAULT_CHIP_MAP_PROFILE].active_points), 3)
        self.assertIs(loaded.chip_maps[Calibration.DEFAULT_CHIP_MAP_PROFILE].points[0].device, devices["0"])

    def test_mover_dumps_chip_map_file(self):
        self.mover._calibrations[(Orientation.LEFT, DevicePort.INPUT)] = self.calibration
        self.calibration.update_chip_map(self.pair(1, 0, 0))

        with tempfile.TemporaryDirectory() as directory:
            path = self.mover.dump_chip_map(directory)
            with open(path) as fp:
                data = json.load(fp)
            created = data["created"]
            self.mover.dump_chip_map(directory)
            with open(path) as fp:
                data_again = json.load(fp)

        self.assertEqual(os.path.basename(path), "unknown_chip_default.json")
        self.assertEqual(data["profile"], Calibration.DEFAULT_CHIP_MAP_PROFILE)
        stage_data = data["stages"][self.calibration.short_str]
        self.assertEqual(len(stage_data["points"]), 1)
        self.assertEqual(stage_data["points"][0]["device_id"], "1")
        self.assertEqual(data_again["created"], created)


if __name__ == '__main__':
    unittest.main()
