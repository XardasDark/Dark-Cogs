"""
controls.py – Aktionslogik für temporäre Voice-Channel

Reine Hilfsfunktionen, die einen Channel verändern und – wo sinnvoll – das
Profil des Besitzers aktualisieren, damit die Einstellung beim nächsten Mal
wieder angewendet wird. Alle Funktionen geben eine deutsche Erfolgsmeldung
zurück oder werfen ValueError mit einer deutschen Fehlermeldung.
"""

from typing import Any, Dict, List, Optional, Union

import discord

from . import constants as C

Target = Union[discord.Member, discord.Role]


# ─────────────────────────────────────────────────────────────────────────────
# ERSTELLUNG: Overwrites aus Einstellungen bauen
# ─────────────────────────────────────────────────────────────────────────────

def build_overwrites(guild: discord.Guild, owner: discord.Member,
                     settings: Dict[str, Any]) -> Dict[Target, discord.PermissionOverwrite]:
    """Baut die Permission-Overwrites für einen neuen Channel aus einem Profil."""
    overwrites: Dict[Target, discord.PermissionOverwrite] = {}

    everyone = discord.PermissionOverwrite()
    if settings.get("locked"):
        everyone.connect = False
    if settings.get("hidden"):
        everyone.view_channel = False
    overwrites[guild.default_role] = everyone

    # Besitzer darf immer sehen & joinen
    overwrites[owner] = discord.PermissionOverwrite(view_channel=True, connect=True)

    # Dauerhaft erlaubte User/Rollen
    for target in _resolve_targets(guild, settings.get("permit", [])):
        overwrites[target] = discord.PermissionOverwrite(view_channel=True, connect=True)

    # Dauerhaft gesperrte User
    for target in _resolve_targets(guild, settings.get("reject", [])):
        overwrites[target] = discord.PermissionOverwrite(view_channel=False, connect=False)

    return overwrites


def _resolve_targets(guild: discord.Guild, ids: List[int]) -> List[Target]:
    """Wandelt gespeicherte IDs in Member/Rollen um; Unbekanntes wird übersprungen."""
    out: List[Target] = []
    for tid in ids:
        obj = guild.get_member(tid) or guild.get_role(tid)
        if obj is not None:
            out.append(obj)
    return out


# ─────────────────────────────────────────────────────────────────────────────
# ZUSTAND LESEN
# ─────────────────────────────────────────────────────────────────────────────

def is_locked(channel: discord.VoiceChannel) -> bool:
    ow = channel.overwrites_for(channel.guild.default_role)
    return ow.connect is False


def is_hidden(channel: discord.VoiceChannel) -> bool:
    ow = channel.overwrites_for(channel.guild.default_role)
    return ow.view_channel is False


# ─────────────────────────────────────────────────────────────────────────────
# AKTIONEN  (verändern Channel + schreiben ins Besitzer-Profil via store)
# ─────────────────────────────────────────────────────────────────────────────

async def rename(store, channel: discord.VoiceChannel, owner: discord.Member,
                 new_name: str) -> str:
    new_name = new_name.strip()[:C.MAX_CHANNEL_NAME]
    if not new_name:
        raise ValueError("Der Name darf nicht leer sein.")
    await channel.edit(name=new_name, reason="TempVoice: Umbenennung durch Besitzer")
    gen_id = await store.get_generator_id_for_channel(channel.guild, channel.id)
    await store.update_profile(owner, gen_id, name=new_name)
    # Warteraum-Namen mitziehen, falls vorhanden
    wr_id = await store.get_waiting_room_id(channel.guild, channel.id)
    if wr_id:
        waiting = channel.guild.get_channel(wr_id)
        if waiting is not None:
            try:
                await waiting.edit(name=C.waiting_room_name(new_name),
                                   reason="TempVoice: Warteraum-Name angepasst")
            except discord.HTTPException:
                pass
    return f"✅ Channel heißt jetzt **{new_name}**."


async def set_limit(store, channel: discord.VoiceChannel, owner: discord.Member,
                    limit: int) -> str:
    if limit < 0 or limit > C.MAX_USER_LIMIT:
        raise ValueError(f"Das Limit muss zwischen 0 und {C.MAX_USER_LIMIT} liegen (0 = unbegrenzt).")
    await channel.edit(user_limit=limit, reason="TempVoice: Limit durch Besitzer")
    gen_id = await store.get_generator_id_for_channel(channel.guild, channel.id)
    await store.update_profile(owner, gen_id, limit=limit)
    return ("✅ Mitglieder-Limit **aufgehoben** (unbegrenzt)." if limit == 0
            else f"✅ Mitglieder-Limit auf **{limit}** gesetzt.")


async def toggle_lock(store, channel: discord.VoiceChannel, owner: discord.Member) -> str:
    locked_now = not is_locked(channel)
    ow = channel.overwrites_for(channel.guild.default_role)
    ow.connect = False if locked_now else None
    await channel.set_permissions(channel.guild.default_role, overwrite=ow,
                                  reason="TempVoice: Sperren/Entsperren durch Besitzer")
    gen_id = await store.get_generator_id_for_channel(channel.guild, channel.id)
    await store.update_profile(owner, gen_id, locked=locked_now)
    return ("🔒 Channel **gesperrt** – andere können nicht mehr beitreten." if locked_now
            else "🔓 Channel **entsperrt** – andere können wieder beitreten.")


