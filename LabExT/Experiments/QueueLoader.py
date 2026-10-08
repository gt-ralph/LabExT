#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
LabExT  Copyright (C) 2021  ETH Zurich and Polariton Technologies AG
This program is free software and comes with ABSOLUTELY NO WARRANTY; for details see LICENSE file.
"""

import copy
import json
import logging
import os
from typing import List

from LabExT.Experiments.QueueValidation import validate_queue, validate_queue_warnings
from LabExT.Experiments.ToDo import CalibEntry, MoveEntry, SfpEntry, ToDo
from LabExT.Measurements.MeasAPI.Measparam import MeasParamFloat
from LabExT.SearchForPeak.PeakSearcher import PeakSearcher
from LabExT.Utils import get_configuration_file_path, get_visa_address

QUEUE_FILE_VERSION = 1

# instrument selections are stored per measurement by the Experiment Wizard under this
# prefix (see InstrumentSelection.SETTINGS_FILE_PREFIX in LabExT/View/ExperimentWizard.py);
# a queue entry that does not specify instruments itself reuses that last GUI selection
INSTRUMENT_SETTINGS_FILE_PREFIX = "ExperimentWizard_instr_"


class QueueLoadError(Exception):
    """Raised when an experiment queue file cannot be loaded."""


def _load_instrument_selection(measurement) -> dict:
    """Reads the instrument selection the Experiment Wizard last saved for this measurement."""
    settings_path = get_configuration_file_path(INSTRUMENT_SETTINGS_FILE_PREFIX + measurement.settings_path)
    if not os.path.isfile(settings_path):
        return {}
    with open(settings_path, "r") as json_file:
        saved = json.load(json_file)
    wanted = measurement.get_wanted_instrument()
    return {role: choice for role, choice in saved.items() if role in wanted}


def _build_measurement(experiment, entry: dict, index: int):
    """Creates and initialises a Measurement for one 'meas' queue entry."""
    class_name = entry.get("measurement")
    if not class_name:
        raise QueueLoadError(f"entry {index}: 'meas' entry is missing the 'measurement' key.")
    if class_name not in experiment.measurements_classes:
        raise QueueLoadError(
            f"entry {index}: unknown measurement '{class_name}'. "
            f"Known measurements: {sorted(experiment.measurement_list)}"
        )

    measurement = experiment.create_measurement_object(class_name)

    selected_instruments = entry.get("instruments") or _load_instrument_selection(measurement)
    if selected_instruments:
        measurement.selected_instruments.update(selected_instruments)
    try:
        measurement.init_instruments()
    except Exception as exc:
        raise QueueLoadError(
            f"entry {index}: could not initialise instruments for '{class_name}': {repr(exc)}. "
            f"Select instruments for this measurement once via the Experiment Wizard, or name them "
            f"in the queue file."
        ) from exc

    for name, value in (entry.get("parameters") or {}).items():
        if name not in measurement.parameters:
            raise QueueLoadError(
                f"entry {index}: '{class_name}' has no parameter '{name}'. "
                f"Known parameters: {sorted(measurement.parameters)}"
            )
        parameter = measurement.parameters[name]
        # dropdown parameters fail confusingly deep inside the instrument if given a value
        # that is not one of their options, so this checks options too
        parameter.value = _coerce_parameter_value(parameter, name, value, index, class_name)

    return measurement


def _coerce_parameter_value(parameter, name: str, value, index: int, owner: str):
    """Checks one parameter value against its MeasParam and returns the value to set."""
    # JSON has no int/float distinction, but MeasParamFloat insists on a real float,
    # so a queue writing 1550 for a float parameter would otherwise be rejected
    if isinstance(parameter, MeasParamFloat) and type(value) is int:
        value = float(value)

    options = getattr(parameter, "options", None)
    if options is not None and value not in options:
        raise QueueLoadError(
            f"entry {index}: '{value}' is not a valid option for parameter '{name}' of "
            f"'{owner}'. Valid options: {list(options)}"
        )

    # setting a throwaway copy validates the value without touching the original
    probe = copy.deepcopy(parameter)
    try:
        probe.value = value
    except ValueError as exc:
        raise QueueLoadError(f"entry {index}: cannot set parameter '{name}' of '{owner}' to {value!r}: {exc}") from exc
    return value


def _build_sfp_parameters(entry: dict, index: int) -> dict:
    """Validates the Search for Peak parameter overrides of one 'sfp' entry."""
    raw = entry.get("parameters") or {}
    if not isinstance(raw, dict):
        raise QueueLoadError(f"entry {index}: 'parameters' of an 'sfp' entry must be an object.")

    known = PeakSearcher.get_default_parameter()
    parameters = {}
    for name, value in raw.items():
        if name not in known:
            raise QueueLoadError(
                f"entry {index}: Search for Peak has no parameter '{name}'. Known parameters: {sorted(known)}"
            )
        parameters[name] = _coerce_parameter_value(known[name], name, value, index, "Search for Peak")
    return parameters


def resolve_instrument(role: str, spec, index: int) -> dict:
    """Turns a queue's instrument choice for a role into a full instrument descriptor.

    Accepts a full descriptor (has a "visa" key, used as-is, like 'meas' entries store it), a
    class name ("LaserSantec"), or {"class": ..., "visa": ... (optional), "channel": ... (optional)}.
    Short forms are looked up in instruments.config; they must match exactly one configured
    instrument, and a channel must be given if that instrument has more than one.
    """
    if isinstance(spec, str):
        spec = {"class": spec}
    if not isinstance(spec, dict) or not spec.get("class"):
        raise QueueLoadError(f"entry {index}: instrument for role '{role}' must be a class name or an object with 'class'.")
    if "visa" in spec and ("channel" in spec or "channels" in spec):
        # complete descriptor, e.g. copied from a saved queue or the GUI selection
        descriptor = copy.deepcopy(spec)
        descriptor.setdefault("channel", (descriptor.get("channels") or [None])[0])
        return descriptor

    try:
        configured = get_visa_address(role)
    except Exception as exc:
        raise QueueLoadError(f"entry {index}: cannot look up instruments for role '{role}': {exc}") from exc

    matches = [
        c for c in configured
        if c.get("class") == spec["class"] and ("visa" not in spec or c.get("visa") == spec["visa"])
    ]
    if len(matches) != 1:
        available = [f"{c.get('class')} at {c.get('visa')}" for c in configured]
        raise QueueLoadError(
            f"entry {index}: {len(matches)} configured instruments match {spec} for role '{role}', "
            f"expected exactly one. Configured: {available}. Add 'visa' to pick one."
        )

    descriptor = copy.deepcopy(matches[0])
    channels = descriptor.get("channels") or []
    channel = spec.get("channel")
    if channel is None:
        if len(channels) > 1:
            raise QueueLoadError(
                f"entry {index}: instrument {spec['class']} for role '{role}' has channels {channels}, name one with 'channel'."
            )
        channel = channels[0] if channels else None
    elif channels and channel not in channels:
        raise QueueLoadError(
            f"entry {index}: channel {channel} is not available on {spec['class']} for role '{role}' (channels {channels})."
        )
    descriptor["channel"] = channel
    return descriptor


def _build_sfp_instruments(entry: dict, index: int) -> dict:
    """Resolves the instrument overrides of one 'sfp' entry to full descriptors."""
    raw = entry.get("instruments") or {}
    if not isinstance(raw, dict):
        raise QueueLoadError(f"entry {index}: 'instruments' of an 'sfp' entry must be an object.")

    wanted = PeakSearcher.get_wanted_instrument()
    instruments = {}
    for role, spec in raw.items():
        if role not in wanted:
            raise QueueLoadError(f"entry {index}: Search for Peak has no instrument role '{role}'. Roles: {wanted}")
        instruments[role] = resolve_instrument(role, spec, index)
    return instruments


def _build_calib_entry(entry: dict, index: int) -> CalibEntry:
    """Creates the CalibEntry of one 'calib' entry."""
    file_path = entry.get("file")
    if file_path is not None and not os.path.isfile(file_path):
        raise QueueLoadError(f"entry {index}: chip map file {file_path!r} does not exist.")
    try:
        return CalibEntry(
            action=entry.get("action"), profile=entry.get("profile"), pause=entry.get("pause"), file=file_path)
    except ValueError as exc:
        raise QueueLoadError(f"entry {index}: {exc}") from exc


def _resolve_device(chip, entry: dict, index: int, required: bool):
    """Looks a device up on the chip by the entry's device_id."""
    device_id = entry.get("device_id")
    if device_id is None:
        if required:
            raise QueueLoadError(f"entry {index}: '{entry.get('type')}' entry is missing 'device_id'.")
        return None
    device_id = str(device_id)
    if device_id not in chip.devices:
        raise QueueLoadError(f"entry {index}: device id '{device_id}' is not on chip '{chip.name}'.")
    return chip.devices[device_id]


