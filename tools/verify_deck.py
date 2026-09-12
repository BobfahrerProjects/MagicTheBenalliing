#!/usr/bin/env python3
"""Check a 60-card deck plan against the collection it claims to be built from.

Every rule here is one a human reading the list would get wrong. The deck is a
gift built from someone else's collection: "this card is available" and "this
card is legal in a 60-card deck" are both claims, and a claim nobody executed is
not evidence. Run this, read the output, and only then believe the decklist.

    python3 tools/verify_deck.py data/decks/mardu-cats.json

Exit status is 0 only when every check passes.
"""
from __future__ import annotations

import argparse
import glob
import json
import os
import sqlite3
import sys
from collections import defaultdict

_SIBLING = os.path.join(
    os.path.dirname(os.path.abspath(__file__)), os.pardir, os.pardir,
    "magic-collection-manager-856d48")
DEFAULT_DB = os.path.join(_SIBLING, "collection.db")
# The other deck plans live with the tool, on its own branch. Checking against an
# empty directory would make the contested-card check pass for the wrong reason,
# so the default points at the real plans and the count is reported either way.
DEFAULT_DECKS = os.path.join(_SIBLING, "data", "decks")
# mtgtool itself, for the one check our own code cannot honestly make: whether
# the ManaBox CSV we wrote is readable by the parser that will actually read it.
MTGTOOL_ROOT = _SIBLING

BASIC_LANDS = {"Plains", "Island", "Swamp", "Mountain", "Forest", "Wastes"}

# Cards whose only ability needs a commander. Colour identity cannot see this,
# and the proxy binder is full of them, so they get named outright.
NEEDS_COMMANDER = {"Command Tower", "Path of Ancestry", "Arcane Signet",
                   "Commander's Sphere", "Opal Palace"}

# Slot statuses that consume a real owned copy, mirroring mtgtool/decks.py.
CONSUMING = ("assigned", "contested")


def _name_of(cards, oracle_id):
    for c in cards:
        if c["oracle_id"] == oracle_id:
            return c["name"]
    return oracle_id


class Checker:
    def __init__(self):
        self.failures = []
        self.passes = []

    def check(self, name, ok, detail=""):
        (self.passes if ok else self.failures).append((name, detail))
        print("  {} {}{}".format("PASS" if ok else "FAIL", name,
                                 ("  -- " + detail) if detail else ""))
        return ok


def load_deck(path):
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


def available_stock(conn, exclude_binders):
    """copies of each printing sitting in a binder, split real vs proxy."""
    snap = conn.execute("SELECT MAX(snapshot_id) s FROM lots").fetchone()["s"]
    real, proxy, binders = defaultdict(int), defaultdict(int), defaultdict(set)
    for row in conn.execute(
            "SELECT scryfall_id, binder_name, is_proxy, SUM(quantity) q"
            "  FROM lots"
            " WHERE snapshot_id = ? AND is_wishlist = 0 AND binder_type = 'binder'"
            " GROUP BY scryfall_id, binder_name, is_proxy", (snap,)):
        if row["binder_name"] in exclude_binders:
            continue
        (proxy if row["is_proxy"] else real)[row["scryfall_id"]] += row["q"]
        binders[row["scryfall_id"]].add(row["binder_name"])
    return real, proxy, binders


def total_real_owned(conn):
    """Real copies owned anywhere that is not a wishlist -- deck binders included.

    Availability for *building* excludes deck binders, but the one-card-one-deck
    invariant is about ownership: a card sitting in another deck's binder is
    already paying for that deck's claim on it.
    """
    snap = conn.execute("SELECT MAX(snapshot_id) s FROM lots").fetchone()["s"]
    owned = defaultdict(int)
    for row in conn.execute(
            "SELECT c.oracle_id, SUM(l.quantity) q FROM lots l"
            "  JOIN cards c ON c.scryfall_id = l.scryfall_id"
            " WHERE l.snapshot_id = ? AND l.is_wishlist = 0 AND l.is_proxy = 0"
            " GROUP BY c.oracle_id", (snap,)):
        owned[row["oracle_id"]] += row["q"]
    return owned


