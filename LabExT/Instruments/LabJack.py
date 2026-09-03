#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
LabExT  Copyright (C) 2021  ETH Zurich and Polariton Technologies AG
This program is free software and comes with ABSOLUTELY NO WARRANTY; for details see LICENSE file.
"""

import logging
import threading

from labjack import ljm
import time
import sys
import numpy as np

# LJM reports "no stream is running" under two different codes: 1303 from the library and
# 2620 from the T-series device itself. Only the first has a name in ljm.errorcodes, and a
# stop on an idle device raises the second, so both have to count as "nothing to stop".
STREAM_NOT_RUNNING_CODES = (ljm.errorcodes.STREAM_NOT_RUNNING, 2620)

# how a handle describes itself, for the log. USB and Ethernet carry stream data differently,
# so which one is in use is the first thing worth knowing about a stream that broke.
_DEVICE_NAMES = {getattr(ljm.constants, n): n[2:] for n in ("dtT4", "dtT7", "dtT8", "dtDIGIT")
                 if hasattr(ljm.constants, n)}
_CONNECTION_NAMES = {getattr(ljm.constants, n): n[2:] for n in ("ctUSB", "ctTCP", "ctETHERNET", "ctWIFI")
                     if hasattr(ljm.constants, n)}
_USB_CONNECTION = getattr(ljm.constants, "ctUSB", None)


class LabJack:
    def __init__(self):
        self.logger = logging.getLogger()
        # every Koheron power meter shares this one object and reads self.handle when it needs
        # it, so a reconnect swapping the handle out has to be serialised against itself
        self._handle_lock = threading.Lock()
        self.handle = None
        self.open()

    def open(self):
        self.handle = ljm.openS("ANY", "ANY", "ANY")
        self.info = ljm.getHandleInfo(self.handle)
        # logged at info on purpose: the log file is written at info level, so a debug line
        # here never reaches it, and a second connection to the one device - which is what
        # makes LJM lose track of which response belongs to which request - is only visible
        # as a second handle appearing in the log
        self.logger.info("opened LabJack handle %s: %s", self.handle, self.describe())

    def close(self):
        if self.handle is None:
            return
        handle, self.handle = self.handle, None
        ljm.close(handle)
        self.logger.debug("closed LabJack handle %s", handle)

    def describe(self):
        """Names the device behind the current handle, and the link it is reached over."""
        device_type, connection_type, serial, ip, port, _ = self.info
        described = "%s serial %s over %s" % (_DEVICE_NAMES.get(device_type, device_type),
                                              serial,
                                              _CONNECTION_NAMES.get(connection_type, connection_type))
        if connection_type != _USB_CONNECTION:
            described += " at %s:%s" % (ljm.numberToIP(ip), port)
        return described

    def reconnect(self, reason):
        """Drops the connection to the LabJack and opens a fresh one.

        LJM pairs every response with the request that asked for it by transaction id, and a
        mismatch (LJME_TRANSACTION_ID_ERR) means the traffic on the connection has got out of
        step - a stream packet from an earlier session still in the pipe, a device that kept
        streaming after a stop went missing, or a dropped packet. None of that can be undone
        on the connection it happened on: only a new handle starts from an empty receive
        buffer. Reopening also costs nothing between measurements, and unlike a second
        parallel connection it leaves exactly one handle on the device.
        """
        with self._handle_lock:
            self.logger.debug("reconnecting to the LabJack: %s", reason)
            try:
                self.close()
            except Exception:
                # the connection is being thrown away anyway, so a refused close is not a
                # reason to abandon the reopen below
                self.logger.exception("could not close the LabJack handle before reopening it")
                self.handle = None
            self.open()

    def _ensure_handle(self):
        """Opens a connection if a previous reconnect could not get one back."""
        if self.handle is None:
            self.open()

    def read_from_port(self, port):
        self._ensure_handle()
        return ljm.eReadName(self.handle, port)

    def configure_device_for_triggered_stream(self):
        """Configure the device to wait for a trigger before beginning stream.

        @para handle: The device handle
        @type handle: int
        @para triggerName: The name of the channel that will trigger stream to start
        @type triggerName: str
        """
        self.TRIGGER_NAME = "DIO1"
        address = ljm.nameToAddress(self.TRIGGER_NAME)[0]
        ljm.eWriteName(self.handle, "STREAM_TRIGGER_INDEX", address)

        # Clear any previous settings on triggerName's Extended Feature registers
        ljm.eWriteName(self.handle, "%s_EF_ENABLE" % self.TRIGGER_NAME, 0)

        # 5 enables a rising or falling edge to trigger stream
        ljm.eWriteName(self.handle, "%s_EF_INDEX" % self.TRIGGER_NAME, 12)

        # Enable
        ljm.eWriteName(self.handle, "%s_EF_ENABLE" % self.TRIGGER_NAME, 1)

    def configure_ljm_for_triggered_stream(self):
        ljm.writeLibraryConfigS(ljm.constants.STREAM_SCANS_RETURN, ljm.constants.STREAM_SCANS_RETURN_ALL_OR_NONE)
        ljm.writeLibraryConfigS(ljm.constants.STREAM_RECEIVE_TIMEOUT_MS, 0)
        # By default, LJM will time out with an error while waiting for the stream
        # trigger to occur.

    def init_triggered_stream(self):
        self._ensure_handle()

        # Ensure triggered stream is disabled.
        ljm.eWriteName(self.handle, "STREAM_TRIGGER_INDEX", 0)

        # Enabling internally-clocked stream.
        ljm.eWriteName(self.handle, "STREAM_CLOCK_SOURCE", 0)

        # All negative channels are single-ended, AIN0 and AIN1 ranges are
        # +/-10 V, stream settling is 0 (default) and stream resolution index
        # is 0 (default).
        aNames = ["AIN_ALL_NEGATIVE_CH", "AIN0_RANGE", "AIN1_RANGE",
                "STREAM_SETTLING_US", "STREAM_RESOLUTION_INDEX"]
        aValues = [ljm.constants.GND, 10.0, 10.0, 0, 0]

        numFrames = len(aNames)
        ljm.eWriteNames(self.handle, numFrames, aNames, aValues)

        self.configure_device_for_triggered_stream()
        self.configure_ljm_for_triggered_stream()

    def stop_stream(self):
        """Stops the stream if one is running. Safe to call when none is."""
        try:
            ljm.eStreamStop(self.handle)
        except ljm.LJMError as err:
            if err.errorCode not in STREAM_NOT_RUNNING_CODES:
                raise

    def start_stream(self, scans_per_read, nc, a_scan_list, scan_rate):
        self._ensure_handle()
        # a stream left running by an earlier measurement that raised before stopping it
        # makes every later one fail with STREAM_IS_ACTIVE, which reads like a wiring fault
        # rather than leftover state, so clear it here instead of requiring a replug
        self.stop_stream()
        return ljm.eStreamStart(self.handle, scans_per_read, nc, a_scan_list, scan_rate)

    def make_scan_list(self, nc, channels):
        return ljm.namesToAddresses(nc, channels)[0]

    def calculate_sleep_factor(self, scansPerRead, LJMScanBacklog):
        """Calculates how much sleep should be done based on how far behind stream is.

        @para scansPerRead: The number of scans returned by a eStreamRead call
        @type scansPerRead: int
        @para LJMScanBacklog: The number of backlogged scans in the LJM buffer
        @type LJMScanBacklog: int
        @return: A factor that should be multiplied the normal sleep time
        @type: float
        """
        DECREASE_TOTAL = 0.9
        portionScansReady = float(LJMScanBacklog) / scansPerRead
        if (portionScansReady > DECREASE_TOTAL):
            return 0.0
        return (1 - portionScansReady) * DECREASE_TOTAL

    def variable_stream_sleep(self, scansPerRead, scanRate, LJMScanBacklog):
        """Sleeps for approximately the expected amount of time until the next scan
        is ready to be read.

        @para scansPerRead: The number of scans returned by a eStreamRead call
        @type scansPerRead: int
        @para scanRate: The stream scan rate
        @type scanRate: numerical
        @para LJMScanBacklog: The number of backlogged scans in the LJM buffer
        @type LJMScanBacklog: int
        """
        sleepFactor = self.calculate_sleep_factor(scansPerRead, LJMScanBacklog)
        sleepTime = sleepFactor * scansPerRead / float(scanRate)
        time.sleep(sleepTime)

    def start_logging(self, max_requests, scans_per_read, new_scan_rate, channels:list, nc: int, vector_length):
        global_data = []
        stats = {'reads': 0, 'scans': 0, 'skipped': 0, 'device backlog': 0, 'LJM backlog': 0}

        try:
            self._read_stream_into(global_data, max_requests, scans_per_read,
                                   new_scan_rate, channels, nc, stats)
        finally:
            # how far the stream got is the difference between a connection or trigger that
            # never delivered anything and one that broke partway through, and the traceback
            # alone does not say which, so record it either way
            self.logger.info(
                "LabJack stream on handle %s stopped after %s of %s reads: %s scans, %s skipped, "
                "backlog at last read: device %s, LJM %s",
                self.handle, stats['reads'], int(max_requests), int(stats['scans']),
                stats['skipped'], stats['device backlog'], stats['LJM backlog'])
            self._end_stream_session()

        global_data = np.atleast_2d(np.concatenate(global_data)).T
        # throw away garbage data
        global_data = global_data[:, 0:vector_length]

        return global_data

    def _end_stream_session(self):
        """Stops the stream and leaves the next one a connection it can trust.

        Called however the session ended: the stream has to be stopped whatever happened, so
        that a failure here cannot stop the next measurement from ever starting one.
        """
        try:
            self.stop_stream()
        except Exception:
            self.logger.exception("could not stop the LabJack stream")

        try:
            self.reconnect("stream session finished")
        except Exception:
            # the data read before this point is good and is about to be returned, so a
            # connection that will not come back must not take it down with it - the next
            # call reopens (see _ensure_handle)
            self.logger.exception("could not reopen the LabJack after the stream session")
            return

        # the stop above went out on the connection the stream ran on, and a stream that
        # broke that connection can swallow it - LJM then reports a clean stop while the
        # device keeps streaming, and every later measurement fails at once with
        # STREAM_IS_ACTIVE. Repeating it on the new handle is a no-op if the first landed.
        try:
            self.stop_stream()
        except Exception:
            self.logger.exception("could not stop the LabJack stream after reconnecting")

    def _read_stream_into(self, global_data, max_requests, scans_per_read, new_scan_rate,
                          channels, nc, stats):
        """Reads `max_requests` blocks off the running stream, appending each to global_data.

        `stats` is filled in as the reads happen rather than returned, so that a read which
        raises still leaves behind how much of the stream had arrived before it did.
        """
        i = 1
        ljmScanBacklog = 0

        while i <= max_requests:
            self.variable_stream_sleep(scans_per_read, new_scan_rate, ljmScanBacklog)
            try:
                ret = ljm.eStreamRead(self.handle)
                aData = ret[0]
                ljmScanBacklog = ret[2]
                scans = len(aData) / nc
                temp = np.array(aData)
                temp=temp.reshape(scans_per_read, nc,order='C')
                global_data.append(temp)
                # Count the skipped samples which are indicated by -9999 values. Missed
                # samples occur after a device's stream buffer overflows and are
                # reported after auto-recover mode ends.
                curSkip = aData.count(-9999.0)

                stats['reads'] = i
                stats['scans'] += scans
                stats['skipped'] += curSkip
                stats['device backlog'] = ret[1]
                stats['LJM backlog'] = ljmScanBacklog

                self.logger.debug(
                    "eStreamRead %i: 1st scan out of %i is %s, scans skipped = %0.0f, "
                    "backlog: device = %i, LJM = %i",
                    i, scans,
                    ", ".join("%s = %0.5f" % (channels[j], aData[j]) for j in range(0, nc)),
                    curSkip / nc, ret[1], ljmScanBacklog)
                i += 1
            except ljm.LJMError as err:
                if err.errorCode == ljm.errorcodes.NO_SCANS_RETURNED:
                    sys.stdout.write('.')
                    sys.stdout.flush()
                    continue
                else:
                    raise err

