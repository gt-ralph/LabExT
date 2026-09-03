#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
LabExT  Copyright (C) 2021  ETH Zurich and Polariton Technologies AG
This program is free software and comes with ABSOLUTELY NO WARRANTY; for details see LICENSE file.
"""

import copy
import json
import logging
from typing import List

from LabExT.Experiments.QueueLoader import QUEUE_FILE_VERSION
from LabExT.Experiments.QueueValidation import validate_queue

QUEUE_SAVE_INDENT = 2

# instrument descriptors are copied verbatim out of instruments.config, and some carry login
# details for networked instruments. Saving them is deliberate - a queue that rebinds its own
# instruments is the point - but the user should know before passing the file on.
SENSITIVE_ARG_KEYS = frozenset({"password", "passwd", "secret", "token", "api_key", "apikey"})


class QueueSaveError(Exception):
    """Raised when an experiment queue cannot be written to file."""


def _json_safe(value, index: int, name: str):
    """Returns `value` if a queue file can hold it, raising `QueueSaveError` if it cannot.

    Sweep entries take their parameter values from pandas rows, so numpy scalars are a
    realistic arrival here; they serialise as garbage rather than failing loudly.
    """
    if isinstance(value, (str, bool, int, float, list, dict)) or value is None:
        return value
    if hasattr(value, "item"):  # numpy scalar
        return value.item()
    raise QueueSaveError(
        f"entry {index}: parameter {name!r} holds a {type(value).__name__}, "
        f"which cannot be written to a queue file."
    )


def _serialize_parameters(measurement, index: int) -> dict:
    """Flat {name: value} map of a measurement's parameters, in the shape the loader reads.

    Deliberately not `MeasParam.as_dict()`: that emits {"value", "unit"}, which is the shape
    used inside measurement result files, and the loader would reject it.

    Parameters left at `None` are omitted rather than written as null. The loader leaves an
    absent parameter at the measurement's own default, which is what an unset parameter
    means, whereas null is refused outright by the int/float/dropdown setters.
    """
    parameters = {}
    for name, parameter in measurement.parameters.items():
        if parameter.value is None:
            continue
        parameters[name] = _json_safe(parameter.value, index, name)
    return parameters


def _serialize_instruments(measurement) -> dict:
    """The measurement's selected instrument descriptors, copied so the queue owns them."""
    return copy.deepcopy(measurement.selected_instruments)


def _serialize_entry(entry, index: int, include_instruments: bool = True) -> dict:
    """Turns one queue entry object back into the dict form `build_entries` reads.

    Dispatches on `entry_type` rather than the class, since that attribute is exactly the
    JSON "type" field and keeps this symmetric with the loader.
    """
    entry_type = getattr(entry, "entry_type", None)

    if entry_type == "move":
        if entry.device is None:
            raise QueueSaveError(f"entry {index}: a move step must name a device.")
        return {"type": "move", "device_id": str(entry.device.id)}

    if entry_type == "sfp":
        # a search for peak may run wherever the stages already are, and the loader treats a
        # missing device_id as exactly that, so leave the key out rather than writing null
        saved = {"type": "sfp"}
        if entry.device is not None:
            saved["device_id"] = str(entry.device.id)
        return saved

    if entry_type == "meas":
        if entry.device is None:
            raise QueueSaveError(f"entry {index}: a measurement must name a device.")
        if entry.measurement is None:
            raise QueueSaveError(f"entry {index}: measurement entry carries no measurement.")

        # experiment.measurements_classes is keyed by class name, so that is what the loader
        # looks up. measurement.name is a display name and is often different - every Luna
        # variant calls itself 'Luna_sweep' - which would reload as an unknown measurement.
        saved = {
            "type": "meas",
            "device_id": str(entry.device.id),
            "measurement": type(entry.measurement).__name__,
            "parameters": _serialize_parameters(entry.measurement, index),
            "auto_align": bool(entry.auto_align),
        }
        if include_instruments:
            instruments = _serialize_instruments(entry.measurement)
            if instruments:
                saved["instruments"] = instruments
        return saved

    raise QueueSaveError(
        f"entry {index}: cannot save entry of type {entry_type!r}, expected 'move', 'sfp' or 'meas'."
    )


def serialize_queue(entries: List, chip_name: str = None, include_instruments: bool = True) -> dict:
    """Turns queue entry objects into the queue-file dict. Mirror of `build_entries`.

    Pure: writes nothing and validates nothing, so a caller can inspect or diff the result.

    Args:
        entries: The queue entries, in execution order.
        chip_name: Recorded in the file for the reader's benefit. The loader does not check
            it, but a queue naming its chip is a good deal easier to place later on.
        include_instruments: Whether to record each measurement's instrument selection. With
            this off, reloading rebinds instruments from the Experiment Wizard's last
            selection for that measurement class instead.
    """
    return {
        "labext_queue_version": QUEUE_FILE_VERSION,
        "chip_name": chip_name or "",
        "entries": [
            _serialize_entry(entry, index, include_instruments) for index, entry in enumerate(entries)
        ],
    }


def queue_save_warnings(entries: List, include_instruments: bool = True) -> List[str]:
    """Non-fatal warnings about saving this queue. Mirror of `validate_queue_warnings`."""
    warnings = []

    swept = sum(1 for entry in entries if getattr(entry, "part_of_sweep", False))
    if swept:
        warnings.append(
            f"{swept} entries belong to a parameter sweep. A queue file has no way to express "
            f"a sweep, so they are saved as individual measurements: reloading gives {swept} "
            f"independent measurements with the same settings, but no shared sweep summary "
            f"file and no common results subfolder."
        )

    if include_instruments:
        for entry in entries:
            measurement = getattr(entry, "measurement", None)
            if measurement is None:
                continue
            for descriptor in measurement.selected_instruments.values():
                args = descriptor.get("args") or {}
                if any(key.lower() in SENSITIVE_ARG_KEYS for key in args):
                    warnings.append(
                        "The saved queue contains instrument login details in plain text, "
                        "because it records which instrument each measurement uses. Treat the "
                        "file accordingly."
                    )
                    break
            else:
                continue
            break

    return warnings


def save_queue_file(
    file_path: str, entries: List, experiment_manager, include_instruments: bool = True
) -> List[str]:
    """Validates, serialises and writes an experiment queue file. Mirror of `load_queue_file`.

    Raises `QueueSaveError` if the queue would produce a file the loader refuses, so saving
    is all-or-nothing - the point of saving is to reload later, and a file that will not
    reload would only be discovered after the restart it was meant to survive. Nothing is
    written until the whole queue has serialised.

    Returns:
        Non-fatal warnings, for the caller to show.
    """
    logger = logging.getLogger()

    errors = validate_queue(entries, chip=getattr(experiment_manager, "chip", None))
    if errors:
        raise QueueSaveError("\n".join(errors))

    warnings = queue_save_warnings(entries, include_instruments)
    for warning in warnings:
        logger.warning("Experiment queue: %s", warning)

    chip = getattr(experiment_manager, "chip", None)
    queue_data = serialize_queue(
        entries, chip_name=getattr(chip, "name", None), include_instruments=include_instruments
    )

    with open(file_path, "w") as json_file:
        json.dump(queue_data, json_file, indent=QUEUE_SAVE_INDENT)

    logger.info("Saved experiment queue with %d entries to %s", len(entries), file_path)
    return warnings
