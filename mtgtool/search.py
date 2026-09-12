"""Querying the collection by art, colour, type and availability.

Availability is the deck-builder's question and it is not the same as ownership:
a card you own but have already built into another deck is not available. That
distinction is computed here once so the CLI and the gallery cannot disagree.
"""
from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from typing import Dict, List, Optional

from . import db, decks


@dataclass
class Hit:
    scryfall_id: str
    oracle_id: str
    name: str
    set_code: str
    collector_number: str
    type_line: str
    mana_cost: str
    cmc: float
    color_identity: str
    rarity: str
    artist: str
    price_eur: Optional[float]
    illustration_id: str
    art_crop_url: str
    owned: int
    proxies: int
    binders: List[str] = field(default_factory=list)
    tags: List[str] = field(default_factory=list)
    description: str = ""
    claimed_by: List[str] = field(default_factory=list)

    @property
    def available(self) -> int:
        return max(0, self.owned - sum(q for _, q in self._claims))

    _claims: List[tuple] = field(default_factory=list)

    def short(self) -> str:
        return "{} ({}) {}".format(self.name, (self.set_code or "").upper(),
                                   self.collector_number)


def _claims_by_oracle() -> Dict[str, List[tuple]]:
    out: Dict[str, List[tuple]] = defaultdict(list)
    for deck in decks.load_all():
        for card in deck.cards:
            if card.status in decks.CONSUMING:
                out[card.oracle_id].append((deck.name, card.quantity))
    return out


def find(conn, tags: List[str] = None, text: str = "", colors: str = "",
         type_contains: str = "", available_only: bool = False,
         include_proxies: bool = True, snapshot_id: int = None,
         limit: int = None) -> List[Hit]:
    snapshot_id = snapshot_id or db.latest_snapshot_id(conn)
    if snapshot_id is None:
        return []

    where = ["l.snapshot_id = ?", "l.is_wishlist = 0"]
    params: List = [snapshot_id]
    if not include_proxies:
        where.append("l.is_proxy = 0")
    if type_contains:
        where.append("LOWER(c.type_line) LIKE ?")
        params.append("%" + type_contains.lower() + "%")
    if colors:
        # Colour identity must be a subset of what was asked for (deck legality).
        for letter in "WUBRG":
            if letter not in colors.upper():
                where.append("c.color_identity NOT LIKE ?")
                params.append("%" + letter + "%")

    sql = """
        SELECT c.scryfall_id, c.oracle_id, c.name, c.set_code, c.collector_number,
               c.type_line, c.mana_cost, c.cmc, c.color_identity, c.rarity,
               c.artist, c.price_eur,
               COALESCE(SUM(CASE WHEN l.is_proxy=0 THEN l.quantity END),0) AS owned,
               COALESCE(SUM(CASE WHEN l.is_proxy=1 THEN l.quantity END),0) AS proxies,
               GROUP_CONCAT(DISTINCT l.binder_name) AS binders
          FROM lots l JOIN cards c ON c.scryfall_id = l.scryfall_id
         WHERE {}
      GROUP BY c.scryfall_id
    """.format(" AND ".join(where))

    rows = conn.execute(sql, params).fetchall()

    art = defaultdict(lambda: {"tags": [], "description": "", "illus": "", "url": ""})
    for row in conn.execute(
            "SELECT f.scryfall_id, f.illustration_id, f.art_crop_url,"
            "       t.tag, n.description"
            "  FROM card_faces f"
            "  LEFT JOIN art_tags t ON t.illustration_id = f.illustration_id"
            "  LEFT JOIN art_notes n ON n.illustration_id = f.illustration_id"):
        entry = art[row["scryfall_id"]]
        entry["illus"] = entry["illus"] or (row["illustration_id"] or "")
        entry["url"] = entry["url"] or (row["art_crop_url"] or "")
        entry["description"] = entry["description"] or (row["description"] or "")
        if row["tag"] and row["tag"] not in entry["tags"]:
            entry["tags"].append(row["tag"])

    claims = _claims_by_oracle()
    wanted_tags = [t.lower() for t in (tags or [])]
    needle = text.lower().strip()

    hits = []
    for row in rows:
        meta = art.get(row["scryfall_id"], {"tags": [], "description": "",
                                            "illus": "", "url": ""})
        card_tags = meta["tags"]
        if wanted_tags and not all(t in card_tags for t in wanted_tags):
            continue
        if needle:
            haystack = " ".join([row["name"], row["type_line"] or "",
                                 meta["description"], " ".join(card_tags)]).lower()
            if needle not in haystack:
                continue

        hit = Hit(
            scryfall_id=row["scryfall_id"], oracle_id=row["oracle_id"],
            name=row["name"], set_code=row["set_code"] or "",
            collector_number=row["collector_number"] or "",
            type_line=row["type_line"] or "", mana_cost=row["mana_cost"] or "",
            cmc=row["cmc"] or 0.0, color_identity=row["color_identity"] or "",
            rarity=row["rarity"] or "", artist=row["artist"] or "",
            price_eur=row["price_eur"], illustration_id=meta["illus"],
            art_crop_url=meta["url"], owned=row["owned"], proxies=row["proxies"],
            binders=sorted(set((row["binders"] or "").split(","))) if row["binders"] else [],
            tags=sorted(card_tags), description=meta["description"],
        )
        hit._claims = claims.get(row["oracle_id"], [])
        hit.claimed_by = [name for name, _ in hit._claims]
        if available_only and hit.available <= 0:
            continue
        hits.append(hit)

    hits.sort(key=lambda h: (-h.available, h.name))
    return hits[:limit] if limit else hits


def format_hits(hits: List[Hit], limit: int = 40) -> str:
    if not hits:
        return "Nothing matched."
    lines = ["{} card(s) matched:".format(len(hits)), ""]
    for hit in hits[:limit]:
        status = "{} available".format(hit.available)
        if hit.claimed_by:
            status += ", in " + "/".join(sorted(set(hit.claimed_by)))
        if hit.proxies:
            status += ", {} proxy".format(hit.proxies)
        lines.append("  {:<40s} {:<26s} [{}]".format(
            hit.short()[:40], hit.type_line[:26], status))
        if hit.description:
            lines.append("      {}".format(hit.description[:90]))
        elif hit.tags:
            lines.append("      {}".format(", ".join(hit.tags[:10])))
    if len(hits) > limit:
        lines.append("  ... and {} more".format(len(hits) - limit))
    return "\n".join(lines)
