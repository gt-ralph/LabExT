from LabExT.Measurements.MeasAPI import *
import time
import numpy as np

class IL_power_and_wl_sweep_trigger(Measurement):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)  # calling parent constructor

        self.name = 'IL_power_and_wl_sweep_trigger'
        self.settings_path = 'IL_power_and_wl_sweep_trigger_settings.json'
        self.instr_laser = None
        self.labjack = None

    @staticmethod
    def get_default_parameter():
        return {
            'start wavelength': MeasParamFloat(value=1240.0,unit='nm'),
            'stop wavelength': MeasParamFloat(value=1380.0,unit='nm'),
            'wavelength step': MeasParamFloat(value=0.1,unit='nm'),
            'power start': MeasParamFloat(value=0.0,unit='dB'),
            'power stop': MeasParamFloat(value=13.0,unit='dB'),
            'power step': MeasParamFloat(value=1.0, unit='dB'),
            'step delay': MeasParamFloat(value=0.1, unit='s'),
        }

    @staticmethod
    def get_wanted_instrument():
        return ['Laser', 'Power Meter 1', 'Power Meter 2', 'Power Meter 3']
    
    def algorithm(self, device, data, instruments, parameters):
        # get the parameters
        start_lambda = parameters.get('start wavelength').value
        stop_lambda = parameters.get('stop wavelength').value
        step_lambda = parameters.get('wavelength step').value
        start_power = parameters.get('power start').value
        stop_power = parameters.get('power stop').value
        step_power = parameters.get('power step').value
        step_delay = parameters.get('step delay').value
        

        # get instrument pointers
        self.instr_laser = instruments['Laser']
        self.instr_pm1 = instruments['Power Meter 1']
        self.instr_pm2 = instruments['Power Meter 2']
        self.instr_pm3 = instruments['Power Meter 3']


        # open connection to Laser & PM
         # open connection to power supply
        self.instr_laser.open()
        self.instr_pm1.open()
        self.instr_pm2.open()
        self.instr_pm3.open()


        # clear errors
        self.instr_pm1.clear()
        self.instr_pm2.clear()
        self.instr_pm3.clear()
        self.instr_laser.clear()
    

        # Ask minimal possible wavelength
        min_lambda = float(self.instr_laser.min_lambda)
        print("The following is the minimum possible lambda:")
        print(min_lambda)

        # Ask maximal possible wavelength
        max_lambda = float(self.instr_laser.max_lambda)
        print("The following is the maximum possible lambda:")
        print(max_lambda)

        
        if start_lambda < min_lambda or start_lambda > max_lambda:
            start_lambda = min_lambda
            parameters['start wavelength'].value = start_lambda
            self.logger.warning('start_lambda has been changed to bottom of range: ' + str(start_lambda))
        
        if stop_lambda < min_lambda or stop_lambda > max_lambda:
            stop_lambda = max_lambda
            parameters['stop wavelength'].value = stop_lambda
            self.logger.warning('stop_lambda has been changed to end of range: ' + str(stop_lambda))

        if start_power < 0 or start_power > 13:
            start_power = 0
            parameters['start power'].value = start_power
            self.logger.warning('start_power has been changed to start of range: ' + str(start_power))

        if stop_power < 0 or stop_power > 13:
            stop_power = 13
            parameters['stop power'].value = stop_power
            self.logger.warning('stop_power has been changed to end of range: ' + str(stop_power))



        # Laser settings
        self.instr_laser.unit = 'dBm'
        self.instr_laser.power = start_power
        self.instr_laser.wavelength = start_lambda
        self.instr_laser.enable = True

        self.logger.info("About to start power and wavelength sweep.")

        power_data = np.arange(start_power, stop_power + step_power, step_power)
        wavelength_data = np.arange(start_lambda,stop_lambda + step_lambda, step_lambda)
        optical_power_result_list = []
        keithley_current_result_list = []
        final_pd_keithley_current_result_list = []
        wavelength_result_list = []
        power_result_list = []
        for lam in wavelength_data:
            for pow in power_data:
                self.instr_laser.power = pow
                self.instr_laser.wavelength = lam
                time.sleep(step_delay)
                wavelength_result_list.append(self.instr_laser.wavelength)
                power_result_list.append(self.instr_laser.power)
                optical_power_result_list.append(self.instr_pm1.fetch_power())
                keithley_current_result_list.append(self.instr_pm2.fetch_power())
                final_pd_keithley_current_result_list.append(self.instr_pm3.fetch_power())


        # convert numpy float32/float64 to python float
        data['values']['tx_wavelength'] = wavelength_result_list
        data['values']['tx_power'] = power_result_list
        data['values']['optical_power'] = optical_power_result_list
        data['values']['keithley_current'] = keithley_current_result_list
        data['values']['final_pd_keithley_current'] = final_pd_keithley_current_result_list


        self.instr_laser.enable = False
        # close connection
        self.instr_laser.close()
        self.instr_pm1.close()
        self.instr_pm2.close()
        self.instr_pm3.close()


        # sanity check if data contains all necessary keys
        self._check_data(data)

        return data