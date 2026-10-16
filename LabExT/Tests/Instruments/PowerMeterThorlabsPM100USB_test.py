#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
LabExT  Copyright (C) 2021  ETH Zurich and Polariton Technologies AG
This program is free software and comes with ABSOLUTELY NO WARRANTY; for details see LICENSE file.

Unit tests for the Thorlabs PM100USB driver. The software tests need no meter and no Thorlabs
installation: a stub library is substituted through the driver's _load_library seam, the same way
PowerMeterCameraAlvium_test substitutes a camera through _make_camera.

The laboratory tests at the bottom need:

  * a Thorlabs PM100USB with a sensor attached, over USB
  * a Thorlabs TLPM installation, which ships with Optical Power Monitor among others
  * **Optical Power Monitor closed** - a TLPM session is exclusive, so a meter held by the GUI
    cannot be opened here
"""

import json
import math
import time
import unittest
from unittest import mock

import LabExT.Instruments.PowerMeterThorlabsPM100USB as pm_module
from LabExT.Instruments.InstrumentAPI import InstrumentException
from LabExT.Instruments.PowerMeterThorlabsPM100USB import (MINIMUM_DBM,
                                                           VI_INSTR_WARN_OVERFLOW,
                                                           PowerMeterThorlabsPM100USB)
from LabExT.Tests.Utils import mark_as_laboratory_test


class FakeFunction:
    """One TLPM entry point: records its calls and writes into the pointers it is handed."""

    def __init__(self, library, name):
        self.library = library
        self.name = name
        self.restype = None
        self.argtypes = None

    def __call__(self, *args):
        self.library.calls.append((self.name, args))
        return self.library.dispatch(self.name, args)


class FakeTLPM:
    """Enough of TLPM_64.dll to drive the driver, with scripted devices and readings."""

    def __init__(self, devices=None, power=1e-3, power_status=0, init_status=0):
        self.devices = devices if devices is not None else [
            {'resource': 'USB0::0x1313::0x8072::M0000001::INSTR', 'model': 'PM100USB',
             'serial': 'M0000001', 'manufacturer': 'Thorlabs', 'available': 1}]
        self.power = power
        self.power_status = power_status
        self.init_status = init_status
        self.calls = []
        self.closed = 0
        #: written by the setters, read back by the getters, so a round trip is a real round trip
        self.state = {
            'wavelength': 1550.0,
            'wavelength min': 800.0,
            'wavelength max': 1700.0,
            'unit': 0,
            'avg time': 0.001,
            'autorange': 1,
            'range': 1e-3,
            'sensor flags': 0x0020,
        }

    def __getattr__(self, name):
        if not name.startswith('TLPM_'):
            raise AttributeError(name)
        return FakeFunction(self, name)

    def calls_to(self, name):
        return [args for called, args in self.calls if called == name]

    @staticmethod
    def _write_string(buffer, text):
        buffer.value = text.encode('ascii')

    def dispatch(self, name, args):
        if name == 'TLPM_findRsrc':
            args[1]._obj.value = len(self.devices)
        elif name == 'TLPM_getRsrcName':
            self._write_string(args[2], self.devices[args[1].value]['resource'])
        elif name == 'TLPM_getRsrcInfo':
            device = self.devices[args[1].value]
            self._write_string(args[2], device['model'])
            self._write_string(args[3], device['serial'])
            self._write_string(args[4], device['manufacturer'])
            args[5]._obj.value = device['available']
        elif name == 'TLPM_init':
            if self.init_status < 0:
                return self.init_status
            args[3]._obj.value = 42
        elif name == 'TLPM_close':
            self.closed += 1
        elif name == 'TLPM_errorMessage':
            self._write_string(args[2], "fake TLPM error text")
        elif name == 'TLPM_identificationQuery':
            for buffer, text in zip(args[1:], ('Thorlabs', 'PM100USB', 'M0000001', '1.7.0')):
                self._write_string(buffer, text)
        elif name == 'TLPM_getSensorInfo':
            self._write_string(args[1], 'S144C')
            self._write_string(args[2], '260000000')
            self._write_string(args[3], '1-JAN-2026')
            args[4]._obj.value = 1
            args[5]._obj.value = 18
            args[6]._obj.value = self.state['sensor flags']
        elif name == 'TLPM_setWavelength':
            self.state['wavelength'] = args[1].value
        elif name == 'TLPM_getWavelength':
            attribute = args[1].value
            args[2]._obj.value = {0: self.state['wavelength'],
                                  1: self.state['wavelength min'],
                                  2: self.state['wavelength max']}[attribute]
        elif name == 'TLPM_setPowerUnit':
            self.state['unit'] = args[1].value
        elif name == 'TLPM_getPowerUnit':
            args[1]._obj.value = self.state['unit']
        elif name == 'TLPM_setAvgTime':
            self.state['avg time'] = args[1].value
        elif name == 'TLPM_getAvgTime':
            args[2]._obj.value = self.state['avg time']
        elif name == 'TLPM_setPowerAutoRange':
            self.state['autorange'] = args[1].value
        elif name == 'TLPM_getPowerAutorange':
            args[1]._obj.value = self.state['autorange']
        elif name == 'TLPM_setPowerRange':
            self.state['range'] = args[1].value
        elif name == 'TLPM_getPowerRange':
            args[2]._obj.value = self.state['range']
        elif name == 'TLPM_measPower':
            args[1]._obj.value = self.power
            return self.power_status
        return 0


class PowerMeterThorlabsPM100USBSoftwareTest(unittest.TestCase):
    """
    Tests for the PM100USB driver which need no instrument.

    Required lab setup: none, only SW testing
    """

    user_input_required = False

    def setUp(self):
        # the module caches sessions and libraries process-wide, and a test must not inherit
        # either from the one before it
        pm_module._SESSIONS.clear()
        self.addCleanup(pm_module._SESSIONS.clear)

    def make_meter(self, library=None, **kwargs):
        """A driver wired to a stub library, opened, with its close registered for cleanup."""
        library = library if library is not None else FakeTLPM()
        kwargs.setdefault('visa_address', 'None')
        meter = PowerMeterThorlabsPM100USB(**kwargs)
        meter._load_library = lambda: library
        meter.open()
        self.addCleanup(meter.close)
        return meter, library

    #
    # loading and opening
    #

    def test_a_missing_library_says_what_is_missing(self):
        meter = PowerMeterThorlabsPM100USB(visa_address='None',
                                           dll_path=r'C:\nowhere\TLPM_64.dll')
        with self.assertRaises(InstrumentException) as raised:
            meter.open()

        message = str(raised.exception)
        self.assertIn('TLPM', message)
        self.assertIn('nowhere', message)

    def test_a_failed_init_names_optical_power_monitor(self):
        """The likely cause of a refused open is the GUI holding the exclusive session."""
        library = FakeTLPM(init_status=-1074000000)
        meter = PowerMeterThorlabsPM100USB(visa_address='None')
        meter._load_library = lambda: library

        with self.assertRaises(InstrumentException) as raised:
            meter.open()

        self.assertIn('Optical Power Monitor', str(raised.exception))

    def test_no_devices_explains_that_ni_visa_cannot_see_the_meter(self):
        library = FakeTLPM(devices=[])
        meter = PowerMeterThorlabsPM100USB(visa_address='None')
        meter._load_library = lambda: library

        with self.assertRaises(InstrumentException) as raised:
            meter.open()

        self.assertIn('NI-VISA', str(raised.exception))

    def test_the_hardware_unit_is_pinned_to_watts_on_open(self):
        _, library = self.make_meter()

        self.assertEqual([0], [args[1].value for args in library.calls_to('TLPM_setPowerUnit')])

    def test_a_sensor_without_a_settable_wavelength_is_reported(self):
        library = FakeTLPM()
        library.state['sensor flags'] = 0  # wavelength flag clear
        meter, _ = self.make_meter(library=library)

        self.assertFalse(meter.sensor_info['wavelength settable'])

    #
    # device selection
    #

    def test_a_serial_picks_the_right_meter_out_of_several(self):
        library = FakeTLPM(devices=[
            {'resource': 'USB0::A::INSTR', 'model': 'PM100USB', 'serial': 'AAA',
             'manufacturer': 'Thorlabs', 'available': 1},
            {'resource': 'USB0::B::INSTR', 'model': 'PM100USB', 'serial': 'BBB',
             'manufacturer': 'Thorlabs', 'available': 1}])
        meter, _ = self.make_meter(library=library, serial='bbb')

        self.assertEqual('USB0::B::INSTR', meter._resource)

    def test_several_meters_and_no_serial_is_refused_rather_than_guessed(self):
        library = FakeTLPM(devices=[
            {'resource': 'USB0::A::INSTR', 'model': 'PM100USB', 'serial': 'AAA',
             'manufacturer': 'Thorlabs', 'available': 1},
            {'resource': 'USB0::B::INSTR', 'model': 'PM100USB', 'serial': 'BBB',
             'manufacturer': 'Thorlabs', 'available': 1}])
        meter = PowerMeterThorlabsPM100USB(visa_address='None')
        meter._load_library = lambda: library

        with self.assertRaises(InstrumentException) as raised:
            meter.open()

        message = str(raised.exception)
        self.assertIn('AAA', message)
        self.assertIn('BBB', message)

    def test_an_unknown_serial_lists_what_was_found(self):
        meter = PowerMeterThorlabsPM100USB(visa_address='None', serial='NOPE')
        meter._load_library = lambda: FakeTLPM()

        with self.assertRaises(InstrumentException) as raised:
            meter.open()

        self.assertIn('M0000001', str(raised.exception))

    def test_a_configured_resource_name_skips_the_search(self):
        library = FakeTLPM()
        meter = PowerMeterThorlabsPM100USB(visa_address='USB0::CONFIGURED::INSTR')
        meter._load_library = lambda: library
        meter.open()
        self.addCleanup(meter.close)

        self.assertEqual('USB0::CONFIGURED::INSTR', meter._resource)
        self.assertEqual([], library.calls_to('TLPM_findRsrc'))

    #
    # session sharing
    #

    def test_two_drivers_for_one_meter_share_a_session(self):
        library = FakeTLPM()
        first = PowerMeterThorlabsPM100USB(visa_address='None', serial='M0000001')
        first._load_library = lambda: library
        first.open()
        second = PowerMeterThorlabsPM100USB(visa_address='None', serial='M0000001')
        second._load_library = lambda: library
        second.open()

        self.assertEqual(1, len(library.calls_to('TLPM_init')))
        self.assertEqual(first._session, second._session)

        first.close()
        # the meter is still usable by the second holder
        self.assertEqual(0, library.closed)
        self.assertTrue(second._open)

        second.close()
        self.assertEqual(1, library.closed)

    def test_a_second_driver_finds_the_session_even_though_tlpm_hides_the_serial(self):
        """Once a meter is claimed, TLPM reports its serial as 'n/a' and drops it from the
        resource name, so a second driver can only recognise it from the session registry."""
        library = FakeTLPM()
        first = PowerMeterThorlabsPM100USB(visa_address='None', serial='M0000001')
        first._load_library = lambda: library
        first.open()
        self.addCleanup(first.close)

        # what TLPM actually does to a claimed device
        library.devices[0].update({'serial': 'n/a', 'available': 0,
                                   'resource': 'USB0::0x1313::0x8072::::INSTR'})

        second = PowerMeterThorlabsPM100USB(visa_address='None', serial='M0000001')
        second._load_library = lambda: library
        second.open()
        self.addCleanup(second.close)

        self.assertEqual(first._session, second._session)

    def test_close_is_idempotent(self):
        meter, library = self.make_meter()

        meter.close()
        meter.close()

        self.assertEqual(1, library.closed)

    #
    # readings
    #

    def test_a_reading_comes_back_in_watts(self):
        meter, _ = self.make_meter()

        self.assertAlmostEqual(1e-3, meter.read_power_watt(), places=12)

    def test_an_over_range_warning_is_flagged_rather_than_raised(self):
        library = FakeTLPM(power_status=VI_INSTR_WARN_OVERFLOW)
        meter, _ = self.make_meter(library=library)

        self.assertTrue(math.isnan(meter.read_power_watt()))
        self.assertTrue(meter.last_read_was_over_range())

    def test_the_over_range_sentinel_is_flagged_too(self):
        """TLPM reports an over-range reading as ~9.9e37 rather than as a status code."""
        meter, _ = self.make_meter(library=FakeTLPM(power=9.9e37))

        self.assertTrue(math.isnan(meter.read_power_watt()))
        self.assertTrue(meter.last_read_was_over_range())

    def test_a_nan_reading_is_flagged(self):
        meter, _ = self.make_meter(library=FakeTLPM(power=float('nan')))

        self.assertTrue(math.isnan(meter.read_power_watt()))
        self.assertTrue(meter.last_read_was_over_range())

    def test_a_negative_status_raises_with_the_vendor_text(self):
        library = FakeTLPM(power_status=-1074000000)
        meter, _ = self.make_meter(library=library)

        with self.assertRaises(InstrumentException) as raised:
            meter.read_power_watt()

        message = str(raised.exception)
        self.assertIn('fake TLPM error text', message)
        self.assertIn('TLPM_measPower', message)

    #
    # units
    #

    def test_dbm_is_computed_here_rather_than_by_the_meter(self):
        meter, library = self.make_meter()
        before = len(library.calls_to('TLPM_setPowerUnit'))

        meter.unit = 'dBm'

        self.assertEqual(before, len(library.calls_to('TLPM_setPowerUnit')))
        self.assertAlmostEqual(0.0, meter.power, places=9)  # 1 mW is 0 dBm

    def test_a_dark_reading_floors_instead_of_going_to_minus_infinity(self):
        meter, _ = self.make_meter(library=FakeTLPM(power=0.0))
        meter.unit = 'dBm'

        self.assertEqual(MINIMUM_DBM, meter.power)

    def test_a_negative_reading_floors_too(self):
        """A photodiode with no light on it reads slightly negative, which log() cannot take."""
        meter, _ = self.make_meter(library=FakeTLPM(power=-1.1e-8))
        meter.unit = 'dBm'

        self.assertEqual(MINIMUM_DBM, meter.power)

    def test_an_unreadable_reading_stays_nan_in_dbm_rather_than_flooring(self):
        """The floor means "no light"; nan means "no reading". Search for Peak tells them apart
        with np.isfinite to decide whether a range change is needed, so the distinction has to
        survive the watts-to-dBm conversion."""
        meter, _ = self.make_meter(library=FakeTLPM(power=9.9e37))
        meter.unit = 'dBm'

        self.assertTrue(math.isnan(meter.power))
        self.assertNotEqual(MINIMUM_DBM, meter.power)

    def test_an_unknown_unit_is_refused(self):
        meter, _ = self.make_meter()

        with self.assertRaises(InstrumentException):
            meter.unit = 'furlongs'

    #
    # range and wavelength
    #

    def test_a_range_in_dbm_is_sent_as_watts(self):
        """PeakSearcher and the Live Viewer both put dBm into `range`, so the driver reads it so."""
        meter, library = self.make_meter()

        meter.range = -20.0

        self.assertAlmostEqual(1e-5, library.calls_to('TLPM_setPowerRange')[-1][1].value,
                               places=12)
        self.assertEqual(0, library.calls_to('TLPM_setPowerAutoRange')[-1][1].value)

    def test_auto_switches_autoranging_back_on(self):
        meter, library = self.make_meter()

        meter.range = 'auto'

        self.assertEqual(1, library.calls_to('TLPM_setPowerAutoRange')[-1][1].value)
        self.assertEqual('auto', meter.range)

    def test_a_range_which_is_neither_a_number_nor_auto_is_refused(self):
        meter, _ = self.make_meter()

        with self.assertRaises(InstrumentException):
            meter.range = 'widest'

    def test_the_wavelength_round_trips(self):
        meter, _ = self.make_meter()

        meter.wavelength = 1310.0

        self.assertAlmostEqual(1310.0, meter.wavelength, places=9)

    def test_a_wavelength_outside_the_sensor_names_both_bounds(self):
        meter, _ = self.make_meter()

        with self.assertRaises(InstrumentException) as raised:
            meter.wavelength = 400.0

        message = str(raised.exception)
        self.assertIn('800', message)
        self.assertIn('1700', message)

    def test_the_averaging_time_round_trips(self):
        meter, _ = self.make_meter()

        meter.averagetime = 0.02

        self.assertAlmostEqual(0.02, meter.averagetime, places=9)

    #
    # metadata
    #

    def test_the_startup_arguments_are_applied(self):
        meter, _ = self.make_meter(average_time=0.05, wavelength=1310.0, autoranging=False)

        self.assertAlmostEqual(0.05, meter.averagetime, places=9)
        self.assertAlmostEqual(1310.0, meter.wavelength, places=9)
        self.assertFalse(meter.autoranging)

    def test_idn_names_the_meter_and_the_sensor(self):
        meter, _ = self.make_meter()

        identity = meter.idn()
        self.assertIn('PM100USB', identity)
        self.assertIn('S144C', identity)

    def test_idn_never_raises(self):
        """Metadata collection calls it unguarded, so a broken meter must not abort a run."""
        meter, library = self.make_meter()
        meter._session = None  # as if the meter had gone away mid-run

        self.assertIn('unavailable', meter.idn())

    def test_the_recorded_parameters_are_json_serialisable(self):
        meter, _ = self.make_meter()

        parameters = meter.get_instrument_parameter()
        self.assertEqual('Watt', parameters['hardware power unit'])
        self.assertTrue(json.dumps(parameters))

    def test_a_property_that_fails_is_recorded_rather_than_raised(self):
        meter, _ = self.make_meter()
        with mock.patch.object(type(meter), 'wavelength',
                               property(lambda self: 1 / 0)):
            parameters = meter.get_instrument_parameter()

        self.assertIn('ERROR', parameters['wavelength'])


@mark_as_laboratory_test
class PowerMeterThorlabsPM100USBLabTest(unittest.TestCase):
    """
    Tests for the PM100USB driver against a real meter.

    Required lab setup: a Thorlabs PM100USB with a sensor attached over USB, a Thorlabs TLPM
    installation, and Thorlabs Optical Power Monitor closed.
    """

    user_input_required = False

    @classmethod
    def setUpClass(cls):
        cls.meter = PowerMeterThorlabsPM100USB(visa_address='None')
        cls.meter.open()

    @classmethod
    def tearDownClass(cls):
        cls.meter.close()

    def test_idn_reports_thorlabs(self):
        self.assertIn('Thorlabs', self.meter.idn())

    def test_the_sensor_identifies_itself(self):
        info = self.meter.sensor_info
        self.assertTrue(info['name'])
        self.assertTrue(info['wavelength settable'],
                        "this driver's wavelength tracking needs a sensor which accepts one")

    def test_the_wavelength_round_trips(self):
        low, high = self.meter.wavelength_range
        self.assertLess(low, high)
        target = round((low + high) / 2.0)
        original = self.meter.wavelength
        try:
            self.meter.wavelength = float(target)
            self.assertAlmostEqual(float(target), self.meter.wavelength, places=3)
        finally:
            self.meter.wavelength = original

    def test_a_reading_is_a_finite_number_of_watts(self):
        reading = self.meter.read_power_watt()
        self.assertTrue(math.isfinite(reading), "reading came back over range or invalid")

    def test_autoranging_round_trips(self):
        original = self.meter.autoranging
        try:
            self.meter.autoranging = not original
            self.assertEqual(not original, self.meter.autoranging)
        finally:
            self.meter.autoranging = original

    def test_a_read_is_quick_enough_for_one_per_bracket(self):
        """The time budget a sweep is planned against, asserted rather than assumed."""
        original = self.meter.averagetime
        try:
            self.meter.averagetime = 0.01
            self.meter.read_power_watt()  # discard the first, which may straddle a range change
            started = time.perf_counter()
            for _ in range(5):
                self.meter.read_power_watt()
            per_read = (time.perf_counter() - started) / 5
        finally:
            self.meter.averagetime = original

        self.assertLess(per_read, 0.1,
                        "a read took {:.1f} ms, which makes one per bracket expensive".format(
                            1000 * per_read))

    def test_a_second_driver_shares_the_session(self):
        second = PowerMeterThorlabsPM100USB(visa_address='None')
        second.open()
        try:
            self.assertEqual(self.meter._session, second._session)
            self.assertTrue(math.isfinite(second.read_power_watt()))
        finally:
            second.close()
        # the shared session survives the second holder letting go
        self.assertTrue(math.isfinite(self.meter.read_power_watt()))


if __name__ == '__main__':
    unittest.main()