def other_deck_claims(decks_dir, this_deck_id):
    claims = defaultdict(list)
    seen = 0
    for path in sorted(glob.glob(os.path.join(decks_dir, "*.json"))):
        blob = json.load(open(path, encoding="utf-8"))
        if blob.get("deck_id") == this_deck_id:
            continue
        seen += 1
        for card in blob.get("cards", []):
            if card.get("status", "assigned") in CONSUMING:
                claims[card["oracle_id"]].append(
                    (blob.get("name", path), int(card.get("quantity", 1))))
    return claims, seen


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("deck")
    ap.add_argument("--db", default=DEFAULT_DB)
    ap.add_argument("--decks-dir", default=DEFAULT_DECKS,
                    help="other deck plans to check against for contested cards")
    ap.add_argument("--colors", default="WBR")
    ap.add_argument("--manabox", default=None,
                    help="ManaBox CSV to validate against mtgtool's own parser")
    ap.add_argument("--min-cards", type=int, default=60)
    args = ap.parse_args()

    if not os.path.exists(args.db):
        sys.exit("no collection database at {}\n"
                 "pass --db; it lives in the magic-collection-manager worktree "
                 "and is not committed.".format(args.db))

    deck = load_deck(args.deck)
    cards = deck.get("cards", [])
    conn = sqlite3.connect(args.db)
    conn.row_factory = sqlite3.Row
    chk = Checker()

    print("{} -- {} rows".format(deck.get("name", args.deck), len(cards)))
    print()

    # 1. size and the four-of rule
    total = sum(c["quantity"] for c in cards)
    chk.check("deck has at least {} cards".format(args.min_cards),
              total >= args.min_cards, "{} cards".format(total))

    by_name = defaultdict(int)
    for c in cards:
        by_name[c["name"]] += c["quantity"]
    over = {n: q for n, q in by_name.items()
            if q > 4 and n.split(" //")[0] not in BASIC_LANDS}
    chk.check("no more than 4 copies of any non-basic card", not over, str(over))

    # 2. every printing is real, and the name/set/number recorded match it
    mismatched, unknown = [], []
    rowcache = {}
    for c in cards:
        row = conn.execute(
            "SELECT name, set_code, collector_number, color_identity, type_line"
            "  FROM cards WHERE scryfall_id = ?", (c["scryfall_id"],)).fetchone()
        if row is None:
            unknown.append(c["name"])
            continue
        rowcache[c["scryfall_id"]] = row
        if (row["name"] != c["name"] or row["set_code"] != c["set"]
                or row["collector_number"] != c["number"]):
            mismatched.append("{} recorded as ({}) {} but is ({}) {}".format(
                c["name"], c["set"], c["number"], row["set_code"],
                row["collector_number"]))
    chk.check("every scryfall id is a card we know", not unknown, str(unknown))
    chk.check("recorded name/set/number match that printing",
              not mismatched, "; ".join(mismatched))

    # 3+4. availability, counting real and proxy stock separately
    staging = set(deck.get("excluded_binders", []))
    real, proxy, binders = available_stock(conn, staging)
    unavailable, short = [], []
    want = defaultdict(int)
    for c in cards:
        want[(c["scryfall_id"], c.get("status", "assigned"))] += c["quantity"]
    for (sid, status), qty in want.items():
        stock = proxy[sid] if status == "proxy" else real[sid]
        name = rowcache[sid]["name"] if sid in rowcache else sid
        if stock == 0:
            unavailable.append("{} ({})".format(name, status))
        elif qty > stock:
            short.append("{}: want {} {}, have {}".format(name, qty, status, stock))
    chk.check("every card is in a binder, not a deck and not a wishlist",
              not unavailable, "; ".join(unavailable))
    chk.check("enough copies of each, real and proxy counted separately",
              not short, "; ".join(short))

    # 5. nothing already claimed by another deck plan
    decks_dir = args.decks_dir
    claims, n_other = other_deck_claims(decks_dir, deck.get("deck_id"))
    chk.check("other deck plans were actually read", n_other > 0,
              "{} plan(s) in {}".format(n_other, decks_dir))
    owned = total_real_owned(conn)
    mine = defaultdict(int)
    for c in cards:
        if c.get("status") in CONSUMING:
            mine[c["oracle_id"]] += c["quantity"]
    over, tight = [], []
    for oid, qty in mine.items():
        others = claims.get(oid, [])
        demanded = qty + sum(q for _, q in others)
        if demanded > owned[oid]:
            over.append("{}: {} wanted across all decks, {} owned (also in {})".format(
                _name_of(cards, oid), demanded, owned[oid],
                ", ".join(sorted({n for n, _ in others}))))
        elif others and demanded == owned[oid]:
            tight.append("{} (also in {})".format(
                _name_of(cards, oid), ", ".join(sorted({n for n, _ in others}))))
    chk.check("no card is claimed by more decks than you own copies",
              not over, "; ".join(over))
    if tight:
        print("  NOTE  shares the last spare copy with another deck: "
              + "; ".join(tight))

    # 6. everything is castable in the deck's colours
    allowed = set(args.colors.upper())
    offcolor = []
    for c in cards:
        row = rowcache.get(c["scryfall_id"])
        if row is None:
            continue
        ci = set((row["color_identity"] or "").replace(",", ""))
        if not ci <= allowed:
            offcolor.append("{} needs {}".format(c["name"], "".join(sorted(ci))))
    chk.check("every card is castable in {}".format(args.colors),
              not offcolor, "; ".join(offcolor))

    # 7. the exported CSV is readable by the parser that will read it
    repo = os.path.join(os.path.dirname(os.path.abspath(args.deck)),
                        os.pardir, os.pardir)
    candidates = [os.path.join(repo, "decks",
                               "{}_manabox.csv".format(deck.get("deck_id"))),
                  os.path.join(repo, "build",
                               "{}_manabox.csv".format(deck.get("deck_id")))]
    csv_path = args.manabox or next(
        (c for c in candidates if os.path.exists(c)), candidates[0])
    if not os.path.exists(csv_path):
        print("  SKIP ManaBox CSV check -- no file at {}".format(csv_path))
    elif not os.path.isdir(MTGTOOL_ROOT):
        print("  SKIP ManaBox CSV check -- mtgtool not found at {}"
              .format(MTGTOOL_ROOT))
    else:
        sys.path.insert(0, MTGTOOL_ROOT)
        try:
            from mtgtool import manabox  # noqa: E402
        except ImportError as exc:
            print("  SKIP ManaBox CSV check -- {}".format(exc))
        else:
            try:
                parsed = manabox.parse(csv_path)
            except Exception as exc:
                chk.check("ManaBox CSV parses with mtgtool", False,
                          "{}: {}".format(type(exc).__name__, exc))
            else:
                chk.check("ManaBox CSV parses with mtgtool", True,
                          "{} rows, {} cards".format(parsed.csv_rows,
                                                     parsed.csv_cards))
                # A proxy is only a proxy when condition, misprint and price all
                # agree. Disagreement makes mtgtool stop the import and ask.
                chk.check("proxy markers in the CSV are unanimous",
                          not parsed.conflicts,
                          "; ".join(c.describe() for c in parsed.conflicts[:3]))
                as_proxy = sum(parsed.lots[k].quantity
                               for k, v in parsed.proxy_flags.items() if v)
                want_proxy = sum(c["quantity"] for c in cards
                                 if c.get("status") == "proxy")
                chk.check("CSV and deck plan agree on what is a proxy",
                          as_proxy == want_proxy and parsed.csv_cards == total,
                          "csv says {} proxy of {}, plan says {} of {}".format(
                              as_proxy, parsed.csv_cards, want_proxy, total))

    # 8. nothing that silently does nothing without a commander
    dead = [c["name"] for c in cards if c["name"] in NEEDS_COMMANDER]
    chk.check("no card that needs a commander to function", not dead, str(dead))

    print()
    print("{} passed, {} failed".format(len(chk.passes), len(chk.failures)))
    return 1 if chk.failures else 0


if __name__ == "__main__":
    sys.exit(main())
