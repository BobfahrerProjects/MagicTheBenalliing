"""Test fixtures: a small synthetic collection in a temp directory.

Tests must never touch Ben's real data/ or collection.db, and must not depend on
his export being present. Everything here builds a throwaway world.
"""
from __future__ import annotations

import csv
import json
import os
import shutil
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from mtgtool import db, manabox, paths  # noqa: E402

# Two printings of one card (the ambiguity case) plus two unrelated cards.
SOL_A = "11111111-1111-1111-1111-111111111111"
SOL_B = "22222222-2222-2222-2222-222222222222"
SOL_ORACLE = "aaaa0000-0000-0000-0000-00000000aaaa"
STUDY = "33333333-3333-3333-3333-333333333333"
STUDY_ORACLE = "bbbb0000-0000-0000-0000-00000000bbbb"
BIRD = "44444444-4444-4444-4444-444444444444"
BIRD_ORACLE = "cccc0000-0000-0000-0000-00000000cccc"

FAKE_CARDS = {
    SOL_A: {
        "id": SOL_A, "oracle_id": SOL_ORACLE, "name": "Sol Ring", "set": "40k",
        "set_name": "Warhammer 40,000 Commander", "set_type": "commander",
        "collector_number": "252", "type_line": "Artifact", "cmc": 1.0,
        "color_identity": [], "colors": [], "rarity": "uncommon", "layout": "normal",
        "artist": "Lucas Terryn", "illustration_id": "illus-sol-a",
        "promo_types": ["universesbeyond"], "border_color": "black",
        "image_uris": {"art_crop": "https://example.invalid/sol-a.jpg",
                       "normal": "https://example.invalid/sol-a-n.jpg"},
        "prices": {"eur": "2.00", "eur_foil": "5.00", "usd": "2.20", "usd_foil": None},
        "legalities": {"commander": "legal"}, "keywords": [],
    },
    SOL_B: {
        "id": SOL_B, "oracle_id": SOL_ORACLE, "name": "Sol Ring", "set": "blc",
        "set_name": "Bloomburrow Commander", "set_type": "commander",
        "collector_number": "129", "type_line": "Artifact", "cmc": 1.0,
        "color_identity": [], "colors": [], "rarity": "uncommon", "layout": "normal",
        "artist": "Volkan Baga", "illustration_id": "illus-sol-b",
        "promo_types": [], "border_color": "black",
        "image_uris": {"art_crop": "https://example.invalid/sol-b.jpg",
                       "normal": "https://example.invalid/sol-b-n.jpg"},
        "prices": {"eur": "1.50", "eur_foil": None, "usd": "1.70", "usd_foil": None},
        "legalities": {"commander": "legal"}, "keywords": [],
    },
    STUDY: {
        "id": STUDY, "oracle_id": STUDY_ORACLE, "name": "Rhystic Study", "set": "pcy",
        "set_name": "Prophecy", "set_type": "expansion", "collector_number": "45",
        "type_line": "Enchantment", "cmc": 3.0, "color_identity": ["U"],
        "colors": ["U"], "rarity": "common", "layout": "normal",
        "artist": "Terese Nielsen", "illustration_id": "illus-study",
        "promo_types": [], "border_color": "black",
        "image_uris": {"art_crop": "https://example.invalid/study.jpg",
                       "normal": "https://example.invalid/study-n.jpg"},
        "prices": {"eur": "30.00", "eur_foil": None, "usd": None, "usd_foil": None},
        "legalities": {"commander": "legal"}, "keywords": [],
    },
    BIRD: {
        "id": BIRD, "oracle_id": BIRD_ORACLE, "name": "Squirrel Sovereign",
        "set": "blb", "set_name": "Bloomburrow", "set_type": "expansion",
        "collector_number": "7", "type_line": "Creature — Squirrel Noble",
        "cmc": 2.0, "color_identity": ["G"], "colors": ["G"], "rarity": "rare",
        "layout": "normal", "artist": "Someone", "illustration_id": "illus-bird",
        "promo_types": [], "border_color": "black",
        "image_uris": {"art_crop": "https://example.invalid/bird.jpg",
                       "normal": "https://example.invalid/bird-n.jpg"},
        "prices": {"eur": None, "eur_foil": None, "usd": "4.00", "usd_foil": None},
        "legalities": {"commander": "legal"}, "keywords": [],
    },
}


