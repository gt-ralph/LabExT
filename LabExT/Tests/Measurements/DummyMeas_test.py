#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
LabExT  Copyright (C) 2021  ETH Zurich and Polariton Technologies AG
This program is free software and comes with ABSOLUTELY NO WARRANTY; for details see LICENSE file.
"""

import unittest

import numpy as np

from LabExT.Measurements.DummyMeas import DummyMeas
from LabExT.Measurements.MeasAPI import Measurement


class DummyMeasTest(unittest.TestCase):
    """
    Test for the DummyMeas measurement.

    Required lab setup: none, only SW testing
    """

    #
    # test case constants
    #

    user_input_required = False
    meas = None

    #
    # test cases
    #

    def test_default_parameters(self):

        #
        # parameter and instrument preparation section
        # note: always operate on a copy of data, since the algorithm is supposed to fill it
        #

        data = Measurement.setup_return_dict()

        params = DummyMeas.get_default_parameter()

        #
        # run the measurement algorithm
        #

        self.meas = DummyMeas()
        self.meas.algorithm(None,  # no device given
                            data=data,
                            instruments={},
                            parameters=params)

        #
        # test output section
        #

        self.meas._check_data(data=data)

        # force all to be numpy arrays
        values_arr = {}
        for k, v in data['values'].items():
            values_arr[k] = np.array(v)

        # check data point output
        n_data = len(values_arr['point indices'])
        self.assertTrue(params['number of points'].value == n_data)
        np.testing.assert_array_equal(values_arr['point indices'], np.arange(n_data))

        # check noise output
        self.assertTrue(len(values_arr['point values']) == n_data)
        self.assertTrue(np.all(np.isfinite(values_arr['point values'])))

    def test_raise_error(self):
        #
        # parameter and instrument preparation section
        # note: always operate on a copy of data, since the algorithm is supposed to fill it
        #

        data = Measurement.setup_return_dict()

        params = DummyMeas.get_default_parameter()
        params['simulate measurement error'].value = True

        #
        # run the measurement algorithm
        #

        self.meas = DummyMeas()
        with self.assertRaises(Exception):
            self.meas.algorithm(None,  # no device given
                                data=data,
                                instruments={},
                                parameters=params)
