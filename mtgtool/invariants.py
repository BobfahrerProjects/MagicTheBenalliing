"""Checks that run on every command.

Deterministic, offline, milliseconds, no API key. Each one exists because
something could silently go wrong, and the bar for adding another is simple: it
must be a check that would have failed on the day the bug landed.

Anything a script can verify must never be left to a human reading a diff.
"""
from __future__ import annotations

import json
import os
from collections import defaultdict
from dataclasses import dataclass
from typing import Callable, List, Optional

from . import db, decks, identity, manabox, paths, vocab


@dataclass
class Result:
    name: str
    ok: bool
    detail: str = ""
    fatal: bool = True

    @property
    def symbol(self) -> str:
        if self.ok:
            return "ok  "
        return "FAIL" if self.fatal else "warn"


def _snapshot(conn, snapshot_id=None):
    return snapshot_id or db.latest_snapshot_id(conn)


def check_aggregation_lossless(conn, snapshot_id=None) -> Result:
    """Summing duplicate lot rows must never lose or invent a card."""
    snapshot_id = _snapshot(conn, snapshot_id)
    row = conn.execute(
        "SELECT csv_cards, (SELECT COALESCE(SUM(quantity),0) FROM lots"
        "  WHERE snapshot_id = s.snapshot_id) AS lot_cards"
        "  FROM snapshots s WHERE snapshot_id = ?", (snapshot_id,)).fetchone()
    if row is None:
        return Result("aggregation is lossless", False, "no snapshot imported")
    if row["csv_cards"] != row["lot_cards"]:
        return Result("aggregation is lossless", False,
                      "CSV holds {} cards, lots hold {}".format(
                          row["csv_cards"], row["lot_cards"]))
    return Result("aggregation is lossless", True,
                  "{:,} cards".format(row["csv_cards"]))


def check_proxy_markers_agree(conn, snapshot_id=None) -> Result:
    """Re-derive proxy status from the snapshot file and confirm it still agrees."""
    snapshot_id = _snapshot(conn, snapshot_id)
    row = conn.execute("SELECT filename FROM snapshots WHERE snapshot_id = ?",
                       (snapshot_id,)).fetchone()
    if row is None:
        return Result("proxy markers agree", False, "no snapshot imported")
    path = os.path.join(paths.SNAPSHOTS, row["filename"])
    if not os.path.exists(path):
        return Result("proxy markers agree", False,
                      "snapshot file {} is missing".format(row["filename"]))
    parsed = manabox.parse(path)
    if parsed.conflicts:
        return Result("proxy markers agree", False,
                      "{} lot(s) disagree; run `mtg doctor` for detail"
                      .format(len(parsed.conflicts)))
    stored = conn.execute(
        "SELECT COALESCE(SUM(quantity),0) AS n FROM lots"
        " WHERE snapshot_id = ? AND is_proxy = 1 AND is_wishlist = 0",
        (snapshot_id,)).fetchone()["n"]
    fresh = sum(l.quantity for k, l in parsed.lots.items()
                if parsed.proxy_flags.get(k) and not l.is_wishlist)
    if stored != fresh:
        return Result("proxy markers agree", False,
                      "database says {} proxy cards, the export says {}"
                      .format(stored, fresh))
    return Result("proxy markers agree", True, "{} proxy cards".format(fresh))


def check_deck_names_match_ids(conn, snapshot_id=None) -> Result:
    """The identification guarantee, checked rather than assumed.

    A stored deck entry carries both a Scryfall ID and the name it was created
    from. If those ever disagree, the plan is pointing at a different card than
    it claims -- exactly the mistake that must never reach a sleeve.
    """
    problems = []
    for deck in decks.load_all():
        for card in deck.cards:
            mismatch = identity.verify_name(conn, card.scryfall_id, card.name)
            if mismatch:
                problems.append("{}: {}".format(deck.name, mismatch))
    if problems:
        return Result("deck names match their card IDs", False,
                      "; ".join(problems[:5]))
    total = sum(len(d.cards) for d in decks.load_all())
    return Result("deck names match their card IDs", True,
                  "{} entries verified".format(total))


def check_no_card_over_allocated(conn, snapshot_id=None) -> Result:
    """One physical card, one deck."""
    found = decks.contentions(conn)
    if found:
        names = ", ".join("{} (need {}, own {})".format(c.name, c.demanded, c.owned)
                          for c in found[:5])
        return Result("no card is claimed by two decks", False, names, fatal=False)
    return Result("no card is claimed by two decks", True)


