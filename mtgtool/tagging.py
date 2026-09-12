"""Vision tagging of card artwork, via the Batch API.

Scope discipline is what keeps this cheap:

* The unit of work is an **artwork**, not a card row. 2,152 rows collapse to 1,775
  distinct illustrations, and a reprint inherits the tags of the art it shares.
* Only artwork that has never been tagged is ever sent. A re-import of the whole
  collection costs nothing.
* The image is Scryfall's `art_crop` -- the painting alone at 626x457, about 381
  image tokens. The full card would cost more and read worse, because the frame
  and rules text are noise for this question.
* The model is asked only for what metadata cannot already answer. Creature types
  and Universes Beyond franchises are derived for free at import time.

Results land in data/art_tags.jsonl, one JSON object per line, which is the
durable copy: hand-editable, diffable, and independent of the database.
"""
from __future__ import annotations

import json
import os
import urllib.request
from dataclasses import dataclass
from typing import Dict, List, Optional

from . import paths, vocab

MODEL = "claude-opus-5"

# Measured, not guessed: every Scryfall art_crop is 626x457, and the image token
# cost is roughly (width * height) / 750.
IMAGE_TOKENS = 381
PROMPT_TOKENS = 90        # per-request text after the cached vocabulary prefix
OUTPUT_TOKENS = 130       # tags plus a one-line description

# Batch API is half price. Opus 5 list price is $5 / $25 per MTok.
BATCH_INPUT_PER_MTOK = 2.50
BATCH_OUTPUT_PER_MTOK = 12.50
USD_TO_EUR = 0.92

SYSTEM = """You label Magic: The Gathering card ARTWORK for a personal collection \
so the owner can build decks around what the pictures show and how they feel.

You are given only the painting -- no card frame, no rules text, no card name.
Describe what you can actually see.

Choose tags ONLY from this vocabulary. Never invent a tag; if something important \
has no tag, put a short phrase in "other".

{vocabulary}

Guidance:
- Multiple tags per facet are fine. Use only what is clearly present.
- "mood": how the picture feels. "cute" is for genuinely endearing art -- small \
round creatures, soft faces, playful scenes -- not merely pleasant art.
- "palette": the dominant colour character of the image.
- "style": how it is painted, not what it depicts.
- "franchise": only when the art unmistakably belongs to a named crossover \
(Warhammer 40,000, Lord of the Rings, Fallout, Final Fantasy, Marvel, Doctor Who, \
Stardew Valley, Avatar: The Last Airbender, and so on). Use "none" for ordinary \
Magic art. Do not guess from art style alone -- an anime look is not a franchise.
- "description": one plain sentence naming what is depicted, concrete enough to \
search later. Name every animal or character you can see, including small ones in \
the background. For example "a tabby cat in a wizard hat asleep on a stack of \
spellbooks". No flourishes."""


def facets_for(full: bool = False) -> Dict[str, List[str]]:
    """Which facets vision is asked for.

    By default only what the free sources cannot supply: Scryfall Tagger already
    covers subjects, settings and objects for most artwork, and far more reliably
    than a model would, because humans tagged it. Asking again would cost money to
    get a second opinion we did not need.

    `full=True` also asks for subject/setting/object, for artwork Scryfall has
    never tagged.
    """
    wanted = list(vocab.VISION_FACETS) + ["franchise"]
    if full:
        wanted = ["subject", "setting", "object"] + wanted
    out = {}
    for facet in wanted:
        out[facet] = vocab.VISION_ONLY_TAGS.get(facet) or vocab.FACETS[facet]
    return out


def vocabulary_block(facets: Dict[str, List[str]]) -> str:
    return "\n".join("{}: {}".format(f, ", ".join(t)) for f, t in facets.items())