async def toggle_hide(store, channel: discord.VoiceChannel, owner: discord.Member) -> str:
    hidden_now = not is_hidden(channel)
    ow = channel.overwrites_for(channel.guild.default_role)
    ow.view_channel = False if hidden_now else None
    await channel.set_permissions(channel.guild.default_role, overwrite=ow,
                                  reason="TempVoice: Verbergen/Zeigen durch Besitzer")
    gen_id = await store.get_generator_id_for_channel(channel.guild, channel.id)
    await store.update_profile(owner, gen_id, hidden=hidden_now)
    return ("👁️ Channel **verborgen** – andere sehen ihn nicht mehr." if hidden_now
            else "👁️ Channel wieder **sichtbar** für alle.")


async def permit(store, channel: discord.VoiceChannel, owner: discord.Member,
                 target: Target) -> str:
    """Gibt einem User/einer Rolle Zugang und entfernt ihn von der Sperrliste."""
    await channel.set_permissions(target, view_channel=True, connect=True,
                                  reason="TempVoice: Zugang erlaubt durch Besitzer")
    gen_id = await store.get_generator_id_for_channel(channel.guild, channel.id)
    profile = await store.get_profile(owner, gen_id)
    permit_ids: List[int] = list(profile.get("permit", []))
    reject_ids: List[int] = list(profile.get("reject", []))
    if target.id not in permit_ids:
        permit_ids.append(target.id)
    if target.id in reject_ids:
        reject_ids.remove(target.id)
    await store.update_profile(owner, gen_id, permit=permit_ids, reject=reject_ids)
    return f"✅ {target.mention} darf diesen Channel jetzt betreten."


async def kick(store, channel: discord.VoiceChannel, owner: discord.Member,
               target: discord.Member) -> str:
    """Wirft ein Mitglied raus und sperrt es dauerhaft aus."""
    if target.id == owner.id:
        raise ValueError("Du kannst dich nicht selbst rauswerfen.")
    await channel.set_permissions(target, view_channel=False, connect=False,
                                  reason="TempVoice: Rauswurf durch Besitzer")
    # aus dem Channel entfernen, falls gerade drin
    if target.voice and target.voice.channel and target.voice.channel.id == channel.id:
        try:
            await target.move_to(None, reason="TempVoice: Rauswurf durch Besitzer")
        except discord.HTTPException:
            pass
    gen_id = await store.get_generator_id_for_channel(channel.guild, channel.id)
    profile = await store.get_profile(owner, gen_id)
    permit_ids: List[int] = list(profile.get("permit", []))
    reject_ids: List[int] = list(profile.get("reject", []))
    if target.id not in reject_ids:
        reject_ids.append(target.id)
    if target.id in permit_ids:
        permit_ids.remove(target.id)
    await store.update_profile(owner, gen_id, permit=permit_ids, reject=reject_ids)
    return f"🚫 {target.mention} wurde rausgeworfen und ausgesperrt."


async def set_bitrate(store, channel: discord.VoiceChannel, owner: discord.Member,
                      bitrate: int) -> str:
    limit = int(channel.guild.bitrate_limit)
    bitrate = max(C.MIN_BITRATE, min(bitrate, limit))
    await channel.edit(bitrate=bitrate, reason="TempVoice: Bitrate durch Besitzer")
    gen_id = await store.get_generator_id_for_channel(channel.guild, channel.id)
    await store.update_profile(owner, gen_id, bitrate=bitrate)
    return f"🎚️ Bitrate auf **{bitrate // 1000} kbps** gesetzt."


async def set_region(store, channel: discord.VoiceChannel, owner: discord.Member,
                     region: Optional[str]) -> str:
    await channel.edit(rtc_region=region, reason="TempVoice: Region durch Besitzer")
    gen_id = await store.get_generator_id_for_channel(channel.guild, channel.id)
    await store.update_profile(owner, gen_id, region=region)
    name = C.REGION_NAMES.get(region, region or "Automatisch")
    return f"🌍 Region auf **{name}** gesetzt."


async def transfer(store, channel: discord.VoiceChannel, old_owner: discord.Member,
                   new_owner: discord.Member) -> str:
    """Übergibt die Besitzerschaft freiwillig an ein anderes Mitglied im Channel."""
    if new_owner.bot:
        raise ValueError("Bots können nicht Besitzer werden.")
    if new_owner.id == old_owner.id:
        raise ValueError("Du bist bereits der Besitzer.")
    if not (new_owner.voice and new_owner.voice.channel
            and new_owner.voice.channel.id == channel.id):
        raise ValueError("Diese Person befindet sich nicht in deinem Channel.")
    await store.set_owner(channel.guild, channel.id, new_owner.id)
    await channel.set_permissions(new_owner, view_channel=True, connect=True,
                                  reason="TempVoice: Besitz übertragen")
    return f"🔁 {new_owner.mention} ist jetzt Besitzer des Channels."
