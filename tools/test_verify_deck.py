#!/usr/bin/env python3
"""Negative control for verify_deck.py.

A check that has never failed is not evidence -- it might be asserting nothing.
Every rule in verify_deck.py is broken here on purpose, and this test insists the
verifier notices. If you add a check, add the break that proves it works.

    python3 tools/test_verify_deck.py
"""
import copy
import json
import os
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
VERIFY = os.path.join(HERE, "verify_deck.py")
DECK = os.path.join(HERE, os.pardir, "data", "decks", "mardu-cats.json")


def run(deck_blob, extra=()):
    with tempfile.TemporaryDirectory() as tmp:
        path = os.path.join(tmp, "broken.json")
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(deck_blob, fh)
        proc = subprocess.run([sys.executable, VERIFY, path] + list(extra),
                              capture_output=True, text=True)
        return proc.returncode, proc.stdout + proc.stderr


def find(deck, name):
    for card in deck["cards"]:
        if card["name"].startswith(name):
            return card
    raise AssertionError("no {} in the deck".format(name))


def expect_failure(label, deck_blob, must_mention, extra=()):
    code, out = run(deck_blob, extra)
    failed_lines = [l for l in out.splitlines() if l.strip().startswith("FAIL")]
    hit = any(must_mention in l for l in failed_lines)
    ok = code != 0 and hit
    print("  {} {}".format("ok  " if ok else "BAD ", label))
    if not ok:
        print("      expected a FAIL mentioning {!r}; got:".format(must_mention))
        for line in out.splitlines():
            print("      " + line)
    return ok


def main():
    base = json.load(open(DECK, encoding="utf-8"))

    # The unmodified deck must pass, or every failure below proves nothing.
    code, out = run(base)
    print("  {} unmodified deck still passes".format("ok  " if code == 0 else "BAD "))
    results = [code == 0]
    if code != 0:
        print(out)

    d = copy.deepcopy(base); find(d, "Bitter Triumph")["quantity"] = 5
    results.append(expect_failure("5th copy of a non-basic is caught", d,
                                  "no more than 4 copies"))

    d = copy.deepcopy(base); d["cards"] = d["cards"][:5]
    results.append(expect_failure("a 59-card deck is caught", d, "at least 60 cards"))

    d = copy.deepcopy(base); find(d, "Queen Marchesa")["number"] = "9999"
    results.append(expect_failure("a mis-recorded collector number is caught", d,
                                  "name/set/number match"))

    d = copy.deepcopy(base)
    find(d, "Queen Marchesa")["scryfall_id"] = "00000000-0000-0000-0000-000000000000"
    results.append(expect_failure("an unknown scryfall id is caught", d,
                                  "scryfall id is a card we know"))

    # Sol Ring: owned only as proxies, and this slot claims a real copy.
    d = copy.deepcopy(base)
    d["cards"].append({"oracle_id": "ad8f4e3b-9d25-4c5a-b8d1-1cd1e3d6a1f0",
                       "name": "Sol Ring", "scryfall_id":
                       "a74aeeeb-3d6c-4b1c-a1b6-e0a2b2d0ed1c",
                       "set": "soc", "number": "128", "quantity": 1,
                       "role": "ramp", "status": "assigned"})
    results.append(expect_failure("a card that is not in the collection is caught", d,
                                  "scryfall id is a card we know"))

    d = copy.deepcopy(base); find(d, "Swords to Plowshares")["quantity"] = 4
    results.append(expect_failure("wanting more copies than exist is caught", d,
                                  "enough copies of each"))

    # Ask for a real copy of a card owned only as a proxy.
    d = copy.deepcopy(base); find(d, "The One Ring")["status"] = "assigned"
    results.append(expect_failure("spending a proxy as if it were real is caught", d,
                                  "in a binder, not a deck"))

    d = copy.deepcopy(base); find(d, "Chaos Warp")["quantity"] = 2
    results.append(expect_failure("over-allocating against another deck is caught", d,
                                  "claimed by more decks"))

    d = copy.deepcopy(base)
    results.append(expect_failure("an empty deck directory is caught", d,
                                  "other deck plans were actually read",
                                  extra=("--decks-dir", tempfile.gettempdir() + "/nope")))

    d = copy.deepcopy(base); results.append(
        expect_failure("an off-colour card is caught", d, "castable in",
                       extra=("--colors", "WB")))

    d = copy.deepcopy(base)
    d["cards"].append({"oracle_id": "0895c9b7-ae7d-4bb3-af17-3b75deb50a25", "name": "Command Tower",
                       "scryfall_id": "c46a217c-0ed2-4b3c-9a01-ee38d12d76f3", "set": "soc", "number": "129",
                       "quantity": 1, "role": "land", "status": "proxy"})
    results.append(expect_failure("a card that needs a commander is caught", d,
                                  "needs a commander"))

    # The CSV contract check: break the proxy fingerprint the way a hand-written
    # export would, and confirm mtgtool's parser -- not ours -- notices.
    import csv as _csv
    with tempfile.TemporaryDirectory() as tmp:
        src = os.path.join(HERE, os.pardir, "decks", "mardu-cats_manabox.csv")
        bad = os.path.join(tmp, "bad.csv")
        if os.path.exists(src):
            rows = list(_csv.DictReader(open(src, encoding="utf-8")))
            for r in rows:
                if r["Condition"] == "poor":
                    r["Purchase price"] = ""      # blank is not zero
            with open(bad, "w", newline="", encoding="utf-8") as fh:
                w = _csv.DictWriter(fh, fieldnames=list(rows[0].keys()))
                w.writeheader(); w.writerows(rows)
            results.append(expect_failure(
                "a broken proxy fingerprint in the CSV is caught",
                base, "proxy markers in the CSV", extra=("--manabox", bad)))

    print()
    print("{}/{} negative controls behaved".format(sum(results), len(results)))
    return 0 if all(results) else 1


if __name__ == "__main__":
    sys.exit(main())
