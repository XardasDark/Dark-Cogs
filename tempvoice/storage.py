"""
storage.py – Persistenz-Schicht über Reds Config

Guild-Daten:
  generators           = {gen_id(str): {category_id, name, limit, bitrate,
                                        region, locked, hidden}}
  interface_channel_id = int | None                # Textkanal mit dem Panel
  interface_message_id = int | None                # Panel-Nachricht
  temp_channels        = {channel_id(str): {owner_id, created_at,
                                            generator_id, guests[], waiting_room_id}}

Member-Daten (pro Server & Mitglied):
  profiles = {gen_id(str): {name?, limit?, bitrate?, region?, locked?, hidden?,
                            permit?[], reject?[]}}
             – Partial-Overrides je Generator; was nicht gesetzt ist, kommt aus
               den Generator-Defaults.
"""

from typing import Any, Dict, List, Optional

import discord

from . import constants as C


def default_settings() -> Dict[str, Any]:
    """Standard-Verhaltenswerte für neue Channel."""
    return {
        "name":    C.DEFAULT_NAME_TEMPLATE,
        "limit":   C.DEFAULT_LIMIT,
        "bitrate": C.DEFAULT_BITRATE,
        "region":  C.DEFAULT_REGION,
        "locked":  C.DEFAULT_LOCKED,
        "hidden":  C.DEFAULT_HIDDEN,
    }


def generator_defaults() -> Dict[str, Any]:
    """Vollständige Standard-Konfiguration eines Generators (inkl. Kategorie)."""
    cfg = default_settings()
    cfg["category_id"] = None
    return cfg


class TempVoiceStore:
    """Dünne, async Zugriffsschicht auf Reds Config."""

    def __init__(self, config):
        self.config = config

    # ── Generatoren (jeder mit eigenen Einstellungen) ───────────────────────────

    async def all_generators(self, guild: discord.Guild) -> Dict[str, Dict[str, Any]]:
        return await self.config.guild(guild).generators()

    async def get_generator_ids(self, guild: discord.Guild) -> List[int]:
        return [int(gid) for gid in (await self.config.guild(guild).generators()).keys()]

    async def is_generator(self, guild: discord.Guild, channel_id: int) -> bool:
        return str(channel_id) in await self.config.guild(guild).generators()

    async def get_generator(self, guild: discord.Guild, channel_id: int) -> Optional[Dict[str, Any]]:
        """Einstellungen eines Generators (vollständig mit Defaults aufgefüllt)."""
        gens = await self.config.guild(guild).generators()
        raw = gens.get(str(channel_id))
        if raw is None:
            return None
        cfg = generator_defaults()
        cfg.update(raw)
        return cfg

    async def add_generator(self, guild: discord.Guild, channel_id: int,
                            **settings: Any) -> None:
        cfg = generator_defaults()
        cfg.update({k: v for k, v in settings.items() if v is not None})
        async with self.config.guild(guild).generators() as gens:
            gens[str(channel_id)] = cfg

    async def update_generator(self, guild: discord.Guild, channel_id: int,
                               **changes: Any) -> None:
        async with self.config.guild(guild).generators() as gens:
            cfg = gens.get(str(channel_id))
            if cfg is not None:
                cfg.update({k: v for k, v in changes.items() if v is not None})

    async def remove_generator(self, guild: discord.Guild, channel_id: int) -> None:
        async with self.config.guild(guild).generators() as gens:
            gens.pop(str(channel_id), None)

    # ── Interface ───────────────────────────────────────────────────────────────

    async def get_interface(self, guild: discord.Guild) -> tuple:
        g = self.config.guild(guild)
        return await g.interface_channel_id(), await g.interface_message_id()

    async def set_interface(self, guild: discord.Guild, channel_id: Optional[int],
                            message_id: Optional[int]) -> None:
        g = self.config.guild(guild)
        await g.interface_channel_id.set(channel_id)
        await g.interface_message_id.set(message_id)

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
                               owner_id: int, created_at: str,
                               generator_id: Optional[int] = None) -> None:
        async with self.config.guild(guild).temp_channels() as temps:
            temps[str(channel_id)] = {
                "owner_id": owner_id,
                "created_at": created_at,
                "generator_id": generator_id,
            }

    async def get_generator_id_for_channel(self, guild: discord.Guild,
                                           channel_id: int) -> Optional[int]:
        rec = await self.get_temp_channel(guild, channel_id)
        return rec.get("generator_id") if rec else None

    async def count_channels_for_generator(self, guild: discord.Guild,
                                           generator_id: int) -> int:
        temps = await self.config.guild(guild).temp_channels()
        return sum(1 for rec in temps.values() if rec.get("generator_id") == generator_id)

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

    # ── Mitglieds-Profil (Partial-Override je Generator) ──────────────────────────

    @staticmethod
    def _gkey(generator_id: Optional[int]) -> str:
        return str(generator_id) if generator_id is not None else "0"

    async def all_profiles(self, member: discord.Member) -> Dict[str, Dict[str, Any]]:
        return await self.config.member(member).profiles()

    async def get_profile(self, member: discord.Member,
                          generator_id: Optional[int]) -> Dict[str, Any]:
        """Partial-Override des Mitglieds für diesen Generator (ggf. leeres Dict)."""
        profiles = await self.config.member(member).profiles()
        return dict(profiles.get(self._gkey(generator_id), {}))

    async def update_profile(self, member: discord.Member, generator_id: Optional[int],
                             **changes: Any) -> Dict[str, Any]:
        """Schreibt einzelne Felder in das Generator-Profil und gibt es (partial) zurück."""
        key = self._gkey(generator_id)
        async with self.config.member(member).profiles() as profiles:
            profile = profiles.get(key, {})
            profile.update(changes)
            profiles[key] = profile
            return dict(profile)

    async def clear_profiles(self, member: discord.Member) -> None:
        await self.config.member(member).profiles.clear()

    async def effective_settings(self, member: discord.Member, guild: discord.Guild,
                                 generator_id: Optional[int]) -> Dict[str, Any]:
        """Generator-Defaults, überlagert mit dem Partial-Override des Mitglieds.
        Immer inklusive permit/reject-Listen."""
        gen_cfg = await self.get_generator(guild, generator_id) if generator_id else None
        base = default_settings()
        if gen_cfg:
            base.update({k: gen_cfg[k] for k in base if k in gen_cfg})
        base["permit"] = []
        base["reject"] = []
        base.update(await self.get_profile(member, generator_id))
        return base
