"""
subscriptions.py – Abo / LFG-Alarm

Spieler abonnieren Ziele, Rollen, Klassen oder „alle neuen Gruppen" und werden
per DM benachrichtigt, wenn eine passende Gruppe entsteht ODER ein passender
Slot frei wird.

Öffentlicher Helfer (async, bot-basiert – aus cog.py UND scheduler.py nutzbar):

  notify_subscribers(bot, group)
      → Prüft alle Abonnenten der Guild, benachrichtigt passende (per DM) und
        merkt sich pro Gruppe, wer schon gepingt wurde (Dedup über
        group["notified_subscribers"]). Ersteller/Mitglieder/Warteliste werden nie
        gepingt. Best effort.

Matching berücksichtigt eine Klasse↔Rolle-Brücke über die Standard-Rolle aus
classes.json (z.B. Akolyth ↔ Rollen-Slot „Heiler").
"""

from typing import Dict, List, Optional, Set

import discord

from .constants import (
    SLOT_TYPE_FREE, SLOT_TYPE_ROLE, SLOT_TYPE_CLASS, ROLE_TYPES, COLOR_OPEN,
)
from .data_manager import (
    get_guild_subscriptions, get_open_slots, load_classes,
    resolve_goal_name, save_group,
)
from .notifications import _get_user, _send_dm


# ─────────────────────────────────────────────────────────────────────────────
# MATCHING
# ─────────────────────────────────────────────────────────────────────────────

def _role_name(role_key: str) -> str:
    return ROLE_TYPES.get(role_key, {}).get("name", role_key)


def match_reason(group: Dict, sub: Dict, class_map: Dict) -> Optional[str]:
    """
    Gibt einen kurzen Grund zurück, warum die Gruppe zum Abo passt (für die DM),
    oder None wenn sie nicht passt. class_map = {class_key: class_dict}.
    """
    if sub.get("all"):
        return "Neue Gruppe"

    goal_key = group.get("goal")
    if goal_key and goal_key in set(sub.get("goals", [])):
        return f"Ziel: {resolve_goal_name(group)}"

    sub_roles   = set(sub.get("roles", []))
    sub_classes = set(sub.get("classes", []))
    if not sub_roles and not sub_classes:
        return None

    for s in get_open_slots(group):
        st = s.get("slot_type")

        if st == SLOT_TYPE_FREE:
            return "Freier Platz (beliebig)"

        if st == SLOT_TYPE_ROLE:
            rk = s.get("role_key")
            if rk in sub_roles:
                return f"Rolle: {_role_name(rk)}"
            # Brücke: Klassen-Abo, dessen Standard-Rolle diesen Rollen-Slot trifft
            for ck in sub_classes:
                c = class_map.get(ck)
                if c and c.get("default_role") == rk:
                    return f"{c['name']} (passt in Rolle {_role_name(rk)})"

        elif st == SLOT_TYPE_CLASS:
            ck = s.get("class_key")
            if ck in sub_classes:
                c = class_map.get(ck)
                return f"Klasse: {c['name'] if c else ck}"
            # Brücke: Rollen-Abo trifft die Standard-Rolle der gesuchten Klasse
            c = class_map.get(ck)
            if c and c.get("default_role") in sub_roles:
                return f"Rolle {_role_name(c['default_role'])} (Slot: {c['name']})"

    return None


# ─────────────────────────────────────────────────────────────────────────────
# TEILNEHMER (nie pingen)
# ─────────────────────────────────────────────────────────────────────────────

def _participant_ids(group: Dict) -> Set[int]:
    ids: Set[int] = set()
    creator = group.get("creator_id")
    if creator:
        ids.add(creator)
    for slot in group.get("slots", []):
        uid = slot.get("filled_by_id")
        if uid:
            ids.add(uid)
    for w in group.get("waitlist", []):
        uid = w.get("user_id")
        if uid:
            ids.add(uid)
    return ids


# ─────────────────────────────────────────────────────────────────────────────
# DM-EMBED
# ─────────────────────────────────────────────────────────────────────────────

def _build_sub_embed(group: Dict, reason: str) -> discord.Embed:
    goal    = resolve_goal_name(group)
    creator = group.get("creator_id")
    creator_txt = f"<@{creator}>" if creator else group.get("creator_name", "?")

    filled = sum(1 for s in group.get("slots", []) if s.get("filled_by_id"))
    total  = group.get("player_count", "?")

    embed = discord.Embed(
        title="🔔 Passende Gruppe für dich!",
        description=f"**{goal}**\n\n✅ Passt zu deinem Abo: **{reason}**",
        color=COLOR_OPEN,
    )
    embed.add_field(name="👑 Leiter", value=creator_txt, inline=True)
    embed.add_field(name="👥 Belegung", value=f"{filled}/{total}", inline=True)

    guild_id   = group.get("guild_id")
    channel_id = group.get("channel_id")
    msg_id     = group.get("message_id")
    if guild_id and channel_id and msg_id:
        jump = f"https://discord.com/channels/{guild_id}/{channel_id}/{msg_id}"
        embed.add_field(name="🔗 Zur Gruppe", value=f"[Gruppen-Post öffnen]({jump})", inline=False)

    embed.set_footer(text="Abo verwalten mit /gruppe abo")
    return embed


# ─────────────────────────────────────────────────────────────────────────────
# BENACHRICHTIGEN
# ─────────────────────────────────────────────────────────────────────────────

async def notify_subscribers(bot, group: Dict) -> None:
    """
    Benachrichtigt passende Abonnenten der Guild per DM. Wird bei Erstellung und
    wenn ein Slot frei wird aufgerufen; über group["notified_subscribers"] wird
    jeder Abonnent pro Gruppe nur einmal gepingt.
    """
    guild_id = group.get("guild_id")
    if not guild_id:
        return

    subs = get_guild_subscriptions(guild_id)
    if not subs:
        return

    # Nur aktive Gruppen bewerben
    if group.get("status") not in ("open", "full"):
        return

    already   = set(group.get("notified_subscribers", []))
    exclude   = _participant_ids(group)
    class_map = {c["key"]: c for c in load_classes()}
    changed   = False

    for ukey, sub in subs.items():
        try:
            uid = int(ukey)
        except (TypeError, ValueError):
            continue
        if uid in already or uid in exclude:
            continue

        reason = match_reason(group, sub, class_map)
        if not reason:
            continue

        user = await _get_user(bot, uid)
        if user is not None:
            await _send_dm(user, _build_sub_embed(group, reason))

        # Auch bei geschlossenen DMs vormerken, um Wiederhol-Versuche zu vermeiden.
        already.add(uid)
        changed = True

    if changed:
        group["notified_subscribers"] = list(already)
        save_group(guild_id, group)
