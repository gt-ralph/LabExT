#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
LabExT  Copyright (C) 2021  ETH Zurich and Polariton Technologies AG
This program is free software and comes with ABSOLUTELY NO WARRANTY; for details see LICENSE file.
"""

import json
import logging
import math
import os
import re
from contextlib import nullcontext
from datetime import datetime, timezone
from time import sleep

from typing import TYPE_CHECKING, Dict

import numpy as np

from LabExT.Instruments.CameraAlliedVisionAlvium import converge_exposure
from LabExT.Instruments.PowerMeterCameraAlvium import PowerMeterCameraAlvium
from LabExT.Measurements.MeasAPI import *
from LabExT.Utils import get_configuration_file_path

if TYPE_CHECKING:
    from LabExT.Measurements.MeasAPI.Measparam import MeasParam
else:
    MeasParam = None

#: leading digits of a pixel format name, e.g. 12 from 'Mono12'
_PIXEL_FORMAT_BITS = re.compile(r'(\d+)')


def _full_scale_counts(pixel_format, dtype):
    """Full scale in counts for a frame, taken from the pixel format not the numpy dtype.

    Mono10 and Mono12 arrive in a uint16 array but only fill 10 or 12 bits, so
    `np.iinfo(dtype).max` says 65535 and a clipped frame is never reported as saturated. The
    dtype is only the fallback, for a format whose name carries no bit count.
    """
    match = _PIXEL_FORMAT_BITS.search(str(pixel_format or ''))
    if match:
        bits = int(match.group(1))
        if 0 < bits <= 32:
            return float((1 << bits) - 1)
    return float(np.iinfo(dtype).max)


def _clip_level(full_scale):
    """The count at which to call a pixel saturated.

    One below nominal full scale, because a sensor's ceiling need not be the format's: the Alvium
    used here clips Mono12 at 4094, so testing for 4095 finds nothing in a frame that is plainly
    railed - 2156 pixels at 4094 against 2 at 4093. A pixel one count under a true full scale is
    saturated for any practical purpose, so the tolerance cannot mislead in the other direction.
    """
    return full_scale - 1.0 if full_scale > 1.0 else full_scale


class CameraSnapshot(Measurement):
    """
    ## CameraSnapshot

    Captures one or more frames from a camera as part of a regular LabExT measurement run, at a
    single wavelength or stepping over a range of them. The images are written next to the
    measurement's own result file, and the data file records per frame statistics plus the file
    names, so an image can always be traced back to the settings it was taken with.

    The images themselves are deliberately kept out of the result file: a single frame of this
    camera is over a megapixel, which would make the JSON unusable.

    #### example setup
    ```
    Camera ---USB3--- Computer
    ```

    #### parameters
    * **exposure time**: Exposure time in microseconds. Clamped to the camera's own limits and
      snapped to its increment, so the value written back into the measurement settings may differ
      slightly from what was requested. With bracketing on this is the longest bracket.
    * **gain**: Analog gain in dB, clamped and snapped in the same way.
    * **pixel format**: `Mono8` gives 8 bit data, `Mono10` and `Mono12` give 16 bit data. Deeper
      formats carry more dynamic range but need a container that can hold it, so keep TIFF or NPY
      on when using them.
    * **ROI width / ROI height / ROI offset x / ROI offset y**: Region of interest in pixels. Leave
      width and height at 0 to use the full sensor. A smaller ROI reads out faster.
    * **integration ROI x / y / width / height**: The region the reported sums are taken over, in
      pixels of the delivered frame. Not the same thing as the camera ROI above: that one crops
      what the sensor reads out, this one only says which part of the frame is measured. Leave
      width and height at 0 to sum the whole frame, or to pick up the region set on a live image in
      the Camera View, which is where the peak search gets its own from. Whichever is used is
      written back into the settings, so the result file records what was actually summed.
    * **fit integration ROI to spot**: Fit the region to the beam instead of setting it by hand. It
      is fitted once, on a frame taken before the first bracket, then held for the whole
      measurement - a region that follows the spot from frame to frame measures the spot's shape
      rather than its power, and one whose size changes between wavelengths puts its own area into
      the response curve.
    * **laser enabled**: Whether the laser drives the chip while the frames are captured. Search
      for Peak switches the light off when it finishes, so a measurement that wants light has to
      turn it back on; untick this to capture the sensor in the dark.
    * **laser wavelength / laser power**: What the laser is set to before it is switched on, and the
      first wavelength of a sweep. Sweeping this through the experiment wizard instead is a trap:
      that makes one to-do per wavelength, and the stages are moved and Search for Peak is run once
      per to-do, so the spectrum would record the alignment as much as the device. Use *wavelength
      stop* below.
    * **wavelength stop**: The last wavelength to capture at. Leave at 0 to capture at *laser
      wavelength* only, which is what a plain snapshot does. Otherwise the measurement steps from
      *laser wavelength* to here, in either direction, and captures the full set of brackets at
      every step - one to-do, one result file, one alignment for the whole spectrum.
    * **wavelength step**: Distance between wavelengths, as a positive number whichever way the
      sweep runs.
    * **wavelength settle time**: Seconds to wait after each step before capturing. A frame taken
      while the laser is still tuning was taken at a wavelength nobody recorded.
    * **laser settle time**: Seconds to wait after the laser is switched on, and again after it is
      switched off before a dark frame. The dark frames are what this is really for: it is what
      keeps the tail of the light out of them.
    * **auto exposure**: Scale the exposure before capturing, so that the brightest pixel lands at
      *auto exposure target fill* of full scale. Costs a handful of extra frames per point. The
      exposure it settles on is written back into the settings, so the recorded exposure always
      describes the images that were taken.
    * **auto exposure target fill**: Where to put the peak, as a fraction of full scale. Keep it
      below ~0.8: the sensor is linear in integration time until it approaches its well limit, and
      that linearity is what makes counts taken at different exposures comparable.
    * **auto exposure max**: Longest exposure auto exposure may ask for, in microseconds. Must be
      shorter than *frame timeout*, or the frame it asks for times out instead of arriving.
    * **exposure brackets**: How many exposures to capture at this point, starting from *exposure
      time* and getting shorter. Leave at 1 to capture a single exposure.
    * **exposure bracket factor**: How much shorter each bracket is than the one before it. At 4,
      three brackets span a factor of 16 in exposure.
    * **capture dark frame**: After the frames, switch the light off and capture one more frame at
      each bracket exposure. They are written as `..._darkNNN.*` and their means are subtracted in
      `dark corrected mean counts`. Both the black level and the dark current come out with them,
      and only a dark at the same exposure can do that, since the dark current scales with the
      exposure while the black level does not.
    * **number of frames**: How many frames to capture at each bracket exposure.
    * **inter-frame delay**: Wait time between frames in seconds. Leave at 0 to capture as fast as
      the camera allows.
    * **frame timeout**: How long to wait for each frame in milliseconds. Must be longer than the
      exposure time.
    * **save TIFF / save PNG / save NPY**: Which formats to write for every captured frame. They are
      independent, so a run can keep the data and something viewable side by side; with all three
      off the measurement records statistics without writing any image.
      TIFF and NPY hold the sensor counts unchanged and are the ones to measure from. The PNG is
      stretched to fill 8 bits so it opens correctly in any viewer, which means it is a picture of
      the frame rather than the frame itself - a raw Mono12 PNG would render almost black, since
      those counts occupy the bottom sixteenth of a 16 bit range.
    * **image output directory**: Where to write the images. Leave empty to write them next to the
      measurement result file. A relative path is taken relative to that same place, so
      `bracket_test` makes a folder beside the results rather than one wherever LabExT was started.
    * **close camera after measurement**: Leave off unless something else needs the camera. Keeping
      the connection open avoids a full camera re-open every time LabExT collects instrument
      metadata, which it does twice per measurement.

    #### bracketing for a relative wavelength response

    Counts follow power only between the noise floor and the well limit, a range of about two
    decades - far less than a transmission spectrum covers. Setting *wavelength stop* and using
    several brackets captures every wavelength at several exposures, and analysis keeps, per
    wavelength, the longest bracket that is not clipped: `saturated pixel fraction` says which
    those are, `exposure time us` turns its counts back into a rate, and the dark frame at the same
    exposure removes the pedestal first. `roi counts per second` is that rate, computed here, and
    `wavelength nm` is the axis to plot it against.

    The sweep runs inside the measurement, which is what keeps the alignment out of the result: one
    to-do per device means the stages are moved and Search for Peak is run once, before the whole
    spectrum, rather than between wavelengths. Auto exposure, if it is on, likewise runs once
    before the sweep, and the bracket ladder is fixed for all of it - the same exposures at every
    wavelength are what make two wavelengths comparable, since whatever systematic an exposure
    carries is then identical at both and cancels in the ratio.

    The dark frames are taken once, per bracket, after the sweep. The dark does not depend on
    wavelength, only on exposure and gain, so one set serves the whole spectrum; over a long sweep
    that does assume the sensor's dark level has not drifted, which a second sweep in the other
    direction will show up.

    Use the ROI series rather than the whole-frame ones. Measured on this setup with three brackets
    a factor of four apart: summed over a region around the spot the rate holds to about 6% across
    a factor of 16 in exposure, while the whole-frame rate climbs from 2186 to 6301 counts/s, a
    spread of 65%, over the same ladder. The difference is a background of one to two counts per
    pixel which is present wherever the beam is not and does not scale with the exposure, so
    dividing it by a shorter exposure inflates it. Over a megapixel that swamps a spot.

    Inside a region it barely matters how big the region is: the same frames give a 5 to 6% spread
    for every box between 30 and 138 px, and only get worse at 200. What does change with the box
    is how much of the beam it holds - 15% at 40 px, 68% at 138 px on that spot - so a box which is
    refitted between wavelengths should be checked for its size wandering, since that fraction goes
    straight into the response curve. `integration roi` is recorded per point for exactly that.

    With auto exposure off the ladder is the same at every wavelength, so the ratio between two
    wavelengths never depends on a setting that moved between them - which is what makes the result
    a relative response rather than a set of unrelated pictures. This measures the response of
    everything in the path together, the laser's own power flatness included, so a reference sweep
    without the device is what turns it into the device's response.

    The frame layout is fixed once acquisition starts, so bracketing and auto exposure are both
    skipped while the Camera View streams; stop it first when either is needed.
    """

    #: settings file in ~/.labext. Also the handover file the camera view writes into, so that
    #: settings dialled in on a live image become this measurement's starting point.
    SETTINGS_FILE_NAME = 'CameraSnapshot_settings.json'

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)  # calling parent constructor

        self.name = 'CameraSnapshot'
        self.settings_path = self.SETTINGS_FILE_NAME

        self.parameters = CameraSnapshot.get_default_parameter()
        self.wanted_instruments = CameraSnapshot.get_wanted_instrument()

        self.instr_camera = None
        self.instr_laser = None

    #: parameters the camera view can hand over, mapped to the type each one must be stored as.
    #: Kept here rather than in the GUI so that the two sides cannot drift apart.
    CAMERA_SETTING_TYPES = {
        'exposure time': float,
        'gain': float,
        'pixel format': str,
        'ROI width': int,
        'ROI height': int,
        'ROI offset x': int,
        'ROI offset y': int,
    }

    @classmethod
    def get_default_parameter(cls):
        parameters = {
            'exposure time': MeasParamFloat(value=10000.0, unit='us'),
            'gain': MeasParamFloat(value=0.0, unit='dB'),
            'pixel format': MeasParamList(options=['Mono8', 'Mono10', 'Mono12'], value='Mono8'),
            'ROI width': MeasParamInt(value=0, unit='px'),
            'ROI height': MeasParamInt(value=0, unit='px'),
            'ROI offset x': MeasParamInt(value=0, unit='px'),
            'ROI offset y': MeasParamInt(value=0, unit='px'),
            # The region the reported sums are taken over, in pixels of the delivered frame, and a
            # different thing from the camera ROI above, which crops what the sensor reads out.
            # Leave at zero to sum the whole frame, or to pick up the region dialled in on a live
            # image in the Camera View, which is where the peak search takes its own from.
            'integration ROI x': MeasParamInt(value=0, unit='px'),
            'integration ROI y': MeasParamInt(value=0, unit='px'),
            'integration ROI width': MeasParamInt(value=0, unit='px'),
            'integration ROI height': MeasParamInt(value=0, unit='px'),
            # Fitted once, on a frame taken before the first bracket, and then held for every frame
            # in the measurement. Held rather than re-fitted per frame because a region that
            # follows the spot measures the spot's shape instead of its power, and one that changes
            # size between wavelengths puts its own area into the response curve.
            'fit integration ROI to spot': MeasParamBool(value=False),
            # The laser has to be driven from here. Search for Peak enables it only inside a
            # `with self.instr_laser:` block and switches it off again on the way out, so by the
            # time a measurement runs the light is off unless the measurement turns it back on.
            'laser enabled': MeasParamBool(value=True),
            'laser wavelength': MeasParamFloat(value=1550.0, unit='nm'),
            'laser power': MeasParamFloat(value=-15.0, unit='dBm'),
            # The sweep runs inside the measurement rather than through the experiment wizard's
            # parameter sweep, which would make one to-do per wavelength: the stages are moved and
            # Search for Peak is run once per to-do, so a wizard sweep re-aligns between wavelengths
            # and the spectrum that comes out is a record of the alignment as much as of the device.
            # One to-do per device also puts the whole spectrum in one result file.
            # Named 'wavelength stop' rather than renaming 'laser wavelength' to 'wavelength start'
            # so that a single-wavelength capture, and a saved settings file, keep working unchanged.
            'wavelength stop': MeasParamFloat(value=0.0, unit='nm'),
            'wavelength step': MeasParamFloat(value=5.0, unit='nm'),
            # Tuning is not instant and a frame taken while the laser is still settling is taken at
            # a wavelength nobody recorded, so this waits after every step.
            'wavelength settle time': MeasParamFloat(value=0.2, unit='s'),
            # Time for the laser to settle after being switched on or off. The dark frames are
            # taken right after the light goes out, so this is what keeps residual light out of
            # them, not just a courtesy delay.
            'laser settle time': MeasParamFloat(value=0.2, unit='s'),
            # Fills the well rather than clipping it: exposure is scaled so the brightest pixel
            # lands at this fraction of full scale. Below ~0.8, where a CMOS sensor is still
            # linear in integration time, which is what makes counts/s comparable between points
            # taken at different exposures. Off by default so an existing sweep is unaffected.
            'auto exposure': MeasParamBool(value=False),
            'auto exposure target fill': MeasParamFloat(value=0.7),
            'auto exposure max': MeasParamFloat(value=200000.0, unit='us'),
            # Several exposures at the same point, each one shorter than the last. One exposure
            # only covers the two decades or so between the noise floor and the well limit, which
            # is less than a transmission spectrum spans; a ladder covers the rest, because every
            # point then has a bracket short enough not to clip and one long enough to be seen.
            'exposure brackets': MeasParamInt(value=1),
            'exposure bracket factor': MeasParamFloat(value=4.0),
            # A dark frame at the same exposure and gain, taken with the light gated off. Both the
            # fixed black level and the dark current are removed by it, and the dark current is
            # the part that scales with exposure - so a dark taken at one exposure cannot correct
            # a frame taken at another, which is why this is per point rather than a stored file.
            'capture dark frame': MeasParamBool(value=False),
            'number of frames': MeasParamInt(value=1),
            'inter-frame delay': MeasParamFloat(value=0.0, unit='s'),
            'frame timeout': MeasParamFloat(value=5000.0, unit='ms'),
            # Independent rather than one choice, so a run can keep the data and something
            # viewable side by side. Untick them all to record statistics without writing images.
            'save TIFF': MeasParamBool(value=True),
            'save PNG': MeasParamBool(value=True),
            'save NPY': MeasParamBool(value=False),
            'image output directory': MeasParamString(value=''),
            'close camera after measurement': MeasParamBool(value=False),
            'users comment': MeasParamString(value='')
        }
        cls._apply_saved_camera_settings(parameters)
        return parameters

    @classmethod
    def _apply_saved_camera_settings(cls, parameters):
        """Overlay the camera settings the camera view last handed over, if there are any.

        The camera view writes them into this measurement's own settings file, so the swept
        experiment wizard and the settings window pick them up on their own. Applying them here as
        well covers the new-measurement wizard, which builds its table from the defaults instead of
        reading that file.
        """
        try:
            settings_file_path = get_configuration_file_path(cls.SETTINGS_FILE_NAME)
            if not os.path.isfile(settings_file_path):
                return
            with open(settings_file_path, 'r') as settings_file:
                stored = json.load(settings_file).get('data', {})
        except Exception as exc:
            # a damaged settings file must never stop the measurement from being constructed
            logging.getLogger().warning(
                "Could not read stored CameraSnapshot settings (%r), using built-in defaults.", exc)
            return
        if not isinstance(stored, dict):
            return

        for name, wanted_type in cls.CAMERA_SETTING_TYPES.items():
            if name not in stored or name not in parameters:
                continue
            try:
                # MeasParamInt/MeasParamFloat type-check on assignment, so cast explicitly
                parameters[name].value = wanted_type(stored[name])
            except (TypeError, ValueError):
                logging.getLogger().warning(
                    "Ignoring stored CameraSnapshot setting %r: %r is not a %s.",
                    name, stored[name], wanted_type.__name__)

    @staticmethod
    def get_non_sweepable_parameters() -> Dict[str, MeasParam]:
        def_params = CameraSnapshot.get_default_parameter()
        return {
            # Settings which say how a point is captured rather than what it is captured at, so
            # there is nothing to plot against them - sweeping them would just relabel the axis.
            'wavelength stop': def_params['wavelength stop'],
            'wavelength step': def_params['wavelength step'],
            'wavelength settle time': def_params['wavelength settle time'],
            'integration ROI x': def_params['integration ROI x'],
            'integration ROI y': def_params['integration ROI y'],
            'integration ROI width': def_params['integration ROI width'],
            'integration ROI height': def_params['integration ROI height'],
            'fit integration ROI to spot': def_params['fit integration ROI to spot'],
            'laser settle time': def_params['laser settle time'],
            'auto exposure': def_params['auto exposure'],
            'auto exposure target fill': def_params['auto exposure target fill'],
            'auto exposure max': def_params['auto exposure max'],
            'exposure brackets': def_params['exposure brackets'],
            'exposure bracket factor': def_params['exposure bracket factor'],
            'capture dark frame': def_params['capture dark frame'],
            'number of frames': def_params['number of frames'],
            'save TIFF': def_params['save TIFF'],
            'save PNG': def_params['save PNG'],
            'save NPY': def_params['save NPY'],
            'image output directory': def_params['image output directory'],
            'close camera after measurement': def_params['close camera after measurement'],
            'users comment': def_params['users comment']
        }

    @staticmethod
    def get_wanted_instrument():
        return ['Laser', 'Camera']

    @staticmethod
    def _resolve_output_target(output_directory, data):
        """Work out where to write images and what to call them.

        By default images land next to the measurement's own result file and share its name, so
        that they stay associated with it even once they are moved around. `data` is an
        AutosaveDict during a real run and carries the result file path; when a measurement is
        driven standalone it is an ordinary dict and has no such path.

        Returns:
            tuple: `(directory, file name stem)`
        """
        result_file_path = getattr(data, 'file_path', None)
        if result_file_path:
            # the result file is called <stem>.json.part while the measurement is running
            stem = os.path.basename(result_file_path).split('.json')[0]
            default_directory = os.path.dirname(os.path.abspath(result_file_path))
        else:
            stem = 'CameraSnapshot'
            default_directory = os.getcwd()

        if not output_directory:
            directory = default_directory
        else:
            # A relative path is taken as relative to where the result file goes, not to where
            # LabExT happens to have been started: 'bracket_test' next to the results is what
            # someone typing it means, and the process working directory is invisible from the GUI.
            directory = os.path.abspath(os.path.join(default_directory, output_directory))
        os.makedirs(directory, exist_ok=True)
        return directory, stem

    #: refuse a sweep longer than this. Each wavelength costs a settle plus every bracket and
    #: frame, so a step which is a thousand times too small is a runaway rather than a long run.
    MAX_SWEEP_POINTS = 2000

    @classmethod
    def _sweep_wavelengths(cls, start, stop, step):
        """The wavelengths to capture at, in the order they are set.

        A stop at or below zero means no sweep at all, which is the single-wavelength capture this
        measurement has always done. Otherwise it walks from `start` to `stop` inclusive, in either
        direction, so a descending sweep needs no negative step.

        Returns:
            list: wavelengths in nm, always at least one long
        """
        start = float(start)
        stop = float(stop)
        if stop <= 0.0 or stop == start:
            return [start]
        step = abs(float(step))
        if step <= 0.0:
            raise ValueError(
                "wavelength step must be greater than 0 to sweep from {:.3f} nm to {:.3f} nm, got "
                "{:.3f} nm.".format(start, stop, step))

        span = abs(stop - start)
        count = int(math.floor(span / step + 1e-9)) + 1
        if count > cls.MAX_SWEEP_POINTS:
            raise ValueError(
                "A sweep from {:.3f} nm to {:.3f} nm in steps of {:.3f} nm is {:d} wavelengths, "
                "more than the {:d} this measurement will run. Use a larger step.".format(
                    start, stop, step, count, cls.MAX_SWEEP_POINTS))

        direction = 1.0 if stop > start else -1.0
        wavelengths = [start + direction * step * index for index in range(count)]
        # the last step rarely lands exactly on the stop; include it when there is room left for it,
        # so a sweep to 1570 nm actually reaches 1570 nm rather than stopping just short
        if abs(wavelengths[-1] - stop) > 1e-9 and abs(stop - start) >= abs(wavelengths[-1] - start):
            wavelengths.append(stop)
        return wavelengths

    def _check_wavelengths_tunable(self, wavelengths):
        """Refuse a sweep the laser cannot tune over, before any of it is captured.

        Otherwise the run gets as far as the first wavelength outside the laser's range and fails
        there, having spent the intervening minutes on frames that are then thrown away.
        """
        try:
            minimum = float(self.instr_laser.min_lambda)
            maximum = float(self.instr_laser.max_lambda)
        except Exception:
            # not every laser driver reports its range, and a failed read must not stop a sweep
            # which the laser may well be able to do
            self.logger.debug("Could not read the laser's tuning range.", exc_info=True)
            return
        outside = [wl for wl in wavelengths if wl < minimum or wl > maximum]
        if outside:
            raise ValueError(
                "The laser tunes from {:.3f} nm to {:.3f} nm, so it cannot reach {:s} of the {:d} "
                "wavelengths asked for, starting at {:.3f} nm.".format(
                    minimum, maximum,
                    "any" if len(outside) == len(wavelengths) else "{:d}".format(len(outside)),
                    len(wavelengths), outside[0]))

    @staticmethod
    def _resolve_integration_roi(x, y, width, height, frame_width, frame_height):
        """The integration region in pixels of the delivered frame.

        A zero width or height means the whole frame, which is what a measurement that was never
        given a region gets. A region that hangs off the edge raises rather than being silently
        shrunk: numpy slicing would return a smaller patch and quietly rescale every sum in the run.

        Returns:
            tuple: `(x, y, width, height)`
        """
        x, y = int(x), int(y)
        width = int(width) or int(frame_width)
        height = int(height) or int(frame_height)
        if (x < 0 or y < 0 or width <= 0 or height <= 0
                or x + width > frame_width or y + height > frame_height):
            raise ValueError(
                "Integration ROI x={:d} y={:d} width={:d} height={:d} does not fit inside the "
                "{:d}x{:d} frame the camera delivers. The region is in pixels of the delivered "
                "frame, so if the camera ROI is cropped these are relative to that crop, not to "
                "the full sensor.".format(x, y, width, height, frame_width, frame_height))
        return x, y, width, height

    @staticmethod
    def _record_integration_roi(parameters, roi, data=None):
        """Write the region actually summed back into the settings, so the results describe it.

        `data` is given when the settings have already been written into the result file and the
        entries have to be corrected as well, which is the case for a region fitted at capture time.
        """
        names = ('integration ROI x', 'integration ROI y',
                 'integration ROI width', 'integration ROI height')
        for name, value in zip(names, roi):
            parameters.get(name).value = int(value)
            if data is not None:
                data['measurement settings'][name] = parameters.get(name).as_dict()

    def _fit_integration_roi(self, frame_timeout, frame_width, frame_height, unlit_frame=None):
        """Fit the integration region to the beam on a frame taken for the purpose.

        Uses the camera-backed power meter's fit, so the region a measurement sums is chosen the
        same way as the region a peak search sums, rather than by a lookalike that can drift.

        `unlit_frame` is a frame taken at the same exposure with the light off, and it is what makes
        this survive a long exposure. The sensor's hot pixels grow with the exposure - one on this
        camera runs at 41.6 counts per millisecond - so past a few tens of milliseconds they are
        brighter than a weak spot, and a fit looking for one compact bright thing finds two and
        gives up. Subtracting the unlit frame removes them exactly, because they are the same
        pixels doing the same thing whether the light is on or not.

        Returns:
            tuple: `((x, y, width, height), note)`, the region falling back to the whole frame when
            there is no compact spot to fit.
        """
        image = self.instr_camera.snap_photo(timeout_ms=int(frame_timeout))
        roi, note = PowerMeterCameraAlvium.fit_roi_to_spot(image, dark_reference=unlit_frame)
        if roi is None:
            self.logger.warning(
                "Could not fit the integration ROI to a spot (%s); summing the whole frame. The "
                "whole-frame sum carries a background that does not scale with the exposure, so a "
                "rate taken from it is not comparable between brackets.", note)
            return (0, 0, int(frame_width), int(frame_height)), note
        return self._resolve_integration_roi(*roi, frame_width, frame_height), note

    def _save_frame(self, path_stem, image, save_tiff, save_png, save_npy):
        """Write one frame in every selected format and return the file names written.

        Every format gets the same array, so a TIFF and the PNG beside it are one shot rather than
        two captures.

        Returns:
            list: the base names of the files written, in the order TIFF, PNG, NPY.
        """
        file_names = []
        if save_tiff:
            file_names.append(os.path.basename(self.instr_camera.save_photo(
                path_stem + '.tif', image=image)))
        if save_png:
            # stretched to fill 8 bits, so it is viewable whatever the pixel format. The TIFF or
            # NPY alongside it carries the counts; a raw Mono12 PNG is near black.
            file_names.append(os.path.basename(self.instr_camera.save_photo(
                path_stem + '.png', image=self.instr_camera.stretch_to_8bit(image))))
        if save_npy:
            file_names.append(os.path.basename(self.instr_camera.save_photo_raw(
                path_stem + '.npy', image=image)))
        return file_names

    #: peak fill above which a dark frame is reported as not dark. Well above the pedestal and the
    #: dark current of a short exposure, so it fires on light reaching the sensor rather than on a
    #: sensor doing what it normally does.
    DARK_FRAME_MAX_FILL = 0.1

    def _auto_expose(self, target_fill, max_exposure, frame_timeout, region=None):
        """Scale the exposure until the brightest pixel sits at `target_fill` of full scale.

        Runs with the light on and settled, immediately before the frames are captured, so that the
        exposure it settles on is the one they are taken at. The convergence itself lives with the
        camera driver, so that this measurement, the live Camera View's button and anything else
        that needs it are the same code rather than three loops that drift apart.

        Returns:
            dict: what happened, for the result file: the exposure reached, the peak fill of the
            last frame taken - which is always a frame at that exposure - the number of frames it
            cost and whether it converged.
        """
        outcome = converge_exposure(
            self.instr_camera,
            # The dtype is only the fallback for a format name that carries no bit count, which
            # Mono8, Mono10 and Mono12 all do, so it never decides anything here.
            full_scale=_full_scale_counts(self.instr_camera.pixel_format, np.uint16),
            target_fill=target_fill,
            max_exposure=max_exposure,
            timeout_ms=int(frame_timeout),
            # the beam's own peak, not the frame's: a hot pixel grows with the exposure and would
            # otherwise be what gets filled to the target, leaving the beam far under-exposed
            region=region)

        if outcome['status'] != 'converged':
            # Both failures leave the frames being captured at an exposure nobody chose, which is
            # worth saying out loud; only 'at a limit' is a setting the user can do something about.
            self.logger.warning("Auto exposure %s. The frames are captured at this exposure.",
                                outcome['note'])

        return {
            'converged': outcome['status'] == 'converged',
            'status': outcome['status'],
            'frames used': int(outcome['frames used']),
            'exposure time us': float(self.instr_camera.exposure_time),
            'peak fill': float(outcome['peak fill']),
            'target fill': float(target_fill),
        }

    def _bracket_exposures(self, count, factor):
        """The exposure ladder for one point, longest first.

        Each bracket is `factor` times shorter than the one before it, starting from the exposure
        the camera is set to. Descending rather than ascending on purpose: the longest exposure is
        the one the frame timeout and the camera's own limits were checked against, so every
        bracket below it is safe by construction, and a point whose longest bracket clips still has
        a shorter one which does not.

        Returns:
            list: requested exposures in microseconds. The camera snaps each onto its own increment
            grid when it is set, so the value actually used is read back per bracket.
        """
        low, high = self.instr_camera.exposure_time_range
        anchor = float(self.instr_camera.exposure_time)
        exposures = []
        for step in range(count):
            wanted = min(max(anchor / (factor ** step), low), high)
            if exposures and wanted >= exposures[-1] * (1.0 - 1e-6):
                # The ladder has run into the camera's shortest exposure, so a further bracket
                # would repeat the last one instead of adding a point to the response.
                self.logger.warning(
                    "Only %d of %d exposure brackets fit above the camera's shortest exposure "
                    "(%.1f us); the rest would repeat it.", len(exposures), count, low)
                break
            exposures.append(wanted)
        return exposures

    def algorithm(self, device, data, instruments, parameters):

        # get the parameters
        exposure_time = parameters.get('exposure time').value
        gain = parameters.get('gain').value
        pixel_format = parameters.get('pixel format').value
        roi = (parameters.get('ROI width').value,
               parameters.get('ROI height').value,
               parameters.get('ROI offset x').value,
               parameters.get('ROI offset y').value)
        n_frames = parameters.get('number of frames').value
        n_brackets = parameters.get('exposure brackets').value
        bracket_factor = parameters.get('exposure bracket factor').value
        frame_delay = parameters.get('inter-frame delay').value
        frame_timeout = parameters.get('frame timeout').value
        save_tiff = parameters.get('save TIFF').value
        save_png = parameters.get('save PNG').value
        save_npy = parameters.get('save NPY').value
        output_directory = parameters.get('image output directory').value
        close_camera = parameters.get('close camera after measurement').value
        laser_enabled = parameters.get('laser enabled').value
        laser_wavelength = parameters.get('laser wavelength').value
        laser_power = parameters.get('laser power').value
        laser_settle_time = parameters.get('laser settle time').value
        wavelength_stop = parameters.get('wavelength stop').value
        wavelength_step = parameters.get('wavelength step').value
        wavelength_settle_time = parameters.get('wavelength settle time').value
        auto_exposure = parameters.get('auto exposure').value
        auto_exposure_target_fill = parameters.get('auto exposure target fill').value
        auto_exposure_max = parameters.get('auto exposure max').value
        capture_dark_frame = parameters.get('capture dark frame').value
        fit_integration_roi = parameters.get('fit integration ROI to spot').value
        integration_roi = (parameters.get('integration ROI x').value,
                           parameters.get('integration ROI y').value,
                           parameters.get('integration ROI width').value,
                           parameters.get('integration ROI height').value)

        if n_frames < 1:
            raise ValueError("number of frames must be at least 1, got {:d}.".format(n_frames))
        if n_brackets < 1:
            raise ValueError(
                "exposure brackets must be at least 1, got {:d}.".format(n_brackets))
        if n_brackets > 1 and bracket_factor <= 1.0:
            raise ValueError(
                "exposure bracket factor must be greater than 1 to separate the brackets, got "
                "{:.3f}.".format(bracket_factor))
        if laser_settle_time < 0.0:
            raise ValueError(
                "laser settle time cannot be negative, got {:.3f} s.".format(laser_settle_time))
        if wavelength_settle_time < 0.0:
            raise ValueError(
                "wavelength settle time cannot be negative, got {:.3f} s.".format(
                    wavelength_settle_time))
        # raises on a step which cannot get from one end to the other, before the camera is touched
        wavelengths = self._sweep_wavelengths(laser_wavelength, wavelength_stop, wavelength_step)
        # Only the longest exposure needs checking against the timeout: the brackets below it are
        # shorter, and auto exposure is bounded by its own maximum.
        if frame_timeout <= exposure_time / 1000.0:
            raise ValueError(
                "frame timeout ({:.1f} ms) must be longer than the exposure time ({:.1f} us = "
                "{:.1f} ms).".format(frame_timeout, exposure_time, exposure_time / 1000.0))
        if auto_exposure:
            if not 0.0 < auto_exposure_target_fill < 1.0:
                raise ValueError(
                    "auto exposure target fill must be between 0 and 1 exclusive, got "
                    "{:.3f}.".format(auto_exposure_target_fill))
            if auto_exposure_max <= 0.0:
                raise ValueError(
                    "auto exposure max must be positive, got {:.1f} us.".format(auto_exposure_max))
            if frame_timeout <= auto_exposure_max / 1000.0:
                raise ValueError(
                    "frame timeout ({:.1f} ms) must be longer than auto exposure max ({:.1f} us = "
                    "{:.1f} ms), otherwise the frame auto exposure asks for times out instead of "
                    "arriving.".format(
                        frame_timeout, auto_exposure_max, auto_exposure_max / 1000.0))
            if auto_exposure_target_fill > 0.9:
                self.logger.warning(
                    "An auto exposure target fill of %.2f leaves almost no headroom: a sensor "
                    "stops being linear in exposure as it approaches its well limit, and noise on "
                    "the peak pixel alone can clip it. 0.7 or below keeps counts proportional to "
                    "power.", auto_exposure_target_fill)

        # get the instruments
        self.instr_camera = instruments['Camera']
        self.instr_laser = instruments['Laser']

        # open connections
        self.instr_camera.open()
        self.instr_laser.open()

        if laser_enabled:
            self.instr_laser.unit = 'dBm'
            self._check_wavelengths_tunable(wavelengths)
            self.instr_laser.wavelength = wavelengths[0]
            self.instr_laser.power = laser_power

        # Apply settings. The order matters: changing the pixel format or the ROI can move the
        # limits of the other features, so those go first.
        #
        # The camera fixes its frame layout once acquisition starts, so with the live Camera View
        # streaming these two cannot be changed. Rather than failing the run - which would abort
        # every device in a multi-device experiment - the frames are captured in whatever layout
        # the stream is already producing, and the read-back below records what was actually used.
        # Exposure and gain are held back for the same reason even though the camera would accept
        # them mid-stream: a dark reference captured through the live view is only valid for the
        # exposure it was taken at, so quietly re-exposing a streaming camera breaks the peak
        # search rather than this measurement, which is the hardest kind of failure to trace back.
        streaming = bool(getattr(self.instr_camera, 'is_streaming', False))
        if streaming:
            self.logger.warning(
                "Camera is streaming, so this measurement captures with the live Camera View's "
                "settings (%s, ROI %s, %.1f us, %.2f dB) rather than its own (%s, ROI %s, "
                "%.1f us, %.2f dB). Stop the Camera View if the configured settings are required.",
                self.instr_camera.pixel_format, self.instr_camera.roi,
                self.instr_camera.exposure_time, self.instr_camera.gain,
                pixel_format, roi, exposure_time, gain)
            # Both of these work by re-exposing the camera, which a streaming camera does accept -
            # but the stream is what the peak search is reading, and its own reference frames go
            # with it, so this measurement does not move the exposure under it.
            if auto_exposure:
                self.logger.warning(
                    "Auto exposure is skipped while the Camera View streams. Stop it to use it.")
                auto_exposure = False
            if n_brackets > 1:
                self.logger.warning(
                    "Exposure bracketing is skipped while the Camera View streams: one bracket is "
                    "captured, at the live view's exposure. Stop it to use bracketing.")
                n_brackets = 1
        else:
            self.instr_camera.pixel_format = pixel_format
            self.instr_camera.set_roi(*roi)
            self.instr_camera.exposure_time = exposure_time
            self.instr_camera.gain = gain

        # read back what the camera actually accepted: both exposure and gain get snapped onto the
        # camera's increment grid, and the ROI onto its own. Writing these back means the saved
        # settings describe the images which were taken, not the ones which were asked for.
        parameters.get('exposure time').value = float(self.instr_camera.exposure_time)
        parameters.get('gain').value = float(self.instr_camera.gain)
        parameters.get('pixel format').value = str(self.instr_camera.pixel_format)
        actual_roi = self.instr_camera.roi
        parameters.get('ROI width').value = int(actual_roi[0])
        parameters.get('ROI height').value = int(actual_roi[1])
        parameters.get('ROI offset x').value = int(actual_roi[2])
        parameters.get('ROI offset y').value = int(actual_roi[3])

        frame_width, frame_height = int(actual_roi[0]), int(actual_roi[1])
        integration_roi_note = None
        if not fit_integration_roi and not any(integration_roi):
            # Nothing set here, so take the region dialled in on a live image in the Camera View if
            # there is one. That is where the peak search takes its own from, and having a
            # measurement sum a different part of the frame than the search aimed at is a trap.
            stored_roi = PowerMeterCameraAlvium.load_integration_roi()
            if stored_roi is not None:
                integration_roi = tuple(stored_roi)
                integration_roi_note = "taken from the Camera View's stored integration ROI"
                self.logger.info(
                    "Summing the integration ROI %s set in the Camera View; set 'integration ROI "
                    "width' and 'height' here to override it.", list(integration_roi))
        if not fit_integration_roi:
            # validated before anything is captured: a region that does not fit the frame is a
            # settings mistake, and finding out after the images are written wastes the run
            integration_roi = self._resolve_integration_roi(
                *integration_roi, frame_width, frame_height)
            self._record_integration_roi(parameters, integration_roi)

        # write the measurement parameters into the measurement settings
        for pname, pparam in parameters.items():
            data['measurement settings'][pname] = pparam.as_dict()

        directory, stem = self._resolve_output_target(output_directory, data)

        # capture. Frames are collected into local lists and only written into data at the end:
        # data is an AutosaveDict which re-serialises the whole result file every few accesses, so
        # touching it inside the loop would rewrite the file once per frame.
        frame_indices = []
        frame_wavelengths = []
        bracket_indices = []
        frame_exposures = []
        timestamps = []
        file_names = []
        mean_counts = []
        min_counts = []
        max_counts = []
        std_counts = []
        saturated_fractions = []
        roi_sums = []

        # The laser is on for the whole capture: `with` enables it on the way in and switches it
        # off again on the way out, including if a capture raises, so a failed run cannot leave
        # light on the chip. nullcontext covers 'laser enabled' being unticked, which is how a
        # dark frame or a reference shot is taken.
        laser_on = self.instr_laser if laser_enabled else nullcontext()
        auto_exposure_result = None

        # One frame with the light still off, at the exposure the run starts with, kept only to
        # locate the beam. Its hot pixels are the same pixels the lit frame has, so subtracting it
        # leaves the beam and nothing else - which is what a fit looking for one compact bright
        # thing needs, and what a whole-frame maximum cannot give once a hot pixel outshines a weak
        # spot. Skipped when the laser is off, since then there is no beam to find either way.
        unlit_frame = None
        if fit_integration_roi and laser_enabled:
            unlit_frame = self.instr_camera.snap_photo(
                timeout_ms=int(frame_timeout)).astype(np.float64)

        with laser_on:
            # The light has to be there before the first frame is read out, and the fit and auto
            # exposure both measure it, so this settle comes before any of them.
            if laser_enabled and laser_settle_time > 0.0:
                sleep(laser_settle_time)

            if fit_integration_roi:
                # Before auto exposure, not after: auto exposure needs to know which pixels are the
                # beam so that it fills those rather than whatever is brightest in the frame.
                integration_roi, integration_roi_note = self._fit_integration_roi(
                    frame_timeout, frame_width, frame_height, unlit_frame=unlit_frame)
                self._record_integration_roi(parameters, integration_roi, data)
                self.logger.info("Fitted integration ROI %s: %s.",
                                 list(integration_roi), integration_roi_note)

            roi_x, roi_y, roi_width, roi_height = integration_roi

            if auto_exposure:
                auto_exposure_result = self._auto_expose(
                    auto_exposure_target_fill, auto_exposure_max, frame_timeout,
                    region=integration_roi)
                # The frames are taken at whatever it settled on, so the recorded exposure follows
                # it rather than the value the run started with. The bracket ladder starts here too.
                parameters.get('exposure time').value = float(self.instr_camera.exposure_time)
                data['measurement settings']['exposure time'] = \
                    parameters.get('exposure time').as_dict()

            # One ladder for the whole sweep, and one auto exposure above it: the same exposures at
            # every wavelength are what make two wavelengths comparable, since whatever systematic
            # an exposure carries is then identical at both and cancels in the ratio.
            bracket_exposures = self._bracket_exposures(n_brackets, bracket_factor)

            image = None
            for index, wavelength in enumerate(wavelengths):
                # The first wavelength was set before the laser was switched on, and the settle
                # after switching on covers it, so only the steps after it need tuning and waiting.
                if laser_enabled and index > 0:
                    self.instr_laser.wavelength = wavelength
                    if wavelength_settle_time > 0.0:
                        sleep(wavelength_settle_time)
                first_index_here = len(frame_indices)

                for bracket, bracket_exposure in enumerate(bracket_exposures):
                    if len(frame_indices) > first_index_here and frame_delay > 0.0:
                        # a bracket boundary is a gap between two frames like any other
                        sleep(frame_delay)
                    if len(bracket_exposures) > 1:
                        self.instr_camera.exposure_time = bracket_exposure
                    # Read back per bracket rather than trusting the request: this is the exposure
                    # the counts are divided by, and the camera snaps it onto its increment grid.
                    actual_exposure = float(self.instr_camera.exposure_time)

                    if frame_delay > 0.0:
                        images = None  # captured one at a time below, so the delay is honoured
                    else:
                        images = self.instr_camera.snap_photos(
                            n_frames, timeout_ms=int(frame_timeout))

                    for idx in range(n_frames):
                        if images is None:
                            if idx > 0:
                                sleep(frame_delay)
                            image = self.instr_camera.snap_photo(timeout_ms=int(frame_timeout))
                        else:
                            image = images[idx]

                        full_scale = _full_scale_counts(
                            parameters.get('pixel format').value, image.dtype)
                        clip_level = _clip_level(full_scale)

                        # Numbered straight through the sweep, so a frame index is unique within
                        # the result file and unchanged from a single-point run. Which wavelength
                        # and exposure a frame was taken at is in the series beside it.
                        frame_number = len(frame_indices)
                        frame_files = self._save_frame(
                            os.path.join(directory, "{:s}_frame{:03d}".format(stem, frame_number)),
                            image, save_tiff, save_png, save_npy)

                        frame_indices.append(int(frame_number))
                        frame_wavelengths.append(float(wavelength))
                        bracket_indices.append(int(bracket))
                        frame_exposures.append(actual_exposure)
                        timestamps.append(datetime.now(timezone.utc).isoformat())
                        file_names.append(frame_files)
                        # numpy float64 -> python float, else the result file is not serialisable
                        mean_counts.append(float(np.mean(image)))
                        min_counts.append(float(np.min(image)))
                        max_counts.append(float(np.max(image)))
                        std_counts.append(float(np.std(image)))
                        # Summed with the power meter's own helper, so a number here and a number
                        # the peak search acts on are the same computation. Raw: the dark frames
                        # are not taken until the light is off, and subtracting a sum from a sum
                        # afterwards is exact, so nothing has to be held in memory for it.
                        roi_sums.append(PowerMeterCameraAlvium.integrate_patch(
                            image[roi_y:roi_y + roi_height, roi_x:roi_x + roi_width]))
                        saturated = int(np.count_nonzero(image >= clip_level))
                        saturated_fractions.append(float(saturated / image.size))
                        if saturated and len(bracket_exposures) == 1:
                            # Worth a warning rather than only a number in the results: clipped
                            # counts are no longer proportional to power, so a sweep comparing
                            # frames to each other is silently wrong at the bright end unless the
                            # exposure comes down. With brackets there is nothing to warn about yet
                            # - a clipped long bracket is what the short ones are for - so that
                            # case is summarised per wavelength below.
                            self.logger.warning(
                                "Frame %d of %d at %.3f nm is clipped: %d pixels (%.3f%%) at or "
                                "above full scale (%.0f counts for %s). Reduce the exposure time "
                                "(%.1f us) or gain (%.2f dB); counts are not proportional to power "
                                "once clipped.",
                                idx + 1, n_frames, wavelength, saturated,
                                100.0 * saturated / image.size, clip_level,
                                parameters.get('pixel format').value,
                                actual_exposure, float(self.instr_camera.gain))

                if (len(bracket_exposures) > 1
                        and all(f > 0.0 for f in saturated_fractions[first_index_here:])):
                    # No bracket got through unclipped, so this wavelength has no usable
                    # measurement in it at all - the shortest bracket is still too long. Reported
                    # per wavelength, because it is a property of the ladder against this point
                    # rather than of any one frame, and a spectrum can clip at one end only.
                    self.logger.warning(
                        "Every one of the %d exposure brackets clipped at %.3f nm, down to %.1f "
                        "us, so none of them measures the power there. Shorten 'exposure time' or "
                        "add brackets.", len(bracket_exposures), wavelength, frame_exposures[-1])

        # Dark frames come after the signal frames and outside the laser context, which has just
        # switched the light off, and there is one per bracket: the dark current scales with the
        # exposure while the black level does not, so a dark taken at one bracket cannot correct
        # another. Taking them last also means they follow whatever auto exposure settled on.
        dark_frames = []
        if capture_dark_frame:
            if not laser_enabled:
                self.logger.warning(
                    "'capture dark frame' is on but 'laser enabled' is off, so the frames above "
                    "were already taken in the dark. No separate dark frame was captured.")
            else:
                if laser_settle_time > 0.0:
                    sleep(laser_settle_time)
                for bracket, bracket_exposure in enumerate(bracket_exposures):
                    if len(bracket_exposures) > 1:
                        self.instr_camera.exposure_time = bracket_exposure
                    dark_image = self.instr_camera.snap_photo(timeout_ms=int(frame_timeout))
                    dark_files = self._save_frame(
                        os.path.join(directory, "{:s}_dark{:03d}".format(stem, bracket)),
                        dark_image, save_tiff, save_png, save_npy)

                    # the peak inside the region that is summed, not the frame's: a hot pixel
                    # elsewhere grows with the exposure and would report every long dark as "not
                    # dark" while saying nothing about the pixels the reading is taken from
                    dark_peak_fill = float(np.max(
                        dark_image[roi_y:roi_y + roi_height, roi_x:roi_x + roi_width])) / full_scale
                    if dark_peak_fill > self.DARK_FRAME_MAX_FILL:
                        self.logger.warning(
                            "Dark frame %d is not dark: inside the integration ROI its brightest "
                            "pixel is at %.1f%% of full scale. Either light is still reaching the "
                            "camera - which a longer 'laser settle time' or a light-tight enclosure "
                            "fixes - or the exposure and gain are high enough for dark current "
                            "alone to fill the sensor.", bracket, 100.0 * dark_peak_fill)

                    dark_frames.append({
                        'bracket index': int(bracket),
                        'exposure time us': float(self.instr_camera.exposure_time),
                        'files': dark_files,
                        'timestamp utc': datetime.now(timezone.utc).isoformat(),
                        'roi sum': PowerMeterCameraAlvium.integrate_patch(
                            dark_image[roi_y:roi_y + roi_height, roi_x:roi_x + roi_width]),
                        'mean counts': float(np.mean(dark_image)),
                        'min counts': float(np.min(dark_image)),
                        'max counts': float(np.max(dark_image)),
                        'std counts': float(np.std(dark_image)),
                    })

        # Leave the camera on the exposure the saved settings describe, rather than on the last and
        # shortest bracket, so that the live view and the next peak search find what they expect.
        if len(bracket_exposures) > 1:
            self.instr_camera.exposure_time = bracket_exposures[0]

        # data['values'] must hold numeric series only: LabExT plots every one of them, and
        # PlotControl runs np.isfinite over the y data, which raises on strings. The file names and
        # timestamps are metadata about the capture rather than measured values, so they belong
        # alongside the other settings.
        data['values']['frame index'] = frame_indices
        # first, so it is the natural x axis for a spectrum in the plots
        data['values']['wavelength nm'] = frame_wavelengths
        data['values']['bracket index'] = bracket_indices
        data['values']['exposure time us'] = frame_exposures
        data['values']['mean counts'] = mean_counts
        data['values']['min counts'] = min_counts
        data['values']['max counts'] = max_counts
        data['values']['std counts'] = std_counts
        data['values']['saturated pixel fraction'] = saturated_fractions
        data['values']['roi sum counts'] = roi_sums

        if dark_frames:
            # The mean is linear, so subtracting the dark's mean from a frame's mean is exact - no
            # need to keep the frames themselves in memory to do it. The corrected peak is not
            # available this way, which is why the dark images are written out as well.
            dark_means = {dark['bracket index']: dark['mean counts'] for dark in dark_frames}
            # Left unclipped at zero on purpose: a frame with no light in it scatters either side of
            # its dark, and flooring that at zero would turn the noise into a positive bias exactly
            # where the response is weakest.
            corrected_means = [mean - dark_means[bracket]
                               for mean, bracket in zip(mean_counts, bracket_indices)]
            data['values']['dark mean counts'] = [dark_means[b] for b in bracket_indices]
            data['values']['dark corrected mean counts'] = corrected_means

            dark_roi_sums = {dark['bracket index']: dark['roi sum'] for dark in dark_frames}
            corrected_roi_sums = [roi_sum - dark_roi_sums[bracket]
                                  for roi_sum, bracket in zip(roi_sums, bracket_indices)]
            data['values']['dark corrected roi sum counts'] = corrected_roi_sums
        else:
            corrected_means = mean_counts
            corrected_roi_sums = roi_sums

        # A count on its own only means something next to the exposure it was integrated over, so
        # these are the series to compare between points captured at different exposures - which is
        # every point, once bracketing or auto exposure is on.
        #
        # The ROI rate is the one to use. Measured on this setup, it holds to 3% across a factor of
        # 16 in exposure where the whole-frame rate moves by 3x, because the frame outside the beam
        # carries a background which does not scale with the exposure and so does not divide out.
        data['values']['roi counts per second'] = [
            1e6 * counts / exposure
            for counts, exposure in zip(corrected_roi_sums, frame_exposures)]
        data['values']['mean counts per second'] = [
            1e6 * counts / exposure for counts, exposure in zip(corrected_means, frame_exposures)]

        # one list per frame, since a frame can now be written in several formats
        data['measurement settings']['image files'] = file_names
        data['measurement settings']['frame timestamps utc'] = timestamps
        # fewer than requested if the ladder ran into the camera's shortest exposure
        data['measurement settings']['exposure brackets captured'] = int(len(bracket_exposures))
        # the wavelengths as they were actually set, which the last step of a sweep can nudge off
        # the requested grid, and one number to say whether this was a sweep at all
        data['measurement settings']['wavelengths nm'] = [float(wl) for wl in wavelengths]
        data['measurement settings']['wavelength count'] = int(len(wavelengths))
        # the region every sum above was taken over, and how many pixels it holds, so a sum can be
        # turned back into counts per pixel without re-deriving it from the four settings
        data['measurement settings']['integration roi'] = [int(v) for v in integration_roi]
        data['measurement settings']['integration roi pixels'] = int(roi_width * roi_height)
        if integration_roi_note:
            data['measurement settings']['integration roi note'] = integration_roi_note
        # says whether 'mean counts per second' had a dark frame subtracted from it or not, which
        # is otherwise only visible by noticing that 'dark frames' is missing
        data['measurement settings']['counts per second dark corrected'] = bool(dark_frames)
        if auto_exposure_result is not None:
            data['measurement settings']['auto exposure result'] = auto_exposure_result
        if dark_frames:
            data['measurement settings']['dark frames'] = dark_frames

        data['measurement settings']['image shape'] = [int(v) for v in image.shape]
        data['measurement settings']['image dtype'] = str(image.dtype)
        data['measurement settings']['image directory'] = directory
        # recorded so a dark-looking frame can be told apart from a dark chip without guessing
        data['measurement settings']['laser on during capture'] = bool(laser_enabled)
        # so analysis can normalise counts and judge headroom without re-deriving them from the
        # pixel format, which is exactly the step that went wrong here
        data['measurement settings']['full scale counts'] = float(full_scale)
        data['measurement settings']['saturation count level'] = float(clip_level)

        self.instr_laser.close()

        if close_camera:
            self.instr_camera.close()

        # sanity check if data contains all necessary keys
        self._check_data(data)

        return data
