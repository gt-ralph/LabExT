#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
LabExT  Copyright (C) 2021  ETH Zurich and Polariton Technologies AG
This program is free software and comes with ABSOLUTELY NO WARRANTY; for details see LICENSE file.
"""

import time
from LabExT.Measurements.MeasAPI import *
import numpy as np
import pythoncom
import win32com.client as win32
pythoncom.CoInitialize()
from datetime import datetime
from scipy import io
import os
from LabExT.Instruments.InstrumentAPI import Instrument, InstrumentException

class span_sweep(Measurement):

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)  # calling parent constructor

        self.name = 'span_sweep'
        self.settings_path = 'span_sweep_settings.json'

    @staticmethod
    def get_default_parameter():
        return {
            # outer sweep for LP
            'LP start value': MeasParamInt(value = 8, unit = 'dBm'),
            'LP end value': MeasParamInt(value = 1, unit = 'dBm'),
            'LP step size': MeasParamInt(value = 1, unit = 'dBm'),

            #inner sweep OSNR
            'OSNR start value': MeasParamInt(value = 17, unit = 'dBm'),
            'OSNR end value': MeasParamInt(value = 24, unit = 'dBm'),
            'OSNR step size': MeasParamInt(value = 1, unit = 'dBm'),

            #power supply voltage
            # 'power supply voltage': MeasParamFloat(value=0, unit='V'),

            #attenuation
            'attenuation value 3db': MeasParamInt(value = 0, unit = 'dBm'),
            'attenuation value 6db': MeasParamInt(value = 0, unit = 'dBm'),
            
            'delay':MeasParamInt(value = 1, unit='s'), 

            'number of waveforms':MeasParamInt(value = 3)
        }

    @staticmethod
    def get_wanted_instrument():
        return ['Attenuator 1', 'Attenuator 2', 'UXR']

    def algorithm(self, device, data, instruments, parameters):
        # get the parameters
        LP_start_value = parameters.get('LP start value').value
        LP_end_value = parameters.get('LP end value').value
        LP_step= parameters.get('LP step size').value
        OSNR_start_value = parameters.get('OSNR start value').value
        OSNR_end_value = parameters.get('OSNR end value').value
        OSNR_step= parameters.get('OSNR step size').value
        # voltage = parameters.get('power supply voltage').value
        attenuation3db = parameters.get('attenuation value 3db').value
        attenuation6db = parameters.get('attenuation value 6db').value
        delay = parameters.get('delay').value
        num_waveform = parameters.get('number of waveforms').value

        # get instrument pointers
        # self.instr_ps = instruments['Power Supply']
        self.instr_a3db = instruments['Attenuator 1']
        self.instr_a6db = instruments['Attenuator 2']
        self.instr_uxr = instruments['UXR']
 
        # open connection to power supply
        # self.instr_ps.open()
        self.instr_a3db.open()
        self.instr_a6db.open()
        self.instr_uxr.open()

        # clear errors
        # self.instr_ps.clear()
        self.instr_a3db.clear()
        self.instr_a6db.clear()
        self.instr_uxr.clear()

        # self.instr_ps.set_voltage(voltage = voltage, current = 0)
        self.instr_a3db.atten = attenuation3db
        self.instr_a6db.atten = attenuation6db
        time.sleep(delay)
        points_inner = np.arange(OSNR_start_value, OSNR_end_value + OSNR_step, OSNR_step)
        points_outer = np.arange(LP_start_value, LP_end_value - LP_step, -LP_step)

        pn = 'C:\\Users\\Prankush\\Desktop\\Prankush\\auto_test'
        channels = [1, 2, 3, 4]
        for LP in points_outer:
            diff_LP = 8 - LP
            self.instr_a6db.atten = attenuation6db + diff_LP
            time.sleep(delay)
            ##################################
            # power = powermeter
            # diff = power - LP
            # if diff > 0:
            #     while (diff > 0):
            #         self.instr_ps.set_voltage(voltage = voltage + 0.01, current = 0)
            #         time.sleep(delay)
            #         power = powermeter
            #         diff =  power - LP
            # else:
            #     continue
            ##################################
            for OSNR in points_inner:
                diff_OSNR = OSNR - 17
                self.instr_a3db.atten = attenuation3db + diff_OSNR
                time.sleep(delay)

                pythoncom.CoInitialize()
                outlook = win32.Dispatch('outlook.application')
                mail = outlook.CreateItem(0)
                mail.To = 'pagarwal306@gatech.edu'
                mail.Subject = 'Singlemode Status'
                mail.Body = 'LP=' + str(LP) + '_OSNR=' + str(OSNR)
                mail.Send()

                for k in range(num_waveform):
                    self.instr_uxr.single()
                    for i in channels:
                        data = self.instr_uxr.get_waveform(channel = str(i))
                        if not os.path.exists(pn + '\\' + 'LP_' + str(LP) + 'OSNR' + str(OSNR)):
                            os.makedirs(pn + '\\' + 'LP_' + str(LP) + 'OSNR' + str(OSNR))
                        fn = pn + '\\' + 'LP_' + str(LP) + 'OSNR' + str(OSNR) + '\\' + 'file_' + str(k) + 'channel_' + str(i)
                        np.save(fn, data)

        # close connection
        # self.instr_ps.close()
        self.instr_a3db.close()
        self.instr_a6db.close()
        self.instr_uxr.close()


