#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
LabExT  Copyright (C) 2021  ETH Zurich and Polariton Technologies AG
This program is free software and comes with ABSOLUTELY NO WARRANTY; for details see LICENSE file.

Required lab setup:
 none - the LJM library is replaced by a fake here, so these run anywhere.
"""

import unittest
from unittest.mock import patch

try:
    from LabExT.Instruments.LabJack import (LabJack, STREAM_IS_ACTIVE_CODE,
                                            STREAM_NOT_RUNNING_CODES)
except ImportError:  # pragma: no cover - the LJM bindings are not installed everywhere
    LabJack = None


class FakeLJMError(Exception):
    def __init__(self, error_code):
        super().__init__("fake LJM error %d" % error_code)
        self.errorCode = error_code


class FakeErrorCodes:
    STREAM_NOT_RUNNING = STREAM_NOT_RUNNING_CODES[0] if LabJack is not None else 1303
    NO_SCANS_RETURNED = 1301


class FakeLJM:
    """Records the LJM calls a stream session makes, in the order it makes them."""

    LJMError = FakeLJMError
    errorcodes = FakeErrorCodes

    def __init__(self, read_results):
        self.calls = []
        self.read_results = list(read_results)
        self.next_handle = 1
        self.open_error = None
        self.stop_error = None
        self.write_errors = []

    # connection handling

    def openS(self, *args):
        self.calls.append("openS")
        if self.open_error is not None:
            raise self.open_error
        handle, self.next_handle = self.next_handle, self.next_handle + 1
        return handle

    def getHandleInfo(self, handle):
        return (7, 1, 470000000 + handle, 0, 0, 64)

    def close(self, handle):
        self.calls.append("close %d" % handle)

    def closeAll(self):
        self.calls.append("closeAll")

    # registers

    def eWriteNames(self, handle, num_frames, names, values):
        self.calls.append("eWriteNames %d %s" % (handle, list(zip(names, values))))
        if self.write_errors:
            error = self.write_errors.pop(0)
            if error is not None:
                raise error

    # streaming

    def eStreamStop(self, handle):
        self.calls.append("eStreamStop %d" % handle)
        if self.stop_error is not None:
            raise self.stop_error

    def eStreamRead(self, handle):
        self.calls.append("eStreamRead %d" % handle)
        result = self.read_results.pop(0)
        if isinstance(result, Exception):
            raise result
        return result

    def eReadName(self, handle, port):
        self.calls.append("eReadName %d %s" % (handle, port))
        return 1.0


def one_scan_block(value):
    """A single-channel eStreamRead return value: (data, device backlog, LJM backlog)."""
    return ([value], 0, 0)


@unittest.skipIf(LabJack is None, "the LJM bindings are not installed")
class LabJackStreamSessionTest(unittest.TestCase):
    """The stream, trigger and connection handling around a stream session.

    The recording stops on the scans the measurement asked for, which lands while the laser
    still has a fraction of a second of sweep - and so of trigger pulses - to go. A trigger
    left armed over the stopped stream let the device start streaming again on its own, which
    showed up as a stop LJM called clean followed by a device that was still streaming, and as
    packets matching no request on the next session.
    """

    def _labjack(self, read_results):
        fake = FakeLJM(read_results)
        with patch("LabExT.Instruments.LabJack.ljm", fake):
            labjack = LabJack()
        labjack.TRIGGER_NAME = "DIO1"  # set by configure_device_for_triggered_stream()
        fake.calls.clear()
        return labjack, fake

    def _start_logging(self, labjack, fake, max_requests):
        with patch("LabExT.Instruments.LabJack.ljm", fake):
            return labjack.start_logging(max_requests, 1, 1000.0, ["AIN0"], 1, max_requests)

    def test_finished_session_stops_the_stream_and_disarms_the_trigger(self):
        labjack, fake = self._labjack([one_scan_block(0.5), one_scan_block(0.6)])

        data = self._start_logging(labjack, fake, 2)

        self.assertEqual([0.5, 0.6], data[0].tolist())
        self.assertEqual(
            ["eStreamRead 1", "eStreamRead 1", "eStreamStop 1",
             "eWriteNames 1 [('STREAM_TRIGGER_INDEX', 0), ('DIO1_EF_ENABLE', 0)]",
             "eStreamStop 1"],
            fake.calls)
        # a session that ended cleanly keeps its connection: a new handle resets the
        # transaction ids LJM expects, and a packet still on its way from the session just
        # ended then reads as a mismatch on the first read of the next one
        self.assertEqual(1, labjack.handle)

    def test_a_stream_the_device_restarted_is_stopped_and_disarmed(self):
        labjack, fake = self._labjack([one_scan_block(0.5)])
        # the device answers the first disarm with STREAM_IS_ACTIVE: a trigger pulse got in
        # between the stop and the disarm and started it streaming again
        fake.write_errors = [FakeLJMError(STREAM_IS_ACTIVE_CODE), None]

        self._start_logging(labjack, fake, 1)

        self.assertEqual(
            ["eStreamRead 1", "eStreamStop 1",
             "eWriteNames 1 [('STREAM_TRIGGER_INDEX', 0), ('DIO1_EF_ENABLE', 0)]",
             "eStreamStop 1",
             "eWriteNames 1 [('STREAM_TRIGGER_INDEX', 0), ('DIO1_EF_ENABLE', 0)]",
             "eStreamStop 1"],
            fake.calls)
        self.assertEqual(1, labjack.handle)

    def test_failed_read_reconnects_and_reports_the_error(self):
        labjack, fake = self._labjack([one_scan_block(0.5), FakeLJMError(1279)])

        with self.assertRaises(FakeLJMError) as raised:
            self._start_logging(labjack, fake, 2)

        self.assertEqual(1279, raised.exception.errorCode)
        # out of step with the device, and nothing sent over this connection puts it back
        self.assertIn("close 1", fake.calls)
        self.assertEqual(2, labjack.handle)
        self.assertEqual("eStreamStop 2", fake.calls[-1])

    def test_a_stop_that_is_refused_does_not_hide_the_read_error(self):
        labjack, fake = self._labjack([FakeLJMError(1279)])
        fake.stop_error = FakeLJMError(STREAM_IS_ACTIVE_CODE)

        with self.assertRaises(FakeLJMError) as raised:
            self._start_logging(labjack, fake, 1)

        self.assertEqual(1279, raised.exception.errorCode)

    def test_a_reopen_that_fails_keeps_the_data_and_reopens_later(self):
        labjack, fake = self._labjack([one_scan_block(0.5)])
        # the teardown itself fails, so the connection is dropped - and cannot be got back
        fake.write_errors = [FakeLJMError(1279)]
        fake.open_error = FakeLJMError(1314)  # NO_DEVICES_FOUND

        data = self._start_logging(labjack, fake, 1)

        self.assertEqual([0.5], data[0].tolist())
        self.assertIsNone(labjack.handle)

        # the connection comes back on the next use rather than staying broken
        fake.open_error = None
        with patch("LabExT.Instruments.LabJack.ljm", fake):
            labjack.read_from_port("AIN0")
        self.assertIsNotNone(labjack.handle)

    def test_a_stop_of_an_idle_stream_is_not_an_error(self):
        labjack, fake = self._labjack([])
        fake.stop_error = FakeLJMError(STREAM_NOT_RUNNING_CODES[1])

        with patch("LabExT.Instruments.LabJack.ljm", fake):
            labjack.stop_stream()


@unittest.skipIf(LabJack is None, "the LJM bindings are not installed")
class LabJackResetTest(unittest.TestCase):
    """The reset a failed sweep asks for before it is recorded again."""

    def _labjack(self):
        fake = FakeLJM([])
        with patch("LabExT.Instruments.LabJack.ljm", fake):
            labjack = LabJack()
        fake.calls.clear()
        return labjack, fake

    def test_reset_drops_every_connection_and_opens_a_new_one(self):
        labjack, fake = self._labjack()

        with patch("LabExT.Instruments.LabJack.ljm", fake),                 patch("LabExT.Instruments.LabJack.time.sleep") as settle:
            labjack.reset("testing")

        # closeAll, not close: a handle this object lost track of would keep the device busy
        self.assertEqual(["closeAll", "openS"], fake.calls)
        self.assertEqual(2, labjack.handle)
        settle.assert_called_once()

    def test_reset_opens_a_new_connection_even_if_closing_refused(self):
        labjack, fake = self._labjack()
        fake.closeAll = lambda: (_ for _ in ()).throw(FakeLJMError(1230))

        with patch("LabExT.Instruments.LabJack.ljm", fake),                 patch("LabExT.Instruments.LabJack.time.sleep"):
            labjack.reset("testing")

        self.assertEqual(2, labjack.handle)

    def test_only_the_stream_errors_that_say_nothing_about_the_sweep_are_recoverable(self):
        with patch("LabExT.Instruments.LabJack.ljm", FakeLJM([])):
            self.assertTrue(LabJack.is_recoverable_stream_error(FakeLJMError(1279)))
            self.assertTrue(LabJack.is_recoverable_stream_error(FakeLJMError(STREAM_IS_ACTIVE_CODE)))
            self.assertFalse(LabJack.is_recoverable_stream_error(FakeLJMError(1314)))
            self.assertFalse(LabJack.is_recoverable_stream_error(ValueError("not LJM at all")))


if __name__ == "__main__":
    unittest.main()
