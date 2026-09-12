"""What the collection is worth.

One rule dominates: **proxies never count.** In the 2026-09-12 export the proxies
carry more Scryfall value than the real cards do (about EUR 4,300 against EUR 2,000),
so a valuation that merely forgot to exclude them would be wrong by a factor of
three and would still look plausible. Wishlists ("list" binders) are excluded for
the same reason -- Ben does not own those cards at all.

Prices that are missing are reported as missing. They are never silently treated
as zero, because a zero is indistinguishable from a cheap card in a total.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

from . import db

# Used only when a card has a USD price but no EUR one (25 cards today).
# Surfaced in every report that relies on it, never applied invisibly.
DEFAULT_USD_TO_EUR = 0.92


@dataclass
class Bucket:
    cards: int = 0
    value: float = 0.0
    priced_cards: int = 0
    unpriced_cards: int = 0
    converted_cards: int = 0
    finish_fallback_cards: int = 0


@dataclass
class Valuation:
    real: Bucket = field(default_factory=Bucket)
    proxy: Bucket = field(default_factory=Bucket)
    wishlist: Bucket = field(default_factory=Bucket)
    by_binder: Dict[str, Bucket] = field(default_factory=dict)
    top: List[Tuple[str, str, int, float]] = field(default_factory=list)
    unpriced_examples: List[str] = field(default_factory=list)
    usd_rate: float = DEFAULT_USD_TO_EUR


def unit_price(row, usd_rate: float) -> Tuple[Optional[float], str]:
    """(price in EUR, how we got it).

    Sources, in order of preference:
      exact  -- EUR price for this card's actual finish
      finish -- EUR price for the *other* finish (a foil valued at its non-foil
                price, or vice versa). An approximation, so it is counted and
                reported rather than blended invisibly into the total.
      usd    -- converted from USD at usd_rate
      none   -- genuinely unpriced; excluded from the total, never counted as 0
    """
    foil = row["foil"] != "normal"

    eur = row["price_eur_foil"] if foil else row["price_eur"]
    if eur is not None:
        return float(eur), "exact"

    other_eur = row["price_eur"] if foil else row["price_eur_foil"]
    if other_eur is not None:
        return float(other_eur), "finish"

    usd = row["price_usd_foil"] if foil else row["price_usd"]
    if usd is None:
        usd = row["price_usd"] if foil else row["price_usd_foil"]
    if usd is not None:
        return float(usd) * usd_rate, "usd"

    return None, "none"


def value_collection(conn, snapshot_id: int = None,
                     usd_rate: float = DEFAULT_USD_TO_EUR) -> Valuation:
    snapshot_id = snapshot_id or db.latest_snapshot_id(conn)
    if snapshot_id is None:
        return Valuation(usd_rate=usd_rate)

    rows = conn.execute(
        "SELECT l.binder_name, l.binder_type, l.quantity, l.foil, l.is_proxy,"
        "       l.is_wishlist, c.name, c.set_code, c.price_eur, c.price_eur_foil,"
        "       c.price_usd, c.price_usd_foil"
        "  FROM lots l JOIN cards c ON c.scryfall_id = l.scryfall_id"
        " WHERE l.snapshot_id = ?", (snapshot_id,)).fetchall()

    out = Valuation(usd_rate=usd_rate)
    holdings: List[Tuple[str, str, int, float]] = []

    for row in rows:
        if row["is_wishlist"]:
            bucket = out.wishlist
        elif row["is_proxy"]:
            bucket = out.proxy
        else:
            bucket = out.real

        qty = row["quantity"]
        price, source = unit_price(row, usd_rate)
        bucket.cards += qty

        if price is None:
            bucket.unpriced_cards += qty
            if not row["is_wishlist"] and not row["is_proxy"] \
                    and len(out.unpriced_examples) < 10:
                out.unpriced_examples.append(
                    "{} ({})".format(row["name"], (row["set_code"] or "").upper()))
            continue

        line = price * qty
        bucket.value += line
        bucket.priced_cards += qty
        if source == "usd":
            bucket.converted_cards += qty
        elif source == "finish":
            bucket.finish_fallback_cards += qty

        if bucket is out.real:
            binder = out.by_binder.setdefault(row["binder_name"], Bucket())
            binder.cards += qty
            binder.value += line
            holdings.append((row["name"], row["binder_name"], qty, line))

    # Binder card counts must include unpriced cards too.
    for row in rows:
        if row["is_wishlist"] or row["is_proxy"]:
            continue
        price, _source = unit_price(row, usd_rate)
        if price is None:
            binder = out.by_binder.setdefault(row["binder_name"], Bucket())
            binder.cards += row["quantity"]
            binder.unpriced_cards += row["quantity"]

    holdings.sort(key=lambda h: -h[3])
    out.top = holdings[:15]
    return out


def format_report(valuation: Valuation) -> str:
    lines = []
    real, proxy, wish = valuation.real, valuation.proxy, valuation.wishlist

    lines.append("COLLECTION VALUE")
    lines.append("  real cards        EUR {:>10,.2f}   ({:,} cards)".format(
        real.value, real.cards))
    if real.unpriced_cards:
        lines.append("    {} card(s) have no price and are excluded from the total"
                     .format(real.unpriced_cards))
        if valuation.unpriced_examples:
            lines.append("      e.g. " + ", ".join(valuation.unpriced_examples[:5]))
    if real.converted_cards:
        lines.append("    {} card(s) priced from USD at {:.2f} EUR/USD"
                     .format(real.converted_cards, valuation.usd_rate))
    if real.finish_fallback_cards:
        lines.append("    {} card(s) priced from the other finish (approximate)"
                     .format(real.finish_fallback_cards))

    lines.append("")
    lines.append("  EXCLUDED from the total:")
    lines.append("    proxies         EUR {:>10,.2f}   ({:,} cards)".format(
        proxy.value, proxy.cards))
    lines.append("    wishlists       EUR {:>10,.2f}   ({:,} cards, not owned)".format(
        wish.value, wish.cards))

    if valuation.by_binder:
        lines.append("")
        lines.append("BY BINDER (real cards only)")
        for name, bucket in sorted(valuation.by_binder.items(),
                                   key=lambda kv: -kv[1].value):
            lines.append("  {:<28s} EUR {:>9,.2f}  ({:,} cards)".format(
                name[:28], bucket.value, bucket.cards))

    if valuation.top:
        lines.append("")
        lines.append("MOST VALUABLE REAL CARDS")
        for name, binder, qty, line_value in valuation.top:
            qty_label = "{}x ".format(qty) if qty > 1 else "   "
            lines.append("  EUR {:>8,.2f}  {}{:<32s} {}".format(
                line_value, qty_label, name[:32], binder))

    return "\n".join(lines)
