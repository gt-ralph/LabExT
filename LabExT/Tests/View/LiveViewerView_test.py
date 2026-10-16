#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
LabExT  Copyright (C) 2021  ETH Zurich and Polariton Technologies AG
This program is free software and comes with ABSOLUTELY NO WARRANTY; for details see LICENSE file.

Tests for the live viewer's card rendering. No Tk window and no instruments: set_cards is driven
against a stand-in, because what is being tested is which cards survive a failure, not how they
are drawn.
"""

import unittest
from unittest import mock

from LabExT.View.LiveViewer.LiveViewerView import ControlFrame


class StubCard:
    """A card that builds."""

    def __init__(self, parent, controller, model):
        self.packed = False
        self.settings_enabled = False

    def pack(self, **kwargs):
        self.packed = True

    def enable_settings_interaction(self):
        self.settings_enabled = True


class UnbuildableCard:
    """A card whose instrument type has no section in instruments.config.

    This is what SwitchCard is on a bench with no switch: CardFrame.__init__ calls
    get_visa_address('Switch'), which raises RuntimeError for a missing section.
    """

    def __init__(self, parent, controller, model):
        raise RuntimeError("Fatal Error, no instrument type Switch found!")


class SetCardsTest(unittest.TestCase):
    """
    Tests that one unbuildable card does not take the whole live viewer down with it.

    Required lab setup: none, only SW testing
    """

    user_input_required = False

    def make_view(self, card_titles):
        """A stand-in carrying only what set_cards touches."""
        view = mock.MagicMock()
        view.model.cards = [(title, None) for title in card_titles]
        view.model.lvcards_classes = {'Power Meter': StubCard,
                                      'Laser': StubCard,
                                      'Switch': UnbuildableCard}
        view.canvas.bbox.return_value = (0, 0, 100, 100)
        return view

    def test_a_card_whose_role_is_missing_is_dropped_and_the_rest_survive(self):
        view = self.make_view(['Power Meter', 'Switch', 'Laser'])

        with mock.patch('LabExT.View.LiveViewer.LiveViewerView.showerror') as showerror:
            ControlFrame.set_cards(view)

        titles = [title for title, _ in view.model.cards]
        self.assertEqual(['Power Meter', 'Laser'], titles)
        # nothing downstream copes with a (title, None) entry, so none may be left behind
        self.assertTrue(all(card is not None for _, card in view.model.cards))
        # and the user is told which card went missing, and why
        self.assertEqual(1, showerror.call_count)
        message = showerror.call_args[0][1]
        self.assertIn('Switch', message)
        self.assertIn('instruments.config', message)

    def test_the_cards_that_did_build_are_rendered_and_enabled(self):
        view = self.make_view(['Power Meter', 'Switch'])

        with mock.patch('LabExT.View.LiveViewer.LiveViewerView.showerror'):
            ControlFrame.set_cards(view)

        card = view.model.cards[0][1]
        self.assertTrue(card.packed)
        self.assertTrue(card.settings_enabled)

    def test_nothing_is_reported_when_every_card_builds(self):
        view = self.make_view(['Power Meter', 'Laser'])

        with mock.patch('LabExT.View.LiveViewer.LiveViewerView.showerror') as showerror:
            ControlFrame.set_cards(view)

        self.assertEqual(0, showerror.call_count)
        self.assertEqual(2, len(view.model.cards))

    def test_already_built_cards_are_left_alone(self):
        view = self.make_view(['Power Meter'])
        existing = StubCard(None, None, None)
        view.model.cards = [('Power Meter', existing)]

        with mock.patch('LabExT.View.LiveViewer.LiveViewerView.showerror'):
            ControlFrame.set_cards(view)

        self.assertIs(existing, view.model.cards[0][1])
        self.assertFalse(existing.packed)

    def test_every_card_failing_leaves_an_empty_but_usable_viewer(self):
        view = self.make_view(['Switch', 'Switch'])

        with mock.patch('LabExT.View.LiveViewer.LiveViewerView.showerror') as showerror:
            ControlFrame.set_cards(view)

        self.assertEqual([], view.model.cards)
        self.assertEqual(1, showerror.call_count)


if __name__ == '__main__':
    unittest.main()
