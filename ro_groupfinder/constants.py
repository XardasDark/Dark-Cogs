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

# Wie lange ein Snapshot einer abgelaufenen Gruppe für "Erneut suchen" aufbewahrt wird.
EXPIRED_SNAPSHOT_RETENTION_DAYS  = 30

# ─────────────────────────────────────────────────────────────────────────────
# SLOT-AUSWAHLTYPEN
# Gibt an wie ein Slot im Wizard definiert wurde.
# ─────────────────────────────────────────────────────────────────────────────
SLOT_TYPE_ROLE  = "role"   # Generische Rolle (Tank/Heiler/etc.)
SLOT_TYPE_CLASS = "class"  # Konkreter 1. Job (Dieb/Magier/etc.)
SLOT_TYPE_FREE  = "free"   # Freitext (RP, Quest, etc.)
