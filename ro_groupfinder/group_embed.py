"""
group_embed.py – Discord-Embed und Action-Views für aktive Gruppenpost

Verantwortlich für:
  - build_group_embed()         Haupt-Embed des Gruppen-Posts
  - build_group_action_view()   Buttons unter dem Post (Beitreten / Verlassen / Verwalten)
  - build_manage_view()         Verwaltungsmenü für den Gruppenersteller
  - build_join_class_view()     Klassen/Rollen-Auswahl beim Beitreten
  - build_manage_member_view()  Mitglieder-Entfernen-Menü für Ersteller
  - build_edit_view()           Bearbeitungsmenü für den Gruppenersteller
"""

import discord
from discord import ui
from typing import Optional, Dict, List

from .constants import (
    COLOR_OPEN, COLOR_FULL, COLOR_CLOSED, COLOR_EXPIRED,
    GROUP_STATUS, ROLE_TYPES, RECURRENCE_OPTIONS,
    SLOT_TYPE_ROLE, SLOT_TYPE_CLASS, SLOT_TYPE_FREE,
)
from .data_manager import (
    load_classes, get_open_slots, get_filled_slots,
    is_user_in_group, is_user_in_waitlist,
)


# ─────────────────────────────────────────────────────────────────────────────
# HAUPT-EMBED
# ─────────────────────────────────────────────────────────────────────────────

def build_group_embed(group: Dict) -> discord.Embed:
    """Baut das vollständige Discord-Embed für einen Gruppen-Post."""

    status    = group.get("status", "open")
    color_map = {
        "open":    COLOR_OPEN,
        "full":    COLOR_FULL,
        "closed":  COLOR_CLOSED,
        "expired": COLOR_EXPIRED,
    }
    color = color_map.get(status, COLOR_OPEN)

    # Ziel-Anzeige
    goal_text = group.get("goal_custom") or group.get("goal") or "–"

    embed = discord.Embed(
        title=f"🗡️ Gruppenanfrage: {goal_text}",
        color=color,
    )

    # ── Kopfzeile ─────────────────────────────────────────────────────────────
    creator = group.get("creator_name", "Unbekannt")
    embed.add_field(name="👑 Ersteller",   value=creator,                     inline=True)
    embed.add_field(name="👥 Spieler",      value=str(group.get("player_count", "?")), inline=True)
    embed.add_field(name="📊 Status",       value=_status_label(status),       inline=True)

    # ── Level-Anforderung ─────────────────────────────────────────────────────
    level_str = _level_display(group)
    embed.add_field(name="🔢 Level",        value=level_str,                   inline=True)

    # ── Datum & Zeit ──────────────────────────────────────────────────────────
    dt = group.get("datetime")
    rec = RECURRENCE_OPTIONS.get(group.get("recurrence", "none"), "Einmalig")
    embed.add_field(name="📅 Datum & Zeit", value=dt or "Offen / Zeitlos",    inline=True)
    embed.add_field(name="🔄 Wiederholung", value=rec,                         inline=True)

    # ── Slots ─────────────────────────────────────────────────────────────────
    slot_lines = _build_slot_lines(group)
    embed.add_field(
        name=f"🗂️ Slots ({_count_filled(group)}/{group.get('player_count', 0)})",
        value="\n".join(slot_lines) or "–",
        inline=False,
    )

    # ── Warteschlange ─────────────────────────────────────────────────────────
    waitlist = group.get("waitlist", [])
    if waitlist:
        wl_lines = [
            f"`{i + 1}.` {w['class_emoji']} **{w.get('ingame_name') or w['username']}**"
            + (f" ({w['class_display']})" if w.get("class_display") else "")
            for i, w in enumerate(waitlist)
        ]
        embed.add_field(
            name=f"⏳ Warteliste ({len(waitlist)})",
            value="\n".join(wl_lines),
            inline=False,
        )

    # ── Kommentar ─────────────────────────────────────────────────────────────
    comment = group.get("comment")
    if comment:
        embed.add_field(name="💬 Kommentar", value=comment, inline=False)

    # ── Footer ────────────────────────────────────────────────────────────────
    expires = group.get("expires_at", "")[:10]
    embed.set_footer(text=f"ID: {group.get('group_id', '?')[:8]}  |  Läuft ab: {expires}")

    return embed


# ─────────────────────────────────────────────────────────────────────────────
# ACTION-VIEW  (Buttons unter dem Post)
# ─────────────────────────────────────────────────────────────────────────────

