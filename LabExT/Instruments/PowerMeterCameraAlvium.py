#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
LabExT  Copyright (C) 2021  ETH Zurich and Polariton Technologies AG
This program is free software and comes with ABSOLUTELY NO WARRANTY; for details see LICENSE file.
"""

import json
import math
import os
import re
import threading
from datetime import datetime, timezone

import numpy as np

from LabExT.Instruments.CameraAlliedVisionAlvium import CameraAlliedVisionAlvium
from LabExT.Instruments.InstrumentAPI import Instrument, InstrumentException
from LabExT.Utils import get_configuration_file_path

#: pixel formats carry their bit depth in the name, e.g. Mono12 -> 12 bits -> full scale 4095
_BIT_DEPTH_PATTERN = re.compile(r'(\d+)')

#: reported instead of -inf when the integrated intensity is not positive, see _reduce()
MINIMUM_DB = -300.0

#: default fraction of ROI pixels at full scale which is treated as "the spot is saturated"
DEFAULT_SATURATION_FRACTION_LIMIT = 1e-4

#: how many trigger() calls between re-reads of the camera's exposure and gain
DEFAULT_SETTINGS_CHECK_INTERVAL = 20


class PowerMeterCameraAlvium(Instrument):
    """
    ### Beam intensity on an Allied Vision Alvium camera, presented as a power meter

    Sums the pixel values inside a region of interest and reports that as the reading, so that any
    part of LabExT which expects an optical power meter can be driven by the image of a beam spot
    instead. The main use is the Search for Peak: select this instead of a photodiode and the
    alignment optimises the light landing on the camera.

    Register it under the `Power Meter` role in instruments.config, alongside the real power meters.
    It appears in the instrument dropdowns by class name, so it can be picked per meter slot.

    #### what the reading means

    The value is the sum of the counts in the integration ROI, with a reference dark frame
    subtracted. That sum is proportional to the optical power falling in the ROI, but it is in
    camera counts, not watts: it is only meaningful relative to other readings taken with the same
    exposure, gain and pixel format. This is exactly what a peak search needs, since it only ever
    compares readings to each other.

    `unit` selects between raw counts and `10*log10(counts)`. Counts is the better default: the
    intensity profile of a beam is Gaussian in linear units, which is the shape the Search for Peak
    fits.

    #### dark reference

    A dark frame captured with the beam blocked is subtracted from every frame. Without it the
    sensor's own offset dominates the sum and swamps the changes the search is trying to follow.
    Capture one with the button in the Camera View, or with `capture_dark_reference()`.

    The dark frame is only valid for the exposure, gain, pixel format and camera ROI it was taken
    at, so `open()` refuses to run if any of those have moved since. Recapture after changing them.

    #### constructor keyword arguments

    All come from the `args` dict of the instruments.config entry.

    Forwarded to the camera driver: `camera_id`, `exposure_time` (us), `gain` (dB), `pixel_format`,
    `roi` (the camera's *hardware* ROI, `[width, height, offset_x, offset_y]`), `timeout_ms`,
    `throughput_limit`.

    Used by this class:

    * `roi_x`, `roi_y`, `roi_width`, `roi_height` (int): the **integration** ROI, in pixels of the
      frame the camera delivers. A width or height of 0 means the whole frame. Deliberately four
      separate values rather than a list, so it cannot be confused with the camera's `roi`, which is
      also four numbers but in a different order.
    * `merit_unit` (str): `'counts'` (default) or `'dB'`.
    * `frames_per_sample` (int): frames averaged per reading, default 1. The Search for Peak does its
      own averaging over time, so leave this at 1 unless you want averaging elsewhere too.
    * `saturation_fraction_limit` (float): fraction of ROI pixels at full scale which counts as
      saturated, default 1e-4. Reading a saturated spot raises.
    * `require_dark_reference` (bool): default True. Set false to run without one, which logs a
      warning and skips the subtraction.
    * `dark_reference_file` (str): file name in the LabExT settings directory, default
      `camera_dark_reference.npy`.
    * `clip_negative` (bool): default False. See `_integrate` for why clipping is the wrong default.
    * `keep_camera_open` (bool): default True, so `close()` leaves the camera connected.
    * `stop_streaming_on_open` (bool): default False. See `_ensure_not_streaming`.

    #### example instruments.config entry

    ```
    {"visa": "None", "class": "PowerMeterCameraAlvium", "channels": [0],
     "args": {"camera_id": "DEV_1AB22C0B3989", "pixel_format": "Mono8",
              "exposure_time": 5000.0, "gain": 0.0,
              "roi_x": 520, "roi_y": 400, "roi_width": 240, "roi_height": 240}}
    ```
    """

    #: Marks this class as reporting camera counts rather than optical power. Consumers check it on
    #: the class (not the instance) to decide whether dBm-specific settings apply. An explicit flag
    #: rather than probing for `unit`, which the SCPI power meters also declare.
    IS_CAMERA_BACKED = True

    #: keys handed straight to the camera driver
    CAMERA_ARGUMENT_NAMES = ('camera_id', 'exposure_time', 'gain', 'pixel_format', 'roi',
                             'timeout_ms', 'throughput_limit')

    DEFAULT_DARK_REFERENCE_FILE = 'camera_dark_reference.npy'

    MINIMUM_DB = MINIMUM_DB

    def __init__(self, *args, **kwargs):
        # before super(): a failure in the parent constructor still leaves __del__ -> close() safe
        self._camera = None
        self._capture_lock = threading.Lock()
        self._pending_frames = None
        self._dark_reference = None
        self._dark_metadata = {}
        self._last_statistics = {}
        self._reference_settings = None
        self._triggers_since_settings_check = 0

        super().__init__(*args, **kwargs)

        self._category = "Power Meter"

        self._merit_unit = self._normalised_unit(self._kwargs.get('merit_unit', 'counts'))
        self._roi_x = int(self._kwargs.get('roi_x', 0))
        self._roi_y = int(self._kwargs.get('roi_y', 0))
        self._roi_width = int(self._kwargs.get('roi_width', 0))
        self._roi_height = int(self._kwargs.get('roi_height', 0))
        self._frames_per_sample = max(1, int(self._kwargs.get('frames_per_sample', 1)))
        self._saturation_fraction_limit = float(
            self._kwargs.get('saturation_fraction_limit', DEFAULT_SATURATION_FRACTION_LIMIT))
        self._require_dark_reference = bool(self._kwargs.get('require_dark_reference', True))
        self._dark_reference_file = str(
            self._kwargs.get('dark_reference_file', self.DEFAULT_DARK_REFERENCE_FILE))
        self._clip_negative = bool(self._kwargs.get('clip_negative', False))
        self._keep_camera_open = bool(self._kwargs.get('keep_camera_open', True))
        self._stop_streaming_on_open = bool(self._kwargs.get('stop_streaming_on_open', False))
        self._settings_check_interval = int(
            self._kwargs.get('settings_check_interval', DEFAULT_SETTINGS_CHECK_INTERVAL))

        self._frame_timeout_ms = None

        self.networked_instrument_properties.extend([
            'unit',
            'roi',
            'exposure_time',
            'gain',
            'pixel_format',
            'dark_reference_available',
        ])

    #
    # camera ownership
    #

    def _make_camera(self):
        """Build the camera driver this adapter reads from.

        Split out so tests can substitute a stub camera without any hardware or vmbpy.
        """
        camera_kwargs = {name: self._kwargs[name]
                         for name in self.CAMERA_ARGUMENT_NAMES if name in self._kwargs}
        return CameraAlliedVisionAlvium(visa_address='None', channel=self.channel, **camera_kwargs)

    @property
    def camera(self):
        """The underlying camera driver."""
        return self._camera

    #
    # connection handling
    #

    @Instrument._open.getter  # weird way to override the parent's class property getter
    def _open(self):
        return self._camera is not None and self._camera._open

    def open(self):
        """Connect to the camera, load the dark reference and check it still applies."""
        if self._camera is None:
            self._camera = self._make_camera()

        if not self._camera._open:
            self._camera.open()

        self._ensure_not_streaming(
            "Cannot start a camera-backed power meter while the camera is streaming.")

        # a frame timeout shorter than the exposure would abort a long acquisition
        exposure_ms = float(self._camera.exposure_time) / 1000.0
        self._frame_timeout_ms = int(max(int(self._kwargs.get('timeout_ms', 5000)),
                                         2.0 * exposure_ms + 500.0))

        self._reference_settings = self._current_settings()
        self._triggers_since_settings_check = 0

        self.load_dark_reference()

        self.logger.info(
            "Camera-backed power meter ready on %s: integration ROI %s, unit %s, dark reference %s.",
            self._camera.idn(), self.roi, self._merit_unit,
            "loaded" if self._dark_reference is not None else "none")

    def close(self):
        """Release the pending frames, and the camera unless it is being kept open."""
        with self._capture_lock:
            self._pending_frames = None

        camera = getattr(self, '_camera', None)
        if camera is None:
            return
        if not getattr(self, '_keep_camera_open', True):
            camera.close()

    @Instrument.thread_lock.getter  # weird way to override the parent's class property getter
    def thread_lock(self):
        return self._camera.thread_lock if self._camera is not None else self._capture_lock

    def _ensure_not_streaming(self, context):
        """Refuse to acquire while the live Camera View owns the camera.

        Single frames cannot be taken during a stream. Stealing the stream would silently freeze a
        preview somebody is watching, and reusing the streamed frames is worse: they are not
        synchronised with stage motion, so a scan point could be scored on a frame captured before
        the stage finished moving.
        """
        if self._camera is None or not self._camera.is_streaming:
            return
        if self._stop_streaming_on_open:
            self.logger.warning("Stopping the camera stream, stop_streaming_on_open is set.")
            self._camera.stop_streaming()
            return
        raise InstrumentException(
            context + " The live Camera View is running: press Stop there and try again, or set "
            '"stop_streaming_on_open": true in this instrument\'s config args.')

    #
    # settings
    #

    @staticmethod
    def _normalised_unit(unit):
        """Accept 'counts', 'dB' and the 'dBm' that generic power meter consumers write."""
        text = str(unit).strip().lower()
        if text in ('counts', 'count', 'linear'):
            return 'counts'
        if text in ('db', 'dbm'):
            return 'dB'
        raise InstrumentException(
            "Unknown unit {!r} for a camera-backed power meter, expected 'counts' or 'dB'.".format(
                unit))

    @property
    def unit(self):
        """`'counts'` for the raw integrated intensity, `'dB'` for `10*log10` of it."""
        return self._merit_unit

    @unit.setter
    def unit(self, value):
        self._merit_unit = self._normalised_unit(value)

    @property
    def roi(self):
        """Integration ROI as `[x, y, width, height]`, in pixels of the delivered frame."""
        return [self._roi_x, self._roi_y, self._roi_width, self._roi_height]

    @roi.setter
    def roi(self, value):
        x, y, width, height = [int(v) for v in value]
        self._roi_x, self._roi_y, self._roi_width, self._roi_height = x, y, width, height

    @property
    def exposure_time(self):
        """Exposure time of the underlying camera, in microseconds."""
        return float(self._camera.exposure_time)

    @property
    def gain(self):
        """Gain of the underlying camera, in dB."""
        return float(self._camera.gain)

    @property
    def pixel_format(self):
        """Pixel format of the underlying camera."""
        return str(self._camera.pixel_format)

    @property
    def last_frame_statistics(self):
        """Diagnostics from the most recent integration: ROI sum, ROI max, saturated fraction."""
        return dict(self._last_statistics)

    def _current_settings(self):
        return {
            'exposure time': float(self._camera.exposure_time),
            'gain': float(self._camera.gain),
            'pixel format': str(self._camera.pixel_format),
            'roi': [int(v) for v in self._camera.roi],
        }

    def _check_settings_unchanged(self):
        """Catch the camera being reconfigured underneath a running scan.

        A scan trace whose points were taken at different exposures is meaningless, and the search
        would move the stage to a position derived from it, so this raises rather than warns. Only
        checked every few triggers: it costs device reads, and nothing changes it mid-scan except a
        human at the Camera View.
        """
        if self._reference_settings is None:
            return
        self._triggers_since_settings_check += 1
        if self._triggers_since_settings_check < self._settings_check_interval:
            return
        self._triggers_since_settings_check = 0

        differences = self._describe_setting_differences(self._reference_settings,
                                                        self._current_settings())
        if differences:
            raise InstrumentException(
                "The camera settings changed while the power meter was in use, so readings taken "
                "before and after are not comparable: " + "; ".join(differences)
                + ". Reconnect the instrument to accept the new settings.")

    @staticmethod
    def _describe_setting_differences(expected, actual):
        """List the human-readable differences between two settings dicts, empty if they agree."""
        differences = []
        expected_exposure = float(expected.get('exposure time', 0.0))
        actual_exposure = float(actual.get('exposure time', 0.0))
        if expected_exposure > 0 and abs(actual_exposure - expected_exposure) > 0.01 * expected_exposure:
            differences.append("exposure time {:.1f} us, expected {:.1f} us".format(
                actual_exposure, expected_exposure))
        if abs(float(actual.get('gain', 0.0)) - float(expected.get('gain', 0.0))) > 0.05:
            differences.append("gain {:.2f} dB, expected {:.2f} dB".format(
                float(actual.get('gain', 0.0)), float(expected.get('gain', 0.0))))
        if str(actual.get('pixel format')) != str(expected.get('pixel format')):
            differences.append("pixel format {!s}, expected {!s}".format(
                actual.get('pixel format'), expected.get('pixel format')))
        if list(actual.get('roi', [])) != list(expected.get('roi', [])):
            differences.append("camera ROI {!s}, expected {!s}".format(
                actual.get('roi'), expected.get('roi')))
        return differences

    #
    # dark reference
    #

    @classmethod
    def dark_reference_paths(cls, file_name=None):
        """Absolute paths of the dark reference image and its metadata sidecar."""
        file_name = file_name or cls.DEFAULT_DARK_REFERENCE_FILE
        image_path = get_configuration_file_path(file_name)
        sidecar_path = os.path.splitext(image_path)[0] + '.json'
        return image_path, sidecar_path

    @property
    def dark_reference(self):
        """The loaded dark reference frame, or None."""
        return self._dark_reference

    @property
    def dark_reference_available(self):
        """Whether a dark reference is loaded and being subtracted."""
        return self._dark_reference is not None

    def clear_dark_reference(self):
        """Stop subtracting a dark reference."""
        self._dark_reference = None
        self._dark_metadata = {}

    @classmethod
    def capture_dark_reference(cls, camera, frames=16, timeout_ms=None, file_name=None):
        """Average several frames into a dark reference and store it with its camera settings.

        The beam must be blocked before calling this: whatever the camera sees is what gets
        subtracted from every later reading.

        Arguments:
            camera (CameraAlliedVisionAlvium): an open camera
            frames (int): how many frames to average
            timeout_ms (int): per-frame timeout, defaults to the camera's own
            file_name (str): file name in the LabExT settings directory

        Returns:
            dict: the metadata written alongside the image
        """
        frames = max(1, int(frames))
        images = camera.snap_photos(frames, timeout_ms=timeout_ms)

        # float32, not the camera's integer dtype: averaging buys sub-count precision in the dark
        # level, and rounding back to integers would leave up to half a count of bias on every ROI
        # pixel, which over tens of thousands of pixels is not small next to a weak spot
        dark = np.mean(np.stack(images).astype(np.float64), axis=0).astype(np.float32)

        metadata = {
            'camera id': camera.instrument_parameters.get('camera id'),
            'camera serial': camera.instrument_parameters.get('camera serial'),
            'pixel format': str(camera.pixel_format),
            'exposure time': float(camera.exposure_time),
            'gain': float(camera.gain),
            'roi': [int(v) for v in camera.roi],
            'shape': [int(v) for v in dark.shape],
            'source dtype': str(images[0].dtype),
            'frames averaged': frames,
            'mean level': float(dark.mean()),
            'timestamp utc': datetime.now(timezone.utc).isoformat(),
        }

        image_path, sidecar_path = cls.dark_reference_paths(file_name)
        np.save(image_path, dark)
        with open(sidecar_path, 'w') as sidecar_file:
            json.dump(metadata, sidecar_file, indent=4)

        return metadata

    @classmethod
    def load_dark_reference_metadata(cls, file_name=None):
        """The stored dark reference's metadata, or None if there is no usable sidecar."""
        _, sidecar_path = cls.dark_reference_paths(file_name)
        try:
            with open(sidecar_path, 'r') as sidecar_file:
                return json.load(sidecar_file)
        except Exception:
            return None

    def load_dark_reference(self, file_name=None):
        """Load the dark reference and verify it still matches the camera.

        Every mismatch is reported in one message rather than one at a time, so a recapture does not
        just reveal the next problem.
        """
        file_name = file_name or self._dark_reference_file
        image_path, _ = self.dark_reference_paths(file_name)

        if not os.path.isfile(image_path):
            self._dark_reference = None
            self._dark_metadata = {}
            message = ("No dark reference found at {:s}. Readings will include the sensor's own "
                       "background, which can be far larger than the signal.".format(image_path))
            if self._require_dark_reference:
                raise InstrumentException(
                    message + " Capture one with the beam blocked using the Camera View, or set "
                    '"require_dark_reference": false to run without it.')
            self.logger.warning(message)
            return

        dark = np.load(image_path).astype(np.float64)
        metadata = self.load_dark_reference_metadata(file_name) or {}

        differences = []
        actual = self._current_settings()
        frame_shape = self._probe_frame_shape()
        if tuple(dark.shape) != tuple(frame_shape):
            differences.append("dark frame is {!s}, camera delivers {!s}".format(
                tuple(dark.shape), tuple(frame_shape)))
        if metadata:
            differences.extend(self._describe_setting_differences(metadata, actual))
            stored_serial = metadata.get('camera serial')
            actual_serial = self._camera.instrument_parameters.get('camera serial')
            if stored_serial is not None and actual_serial is not None \
                    and str(stored_serial) != str(actual_serial):
                differences.append("dark reference is from camera {!s}, this is {!s}".format(
                    stored_serial, actual_serial))
        else:
            self.logger.warning(
                "Dark reference at %s has no metadata sidecar, so it cannot be checked against the "
                "current camera settings.", image_path)

        if differences:
            raise InstrumentException(
                "The stored dark reference does not match the camera as it is now, so subtracting "
                "it would corrupt every reading: " + "; ".join(differences)
                + ". Capture a new dark reference with the beam blocked.")

        self._dark_reference = dark
        self._dark_metadata = metadata

    def _probe_frame_shape(self):
        """Shape of a frame as the camera is currently configured, without acquiring one."""
        width, height, _, _ = self._camera.roi
        return int(height), int(width)

    #
    # acquisition
    #

    def trigger(self, continuous=None):
        """Acquire the frame(s) that the next `fetch_power()` will reduce.

        Mirrors how the SCPI power meters split starting an acquisition from reading the result,
        which is what the Search for Peak relies on: it triggers every meter before fetching any of
        them, so their acquisitions overlap instead of stacking up.
        """
        self._check_settings_unchanged()
        frames = self._acquire(self._frames_per_sample)
        with self._capture_lock:
            self._pending_frames = frames

    def fetch_power(self):
        """The integrated ROI intensity of the triggered frame(s).

        Acquires on the spot if `trigger()` was not called first, so it also works standalone.

        Returns:
            float: counts, or dB relative to one count, following `unit`
        """
        with self._capture_lock:
            frames, self._pending_frames = self._pending_frames, None
        if frames is None:
            frames = self._acquire(self._frames_per_sample)
        return self._reduce(frames)

    @property
    def power(self):
        """Acquire and return one reading."""
        return self._reduce(self._acquire(self._frames_per_sample))

    def _acquire(self, count):
        if not self._open:
            raise InstrumentException(
                "Camera-backed power meter is not open. Call open() before taking readings.")
        self._ensure_not_streaming("Cannot take a reading while the camera is streaming.")
        if count == 1:
            return [self._camera.snap_photo(timeout_ms=self._frame_timeout_ms)]
        return self._camera.snap_photos(count, timeout_ms=self._frame_timeout_ms)

    def _reduce(self, frames):
        total = float(np.mean([self._integrate(frame) for frame in frames]))
        if self._merit_unit == 'counts':
            return total
        if total <= 0.0:
            # A finite floor rather than -inf or an exception. -inf would make the Gaussian fit
            # produce NaNs and would trip the search's finite-value guard, throwing away the whole
            # axis, and a scan legitimately starts with no signal at all. One count is 0 dB, so
            # nothing real ever reaches this value.
            self.logger.debug(
                "Integrated ROI intensity is %.4g after dark subtraction, reporting the %.0f dB "
                "floor.", total, MINIMUM_DB)
            return MINIMUM_DB
        return 10.0 * math.log10(total)

    def _saturation_level(self):
        """Full scale in counts, from the pixel format rather than the numpy dtype.

        Mono10 and Mono12 arrive in a uint16 array but only fill 10 or 12 bits, so
        `np.iinfo(image.dtype).max` would say 65535 and no frame would ever look saturated.
        """
        match = _BIT_DEPTH_PATTERN.search(self.pixel_format)
        return float((1 << int(match.group(1))) - 1) if match else 255.0

    def _resolved_roi(self, image_shape):
        """The integration ROI clamped to nothing and validated against the delivered frame.

        Numpy slicing silently shrinks a ROI that hangs off the edge, which would quietly rescale
        every reading, so an out-of-range ROI raises instead.
        """
        height, width = int(image_shape[0]), int(image_shape[1])
        x, y = self._roi_x, self._roi_y
        w = self._roi_width or width
        h = self._roi_height or height

        if x < 0 or y < 0 or w <= 0 or h <= 0 or x + w > width or y + h > height:
            raise InstrumentException(
                "Integration ROI x={:d} y={:d} w={:d} h={:d} does not fit inside the {:d}x{:d} "
                "frame the camera delivers. The ROI is in pixels of the delivered frame, so if the "
                "camera's own ROI is cropped the coordinates are relative to that crop, not to the "
                "full sensor.".format(x, y, w, h, width, height))
        return x, y, w, h

    def _integrate(self, image):
        """Sum the counts in the integration ROI of one frame, with the dark reference removed."""
        image = np.asarray(image)
        if image.ndim != 2:
            raise InstrumentException(
                "Expected a 2D monochrome frame, got shape {!s}.".format(image.shape))

        x, y, w, h = self._resolved_roi(image.shape)
        patch = image[y:y + h, x:x + w]

        level = self._saturation_level()
        saturated = int(np.count_nonzero(patch >= level))
        fraction = saturated / float(patch.size)
        if fraction > self._saturation_fraction_limit:
            raise InstrumentException(
                "The beam spot is saturated: {:d} of {:d} pixels ({:.3%}) in the integration ROI "
                "are at or above full scale ({:.0f} counts for {:s}). Integrated intensity stops "
                "being proportional to optical power once that happens, so a search would settle "
                "on a plateau rather than the real peak. Reduce the exposure time or gain, then "
                "capture a new dark reference.".format(
                    saturated, patch.size, fraction, level, self.pixel_format))

        # dtype=np.float64 is not tidiness: numpy sums integer arrays in the platform's default
        # integer, which is uint32 on Windows, and a full-frame Mono12 sum passes 2**32
        if self._dark_reference is not None:
            values = patch.astype(np.float64) - self._dark_reference[y:y + h, x:x + w]
            if self._clip_negative:
                np.clip(values, 0.0, None, out=values)
            total = float(values.sum(dtype=np.float64))
        else:
            total = float(patch.sum(dtype=np.float64))

        self._last_statistics = {
            'roi sum': total,
            'roi max': float(patch.max()),
            'saturated fraction': fraction,
            'roi': [x, y, w, h],
        }
        return total

    #
    # identification
    #

    def idn(self):
        """Identification string. Must not raise: metadata collection calls it unguarded."""
        try:
            return "PowerMeterCameraAlvium via " + str(self._camera.idn())
        except Exception as exc:
            return "PowerMeterCameraAlvium (identification unavailable: {!r})".format(exc)

    def get_instrument_parameter(self):
        """Settings worth recording with a measurement, including the camera's own."""
        parameters = dict(self.instrument_parameters)
        parameters['idn'] = self.idn()
        parameters['merit unit'] = self._merit_unit
        parameters['integration roi'] = self.roi
        parameters['dark reference'] = dict(self._dark_metadata) if self._dark_metadata else None
        try:
            parameters['camera'] = self._camera.get_instrument_parameter()
        except Exception as exc:
            parameters['camera'] = "ERROR getting camera parameters: {!r}".format(exc)
        return parameters

    #
    # SCPI/VISA surface of the parent class, stubbed out as for any non-VISA instrument
    #

    def logging_stop(self):
        return None

    def clear(self):
        return None

    def reset(self):
        return None

    def ready_check_sync(self):
        return True

    def ready_check_async_setup(self):
        return None

    def ready_check_async(self):
        return True

    def check_instrument_errors(self):
        return None

    def command(self, *args, **kwargs):
        return None

    def command_channel(self, *args, **kwargs):
        return None

    def request(self, *args, **kwargs):
        return ""

    def request_channel(self, *args, **kwargs):
        return ""

    def query(self, *args, **kwargs):
        return ""

    def query_channel(self, *args, **kwargs):
        return ""

    def write(self, *args, **kwargs):
        return None

    def write_channel(self, *args, **kwargs):
        return None

    def query_raw_bytes(self, *args, **kwargs):
        return None
