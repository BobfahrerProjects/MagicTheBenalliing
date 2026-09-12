"""Turning what a human typed into an unambiguous card.

This module exists because of one measured fact: 69 card names in this collection
map to more than one printing. "Sol Ring" is four different physical objects here;
"Forest" is twenty-five. Any code path that accepts a name and quietly picks a
printing will eventually pick the wrong one, and in deck building that means
sleeving up the wrong card.

So the rule is absolute: **a name never resolves to a single card by itself.**
`resolve` reports every match and refuses to choose. Callers that need one card
must pass a Scryfall ID, or ask the human.
"""
from __future__ import annotations

import difflib
from dataclasses import dataclass
from typing import List, Optional

from . import db


class AmbiguousCard(Exception):
    """A name matched several printings. The message lists them all."""

    def __init__(self, name: str, candidates: List["Candidate"]):
        self.name = name
        self.candidates = candidates
        super().__init__(
            "{!r} matches {} printings you own -- refusing to guess:\n{}\n"
            "Say which one by its set and collector number, e.g. \"{}\"."
            .format(name, len(candidates),
                    "\n".join("  " + c.describe() for c in candidates),
                    candidates[0].short()))


class UnknownCard(Exception):
    def __init__(self, name: str, suggestions: List[str] = None):
        self.name = name
        self.suggestions = suggestions or []
        extra = ""
        if self.suggestions:
            extra = "\nDid you mean: {}?".format(", ".join(self.suggestions[:8]))
        super().__init__("no card named {!r} in the collection.{}".format(name, extra))


@dataclass
class Candidate:
    scryfall_id: str
    oracle_id: str
    name: str
    set_code: str
    set_name: str
    collector_number: str
    artist: str
    owned: int
    proxies: int
    binders: str

    def short(self) -> str:
        return "{} ({}) {}".format(self.name, (self.set_code or "").upper(),
                                   self.collector_number)

    def describe(self) -> str:
        where = self.binders or "-"
        bits = "{} real".format(self.owned)
        if self.proxies:
            bits += ", {} proxy".format(self.proxies)
        return "{:<44s} {:<22s} [{}]  in: {}".format(
            self.short(), (self.artist or "")[:22], bits, where)


def _candidates_sql(where: str) -> str:
    return """
        SELECT c.scryfall_id, c.oracle_id, c.name, c.set_code, c.set_name,
               c.collector_number, c.artist,
               COALESCE(SUM(CASE WHEN l.is_proxy=0 AND l.is_wishlist=0
                                 THEN l.quantity END), 0) AS owned,
               COALESCE(SUM(CASE WHEN l.is_proxy=1 AND l.is_wishlist=0
                                 THEN l.quantity END), 0) AS proxies,
               GROUP_CONCAT(DISTINCT CASE WHEN l.is_wishlist=0
                                          THEN l.binder_name END) AS binders
          FROM cards c
          JOIN lots l ON l.scryfall_id = c.scryfall_id AND l.snapshot_id = ?
         WHERE {}
      GROUP BY c.scryfall_id
        HAVING owned > 0 OR proxies > 0
      ORDER BY owned DESC, c.set_code, c.collector_number
    """.format(where)


def _rows_to_candidates(rows) -> List[Candidate]:
    return [Candidate(
        scryfall_id=r["scryfall_id"], oracle_id=r["oracle_id"], name=r["name"],
        set_code=r["set_code"] or "", set_name=r["set_name"] or "",
        collector_number=r["collector_number"] or "", artist=r["artist"] or "",
        owned=r["owned"], proxies=r["proxies"], binders=r["binders"] or "",
    ) for r in rows]


def candidates(conn, name: str, snapshot_id: int = None) -> List[Candidate]:
    """Every owned printing whose name matches exactly (case-insensitively)."""
    snapshot_id = snapshot_id or db.latest_snapshot_id(conn)
    rows = conn.execute(
        _candidates_sql("LOWER(c.name) = LOWER(?)"), (snapshot_id, name)).fetchall()
    return _rows_to_candidates(rows)


