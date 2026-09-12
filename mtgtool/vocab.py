"""The tag vocabulary, and where each tag comes from.

Three sources, cheapest first. A tag is only asked of a model when nothing else
can answer it:

1. **Card metadata** (free) -- creature types from the type line, and the
   Universes Beyond franchise from the set.
2. **Scryfall Tagger** (free) -- a community project that has tagged what is
   actually *painted* on tens of thousands of cards. This is the source that
   finds the cat on Cyclonic Rift and on Lord Windgrace: neither card's type line
   mentions a cat, so metadata alone misses them entirely. `art:` searches match
   tag names exactly -- verified: `cat` matches, `ca`/`cats`/`catt` do not -- so
   there are no substring false positives.
3. **Vision** (paid) -- only the facets nobody has tagged for us: mood, palette,
   most art styles, and a one-line description of the scene.

The vocabulary is closed. Free-text tagging reliably produces cat/cats/feline/
kitty for the same picture, and then a search for "cat" silently misses most of
them.
"""
from __future__ import annotations

from typing import Dict, List, Tuple

# ---------------------------------------------------------------------------
# The vocabulary, by facet.
# ---------------------------------------------------------------------------

FACETS: Dict[str, List[str]] = {
    # Living beings visible in the art.
    "subject": [
        "cat", "dog", "wolf", "bear", "horse", "deer", "elk", "boar", "pig",
        "goat", "sheep", "cow", "rabbit", "raccoon", "squirrel", "mouse", "rat",
        "bat", "otter", "badger", "hedgehog", "ferret", "weasel", "beaver",
        "monkey", "ape", "gorilla", "elephant", "rhino", "camel", "llama",
        "bird", "owl", "crow", "eagle", "hawk", "duck", "chicken", "penguin",
        "fish", "shark", "whale", "octopus", "crab", "turtle", "frog", "snake",
        "serpent", "lizard", "dinosaur", "insect", "spider", "worm",
        "human", "elf", "goblin", "orc", "dwarf", "gnome", "kobold", "giant",
        "troll", "ogre", "minotaur", "centaur", "satyr", "faerie", "merfolk",
        "angel", "demon", "devil", "imp", "zombie", "skeleton", "ghost",
        "spirit", "wraith", "mummy", "vampire", "werewolf",
        "dragon", "drake", "wyvern", "hydra", "kraken", "wurm", "phoenix",
        "griffon", "sphinx", "unicorn", "pegasus", "beast",
        "robot", "golem", "construct", "alien", "elemental", "ooze",
        "plant-creature", "treefolk", "fungus", "no-creature",
    ],
    # Where the scene takes place.
    "setting": [
        "forest", "swamp", "mountain", "ocean", "sea", "river", "lake",
        "waterfall", "beach", "island", "desert", "snow", "volcano", "cliff",
        "valley", "meadow", "field", "farmland", "garden", "cave", "city",
        "village", "castle", "tower", "bridge", "road", "wall", "gate",
        "library", "temple", "church", "cathedral", "monastery", "laboratory",
        "forge", "tavern", "market", "harbor", "graveyard", "tomb",
        "battlefield", "ruins", "space", "sky", "interior", "void",
    ],
    # Notable things depicted, including elements and weather.
    "object": [
        "sword", "axe", "hammer", "bow", "arrow", "shield", "armor", "helmet",
        "crown", "ring", "amulet", "staff", "wand", "orb", "crystal", "gem",
        "gold", "coin", "chest", "throne", "statue", "altar", "door", "key",
        "lock", "mirror", "clock", "map", "book", "scroll", "letter", "quill",
        "candle", "lantern", "torch", "potion", "bottle", "cauldron",
        "food", "bread", "fruit", "apple", "mushroom", "flower", "rose",
        "tree", "leaf", "vine", "wheat", "ship", "boat", "music",
        "skull", "bone", "blood", "grave",
        "fire", "ice", "water", "smoke", "explosion", "lightning", "storm",
        "rain", "fog", "cloud", "sun", "moon", "star", "night", "sunset",
        "machinery", "magic-glow",
    ],
    # How it feels. Mostly vision's job.
    "mood": [
        "cute", "cozy", "wholesome", "funny", "epic", "heroic", "serene",
        "melancholy", "sad", "romantic", "grim", "menacing", "creepy", "horror",
        "gore", "chaotic", "surreal",
    ],
    "style": [
        "painterly", "anime", "chibi", "cartoon", "comic", "photorealistic",
        "ink-sketch", "lineart", "sketch", "watercolor", "woodcut", "pixel-art",
        "retro", "abstract", "psychedelic", "storybook",
    ],
    "composition": [
        "portrait", "close-up", "wide-landscape", "action", "still-life",
        "crowd", "single-figure", "silhouette", "flying", "sleeping", "eating",
        "reading", "dance",
    ],
    # Vision only -- nobody tags colour for us.
    "palette": [
        "warm", "cool", "monochrome", "high-contrast", "pastel", "neon",
        "earthy", "dark", "bright", "sepia",
    ],
    # Free from set metadata.
    "franchise": [
        "warhammer-40k", "lord-of-the-rings", "fallout", "final-fantasy",
        "marvel", "doctor-who", "stardew-valley", "assassins-creed",
        "transformers", "street-fighter", "godzilla", "jurassic-park",
        "avatar-last-airbender", "spongebob", "none",
    ],
}

