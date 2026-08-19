#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
LabExT  Copyright (C) 2021  ETH Zurich and Polariton Technologies AG
This program is free software and comes with ABSOLUTELY NO WARRANTY; for details see LICENSE file.
"""

import json
import logging
import os
from datetime import datetime, timezone
from time import sleep

from typing import TYPE_CHECKING, Dict

import numpy as np

from LabExT.Measurements.MeasAPI import *
from LabExT.Utils import get_configuration_file_path

if TYPE_CHECKING:
    from LabExT.Measurements.MeasAPI.Measparam import MeasParam
else:
    MeasParam = None


class CameraSnapshot(Measurement):
    """
    ## CameraSnapshot

    Captures one or more frames from a camera as part of a regular LabExT measurement run. The
    images are written next to the measurement's own result file, and the data file records per
    frame statistics plus the file names, so an image can always be traced back to the settings it
    was taken with.

    The images themselves are deliberately kept out of the result file: a single frame of this
    camera is over a megapixel, which would make the JSON unusable.

    #### example setup
    ```
    Camera ---USB3--- Computer
    ```

    #### parameters
    * **exposure time**: Exposure time in microseconds. Clamped to the camera's own limits and
      snapped to its increment, so the value written back into the measurement settings may differ
      slightly from what was requested.
    * **gain**: Analog gain in dB, clamped and snapped in the same way.
    * **pixel format**: `Mono8` gives 8 bit data, `Mono10` and `Mono12` give 16 bit data. Deeper
      formats carry more dynamic range but need a container that can hold it, so keep TIFF or NPY
      on when using them.
    * **ROI width / ROI height / ROI offset x / ROI offset y**: Region of interest in pixels. Leave
      width and height at 0 to use the full sensor. A smaller ROI reads out faster.
    * **number of frames**: How many frames to capture in this measurement.
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
      measurement result file.
    * **close camera after measurement**: Leave off unless something else needs the camera. Keeping
      the connection open avoids a full camera re-open every time LabExT collects instrument
      metadata, which it does twice per measurement.
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
        return ['Camera']

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

        directory = os.path.abspath(output_directory) if output_directory else default_directory
        os.makedirs(directory, exist_ok=True)
        return directory, stem

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
        frame_delay = parameters.get('inter-frame delay').value
        frame_timeout = parameters.get('frame timeout').value
        save_tiff = parameters.get('save TIFF').value
        save_png = parameters.get('save PNG').value
        save_npy = parameters.get('save NPY').value
        output_directory = parameters.get('image output directory').value
        close_camera = parameters.get('close camera after measurement').value

        if n_frames < 1:
            raise ValueError("number of frames must be at least 1, got {:d}.".format(n_frames))
        if frame_timeout <= exposure_time / 1000.0:
            raise ValueError(
                "frame timeout ({:.1f} ms) must be longer than the exposure time ({:.1f} us = "
                "{:.1f} ms).".format(frame_timeout, exposure_time, exposure_time / 1000.0))

        # get the instrument
        self.instr_camera = instruments['Camera']

        # open connection to the camera
        self.instr_camera.open()

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
        if getattr(self.instr_camera, 'is_streaming', False):
            self.logger.warning(
                "Camera is streaming, so this measurement captures with the live Camera View's "
                "settings (%s, ROI %s, %.1f us, %.2f dB) rather than its own (%s, ROI %s, "
                "%.1f us, %.2f dB). Stop the Camera View if the configured settings are required.",
                self.instr_camera.pixel_format, self.instr_camera.roi,
                self.instr_camera.exposure_time, self.instr_camera.gain,
                pixel_format, roi, exposure_time, gain)
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

        # write the measurement parameters into the measurement settings
        for pname, pparam in parameters.items():
            data['measurement settings'][pname] = pparam.as_dict()

        directory, stem = self._resolve_output_target(output_directory, data)

        # capture. Frames are collected into local lists and only written into data at the end:
        # data is an AutosaveDict which re-serialises the whole result file every few accesses, so
        # touching it inside the loop would rewrite the file once per frame.
        frame_indices = []
        timestamps = []
        file_names = []
        mean_counts = []
        min_counts = []
        max_counts = []
        std_counts = []
        saturated_fractions = []

        if frame_delay > 0.0:
            images = None  # captured one at a time below, so the delay is actually honoured
        else:
            images = self.instr_camera.snap_photos(n_frames, timeout_ms=int(frame_timeout))

        image = None
        for idx in range(n_frames):
            if images is None:
                if idx > 0:
                    sleep(frame_delay)
                image = self.instr_camera.snap_photo(timeout_ms=int(frame_timeout))
            else:
                image = images[idx]

            full_scale = float(np.iinfo(image.dtype).max)

            # every selected format gets the same frame, so a TIFF and its PNG are the same shot
            frame_files = []
            base_name = "{:s}_frame{:03d}".format(stem, idx)

            if save_tiff:
                frame_files.append(os.path.basename(self.instr_camera.save_photo(
                    os.path.join(directory, base_name + '.tif'), image=image)))
            if save_png:
                # stretched to fill 8 bits, so it is viewable whatever the pixel format. The TIFF
                # or NPY alongside it carries the counts; a raw Mono12 PNG would be nearly black.
                frame_files.append(os.path.basename(self.instr_camera.save_photo(
                    os.path.join(directory, base_name + '.png'),
                    image=self.instr_camera.stretch_to_8bit(image))))
            if save_npy:
                frame_files.append(os.path.basename(self.instr_camera.save_photo_raw(
                    os.path.join(directory, base_name + '.npy'), image=image)))

            frame_indices.append(int(idx))
            timestamps.append(datetime.now(timezone.utc).isoformat())
            file_names.append(frame_files)
            # convert numpy float64 to python float, otherwise the result file is not serialisable
            mean_counts.append(float(np.mean(image)))
            min_counts.append(float(np.min(image)))
            max_counts.append(float(np.max(image)))
            std_counts.append(float(np.std(image)))
            saturated_fractions.append(float(np.count_nonzero(image >= full_scale) / image.size))

        # data['values'] must hold numeric series only: LabExT plots every one of them, and
        # PlotControl runs np.isfinite over the y data, which raises on strings. The file names and
        # timestamps are metadata about the capture rather than measured values, so they belong
        # alongside the other settings.
        data['values']['frame index'] = frame_indices
        data['values']['mean counts'] = mean_counts
        data['values']['min counts'] = min_counts
        data['values']['max counts'] = max_counts
        data['values']['std counts'] = std_counts
        data['values']['saturated pixel fraction'] = saturated_fractions

        # one list per frame, since a frame can now be written in several formats
        data['measurement settings']['image files'] = file_names
        data['measurement settings']['frame timestamps utc'] = timestamps

        data['measurement settings']['image shape'] = [int(v) for v in image.shape]
        data['measurement settings']['image dtype'] = str(image.dtype)
        data['measurement settings']['image directory'] = directory

        if close_camera:
            self.instr_camera.close()

        # sanity check if data contains all necessary keys
        self._check_data(data)

        return data
