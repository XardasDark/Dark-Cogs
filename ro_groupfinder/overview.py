"""
overview.py – Live-Übersicht offener Gruppensuchen + Post-Finalisierung

Zwei Aufgaben (async, bot-basiert – nutzbar aus cog.py UND scheduler.py):

  refresh_overview(bot, guild_id, move_to_bottom=False)
      → Baut/aktualisiert eine einzige "📋 Offene Gruppensuchen"-Nachricht im
        Gruppen-Channel. Mit move_to_bottom=True wird sie gelöscht und neu unten
        gepostet (damit sie nach neuen Gruppen-Posts wieder ganz unten steht).

  finalize_group_post(bot, group, allow_keep=True)
      → Entscheidet, was mit dem Post einer geschlossenen/abgeschlossenen/
        abgelaufenen Gruppe passiert:
          - Archiv-Channel gesetzt → Embed dorthin kopieren, Original löschen
          - sonst closed_post_action == "delete" → Original löschen
          - sonst (keep, nur wenn allow_keep) → Original mit Status-Embed belassen
        Gibt True zurück, wenn der Post aus dem Gruppen-Channel ENTFERNT wurde
        (→ Aufrufer sollte die Gruppe aus groups.json entfernen).

Beide Funktionen sind "best effort": Discord-Fehler werden abgefangen.
"""

import discord
from typing import Dict, Optional

from .constants import COLOR_OPEN
from .data_manager import (
    get_guild_settings,
    set_guild_setting,
    get_guild_groups,
    parse_stored_datetime,
    resolve_goal_name,
    list_profiles,
    get_profile_by_channel,
    set_profile_field,
    get_archive_channel_for_group,
)
from .group_embed import build_group_embed, build_group_action_view

MAX_OVERVIEW_GROUPS = 25


# ─────────────────────────────────────────────────────────────────────────────
# HILFSFUNKTIONEN
# ─────────────────────────────────────────────────────────────────────────────

async def _resolve_channel(bot, channel_id: int):
    channel = bot.get_channel(channel_id)
    if channel is None:
        try:
            channel = await bot.fetch_channel(channel_id)
        except Exception:
            return None
    return channel


def _group_line(group: Dict) -> str:
    goal    = resolve_goal_name(group)
    filled  = sum(1 for s in group.get("slots", []) if s.get("filled_by_id"))
    total   = group.get("player_count", "?")
    icon    = "🟢" if group.get("status") == "open" else "🟡"

    creator = group.get("creator_id")
    creator_txt = f"<@{creator}>" if creator else (group.get("creator_name") or "🤖 Offene Gruppe")

    guild_id   = group.get("guild_id")
    channel_id = group.get("channel_id")
    msg_id     = group.get("message_id")

    dt   = parse_stored_datetime(group.get("datetime"), guild_id)
    when = f"⏰ <t:{int(dt.timestamp())}:R>" if dt else "🕓 zeitlos"

    # Der Titel selbst ist der Klick-Link zum Post (kein separates "→ Post",
    # das sonst hässlich umbricht). Details ruhig in eine zweite, eingerückte Zeile.
    if guild_id and channel_id and msg_id:
        jump  = f"https://discord.com/channels/{guild_id}/{channel_id}/{msg_id}"
        title = f"[**{goal}**]({jump})"
    else:
        title = f"**{goal}**"

    return f"{icon} {title}\n　└ `{filled}/{total}` · {when} · 👑 {creator_txt}"


def _sort_key(group: Dict):
    # Gruppen mit Termin zuerst (nach Startzeit aufsteigend), danach zeitlose
    # (nach Erstellzeit). So stehen die als Nächstes startenden oben.
    dt = parse_stored_datetime(group.get("datetime"), group.get("guild_id"))
    if dt is not None:
        return (0, dt.timestamp(), "")
    return (1, 0.0, group.get("created_at", ""))


def build_overview_embed(guild_id: int, channel_id: Optional[int] = None) -> discord.Embed:
    """
    Baut das Übersichts-Embed der offenen/vollen Gruppen.
    Mit `channel_id` nur Gruppen dieses Spiel-Channels (pro Profil eine Übersicht),
    ohne `channel_id` alle Gruppen der Guild (Alt-Verhalten).
    """
    active = [
        g for g in get_guild_groups(guild_id).values()
        if g.get("status") in ("open", "full")
        and (channel_id is None or g.get("channel_id") == channel_id)
    ]
    active.sort(key=_sort_key)

    if not active:
        embed = discord.Embed(title="📋 Offene Gruppensuchen", color=COLOR_OPEN)
        embed.description = (
            "Aktuell keine offenen Gruppensuchen.\n"
            "Erstelle eine mit `/gruppe erstellen`!"
        )
    else:
        embed = discord.Embed(
            title=f"📋 Offene Gruppensuchen ({len(active)})",
            color=COLOR_OPEN,
        )
        lines = [_group_line(g) for g in active[:MAX_OVERVIEW_GROUPS]]
        desc  = "\n\n".join(lines)
        extra = len(active) - MAX_OVERVIEW_GROUPS
        if extra > 0:
            desc += f"\n\n… und **{extra}** weitere. Nutze `/gruppe liste`."
        embed.description = desc

    embed.set_footer(text="Aktualisiert sich automatisch · /gruppe meine zeigt deine Gruppen")
    return embed


