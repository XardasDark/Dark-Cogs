"""
forum.py – Forum-Diskussionsposts für den RO Group Finder

Für jede erstellte Gruppe wird optional ein Beitrag in einem konfigurierten
Forum-Channel angelegt, in dem sich die Spieler über die Gruppe austauschen können.

Öffentliche Helfer (async, bot-basiert – nutzbar aus cog.py UND scheduler.py):

  create_forum_post(bot, group)
      → Erstellt einen Forum-Thread für die Gruppe und speichert dessen ID in
        group["forum_thread_id"]. Gibt die Thread-ID zurück (oder None).

  close_forum_post(bot, group, announce_deletion=True)
      → Kündigt im Thread an, dass die Gruppe beendet ist, und schließt
        (sperrt + archiviert) den Thread. Der Thread wird NIE gelöscht.

Beide Funktionen sind "best effort": Discord-Fehler werden abgefangen, damit die
Gruppen-Logik nie an einem Forum-Problem scheitert.
"""

import discord
from typing import Optional, Dict

from .constants import COLOR_OPEN
from .data_manager import (
    get_guild_settings,
    resolve_goal_name,
    stored_datetime_to_local_str,
)


# ─────────────────────────────────────────────────────────────────────────────
# HILFSFUNKTIONEN
# ─────────────────────────────────────────────────────────────────────────────

def _short_id(group: Dict) -> str:
    """Kurze, im Titel/Footer sichtbare Gruppen-ID (macht Gruppe↔Post erkennbar)."""
    return str(group.get("group_id", "?"))[:8]


def _goal_text(group: Dict) -> str:
    return resolve_goal_name(group)


def _group_jump_url(group: Dict) -> Optional[str]:
    """Jump-Link zum Gruppen-Post (nur wenn message_id vorhanden)."""
    guild_id   = group.get("guild_id")
    channel_id = group.get("channel_id")
    msg_id     = group.get("message_id")
    if not (guild_id and channel_id and msg_id):
        return None
    return f"https://discord.com/channels/{guild_id}/{channel_id}/{msg_id}"


async def _resolve_channel(bot, channel_id: int):
    """Holt einen Channel/Thread aus Cache oder API. None bei Fehler."""
    channel = bot.get_channel(channel_id)
    if channel is None:
        try:
            channel = await bot.fetch_channel(channel_id)
        except Exception:
            return None
    return channel


# ─────────────────────────────────────────────────────────────────────────────
# FORUM-POST ERSTELLEN
# ─────────────────────────────────────────────────────────────────────────────

async def create_forum_post(bot, group: Dict) -> Optional[int]:
    """
    Erstellt einen Forum-Thread für die Gruppe (wenn ein Forum konfiguriert ist).

    Muss NACH dem Gruppen-Post aufgerufen werden, da der Startpost den Jump-Link
    zur Gruppe enthält (braucht message_id). Der Aufrufer muss die Gruppe
    anschließend speichern (save_group), damit die Thread-ID persistiert wird.
    """
    settings = get_guild_settings(group["guild_id"])
    forum_id = settings.get("forum_channel_id")
    if not forum_id:
        return None

    forum = await _resolve_channel(bot, forum_id)
    if not isinstance(forum, discord.ForumChannel):
        return None

    short   = _short_id(group)
    goal    = _goal_text(group)
    creator = group.get("creator_name", "Unbekannt")
    date    = stored_datetime_to_local_str(group.get("datetime"), group["guild_id"])

    # Titel enthält die Gruppen-ID → Gruppe ↔ Forum-Post eindeutig zuordenbar.
    # Das Start-Datum steht direkt hinter der ID, damit sich Threads leichter
    # unterscheiden lassen und es bei der 100-Zeichen-Kürzung erhalten bleibt.
    prefix = f"[{short}] " + (f"\U0001f4c5 {date} · " if date else "")
    title = f"{prefix}{goal} – {creator}"[:100]

    embed = discord.Embed(
        title=f"💬 Diskussion: {goal}",
        description=(
            "Nutzt diesen Beitrag, um euch über die Gruppe abzustimmen – "
            "Termine, Setup, Fragen und alles Weitere.\n\n"
            f"👑 **Ersteller:** {creator}"
        ),
        color=COLOR_OPEN,
    )
    jump = _group_jump_url(group)
    if jump:
        embed.add_field(name="🔗 Zur Gruppe", value=f"[Gruppen-Post öffnen]({jump})", inline=False)
    embed.set_footer(text=f"Gruppen-ID: {short}")

    try:
        created = await forum.create_thread(name=title, embed=embed)
        # discord.py gibt ein ThreadWithMessage-Objekt zurück (.thread / .message).
        thread = getattr(created, "thread", created)
        group["forum_thread_id"] = thread.id
        return thread.id
    except Exception as e:  # noqa: BLE001
        print(f"[RO GroupFinder Forum] Konnte Forum-Post nicht erstellen: {e}")
        return None


