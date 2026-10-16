#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
LabExT  Copyright (C) 2021  ETH Zurich and Polariton Technologies AG
This program is free software and comes with ABSOLUTELY NO WARRANTY; for details see LICENSE file.
"""

import ctypes
import math
import os
import threading
from ctypes import byref, create_string_buffer

from LabExT.Instruments.InstrumentAPI import Instrument, InstrumentException

#: Buffer sizes TLPM.h asks callers to provide. Anything shorter is a buffer overrun in a C library.
TLPM_BUFFER_SIZE = 256
TLPM_ERR_DESCR_BUFFER_SIZE = 512

#: Attribute selectors. Every TLPM getter takes one: SET_VAL reads back what was set, MIN_VAL and
#: MAX_VAL report the instrument's own limits. There is no TLPM_getWavelengthRange, so the
#: wavelength limits are read as attributes of TLPM_getWavelength.
TLPM_ATTR_SET_VAL = 0
TLPM_ATTR_MIN_VAL = 1
TLPM_ATTR_MAX_VAL = 2

#: Hardware power units. This driver pins the hardware to watts - see the `unit` property.
TLPM_POWER_UNIT_WATT = 0
TLPM_POWER_UNIT_DBM = 1

#: Sensor capability flag. Clear means the sensor has no settable wavelength, so the responsivity
#: correction this driver relies on is not being applied and the readings are not calibrated.
TLPM_SENS_FLAG_IS_WAVEL_SET = 0x0020

#: Positive ViStatus values are warnings rather than errors, and these three are the ones worth
#: naming: a reading that overflowed the range, underran it, or came back as not-a-number.
VI_INSTR_WARN_OVERFLOW = 0x3FFC0901
VI_INSTR_WARN_UNDERRUN = 0x3FFC0902
VI_INSTR_WARN_NAN = 0x3FFC0903

#: An over-range reading comes back as a sentinel around 9.9e37 rather than as an error, so any
#: value above this is treated as "no reading" whatever the status code said.
OVER_RANGE_SENTINEL = 1e30

#: Floor for the watts-to-dBm conversion. A dark sensor reads zero or slightly negative, and
#: log(0) is -inf, which is not JSON serialisable and poisons a plot's axis limits.
MINIMUM_DBM = -300.0

# VISA scalar types, spelled out because ctypes has to be told. ViStatus is *signed*: negative is
# an error, zero is success, positive is a warning.
_ViStatus = ctypes.c_int32
_ViSession = ctypes.c_uint32
_ViBoolean = ctypes.c_uint16
_ViInt16 = ctypes.c_int16
_ViUInt32 = ctypes.c_uint32
_ViReal64 = ctypes.c_double
_ViString = ctypes.c_char_p

#: Prototypes for every TLPM entry point this driver uses, as name -> argtypes. Thorlabs' own
#: TLPM.py sets no argtypes at all, which lets ctypes guess the width of the ViInt16 attribute
#: arguments; that happens to work on 32 bit and is a latent corruption on 64 bit. Spelling them
#: out costs a table and removes the whole class of bug.
#:
#: Note TLPM_setPowerAutoRange against TLPM_getPowerAutorange: the capitalisation of the setter and
#: the getter genuinely differ in the library. Both spellings below were checked against the
#: installed DLL. Getting one wrong surfaces as an AttributeError at first use, not at load.
_TLPM_PROTOTYPES = {
    'TLPM_findRsrc': [_ViSession, ctypes.POINTER(_ViUInt32)],
    'TLPM_getRsrcName': [_ViSession, _ViUInt32, ctypes.c_char_p],
    'TLPM_getRsrcInfo': [_ViSession, _ViUInt32, ctypes.c_char_p, ctypes.c_char_p,
                         ctypes.c_char_p, ctypes.POINTER(_ViBoolean)],
    'TLPM_init': [_ViString, _ViBoolean, _ViBoolean, ctypes.POINTER(_ViSession)],
    'TLPM_close': [_ViSession],
    'TLPM_errorMessage': [_ViSession, _ViStatus, ctypes.c_char_p],
    'TLPM_identificationQuery': [_ViSession, ctypes.c_char_p, ctypes.c_char_p,
                                 ctypes.c_char_p, ctypes.c_char_p],
    'TLPM_getSensorInfo': [_ViSession, ctypes.c_char_p, ctypes.c_char_p, ctypes.c_char_p,
                           ctypes.POINTER(_ViInt16), ctypes.POINTER(_ViInt16),
                           ctypes.POINTER(_ViInt16)],
    'TLPM_setWavelength': [_ViSession, _ViReal64],
    'TLPM_getWavelength': [_ViSession, _ViInt16, ctypes.POINTER(_ViReal64)],
    'TLPM_setPowerUnit': [_ViSession, _ViInt16],
    'TLPM_getPowerUnit': [_ViSession, ctypes.POINTER(_ViInt16)],
    'TLPM_setAvgTime': [_ViSession, _ViReal64],
    'TLPM_getAvgTime': [_ViSession, _ViInt16, ctypes.POINTER(_ViReal64)],
    'TLPM_setPowerAutoRange': [_ViSession, _ViInt16],
    'TLPM_getPowerAutorange': [_ViSession, ctypes.POINTER(_ViInt16)],
    'TLPM_setPowerRange': [_ViSession, _ViReal64],
    'TLPM_getPowerRange': [_ViSession, _ViInt16, ctypes.POINTER(_ViReal64)],
    'TLPM_measPower': [_ViSession, ctypes.POINTER(_ViReal64)],
}


def _default_dll_paths():
    """Where TLPM is installed, most specific first, for the bitness of this interpreter.

    The 32 and 64 bit installations live in different trees and a machine can carry both, so the
    interpreter's own pointer size decides which one can be loaded at all.
    """
    sixty_four_bit = ctypes.sizeof(ctypes.c_void_p) == 8
    paths = []

    vxi = os.environ.get('VXIPNPPATH64' if sixty_four_bit else 'VXIPNPPATH')
    if vxi:
        paths.append(os.path.join(vxi, 'Win64' if sixty_four_bit else 'WinNT', 'Bin',
                                  'TLPM_64.dll' if sixty_four_bit else 'TLPM_32.dll'))

    if sixty_four_bit:
        paths.append(r'C:\Program Files\IVI Foundation\VISA\Win64\Bin\TLPM_64.dll')
    else:
        paths.append(r'C:\Program Files (x86)\IVI Foundation\VISA\WinNT\Bin\TLPM_32.dll')

    # last resort: whatever is on PATH, which is how a non-standard install is usually reachable
    paths.append('TLPM_64.dll' if sixty_four_bit else 'TLPM_32.dll')
    return paths


def _probe_for_tlpm():
    """Is a TLPM DLL present? Only a file test - loading it is left to open()."""
    for path in _default_dll_paths():
        if os.path.isfile(path):
            return True, path, None
    return False, None, FileNotFoundError(
        "No TLPM DLL found. Looked at: " + ", ".join(_default_dll_paths()))


#: Whether a TLPM installation was found at import time. The DLL is deliberately *not* loaded here.
#: PluginLoader silently drops any module which raises on import, which would make this driver
#: disappear from the instrument list with nothing but a log line to explain it, so the expensive
#: and failure-prone part is deferred to open() where an exception can say what is wrong.
TLPM_AVAILABLE, TLPM_DEFAULT_DLL, TLPM_IMPORT_ERROR = _probe_for_tlpm()

#: One loaded library per DLL path, and the os.add_dll_directory cookies keeping their dependency
#: directories searchable for as long as the process lives.
_LIBRARIES = {}
_LIBRARY_COOKIES = []
_LIBRARY_LOCK = threading.Lock()

#: Open TLPM sessions by resource name, each with a reference count. A TLPM USB session is
#: exclusive, and LabExT routinely holds more than one driver object for one instrument: the
#: measurement's, Search for Peak's, the Live Viewer's, plus the transient ones
#: Instrument.get_instrument_parameter() builds during metadata collection. Sharing one session
#: between them is what stops opening a window mid-sweep from killing the sweep.
_SESSIONS = {}
_SESSION_LOCK = threading.Lock()


class _Session:
    """One TLPM session and the number of driver objects currently holding it.

    The serial is recorded because TLPM cannot be asked for it again later: once a device is
    claimed, `TLPM_getRsrcInfo` reports its serial as 'n/a' and drops it from the resource name
    too, so a second driver object for the same meter can only recognise it from here.
    """

    def __init__(self, handle, resource, serial, model):
        self.handle = handle
        self.resource = resource
        self.serial = serial
        self.model = model
        self.refcount = 0


class PowerMeterThorlabsPM100USB(Instrument):
    """
    ## Thorlabs PM100USB optical power meter (via the Thorlabs TLPM driver)

    Console (`PM100USB`) plus whichever sensor is plugged into it - an S144C integrating sphere on
    the bench this was written for. Readings are taken through Thorlabs' own `TLPM` driver rather
    than over VISA/SCPI, because a PM100USB bound to the Thorlabs USB driver class is invisible to
    NI-VISA: it does not appear in `list_resources()` and opening its USBTMC resource string fails
    with `VI_ERROR_RSRC_NFOUND`. Switching the device to the USBTMC driver would make SCPI work, at
    the cost of the Thorlabs applications; going through TLPM costs nothing and needs no re-binding.

    ### Requirements

    A Thorlabs TLPM installation, which ships with Optical Power Monitor and with several other
    Thorlabs packages. The DLL is looked for at `%VXIPNPPATH64%\\Win64\\Bin\\TLPM_64.dll`, then at
    `C:\\Program Files\\IVI Foundation\\VISA\\Win64\\Bin\\TLPM_64.dll`, then on PATH.

    A TLPM session is exclusive: **close Optical Power Monitor** before using the meter here.

    ### Properties

    The interface is the one `PowerMeterGenericKeysight` defines, so this meter can stand in
    anywhere a LabExT power meter is expected, Search for Peak included:

    * `wavelength` (nm), `unit` (`'Watt'`/`'dBm'`), `range` (dBm), `autoranging`, `averagetime` (s)
    * `power`, `fetch_power()`, `trigger()`

    plus `read_power_watt()`, which returns linear watts whatever `unit` is set to. Prefer it in
    measurement code: `unit` is presentation only, and other parts of LabExT change it.

    ### Config example

        "Power Meter": [
            {"visa": "None", "class": "PowerMeterThorlabsPM100USB", "channels": [],
             "args": {"serial": "M01331478", "average_time": 0.01}}
        ]

    `visa` may instead carry a TLPM resource name (`USB0::0x1313::0x8072::M01331478::INSTR`), which
    is used verbatim and skips the device search.

    ### Keyword arguments

    * `serial` (str): serial number of the meter to open. Omit it only when exactly one is attached.
    * `dll_path` (str): explicit path to the TLPM DLL, overriding the search above.
    * `average_time` (float): averaging time in seconds, applied on open. Default: leave as found.
    * `wavelength` (float): wavelength in nm, applied on open.
    * `autoranging` (bool): applied on open. Default: leave as found.
    * `keep_open` (bool): do not release the shared session on close(). Default False.
    * `reset_on_open` (bool): send a device reset when opening. Default False - a reset costs
      seconds and discards the settings we are about to apply anyway.
    """

    #: Deliberately absent rather than False: `PowerMeterCameraAlvium` sets IS_CAMERA_BACKED to mark
    #: a "meter" which is really a camera, and consumers test it with getattr. This is a real meter.

    def __init__(self, *args, **kwargs):
        # Before super().__init__, because a failure in there still reaches __del__ -> close(),
        # which reads both of these.
        self._session = None
        self._lib = None
        self._resource = None

        super().__init__(*args, **kwargs)

        self._dll_path = self._kwargs.get('dll_path', None)
        self._serial = self._kwargs.get('serial', None)
        self._keep_open = bool(self._kwargs.get('keep_open', False))
        self._reset_on_open = bool(self._kwargs.get('reset_on_open', False))
        self._startup_average_time = self._kwargs.get('average_time', None)
        self._startup_wavelength = self._kwargs.get('wavelength', None)
        self._startup_autoranging = self._kwargs.get('autoranging', None)

        #: Presentation unit only. The hardware is pinned to watts in open() and left there.
        self._unit = 'Watt'
        #: Cached so averagetime can be reported without a round trip, as the Keysight driver does.
        self._last_set_atime_s = None
        self._sensor_info = None
        #: Whether the most recent reading was over range. Kept beside the reading rather than
        #: encoded into it, because the reading itself is nan by then and nan has other causes.
        self._last_over_range = False

        # One lock per instrument, held for the life of the object. Note that
        # PowerMeterKoheronPD10R returns a fresh Lock() from its thread_lock getter, which locks
        # nothing at all; a shared instrument needs the same lock every time it is asked for one.
        self._instr_lock = threading.RLock()

        self.networked_instrument_properties.extend([
            'wavelength',
            'unit',
            'range',
            'autoranging',
            'averagetime',
        ])

    #
    # library loading
    #

    def _load_library(self):
        """Load the TLPM DLL, once per path per process, and return it.

        Kept as its own method so tests can substitute a stub library without a Thorlabs
        installation - the same seam `PowerMeterCameraAlvium._make_camera` provides.
        """
        candidates = [self._dll_path] if self._dll_path else _default_dll_paths()

        with _LIBRARY_LOCK:
            for path in candidates:
                if path in _LIBRARIES:
                    return _LIBRARIES[path]

            errors = []
            for path in candidates:
                if os.path.isabs(path) and not os.path.isfile(path):
                    errors.append("{:s}: not found".format(path))
                    continue
                try:
                    directory = os.path.dirname(os.path.abspath(path))
                    if os.path.isdir(directory) and hasattr(os, 'add_dll_directory'):
                        # Python 3.8 stopped searching PATH for a DLL's *dependencies* on Windows,
                        # and TLPM pulls the IVI VISA runtime out of its own Bin directory. Without
                        # this the load fails with a bare "DLL load failed" that names TLPM rather
                        # than the dependency it could not find.
                        _LIBRARY_COOKIES.append(os.add_dll_directory(directory))
                    library = ctypes.cdll.LoadLibrary(path)
                except OSError as exc:
                    if getattr(exc, 'winerror', None) == 193:
                        errors.append(
                            "{:s}: not a valid {:d} bit library. This interpreter is {:d} bit, so "
                            "it needs the matching TLPM installation.".format(
                                path, 8 * ctypes.sizeof(ctypes.c_void_p),
                                8 * ctypes.sizeof(ctypes.c_void_p)))
                    else:
                        errors.append("{:s}: {!r}".format(path, exc))
                    continue

                self._apply_prototypes(library)
                _LIBRARIES[path] = library
                self.logger.debug("Loaded TLPM library from %s", path)
                return library

        raise InstrumentException(
            "Cannot use " + self.__class__.__name__ + ": the Thorlabs TLPM library could not be "
            "loaded. Install the Thorlabs software which ships it (Optical Power Monitor, for "
            "instance), or point the driver at the DLL with the 'dll_path' argument. Tried: "
            + "; ".join(errors))

    @staticmethod
    def _apply_prototypes(library):
        """Tell ctypes the signature of every entry point, so nothing is guessed."""
        for name, argtypes in _TLPM_PROTOTYPES.items():
            try:
                function = getattr(library, name)
            except AttributeError as exc:
                raise InstrumentException(
                    "The TLPM library is missing " + name + ", so it is either too old or not "
                    "TLPM at all. Original error: " + repr(exc)) from exc
            function.restype = _ViStatus
            function.argtypes = argtypes

    #
    # error handling
    #

    def _error_text(self, status):
        """Thorlabs' own description of a status code, or a hex code if even that fails."""
        try:
            buffer = create_string_buffer(TLPM_ERR_DESCR_BUFFER_SIZE)
            self._lib.TLPM_errorMessage(_ViSession(self._session.handle if self._session else 0),
                                        _ViStatus(status), buffer)
            text = buffer.value.decode('ascii', errors='replace').strip()
            if text:
                return text
        except Exception:
            # TLPM_errorMessage wants a session, and the failures worth explaining are exactly the
            # ones where there is not one yet. Fall through to the raw code rather than masking the
            # original error with a second one.
            pass
        return "TLPM status 0x{:08X}".format(status & 0xFFFFFFFF)

    def _check(self, status, what):
        """Raise on a TLPM error, log a TLPM warning, and hand the status back either way.

        ViStatus is signed: negative is a failure, positive is a warning about a reading which was
        still produced. An over-range measurement arrives as a warning, so returning the status
        rather than swallowing it is what lets the caller flag the reading.
        """
        if status == 0:
            return 0
        if status < 0:
            raise InstrumentException(
                "{:s} failed: {:s} (status 0x{:08X})".format(
                    what, self._error_text(status), status & 0xFFFFFFFF))
        self.logger.warning("%s reported a TLPM warning: %s", what, self._error_text(status))
        return status

    #
    # connection handling
    #

    @Instrument._open.getter  # weird way to override the parent's class property getter
    def _open(self):
        return self._session is not None

    def open(self):
        """Open the meter. Does nothing if this object already holds a session.

        A TLPM session is exclusive per device, so sessions are shared process-wide by resource
        name and reference counted; see `_SESSIONS`.
        """
        if self._session is not None:
            return

        self._lib = self._load_library()

        # The whole open is done under the lock, device search included. It is a few milliseconds
        # and not on any hot path, and holding it throughout is what stops two threads from both
        # deciding a meter is free and racing into TLPM_init on it.
        with _SESSION_LOCK:
            session = self._matching_open_session()
            if session is None:
                resource, serial, model, available = self._resolve_resource()
                session = _SESSIONS.get(resource)
                if session is None:
                    handle = _ViSession(0)
                    status = self._lib.TLPM_init(
                        resource.encode('ascii'), _ViBoolean(1),
                        _ViBoolean(1 if self._reset_on_open else 0), byref(handle))
                    if status < 0:
                        if not available:
                            # TLPM told us the meter was claimed and the open then failed, so lead
                            # with that rather than with the driver's own wording - which for a
                            # meter held elsewhere is "Insufficient location information", naming
                            # neither the meter nor the reason.
                            raise InstrumentException(
                                "The Thorlabs power meter is already in use by another program. A "
                                "TLPM session is exclusive, so close Thorlabs Optical Power "
                                "Monitor - or whichever program holds the meter - and try again. "
                                "(" + self._error_text(status) + ", status 0x{:08X})".format(
                                    status & 0xFFFFFFFF))
                        raise InstrumentException(
                            "Could not open the Thorlabs power meter at " + resource + ": "
                            + self._error_text(status) + ". A TLPM session is exclusive, so close "
                            "Thorlabs Optical Power Monitor and any other program holding the "
                            "meter, then try again. (status 0x{:08X})".format(
                                status & 0xFFFFFFFF))
                    session = _Session(handle.value, resource, serial, model)
                    _SESSIONS[resource] = session
            session.refcount += 1

        self._session = session
        self._resource = session.resource

        try:
            # Watts, always. See the `unit` property for why the hardware unit is not the one the
            # user selects.
            self._check(self._lib.TLPM_setPowerUnit(_ViSession(self._session.handle),
                                                    _ViInt16(TLPM_POWER_UNIT_WATT)),
                        'TLPM_setPowerUnit')
            self._read_sensor_info()
            self._apply_startup_settings()
        except Exception:
            self.close()
            raise

        self.logger.info("Opened Thorlabs power meter %s (%s).", self._resource, self.idn())

    def _matching_open_session(self):
        """A session this process already holds for the meter this object wants, or None.

        Checked before searching for devices, because TLPM stops reporting a claimed meter's serial
        - `TLPM_getRsrcInfo` returns 'n/a' and the resource name loses it - so a search would fail
        to recognise the very meter we are already talking to, and then report it as in use. Caller
        must hold `_SESSION_LOCK`.
        """
        if not _SESSIONS:
            return None

        if self._address and str(self._address).lower() not in ('none', ''):
            return _SESSIONS.get(str(self._address))

        if self._serial:
            wanted = str(self._serial).lower()
            for session in _SESSIONS.values():
                if session.serial and session.serial.lower() == wanted:
                    return session
            return None

        # No serial asked for. One open session is unambiguous; more than one is not, and falling
        # through to the search produces the error that says so.
        return next(iter(_SESSIONS.values())) if len(_SESSIONS) == 1 else None

    def _resolve_resource(self):
        """Which meter to open, as (resource name, serial, model, available).

        Either the resource name configured in the `visa` field, or the result of a device search.
        """
        if self._address and str(self._address).lower() not in ('none', ''):
            return str(self._address), self._serial, None, True

        count = _ViUInt32(0)
        self._check(self._lib.TLPM_findRsrc(_ViSession(0), byref(count)), 'TLPM_findRsrc')

        found = []
        for index in range(count.value):
            name = create_string_buffer(TLPM_BUFFER_SIZE)
            self._check(self._lib.TLPM_getRsrcName(_ViSession(0), _ViUInt32(index), name),
                        'TLPM_getRsrcName')
            model = create_string_buffer(TLPM_BUFFER_SIZE)
            serial = create_string_buffer(TLPM_BUFFER_SIZE)
            manufacturer = create_string_buffer(TLPM_BUFFER_SIZE)
            available = _ViBoolean(0)
            self._check(
                self._lib.TLPM_getRsrcInfo(_ViSession(0), _ViUInt32(index), model, serial,
                                           manufacturer, byref(available)),
                'TLPM_getRsrcInfo')
            found.append({
                'resource': name.value.decode('ascii', errors='replace'),
                'model': model.value.decode('ascii', errors='replace'),
                'serial': serial.value.decode('ascii', errors='replace'),
                'available': bool(available.value),
            })

        if not found:
            raise InstrumentException(
                "No Thorlabs power meter found by the TLPM driver. Check the USB connection. Note "
                "that a PM100USB bound to the Thorlabs USB driver is invisible to NI-VISA, so its "
                "absence from a VISA resource list is expected and not the problem here.")

        if self._serial:
            wanted = str(self._serial).lower()
            matches = [entry for entry in found
                       if wanted == entry['serial'].lower() or wanted in entry['resource'].lower()]
            if not matches:
                raise InstrumentException(
                    "No Thorlabs power meter with serial '" + str(self._serial) + "'. Found: "
                    + "; ".join("{model} serial {serial} ({resource}, {avail})".format(
                        avail='available' if e['available'] else 'in use', **e) for e in found))
            entry = matches[0]
        elif len(found) == 1:
            entry = found[0]
        else:
            # Picking the first of several is the kind of mistake that only shows up later, in the
            # data, as a spectrum taken with the wrong detector.
            raise InstrumentException(
                "More than one Thorlabs power meter is attached, so the 'serial' argument is "
                "required to say which one to use. Found: "
                + "; ".join("{model} serial {serial}".format(**e) for e in found))

        if not entry['available']:
            # Advisory only, so log it and try anyway. The flag lags reality: immediately after a
            # TLPM_close the meter is still reported as in use, which would make a close/reopen
            # cycle within one LabExT session fail for no reason. If it really is held, TLPM_init
            # says so in a message that names the likely culprit.
            self.logger.info(
                "TLPM reports %s as in use; opening it anyway, since that flag lags a close.",
                entry['resource'])

        if not self._serial:
            self.logger.info("Using the only Thorlabs power meter found: %s serial %s.",
                             entry['model'], entry['serial'])
        return entry['resource'], entry['serial'], entry['model'], entry['available']

    def _read_sensor_info(self):
        """Cache what the attached sensor says about itself, and warn if it is uncalibratable."""
        name = create_string_buffer(TLPM_BUFFER_SIZE)
        serial = create_string_buffer(TLPM_BUFFER_SIZE)
        message = create_string_buffer(TLPM_BUFFER_SIZE)
        sensor_type = _ViInt16(0)
        subtype = _ViInt16(0)
        flags = _ViInt16(0)
        self._check(
            self._lib.TLPM_getSensorInfo(_ViSession(self._session.handle), name, serial, message,
                                         byref(sensor_type), byref(subtype), byref(flags)),
            'TLPM_getSensorInfo')
        self._sensor_info = {
            'name': name.value.decode('ascii', errors='replace'),
            'serial': serial.value.decode('ascii', errors='replace'),
            'message': message.value.decode('ascii', errors='replace'),
            'type': int(sensor_type.value),
            'subtype': int(subtype.value),
            'flags': int(flags.value),
            'wavelength settable': bool(flags.value & TLPM_SENS_FLAG_IS_WAVEL_SET),
        }
        if not self._sensor_info['wavelength settable']:
            self.logger.warning(
                "The sensor on this power meter (%s) has no settable wavelength, so its "
                "responsivity correction cannot be tuned to the laser. Readings taken across a "
                "wavelength sweep will not be calibrated.", self._sensor_info['name'])

    def _apply_startup_settings(self):
        """Apply whatever the config asked for. Anything not given is left as the meter had it."""
        if self._startup_autoranging is not None:
            self.autoranging = bool(self._startup_autoranging)
        if self._startup_average_time is not None:
            self.averagetime = float(self._startup_average_time)
        if self._startup_wavelength is not None:
            self.wavelength = float(self._startup_wavelength)

    def close(self):
        """Release this object's hold on the session, closing it on the last release.

        Safe to call more than once, and safe on an object whose open() never ran - the parent
        class calls it from __del__.
        """
        session = getattr(self, '_session', None)
        if session is None:
            return

        self._session = None
        if getattr(self, '_keep_open', False):
            return

        with _SESSION_LOCK:
            session.refcount -= 1
            if session.refcount > 0:
                return
            _SESSIONS.pop(session.resource, None)

        try:
            self._lib.TLPM_close(_ViSession(session.handle))
        except Exception as exc:
            self.logger.warning("Error closing the Thorlabs power meter: %r", exc)

        self.logger.debug("Closed Thorlabs power meter %s.", session.resource)

    @Instrument.thread_lock.getter  # weird way to override the parent's class property getter
    def thread_lock(self):
        return self._instr_lock

    #
    # internal helpers
    #

    def _handle(self):
        if self._session is None:
            raise InstrumentException(
                "The power meter connection is not open. Call open() before reading it.")
        return _ViSession(self._session.handle)

    def _get_real(self, function_name, attribute=TLPM_ATTR_SET_VAL):
        value = _ViReal64(0.0)
        self._check(getattr(self._lib, function_name)(self._handle(), _ViInt16(attribute),
                                                      byref(value)), function_name)
        return float(value.value)

    @staticmethod
    def _watt_to_dbm(watt):
        """Watts to dBm, floored rather than -inf.

        A nan comes through as nan rather than as the floor, because the two mean different
        things: the floor is a sensor reading no light, while nan is a reading that could not be
        taken at all - over range, or a failed read. Search for Peak tests its scan values with
        `np.isfinite` and reports "change of power meter range required" for a nan, which it can
        only do if the distinction survives the conversion. It is also what
        `PowerMeterGenericKeysight.fetch_power` does for an unreadable value.
        """
        if watt is None:
            return float('nan')
        if not math.isfinite(watt):
            return float(watt)
        if watt <= 0.0:
            return MINIMUM_DBM
        return max(MINIMUM_DBM, 10.0 * math.log10(watt * 1000.0))

    @staticmethod
    def _dbm_to_watt(dbm):
        return 10.0 ** (float(dbm) / 10.0) / 1000.0

    #
    # measurement
    #

    def read_power_watt(self):
        """One reading, in linear watts, whatever `unit` happens to be set to.

        This is the method measurement code should call. `unit` is a presentation choice and other
        parts of LabExT change it - Search for Peak sets every non-camera-backed meter to dBm - so
        code which needs a number it can do arithmetic on should not go through `power`.

        An over-range reading is returned as `nan` rather than as the ~9.9e37 sentinel the driver
        produces, because that sentinel propagates through a normalisation as a plausible-looking
        zero.
        """
        with self._instr_lock:
            value = _ViReal64(0.0)
            status = self._check(self._lib.TLPM_measPower(self._handle(), byref(value)),
                                 'TLPM_measPower')
            reading = float(value.value)

        if (status in (VI_INSTR_WARN_OVERFLOW, VI_INSTR_WARN_NAN)
                or not math.isfinite(reading)
                or abs(reading) > OVER_RANGE_SENTINEL):
            self.logger.warning("Power meter reading is over range or invalid (%r).", reading)
            self._last_over_range = True
            return float('nan')
        self._last_over_range = False
        return reading

    def last_read_was_over_range(self):
        """Whether the most recent `read_power_watt()` came back over range or invalid."""
        return self._last_over_range

    @property
    def power(self):
        """One reading, in the currently selected `unit`."""
        watt = self.read_power_watt()
        return self._watt_to_dbm(watt) if self._unit == 'dBm' else watt

    def fetch_power(self):
        """One reading, in the currently selected `unit`.

        TLPM has no trigger/fetch split for a simple power read - `TLPM_measPower` starts a
        measurement and returns its result - so this is the same as `power`, and `trigger()` is a
        no-op. Both exist because the LabExT power meter interface is written around the SCPI
        instruments, which do split the two.
        """
        return self.power

    def trigger(self, continuous=None):
        """No-op. See `fetch_power` for why this instrument has nothing to trigger."""
        return None

    #
    # properties
    #

    @property
    def wavelength(self):
        """Wavelength the sensor's responsivity is corrected for, in nm."""
        return self._get_real('TLPM_getWavelength')

    @wavelength.setter
    def wavelength(self, value):
        low, high = self.wavelength_range
        value = float(value)
        if not low <= value <= high:
            raise InstrumentException(
                "Wavelength {:.3f} nm is outside what this sensor is calibrated for "
                "({:.3f} nm to {:.3f} nm).".format(value, low, high))
        with self._instr_lock:
            self._check(self._lib.TLPM_setWavelength(self._handle(), _ViReal64(value)),
                        'TLPM_setWavelength')

    @property
    def wavelength_range(self):
        """[min, max] wavelength the attached sensor accepts, in nm."""
        return [self._get_real('TLPM_getWavelength', TLPM_ATTR_MIN_VAL),
                self._get_real('TLPM_getWavelength', TLPM_ATTR_MAX_VAL)]

    @property
    def unit(self):
        """Unit readings are reported in: 'Watt' or 'dBm'.

        Presentation only. The hardware is pinned to watts at open() and left there, and dBm is
        computed here, floored at MINIMUM_DBM. In hardware dBm mode a dark sensor returns a large
        negative sentinel which is neither plottable nor JSON serialisable, and the conversion is
        exact anyway, so there is nothing to gain by letting the meter do it.
        """
        return self._unit

    @unit.setter
    def unit(self, value):
        text = str(value).strip().lower()
        if text in ('w', 'watt', 'watts', 'linear'):
            self._unit = 'Watt'
        elif text in ('dbm', 'db'):
            self._unit = 'dBm'
        else:
            raise InstrumentException(
                "Power meter unit must be 'Watt' or 'dBm', not " + repr(value) + ".")

    @property
    def averagetime(self):
        """Averaging time per reading, in seconds."""
        return self._get_real('TLPM_getAvgTime')

    @averagetime.setter
    def averagetime(self, value):
        value = float(value)
        with self._instr_lock:
            self._check(self._lib.TLPM_setAvgTime(self._handle(), _ViReal64(value)),
                        'TLPM_setAvgTime')
        self._last_set_atime_s = value

    @property
    def autoranging(self):
        value = _ViInt16(0)
        # Note the capitalisation: the getter is 'Autorange', the setter 'AutoRange'. That is how
        # the library spells them.
        self._check(self._lib.TLPM_getPowerAutorange(self._handle(), byref(value)),
                    'TLPM_getPowerAutorange')
        return bool(value.value)

    @autoranging.setter
    def autoranging(self, value):
        with self._instr_lock:
            self._check(
                self._lib.TLPM_setPowerAutoRange(self._handle(), _ViInt16(1 if value else 0)),
                'TLPM_setPowerAutoRange')

    @property
    def range(self):
        """Measurement range in dBm, or the string 'auto' when autoranging is on.

        dBm rather than watts because that is what the rest of LabExT puts into `range`: both
        `PeakSearcher` and the Live Viewer's power meter card hand it a dBm number, and a driver
        which read those as watts would silently sit at the top of its range.
        """
        if self.autoranging:
            return 'auto'
        return self._watt_to_dbm(self._get_real('TLPM_getPowerRange'))

    @range.setter
    def range(self, value):
        if isinstance(value, str):
            if value.strip().lower() != 'auto':
                raise InstrumentException(
                    "Power meter range must be a number in dBm or the string 'auto', not "
                    + repr(value) + ".")
            self.autoranging = True
            return
        self.autoranging = False
        with self._instr_lock:
            self._check(
                self._lib.TLPM_setPowerRange(self._handle(),
                                             _ViReal64(self._dbm_to_watt(float(value)))),
                'TLPM_setPowerRange')

    @property
    def range_watt(self):
        """Measurement range in watts, as TLPM itself expresses it."""
        return self._get_real('TLPM_getPowerRange')

    @property
    def sensor_info(self):
        """What the attached sensor reports about itself, read once at open()."""
        return dict(self._sensor_info) if self._sensor_info else None

    #
    # identification
    #

    def idn(self):
        """Identification string. Must not raise: metadata collection calls it unguarded."""
        try:
            manufacturer = create_string_buffer(TLPM_BUFFER_SIZE)
            device = create_string_buffer(TLPM_BUFFER_SIZE)
            serial = create_string_buffer(TLPM_BUFFER_SIZE)
            firmware = create_string_buffer(TLPM_BUFFER_SIZE)
            self._check(
                self._lib.TLPM_identificationQuery(self._handle(), manufacturer, device, serial,
                                                   firmware),
                'TLPM_identificationQuery')
            parts = [manufacturer.value.decode('ascii', errors='replace'),
                     device.value.decode('ascii', errors='replace'),
                     serial.value.decode('ascii', errors='replace'),
                     firmware.value.decode('ascii', errors='replace')]
            text = ",".join(part.strip() for part in parts)
            if self._sensor_info:
                text += " with sensor " + self._sensor_info['name']
            return text
        except Exception as exc:
            return "PowerMeterThorlabsPM100USB (identification unavailable: {!r})".format(exc)

    def get_instrument_parameter(self):
        """Settings worth recording with a measurement."""
        parameters = dict(self.instrument_parameters)
        parameters['idn'] = self.idn()
        # Recorded so that a result file says which unit the numbers in it were actually taken in,
        # independent of whatever `unit` was set to for display.
        parameters['hardware power unit'] = 'Watt'
        parameters['resource'] = self._resource
        parameters['sensor'] = self.sensor_info
        for name in self.networked_instrument_properties:
            try:
                parameters[name] = getattr(self, name)
            except Exception as exc:
                parameters[name] = "ERROR reading {:s}: {!r}".format(name, exc)
        return parameters

    #
    # SCPI/VISA surface of the parent class, stubbed out as for any non-VISA instrument
    #

    def logging_stop(self):
        return None

    def clear(self):
        return None

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

    def write(self, *args, **kwargs):
        return None

    def write_channel(self, *args, **kwargs):
        return None

    def query_raw_bytes(self, *args, **kwargs):
        return None
