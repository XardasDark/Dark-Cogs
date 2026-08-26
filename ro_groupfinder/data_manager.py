"""
data_manager.py – Datenverwaltung für den RO Group Finder

Verantwortlich für:
  - Laden und Speichern von groups.json (aktive Gruppen pro Guild)
  - Laden und Speichern von settings.json (Guild-Einstellungen)
  - Laden von goals.json und classes.json (read-only, nach reload aktuell)
  - Hilfsfunktionen für Gruppenoperationen (Slot belegen, Warteliste, etc.)

Alle Datei-Pfade sind relativ zum Ordner des Cogs (ro_groupfinder/data/).
"""

import json
import uuid
import os
from datetime import datetime, timedelta, timezone, tzinfo
from typing import Optional, Dict, List, Any

# zoneinfo ist ab Python 3.9 in der Stdlib; darunter über backports.zoneinfo.
try:
    from zoneinfo import ZoneInfo, ZoneInfoNotFoundError
except ImportError:  # pragma: no cover  (Python < 3.9)
    try:
        from backports.zoneinfo import ZoneInfo, ZoneInfoNotFoundError  # type: ignore
    except ImportError:
        ZoneInfo = None                 # type: ignore
        ZoneInfoNotFoundError = Exception  # type: ignore

from .constants import (
    DEFAULT_CLEANUP_DAYS,
    DEFAULT_EXPIRY_WARNING_DAYS,
    DEFAULT_REMINDER_MINUTES,
    DEFAULT_WAITLIST_TIMEOUT_MINUTES,
    DEFAULT_FORUM_CLOSE_HOURS,
    DEFAULT_READONLY_ENFORCED,
    DEFAULT_CLOSED_POST_ACTION,
    DEFAULT_TIMEZONE,
    EXPIRED_SNAPSHOT_RETENTION_DAYS,
    GROUP_STATUS,
    DEFAULT_NOTIFICATION_PREFS,
)

# ─────────────────────────────────────────────────────────────────────────────
# PFADE
# ─────────────────────────────────────────────────────────────────────────────
_BASE_DIR      = os.path.dirname(os.path.abspath(__file__))
_DATA_DIR      = os.path.join(_BASE_DIR, "data")
_GROUPS_FILE   = os.path.join(_DATA_DIR, "groups.json")
_SETTINGS_FILE = os.path.join(_DATA_DIR, "settings.json")
_USER_PREFS_FILE = os.path.join(_DATA_DIR, "user_prefs.json")
_EXPIRED_FILE  = os.path.join(_DATA_DIR, "expired_snapshots.json")
_GOALS_FILE    = os.path.join(_DATA_DIR, "goals.json")
_CLASSES_FILE  = os.path.join(_DATA_DIR, "classes.json")


# ─────────────────────────────────────────────────────────────────────────────
# INTERNE HILFSFUNKTIONEN
# ─────────────────────────────────────────────────────────────────────────────

def _load_json(path: str, default: Any) -> Any:
    """Lädt eine JSON-Datei. Gibt `default` zurück wenn die Datei fehlt."""
    os.makedirs(_DATA_DIR, exist_ok=True)
    if not os.path.exists(path):
        return default
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError):
        return default


