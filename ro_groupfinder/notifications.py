"""
notifications.py – Private DM-Benachrichtigungen für den RO Group Finder

Alle Nachrichten an Spieler und Gruppenersteller werden hier zentral
verwaltet. Jede Funktion ist async und erwartet ein discord.Bot-Objekt
um User-Objekte abrufen zu können.

Übersicht der Benachrichtigungen:
  notify_creator_join()         → Ersteller: Spieler ist beigetreten
  notify_creator_leave()        → Ersteller: Spieler hat verlassen
  notify_creator_removed()      → Ersteller: Du hast Spieler X entfernt (Bestätigung)
  notify_player_removed()       → Spieler: Du wurdest entfernt
  notify_group_full()           → Alle Mitglieder: Gruppe ist voll
  notify_group_deleted()        → Alle Mitglieder + Warteliste: Gruppe wurde gelöscht
  notify_group_expired()        → Alle Mitglieder + Warteliste: Gruppe abgelaufen
  notify_reminder()             → Alle Mitglieder: Erinnerung vor Gruppenstart
  notify_waitlist_joined()      → Spieler: Du bist auf der Warteliste
  notify_waitlist_slot_free()   → Nächster Wartelisten-Spieler: Slot frei
  notify_waitlist_timeout()     → Wartelisten-Spieler: Zeit abgelaufen
  notify_waitlist_removed()     → Spieler: Du wurdest von der Warteliste entfernt
  notify_recurrence_new_post()  → Alle Mitglieder: Neue Wiederholungs-Gruppe erstellt
  notify_edit()                 → Alle Mitglieder: Gruppe wurde bearbeitet
"""

import discord
from typing import Optional, Dict, List

from .constants import RECURRENCE_OPTIONS, COLOR_OPEN, COLOR_CLOSED, COLOR_EXPIRED


# ─────────────────────────────────────────────────────────────────────────────
# HILFSFUNKTIONEN
# ─────────────────────────────────────────────────────────────────────────────

async def _get_user(bot: discord.Client, user_id: int) -> Optional[discord.User]:
    """Versucht einen Discord-User zu holen (Cache → API-Fallback)."""
    user = bot.get_user(user_id)
    if user is None:
        try:
            user = await bot.fetch_user(user_id)
        except (discord.NotFound, discord.HTTPException):
            return None
    return user


async def _send_dm(user: discord.User, embed: discord.Embed) -> bool:
    """
    Sendet eine DM an einen Nutzer.
    Gibt True zurück wenn erfolgreich, False wenn DMs deaktiviert sind.
    """
    try:
        await user.send(embed=embed)
        return True
    except (discord.Forbidden, discord.HTTPException):
        return False


def _group_title(group: Dict) -> str:
    """Gibt den Anzeige-Titel einer Gruppe zurück."""
    return group.get("goal_custom") or group.get("goal") or "Unbekannte Gruppe"


def _base_embed(title: str, description: str, color: int) -> discord.Embed:
    embed = discord.Embed(title=title, description=description, color=color)
    embed.set_footer(text="RO Group Finder")
    return embed


# ─────────────────────────────────────────────────────────────────────────────
# ERSTELLER-BENACHRICHTIGUNGEN
# ─────────────────────────────────────────────────────────────────────────────

async def notify_creator_join(
    bot:          discord.Client,
    group:        Dict,
    player_name:  str,
    class_display: str,
    class_emoji:  str,
    slot_index:   int,
) -> None:
    """
    Benachrichtigt den Gruppenersteller wenn sich ein Spieler anmeldet.
    """
    creator = await _get_user(bot, group["creator_id"])
    if not creator:
        return

    title = group.get("goal") or group.get("goal_custom") or "Gruppe"
    embed = _base_embed(
        title="🟢 Neuer Spieler in deiner Gruppe!",
        description=(
            f"**{class_emoji} {player_name}** hat sich für deine Gruppe angemeldet.\n\n"
            f"🎯 **Gruppe:** {_group_title(group)}\n"
            f"🗂️ **Slot:** {slot_index + 1}\n"
            f"⚔️ **Klasse/Rolle:** {class_display}"
        ),
        color=COLOR_OPEN,
    )
    await _send_dm(creator, embed)


