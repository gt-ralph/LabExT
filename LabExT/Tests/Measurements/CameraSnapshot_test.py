#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
LabExT  Copyright (C) 2021  ETH Zurich and Polariton Technologies AG
This program is free software and comes with ABSOLUTELY NO WARRANTY; for details see LICENSE file.
"""

import os
import tempfile
import unittest

import numpy as np

from LabExT.Measurements.CameraSnapshot import CameraSnapshot
from LabExT.Measurements.MeasAPI import Measurement


class StubCamera:
    """A perfectly linear sensor: counts = pedestal + rate * exposure, clipped below full scale.

    Linear on purpose. It is what lets the tests below check the arithmetic the measurement does
    for a relative response - dark subtraction and dividing by the exposure - by asserting that
    every bracket reports the same rate, which only holds if both are right.
    """

    #: one below the Mono12 full scale of 4095, as the real Alvium clips
    clip_level = 4094.0

    def __init__(self, rate=0.05, pedestal=8.0):
        self.rate = rate
        self.pedestal = pedestal
        self._exposure = 10000.0
        self.gain = 0.0
        self.pixel_format = 'Mono12'
        self.is_streaming = False
        self.roi = (64, 48, 0, 0)
        self.light = False  # switched by the stub laser's context manager
        self.saved_files = []
        self.exposures_used = []

    @property
    def exposure_time(self):
        return self._exposure

    @exposure_time.setter
    def exposure_time(self, value):
        low, high = self.exposure_time_range
        # snapped onto a 1 us grid, as the camera snaps onto its own increment
        self._exposure = float(min(max(round(float(value)), low), high))

    @property
    def exposure_time_range(self):
        return [20.0, 1000000.0]

    def set_roi(self, *args, **kwargs):
        pass

    def open(self):
        pass

    def close(self):
        pass

    def _frame(self):
        self.exposures_used.append(self._exposure)
        peak = self.pedestal + (self.rate * self._exposure if self.light else 0.0)
        image = np.full((48, 64), self.pedestal, dtype=np.uint16)
        image[24, 32] = min(peak, self.clip_level)
        return image

    def snap_photo(self, timeout_ms=None):
        return self._frame()

    def snap_photos(self, count, timeout_ms=None):
        return [self._frame() for _ in range(count)]

    def save_photo(self, file_path, image=None, overwrite=True):
        self.saved_files.append(os.path.basename(file_path))
        return file_path

    def save_photo_raw(self, file_path, image=None):
        self.saved_files.append(os.path.basename(file_path))
        return file_path

    @staticmethod
    def stretch_to_8bit(image):
        return image


class StubLaser:
    """Gates the stub camera's light the way the real laser gates the chip's."""

    def __init__(self, camera):
        self.camera = camera
        self.unit = 'dBm'
        self.wavelength = 1550.0
        self.power = -15.0

    def open(self):
        pass

    def close(self):
        pass

    def __enter__(self):
        self.camera.light = True

    def __exit__(self, exc_type, exc_value, traceback):
        self.camera.light = False


class CameraSnapshotTest(unittest.TestCase):
    """
    Tests for the CameraSnapshot measurement, driven with a stub camera and laser.

    Required lab setup: none, only SW testing
    """

    #
    # test case constants
    #

    user_input_required = False

    #: what the frames are captured at unless a test says otherwise. Long enough that the ladder
    #: below it stays well clear of the stub camera's shortest exposure.
    exposure_time = 40000.0

    #
    # helpers
    #

    def run_algorithm(self, camera=None, **overrides):
        """Run the measurement once against the stubs and return `(data, parameters, camera)`."""
        camera = camera if camera is not None else StubCamera()
        laser = StubLaser(camera)
        measurement = CameraSnapshot()

        parameters = measurement.parameters
        settings = {
            # explicit rather than defaulted: get_default_parameter() overlays whatever the Camera
            # View last handed over, which would otherwise decide what these tests capture
            'exposure time': self.exposure_time,
            'gain': 0.0,
            'pixel format': 'Mono12',
            'laser enabled': True,
            'laser settle time': 0.0,
            'number of frames': 1,
            'inter-frame delay': 0.0,
            'frame timeout': 5000.0,
            'save TIFF': False,
            'save PNG': False,
            'save NPY': False,
        }
        settings.update(overrides)
        for name, value in settings.items():
            parameters.get(name).value = value

        data = Measurement.setup_return_dict()
        with tempfile.TemporaryDirectory(prefix='camera_snapshot_test_') as directory:
            parameters.get('image output directory').value = directory
            measurement.algorithm(None, data=data,
                                  instruments={'Camera': camera, 'Laser': laser},
                                  parameters=parameters)
        measurement._check_data(data=data)
        return data, parameters, camera

    #
    # test cases
    #

    def test_single_frame_records_one_of_everything(self):
        data, parameters, camera = self.run_algorithm(**{'save TIFF': True, 'save PNG': True})

        for series in data['values'].values():
            self.assertEqual(1, len(series))
        self.assertEqual([0], data['values']['frame index'])
        self.assertEqual([0], data['values']['bracket index'])
        self.assertEqual([self.exposure_time], data['values']['exposure time us'])
        self.assertEqual(1, data['measurement settings']['exposure brackets captured'])
        self.assertNotIn('dark frames', data['measurement settings'])
        self.assertFalse(data['measurement settings']['counts per second dark corrected'])
        # a run without brackets keeps the file names it has always used
        self.assertEqual(['CameraSnapshot_frame000.tif', 'CameraSnapshot_frame000.png'],
                         camera.saved_files)

    def test_brackets_descend_from_the_exposure_time(self):
        data, _, _ = self.run_algorithm(**{'exposure brackets': 3,
                                           'exposure bracket factor': 4.0})

        self.assertEqual([0, 1, 2], data['values']['frame index'])
        self.assertEqual([0, 1, 2], data['values']['bracket index'])
        self.assertEqual([40000.0, 10000.0, 2500.0], data['values']['exposure time us'])
        self.assertEqual(3, data['measurement settings']['exposure brackets captured'])

    def test_frames_are_numbered_through_the_brackets(self):
        data, _, camera = self.run_algorithm(**{'exposure brackets': 2, 'number of frames': 2,
                                                'save TIFF': True})

        self.assertEqual([0, 1, 2, 3], data['values']['frame index'])
        self.assertEqual([0, 0, 1, 1], data['values']['bracket index'])
        self.assertEqual(['CameraSnapshot_frame000.tif', 'CameraSnapshot_frame001.tif',
                          'CameraSnapshot_frame002.tif', 'CameraSnapshot_frame003.tif'],
                         camera.saved_files)

    def test_dark_frames_are_taken_per_bracket_with_the_light_off(self):
        camera = StubCamera()
        data, _, camera = self.run_algorithm(camera=camera, **{
            'exposure brackets': 3, 'capture dark frame': True, 'save TIFF': True})

        darks = data['measurement settings']['dark frames']
        self.assertEqual([0, 1, 2], [dark['bracket index'] for dark in darks])
        self.assertEqual([40000.0, 10000.0, 2500.0],
                         [dark['exposure time us'] for dark in darks])
        # the light was gated off for all of them, so each holds the pedestal and nothing else
        self.assertEqual([camera.pedestal] * 3, [dark['mean counts'] for dark in darks])
        self.assertEqual(['CameraSnapshot_dark000.tif', 'CameraSnapshot_dark001.tif',
                          'CameraSnapshot_dark002.tif'], camera.saved_files[3:])

    def test_dark_subtraction_makes_the_brackets_agree(self):
        """The point of the whole feature: one rate, whatever exposure it was measured at."""
        data, _, _ = self.run_algorithm(**{'exposure brackets': 3, 'capture dark frame': True})

        self.assertTrue(data['measurement settings']['counts per second dark corrected'])
        rates = data['values']['mean counts per second']
        np.testing.assert_allclose(rates, rates[0], rtol=1e-9)

        # without the dark frame the pedestal is still in there, and since it does not scale with
        # the exposure it lands on the short brackets as a rate far above the true one
        uncorrected, _, _ = self.run_algorithm(**{'exposure brackets': 3})
        raw_rates = uncorrected['values']['mean counts per second']
        self.assertGreater(raw_rates[-1], 10.0 * raw_rates[0])

    def test_dark_frame_is_skipped_when_the_laser_is_off(self):
        data, _, camera = self.run_algorithm(**{'laser enabled': False,
                                                'capture dark frame': True})

        # nothing to gate, so the frames already are dark frames
        self.assertNotIn('dark frames', data['measurement settings'])
        self.assertFalse(data['measurement settings']['counts per second dark corrected'])
        self.assertFalse(data['measurement settings']['laser on during capture'])

    def test_auto_exposure_reaches_the_target_fill(self):
        data, parameters, camera = self.run_algorithm(**{
            'exposure time': 100.0,  # three decades short of the target
            'auto exposure': True,
            'auto exposure target fill': 0.7,
            'auto exposure max': 200000.0})

        result = data['measurement settings']['auto exposure result']
        self.assertTrue(result['converged'])
        self.assertAlmostEqual(0.7, result['peak fill'], delta=0.07)
        # the frames were taken at what it settled on, and the recorded settings say so
        self.assertEqual(result['exposure time us'], camera.exposure_time)
        self.assertEqual(result['exposure time us'], parameters.get('exposure time').value)
        self.assertEqual([result['exposure time us']], data['values']['exposure time us'])
        self.assertEqual(result['exposure time us'],
                         data['measurement settings']['exposure time']['value'])

    def test_auto_exposure_stops_at_its_maximum(self):
        # a sensor this insensitive cannot reach the target within the allowed exposure
        data, _, camera = self.run_algorithm(camera=StubCamera(rate=1e-5), **{
            'auto exposure': True, 'auto exposure max': 50000.0})

        result = data['measurement settings']['auto exposure result']
        self.assertFalse(result['converged'])
        self.assertEqual(50000.0, camera.exposure_time)
        self.assertLess(result['peak fill'], 0.7)

    def test_auto_exposure_leads_the_bracket_ladder(self):
        data, _, _ = self.run_algorithm(**{
            'exposure time': 100.0, 'auto exposure': True, 'exposure brackets': 3,
            'capture dark frame': True})

        anchor = data['measurement settings']['auto exposure result']['exposure time us']
        exposures = data['values']['exposure time us']
        self.assertEqual(anchor, exposures[0])
        np.testing.assert_allclose([anchor / 4.0, anchor / 16.0], exposures[1:], rtol=1e-3)
        # The rate is the same whichever bracket it comes from, auto exposed or not. Not to the
        # last digit: an auto exposed anchor divided by the factor lands between the camera's
        # increments, and the peak pixel is a whole number of counts, so the shortest bracket
        # carries a percent or so of quantisation.
        rates = data['values']['mean counts per second']
        np.testing.assert_allclose(rates, rates[0], rtol=1e-2)

    def test_ladder_stops_at_the_cameras_shortest_exposure(self):
        # 200, 50, then 12.5 which the camera cannot do, so the ladder ends at its minimum of 20
        data, _, _ = self.run_algorithm(**{'exposure time': 200.0, 'exposure brackets': 4})

        self.assertEqual([200.0, 50.0, 20.0], data['values']['exposure time us'])
        self.assertEqual(3, data['measurement settings']['exposure brackets captured'])

    def test_clipping_is_reported_per_frame(self):
        data, _, _ = self.run_algorithm(camera=StubCamera(rate=5.0), **{
            'exposure brackets': 2})

        # both brackets are railed, so neither measures the power here
        for fraction in data['values']['saturated pixel fraction']:
            self.assertGreater(fraction, 0.0)
        self.assertEqual(4094.0, data['measurement settings']['saturation count level'])
        self.assertEqual(4095.0, data['measurement settings']['full scale counts'])

    def test_camera_is_left_on_the_recorded_exposure(self):
        _, parameters, camera = self.run_algorithm(**{'exposure brackets': 3,
                                                      'capture dark frame': True})

        # not on the last and shortest bracket, which the live view would then be stuck with
        self.assertEqual(parameters.get('exposure time').value, camera.exposure_time)

    def test_rejects_settings_which_cannot_be_captured(self):
        for overrides, why in [
                ({'exposure brackets': 0}, "no brackets to capture"),
                ({'exposure brackets': 3, 'exposure bracket factor': 1.0},
                 "brackets which do not differ"),
                ({'number of frames': 0}, "no frames to capture"),
                ({'laser settle time': -1.0}, "a negative settle time"),
                ({'auto exposure': True, 'auto exposure target fill': 1.5},
                 "a target fill beyond full scale"),
                ({'auto exposure': True, 'auto exposure max': 0.0},
                 "no exposure to work with"),
                ({'auto exposure': True, 'auto exposure max': 9e6},
                 "an exposure longer than the frame timeout")]:
            with self.subTest(why):
                with self.assertRaises(ValueError):
                    self.run_algorithm(**overrides)

    def test_streaming_camera_keeps_its_own_exposure(self):
        camera = StubCamera()
        camera.is_streaming = True
        camera.exposure_time = 12345.0

        data, _, camera = self.run_algorithm(camera=camera, **{
            'exposure brackets': 3, 'auto exposure': True})

        # the frame layout is fixed once the live view is streaming, so neither feature runs and
        # the exposure it dialled in is left alone
        self.assertEqual(12345.0, camera.exposure_time)
        self.assertEqual([12345.0], data['values']['exposure time us'])
        self.assertEqual(1, data['measurement settings']['exposure brackets captured'])
        self.assertNotIn('auto exposure result', data['measurement settings'])
