#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
LabExT  Copyright (C) 2021  ETH Zurich and Polariton Technologies AG
This program is free software and comes with ABSOLUTELY NO WARRANTY; for details see LICENSE file.

Tests for Search for Peak's power meter ranging: how the range dropdown reaches the meters, and
how a scan point copes with samples the meter could not take. No stages, no instruments.
"""

import unittest
from unittest import mock

import numpy as np

from LabExT.Measurements.MeasAPI import MeasParamFloat, MeasParamInt, MeasParamList
from LabExT.SearchForPeak.PeakSearcher import PeakSearcher
from LabExT.Utils import AUTO_POWER_METER_RANGE, POWER_METER_RANGE_OPTIONS


class StubMeter:
    """A conventional optical power meter: has a range, and is not camera backed."""

    def __init__(self, samples=None):
        self.unit = None
        self.wavelength = None
        self._range = None
        self.samples = list(samples) if samples is not None else [-20.0]
        self.index = 0

    @property
    def range(self):
        return self._range

    @range.setter
    def range(self, value):
        self._range = value

    def trigger(self, continuous=None):
        pass

    def fetch_power(self):
        value = self.samples[self.index % len(self.samples)]
        self.index += 1
        return value


class CameraBackedStubMeter(StubMeter):
    IS_CAMERA_BACKED = True


class RangeParameterTest(unittest.TestCase):
    """
    Tests for the Power Meter range parameter.

    Required lab setup: none, only SW testing
    """

    user_input_required = False

    def test_the_range_is_a_dropdown_offering_auto(self):
        parameter = PeakSearcher.get_default_parameter()['Power Meter range']

        self.assertIsInstance(parameter, MeasParamList)
        self.assertEqual(AUTO_POWER_METER_RANGE, parameter.value)
        self.assertEqual(POWER_METER_RANGE_OPTIONS, parameter.options)
        self.assertEqual('dBm', parameter.unit)


class ConfigurePowerMetersTest(unittest.TestCase):
    """
    Tests for pushing the range onto the selected meters.

    Required lab setup: none, only SW testing
    """

    user_input_required = False

    def make_searcher(self, meters, range_value=AUTO_POWER_METER_RANGE):
        searcher = mock.MagicMock()
        searcher.instr_powermeters = meters
        searcher.parameters = {
            'Power Meter range': MeasParamList(options=POWER_METER_RANGE_OPTIONS,
                                               value=range_value, unit='dBm'),
            'Laser wavelength': MeasParamInt(value=1550, unit='nm'),
            'Camera merit unit': MeasParamList(options=['counts', 'dB'], value='counts'),
            'Camera no-peak contrast threshold': MeasParamFloat(value=10.0, unit='%'),
        }
        return searcher

    def test_auto_reaches_the_meter_as_auto(self):
        meter = StubMeter()
        searcher = self.make_searcher([meter])

        PeakSearcher._configure_power_meters(searcher)

        self.assertEqual(AUTO_POWER_METER_RANGE, meter.range)
        self.assertEqual('dBm', meter.unit)
        self.assertEqual(1550, meter.wavelength)

    def test_a_fixed_range_reaches_the_meter_as_a_float_in_dbm(self):
        meter = StubMeter()
        searcher = self.make_searcher([meter], range_value='-40')

        PeakSearcher._configure_power_meters(searcher)

        self.assertEqual(-40.0, meter.range)

    def test_a_range_saved_before_the_dropdown_existed_still_applies(self):
        meter = StubMeter()
        searcher = self.make_searcher([meter])
        searcher.parameters['Power Meter range'] = MeasParamFloat(value=0.0, unit='dBm')

        PeakSearcher._configure_power_meters(searcher)

        self.assertEqual(0.0, meter.range)

    def test_a_camera_backed_meter_is_given_no_range_at_all(self):
        meter = CameraBackedStubMeter()
        searcher = self.make_searcher([meter])

        PeakSearcher._configure_power_meters(searcher)

        self.assertIsNone(meter.range)
        self.assertEqual('counts', meter.unit)


class AverageSamplesTest(unittest.TestCase):
    """
    Tests for averaging a scan point's samples when some could not be taken.

    Required lab setup: none, only SW testing
    """

    user_input_required = False

    def average(self, samples):
        searcher = mock.MagicMock()
        return PeakSearcher._average_samples(searcher, StubMeter(), samples)

    def test_ordinary_samples_average_as_before(self):
        self.assertAlmostEqual(-20.0, self.average([-19.0, -20.0, -21.0]), places=9)

    def test_a_transient_unreadable_sample_is_left_out_rather_than_poisoning_the_point(self):
        """A range switch produces one of these, and it must not cost the whole scan."""
        self.assertAlmostEqual(-20.0, self.average([-19.0, float('nan'), -21.0]), places=9)

    def test_a_point_where_nothing_could_be_read_is_still_nan(self):
        """That is what makes the searcher report that a range change is needed."""
        self.assertTrue(np.isnan(self.average([float('nan'), float('nan')])))

    def test_an_infinite_sample_is_treated_as_unreadable_too(self):
        self.assertAlmostEqual(-20.0, self.average([float('-inf'), -20.0]), places=9)


if __name__ == '__main__':
    unittest.main()
