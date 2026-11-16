"""
storage.py – Config-Wrapper für KuhHilfe

Kapselt den gesamten Zugriff auf die Red-Config: Guild-Einstellungen,
Kategorien (inkl. zugewiesener Rollen/User und konfigurierbarer Modal-Felder)
sowie die laufenden Ticket-Datensätze. Ticket-IDs sind zufällig (Buchstaben +
Zahlen), damit sie die tatsächliche Anzahl der Tickets nicht verraten.
"""

import random
import re
import time
from typing import Any, Dict, List, Optional

import discord
from redbot.core import Config

from .constants import (
    TICKET_ID_ALPHABET, TICKET_ID_LENGTH, KEY_MAX_LENGTH,
    DEFAULT_FIELDS, MAX_FIELDS, STATUS_OPEN,
)

_UMLAUTS = {"ä": "ae", "ö": "oe", "ü": "ue", "ß": "ss"}


class KuhHilfeStore:
    """Dünner, async Wrapper um die Red-Config (eine Instanz pro Cog)."""

    def __init__(self, config: Config):
        self.config = config

    # ─────────────────────────────────────────────────────────────────────────
    # GUILD-EINSTELLUNGEN
    # ─────────────────────────────────────────────────────────────────────────
    async def get_settings(self, guild: discord.Guild) -> Dict[str, Any]:
        return await self.config.guild(guild).all()

    async def set_panel(self, guild: discord.Guild, channel_id: Optional[int],
                        message_id: Optional[int]) -> None:
        await self.config.guild(guild).panel_channel_id.set(channel_id)
        await self.config.guild(guild).panel_message_id.set(message_id)

    async def set_ticket_category(self, guild: discord.Guild, category_id: Optional[int]) -> None:
        await self.config.guild(guild).ticket_category_id.set(category_id)

    async def set_transcript_channel(self, guild: discord.Guild, channel_id: Optional[int]) -> None:
        await self.config.guild(guild).transcript_channel_id.set(channel_id)

    async def set_max_open(self, guild: discord.Guild, value: int) -> None:
        await self.config.guild(guild).max_open_per_user.set(max(0, value))

    async def set_delete_delay(self, guild: discord.Guild, seconds: int) -> None:
        await self.config.guild(guild).delete_delay_seconds.set(max(0, seconds))

    # ─────────────────────────────────────────────────────────────────────────
    # KATEGORIEN
    # ─────────────────────────────────────────────────────────────────────────
    @classmethod
    def normalize_key(cls, name: str) -> str:
        """Wunschnamen → sauberer Key (klein, kebab, ohne Umlaute)."""
        key = name.strip().lower()
        for src, dst in _UMLAUTS.items():
            key = key.replace(src, dst)
        key = re.sub(r"[^a-z0-9]+", "-", key).strip("-")
        return key[:KEY_MAX_LENGTH]

    async def list_categories(self, guild: discord.Guild) -> Dict[str, Dict[str, Any]]:
        return await self.config.guild(guild).categories()

    async def get_category(self, guild: discord.Guild, key: str) -> Optional[Dict[str, Any]]:
        cats = await self.config.guild(guild).categories()
        return cats.get(key)

    async def add_category(self, guild: discord.Guild, name: str, emoji: Optional[str],
                          description: Optional[str]) -> Optional[Dict[str, Any]]:
        """Legt eine Kategorie an; gibt sie zurück oder None bei Key-Kollision."""
        key = self.normalize_key(name)
        if not key:
            return None
        async with self.config.guild(guild).categories() as cats:
            if key in cats:
                return None
            cat = {
                "key": key,
                "name": name.strip(),
                "emoji": (emoji or "").strip() or None,
                "description": (description or "").strip() or None,
                "role_ids": [],
                "user_ids": [],
                "fields": [dict(f) for f in DEFAULT_FIELDS],
            }
            cats[key] = cat
            return cat

    async def remove_category(self, guild: discord.Guild, key: str) -> bool:
        async with self.config.guild(guild).categories() as cats:
            if key in cats:
                del cats[key]
                return True
            return False

    async def update_category(self, guild: discord.Guild, key: str,
                             **changes: Any) -> Optional[Dict[str, Any]]:
        async with self.config.guild(guild).categories() as cats:
            cat = cats.get(key)
            if not cat:
                return None
            cat.update(changes)
            return cat

    # ── Rollen / User zuweisen ───────────────────────────────────────────────
    async def toggle_role(self, guild: discord.Guild, key: str, role_id: int,
                         add: bool) -> Optional[bool]:
        """True=hinzugefügt, False=entfernt, None=Kategorie fehlt."""
        async with self.config.guild(guild).categories() as cats:
            cat = cats.get(key)
            if not cat:
                return None
            ids = cat.setdefault("role_ids", [])
            if add:
                if role_id not in ids:
                    ids.append(role_id)
                return True
            if role_id in ids:
                ids.remove(role_id)
            return False

    async def toggle_user(self, guild: discord.Guild, key: str, user_id: int,
                         add: bool) -> Optional[bool]:
        async with self.config.guild(guild).categories() as cats:
            cat = cats.get(key)
            if not cat:
                return None
            ids = cat.setdefault("user_ids", [])
            if add:
                if user_id not in ids:
                    ids.append(user_id)
                return True
            if user_id in ids:
                ids.remove(user_id)
            return False

    # ── Modal-Felder ─────────────────────────────────────────────────────────
    async def add_field(self, guild: discord.Guild, key: str, label: str, style: str,
                       required: bool, placeholder: Optional[str],
                       max_length: int) -> Optional[str]:
        """Fügt ein Feld hinzu. Rückgabe: None=OK, sonst Fehlertext."""
        async with self.config.guild(guild).categories() as cats:
            cat = cats.get(key)
            if not cat:
                return "Kategorie nicht gefunden."
            fields = cat.setdefault("fields", [])
            if len(fields) >= MAX_FIELDS:
                return f"Maximal {MAX_FIELDS} Felder pro Kategorie (Discord-Limit)."
            fields.append({
                "label": label.strip()[:45],
                "style": style if style in ("short", "paragraph") else "short",
                "required": bool(required),
                "placeholder": (placeholder or "").strip()[:100] or None,
                "max_length": max(1, min(int(max_length), 4000)),
            })
            return None

    async def remove_field(self, guild: discord.Guild, key: str, index: int) -> Optional[Dict[str, Any]]:
        """Entfernt das Feld an 1-basierter Position; gibt es zurück oder None."""
        async with self.config.guild(guild).categories() as cats:
            cat = cats.get(key)
            if not cat:
                return None
            fields = cat.setdefault("fields", [])
            if 1 <= index <= len(fields):
                return fields.pop(index - 1)
            return None

    # ─────────────────────────────────────────────────────────────────────────
    # TICKETS
    # ─────────────────────────────────────────────────────────────────────────
    async def generate_ticket_id(self, guild: discord.Guild) -> str:
        """Zufällige, in der Guild eindeutige Ticket-ID (verschleiert die Anzahl)."""
        tickets = await self.config.guild(guild).tickets()
        existing = {t.get("id") for t in tickets.values()}
        for _ in range(50):
            tid = "".join(random.choice(TICKET_ID_ALPHABET) for _ in range(TICKET_ID_LENGTH))
            if tid not in existing:
                return tid
        # Fallback: eine Stelle länger
        while True:
            tid = "".join(random.choice(TICKET_ID_ALPHABET) for _ in range(TICKET_ID_LENGTH + 1))
            if tid not in existing:
                return tid

    async def create_ticket(self, guild: discord.Guild, ticket_id: str, channel_id: int,
                           opener_id: int, category_key: str,
                           answers: Dict[str, str]) -> Dict[str, Any]:
        ticket = {
            "id": ticket_id,
            "channel_id": channel_id,
            "opener_id": opener_id,
            "category_key": category_key,
            "status": STATUS_OPEN,
            "claimed_by": None,
            "created_at": int(time.time()),
            "closed_at": None,
            "closed_by": None,
            "answers": answers,
        }
        async with self.config.guild(guild).tickets() as tickets:
            tickets[str(channel_id)] = ticket
        return ticket

    async def get_ticket(self, guild: discord.Guild, channel_id: int) -> Optional[Dict[str, Any]]:
        tickets = await self.config.guild(guild).tickets()
        return tickets.get(str(channel_id))

    async def update_ticket(self, guild: discord.Guild, channel_id: int,
                           **changes: Any) -> Optional[Dict[str, Any]]:
        async with self.config.guild(guild).tickets() as tickets:
            ticket = tickets.get(str(channel_id))
            if not ticket:
                return None
            ticket.update(changes)
            return ticket

    async def delete_ticket(self, guild: discord.Guild, channel_id: int) -> None:
        async with self.config.guild(guild).tickets() as tickets:
            tickets.pop(str(channel_id), None)

    async def all_tickets(self, guild: discord.Guild) -> Dict[str, Dict[str, Any]]:
        return await self.config.guild(guild).tickets()

    async def count_open_for_user(self, guild: discord.Guild, user_id: int) -> int:
        tickets = await self.config.guild(guild).tickets()
        return sum(
            1 for t in tickets.values()
            if t.get("opener_id") == user_id and t.get("status") != "closed"
        )
