from LabExT.Measurements.MeasAPI import *
import time
import numpy as np

class IL_sweep_trigger_manypd(Measurement):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)  # calling parent constructor

        self.name = 'IL_power_sweep_trigger'
        self.settings_path = 'IL_power_sweep_trigger_settings.json'
        self.instr_laser = None
        self.labjack = None

    @staticmethod
    def get_default_parameter():
        return {
            'center wavelength': MeasParamFloat(value=1310.0, unit='nm'),
            'power step': MeasParamFloat(value=1.0, unit='dB'),
            'step delay': MeasParamFloat(value=0.1, unit='s'),
        }

    @staticmethod
    def get_wanted_instrument():
        return ['Laser', 'Power Meter 1', 'Power Meter 2', 'Power Meter 3']
    
    def algorithm(self, device, data, instruments, parameters):
        # get the parameters
        center_lambda = parameters.get('center wavelength').value
        power_step = parameters.get('power step').value
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

        # Ask maximal possible wavelength
        max_lambda = float(self.instr_laser.max_lambda)

        # change the center wavelength if necessary
        if center_lambda < min_lambda or center_lambda > max_lambda:
            center_lambda = (max_lambda + min_lambda)/2
            parameters['center wavelength'].value = center_lambda
            self.logger.warning('start_lambda has been changed to center of range ' + str(center_lambda))


        # Laser settings
        self.instr_laser.unit = 'dBm'
        self.instr_laser.power = 0
        self.instr_laser.wavelength = center_lambda


        self.logger.info("About to start power sweep.")

        power_data = np.arange(0, 13 + power_step, power_step)
        optical_power_result_list = []
        keithley_current_result_list = []
        final_pd_keithley_current_result_list = []
        for pow in power_data:
            self.instr_laser.power = pow
            time.sleep(step_delay)
            optical_power_result_list.append(self.instr_pm1.fetch_power())
            keithley_current_result_list.append(self.instr_pm2.fetch_power())
            final_pd_keithley_current_result_list.append(self.instr_pm3.fetch_power())


        # power_data = self.instr_pm.voltage_to_dBm(power_data)

        # convert numpy float32/float64 to python float
        data['values']['optical_power_result_list'] = optical_power_result_list
        data['values']['keithley_current_result_list'] = keithley_current_result_list
        data['values']['final_pd_keithley_current_result_list'] = final_pd_keithley_current_result_list

        # close connection
        self.instr_laser.close()
        self.instr_pm1.close()
        self.instr_pm2.close()
        self.instr_pm3.close()


        # sanity check if data contains all necessary keys
        self._check_data(data)

        return data