ALL_TAGS = {tag for tags in FACETS.values() for tag in tags}


def is_valid(facet: str, tag: str) -> bool:
    return tag in FACETS.get(facet, ())


# ---------------------------------------------------------------------------
# Scryfall Tagger -> our vocabulary.
#
# Only tags whose meaning was verified against real search results are mapped.
# Deliberately excluded as ambiguous: `mount`/`riding` (returns Abyssal Specter,
# which is not ridden), `symmetry`, `day`, `smile`, `avatar`, `landscape`.
# Wrong tags are worse than missing ones -- a search you cannot trust is a search
# you stop using.
# ---------------------------------------------------------------------------

def _m(facet: str, *tags: str) -> Dict[str, Tuple[str, str]]:
    return {tag: (facet, tag) for tag in tags}


SCRYFALL_MAP: Dict[str, Tuple[str, str]] = {}

SCRYFALL_MAP.update(_m(
    "subject",
    "cat", "dog", "wolf", "bear", "horse", "deer", "elk", "boar", "pig", "goat",
    "sheep", "cow", "rabbit", "raccoon", "squirrel", "mouse", "rat", "bat",
    "otter", "badger", "hedgehog", "ferret", "weasel", "beaver", "monkey", "ape",
    "gorilla", "elephant", "rhino", "camel", "llama", "bird", "owl", "crow",
    "eagle", "hawk", "duck", "chicken", "penguin", "fish", "shark", "whale",
    "octopus", "crab", "turtle", "frog", "snake", "serpent", "lizard",
    "dinosaur", "insect", "spider", "worm", "human", "elf", "goblin", "orc",
    "dwarf", "gnome", "kobold", "giant", "troll", "ogre", "minotaur", "centaur",
    "satyr", "faerie", "angel", "demon", "devil", "imp", "zombie", "skeleton",
    "ghost", "spirit", "wraith", "mummy", "dragon", "drake", "wyvern", "hydra",
    "kraken", "wurm", "phoenix", "griffon", "sphinx", "unicorn", "pegasus",
    "beast", "robot", "golem", "construct", "alien", "elemental", "ooze",
    "plant-creature", "treefolk", "fungus",
))

SCRYFALL_MAP.update(_m(
    "setting",
    "forest", "swamp", "mountain", "ocean", "sea", "river", "lake", "waterfall",
    "beach", "island", "desert", "snow", "volcano", "cliff", "valley", "meadow",
    "field", "garden", "cave", "city", "village", "castle", "tower", "bridge",
    "road", "wall", "gate", "library", "temple", "church", "cathedral",
    "monastery", "laboratory", "forge", "tavern", "market", "harbor",
    "graveyard", "tomb", "battlefield", "ruins", "space", "sky",
))

