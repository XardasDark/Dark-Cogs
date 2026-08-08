"""
models.py – Datenstrukturen & Validierung für Muhfrage

Umfragen und Fragen werden als einfache Dicts gespeichert (kompatibel mit Reds
Config / JSON). Dieses Modul liefert Factory-Funktionen, Standard-Konfigurationen
und die Antwort-Validierung pro Frage-Typ.

Antwort-Formate (`answer`) je Frage-Typ:
  points_pool      {option_index(str): punkte(int)}   – nur belegte Optionen
  plus_minus       {"plus": [idx], "minus": [idx]}
  ranked           [idx, idx, ...]                     – Reihenfolge = Rang 1,2,3…
  single_choice    idx(int)
  multiple_choice  [idx, ...]
  scale            {option_index(str): wert(int)}
  text             "freitext"
"""

import uuid
from typing import Any, Dict, List, Optional, Tuple

from .constants import (
    OPTION_BASED_TYPES,
    DEFAULT_RANK_VALUES,
    DEFAULT_SCALE_MIN,
    DEFAULT_SCALE_MAX,
    DEFAULT_TEXT_MAXLEN,
)


# ─────────────────────────────────────────────────────────────────────────────
# FACTORIES
# ─────────────────────────────────────────────────────────────────────────────

def new_survey(slug: str, title: str, description: str, creator_id: int, created_at: str) -> Dict[str, Any]:
    """Erzeugt ein leeres Umfrage-Dict im Entwurfs-Status."""
    return {
        "id":                 slug,
        "title":              title,
        "description":        description,
        "status":             "draft",
        "anonymous":          False,
        "results_visibility": "public",
        "result_channel_id":  None,
        "results_timing":     "on_close",
        "allow_change":       True,
        "deadline":           None,
        "allowed_user_ids":   [],
        "allowed_role_ids":   [],
        "created_by":         creator_id,
        "created_at":         created_at,
        "published":          None,
        "questions":          [],
    }


def default_config(qtype: str, option_count: int = 0) -> Dict[str, Any]:
    """Sinnvolle Standard-Konfiguration für einen Frage-Typ."""
    if qtype == "points_pool":
        return {"points_total": max(option_count, 3), "max_per_option": None}
    if qtype == "plus_minus":
        return {"plus_count": 1, "minus_count": 1, "value": 1}
    if qtype == "ranked":
        n = option_count or len(DEFAULT_RANK_VALUES)
        return {"rank_values": DEFAULT_RANK_VALUES[:n] if n <= len(DEFAULT_RANK_VALUES)
                else list(range(n, 0, -1))}
    if qtype == "multiple_choice":
        return {"max_choices": max(1, min(option_count, 3))}
    if qtype == "scale":
        return {"scale_min": DEFAULT_SCALE_MIN, "scale_max": DEFAULT_SCALE_MAX}
    if qtype == "text":
        return {"max_length": DEFAULT_TEXT_MAXLEN}
    return {}


