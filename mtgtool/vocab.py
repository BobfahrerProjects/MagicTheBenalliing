"""The closed tag vocabulary.

Why closed: free-text tagging reliably produces cat / cats / feline / kitty for the
same picture, and then a search for "cat" silently misses three quarters of them.
Every vision tag must come from these lists; anything the model considers important
but absent goes into `other[]`, which is surfaced for review and only then promoted
into the vocabulary here.
"""
from __future__ import annotations

from typing import Dict, List

FACETS: Dict[str, List[str]] = {
    "subject": [
        "cat", "dog", "bird", "rat", "frog", "bat", "squirrel", "fish", "insect",
        "spider", "snake", "horse", "bear", "wolf", "octopus", "dragon", "human",
        "elf", "goblin", "zombie", "skeleton", "angel", "demon", "robot", "alien",
        "plant-creature", "no-creature",
    ],
    "mood": [
        "cute", "wholesome", "cozy", "funny", "epic", "heroic", "grim", "horror",
        "serene", "chaotic", "melancholy", "romantic", "menacing",
    ],
    "setting": [
        "forest", "swamp", "mountain", "ocean", "desert", "city", "village",
        "castle", "cave", "library", "temple", "battlefield", "space", "void",
        "interior", "farmland", "snow", "ruins",
    ],
    "style": [
        "painterly", "anime", "cartoon", "photorealistic", "ink-sketch", "woodcut",
        "pixel-art", "retro", "abstract", "comic", "storybook",
    ],
    "composition": [
        "portrait", "close-up", "wide-landscape", "action", "still-life", "crowd",
        "single-figure", "silhouette",
    ],
    "palette": [
        "warm", "cool", "monochrome", "high-contrast", "pastel", "neon", "earthy",
        "dark", "bright",
    ],
    "motif": [
        "food", "music", "books", "weather", "fire", "water", "blood", "bones",
        "machinery", "flowers", "treasure", "moon-stars", "magic-glow",
    ],
    "franchise": [
        "warhammer-40k", "lord-of-the-rings", "fallout", "final-fantasy", "marvel",
        "doctor-who", "stardew-valley", "assassins-creed", "transformers",
        "street-fighter", "godzilla", "jurassic-park", "none",
    ],
}

VISION_FACETS = [f for f in FACETS if f != "franchise"]

ALL_TAGS = {tag for tags in FACETS.values() for tag in tags}


def is_valid(facet: str, tag: str) -> bool:
    return tag in FACETS.get(facet, ())


# Set name (or fragment) -> franchise tag. Applied at import for zero tokens.
# Scryfall's promo_types carries "universesbeyond" for most of these; this map turns
# that flag into a specific franchise, which the flag alone cannot tell us.
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
