# MagicTheBenalliing

A local, offline-first manager for a Magic: The Gathering collection scanned with
**ManaBox**, built for deck building.

Export your whole collection from ManaBox, drop it in, and the tool works out what
changed. Everything it stores about a card — art tags, deck plans — survives every
re-import, because nothing is keyed on a row.

```bash
./mtg import ~/Downloads/ManaBox_Collection261015.csv
./mtg value
./mtg find --tag cute --tag cat --available
./mtg gallery && open build/gallery.html
```

Python 3.9+, standard library only. The one exception is art tagging, which needs
the `anthropic` package and is the only step that costs money.

---

## The rules it enforces

**Proxies never count toward collection value.** In the current export the proxies
are worth more than the real cards (€4,421 against €1,963), so forgetting them
would overstate the collection by a factor of three — and still look plausible. A
proxy is detected from the card itself (condition `poor`, misprint `true`, price 0),
not from which binder it sits in, so a proxy stays a proxy once it is built into a
deck. If those markers ever disagree, the import **stops and asks** instead of
guessing.

**Lists are wishlists.** ManaBox `list` binders are imported but excluded from
every count, value and availability figure. You do not own those cards.

**A card is never identified by name.** 69 names in this collection map to more
than one printing — `Sol Ring` is four different objects, `Forest` is twenty-five.
Every card is keyed by its Scryfall ID, and asking for an ambiguous name is an
error that lists the alternatives rather than picking one:

```
$ ./mtg deck add cat-tribal "Sol Ring"
error: 'Sol Ring' matches 3 printings you own -- refusing to guess:
  Sol Ring (40K) 252    Lucas Terryn    [1 real]          in: Necron Dynasties
  Sol Ring (BLC) 129    Volkan Baga     [1 real]          in: Hazel
  Sol Ring (SOC) 128    Jorge Jacinto   [1 real, 6 proxy] in: Proxies, Rootha
Say which one by its set and collector number, e.g. "Sol Ring (40K) 252".
```

**One physical card, one deck.** A deck asks for cards at the *oracle* level (a Sol
Ring is a Sol Ring) and each request is filled by a specific printing. If more
decks claim a card than you own copies, the slot becomes `contested` and you are
offered the three real options — move it, proxy it, or buy another. Nothing is
resolved behind your back.

---

## How the delta works

You never export a subset. Drop in the full export and the tool diffs it against
the previous snapshot, classifying each change as `added`, `removed`, `qty_up`,
`qty_down` or **`moved`**.

Moves matter most: a card leaving `Pool - Old` and appearing in deck `Hazel` is one
event — you built it into a deck — not an addition plus a removal. Without that
pairing, every deck-building session would read as though the collection churned.

Where the export genuinely cannot say what happened (no binder decreased, so
nothing demonstrably moved), it reports acquisition rather than inventing a move.

---

## Art tagging

Metadata gives a lot away for free, at import, with no model involved: creature
types (a "Creature — Cat Wizard" is a cat), artist, set, colour identity, rarity,
and the Universes Beyond franchise. That is 695 tags in this collection for €0.

Vision is asked only for what metadata cannot know — what is actually painted, and
how it feels:

| Facet | Examples |
|---|---|
| `subject` | cat, dog, frog, squirrel, dragon, robot, angel, no-creature |
| `mood` | cute, wholesome, cozy, funny, epic, grim, horror, serene |
| `setting` | forest, swamp, city, castle, library, battlefield, space |
| `style` | painterly, anime, cartoon, ink-sketch, woodcut, pixel-art, storybook |
| `composition` | portrait, close-up, wide-landscape, action, crowd, silhouette |
| `palette` | warm, cool, monochrome, pastel, neon, earthy, dark, bright |
| `motif` | food, music, books, fire, water, bones, flowers, treasure, moon-stars |
| `franchise` | warhammer-40k, lord-of-the-rings, stardew-valley, fallout, marvel |

