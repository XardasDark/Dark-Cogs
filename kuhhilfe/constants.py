"""
constants.py – zentrale Konstanten für KuhHilfe

custom_id-Präfixe für persistente Komponenten (Panel-Button & Ticket-Controls),
Farben, das Alphabet für verschleierte Ticket-IDs, Feld-Styles und Defaults.
"""

import discord

# ─────────────────────────────────────────────────────────────────────────────
# custom_ids (persistente Komponenten – geroutet über on_interaction)
# ─────────────────────────────────────────────────────────────────────────────
PANEL_OPEN          = "kuhhilfe:panel:open"
TICKET_CLAIM        = "kuhhilfe:ticket:claim"
TICKET_CLOSE        = "kuhhilfe:ticket:close"
TICKET_CONFIRM      = "kuhhilfe:ticket:confirm_close"
TICKET_REOPEN       = "kuhhilfe:ticket:reopen"
TICKET_MANAGE       = "kuhhilfe:ticket:manage"

# Präfixe der ephemeren Wizard-Komponenten (kurzlebig, eigene View-Callbacks)
WIZARD_CAT_SELECT   = "kuhhilfe:wizard:cat"

# ─────────────────────────────────────────────────────────────────────────────
# Farben
# ─────────────────────────────────────────────────────────────────────────────
COLOR_PANEL   = 0x5865F2   # Blurple – Support-Panel
COLOR_TICKET  = 0x2ECC71   # Grün – offenes Ticket
COLOR_PENDING = 0xF1C40F   # Gelb – Schließung angefragt
COLOR_CLOSED  = 0x95A5A6   # Grau – geschlossen
COLOR_INFO    = 0x3498DB
COLOR_ERROR   = 0xE74C3C
COLOR_NOTICE  = 0xFFC0CB   # einheitliche Farbe für Bot-Hinweise im Ticket-Channel

# ─────────────────────────────────────────────────────────────────────────────
# Ticket-IDs (verschleiern die laufende Anzahl → Buchstaben + Zahlen, zufällig)
# Nur Kleinbuchstaben/Ziffern, ohne verwechselbare Zeichen (Discord-Channelname-tauglich)
# ─────────────────────────────────────────────────────────────────────────────
TICKET_ID_ALPHABET = "abcdefghjkmnpqrstuvwxyz23456789"
TICKET_ID_LENGTH   = 6

# ─────────────────────────────────────────────────────────────────────────────
# Status eines Tickets
# ─────────────────────────────────────────────────────────────────────────────
STATUS_OPEN            = "open"
STATUS_CLOSE_REQUESTED = "close_requested"
STATUS_CLOSED          = "closed"

STATUS_LABELS = {
    STATUS_OPEN:            "🟢 Offen",
    STATUS_CLOSE_REQUESTED: "🟡 Schließung angefragt",
    STATUS_CLOSED:          "⚪ Geschlossen",
}

# Bearbeitungs-Stufe (Unter-Status eines offenen Tickets, rein informativ)
STAGE_IN_PROGRESS = "in_progress"
STAGE_WAITING     = "waiting_user"
STAGE_LABELS = {
    STAGE_IN_PROGRESS: "🛠️ In Bearbeitung",
    STAGE_WAITING:     "⏳ Wartet auf Nutzer",
}

# ─────────────────────────────────────────────────────────────────────────────
# Modal-Felder pro Kategorie
# ─────────────────────────────────────────────────────────────────────────────
MAX_FIELDS      = 5          # harte Discord-Grenze: max. 5 Komponenten pro Modal
FIELD_STYLES    = {
    "short":     discord.TextStyle.short,
    "paragraph": discord.TextStyle.paragraph,
}
FIELD_STYLE_LABELS = {
    "short":     "Kurz (einzeilig)",
    "paragraph": "Lang (mehrzeilig)",
}

# Standard-Felder, mit denen eine neu angelegte Kategorie startet
DEFAULT_FIELDS = [
    {"label": "Betreff",      "style": "short",     "required": True,
     "placeholder": "Kurz: worum geht es?",        "max_length": 100},
    {"label": "Beschreibung", "style": "paragraph", "required": True,
     "placeholder": "Beschreibe dein Anliegen so genau wie möglich …", "max_length": 1000},
]

# ─────────────────────────────────────────────────────────────────────────────
# Defaults für Guild-Einstellungen
# ─────────────────────────────────────────────────────────────────────────────
DEFAULT_MAX_OPEN      = 2      # gleichzeitig offene Tickets pro Nutzer (0 = unbegrenzt)
DEFAULT_DELETE_DELAY  = 300    # Sekunden bis zum Löschen nach dem Schließen (5 Min)
DEFAULT_CONFIRM_DELETE = 60    # Sekunden, bis ephemere Nutzer-Bestätigungen verschwinden (0 = aus)

# Länge/Validierung für Kategorie-Keys
KEY_MAX_LENGTH = 32

# Trennzeichen für Pfade "gruppe/kategorie" (darf nicht im Key vorkommen)
CATEGORY_PATH_SEP = "/"

# Knoten-Typen im Kategorie-Baum
KIND_GROUP = "group"   # Oberkategorie mit Unterkategorien
KIND_LEAF  = "leaf"    # finale Kategorie (trägt Rollen/User/Felder)
