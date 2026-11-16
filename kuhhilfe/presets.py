"""
presets.py – generische Standard-Vorlage für KuhHilfe

Ein serverunabhängiger Satz an Gruppen/Kategorien, der auf praktisch jeden
Community-Server passt. Wird NICHT automatisch beim Laden angelegt, sondern kann
bei Bedarf hinzugefügt werden – über ``/kuhhilfe-setup vorlagen`` oder den
Button **📥 Vorlage** im Verwaltungs-Menü.

``apply_presets`` ist idempotent: bereits vorhandene Keys werden übersprungen,
sodass man die Vorlage gefahrlos (auch mehrfach, auch ergänzend) laden kann.
"""

from typing import Any, Dict, List

from .constants import CATEGORY_PATH_SEP


def _f(label: str, style: str = "short", required: bool = True,
       placeholder: str = None, max_length: int = 300) -> Dict[str, Any]:
    return {"label": label, "style": style, "required": required,
            "placeholder": placeholder, "max_length": max_length}


def _game_fields(char_placeholder: str, loc_label: str, loc_placeholder: str) -> List[Dict[str, Any]]:
    """Einheitliche Felder für eine Spiel-Unterkategorie (max. 5 Felder)."""
    return [
        _f("Betreff", placeholder="Worum geht es?", max_length=100),
        _f("Charaktername", required=False, placeholder=char_placeholder, max_length=100),
        _f(loc_label, required=False, placeholder=loc_placeholder, max_length=100),
        _f("Dein Anliegen", style="paragraph",
           placeholder="Beschreibe deine Frage / dein Problem so genau wie möglich.", max_length=1000),
    ]


# Reihenfolge = Anzeige-Reihenfolge im Panel/Wizard
DEFAULT_PRESETS: List[Dict[str, Any]] = [
    {
        "type": "leaf", "name": "Allgemeine Frage", "emoji": "❓",
        "description": "Fragen rund um den Server, die in keine andere Kategorie passen.",
        "fields": [
            _f("Betreff", placeholder="Worum geht es?", max_length=100),
            _f("Deine Frage", style="paragraph",
               placeholder="Beschreibe deine Frage so genau wie möglich …", max_length=1000),
        ],
    },
    {
        "type": "leaf", "name": "Technisches Problem", "emoji": "🛠️",
        "description": "Probleme mit Discord, Sprachkanälen, Benachrichtigungen oder Bots.",
        "fields": [
            _f("Betreff", placeholder="Kurzbeschreibung des Problems", max_length=100),
            _f("Was funktioniert nicht?", style="paragraph",
               placeholder="Was passiert, was sollte passieren?", max_length=1000),
            _f("Schon versucht / Fehlermeldung", style="paragraph", required=False,
               placeholder="Optional: was du schon probiert hast, Fehlermeldungen …", max_length=1000),
        ],
    },
    {
        "type": "leaf", "name": "Mitglied melden", "emoji": "🚩",
        "description": "Melde Regelverstöße oder Probleme mit anderen Mitgliedern (vertraulich).",
        "fields": [
            _f("Wen meldest du? (Name / ID)", placeholder="z. B. @Name oder die User-ID", max_length=100),
            _f("Was ist passiert?", style="paragraph",
               placeholder="Schildere den Vorfall möglichst sachlich.", max_length=1000),
            _f("Beweise / Links", style="paragraph", required=False,
               placeholder="Optional: Links zu Nachrichten, Screenshots …", max_length=500),
        ],
    },
    {
        "type": "leaf", "name": "Vorschlag & Feedback", "emoji": "💡",
        "description": "Ideen, Wünsche oder Feedback für den Server.",
        "fields": [
            _f("Betreff", placeholder="Dein Vorschlag in einem Satz", max_length=100),
            _f("Details", style="paragraph",
               placeholder="Beschreibe deine Idee und warum sie den Server verbessert.", max_length=1000),
        ],
    },
    {
        "type": "leaf", "name": "Team-Bewerbung", "emoji": "🤝",
        "description": "Bewirb dich für eine Rolle im Team (z. B. Moderation / Support).",
        "fields": [
            _f("Für welche Position?", placeholder="z. B. Moderation", max_length=100),
            _f("Über dich", style="paragraph",
               placeholder="Alter, Zeitzone, verfügbare Zeit, Erfahrung …", max_length=1000),
            _f("Warum du?", style="paragraph",
               placeholder="Warum sollten wir dich ins Team holen?", max_length=1000),
        ],
    },
    {
        "type": "group", "name": "Fragen zu einem Spiel", "emoji": "🎮",
        "description": "Spielbezogene Anliegen - wähle anschließend das passende Spiel.",
        "children": [
            {
                "name": "WoW", "emoji": "⚔️",
                "description": "Fragen & Hilfe zu World of Warcraft.",
                "fields": _game_fields("Dein Charakter in WoW", "Realm & Region",
                                        "z. B. Blackrock (EU)"),
            },
            {
                "name": "Aion 2", "emoji": "🌀",
                "description": "Fragen & Hilfe zu Aion 2.",
                "fields": _game_fields("Dein Charakter in Aion 2", "Server & Region",
                                        "z. B. Server (EU)"),
            },
            {
                "name": "FFXIV", "emoji": "🐉",
                "description": "Fragen & Hilfe zu Final Fantasy XIV.",
                "fields": _game_fields("Vor- & Nachname deines Charakters", "Welt & Datacenter",
                                        "z. B. Twintania (Light)"),
            },
            {
                "name": "Ragnarok X", "emoji": "🏹",
                "description": "Fragen & Hilfe zu Ragnarok X: Next Generation.",
                "fields": _game_fields("Dein Charakter in Ragnarok X", "Server",
                                        "z. B. dein Server"),
            },
            {
                "name": "Ragnarok Zero", "emoji": "🗡️",
                "description": "Fragen & Hilfe zu Ragnarok Zero.",
                "fields": _game_fields("Dein Charakter in Ragnarok Zero", "Server",
                                        "z. B. dein Server"),
            },
            {
                "name": "Allgemein", "emoji": "🕹️",
                "description": "Allgemeine Spielfragen oder ein hier nicht gelistetes Spiel.",
                "fields": [
                    _f("Um welches Spiel geht es?", placeholder="Name des Spiels", max_length=100),
                    _f("Dein Anliegen", style="paragraph",
                       placeholder="Beschreibe deine Frage / dein Problem.", max_length=1000),
                ],
            },
        ],
    },
]