async def notify_creator_leave(
    bot:         discord.Client,
    group:       Dict,
    player_name: str,
    slot_index:  int,
) -> None:
    """
    Benachrichtigt den Gruppenersteller wenn ein Spieler die Gruppe verlässt.
    """
    creator = await _get_user(bot, group["creator_id"])
    if not creator:
        return

    embed = _base_embed(
        title="🔴 Spieler hat deine Gruppe verlassen",
        description=(
            f"**{player_name}** hat deine Gruppe verlassen.\n\n"
            f"🎯 **Gruppe:** {_group_title(group)}\n"
            f"🗂️ **Freigewordener Slot:** {slot_index + 1}"
        ),
        color=0xFFAB00,
    )
    await _send_dm(creator, embed)


async def notify_creator_removed(
    bot:         discord.Client,
    group:       Dict,
    player_name: str,
    slot_index:  int,
) -> None:
    """
    Bestätigt dem Ersteller das manuelle Entfernen eines Spielers.
    """
    creator = await _get_user(bot, group["creator_id"])
    if not creator:
        return

    embed = _base_embed(
        title="✅ Spieler entfernt",
        description=(
            f"Du hast **{player_name}** aus deiner Gruppe entfernt.\n\n"
            f"🎯 **Gruppe:** {_group_title(group)}\n"
            f"🗂️ **Slot {slot_index + 1}** ist jetzt wieder offen."
        ),
        color=COLOR_OPEN,
    )
    await _send_dm(creator, embed)


# ─────────────────────────────────────────────────────────────────────────────
# SPIELER-BENACHRICHTIGUNGEN
# ─────────────────────────────────────────────────────────────────────────────

async def notify_player_removed(
    bot:        discord.Client,
    group:      Dict,
    player_id:  int,
) -> None:
    """
    Benachrichtigt einen Spieler dass er vom Ersteller entfernt wurde.
    """
    player = await _get_user(bot, player_id)
    if not player:
        return

    embed = _base_embed(
        title="❌ Du wurdest aus einer Gruppe entfernt",
        description=(
            f"Der Gruppenersteller hat dich aus der Gruppe entfernt.\n\n"
            f"🎯 **Gruppe:** {_group_title(group)}\n"
            f"👑 **Ersteller:** {group.get('creator_name', '?')}"
        ),
        color=COLOR_CLOSED,
    )
    await _send_dm(player, embed)


async def notify_group_full(
    bot:   discord.Client,
    group: Dict,
) -> None:
    """
    Informiert alle Mitglieder wenn die Gruppe vollständig belegt ist.
    """
    dt_str = group.get("datetime") or "Noch nicht festgelegt"
    rec    = RECURRENCE_OPTIONS.get(group.get("recurrence", "none"), "Einmalig")

    embed = _base_embed(
        title="🎉 Deine Gruppe ist vollständig!",
        description=(
            f"Alle Slots für **{_group_title(group)}** sind belegt.\n\n"
            f"📅 **Termin:** {dt_str}\n"
            f"🔄 **Wiederholung:** {rec}"
        ),
        color=0x00BCD4,
    )

    member_ids = _get_all_member_ids(group)
    for uid in member_ids:
        user = await _get_user(bot, uid)
        if user:
            await _send_dm(user, embed)


async def notify_group_deleted(
    bot:    discord.Client,
    group:  Dict,
    reason: str = "manuell vom Ersteller gelöscht",
) -> None:
    """
    Informiert alle Mitglieder und Wartelisten-Spieler wenn eine Gruppe gelöscht wird.
    """
    embed = _base_embed(
        title="🗑️ Gruppe wurde aufgelöst",
        description=(
            f"Die Gruppe **{_group_title(group)}** wurde {reason}.\n\n"
            f"👑 **Ersteller:** {group.get('creator_name', '?')}"
        ),
        color=COLOR_CLOSED,
    )

    all_ids = _get_all_member_ids(group) + _get_waitlist_ids(group)
    # Ersteller nicht doppelt benachrichtigen
    all_ids = list({uid for uid in all_ids if uid != group["creator_id"]})

    for uid in all_ids:
        user = await _get_user(bot, uid)
        if user:
            await _send_dm(user, embed)


