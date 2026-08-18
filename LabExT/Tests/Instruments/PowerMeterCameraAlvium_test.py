#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
LabExT  Copyright (C) 2021  ETH Zurich and Polariton Technologies AG
This program is free software and comes with ABSOLUTELY NO WARRANTY; for details see LICENSE file.

Unit tests for the camera-backed power meter. These need no camera and no vmbpy: a stub camera is
substituted through the driver's _make_camera seam.
"""

import json
import os
import shutil
import tempfile
import unittest
from unittest import mock

import numpy as np

from LabExT.Instruments.InstrumentAPI import InstrumentException
from LabExT.Instruments.PowerMeterCameraAlvium import MINIMUM_DB, PowerMeterCameraAlvium


class StubCamera:
    """Enough of CameraAlliedVisionAlvium for the adapter, with scripted frames."""

    def __init__(self, frames=None, pixel_format='Mono8', exposure_time=5000.0, gain=0.0,
                 roi=None, serial='STUB1'):
        self.frames = frames if frames is not None else [np.zeros((16, 20), dtype=np.uint8)]
        self.pixel_format = pixel_format
        self.exposure_time = exposure_time
        self.gain = gain
        shape = self.frames[0].shape
        self.roi = roi if roi is not None else [shape[1], shape[0], 0, 0]
        self.is_streaming = False
        self.instrument_parameters = {'camera id': 'DEV_STUB', 'camera serial': serial}
        self.thread_lock = None
        self.snap_count = 0
        self._open = False

    def open(self):
        self._open = True

    def close(self):
        self._open = False

    def idn(self):
        return "AlliedVision,StubCam,{:s},0".format(self.instrument_parameters['camera serial'])

    def snap_photo(self, timeout_ms=None, squeeze=True):
        self.snap_count += 1
        return self.frames[(self.snap_count - 1) % len(self.frames)]

    def snap_photos(self, count, timeout_ms=None, squeeze=True):
        return [self.snap_photo() for _ in range(count)]

    def get_instrument_parameter(self):
        return {'idn': self.idn(), 'exposure_time': self.exposure_time}


def make_meter(camera, **kwargs):
    """An adapter wired to a stub camera, opened, with no dark reference required by default."""
    kwargs.setdefault('require_dark_reference', False)
    meter = PowerMeterCameraAlvium(visa_address='None', **kwargs)
    meter._make_camera = lambda: camera
    return meter


class PowerMeterCameraAlviumTest(unittest.TestCase):

    def setUp(self):
        # keep every dark reference inside a scratch settings directory
        self.settings_dir = tempfile.mkdtemp(prefix='pmcam_')
        patcher = mock.patch(
            'LabExT.Instruments.PowerMeterCameraAlvium.get_configuration_file_path',
            side_effect=lambda name: os.path.join(self.settings_dir, os.path.basename(name)))
        patcher.start()
        self.addCleanup(patcher.stop)
        self.addCleanup(shutil.rmtree, self.settings_dir, True)

    #
    # integration and units
    #

    def test_roi_sum_matches_manual_sum(self):
        frame = np.arange(16 * 20, dtype=np.uint8).reshape(16, 20) % 200
        meter = make_meter(StubCamera([frame]), roi_x=4, roi_y=2, roi_width=6, roi_height=5)
        meter.open()
        expected = float(frame[2:7, 4:10].sum(dtype=np.float64))
        self.assertAlmostEqual(meter.fetch_power(), expected, places=6)

    def test_zero_size_roi_means_whole_frame(self):
        frame = np.ones((16, 20), dtype=np.uint8)
        meter = make_meter(StubCamera([frame]))
        meter.open()
        self.assertAlmostEqual(meter.fetch_power(), 320.0, places=6)

    def test_full_frame_uint16_sum_does_not_overflow(self):
        # numpy sums integers in the platform default int, uint32 on Windows: a full-scale Mono12
        # frame this size sums past 2**32 and silently wraps unless float64 is forced
        frame = np.full((1032, 1296), 4095, dtype=np.uint16)
        meter = make_meter(StubCamera([frame], pixel_format='Mono12'),
                           saturation_fraction_limit=1.1)  # allow the deliberately saturated frame
        meter.open()
        self.assertAlmostEqual(meter.fetch_power(), float(1032 * 1296 * 4095), places=0)

    def test_db_mode_is_ten_log_ten_of_counts(self):
        frame = np.full((8, 8), 10, dtype=np.uint8)
        meter = make_meter(StubCamera([frame]), merit_unit='dB')
        meter.open()
        self.assertAlmostEqual(meter.fetch_power(), 10.0 * np.log10(640.0), places=9)

    def test_zero_signal_in_db_mode_returns_finite_floor(self):
        meter = make_meter(StubCamera([np.zeros((8, 8), dtype=np.uint8)]), merit_unit='dB')
        meter.open()
        value = meter.fetch_power()
        self.assertEqual(value, MINIMUM_DB)
        self.assertTrue(np.isfinite(value))

    def test_unit_accepts_dbm_alias_and_rejects_nonsense(self):
        meter = make_meter(StubCamera())
        meter.unit = 'dBm'          # what generic power meter consumers write
        self.assertEqual(meter.unit, 'dB')
        meter.unit = 'counts'
        self.assertEqual(meter.unit, 'counts')
        with self.assertRaises(InstrumentException):
            meter.unit = 'watts'

    #
    # dark reference
    #

    def _capture_dark(self, camera, frames=4):
        return PowerMeterCameraAlvium.capture_dark_reference(camera, frames=frames)

    def test_dark_subtraction_removes_pedestal(self):
        dark_level, signal = 30, 5
        camera = StubCamera([np.full((16, 20), dark_level, dtype=np.uint8)])
        self._capture_dark(camera)

        camera.frames = [np.full((16, 20), dark_level + signal, dtype=np.uint8)]
        meter = make_meter(camera, require_dark_reference=True)
        meter.open()
        self.assertAlmostEqual(meter.fetch_power(), float(16 * 20 * signal), places=6)

    def test_unclipped_noise_integrates_to_about_zero(self):
        rng = np.random.default_rng(0)
        base = 40
        camera = StubCamera([np.full((64, 64), base, dtype=np.uint8)])
        self._capture_dark(camera)

        noisy = (base + rng.integers(-3, 4, size=(64, 64))).astype(np.uint8)
        camera.frames = [noisy]
        meter = make_meter(camera, require_dark_reference=True)
        meter.open()
        # symmetric noise cancels; the point of not clipping
        self.assertLess(abs(meter.fetch_power()), 0.02 * base * 64 * 64)

    def test_clip_negative_biases_the_reading_upwards(self):
        rng = np.random.default_rng(1)
        base = 40
        camera = StubCamera([np.full((64, 64), base, dtype=np.uint8)])
        self._capture_dark(camera)
        camera.frames = [(base + rng.integers(-3, 4, size=(64, 64))).astype(np.uint8)]

        clipped = make_meter(camera, require_dark_reference=True, clip_negative=True)
        clipped.open()
        # documents why clipping is not the default: it turns symmetric noise into a pedestal
        self.assertGreater(clipped.fetch_power(), 1000.0)

    def test_missing_dark_reference_raises_when_required(self):
        meter = make_meter(StubCamera(), require_dark_reference=True)
        with self.assertRaises(InstrumentException) as ctx:
            meter.open()
        self.assertIn("dark reference", str(ctx.exception).lower())

    def test_missing_dark_reference_only_warns_when_not_required(self):
        meter = make_meter(StubCamera(), require_dark_reference=False)
        meter.open()
        self.assertFalse(meter.dark_reference_available)

    def test_dark_reference_mismatches_are_all_reported_together(self):
        camera = StubCamera([np.full((16, 20), 10, dtype=np.uint8)],
                            exposure_time=5000.0, gain=0.0, pixel_format='Mono8')
        self._capture_dark(camera)

        camera.exposure_time = 9000.0
        camera.gain = 3.0
        meter = make_meter(camera, require_dark_reference=True)
        with self.assertRaises(InstrumentException) as ctx:
            meter.open()
        message = str(ctx.exception)
        self.assertIn("exposure time", message)
        self.assertIn("gain", message)

    def test_dark_reference_shape_mismatch_raises(self):
        camera = StubCamera([np.full((16, 20), 10, dtype=np.uint8)])
        self._capture_dark(camera)

        camera.frames = [np.full((8, 10), 10, dtype=np.uint8)]
        camera.roi = [10, 8, 0, 0]
        meter = make_meter(camera, require_dark_reference=True)
        with self.assertRaises(InstrumentException):
            meter.open()

    def test_dark_reference_from_another_camera_raises(self):
        camera = StubCamera([np.full((16, 20), 10, dtype=np.uint8)], serial='CAM_A')
        self._capture_dark(camera)

        camera.instrument_parameters['camera serial'] = 'CAM_B'
        meter = make_meter(camera, require_dark_reference=True)
        with self.assertRaises(InstrumentException) as ctx:
            meter.open()
        self.assertIn("CAM_A", str(ctx.exception))

    def test_capture_dark_reference_writes_image_and_sidecar(self):
        camera = StubCamera([np.full((16, 20), 12, dtype=np.uint8)])
        metadata = self._capture_dark(camera, frames=8)
        image_path, sidecar_path = PowerMeterCameraAlvium.dark_reference_paths()

        self.assertTrue(os.path.isfile(image_path))
        self.assertTrue(os.path.isfile(sidecar_path))
        self.assertEqual(np.load(image_path).dtype, np.float32)
        with open(sidecar_path) as sidecar_file:
            stored = json.load(sidecar_file)
        self.assertEqual(stored['frames averaged'], 8)
        self.assertEqual(stored['exposure time'], camera.exposure_time)
        self.assertEqual(metadata['shape'], [16, 20])
        json.dumps(stored)

    #
    # saturation
    #

    def test_saturation_raises_for_mono8(self):
        frame = np.zeros((16, 20), dtype=np.uint8)
        frame[5:9, 5:9] = 255
        meter = make_meter(StubCamera([frame]))
        meter.open()
        with self.assertRaises(InstrumentException) as ctx:
            meter.fetch_power()
        self.assertIn("saturated", str(ctx.exception).lower())

    def test_saturation_level_follows_pixel_format_not_dtype(self):
        # Mono12 lives in uint16 but tops out at 4095; using np.iinfo(uint16).max would never fire
        frame = np.full((16, 20), 4095, dtype=np.uint16)
        meter = make_meter(StubCamera([frame], pixel_format='Mono12'))
        meter.open()
        with self.assertRaises(InstrumentException) as ctx:
            meter.fetch_power()
        self.assertIn("4095", str(ctx.exception))

    def test_saturation_outside_the_roi_is_ignored(self):
        frame = np.zeros((16, 20), dtype=np.uint8)
        frame[0:3, 0:3] = 255            # bright corner, outside the ROI
        frame[8:10, 8:10] = 20
        meter = make_meter(StubCamera([frame]), roi_x=6, roi_y=6, roi_width=8, roi_height=8)
        meter.open()
        self.assertAlmostEqual(meter.fetch_power(), 80.0, places=6)

    #
    # roi validation
    #

    def test_roi_outside_the_frame_raises(self):
        meter = make_meter(StubCamera([np.zeros((16, 20), dtype=np.uint8)]),
                           roi_x=15, roi_y=0, roi_width=10, roi_height=4)
        meter.open()
        with self.assertRaises(InstrumentException) as ctx:
            meter.fetch_power()
        self.assertIn("does not fit", str(ctx.exception))

    def test_roi_is_settable(self):
        frame = np.zeros((16, 20), dtype=np.uint8)
        frame[0:4, 0:4] = 7
        meter = make_meter(StubCamera([frame]))
        meter.open()
        meter.roi = [0, 0, 4, 4]
        self.assertEqual(meter.roi, [0, 0, 4, 4])
        self.assertAlmostEqual(meter.fetch_power(), 7.0 * 16, places=6)

    #
    # acquisition protocol
    #

    def test_trigger_then_fetch_acquires_once(self):
        camera = StubCamera([np.ones((8, 8), dtype=np.uint8)])
        meter = make_meter(camera)
        meter.open()
        meter.trigger()
        self.assertEqual(camera.snap_count, 1)
        meter.fetch_power()
        self.assertEqual(camera.snap_count, 1, "fetch_power re-acquired after trigger")

    def test_fetch_without_trigger_still_acquires(self):
        camera = StubCamera([np.ones((8, 8), dtype=np.uint8)])
        meter = make_meter(camera)
        meter.open()
        meter.fetch_power()
        self.assertEqual(camera.snap_count, 1)

    def test_frames_per_sample_averages(self):
        camera = StubCamera([np.full((8, 8), 4, dtype=np.uint8),
                             np.full((8, 8), 8, dtype=np.uint8)])
        meter = make_meter(camera, frames_per_sample=2)
        meter.open()
        self.assertAlmostEqual(meter.fetch_power(), (4 * 64 + 8 * 64) / 2.0, places=6)

    def test_streaming_camera_is_refused(self):
        camera = StubCamera()
        camera.is_streaming = True
        meter = make_meter(camera)
        with self.assertRaises(InstrumentException) as ctx:
            meter.open()
        self.assertIn("Camera View", str(ctx.exception))

    def test_streaming_started_after_open_is_refused_on_read(self):
        camera = StubCamera()
        meter = make_meter(camera)
        meter.open()
        camera.is_streaming = True
        with self.assertRaises(InstrumentException):
            meter.fetch_power()

    def test_settings_change_mid_run_is_detected(self):
        camera = StubCamera([np.ones((8, 8), dtype=np.uint8)])
        meter = make_meter(camera, settings_check_interval=2)
        meter.open()
        meter.trigger()
        camera.exposure_time = 99000.0
        with self.assertRaises(InstrumentException) as ctx:
            meter.trigger()
        self.assertIn("exposure time", str(ctx.exception))

    def test_reading_before_open_raises(self):
        meter = make_meter(StubCamera())
        with self.assertRaises(InstrumentException):
            meter.fetch_power()

    #
    # instrument interface
    #

    def test_power_meter_interface_is_complete(self):
        # everything PeakSearcher, EdgeSearcher and the LiveViewer card touch on a power meter
        for name in ('open', 'close', '_open', 'thread_lock', 'trigger', 'fetch_power', 'power',
                     'logging_stop', 'clear', 'idn', 'get_instrument_parameter', 'channel'):
            self.assertTrue(hasattr(PowerMeterCameraAlvium, name)
                            or hasattr(make_meter(StubCamera()), name),
                            "missing power meter member: " + name)

    def test_is_camera_backed_flag_is_on_the_class(self):
        # consumers probe the class, not the instance
        self.assertTrue(getattr(PowerMeterCameraAlvium, 'IS_CAMERA_BACKED', False))

    def test_instrument_parameters_are_json_serialisable(self):
        camera = StubCamera([np.ones((8, 8), dtype=np.uint8)])
        meter = make_meter(camera)
        meter.open()
        parameters = meter.get_instrument_parameter()
        json.dumps(parameters)
        self.assertEqual(parameters['merit unit'], 'counts')
        self.assertIn('integration roi', parameters)

    def test_idn_never_raises(self):
        meter = make_meter(StubCamera())
        self.assertIsInstance(meter.idn(), str)   # not open yet

    def test_close_is_safe_before_open_and_keeps_camera_by_default(self):
        camera = StubCamera()
        meter = make_meter(camera)
        meter.close()
        meter.open()
        meter.close()
        self.assertTrue(camera._open, "camera should stay open with keep_camera_open")

    def test_close_releases_camera_when_asked(self):
        camera = StubCamera()
        meter = make_meter(camera, keep_camera_open=False)
        meter.open()
        meter.close()
        self.assertFalse(camera._open)


if __name__ == '__main__':
    unittest.main()
