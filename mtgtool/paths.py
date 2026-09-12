"""Canonical locations. Everything else asks this module; nothing hardcodes a path."""
from __future__ import annotations

import os

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

DATA = os.path.join(ROOT, "data")
SNAPSHOTS = os.path.join(DATA, "snapshots")
DECKS = os.path.join(DATA, "decks")
SCRYFALL = os.path.join(DATA, "scryfall")

CARD_CACHE = os.path.join(SCRYFALL, "cards.json")
ART_TAGS = os.path.join(DATA, "art_tags.jsonl")
OVERRIDES = os.path.join(DATA, "overrides.json")
DB = os.path.join(ROOT, "collection.db")

BUILD = os.path.join(ROOT, "build")
ART_THUMBS = os.path.join(BUILD, "art")


def ensure_dirs() -> None:
    for d in (DATA, SNAPSHOTS, DECKS, SCRYFALL, BUILD):
        os.makedirs(d, exist_ok=True)
