from LabExT.Measurements.MeasAPI import *
import time
import numpy as np

import sys
from PyQt5.QtCore import Qt
from PyQt5.QtWidgets import (
    QApplication,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QPushButton,
    QStackedLayout,
    QVBoxLayout,
    QWidget,
    QGridLayout
)

from typing import Type
from LabExT.Movement.MoverNew import MoverNew

class EdgeAlign(Measurement):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)  # calling parent constructor

        self.name = 'EdgeAlign'
        self.settings_path = 'EdgeAlign_settings.json'
        self.instr_laser = None
        self.labjack = None
        self.mover = self._experiment_manager.mover

    @staticmethod
    def get_default_parameter():
        return {
            'wavelength start': MeasParamFloat(value=1520.0, unit='nm'),
            'wavelength stop': MeasParamFloat(value=1600.0, unit='nm'),
            'wavelength step': MeasParamFloat(value=10.0, unit='pm'),
            'sweep speed': MeasParamFloat(value=10.0, unit='nm/s'),
            'sweep cycles': MeasParamInt(value=1),
            'scan rate': MeasParamInt(value=1000, unit='Hz'), #TODO
            'laser power': MeasParamFloat(value=0.0, unit='dBm'),
            'nbr of pds': MeasParamInt(value=1)
        }

    @staticmethod
    def get_wanted_instrument():
        return ['Laser', 'Power Meter 0', 'Power Meter 1', 'Power Meter 2', 'Power Meter 3', 'Power Meter 4', 'Power Meter 5']

    def algorithm(self, device, data, instruments, parameters):

        class MainWindow(QMainWindow):
            def __init__(self):
                super().__init__()

                self.setWindowTitle("Edge Alignment")
                self.setGeometry(100, 100, 400, 300)  # Set window size

                pagelayout = QVBoxLayout()
                self.stacklayout = QStackedLayout()

                pagelayout.addLayout(self.stacklayout)

                button_layout = QGridLayout()  # Use QGridLayout for button placement

                pagelayout.addLayout(button_layout)

                btn = QPushButton("red")
                btn.pressed.connect(self.activate_tab_1)
                button_layout.addWidget(btn, 0, 0)  # Top-left corner

                btn = QPushButton("green")
                btn.pressed.connect(self.activate_tab_2)
                button_layout.addWidget(btn, 0, 1)  # Top-right corner

                btn = QPushButton("yellow")
                btn.pressed.connect(self.activate_tab_3)
                button_layout.addWidget(btn, 1, 0)  # Bottom-left corner

                btn = QPushButton("joel")
                btn.pressed.connect(self.activate_tab_4)
                button_layout.addWidget(btn, 1, 1)  # Bottom-right corner

                widget = QWidget()
                widget.setLayout(pagelayout)
                self.setCentralWidget(widget)

                # Add stretch to push the buttons to the corners
                pagelayout.addStretch()

                # Create a QLabel for displaying the button text at the bottom
                self.bottom_label = QLabel()
                pagelayout.addWidget(self.bottom_label)
                self.bottom_label.setText("Current Motor Position: X=1um Y=2um Z=3um")

                # Create a Close button in the bottom right corner
                close_button = QPushButton("Close")
                close_button.setStyleSheet("color: red;")  # Make text red
                close_button.clicked.connect(self.close)
                button_layout.addWidget(close_button, 2, 2, alignment=Qt.AlignBottom | Qt.AlignRight)  # Bottom-right corner

            def activate_tab_1(self):
                self.stacklayout.setCurrentIndex(0)
                self.bottom_label.setText("Button Pressed: red")

            def activate_tab_2(self):
                self.stacklayout.setCurrentIndex(1)
                self.bottom_label.setText("Button Pressed: green")

            def activate_tab_3(self):
                self.stacklayout.setCurrentIndex(2)
                self.bottom_label.setText("Button Pressed: yellow")

            def activate_tab_4(self):
                self.stacklayout.setCurrentIndex(3)
                self.bottom_label.setText("Button Pressed: joel")

        app = QApplication(sys.argv)
        window = MainWindow()
        window.show()
        app.exec()

        print(self.mover.get_position())





        # # get the parameters
        # start_lambda = parameters.get('wavelength start').value
        # end_lambda = parameters.get('wavelength stop').value
        # center_wavelength = (start_lambda + end_lambda)/2
        # step_pm = parameters.get('wavelength step').value
        # laser_power = parameters.get('laser power').value
        # sweep_speed = parameters.get('sweep speed').value

        # sweep_cycles = parameters.get('sweep cycles').value

        # scan_rate = parameters.get('scan rate').value
        # scans_per_read = parameters.get('scan rate').value

        # nbr_pds = parameters.get("nbr of pds").value
        # pd_list = [i for i in range(nbr_pds)]

        # # get instrument pointers
        # self.instr_laser = instruments['Laser']
        # self.instr_pms = [instruments[f'Power Meter {pd}'] for pd in pd_list]

        # # open connection to Laser & PM
        # self.instr_laser.open()
        # for pm in self.instr_pms:
        #     pm.open()
        # self.lj = self.instr_pms[0].lj

        # # clear errors
        # self.instr_laser.clear()

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

        # # write the measurement parameters into the measurement settings
        # for pname, pparam in parameters.items():
        #     data['measurement settings'][pname] = pparam.as_dict()


        # # Set sweep parameters
        # speed = (end_lambda - start_lambda) / sweep_speed
        # vector_length = int(scans_per_read * speed)
        # MAX_REQUESTS = np.ceil(speed)

        # channels = [pm.lj_port for pm in self.instr_pms]
        # nc = len(channels)
        # a_scan_list = self.lj.make_scan_list(nc, channels)

        # # init triggered stream on pm
        # self.lj.init_triggered_stream()

        # new_scan_rate = self.lj.start_stream(scans_per_read, nc, a_scan_list, scan_rate)
        # self.logger.debug(f"Stream started with a scan rate of {new_scan_rate:0.0f} Hz \n Performing {MAX_REQUESTS} stream reads.")

        # # Laser settings
        # self.instr_laser.unit = 'dBm'
        # self.instr_laser.power = laser_power
        # self.instr_laser.wavelength = center_wavelength
        # self.instr_laser.step_pm = step_pm
        # self.instr_laser.triggered_sweep_wl_setup(start_lambda, end_lambda, step_pm, sweep_speed, sweep_cycles)

        # with self.instr_laser:
        #     self.instr_laser.triggered_sweep_wl_start()
        #     power_data = self.lj.start_logging(MAX_REQUESTS, scans_per_read, new_scan_rate, channels, nc, vector_length)

        # self.logger.info("Downloading wavelength data from laser.")
        # # used_n_samples = self.instr_laser.sweep_wl_get_n_points()
        # # lambda_data = self.instr_laser.sweep_wl_get_data(N_samples=used_n_samples)

        # lambda_data = np.linspace(start_lambda, end_lambda, vector_length)

        # # Calibrate data
        # for i, pm in enumerate(self.instr_pms):
        #     power_data[i, :] = pm.voltage_to_dBm(power_data[i, :])
        # # power_data = self.instr_pm.voltage_to_dBm(power_data)

        # # convert numpy float32/float64 to python float
        # data['values']['wavelength [nm]'] = lambda_data.tolist()
        # for i, pm in enumerate(self.instr_pms):
        #     data['values'][f'transmission {pm.lj_port} [dBm]'] = power_data[i, :].tolist()

        # # close connection
        # self.instr_laser.close()
        # self.instr_pms[0].close()

        # # sanity check if data contains all necessary keys
        # self._check_data(data)
        data = []

        return data