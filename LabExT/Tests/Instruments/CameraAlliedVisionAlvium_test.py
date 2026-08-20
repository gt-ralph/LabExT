#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
LabExT  Copyright (C) 2021  ETH Zurich and Polariton Technologies AG
This program is free software and comes with ABSOLUTELY NO WARRANTY; for details see LICENSE file.

Required lab setup:
 An Allied Vision Alvium camera connected over USB3, with Vimba X installed and the vmbpy wheel
 shipped with it installed into the active Python environment. No optical setup is needed: the
 tests only check that settings round-trip and that frames have the expected shape and dtype, so
 the camera may well be looking at nothing.

 The Vimba X Viewer (and anything else holding the camera) must be closed, otherwise the camera
 cannot be opened here.
"""

import json
import os
import tempfile
import time
import unittest

import numpy as np

import logging
import threading
from unittest import mock

from LabExT.Instruments.CameraAlliedVisionAlvium import (
    CameraAlliedVisionAlvium, FrameStatus, VMBPY_AVAILABLE, converge_exposure)
from LabExT.Instruments.InstrumentAPI import InstrumentException
from LabExT.Tests.Utils import mark_as_laboratory_test


@mark_as_laboratory_test
class CameraAlliedVisionAlviumTest(unittest.TestCase):

    #
    # test case constants
    #

    # leave as None to simply use the first camera found
    camera_id = None
    instr = None

    #
    # setup and teardown methods
    #

    @classmethod
    def setUpClass(cls) -> None:
        cls.instr = CameraAlliedVisionAlvium(visa_address="None", camera_id=cls.camera_id)
        cls.instr.open()
        cls.tmp_dir = tempfile.mkdtemp(prefix="alvium_test_")

    @classmethod
    def tearDownClass(cls) -> None:
        cls.instr.close()

    # exposure used between test cases. Short enough that every frame beats the default timeout,
    # which matters because the exposure test cases deliberately drive the camera to its maximum
    # exposure of several seconds.
    idle_exposure_time = 10000.0

    def setUp(self) -> None:
        # start every test case from the same camera state, so that ROI, pixel format and exposure
        # changes made by one test case cannot leak into the next
        self.instr.stop_streaming()  # in case a failing streaming test left one running
        self.instr.pixel_format = 'Mono8'
        self.instr.set_roi(0, 0, 0, 0)
        self.instr.exposure_time = self.idle_exposure_time
        self.instr.gain = 0.0

    #
    # test cases
    #

    def test_idn_reports_model_and_serial(self):
        idn = self.instr.idn()
        self.assertIsInstance(idn, str)
        self.assertIn("AlliedVision", idn)
        self.assertNotIn("unavailable", idn)

    def test_open_is_idempotent(self):
        # a second open() on an already open camera must not raise or reconnect
        self.instr.open()
        self.assertTrue(self.instr._open)

    def test_second_instance_shares_the_camera(self):
        # LabExT can end up with more than one driver object for one camera, most easily when it
        # collects instrument metadata from a closed instance while something else is using it.
        # vmbpy reference counts the camera context, so the second instance must work and must not
        # pull the camera out from under the first when it closes.
        second = CameraAlliedVisionAlvium(visa_address="None", camera_id=self.camera_id)
        second.open()
        try:
            self.assertTrue(second._open)
            self.assertEqual(second.snap_photo().shape, (self.instr.height, self.instr.width))
            # metadata must come back with real values, not a degraded error string
            params = second.get_instrument_parameter()
            self.assertNotIn('remote_instrument_properties', params)
        finally:
            second.close()

        self.assertTrue(self.instr._open)
        self.assertIsNotNone(self.instr.snap_photo())

    def test_close_on_never_opened_instance(self):
        # Instrument.__del__ calls close() unconditionally, so this must not raise
        never_opened = CameraAlliedVisionAlvium(visa_address="None")
        never_opened.close()
        never_opened.close()

    def test_exposure_time_round_trip(self):
        minimum, maximum = self.instr.exposure_time_range
        self.assertLess(minimum, maximum)

        for wanted in (minimum, 1000.0, 20000.0, maximum):
            self.instr.exposure_time = wanted
            # the camera snaps onto its own increment grid, so allow one increment of slack
            self.assertAlmostEqual(self.instr.exposure_time, wanted, delta=abs(wanted) * 1e-3 + 20.0)

    def test_exposure_time_is_clamped_not_rejected(self):
        minimum, maximum = self.instr.exposure_time_range
        self.instr.exposure_time = 1e12
        self.assertLessEqual(self.instr.exposure_time, maximum)
        self.instr.exposure_time = -1e12
        self.assertGreaterEqual(self.instr.exposure_time, minimum)

    def test_gain_round_trip(self):
        minimum, maximum = self.instr.gain_range
        for wanted in (minimum, 3.0, 12.5, maximum):
            self.instr.gain = wanted
            self.assertAlmostEqual(self.instr.gain, wanted, places=3)

    def test_gain_is_clamped_not_rejected(self):
        minimum, maximum = self.instr.gain_range
        self.instr.gain = 1e6
        self.assertLessEqual(self.instr.gain, maximum)
        self.instr.gain = -1e6
        self.assertGreaterEqual(self.instr.gain, minimum)

    def test_snap_photo_mono8_is_2d_uint8(self):
        self.instr.pixel_format = 'Mono8'
        image = self.instr.snap_photo()
        self.assertEqual(image.ndim, 2)
        self.assertEqual(image.dtype, np.uint8)
        self.assertEqual(image.shape, (self.instr.height, self.instr.width))

    def test_snap_photo_mono12_is_2d_uint16(self):
        if 'Mono12' not in self.instr.available_pixel_formats:
            self.skipTest("camera does not support Mono12")
        self.instr.pixel_format = 'Mono12'
        image = self.instr.snap_photo()
        self.assertEqual(image.ndim, 2)
        self.assertEqual(image.dtype, np.uint16)

    def test_snap_photo_packed_format_is_converted(self):
        # packed formats have no numpy memory layout in vmbpy and must be converted first
        packed = [f for f in self.instr.available_pixel_formats if f.endswith('p')]
        if not packed:
            self.skipTest("camera offers no packed pixel format")
        self.instr.pixel_format = packed[0]
        image = self.instr.snap_photo()
        self.assertEqual(image.ndim, 2)
        self.assertEqual(image.dtype, np.uint16)
        self.assertEqual(self.instr.get_recent_photo_metadata()['pixel format'], packed[0])

    def test_unsupported_pixel_format_is_rejected(self):
        with self.assertRaises(Exception):
            self.instr.pixel_format = 'NotAPixelFormat'

    def test_snap_photos_returns_requested_count(self):
        images = self.instr.snap_photos(3)
        self.assertEqual(len(images), 3)
        for image in images:
            self.assertEqual(image.shape, (self.instr.height, self.instr.width))

    def test_snapped_image_is_a_copy(self):
        # the array vmbpy hands out only borrows the frame buffer, so the driver must copy it
        first = self.instr.snap_photo()
        original = first.copy()
        self.instr.snap_photo()
        np.testing.assert_array_equal(first, original)

    def test_snapped_image_does_not_pin_the_vmbpy_buffer(self):
        # vmbpy keeps its frame buffer alive through dtype metadata, which numpy carries across a
        # plain copy. If that leaks out of the driver the array silently pins a buffer vmbpy wants
        # to reuse, and numpy refuses to np.save it.
        for image in (self.instr.snap_photo(), self.instr.snap_photos(2)[0]):
            self.assertIsNone(image.dtype.metadata)
            self.assertTrue(image.flags['C_CONTIGUOUS'])
            self.assertTrue(image.flags['OWNDATA'])

    def test_set_roi_round_trip(self):
        width, height, offset_x, offset_y = self.instr.set_roi(640, 480, 96, 96)
        self.assertEqual((width, height), (640, 480))
        self.assertEqual((offset_x, offset_y), (96, 96))
        self.assertEqual(self.instr.snap_photo().shape, (480, 640))

    def test_set_roi_zero_means_full_sensor(self):
        self.instr.set_roi(640, 480, 0, 0)
        width, height, offset_x, offset_y = self.instr.set_roi(0, 0, 0, 0)
        self.assertEqual([width, height], self.instr.sensor_size)
        self.assertEqual((offset_x, offset_y), (0, 0))

    def test_set_roi_snaps_to_increment(self):
        # 13 x 7 is on neither increment grid; the camera must still end up in a valid state
        width, height, _, _ = self.instr.set_roi(13, 7, 0, 0)
        self.assertGreaterEqual(width, 8)
        self.assertGreaterEqual(height, 7)
        self.assertEqual(self.instr.snap_photo().shape, (height, width))

    def test_save_photo_png_and_tiff(self):
        self.instr.snap_photo()
        for name in ('frame.png', 'frame.tif', 'frame.tiff'):
            path = self.instr.save_photo(os.path.join(self.tmp_dir, name))
            self.assertTrue(os.path.isfile(path))
            self.assertGreater(os.path.getsize(path), 0)

    def test_save_photo_rejects_unknown_extension(self):
        self.instr.snap_photo()
        with self.assertRaises(Exception):
            self.instr.save_photo(os.path.join(self.tmp_dir, 'frame.jpg'))

    def test_save_photo_raw_round_trips_values(self):
        image = self.instr.snap_photo()
        path = self.instr.save_photo_raw(os.path.join(self.tmp_dir, 'frame.npy'))
        np.testing.assert_array_equal(np.load(path), image)

    def test_get_recent_photo_matches_last_snap(self):
        image = self.instr.snap_photo()
        np.testing.assert_array_equal(self.instr.get_recent_photo(), image)

    #
    # streaming
    #

    def _collect_stream(self, duration_s=1.0):
        """Stream for a while and return the frames the handler received."""
        frames = []

        def handler(cam, stream, frame):
            frames.append(self.instr.frame_to_array(frame))
            cam.queue_frame(frame)

        self.instr.start_streaming(handler=handler, buffer_count=10)
        try:
            deadline = time.time() + duration_s
            while time.time() < deadline:
                time.sleep(0.02)
        finally:
            self.instr.stop_streaming()
        return frames

    def test_streaming_delivers_frames(self):
        frames = self._collect_stream(duration_s=1.0)
        self.assertGreater(len(frames), 1)
        for image in frames[:5]:
            self.assertEqual(image.shape, (self.instr.height, self.instr.width))
            self.assertIsNone(image.dtype.metadata)

    def test_is_streaming_tracks_state(self):
        self.assertFalse(self.instr.is_streaming)

        def handler(cam, stream, frame):
            cam.queue_frame(frame)

        self.instr.start_streaming(handler=handler)
        try:
            self.assertTrue(self.instr.is_streaming)
        finally:
            self.instr.stop_streaming()
        self.assertFalse(self.instr.is_streaming)

    def test_stop_streaming_is_safe_when_not_streaming(self):
        self.instr.stop_streaming()
        self.instr.stop_streaming()
        self.assertFalse(self.instr.is_streaming)

    def test_starting_twice_is_rejected(self):
        def handler(cam, stream, frame):
            cam.queue_frame(frame)

        self.instr.start_streaming(handler=handler)
        try:
            with self.assertRaises(Exception):
                self.instr.start_streaming(handler=handler)
        finally:
            self.instr.stop_streaming()

    def test_exposure_and_gain_change_while_streaming(self):
        received = []

        def handler(cam, stream, frame):
            received.append(1)
            cam.queue_frame(frame)

        self.instr.start_streaming(handler=handler)
        try:
            time.sleep(0.3)
            before = len(received)
            self.instr.exposure_time = 15000.0
            self.instr.gain = 5.0
            time.sleep(0.5)
            # settings took effect and frames kept flowing
            self.assertAlmostEqual(self.instr.gain, 5.0, places=3)
            self.assertGreater(len(received), before)
        finally:
            self.instr.stop_streaming()

    def test_snap_photo_is_rejected_while_streaming(self):
        def handler(cam, stream, frame):
            cam.queue_frame(frame)

        self.instr.start_streaming(handler=handler)
        try:
            with self.assertRaises(Exception):
                self.instr.snap_photo()
            with self.assertRaises(Exception):
                self.instr.snap_photos(2)
        finally:
            self.instr.stop_streaming()

    def test_snap_photo_works_again_after_streaming(self):
        self._collect_stream(duration_s=0.3)
        image = self.instr.snap_photo()
        self.assertEqual(image.shape, (self.instr.height, self.instr.width))

    def test_close_stops_an_active_stream(self):
        separate = CameraAlliedVisionAlvium(visa_address="None", camera_id=self.camera_id)
        separate.open()

        def handler(cam, stream, frame):
            cam.queue_frame(frame)

        separate.start_streaming(handler=handler)
        self.assertTrue(separate.is_streaming)
        separate.close()  # must stop the stream, not leave it dangling
        self.assertFalse(separate._open)
        # the shared camera object must be usable again straight away
        self.assertIsNotNone(self.instr.snap_photo())

    def test_instrument_parameters_are_json_serialisable(self):
        # LabExT writes these into every measurement result file, so numpy types would break it
        params = self.instr.get_instrument_parameter()
        json.dumps(params)
        for key in ('idn', 'exposure_time', 'gain', 'pixel_format', 'width', 'height'):
            self.assertIn(key, params)


if __name__ == '__main__':
    unittest.main()


@mark_as_laboratory_test
class CameraAlliedVisionAlviumStreamingSettingsTest(unittest.TestCase):
    """Changing frame-layout settings while the camera streams.

    The camera locks the pixel format and the sensor ROI once acquisition starts, so re-applying a
    value that already matches must be a no-op rather than a failure. A multi-device run of
    CameraSnapshot with the live view open failed on exactly this: every device tried to set the
    pixel format it was already at.
    """

    camera_id = None

    @classmethod
    def setUpClass(cls) -> None:
        cls.instr = CameraAlliedVisionAlvium(visa_address="None", camera_id=cls.camera_id)
        cls.instr.open()

    @classmethod
    def tearDownClass(cls) -> None:
        cls.instr.stop_streaming()
        cls.instr.close()

    def setUp(self) -> None:
        self.instr.stop_streaming()
        self.instr.pixel_format = 'Mono8'
        self.instr.set_roi(0, 0, 0, 0)

    def _start_stream(self):
        self.instr.start_streaming()
        self.addCleanup(self.instr.stop_streaming)

    def test_reapplying_the_same_pixel_format_while_streaming_is_a_no_op(self):
        self._start_stream()
        self.instr.pixel_format = 'Mono8'          # must not raise
        self.assertEqual(self.instr.pixel_format, 'Mono8')

    def test_changing_the_pixel_format_while_streaming_is_refused_clearly(self):
        self._start_stream()
        with self.assertRaises(InstrumentException) as ctx:
            self.instr.pixel_format = 'Mono12'
        self.assertIn("streaming", str(ctx.exception))
        self.assertIn("Camera View", str(ctx.exception))

    def test_reapplying_the_same_roi_while_streaming_is_a_no_op(self):
        self._start_stream()
        self.assertEqual(self.instr.set_roi(0, 0, 0, 0), self.instr.roi)

    def test_changing_the_roi_while_streaming_is_refused_clearly(self):
        self._start_stream()
        with self.assertRaises(InstrumentException) as ctx:
            self.instr.set_roi(640, 480, 0, 0)
        self.assertIn("streaming", str(ctx.exception))

    def test_exposure_and_gain_still_change_while_streaming(self):
        self._start_stream()
        self.instr.exposure_time = 15000.0
        self.instr.gain = 2.0
        self.assertAlmostEqual(self.instr.exposure_time, 15000.0, delta=50.0)
        self.assertAlmostEqual(self.instr.gain, 2.0, places=2)

    def test_settings_still_change_when_not_streaming(self):
        self.instr.pixel_format = 'Mono12'
        self.assertEqual(self.instr.pixel_format, 'Mono12')
        self.assertEqual(self.instr.set_roi(640, 480, 0, 0)[:2], [640, 480])


class ConvergeExposureTest(unittest.TestCase):
    """
    Tests for the shared auto-exposure routine, against stub cameras.

    Required lab setup: none, only SW testing. Deliberately not part of the class above: the
    convergence is what a measurement, the live Camera View and a peak search all call, so it has
    to be checkable without the camera being on the bench.
    """

    user_input_required = False

    class SnappingCamera:
        """A linear sensor that hands over a frame on demand, exposed as asked."""

        exposure_time_range = [20.0, 1000000.0]
        is_streaming = False
        pixel_format = 'Mono12'

        def __init__(self, rate=0.05, full_scale=4095.0):
            self.rate = rate
            self.full_scale = full_scale
            self._exposure = 100.0
            self.frames_taken = 0

        @property
        def exposure_time(self):
            return self._exposure

        @exposure_time.setter
        def exposure_time(self, value):
            low, high = self.exposure_time_range
            self._exposure = float(min(max(round(float(value)), low), high))

        def _frame_at(self, exposure):
            self.frames_taken += 1
            frame = np.zeros((8, 8), dtype=np.uint16)
            frame[4, 4] = min(self.rate * exposure, self.full_scale - 1.0)
            return frame

        def snap_photo(self, timeout_ms=None):
            return self._frame_at(self._exposure)

    class StreamingCamera(SnappingCamera):
        """The same sensor, but read through a stream which is two frames behind the setting.

        That lag is the point: frames already in flight when the exposure changes were exposed
        before it, so a loop which measures the very next frame scores the exposure it just left.
        """

        is_streaming = True
        LAG = 2

        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            self._pipeline = [self._exposure] * self.LAG
            self._counter = 0

        def latest_streamed_frame(self, newer_than=None, timeout_s=5.0):
            exposed_at = self._pipeline.pop(0)
            self._pipeline.append(self._exposure)
            self._counter += 1
            return self._frame_at(exposed_at), self._counter

    def test_snapping_camera_converges_on_the_target(self):
        camera = self.SnappingCamera()
        outcome = converge_exposure(camera, full_scale=4095.0, target_fill=0.7)

        self.assertEqual('converged', outcome['status'])
        self.assertAlmostEqual(0.7, outcome['peak fill'], delta=0.07)
        # the fill reported was measured at the exposure reported
        self.assertEqual(camera.exposure_time, outcome['exposure time us'])

    def test_streaming_camera_converges_to_the_same_exposure(self):
        """The frames in flight must not be what it converges on; the stale ones are dropped."""
        snapped = self.SnappingCamera()
        converge_exposure(snapped, full_scale=4095.0, target_fill=0.7)

        streamed = self.StreamingCamera()
        outcome = converge_exposure(streamed, full_scale=4095.0, target_fill=0.7)

        self.assertEqual('converged', outcome['status'])
        self.assertEqual(snapped.exposure_time, streamed.exposure_time)
        # it costs frames to let the pipeline drain, which is why it is bounded
        self.assertGreater(streamed.frames_taken, snapped.frames_taken)

    def test_stops_at_the_maximum_it_is_given(self):
        camera = self.SnappingCamera(rate=1e-5)  # too insensitive to reach the target in range
        outcome = converge_exposure(camera, full_scale=4095.0, target_fill=0.7,
                                    max_exposure=50000.0)

        self.assertEqual('at a limit', outcome['status'])
        self.assertEqual(50000.0, camera.exposure_time)
        self.assertLess(outcome['peak fill'], 0.7)
        self.assertIn('stuck', outcome['note'])

    def test_a_dark_view_goes_to_the_longest_exposure_allowed(self):
        camera = self.SnappingCamera(rate=0.0)  # nothing to see at any exposure
        outcome = converge_exposure(camera, full_scale=4095.0, target_fill=0.7,
                                    max_exposure=30000.0)

        self.assertNotEqual('converged', outcome['status'])
        self.assertEqual(30000.0, camera.exposure_time)

    def test_a_clipped_view_takes_more_than_one_pass(self):
        camera = self.SnappingCamera(rate=50.0)  # railed at the starting exposure
        outcome = converge_exposure(camera, full_scale=4095.0, target_fill=0.7)

        self.assertEqual('converged', outcome['status'])
        self.assertGreater(outcome['frames used'], 1)
        self.assertAlmostEqual(0.7, outcome['peak fill'], delta=0.07)

    def test_a_hot_pixel_outside_the_region_is_ignored(self):
        """The reason `region` exists: a hot pixel grows with the exposure and outshines a weak spot.

        Without it the exposure gets scaled until the defect reads the target fill, which leaves
        the beam far under-exposed - measured on this camera at 41.6 counts per millisecond, so it
        wins outright past a few tens of milliseconds.
        """
        class HotPixelCamera(ConvergeExposureTest.SnappingCamera):
            HOT_COUNTS_PER_MS = 41.6

            def _frame_at(self, exposure):
                frame = super()._frame_at(exposure)
                frame[1, 7] = min(self.HOT_COUNTS_PER_MS * exposure / 1000.0, self.full_scale - 1.0)
                return frame

        beam = (2, 2, 4, 4)  # the spot at (4, 4) with room around it; the hot pixel is outside

        blind = HotPixelCamera(rate=0.005)
        converge_exposure(blind, full_scale=4095.0, target_fill=0.7)
        aimed = HotPixelCamera(rate=0.005)
        outcome = converge_exposure(aimed, full_scale=4095.0, target_fill=0.7, region=beam)

        self.assertEqual('converged', outcome['status'])
        self.assertAlmostEqual(0.7, outcome['peak fill'], delta=0.07)
        # the hot pixel is 8x more sensitive than the beam here, so filling it instead lands an
        # order of magnitude short on exposure
        self.assertGreater(aimed.exposure_time, 5.0 * blind.exposure_time)

    def test_converges_from_a_badly_clipped_start(self):
        """A clipped frame hides its own peak, so scaling by it only ever asks for a 30% cut.

        Seen on the bench: a run inherited 27 ms from a measurement in a shallower pixel format,
        which in Mono12 is about 60x over. Five frames of ratio steps got a quarter of the way and
        the run captured with the longest bracket clipped and the fitted region twice the size it
        should have been, because a clipped plateau looks like a fat spot.
        """
        camera = self.SnappingCamera(rate=0.05)
        camera.exposure_time = 400000.0  # about 60x too long, railed from the first frame

        outcome = converge_exposure(camera, full_scale=4095.0, target_fill=0.7)

        self.assertEqual('converged', outcome['status'], outcome['note'])
        self.assertAlmostEqual(0.7, outcome['peak fill'], delta=0.07)
        self.assertLessEqual(outcome['frames used'], 5)


@unittest.skipUnless(VMBPY_AVAILABLE, "needs vmbpy for the frame status values")
class IncompleteFrameTest(unittest.TestCase):
    """
    Tests that a frame the link dropped costs a frame, not a run.

    Required lab setup: none. The camera is faked; only the retry policy is under test, which is
    the part that decided a minute-old sweep was worthless when one frame arrived incomplete.
    """

    user_input_required = False

    class FrameStub:
        def __init__(self, complete):
            self._complete = complete

        def get_status(self):
            return FrameStatus.Complete if self._complete else FrameStatus.Incomplete

        def get_id(self):
            return 0

        def get_timestamp(self):
            return 0

    class CameraStub:
        """Hands out a scripted sequence of complete and incomplete frames."""

        def __init__(self, pattern):
            self.pattern = list(pattern)
            self.frames_handed_out = 0

        def _next(self):
            complete = self.pattern[min(self.frames_handed_out, len(self.pattern) - 1)]
            self.frames_handed_out += 1
            return IncompleteFrameTest.FrameStub(complete)

        def get_frame(self, timeout_ms=None):
            return self._next()

        def get_frame_generator(self, limit, timeout_ms=None):
            for _ in range(limit):
                yield self._next()

    def make_camera(self, pattern):
        """A driver with its device access faked out, and nothing else changed."""
        camera = object.__new__(CameraAlliedVisionAlvium)
        camera.logger = logging.getLogger()
        camera._cam_lock = threading.RLock()
        camera._cam = self.CameraStub(pattern)
        camera._timeout_ms = 1000
        camera._last_image = None
        camera._last_meta = {}
        # the properties these methods touch, faked at the class level so the real code runs
        patches = [
            mock.patch.object(CameraAlliedVisionAlvium, '_open', property(lambda self: True)),
            mock.patch.object(CameraAlliedVisionAlvium, 'is_streaming', property(lambda self: False)),
            mock.patch.object(CameraAlliedVisionAlvium, 'exposure_time', property(lambda self: 1.0)),
            mock.patch.object(CameraAlliedVisionAlvium, 'gain', property(lambda self: 0.0)),
            mock.patch.object(CameraAlliedVisionAlvium, 'roi', property(lambda self: [8, 8, 0, 0])),
            mock.patch.object(CameraAlliedVisionAlvium, 'pixel_format', property(lambda self: 'Mono12')),
            mock.patch.object(CameraAlliedVisionAlvium, '_frame_to_array',
                              lambda self, frame, squeeze=True: (np.zeros((8, 8), np.uint16),
                                                                 'Mono12', 'Mono12')),
        ]
        for patcher in patches:
            patcher.start()
            self.addCleanup(patcher.stop)
        return camera

    def test_snap_photo_retries_past_a_dropped_frame(self):
        camera = self.make_camera([False, False, True])
        image = camera.snap_photo()
        self.assertEqual((8, 8), image.shape)
        self.assertEqual(3, camera._cam.frames_handed_out)

    def test_snap_photo_gives_up_on_a_run_of_them(self):
        camera = self.make_camera([False])
        with self.assertRaises(InstrumentException) as ctx:
            camera.snap_photo()
        self.assertIn("throughput_limit", str(ctx.exception))

    def test_snap_photos_spends_spare_frames_on_the_dropped_ones(self):
        # one dropped frame in the middle of a burst of three
        camera = self.make_camera([True, False, True, True])
        images = camera.snap_photos(3)
        self.assertEqual(3, len(images))
        self.assertEqual(4, camera._cam.frames_handed_out, "should have taken one spare frame")

    def test_snap_photos_stops_at_the_count_it_was_asked_for(self):
        camera = self.make_camera([True])
        self.assertEqual(2, len(camera.snap_photos(2)))
        self.assertEqual(2, camera._cam.frames_handed_out, "must not use the spares when clean")

    def test_snap_photos_reports_how_many_the_link_dropped(self):
        camera = self.make_camera([False])
        with self.assertRaises(InstrumentException) as ctx:
            camera.snap_photos(2)
        self.assertIn("0 of 2 frames arrived complete", str(ctx.exception))