def schema(facets: Dict[str, List[str]]) -> dict:
    properties = {}
    for facet, tags in facets.items():
        properties[facet] = {
            "type": "array",
            "items": {"type": "string", "enum": list(tags)},
        }
    properties["description"] = {"type": "string"}
    properties["other"] = {"type": "array", "items": {"type": "string"}}
    return {
        "type": "object",
        "properties": properties,
        "required": list(facets) + ["description", "other"],
        "additionalProperties": False,
    }


@dataclass
class ArtUnit:
    illustration_id: str
    art_crop_url: str
    face_name: str
    example_card: str


def untagged(conn, limit: int = None, include: List[str] = None) -> List[ArtUnit]:
    """Artwork with no vision tags yet, newest sets first."""
    rows = conn.execute(
        "SELECT f.illustration_id, f.art_crop_url, f.face_name, c.name AS card_name,"
        "       c.released_at"
        "  FROM card_faces f"
        "  JOIN cards c ON c.scryfall_id = f.scryfall_id"
        " WHERE f.illustration_id IS NOT NULL AND f.art_crop_url IS NOT NULL"
        "   AND f.illustration_id NOT IN (SELECT illustration_id FROM art_notes)"
        " GROUP BY f.illustration_id"
        " ORDER BY c.released_at DESC, c.name"
    ).fetchall()
    units = [ArtUnit(r["illustration_id"], r["art_crop_url"], r["face_name"],
                     r["card_name"]) for r in rows]
    if include:
        wanted = set(include)
        units = [u for u in units if u.illustration_id in wanted]
    return units[:limit] if limit else units


def estimate(count: int) -> Dict[str, float]:
    input_tokens = count * (IMAGE_TOKENS + PROMPT_TOKENS)
    output_tokens = count * OUTPUT_TOKENS
    usd = (input_tokens / 1_000_000 * BATCH_INPUT_PER_MTOK
           + output_tokens / 1_000_000 * BATCH_OUTPUT_PER_MTOK)
    return {
        "artworks": count,
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "usd": usd,
        "eur": usd * USD_TO_EUR,
    }


def format_estimate(est: Dict[str, float]) -> str:
    if not est["artworks"]:
        return "Nothing to tag: every artwork in the collection already has tags."
    return (
        "{artworks:,} artwork(s) need tagging with {model}.\n"
        "  input   ~{input_tokens:,} tokens (art crop {img} + prompt {prompt} each)\n"
        "  output  ~{output_tokens:,} tokens\n"
        "  cost    ~${usd:,.2f}  (about EUR {eur:,.2f}) at Batch API rates\n"
        "\nThis is an estimate from measured image sizes, not a quote."
    ).format(model=MODEL, img=IMAGE_TOKENS, prompt=PROMPT_TOKENS, **est)


def _image_block(unit: ArtUnit, embed: bool) -> dict:
    if not embed:
        return {"type": "image", "source": {"type": "url", "url": unit.art_crop_url}}
    request = urllib.request.Request(
        unit.art_crop_url, headers={"User-Agent": "MagicTheBenalliing/0.1"})
    with urllib.request.urlopen(request, timeout=30) as response:
        import base64
        data = base64.b64encode(response.read()).decode("ascii")
    return {"type": "image",
            "source": {"type": "base64", "media_type": "image/jpeg", "data": data}}


def build_request(unit: ArtUnit, embed: bool = False, full: bool = False) -> dict:
    """One batch entry. The vocabulary prefix is cached across the whole batch."""
    facets = facets_for(full)
    return {
        "custom_id": unit.illustration_id,
        "params": {
            "model": MODEL,
            "max_tokens": 1024,
            "system": [{
                "type": "text",
                "text": SYSTEM.format(vocabulary=vocabulary_block(facets)),
                "cache_control": {"type": "ephemeral"},
            }],
            "output_config": {
                "effort": "low",
                "format": {"type": "json_schema", "schema": schema(facets)},
            },
            "messages": [{
                "role": "user",
                "content": [
                    _image_block(unit, embed),
                    {"type": "text", "text": "Label this artwork."},
                ],
            }],
        },
    }


