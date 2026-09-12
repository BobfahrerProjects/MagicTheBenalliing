"""Scryfall metadata: fetch, cache, and normalise.

We ask Scryfall for the printing behind every Scryfall ID in the export. That gives
us, for free and with no model involved: the oracle identity (which groups all
printings of one card), the artwork identity per face, prices, colour identity,
type line, legality, and the Universes Beyond flag.

The /cards/collection endpoint takes 75 identifiers per request, so this whole
collection is 24 requests -- a few seconds, once.
"""
from __future__ import annotations

import datetime
import json
import os
import time
import urllib.error
import urllib.request
from typing import Dict, Iterable, List, Optional, Tuple

from . import paths

API = "https://api.scryfall.com"
USER_AGENT = "MagicTheBenalliing/0.1 (personal collection manager)"
CHUNK = 75
DELAY_S = 0.12  # Scryfall asks for 50-100ms between requests; we allow a bit more.


class ScryfallError(Exception):
    pass


def _post(path: str, payload: dict, timeout: int = 30) -> dict:
    request = urllib.request.Request(
        API + path,
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Content-Type": "application/json",
            "Accept": "application/json",
            "User-Agent": USER_AGENT,
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        raise ScryfallError("{} {}: {}".format(exc.code, path, exc.read()[:400]))
    except urllib.error.URLError as exc:
        raise ScryfallError("cannot reach Scryfall ({}): {}".format(path, exc.reason))


def load_cache(path: str = None) -> Dict[str, dict]:
    path = path or paths.CARD_CACHE
    if not os.path.exists(path):
        return {}
    with open(path, "r", encoding="utf-8") as fh:
        return json.load(fh)


def save_cache(cards: Dict[str, dict], path: str = None) -> None:
    path = path or paths.CARD_CACHE
    paths.ensure_dirs()
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(cards, fh, sort_keys=True)
    os.replace(tmp, path)


def fetch_missing(
    scryfall_ids: Iterable[str],
    cache: Dict[str, dict] = None,
    progress=None,
) -> Tuple[Dict[str, dict], List[str]]:
    """Fetch any IDs absent from the cache. Returns (cache, not_found).

    Never silently drops an ID: anything Scryfall cannot resolve comes back in
    not_found for the caller to report.
    """
    cache = load_cache() if cache is None else cache
    wanted = [sid for sid in dict.fromkeys(scryfall_ids) if sid not in cache]
    not_found: List[str] = []

    for index in range(0, len(wanted), CHUNK):
        chunk = wanted[index:index + CHUNK]
        if progress:
            progress(min(index + CHUNK, len(wanted)), len(wanted))
        body = _post("/cards/collection",
                     {"identifiers": [{"id": sid} for sid in chunk]})
        stamp = datetime.datetime.now().isoformat(timespec="seconds")
        for card in body.get("data", []):
            card["_fetched_at"] = stamp
            cache[card["id"]] = card
        for miss in body.get("not_found", []):
            not_found.append(miss.get("id", str(miss)))
        time.sleep(DELAY_S)

    return cache, not_found


def refresh_prices(cache: Dict[str, dict], progress=None) -> Dict[str, dict]:
    """Re-fetch every cached card so prices are current. Same 24 requests."""
    ids = list(cache.keys())
    for index in range(0, len(ids), CHUNK):
        chunk = ids[index:index + CHUNK]
        if progress:
            progress(min(index + CHUNK, len(ids)), len(ids))
        body = _post("/cards/collection",
                     {"identifiers": [{"id": sid} for sid in chunk]})
        stamp = datetime.datetime.now().isoformat(timespec="seconds")
        for card in body.get("data", []):
            card["_fetched_at"] = stamp
            cache[card["id"]] = card
        time.sleep(DELAY_S)
    return cache


# ---- normalisation ----

def faces(card: dict) -> List[dict]:
    """The artwork units of a printing.

    A card with two physical sides carries two distinct artworks and must be
    tagged twice; an adventure or split card has one artwork under two names and
    must be tagged once. Scryfall distinguishes these by whether image_uris sits
    at the top level or inside each face.
    """
    if card.get("illustration_id") or card.get("image_uris"):
        images = card.get("image_uris", {})
        # One artwork: the whole card's type line describes what is painted.
        type_line = card.get("type_line") or " // ".join(
            f.get("type_line", "") for f in card.get("card_faces", []))
        return [{
            "face_index": 0,
            "face_name": card.get("name", ""),
            "face_type_line": type_line,
            "illustration_id": card.get("illustration_id"),
            "art_crop_url": images.get("art_crop"),
            "normal_url": images.get("normal"),
        }]

    out = []
    for index, face in enumerate(card.get("card_faces", [])):
        images = face.get("image_uris", {})
        out.append({
            "face_index": index,
            "face_name": face.get("name", ""),
            # Per-face: a Pathway's two sides are different creatures/lands, so a
            # card-level type line would tag both artworks with both sides' types.
            "face_type_line": face.get("type_line", ""),
            "illustration_id": face.get("illustration_id"),
            "art_crop_url": images.get("art_crop"),
            "normal_url": images.get("normal"),
        })
    return out


def _num(value) -> Optional[float]:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def to_row(card: dict) -> dict:
    """Flatten a Scryfall card into the `cards` table shape."""
    prices = card.get("prices") or {}
    return {
        "scryfall_id": card["id"],
        "oracle_id": card.get("oracle_id"),
        "name": card.get("name", ""),
        "set_code": card.get("set"),
        "set_name": card.get("set_name"),
        "set_type": card.get("set_type"),
        "collector_number": card.get("collector_number"),
        "type_line": card.get("type_line") or " // ".join(
            f.get("type_line", "") for f in card.get("card_faces", [])),
        "mana_cost": card.get("mana_cost"),
        "cmc": _num(card.get("cmc")),
        "color_identity": ",".join(card.get("color_identity") or []),
        "colors": ",".join(card.get("colors") or []),
        "rarity": card.get("rarity"),
        "layout": card.get("layout"),
        "artist": card.get("artist"),
        "keywords": json.dumps(card.get("keywords") or []),
        "promo_types": json.dumps(card.get("promo_types") or []),
        "border_color": card.get("border_color"),
        "full_art": 1 if card.get("full_art") else 0,
        "textless": 1 if card.get("textless") else 0,
        "game_changer": 1 if card.get("game_changer") else 0,
        "edhrec_rank": card.get("edhrec_rank"),
        "reserved": 1 if card.get("reserved") else 0,
        "legalities": json.dumps(card.get("legalities") or {}),
        "price_eur": _num(prices.get("eur")),
        "price_eur_foil": _num(prices.get("eur_foil")),
        "price_usd": _num(prices.get("usd")),
        "price_usd_foil": _num(prices.get("usd_foil")),
        "released_at": card.get("released_at"),
        "scryfall_uri": card.get("scryfall_uri"),
        # When the data (and so the price) was actually fetched -- NOT when this
        # row was written. Stamping write time would make every rebuild produce a
        # different database and would overstate how fresh the prices are.
        "fetched_at": card.get("_fetched_at"),
    }
