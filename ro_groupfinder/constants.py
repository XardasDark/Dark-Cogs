"""
constants.py – Statische Konstanten für den RO Group Finder

Hier sind alle festen Werte definiert:
  - Rollen-Typen (Tank, Heiler, DD, Support, Beliebig)
  - Wizard-Schritte
  - Gruppen-Status
  - Standardwerte für Timings & Farben

Klassen (1. Jobs) und Gruppenziele befinden sich in:
  data/classes.json  →  editierbar, nach /reload aktiv
  data/goals.json    →  editierbar, nach /reload aktiv
"""

from typing import Dict

# ─────────────────────────────────────────────────────────────────────────────
# ROLLEN-TYPEN
# Generische Rollen die jede Klasse annehmen kann.
# Werden in Schritt 3 des Wizards als erste Auswahl-Ebene angezeigt.
# ─────────────────────────────────────────────────────────────────────────────
ROLE_TYPES: Dict[str, Dict[str, str]] = {
    "tank": {
        "name":  "Tank",
        "emoji": "🛡️",
    },
    "heiler": {
        "name":  "Heiler",
        "emoji": "💚",
    },
    "dd": {
        "name":  "DD",
        "emoji": "⚔️",
    },
    "support": {
        "name":  "Support",
        "emoji": "✨",
    },
    "beliebig": {
        "name":  "Beliebig",
        "emoji": "❓",
    },
}

# ─────────────────────────────────────────────────────────────────────────────
# WIEDERHOLUNGS-OPTIONEN
# Steuert ob eine Gruppe sich regelmäßig trifft.
# ─────────────────────────────────────────────────────────────────────────────
RECURRENCE_OPTIONS: Dict[str, str] = {
    "none":    "Einmalig",
    "daily":   "Täglich",
    "weekly":  "Wöchentlich",
}

# ─────────────────────────────────────────────────────────────────────────────
# WIZARD-SCHRITTE
# Reihenfolge der Schritte im Gruppen-Erstellungs-Wizard.
# Wird für "Zurück"-Navigation und Fortschrittsanzeige genutzt.
# ─────────────────────────────────────────────────────────────────────────────
WIZARD_STEPS = [
    "goal",          # Schritt 1 – Ziel der Gruppe
    "player_count",  # Schritt 2 – Spieleranzahl
    "slots",         # Schritt 3 – Slots konfigurieren
    "members",       # Schritt 4 – Bestehende Mitglieder hinzufügen
    "datetime",      # Schritt 5 – Datum & Uhrzeit (optional)
    "recurrence",    # Schritt 6 – Wiederholung (optional)
    "comment",       # Schritt 7 – Kommentar (optional)
    "level",         # Schritt 8 – Mindest-Level (optional)
    "preview",       # Schritt 9 – Vorschau & Bestätigen
]

# ─────────────────────────────────────────────────────────────────────────────
# GRUPPEN-STATUS
# ─────────────────────────────────────────────────────────────────────────────
GROUP_STATUS = {
    "open":     "Offen",
    "full":     "Voll",
    "closed":   "Geschlossen",
    "expired":  "Abgelaufen",
    "finished": "Abgeschlossen",
}

# ─────────────────────────────────────────────────────────────────────────────
# EMBED-FARBEN (hex)
# ─────────────────────────────────────────────────────────────────────────────
COLOR_OPEN     = 0x00C853   # Grün      – Gruppe sucht noch Mitglieder
COLOR_FULL     = 0xFFAB00   # Orange    – Alle Slots belegt (Warteliste möglich)
COLOR_CLOSED   = 0xD50000   # Rot       – Manuell geschlossen
COLOR_EXPIRED  = 0x757575   # Grau      – Abgelaufen (Cleanup)
COLOR_FINISHED = 0x1E88E5   # Blau      – Erfolgreich abgeschlossen (bleibt erhalten)