def build_group_action_view(group: Dict) -> ui.View:
    """
    Erstellt die Button-Leiste unter dem Gruppen-Post.
    Die Callbacks werden im cog.py als persistent View registriert.
    Hier wird nur die View mit korrekten Labels und Disabled-States erstellt.
    """
    view    = ui.View(timeout=None)
    status  = group.get("status", "open")
    msg_id  = str(group.get("message_id", ""))

    # Beitreten
    join_btn = ui.Button(
        label="🟢 Beitreten",
        style=discord.ButtonStyle.success,
        custom_id=f"group_join:{msg_id}",
        disabled=(status == "closed" or status == "expired"),
    )
    view.add_item(join_btn)

    # Verlassen
    leave_btn = ui.Button(
        label="🔴 Verlassen",
        style=discord.ButtonStyle.danger,
        custom_id=f"group_leave:{msg_id}",
        disabled=(status == "closed" or status == "expired"),
    )
    view.add_item(leave_btn)

    # Verwalten (nur Ersteller sieht es wirklich – Prüfung im Callback)
    manage_btn = ui.Button(
        label="⚙️ Verwalten",
        style=discord.ButtonStyle.secondary,
        custom_id=f"group_manage:{msg_id}",
        disabled=(status == "closed" or status == "expired"),
    )
    view.add_item(manage_btn)

    return view


# ─────────────────────────────────────────────────────────────────────────────
# JOIN-FLOW: Klassen-/Rollen-Auswahl beim Beitreten
# ─────────────────────────────────────────────────────────────────────────────

def build_join_slot_view(group: Dict, user_id: int) -> Optional[ui.View]:
    """
    Gibt eine Ephemeral-View zurück, mit der der Spieler einen offenen Slot
    und seine Klasse/Rolle auswählen kann.
    Gibt None zurück wenn keine passenden Slots verfügbar.
    """
    open_slots = get_open_slots(group)
    if not open_slots:
        return None

    view = ui.View(timeout=120)
    msg_id = str(group.get("message_id", ""))

    # Slot-Auswahl (wenn mehrere offen)
    if len(open_slots) > 1:
        slot_options = [
            discord.SelectOption(
                label=f"Slot {s['slot_index'] + 1}: {s['emoji']} {s['display_name']}",
                value=str(s["slot_index"]),
            )
            for s in open_slots[:25]
        ]
        slot_sel = ui.Select(
            placeholder="Slot wählen...",
            options=slot_options,
            custom_id=f"join_slot_select:{msg_id}:{user_id}",
            row=0,
        )
        view.add_item(slot_sel)

    # Klasse/Rolle Auswahl
    class_options = _build_join_class_options()
    class_sel = ui.Select(
        placeholder="Deine Klasse / Rolle...",
        options=class_options,
        custom_id=f"join_class_select:{msg_id}:{user_id}",
        row=1,
    )
    view.add_item(class_sel)

    # Ingame-Name Button (öffnet Modal)
    confirm_btn = ui.Button(
        label="✅ Beitreten bestätigen",
        style=discord.ButtonStyle.success,
        custom_id=f"join_confirm:{msg_id}:{user_id}",
        row=4,
    )
    view.add_item(confirm_btn)

    cancel_btn = ui.Button(
        label="✕ Abbrechen",
        style=discord.ButtonStyle.secondary,
        custom_id=f"join_cancel:{msg_id}:{user_id}",
        row=4,
    )
    view.add_item(cancel_btn)

    return view


def _build_join_class_options() -> List[discord.SelectOption]:
    """Kombiniert Rollen-Typen + 1. Job-Klassen für die Beitrittsauswahl."""
    options = []
    # Generische Rollen zuerst
    for key, role in ROLE_TYPES.items():
        options.append(discord.SelectOption(
            label=f"{role['emoji']} {role['name']}",
            value=f"role:{key}",
            description="Generische Rolle",
        ))
    # Klassen
    for cls in load_classes()[:18]:  # Max 25 gesamt – 7 Rollen = 18 Klassen
        options.append(discord.SelectOption(
            label=f"{cls.get('emoji', '⚔️')} {cls['name']}",
            value=f"class:{cls['key']}",
        ))
    return options[:25]


# ─────────────────────────────────────────────────────────────────────────────
# VERWALTUNGS-VIEW  (für den Gruppenersteller)
# ─────────────────────────────────────────────────────────────────────────────

