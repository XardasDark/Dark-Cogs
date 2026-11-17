"""
constants.py – Statische Konstanten für den Herdbuch-Cog

Enthält:
  - custom_id-Schema der persistenten (DynamicItem-)Komponenten
  - Embed-Farben & Status-Marker (aus der verbindlichen Embed-Spec)
  - Namen der bei /gildenadmin setup automatisch erstellten Objekte
  - Slug-/ID-Helfer und Discord-Grenzen
"""

import re
from typing import Dict, Iterable

# ─────────────────────────────────────────────────────────────────────────────
# CUSTOM-ID-SCHEMA
# Persistente Komponenten tragen custom_ids der Form "herdbuch:<bereich>:<data>".
# Der veränderliche Teil (Clan-Slug bzw. Antrags-ID) steckt in der ID, damit die
# Buttons über DynamicItem Neustarts überleben (Umsetzung in Phase 2, views.py).
# ─────────────────────────────────────────────────────────────────────────────
CID_PREFIX = "herdbuch"

CID_JOIN     = f"{CID_PREFIX}:join"      # herdbuch:join:<slug>
CID_OTHER    = f"{CID_PREFIX}:other"     # herdbuch:other
CID_APPROVE  = f"{CID_PREFIX}:approve"   # herdbuch:approve:<app_id>
CID_REJECT   = f"{CID_PREFIX}:reject"    # herdbuch:reject:<app_id>
CID_WITHDRAW = f"{CID_PREFIX}:withdraw"  # herdbuch:withdraw:<app_id>
CID_LEAVE    = f"{CID_PREFIX}:leave"     # herdbuch:leave:<slug>

# ─────────────────────────────────────────────────────────────────────────────
# EMBED-FARBEN  (verbindliche Palette aus der Embed-Spec)
# ─────────────────────────────────────────────────────────────────────────────
COLOR_BRAND   = 0x3BA55C   # Grün   – neuer Antrag · genehmigt · aufgenommen
COLOR_DANGER  = 0xED4245   # Rot    – abgelehnt · entfernt · Fehler
COLOR_REQUEST = 0xB08D57   # Bronze – neue Gilde angefragt
COLOR_NEUTRAL = 0x89939F   # Slate  – freiwillig ausgetreten
COLOR_INFO    = 0x5865F2   # Blurple – Admin-/Info-Embeds

# Status-Marker für die Antrags-/Log-Embeds
STATUS: Dict[str, str] = {
    "open":     "🟡 Offen",
    "approved": "✅ Genehmigt",
    "rejected": "❌ Abgelehnt",
    "added":    "✅ Aufgenommen",
    "removed":  "❌ Entfernt",
    "left":     "Ausgetreten",
}

# Antrags-Status (Persistenz)
ST_PENDING   = "PENDING"
ST_APPROVED  = "APPROVED"
ST_REJECTED  = "REJECTED"
ST_WITHDRAWN = "WITHDRAWN"

# Unsichtbarer Abstands-Trick (Soft-Hyphen) für vertikale Luft in Embeds.
PAD = "\n­ "

# ─────────────────────────────────────────────────────────────────────────────
# AUTOMATISCH ERSTELLTE OBJEKTE  (/gildenadmin setup & erstellen)
# ─────────────────────────────────────────────────────────────────────────────
SETUP_CATEGORY_NAME = "== Gilden Administration =="
SETUP_ROSTER_NAME   = "gilden-rollen"      # öffentlicher Channel mit dem Button-Panel
SETUP_CENTRAL_NAME  = "gilden-anfragen"    # privater Channel für „Andere Gilde"-Anfragen
ORGA_ROLE_SUFFIX    = "Orga"               # Name der Orga-Rolle: "<Gilde> Orga"
ADMIN_CHANNEL_SUFFIX = "orga"              # Admin-Channel: "<slug>｜orga"

# Obergrenze gespeicherter Audit-Einträge pro Server (verhindert unbegrenztes Wachstum).
AUDIT_LIMIT = 500

# Discord-Grenzen
MAX_CHANNEL_NAME = 100
MAX_ROLE_NAME    = 100
MAX_SLUG_LEN     = 40


def slugify(name: str) -> str:
    """Macht aus einem Gildennamen einen kleingeschriebenen URL-tauglichen Slug."""
    s = (name or "").lower().strip()
    s = re.sub(r"[^a-z0-9]+", "-", s).strip("-")
    return s[:MAX_SLUG_LEN] or "gilde"


def make_slug(name: str, existing: Iterable[str]) -> str:
    """Eindeutiger Slug: hängt bei Kollision -2, -3 … an."""
    existing = set(existing)
    base = slugify(name)
    slug = base
    i = 2
    while slug in existing:
        slug = f"{base}-{i}"
        i += 1
    return slug


def admin_channel_name(name: str) -> str:
    """Channelname des privaten Orga-Channels einer Gilde: '<slug>｜orga'."""
    return f"{slugify(name)}｜{ADMIN_CHANNEL_SUFFIX}"[:MAX_CHANNEL_NAME]


def make_app_id(slug: str, seq: int) -> str:
    """Menschenlesbare, eindeutige Antrags-ID, z. B. 'kuhmuhnisten-000124'."""
    return f"{slug}-{seq:06d}"
