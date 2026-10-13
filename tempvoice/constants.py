"""
constants.py – Statische Konstanten für den TempVoice-Cog

Enthält:
  - custom_id-Prefix & Button-IDs des persistenten Panels
  - Emojis und deutsche Button-Beschriftungen
  - Auswahl an Voice-Regionen
  - Bitrate-Stufen
  - Standard-Werte für neue Channel
  - Embed-Farben
"""

from typing import Dict, List, Optional, Tuple

# ─────────────────────────────────────────────────────────────────────────────
# PANEL / INTERAKTIONEN
# Alle Panel-Buttons tragen custom_ids der Form "tv:<aktion>" und werden über
# die persistente View (views.TempVoicePanel) geroutet – übersteht Neustarts.
# ─────────────────────────────────────────────────────────────────────────────
CID_PREFIX = "tv"

CID_RENAME   = f"{CID_PREFIX}:rename"
CID_LIMIT    = f"{CID_PREFIX}:limit"
CID_LOCK     = f"{CID_PREFIX}:lock"
CID_HIDE     = f"{CID_PREFIX}:hide"
CID_PERMIT   = f"{CID_PREFIX}:permit"
CID_KICK     = f"{CID_PREFIX}:kick"
CID_CLAIM    = f"{CID_PREFIX}:claim"
CID_TRANSFER = f"{CID_PREFIX}:transfer"
CID_BITRATE  = f"{CID_PREFIX}:bitrate"
CID_REGION   = f"{CID_PREFIX}:region"
CID_KNOCK    = f"{CID_PREFIX}:knock"
CID_WAITING  = f"{CID_PREFIX}:waiting"

# Button-Beschriftungen (Label, Emoji) – rein deutsch
BTN_RENAME   = ("Name",       "✏️")
BTN_LIMIT    = ("Limit",      "🔢")
BTN_LOCK     = ("Sperren",    "🔒")
BTN_HIDE     = ("Verbergen",  "👁️")
BTN_PERMIT   = ("Erlauben",   "✅")
BTN_KICK     = ("Rauswerfen", "🚫")
BTN_CLAIM    = ("Übernehmen", "👑")
BTN_TRANSFER = ("Übertragen", "🔁")
BTN_BITRATE  = ("Bitrate",    "🎚️")
BTN_REGION   = ("Region",     "🌍")
BTN_KNOCK    = ("Anklopfen",  "✋")
BTN_KNOCK_ACCEPT = ("Reinlassen", "✅")
BTN_KNOCK_REJECT = ("Ablehnen",   "❌")
BTN_WAITING  = ("Warteraum",  "⏳")
BTN_ADMIT    = ("Reinholen",   "✅")
BTN_DISMISS  = ("Wegschicken", "❌")

# Wie lange (Sekunden) eine offene Anklopf-/Warteraum-Anfrage gültig bleibt
KNOCK_REQUEST_TIMEOUT = 300

# Name des optionalen Warteraum-Voice-Channels.
# Leitet sich vom Namen des Haupt-Channels ab ({name}) und wird mitgezogen,
# wenn der Haupt-Channel umbenannt wird.
WAITING_ROOM_NAME          = "⏳ Warteraum"            # Fallback, falls kein Name vorhanden
WAITING_ROOM_NAME_TEMPLATE = "⏳ Warteraum – {name}"


def waiting_room_name(main_name: str) -> str:
    """Baut den Warteraum-Namen aus dem Namen des Haupt-Channels (auf 100 Zeichen gekürzt)."""
    main_name = (main_name or "").strip()
    if not main_name:
        return WAITING_ROOM_NAME
    return WAITING_ROOM_NAME_TEMPLATE.format(name=main_name)[:MAX_CHANNEL_NAME]

# ─────────────────────────────────────────────────────────────────────────────
# VOICE-REGIONEN  (rtc_region-String → Anzeigename)
# None = Automatisch (Discord wählt die beste Region selbst)
# ─────────────────────────────────────────────────────────────────────────────
REGIONS: List[Tuple[Optional[str], str, str]] = [
    (None,          "Automatisch",   "🤖"),
    ("rotterdam",   "Rotterdam",     "🇳🇱"),
    ("us-west",     "US-West",       "🇺🇸"),
    ("us-east",     "US-Ost",        "🇺🇸"),
    ("us-central",  "US-Zentral",    "🇺🇸"),
    ("us-south",    "US-Süd",        "🇺🇸"),
    ("brazil",      "Brasilien",     "🇧🇷"),
    ("hongkong",    "Hongkong",      "🇭🇰"),
    ("india",       "Indien",        "🇮🇳"),
    ("japan",       "Japan",         "🇯🇵"),
    ("singapore",   "Singapur",      "🇸🇬"),
    ("south-korea", "Südkorea",      "🇰🇷"),
    ("sydney",      "Sydney",        "🇦🇺"),
    ("southafrica", "Südafrika",     "🇿🇦"),
]

REGION_NAMES: Dict[Optional[str], str] = {code: name for code, name, _ in REGIONS}

# ─────────────────────────────────────────────────────────────────────────────
# BITRATE-STUFEN  (in bit/s). Welche Stufen wählbar sind, hängt vom Boost-Level
# des Servers ab (guild.bitrate_limit) und wird zur Laufzeit gefiltert.
# ─────────────────────────────────────────────────────────────────────────────
BITRATE_STEPS: List[int] = [8000, 16000, 32000, 64000, 96000, 128000, 256000, 384000]

# ─────────────────────────────────────────────────────────────────────────────
# STANDARD-WERTE für neue Channel (überschreibbar per /tempvoice defaults)
# ─────────────────────────────────────────────────────────────────────────────
DEFAULT_NAME_TEMPLATE = "{user}s Channel"   # {user} → Anzeigename des Besitzers
DEFAULT_LIMIT         = 0                    # 0 = unbegrenzt
DEFAULT_BITRATE       = 64000
DEFAULT_REGION        = None                 # automatisch
DEFAULT_LOCKED        = False
DEFAULT_HIDDEN        = False

# Discord-Grenzen
MIN_BITRATE      = 8000
MAX_CHANNEL_NAME = 100
MAX_USER_LIMIT   = 99
NAME_PLACEHOLDER = "{user}"

# Namen der automatisch erstellten Objekte bei /tempvoice setup
SETUP_CATEGORY_NAME  = "Temporäre Channel"
SETUP_GENERATOR_NAME = "➕ Channel erstellen"
SETUP_INTERFACE_NAME = "voice-steuerung"

# Wartezeit (Sekunden), bevor ein leerer Channel gelöscht wird – schützt vor
# versehentlichem Verlassen / schnellem Rejoin.
EMPTY_DELETE_GRACE = 5

# ─────────────────────────────────────────────────────────────────────────────
# EMBED-FARBEN
# ─────────────────────────────────────────────────────────────────────────────
COLOR_INFO  = 0x5865F2   # Blurple – Info/Panel
COLOR_OK    = 0x00C853   # Grün    – Erfolg
COLOR_ERROR = 0xFF5252   # Rot     – Fehler