def _save_json(path: str, data: Any) -> None:
    """Speichert Daten als JSON-Datei (pretty-printed)."""
    os.makedirs(_DATA_DIR, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


# ─────────────────────────────────────────────────────────────────────────────
# GOALS & CLASSES  (Read-only, aus JSON geladen)
# ─────────────────────────────────────────────────────────────────────────────

def load_goals() -> List[Dict]:
    """
    Lädt die vordefinierten Gruppenziele aus goals.json.
    Änderungen in der Datei werden nach /reload aktiv.
    """
    return _load_json(_GOALS_FILE, [])


def load_classes() -> List[Dict]:
    """
    Lädt die 1. Job-Klassen aus classes.json.
    Änderungen in der Datei werden nach /reload aktiv.
    """
    return _load_json(_CLASSES_FILE, [])


def get_class_by_key(key: str) -> Optional[Dict]:
    """Gibt eine Klasse anhand ihres Keys zurück, oder None."""
    for cls in load_classes():
        if cls["key"] == key:
            return cls
    return None


def resolve_goal_name(group: Dict) -> str:
    """
    Gibt den anzeigbaren Zielnamen einer Gruppe zurück.

    Reihenfolge:
      1. eigener Titel (goal_custom), falls gesetzt
      2. der in goals.json hinterlegte Name zum gespeicherten Key
         (z.B. "memorial_poring" → "Memorial Dungeon: Poring Village")
      3. andernfalls der Key selbst

    Das Feld `goal` bleibt bewusst der Key (wird u.a. für "Gruppe kopieren"
    zum Abgleich mit goals.json genutzt); nur die Anzeige wird hier aufgelöst.
    """
    custom = group.get("goal_custom")
    if custom:
        return custom
    key = group.get("goal") or ""
    if key and key != "__custom__":
        for g in load_goals():
            if g.get("key") == key:
                return g.get("name") or key
    return key or "–"


# ─────────────────────────────────────────────────────────────────────────────
# SETTINGS  (Pro Guild)
# ─────────────────────────────────────────────────────────────────────────────

def load_settings() -> Dict:
    """Lädt alle Guild-Einstellungen."""
    return _load_json(_SETTINGS_FILE, {})


def save_settings(settings: Dict) -> None:
    """Speichert alle Guild-Einstellungen."""
    _save_json(_SETTINGS_FILE, settings)


def get_guild_settings(guild_id: int) -> Dict:
    """
    Gibt die Einstellungen einer Guild zurück.
    Fehlende Felder werden mit Standardwerten gefüllt.
    """
    all_settings = load_settings()
    guild_key = str(guild_id)
    defaults = {
        "group_channel_id":           None,
        "forum_channel_id":           None,
        "forum_close_hours":          DEFAULT_FORUM_CLOSE_HOURS,
        "readonly_enforced":          DEFAULT_READONLY_ENFORCED,
        "archive_channel_id":         None,
        "closed_post_action":         DEFAULT_CLOSED_POST_ACTION,
        "overview_message_id":        None,
        "cleanup_days":               DEFAULT_CLEANUP_DAYS,
        "warning_days":               DEFAULT_EXPIRY_WARNING_DAYS,
        "reminder_minutes":           DEFAULT_REMINDER_MINUTES,
        "waitlist_timeout_minutes":   DEFAULT_WAITLIST_TIMEOUT_MINUTES,
        "timezone":                   DEFAULT_TIMEZONE,
    }
    existing = all_settings.get(guild_key, {})
    # Merge: defaults werden durch gespeicherte Werte überschrieben
    return {**defaults, **existing}


def set_guild_setting(guild_id: int, key: str, value: Any) -> None:
    """Setzt einen einzelnen Einstellungswert für eine Guild."""
    all_settings = load_settings()
    guild_key = str(guild_id)
    if guild_key not in all_settings:
        all_settings[guild_key] = {}
    all_settings[guild_key][key] = value
    save_settings(all_settings)


def set_group_channel(guild_id: int, channel_id: int) -> None:
    """Legt den Channel fest, in dem Gruppen erstellt werden dürfen."""
    set_guild_setting(guild_id, "group_channel_id", channel_id)


def get_group_channel(guild_id: int) -> Optional[int]:
    """Gibt die Channel-ID zurück in der Gruppen erlaubt sind, oder None."""
    return get_guild_settings(guild_id).get("group_channel_id")


def set_forum_channel(guild_id: int, channel_id: int) -> None:
    """Legt den Forum-Channel fest, in dem Diskussionsposts erstellt werden."""
    set_guild_setting(guild_id, "forum_channel_id", channel_id)


def get_forum_channel(guild_id: int) -> Optional[int]:
    """Gibt die Forum-Channel-ID für Diskussionsposts zurück, oder None."""
    return get_guild_settings(guild_id).get("forum_channel_id")


# ─────────────────────────────────────────────────────────────────────────────
# BENACHRICHTIGUNGS-EINSTELLUNGEN (pro Spieler, guild-übergreifend)
# ─────────────────────────────────────────────────────────────────────────────
#
# DMs erreichen einen Spieler unabhängig von der Guild, daher werden die
# Präferenzen global pro User-ID gespeichert. Fehlende Kategorien gelten als
# aktiv (Standard aus DEFAULT_NOTIFICATION_PREFS).

def load_user_prefs() -> Dict:
    """Lädt alle Benachrichtigungs-Einstellungen (Key = User-ID als String)."""
    return _load_json(_USER_PREFS_FILE, {})


def save_user_prefs(prefs: Dict) -> None:
    """Speichert alle Benachrichtigungs-Einstellungen."""
    _save_json(_USER_PREFS_FILE, prefs)


def get_user_notif_prefs(user_id: int) -> Dict[str, bool]:
    """
    Gibt die Benachrichtigungs-Präferenzen eines Spielers zurück.
    Fehlende Kategorien werden mit dem Standard (aktiv) aufgefüllt.
    """
    stored = load_user_prefs().get(str(user_id), {})
    merged = dict(DEFAULT_NOTIFICATION_PREFS)
    for key, value in stored.items():
        if key in merged:
            merged[key] = bool(value)
    return merged


def set_user_notif_prefs(user_id: int, prefs: Dict[str, bool]) -> None:
    """Speichert die vollständigen Präferenzen eines Spielers."""
    all_prefs = load_user_prefs()
    all_prefs[str(user_id)] = {
        key: bool(prefs.get(key, DEFAULT_NOTIFICATION_PREFS[key]))
        for key in DEFAULT_NOTIFICATION_PREFS
    }
    save_user_prefs(all_prefs)


def is_notif_enabled(user_id: int, category: str) -> bool:
    """True wenn der Spieler die Benachrichtigungs-Kategorie aktiviert hat."""
    return get_user_notif_prefs(user_id).get(category, True)


# ─────────────────────────────────────────────────────────────────────────────
# ZEITZONE & DATUM-KONVERTIERUNG
# ─────────────────────────────────────────────────────────────────────────────
#
# Grundregel: Termine werden IMMER als UTC (ISO-String) gespeichert.
# Nutzereingaben ("DD.MM.YYYY HH:MM") gelten als LOKALE Zeit der Guild-Zeitzone
# und werden vor dem Speichern nach UTC umgerechnet. Die Anzeige nutzt
# dynamische Discord-Timestamps, die sich pro Betrachter lokalisieren.

def is_valid_timezone(name: str) -> bool:
    """True wenn `name` eine auflösbare IANA-Zeitzone ist (z.B. 'Europe/Berlin')."""
    if not name or ZoneInfo is None:
        return False
    try:
        ZoneInfo(name)
        return True
    except (ZoneInfoNotFoundError, ValueError):
        return False


def get_guild_timezone(guild_id: int) -> tzinfo:
    """
    Gibt die konfigurierte Zeitzone der Guild zurück (Standard: DEFAULT_TIMEZONE).
    Fällt bei fehlendem zoneinfo-Modul oder ungültigem Namen auf UTC zurück.
    """
    name = get_guild_settings(guild_id).get("timezone") or DEFAULT_TIMEZONE
    if ZoneInfo is None:
        return timezone.utc
    for candidate in (name, DEFAULT_TIMEZONE):
        try:
            return ZoneInfo(candidate)
        except (ZoneInfoNotFoundError, ValueError):
            continue
    return timezone.utc


def parse_local_input(dt_str: str, guild_id: int) -> Optional[datetime]:
    """
    Parst eine Nutzereingabe 'DD.MM.YYYY HH:MM' als LOKALE Zeit der Guild-Zeitzone
    und gibt ein timezone-aware datetime in UTC zurück.
    Gibt None zurück wenn das Format ungültig ist.
    """
    try:
        naive = datetime.strptime((dt_str or "").strip(), "%d.%m.%Y %H:%M")
    except (ValueError, AttributeError):
        return None
    return naive.replace(tzinfo=get_guild_timezone(guild_id)).astimezone(timezone.utc)


def parse_stored_datetime(value: Optional[str], guild_id: int) -> Optional[datetime]:
    """
    Wandelt einen gespeicherten datetime-Wert in ein aware UTC-datetime um.

    Unterstützt:
      - ISO-Format (aware → direkt; naiv → als UTC interpretiert)
      - Alt-Format 'DD.MM.YYYY HH:MM' (als lokale Guild-Zeit interpretiert)
    """
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(value)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc)
    except ValueError:
        pass
    return parse_local_input(value, guild_id)


