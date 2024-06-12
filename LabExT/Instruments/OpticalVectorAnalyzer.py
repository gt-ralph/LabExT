#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
LabExT  Copyright (C) 2021  ETH Zurich and Polariton Technologies AG
This program is free software and comes with ABSOLUTELY NO WARRANTY; for details see LICENSE file.
"""

from LabExT.Instruments.InstrumentAPI import Instrument, InstrumentException
import numpy as  np
import win32com.client
import os
import numpy as np
import pythoncom

class OpticalVectorAnalyzer(Instrument):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

    def open(self):
        pythoncom.CoInitialize()

        self.labview_app = win32com.client.Dispatch("LabVIEW.Application")

        # Load the VI #TODO: Needs to be generic
        vi_path = os.path.join('C:\\', 'OVA_5000_SDK_v5.14.3','LabVIEW','ConfigureOVA.vi')
        vi = self.labview_app.GetVIReference(vi_path)

        vi.Run

        # Grab data in graph object
        instrFound = np.array(vi.GetControlValue("instrFound"))

        CfgOK = np.array(vi.GetControlValue("Cfg OK?"))

        if not instrFound or not CfgOK:
            raise InstrumentException("Instrument Not Found or Config is Wrong")
        else:
            return

    def grab_data(self, find_dut_L: bool = True, plot_data_type: str = "INSERTION_LOSS", center_wavelength: float = 1550.00, wl_range: float = 2.54):
        # Load the VI #TODO: Needs to be generic
        # labview_app = win32com.client.Dispatch("LabVIEW.Application")
        vi_path = os.path.join('C:\\', 'OVA_5000_SDK_v5.14.3','LabVIEW','Example Top-Level VIs','_AcquireSingleScan.vi')
        vi = self.labview_app.GetVIReference(vi_path)

        wl_range_dict = {
            '0.63': 0,
            '1.27': 1,
            '2.54': 2,
            '5.09': 3,
            '10.22': 4,
            '20.58': 5,
            '41.72': 6,
            '85.78': 7
        }

        plot_data_dict = {
            'INSERTION_LOSS' : 0,
            'GROUP_DELAY' : 1,
            'CHROMATIC_DISPERSION' : 2,
            'POLARIZATION_DEPENDENT_LOSS' : 3,
            'POLARIZATION_MODE_DISPERSION' : 4,
            'LINEAR_PHASE_DEVIATION' : 5,
            'QUADRATIC_PHASE_DEVIATION' : 6,
            'JONES_MATRIX_ELEMENT_AMPLITUDES' : 7,
            'JONES_MATRIX_ELEMENT_PHASES' : 8,
            'TIME_DOMAIN_AMPLITUDE' : 9,
            'TIME_DOMAIN_WAVELENGTH' : 10,
            'MIN_MAX_LOSS' : 11,
            'SECOND_ORDER_PMD' : 12,
            'PHASE_RIPPLE_LINEAR' : 13,
            'PHASE_RIPPLE_QUADRATIC' : 14
        }

        # Set control values if any
        vi.SetControlValue("Find DUT Length?", find_dut_L)
        vi.SetControlValue("New Scan", True)
        vi.SetControlValue("Plot Data", True)
        vi.SetControlValue("Graph Sel", plot_data_dict[plot_data_type])
        vi.SetControlValue("Center WL", center_wavelength)
        vi.SetControlValue("WL Range", wl_range_dict[wl_range])

        vi.Run

        # Grab data in graph object
        result = np.array(vi.GetControlValue("Graph"))

        return result
    
    def save_data(self):
        # labview_app = win32com.client.Dispatch("LabVIEW.Application")
        vi_path = os.path.join('C:\\', 'OVA_5000_SDK_v5.14.3','LabVIEW','WriteOVASprdshtFile.vi')
        vi = self.labview_app.GetVIReference(vi_path)

        # Set control values if any
        vi.SetControlValue("Output Spreadsheet File Path", "C:\\Users\\Luna\\Documents\\test.txt")
        vi.SetControlValue("Graph Data to Output", [True] * 20)

        vi.Run

    # def close(self):
    #     pythoncom.CoUninitialize()
    #     return

    def idn(self):
        return f"OVA"

    def get_instrument_parameter(self):
        return {'idn': self.idn()}