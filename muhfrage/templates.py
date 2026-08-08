"""
templates.py – Vorgefertigte Umfrage-Vorlagen

Eine Vorlage erzeugt eine Umfrage mit genau einer vorbereiteten Frage. Der Manager
muss danach nur noch die Kandidaten (Optionen) und ggf. den Titel ergänzen.
"""

from typing import Any, Dict, List

from . import models
from .constants import DEFAULT_RANK_VALUES

# key → Metadaten + Fragen-Vorlage
TEMPLATES: Dict[str, Dict[str, Any]] = {
    "plus_minus": {
        "name":      "Spieler-Bewertung (Plus/Minus)",
        "emoji":     "➕",
        "title":     "Spieler-Bewertung",
        "qtype":     "plus_minus",
        "question":  "Wem gibst du einen Pluspunkt und wem einen Minuspunkt?",
        "config":    {"plus_count": 1, "minus_count": 1, "value": 1},
    },
    "points_pool": {
        "name":      "Freie Punktverteilung",
        "emoji":     "🎯",
        "title":     "Punktevergabe",
        "qtype":     "points_pool",
        "question":  "Verteile deine Punkte frei auf die Kandidaten.",
        "config":    {"points_total": 3, "max_per_option": None},
    },
    "ranked": {
        "name":      "Rangliste (5-4-3-2-1)",
        "emoji":     "🏆",
        "title":     "Rangliste",
        "qtype":     "ranked",
        "question":  "Bringe die Kandidaten in deine Reihenfolge (Platz 1 zuerst).",
        "config":    {"rank_values": list(DEFAULT_RANK_VALUES)},
    },
    "poll": {
        "name":      "Einfache Abstimmung",
        "emoji":     "🔘",
        "title":     "Abstimmung",
        "qtype":     "single_choice",
        "question":  "Wofür stimmst du ab?",
        "config":    {},
    },
}


def template_choices() -> List[Dict[str, str]]:
    """Liste für Slash-Choices / Selects."""
    return [
        {"key": key, "name": tpl["name"], "emoji": tpl["emoji"]}
        for key, tpl in TEMPLATES.items()
    ]


def apply_template(survey: Dict[str, Any], key: str) -> bool:
    """
    Fügt der Umfrage die vorbereitete Frage der Vorlage hinzu.
    Gibt False zurück, wenn die Vorlage unbekannt ist.
    """
    tpl = TEMPLATES.get(key)
    if not tpl:
        return False
    if not survey.get("title"):
        survey["title"] = tpl["title"]
    question = models.new_question(
        qtype=tpl["qtype"],
        text=tpl["question"],
        options=[],
        config=dict(tpl["config"]),
    )
    survey["questions"].append(question)
    return True