def search(conn, fragment: str, snapshot_id: int = None, limit: int = 25) -> List[Candidate]:
    """Substring search. Used for suggestions only -- never to auto-select."""
    snapshot_id = snapshot_id or db.latest_snapshot_id(conn)
    rows = conn.execute(
        _candidates_sql("LOWER(c.name) LIKE LOWER(?)"),
        (snapshot_id, "%" + fragment + "%")).fetchall()
    return _rows_to_candidates(rows)[:limit]


def resolve(conn, name: str, snapshot_id: int = None) -> Candidate:
    """Exactly one owned printing, or an exception naming the alternatives.

    Never returns a best guess. This is the function every deck-building path
    must go through.
    """
    found = candidates(conn, name, snapshot_id)
    if len(found) == 1:
        return found[0]
    if not found:
        raise UnknownCard(name, suggest(conn, name, snapshot_id))
    raise AmbiguousCard(name, found)


def suggest(conn, name: str, snapshot_id: int = None, limit: int = 8) -> List[str]:
    """Near-miss names for a typo.

    Safe to be fuzzy precisely because these are only ever printed for a human to
    read -- nothing downstream may select from them automatically.
    """
    snapshot_id = snapshot_id or db.latest_snapshot_id(conn)
    names = [r["name"] for r in conn.execute(
        "SELECT DISTINCT c.name FROM cards c"
        "  JOIN lots l ON l.scryfall_id = c.scryfall_id AND l.snapshot_id = ?"
        " WHERE l.is_wishlist = 0", (snapshot_id,))]
    close = difflib.get_close_matches(name, names, n=limit, cutoff=0.6)
    substring = sorted({n for n in names if name.lower() in n.lower()})
    for n in substring:
        if n not in close:
            close.append(n)
    return close[:limit]


def by_id(conn, scryfall_id: str, snapshot_id: int = None) -> Optional[Candidate]:
    snapshot_id = snapshot_id or db.latest_snapshot_id(conn)
    rows = conn.execute(
        _candidates_sql("c.scryfall_id = ?"), (snapshot_id, scryfall_id)).fetchall()
    found = _rows_to_candidates(rows)
    return found[0] if found else None


def verify_name(conn, scryfall_id: str, recorded_name: str) -> Optional[str]:
    """Confirm a stored name still belongs to its stored ID.

    Returns None when consistent, or a description of the mismatch. This is what
    turns "no identification mistakes" from an intention into a checked property.
    """
    row = conn.execute(
        "SELECT name FROM cards WHERE scryfall_id = ?", (scryfall_id,)).fetchone()
    if row is None:
        return "{} is not a known card".format(scryfall_id)
    if row["name"].lower() != (recorded_name or "").lower():
        return "{} is {!r}, but the record says {!r}".format(
            scryfall_id, row["name"], recorded_name)
    return None


def owned_copies(conn, oracle_id: str, snapshot_id: int = None) -> int:
    """Real, non-proxy, non-wishlist copies of a card across every printing.

    Oracle level on purpose: for deck building a Sol Ring is a Sol Ring, whichever
    set it came from.
    """
    snapshot_id = snapshot_id or db.latest_snapshot_id(conn)
    row = conn.execute(
        "SELECT COALESCE(SUM(l.quantity), 0) AS n"
        "  FROM lots l JOIN cards c ON c.scryfall_id = l.scryfall_id"
        " WHERE l.snapshot_id = ? AND c.oracle_id = ?"
        "   AND l.is_proxy = 0 AND l.is_wishlist = 0",
        (snapshot_id, oracle_id)).fetchone()
    return row["n"]


def proxy_copies(conn, oracle_id: str, snapshot_id: int = None) -> int:
    snapshot_id = snapshot_id or db.latest_snapshot_id(conn)
    row = conn.execute(
        "SELECT COALESCE(SUM(l.quantity), 0) AS n"
        "  FROM lots l JOIN cards c ON c.scryfall_id = l.scryfall_id"
        " WHERE l.snapshot_id = ? AND c.oracle_id = ?"
        "   AND l.is_proxy = 1 AND l.is_wishlist = 0",
        (snapshot_id, oracle_id)).fetchone()
    return row["n"]