def new_question(qtype: str, text: str, options: List[str], config: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """Erzeugt ein Frage-Dict."""
    return {
        "id":      uuid.uuid4().hex[:8],
        "type":    qtype,
        "text":    text,
        "options": options if qtype in OPTION_BASED_TYPES else [],
        "config":  config if config is not None else default_config(qtype, len(options)),
    }


# ─────────────────────────────────────────────────────────────────────────────
# VALIDIERUNG
# Gibt (True, "") bei gültiger Antwort, sonst (False, "Fehlermeldung").
# ─────────────────────────────────────────────────────────────────────────────

def validate_answer(question: Dict[str, Any], answer: Any) -> Tuple[bool, str]:
    qtype   = question["type"]
    options = question.get("options", [])
    cfg     = question.get("config", {})
    n       = len(options)

    def _valid_idx(i: Any) -> bool:
        return isinstance(i, int) and 0 <= i < n

    if qtype == "points_pool":
        if not isinstance(answer, dict):
            return False, "Ungültige Antwort."
        total = cfg.get("points_total", 0)
        cap   = cfg.get("max_per_option")
        summe = 0
        for k, v in answer.items():
            if not _valid_idx(int(k)):
                return False, "Ungültiger Kandidat."
            if not isinstance(v, int) or v < 0:
                return False, "Punkte müssen 0 oder größer sein."
            if cap is not None and v > cap:
                return False, f"Maximal {cap} Punkte pro Kandidat."
            summe += v
        if summe <= 0:
            return False, "Bitte verteile mindestens einen Punkt."
        if summe > total:
            return False, f"Du hast {summe} von {total} Punkten verteilt – zu viel."
        return True, ""

    if qtype == "plus_minus":
        if not isinstance(answer, dict):
            return False, "Ungültige Antwort."
        plus  = answer.get("plus", [])
        minus = answer.get("minus", [])
        if len(plus) != cfg.get("plus_count", 1):
            return False, f"Bitte genau {cfg.get('plus_count', 1)} Kandidat(en) mit Plus wählen."
        if len(minus) != cfg.get("minus_count", 1):
            return False, f"Bitte genau {cfg.get('minus_count', 1)} Kandidat(en) mit Minus wählen."
        if any(not _valid_idx(i) for i in plus + minus):
            return False, "Ungültiger Kandidat."
        if set(plus) & set(minus):
            return False, "Ein Kandidat kann nicht gleichzeitig Plus und Minus bekommen."
        if len(set(plus)) != len(plus) or len(set(minus)) != len(minus):
            return False, "Doppelte Auswahl ist nicht erlaubt."
        return True, ""

    if qtype == "ranked":
        if not isinstance(answer, list):
            return False, "Ungültige Antwort."
        needed = len(cfg.get("rank_values", []))
        if len(answer) != needed:
            return False, f"Bitte genau {needed} Kandidaten in eine Reihenfolge bringen."
        if any(not _valid_idx(i) for i in answer):
            return False, "Ungültiger Kandidat."
        if len(set(answer)) != len(answer):
            return False, "Jeder Kandidat darf nur einmal platziert werden."
        return True, ""

    if qtype == "single_choice":
        if not _valid_idx(answer):
            return False, "Bitte eine Option wählen."
        return True, ""

    if qtype == "multiple_choice":
        if not isinstance(answer, list) or not answer:
            return False, "Bitte mindestens eine Option wählen."
        if any(not _valid_idx(i) for i in answer):
            return False, "Ungültige Option."
        if len(set(answer)) != len(answer):
            return False, "Doppelte Auswahl ist nicht erlaubt."
        if len(answer) > cfg.get("max_choices", n):
            return False, f"Maximal {cfg.get('max_choices', n)} Optionen wählbar."
        return True, ""

    if qtype == "scale":
        if not isinstance(answer, dict) or not answer:
            return False, "Bitte mindestens einen Kandidaten bewerten."
        lo, hi = cfg.get("scale_min", 1), cfg.get("scale_max", 5)
        for k, v in answer.items():
            if not _valid_idx(int(k)):
                return False, "Ungültiger Kandidat."
            if not isinstance(v, int) or v < lo or v > hi:
                return False, f"Bewertungen müssen zwischen {lo} und {hi} liegen."
        return True, ""

    if qtype == "text":
        if not isinstance(answer, str) or not answer.strip():
            return False, "Bitte gib eine Antwort ein."
        if len(answer) > cfg.get("max_length", DEFAULT_TEXT_MAXLEN):
            return False, "Antwort ist zu lang."
        return True, ""

    return False, "Unbekannter Frage-Typ."


def question_summary(question: Dict[str, Any]) -> str:
    """Kurze Ein-Zeilen-Beschreibung der Frage-Konfiguration (für Vorschauen)."""
    qtype = question["type"]
    cfg   = question.get("config", {})
    if qtype == "points_pool":
        return f"{cfg.get('points_total')} Punkte frei verteilen"
    if qtype == "plus_minus":
        return f"{cfg.get('plus_count',1)}× Plus, {cfg.get('minus_count',1)}× Minus (Wert {cfg.get('value',1)})"
    if qtype == "ranked":
        return "Rangfolge: " + "-".join(str(v) for v in cfg.get("rank_values", []))
    if qtype == "multiple_choice":
        return f"Bis zu {cfg.get('max_choices')} Optionen"
    if qtype == "scale":
        return f"Skala {cfg.get('scale_min')}–{cfg.get('scale_max')}"
    if qtype == "single_choice":
        return "Eine Option wählen"
    if qtype == "text":
        return "Freie Textantwort"
    return ""
