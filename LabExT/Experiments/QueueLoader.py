#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
LabExT  Copyright (C) 2021  ETH Zurich and Polariton Technologies AG
This program is free software and comes with ABSOLUTELY NO WARRANTY; for details see LICENSE file.
"""

import json
import logging
import os
from typing import List

from LabExT.Experiments.QueueValidation import validate_queue, validate_queue_warnings
from LabExT.Experiments.ToDo import MoveEntry, SfpEntry, ToDo
from LabExT.Measurements.MeasAPI.Measparam import MeasParamFloat
from LabExT.Utils import get_configuration_file_path

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

        # JSON has no int/float distinction, but MeasParamFloat insists on a real float,
        # so a queue writing 1550 for a float parameter would otherwise be rejected
        if isinstance(parameter, MeasParamFloat) and type(value) is int:
            value = float(value)

        # dropdown parameters fail confusingly deep inside the instrument if given a value
        # that is not one of their options, so catch it here instead
        options = getattr(parameter, "options", None)
        if options is not None and value not in options:
            raise QueueLoadError(
                f"entry {index}: '{value}' is not a valid option for parameter '{name}' of "
                f"'{class_name}'. Valid options: {list(options)}"
            )

        try:
            parameter.value = value
        except ValueError as exc:
            raise QueueLoadError(
                f"entry {index}: cannot set parameter '{name}' of '{class_name}' to {value!r}: {exc}"
            ) from exc

    return measurement


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
            entries.append(SfpEntry(device=_resolve_device(chip, raw_entry, index, required=False)))
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
            raise QueueLoadError(f"entry {index}: unknown entry type {entry_type!r}, expected 'move', 'sfp' or 'meas'.")

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