async def notify_group_expired(
    bot:   discord.Client,
    group: Dict,
) -> None:
    """
    Informiert alle Beteiligten wenn eine Gruppe durch den Cleanup-Task abläuft.
    """
    embed = _base_embed(
        title="⏰ Gruppe abgelaufen",
        description=(
            f"Die Gruppe **{_group_title(group)}** wurde automatisch nach 14 Tagen Inaktivität geschlossen.\n\n"
            f"Du kannst jederzeit eine neue Gruppe erstellen."
        ),
        color=COLOR_EXPIRED,
    )

    all_ids = _get_all_member_ids(group) + _get_waitlist_ids(group)
    all_ids = list(set(all_ids))

    for uid in all_ids:
        user = await _get_user(bot, uid)
        if user:
            await _send_dm(user, embed)


async def notify_reminder(
    bot:   discord.Client,
    group: Dict,
) -> None:
    """
    Sendet eine Erinnerungs-DM an alle Mitglieder X Minuten vor Gruppenstart.
    """
    dt_str = group.get("datetime", "Unbekannt")

    embed = _base_embed(
        title="⏰ Erinnerung: Deine Gruppe startet bald!",
        description=(
            f"**{_group_title(group)}** startet in Kürze!\n\n"
            f"📅 **Startzeit:** {dt_str}\n"
            f"👥 **Mitglieder:** {_count_filled(group)}/{group.get('player_count', '?')}"
        ),
        color=0xFFAB00,
    )

    member_ids = _get_all_member_ids(group)
    for uid in member_ids:
        user = await _get_user(bot, uid)
        if user:
            await _send_dm(user, embed)


async def notify_edit(
    bot:     discord.Client,
    group:   Dict,
    changes: Dict,
) -> None:
    """
    Informiert alle Mitglieder wenn der Ersteller die Gruppe bearbeitet hat.
    changes ist ein Dict mit geänderten Feldern z.B. {"datetime": "20.11.2024 21:00"}
    """
    change_lines = []
    labels = {
        "datetime":   "📅 Datum & Zeit",
        "recurrence": "🔄 Wiederholung",
        "comment":    "💬 Kommentar",
        "level_min":  "📊 Level",
        "goal":       "🎯 Ziel",
        "goal_custom":"🎯 Ziel",
    }
    for key, val in changes.items():
        label = labels.get(key, key)
        change_lines.append(f"{label}: **{val}**")

    embed = _base_embed(
        title="✏️ Gruppendetails wurden geändert",
        description=(
            f"Der Ersteller hat **{_group_title(group)}** aktualisiert:\n\n"
            + "\n".join(change_lines)
        ),
        color=COLOR_OPEN,
    )

    member_ids = [uid for uid in _get_all_member_ids(group) if uid != group["creator_id"]]
    for uid in member_ids:
        user = await _get_user(bot, uid)
        if user:
            await _send_dm(user, embed)


# ─────────────────────────────────────────────────────────────────────────────
# WARTELISTEN-BENACHRICHTIGUNGEN
# ─────────────────────────────────────────────────────────────────────────────

async def notify_waitlist_joined(
    bot:      discord.Client,
    group:    Dict,
    player_id: int,
    position: int,
) -> None:
    """
    Bestätigt dem Spieler seinen Eintrag in die Warteliste.
    """
    player = await _get_user(bot, player_id)
    if not player:
        return

    embed = _base_embed(
        title="⏳ Du bist auf der Warteliste",
        description=(
            f"Die Gruppe **{_group_title(group)}** ist aktuell voll.\n\n"
            f"Du bist auf **Platz {position}** der Warteliste.\n"
            f"Du wirst automatisch benachrichtigt sobald ein Platz frei wird."
        ),
        color=0xFFAB00,
    )
    await _send_dm(player, embed)


async def notify_waitlist_slot_free(
    bot:       discord.Client,
    group:     Dict,
    player_id: int,
    timeout_minutes: int,
) -> None:
    """
    Benachrichtigt den nächsten Wartelisten-Spieler dass ein Slot frei ist.
    Er hat timeout_minutes Zeit um zu reagieren.
    """
    player = await _get_user(bot, player_id)
    if not player:
        return

    embed = _base_embed(
        title="🟢 Ein Platz in deiner Wartelisten-Gruppe ist frei!",
        description=(
            f"In der Gruppe **{_group_title(group)}** ist ein Slot frei geworden.\n\n"
            f"⏱️ Du hast **{timeout_minutes} Minuten** um den Slot anzunehmen.\n"
            f"Öffne die Gruppenanfrage im Discord und klicke auf **Beitreten**."
        ),
        color=COLOR_OPEN,
    )
    await _send_dm(player, embed)


