"""
storage.py – Persistenz-Schicht über Reds Config

Guild-Daten:
  generator_ids        = [voice_channel_id, ...]   # "Join to Create"-Channel
  category_id          = int | None                # Kategorie für neue Channel
  interface_channel_id = int | None                # Textkanal mit dem Panel
  interface_message_id = int | None                # Panel-Nachricht
  defaults             = {name, limit, bitrate, region, locked, hidden}
  temp_channels        = {channel_id(str): {owner_id, created_at}}

Member-Daten (pro Server & Mitglied):
  profile = {name, limit, bitrate, region, locked, hidden, permit[], reject[]} | None
"""

from typing import Any, Dict, List, Optional

import discord

from . import constants as C


def default_settings() -> Dict[str, Any]:
    """Standard-Einstellungen für neue Channel (Guild-Defaults)."""
    return {
        "name":    C.DEFAULT_NAME_TEMPLATE,
        "limit":   C.DEFAULT_LIMIT,
        "bitrate": C.DEFAULT_BITRATE,
        "region":  C.DEFAULT_REGION,
        "locked":  C.DEFAULT_LOCKED,
        "hidden":  C.DEFAULT_HIDDEN,
    }


def empty_profile() -> Dict[str, Any]:
    """Frisches Mitglieds-Profil auf Basis der Standardwerte."""
    prof = default_settings()
    prof["permit"] = []
    prof["reject"] = []
    return prof