# ─────────────────────────────────────────────────────────────────────────────
# ÜBERSICHT AKTUALISIEREN
# ─────────────────────────────────────────────────────────────────────────────

def _overview_targets(guild_id: int, channel_id: Optional[int]) -> list:
    """Welche Gruppen-Channel ihre Übersicht bekommen sollen."""
    if channel_id is not None:
        return [channel_id]
    ids = [p["group_channel_id"] for p in list_profiles(guild_id) if p.get("group_channel_id")]
    if ids:
        return ids
    legacy = get_guild_settings(guild_id).get("group_channel_id")
    return [legacy] if legacy else []


def _get_overview_msg_id(guild_id: int, channel_id: int) -> Optional[int]:
    prof = get_profile_by_channel(guild_id, channel_id)
    if prof is not None:
        return prof.get("overview_message_id")
    return get_guild_settings(guild_id).get("overview_message_id")


def _set_overview_msg_id(guild_id: int, channel_id: int, msg_id: Optional[int]) -> None:
    prof = get_profile_by_channel(guild_id, channel_id)
    if prof is not None:
        set_profile_field(guild_id, channel_id, "overview_message_id", msg_id)
    else:
        set_guild_setting(guild_id, "overview_message_id", msg_id)


async def refresh_overview(bot, guild_id: int, channel_id: Optional[int] = None,
                           *, move_to_bottom: bool = False) -> None:
    """
    Aktualisiert die Übersichts-Nachricht(en).

    Mit `channel_id` nur die Übersicht dieses Spiel-Channels, ohne `channel_id`
    die aller konfigurierten Spiel-Channel (bzw. der Alt-Gruppen-Channel).
    move_to_bottom=True: alte Nachricht löschen und neu ganz unten posten.
    """
    for cid in _overview_targets(guild_id, channel_id):
        await _refresh_one(bot, guild_id, cid, move_to_bottom=move_to_bottom)


async def _refresh_one(bot, guild_id: int, channel_id: int, *, move_to_bottom: bool) -> None:
    channel = await _resolve_channel(bot, channel_id)
    if channel is None:
        return

    embed  = build_overview_embed(guild_id, channel_id)
    msg_id = _get_overview_msg_id(guild_id, channel_id)

    async def _post_new():
        try:
            message = await channel.send(embed=embed)
            _set_overview_msg_id(guild_id, channel_id, message.id)
        except Exception:
            pass

    if move_to_bottom:
        if msg_id:
            try:
                old = await channel.fetch_message(msg_id)
                await old.delete()
            except Exception:
                pass
        await _post_new()
        return

    # In-Place-Update (Übersicht steht bereits als letzte Nachricht)
    if msg_id:
        try:
            message = await channel.fetch_message(msg_id)
            await message.edit(embed=embed)
            return
        except discord.NotFound:
            pass
        except Exception:
            return
    await _post_new()


# ─────────────────────────────────────────────────────────────────────────────
# POST-FINALISIERUNG (Archiv / Löschen / Behalten)
# ─────────────────────────────────────────────────────────────────────────────

async def finalize_group_post(bot, group: Dict, *, allow_keep: bool = True) -> bool:
    """
    Wendet die Guild-Richtlinie auf den Post einer geschlossenen Gruppe an.

    Rückgabe True → der Post wurde aus dem Gruppen-Channel entfernt
    (archiviert oder gelöscht); der Aufrufer sollte die Gruppe aus groups.json
    entfernen. False → der Post bleibt im Channel (nur bei allow_keep + keep).
    """
    settings   = get_guild_settings(group["guild_id"])
    channel_id = group.get("channel_id")
    msg_id     = group.get("message_id")

    channel  = await _resolve_channel(bot, channel_id) if channel_id else None
    original = None
    if channel and msg_id:
        try:
            original = await channel.fetch_message(msg_id)
        except Exception:
            original = None

    archive_id = get_archive_channel_for_group(group)      # pro Spiel-Profil (Alt-Fallback intern)
    action     = settings.get("closed_post_action", "keep")  # serverweit

    # 1. Archiv-Channel hat Vorrang
    if archive_id:
        archive_ch = await _resolve_channel(bot, archive_id)
        if archive_ch is not None:
            try:
                await archive_ch.send(embed=build_group_embed(group))
            except Exception:
                pass
            if original:
                try:
                    await original.delete()
                except Exception:
                    pass
            return True

    # 2. Ohne Archiv: löschen oder behalten
    if action == "delete" or not allow_keep:
        if original:
            try:
                await original.delete()
            except Exception:
                pass
        return True

    # 3. Behalten: Post mit finalem Status-Embed (deaktivierte Buttons) belassen
    if original:
        try:
            await original.edit(
                embed=build_group_embed(group),
                view=build_group_action_view(group),
            )
        except Exception:
            pass
    return False
