#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
LabExT  Copyright (C) 2021  ETH Zurich and Polariton Technologies AG
This program is free software and comes with ABSOLUTELY NO WARRANTY; for details see LICENSE file.
"""

import json
import logging
import os
import re
import threading
import time
from tkinter import (BOTH, BOTTOM, DISABLED, HORIZONTAL, LEFT, NORMAL, RIGHT, TOP, X, Y,
                     BooleanVar, Button, Canvas, Checkbutton, Entry, Frame, Label, OptionMenu,
                     Scale, StringVar, Toplevel, filedialog, messagebox)

import numpy as np
from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg
from matplotlib.figure import Figure
from PIL import Image, ImageTk

from LabExT.Measurements.CameraSnapshot import CameraSnapshot
from LabExT.Utils import get_configuration_file_path, get_visa_address
from LabExT.View.Controls.CustomFrame import CustomFrame
from LabExT.View.Controls.InstrumentSelector import InstrumentRole, InstrumentSelector


class CameraViewWindow(Toplevel):
    """Live camera preview with exposure, gain, pixel format and ROI controls.

    The camera streams continuously; frames arrive on a vmbpy callback thread which only stores the
    most recent one, and a periodic Tk callback renders whatever is current. Frames that arrive
    faster than the GUI can draw are dropped on purpose: the camera reaches 40-70 fps and a stale
    preview frame is worth nothing.

    Closing the window stops the stream but leaves the camera connected, so reopening is instant and
    LabExT's instrument metadata collection does not have to re-open the camera.
    """

    #: role string in instruments.config
    INSTRUMENT_TYPE = 'Camera'
    #: file in ~/.labext holding this window's settings
    SETTINGS_FILE_NAME = 'CameraViewWindow_settings.json'
    #: how often the GUI picks up the newest frame
    TICK_INTERVAL_MS = 50
    #: the histogram is redrawn every Nth tick; it is not the hot path
    HISTOGRAM_TICK_DIVIDER = 5
    #: number of frame buffers announced to the transport layer
    STREAM_BUFFER_COUNT = 10

    def __init__(self, parent, experiment_manager):
        super().__init__(parent)

        self.logger = logging.getLogger()
        # deliberately not called _root: Misc._root() is a tkinter method, and shadowing it breaks
        # every StringVar/BooleanVar created with this window as master
        self._parent = parent
        self._experiment_manager = experiment_manager

        self.camera = None

        # frame hand-off between the vmbpy callback thread and the GUI thread. Only ever holds the
        # newest frame; the lock is this window's own, never the instrument's, because the callback
        # must not contend with a stop_streaming() that is waiting for the stream to end.
        self._frame_lock = threading.Lock()
        self._incoming_frame = None
        self._incoming_format = None
        self._displayed_frame = None
        self._incomplete_frames = 0

        # frame rate measurement
        self._frames_since_tick = 0
        self._fps = 0.0
        self._fps_window_start = time.time()
        self._tick_count = 0
        self._tick_job = None

        # keeping a reference is mandatory: Tk does not own the image and it would otherwise be
        # garbage collected, leaving the canvas blank
        self._photo_image = None
        self._canvas_image_id = None

        # slider values are applied once per tick rather than per event, so dragging a slider does
        # not flood the camera with feature writes
        self._pending_exposure = None
        self._pending_gain = None

        self._display_bit_depth = 8

        self.title("Camera View")
        self.geometry('+%d+%d' % (self.winfo_screenwidth() / 8, self.winfo_screenheight() / 8))
        self.protocol("WM_DELETE_WINDOW", self.destroy)

        self._build_widgets()
        self._load_settings()
        self._update_button_states()
        self.lift()

        self._tick_job = self.after(self.TICK_INTERVAL_MS, self._tick)

    #
    # widget construction
    #

    def _build_widgets(self):
        main = Frame(self)
        main.pack(side=TOP, fill=BOTH, expand=True, padx=5, pady=5)

        # ---- left: preview + status -------------------------------------------------
        preview_frame = CustomFrame(main)
        preview_frame.title = " Preview "
        preview_frame.pack(side=LEFT, fill=BOTH, expand=True, padx=5, pady=5)

        self._preview_canvas = Canvas(preview_frame, width=640, height=512,
                                      background='#202020', highlightthickness=0)
        self._preview_canvas.pack(side=TOP, fill=BOTH, expand=True)

        self._status_var = StringVar(self, value="Not connected.")
        Label(preview_frame, textvariable=self._status_var, anchor='w').pack(
            side=BOTTOM, fill=X, pady=(4, 0))

        # ---- right: controls --------------------------------------------------------
        controls = Frame(main)
        controls.pack(side=RIGHT, fill=Y, padx=5, pady=5)

        self._build_instrument_controls(controls)
        self._build_exposure_gain_controls(controls)
        self._build_format_roi_controls(controls)
        self._build_display_controls(controls)
        self._build_save_controls(controls)
        self._build_histogram(controls)

    def _build_instrument_controls(self, parent):
        self.available_instruments = {}
        try:
            descriptors = get_visa_address(self.INSTRUMENT_TYPE)
        except RuntimeError:
            # no Camera role in instruments.config at all
            descriptors = []
            self.logger.error("No '%s' section in instruments.config.", self.INSTRUMENT_TYPE)
        self.available_instruments[self.INSTRUMENT_TYPE] = InstrumentRole(self._parent, descriptors)

        self.instrument_selector = InstrumentSelector(parent)
        self.instrument_selector.title = " Camera "
        self.instrument_selector.instrument_source = self.available_instruments
        self.instrument_selector.pack(side=TOP, fill=X, pady=2)

        button_frame = Frame(parent)
        button_frame.pack(side=TOP, fill=X, pady=2)
        self._connect_button = Button(button_frame, text="Connect", command=self._on_connect)
        self._connect_button.pack(side=LEFT, expand=True, fill=X, padx=1)
        self._start_button = Button(button_frame, text="Start", command=self._on_start)
        self._start_button.pack(side=LEFT, expand=True, fill=X, padx=1)
        self._stop_button = Button(button_frame, text="Stop", command=self._on_stop)
        self._stop_button.pack(side=LEFT, expand=True, fill=X, padx=1)

    def _build_exposure_gain_controls(self, parent):
        frame = CustomFrame(parent)
        frame.title = " Exposure and Gain "
        frame.pack(side=TOP, fill=X, pady=2)

        # Exposure spans 20 us to 10 s on this camera, so a linear slider would be unusable: the
        # entire useful range would sit in the first pixel. The slider is in log10(us).
        Label(frame, text="exposure [us]").grid(row=0, column=0, sticky='w')
        self._exposure_value_var = StringVar(self, value="-")
        Label(frame, textvariable=self._exposure_value_var, width=12, anchor='e').grid(
            row=0, column=1, sticky='e')
        self._exposure_scale = Scale(frame, from_=0.0, to=1.0, resolution=0.001,
                                     orient=HORIZONTAL, showvalue=False, length=220,
                                     command=self._on_exposure_slider)
        self._exposure_scale.grid(row=1, column=0, columnspan=2, sticky='we')

        Label(frame, text="gain [dB]").grid(row=2, column=0, sticky='w')
        self._gain_value_var = StringVar(self, value="-")
        Label(frame, textvariable=self._gain_value_var, width=12, anchor='e').grid(
            row=2, column=1, sticky='e')
        self._gain_scale = Scale(frame, from_=0.0, to=1.0, resolution=0.01,
                                 orient=HORIZONTAL, showvalue=False, length=220,
                                 command=self._on_gain_slider)
        self._gain_scale.grid(row=3, column=0, columnspan=2, sticky='we')
        frame.columnconfigure(0, weight=1)

    def _build_format_roi_controls(self, parent):
        frame = CustomFrame(parent)
        frame.title = " Pixel Format and ROI "
        frame.pack(side=TOP, fill=X, pady=2)

        Label(frame, text="pixel format").grid(row=0, column=0, sticky='w')
        self._pixel_format_var = StringVar(self, value='Mono8')
        self._pixel_format_menu = OptionMenu(frame, self._pixel_format_var, 'Mono8')
        self._pixel_format_menu.grid(row=0, column=1, columnspan=3, sticky='we')

        self._roi_vars = {}
        for idx, (key, label) in enumerate((('width', 'width'), ('height', 'height'),
                                            ('offset_x', 'offset x'), ('offset_y', 'offset y'))):
            row, col = 1 + idx // 2, (idx % 2) * 2
            Label(frame, text=label).grid(row=row, column=col, sticky='w')
            var = StringVar(self, value='0')
            Entry(frame, textvariable=var, width=7).grid(row=row, column=col + 1, sticky='we')
            self._roi_vars[key] = var

        Label(frame, text="0 = full sensor").grid(row=3, column=0, columnspan=2, sticky='w')
        self._apply_button = Button(frame, text="Apply", command=self._on_apply_format_roi)
        self._apply_button.grid(row=3, column=2, columnspan=2, sticky='we')

    def _build_display_controls(self, parent):
        frame = CustomFrame(parent)
        frame.title = " Display "
        frame.pack(side=TOP, fill=X, pady=2)

        self._stretch_var = BooleanVar(self, value=True)
        Checkbutton(frame, text="percentile stretch", variable=self._stretch_var).grid(
            row=0, column=0, columnspan=4, sticky='w')

        Label(frame, text="low %").grid(row=1, column=0, sticky='w')
        self._stretch_low_var = StringVar(self, value='1.0')
        Entry(frame, textvariable=self._stretch_low_var, width=6).grid(row=1, column=1, sticky='we')
        Label(frame, text="high %").grid(row=1, column=2, sticky='w')
        self._stretch_high_var = StringVar(self, value='99.0')
        Entry(frame, textvariable=self._stretch_high_var, width=6).grid(row=1, column=3, sticky='we')

        self._handover_button = Button(frame, text="Use for CameraSnapshot",
                                       command=self._on_use_for_measurement)
        self._handover_button.grid(row=2, column=0, columnspan=4, sticky='we', pady=(4, 0))

    def _build_save_controls(self, parent):
        frame = CustomFrame(parent)
        frame.title = " Save Images "
        frame.pack(side=TOP, fill=X, pady=2)

        Label(frame, text="folder").grid(row=0, column=0, sticky='w')
        self._save_directory_var = StringVar(self, value=os.getcwd())
        Entry(frame, textvariable=self._save_directory_var, width=22).grid(
            row=0, column=1, columnspan=2, sticky='we')
        Button(frame, text="...", width=3, command=self._on_browse_save_directory).grid(
            row=0, column=3, sticky='we')

        Label(frame, text="prefix").grid(row=1, column=0, sticky='w')
        self._filename_prefix_var = StringVar(self, value='camera')
        Entry(frame, textvariable=self._filename_prefix_var, width=22).grid(
            row=1, column=1, columnspan=3, sticky='we')

        # PNG carries the image as displayed, stretch included, so it is only ever a picture of the
        # data. TIFF carries the counts the camera actually reported.
        self._save_png_var = BooleanVar(self, value=True)
        Checkbutton(frame, text="PNG (as displayed)", variable=self._save_png_var).grid(
            row=2, column=0, columnspan=4, sticky='w')
        self._save_tiff_var = BooleanVar(self, value=True)
        Checkbutton(frame, text="TIFF (raw counts)", variable=self._save_tiff_var).grid(
            row=3, column=0, columnspan=4, sticky='w')

        self._save_button = Button(frame, text="Snap and Save", command=self._on_snap_and_save)
        self._save_button.grid(row=4, column=0, columnspan=4, sticky='we', pady=(4, 0))

        frame.columnconfigure(1, weight=1)

    def _build_histogram(self, parent):
        frame = CustomFrame(parent)
        frame.title = " Histogram "
        frame.pack(side=TOP, fill=X, pady=2)

        self._histogram_figure = Figure(figsize=(3.0, 1.6), dpi=80)
        self._histogram_axes = self._histogram_figure.add_subplot(111)
        self._histogram_axes.set_yscale('log')
        self._histogram_axes.tick_params(labelsize=6)
        self._histogram_figure.tight_layout()
        self._histogram_line = None
        self._histogram_canvas = FigureCanvasTkAgg(self._histogram_figure, master=frame)
        self._histogram_canvas.get_tk_widget().pack(side=TOP, fill=X)

    #
    # connection and streaming
    #

    @property
    def _connected(self):
        return self.camera is not None and self.camera._open

    @property
    def _streaming(self):
        try:
            return self._connected and self.camera.is_streaming
        except Exception:
            return False

    def _update_button_states(self):
        connected, streaming = self._connected, self._streaming
        self._connect_button.config(state=DISABLED if connected else NORMAL)
        self._start_button.config(state=NORMAL if connected and not streaming else DISABLED)
        self._stop_button.config(state=NORMAL if streaming else DISABLED)
        self._apply_button.config(state=NORMAL if connected else DISABLED)
        self._handover_button.config(state=NORMAL if connected else DISABLED)
        self._save_button.config(
            state=NORMAL if self._displayed_frame is not None else DISABLED)
        self.instrument_selector.enabled = not connected

    def _on_connect(self):
        try:
            selected = {role: instr.choice
                        for role, instr in self.available_instruments.items()}
            camera = self._experiment_manager.instrument_api.create_instrument_obj(
                self.INSTRUMENT_TYPE, selected, {})
            if camera is None:
                # create_instrument_obj swallows constructor errors and returns None
                raise RuntimeError(
                    "Could not instantiate the selected camera. See the log for details.")
            camera.open()
        except Exception as exc:
            self.logger.exception("Could not connect to camera.")
            messagebox.showerror("Camera error", "Could not connect to the camera:\n\n"
                                 + str(exc), parent=self)
            return

        self.camera = camera
        self._sync_controls_from_camera()
        self._status_var.set("Connected to " + str(self.camera.idn()))
        self._update_button_states()

    def _sync_controls_from_camera(self):
        """Set slider ranges and control values from what the camera actually reports."""
        exposure_min, exposure_max = self.camera.exposure_time_range
        self._exposure_scale.config(from_=np.log10(exposure_min), to=np.log10(exposure_max))
        self._exposure_scale.set(np.log10(self.camera.exposure_time))
        self._exposure_value_var.set("{:.1f}".format(self.camera.exposure_time))

        gain_min, gain_max = self.camera.gain_range
        self._gain_scale.config(from_=gain_min, to=gain_max)
        self._gain_scale.set(self.camera.gain)
        self._gain_value_var.set("{:.2f}".format(self.camera.gain))

        # rebuild the pixel format menu from the camera's own list
        formats = self.camera.available_pixel_formats
        menu = self._pixel_format_menu['menu']
        menu.delete(0, 'end')
        for name in formats:
            menu.add_command(label=name,
                             command=lambda value=name: self._pixel_format_var.set(value))
        current_format = self.camera.pixel_format
        self._pixel_format_var.set(current_format)
        self._display_bit_depth = self._bit_depth_of(current_format)

        width, height, offset_x, offset_y = self.camera.roi
        for key, value in (('width', width), ('height', height),
                           ('offset_x', offset_x), ('offset_y', offset_y)):
            self._roi_vars[key].set(str(value))

        # the pending values would otherwise immediately overwrite what we just read
        self._pending_exposure = None
        self._pending_gain = None

    @staticmethod
    def _bit_depth_of(pixel_format_name):
        """Bits per pixel carried by a `MonoNN` / `MonoNNp` format name, defaulting to 8.

        This cannot be taken from the numpy dtype: Mono12 data arrives in a uint16 array but only
        occupies 12 bits, so treating it as full-scale uint16 would render an almost black preview.
        """
        match = re.search(r'(\d+)', str(pixel_format_name))
        return int(match.group(1)) if match else 8

    def _on_start(self):
        try:
            self.camera.start_streaming(handler=self._frame_handler,
                                        buffer_count=self.STREAM_BUFFER_COUNT)
        except Exception as exc:
            self.logger.exception("Could not start camera stream.")
            messagebox.showerror("Camera error", "Could not start streaming:\n\n" + str(exc),
                                 parent=self)
            return
        self._fps_window_start = time.time()
        self._frames_since_tick = 0
        self._update_button_states()

    def _on_stop(self):
        try:
            self.camera.stop_streaming()
        except Exception:
            self.logger.exception("Error while stopping camera stream.")
        self._fps = 0.0
        self._update_button_states()

    def _frame_handler(self, camera, stream, frame):
        """Runs on a vmbpy thread. Keeps only the newest frame and hands the buffer straight back.

        Must not touch Tk, and must not take the instrument's thread_lock: stop_streaming() holds
        that lock while waiting for the stream to end.
        """
        try:
            # incomplete frames carry no usable pixel format and must never reach the converter
            if not self.camera.frame_is_complete(frame):
                with self._frame_lock:
                    self._incomplete_frames += 1
                return

            # take the format off the frame, not off the camera: it is free, and it keeps the
            # display scaling correct even if the camera is reconfigured from elsewhere
            source_format = self.camera.frame_pixel_format(frame)
            image = self.camera.frame_to_array(frame)
            with self._frame_lock:
                self._incoming_frame = image
                self._incoming_format = source_format
                self._frames_since_tick += 1
        except Exception:
            # at video rate this must not spam a traceback per frame
            with self._frame_lock:
                self._incomplete_frames += 1
            self.logger.debug("Error converting a streamed camera frame.", exc_info=True)
        finally:
            camera.queue_frame(frame)

    #
    # periodic GUI update
    #

    def _tick(self):
        self._tick_job = None
        try:
            self._apply_pending_slider_values()

            with self._frame_lock:
                frame = self._incoming_frame
                frame_format = self._incoming_format
                self._incoming_frame = None
                new_frames = self._frames_since_tick
                self._frames_since_tick = 0

            if frame_format is not None:
                self._display_bit_depth = self._bit_depth_of(frame_format)

            self._update_frame_rate(new_frames)

            if frame is not None:
                self._displayed_frame = frame
                self._render_frame(frame)
                self._update_status(frame)
                if self._tick_count % self.HISTOGRAM_TICK_DIVIDER == 0:
                    self._update_histogram(frame)
                if str(self._save_button['state']) == DISABLED:
                    self._save_button.config(state=NORMAL)

            self._tick_count += 1
        except Exception:
            self.logger.exception("Error in camera view update.")
        finally:
            # always reschedule, so one bad frame does not silently kill the preview
            self._tick_job = self.after(self.TICK_INTERVAL_MS, self._tick)

    def _update_frame_rate(self, new_frames):
        self._fps_frames = getattr(self, '_fps_frames', 0) + new_frames
        elapsed = time.time() - self._fps_window_start
        if elapsed >= 0.5:
            self._fps = self._fps_frames / elapsed
            self._fps_frames = 0
            self._fps_window_start = time.time()

    def _apply_pending_slider_values(self):
        if not self._connected:
            return
        if self._pending_exposure is not None:
            wanted, self._pending_exposure = self._pending_exposure, None
            try:
                self.camera.exposure_time = wanted
                self._exposure_value_var.set("{:.1f}".format(self.camera.exposure_time))
            except Exception:
                self.logger.exception("Could not set exposure time.")
        if self._pending_gain is not None:
            wanted, self._pending_gain = self._pending_gain, None
            try:
                self.camera.gain = wanted
                self._gain_value_var.set("{:.2f}".format(self.camera.gain))
            except Exception:
                self.logger.exception("Could not set gain.")

    def _on_exposure_slider(self, value):
        # the slider is in log10(microseconds), see _build_exposure_gain_controls
        self._pending_exposure = float(10.0 ** float(value))

    def _on_gain_slider(self, value):
        self._pending_gain = float(value)

    #
    # rendering
    #

    def _to_display_image(self, frame):
        """Scale a raw frame to 8 bit for display. Never used for anything that gets saved."""
        full_scale = float(2 ** self._display_bit_depth - 1)

        if self._stretch_var.get():
            try:
                low_percentile = float(self._stretch_low_var.get())
                high_percentile = float(self._stretch_high_var.get())
            except ValueError:
                low_percentile, high_percentile = 1.0, 99.0
            low, high = np.percentile(frame, [low_percentile, high_percentile])
            if high <= low:
                high = low + 1.0
            scaled = (frame.astype(np.float32) - low) * (255.0 / (high - low))
            return np.clip(scaled, 0.0, 255.0).astype(np.uint8)

        if frame.dtype == np.uint8:
            return frame
        return np.clip(frame.astype(np.float32) * (255.0 / full_scale), 0.0, 255.0).astype(np.uint8)

    def _render_frame(self, frame):
        canvas_width = max(self._preview_canvas.winfo_width(), 1)
        canvas_height = max(self._preview_canvas.winfo_height(), 1)
        if canvas_width < 10 or canvas_height < 10:
            return  # window not laid out yet

        image = Image.fromarray(self._to_display_image(frame))

        # fit inside the canvas, preserving aspect ratio
        scale = min(canvas_width / image.width, canvas_height / image.height)
        target = (max(int(image.width * scale), 1), max(int(image.height * scale), 1))
        if target != (image.width, image.height):
            image = image.resize(target, Image.NEAREST)

        # the reference must survive this function or Tk shows an empty canvas
        self._photo_image = ImageTk.PhotoImage(image, master=self._preview_canvas)
        if self._canvas_image_id is None:
            self._canvas_image_id = self._preview_canvas.create_image(
                canvas_width // 2, canvas_height // 2, image=self._photo_image)
        else:
            self._preview_canvas.coords(
                self._canvas_image_id, canvas_width // 2, canvas_height // 2)
            self._preview_canvas.itemconfig(self._canvas_image_id, image=self._photo_image)

    def _update_status(self, frame):
        status = "{:.1f} fps   {:d}x{:d} {:s}   min {:.0f} / mean {:.1f} / max {:.0f}".format(
            self._fps, frame.shape[1], frame.shape[0], str(frame.dtype),
            float(frame.min()), float(frame.mean()), float(frame.max()))
        if self._incomplete_frames:
            # a steadily climbing count means the link is dropping data
            status += "   {:d} incomplete".format(self._incomplete_frames)
        self._status_var.set(status)

    def _update_histogram(self, frame):
        full_scale = 2 ** self._display_bit_depth - 1
        counts, edges = np.histogram(frame, bins=128, range=(0, full_scale))
        centres = (edges[:-1] + edges[1:]) / 2.0
        counts = np.maximum(counts, 1)  # log scale cannot show zero

        if self._histogram_line is None:
            self._histogram_line, = self._histogram_axes.plot(centres, counts, linewidth=0.8)
            self._histogram_axes.set_xlim(0, full_scale)
        else:
            self._histogram_line.set_data(centres, counts)
            self._histogram_axes.set_xlim(0, full_scale)
        self._histogram_axes.set_ylim(1, max(counts.max() * 1.5, 10))
        self._histogram_canvas.draw_idle()

    #
    # actions
    #

    def _on_apply_format_roi(self):
        """Apply pixel format and ROI, restarting the stream around them if one is running."""
        was_streaming = self._streaming
        try:
            if was_streaming:
                self.camera.stop_streaming()

            self.camera.pixel_format = self._pixel_format_var.get()
            self.camera.set_roi(*[int(float(self._roi_vars[key].get()))
                                  for key in ('width', 'height', 'offset_x', 'offset_y')])
        except Exception as exc:
            self.logger.exception("Could not apply pixel format / ROI.")
            messagebox.showerror("Camera error", "Could not apply these settings:\n\n" + str(exc),
                                 parent=self)
        finally:
            # read back whatever the camera settled on, including increment snapping
            try:
                self._sync_controls_from_camera()
                self._reset_preview()
            except Exception:
                self.logger.exception("Could not read camera settings back.")
            if was_streaming:
                try:
                    self.camera.start_streaming(handler=self._frame_handler,
                                                buffer_count=self.STREAM_BUFFER_COUNT)
                except Exception:
                    self.logger.exception("Could not restart camera stream.")
            self._update_button_states()

    def _reset_preview(self):
        """Drop the current image so a changed frame size is not drawn into a stale canvas item."""
        with self._frame_lock:
            self._incoming_frame = None
            self._incoming_format = None
        if self._canvas_image_id is not None:
            self._preview_canvas.delete(self._canvas_image_id)
            self._canvas_image_id = None
        self._photo_image = None

    def _on_browse_save_directory(self):
        directory = filedialog.askdirectory(
            parent=self, title="Folder for saved camera images",
            initialdir=self._save_directory_var.get() or os.getcwd())
        if directory:
            self._save_directory_var.set(os.path.normpath(directory))

    def _next_free_basename(self, directory, prefix):
        """`<prefix>_000`, `<prefix>_001`, ... skipping any index already taken by either format.

        Both formats of one snap share an index, so `foo_007.png` and `foo_007.tif` are always the
        same shot.
        """
        index = 0
        while True:
            basename = "{:s}_{:03d}".format(prefix, index)
            if not any(os.path.exists(os.path.join(directory, basename + extension))
                       for extension in ('.png', '.tif')):
                return basename
            index += 1

    def _on_snap_and_save(self):
        """Save the frame currently on screen. Does not interrupt the stream.

        PNG holds the image exactly as displayed, percentile stretch included, so it is a picture
        of the data rather than the data itself. TIFF holds the raw counts the camera reported, at
        the full bit depth of the active pixel format.
        """
        frame = self._displayed_frame
        if frame is None:
            messagebox.showinfo("No image", "No frame has been captured yet.", parent=self)
            return

        save_png = bool(self._save_png_var.get())
        save_tiff = bool(self._save_tiff_var.get())
        if not (save_png or save_tiff):
            messagebox.showinfo("Nothing to save",
                                "Tick PNG, TIFF or both before saving.", parent=self)
            return

        directory = self._save_directory_var.get().strip() or os.getcwd()
        prefix = self._filename_prefix_var.get().strip() or 'camera'
        # keep the prefix usable as a file name
        prefix = re.sub(r'[<>:"/\\|?*]', '_', prefix)

        try:
            os.makedirs(directory, exist_ok=True)
            basename = self._next_free_basename(directory, prefix)

            written = []
            if save_tiff:
                written.append(self.camera.save_photo(
                    os.path.join(directory, basename + '.tif'), image=frame))
            if save_png:
                # the displayed 8 bit image, not the raw frame
                written.append(self.camera.save_photo(
                    os.path.join(directory, basename + '.png'),
                    image=self._to_display_image(frame)))
        except Exception as exc:
            self.logger.exception("Could not save camera image.")
            messagebox.showerror("Save failed", str(exc), parent=self)
            return

        self.logger.info("Saved camera images: %s", written)
        self._status_var.set("Saved " + basename + " (" + ", ".join(
            os.path.splitext(path)[1].lstrip('.') for path in written) + ") to " + directory)

    def _on_use_for_measurement(self):
        """Hand the camera's current settings to the CameraSnapshot measurement.

        The values are read back off the camera rather than off the sliders, so what the
        measurement gets is what the camera actually settled on after increment snapping, exactly
        the values that produced the image on screen.

        The measurement's own settings file is merged rather than rewritten: everything the camera
        view does not control (number of frames, output directory, file format, ...) has to survive.
        """
        if not self._connected:
            messagebox.showinfo("Not connected",
                                "Connect to the camera first, so its actual settings can be read.",
                                parent=self)
            return

        try:
            width, height, offset_x, offset_y = self.camera.roi
            camera_settings = {
                'exposure time': float(self.camera.exposure_time),
                'gain': float(self.camera.gain),
                'pixel format': str(self.camera.pixel_format),
                'ROI width': int(width),
                'ROI height': int(height),
                'ROI offset x': int(offset_x),
                'ROI offset y': int(offset_y),
            }
        except Exception as exc:
            self.logger.exception("Could not read camera settings for hand-over.")
            messagebox.showerror("Camera error",
                                 "Could not read the camera settings:\n\n" + str(exc), parent=self)
            return

        # only hand over settings the measurement actually has a parameter for
        unknown = set(camera_settings) - set(CameraSnapshot.CAMERA_SETTING_TYPES)
        for name in unknown:
            del camera_settings[name]

        measurement_settings_path = get_configuration_file_path(CameraSnapshot.SETTINGS_FILE_NAME)
        try:
            stored = {}
            if os.path.isfile(measurement_settings_path):
                with open(measurement_settings_path, 'r') as settings_file:
                    stored = json.load(settings_file)
            if not isinstance(stored.get('data'), dict):
                stored['data'] = {}
            stored['data'].update(camera_settings)
            with open(measurement_settings_path, 'w') as settings_file:
                json.dump(stored, settings_file, indent=4)
        except Exception as exc:
            self.logger.exception("Could not write CameraSnapshot settings.")
            messagebox.showerror("Save failed",
                                 "Could not write the measurement settings:\n\n" + str(exc),
                                 parent=self)
            return

        self.logger.info("Handed camera settings to CameraSnapshot: %s", camera_settings)
        self._status_var.set(
            "CameraSnapshot will start from: {:.1f} us, {:.2f} dB, {:s}, {:d}x{:d}+{:d}+{:d}".format(
                camera_settings['exposure time'], camera_settings['gain'],
                camera_settings['pixel format'], camera_settings['ROI width'],
                camera_settings['ROI height'], camera_settings['ROI offset x'],
                camera_settings['ROI offset y']))

    #
    # settings persistence
    #

    def _settings_path(self):
        return get_configuration_file_path(self.SETTINGS_FILE_NAME)

    def _save_settings(self):
        settings = {'instrument': {}}
        try:
            self.instrument_selector.serialize_to_dict(settings['instrument'])
            settings.update({
                'pixel format': self._pixel_format_var.get(),
                'roi': {key: var.get() for key, var in self._roi_vars.items()},
                'stretch': bool(self._stretch_var.get()),
                'stretch low': self._stretch_low_var.get(),
                'stretch high': self._stretch_high_var.get(),
                'save directory': self._save_directory_var.get(),
                'filename prefix': self._filename_prefix_var.get(),
                'save png': bool(self._save_png_var.get()),
                'save tiff': bool(self._save_tiff_var.get()),
            })
            if self._connected:
                settings['exposure time'] = float(self.camera.exposure_time)
                settings['gain'] = float(self.camera.gain)
            with open(self._settings_path(), 'w') as settings_file:
                json.dump(settings, settings_file, indent=4)
        except Exception:
            # never let a settings problem interfere with closing the window
            self.logger.exception("Could not save camera view settings.")

    def _load_settings(self):
        # a corrupt or outdated settings file must never stop the window from opening
        try:
            with open(self._settings_path(), 'r') as settings_file:
                settings = json.load(settings_file)
        except FileNotFoundError:
            return
        except Exception:
            self.logger.exception("Could not read camera view settings.")
            return

        try:
            self.instrument_selector.deserialize_from_dict(settings.get('instrument', {}))
            self._pixel_format_var.set(settings.get('pixel format', 'Mono8'))
            for key, value in settings.get('roi', {}).items():
                if key in self._roi_vars:
                    self._roi_vars[key].set(str(value))
            self._stretch_var.set(bool(settings.get('stretch', True)))
            self._stretch_low_var.set(str(settings.get('stretch low', '1.0')))
            self._stretch_high_var.set(str(settings.get('stretch high', '99.0')))
            self._save_directory_var.set(str(settings.get('save directory', os.getcwd())))
            self._filename_prefix_var.set(str(settings.get('filename prefix', 'camera')))
            self._save_png_var.set(bool(settings.get('save png', True)))
            self._save_tiff_var.set(bool(settings.get('save tiff', True)))
        except (KeyError, IndexError, ValueError, TypeError, json.JSONDecodeError):
            self.logger.exception("Camera view settings could not be applied, using defaults.")

    #
    # teardown
    #

    def destroy(self):
        """Stop streaming and save settings, then tear the window down.

        Overriding destroy rather than only binding WM_DELETE_WINDOW means this also runs when a
        parent window destroys us. The camera connection is deliberately left open so that
        reopening the window is instant and instrument metadata collection stays fast.
        """
        if self._tick_job is not None:
            try:
                self.after_cancel(self._tick_job)
            except Exception:
                pass
            self._tick_job = None

        try:
            if self._streaming:
                self.camera.stop_streaming()
        except Exception:
            self.logger.exception("Error stopping camera stream while closing the camera view.")

        self._save_settings()
        self._photo_image = None

        Toplevel.destroy(self)
