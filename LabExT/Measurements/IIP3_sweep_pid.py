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
class IIP3_sweep(Measurement):
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

    #Performs PID control given a voltage to control
    #returns the history of currents we're measuring
    #and some other variables
    def PID(self, controlled_power_supply, process_measuring_device, setpoint):
        kp = 0.5  # Proportional gain
        ki = 0.2  # Integral gain
        kd = 0.1  # Derivative gain
        sample_time = 0.01
        iteration = 0 
        
        while abs(error) > 1e-11 : # 1 nano amps 

            # Calculate the error and integral term
            process_value = process_measuring_device.fetch_power()
            controller_output = controlled_power_supply.get_voltage()
            print(f"PD current = {process_value}, MZM Bias = {controller_output}")

            error = setpoint - process_value
            print("error is:")
            print(error)
            print("\n")

            integral += error * sample_time
            print("integral is:")
            print(integral)
            print("\n")

            # Calculate the derivative term
            derivative = (error - last_error) / sample_time
            print("derivative is:")
            print(derivative)
            print("\n")
            # Calculate the control output
            control_output = kp * error + ki * integral + kd * derivative

            print(f"error = {error}, control_output = {control_output}")
            
            # Update the parameter and process variable value based on the control output
            plant_voltage= plant_voltage + control_output
            if (abs(controller_output) <= 0.75 and abs(controller_output) >= 0):
                self.instr_ps1.set_voltage(voltage=controller_output)
            else:
                #Added Instrument Exception import 
                #Betting this type of exception will deactviate our instruments nicely
                #and close the gpib stuff, as opposed to a straight up python exception
                raise InstrumentException(f'voltage exceeded safe limit. It was: {controller_output}')
            time.sleep(sample_time)
            iteration += 1
            print("\n")
            print(f"iteration number = {iteration}")
            print("\n")
            iter_limit = 30
            if(iteration > iter_limit):
                raise InstrumentException(f'reached {iter_limit} iterations, terminating')
            # Set the last error for the next iteration
            last_error = error

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
            # # laser power in dBm
            # 'laser power': MeasParamFloat(value=6.0, unit='dBm'),
            # # range of the power meter in dBm
            # 'powermeter range': MeasParamFloat(value=10.0, unit='dBm'),
            # let the user give some own comment
            'users comment': MeasParamString(value=''),
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

        # laser_power = parameters.get('laser power').value
        # pm_range = parameters.get('powermeter range').value

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

        # # Ask minimal possible wavelength
        # min_lambda = float(self.instr_laser.min_lambda)

        # # Ask maximal possible wavelength
        # max_lambda = float(self.instr_laser.max_lambda)

        # # change the minimal & maximal wavelengths if necessary
        # if start_lambda < min_lambda or start_lambda > max_lambda:
        #     start_lambda = min_lambda
        #     parameters['wavelength start'].value = start_lambda
        #     self.logger.warning('start_lambda has been changed to smallest possible value ' + str(min_lambda))

        # if end_lambda > max_lambda or end_lambda < min_lambda:
        #     end_lambda = max_lambda
        #     parameters['wavelength stop'].value = end_lambda
        #     self.logger.warning('end_lambda has been changed to greatest possible value ' + str(max_lambda))

        # write the measurement parameters into the measurement settings
        for pname, pparam in parameters.items():
            data['measurement settings'][pname] = pparam.as_dict()

        points_inner = np.arange(inner_sweep_start_volt,inner_sweep_stop_volt+inner_sweep_step_volt,inner_sweep_step_volt)
        points_outer = np.arange(outer_sweep_start_volt,outer_sweep_stop_volt+outer_sweep_step_volt,outer_sweep_step_volt)
        number_of_points = points_inner.size*points_outer.size


        # PM settings
        # self.instr_pm.wavelength = center_wavelength
        # self.instr_pm.range = pm_range
        # self.instr_pm.unit = 'dBm'
        # max_avg_time = abs(start_lambda - end_lambda) / (sweep_speed * number_of_points)
        # self.instr_pm.averagetime = max_avg_time / 2
        # # note: this check makes sense here, since the instrument might quietly set avg. time to something larger
        # # than desired
        # if self.instr_pm.averagetime > max_avg_time:
        #     raise RuntimeError("Power meter minimum average time is longer than one WL step time!")
        # self.instr_pm.logging_setup(n_measurement_points=number_of_points,
        #                             triggered=True,
        #                             trigger_each_meas_separately=True)
        #Reverse bias for modulator
        self.instr_ps3.set_voltage(voltage=extra_voltage_1, current = 1)
        self.instr_ps4.set_voltage(voltage=extra_voltage_2, current = 1)
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
        initial_current = self.instr_pm2.fetch_power()
        # STARTET DIE MOTOREN!
        # with self.instr_ps:
        trace_data = []
        # start sweeping
        for volt_outer in points_outer:
            for volt_inner in points_inner:
                #Use the MeasParamString's value (rather than the stupid tostring that adds a random colon)
                self.instr_ps2.set_voltage(voltage=volt_inner,current = 1)
                self.instr_ps1.set_voltage(voltage=volt_outer,current = 1)
                time.sleep(ESA_delay)
                self.PID(self.instr_ps4, self.instr_pm2, initial_current)
                outer_current_result_list.append(self.instr_ps1.get_current())
                outer_voltage_result_list.append(self.instr_ps1.get_voltage())
                inner_current_result_list.append(self.instr_ps2.get_current())
                inner_voltage_result_list.append(self.instr_ps2.get_voltage())
                trace_data.append(self.instr_sa.get_trace().tolist())
                optical_power_result_list.append(self.instr_pm1.fetch_power())
                if(rf_state == 1):
                    self.instr_sg2.set_output(0)
                    self.instr_sg1.set_output(0)
                keithley_current_result_list.append(self.instr_pm2.fetch_power())

                if(rf_state == 1):
                    self.instr_sg2.set_output(1)
                    self.instr_sg1.set_output(1)
        np_trace_data = np.asarray(trace_data)


        mdic = {"data": np_trace_data}
        io.savemat("C:\\Users\\ckaylor30\\OneDrive - Georgia Institute of Technology\\laboratory_measurements\\IIP3_sweep_"+time.strftime("%Y_%m_%d_%H_%M_%S")+".mat", mdic)
            #Will  need to read in all the stuff here

        

            # wait for pm finished logging, needs to be time-out checked since hw triggering of PM could silently fail
            # time_start_wait_pms = time.time()
            # while self.instr_pm.logging_busy():
            #     if time.time() - time_start_wait_pms > 3.0:
            #         raise RuntimeError("PM did not finish sweep in 3 seconds after laser sweep done.")
            #     time.sleep(0.1)

        # read out data
        # self.logger.info("Downloading optical power data from power meter.")
        # power_data = self.instr_pm.logging_get_data()
        # self.logger.info("Downloading wavelength data from laser.")
        # used_n_samples = self.instr_laser.sweep_wl_get_n_points()
        # lambda_data = self.instr_laser.sweep_wl_get_data(N_samples=used_n_samples)

        # Reset PM for manual Measurements
        # self.instr_pm.range = 'auto'

        # convert numpy float32/float64 to python float
        # data['values']['transmission [dBm]'] = power_data.tolist()
        # data['values']['wavelength [nm]'] = lambda_data.tolist()
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