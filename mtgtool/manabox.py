"""Reading a ManaBox collection export.

Two things in here exist because the real export proved they were needed, not
because they seemed prudent:

1. **Lot aggregation.** ManaBox emits more than one row for the same physical stack
   when the copies were bought at different prices. The 2026-09-12 export contains
   three such pairs (Lightshell Duo, Sylvan Scrying, Seething Song). Code that
   assumed the lot key was unique would silently drop cards. We sum instead, and
   assert the total is preserved.

2. **Four-signal proxy detection.** Ben's proxies agree on four independent markers:
   binder "Proxies", condition "poor", misprint true, purchase price 0. All 255 of
   them, unanimously. We require that unanimity and stop when it breaks, because a
   changed filing habit must become a question rather than a quietly wrong
   collection value -- proxies are 69% of the naive total.
"""
from __future__ import annotations

import csv
import json
import os
import re
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

from . import paths

EXPECTED_COLUMNS = [
    "Binder Name", "Binder Type", "Name", "Set code", "Set name",
    "Collector number", "Foil", "Rarity", "Quantity", "ManaBox ID",
    "Scryfall ID", "Purchase price", "Misprint", "Altered", "Condition",
    "Language", "Purchase price currency", "Added",
]

PROXY_BINDER = "proxies"

LotKey = Tuple[str, str, str, str, str, str, int, int]


class ManaBoxFormatError(Exception):
    """The export is not shaped the way we understand. Never guess past this."""


@dataclass
class Lot:
    binder_name: str
    binder_type: str
    scryfall_id: str
    foil: str
    condition: str
    language: str
    misprint: int
    altered: int
    quantity: int = 0
    prices: List[Optional[float]] = field(default_factory=list)
    display_name: str = ""
    set_code: str = ""
    collector_number: str = ""
    source_rows: int = 0

    @property
    def key(self) -> LotKey:
        return (self.binder_name, self.binder_type, self.scryfall_id, self.foil,
                self.condition, self.language, self.misprint, self.altered)

    @property
    def is_wishlist(self) -> bool:
        return self.binder_type == "list"

    @property
    def unit_price(self) -> Optional[float]:
        """Mean known purchase price, or None when no row carried one."""
        known = [p for p in self.prices if p is not None]
        return sum(known) / len(known) if known else None

    def proxy_signals(self) -> Dict[str, bool]:
        """The card-level proxy fingerprint.

        Deliberately excludes the binder name. Proxies live in the "Proxies"
        binder only until they are built into a deck -- at which point they sit in
        a deck binder and are still proxies. Requiring the binder name would make
        every proxied deck slot look like a real card and inflate the collection
        value, which is the one error this whole module exists to prevent.
        """
        known = [p for p in self.prices if p is not None]
        return {
            "condition": self.condition == "poor",
            "misprint": bool(self.misprint),
            "price_zero": bool(known) and all(p == 0 for p in known),
        }

    def binder_says_proxy(self) -> bool:
        """Corroborating evidence, used only as a cross-check."""
        return self.binder_name.strip().lower() == PROXY_BINDER


@dataclass
class ProxyConflict:
    lot: Lot
    signals: Dict[str, bool]
    reason: str = "markers disagree"

    def describe(self) -> str:
        agree = ", ".join(k for k, v in self.signals.items() if v) or "(none)"
        disagree = ", ".join(k for k, v in self.signals.items() if not v) or "(none)"
        return (
            "  {name} [{sc} {cn}] in {binder!r} -- {reason}\n"
            "      says proxy: {agree}\n"
            "      says real:  {disagree}".format(
                name=self.lot.display_name, sc=self.lot.set_code,
                cn=self.lot.collector_number, binder=self.lot.binder_name,
                reason=self.reason, agree=agree, disagree=disagree,
            )
        )


@dataclass
class ParsedExport:
    lots: Dict[LotKey, Lot]
    csv_rows: int
    csv_cards: int
    proxy_flags: Dict[LotKey, bool]
    conflicts: List[ProxyConflict]

    @property
    def lot_cards(self) -> int:
        return sum(l.quantity for l in self.lots.values())


def _to_bool(value: str, column: str, row_no: int) -> int:
    v = (value or "").strip().lower()
    if v in ("true", "1", "yes"):
        return 1
    if v in ("false", "0", "no", ""):
        return 0
    raise ManaBoxFormatError(
        "row {}: column {!r} holds {!r}; expected true/false".format(row_no, column, value)
    )


def _to_price(value: str) -> Optional[float]:
    v = (value or "").strip()
    if not v:
        return None
    try:
        return float(v)
    except ValueError:
        return None


def load_overrides(path: str = None) -> Dict[str, bool]:
    """Manual proxy verdicts, keyed by the stringified lot key. Overrides always win."""
    path = path or paths.OVERRIDES
    if not os.path.exists(path):
        return {}
    with open(path, "r", encoding="utf-8") as fh:
        blob = json.load(fh)
    return {str(k): bool(v) for k, v in blob.get("proxy", {}).items()}