def stored_datetime_to_local_str(value: Optional[str], guild_id: int) -> Optional[str]:
    """Gespeichertes datetime → 'DD.MM.YYYY HH:MM' in der Guild-Zeitzone (oder None)."""
    dt = parse_stored_datetime(value, guild_id)
    if dt is None:
        return None
    return dt.astimezone(get_guild_timezone(guild_id)).strftime("%d.%m.%Y %H:%M")


def format_datetime_display(
    value:    Optional[str],
    guild_id: int,
    fallback: str = "Offen / Zeitlos",
) -> str:
    """
    Gespeichertes datetime → dynamischer Discord-Timestamp (pro Betrachter
    lokalisiert, mit relativer Angabe). Fällt auf `fallback` zurück wenn kein Datum.
    """
    dt = parse_stored_datetime(value, guild_id)
    if dt is None:
        return fallback
    ts = int(dt.timestamp())
    return f"<t:{ts}:F> (<t:{ts}:R>)"


# ─────────────────────────────────────────────────────────────────────────────
# GROUPS  (Aktive Gruppen pro Guild)
# ─────────────────────────────────────────────────────────────────────────────

def load_groups() -> Dict:
    """Lädt alle Gruppen aller Guilds."""
    return _load_json(_GROUPS_FILE, {})


