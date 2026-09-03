#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
LabExT  Copyright (C) 2021  ETH Zurich and Polariton Technologies AG
This program is free software and comes with ABSOLUTELY NO WARRANTY; for details see LICENSE file.

Required lab setup:
 none - no instrument is touched, only the decision to sweep again.
"""

import unittest

from LabExT.Measurements.IL_sweep_switch import IL_sweep_switch, STREAM_ATTEMPTS


class StreamError(Exception):
    """Stands in for the LJM error a broken stream raises."""


class FakeLabJack:
    """Answers the two questions the retry asks of the LabJack, and counts the resets."""

    def __init__(self):
        self.resets = []

    @staticmethod
    def is_recoverable_stream_error(err):
        return isinstance(err, StreamError)

    def reset(self, reason):
        self.resets.append(reason)


class ILSweepSwitchRetryTest(unittest.TestCase):
    """Sweeping again after a stream failure.

    A stream that fails says the connection to the LabJack is out of step, not that anything
    is wrong with the device under test - so the sweep is taken again rather than costing the
    queue an entry and the operator a restart of LabExT.
    """

    def setUp(self):
        self.meas = IL_sweep_switch()
        self.meas.lj = FakeLabJack()
        self.sweeps = 0

    def _sweep(self, fail_times, error=None):
        error = error if error is not None else StreamError("out of step")

        def record_sweep():
            self.sweeps += 1
            if self.sweeps <= fail_times:
                raise error
            return "power data"

        return record_sweep

    def test_a_sweep_that_works_is_not_repeated(self):
        data, attempts = self.meas._record_with_stream_retries(self._sweep(fail_times=0))

        self.assertEqual("power data", data)
        self.assertEqual(1, attempts)
        self.assertEqual(1, self.sweeps)
        self.assertEqual([], self.meas.lj.resets)

    def test_a_broken_stream_resets_the_labjack_and_sweeps_again(self):
        data, attempts = self.meas._record_with_stream_retries(self._sweep(fail_times=1))

        self.assertEqual("power data", data)
        self.assertEqual(2, attempts)
        self.assertEqual(2, self.sweeps)
        self.assertEqual(1, len(self.meas.lj.resets))

    def test_a_stream_that_never_recovers_reports_the_failure(self):
        with self.assertRaises(StreamError):
            self.meas._record_with_stream_retries(self._sweep(fail_times=STREAM_ATTEMPTS))

        self.assertEqual(STREAM_ATTEMPTS, self.sweeps)
        # the last attempt reports rather than resetting for a sweep nobody will take
        self.assertEqual(STREAM_ATTEMPTS - 1, len(self.meas.lj.resets))

    def test_a_failure_that_is_not_the_stream_is_reported_at_once(self):
        with self.assertRaises(ValueError):
            self.meas._record_with_stream_retries(
                self._sweep(fail_times=1, error=ValueError("the laser refused its settings")))

        self.assertEqual(1, self.sweeps)
        self.assertEqual([], self.meas.lj.resets)


if __name__ == "__main__":
    unittest.main()