def check_assignments_exist(conn, snapshot_id=None) -> Result:
    """Every planned card must still be a card in the current snapshot."""
    snapshot_id = _snapshot(conn, snapshot_id)
    known = {r["scryfall_id"] for r in conn.execute(
        "SELECT DISTINCT scryfall_id FROM lots WHERE snapshot_id = ?", (snapshot_id,))}
    missing = []
    for deck in decks.load_all():
        for card in deck.cards:
            if card.status in ("assigned", "contested") and card.scryfall_id not in known:
                missing.append("{}: {}".format(deck.name, card.name))
    if missing:
        return Result("planned cards exist in the collection", False,
                      "; ".join(missing[:5]), fatal=False)
    return Result("planned cards exist in the collection", True)


def check_art_tags_reference_real_art(conn, snapshot_id=None) -> Result:
    orphans = conn.execute(
        "SELECT COUNT(DISTINCT t.illustration_id) AS n FROM art_tags t"
        " WHERE NOT EXISTS (SELECT 1 FROM card_faces f"
        "                    WHERE f.illustration_id = t.illustration_id)"
    ).fetchone()["n"]
    if orphans:
        return Result("art tags reference real artwork", False,
                      "{} tagged artwork(s) are not in the collection".format(orphans),
                      fatal=False)
    return Result("art tags reference real artwork", True)


def check_tags_are_in_vocabulary(conn, snapshot_id=None) -> Result:
    """A tag outside the vocabulary means search will silently miss cards."""
    bad = []
    for row in conn.execute("SELECT DISTINCT facet, tag FROM art_tags"):
        if not vocab.is_valid(row["facet"], row["tag"]):
            bad.append("{}={}".format(row["facet"], row["tag"]))
    if bad:
        return Result("tags come from the vocabulary", False, ", ".join(bad[:8]))
    total = conn.execute("SELECT COUNT(*) AS n FROM art_tags").fetchone()["n"]
    return Result("tags come from the vocabulary", True, "{} tags".format(total))


def check_wishlists_are_excluded(conn, snapshot_id=None) -> Result:
    """Lists are wishlists. They must never reach ownership or value."""
    snapshot_id = _snapshot(conn, snapshot_id)
    leaked = conn.execute(
        "SELECT COUNT(*) AS n FROM lots"
        " WHERE snapshot_id = ? AND binder_type = 'list' AND is_wishlist = 0",
        (snapshot_id,)).fetchone()["n"]
    if leaked:
        return Result("wishlists are excluded", False,
                      "{} list row(s) are not flagged as wishlist".format(leaked))
    counted = conn.execute(
        "SELECT COALESCE(SUM(quantity),0) AS n FROM lots"
        " WHERE snapshot_id = ? AND is_wishlist = 1", (snapshot_id,)).fetchone()["n"]
    return Result("wishlists are excluded", True,
                  "{} wishlist cards held aside".format(counted))


def check_deck_files_parse(conn, snapshot_id=None) -> Result:
    """The JSON deck files are the durable copy; they must always load."""
    try:
        loaded = decks.load_all()
    except Exception as exc:  # noqa: BLE001 - surfacing any load failure is the point
        return Result("deck files load", False, str(exc))
    return Result("deck files load", True, "{} deck(s)".format(len(loaded)))


CHECKS: List[Callable] = [
    check_aggregation_lossless,
    check_proxy_markers_agree,
    check_wishlists_are_excluded,
    check_deck_files_parse,
    check_deck_names_match_ids,
    check_assignments_exist,
    check_no_card_over_allocated,
    check_art_tags_reference_real_art,
    check_tags_are_in_vocabulary,
]


def run_all(conn, snapshot_id=None) -> List[Result]:
    results = []
    for check in CHECKS:
        try:
            results.append(check(conn, snapshot_id))
        except Exception as exc:  # noqa: BLE001
            results.append(Result(check.__name__, False,
                                  "check itself failed: {}".format(exc)))
    return results


def format_results(results: List[Result]) -> str:
    lines = []
    for result in results:
        line = "  [{}] {}".format(result.symbol, result.name)
        if result.detail:
            line += " -- " + result.detail
        lines.append(line)
    failed = [r for r in results if not r.ok and r.fatal]
    warned = [r for r in results if not r.ok and not r.fatal]
    lines.append("")
    if failed:
        lines.append("{} check(s) FAILED.".format(len(failed)))
    elif warned:
        lines.append("All structural checks passed; {} thing(s) need your attention."
                     .format(len(warned)))
    else:
        lines.append("All {} checks passed.".format(len(results)))
    return "\n".join(lines)


def worst_exit_code(results: List[Result]) -> int:
    if any(not r.ok and r.fatal for r in results):
        return 2
    if any(not r.ok for r in results):
        return 1
    return 0