def _client():
    try:
        import anthropic
    except ImportError:
        raise RuntimeError(
            "the `anthropic` package is not installed.\n"
            "  python3 -m venv .venv && .venv/bin/pip install anthropic\n"
            "then run this command with .venv/bin/python.")
    return anthropic.Anthropic()


def submit(conn, units: List[ArtUnit], embed: bool = False,
           full: bool = False) -> str:
    """Send a batch and return its id. The id is also written to build/."""
    client = _client()
    requests = [build_request(unit, embed, full) for unit in units]
    batch = client.messages.batches.create(requests=requests)
    paths.ensure_dirs()
    with open(os.path.join(paths.BUILD, "batch.json"), "w", encoding="utf-8") as fh:
        json.dump({"batch_id": batch.id, "count": len(units), "model": MODEL}, fh)
    return batch.id


def status(batch_id: str) -> str:
    return _client().messages.batches.retrieve(batch_id).processing_status


def collect(conn, batch_id: str = None) -> Dict[str, int]:
    """Fetch finished results, append to art_tags.jsonl, and load into the DB."""
    if batch_id is None:
        marker = os.path.join(paths.BUILD, "batch.json")
        if not os.path.exists(marker):
            raise RuntimeError("no batch id given and none recorded in build/batch.json")
        with open(marker, "r", encoding="utf-8") as fh:
            batch_id = json.load(fh)["batch_id"]

    client = _client()
    counts = {"succeeded": 0, "errored": 0, "other": 0}
    records = []
    for result in client.messages.batches.results(batch_id):
        kind = result.result.type
        if kind != "succeeded":
            counts["errored" if kind == "errored" else "other"] += 1
            continue
        text = "".join(b.text for b in result.result.message.content
                       if getattr(b, "type", "") == "text")
        try:
            payload = json.loads(text)
        except ValueError:
            counts["errored"] += 1
            continue
        payload["illustration_id"] = result.custom_id
        payload["model"] = MODEL
        records.append(payload)
        counts["succeeded"] += 1

    append_records(records)
    load_into_db(conn, records)
    return counts


def append_records(records: List[dict], path: str = None) -> None:
    path = path or paths.ART_TAGS
    paths.ensure_dirs()
    with open(path, "a", encoding="utf-8") as fh:
        for record in records:
            fh.write(json.dumps(record, sort_keys=True, ensure_ascii=False) + "\n")


def read_records(path: str = None) -> List[dict]:
    path = path or paths.ART_TAGS
    if not os.path.exists(path):
        return []
    out = []
    with open(path, "r", encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if line:
                out.append(json.loads(line))
    return out


def load_into_db(conn, records: List[dict]) -> int:
    """Write vision tags. Anything outside the vocabulary is kept aside, not dropped."""
    written = 0
    for record in records:
        illus = record.get("illustration_id")
        if not illus:
            continue
        conn.execute("DELETE FROM art_tags WHERE illustration_id = ? AND source = 'vision'",
                     (illus,))
        for facet in vocab.FACETS:
            for tag in record.get(facet) or []:
                if not vocab.is_valid(facet, tag):
                    record.setdefault("other", []).append("{}={}".format(facet, tag))
                    continue
                conn.execute(
                    "INSERT OR REPLACE INTO art_tags (illustration_id, facet, tag,"
                    " source, confidence) VALUES (?,?,?,'vision',1.0)",
                    (illus, facet, tag))
                written += 1
        conn.execute(
            "INSERT OR REPLACE INTO art_notes (illustration_id, description,"
            " other_tags, model, tagged_at) VALUES (?,?,?,?,datetime('now'))",
            (illus, record.get("description", ""),
             json.dumps(record.get("other") or []), record.get("model", MODEL)))
    conn.commit()
    return written


def resync_from_file(conn) -> int:
    """Rebuild every vision tag from art_tags.jsonl. The file is the durable copy."""
    return load_into_db(conn, read_records())
