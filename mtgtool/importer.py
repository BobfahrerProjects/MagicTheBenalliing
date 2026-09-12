"""Importing an export and working out what changed.

The delta is the heart of the whole tool: Ben exports everything, every time, and
we work out the difference. The part that matters most is **move detection**. A
card leaving "Pool - Old" and appearing in deck "Hazel" is one event -- it got built
into a deck -- not an addition plus a removal. Without pairing those up, every
deck-building session would read as though the collection churned wildly, and the
signal we actually care about (what is genuinely new) would drown.
"""
from __future__ import annotations

import datetime
import os
import shutil
from collections import defaultdict
from typing import Dict, List, Optional, Tuple

from . import db, manabox, paths, scryfall, tagging_meta

# Identity of a physical card, ignoring which binder it currently sits in.
Identity = Tuple[str, str, str, str, int, int]


def identity_of(key: manabox.LotKey) -> Identity:
    binder_name, binder_type, sid, foil, condition, language, misprint, altered = key
    return (sid, foil, condition, language, misprint, altered)


def binder_of(key: manabox.LotKey) -> Tuple[str, str]:
    return (key[0], key[1])


class ImportBlocked(Exception):
    """Import stopped on purpose. The message says what a human must decide."""


def _same_bytes(a_path: str, b_path: str) -> bool:
    with open(a_path, "rb") as a, open(b_path, "rb") as b:
        return a.read() == b.read()


def stage_snapshot(csv_path: str) -> Tuple[str, str]:
    """Copy an export into data/snapshots/. Returns (path, export_date).

    Snapshots are immutable: an existing file is never overwritten. Re-importing
    the identical file is a no-op and reuses it.

    ManaBox's own download is just "ManaBox_Collection.csv" with no date, and
    exporting twice in one day is normal -- you scan a few packs, export, scan
    more, export again. So a same-day export with different content gets a time
    suffix rather than being refused; refusing would make the tool useless on the
    day you actually use it most.
    """
    paths.ensure_dirs()
    date = manabox.export_date_from_filename(csv_path)
    stamp = datetime.datetime.fromtimestamp(os.path.getmtime(csv_path))
    if not date:
        date = stamp.strftime("%Y-%m-%d")

    stems = [
        (date.replace("-", ""), date),
        (date.replace("-", "") + "T" + stamp.strftime("%H%M"),
         date + "T" + stamp.strftime("%H:%M")),
    ]
    for index in range(2, 40):
        stems.append((date.replace("-", "") + "T{}{}".format(stamp.strftime("%H%M"), index),
                      date + "T{}-{}".format(stamp.strftime("%H:%M"), index)))

    for stem, export_date in stems:
        target = os.path.join(paths.SNAPSHOTS, "ManaBox_{}.csv".format(stem))
        if os.path.abspath(csv_path) == os.path.abspath(target):
            return target, export_date
        if not os.path.exists(target):
            shutil.copy2(csv_path, target)
            return target, export_date
        if _same_bytes(target, csv_path):
            return target, export_date  # already imported; nothing to do

    raise ImportBlocked(
        "too many distinct exports already staged for {}. Move older snapshots "
        "out of {} if you really need more.".format(date, paths.SNAPSHOTS))


def snapshot_lots(conn, snapshot_id: int) -> Dict[manabox.LotKey, int]:
    rows = conn.execute(
        "SELECT binder_name, binder_type, scryfall_id, foil, condition, language,"
        "       misprint, altered, quantity FROM lots WHERE snapshot_id = ?",
        (snapshot_id,),
    ).fetchall()
    return {
        (r["binder_name"], r["binder_type"], r["scryfall_id"], r["foil"],
         r["condition"], r["language"], r["misprint"], r["altered"]): r["quantity"]
        for r in rows
    }