Plus a one-line description per artwork ("a tabby cat in a wizard hat asleep on a
stack of spellbooks"), which is what makes free-text search work later without
re-running vision.

The vocabulary is **closed** on purpose. Free-text tagging reliably yields
cat/cats/feline/kitty for the same picture, and then searching for "cat" silently
misses most of them. Anything the model thinks is important but missing goes into
`other` for review.

**Cost.** The unit of work is an *artwork*, not a row: 2,152 rows collapse to 1,775
distinct illustrations, and reprints inherit the tags of the art they share. The
image sent is Scryfall's `art_crop` — the painting alone, 626×457, ~381 image
tokens, no frame or rules text. Via the Batch API with Claude Opus 5 that is about
**€4.60 once** for the whole collection, and pennies for each new batch of cards.

```bash
./mtg tag --dry-run              # how many artworks, and what it will cost
python3 -m venv .venv && .venv/bin/pip install anthropic
.venv/bin/python mtg tag --submit --limit 20   # try 20 first and look at them
.venv/bin/python mtg tag --collect
```

Always run `--limit 20` first and read the results before spending the full batch.

---

## Gallery and deck workbench

`./mtg gallery` writes one self-contained HTML file. It shows the real artwork in a
filterable grid — click any tag, colour, or type — and marks which cards are already
claimed by a deck.

The workbench lets you pick a commander's colours, filter by art tag, and build a
deck while it warns live about contested cards and draws the mana curve. A browser
tab cannot write to disk, so finishing hands you a deck JSON to drop into
`data/decks/` plus the exact `mtg deck add` commands. Nothing changes your files on
its own.

Art loads from Scryfall's CDN; `--offline-art` downloads it into `build/art/`.

---

## Where truth lives

**ManaBox is authoritative about where cards physically are.** This repo holds deck
*plans*. After each import, `./mtg check` reports drift — "you planned Rhystic Study
into Temur Lorespinner, ManaBox says it's in Rootha" — and never resolves it for you.

The database is a **derived cache**. Everything is rebuildable from text:

```
data/snapshots/ManaBox_YYYYMMDD.csv   immutable exports, the raw history
data/decks/<deck>.json                deck plans and notes
data/art_tags.jsonl                   one line per artwork
data/overrides.json                   manual proxy verdicts
collection.db                         DERIVED -- `./mtg rebuild` recreates it
```

`./mtg rebuild` is verified to reproduce a byte-identical database, which is what
makes that claim true rather than aspirational.

---

## Commands

| Command | What it does | Cost |
|---|---|---|
| `mtg import <csv>` | stage a snapshot, diff it, enrich from Scryfall | free |
| `mtg value` | collection worth, proxies and wishlists excluded | free |
| `mtg find [text]` | search by art tag, colour, type, availability | free |
| `mtg tag --dry-run` | count artworks needing tags and estimate cost | free |
| `mtg tag --submit / --collect` | run the vision batch | ~€4.60 once |
| `mtg deck seed` | create deck plans from the decks already in ManaBox | free |
| `mtg deck list / show / add / remove` | build decks | free |
| `mtg deck conflicts` | cards claimed by more decks than you own | free |
| `mtg deck export <id>` | ManaBox-importable CSV and a plain decklist | free |
| `mtg gallery` | build the HTML gallery and workbench | free |
| `mtg check` | every invariant, plus plan-vs-physical drift | free |
| `mtg rebuild` | recreate the database from the text files | free |
| `mtg doctor` | diagnose known dead ends and say how to fix them | free |

---

## Checks that run on every command

Anything a script can verify is never left to a human reading a diff.
`./mtg check` confirms: lot aggregation loses no cards; the proxy markers still
agree; wishlists reach no total; every deck file loads; every stored deck name still
matches its Scryfall ID; every planned card exists in the collection; no card is
claimed twice; every art tag points at real artwork; and every tag is in the
vocabulary.

Run the test suite with:

```bash
python3 -m unittest discover -s tests
```

## Not yet verified

`mtg deck export` writes ManaBox's exact 18-column format and our own parser reads
it back, but **it has not been imported into ManaBox itself**. Test it with a
two-card deck before trusting a real one — a format is a contract with another
program, and only that program can confirm it.
