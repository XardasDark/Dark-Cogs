"""
constants.py – Statische Konstanten für den Muhfrage-Cog

Enthält:
  - Frage-Typen (Abstimmungs-Modi) inkl. Metadaten
  - Sichtbarkeits-/Timing-Optionen
  - Embed-Farben
  - Standardwerte
"""

from typing import Dict, List

# ─────────────────────────────────────────────────────────────────────────────
# FRAGE-TYPEN  (Abstimmungs-Modi)
# key → Anzeigename, Emoji, Kurzbeschreibung
# ─────────────────────────────────────────────────────────────────────────────
QUESTION_TYPES: Dict[str, Dict[str, str]] = {
    "points_pool": {
        "name":  "Freie Punktverteilung",
        "emoji": "🎯",
        "desc":  "Eine feste Punktzahl frei auf die Kandidaten verteilen.",
    },
    "plus_minus": {
        "name":  "Plus / Minus",
        "emoji": "➕",
        "desc":  "Einem Kandidaten Plus-, einem anderen Minuspunkte geben.",
    },
    "ranked": {
        "name":  "Rang-Vergabe",
        "emoji": "🏆",
        "desc":  "Die Top-Kandidaten mit absteigenden Punkten platzieren (5-4-3-2-1).",
    },
    "single_choice": {
        "name":  "Einzelauswahl",
        "emoji": "🔘",
        "desc":  "Genau eine Option wählen.",
    },
    "multiple_choice": {
        "name":  "Mehrfachauswahl",
        "emoji": "☑️",
        "desc":  "Mehrere Optionen wählen (bis zu einem Limit).",
    },
    "scale": {
        "name":  "Skala-Bewertung",
        "emoji": "⭐",
        "desc":  "Jeden Kandidaten auf einer Skala bewerten (z.B. 1–5).",
    },
    "text": {
        "name":  "Freie Textantwort",
        "emoji": "✍️",
        "desc":  "Eine freie Textantwort eingeben.",
    },
}

# Typen, die eine Optionsliste (Kandidaten) brauchen
OPTION_BASED_TYPES = {
    "points_pool", "plus_minus", "ranked",
    "single_choice", "multiple_choice", "scale",
}

# ─────────────────────────────────────────────────────────────────────────────
# SICHTBARKEIT & TIMING
# ─────────────────────────────────────────────────────────────────────────────
VISIBILITY_OPTIONS: Dict[str, str] = {
    "manager": "Nur für die Manager-Rolle",
    "public":  "Öffentlich in einem Kanal",
}

TIMING_OPTIONS: Dict[str, str] = {
    "on_close": "Erst nach Beenden der Umfrage",
    "live":     "Schon während die Umfrage läuft",
}

STATUS_LABELS: Dict[str, str] = {
    "draft":  "📝 Entwurf",
    "open":   "🟢 Läuft",
    "closed": "🔴 Beendet",
}

# ─────────────────────────────────────────────────────────────────────────────
# EMBED-FARBEN (hex)
# ─────────────────────────────────────────────────────────────────────────────
COLOR_DRAFT  = 0x9E9E9E   # Grau  – Entwurf
COLOR_OPEN   = 0x00C853   # Grün  – läuft
COLOR_CLOSED = 0xD50000   # Rot   – beendet
COLOR_INFO   = 0x2979FF   # Blau  – Info/Ergebnisse
COLOR_ERROR  = 0xFF5252   # Rot   – Fehler

# ─────────────────────────────────────────────────────────────────────────────
# STANDARD-WERTE
# ─────────────────────────────────────────────────────────────────────────────
SLUG_LENGTH        = 4                    # Länge der zufälligen Umfrage-ID
SLUG_ALPHABET      = "abcdefghjkmnpqrstuvwxyz23456789"  # ohne verwechselbare Zeichen
MAX_OPTIONS        = 25                   # Discord Select-Limit pro Seite
DEFAULT_RANK_VALUES: List[int] = [5, 4, 3, 2, 1]
DEFAULT_SCALE_MIN  = 1
DEFAULT_SCALE_MAX  = 5
DEFAULT_TEXT_MAXLEN = 300

# Buchstaben zur kompakten Kandidaten-Anzeige (A, B, C … Z, AA …)
def option_letter(index: int) -> str:
    """Gibt einen Buchstaben-Marker für einen Options-Index zurück (0→A, 1→B …)."""
    letters = ""
    n = index
    while True:
        letters = chr(ord("A") + (n % 26)) + letters
        n = n // 26 - 1
        if n < 0:
            break
    return letters
