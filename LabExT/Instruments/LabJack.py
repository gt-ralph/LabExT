#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
LabExT  Copyright (C) 2021  ETH Zurich and Polariton Technologies AG
This program is free software and comes with ABSOLUTELY NO WARRANTY; for details see LICENSE file.
"""

import threading

from LabExT.Instruments.InstrumentAPI import Instrument

from labjack import ljm
import time
import sys
import numpy as np

class LabJack(Instrument):
    """
    ## Dummy Instrument

    This class "plays" as if it's an instrument, similar to
    [mocking](https://docs.python.org/3.7/library/unittest.mock.html). If you want to make instruments optional in your
    measurement classes, you can use this instrument and setting / getting properties will not error. This helps
    avoiding enable checks everywhere in your measurement code.

    To be used e.g. to make the "SMU" instrument optional by a parameter setting:
    ```
        if self.use_smu:
            self.instr_smu = self.get_instrument('SMU')
        else:
            self.instr_smu = DummyInstrument()
    ```

    Later, this line returns either None or an actual measurement:
    ```
        t = self.instr_smu.spot_measurement()
    ```
    without having to do another check to self.use_smu.
    """

    def __init__(self, *args, **kwargs):
        super().__init__(visa_address="")
        self.handle = None
        self._dummy_open = False

    def __getattr__(self, item):
        """
        This function is called when we want to get an attribute which does not exist.
        So you can access any non-existing attribute of this class and will get a None-returning function back.
        """
        self.logger.debug(f"DummyInstrument reading attribute {item:s}, returning None-fct.")

        def dummy_fct(*args, **kwargs):
            return None

        return dummy_fct

    def __enter__(self):
        """
        Makes this class a context manager which does simply nothing.
        """
        self.logger.debug("DummyInstrument entering context.")

    def __exit__(self, exc_type, exc_val, exc_tb):
        """
        Makes this class a context manager which does simply nothing.
        """
        self.logger.debug("DummyInstrument exiting context.")

    def get_instrument_parameter(self):
        return {'idn': self.idn()}

    @Instrument._open.getter  # weird way to override the parent's class property getter
    def _open(self):
        return self._dummy_open

    def open(self):
        self.handle = ljm.openS("ANY", "ANY", "ANY")
        self.info = ljm.getHandleInfo(self.handle)

    def close(self):
        ljm.close(self.handle)

    #
    #   LabJack Functions
    #
    def read_from_port(self, port):
        return ljm.eReadName(self.handle, port)

    # def triggered_sweep_init(
    #     start_wavelength_nm = 1510,
    #     stop_wavelength_nm = 1630,
    #     sweep_speed_nm_per_sec = 10,           # nm/s ()
    #     scanRate = 1000,
    #     pwr_in = 0,                      # pts/sec/channel 
    #     channels = ["AIN0"],
    #     verbose = True,
    #     nbr_cycles = 1
    # ):

    #     self.start_wavelength_nm = start_wavelength_nm
    #     self.stop_wavelength_nm = stop_wavelength_nm
    #     self.sweep_speed_nm_per_sec = sweep_speed_nm_per_sec
    #     self.sweep_cycles = nbr_cycles
    #     self.scanRate = scanRate
    #     self.channels = channels
    #     self.nc = len(channels)
    #     self.verbose = verbose
    #     self.pwr_in = pwr_in

    #     self.scansPerRead = int(scanRate)
    #     self.scanRate = int(scanRate)

    #     self.speed = (self.stop_wavelength_nm-self.start_wavelength_nm) / self.sweep_speed_nm_per_sec
    #     self.vector_length = int(self.scansPerRead*self.speed)
    #     self.wavelength = np.linspace(self.start_wavelength_nm,self.stop_wavelength_nm,self.vector_length)
    #     self.MAX_REQUESTS = np.ceil(self.speed)  # The number of eStreamRead calls that will be performed.

    #     self.aScanList = ljm.namesToAddresses(self.nc, self.channels)[0]
    #     self.TRIGGER_NAME = "DIO0"

    def configure_device_for_triggered_stream(self):
        """Configure the device to wait for a trigger before beginning stream.

        @para handle: The device handle
        @type handle: int
        @para triggerName: The name of the channel that will trigger stream to start
        @type triggerName: str
        """
        self.TRIGGER_NAME = "DIO0"
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

    def start_stream(self, scans_per_read, nc, a_scan_list, scan_rate):
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

    def start_logging(self, max_requests, scans_per_read, new_scan_rate, nc, vector_length):
        global_data = []

        totScans = 0
        totSkip = 0  # Total skipped samples

        i = 1
        ljmScanBacklog = 0

        while i <= max_requests:
            self.variable_stream_sleep(scans_per_read, new_scan_rate, ljmScanBacklog)
            try:
                ret = ljm.eStreamRead(self.handle)
                aData = ret[0]
                ljmScanBacklog = ret[2]
                scans = len(aData) / nc
                totScans += scans
                print(f'totScans: {totScans}')
                temp = np.array(aData)
                temp=temp.reshape(scans_per_read, nc,order='C')
                global_data.append(temp)
                # Count the skipped samples which are indicated by -9999 values. Missed
                # samples occur after a device's stream buffer overflows and are
                # reported after auto-recover mode ends.
                curSkip = aData.count(-9999.0)
                totSkip += curSkip
            
                # if self.verbose:
                print("\neStreamRead %i" % i)
                ainStr = ""
                channels=[0,1,2]
                for j in range(0, nc):
                    ainStr += "%s = %0.5f, " % (channels[j], aData[j])
                print("  1st scan out of %i: %s" % (scans, ainStr))
                print("  Scans Skipped = %0.0f, Scan Backlogs: Device = %i, LJM = "
                    "%i" % (curSkip/nc, ret[1], ljmScanBacklog))
                i += 1
            except ljm.LJMError as err:
                if err.errorCode == ljm.errorcodes.NO_SCANS_RETURNED:
                    sys.stdout.write('.')
                    sys.stdout.flush()
                    continue
                else:
                    raise err

        try:
            if self.verbose:
                print("\nStop Stream")
            ljm.eStreamStop(self.handle)
        except ljm.LJMError:
            ljme = sys.exc_info()[1]
            print(ljme)
        except Exception:
            e = sys.exc_info()[1]
            print(e)

        global_data = np.atleast_2d(np.concatenate(global_data)).T
        # throw away garbage data
        global_data = global_data[0:vector_length,:]

        return global_data

    @Instrument.thread_lock.getter  # weird way to override the parent's class property getter
    def thread_lock(self):
        return threading.Lock()

    def clear(self):
        return None

    def idn(self):
        return "LabJack class"

    def reset(self):
        return None

    def ready_check_sync(self):
        return True

    def ready_check_async_setup(self):
        return None

    def ready_check_async(self):
        return True

    def check_instrument_errors(self):
        return None

    def command(self, *args, **kwargs):
        return None

    def command_channel(self, *args, **kwargs):
        return None

    def request(self, *args, **kwargs):
        return ""

    def request_channel(self, *args, **kwargs):
        return ""

    def query(self, *args, **kwargs):
        return ""

    def query_channel(self, *args, **kwargs):
        return ""

    def write(self, *args):
        return None

    def write_channel(self, *args, **kwargs):
        return None

    def query_raw_bytes(self, *args, **kwargs):
        return None
