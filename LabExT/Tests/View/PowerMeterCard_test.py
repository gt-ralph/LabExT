#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
LabExT  Copyright (C) 2021  ETH Zurich and Polariton Technologies AG
This program is free software and comes with ABSOLUTELY NO WARRANTY; for details see LICENSE file.

Tests for the live viewer's power meter card: how the range dropdown is turned into a driver
setting, and which meters it is applied to. No Tk window and no instruments.
"""

import unittest
from unittest import mock

from LabExT.Measurements.MeasAPI import MeasParamFloat, MeasParamList, MeasParamString
from LabExT.Utils import (AUTO_POWER_METER_RANGE, POWER_METER_RANGE_OPTIONS,
                          power_meter_range_setting)
from LabExT.View.LiveViewer.Cards.PowerMeterCard import PowerMeterCard


class StubMeter:
    """A meter with a range, as the Keysight and Thorlabs drivers have."""

    def __init__(self):
        self.thread_lock = mock.MagicMock()
        self.instrument_config_descriptor = {'channels': [0]}
        self.channel = None
        self.wavelength = None
        self.averagetime = None
        self.unit = None
        self._range = 'auto'
        self.triggered = []

    @property
    def range(self):
        return self._range

    @range.setter
    def range(self, value):
        self._range = value

    def trigger(self, continuous=None):
        self.triggered.append(continuous)


class StubMeterWithoutRange:
    """A meter with no range at all, as the camera-backed and LabJack ones are."""

    def __init__(self):
        self.thread_lock = mock.MagicMock()
        self.instrument_config_descriptor = {'channels': [0]}
        self.channel = None
        self.wavelength = None
        self.averagetime = None
        self.unit = None
        self.triggered = []

    def trigger(self, continuous=None):
        self.triggered.append(continuous)


class RangeSettingTest(unittest.TestCase):
    """
    Tests for the range dropdown's values.

    Required lab setup: none, only SW testing
    """

    user_input_required = False

    def test_the_range_is_a_dropdown_defaulting_to_auto(self):
        parameter = PowerMeterCard.default_parameters['powermeter range']

        self.assertIsInstance(parameter, MeasParamList)
        self.assertEqual(AUTO_POWER_METER_RANGE, parameter.value)
        self.assertIn(AUTO_POWER_METER_RANGE, parameter.options)
        self.assertEqual('dBm', parameter.unit)

    def test_every_option_converts_to_something_a_driver_accepts(self):
        for option in POWER_METER_RANGE_OPTIONS:
            converted = power_meter_range_setting(option)
            if option == AUTO_POWER_METER_RANGE:
                self.assertEqual(AUTO_POWER_METER_RANGE, converted)
            else:
                self.assertIsInstance(converted, float)

    def test_a_range_saved_before_the_dropdown_existed_still_loads(self):
        """The live viewer state used to store the range as a plain integer."""
        self.assertEqual(0.0, power_meter_range_setting(0))
        self.assertEqual(-30.0, power_meter_range_setting(-30.0))

    def test_an_empty_selection_means_auto(self):
        self.assertEqual(AUTO_POWER_METER_RANGE, power_meter_range_setting(''))
        self.assertEqual(AUTO_POWER_METER_RANGE, power_meter_range_setting(None))

    def test_nonsense_is_refused_with_a_message_naming_the_alternatives(self):
        with self.assertRaises(ValueError) as raised:
            power_meter_range_setting('widest')

        message = str(raised.exception)
        self.assertIn('dBm', message)
        self.assertIn(AUTO_POWER_METER_RANGE, message)


class UpdatePmTest(unittest.TestCase):
    """
    Tests for applying the dropdown to a meter.

    Required lab setup: none, only SW testing
    """

    user_input_required = False

    def make_card(self, instrument, range_value=AUTO_POWER_METER_RANGE):
        card = mock.MagicMock()
        card.instrument = instrument
        card.enabled_channels = {}
        card.ptable.to_meas_param.return_value = {
            'powermeter range': MeasParamList(options=POWER_METER_RANGE_OPTIONS, value=range_value,
                                              unit='dBm'),
            'powermeter averagetime': MeasParamFloat(value=0.1, unit='s'),
            'powermeter wavelength': MeasParamFloat(value=1550.0, unit='nm'),
            'channels to plot (comma separated)': MeasParamString(value='0'),
        }
        return card

    def test_auto_reaches_the_meter_as_auto(self):
        meter = StubMeter()
        card = self.make_card(meter)

        PowerMeterCard.update_pm_raise_errors(card)

        self.assertEqual(AUTO_POWER_METER_RANGE, meter.range)
        self.assertEqual('dBm', meter.unit)
        self.assertEqual(1550.0, meter.wavelength)

    def test_a_number_reaches_the_meter_as_a_float_in_dbm(self):
        meter = StubMeter()
        card = self.make_card(meter, range_value='-20')

        PowerMeterCard.update_pm_raise_errors(card)

        self.assertEqual(-20.0, meter.range)

    def test_a_meter_with_no_range_is_left_alone_rather_than_given_a_stray_attribute(self):
        meter = StubMeterWithoutRange()
        card = self.make_card(meter)

        PowerMeterCard.update_pm_raise_errors(card)

        # assigning to a property a class does not have would silently create an instance
        # attribute that nothing ever reads, which looks like it worked
        self.assertFalse(hasattr(meter, 'range'))
        # the rest of the configuration still happened
        self.assertEqual('dBm', meter.unit)
        self.assertEqual([False], meter.triggered)

    def test_an_invalid_channel_is_refused_before_the_meter_is_touched(self):
        meter = StubMeter()
        card = self.make_card(meter)
        card.ptable.to_meas_param.return_value['channels to plot (comma separated)'] = \
            MeasParamString(value='7')

        with self.assertRaises(ValueError):
            PowerMeterCard.update_pm_raise_errors(card)

        self.assertIsNone(meter.unit)


if __name__ == '__main__':
    unittest.main()