# ─────────────────────────────────────────────────────────────────────────────
# BEITRITT IM FORUM ANKÜNDIGEN
# ─────────────────────────────────────────────────────────────────────────────

async def notify_join_in_forum(
    bot,
    group:         Dict,
    user_id:       int,
    *,
    ingame_name:   Optional[str] = None,
    class_display: Optional[str] = None,
    class_emoji:   Optional[str] = None,
) -> None:
    """
    Pingt einen neu beigetretenen Spieler im Forum-Thread der Gruppe.

    Der Ping erzeugt eine Benachrichtigung, die den Spieler direkt in DIESEN
    Thread führt, so findet er den richtigen Diskussionsbeitrag sofort, auch
    wenn es viele Beiträge gibt. Zudem wird er dem Thread als Teilnehmer
    hinzugefügt.
    """
    thread_id = group.get("forum_thread_id")
    if not thread_id:
        return

    thread = await _resolve_channel(bot, thread_id)
    if not isinstance(thread, discord.Thread):
        return
    # In bereits geschlossene/archivierte Threads nicht mehr hineinpingen.
    if thread.archived or thread.locked:
        return

    name = ingame_name or "Spieler"
    role = f" als {class_emoji or ''} {class_display}".rstrip() if class_display else ""
    text = (
        f"👋 <@{user_id}> (**{name}**) ist der Gruppe beigetreten{role}!"
    )

    try:
        await thread.send(
            text,
            allowed_mentions=discord.AllowedMentions(users=True),
        )
    except Exception:
        pass


# ─────────────────────────────────────────────────────────────────────────────
# AUSTRITT IM FORUM ANKÜNDIGEN
# ─────────────────────────────────────────────────────────────────────────────

async def notify_leave_in_forum(
    bot,
    group:       Dict,
    player_name: str,
    *,
    removed:     bool = False,
) -> None:
    """
    Informiert den Forum-Thread, dass ein Spieler die Gruppe verlassen hat
    (oder vom Ersteller entfernt wurde).

    Es wird bewusst NICHT gepingt – die verbleibenden Mitglieder sehen die
    Nachricht im Thread, der ausgetretene Spieler wird nicht belästigt.
    Best effort: Fehler werden abgefangen.
    """
    thread_id = group.get("forum_thread_id")
    if not thread_id:
        return

    thread = await _resolve_channel(bot, thread_id)
    if not isinstance(thread, discord.Thread):
        return
    if thread.archived or thread.locked:
        return

    if removed:
        text = f"➖ **{player_name}** wurde aus der Gruppe entfernt."
    else:
        text = f"👋 **{player_name}** hat die Gruppe verlassen."

    try:
        await thread.send(text, allowed_mentions=discord.AllowedMentions.none())
    except Exception:
        pass


# ─────────────────────────────────────────────────────────────────────────────
# FÜHRUNGS-WECHSEL IM FORUM ANKÜNDIGEN
# ─────────────────────────────────────────────────────────────────────────────

async def notify_leader_change_in_forum(
    bot,
    group:          Dict,
    new_leader_id:  int,
    new_leader_name: str,
    old_leader_name: Optional[str] = None,
) -> None:
    """
    Kündigt im Forum-Thread an, dass die Gruppenführung gewechselt hat.

    Der neue Führer wird gepingt (er soll es sofort mitbekommen). Best effort.
    """
    thread_id = group.get("forum_thread_id")
    if not thread_id:
        return

    thread = await _resolve_channel(bot, thread_id)
    if not isinstance(thread, discord.Thread):
        return
    if thread.archived or thread.locked:
        return

    prefix = f"von **{old_leader_name}** " if old_leader_name else ""
    text = (
        f"👑 Die Gruppenführung wurde {prefix}an <@{new_leader_id}> "
        f"(**{new_leader_name}**) übergeben."
    )

    try:
        await thread.send(text, allowed_mentions=discord.AllowedMentions(users=True))
    except Exception:
        pass


# ─────────────────────────────────────────────────────────────────────────────
# FORUM-POST SCHLIESSEN
# ─────────────────────────────────────────────────────────────────────────────

