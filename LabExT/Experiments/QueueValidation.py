#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
LabExT  Copyright (C) 2021  ETH Zurich and Polariton Technologies AG
This program is free software and comes with ABSOLUTELY NO WARRANTY; for details see LICENSE file.
"""

from typing import List

from LabExT.Experiments.ToDo import MoveEntry, SfpEntry, ToDo

# Two devices are considered to sit at the same input location - and therefore to be
# measurable without re-aligning between them - if every coordinate of their input
# position differs by no more than this. Chip files store input positions in um, and
# devices sharing one input grating coupler normally carry the identical coordinate, so
# this only has to absorb float noise and minor per-device bookkeeping differences.
INPUT_LOCATION_TOLERANCE_UM = 1.0


def input_locations_match(first: List[float], second: List[float]) -> bool:
    """Whether two device input positions are the same to within INPUT_LOCATION_TOLERANCE_UM."""
    if len(first) != len(second):
        return False
    return all(abs(a - b) <= INPUT_LOCATION_TOLERANCE_UM for a, b in zip(first, second))


def split_into_blocks(entries: list) -> List[List[tuple]]:
    """Splits a queue into blocks of measurements delimited by alignment steps.

    A `MoveEntry` or `SfpEntry` closes the block before it, since the stages are
    (re-)positioned there. So does a ToDo that aligns itself: `auto_align` means the global
    auto-move/auto-sfp settings position the stages before that measurement, which is an
    alignment step like any other, just not a separate queue entry. Without that a queue
    built in the GUI - which carries no explicit move/sfp entries at all - would be read as
    one block spanning every device on the chip.

    Returns one list of `(queue_index, ToDo)` pairs per block; blocks containing no
    measurements are omitted.
    """
    blocks = []
    current = []
    for index, entry in enumerate(entries):
        if isinstance(entry, (MoveEntry, SfpEntry)):
            if current:
                blocks.append(current)
                current = []
            continue
        if getattr(entry, "auto_align", False) and current:
            blocks.append(current)
            current = []
        current.append((index, entry))
    if current:
        blocks.append(current)
    return blocks


def validate_queue(entries: list, chip=None) -> List[str]:
    """Validates a loaded experiment queue and returns a list of human-readable errors.

    The central check is that every measurement inside one block - i.e. between two
    alignment steps - targets a device at the same input location. The stages are aligned
    once per block, so a measurement on a device whose input coupler sits somewhere else
    would run misaligned. Note this deliberately allows *different* devices in one block
    as long as they share an input location, which is the normal case for several devices
    fed by one input grating coupler.

    Measurement classes and parameter names are not checked here - they are resolved (and
    rejected with a better message) when the loader instantiates the measurement.

    Args:
        entries: The queue entries, in execution order.
        chip: Optional `Chip`, used to check that referenced device ids exist.

    Returns:
        A list of error strings; empty means the queue is valid.
    """
    errors = []

    for index, entry in enumerate(entries):
        if chip is not None and entry.device is not None and entry.device.id not in chip.devices:
            errors.append(f"entry {index}: device id '{entry.device.id}' is not on chip '{chip.name}'.")

    for block in split_into_blocks(entries):
        reference_index, reference_todo = block[0]
        reference_position = reference_todo.device.in_position
        for index, todo in block[1:]:
            if not input_locations_match(reference_position, todo.device.in_position):
                errors.append(
                    f"entry {index}: device '{todo.device.id}' at input location "
                    f"{list(todo.device.in_position)} does not match device "
                    f"'{reference_todo.device.id}' at {list(reference_position)} "
                    f"(entry {reference_index}) in the same block. Measurements between two "
                    f"alignment steps must share an input location - insert a move/sfp step "
                    f"between them, or the second device will be measured misaligned."
                )

    return errors


def validate_queue_warnings(entries: list) -> List[str]:
    """Returns non-fatal warnings about a queue (things worth telling the user, but not refusing)."""
    warnings = []
    for entry in entries:
        if isinstance(entry, (MoveEntry, SfpEntry)):
            break
        if isinstance(entry, ToDo):
            if getattr(entry, "auto_align", False):
                # this one aligns itself before running, so there is nothing to warn about
                break
            warnings.append(
                "The queue starts with a measurement before any move/sfp step: it will run at "
                "whatever position the stages currently hold."
            )
            break
    return warnings