# ─────────────────────────────────────────────────────────────────────────────
# STANDARD-WERTE (werden in settings.json pro Guild überschrieben)
# ─────────────────────────────────────────────────────────────────────────────
DEFAULT_CLEANUP_DAYS             = 30   # Tage OHNE Aktivität bis eine Gruppe gelöscht wird
DEFAULT_EXPIRY_WARNING_DAYS      = 3    # Tage vor Ablauf → Vorwarnung an den Ersteller
DEFAULT_REMINDER_MINUTES         = 30   # Minuten vor Gruppenstart → Erinnerungs-DM
DEFAULT_WAITLIST_TIMEOUT_MINUTES = 30   # Minuten die ein Wartelisten-Spieler Zeit hat

# Tage nach Abschluss einer Gruppe bis der zugehörige Forum-Diskussionspost
# geschlossen (gesperrt + archiviert) wird. Der Post wird NIE automatisch gelöscht.
DEFAULT_FORUM_CLOSE_DAYS         = 7

# Ob der Bot den Gruppen-Channel automatisch read-only für @everyone hält.
DEFAULT_READONLY_ENFORCED        = True

# IANA-Zeitzone in der Termin-Eingaben interpretiert werden (pro Guild überschreibbar).
# Nutzereingaben (z.B. "20:30") gelten als lokale Zeit dieser Zone; gespeichert wird UTC.
DEFAULT_TIMEZONE                 = "Europe/Berlin"

# Wie lange ein Snapshot einer abgelaufenen Gruppe für "Erneut suchen" aufbewahrt wird.
EXPIRED_SNAPSHOT_RETENTION_DAYS  = 30

# ─────────────────────────────────────────────────────────────────────────────
# BENACHRICHTIGUNGS-KATEGORIEN
# Jeder Spieler kann pro Kategorie einstellen ob er die zugehörigen DM-
# Benachrichtigungen des Bots erhalten möchte (/gruppe benachrichtigungen).
# Reihenfolge = Anzeige-Reihenfolge im Auswahlmenü.
# ─────────────────────────────────────────────────────────────────────────────
NOTIFICATION_CATEGORIES: Dict[str, Dict[str, str]] = {
    "beitritte": {
        "emoji": "🟢",
        "label": "Bei- & Austritte",
        "desc":  "Wenn Spieler deiner Gruppe bei- oder austreten (für Gruppenführer)",
    },
    "gruppe_voll": {
        "emoji": "🎉",
        "label": "Gruppe ist voll",
        "desc":  "Wenn alle Slots deiner Gruppe belegt sind",
    },
    "erinnerung": {
        "emoji": "⏰",
        "label": "Start-Erinnerung",
        "desc":  "Kurz bevor eine Gruppe startet",
    },
    "aenderungen": {
        "emoji": "✏️",
        "label": "Änderungen",
        "desc":  "Wenn Gruppendetails (Termin, Ziel, ...) bearbeitet werden",
    },
    "warteliste": {
        "emoji": "⏳",
        "label": "Warteliste",
        "desc":  "Rund um deinen Wartelisten-Platz (frei, Timeout, ...)",
    },
    "status": {
        "emoji": "📢",
        "label": "Gruppen-Status",
        "desc":  "Auflösung, Abschluss, Ablauf oder Entfernung aus einer Gruppe",
    },
    "wiederholung": {
        "emoji": "🔄",
        "label": "Wiederholungen",
        "desc":  "Wenn eine wiederkehrende Gruppe neu gepostet wird",
    },
}

# Standard: alle Benachrichtigungen aktiv.
DEFAULT_NOTIFICATION_PREFS: Dict[str, bool] = {key: True for key in NOTIFICATION_CATEGORIES}

# ─────────────────────────────────────────────────────────────────────────────
# SLOT-AUSWAHLTYPEN
# Gibt an wie ein Slot im Wizard definiert wurde.
# ─────────────────────────────────────────────────────────────────────────────
SLOT_TYPE_ROLE  = "role"   # Generische Rolle (Tank/Heiler/etc.)
SLOT_TYPE_CLASS = "class"  # Konkreter 1. Job (Dieb/Magier/etc.)
SLOT_TYPE_FREE  = "free"   # Freitext (RP, Quest, etc.)