def key_str(key: LotKey) -> str:
    return "|".join(str(part) for part in key)


def parse(csv_path: str, overrides: Dict[str, bool] = None) -> ParsedExport:
    """Parse an export into aggregated lots.

    Raises ManaBoxFormatError when the columns are not what we understand -- a
    changed export format must stop the import, not be guessed around.
    """
    overrides = overrides if overrides is not None else load_overrides()

    with open(csv_path, "r", encoding="utf-8-sig", newline="") as fh:
        reader = csv.DictReader(fh)
        if reader.fieldnames is None:
            raise ManaBoxFormatError("{}: file is empty".format(csv_path))
        missing = [c for c in EXPECTED_COLUMNS if c not in reader.fieldnames]
        if missing:
            raise ManaBoxFormatError(
                "{}: missing expected column(s): {}".format(csv_path, ", ".join(missing))
            )

        lots: Dict[LotKey, Lot] = {}
        csv_rows = 0
        csv_cards = 0

        for row_no, row in enumerate(reader, start=2):
            sid = (row["Scryfall ID"] or "").strip()
            if not sid:
                raise ManaBoxFormatError(
                    "row {}: no Scryfall ID for {!r}. Identification is impossible "
                    "without it; fix the export rather than guessing from the name."
                    .format(row_no, row.get("Name"))
                )
            try:
                qty = int((row["Quantity"] or "0").strip())
            except ValueError:
                raise ManaBoxFormatError(
                    "row {}: Quantity {!r} is not a number".format(row_no, row["Quantity"])
                )

            csv_rows += 1
            csv_cards += qty

            lot = Lot(
                binder_name=(row["Binder Name"] or "").strip(),
                binder_type=(row["Binder Type"] or "").strip(),
                scryfall_id=sid,
                foil=(row["Foil"] or "normal").strip(),
                condition=(row["Condition"] or "").strip(),
                language=(row["Language"] or "").strip(),
                misprint=_to_bool(row["Misprint"], "Misprint", row_no),
                altered=_to_bool(row["Altered"], "Altered", row_no),
                display_name=(row["Name"] or "").strip(),
                set_code=(row["Set code"] or "").strip(),
                collector_number=(row["Collector number"] or "").strip(),
            )

            existing = lots.get(lot.key)
            if existing is None:
                lots[lot.key] = lot
                existing = lot
            existing.quantity += qty
            existing.prices.append(_to_price(row["Purchase price"]))
            existing.source_rows += 1

    # Losslessness: aggregation must never lose a card.
    total = sum(l.quantity for l in lots.values())
    if total != csv_cards:
        raise ManaBoxFormatError(
            "aggregation lost cards: CSV totals {} but lots total {}".format(csv_cards, total)
        )

    proxy_flags: Dict[LotKey, bool] = {}
    conflicts: List[ProxyConflict] = []
    for key, lot in lots.items():
        override = overrides.get(key_str(key))
        if override is not None:
            proxy_flags[key] = override
            continue
        if lot.is_wishlist:
            # Wishlists are excluded from everything anyway; the binder name is
            # enough to remember that these were proxies-to-print.
            proxy_flags[key] = lot.binder_name.strip().lower() == PROXY_BINDER
            continue
        signals = lot.proxy_signals()
        if all(signals.values()):
            # Markers unanimous: a proxy, wherever it happens to be filed.
            proxy_flags[key] = True
        elif not any(signals.values()):
            if lot.binder_says_proxy():
                # Filed among the proxies but looks like a real card. Suspicious
                # enough to ask about rather than quietly value at full price.
                conflicts.append(ProxyConflict(
                    lot, signals,
                    reason="filed in the Proxies binder but has no proxy markers"))
            else:
                proxy_flags[key] = False
        else:
            conflicts.append(ProxyConflict(lot, signals))

    return ParsedExport(lots=lots, csv_rows=csv_rows, csv_cards=csv_cards,
                        proxy_flags=proxy_flags, conflicts=conflicts)


# 8 digits first: on "ManaBox_20260912" a leading \d{6} alternative would match
# "202609" and yield the nonsense date 2020-26-09.
DATE_RE = re.compile(r"(\d{8}|\d{6})")


def export_date_from_filename(filename: str) -> str:
    """ManaBox names exports ManaBox_Collection260912.csv -- YYMMDD.

    Returns ISO date, or "" when the name carries none (caller falls back to mtime).
    """
    base = os.path.basename(filename)
    match = DATE_RE.search(base)
    if not match:
        return ""
    digits = match.group(1)
    if len(digits) == 8:
        return "{}-{}-{}".format(digits[0:4], digits[4:6], digits[6:8])
    return "20{}-{}-{}".format(digits[0:2], digits[2:4], digits[4:6])
