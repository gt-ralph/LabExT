#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
LabExT  Copyright (C) 2021  ETH Zurich and Polariton Technologies AG
This program is free software and comes with ABSOLUTELY NO WARRANTY; for details see LICENSE file.
"""

import logging
import threading
import time
from time import sleep
from tkinter import Button

from LabExT.Measurements.MeasAPI import MeasParamFloat, MeasParamList, MeasParamString
from LabExT.View.LiveViewer.Cards.CardFrame import CardFrame, show_errors_as_popup
from LabExT.Utils import (AUTO_POWER_METER_RANGE, POWER_METER_RANGE_OPTIONS,
                          power_meter_range_setting)
from LabExT.View.LiveViewer.LiveViewerModel import PlotDataPoint


class PowerMeterCard(CardFrame):
    """
    Class to represent power meters. Contains functionality to connect, close and update the PM's parameters.
    """

    # these are the default powermeter parameters
    default_parameters = {
        # Range of the power meter, as a dropdown rather than a free number: a meter has a
        # handful of discrete ranges and snaps whatever it is given onto them, so an arbitrary
        # value only looks more precise than it is. 'auto' lets the meter choose, which is the
        # right answer whenever the signal moves - and RANGE_READBACK below records what it
        # actually settled on. The ladder is in dBm because that is what `range` means on every
        # LabExT power meter driver.
        'powermeter range': MeasParamList(
            options=POWER_METER_RANGE_OPTIONS, value=AUTO_POWER_METER_RANGE, unit='dBm'),
        # integration time of power meter
        'powermeter averagetime': MeasParamFloat(value=0.1, unit='s'),
        # center wavelength of observed signal
        'powermeter wavelength': MeasParamFloat(value=1550.0, unit='nm'),
        # which channels to interrogate
        'channels to plot (comma separated)': MeasParamString(value='1')
    }

    INSTRUMENT_TYPE = 'Power Meter'
    CARD_TITLE = 'Poll OPM'

    def __init__(self, parent, controller, model):
        """Constructor.

        Parameters
        ----------
        parent : Tk
            Tkinter parent frame
        controller :
            The Live viewer controller
        model :
            The Live viewer model
        """

        # create basics GUI elements from super class
        super().__init__(parent, controller, model)
        content_frame = self.content_frame  # created in constructor of super class
        content_frame.columnconfigure(0, minsize=120)
        content_frame.columnconfigure(1, minsize=120)
        content_frame.columnconfigure(2, minsize=120)
        content_frame.columnconfigure(3, weight=4)

        # thread-control related
        self.stop_thread = False
        self.thread_finished = True

        # row 0: control buttons
        self.enable_button = Button(content_frame,
                                    text="Start PM",
                                    command=lambda: self.start_pm())
        self.enable_button.grid(row=0, column=0, padx=2, pady=2, sticky='NESW')
        self.disable_button = Button(content_frame,
                                     text="Stop PM",
                                     command=lambda: self.stop_pm())
        self.disable_button.grid(row=0, column=1, padx=2, pady=2, sticky='NESW')
        self.update_button = Button(content_frame,
                                    text="Update Settings",
                                    command=lambda: self.update_pm())
        self.update_button.grid(row=0, column=2, padx=2, pady=2, sticky='NESW')

        # register which buttons to enable / disable on state change
        self.buttons_active_when_settings_enabled.append(self.enable_button)
        self.buttons_inactive_when_settings_enabled.append(self.disable_button)
        self.buttons_inactive_when_settings_enabled.append(self.update_button)

        # internal state keeping: which channels are enabled?
        self.enabled_channels = {}

    @show_errors_as_popup()
    def start_pm(self):
        """
        Sets up the pm and starts it.
        """
        if self.card_active.get():
            raise RuntimeError('Cannot start pm when already started.')

        for _, trace in self.enabled_channels.items():
            self.data_to_plot_queue.put(PlotDataPoint(trace_name=trace, delete_trace=True))

        loaded_instr = self.load_instrument_instance()
        loaded_instr.logger = logging.getLogger('nogui')
        loaded_instr.open()
        self.instrument = loaded_instr

        # ToDo - nice to have would be saving existing instrument parameters and restoring them afterwards

        # do the first setting of params
        self.update_pm_raise_errors()

        # Startet die Motoren!
        self._start_polling()

        self.card_active.set(True)

    def _start_polling(self):
        """ start polling thread """
        if self.active_thread is not None:
            return  # do not start thread twice 
        th = threading.Thread(target=lambda: self.poll_pm(), name="live viewer measurement")
        self.active_thread = th
        self.stop_thread = False
        self.thread_finished = False
        th.start()

    def _stop_polling(self):
        """ stop polling thread (busy wait until thread terminated) """
        self.stop_thread = True
        if self.active_thread is not None:
            self.active_thread.join()
        self.active_thread = None

    @show_errors_as_popup()
    def stop_pm(self):
        """
        Stops the pm.
        """
        self._stop_polling()

        if self.instrument is not None:
            # ToDo: would be nice to restore saved parameters here
            self.instrument.close()
            self.instrument = None

        self.card_active.set(False)

    @show_errors_as_popup()
    def update_pm(self, *args, **kwargs):
        self.update_pm_raise_errors(*args, **kwargs)

    def update_pm_raise_errors(self):
        """
        Updates the power meters parameters.
        """
        loaded_instr = self.instrument
        if loaded_instr is None:
            raise RuntimeError('Instrument pointer is None, instrument is not loaded so cannot update.')

        parameters = self.ptable.to_meas_param()

        pm_range = power_meter_range_setting(parameters['powermeter range'].value)
        pm_atime = parameters['powermeter averagetime'].value
        pm_wavelength = parameters['powermeter wavelength'].value

        # validate chosen channels before changing anything
        channel_designators = [str(c).strip() for c in
                               str(parameters['channels to plot (comma separated)'].value).split(',')]
        valid_channels = [str(v) for v in loaded_instr.instrument_config_descriptor['channels']]
        for ac in channel_designators:
            if str(ac) not in valid_channels:
                raise ValueError(f'Channel {ac:s} is not a valid channel designator. Valid channels: {valid_channels}')

        self._stop_polling()

        # remove plots for previously enabled, now disabled channels
        to_remove = []
        for ac, trace in self.enabled_channels.items():
            if ac not in channel_designators:
                self.data_to_plot_queue.put(PlotDataPoint(trace_name=trace, delete_trace=True))
                to_remove.append(ac)
        for ac in to_remove:
            self.enabled_channels.pop(ac)

        # add trace name for each newly enabled channel
        for ac in channel_designators:
            if ac in self.enabled_channels:
                continue
            self.enabled_channels[ac] = f'Ch{ac:s}'

        # Not every meter has a range at all - a camera-backed one does not, and neither does a
        # photodiode read through a LabJack. Assigning to a missing property would quietly create
        # an attribute nothing reads, so ask the class whether it really has one.
        has_range = isinstance(getattr(type(loaded_instr), 'range', None), property)
        if not has_range:
            self.logger.info("%s has no range setting, so the range dropdown does not apply to it.",
                             type(loaded_instr).__name__)

        # configure the instrument for each activated channel
        for ac in self.enabled_channels.keys():
            with loaded_instr.thread_lock:
                loaded_instr.channel = ac
                loaded_instr.wavelength = pm_wavelength
                loaded_instr.averagetime = pm_atime
                loaded_instr.unit = 'dBm'
                if has_range:
                    loaded_instr.range = pm_range
                    # A meter rounds a requested range up to the next one it actually has, so what
                    # was asked for and what is in force are routinely different numbers. Worth a
                    # line in the log, because a reading that clips or sits in the noise is
                    # explained by the range in force and not by the one in the dropdown.
                    try:
                        self.logger.info("Channel %s range: asked for %s, meter is using %s.",
                                         ac, pm_range, loaded_instr.range)
                    except Exception:
                        self.logger.debug("Could not read the range back.", exc_info=True)
                loaded_instr.trigger(continuous=False)

        self._start_polling()

    @show_errors_as_popup()
    def poll_pm(self):
        """
        Function to be run in a thread, continuously polls the pm.
        """
        first = True
        while not self.stop_thread:
            with self.instrument.thread_lock:
                for ac, trace in self.enabled_channels.items():

                    # check break conditions
                    if self.stop_thread or (self.instrument is None):
                        break

                    # get up-to-date power value
                    self.instrument.channel = ac
                    if not first:
                        power_data = self.instrument.fetch_power()
                        time_stamp = time.time()
                        self.data_to_plot_queue.put(PlotDataPoint(trace_name=trace,
                                                                  timestamp=time_stamp,
                                                                  y_value=power_data))

                    # trigger channel anew
                    self.instrument.trigger()
            first = False
            sleep(1e-3)

        self.thread_finished = True

    def stop_instr(self):
        """
        This function is needed as a generic stopping function.
        """
        self.stop_pm()
