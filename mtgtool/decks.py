"""Deck plans, and the rule that one physical card lives in one deck.

Two layers are kept deliberately apart:

  physical  -- where ManaBox says a card actually is. Authoritative, read-only.
  planned   -- which deck has *claimed* a copy. Ours, and only ever a plan.

A deck asks for cards at the **oracle** level (a Sol Ring is a Sol Ring, whichever
set it came from) and each request is then filled by a specific printing. The
constraint that matters:

    for every card:  copies claimed by all decks  <=  real copies owned

When that breaks, nothing is auto-resolved. The slot becomes `contested` and the
three real options are offered: move it, proxy it, or buy another. That is what Ben
already does by hand -- fifteen Rootha staples exist as proxies precisely so other
decks can run them -- so the tool surfaces the choice rather than pretending the
conflict is not there.

Deck plans live in data/decks/*.json. That file is the durable copy; the database
tables are rebuilt from it.
"""
from __future__ import annotations

import csv
import datetime
import json
import os
import re
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Dict, List, Optional

from . import db, identity, manabox, paths

# Only these consume a real owned copy.
CONSUMING = ("assigned", "contested")
STATUSES = ("assigned", "proxy", "wanted", "contested")


class DeckError(Exception):
    pass


def slugify(name: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")
    return slug or "deck"


@dataclass
class DeckCard:
    oracle_id: str
    name: str
    scryfall_id: str
    set_code: str = ""
    collector_number: str = ""
    quantity: int = 1
    role: str = ""
    status: str = "assigned"

    def to_json(self) -> dict:
        return {
            "oracle_id": self.oracle_id, "name": self.name,
            "scryfall_id": self.scryfall_id, "set": self.set_code,
            "number": self.collector_number, "quantity": self.quantity,
            "role": self.role, "status": self.status,
        }

    @staticmethod
    def from_json(blob: dict) -> "DeckCard":
        return DeckCard(
            oracle_id=blob["oracle_id"], name=blob["name"],
            scryfall_id=blob["scryfall_id"], set_code=blob.get("set", ""),
            collector_number=blob.get("number", ""),
            quantity=int(blob.get("quantity", 1)), role=blob.get("role", ""),
            status=blob.get("status", "assigned"))


@dataclass
class Deck:
    deck_id: str
    name: str
    fmt: str = "commander"
    commander_scryfall_id: Optional[str] = None
    manabox_binder: str = ""
    status: str = "building"
    notes: str = ""
    cards: List[DeckCard] = field(default_factory=list)

    @property
    def binder(self) -> str:
        return self.manabox_binder or self.name

    @property
    def total_cards(self) -> int:
        return sum(c.quantity for c in self.cards)

    def path(self) -> str:
        return os.path.join(paths.DECKS, self.deck_id + ".json")

    def to_json(self) -> dict:
        return {
            "deck_id": self.deck_id, "name": self.name, "format": self.fmt,
            "commander_scryfall_id": self.commander_scryfall_id,
            "manabox_binder": self.manabox_binder, "status": self.status,
            "notes": self.notes,
            "cards": [c.to_json() for c in self.cards],
        }

    @staticmethod
    def from_json(blob: dict) -> "Deck":
        return Deck(
            deck_id=blob["deck_id"], name=blob["name"],
            fmt=blob.get("format", "commander"),
            commander_scryfall_id=blob.get("commander_scryfall_id"),
            manabox_binder=blob.get("manabox_binder", ""),
            status=blob.get("status", "building"), notes=blob.get("notes", ""),
            cards=[DeckCard.from_json(c) for c in blob.get("cards", [])])


# ---- persistence ----

def save(deck: Deck) -> str:
    paths.ensure_dirs()
    target = deck.path()
    tmp = target + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(deck.to_json(), fh, indent=2, ensure_ascii=False)
        fh.write("\n")
    os.replace(tmp, target)
    return target


def load(deck_id: str) -> Deck:
    target = os.path.join(paths.DECKS, deck_id + ".json")
    if not os.path.exists(target):
        raise DeckError("no deck {!r} (looked in {})".format(
            deck_id, os.path.relpath(paths.DECKS, paths.ROOT)))
    with open(target, "r", encoding="utf-8") as fh:
        return Deck.from_json(json.load(fh))


def load_all() -> List[Deck]:
    paths.ensure_dirs()
    out = []
    for entry in sorted(os.listdir(paths.DECKS)):
        if entry.endswith(".json"):
            with open(os.path.join(paths.DECKS, entry), "r", encoding="utf-8") as fh:
                out.append(Deck.from_json(json.load(fh)))
    return out


def sync_to_db(conn) -> int:
    """Rebuild the deck tables from the JSON files. The files are the truth."""
    conn.execute("DELETE FROM assignments")
    conn.execute("DELETE FROM deck_slots")
    conn.execute("DELETE FROM decks")
    decks = load_all()
    for deck in decks:
        conn.execute(
            "INSERT INTO decks (deck_id, name, format, commander_scryfall_id, status, notes)"
            " VALUES (?,?,?,?,?,?)",
            (deck.deck_id, deck.name, deck.fmt, deck.commander_scryfall_id,
             deck.status, deck.notes))
        for index, card in enumerate(deck.cards):
            slot_id = "{}:{}".format(deck.deck_id, index)
            conn.execute(
                "INSERT INTO deck_slots (slot_id, deck_id, oracle_id, display_name,"
                " role, status, quantity) VALUES (?,?,?,?,?,?,?)",
                (slot_id, deck.deck_id, card.oracle_id, card.name, card.role,
                 card.status, card.quantity))
            conn.execute(
                "INSERT INTO assignments (slot_id, scryfall_id, recorded_name,"
                " recorded_set, recorded_number, quantity) VALUES (?,?,?,?,?,?)",
                (slot_id, card.scryfall_id, card.name, card.set_code,
                 card.collector_number, card.quantity))
    conn.commit()
    return len(decks)


# ---- building ----

def create(name: str, fmt: str = "commander", binder: str = "") -> Deck:
    deck = Deck(deck_id=slugify(name), name=name, fmt=fmt, manabox_binder=binder)
    if os.path.exists(deck.path()):
        raise DeckError("deck {!r} already exists at {}".format(
            deck.deck_id, os.path.relpath(deck.path(), paths.ROOT)))
    save(deck)
    return deck


def add_card(conn, deck: Deck, candidate: identity.Candidate, quantity: int = 1,
             role: str = "", status: str = None) -> DeckCard:
    """Claim a printing for this deck.

    `candidate` comes from identity.resolve/by_id, so a bare name can never reach
    here without a human having disambiguated it.
    """
    if status is not None and status not in STATUSES:
        raise DeckError("status must be one of {}".format(", ".join(STATUSES)))

    if status is None:
        owned = identity.owned_copies(conn, candidate.oracle_id)
        claimed = claimed_elsewhere(conn, candidate.oracle_id, deck.deck_id)
        status = "assigned" if claimed + quantity <= owned else "contested"
        if owned == 0:
            status = "proxy" if identity.proxy_copies(conn, candidate.oracle_id) else "wanted"

    for existing in deck.cards:
        if existing.scryfall_id == candidate.scryfall_id:
            existing.quantity += quantity
            save(deck)
            return existing

    card = DeckCard(
        oracle_id=candidate.oracle_id, name=candidate.name,
        scryfall_id=candidate.scryfall_id, set_code=candidate.set_code,
        collector_number=candidate.collector_number, quantity=quantity,
        role=role, status=status)
    deck.cards.append(card)
    save(deck)
    return card


def remove_card(deck: Deck, scryfall_id: str, quantity: int = None) -> bool:
    for card in list(deck.cards):
        if card.scryfall_id == scryfall_id:
            if quantity is None or card.quantity <= quantity:
                deck.cards.remove(card)
            else:
                card.quantity -= quantity
            save(deck)
            return True
    return False


def claimed_elsewhere(conn, oracle_id: str, except_deck: str = None) -> int:
    """Copies of this card claimed by other decks (only consuming statuses)."""
    total = 0
    for deck in load_all():
        if deck.deck_id == except_deck:
            continue
        for card in deck.cards:
            if card.oracle_id == oracle_id and card.status in CONSUMING:
                total += card.quantity
    return total


def seed_from_snapshot(conn, snapshot_id: int = None,
                       overwrite: bool = False) -> List[Deck]:
    """Create deck plans from the decks that already exist in ManaBox.

    Ben has seven decks filed in the app already. Starting from an empty plan
    would make the tool useless on day one and would invite a plan that silently
    contradicts the shoebox. Cards physically filed in a deck are `assigned` by
    definition -- they are already there.
    """
    snapshot_id = snapshot_id or db.latest_snapshot_id(conn)
    if snapshot_id is None:
        raise DeckError("no snapshot imported yet")

    rows = conn.execute(
        "SELECT l.binder_name, l.scryfall_id, l.quantity, l.is_proxy,"
        "       c.oracle_id, c.name, c.set_code, c.collector_number"
        "  FROM lots l JOIN cards c ON c.scryfall_id = l.scryfall_id"
        " WHERE l.snapshot_id = ? AND l.binder_type = 'deck'"
        " ORDER BY l.binder_name, c.name", (snapshot_id,)).fetchall()

    grouped: Dict[str, List] = defaultdict(list)
    for row in rows:
        grouped[row["binder_name"]].append(row)

    created = []
    for binder_name, cards in grouped.items():
        deck_id = slugify(binder_name)
        target = os.path.join(paths.DECKS, deck_id + ".json")
        if os.path.exists(target) and not overwrite:
            continue
        deck = Deck(deck_id=deck_id, name=binder_name, manabox_binder=binder_name,
                    notes="Seeded from ManaBox snapshot {}.".format(snapshot_id))
        for row in cards:
            deck.cards.append(DeckCard(
                oracle_id=row["oracle_id"], name=row["name"],
                scryfall_id=row["scryfall_id"], set_code=row["set_code"] or "",
                collector_number=row["collector_number"] or "",
                quantity=row["quantity"],
                status="proxy" if row["is_proxy"] else "assigned"))
        save(deck)
        created.append(deck)
    return created


# ---- contention ----

@dataclass
class Contention:
    oracle_id: str
    name: str
    owned: int
    proxies: int
    demanded: int
    claims: List[tuple]  # (deck_name, quantity, status)

    @property
    def shortfall(self) -> int:
        return self.demanded - self.owned


def contentions(conn) -> List[Contention]:
    """Cards wanted by more decks than there are real copies."""
    demand: Dict[str, List[tuple]] = defaultdict(list)
    names: Dict[str, str] = {}
    for deck in load_all():
        for card in deck.cards:
            if card.status in CONSUMING:
                demand[card.oracle_id].append((deck.name, card.quantity, card.status))
                names[card.oracle_id] = card.name

    out = []
    for oracle_id, claims in demand.items():
        demanded = sum(q for _, q, _ in claims)
        owned = identity.owned_copies(conn, oracle_id)
        if demanded > owned:
            out.append(Contention(
                oracle_id=oracle_id, name=names[oracle_id], owned=owned,
                proxies=identity.proxy_copies(conn, oracle_id),
                demanded=demanded, claims=claims))
    out.sort(key=lambda c: (-c.shortfall, c.name))
    return out


def format_contentions(items: List[Contention]) -> str:
    if not items:
        return "No contested cards: every claimed copy is backed by a real card."
    lines = ["{} contested card(s) -- more decks want them than you own:".format(len(items))]
    for item in items:
        lines.append("")
        lines.append("  {}  (own {} real, {} proxy; {} claimed)".format(
            item.name, item.owned, item.proxies, item.demanded))
        for deck_name, qty, status in item.claims:
            lines.append("      {}x  {}  [{}]".format(qty, deck_name, status))
        options = ["move it between decks",
                   "buy {} more".format(item.shortfall)]
        if item.proxies:
            options.insert(1, "use one of your {} proxies".format(item.proxies))
        else:
            options.insert(1, "proxy it")
        lines.append("      options: " + "; ".join(options))
    return "\n".join(lines)


# ---- drift: plan vs. physical reality ----

@dataclass
class Drift:
    deck_name: str
    card_name: str
    expected_binder: str
    actual: str


def drift(conn, snapshot_id: int = None) -> List[Drift]:
    """Where the plan and ManaBox disagree about a card's location.

    ManaBox always wins as a statement of fact. This only reports; it never
    rewrites either side.
    """
    snapshot_id = snapshot_id or db.latest_snapshot_id(conn)
    if snapshot_id is None:
        return []

    placement: Dict[str, List[str]] = defaultdict(list)
    for row in conn.execute(
            "SELECT scryfall_id, binder_name, quantity FROM lots"
            " WHERE snapshot_id = ? AND is_wishlist = 0", (snapshot_id,)):
        placement[row["scryfall_id"]].append(row["binder_name"])

    out = []
    for deck in load_all():
        for card in deck.cards:
            if card.status != "assigned":
                continue
            where = placement.get(card.scryfall_id, [])
            if not where:
                out.append(Drift(deck.name, card.name, deck.binder,
                                 "not in the collection at all"))
            elif deck.binder not in where:
                out.append(Drift(deck.name, card.name, deck.binder,
                                 "physically in " + ", ".join(sorted(set(where)))))
    return out


def format_drift(items: List[Drift]) -> str:
    if not items:
        return "No drift: every assigned card is physically where the plan expects."
    lines = ["{} card(s) are not where the plan expects:".format(len(items))]
    for item in items:
        lines.append("  {:<30s} planned in {!r} but {}".format(
            item.card_name[:30], item.expected_binder, item.actual))
    lines.append("")
    lines.append("ManaBox is the authority on physical location. Either move the "
                 "cards, or update the plan.")
    return "\n".join(lines)


# ---- export ----

def export_manabox(conn, deck: Deck, out_path: str = None,
                   snapshot_id: int = None) -> str:
    """Write the deck as a ManaBox-importable CSV.

    The column list is taken verbatim from the export ManaBox itself produced, so
    the round trip is against the real format rather than a guess at it. Values we
    cannot know for a planned card (purchase price, added date) are left blank
    rather than invented.
    """
    snapshot_id = snapshot_id or db.latest_snapshot_id(conn)
    out_path = out_path or os.path.join(paths.BUILD, "{}_manabox.csv".format(deck.deck_id))
    paths.ensure_dirs()

    lot_by_card = {}
    for row in conn.execute(
            "SELECT scryfall_id, foil, condition, language, misprint, altered,"
            "       purchase_price, is_proxy FROM lots"
            " WHERE snapshot_id = ? AND is_wishlist = 0", (snapshot_id,)):
        lot_by_card.setdefault(row["scryfall_id"], row)

    with open(out_path, "w", encoding="utf-8", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=manabox.EXPECTED_COLUMNS)
        writer.writeheader()
        for card in deck.cards:
            if card.status == "wanted":
                continue  # not owned; nothing to file in ManaBox yet
            info = conn.execute(
                "SELECT name, set_code, set_name, collector_number, rarity"
                "  FROM cards WHERE scryfall_id = ?", (card.scryfall_id,)).fetchone()
            if info is None:
                raise DeckError(
                    "{} is not a known card; refusing to export a deck we cannot "
                    "identify".format(card.scryfall_id))
            lot = lot_by_card.get(card.scryfall_id)
            is_proxy = card.status == "proxy" or (lot and lot["is_proxy"])
            writer.writerow({
                "Binder Name": deck.binder,
                "Binder Type": "deck",
                "Name": info["name"],
                "Set code": info["set_code"],
                "Set name": info["set_name"],
                "Collector number": info["collector_number"],
                "Foil": lot["foil"] if lot else "normal",
                "Rarity": info["rarity"],
                "Quantity": card.quantity,
                "ManaBox ID": "",
                "Scryfall ID": card.scryfall_id,
                "Purchase price": 0 if is_proxy else (
                    lot["purchase_price"] if lot and lot["purchase_price"] is not None else ""),
                "Misprint": "true" if is_proxy else "false",
                "Altered": "true" if is_proxy else "false",
                "Condition": "poor" if is_proxy else (lot["condition"] if lot else "near_mint"),
                "Language": lot["language"] if lot else "en",
                "Purchase price currency": "EUR",
                "Added": datetime.datetime.now().strftime("%Y-%m-%dT%H:%M:%S.000Z"),
            })
    return out_path


def export_text(conn, deck: Deck, out_path: str = None) -> str:
    """Plain decklist: "1 Sol Ring (40K) 252" -- Moxfield/Archidekt readable."""
    out_path = out_path or os.path.join(paths.BUILD, "{}.txt".format(deck.deck_id))
    paths.ensure_dirs()
    with open(out_path, "w", encoding="utf-8") as fh:
        for card in deck.cards:
            fh.write("{} {} ({}) {}\n".format(
                card.quantity, card.name, (card.set_code or "").upper(),
                card.collector_number))
    return out_path
