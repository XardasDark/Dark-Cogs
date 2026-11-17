"""
storage.py – Persistenz-Schicht über Reds Config

Guild-Daten:
  clans            = {slug: {name, emoji, member_role_id, orga_role_id,
                             admin_channel_id, active, app_seq, created_at,
                             members: {user_id(str): {since, approved_by, via}}}}
  applications     = {app_id: {clan, user_id, character, message, status,
                               created_at, handled_by, handled_at,
                               channel_id, message_id, reason}}
  guild_requests   = {req_id: {user_id, guild_name, leader, message,
                               screenshot_url, status, created_at}}
  admin_category_id = int | None      # Kategorie "== Gilden Administration =="
  roster_channel_id = int | None      # öffentlicher Channel mit dem Button-Panel
  roster_message_ids = [int, ...]     # Panel-Nachricht(en)
  central_channel_id = int | None     # Ziel für "Andere Gilde"-Anfragen
  audit            = [ {ts, clan, action, target_id, actor_id, reason}, ... ]

Die Quelle der Wahrheit für "ist Mitglied" ist die Discord-Rolle selbst; der
members-Dict ist nur ein Metadaten-Spiegel (wer/wann bestätigt).
"""

from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

import discord

from . import constants as C


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class HerdbuchStore:
    """Dünne, async Zugriffsschicht auf Reds Config."""

    def __init__(self, config):
        self.config = config

    # ── Gilden (Clans) ───────────────────────────────────────────────────────

    async def all_clans(self, guild: discord.Guild) -> Dict[str, Dict[str, Any]]:
        return await self.config.guild(guild).clans()

    async def get_clan(self, guild: discord.Guild, slug: str) -> Optional[Dict[str, Any]]:
        clans = await self.config.guild(guild).clans()
        return clans.get(slug)

    async def clan_by_channel(self, guild: discord.Guild,
                              channel_id: int) -> Tuple[Optional[str], Optional[Dict[str, Any]]]:
        """Findet die Gilde, deren Orga-Channel diese ID hat."""
        clans = await self.config.guild(guild).clans()
        for slug, clan in clans.items():
            if clan.get("admin_channel_id") == channel_id:
                return slug, clan
        return None, None

    async def find_by_name(self, guild: discord.Guild, name: str) -> Optional[str]:
        clans = await self.config.guild(guild).clans()
        name = name.lower().strip()
        for slug, clan in clans.items():
            if clan.get("name", "").lower() == name:
                return slug
        return None

    async def role_in_use(self, guild: discord.Guild, role_id: int) -> Optional[str]:
        """Slug der Gilde, die diese Rolle (als Mitglieds- oder Orga-Rolle) nutzt."""
        clans = await self.config.guild(guild).clans()
        for slug, clan in clans.items():
            if role_id in (clan.get("member_role_id"), clan.get("orga_role_id")):
                return slug
        return None

    async def channel_in_use(self, guild: discord.Guild, channel_id: int) -> Optional[str]:
        slug, _ = await self.clan_by_channel(guild, channel_id)
        return slug

    async def add_clan(self, guild: discord.Guild, slug: str, *, name: str,
                       member_role_id: int, orga_role_id: int, admin_channel_id: int,
                       emoji: Optional[str] = None) -> None:
        async with self.config.guild(guild).clans() as clans:
            clans[slug] = {
                "name": name,
                "emoji": emoji,
                "member_role_id": member_role_id,
                "orga_role_id": orga_role_id,
                "admin_channel_id": admin_channel_id,
                "active": True,
                "app_seq": 0,
                "created_at": _now(),
                "members": {},
            }

    async def update_clan(self, guild: discord.Guild, slug: str, **changes: Any) -> None:
        async with self.config.guild(guild).clans() as clans:
            clan = clans.get(slug)
            if clan is not None:
                clan.update({k: v for k, v in changes.items() if v is not None})

    async def set_active(self, guild: discord.Guild, slug: str, active: bool) -> None:
        async with self.config.guild(guild).clans() as clans:
            clan = clans.get(slug)
            if clan is not None:
                clan["active"] = active

    async def next_app_seq(self, guild: discord.Guild, slug: str) -> Optional[int]:
        """Erhöht den Antrags-Zähler der Gilde atomar und gibt die neue Nummer zurück."""
        async with self.config.guild(guild).clans() as clans:
            clan = clans.get(slug)
            if clan is None:
                return None
            seq = int(clan.get("app_seq", 0)) + 1
            clan["app_seq"] = seq
            return seq

    # ── Mitglieds-Metadaten (Spiegel; Live-Wahrheit = Rolle) ──────────────────

    async def set_membership(self, guild: discord.Guild, slug: str, user_id: int, *,
                             approved_by: Optional[int], via: str) -> None:
        async with self.config.guild(guild).clans() as clans:
            clan = clans.get(slug)
            if clan is not None:
                clan.setdefault("members", {})[str(user_id)] = {
                    "since": _now(), "approved_by": approved_by, "via": via,
                }

    async def clear_membership(self, guild: discord.Guild, slug: str, user_id: int) -> None:
        async with self.config.guild(guild).clans() as clans:
            clan = clans.get(slug)
            if clan is not None:
                clan.get("members", {}).pop(str(user_id), None)

    # ── Setup-Channels & Kategorie ────────────────────────────────────────────

    async def get_admin_category(self, guild: discord.Guild) -> Optional[int]:
        return await self.config.guild(guild).admin_category_id()

    async def set_admin_category(self, guild: discord.Guild, category_id: Optional[int]) -> None:
        await self.config.guild(guild).admin_category_id.set(category_id)

    async def get_roster(self, guild: discord.Guild) -> Tuple[Optional[int], List[int]]:
        g = self.config.guild(guild)
        return await g.roster_channel_id(), await g.roster_message_ids()

    async def set_roster_channel(self, guild: discord.Guild, channel_id: Optional[int]) -> None:
        await self.config.guild(guild).roster_channel_id.set(channel_id)

    async def set_roster_messages(self, guild: discord.Guild, message_ids: List[int]) -> None:
        await self.config.guild(guild).roster_message_ids.set(message_ids)

    async def get_central_channel(self, guild: discord.Guild) -> Optional[int]:
        return await self.config.guild(guild).central_channel_id()

    async def set_central_channel(self, guild: discord.Guild, channel_id: Optional[int]) -> None:
        await self.config.guild(guild).central_channel_id.set(channel_id)

    # ── Audit-Log ─────────────────────────────────────────────────────────────

    async def add_audit(self, guild: discord.Guild, *, clan: Optional[str], action: str,
                        target_id: Optional[int], actor_id: Optional[int],
                        reason: Optional[str] = None) -> None:
        entry = {
            "ts": _now(), "clan": clan, "action": action,
            "target_id": target_id, "actor_id": actor_id, "reason": reason,
        }
        async with self.config.guild(guild).audit() as audit:
            audit.append(entry)
            if len(audit) > C.AUDIT_LIMIT:
                del audit[:-C.AUDIT_LIMIT]

    async def get_audit(self, guild: discord.Guild) -> List[Dict[str, Any]]:
        return await self.config.guild(guild).audit()
