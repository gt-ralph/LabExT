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

from LabExT.Instruments.InstrumentAPI import Instrument, InstrumentException
class IIP3_sweep_double_pid(Measurement):
    """
    ## IIp3_sweep

    This measurement uses a tunable laser source to make a fast high-resolution spectral measurement by sweeping the
    output signal wavelength at a fixed speed, synchronized with an optical power meter. The laser measures and logs
    the wavelength values during the sweep with a chosen interval and outputs electrical output triggers to synchronize
    detection sampling. The resulting arrays of wavelength and detected signal samples provide a spectrum showing the
    wavelength dependence of the DUT.

    Currently this measurement supports Agilent/Keysight swept lasers (model numbers 816x) and triggered power
    meters (model numbers 816x or N77xx). The measurement procedure is described in the Keysight application
    note 5992-1125EN, see https://www.keysight.com/ch/de/assets/7018-04983/application-notes/5992-1125.pdf.

    #### example lab setup
    ```
    laser -> DUT -> power meter
      \--trigger-cable--/
    ```
    If your optical power meter is NOT in the same mainframe as the swept laser, you must connect the laser's trigger
    output port to the power meters's trigger input port with a BNC cable!

    #### laser parameters
    * **wavelength start**: starting wavelength of the laser sweep in [nm]
    * **wavelength stop**: stopping wavelength of the laser sweep in [nm]
    * **wavelength step**: wavelength step size of the laser sweep in [pm]
    * **sweep speed**: wavelength sweep speed in [nm/s]
    * **laser power**: laser instrument output power in [dBm]

    #### power meter parameter
    * **powermeter range**: range of the power meter in [dBm]

    #### user parameter
    * **users comment**: this string will simply get stored in the saved output data file. Use this at your discretion.
    """

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)  # calling parent constructor

        self.name = 'IIP3_sweep_double_pid'
        self.settings_path = 'IIP3_sweepdouble_pid_settings.json'
        self.instr_ps1 = None
        self.voltage_limit = 1.6
        self.current_limit = 0.08

    def one_pid_step(self, process_value, controlled_power_supply, setpoint, sample_time,kp,last_error, last_last_error, iteration, min_ever, slope_sign,plant_voltage):

            # print(f"PD current = {process_value}, MZM Bias = {plant_voltage}")
            if(process_value < min_ever):
                min_ever = process_value
                # print(f"Found new minimum, {min_ever}")
            
            error = setpoint - process_value
            # print("error is:")
            # print(error)
            # print("")

            # Calculate the control output
            control_output = kp*error

            # print(f"error = {error}, control_output = {control_output}")
            
            # Update the parameter and process variable value based on the control output
            #Note the slope sign comes from the test above (photodiode poweer is quadratic not linear
            # and we are actively moving towards the bit where the slope suddenly changes direction)
            plant_voltage= plant_voltage + slope_sign*control_output
            controlled_power_supply.voltage = plant_voltage
            time.sleep(sample_time)

            if(abs(last_error) > abs(last_last_error) and abs(error) > abs(last_error) and abs(abs(error)-abs(last_last_error)) > 5e-11):
                kp = kp/2
                # print("kp is the following:")
                # print(kp)
                slope_sign = -slope_sign
                # print('reducing kp')
                last_last_error = 1
                last_error = 0.5

            iter_limit = 600
            if(iteration > iter_limit and abs(min_ever - process_value) > 5e-9 ):
                pythoncom.CoInitialize()
                outlook = win32.Dispatch('outlook.application')
                mail = outlook.CreateItem(0)
                mail.To = 'ckaylor30@gatech.edu'
                mail.Subject = 'Job Terminated Early eek'
                mail.Body = f'Figure out what\'s wrong :(. Also process value was: {process_value} '

                mail.Send()
                raise InstrumentException(f'reached {iteration} iterations, terminating')
            elif(iteration > iter_limit):
                return plant_voltage
            # Set the last error for the next iteration
            last_last_error = last_error
            last_error = error
            return last_error, last_last_error, min_ever, slope_sign,plant_voltage

    #This algorithm discards the control systems paradigm and simply does a 
    #gradient descent  instead
    def return_to_min_2(self, controlled_power_supply_1, process_measuring_device_1,controlled_power_supply_2, process_measuring_device_2, setpoint):
        sample_time =0.2
        iteration = 0 
        integral = 0

        last_error_1 = 0.5
        last_last_error_1 = 1
        slope_sign_1 = -1
        kp_1 = 24000
        min_ever_1 = 1


        last_error_2 = 0.5
        last_last_error_2 = 1
        slope_sign_2 = -1
        kp_2 = 0.05
        min_ever_2 = 1

        error_list_1 = []
        error_list_2 = []

        #take a couple steps in a direction to assess what side of the quadratic we're on
        # controlled_power_supply.voltage = controlled_power_supply.voltage - 0.01
        # time.sleep(sample_time)
        initial_plant_voltage_1 = controlled_power_supply_1.voltage
        initial_process_value_1 = process_measuring_device_1.fetch_power()
        initial_plant_voltage_2 = controlled_power_supply_2.voltage
        initial_process_value_2 = process_measuring_device_2.fetch_power()


        controlled_power_supply_1.voltage =initial_plant_voltage_1
        controlled_power_supply_2.voltage =initial_plant_voltage_2
        plant_voltage_1 = initial_plant_voltage_1
        plant_voltage_2 = initial_plant_voltage_2

        while abs(kp_1) > 400 and abs(last_error_1 - last_last_error_1) > 3e-12 and abs(kp_2) > 0.003 and (abs(last_error_2 - last_last_error_2) > 3e-12): # 60 nano amps 
            iteration = iteration + 1
            process_power_1 = process_measuring_device_1.fetch_power()
            last_error_1, last_last_error_1, min_ever_1, slope_sign_1, plant_voltage_1 = self.one_pid_step(process_power_1, controlled_power_supply_1, setpoint, sample_time, kp_1, last_error_1, last_last_error_1, iteration, min_ever_1, slope_sign_1,plant_voltage_1)
            error_list_1.append(last_error_1)
            fetched_power = process_measuring_device_2.fetch_power()
            process_power_2 = 10**(fetched_power/20) #un-log the power so we can linearly control
            print(f'un-logged power is the following: {process_power_2}')
            print(f'logged power is the following: {fetched_power}')
            last_error_2, last_last_error_2, min_ever_2, slope_sign_2, plant_voltage_2 = self.one_pid_step(process_power_2, controlled_power_supply_2, setpoint, sample_time, kp_2, last_error_2, last_last_error_2, iteration, min_ever_2, slope_sign_2,plant_voltage_2)
            error_list_2.append(last_error_2)
        return plant_voltage_1, plant_voltage_2, error_list_1, error_list_1


    @staticmethod
    def get_default_parameter():
        return {
            # lower bound for sweep
            'outer sweep voltage start': MeasParamFloat(value=0.0, unit='V'),
            # upper bound for sweep
            'outer sweep voltage stop': MeasParamFloat(value=0.1, unit='V'),
            # step size
            'outer sweep voltage step': MeasParamFloat(value=0.1, unit='V'),
            # lower bound for sweep
            'inner sweep start': MeasParamFloat(value=0.0, unit='V'),
            # upper bound for sweep
            'inner sweep stop': MeasParamFloat(value=0.1, unit='V'),
            # step size
            'inner sweep step': MeasParamFloat(value=0.1, unit='V'),
            #extra voltage 1 set point
            'extra voltage 1': MeasParamFloat(value=0.1, unit='V'),
            #extra voltage 2 set point
            'extra voltage 2': MeasParamFloat(value=0.1, unit='V'),
            # sweep speed in nm/s
            'ESA delay': MeasParamFloat(value=0.5, unit='s'),
            #center of frequency band of interest
            'center frequency': MeasParamFloat(value = 1e3, unit='MHz'),
            #span of frequency
            'frequency span': MeasParamFloat(value=10e3, unit='Hz'),
            #rf power
            'rf power': MeasParamFloat(value=10.0, unit='dBm'),
            'rf state': MeasParamInt(value=1,unit=''),
            # let the user give some own comment
            'users comment': MeasParamString(value='')
        }

    @staticmethod
    def get_wanted_instrument():
        return ['Power Supply 1','Power Supply 2', 'Spectrum Analyzer', 'Power Meter 1', 'Power Meter 2','Power Supply 3', 'Signal Generator 1', 'Signal Generator 2', 'Power Supply 4']

    def algorithm(self, device, data, instruments, parameters):
        # get the parameters
        outer_sweep_start_volt = parameters.get('outer sweep voltage start').value
        outer_sweep_stop_volt = parameters.get('outer sweep voltage stop').value
        outer_sweep_step_volt= parameters.get('outer sweep voltage step').value
        inner_sweep_start_volt = parameters.get('inner sweep start').value
        inner_sweep_stop_volt = parameters.get('inner sweep stop').value
        inner_sweep_step_volt = parameters.get('inner sweep step').value
        extra_voltage_1 = parameters.get('extra voltage 1').value
        extra_voltage_2 = parameters.get('extra voltage 2').value
        ESA_delay = parameters.get('ESA delay').value
        fcen = parameters.get('center frequency').value
        fspan = parameters.get('frequency span').value
        pow = parameters.get('rf power').value
        rf_state = parameters.get('rf state').value


        # get instrument pointers
        self.instr_ps1 = instruments['Power Supply 1']
        self.instr_ps2 = instruments['Power Supply 2']
        self.instr_sa = instruments['Spectrum Analyzer']
        self.instr_pm1 = instruments['Power Meter 1']
        self.instr_pm2 = instruments['Power Meter 2']
        self.instr_ps3 = instruments['Power Supply 3']
        self.instr_sg1 = instruments['Signal Generator 1']
        self.instr_sg2 = instruments['Signal Generator 2']
        self.instr_ps4 = instruments['Power Supply 4']
 


        # open connection to power supply
        self.instr_ps1.open()
        self.instr_ps2.open()
        self.instr_sa.open()
        self.instr_pm1.open()
        self.instr_pm2.open()
        self.instr_ps3.open()
        self.instr_sg1.open()
        self.instr_sg2.open()
        self.instr_ps4.open()


        # clear errors
        self.instr_ps1.clear()
        self.instr_ps2.clear()
        self.instr_sa.clear()
        self.instr_pm1.clear()
        self.instr_pm2.clear()
        self.instr_ps3.clear()
        self.instr_sg1.clear()
        self.instr_sg2.clear()
        self.instr_ps4.clear()

        self.instr_sg1.set_power(power=pow)
        self.instr_sg1.set_freq(freq=fcen+0.0002)
        self.instr_sg1.set_output(rf_state)
        self.instr_sg2.set_power(power=pow)
        self.instr_sg2.set_freq(freq=fcen)
        self.instr_sg2.set_output(rf_state)


        # write the measurement parameters into the measurement settings
        for pname, pparam in parameters.items():
            data['measurement settings'][pname] = pparam.as_dict()

        points_inner = np.arange(inner_sweep_start_volt,inner_sweep_stop_volt+inner_sweep_step_volt,inner_sweep_step_volt)
        points_outer = np.arange(outer_sweep_start_volt,outer_sweep_stop_volt+outer_sweep_step_volt,outer_sweep_step_volt)
        number_of_points = points_inner.size*points_outer.size

        #set spare ports to something sensible
        self.instr_ps3.voltage = extra_voltage_1
        self.instr_ps4.voltage = extra_voltage_2
        # inform user
        self.logger.info(f"Sweeping over {number_of_points:d} samples "
                         f"at {ESA_delay:e}s sampling period.")
        
        self.instr_sa.set_initial_settings()
        self.instr_sa.set_frequency_band(fcen*1e6,fspan)

        outer_current_result_list = []
        outer_voltage_result_list = []
        inner_current_result_list = []
        inner_voltage_result_list = []
        optical_power_result_list = []
        keithley_current_result_list = []
        plant_voltage_result_list = []
        # STARTET DIE MOTOREN!
        # with self.instr_ps:
        trace_data = []
        # start sweeping
        for volt_outer in points_outer:
            for volt_inner in points_inner:
                #Use the MeasParamString's value (rather than the stupid tostring that adds a random colon)
                self.instr_ps2.voltage = volt_inner
                self.instr_ps1.voltage = volt_outer
                if(rf_state == 1):
                    self.instr_sg2.set_output(0)
                    self.instr_sg1.set_output(0)
                plant_voltage_1, plant_voltage_2, error_list_1, error_list_2 = self.return_to_min_2(self.instr_ps4, self.instr_pm2,self.instr_ps3, self.instr_pm1, 0)
                print("After PID the plant voltage 1 is:")
                print(plant_voltage_1)
                plant_voltage_result_list.append(plant_voltage_1)
                outer_current_result_list.append(self.instr_ps1.current)
                outer_voltage_result_list.append(self.instr_ps1.voltage)
                inner_current_result_list.append(self.instr_ps2.current)
                inner_voltage_result_list.append(self.instr_ps2.voltage)
                keithley_current_result_list.append(self.instr_pm2.fetch_power())
                optical_power_result_list.append(self.instr_pm1.fetch_power())
                if(rf_state == 1):
                    self.instr_sg2.set_output(1)
                    self.instr_sg1.set_output(1)
                time.sleep(ESA_delay)
                trace_data.append(self.instr_sa.get_trace().tolist())
        np_trace_data = np.asarray(trace_data)


        mdic = {"data": np_trace_data}
        io.savemat("C:\\Users\\ckaylor30\\OneDrive - Georgia Institute of Technology\\laboratory_measurements\\IIP3_sweep_"+time.strftime("%Y_%m_%d_%H_%M_%S")+".mat", mdic)
        

        data['values']['current_outer'] = outer_current_result_list
        data['values']['voltage_outer'] = outer_voltage_result_list
        data['values']['current_inner'] = inner_current_result_list
        data['values']['voltage_inner'] = inner_voltage_result_list
        data['values']['optical_power_result_list'] = optical_power_result_list
        data['values']['keithley_current_result_list'] = keithley_current_result_list
        # close connection
        self.instr_ps1.close()
        self.instr_ps2.close()
        self.instr_sa.close()
        self.instr_pm1.close()
        self.instr_pm2.close()
        self.instr_ps3.close()

        self.instr_sg1.close()
        self.instr_sg2.close()
        self.instr_ps4.close()

        pythoncom.CoInitialize()
        outlook = win32.Dispatch('outlook.application')
        mail = outlook.CreateItem(0)
        mail.To = 'ckaylor30@gatech.edu'
        mail.Subject = 'Job done'
        mail.Body = 'Get your data :)'

        # To attach a file to the email (optional):
        # attachment  = "Path to the attachment"
        # mail.Attachments.Add(attachment)

        mail.Send()

        # mail2 = outlook.CreateItem(0)
        # mail2.To = 'jhiesener4@gatech.edu'
        # mail2.Subject = 'Job done'
        # mail2.Body = 'New data in onedrive folder soon :)'

        # mail2.Send()

        # sanity check if data contains all necessary keys
        self._check_data(data)

        return data