def compute_delta(
    previous: Dict[manabox.LotKey, int],
    current: Dict[manabox.LotKey, int],
) -> List[dict]:
    """Classify the change between two snapshots, pairing moves.

    Within one card identity, a negative change in binder A and a positive change
    in binder B are the same cards relocating. Only what is left over after that
    pairing is a genuine addition or removal.

    Where several binders gained and several lost, which copy went where is not
    recoverable from the export -- so the pairing is a best-effort attribution that
    always conserves quantity. It never invents a move: if no binder decreased,
    nothing moved, and the growth is reported as acquisition.
    """
    per_identity: Dict[Identity, Dict[Tuple[str, str], int]] = defaultdict(dict)
    for key in set(previous) | set(current):
        change = current.get(key, 0) - previous.get(key, 0)
        if change:
            per_identity[identity_of(key)][binder_of(key)] = change

    changes: List[dict] = []
    for identity, by_binder in per_identity.items():
        sid = identity[0]
        gains = sorted([(b, q) for b, q in by_binder.items() if q > 0])
        losses = sorted([(b, -q) for b, q in by_binder.items() if q < 0])

        gi = li = 0
        while gi < len(gains) and li < len(losses):
            (to_binder, gain), (from_binder, loss) = gains[gi], losses[li]
            moved = min(gain, loss)
            changes.append({
                "change_type": "moved", "scryfall_id": sid,
                "binder_from": from_binder[0], "binder_to": to_binder[0],
                "qty_delta": moved,
            })
            gains[gi] = (to_binder, gain - moved)
            losses[li] = (from_binder, loss - moved)
            if gains[gi][1] == 0:
                gi += 1
            if losses[li][1] == 0:
                li += 1

        for binder, qty in gains[gi:]:
            if qty <= 0:
                continue
            existed = previous.get(_rekey(identity, binder), 0) > 0
            changes.append({
                "change_type": "qty_up" if existed else "added", "scryfall_id": sid,
                "binder_from": None, "binder_to": binder[0], "qty_delta": qty,
            })
        for binder, qty in losses[li:]:
            if qty <= 0:
                continue
            remains = current.get(_rekey(identity, binder), 0) > 0
            changes.append({
                "change_type": "qty_down" if remains else "removed", "scryfall_id": sid,
                "binder_from": binder[0], "binder_to": None, "qty_delta": qty,
            })
    return changes


def _rekey(identity: Identity, binder: Tuple[str, str]) -> manabox.LotKey:
    sid, foil, condition, language, misprint, altered = identity
    return (binder[0], binder[1], sid, foil, condition, language, misprint, altered)


