#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
LabExT  Copyright (C) 2021  ETH Zurich and Polariton Technologies AG
This program is free software and comes with ABSOLUTELY NO WARRANTY; for details see LICENSE file.
"""

import os
import tempfile
import unittest
from unittest.mock import patch

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

    #: where the beam sits in the frame, as (y, x)
    spot_position = (24, 32)

    def __init__(self, rate=0.05, pedestal=8.0, spot_sigma=None):
        self.rate = rate
        self.pedestal = pedestal
        # None puts all the light in one pixel, which keeps the sums whole numbers and the
        # assertions exact. A width is for the ROI fit, which needs a shape to fit to.
        self.spot_sigma = spot_sigma
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
        amplitude = self.rate * self._exposure if self.light else 0.0
        image = np.full((48, 64), self.pedestal, dtype=np.float64)
        spot_y, spot_x = self.spot_position
        if self.spot_sigma:
            ys, xs = np.ogrid[:48, :64]
            image += amplitude * np.exp(
                -((ys - spot_y) ** 2 + (xs - spot_x) ** 2) / (2.0 * self.spot_sigma ** 2))
        else:
            image[spot_y, spot_x] += amplitude
        return np.minimum(image, self.clip_level).astype(np.uint16)

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

    min_lambda = 1500.0
    max_lambda = 1630.0

    def __init__(self, camera):
        self.camera = camera
        self.unit = 'dBm'
        self._wavelength = 1550.0
        self.power = -15.0
        #: every wavelength this was set to, in order, to check the sweep steps and their timing
        self.wavelengths_set = []

    @property
    def wavelength(self):
        return self._wavelength

    @wavelength.setter
    def wavelength(self, value):
        self._wavelength = float(value)
        self.wavelengths_set.append(float(value))

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

    def run_algorithm(self, camera=None, stored_integration_roi=None, **overrides):
        """Run the measurement once against the stubs and return `(data, parameters, camera)`.

        The Camera View's stored integration ROI is patched out unless a test asks for one: it is a
        real file in the LabExT settings directory, so whether these tests summed a region or the
        whole frame would otherwise depend on what was last dialled in on the bench.
        """
        camera = camera if camera is not None else StubCamera()
        laser = StubLaser(camera)
        # kept for the tests which check what the laser was told to do; the return value stays a
        # triple so every test does not have to unpack it
        self.laser = laser
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
            with patch('LabExT.Measurements.CameraSnapshot.PowerMeterCameraAlvium'
                       '.load_integration_roi', return_value=stored_integration_roi):
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

    #
    # wavelength sweep
    #

    def test_wavelength_list_covers_the_range_in_both_directions(self):
        sweep = CameraSnapshot._sweep_wavelengths

        # a stop of zero, or one equal to the start, is a single-wavelength capture
        self.assertEqual([1550.0], sweep(1550.0, 0.0, 5.0))
        self.assertEqual([1550.0], sweep(1550.0, 1550.0, 5.0))

        self.assertEqual([1550.0, 1555.0, 1560.0], sweep(1550.0, 1560.0, 5.0))
        self.assertEqual([1560.0, 1555.0, 1550.0], sweep(1560.0, 1550.0, 5.0))
        # the step need not divide the span: the stop is still reached
        np.testing.assert_allclose([1550.0, 1554.0, 1558.0, 1560.0],
                                   sweep(1550.0, 1560.0, 4.0))
        # a step wider than the span gives both ends and nothing between
        self.assertEqual([1550.0, 1560.0], sweep(1550.0, 1560.0, 50.0))

        with self.assertRaises(ValueError):
            sweep(1550.0, 1560.0, 0.0)
        with self.assertRaises(ValueError):
            sweep(1500.0, 1600.0, 0.001)  # 100001 points

    def test_sweep_captures_every_bracket_at_every_wavelength(self):
        data, _, _ = self.run_algorithm(**{'wavelength stop': 1560.0, 'wavelength step': 5.0,
                                           'wavelength settle time': 0.0,
                                           'exposure brackets': 2, 'capture dark frame': True})

        # three wavelengths x two brackets x one frame, in that nesting
        self.assertEqual([1550.0, 1550.0, 1555.0, 1555.0, 1560.0, 1560.0],
                         data['values']['wavelength nm'])
        self.assertEqual([0, 1, 0, 1, 0, 1], data['values']['bracket index'])
        self.assertEqual(list(range(6)), data['values']['frame index'])
        for series in data['values'].values():
            self.assertEqual(6, len(series))

        # the laser was tuned once per step, and the first wavelength was set before it came on
        self.assertEqual([1550.0, 1555.0, 1560.0], self.laser.wavelengths_set)
        self.assertEqual([1550.0, 1555.0, 1560.0],
                         data['measurement settings']['wavelengths nm'])
        self.assertEqual(3, data['measurement settings']['wavelength count'])

    def test_dark_frames_are_taken_once_for_the_whole_sweep(self):
        data, _, _ = self.run_algorithm(**{'wavelength stop': 1570.0, 'wavelength step': 10.0,
                                           'wavelength settle time': 0.0,
                                           'exposure brackets': 3, 'capture dark frame': True})

        # one per bracket, not one per bracket per wavelength: the dark depends on the exposure and
        # the gain, and not on the wavelength
        self.assertEqual(3, len(data['measurement settings']['dark frames']))
        self.assertEqual([0, 1, 2],
                         [d['bracket index'] for d in data['measurement settings']['dark frames']])
        # and every frame of the sweep is corrected by its bracket's dark
        self.assertEqual(9, len(data['values']['dark corrected roi sum counts']))

    def test_single_wavelength_run_is_unchanged(self):
        data, _, _ = self.run_algorithm()

        self.assertEqual([1550.0], data['values']['wavelength nm'])
        self.assertEqual(1, data['measurement settings']['wavelength count'])
        # set once, before the laser was switched on, and not touched again
        self.assertEqual([1550.0], self.laser.wavelengths_set)

    def test_sweep_beyond_the_lasers_range_is_refused_before_capturing(self):
        camera = StubCamera()
        with self.assertRaises(ValueError):
            # the stub laser tunes to 1630 nm
            self.run_algorithm(camera=camera, **{'wavelength stop': 1700.0,
                                                 'wavelength step': 10.0,
                                                 'save TIFF': True})
        self.assertEqual([], camera.saved_files)

    def test_auto_exposure_runs_once_for_the_whole_sweep(self):
        data, _, camera = self.run_algorithm(**{
            'exposure time': 100.0, 'auto exposure': True,
            'wavelength stop': 1560.0, 'wavelength step': 5.0, 'wavelength settle time': 0.0})

        settled = data['measurement settings']['auto exposure result']['exposure time us']
        # one exposure for every wavelength, so a systematic in it cancels between them
        self.assertEqual([settled] * 3, data['values']['exposure time us'])
        self.assertEqual(settled, camera.exposure_time)

    #
    # integration ROI
    #

    #: a region of the stub frame which contains the spot at (y=24, x=32)
    spot_roi = {'integration ROI x': 28, 'integration ROI y': 20,
                'integration ROI width': 10, 'integration ROI height': 10}

    def test_roi_sum_covers_only_the_region_asked_for(self):
        data, _, camera = self.run_algorithm(**self.spot_roi)

        pixels = self.spot_roi['integration ROI width'] * self.spot_roi['integration ROI height']
        peak = data['values']['max counts'][0]
        # pedestal everywhere in the region, plus the one lit pixel
        self.assertEqual([pixels * camera.pedestal + (peak - camera.pedestal)],
                         data['values']['roi sum counts'])
        self.assertEqual([28, 20, 10, 10], data['measurement settings']['integration roi'])
        self.assertEqual(pixels, data['measurement settings']['integration roi pixels'])

    def test_roi_rate_is_flat_across_brackets_where_the_frame_rate_is_not(self):
        """The reason the ROI series exists: on this setup the whole-frame rate moved by 3x."""
        data, _, _ = self.run_algorithm(**dict(
            self.spot_roi, **{'exposure brackets': 3, 'capture dark frame': True}))

        # all the light is inside the region and the stub is exactly linear, so the region's rate
        # is the same at every exposure
        roi_rates = data['values']['roi counts per second']
        np.testing.assert_allclose(roi_rates, roi_rates[0], rtol=1e-9)

        # the whole frame carries the pedestal of every pixel outside the region too. That part is
        # removed by the dark as well, so this stub cannot reproduce the drift seen on the bench -
        # what it does show is that the two series are computed over different areas.
        self.assertNotEqual(data['values']['roi sum counts'],
                            data['values']['dark corrected roi sum counts'])
        self.assertLess(data['values']['dark corrected roi sum counts'][0],
                        data['values']['roi sum counts'][0])

    def test_roi_is_taken_from_the_camera_view_when_unset(self):
        data, parameters, _ = self.run_algorithm(stored_integration_roi=[4, 6, 20, 12])

        self.assertEqual([4, 6, 20, 12], data['measurement settings']['integration roi'])
        # written back into the settings, so the result file says what was summed
        self.assertEqual(4, parameters.get('integration ROI x').value)
        self.assertEqual(20, parameters.get('integration ROI width').value)
        self.assertIn('Camera View', data['measurement settings']['integration roi note'])

    def test_roi_defaults_to_the_whole_frame(self):
        data, _, _ = self.run_algorithm()

        self.assertEqual([0, 0, 64, 48], data['measurement settings']['integration roi'])
        self.assertEqual(64 * 48, data['measurement settings']['integration roi pixels'])
        # with the whole frame summed, the region sum and the frame mean say the same thing
        self.assertAlmostEqual(data['values']['mean counts'][0] * 64 * 48,
                               data['values']['roi sum counts'][0], places=6)

    def test_roi_outside_the_frame_is_refused_before_capturing(self):
        camera = StubCamera()
        with self.assertRaises(ValueError):
            self.run_algorithm(camera=camera, **{'integration ROI x': 60,
                                                 'integration ROI width': 20,
                                                 'integration ROI height': 10})
        # refused up front rather than after writing images nobody can use
        self.assertEqual([], camera.saved_files)

    def test_roi_fitted_to_the_spot_brackets_it(self):
        data, parameters, _ = self.run_algorithm(
            camera=StubCamera(spot_sigma=3.0), **{'fit integration ROI to spot': True,
                                                  'exposure brackets': 2})

        x, y, width, height = data['measurement settings']['integration roi']
        spot_y, spot_x = StubCamera.spot_position
        self.assertTrue(0 < width <= 64 and 0 < height <= 48, (width, height))
        self.assertLess(x, spot_x)
        self.assertGreater(x + width, spot_x)
        self.assertLess(y, spot_y)
        self.assertGreater(y + height, spot_y)
        # one region for the whole run, and the settings record it
        self.assertEqual(x, parameters.get('integration ROI x').value)
        self.assertEqual([x, y, width, height],
                         [data['measurement settings']['integration ROI x']['value'],
                          data['measurement settings']['integration ROI y']['value'],
                          data['measurement settings']['integration ROI width']['value'],
                          data['measurement settings']['integration ROI height']['value']])

    #
    # output paths
    #

    def test_relative_image_directory_lands_next_to_the_results(self):
        class ResultData(dict):
            """An AutosaveDict carries the result file path; a plain dict does not."""
            file_path = None

        with tempfile.TemporaryDirectory(prefix='camera_snapshot_test_') as results_directory:
            data = ResultData()
            data.file_path = os.path.join(results_directory, 'a_run.json.part')

            directory, stem = CameraSnapshot._resolve_output_target('bracket_test', data)
            self.assertEqual(os.path.join(results_directory, 'bracket_test'), directory)
            self.assertEqual('a_run', stem)

            # an absolute path is still taken as given, and an empty one means beside the results
            absolute = os.path.join(results_directory, 'elsewhere')
            self.assertEqual(absolute, CameraSnapshot._resolve_output_target(absolute, data)[0])
            self.assertEqual(results_directory, CameraSnapshot._resolve_output_target('', data)[0])

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