def save_groups(groups: Dict) -> None:
    """Speichert alle Gruppen."""
    _save_json(_GROUPS_FILE, groups)


def get_guild_groups(guild_id: int) -> Dict:
    """Gibt alle Gruppen einer Guild zurück. Key = message_id (als String)."""
    all_groups = load_groups()
    return all_groups.get(str(guild_id), {})


def get_group_by_message(guild_id: int, message_id: int) -> Optional[Dict]:
    """Gibt eine Gruppe anhand der Discord-Message-ID zurück."""
    guild_groups = get_guild_groups(guild_id)
    return guild_groups.get(str(message_id))


def save_group(guild_id: int, group: Dict) -> None:
    """
    Speichert oder aktualisiert eine einzelne Gruppe.
    Key im Dict ist die message_id (als String).
    """
    all_groups = load_groups()
    guild_key = str(guild_id)
    msg_key = str(group["message_id"])
    if guild_key not in all_groups:
        all_groups[guild_key] = {}
    all_groups[guild_key][msg_key] = group
    save_groups(all_groups)


def delete_group(guild_id: int, message_id: int) -> bool:
    """Löscht eine Gruppe. Gibt True zurück wenn gefunden und gelöscht."""
    all_groups = load_groups()
    guild_key = str(guild_id)
    msg_key = str(message_id)
    if guild_key in all_groups and msg_key in all_groups[guild_key]:
        del all_groups[guild_key][msg_key]
        save_groups(all_groups)
        return True
    return False


# ─────────────────────────────────────────────────────────────────────────────
# GRUPPEN-ERSTELLUNG
# ─────────────────────────────────────────────────────────────────────────────

