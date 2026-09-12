"""Community art tags from Scryfall Tagger.

Scryfall's Tagger project has human-tagged what is actually *painted* on tens of
thousands of cards, and the public search API exposes it as `art:<tag>`. That is
the source that finds the cat on Cyclonic Rift and on Lord Windgrace -- neither
card's type line mentions a cat, so type-line tagging misses both. In this
collection it raises "cards with a cat in the art" from 14 to 58.

Two properties were verified against the live API before relying on any of it:

* `art:` matches tag names **exactly**. `art:cat` returns 1,200 cards; `art:ca`,
  `art:cats` and `art:catt` each return none. So there are no substring false
  positives.
* A tag that does not exist returns HTTP 404, which is distinguishable from being
  throttled (429). This module never turns an error into an empty result -- doing
  so once already produced a silent, entirely wrong "no tags exist" answer during
  development.
"""
from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request
from collections import defaultdict
from typing import Dict, Iterable, List, Set

from . import paths, vocab

API = "https://api.scryfall.com"
USER_AGENT = "MagicTheBenalliing/0.1 (personal collection manager)"
HEADERS = {"User-Agent": USER_AGENT, "Accept": "application/json"}
# Scryfall asks for 50-100ms between requests, but throttles sustained
# bursts harder than that. Measured: 0.12s dies after ~30 tags.
PAGE_DELAY_S = 0.30
TAG_DELAY_S = 0.30
def cache_path() -> str:
    """Resolved on each call, not at import.

    A module-level constant would freeze the path at import time and ignore any
    later change to `paths` -- which would silently write test data into the real
    repository.
    """
    return os.path.join(paths.SCRYFALL, "art_tags.json")


class Throttled(Exception):
    """Scryfall asked us to slow down and kept asking. Never confused with 'no results'."""


def _get(url: str, tries: int = 9):
    """(body, status). 404 means genuinely no match; throttling raises."""
    delay = 2.0
    for _ in range(tries):
        try:
            request = urllib.request.Request(url, headers=HEADERS)
            with urllib.request.urlopen(request, timeout=30) as response:
                return json.load(response), 200
        except urllib.error.HTTPError as exc:
            if exc.code == 404:
                return None, 404
            if exc.code in (429, 500, 502, 503, 504):
                time.sleep(delay)
                delay = min(delay * 2, 60.0)
                continue
            raise
        except urllib.error.URLError:
            time.sleep(delay)
            delay = min(delay * 2, 60.0)
            continue
    raise Throttled(url)


def illustrations_for_tag(tag: str, page_cap: int = 200) -> Set[str]:
    """Every illustration_id Scryfall has tagged with `tag`.

    `unique=art` collapses reprints that share an artwork, which is exactly the
    granularity our tags live at.
    """
    url = "{}/cards/search?q={}&unique=art".format(
        API, urllib.parse.quote("art:" + tag))
    found: Set[str] = set()
    pages = 0
    while url and pages < page_cap:
        body, status = _get(url)
        if status == 404:
            return found
        for card in body.get("data", []):
            if card.get("illustration_id"):
                found.add(card["illustration_id"])
            for face in card.get("card_faces", []):
                if face.get("illustration_id"):
                    found.add(face["illustration_id"])
        url = body.get("next_page")
        pages += 1
        time.sleep(PAGE_DELAY_S)
    return found


def load_cache(path: str = None) -> Dict[str, List[str]]:
    path = path or cache_path()
    if not os.path.exists(path):
        return {}
    with open(path, "r", encoding="utf-8") as fh:
        return json.load(fh)


def save_cache(data: Dict[str, List[str]], path: str = None) -> None:
    path = path or cache_path()
    paths.ensure_dirs()
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(data, fh, sort_keys=True)
    os.replace(tmp, path)


def fetch_all(tags: Iterable[str] = None, refresh: bool = False,
              log=print) -> Dict[str, List[str]]:
    """Fetch every mapped Scryfall tag, caching as it goes.

    Resumable: an interrupted run keeps what it already fetched, so a throttle
    part-way through costs only the remainder.
    """
    tags = list(tags or sorted(vocab.SCRYFALL_MAP))
    cache = {} if refresh else load_cache()
    todo = [t for t in tags if t not in cache]
    if not todo:
        log("  all {} tag(s) already cached".format(len(tags)))
        return cache

    log("  fetching {} art tag(s) from Scryfall Tagger...".format(len(todo)))
    try:
        for index, tag in enumerate(todo, 1):
            cache[tag] = sorted(illustrations_for_tag(tag))
            time.sleep(TAG_DELAY_S)
            if index % 20 == 0 or index == len(todo):
                log("    {}/{} ({} = {} artworks)".format(
                    index, len(todo), tag, len(cache[tag])))
                save_cache(cache)
    except Throttled:
        save_cache(cache)
        log("    Scryfall is throttling; {} tag(s) fetched, {} remain. "
            "Re-run to continue -- progress is cached."
            .format(len(cache), len(tags) - len(cache)))
        raise
    save_cache(cache)
    return cache


def apply(conn, cache: Dict[str, List[str]] = None) -> Dict[str, int]:
    """Write Scryfall tags for artwork we actually own. Idempotent."""
    cache = load_cache() if cache is None else cache
    mine = {r["illustration_id"] for r in conn.execute(
        "SELECT DISTINCT illustration_id FROM card_faces"
        " WHERE illustration_id IS NOT NULL")}

    conn.execute("DELETE FROM art_tags WHERE source = 'scryfall'")
    written = 0
    per_facet: Dict[str, int] = defaultdict(int)
    covered: Set[str] = set()
    # Counts are taken from rows actually inserted, never from rows attempted --
    # an ignored insert must not be reported as coverage we do not have.

    for scry_tag, illustrations in cache.items():
        mapped = vocab.SCRYFALL_MAP.get(scry_tag)
        if not mapped:
            continue
        facet, tag = mapped
        for illustration in set(illustrations) & mine:
            cursor = conn.execute(
                "INSERT OR IGNORE INTO art_tags (illustration_id, facet, tag,"
                " source, confidence) VALUES (?,?,?,'scryfall',1.0)",
                (illustration, facet, tag))
            if cursor.rowcount:
                written += 1
                per_facet[facet] += 1
                covered.add(illustration)

    conn.commit()
    return {"tags": written, "artworks_covered": len(covered),
            "artworks_total": len(mine), "per_facet": dict(per_facet)}


def coverage(conn) -> Dict[str, int]:
    """How much of the collection any free source has tagged."""
    total = conn.execute(
        "SELECT COUNT(DISTINCT illustration_id) AS n FROM card_faces"
        " WHERE illustration_id IS NOT NULL").fetchone()["n"]
    by_source = {}
    for source in ("metadata", "scryfall", "vision"):
        by_source[source] = conn.execute(
            "SELECT COUNT(DISTINCT illustration_id) AS n FROM art_tags"
            " WHERE source = ?", (source,)).fetchone()["n"]
    any_tag = conn.execute(
        "SELECT COUNT(DISTINCT illustration_id) AS n FROM art_tags").fetchone()["n"]
    return {"total": total, "any": any_tag, **by_source}
