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
from .data_manager import get_guild_settings


# ─────────────────────────────────────────────────────────────────────────────
# HILFSFUNKTIONEN
# ─────────────────────────────────────────────────────────────────────────────

def _short_id(group: Dict) -> str:
    """Kurze, im Titel/Footer sichtbare Gruppen-ID (macht Gruppe↔Post erkennbar)."""
    return str(group.get("group_id", "?"))[:8]


def _goal_text(group: Dict) -> str:
    return group.get("goal_custom") or group.get("goal") or "Gruppe"


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

    # Titel enthält die Gruppen-ID → Gruppe ↔ Forum-Post eindeutig zuordenbar.
    title = f"[{short}] {goal} – {creator}"[:100]

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
# FORUM-POST SCHLIESSEN
# ─────────────────────────────────────────────────────────────────────────────

async def close_forum_post(bot, group: Dict, *, announce_deletion: bool = True) -> None:
    """
    Schließt den Forum-Thread einer Gruppe: Ankündigung senden, dann sperren +
    archivieren. Der Thread wird NICHT gelöscht.

    Der Aufrufer sollte group["forum_closed"] = True setzen und speichern.
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