SCRYFALL_MAP.update(_m(
    "object",
    "sword", "axe", "hammer", "bow", "arrow", "shield", "armor", "helmet",
    "crown", "ring", "amulet", "staff", "wand", "orb", "crystal", "gem", "gold",
    "coin", "chest", "throne", "statue", "altar", "door", "key", "lock",
    "mirror", "clock", "map", "book", "scroll", "letter", "quill", "candle",
    "lantern", "torch", "potion", "bottle", "cauldron", "food", "bread", "fruit",
    "apple", "mushroom", "flower", "rose", "tree", "leaf", "vine", "wheat",
    "ship", "boat", "music", "skull", "bone", "blood", "grave", "fire", "ice",
    "water", "smoke", "explosion", "lightning", "storm", "rain", "fog", "cloud",
    "sun", "moon", "star", "night", "sunset",
))

SCRYFALL_MAP.update(_m(
    "mood", "cute", "cozy", "creepy", "horror", "gore", "sad", "surreal",
))
SCRYFALL_MAP.update(_m(
    "style", "anime", "chibi", "cartoon", "watercolor", "sketch", "lineart",
    "pixel-art", "abstract", "psychedelic",
))
SCRYFALL_MAP.update(_m(
    "composition", "portrait", "close-up", "crowd", "silhouette", "still-life",
    "flying", "sleeping", "eating", "reading", "dance",
))

# Scryfall spells these differently from us.
SCRYFALL_ALIASES: Dict[str, Tuple[str, str]] = {
    "flame": ("object", "fire"),
    "mist": ("object", "fog"),
    "sunrise": ("object", "sunset"),
    "path": ("setting", "road"),
    "hare": ("subject", "rabbit"),
    "bull": ("subject", "cow"),
    "ox": ("subject", "cow"),
    "raven": ("subject", "crow"),
    "mole": ("subject", "mouse"),
}
SCRYFALL_MAP.update(SCRYFALL_ALIASES)

# ---------------------------------------------------------------------------
# What vision is asked for: only what the free sources cannot supply.
# Keeping this list short keeps the prompt small and the bill low.
# ---------------------------------------------------------------------------

VISION_FACETS = ["mood", "style", "palette", "composition"]

VISION_ONLY_TAGS: Dict[str, List[str]] = {
    "mood": [t for t in FACETS["mood"]],
    "style": [t for t in FACETS["style"]],
    "palette": [t for t in FACETS["palette"]],
    "composition": ["wide-landscape", "action", "single-figure", "portrait",
                    "close-up", "crowd", "silhouette", "still-life"],
}

# ---------------------------------------------------------------------------
# Franchise, from set metadata. Free.
# ---------------------------------------------------------------------------

SET_FRANCHISE = {
    "warhammer 40,000": "warhammer-40k",
    "fallout": "fallout",
    "final fantasy": "final-fantasy",
    "marvel": "marvel",
    "spider-man": "marvel",
    "doctor who": "doctor-who",
    "the lord of the rings": "lord-of-the-rings",
    "tales of middle-earth": "lord-of-the-rings",
    "assassin's creed": "assassins-creed",
    "transformers": "transformers",
    "street fighter": "street-fighter",
    "godzilla": "godzilla",
    "jurassic": "jurassic-park",
    "stardew valley": "stardew-valley",
    "avatar: the last airbender": "avatar-last-airbender",
    "spongebob": "spongebob",
}


def franchise_from_set(set_name: str) -> str:
    """Franchise from a set name, or "" when this set tells us nothing.

    Returns "" rather than "none" so callers can distinguish "set says nothing,
    vision may still find something" from "confirmed not a crossover".
    """
    low = (set_name or "").lower()
    for fragment, tag in SET_FRANCHISE.items():
        if fragment in low:
            return tag
    return ""
