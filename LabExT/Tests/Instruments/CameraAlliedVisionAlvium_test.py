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

from LabExT.Instruments.CameraAlliedVisionAlvium import CameraAlliedVisionAlvium
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