def build_entries(queue_data: dict, experiment_manager) -> List:
    """Turns parsed queue JSON into queue entry objects, without validating blocks yet."""
    version = queue_data.get("labext_queue_version")
    if version != QUEUE_FILE_VERSION:
        raise QueueLoadError(
            f"unsupported queue file version {version!r}, this LabExT expects {QUEUE_FILE_VERSION}."
        )

    raw_entries = queue_data.get("entries")
    if not isinstance(raw_entries, list):
        raise QueueLoadError("queue file has no 'entries' list.")

    chip = experiment_manager.chip
    experiment = experiment_manager.exp

    entries = []
    for index, raw_entry in enumerate(raw_entries):
        entry_type = raw_entry.get("type")
        if entry_type == "move":
            entries.append(MoveEntry(device=_resolve_device(chip, raw_entry, index, required=True)))
        elif entry_type == "sfp":
            entries.append(
                SfpEntry(
                    device=_resolve_device(chip, raw_entry, index, required=False),
                    parameters=_build_sfp_parameters(raw_entry, index),
                    instruments=_build_sfp_instruments(raw_entry, index),
                )
            )
        elif entry_type == "calib":
            entries.append(_build_calib_entry(raw_entry, index))
        elif entry_type == "meas":
            device = _resolve_device(chip, raw_entry, index, required=True)
            measurement = _build_measurement(experiment, raw_entry, index)
            # a queue authored as a file carries its own explicit alignment steps, so the
            # global auto-move/auto-sfp settings must not align a second time before this -
            # hence the default. The key exists so that a queue saved out of the GUI, whose
            # entries do align themselves, round-trips instead of silently losing that.
            entries.append(
                ToDo(
                    device=device,
                    measurement=measurement,
                    auto_align=bool(raw_entry.get("auto_align", False)),
                )
            )
        else:
            raise QueueLoadError(
                f"entry {index}: unknown entry type {entry_type!r}, expected 'move', 'sfp', 'meas' or 'calib'."
            )

    return entries


def load_queue_file(file_path: str, experiment_manager) -> List:
    """Loads, builds and validates an experiment queue file.

    Raises `QueueLoadError` if the file cannot be parsed/built or fails validation, so the
    caller can present one message and append nothing (loading is all-or-nothing).

    Returns:
        The list of queue entries, ready to be appended to `experiment.to_do_list`.
    """
    logger = logging.getLogger()

    with open(file_path, "r") as json_file:
        queue_data = json.load(json_file)

    entries = build_entries(queue_data, experiment_manager)

    errors = validate_queue(entries, chip=experiment_manager.chip)
    if errors:
        raise QueueLoadError("\n".join(errors))

    for warning in validate_queue_warnings(entries):
        logger.warning("Experiment queue: %s", warning)

    logger.info("Loaded experiment queue with %d entries from %s", len(entries), file_path)
    return entries
