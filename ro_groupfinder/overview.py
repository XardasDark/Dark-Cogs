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
    creator_txt = f"<@{creator}>" if creator else group.get("creator_name", "?")

    guild_id   = group.get("guild_id")
    channel_id = group.get("channel_id")
    msg_id     = group.get("message_id")

    dt   = parse_stored_datetime(group.get("datetime"), guild_id)
    when = f" · <t:{int(dt.timestamp())}:R>" if dt else ""

    line = f"{icon} **{goal}** — {creator_txt} · {filled}/{total}{when}"
    if guild_id and channel_id and msg_id:
        jump = f"https://discord.com/channels/{guild_id}/{channel_id}/{msg_id}"
        line += f" · [→ Post]({jump})"
    return line


def _sort_key(group: Dict):
    # Gruppen mit Termin zuerst (nach Startzeit aufsteigend), danach zeitlose
    # (nach Erstellzeit). So stehen die als Nächstes startenden oben.
    dt = parse_stored_datetime(group.get("datetime"), group.get("guild_id"))
    if dt is not None:
        return (0, dt.timestamp(), "")
    return (1, 0.0, group.get("created_at", ""))


def build_overview_embed(guild_id: int) -> discord.Embed:
    """Baut das Übersichts-Embed aller offenen/vollen Gruppen einer Guild."""
    active = [
        g for g in get_guild_groups(guild_id).values()
        if g.get("status") in ("open", "full")
    ]
    active.sort(key=_sort_key)

    embed = discord.Embed(title="📋 Offene Gruppensuchen", color=COLOR_OPEN)
    if not active:
        embed.description = (
            "Aktuell keine offenen Gruppensuchen.\n"
            "Erstelle eine mit `/gruppe erstellen`!"
        )
    else:
        lines = [_group_line(g) for g in active[:MAX_OVERVIEW_GROUPS]]
        desc  = "\n".join(lines)
        extra = len(active) - MAX_OVERVIEW_GROUPS
        if extra > 0:
            desc += f"\n\n… und **{extra}** weitere. Nutze `/gruppe liste`."
        embed.description = desc

    embed.set_footer(text="Diese Übersicht aktualisiert sich automatisch.")
    return embed


# ─────────────────────────────────────────────────────────────────────────────
# ÜBERSICHT AKTUALISIEREN
# ─────────────────────────────────────────────────────────────────────────────

async def refresh_overview(bot, guild_id: int, *, move_to_bottom: bool = False) -> None:
    """
    Aktualisiert die Übersichts-Nachricht im Gruppen-Channel.

    move_to_bottom=True: alte Nachricht löschen und neu ganz unten posten
    (nötig, nachdem ein neuer Gruppen-Post erstellt wurde, damit die Übersicht
    wieder als letzte Nachricht steht).
    """
    settings   = get_guild_settings(guild_id)
    channel_id = settings.get("group_channel_id")
    if not channel_id:
        return

    channel = await _resolve_channel(bot, channel_id)
    if channel is None:
        return

    embed  = build_overview_embed(guild_id)
    msg_id = settings.get("overview_message_id")

    async def _post_new():
        try:
            message = await channel.send(embed=embed)
            set_guild_setting(guild_id, "overview_message_id", message.id)
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

    archive_id = settings.get("archive_channel_id")
    action     = settings.get("closed_post_action", "keep")

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
