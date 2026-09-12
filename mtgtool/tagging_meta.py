"""Tags derivable from Scryfall metadata alone -- zero tokens, zero cost.

Anything a script can work out must never be left to a model. A card whose type
line says "Creature - Cat Wizard" is a cat, and no vision call is needed to learn
that. The same goes for franchise: Scryfall flags Universes Beyond printings and
the set name names the franchise.

What this deliberately does NOT cover is what is actually *painted*: a cat asleep
in the background of a non-Cat card, or whether the picture is cute. That is the
only thing vision is asked for, which is why the vision bill stays small.
"""
from __future__ import annotations

import json
import re
from typing import Dict, List, Tuple

from . import vocab

# MTG creature/land subtype -> our `subject` tag.
SUBTYPE_SUBJECT = {
    # Mammals
    "Cat": "cat", "Dog": "dog", "Hound": "dog", "Wolf": "wolf",
    "Werewolf": "werewolf", "Bear": "bear", "Horse": "horse",
    "Pegasus": "pegasus", "Unicorn": "unicorn", "Deer": "deer", "Elk": "elk",
    "Boar": "boar", "Pig": "pig", "Goat": "goat", "Sheep": "sheep",
    "Ox": "cow", "Cow": "cow", "Rabbit": "rabbit", "Raccoon": "raccoon",
    "Squirrel": "squirrel", "Mouse": "mouse", "Hamster": "mouse", "Rat": "rat",
    "Bat": "bat", "Otter": "otter", "Badger": "badger", "Weasel": "weasel",
    "Ferret": "ferret", "Beaver": "beaver", "Hedgehog": "hedgehog",
    "Monkey": "monkey", "Ape": "ape", "Elephant": "elephant", "Rhino": "rhino",
    "Camel": "camel", "Mole": "mouse",
    # Birds
    "Bird": "bird", "Owl": "owl", "Crow": "crow", "Raven": "crow",
    "Eagle": "eagle", "Hawk": "hawk", "Phoenix": "phoenix", "Griffin": "griffon",
    "Griffon": "griffon",
    # Water and reptiles
    "Fish": "fish", "Shark": "shark", "Whale": "whale", "Octopus": "octopus",
    "Squid": "octopus", "Crab": "crab", "Lobster": "crab", "Turtle": "turtle",
    "Frog": "frog", "Salamander": "lizard", "Lizard": "lizard",
    "Dinosaur": "dinosaur", "Snake": "snake", "Serpent": "serpent",
    "Hydra": "hydra", "Kraken": "kraken", "Leviathan": "kraken",
    "Merfolk": "merfolk",
    # Small things
    "Insect": "insect", "Wasp": "insect", "Bee": "insect", "Moth": "insect",
    "Spider": "spider", "Scorpion": "spider", "Worm": "worm", "Slug": "worm",
    "Snail": "worm",
    # People and folk
    "Human": "human", "Elf": "elf", "Goblin": "goblin", "Orc": "orc",
    "Dwarf": "dwarf", "Gnome": "gnome", "Kobold": "kobold", "Giant": "giant",
    "Troll": "troll", "Ogre": "ogre", "Minotaur": "minotaur",
    "Centaur": "centaur", "Satyr": "satyr", "Faerie": "faerie",
    "Vampire": "vampire",
    # Undead and spirits
    "Zombie": "zombie", "Skeleton": "skeleton", "Spirit": "spirit",
    "Ghost": "ghost", "Wraith": "wraith", "Specter": "wraith",
    "Shade": "wraith", "Mummy": "mummy",
    # Celestial and infernal
    "Angel": "angel", "Demon": "demon", "Devil": "devil", "Imp": "imp",
    "Sphinx": "sphinx",
    # Dragons and big beasts
    "Dragon": "dragon", "Drake": "drake", "Wyvern": "wyvern", "Wurm": "wurm",
    "Beast": "beast",
    # Artificial and alien
    "Construct": "construct", "Golem": "golem", "Robot": "robot",
    "Assembly-Worker": "robot", "Servo": "robot", "Thopter": "robot",
    "Alien": "alien", "Eldrazi": "alien", "Phyrexian": "alien",
    "Elemental": "elemental", "Ooze": "ooze", "Slime": "ooze",
    # Plants
    "Plant": "plant-creature", "Treefolk": "treefolk", "Fungus": "fungus",
    "Saproling": "fungus",
}

# Land subtype -> `setting`. A Swamp is painted as a swamp.
LAND_SETTING = {
    "Forest": "forest", "Swamp": "swamp", "Mountain": "mountain",
    "Island": "ocean", "Plains": "farmland", "Cave": "cave", "Desert": "desert",
}


def subjects_from_type_line(type_line: str) -> List[str]:
    if not type_line or "—" not in type_line:
        return []
    tags = []
    for part in type_line.split("—")[1:]:
        for word in re.split(r"[\s/]+", part.strip()):
            tag = SUBTYPE_SUBJECT.get(word.strip())
            if tag and tag not in tags:
                tags.append(tag)
    return tags


def settings_from_type_line(type_line: str) -> List[str]:
    if not type_line or "Land" not in type_line or "—" not in type_line:
        return []
    tags = []
    for part in type_line.split("—")[1:]:
        for word in re.split(r"[\s/]+", part.strip()):
            tag = LAND_SETTING.get(word.strip())
            if tag and tag not in tags:
                tags.append(tag)
    return tags


def franchise_for(set_name: str, promo_types_json: str) -> str:
    """Franchise tag, or "" when metadata cannot say.

    Returns "" rather than "none" so vision can still find a crossover the set
    name does not advertise -- a Secret Lair, for instance.
    """
    named = vocab.franchise_from_set(set_name or "")
    if named:
        return named
    try:
        promos = json.loads(promo_types_json or "[]")
    except (ValueError, TypeError):
        promos = []
    if "universesbeyond" in promos:
        # Flagged as a crossover but the set name did not tell us which one.
        return ""
    return ""


def apply_metadata_tags(conn) -> int:
    """(Re)derive every metadata tag. Idempotent; never touches vision tags."""
    rows = conn.execute(
        "SELECT f.illustration_id, f.face_type_line, c.set_name, c.promo_types,"
        "       c.type_line AS card_type_line"
        "  FROM card_faces f JOIN cards c ON c.scryfall_id = f.scryfall_id"
        " WHERE f.illustration_id IS NOT NULL"
    ).fetchall()

    wanted: Dict[Tuple[str, str, str], None] = {}
    for row in rows:
        illus = row["illustration_id"]
        type_line = row["face_type_line"] or row["card_type_line"] or ""
        for tag in subjects_from_type_line(type_line):
            wanted[(illus, "subject", tag)] = None
        for tag in settings_from_type_line(type_line):
            wanted[(illus, "setting", tag)] = None
        franchise = franchise_for(row["set_name"], row["promo_types"])
        if franchise:
            wanted[(illus, "franchise", franchise)] = None

    conn.execute("DELETE FROM art_tags WHERE source = 'metadata'")
    for (illus, facet, tag) in wanted:
        # A vision tag already saying the same thing wins; both agreeing is fine.
        conn.execute(
            "INSERT OR IGNORE INTO art_tags (illustration_id, facet, tag, source, confidence)"
            " VALUES (?,?,?,'metadata',1.0)", (illus, facet, tag))
    return len(wanted)
