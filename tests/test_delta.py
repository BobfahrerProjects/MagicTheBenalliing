"""Delta and move detection.

Every test here has a negative control: it is written so that it would fail if the
code under test were replaced by something trivial. A delta test that only ever
compares a snapshot to itself would pass against `return []`.
"""
from __future__ import annotations

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from mtgtool.importer import compute_delta  # noqa: E402

SID_A = "aaaaaaaa-0000-0000-0000-000000000001"
SID_B = "bbbbbbbb-0000-0000-0000-000000000002"


def lot(binder, sid, binder_type="binder", foil="normal", condition="near_mint"):
    return (binder, binder_type, sid, foil, condition, "en", 0, 0)


def by_type(changes):
    out = {}
    for change in changes:
        out.setdefault(change["change_type"], []).append(change)
    return out


class TestDelta(unittest.TestCase):

    def test_identical_snapshots_produce_no_changes(self):
        state = {lot("Pool - Old", SID_A): 3, lot("Hazel", SID_B, "deck"): 1}
        self.assertEqual(compute_delta(state, dict(state)), [])

    def test_negative_control_identical_test_would_catch_a_stub(self):
        """Guards the test above: a differing pair MUST produce changes.

        Without this, `compute_delta = lambda a, b: []` would pass the whole file.
        """
        before = {lot("Pool - Old", SID_A): 3}
        after = {lot("Pool - Old", SID_A): 4}
        self.assertNotEqual(compute_delta(before, after), [])

    def test_new_card_is_added(self):
        changes = by_type(compute_delta({}, {lot("ToSort", SID_A): 2}))
        self.assertEqual(len(changes["added"]), 1)
        self.assertEqual(changes["added"][0]["qty_delta"], 2)
        self.assertEqual(changes["added"][0]["binder_to"], "ToSort")

    def test_vanished_card_is_removed(self):
        changes = by_type(compute_delta({lot("ToSort", SID_A): 2}, {}))
        self.assertEqual(len(changes["removed"]), 1)
        self.assertEqual(changes["removed"][0]["qty_delta"], 2)

    def test_more_copies_in_same_binder_is_qty_up(self):
        changes = by_type(compute_delta(
            {lot("Pool - Old", SID_A): 1}, {lot("Pool - Old", SID_A): 4}))
        self.assertIn("qty_up", changes)
        self.assertNotIn("added", changes)
        self.assertEqual(changes["qty_up"][0]["qty_delta"], 3)

    def test_fewer_copies_in_same_binder_is_qty_down(self):
        changes = by_type(compute_delta(
            {lot("Pool - Old", SID_A): 4}, {lot("Pool - Old", SID_A): 1}))
        self.assertIn("qty_down", changes)
        self.assertNotIn("removed", changes)
        self.assertEqual(changes["qty_down"][0]["qty_delta"], 3)

    def test_building_a_card_into_a_deck_is_one_move(self):
        """The case the whole design turns on.

        Pool -> deck must be a single `moved`, never an `added` plus a `removed`.
        If it split, every deck-building session would look like collection churn.
        """
        before = {lot("Pool - Old", SID_A): 1}
        after = {lot("Hazel", SID_A, "deck"): 1}
        changes = compute_delta(before, after)
        self.assertEqual(len(changes), 1)
        self.assertEqual(changes[0]["change_type"], "moved")
        self.assertEqual(changes[0]["binder_from"], "Pool - Old")
        self.assertEqual(changes[0]["binder_to"], "Hazel")
        self.assertEqual(changes[0]["qty_delta"], 1)

    def test_partial_move_leaves_the_rest_in_place(self):
        """2 of 3 copies go into a deck: one move, and nothing else."""
        before = {lot("Pool - Old", SID_A): 3}
        after = {lot("Pool - Old", SID_A): 1, lot("Nata", SID_A, "deck"): 2}
        changes = compute_delta(before, after)
        self.assertEqual(len(changes), 1)
        self.assertEqual(changes[0]["change_type"], "moved")
        self.assertEqual(changes[0]["qty_delta"], 2)

    def test_move_plus_genuine_acquisition_are_reported_separately(self):
        """Moving copies out of a pool while also buying more.

        Pool 3 -> Pool 1 + Hazel 1 + ToSort 3. Two copies genuinely left the pool
        (so: moves) and two more were acquired (so: added). Quantity must balance:
        3 before, 5 after.
        """
        before = {lot("Pool - Old", SID_A): 3}
        after = {lot("Pool - Old", SID_A): 1,
                 lot("Hazel", SID_A, "deck"): 1,
                 lot("ToSort", SID_A): 3}
        changes = by_type(compute_delta(before, after))
        self.assertEqual(sum(c["qty_delta"] for c in changes["moved"]), 2)
        self.assertEqual(sum(c["qty_delta"] for c in changes["added"]), 2)
        self.assertTrue(all(c["binder_from"] == "Pool - Old" for c in changes["moved"]))

    def test_growth_without_a_source_decrease_is_not_a_move(self):
        """Honesty about ambiguity.

        Pool 1 -> Pool 2 + Hazel 1. The pool never shrank, so nothing demonstrably
        moved; two copies were acquired. Reporting a move here would invent an
        event the export does not evidence.
        """
        before = {lot("Pool - Old", SID_A): 1}
        after = {lot("Hazel", SID_A, "deck"): 1, lot("Pool - Old", SID_A): 2}
        changes = by_type(compute_delta(before, after))
        self.assertNotIn("moved", changes)
        self.assertEqual(changes["added"][0]["binder_to"], "Hazel")
        self.assertEqual(changes["qty_up"][0]["qty_delta"], 1)

    def test_different_finishes_are_different_cards(self):
        """A foil is not a swap for a non-foil; this must not pair as a move."""
        before = {lot("Pool - Old", SID_A, foil="normal"): 1}
        after = {lot("Pool - Old", SID_A, foil="foil"): 1}
        changes = by_type(compute_delta(before, after))
        self.assertNotIn("moved", changes)
        self.assertIn("added", changes)
        self.assertIn("removed", changes)

    def test_two_cards_do_not_pair_across_identities(self):
        """Card A leaving and card B arriving is not one card moving."""
        before = {lot("Pool - Old", SID_A): 1}
        after = {lot("Hazel", SID_B, "deck"): 1}
        changes = by_type(compute_delta(before, after))
        self.assertNotIn("moved", changes)
        self.assertEqual(changes["removed"][0]["scryfall_id"], SID_A)
        self.assertEqual(changes["added"][0]["scryfall_id"], SID_B)

    def test_move_across_three_binders_conserves_quantity(self):
        before = {lot("Pool - Old", SID_A): 2, lot("Lands", SID_A): 2}
        after = {lot("Hazel", SID_A, "deck"): 2, lot("Nata", SID_A, "deck"): 2}
        changes = compute_delta(before, after)
        self.assertTrue(all(c["change_type"] == "moved" for c in changes))
        self.assertEqual(sum(c["qty_delta"] for c in changes), 4)


if __name__ == "__main__":
    unittest.main(verbosity=2)
