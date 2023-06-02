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
from scipy.io import savemat
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
            'attenuation value 3db': MeasParamFloat(value = 0.0, unit = 'dBm'),
            'attenuation value 6db': MeasParamFloat(value = 0.0, unit = 'dBm'),
            'wavelength 3db': MeasParamFloat(value = .000001550, unit='m'),
            'wavelength 6db': MeasParamFloat(value=.000001550, unit = 'm'),
            
            'delay':MeasParamInt(value = 1, unit='s'), 

            'number of waveforms':MeasParamInt(value = 3)
        }
    
    #Performs PID control given a voltage to control
    #returns the history of currents we're measuring
    #and some other variables
    def PID(self, controlled_power_supply, process_measuring_device, setpoint):
        kp = 0.5  # Proportional gain
        ki = 0.1#.2  # Integral gain
        kd = 0.01#.1  # Derivative gain
        test_step_size = 0.03 #step size to test the slope
        sample_time =0.2
        iteration = 0 
        integral = 0
        error = 1
        last_error = 0
        slope_sign = 1

        #take a couple steps in a direction to assess what side of the quadratic we're on
        # controlled_power_supply.voltage = controlled_power_supply.voltage - 0.01
        # time.sleep(sample_time)
        # initial_plant_voltage = controlled_power_supply.voltage
        # initial_process_value = process_measuring_device.fetch_power()
        # first_step = initial_plant_voltage - test_step_size
        # controlled_power_supply.voltage = first_step
        # time.sleep(sample_time)
        # first_step_process_value = process_measuring_device.fetch_power()

        # second_step = initial_plant_voltage - 2*test_step_size
        # controlled_power_supply.voltage = second_step
        # time.sleep(sample_time)
        # second_step_process_value = process_measuring_device.fetch_power()


        # if(first_step_process_value > initial_process_value and second_step_process_value > first_step_process_value):
        #     slope_sign = -1
        # elif(first_step_process_value < initial_process_value and second_step_process_value < first_step_process_value):
        #     slope_sign = 1
        # else:
        #     print('test didn\'t produce consistent results. Maybe reducing step size will help?')
        #     print('setting slope_sign to 1 so at least something happens')
        #     slope_sign = 1
        # controlled_power_supply.voltage =initial_plant_voltage - test_step_size
        plant_voltage = controlled_power_supply.voltage

        while abs(error) > 0.01: # hundredth of a db

            # Calculate the error and integral term
            process_value = process_measuring_device.fetch_power()
            # print(f"PD current = {process_value}, MZM Bias = {plant_voltage}")
            
            error = setpoint - process_value
            # print("error is:")
            # print(error)
            # print("")

            integral += error * sample_time
            # print("integral is:")
            # print(integral)
            # print("")

            # Calculate the derivative term
            derivative = (error - last_error) / sample_time
            # print("derivative is:")
            # print(derivative)
            # print("")
            # Calculate the control output
            control_output = kp * error + ki * integral + kd * derivative

            print(f"error = {error}, control_output = {control_output}")
            
            # Update the parameter and process variable value based on the control output
            #Note the slope sign comes from the test above (photodiode poweer is quadratic not linear
            # and we are actively moving towards the bit where the slope suddenly changes direction)
            plant_voltage= plant_voltage + slope_sign*control_output
            if(plant_voltage > 1.6):
                raise InstrumentException(f'Exceeded allowed plant voltage, it was {plant_voltage}')
            controlled_power_supply.voltage = plant_voltage
            time.sleep(sample_time)
            iteration += 1
            # print(f"iteration number = {iteration}")
            # print("\n")
            iter_limit = 50
            if(iteration > iter_limit):
                raise InstrumentException(f'reached {iteration} iterations, terminating')
            # Set the last error for the next iteration
            last_error = error
        return plant_voltage

    @staticmethod
    def get_wanted_instrument():
        return ['Attenuator 1', 'Attenuator 2', 'UXR', 'Power Meter', 'Power Supply']

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
        wavelength3db = parameters.get('wavelength 3db').value
        wavelength6db = parameters.get('wavelength 6db').value
        delay = parameters.get('delay').value
        num_waveform = parameters.get('number of waveforms').value

        # get instrument pointers
        # self.instr_ps = instruments['Power Supply']
        self.instr_a3db = instruments['Attenuator 1']
        self.instr_a6db = instruments['Attenuator 2']
        self.instr_uxr = instruments['UXR']
        self.instr_pm = instruments['Pwer Meter']
        self.instr_ps = instruments['Power Supply']
 
        # open connection to power supply
        # self.instr_ps.open()
        self.instr_a3db.open()
        self.instr_a3db.output = 1
        self.instr_a6db.open()
        self.instr_a6db.output = 1
        self.instr_uxr.open()
        self.instr_pm.open()
        self.instr_ps.open()

        # clear errors
        self.instr_pm.clear()
        self.instr_ps.clear()
        # self.instr_ps.clear()
        # self.instr_a3db.clear()
        # self.instr_a6db.clear()
        # self.instr_uxr.clear()

        # self.instr_ps.set_voltage(voltage = voltage, current = 1)
        self.instr_a3db.atten = attenuation3db
        self.instr_a6db.atten = attenuation6db
        self.instr_a3db.wavelength = wavelength3db
        self.instr_a6db.wavelength = wavelength6db
        time.sleep(delay)
        points_inner = np.arange(OSNR_start_value, OSNR_end_value + OSNR_step, OSNR_step)
        points_outer = np.arange(LP_start_value, LP_end_value - LP_step, -LP_step)

        self.PID(self.instr_pm,self.instr_ps, 16)
        LP_array = []
        OSNR_array = []

        pn = 'C:\\Users\\Prankush\\Desktop\\Prankush\\auto_test1'
        #channels = [1, 2, 3, 4]
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
                self.PID(self.instr_pm,self.instr_ps, -4)
                time.sleep(delay)

                # pythoncom.CoInitialize()
                # outlook = win32.Dispatch('outlook.application')
                # mail = outlook.CreateItem(0)
                # mail.To = 'pagarwal306@gatech.edu'
                # mail.Subject = 'Singlemode Status'
                # mail.Body = 'LP =' + str(LP) + ' OSNR =' + str(OSNR)
                # mail.Send()
                LP_array.append(LP)
                OSNR_array.append(OSNR)
                for k in range(num_waveform):
                    self.instr_uxr.single()
                    data1 = self.instr_uxr.get_waveform(channel_str= "CHAN" + str(1))
                    data2 = self.instr_uxr.get_waveform(channel_str= "CHAN" + str(2))
                    data3 = self.instr_uxr.get_waveform(channel_str= "CHAN" + str(3))
                    data4 = self.instr_uxr.get_waveform(channel_str= "CHAN" + str(4))
                    big_data = np.array([data1[1][0:500000], data2[1][0:500000], data3[1][0:500000], data4[1][0:500000]])
                    data = {'a':big_data}
                    if not os.path.exists(pn + '\\' + 'LP_' + str(LP) + '_OSNR_' + str(OSNR)):
                        os.makedirs(pn + '\\' + 'LP_' + str(LP) + '_OSNR_' + str(OSNR))
                    fn = pn + '\\' + 'LP_' + str(LP) + '_OSNR_' + str(OSNR) + '\\' + 'file_' + str(k+1) + '.mat'
                    savemat(fn, data)

                        # array = self.instr_uxr.get_waveform(channel_str= "CHAN" + str(i))
                        # print(array, type(array))
                        # print()
                        # print(array[0], type(array[0]))
                        # print()
                        # print(array[1], type(array[1]), type(array[1][0]))
                        # print()
                        # if not os.path.exists(pn + '\\' + 'LP_' + str(LP) + '_OSNR_' + str(OSNR)):
                        #     os.makedirs(pn + '\\' + 'LP_' + str(LP) + '_OSNR_' + str(OSNR))
                        # fn = pn + '\\' + 'LP_' + str(LP) + '_OSNR_' + str(OSNR) + '\\' + 'file_' + str(k) + '_channel_' + str(i)
                        # np.save(fn, array)

        # close connection
        # self.instr_ps.close()
        self.instr_a3db.close()
        self.instr_a6db.close()
        self.instr_uxr.close()
        self.instr_pm.close()
        self.instr_ps.close()

        data['values']['Launch Powers'] = LP_array
        data['values']['OSNRs'] = OSNR_array

                #Gotta coinitialize right before emailing
        #do not ask why because I have no clue
        pythoncom.CoInitialize()

        #Email object for sending emails
        outlook = win32.Dispatch('outlook.application')
        mail = outlook.CreateItem(0)

        #add in your own email
        mail.To = 'ckaylor30@gatech.edu'
        mail.Subject = 'Job done'
        mail.Body = 'Get your data :)'

        # To attach a file to the email (optional):
        # attachment  = "Path to the attachment"
        # mail.Attachments.Add(attachment)

        mail.Send()

        mail2 = outlook.CreateItem(0)
        mail2.To = 'pagarwal306@gatech.edu'
        mail2.Subject = 'Job done'
        mail2.Body = 'New data in onedrive folder soon :)'

        mail2.Send()

        # sanity check if data contains all necessary keys
        self._check_data(data)

        return data