def create_group(
    guild_id:       int,
    channel_id:     int,
    creator_id:     int,
    creator_name:   str,
    creator_ingame: Optional[str],
    goal:           str,
    goal_custom:    Optional[str],
    player_count:   int,
    slots:          List[Dict],
    dt:             Optional[datetime],
    recurrence:     str,
    comment:        Optional[str],
    level_min:      Optional[int],
    level_max:      Optional[int],
) -> Dict:
    """
    Erstellt ein neues Gruppen-Dict (noch ohne message_id).
    message_id wird erst nach dem Discord-Post gesetzt (→ set_group_message_id).
    """
    now = datetime.now(timezone.utc)
    settings = get_guild_settings(guild_id)
    expires_at = (now + timedelta(days=settings["cleanup_days"])).isoformat()

    return {
        "group_id":            str(uuid.uuid4()),
        "message_id":          None,     # wird nach Post gesetzt
        "channel_id":          channel_id,
        "guild_id":            guild_id,
        "creator_id":          creator_id,
        "creator_name":        creator_name,
        "creator_ingame":      creator_ingame,
        "goal":                goal,
        "goal_custom":         goal_custom,
        "player_count":        player_count,
        "slots":               slots,    # Liste von Slot-Dicts (siehe unten)
        "waitlist":            [],
        "datetime":            dt.isoformat() if dt else None,
        "recurrence":          recurrence,
        "comment":             comment,
        "level_min":           level_min,
        "level_max":           level_max,
        "status":              "open",
        "created_at":          now.isoformat(),
        # Ablauf basiert auf Inaktivität: expires_at = last_activity_at + cleanup_days.
        # Jede Aktivität (Beitritt, Verlassen, ...) setzt den Timer via touch_group_activity() zurück.
        "last_activity_at":    now.isoformat(),
        "expires_at":          expires_at,
        "expiry_warning_sent": False,
        "reminder_sent":       False,
        # Forum-Diskussionspost (wird nach dem Gruppen-Post erstellt).
        "forum_thread_id":     None,
        # Verhindert doppeltes Schließen des Forum-Posts.
        "forum_closed":        False,
    }


def touch_group_activity(group: Dict) -> Dict:
    """
    Registriert Aktivität in einer Gruppe und verschiebt den Inaktivitäts-Ablauf.

    Wird bei jeder relevanten Interaktion aufgerufen (Beitritt, Verlassen,
    Warteliste, Bearbeitung, Entfernen ...). Setzt:
      - last_activity_at    → jetzt
      - expires_at          → jetzt + cleanup_days
      - expiry_warning_sent → False (damit erneut vorgewarnt wird)

    Der Aufrufer muss die Gruppe anschließend selbst speichern (save_group).
    """
    now      = datetime.now(timezone.utc)
    settings = get_guild_settings(group["guild_id"])
    group["last_activity_at"]    = now.isoformat()
    group["expires_at"]          = (now + timedelta(days=settings["cleanup_days"])).isoformat()
    group["expiry_warning_sent"] = False
    return group


def reset_slots(slots: List[Dict]) -> List[Dict]:
    """
    Erstellt eine Kopie der Slot-Liste mit zurückgesetzten Belegungen.
    Wird für Wiederholungs- und neu erstellte Gruppen verwendet.
    """
    reset = []
    for slot in slots:
        reset.append({
            **slot,
            "filled_by_id":     None,
            "filled_by_name":   None,
            "filled_by_ingame": None,
            "filled_class":     None,
            "filled_emoji":     None,
        })
    return reset


def set_group_message_id(group: Dict, message_id: int) -> Dict:
    """Setzt die Discord-Message-ID nach dem Posten."""
    group["message_id"] = message_id
    return group


