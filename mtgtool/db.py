"""SQLite schema and connection.

collection.db is a DERIVED cache. It is rebuildable in full from the durable text
inputs (snapshots/*.csv, art_tags.jsonl, decks/*.json, overrides.json), so losing it
is an inconvenience, never a data loss. `mtg rebuild` proves that claim.
"""
from __future__ import annotations

import sqlite3

from . import paths

SCHEMA = """
PRAGMA foreign_keys = ON;

-- One row per ManaBox export. Snapshots are immutable history.
CREATE TABLE IF NOT EXISTS snapshots (
    snapshot_id   INTEGER PRIMARY KEY,
    filename      TEXT NOT NULL UNIQUE,
    export_date   TEXT NOT NULL,
    imported_at   TEXT NOT NULL,
    csv_rows      INTEGER NOT NULL,
    csv_cards     INTEGER NOT NULL
);

-- A physical stack of identical cards in one binder. Quantities are SUMMED when
-- ManaBox emits several rows for the same lot (it does -- differing purchase price).
CREATE TABLE IF NOT EXISTS lots (
    snapshot_id   INTEGER NOT NULL REFERENCES snapshots(snapshot_id) ON DELETE CASCADE,
    binder_name   TEXT NOT NULL,
    binder_type   TEXT NOT NULL,
    scryfall_id   TEXT NOT NULL,
    foil          TEXT NOT NULL,
    condition     TEXT NOT NULL,
    language      TEXT NOT NULL,
    misprint      INTEGER NOT NULL,
    altered       INTEGER NOT NULL,
    quantity      INTEGER NOT NULL,
    purchase_price REAL,
    is_proxy      INTEGER NOT NULL,
    is_wishlist   INTEGER NOT NULL,
    PRIMARY KEY (snapshot_id, binder_name, binder_type, scryfall_id, foil,
                 condition, language, misprint, altered)
);
CREATE INDEX IF NOT EXISTS lots_snap_card ON lots(snapshot_id, scryfall_id);
CREATE INDEX IF NOT EXISTS lots_binder ON lots(snapshot_id, binder_type, binder_name);

-- Scryfall printing metadata. Keyed by the printing, carries the oracle identity.
CREATE TABLE IF NOT EXISTS cards (
    scryfall_id      TEXT PRIMARY KEY,
    oracle_id        TEXT,
    name             TEXT NOT NULL,
    set_code         TEXT,
    set_name         TEXT,
    set_type         TEXT,
    collector_number TEXT,
    type_line        TEXT,
    mana_cost        TEXT,
    cmc              REAL,
    color_identity   TEXT,
    colors           TEXT,
    rarity           TEXT,
    layout           TEXT,
    artist           TEXT,
    keywords         TEXT,
    promo_types      TEXT,
    border_color     TEXT,
    full_art         INTEGER,
    textless         INTEGER,
    game_changer     INTEGER,
    edhrec_rank      INTEGER,
    reserved         INTEGER,
    legalities       TEXT,
    price_eur        REAL,
    price_eur_foil   REAL,
    price_usd        REAL,
    price_usd_foil   REAL,
    released_at      TEXT,
    scryfall_uri     TEXT,
    fetched_at       TEXT
);
CREATE INDEX IF NOT EXISTS cards_oracle ON cards(oracle_id);
CREATE INDEX IF NOT EXISTS cards_name ON cards(name);

-- The tagging unit is a FACE, not a card: 17 cards in this collection carry two
-- distinct artworks and each deserves its own tags.
CREATE TABLE IF NOT EXISTS card_faces (
    scryfall_id     TEXT NOT NULL REFERENCES cards(scryfall_id) ON DELETE CASCADE,
    face_index      INTEGER NOT NULL,
    face_name       TEXT NOT NULL,
    face_type_line  TEXT,
    illustration_id TEXT,
    art_crop_url    TEXT,
    normal_url      TEXT,
    PRIMARY KEY (scryfall_id, face_index)
);
CREATE INDEX IF NOT EXISTS faces_illus ON card_faces(illustration_id);

-- Art tags hang off the artwork, never off a row, so a fresh export never
-- disturbs them.
-- `source` is part of the key on purpose. Two sources agreeing that an artwork
-- shows a cat is worth recording as two rows: it keeps the per-source coverage
-- counts honest, and lets a bad source be deleted without taking the other's
-- tags with it. Readers de-duplicate by tag.
CREATE TABLE IF NOT EXISTS art_tags (
    illustration_id TEXT NOT NULL,
    facet           TEXT NOT NULL,
    tag             TEXT NOT NULL,
    source          TEXT NOT NULL,   -- metadata | scryfall | vision | manual
    confidence      REAL,
    PRIMARY KEY (illustration_id, facet, tag, source)
);
CREATE INDEX IF NOT EXISTS art_tags_tag ON art_tags(tag);

CREATE TABLE IF NOT EXISTS art_notes (
    illustration_id TEXT PRIMARY KEY,
    description     TEXT,
    other_tags      TEXT,            -- JSON list: vocabulary candidates for review
    model           TEXT,
    tagged_at       TEXT
);

-- ---- The planning layer. Import never writes these tables. ----

CREATE TABLE IF NOT EXISTS decks (
    deck_id    TEXT PRIMARY KEY,
    name       TEXT NOT NULL,
    format     TEXT NOT NULL DEFAULT 'commander',
    commander_scryfall_id TEXT,
    status     TEXT NOT NULL DEFAULT 'building',
    notes      TEXT
);

-- What the deck WANTS, at oracle level: a Sol Ring is a Sol Ring.
CREATE TABLE IF NOT EXISTS deck_slots (
    slot_id      TEXT PRIMARY KEY,
    deck_id      TEXT NOT NULL REFERENCES decks(deck_id) ON DELETE CASCADE,
    oracle_id    TEXT NOT NULL,
    display_name TEXT NOT NULL,
    role         TEXT,
    status       TEXT NOT NULL,      -- assigned | proxy | wanted | contested
    quantity     INTEGER NOT NULL DEFAULT 1
);
CREATE INDEX IF NOT EXISTS slots_deck ON deck_slots(deck_id);
CREATE INDEX IF NOT EXISTS slots_oracle ON deck_slots(oracle_id);

-- Which physical printing fills the slot. Stores the name it was created from so
-- an invariant can catch a name/ID drift.
CREATE TABLE IF NOT EXISTS assignments (
    slot_id          TEXT PRIMARY KEY REFERENCES deck_slots(slot_id) ON DELETE CASCADE,
    scryfall_id      TEXT NOT NULL,
    recorded_name    TEXT NOT NULL,
    recorded_set     TEXT,
    recorded_number  TEXT,
    quantity         INTEGER NOT NULL DEFAULT 1
);

CREATE TABLE IF NOT EXISTS deltas (
    snapshot_id  INTEGER NOT NULL REFERENCES snapshots(snapshot_id) ON DELETE CASCADE,
    change_type  TEXT NOT NULL,      -- added | removed | qty_up | qty_down | moved
    scryfall_id  TEXT NOT NULL,
    binder_from  TEXT,
    binder_to    TEXT,
    qty_delta    INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS deltas_snap ON deltas(snapshot_id);
"""


def connect(path: str = None) -> sqlite3.Connection:
    paths.ensure_dirs()
    conn = sqlite3.connect(path or paths.DB)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init(conn: sqlite3.Connection) -> None:
    conn.executescript(SCHEMA)
    conn.commit()


def latest_snapshot_id(conn: sqlite3.Connection):
    row = conn.execute(
        "SELECT snapshot_id FROM snapshots ORDER BY export_date DESC, snapshot_id DESC LIMIT 1"
    ).fetchone()
    return row["snapshot_id"] if row else None
