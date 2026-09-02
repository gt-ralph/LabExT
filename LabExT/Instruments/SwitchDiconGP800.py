#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
LabExT  Copyright (C) 2021  ETH Zurich and Polariton Technologies AG
This program is free software and comes with ABSOLUTELY NO WARRANTY; for details see LICENSE file.
"""

from LabExT.Instruments.InstrumentAPI import Instrument, InstrumentException
from LabExT.Instruments.LabJack import LabJack

import threading
# import paramiko as pm

import requests
import requests.cookies
import os

# Questions 07/07/2025:
# 1. Should we have networked_instrument_properties for the switch?
# 2. Should the switch have an enable and disable in the active viewer due to the open and close methods?

class SwitchDiconGP800(Instrument):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.hostname = self._kwargs.get("hostname", None)
        self.username = self._kwargs.get("username", None)
        self.password = self._kwargs.get("password", None)
        self.uri_prefix = f"https://{self.hostname}/backend"
        self.GP_COMMAND_ENDPOINT = f"{self.uri_prefix}/system/gp-command"
        self.SNAPSHOT_ENDPOINT = f"{self.uri_prefix}/system/snapshots"
        self.ssh = None

    @Instrument._open.getter  # weird way to override the parent's class property getter
    def _open(self):
        return self.ssh

    def open(self):
        self.session = requests.Session()
        crt_path = os.path.join(os.path.dirname(__file__), "GP800_ssl_certificate.crt")

        self.session.verify = crt_path
        response = self.session.post(
            f"{self.uri_prefix}/users/session",
            json={"username": self.username, "password": self.password},
        )
        [key, value] = response.headers["Set-Cookie"].split(";")[0].split("=")
        self.session.cookies.set_cookie(requests.cookies.create_cookie(key, value))
        if not response.ok:
            raise InstrumentException("Failed to open connection to Dicon GP800 switch.")
        self.idn()
        self.logger.debug('opened switch at %s.', self._idn)

    # def get_instrument_parameter(self):
        # return {'idn': self.idn()}
    
    def idn(self):
        self._idn = self.session.get((f"{self.uri_prefix}/system/network-info/hostname")).text
        return f"Switch {self._idn}"
    
    def gp_command(self, command: str) -> str:
        """Sends one GP800 command and returns the instrument's reply.

        A successful POST only means the command reached the controller. The GP800 reports a
        command it could not parse in the reply body, so that has to be checked separately -
        otherwise a malformed command silently leaves the switch in its previous routing.
        """
        response = self.session.post(self.GP_COMMAND_ENDPOINT, json=command)
        if not response.ok:
            raise InstrumentException(
                f"GP800 request failed for {command!r}: HTTP {response.status_code} {response.text}"
            )
        reply = response.text.strip()
        # the GP800 answers a command it did not understand with an error string rather than
        # an HTTP failure, so treat anything that mentions an error as a rejected command
        if "error" in reply.lower() or reply.startswith("?"):
            raise InstrumentException(f"GP800 rejected {command!r}: {reply}")
        return reply

    def connect(self, port_tuples: list[tuple]) -> str:
        """
        expecting a list of tuples (M, N) each mapping the M port to an N port

        Each crosspoint is set with its own `X1 CH <M> <N>` command. The GP800 takes one M/N
        pair per command, so the whole mapping cannot be passed as a pair of lists - doing
        that sends the Python list repr, which the switch rejects without changing anything.

        Returns the raw routing readback, so callers can record what the switch actually did.
        """
        for m_port, n_port in port_tuples:
            self.gp_command(f"X1 CH {int(m_port)} {int(n_port)}")

        # the readback is for the record only, so a GP800 that does not answer this query
        # must not take a measurement down with it - the set commands above are the ones
        # allowed to fail loudly
        try:
            readback = self.get_connections()
        except InstrumentException as exc:
            self.logger.warning("could not read switch routing back: %s", exc)
            readback = f"unavailable: {exc}"

        self.logger.debug("switch routing set to %s, readback: %s", port_tuples, readback)
        return readback

    def get_connections(self) -> str:
        """Reads the switch's current M -> N routing back, as the GP800's raw reply.

        Returned unparsed on purpose: the reply layout varies between GP800 module types, and
        a wrong guess at it would either abort good measurements or hide a real mismatch. The
        string is short and is stored verbatim in the measurement file for later auditing.
        """
        return self.gp_command("X1 CH?")

    def close(self):
        self.session.close()
    
    def trigger(self, continuous=False):
        return
    
    @Instrument.thread_lock.getter  # weird way to override the parent's class property getter
    def thread_lock(self):
        return threading.Lock()

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

    def check_instrument_errors(self, context=None):
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
    
    def logging_stop(self):
        return None