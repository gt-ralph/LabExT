#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
LabExT  Copyright (C) 2021  ETH Zurich and Polariton Technologies AG
This program is free software and comes with ABSOLUTELY NO WARRANTY; for details see LICENSE file.
"""

import contextlib
import math
import os
import threading
import time
from datetime import datetime, timezone

import numpy as np

from LabExT.Instruments.InstrumentAPI import Instrument, InstrumentException

# vmbpy is an optional dependency: it ships as a wheel inside the Vimba X installation and loads
# VmbC.dll at import time. The import is guarded (and catches more than ImportError) because
# PluginLoader silently drops any module which raises on import, which would make this driver
# disappear from the instrument list with nothing but a log line to explain it.
try:
    from vmbpy import (VmbSystem, AccessMode, FrameStatus, PixelFormat, VmbTimeout)

    VMBPY_AVAILABLE = True
    VMBPY_IMPORT_ERROR = None
except Exception as _exc:  # ImportError, OSError (missing VmbC.dll), VmbSystemError, ...
    VmbSystem = AccessMode = FrameStatus = PixelFormat = None
    VmbTimeout = Exception

    VMBPY_AVAILABLE = False
    VMBPY_IMPORT_ERROR = _exc

#: pixel formats which vmbpy knows how to lay out as a numpy array. Packed formats (Mono10p,
#: Mono12p, Mono12Packed) are not in vmbpy's PIXEL_FORMAT_TO_LAYOUT table and must be converted
#: before as_numpy_ndarray() will accept them.
NUMPY_SAFE_FORMATS = ('Mono8', 'Mono10', 'Mono12', 'Mono14', 'Mono16')

#: conversion target used for packed source formats, chosen to preserve all bits
PACKED_CONVERSION_TARGET = 'Mono16'

DEFAULT_FRAME_TIMEOUT_MS = 5000


#: how many frames auto exposure may spend converging. Each pass corrects multiplicatively, so a
#: frame three decades out of range is back in range after one; the rest of the budget is for the
#: passes after that, where a clipped frame hides its own peak and only iterating finds it.
AUTO_EXPOSURE_MAX_FRAMES = 5

#: how close to the target fill counts as converged, as a fraction of the target. Loose enough that
#: noise on a single peak pixel does not send it round another pass.
AUTO_EXPOSURE_TOLERANCE = 0.1

#: fill at or above which a frame is treated as clipped, so that its peak says nothing about how
#: far over the exposure is. Just under 1.0, because a sensor's ceiling need not be the format's:
#: this camera rails Mono12 at 4094 rather than 4095.
CLIPPED_FILL = 0.999

#: factor to divide the exposure by per pass while the frame is still clipped. A stride rather than
#: a ratio: from a decade over, scaling by the target fill would take eight passes and this takes
#: two, and overshooting downwards costs one cheap pass back up.
CLIPPED_STRIDE = 8.0

#: frames dropped after each change while streaming. Frames already in flight were exposed before
#: the change, so measuring the next one would score the exposure that has just been replaced.
AUTO_EXPOSURE_SETTLE_FRAMES = 2


def converge_exposure(camera, full_scale, target_fill=0.7, max_exposure=None, timeout_ms=None,
                      max_frames=AUTO_EXPOSURE_MAX_FRAMES, tolerance=AUTO_EXPOSURE_TOLERANCE,
                      region=None):
    """Scale a camera's exposure until its brightest pixel sits at `target_fill` of full scale.

    `region` is `(x, y, width, height)` and limits which pixels count. Give it whenever the region
    the beam lands in is known, because the brightest pixel in a whole frame need not be the beam:
    this sensor has a hot pixel which reads 41.6 counts per millisecond of exposure, so past about
    30 ms it outshines a weak spot and the exposure gets scaled to fill a defect instead.

    A free function rather than a method so that everything camera-shaped can use one
    implementation: a measurement drives a camera that is not streaming and snaps its own frames, a
    live view drives one that is streaming and reads that stream instead, and a test drives a stub.
    All it needs of `camera` is `exposure_time`, `exposure_time_range`, `is_streaming`, and either
    `snap_photo` or `latest_streamed_frame`.

    `full_scale` is passed in rather than derived here: the caller already knows the bit depth of
    the format it is working in, and deriving it in a fourth place is how the saturation reporting
    went wrong once already.

    The peak fill reported is always measured at the exposure reported, never at one the loop was
    about to leave: the last pass measures and does not adjust.

    Returns:
        dict: `status` is `'converged'`, `'out of frames'` or `'at a limit'`; `note` is one line
        for a log or a status bar; plus `exposure time us`, `peak fill`, `target fill` and
        `frames used`.
    """
    low, high = camera.exposure_time_range
    ceiling = min(float(high), float(max_exposure)) if max_exposure else float(high)
    floor = float(low)
    exposure = float(camera.exposure_time)
    timeout_s = None if timeout_ms is None else float(timeout_ms) / 1000.0
    seen = [None]  # stream counter, so each read waits for a frame newer than the last

    def next_frame(discard=0):
        if getattr(camera, 'is_streaming', False):
            image = None
            for _ in range(discard + 1):
                image, seen[0] = camera.latest_streamed_frame(
                    newer_than=seen[0], timeout_s=5.0 if timeout_s is None else timeout_s)
                if image is None:
                    raise InstrumentException(
                        "No new frame arrived from the camera stream while setting the exposure.")
            return image
        return camera.snap_photo(timeout_ms=timeout_ms)

    def peak_of(image):
        if region is None:
            return float(np.max(image))
        x, y, width, height = (int(v) for v in region)
        return float(np.max(image[y:y + height, x:x + width]))

    peak_fill = float('nan')
    frames_used = 0
    for frames_used in range(1, int(max_frames) + 1):
        image = next_frame(discard=0 if frames_used == 1 else AUTO_EXPOSURE_SETTLE_FRAMES)
        peak_fill = peak_of(image) / float(full_scale)

        if abs(peak_fill - target_fill) <= tolerance * target_fill:
            return {'status': 'converged',
                    'note': "peak at {:.1f}% of full scale after {:d} frames, exposure {:.1f} us"
                            .format(100.0 * peak_fill, frames_used, exposure),
                    'exposure time us': exposure, 'peak fill': peak_fill,
                    'target fill': float(target_fill), 'frames used': frames_used}

        if frames_used == int(max_frames):
            break

        if peak_fill <= 0.0:
            # A frame with nothing in it gives no ratio to scale by, so go straight to the longest
            # exposure allowed: either something appears, or the view really is dark.
            wanted = ceiling
        elif peak_fill >= CLIPPED_FILL:
            # A clipped frame does not show its own peak - every railed pixel reads full scale
            # whatever the light behind it - so the ratio below would say "come down to 70%" no
            # matter how far over it really is, and five frames of that only buys a factor of four.
            # Step down by a stride instead, which reaches a decade in two frames. Seen for real
            # starting from an exposure left behind by a run in a shallower pixel format.
            wanted = exposure / CLIPPED_STRIDE
        else:
            wanted = exposure * target_fill / peak_fill
        camera.exposure_time = min(max(wanted, floor), ceiling)

        reached = float(camera.exposure_time)
        if abs(reached - exposure) <= 1e-6 * max(exposure, 1.0):
            # Nothing moved: the request was clamped or snapped back onto the exposure already set,
            # so another pass would measure the same frame again. The fill measured above still
            # describes this exposure, precisely because it did not change.
            return {'status': 'at a limit',
                    'note': "stuck at {:.1f} us (allowed {:.1f} to {:.1f} us) with the peak at "
                            "{:.1f}%, wanted {:.1f}%".format(
                                exposure, floor, ceiling, 100.0 * peak_fill, 100.0 * target_fill),
                    'exposure time us': exposure, 'peak fill': peak_fill,
                    'target fill': float(target_fill), 'frames used': frames_used}
        exposure = reached

    return {'status': 'out of frames',
            'note': "gave up after {:d} frames at {:.1f} us, with the peak at {:.1f}% instead of "
                    "{:.1f}%".format(frames_used, exposure, 100.0 * peak_fill,
                                     100.0 * target_fill),
            'exposure time us': exposure, 'peak fill': peak_fill,
            'target fill': float(target_fill), 'frames used': frames_used}


class _StreamedFrameSlot:
    """Holds the most recent streamed frame, with a counter so a reader can demand a fresh one.

    Only the newest frame is kept: a preview wants the latest image, and a measurement wants one
    taken after it finished moving. Neither is served by a backlog.
    """

    def __init__(self):
        self._condition = threading.Condition()
        self._counter = 0
        self._image = None

    @property
    def counter(self):
        with self._condition:
            return self._counter

    def put(self, image):
        with self._condition:
            self._counter += 1
            self._image = image
            self._condition.notify_all()

    def take_newer_than(self, counter, timeout_s):
        """Block for a frame recorded after `counter`. Returns `(image, counter)`, image None on
        timeout."""
        deadline = time.monotonic() + timeout_s
        with self._condition:
            while self._counter <= counter:
                remaining = deadline - time.monotonic()
                if remaining <= 0.0:
                    return None, self._counter
                self._condition.wait(remaining)
            return self._image, self._counter


#: Newest streamed frame per physical camera, keyed by camera id and shared across driver
#: instances. Two instances of this class can address one camera - vmbpy reference counts the
#: device - and this is what lets a second instance read the frames the first one is already
#: streaming, rather than opening a competing acquisition the camera cannot serve.
_STREAMED_FRAMES = {}
_STREAMED_FRAMES_LOCK = threading.Lock()


class CameraAlliedVisionAlvium(Instrument):
    """
    ### Allied Vision Alvium camera (USB3 Vision / GigE Vision, via the Vimba X `vmbpy` API)

    Developed and tested against an Alvium 1800 U-130 VSWIR, but nothing here is model specific:
    all limits are queried from the camera rather than hard-coded, so any GenICam compliant Alvium
    should work.

    This is not a VISA instrument. `visa_address` is ignored, and the SCPI methods of the parent
    class are stubbed out.

    Requires the `vmbpy` wheel shipped with Vimba X:

        pip install "C:/Program Files/Allied Vision/Vimba X/api/python/vmbpy-1.1.1-py3-none-win_amd64.whl"

    #### Constructor keyword arguments

    All of these come from the `args` dict of the instruments.config entry and are optional:

    * `camera_id` (str): Vimba camera ID, e.g. `"DEV_1AB22C0B3989"`. Defaults to the first camera
      found, which is only unambiguous with a single camera attached.
    * `exposure_time` (float): exposure applied on open, in microseconds.
    * `gain` (float): gain applied on open, in dB.
    * `pixel_format` (str): pixel format applied on open, e.g. `"Mono8"` or `"Mono12"`.
    * `roi` (list): `[width, height, offset_x, offset_y]` applied on open. A 0 means "sensor max".
    * `timeout_ms` (int): default frame timeout, defaults to 5000.
    * `throughput_limit` (int): optional `DeviceLinkThroughputLimit` in Bytes/s, for USB bandwidth
      sharing between multiple cameras.

    #### Example instruments.config entry

    ```
    "Camera": [
        {"visa": "None", "class": "CameraAlliedVisionAlvium", "channels": [0],
         "args": {"camera_id": "DEV_1AB22C0B3989", "pixel_format": "Mono8",
                  "exposure_time": 10000.0, "gain": 0.0, "roi": [0, 0, 0, 0]}}
    ]
    ```
    """

    def __init__(self, *args, **kwargs):
        # These must exist before super().__init__() runs: if the parent constructor raises, the
        # interpreter still calls __del__ -> close(), which reads them.
        self._stack = None
        self._vmb = None
        self._cam = None
        self._cam_lock = threading.RLock()
        self._static_info = {}
        self._last_image = None
        self._last_meta = {}

        super().__init__(*args, **kwargs)

        self._category = "Camera"

        self._cam_id = self._kwargs.get("camera_id", None)
        self._timeout_ms = int(self._kwargs.get("timeout_ms", DEFAULT_FRAME_TIMEOUT_MS))
        self._startup_exposure = self._kwargs.get("exposure_time", None)
        self._startup_gain = self._kwargs.get("gain", None)
        self._startup_format = self._kwargs.get("pixel_format", None)
        self._startup_roi = self._kwargs.get("roi", None)
        self._throughput_limit = self._kwargs.get("throughput_limit", None)

        self.networked_instrument_properties.extend([
            'exposure_time',
            'gain',
            'pixel_format',
            'width',
            'height',
            'offset_x',
            'offset_y',
            'exposure_time_range',
            'gain_range',
            'sensor_size',
            'device_temperature',
        ])

    #
    # connection handling
    #

    @Instrument._open.getter  # weird way to override the parent's class property getter
    def _open(self):
        return self._stack is not None and self._cam is not None

    def open(self):
        """Open the connection to the camera. Does nothing if it is already open.

        vmbpy exposes VmbSystem and Camera as context managers, whereas LabExT wants open()/close().
        Both of vmbpy's context managers are reference counted and carry no thread affinity, so
        driving them by hand is supported. An ExitStack is used rather than bare __enter__ calls so
        that a failure part way through (camera busy, wrong ID) still unwinds whatever was already
        entered, in the right order.
        """
        if not VMBPY_AVAILABLE:
            raise InstrumentException(
                "Cannot use " + self.__class__.__name__ + ": the vmbpy module is not importable. "
                "Install the wheel shipped with Vimba X, e.g. pip install "
                '"%VIMBA_X_HOME%/api/python/vmbpy-1.1.1-py3-none-win_amd64.whl". '
                "Original error: " + repr(VMBPY_IMPORT_ERROR))

        with self._cam_lock:
            if self._open:
                return

            stack = contextlib.ExitStack()
            try:
                vmb = stack.enter_context(VmbSystem.get_instance())

                if self._cam_id:
                    cam = vmb.get_camera_by_id(self._cam_id)
                else:
                    all_cams = vmb.get_all_cameras()
                    if not all_cams:
                        raise InstrumentException(
                            "No Allied Vision camera detected. Check the USB connection and that "
                            "the Vimba X transport layers are installed.")
                    cam = all_cams[0]

                # vmbpy hands out one Camera object per physical camera, and its context is
                # reference counted, so a second driver instance for the same camera shares it
                # rather than colliding. Only set_access_mode is off limits once the context has
                # been entered: it is decorated @RaiseIfInsideContext.
                if not getattr(cam, '_context_entered', False):
                    cam.set_access_mode(AccessMode.Full)
                cam = stack.enter_context(cam)

                self._vmb = vmb
                self._cam = cam
                self._stack = stack

                self._cache_static_info()
                self._apply_startup_settings()
            except Exception as exc:
                stack.close()  # unwinds the camera first, then VmbSystem
                self._vmb = None
                self._cam = None
                self._stack = None
                if 'already in use' in str(exc) or 'AccessDenied' in type(exc).__name__:
                    raise InstrumentException(
                        "Camera is already in use by another application. Close the Vimba X Viewer "
                        "(or any other program holding the camera) and try again. Original error: "
                        + repr(exc)) from exc
                raise

        self.logger.info("Opened Allied Vision camera %s (%s, SN %s).",
                         self._static_info.get('camera id'),
                         self._static_info.get('camera model'),
                         self._static_info.get('camera serial'))

    def close(self):
        """Release the camera and shut the Vimba system down. Safe to call more than once, and safe
        to call on an instance whose open() never ran (the parent class calls it from __del__)."""
        # getattr rather than attribute access: __del__ can reach this even if __init__ did not
        # finish, and the lock itself may not exist yet
        lock = getattr(self, '_cam_lock', None)
        if lock is None or getattr(self, '_stack', None) is None:
            return

        # take the lock so that a capture running on another thread finishes before the camera is
        # pulled out from under it
        with lock:
            stack = self._stack
            if stack is None:
                return

            try:
                if self._cam is not None and self._cam.is_streaming():
                    self._cam.stop_streaming()
            except Exception as exc:
                self.logger.debug("Error stopping camera stream during close: %r", exc)

            try:
                stack.close()
            except Exception as exc:
                self.logger.warning("Error closing Allied Vision camera: %r", exc)
            finally:
                self._stack = None
                self._cam = None
                self._vmb = None

        self.logger.debug("Closed Allied Vision camera %s.", self._static_info.get('camera id'))

    @Instrument.thread_lock.getter  # weird way to override the parent's class property getter
    def thread_lock(self):
        return self._cam_lock

    #
    # internal helpers
    #

    def _feat(self, name):
        """Return a GenICam feature by name, raising a sensible error if we are not connected."""
        if not self._open:
            raise InstrumentException(
                "Camera connection is not open. Call open() before accessing camera features.")
        return self._cam.get_feature_by_name(name)

    def _try_set(self, name, value):
        """Best effort feature write for optional features: logs instead of raising."""
        try:
            self._feat(name).set(value)
            return True
        except Exception as exc:
            self.logger.debug("Optional camera feature %s <- %r failed: %r", name, value, exc)
            return False

    @staticmethod
    def _snap_to_increment(value, minimum, maximum, increment):
        """Clip a value into [minimum, maximum] and snap it onto the feature's increment grid.

        GenICam rejects values which are not on the increment grid, so snapping here turns what
        would be a VmbFeatureError into a silently reasonable value.

        Snapping goes to the *nearest* step rather than downwards: the increments the camera
        reports are floats (gain steps by 0.10000000149 dB), so flooring would drop a requested
        gain of 3.0 dB to 2.9 dB purely through representation error.
        """
        value = max(minimum, min(maximum, value))
        if increment:
            snapped = minimum + round((value - minimum) / increment) * increment
            if snapped > maximum:
                if snapped - maximum < increment / 2.0:
                    # only overshot through float error; the reported maximum is itself settable
                    snapped = maximum
                else:
                    snapped = minimum + math.floor((maximum - minimum) / increment) * increment
            value = max(minimum, snapped)
        return value

    @staticmethod
    def _frame_to_array(frame, squeeze=True):
        """Turn a vmbpy frame into a numpy array which owns its memory outright.

        Two things need care here. Packed pixel formats are absent from vmbpy's layout table and
        have to be converted before they can be viewed as numpy at all. And the array vmbpy hands
        back is only a view onto the frame's buffer, which it keeps alive by hanging a
        `{'VmbPy_buffer': ...}` entry off the array's dtype metadata. numpy carries that metadata
        across a plain copy, so the "copy" would still pin a buffer vmbpy is free to reuse, and
        numpy.save would refuse to write it. Going through astype with the dtype spelled as a
        string builds a fresh dtype and leaves the metadata behind.

        Returns:
            tuple: `(image, source pixel format, delivered pixel format)`
        """
        source_format = str(frame.get_pixel_format())
        if source_format not in NUMPY_SAFE_FORMATS:
            frame = frame.convert_pixel_format(getattr(PixelFormat, PACKED_CONVERSION_TARGET))
        delivered_format = str(frame.get_pixel_format())

        image = frame.as_numpy_ndarray()
        if squeeze and image.ndim == 3 and image.shape[2] == 1:
            image = image[:, :, 0]

        image = image.astype(image.dtype.str, order='C', copy=True)
        return image, source_format, delivered_format

    def _cache_static_info(self):
        """Read everything which cannot change while the camera is open exactly once."""
        cam = self._cam
        info = {
            'camera id': cam.get_id(),
            'camera model': cam.get_model(),
            'camera name': cam.get_name(),
            'camera serial': cam.get_serial(),
            'camera interface': cam.get_interface_id(),
            'vmbpy version': str(self._vmb.get_version()),
        }
        for key, feature_name in (('sensor width', 'SensorWidth'),
                                  ('sensor height', 'SensorHeight'),
                                  ('camera firmware', 'DeviceFirmwareVersion')):
            try:
                info[key] = self._feat(feature_name).get()
            except Exception:
                info[key] = None

        self._static_info = info
        # make the identity part of the saved measurement metadata even if the camera happens to be
        # busy when LabExT collects instrument parameters
        self.instrument_parameters.update(info)

    def _apply_startup_settings(self):
        """Put the camera into a known state and apply whatever the config asked for."""
        # Auto exposure / auto gain silently override anything we write to ExposureTime / Gain.
        self._try_set('ExposureAuto', 'Off')
        self._try_set('GainAuto', 'Off')
        # Only when nothing is acquiring. A second instance opening onto a camera the live view is
        # already streaming would otherwise log a failed write on every connect, and it has no
        # business stopping the mode the running acquisition needs.
        if not self._cam.is_streaming():
            self._try_set('AcquisitionMode', 'SingleFrame')

        if self._throughput_limit is not None:
            self._try_set('DeviceLinkThroughputLimitMode', 'On')
            self._try_set('DeviceLinkThroughputLimit', int(self._throughput_limit))

        # If something else already has this camera streaming - the live Camera View - then
        # connecting must not impose this instrument's configured settings on it. The frame layout
        # cannot be applied at all once acquisition has started, and while the camera does accept
        # exposure and gain mid-stream, they belong to whoever started the acquisition: overwriting
        # what the operator set in the live view silently invalidates any dark reference captured
        # against it, which is worse than the layout case because nothing fails loudly. So whatever
        # the stream is producing is adopted, and since the settings are read back from the camera
        # anyway, a measurement still reports what it actually captured with.
        if self._cam.is_streaming():
            if (self._startup_format or self._startup_roi
                    or self._startup_exposure is not None or self._startup_gain is not None):
                self.logger.warning(
                    "Camera is already streaming, so its settings are kept as they are (%s, ROI "
                    "%s, %.1f us, %.2f dB) rather than set from this instrument's configuration. "
                    "Stop the live Camera View before connecting if the configured settings are "
                    "required.", self.pixel_format, self.roi, self.exposure_time, self.gain)
            return

        if self._startup_format:
            self.pixel_format = self._startup_format
        if self._startup_roi:
            self.set_roi(*self._startup_roi)
        if self._startup_exposure is not None:
            self.exposure_time = float(self._startup_exposure)
        if self._startup_gain is not None:
            self.gain = float(self._startup_gain)

    #
    # exposure and gain
    #

    @property
    def exposure_time(self):
        """Exposure time in microseconds."""
        return float(self._feat('ExposureTime').get())

    @exposure_time.setter
    def exposure_time(self, value):
        with self._cam_lock:
            feature = self._feat('ExposureTime')
            minimum, maximum = feature.get_range()
            feature.set(self._snap_to_increment(
                float(value), minimum, maximum, feature.get_increment()))

    @property
    def exposure_time_range(self):
        """Settable exposure time range as `[min, max]` in microseconds."""
        minimum, maximum = self._feat('ExposureTime').get_range()
        return [float(minimum), float(maximum)]

    @property
    def gain(self):
        """Gain in dB."""
        return float(self._feat('Gain').get())

    @gain.setter
    def gain(self, value):
        with self._cam_lock:
            self._try_set('GainSelector', 'All')
            feature = self._feat('Gain')
            minimum, maximum = feature.get_range()
            feature.set(self._snap_to_increment(
                float(value), minimum, maximum, feature.get_increment()))

    @property
    def gain_range(self):
        """Settable gain range as `[min, max]` in dB."""
        minimum, maximum = self._feat('Gain').get_range()
        return [float(minimum), float(maximum)]

    #
    # pixel format
    #

    @property
    def pixel_format(self):
        """Currently active pixel format, as a string, e.g. `'Mono8'`."""
        return str(self._cam.get_pixel_format()) if self._open else None

    @pixel_format.setter
    def pixel_format(self, value):
        with self._cam_lock:
            if not self._open:
                raise InstrumentException("Camera connection is not open.")

            # Nothing to do, and worth checking first: the camera refuses to change this while it
            # is streaming, so a redundant write would fail a measurement whose settings already
            # match what the live view is showing.
            if str(value) == str(self._cam.get_pixel_format()):
                return

            available = self.available_pixel_formats
            if str(value) not in available:
                raise InstrumentException(
                    "Pixel format '{:s}' is not supported by this camera. Available formats: "
                    "{:s}".format(str(value), ", ".join(available)))
            self._require_not_streaming('the pixel format')
            self._cam.set_pixel_format(getattr(PixelFormat, str(value)))

    def _require_not_streaming(self, what):
        """Refuse a change the camera only accepts while acquisition is stopped.

        Exposure and gain can be changed mid-stream; the pixel format and the sensor ROI cannot,
        because they alter the size of the buffers already announced to the transport layer.
        """
        if not self._cam.is_streaming():
            return
        raise InstrumentException(
            "Cannot change {:s} while the camera is streaming: the frame layout is fixed once "
            "acquisition has started. Press Stop in the Camera View, or set it to the value you "
            "want there so nothing needs changing.".format(what))

    @property
    def available_pixel_formats(self):
        """List of pixel format names this camera supports."""
        return [str(f) for f in self._cam.get_pixel_formats()]

    #
    # region of interest
    #

    @property
    def width(self):
        """Width of the acquired image in pixels."""
        return int(self._feat('Width').get())

    @property
    def height(self):
        """Height of the acquired image in pixels."""
        return int(self._feat('Height').get())

    @property
    def offset_x(self):
        """Horizontal offset of the ROI from the left sensor edge, in pixels."""
        return int(self._feat('OffsetX').get())

    @property
    def offset_y(self):
        """Vertical offset of the ROI from the top sensor edge, in pixels."""
        return int(self._feat('OffsetY').get())

    @property
    def roi(self):
        """The current region of interest as `[width, height, offset_x, offset_y]`."""
        return [self.width, self.height, self.offset_x, self.offset_y]

    @property
    def sensor_size(self):
        """Full sensor size as `[width, height]` in pixels."""
        return [self._static_info.get('sensor width'), self._static_info.get('sensor height')]

    def _roi_matches(self, width, height, offset_x, offset_y):
        """Whether the camera is already at this ROI, with 0 meaning the sensor maximum."""
        current_width, current_height, current_x, current_y = self.roi
        sensor_width, sensor_height = self.sensor_size
        wanted_width = int(width) if width else sensor_width
        wanted_height = int(height) if height else sensor_height
        return ([current_width, current_height, current_x, current_y]
                == [wanted_width, wanted_height, int(offset_x), int(offset_y)])

    def set_roi(self, width=0, height=0, offset_x=0, offset_y=0):
        """Set the region of interest. Pass 0 for width or height to use the full sensor.

        GenICam couples the offsets to the sizes: while Width is at its maximum, the settable range
        of OffsetX is (0, 0). The offsets are therefore zeroed first, then the sizes are applied,
        then the offsets. All four values are snapped onto their increment grids.

        Returns:
            list: the resulting ROI as `[width, height, offset_x, offset_y]`
        """
        with self._cam_lock:
            # Skip a no-op, for the same reason as the pixel format: the camera locks these while
            # streaming, so re-applying the ROI a measurement already agrees with must not fail it.
            if self._roi_matches(width, height, offset_x, offset_y):
                return self.roi
            self._require_not_streaming('the sensor ROI')

            self._try_set('OffsetX', 0)
            self._try_set('OffsetY', 0)

            for feature_name, value in (('Width', width), ('Height', height)):
                feature = self._feat(feature_name)
                minimum, maximum = feature.get_range()
                target = maximum if not value else int(value)
                feature.set(int(self._snap_to_increment(
                    target, minimum, maximum, feature.get_increment())))

            for feature_name, value in (('OffsetX', offset_x), ('OffsetY', offset_y)):
                if not value:
                    continue
                feature = self._feat(feature_name)
                minimum, maximum = feature.get_range()
                feature.set(int(self._snap_to_increment(
                    int(value), minimum, maximum, feature.get_increment())))

        return self.roi

    @property
    def device_temperature(self):
        """Mainboard temperature in degrees Celsius, or None if the camera does not report one."""
        try:
            self._try_set('DeviceTemperatureSelector', 'Mainboard')
            return float(self._feat('DeviceTemperature').get())
        except Exception:
            return None

    #
    # continuous streaming
    #
    # Used by the live camera view. Single frame acquisition via snap_photo() and streaming are
    # mutually exclusive: the camera needs a different AcquisitionMode for each, and vmbpy will not
    # hand out a frame through get_frame() while a stream is running.
    #

    @property
    def is_streaming(self):
        """Whether a continuous acquisition is currently running."""
        return self._open and self._cam.is_streaming()

    @property
    def frame_slot(self):
        """The shared newest-frame slot for this physical camera."""
        camera_id = self._static_info.get('camera id') or str(self._cam_id)
        with _STREAMED_FRAMES_LOCK:
            return _STREAMED_FRAMES.setdefault(camera_id, _StreamedFrameSlot())

    def latest_streamed_frame(self, newer_than=None, timeout_s=5.0):
        """The newest streamed frame, optionally waiting for one recorded after `newer_than`.

        Passing the counter taken before a stage move is how a measurement gets a frame it knows
        was captured after the move finished, rather than one still in flight from before it.

        Returns:
            tuple: `(image, counter)`; image is None if nothing arrived within the timeout
        """
        slot = self.frame_slot
        if newer_than is None:
            return slot.take_newer_than(-1, timeout_s)
        return slot.take_newer_than(newer_than, timeout_s)

    def start_streaming(self, handler=None, buffer_count=10):
        """Start a continuous acquisition.

        Every frame is recorded into this camera's shared newest-frame slot, so anything else
        holding the same camera can read the stream through `latest_streamed_frame()` instead of
        trying to open a second acquisition, which the camera cannot serve.

        `handler` is optional. When given it is called as `handler(camera, stream, frame)` and
        **must** hand the buffer back with `camera.queue_frame(frame)`; without one the buffer is
        requeued here. Use `frame_to_array` to get a numpy array out of a frame; the array vmbpy
        provides directly is only borrowed and must not outlive the callback.

        The handler runs on vmbpy's own thread, so it must not take this instrument's `thread_lock`:
        a `stop_streaming()` on another thread holds that lock while waiting for the stream to end,
        which would deadlock.

        Arguments:
            handler (callable): optional frame callback, signature `(camera, stream, frame)`
            buffer_count (int): how many frame buffers to announce. More buffers absorb longer GUI
                stalls at the cost of memory.
        """
        slot = self.frame_slot

        def recording_handler(camera, stream, frame):
            try:
                if self.frame_is_complete(frame):
                    slot.put(self.frame_to_array(frame))
            except Exception:
                # one unusable frame must not kill the stream, and at video rate this must not
                # spam a traceback per frame
                self.logger.debug("Could not record a streamed frame.", exc_info=True)
            finally:
                if handler is not None:
                    handler(camera, stream, frame)
                else:
                    camera.queue_frame(frame)

        with self._cam_lock:
            if not self._open:
                raise InstrumentException(
                    "Camera connection is not open. Call open() before streaming.")
            if self._cam.is_streaming():
                raise InstrumentException(
                    "Camera is already streaming. Call stop_streaming() first.")

            self._try_set('AcquisitionMode', 'Continuous')
            self._cam.start_streaming(handler=recording_handler, buffer_count=int(buffer_count))

        self.logger.debug("Started streaming from camera %s.", self._static_info.get('camera id'))

    def stop_streaming(self):
        """Stop a continuous acquisition. Does nothing if no stream is running."""
        with self._cam_lock:
            if not self._open or not self._cam.is_streaming():
                return
            self._cam.stop_streaming()
            self._try_set('AcquisitionMode', 'SingleFrame')

        self.logger.debug("Stopped streaming from camera %s.", self._static_info.get('camera id'))

    @staticmethod
    def frame_is_complete(frame):
        """Whether a frame handed to a streaming callback actually carries a usable image.

        Incomplete frames turn up routinely at the start of an acquisition and whenever the link
        drops data. They must be checked for before any conversion is attempted: their pixel format
        field reads back as 0, which is not a valid PixelFormat, so even asking what format they are
        raises.
        """
        try:
            return frame.get_status() == FrameStatus.Complete
        except Exception:
            return False

    @staticmethod
    def frame_pixel_format(frame):
        """The pixel format a streamed frame was captured in, as a string.

        Reads the frame itself rather than the camera, so it stays correct even if the camera has
        been reconfigured since, and costs no device access. Only valid for complete frames.
        """
        return str(frame.get_pixel_format())

    def frame_to_array(self, frame, squeeze=True):
        """Convert a vmbpy frame handed to a streaming callback into an owned numpy array.

        Only call this for frames which `frame_is_complete` accepts.

        Returns:
            numpy.ndarray: the frame, 2D for monochrome data when `squeeze` is set
        """
        image, _, _ = self._frame_to_array(frame, squeeze=squeeze)
        return image

    #
    # acquisition
    #

    def snap_photo(self, timeout_ms=None, squeeze=True):
        """Acquire a single frame and return it as a numpy array.

        The dtype follows the active pixel format: uint8 for Mono8, uint16 for the deeper formats.
        Packed formats are converted before conversion to numpy, because vmbpy has no memory layout
        for them.

        Arguments:
            timeout_ms (int): how long to wait for the frame. Defaults to the `timeout_ms`
                constructor argument. Must exceed the exposure time.
            squeeze (bool): return a 2D (height, width) array instead of the (height, width, 1)
                which vmbpy produces for monochrome data.

        Returns:
            numpy.ndarray: the acquired frame
        """
        if not self._open:
            raise InstrumentException(
                "Camera connection is not open. Call open() before acquiring frames.")
        if self.is_streaming:
            raise InstrumentException(
                "Cannot snap a single frame while the camera is streaming. Stop the stream first, "
                "or use the frames the streaming callback already delivers.")

        timeout_ms = int(timeout_ms if timeout_ms is not None else self._timeout_ms)

        with self._cam_lock:
            try:
                frame = self._cam.get_frame(timeout_ms=timeout_ms)
            except VmbTimeout as exc:
                raise InstrumentException(
                    "No frame arrived within {:d} ms. The exposure time is currently {:.1f} us, so "
                    "the timeout must be at least that long.".format(
                        timeout_ms, self.exposure_time)) from exc

            if frame.get_status() != FrameStatus.Complete:
                raise InstrumentException(
                    "Camera delivered an incomplete frame: {!s}".format(frame.get_status()))

            image, source_format, delivered_format = self._frame_to_array(frame, squeeze=squeeze)

            self._last_image = image
            self._last_meta = {
                'pixel format': source_format,
                'delivered pixel format': delivered_format,
                'frame id': frame.get_id(),
                'camera timestamp': frame.get_timestamp(),
                'exposure time': self.exposure_time,
                'gain': self.gain,
                'roi': self.roi,
                'timestamp utc': datetime.now(timezone.utc).isoformat(),
            }

        return self._last_image

    def snap_photos(self, count, timeout_ms=None, squeeze=True):
        """Acquire several frames in one acquisition run.

        Faster than calling `snap_photo` repeatedly, because the acquisition is only set up once.

        Arguments:
            count (int): number of frames to acquire
            timeout_ms (int): per-frame timeout, defaults to the `timeout_ms` constructor argument
            squeeze (bool): as in `snap_photo`

        Returns:
            list: a list of numpy arrays, one per frame
        """
        if not self._open:
            raise InstrumentException(
                "Camera connection is not open. Call open() before acquiring frames.")
        if self.is_streaming:
            raise InstrumentException(
                "Cannot snap frames while the camera is streaming. Stop the stream first, "
                "or use the frames the streaming callback already delivers.")

        count = int(count)
        if count < 1:
            raise ValueError("count must be at least 1, got {:d}.".format(count))

        timeout_ms = int(timeout_ms if timeout_ms is not None else self._timeout_ms)
        images = []

        with self._cam_lock:
            try:
                for frame in self._cam.get_frame_generator(limit=count, timeout_ms=timeout_ms):
                    if frame.get_status() != FrameStatus.Complete:
                        raise InstrumentException(
                            "Camera delivered an incomplete frame: {!s}".format(frame.get_status()))

                    image, _, _ = self._frame_to_array(frame, squeeze=squeeze)
                    images.append(image)
            except VmbTimeout as exc:
                raise InstrumentException(
                    "No frame arrived within {:d} ms after {:d} of {:d} frames. The exposure time "
                    "is currently {:.1f} us.".format(
                        timeout_ms, len(images), count, self.exposure_time)) from exc

            if images:
                self._last_image = images[-1]
                self._last_meta = {
                    'pixel format': self.pixel_format,
                    'exposure time': self.exposure_time,
                    'gain': self.gain,
                    'roi': self.roi,
                    'timestamp utc': datetime.now(timezone.utc).isoformat(),
                }

        return images

    def get_recent_photo(self):
        """The most recent frame acquired by `snap_photo` or `snap_photos`, without touching the
        camera. Returns None if nothing has been acquired yet."""
        return self._last_image

    def get_recent_photo_metadata(self):
        """Settings and frame identifiers captured alongside the most recent frame."""
        return dict(self._last_meta)

    #
    # saving
    #

    def save_photo(self, file_path, image=None, overwrite=True):
        """Write an image to a PNG or TIFF file. Never called automatically by `snap_photo`.

        Uses PIL rather than `matplotlib.pyplot.imsave`, which normalises and colour maps 2D arrays
        and would therefore destroy the raw counts. TIFF is preferable for anything deeper than 8
        bit: Pillow writes 16 bit PNG as mode 'I;16', which many viewers render incorrectly.

        Arguments:
            file_path (str): destination path, ending in .png, .tif or .tiff
            image (numpy.ndarray): image to write, defaults to the most recent frame
            overwrite (bool): if False, refuse to overwrite an existing file

        Returns:
            str: the absolute path written
        """
        from PIL import Image

        image = self.get_recent_photo() if image is None else image
        if image is None:
            raise InstrumentException("No image available to save. Call snap_photo() first.")

        image = np.asarray(image)
        if image.ndim == 3 and image.shape[2] == 1:
            image = image[:, :, 0]

        file_path = os.path.abspath(file_path)
        extension = os.path.splitext(file_path)[1].lower()
        if extension not in ('.png', '.tif', '.tiff'):
            raise InstrumentException(
                "Unsupported image extension '{:s}'. Use .png, .tif or .tiff.".format(extension))
        if os.path.exists(file_path) and not overwrite:
            raise InstrumentException("Refusing to overwrite existing file " + file_path)
        if image.dtype == np.uint16 and extension == '.png':
            self.logger.warning(
                "Writing 16 bit data to PNG; TIFF is the better container for this depth.")

        directory = os.path.dirname(file_path)
        if directory:
            os.makedirs(directory, exist_ok=True)

        Image.fromarray(image).save(file_path)
        self.logger.info("Saved camera image to %s", file_path)
        return file_path

    @staticmethod
    def stretch_to_8bit(image, low_percentile=1.0, high_percentile=99.0):
        """Rescale a frame to the full 8 bit range so it is viewable in any image viewer.

        For looking at, not for measuring: the mapping depends on the frame's own content, so two
        stretched images are not comparable to each other. Deeper formats need it, because Mono12
        counts occupy the bottom sixteenth of a uint16 and render almost black otherwise.

        Returns:
            numpy.ndarray: uint8, same shape as the input
        """
        values = np.asarray(image, dtype=np.float32)
        low, high = np.percentile(values, [low_percentile, high_percentile])
        if high <= low:
            # a flat frame has nothing to stretch; keep it black rather than amplifying noise
            high = low + 1.0
        scaled = (values - low) * (255.0 / (high - low))
        return np.clip(scaled, 0.0, 255.0).astype(np.uint8)

    def save_photo_raw(self, file_path, image=None):
        """Write an image to a .npy file, preserving dtype and values exactly.

        Returns:
            str: the absolute path written
        """
        image = self.get_recent_photo() if image is None else image
        if image is None:
            raise InstrumentException("No image available to save. Call snap_photo() first.")

        file_path = os.path.abspath(file_path)
        directory = os.path.dirname(file_path)
        if directory:
            os.makedirs(directory, exist_ok=True)

        np.save(file_path, np.asarray(image))
        self.logger.info("Saved raw camera image to %s", file_path)
        return file_path

    #
    # identification
    #

    def idn(self):
        """Identification string.

        Must not raise: `get_instrument_parameter` calls this outside its per-property error
        handling, so an exception here would discard the whole metadata dictionary.
        """
        try:
            info = self._static_info
            return "AlliedVision,{:s},{:s},{:s}".format(
                str(info.get('camera model', 'unknown')),
                str(info.get('camera serial', 'unknown')),
                str(info.get('camera firmware', 'unknown')))
        except Exception as exc:
            return "AlliedVision Alvium (identification unavailable: {!r})".format(exc)

    #
    # SCPI/VISA surface of the parent class, stubbed out: this camera has no such interface
    #

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
