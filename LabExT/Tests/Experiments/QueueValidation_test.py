#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
LabExT  Copyright (C) 2021  ETH Zurich and Polariton Technologies AG
This program is free software and comes with ABSOLUTELY NO WARRANTY;
for details see LICENSE file.
"""

import unittest
from unittest.mock import Mock

from LabExT.Experiments.QueueValidation import (
    INPUT_LOCATION_TOLERANCE_UM,
    input_locations_match,
    split_into_blocks,
    validate_queue,
    validate_queue_warnings,
)
from LabExT.Experiments.ToDo import MoveEntry, SfpEntry, ToDo
from LabExT.Wafer.Device import Device


def make_todo(device: Device) -> ToDo:
    """A ToDo with a stand-in measurement - the validator only looks at devices."""
    measurement = Mock()
    measurement.get_name_with_id.return_value = "MockMeas"
    return ToDo(device=device, measurement=measurement, auto_align=False)


class InputLocationsMatchTest(unittest.TestCase):

    def test_identical_positions_match(self):
        self.assertTrue(input_locations_match([100.0, 200.0], [100.0, 200.0]))

    def test_difference_just_under_tolerance_matches(self):
        offset = INPUT_LOCATION_TOLERANCE_UM * 0.9
        self.assertTrue(input_locations_match([100.0, 200.0], [100.0 + offset, 200.0]))

    def test_difference_over_tolerance_does_not_match(self):
        offset = INPUT_LOCATION_TOLERANCE_UM * 1.1
        self.assertFalse(input_locations_match([100.0, 200.0], [100.0 + offset, 200.0]))

    def test_differing_lengths_do_not_match(self):
        self.assertFalse(input_locations_match([100.0, 200.0], [100.0]))


class SplitIntoBlocksTest(unittest.TestCase):

    def setUp(self):
        self.device = Device(id="1", type="dev", in_position=[0.0, 0.0], out_position=[10.0, 0.0])

    def test_alignment_steps_delimit_blocks(self):
        entries = [
            MoveEntry(self.device),
            SfpEntry(),
            make_todo(self.device),
            make_todo(self.device),
            MoveEntry(self.device),
            make_todo(self.device),
        ]
        blocks = split_into_blocks(entries)
        self.assertEqual([2, 1], [len(block) for block in blocks])
        self.assertEqual([2, 3], [index for index, _ in blocks[0]])

    def test_empty_queue_has_no_blocks(self):
        self.assertEqual([], split_into_blocks([]))

    def test_alignment_only_queue_has_no_blocks(self):
        self.assertEqual([], split_into_blocks([MoveEntry(self.device), SfpEntry()]))


class ValidateQueueTest(unittest.TestCase):

    def setUp(self):
        # two distinct devices sharing one input coupler, plus one somewhere else entirely
        self.device_a = Device(id="1", type="dev", in_position=[100.0, 200.0], out_position=[150.0, 200.0])
        self.device_a_sibling = Device(id="2", type="dev", in_position=[100.0, 200.0], out_position=[150.0, 260.0])
        self.device_far = Device(id="3", type="dev", in_position=[900.0, 200.0], out_position=[950.0, 200.0])

    def test_distinct_devices_sharing_input_location_pass(self):
        entries = [
            MoveEntry(self.device_a),
            SfpEntry(),
            make_todo(self.device_a),
            make_todo(self.device_a_sibling),
        ]
        self.assertEqual([], validate_queue(entries))

    def test_different_input_location_in_same_block_fails(self):
        entries = [
            MoveEntry(self.device_a),
            SfpEntry(),
            make_todo(self.device_a),
            make_todo(self.device_far),
        ]
        errors = validate_queue(entries)
        self.assertEqual(1, len(errors))
        self.assertIn("entry 3", errors[0])
        self.assertIn("'3'", errors[0])

    def test_different_input_location_passes_when_separated_by_alignment(self):
        entries = [
            MoveEntry(self.device_a),
            SfpEntry(),
            make_todo(self.device_a),
            MoveEntry(self.device_far),
            SfpEntry(),
            make_todo(self.device_far),
        ]
        self.assertEqual([], validate_queue(entries))

    def test_difference_just_under_tolerance_passes(self):
        offset = INPUT_LOCATION_TOLERANCE_UM * 0.9
        nudged = Device(
            id="4", type="dev", in_position=[100.0 + offset, 200.0], out_position=[150.0, 200.0]
        )
        entries = [MoveEntry(self.device_a), make_todo(self.device_a), make_todo(nudged)]
        self.assertEqual([], validate_queue(entries))

    def test_empty_queue_is_valid(self):
        self.assertEqual([], validate_queue([]))

    def test_unknown_device_id_fails(self):
        chip = Mock()
        chip.name = "TestChip"
        chip.devices = {"1": self.device_a}
        errors = validate_queue([MoveEntry(self.device_far)], chip=chip)
        self.assertEqual(1, len(errors))
        self.assertIn("is not on chip", errors[0])


class QueueEntryHashTest(unittest.TestCase):
    """The ToDo table uses get_hash() as the Treeview item id, which must be unique.

    Entries built in a tight loop (as the queue loader does) all land on the same
    time.time() value on Windows, so the hash cannot rely on the timestamp alone.
    """

    def setUp(self):
        self.device = Device(id="1", type="dev", in_position=[0.0, 0.0], out_position=[10.0, 0.0])

    def test_many_sfp_entries_have_unique_hashes(self):
        hashes = [SfpEntry().get_hash() for _ in range(50)]
        self.assertEqual(len(hashes), len(set(hashes)))

    def test_repeated_moves_to_same_device_have_unique_hashes(self):
        hashes = [MoveEntry(self.device).get_hash() for _ in range(50)]
        self.assertEqual(len(hashes), len(set(hashes)))

    def test_todos_have_unique_hashes(self):
        hashes = [make_todo(self.device).get_hash() for _ in range(50)]
        self.assertEqual(len(hashes), len(set(hashes)))

    def test_mixed_queue_entries_have_unique_hashes(self):
        entries = []
        for _ in range(20):
            entries += [MoveEntry(self.device), SfpEntry(), make_todo(self.device)]
        hashes = [e.get_hash() for e in entries]
        self.assertEqual(len(hashes), len(set(hashes)))

    def test_hash_is_stable_across_calls(self):
        # the table re-derives the hash on every regenerate() to diff against what it shows
        for entry in [MoveEntry(self.device), SfpEntry(), make_todo(self.device)]:
            self.assertEqual(entry.get_hash(), entry.get_hash())


class ValidateQueueWarningsTest(unittest.TestCase):

    def setUp(self):
        self.device = Device(id="1", type="dev", in_position=[0.0, 0.0], out_position=[10.0, 0.0])

    def test_measurement_before_first_checkpoint_warns(self):
        warnings = validate_queue_warnings([make_todo(self.device), MoveEntry(self.device)])
        self.assertEqual(1, len(warnings))

    def test_queue_starting_with_alignment_does_not_warn(self):
        warnings = validate_queue_warnings([MoveEntry(self.device), make_todo(self.device)])
        self.assertEqual([], warnings)


if __name__ == "__main__":
    unittest.main()