def build_slot(
    slot_index:    int,
    slot_type:     str,      # "role" | "class" | "free"
    display_name:  str,
    emoji:         str,
    class_key:     Optional[str] = None,
    role_key:      Optional[str] = None,
    free_text:     Optional[str] = None,
) -> Dict:
    """
    Erzeugt ein einzelnes Slot-Dict.
    slot_type gibt an wie der Slot definiert wurde.
    """
    return {
        "slot_index":      slot_index,
        "slot_type":       slot_type,
        "display_name":    display_name,    # Anzeigename (z.B. "Dieb" oder "2× DD")
        "emoji":           emoji,
        "class_key":       class_key,
        "role_key":        role_key,
        "free_text":       free_text,
        "filled_by_id":    None,
        "filled_by_name":  None,
        "filled_by_ingame": None,
        "filled_class":    None,            # Klasse die der Beitretende angegeben hat
        "filled_emoji":    None,
    }


# ─────────────────────────────────────────────────────────────────────────────
# SLOT-OPERATIONEN
# ─────────────────────────────────────────────────────────────────────────────

def fill_slot(
    group:         Dict,
    slot_index:    int,
    user_id:       int,
    username:      str,
    ingame_name:   Optional[str],
    filled_class:  str,
    filled_emoji:  str,
) -> bool:
    """
    Belegt einen offenen Slot mit einem Spieler.
    Gibt True zurück wenn erfolgreich, False wenn Slot bereits belegt.
    """
    slot = _get_slot(group, slot_index)
    if slot is None or slot["filled_by_id"] is not None:
        return False

    slot["filled_by_id"]    = user_id
    slot["filled_by_name"]  = username
    slot["filled_by_ingame"] = ingame_name
    slot["filled_class"]    = filled_class
    slot["filled_emoji"]    = filled_emoji
    _update_status(group)
    return True


def clear_slot(group: Dict, slot_index: int) -> bool:
    """
    Leert einen belegten Slot.
    Gibt True zurück wenn erfolgreich.
    """
    slot = _get_slot(group, slot_index)
    if slot is None or slot["filled_by_id"] is None:
        return False

    slot["filled_by_id"]    = None
    slot["filled_by_name"]  = None
    slot["filled_by_ingame"] = None
    slot["filled_class"]    = None
    slot["filled_emoji"]    = None
    _update_status(group)
    return True


def find_user_slot(group: Dict, user_id: int) -> Optional[int]:
    """Gibt den slot_index zurück, in dem ein Spieler sitzt, oder None."""
    for slot in group["slots"]:
        if slot["filled_by_id"] == user_id:
            return slot["slot_index"]
    return None


def get_open_slots(group: Dict) -> List[Dict]:
    """Gibt alle offenen (nicht belegten) Slots zurück."""
    return [s for s in group["slots"] if s["filled_by_id"] is None]


def get_filled_slots(group: Dict) -> List[Dict]:
    """Gibt alle belegten Slots zurück."""
    return [s for s in group["slots"] if s["filled_by_id"] is not None]


# ─────────────────────────────────────────────────────────────────────────────
# WARTELISTE
# ─────────────────────────────────────────────────────────────────────────────

def add_to_waitlist(
    group:        Dict,
    user_id:      int,
    username:     str,
    ingame_name:  Optional[str],
    class_display: str,
    class_emoji:   str,
) -> int:
    """
    Fügt einen Spieler zur Warteliste hinzu.
    Gibt die Position in der Warteliste zurück (1-basiert).
    """
    # Doppelte Einträge verhindern
    if any(w["user_id"] == user_id for w in group["waitlist"]):
        return _waitlist_position(group, user_id)

    group["waitlist"].append({
        "user_id":      user_id,
        "username":     username,
        "ingame_name":  ingame_name,
        "class_display": class_display,
        "class_emoji":  class_emoji,
        "joined_at":    datetime.now(timezone.utc).isoformat(),
        "notified_at":  None,   # Zeitpunkt der letzten Benachrichtigung
    })
    return len(group["waitlist"])