async def apply_presets(store, guild) -> Dict[str, int]:
    """Legt die Vorlage an (vorhandene Keys werden übersprungen). Gibt eine Statistik zurück."""
    added_groups = 0
    added_categories = 0
    skipped = 0

    for preset in DEFAULT_PRESETS:
        if preset["type"] == "leaf":
            key = store.normalize_key(preset["name"])
            if await store.get_node(guild, key) is not None:
                skipped += 1
                continue
            leaf, path = await store.add_leaf(
                guild, preset["name"], None, preset.get("emoji"), preset.get("description"))
            if leaf is None:
                skipped += 1
                continue
            added_categories += 1
            if preset.get("fields"):
                await store.set_fields(guild, path, preset["fields"])
            continue

        # Gruppe
        gkey = store.normalize_key(preset["name"])
        grp = await store.get_node(guild, gkey)
        if grp is None:
            grp = await store.add_group(
                guild, preset["name"], preset.get("emoji"), preset.get("description"))
            if grp is not None:
                added_groups += 1
            else:
                skipped += 1
                continue

        for child in preset.get("children", []):
            ckey = store.normalize_key(child["name"])
            child_path = f"{gkey}{CATEGORY_PATH_SEP}{ckey}"
            if await store.get_node(guild, child_path) is not None:
                skipped += 1
                continue
            leaf, path = await store.add_leaf(
                guild, child["name"], gkey, child.get("emoji"), child.get("description"))
            if leaf is None:
                skipped += 1
                continue
            added_categories += 1
            if child.get("fields"):
                await store.set_fields(guild, path, child["fields"])

    return {"groups": added_groups, "categories": added_categories, "skipped": skipped}