async def notify_waitlist_timeout(
    bot:       discord.Client,
    group:     Dict,
    player_id: int,
) -> None:
    """
    Informiert den Wartelisten-Spieler dass sein Zeitfenster abgelaufen ist.
    """
    player = await _get_user(bot, player_id)
    if not player:
        return

    embed = _base_embed(
        title="⏰ Zeitfenster abgelaufen",
        description=(
            f"Dein reservierter Slot in der Gruppe **{_group_title(group)}** "
            f"wurde weitergegeben, da du nicht innerhalb des Zeitfensters reagiert hast.\n\n"
            f"Du bleibst auf der Warteliste und wirst beim nächsten freien Slot erneut benachrichtigt."
        ),
        color=0xFFAB00,
    )
    await _send_dm(player, embed)


async def notify_waitlist_removed(
    bot:       discord.Client,
    group:     Dict,
    player_id: int,
) -> None:
    """
    Informiert einen Spieler dass er von der Warteliste entfernt wurde
    (z.B. weil die Gruppe gelöscht wurde oder er sich selbst abgemeldet hat).
    """
    player = await _get_user(bot, player_id)
    if not player:
        return

    embed = _base_embed(
        title="❌ Von der Warteliste entfernt",
        description=(
            f"Du wurdest von der Warteliste der Gruppe **{_group_title(group)}** entfernt."
        ),
        color=COLOR_CLOSED,
    )
    await _send_dm(player, embed)


# ─────────────────────────────────────────────────────────────────────────────
# WIEDERHOLUNGS-BENACHRICHTIGUNG
# ─────────────────────────────────────────────────────────────────────────────

async def notify_recurrence_new_post(
    bot:         discord.Client,
    group:       Dict,
    new_channel: discord.TextChannel,
    new_msg_id:  int,
) -> None:
    """
    Informiert alle Mitglieder der alten Gruppe über die neue Wiederholungs-Gruppe.
    """
    dt_str = group.get("datetime", "Unbekannt")
    rec    = RECURRENCE_OPTIONS.get(group.get("recurrence", "none"), "Einmalig")

    embed = _base_embed(
        title="🔄 Neue Wiederholungs-Gruppe wurde erstellt!",
        description=(
            f"Die **{rec.lower()}e** Gruppe **{_group_title(group)}** hat einen neuen Post.\n\n"
            f"📅 **Nächster Termin:** {dt_str}\n"
            f"📌 Schau im Gruppen-Channel vorbei und melde dich erneut an."
        ),
        color=COLOR_OPEN,
    )
    # Link zur neuen Nachricht hinzufügen
    try:
        msg_url = f"https://discord.com/channels/{group['guild_id']}/{new_channel.id}/{new_msg_id}"
        embed.add_field(name="🔗 Direkt zur neuen Gruppe", value=f"[Hier klicken]({msg_url})", inline=False)
    except Exception:
        pass

    member_ids = _get_all_member_ids(group)
    for uid in member_ids:
        user = await _get_user(bot, uid)
        if user:
            await _send_dm(user, embed)


# ─────────────────────────────────────────────────────────────────────────────
# INTERNE HILFSFUNKTIONEN
# ─────────────────────────────────────────────────────────────────────────────

def _get_all_member_ids(group: Dict) -> List[int]:
    """Gibt alle Discord-User-IDs aller belegten Slots zurück."""
    ids = []
    for slot in group.get("slots", []):
        uid = slot.get("filled_by_id")
        if uid:
            ids.append(uid)
    # Ersteller immer einschließen (auch wenn er keinen Slot hat)
    creator_id = group.get("creator_id")
    if creator_id and creator_id not in ids:
        ids.append(creator_id)
    return ids


def _get_waitlist_ids(group: Dict) -> List[int]:
    """Gibt alle Discord-User-IDs der Warteliste zurück."""
    return [w["user_id"] for w in group.get("waitlist", []) if w.get("user_id")]


def _count_filled(group: Dict) -> int:
    return sum(1 for s in group.get("slots", []) if s.get("filled_by_id"))