def row(binder, btype, sid, name, set_code, number, qty, price="1.00",
        condition="near_mint", misprint="false", altered="false", foil="normal"):
    return {
        "Binder Name": binder, "Binder Type": btype, "Name": name,
        "Set code": set_code, "Set name": set_code, "Collector number": number,
        "Foil": foil, "Rarity": "rare", "Quantity": str(qty), "ManaBox ID": "1",
        "Scryfall ID": sid, "Purchase price": price, "Misprint": misprint,
        "Altered": altered, "Condition": condition, "Language": "en",
        "Purchase price currency": "EUR", "Added": "2026-01-01T00:00:00.000Z",
    }


def default_rows():
    """A tiny collection covering every case the real export exercises."""
    return [
        # two printings of one card -> name is ambiguous
        row("Pool - Old", "binder", SOL_A, "Sol Ring", "40k", "252", 1),
        row("Hazel", "deck", SOL_B, "Sol Ring", "blc", "129", 1),
        # two real copies of a staple, in two decks
        row("Rootha", "deck", STUDY, "Rhystic Study", "pcy", "45", 1, price="29.78"),
        row("Nata", "deck", STUDY, "Rhystic Study", "pcy", "45", 1, price="29.78"),
        # a proxy: all four markers agree
        row("Proxies", "binder", STUDY, "Rhystic Study", "pcy", "45", 2,
            price="0", condition="poor", misprint="true", altered="true"),
        # a wishlist entry: owned by nobody
        row("Wish List", "list", BIRD, "Squirrel Sovereign", "blb", "7", 3,
            price="4.00"),
        # duplicate lot rows that must aggregate (the real export does this)
        row("Pool - Old", "binder", BIRD, "Squirrel Sovereign", "blb", "7", 1,
            price="0.21"),
        row("Pool - Old", "binder", BIRD, "Squirrel Sovereign", "blb", "7", 1,
            price="0.06"),
    ]


def write_csv(path, rows):
    with open(path, "w", encoding="utf-8", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=manabox.EXPECTED_COLUMNS)
        writer.writeheader()
        for r in rows:
            writer.writerow(r)
    return path


class Sandbox:
    """Redirects every path in `paths` into a temp dir for the duration."""

    def __init__(self):
        self.dir = None
        self._saved = {}

    def __enter__(self):
        self.dir = tempfile.mkdtemp(prefix="mtgtest-")
        self._saved = {k: getattr(paths, k) for k in
                       ("ROOT", "DATA", "SNAPSHOTS", "DECKS", "SCRYFALL",
                        "CARD_CACHE", "ART_TAGS", "OVERRIDES", "DB", "BUILD",
                        "ART_THUMBS")}
        paths.ROOT = self.dir
        paths.DATA = os.path.join(self.dir, "data")
        paths.SNAPSHOTS = os.path.join(paths.DATA, "snapshots")
        paths.DECKS = os.path.join(paths.DATA, "decks")
        paths.SCRYFALL = os.path.join(paths.DATA, "scryfall")
        paths.CARD_CACHE = os.path.join(paths.SCRYFALL, "cards.json")
        paths.ART_TAGS = os.path.join(paths.DATA, "art_tags.jsonl")
        paths.OVERRIDES = os.path.join(paths.DATA, "overrides.json")
        paths.DB = os.path.join(self.dir, "collection.db")
        paths.BUILD = os.path.join(self.dir, "build")
        paths.ART_THUMBS = os.path.join(paths.BUILD, "art")
        paths.ensure_dirs()
        with open(paths.CARD_CACHE, "w", encoding="utf-8") as fh:
            json.dump(FAKE_CARDS, fh)
        return self

    def __exit__(self, *exc):
        for key, value in self._saved.items():
            setattr(paths, key, value)
        shutil.rmtree(self.dir, ignore_errors=True)
        return False

    def csv(self, rows=None, name="ManaBox_20260101.csv"):
        return write_csv(os.path.join(self.dir, name),
                         default_rows() if rows is None else rows)

    def conn(self):
        connection = db.connect(paths.DB)
        db.init(connection)
        return connection