async def close_forum_post(bot, group: Dict, *, announce_deletion: bool = True) -> None:
    """
    Schließt den Forum-Thread einer Gruppe: Ankündigung senden, dann sperren +
    archivieren. Der Thread wird NICHT gelöscht.

    Der Aufrufer sollte group["thread_closed"] = True setzen und speichern.
    """
    thread_id = group.get("forum_thread_id")
    if not thread_id:
        return

    thread = await _resolve_channel(bot, thread_id)
    if thread is None or not isinstance(thread, discord.Thread):
        return

    goal = _goal_text(group)
    if announce_deletion:
        text = (
            f"🔒 **Die Gruppe {goal} ist beendet.**\n"
            "Dieser Beitrag wird nun geschlossen. Er bleibt zur Ansicht erhalten und "
            "kann bei Bedarf von einem Admin gelöscht werden."
        )
    else:
        text = f"🔒 **Die Gruppe {goal} ist beendet.** Dieser Beitrag wird geschlossen."

    # Ankündigung senden (hebt eine evtl. Auto-Archivierung automatisch auf),
    # danach den Thread sperren + archivieren.
    try:
        await thread.send(text)
    except Exception:
        pass

    try:
        await thread.edit(archived=True, locked=True)
    except Exception:
        pass


# ─────────────────────────────────────────────────────────────────────────────
# FORUM-THREAD WIEDER ÖFFNEN
# ─────────────────────────────────────────────────────────────────────────────

async def reopen_forum_post(bot, group: Dict) -> bool:
    """
    Öffnet einen geschlossenen Forum-Thread wieder (entsperren + entarchivieren).
    Gibt True zurück, wenn ein Thread reaktiviert wurde; False, wenn keiner
    (mehr) existiert – dann sollte der Aufrufer per create_forum_post einen neuen
    anlegen.
    """
    thread_id = group.get("forum_thread_id")
    if not thread_id:
        return False

    thread = await _resolve_channel(bot, thread_id)
    if thread is None or not isinstance(thread, discord.Thread):
        return False

    try:
        # Entsperren + entarchivieren (locked zuerst lösen, sonst schlägt edit fehl)
        await thread.edit(archived=False, locked=False)
    except Exception:
        return False

    try:
        await thread.send(f"🔓 **Die Gruppe {_goal_text(group)} wurde wieder geöffnet.**")
    except Exception:
        pass
    return True


# ─────────────────────────────────────────────────────────────────────────────
# FORUM-THREAD LÖSCHEN (optional mit Archiv-Zusammenfassung)
# ─────────────────────────────────────────────────────────────────────────────

def _build_archive_summary(group: Dict) -> discord.Embed:
    """Kurze Zusammenfassung einer Gruppe für den Archiv-Channel."""
    goal    = _goal_text(group)
    creator = group.get("creator_id")
    creator_txt = f"<@{creator}>" if creator else group.get("creator_name", "?")

    members = []
    for slot in group.get("slots", []):
        if slot.get("filled_by_id"):
            name = slot.get("filled_by_ingame") or slot.get("filled_by_name") or "?"
            cls  = slot.get("filled_class") or slot.get("display_name") or ""
            members.append(f"• {name}" + (f" – {cls}" if cls else ""))

    embed = discord.Embed(
        title=f"🗄️ Archiv: {goal}",
        description=f"👑 **Leiter:** {creator_txt}\n🆔 `{_short_id(group)}`",
        color=COLOR_OPEN,
    )
    if members:
        embed.add_field(name="👥 Mitglieder", value="\n".join(members)[:1024], inline=False)
    return embed


async def delete_forum_post(bot, group: Dict, *, archive: bool = True) -> None:
    """
    Löscht den Forum-Thread einer Gruppe. Ist `archive` gesetzt UND ein
    Archiv-Channel konfiguriert, wird vorher eine Zusammenfassung dorthin
    gepostet. Best effort.
    """
    thread_id = group.get("forum_thread_id")
    if not thread_id:
        return

    if archive:
        settings   = get_guild_settings(group["guild_id"])
        archive_id = settings.get("archive_channel_id")
        if archive_id:
            archive_ch = await _resolve_channel(bot, archive_id)
            if archive_ch is not None:
                try:
                    await archive_ch.send(embed=_build_archive_summary(group))
                except Exception:
                    pass

    thread = await _resolve_channel(bot, thread_id)
    if thread is None or not isinstance(thread, discord.Thread):
        return
    try:
        await thread.delete()
    except Exception:
        pass