def run_import(
    conn,
    csv_path: str,
    allow_proxy_conflicts: bool = False,
    offline: bool = False,
    log=print,
) -> dict:
    db.init(conn)
    staged, export_date = stage_snapshot(csv_path)
    log("snapshot: {}".format(os.path.relpath(staged, paths.ROOT)))

    parsed = manabox.parse(staged)
    log("  {} rows -> {} lots, {} cards".format(
        parsed.csv_rows, len(parsed.lots), parsed.csv_cards))
    if len(parsed.lots) != parsed.csv_rows:
        log("  {} lot(s) aggregated from multiple rows (differing purchase price)"
            .format(parsed.csv_rows - len(parsed.lots)))

    if parsed.conflicts and not allow_proxy_conflicts:
        detail = "\n".join(c.describe() for c in parsed.conflicts[:20])
        raise ImportBlocked(
            "{} lot(s) disagree about being proxies:\n{}\n\n"
            "Every proxy so far has agreed on all four markers (binder 'Proxies',\n"
            "condition 'poor', misprint true, price 0). A disagreement means your\n"
            "filing habit changed, and guessing would put proxy value into your\n"
            "real collection total.\n"
            "Fix: record the verdict in {} under \"proxy\", or re-run with\n"
            "--allow-proxy-conflicts to treat them as real cards."
            .format(len(parsed.conflicts), detail,
                    os.path.relpath(paths.OVERRIDES, paths.ROOT))
        )

    # ---- Scryfall enrichment ----
    cache = scryfall.load_cache()
    needed = {lot.scryfall_id for lot in parsed.lots.values()}
    missing = needed - set(cache)
    if missing and offline:
        raise ImportBlocked(
            "{} card(s) are not in the local Scryfall cache and --offline was given."
            .format(len(missing))
        )
    if missing:
        log("  fetching {} new card(s) from Scryfall...".format(len(missing)))
        cache, not_found = scryfall.fetch_missing(
            missing, cache,
            progress=lambda done, total: log("    {}/{}".format(done, total)))
        if not_found:
            raise ImportBlocked(
                "Scryfall does not recognise {} ID(s), e.g. {}. The export may be "
                "corrupt; identification cannot proceed on a name alone."
                .format(len(not_found), ", ".join(not_found[:3]))
            )
        scryfall.save_cache(cache)

    # ---- Write ----
    existing = conn.execute(
        "SELECT snapshot_id FROM snapshots WHERE filename = ?",
        (os.path.basename(staged),)).fetchone()
    previous_id = db.latest_snapshot_id(conn)
    if existing and previous_id == existing["snapshot_id"]:
        previous_row = conn.execute(
            "SELECT snapshot_id FROM snapshots WHERE snapshot_id < ? "
            "ORDER BY snapshot_id DESC LIMIT 1", (existing["snapshot_id"],)).fetchone()
        previous_id = previous_row["snapshot_id"] if previous_row else None

    previous = snapshot_lots(conn, previous_id) if previous_id else {}

    if existing:
        snapshot_id = existing["snapshot_id"]
        conn.execute("DELETE FROM lots WHERE snapshot_id = ?", (snapshot_id,))
        conn.execute("DELETE FROM deltas WHERE snapshot_id = ?", (snapshot_id,))
        conn.execute(
            "UPDATE snapshots SET export_date=?, imported_at=?, csv_rows=?, csv_cards=? "
            "WHERE snapshot_id=?",
            (export_date, datetime.datetime.now().isoformat(timespec="seconds"),
             parsed.csv_rows, parsed.csv_cards, snapshot_id))
    else:
        cursor = conn.execute(
            "INSERT INTO snapshots (filename, export_date, imported_at, csv_rows, csv_cards)"
            " VALUES (?,?,?,?,?)",
            (os.path.basename(staged), export_date,
             datetime.datetime.now().isoformat(timespec="seconds"),
             parsed.csv_rows, parsed.csv_cards))
        snapshot_id = cursor.lastrowid

    for key, lot in parsed.lots.items():
        conn.execute(
            "INSERT INTO lots (snapshot_id, binder_name, binder_type, scryfall_id,"
            " foil, condition, language, misprint, altered, quantity, purchase_price,"
            " is_proxy, is_wishlist) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (snapshot_id, lot.binder_name, lot.binder_type, lot.scryfall_id, lot.foil,
             lot.condition, lot.language, lot.misprint, lot.altered, lot.quantity,
             lot.unit_price, 1 if parsed.proxy_flags[key] else 0,
             1 if lot.is_wishlist else 0))

    for sid in needed:
        card = cache[sid]
        row = scryfall.to_row(card)
        conn.execute(
            "INSERT OR REPLACE INTO cards ({}) VALUES ({})".format(
                ",".join(row), ",".join("?" * len(row))),
            tuple(row.values()))
        conn.execute("DELETE FROM card_faces WHERE scryfall_id = ?", (sid,))
        for face in scryfall.faces(card):
            conn.execute(
                "INSERT INTO card_faces (scryfall_id, face_index, face_name,"
                " face_type_line, illustration_id, art_crop_url, normal_url)"
                " VALUES (?,?,?,?,?,?,?)",
                (sid, face["face_index"], face["face_name"], face["face_type_line"],
                 face["illustration_id"], face["art_crop_url"], face["normal_url"]))

    current = snapshot_lots(conn, snapshot_id)
    changes = compute_delta(previous, current) if previous_id else []
    for change in changes:
        conn.execute(
            "INSERT INTO deltas (snapshot_id, change_type, scryfall_id, binder_from,"
            " binder_to, qty_delta) VALUES (?,?,?,?,?,?)",
            (snapshot_id, change["change_type"], change["scryfall_id"],
             change["binder_from"], change["binder_to"], change["qty_delta"]))

    # Free, zero-token tags derived from metadata alone.
    tagged = tagging_meta.apply_metadata_tags(conn)
    conn.commit()

    return {
        "snapshot_id": snapshot_id,
        "previous_id": previous_id,
        "lots": len(parsed.lots),
        "cards": parsed.csv_cards,
        "rows": parsed.csv_rows,
        "changes": changes,
        "metadata_tags": tagged,
        "proxy_conflicts": len(parsed.conflicts),
    }


def summarise(changes: List[dict]) -> Dict[str, int]:
    out: Dict[str, int] = defaultdict(int)
    for change in changes:
        out[change["change_type"]] += change["qty_delta"]
    return dict(out)