class TempVoiceStore:
    """Dünne, async Zugriffsschicht auf Reds Config."""

    def __init__(self, config):
        self.config = config

    # ── Generator-Channel ─────────────────────────────────────────────────────

    async def get_generator_ids(self, guild: discord.Guild) -> List[int]:
        return list(await self.config.guild(guild).generator_ids())

    async def add_generator_id(self, guild: discord.Guild, channel_id: int) -> None:
        async with self.config.guild(guild).generator_ids() as ids:
            if channel_id not in ids:
                ids.append(channel_id)

    async def remove_generator_id(self, guild: discord.Guild, channel_id: int) -> None:
        async with self.config.guild(guild).generator_ids() as ids:
            if channel_id in ids:
                ids.remove(channel_id)

    async def is_generator(self, guild: discord.Guild, channel_id: int) -> bool:
        return channel_id in await self.config.guild(guild).generator_ids()

    # ── Kategorie & Interface ──────────────────────────────────────────────────

    async def get_category_id(self, guild: discord.Guild) -> Optional[int]:
        return await self.config.guild(guild).category_id()

    async def set_category_id(self, guild: discord.Guild, category_id: Optional[int]) -> None:
        await self.config.guild(guild).category_id.set(category_id)

    async def get_interface(self, guild: discord.Guild) -> tuple:
        g = self.config.guild(guild)
        return await g.interface_channel_id(), await g.interface_message_id()

    async def set_interface(self, guild: discord.Guild, channel_id: Optional[int],
                            message_id: Optional[int]) -> None:
        g = self.config.guild(guild)
        await g.interface_channel_id.set(channel_id)
        await g.interface_message_id.set(message_id)

    # ── Defaults ───────────────────────────────────────────────────────────────

    async def get_defaults(self, guild: discord.Guild) -> Dict[str, Any]:
        stored = await self.config.guild(guild).channel_defaults()
        base = default_settings()
        base.update(stored or {})
        return base

    async def update_defaults(self, guild: discord.Guild, **changes: Any) -> None:
        async with self.config.guild(guild).channel_defaults() as defaults:
            defaults.update(changes)

    # ── Temporäre Channel ───────────────────────────────────────────────────────

    async def all_temp_channels(self, guild: discord.Guild) -> Dict[str, Dict[str, Any]]:
        return await self.config.guild(guild).temp_channels()

    async def get_temp_channel(self, guild: discord.Guild, channel_id: int) -> Optional[Dict[str, Any]]:
        temps = await self.config.guild(guild).temp_channels()
        return temps.get(str(channel_id))

    async def is_temp_channel(self, guild: discord.Guild, channel_id: int) -> bool:
        temps = await self.config.guild(guild).temp_channels()
        return str(channel_id) in temps

    async def add_temp_channel(self, guild: discord.Guild, channel_id: int,
                               owner_id: int, created_at: str) -> None:
        async with self.config.guild(guild).temp_channels() as temps:
            temps[str(channel_id)] = {"owner_id": owner_id, "created_at": created_at}

    async def remove_temp_channel(self, guild: discord.Guild, channel_id: int) -> None:
        async with self.config.guild(guild).temp_channels() as temps:
            temps.pop(str(channel_id), None)

    async def set_owner(self, guild: discord.Guild, channel_id: int, owner_id: int) -> None:
        async with self.config.guild(guild).temp_channels() as temps:
            rec = temps.get(str(channel_id))
            if rec is not None:
                rec["owner_id"] = owner_id

    async def get_owner_id(self, guild: discord.Guild, channel_id: int) -> Optional[int]:
        rec = await self.get_temp_channel(guild, channel_id)
        return rec.get("owner_id") if rec else None

    # ── Gäste (per Anklopfen reingelassen, Zugang bis zum Verlassen) ────────────

    async def get_guests(self, guild: discord.Guild, channel_id: int) -> List[int]:
        rec = await self.get_temp_channel(guild, channel_id)
        return list(rec.get("guests", [])) if rec else []

    async def add_guest(self, guild: discord.Guild, channel_id: int, user_id: int) -> None:
        async with self.config.guild(guild).temp_channels() as temps:
            rec = temps.get(str(channel_id))
            if rec is not None:
                guests = rec.setdefault("guests", [])
                if user_id not in guests:
                    guests.append(user_id)

    async def remove_guest(self, guild: discord.Guild, channel_id: int, user_id: int) -> None:
        async with self.config.guild(guild).temp_channels() as temps:
            rec = temps.get(str(channel_id))
            if rec is not None and user_id in rec.get("guests", []):
                rec["guests"].remove(user_id)

    # ── Warteraum (optionaler angehängter Voice-Channel) ────────────────────────

    async def get_waiting_room_id(self, guild: discord.Guild, channel_id: int) -> Optional[int]:
        rec = await self.get_temp_channel(guild, channel_id)
        return rec.get("waiting_room_id") if rec else None

    async def set_waiting_room(self, guild: discord.Guild, channel_id: int,
                               waiting_room_id: Optional[int]) -> None:
        async with self.config.guild(guild).temp_channels() as temps:
            rec = temps.get(str(channel_id))
            if rec is not None:
                if waiting_room_id is None:
                    rec.pop("waiting_room_id", None)
                else:
                    rec["waiting_room_id"] = waiting_room_id

    async def find_main_by_waiting(self, guild: discord.Guild,
                                   waiting_room_id: int) -> tuple:
        """Sucht den Haupt-Channel, zu dem ein Warteraum gehört. (main_id, owner_id) | (None, None)."""
        temps = await self.config.guild(guild).temp_channels()
        for cid, rec in temps.items():
            if rec.get("waiting_room_id") == waiting_room_id:
                return int(cid), rec.get("owner_id")
        return None, None

    # ── Mitglieds-Profil ─────────────────────────────────────────────────────────

    async def get_profile(self, member: discord.Member) -> Optional[Dict[str, Any]]:
        return await self.config.member(member).profile()

    async def save_profile(self, member: discord.Member, profile: Dict[str, Any]) -> None:
        await self.config.member(member).profile.set(profile)

    async def update_profile(self, member: discord.Member, **changes: Any) -> Dict[str, Any]:
        """Schreibt einzelne Felder ins Profil und gibt das aktualisierte Profil zurück."""
        profile = await self.config.member(member).profile()
        if profile is None:
            profile = empty_profile()
        profile.update(changes)
        await self.config.member(member).profile.set(profile)
        return profile

    async def clear_profile(self, member: discord.Member) -> None:
        await self.config.member(member).profile.clear()

    async def effective_settings(self, member: discord.Member,
                                 guild: discord.Guild) -> Dict[str, Any]:
        """Profil des Mitglieds oder – falls keins existiert – die Guild-Defaults.
        Immer inklusive permit/reject-Listen."""
        profile = await self.get_profile(member)
        if profile:
            base = empty_profile()
            base.update(profile)
            return base
        base = await self.get_defaults(guild)
        base["permit"] = []
        base["reject"] = []
        return base
