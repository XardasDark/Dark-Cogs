"""
storage.py – Config-Wrapper für KuhHilfe

Kapselt den gesamten Zugriff auf die Red-Config: Guild-Einstellungen, den
Kategorie-Baum und die laufenden Ticket-Datensätze.

Der Kategorie-Baum ist max. zwei Ebenen tief:

    categories = {
        "<key>": {                      # Knoten auf oberster Ebene
            "key", "name", "emoji", "description",
            "kind": "group" | "leaf",
            # kind == "group":
            "children": { "<key>": { …leaf… } },
            # kind == "leaf":
            "role_ids": [], "user_ids": [], "fields": [ … ],
        }
    }

Eine *Leaf* (finale Kategorie) trägt die zuständigen Rollen/User und die
Wizard-Felder. Eine *Group* bündelt nur Leaves. Leaves werden über einen Pfad
adressiert: top-level "key" oder verschachtelt "gruppe/kategorie".

Ticket-IDs sind zufällig (Buchstaben + Zahlen), damit sie die tatsächliche
Anzahl der Tickets nicht verraten.
"""

import random
import re
import time
from typing import Any, Dict, List, Optional, Tuple

import discord
from redbot.core import Config

from .constants import (
    TICKET_ID_ALPHABET, TICKET_ID_LENGTH, KEY_MAX_LENGTH,
    CATEGORY_PATH_SEP, KIND_GROUP, KIND_LEAF,
    DEFAULT_FIELDS, MAX_FIELDS, STATUS_CLOSED,
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

    async def set_confirm_delete(self, guild: discord.Guild, seconds: int) -> None:
        await self.config.guild(guild).confirm_delete_seconds.set(max(0, seconds))

    # ─────────────────────────────────────────────────────────────────────────
    # KATEGORIE-BAUM – Helfer
    # ─────────────────────────────────────────────────────────────────────────
    @classmethod
    def normalize_key(cls, name: str) -> str:
        """Wunschnamen → sauberer Key (klein, kebab, ohne Umlaute, ohne '/')."""
        key = name.strip().lower()
        for src, dst in _UMLAUTS.items():
            key = key.replace(src, dst)
        key = re.sub(r"[^a-z0-9]+", "-", key).strip("-")
        return key[:KEY_MAX_LENGTH]

    @staticmethod
    def is_group(node: Dict[str, Any]) -> bool:
        return node.get("kind") == KIND_GROUP

    @staticmethod
    def _resolve(cats: Dict[str, Any], path: str) -> Optional[Dict[str, Any]]:
        """Löst einen Pfad ('key' oder 'gruppe/kategorie') im Baum auf."""
        parts = path.split(CATEGORY_PATH_SEP, 1)
        top = cats.get(parts[0])
        if top is None:
            return None
        if len(parts) == 1:
            return top
        return (top.get("children") or {}).get(parts[1])

    # ── Lesen ────────────────────────────────────────────────────────────────
    async def list_categories(self, guild: discord.Guild) -> Dict[str, Dict[str, Any]]:
        """Oberste Ebene des Baums (Gruppen + Direkt-Leaves)."""
        return await self.config.guild(guild).categories()

    async def get_node(self, guild: discord.Guild, path: str) -> Optional[Dict[str, Any]]:
        cats = await self.config.guild(guild).categories()
        return self._resolve(cats, path)

    async def resolve_leaf(self, guild: discord.Guild,
                          path: str) -> Tuple[Optional[Dict[str, Any]], Optional[str]]:
        """Gibt (leaf, Anzeige-Label) für einen Pfad zurück; (None, None) wenn keine Leaf."""
        cats = await self.config.guild(guild).categories()
        parts = path.split(CATEGORY_PATH_SEP, 1)
        top = cats.get(parts[0])
        if top is None:
            return None, None
        if len(parts) == 1:
            if self.is_group(top):
                return None, None
            return top, top.get("name")
        child = (top.get("children") or {}).get(parts[1])
        if child is None:
            return None, None
        return child, f"{top.get('name')} › {child.get('name')}"

    async def iter_leaves(self, guild: discord.Guild
                         ) -> List[Tuple[str, Dict[str, Any], Optional[Dict[str, Any]]]]:
        """Alle Leaves als (pfad, leaf, parent_group_or_None)."""
        cats = await self.config.guild(guild).categories()
        out: List[Tuple[str, Dict[str, Any], Optional[Dict[str, Any]]]] = []
        for key, node in cats.items():
            if self.is_group(node):
                for ck, child in (node.get("children") or {}).items():
                    out.append((f"{key}{CATEGORY_PATH_SEP}{ck}", child, node))
            else:
                out.append((key, node, None))
        return out

    async def iter_groups(self, guild: discord.Guild) -> List[Tuple[str, Dict[str, Any]]]:
        cats = await self.config.guild(guild).categories()
        return [(k, n) for k, n in cats.items() if self.is_group(n)]

    # ── Schreiben: Gruppen ────────────────────────────────────────────────────
    async def add_group(self, guild: discord.Guild, name: str, emoji: Optional[str],
                       description: Optional[str]) -> Optional[Dict[str, Any]]:
        key = self.normalize_key(name)
        if not key:
            return None
        async with self.config.guild(guild).categories() as cats:
            if key in cats:
                return None
            node = {
                "key": key,
                "name": name.strip(),
                "emoji": (emoji or "").strip() or None,
                "description": (description or "").strip() or None,
                "kind": KIND_GROUP,
                "children": {},
            }
            cats[key] = node
            return node

    # ── Schreiben: Leaves (finale Kategorien) ─────────────────────────────────
    async def add_leaf(self, guild: discord.Guild, name: str, group_key: Optional[str],
                      emoji: Optional[str], description: Optional[str]
                      ) -> Tuple[Optional[Dict[str, Any]], str]:
        """
        Legt eine finale Kategorie an – top-level (group_key leer) oder unter einer
        Gruppe. Rückgabe: (leaf, pfad) bei Erfolg, sonst (None, fehlertext).
        """
        key = self.normalize_key(name)
        if not key:
            return None, "Name ungültig. Bitte Buchstaben/Zahlen verwenden."

        leaf = {
            "key": key,
            "name": name.strip(),
            "emoji": (emoji or "").strip() or None,
            "description": (description or "").strip() or None,
            "kind": KIND_LEAF,
            "role_ids": [],
            "user_ids": [],
            "fields": [dict(f) for f in DEFAULT_FIELDS],
        }

        async with self.config.guild(guild).categories() as cats:
            if group_key:
                grp = cats.get(group_key)
                if grp is None:
                    return None, f"Gruppe `{group_key}` nicht gefunden."
                if not self.is_group(grp):
                    return None, f"`{group_key}` ist eine Direkt-Kategorie, keine Gruppe."
                children = grp.setdefault("children", {})
                if key in children:
                    return None, f"In der Gruppe `{group_key}` existiert bereits `{key}`."
                children[key] = leaf
                return leaf, f"{group_key}{CATEGORY_PATH_SEP}{key}"

            if key in cats:
                return None, f"Eine Kategorie/Gruppe mit Key `{key}` existiert bereits."
            cats[key] = leaf
            return leaf, key

    async def edit_node(self, guild: discord.Guild, path: str, name: Optional[str],
                       emoji: Optional[str], description: Optional[str]) -> Optional[Dict[str, Any]]:
        """Ändert Anzeige-Felder (Name/Emoji/Beschreibung) eines Knotens; Key bleibt."""
        async with self.config.guild(guild).categories() as cats:
            node = self._resolve(cats, path)
            if node is None:
                return None
            if name and name.strip():
                node["name"] = name.strip()
            node["emoji"] = (emoji or "").strip() or None
            node["description"] = (description or "").strip() or None
            return node

    async def set_roles(self, guild: discord.Guild, path: str,
                       role_ids: List[int]) -> Optional[Dict[str, Any]]:
        async with self.config.guild(guild).categories() as cats:
            leaf = self._resolve(cats, path)
            if leaf is None or self.is_group(leaf):
                return None
            leaf["role_ids"] = list(dict.fromkeys(int(i) for i in role_ids))
            return leaf

    async def set_users(self, guild: discord.Guild, path: str,
                       user_ids: List[int]) -> Optional[Dict[str, Any]]:
        async with self.config.guild(guild).categories() as cats:
            leaf = self._resolve(cats, path)
            if leaf is None or self.is_group(leaf):
                return None
            leaf["user_ids"] = list(dict.fromkeys(int(i) for i in user_ids))
            return leaf

    async def remove_node(self, guild: discord.Guild, path: str) -> bool:
        """Entfernt eine Gruppe (inkl. Kinder) oder eine Leaf am Pfad."""
        async with self.config.guild(guild).categories() as cats:
            parts = path.split(CATEGORY_PATH_SEP, 1)
            if len(parts) == 1:
                if parts[0] in cats:
                    del cats[parts[0]]
                    return True
                return False
            top = cats.get(parts[0])
            if top is None:
                return False
            children = top.get("children") or {}
            if parts[1] in children:
                del children[parts[1]]
                return True
            return False

    # ── Rollen / User zuweisen (an einer Leaf) ────────────────────────────────
    async def toggle_role(self, guild: discord.Guild, path: str, role_id: int,
                         add: bool) -> Optional[bool]:
        """True=hinzugefügt, False=entfernt, None=keine Leaf am Pfad."""
        async with self.config.guild(guild).categories() as cats:
            leaf = self._resolve(cats, path)
            if leaf is None or self.is_group(leaf):
                return None
            ids = leaf.setdefault("role_ids", [])
            if add:
                if role_id not in ids:
                    ids.append(role_id)
                return True
            if role_id in ids:
                ids.remove(role_id)
            return False

    async def toggle_user(self, guild: discord.Guild, path: str, user_id: int,
                         add: bool) -> Optional[bool]:
        async with self.config.guild(guild).categories() as cats:
            leaf = self._resolve(cats, path)
            if leaf is None or self.is_group(leaf):
                return None
            ids = leaf.setdefault("user_ids", [])
            if add:
                if user_id not in ids:
                    ids.append(user_id)
                return True
            if user_id in ids:
                ids.remove(user_id)
            return False

    # ── Modal-Felder (an einer Leaf) ──────────────────────────────────────────
    async def add_field(self, guild: discord.Guild, path: str, label: str, style: str,
                       required: bool, placeholder: Optional[str],
                       max_length: int) -> Optional[str]:
        """Fügt ein Feld hinzu. Rückgabe: None=OK, sonst Fehlertext."""
        async with self.config.guild(guild).categories() as cats:
            leaf = self._resolve(cats, path)
            if leaf is None or self.is_group(leaf):
                return "Kategorie nicht gefunden (Felder gibt es nur an finalen Kategorien)."
            fields = leaf.setdefault("fields", [])
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

    async def set_fields(self, guild: discord.Guild, path: str,
                        fields: List[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
        """Setzt die Felder einer Kategorie komplett (z. B. aus einer Vorlage)."""
        async with self.config.guild(guild).categories() as cats:
            leaf = self._resolve(cats, path)
            if leaf is None or self.is_group(leaf):
                return None
            cleaned = []
            for f in fields[:MAX_FIELDS]:
                cleaned.append({
                    "label": str(f.get("label", "Feld")).strip()[:45] or "Feld",
                    "style": f.get("style") if f.get("style") in ("short", "paragraph") else "short",
                    "required": bool(f.get("required", True)),
                    "placeholder": (f.get("placeholder") or "").strip()[:100] or None,
                    "max_length": max(1, min(int(f.get("max_length", 1000)), 4000)),
                })
            leaf["fields"] = cleaned
            return leaf

    async def remove_field(self, guild: discord.Guild, path: str,
                         index: int) -> Optional[Dict[str, Any]]:
        """Entfernt das Feld an 1-basierter Position; gibt es zurück oder None."""
        async with self.config.guild(guild).categories() as cats:
            leaf = self._resolve(cats, path)
            if leaf is None or self.is_group(leaf):
                return None
            fields = leaf.setdefault("fields", [])
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
        while True:
            tid = "".join(random.choice(TICKET_ID_ALPHABET) for _ in range(TICKET_ID_LENGTH + 1))
            if tid not in existing:
                return tid

    async def create_ticket(self, guild: discord.Guild, ticket_id: str, channel_id: int,
                           opener_id: int, category_path: str, category_label: str,
                           answers: Dict[str, str]) -> Dict[str, Any]:
        ticket = {
            "id": ticket_id,
            "channel_id": channel_id,
            "opener_id": opener_id,
            "category_key": category_path,
            "category_label": category_label,
            "status": "open",
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
            if t.get("opener_id") == user_id and t.get("status") != STATUS_CLOSED
        )