def build_manage_view(group: Dict) -> ui.View:
    """Verwaltungsmenü – nur für den Gruppenersteller."""
    view   = ui.View(timeout=120)
    msg_id = str(group.get("message_id", ""))

    buttons = [
        ("👥 Mitglieder verwalten", f"manage_members:{msg_id}", discord.ButtonStyle.primary),
        ("✏️ Gruppe bearbeiten",    f"manage_edit:{msg_id}",    discord.ButtonStyle.primary),
        ("🗑️ Gruppe löschen",       f"manage_delete:{msg_id}",  discord.ButtonStyle.danger),
    ]
    for label, custom_id, style in buttons:
        view.add_item(ui.Button(label=label, style=style, custom_id=custom_id))

    return view


def build_manage_members_view(group: Dict) -> ui.View:
    """Zeigt alle beigetretenen Mitglieder mit Entfernen-Buttons."""
    view     = ui.View(timeout=120)
    msg_id   = str(group.get("message_id", ""))
    filled   = get_filled_slots(group)

    if not filled:
        return view   # Leer-View wenn keine Mitglieder

    remove_options = [
        discord.SelectOption(
            label=f"Slot {s['slot_index'] + 1}: {s.get('filled_by_ingame') or s.get('filled_by_name', '?')} ({s['display_name']})",
            value=str(s["slot_index"]),
        )
        for s in filled[:25]
    ]
    remove_sel = ui.Select(
        placeholder="Spieler entfernen...",
        options=remove_options,
        custom_id=f"manage_remove_member:{msg_id}",
        row=0,
    )
    view.add_item(remove_sel)

    back_btn = ui.Button(
        label="← Zurück",
        style=discord.ButtonStyle.secondary,
        custom_id=f"manage_back:{msg_id}",
        row=4,
    )
    view.add_item(back_btn)
    return view


def build_edit_view(group: Dict) -> ui.View:
    """Bearbeitungsoptionen für den Gruppenersteller."""
    view   = ui.View(timeout=120)
    msg_id = str(group.get("message_id", ""))

    edit_options = [
        discord.SelectOption(label="📅 Datum & Zeit ändern",    value="datetime"),
        discord.SelectOption(label="🔄 Wiederholung ändern",    value="recurrence"),
        discord.SelectOption(label="💬 Kommentar ändern",        value="comment"),
        discord.SelectOption(label="📊 Level-Anforderung ändern", value="level"),
        discord.SelectOption(label="🎯 Ziel ändern",             value="goal"),
    ]
    edit_sel = ui.Select(
        placeholder="Was möchtest du bearbeiten?",
        options=edit_options,
        custom_id=f"edit_select:{msg_id}",
        row=0,
    )
    view.add_item(edit_sel)

    back_btn = ui.Button(
        label="← Zurück",
        style=discord.ButtonStyle.secondary,
        custom_id=f"manage_back:{msg_id}",
        row=4,
    )
    view.add_item(back_btn)
    return view


# ─────────────────────────────────────────────────────────────────────────────
# INTERNE HILFSFUNKTIONEN
# ─────────────────────────────────────────────────────────────────────────────

def _build_slot_lines(group: Dict) -> List[str]:
    """Erstellt die Slot-Zeilen für das Embed."""
    lines = []
    for slot in group.get("slots", []):
        emoji    = slot.get("emoji", "❓")
        name     = slot.get("display_name", "Slot")
        if slot.get("filled_by_id"):
            player = slot.get("filled_by_ingame") or slot.get("filled_by_name", "?")
            cls    = slot.get("filled_class", "")
            cls_e  = slot.get("filled_emoji", "")
            lines.append(f"{emoji} **{name}**: {cls_e} {player}" + (f" *({cls})*" if cls else ""))
        else:
            lines.append(f"{emoji} **{name}**: *Offen*")
    return lines


def _count_filled(group: Dict) -> int:
    return sum(1 for s in group.get("slots", []) if s.get("filled_by_id"))


def _status_label(status: str) -> str:
    icons = {"open": "🟢 Offen", "full": "🟡 Voll", "closed": "🔴 Geschlossen", "expired": "⚫ Abgelaufen"}
    return icons.get(status, status)


def _level_display(group: Dict) -> str:
    mode = group.get("level_mode", "none")
    if mode == "min":
        return f"Ab Level {group.get('level_min', '?')}"
    if mode == "range":
        return f"Level {group.get('level_min', '?')}–{group.get('level_max', '?')}"
    return "Kein Level erforderlich"