def remove_from_waitlist(group: Dict, user_id: int) -> bool:
    """Entfernt einen Spieler von der Warteliste. Gibt True zurück wenn gefunden."""
    before = len(group["waitlist"])
    group["waitlist"] = [w for w in group["waitlist"] if w["user_id"] != user_id]
    return len(group["waitlist"]) < before


def get_next_waitlist(group: Dict) -> Optional[Dict]:
    """Gibt den nächsten Spieler auf der Warteliste zurück, oder None."""
    return group["waitlist"][0] if group["waitlist"] else None


def is_user_in_waitlist(group: Dict, user_id: int) -> bool:
    return any(w["user_id"] == user_id for w in group["waitlist"])


def is_user_in_group(group: Dict, user_id: int) -> bool:
    """True wenn der Spieler in einem Slot sitzt."""
    return find_user_slot(group, user_id) is not None


# ─────────────────────────────────────────────────────────────────────────────
# GRUPPEN-BEARBEITUNG
# ─────────────────────────────────────────────────────────────────────────────

def set_group_leader(
    group:          Dict,
    new_leader_id:  int,
    new_leader_name: str,
    new_leader_ingame: Optional[str] = None,
) -> Dict:
    """
    Übergibt die Gruppenführung an ein bestehendes Mitglied.

    Aktualisiert nur die Ersteller-Felder – der bisherige Führer behält seinen
    Slot und bleibt normales Mitglied. Slots werden nicht verändert.
    """
    group["creator_id"]   = new_leader_id
    group["creator_name"] = new_leader_name
    if new_leader_ingame is not None:
        group["creator_ingame"] = new_leader_ingame
    return group


def update_group_fields(group: Dict, **kwargs) -> Dict:
    """
    Aktualisiert beliebige Felder einer Gruppe.
    Erlaubte Felder: goal, goal_custom, comment, datetime,
                     recurrence, level_min, level_max
    """
    allowed = {
        "goal", "goal_custom", "comment",
        "datetime", "recurrence", "level_min", "level_max",
    }
    for key, value in kwargs.items():
        if key in allowed:
            group[key] = value
    return group


# ─────────────────────────────────────────────────────────────────────────────
# CLEANUP
# ─────────────────────────────────────────────────────────────────────────────

def get_expired_groups(guild_id: int) -> List[Dict]:
    """
    Gibt alle Gruppen zurück, deren expires_at in der Vergangenheit liegt
    und die noch nicht als 'expired' oder 'closed' markiert sind.
    """
    now = datetime.now(timezone.utc)
    expired = []
    for group in get_guild_groups(guild_id).values():
        if group.get("status") in ("expired", "closed", "finished"):
            continue
        expires_str = group.get("expires_at")
        if expires_str:
            try:
                expires_dt = datetime.fromisoformat(expires_str)
                if expires_dt < now:
                    expired.append(group)
            except ValueError:
                pass
    return expired


def get_all_groups_flat() -> List[Dict]:
    """Gibt alle Gruppen aller Guilds als flache Liste zurück."""
    all_groups = load_groups()
    result = []
    for guild_groups in all_groups.values():
        result.extend(guild_groups.values())
    return result


def get_upcoming_reminder_groups() -> List[Dict]:
    """
    Gibt alle Gruppen zurück, bei denen der Start in <= reminder_minutes
    Minuten liegt und noch keine Erinnerung gesendet wurde.
    """
    now = datetime.now(timezone.utc)
    due = []
    for group in get_all_groups_flat():
        if group.get("reminder_sent") or not group.get("datetime"):
            continue
        if group.get("status") in ("closed", "expired", "finished"):
            continue
        try:
            start = datetime.fromisoformat(group["datetime"])
            settings = get_guild_settings(group["guild_id"])
            reminder_delta = timedelta(minutes=settings["reminder_minutes"])
            if now >= (start - reminder_delta) and now < start:
                due.append(group)
        except ValueError:
            pass
    return due


# ─────────────────────────────────────────────────────────────────────────────
# EXPIRED-SNAPSHOTS  (für "Erneut suchen" nach Ablauf)
# ─────────────────────────────────────────────────────────────────────────────

