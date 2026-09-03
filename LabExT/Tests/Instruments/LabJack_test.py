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
    from LabExT.Instruments.LabJack import LabJack, STREAM_NOT_RUNNING_CODES
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
    """The connection handling around a stream session.

    A stream that raises used to leave the device streaming and LJM out of step with it,
    which cost every later measurement in the queue, not just the one that failed.
    """

    def _labjack(self, read_results):
        fake = FakeLJM(read_results)
        with patch("LabExT.Instruments.LabJack.ljm", fake):
            labjack = LabJack()
        return labjack, fake

    def _start_logging(self, labjack, fake, max_requests):
        with patch("LabExT.Instruments.LabJack.ljm", fake):
            return labjack.start_logging(max_requests, 1, 1000.0, ["AIN0"], 1, max_requests)

    def test_finished_session_stops_the_stream_and_reconnects(self):
        labjack, fake = self._labjack([one_scan_block(0.5), one_scan_block(0.6)])
        fake.calls.clear()

        data = self._start_logging(labjack, fake, 2)

        self.assertEqual([0.5, 0.6], data[0].tolist())
        # stopped on the connection the stream ran on, then again on the fresh one: a stop
        # that never reached the device leaves it streaming, and the second stop is what
        # keeps that from costing the next measurement
        self.assertEqual(["eStreamRead 1", "eStreamRead 1", "eStreamStop 1", "close 1",
                          "openS", "eStreamStop 2"], fake.calls)
        self.assertEqual(2, labjack.handle)

    def test_failed_read_reconnects_and_reports_the_error(self):
        labjack, fake = self._labjack([one_scan_block(0.5), FakeLJMError(1279)])
        fake.calls.clear()

        with self.assertRaises(FakeLJMError) as raised:
            self._start_logging(labjack, fake, 2)

        self.assertEqual(1279, raised.exception.errorCode)
        self.assertIn("close 1", fake.calls)
        self.assertEqual("eStreamStop 2", fake.calls[-1])

    def test_a_stop_that_is_refused_does_not_hide_the_read_error(self):
        labjack, fake = self._labjack([FakeLJMError(1279)])
        fake.stop_error = FakeLJMError(2605)
        fake.calls.clear()

        with self.assertRaises(FakeLJMError) as raised:
            self._start_logging(labjack, fake, 1)

        self.assertEqual(1279, raised.exception.errorCode)

    def test_a_reopen_that_fails_keeps_the_data_and_reopens_later(self):
        labjack, fake = self._labjack([one_scan_block(0.5)])
        fake.calls.clear()
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


if __name__ == "__main__":
    unittest.main()
