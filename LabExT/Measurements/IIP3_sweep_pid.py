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
class IIP3_sweep_pid(Measurement):
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

        self.name = 'IIP3_sweep'
        self.settings_path = 'IIP3_sweep_settings.json'
        self.instr_ps1 = None
        self.voltage_limit = 1.6
        self.current_limit = 0.08

    #This algorithm discards the control systems paradigm and simply does a 
    #gradient descent  instead
    def return_to_min(self, controlled_power_supply, process_measuring_device, setpoint):
        test_step_size = 0.03 #step size to test the slope
        sample_time =0.2
        iteration = 0 
        integral = 0
        error = 1
        last_error = 0.5
        last_last_error = 1
        slope_sign = 0
        kp = 0.1
        #ignore changes in direction for a few iterations
        mask_num = 0
        mask = 0


        #take a couple steps in a direction to assess what side of the quadratic we're on
        # controlled_power_supply.voltage = controlled_power_supply.voltage - 0.01
        # time.sleep(sample_time)
        initial_plant_voltage = controlled_power_supply.voltage
        initial_process_value = process_measuring_device.fetch_power()


        # first_step = initial_plant_voltage - test_step_size
        # controlled_power_supply.voltage = first_step
        # time.sleep(sample_time)
        # first_step_process_value = process_measuring_device.fetch_power()

        # second_step = initial_plant_voltage - 2*test_step_size
        # controlled_power_supply.voltage = second_step
        # time.sleep(sample_time)
        # second_step_process_value = process_measuring_device.fetch_power()
        slope_sign = -1
        min_ever = 1


        # if(first_step_process_value > initial_process_value and second_step_process_value > first_step_process_value):
        #     slope_sign = -1
        # elif(first_step_process_value < initial_process_value and second_step_process_value < first_step_process_value):
        #     slope_sign = 1
        # else:
        #     print('test didn\'t produce consistent results. Maybe reducing step size will help?')
        #     print('setting slope_sign to 1 so at least something happens')
        #     slope_sign = 1
        controlled_power_supply.voltage =initial_plant_voltage
        plant_voltage = initial_plant_voltage

        while abs(kp) > 0.01 and (abs(last_error - last_last_error) > 3e-12): # 60 nano amps 

            # Calculate the error and integral term
            #un-log the photodiode
            process_value = 10**(process_measuring_device.fetch_power()/10)
            print(f"PD current = {process_value}, MZM Bias = {plant_voltage}")
            if(process_value < min_ever):
                min_ever = process_value
                print(f"Found new minimum, {min_ever}")
            
            error = setpoint - process_value
            # print("error is:")
            # print(error)
            # print("")

            # Calculate the control output
            control_output = kp*error

            print(f"error = {error}, control_output = {control_output}")
            
            # Update the parameter and process variable value based on the control output
            #Note the slope sign comes from the test above (photodiode poweer is quadratic not linear
            # and we are actively moving towards the bit where the slope suddenly changes direction)
            plant_voltage= plant_voltage + slope_sign*control_output

            if(plant_voltage > 0.75):
                pythoncom.CoInitialize()
                outlook = win32.Dispatch('outlook.application')
                mail = outlook.CreateItem(0)
                mail.To = 'ckaylor30@gatech.edu'
                mail.Subject = 'Job Terminated Early eek'
                mail.Body = f'Figure out what\'s wrong :(. Also plant voltage was: {plant_voltage} '

                mail.Send()
                raise InstrumentException(f'Exceeded allowed plant voltage, it was {plant_voltage}')

            controlled_power_supply.voltage = plant_voltage
            time.sleep(sample_time)
            iteration += 1
            # print(f"iteration number = {iteration}")
            # print("\n")

            if(abs(last_error) > abs(last_last_error) and abs(error) > abs(last_error) and mask > mask_num and abs(abs(error)-abs(last_last_error)) > 5e-11):
                kp = kp/2
                print("kp is the following:")
                print(kp)
                slope_sign = -slope_sign
                print('reducing kp')
                last_last_error = 1
                last_error = 0.5
                mask = 0
            
            mask = mask + 1
            iter_limit = 150
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
        return plant_voltage


    #Performs PID control given a voltage to control
    #returns the history of currents we're measuring
    #and some other variables
    def PID(self, controlled_power_supply, process_measuring_device, setpoint):
        kp = 8000  # Proportional gain
        ki = 100#.2  # Integral gain
        kd = 10#.1  # Derivative gain
        test_step_size = 0.03 #step size to test the slope
        sample_time =0.2
        iteration = 0 
        integral = 0
        error = 1
        last_error = 0
        slope_sign = 0

        #take a couple steps in a direction to assess what side of the quadratic we're on
        controlled_power_supply.voltage = controlled_power_supply.voltage - 0.01
        time.sleep(sample_time)
        initial_plant_voltage = controlled_power_supply.voltage
        initial_process_value = process_measuring_device.fetch_power()
        first_step = initial_plant_voltage - test_step_size
        controlled_power_supply.voltage = first_step
        time.sleep(sample_time)
        first_step_process_value = process_measuring_device.fetch_power()

        second_step = initial_plant_voltage - 2*test_step_size
        controlled_power_supply.voltage = second_step
        time.sleep(sample_time)
        second_step_process_value = process_measuring_device.fetch_power()


        if(first_step_process_value > initial_process_value and second_step_process_value > first_step_process_value):
            slope_sign = -1
        elif(first_step_process_value < initial_process_value and second_step_process_value < first_step_process_value):
            slope_sign = 1
        else:
            print('test didn\'t produce consistent results. Maybe reducing step size will help?')
            print('setting slope_sign to 1 so at least something happens')
            slope_sign = 1
        controlled_power_supply.voltage =initial_plant_voltage - test_step_size
        

        while abs(error) > 7e-8 : # 60 nano amps 

            # Calculate the error and integral term
            process_value = process_measuring_device.fetch_power()
            plant_voltage = controlled_power_supply.voltage
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
            if(plant_voltage > 0.6):
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
        return ['Power Supply 1','Power Supply 2', 'Spectrum Analyzer', 'Power Meter 1', 'Power Meter 2','Power Supply 3', 'Signal Generator 1', 'Signal Generator 2', 'Power Supply 4', 'Power Meter 3']

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
        self.instr_pm3 = instruments['Power Meter 3']
        
 


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
        self.instr_pm3.open()


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
        self.instr_pm3.clear()

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
        final_pd_keithley_current_result_list = []
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
                plant_voltage = self.return_to_min(self.instr_ps4, self.instr_pm1, 0)
                print("After PID the plant voltage is:")
                print(plant_voltage)
                plant_voltage_result_list.append(plant_voltage)
                outer_current_result_list.append(self.instr_ps1.current)
                outer_voltage_result_list.append(self.instr_ps1.voltage)
                inner_current_result_list.append(self.instr_ps2.current)
                inner_voltage_result_list.append(self.instr_ps2.voltage)
                keithley_current_result_list.append(self.instr_pm2.fetch_power())
                optical_power_result_list.append(self.instr_pm1.fetch_power())
                final_pd_keithley_current_result_list.append(self.instr_pm3.fetch_power())
 
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
        data['values']['final_pd_keithley_current_result_list'] = final_pd_keithley_current_result_list
        # close connection
        self.instr_ps1.close()
        self.instr_ps2.close()
        self.instr_sa.close()
        self.instr_pm1.close()
        self.instr_pm2.close()
        self.instr_ps3.close()
        self.instr_pm3.close()
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