def _load_expired_snapshots() -> Dict:
    """Lädt alle gespeicherten Snapshots abgelaufener Gruppen (Key = group_id)."""
    return _load_json(_EXPIRED_FILE, {})


def _save_expired_snapshots(snapshots: Dict) -> None:
    _save_json(_EXPIRED_FILE, snapshots)


def save_expired_snapshot(group: Dict) -> None:
    """
    Legt eine Kopie einer abgelaufenen Gruppe ab, damit der Ersteller sie
    per Button schnell erneut posten kann.

    Alte Snapshots (> EXPIRED_SNAPSHOT_RETENTION_DAYS) werden dabei aufgeräumt.
    """
    snapshots = _load_expired_snapshots()

    # Retention: abgelaufene Snapshots entfernen
    now    = datetime.now(timezone.utc)
    cutoff = now - timedelta(days=EXPIRED_SNAPSHOT_RETENTION_DAYS)
    for gid in list(snapshots.keys()):
        ts = snapshots[gid].get("expired_at")
        try:
            if ts and datetime.fromisoformat(ts) < cutoff:
                del snapshots[gid]
        except ValueError:
            del snapshots[gid]

    snapshot = dict(group)
    snapshot["expired_at"] = now.isoformat()
    snapshots[str(group["group_id"])] = snapshot
    _save_expired_snapshots(snapshots)


def get_expired_snapshot(group_id: str) -> Optional[Dict]:
    """Gibt den Snapshot einer abgelaufenen Gruppe zurück, oder None."""
    return _load_expired_snapshots().get(str(group_id))


def delete_expired_snapshot(group_id: str) -> bool:
    """Entfernt einen Snapshot. Gibt True zurück wenn gefunden."""
    snapshots = _load_expired_snapshots()
    if str(group_id) in snapshots:
        del snapshots[str(group_id)]
        _save_expired_snapshots(snapshots)
        return True
    return False


def find_group_by_public_id(guild_id: int, public_id: str) -> Optional[Dict]:
    """
    Sucht eine Gruppe anhand der Gruppen-ID, wie sie im Post-Footer angezeigt wird.

    Durchsucht zuerst die aktiven Gruppen der Guild, danach die Snapshots
    abgelaufener Gruppen. Gibt die gefundene Gruppe (Dict) zurück, oder None.
    """
    pid = (public_id or "").strip().lower()
    if not pid:
        return None

    def _matches(gid: str) -> bool:
        gid = gid.lower()
        return gid == pid or gid.startswith(pid)

    # 1. Aktive Gruppen der Guild
    for group in get_guild_groups(guild_id).values():
        if _matches(str(group.get("group_id", ""))):
            return group

    # 2. Snapshots abgelaufener Gruppen (nur dieselbe Guild)
    for snap in _load_expired_snapshots().values():
        if snap.get("guild_id") != guild_id:
            continue
        if _matches(str(snap.get("group_id", ""))):
            return snap

    return None


# ─────────────────────────────────────────────────────────────────────────────
# INTERNE HELFER
# ─────────────────────────────────────────────────────────────────────────────

def _get_slot(group: Dict, slot_index: int) -> Optional[Dict]:
    """Gibt den Slot mit dem gegebenen Index zurück."""
    for slot in group["slots"]:
        if slot["slot_index"] == slot_index:
            return slot
    return None


def _waitlist_position(group: Dict, user_id: int) -> int:
    """Gibt die 1-basierte Position eines Nutzers in der Warteliste zurück."""
    for i, w in enumerate(group["waitlist"]):
        if w["user_id"] == user_id:
            return i + 1
    return -1


def _update_status(group: Dict) -> None:
    """Aktualisiert den Gruppen-Status basierend auf offenen Slots."""
    if group["status"] in ("closed", "expired", "finished"):
        return
    open_count = len(get_open_slots(group))
    group["status"] = "full" if open_count == 0 else "open"
