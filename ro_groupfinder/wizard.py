"""
wizard.py – Interaktiver Gruppen-Erstellungs-Wizard

Ablauf:
  Schritt 1  goal          Ziel der Gruppe
  Schritt 2  player_count  Spieleranzahl
  Schritt 3  slots         Slot-Konfiguration (iterativ, mit Menge)
  Schritt 4  members       Mitglieder vorab hinzufügen (Pflicht: sich selbst)
  Schritt 5  datetime      Datum & Uhrzeit (optional)
  Schritt 6  recurrence    Wiederholung (optional, nur wenn Datum gesetzt)
  Schritt 7  comment       Kommentar (optional)
  Schritt 8  level         Level-Anforderung (optional)
  Schritt 9  preview       Vorschau & Bestätigen

Navigation:
  - "← Zurück"   springt einen Schritt zurück (Schritt 6 wird übersprungen wenn kein Datum)
  - "Weiter →"   nur aktiv wenn Schritt vollständig ausgefüllt
  - "✕ Abbrechen" bricht den gesamten Wizard ab
"""

import discord
from discord import ui
from dataclasses import dataclass, field
from typing import Optional, List, Dict, Callable
from datetime import datetime, timezone
import re

from .data_manager import load_goals, load_classes, build_slot
from .constants import (
    ROLE_TYPES, RECURRENCE_OPTIONS, WIZARD_STEPS,
    SLOT_TYPE_ROLE, SLOT_TYPE_CLASS, SLOT_TYPE_FREE,
    COLOR_OPEN,
)


# ─────────────────────────────────────────────────────────────────────────────
# DATEN-KLASSEN
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class SlotConfig:
    """Repräsentiert eine Gruppe gleicher Slots (z.B. 2× Dieb)."""
    slot_type:    str            # SLOT_TYPE_ROLE | SLOT_TYPE_CLASS | SLOT_TYPE_FREE
    key:          Optional[str]  # role_key oder class_key
    display_name: str            # Anzeigename
    emoji:        str
    free_text:    Optional[str]  # Nur für SLOT_TYPE_FREE
    quantity:     int

    def to_dict(self) -> Dict:
        return {
            "slot_type":    self.slot_type,
            "key":          self.key,
            "display_name": self.display_name,
            "emoji":        self.emoji,
            "free_text":    self.free_text,
            "quantity":     self.quantity,
        }


@dataclass
class WizardState:
    """Hält den gesamten Zustand des Wizards für einen Nutzer."""

    guild_id:     int
    channel_id:   int
    creator_id:   int
    creator_name: str

    # Schritt 1 – Ziel
    goal_key:    Optional[str] = None   # Key aus goals.json oder "__custom__"
    goal_label:  Optional[str] = None
    goal_emoji:  Optional[str] = None
    goal_custom: Optional[str] = None   # Freitext wenn goal_key == "__custom__"

    # Schritt 2 – Spieleranzahl
    player_count: int = 0

    # Schritt 3 – Slots (konfigurierte Gruppen)
    slot_configs: List[SlotConfig] = field(default_factory=list)
    # Draft: aktuell konfigurierter Slot (noch nicht committed)
    draft_type:      Optional[str] = None
    draft_key:       Optional[str] = None
    draft_display:   Optional[str] = None
    draft_emoji:     Optional[str] = None
    draft_free_text: Optional[str] = None
    draft_quantity:  Optional[int] = None

    # Schritt 4 – Vorausgefüllte Mitglieder
    # Jedes Dict: {slot_index, ingame_name, is_creator}
    prefilled_members:  List[Dict] = field(default_factory=list)
    draft_member_slot:  Optional[int] = None   # Aktuell im MembersView ausgewählter Slot

    # Schritt 5 – Datum & Zeit
    dt_str:    Optional[str] = None   # Format: "DD.MM.YYYY HH:MM"
    _dt_day:   Optional[int] = None   # Tag (1-31)
    _dt_month: Optional[int] = None   # Monat (1-12)
    _dt_year:  Optional[int] = None   # Jahr
    _dt_time:  Optional[str] = None   # "HH:MM"

    # Schritt 6 – Wiederholung
    recurrence: str = "none"

    # Schritt 7 – Kommentar
    comment: Optional[str] = None

    # Schritt 8 – Level
    level_mode: str = "none"       # "none" | "min" | "range"
    level_min:  Optional[int] = None
    level_max:  Optional[int] = None

    step_index: int = 0

    # ── Computed properties ──────────────────────────────────────────────────

    @property
    def step(self) -> str:
        return WIZARD_STEPS[self.step_index]

    @property
    def slots_assigned(self) -> int:
        return sum(sc.quantity for sc in self.slot_configs)

    @property
    def slots_remaining(self) -> int:
        return self.player_count - self.slots_assigned

    @property
    def draft_ready(self) -> bool:
        """True wenn der Draft vollständig (Typ + Item + Menge) ist."""
        has_item = self.draft_key is not None or self.draft_free_text is not None
        return self.draft_type is not None and has_item and self.draft_quantity is not None

    @property
    def creator_added(self) -> bool:
        return any(m["is_creator"] for m in self.prefilled_members)

    @property
    def goal_display(self) -> str:
        if self.goal_key == "__custom__":
            return f"✏️ {self.goal_custom}" if self.goal_custom else "✏️ Eigenes Ziel"
        if self.goal_label:
            e = f"{self.goal_emoji} " if self.goal_emoji else ""
            return f"{e}{self.goal_label}"
        return "–"

    @property
    def slots_summary(self) -> str:
        if not self.slot_configs:
            return "*Noch keine Slots konfiguriert*"
        return "\n".join(
            f"{sc.emoji} **{sc.quantity}×** {sc.display_name}"
            for sc in self.slot_configs
        )

    def level_display(self) -> str:
        if self.level_mode == "none" or self.level_min is None:
            return "Kein Level erforderlich"
        if self.level_mode == "min":
            return f"Ab Level {self.level_min}"
        if self.level_mode == "range":
            max_part = str(self.level_max) if self.level_max is not None else "?"
            return f"Level {self.level_min}–{max_part}"
        return "–"

    # ── Navigation ───────────────────────────────────────────────────────────

    def next_step(self) -> None:
        idx = self.step_index + 1
        while idx < len(WIZARD_STEPS):
            if WIZARD_STEPS[idx] == "recurrence" and not self.dt_str:
                idx += 1
            else:
                break
        self.step_index = min(idx, len(WIZARD_STEPS) - 1)

    def prev_step(self) -> None:
        idx = self.step_index - 1
        while idx > 0:
            if WIZARD_STEPS[idx] == "recurrence" and not self.dt_str:
                idx -= 1
            else:
                break
        self.step_index = max(idx, 0)

    # ── Draft-Verwaltung (Schritt 3) ─────────────────────────────────────────

    def clear_draft(self) -> None:
        self.draft_type = self.draft_key = self.draft_display = None
        self.draft_emoji = self.draft_free_text = None
        self.draft_quantity = None

    def commit_draft(self) -> None:
        """Fügt den aktuellen Draft zu slot_configs hinzu."""
        if not self.draft_ready:
            return
        self.slot_configs.append(SlotConfig(
            slot_type=self.draft_type,
            key=self.draft_key,
            display_name=self.draft_display or self.draft_free_text or "Slot",
            emoji=self.draft_emoji or "❓",
            free_text=self.draft_free_text,
            quantity=self.draft_quantity,
        ))
        self.clear_draft()

    def remove_last_slot(self) -> bool:
        if self.slot_configs:
            self.slot_configs.pop()
            self.clear_draft()
            return True
        return False

    # ── Slot-Expansion ───────────────────────────────────────────────────────

    def expand_slots(self) -> List[Dict]:
        """Expandiert slot_configs in individuelle Slot-Dicts (für data_manager)."""
        slots, idx = [], 0
        for sc in self.slot_configs:
            for _ in range(sc.quantity):
                slots.append(build_slot(
                    slot_index=idx,
                    slot_type=sc.slot_type,
                    display_name=sc.display_name,
                    emoji=sc.emoji,
                    class_key=sc.key if sc.slot_type == SLOT_TYPE_CLASS else None,
                    role_key=sc.key if sc.slot_type == SLOT_TYPE_ROLE else None,
                    free_text=sc.free_text,
                ))
                idx += 1
        return slots

    def get_slot_label(self, slot_index: int) -> str:
        slots = self.expand_slots()
        if slot_index < len(slots):
            s = slots[slot_index]
            return f"Slot {slot_index + 1}: {s['emoji']} {s['display_name']}"
        return f"Slot {slot_index + 1}"

    def occupied_slot_indices(self) -> List[int]:
        return [m["slot_index"] for m in self.prefilled_members]

    def free_slot_indices(self) -> List[int]:
        occupied = set(self.occupied_slot_indices())
        return [i for i in range(self.player_count) if i not in occupied]


# ─────────────────────────────────────────────────────────────────────────────
# WIZARD SESSION
# ─────────────────────────────────────────────────────────────────────────────

class WizardSession:
    """
    Verwaltet den gesamten Wizard-Ablauf für einen Nutzer.
    Hält den WizardState und die Discord-Interaktion.
    """

    def __init__(
        self,
        state:       WizardState,
        on_complete: Callable,   # async (interaction, state) -> None
        on_cancel:   Callable,   # async (interaction) -> None
    ):
        self.state = state
        self.on_complete = on_complete
        self.on_cancel = on_cancel
        self._last_interaction: Optional[discord.Interaction] = None

    async def start(self, interaction: discord.Interaction) -> None:
        """Schickt die erste Wizard-Nachricht als Ephemeral an den Nutzer."""
        self._last_interaction = interaction
        embed, view = self._render()
        await interaction.response.send_message(embed=embed, view=view, ephemeral=True)

    async def refresh(self, interaction: discord.Interaction) -> None:
        """Aktualisiert die Wizard-Nachricht nach einer Komponenten-Interaktion."""
        self._last_interaction = interaction
        embed, view = self._render()
        await interaction.response.edit_message(embed=embed, view=view)

    async def refresh_after_modal(self, modal_interaction: discord.Interaction) -> None:
        """
        Aktualisiert die Wizard-Nachricht nach einem Modal-Submit.
        Da Modal-Antworten kein edit_message erlauben, wird die letzte
        Component-Interaktion zum Bearbeiten der Originalnachricht genutzt.
        """
        embed, view = self._render()
        await modal_interaction.response.defer()
        if self._last_interaction:
            await self._last_interaction.edit_original_response(embed=embed, view=view)

    def _render(self):
        """Gibt (embed, view) für den aktuellen Wizard-Schritt zurück."""
        step = self.state.step
        builders = {
            "goal":         _build_goal_step,
            "player_count": _build_player_count_step,
            "slots":        _build_slots_step,
            "members":      _build_members_step,
            "datetime":     _build_datetime_step,
            "recurrence":   _build_recurrence_step,
            "comment":      _build_comment_step,
            "level":        _build_level_step,
            "preview":      _build_preview_step,
        }
        return builders[step](self)


# ─────────────────────────────────────────────────────────────────────────────
# EMBED-HELFER
# ─────────────────────────────────────────────────────────────────────────────

def _base_embed(state: WizardState, step_name: str, description: str = "") -> discord.Embed:
    step_num = state.step_index + 1
    total    = len(WIZARD_STEPS)
    embed = discord.Embed(
        title=f"🗡️ Gruppe erstellen – Schritt {step_num}/{total}: {step_name}",
        description=description,
        color=COLOR_OPEN,
    )
    done  = "✅ " * state.step_index
    curr  = "▶️ "
    todo  = "⬜ " * (total - step_num)
    embed.set_footer(text=f"{done}{curr}{todo}")
    return embed


# ─────────────────────────────────────────────────────────────────────────────
# SCHRITT-BUILDER  (geben jeweils Embed + View zurück)
# ─────────────────────────────────────────────────────────────────────────────

def _build_goal_step(session: WizardSession):
    s = session.state
    desc = "**Wähle das Ziel deiner Gruppe:**\n"
    desc += "Vordefinierte Optionen oder ein eigenes Ziel als Freitext.\n\n"
    if s.goal_key:
        desc += f"✅ Ausgewählt: **{s.goal_display}**"
    return _base_embed(s, "Ziel", desc), GoalView(session)


def _build_player_count_step(session: WizardSession):
    s = session.state
    desc = "**Wie viele Spieler suchst du insgesamt? (inkl. dir selbst)**\n\n"
    if s.player_count:
        desc += f"✅ Ausgewählt: **{s.player_count} Spieler**"
        if s.slot_configs:
            desc += "\n\n⚠️ *Eine Änderung der Spieleranzahl löscht die Slot-Konfiguration.*"
    return _base_embed(s, "Spieleranzahl", desc), PlayerCountView(session)


def _build_slots_step(session: WizardSession):
    s = session.state
    assigned  = s.slots_assigned
    remaining = s.slots_remaining

    desc  = f"**Konfiguriere die Slots für deine Gruppe.**\n"
    desc += f"Spieleranzahl: **{s.player_count}** | Vergeben: **{assigned}** | Ausstehend: **{remaining}**\n\n"

    if s.slot_configs:
        desc += "**Konfigurierte Slots:**\n" + s.slots_summary + "\n\n"

    # Draft-Zusammenfassung anzeigen
    if s.draft_type:
        draft_info = f"*Entwurf: {s.draft_type}"
        if s.draft_display:  draft_info += f" → {s.draft_display}"
        if s.draft_quantity: draft_info += f" × {s.draft_quantity}"
        desc += draft_info + "*\n"

    if remaining == 0:
        desc += "\n✅ **Alle Slots konfiguriert!**"

    return _base_embed(s, "Slots konfigurieren", desc), SlotsView(session)


def _build_members_step(session: WizardSession):
    s     = session.state
    slots = s.expand_slots()
    desc  = "**Füge vorab bekannte Mitglieder hinzu.**\n"
    desc += "Du **musst** dich selbst hinzufügen. 👑 = du\n\n"

    for i, slot in enumerate(slots):
        pm = next((m for m in s.prefilled_members if m["slot_index"] == i), None)
        if pm:
            tag = " 👑" if pm.get("is_creator") else ""
            desc += f"{slot['emoji']} **Slot {i + 1}** ({slot['display_name']}): **{pm['ingame_name']}**{tag}\n"
        else:
            desc += f"{slot['emoji']} **Slot {i + 1}** ({slot['display_name']}): *Offen*\n"

    if not s.creator_added:
        desc += "\n⚠️ **Du hast dich noch nicht hinzugefügt!**"

    return _base_embed(s, "Mitglieder", desc), MembersView(session, slots)


def _build_datetime_step(session: WizardSession):
    s = session.state
    desc = "**Wann trifft sich die Gruppe? (Optional)**\n\n"
    if s.dt_str:
        desc += f"✅ **{s.dt_str}**"
    else:
        desc += "Kein Datum gesetzt → Gruppe gilt als **offen / zeitlos**."
    return _base_embed(s, "Datum & Zeit", desc), DateTimeView(session)


def _build_recurrence_step(session: WizardSession):
    s = session.state
    rec_label = RECURRENCE_OPTIONS.get(s.recurrence, "Einmalig")
    desc = f"**Wie oft trifft sich die Gruppe?**\n\n✅ Ausgewählt: **{rec_label}**"
    return _base_embed(s, "Wiederholung", desc), RecurrenceView(session)


def _build_comment_step(session: WizardSession):
    s = session.state
    desc = "**Möchtest du einen Kommentar hinzufügen? (Optional)**\n\n"
    if s.comment:
        desc += f"✅ Kommentar:\n> {s.comment}"
    else:
        desc += "Kein Kommentar gesetzt."
    return _base_embed(s, "Kommentar", desc), CommentView(session)


def _build_level_step(session: WizardSession):
    s = session.state
    desc = "**Level-Anforderung für die Gruppe? (Optional)**\n"
    desc += "Dient nur als Information – Spieler müssen ihr Level nicht angeben.\n\n"
    desc += f"✅ Aktuell: **{s.level_display()}**"
    return _base_embed(s, "Level-Anforderung", desc), LevelView(session)


def _build_preview_step(session: WizardSession):
    s     = session.state
    slots = s.expand_slots()

    embed = discord.Embed(
        title="🗡️ Vorschau – Gruppenanfrage",
        description="Überprüfe die Einstellungen. Klicke **Posten** um die Gruppe zu veröffentlichen.",
        color=COLOR_OPEN,
    )
    embed.add_field(name="🎯 Ziel",          value=s.goal_display,           inline=True)
    embed.add_field(name="👥 Spieler",        value=str(s.player_count),      inline=True)
    embed.add_field(name="📊 Level",          value=s.level_display(),        inline=True)
    embed.add_field(
        name="📅 Datum & Zeit",
        value=s.dt_str or "Offen / Zeitlos",
        inline=True,
    )
    embed.add_field(
        name="🔄 Wiederholung",
        value=RECURRENCE_OPTIONS.get(s.recurrence, "Einmalig"),
        inline=True,
    )
    embed.add_field(name="👤 Ersteller", value=s.creator_name, inline=True)

    slot_lines = []
    for i, slot in enumerate(slots):
        pm = next((m for m in s.prefilled_members if m["slot_index"] == i), None)
        if pm:
            tag = " 👑" if pm.get("is_creator") else ""
            slot_lines.append(f"{slot['emoji']} {slot['display_name']}: **{pm['ingame_name']}**{tag}")
        else:
            slot_lines.append(f"{slot['emoji']} {slot['display_name']}: *Offen*")

    embed.add_field(
        name=f"🗂️ Slots ({len(slots)})",
        value="\n".join(slot_lines) or "–",
        inline=False,
    )
    if s.comment:
        embed.add_field(name="💬 Kommentar", value=s.comment, inline=False)

    return embed, PreviewView(session)


# ─────────────────────────────────────────────────────────────────────────────
# BASIS-KLASSEN FÜR NAVIGATION
# ─────────────────────────────────────────────────────────────────────────────

class _BaseWizardView(ui.View):
    def __init__(self, session: WizardSession, timeout: int = 600):
        super().__init__(timeout=timeout)
        self.session = session

    def add_nav(self, can_back: bool, can_next: bool) -> None:
        """Fügt Zurück / Abbrechen / Weiter-Buttons in Row 4 hinzu."""
        if can_back:
            self.add_item(_BackBtn(self.session))
        self.add_item(_CancelBtn(self.session))
        self.add_item(_NextBtn(self.session, disabled=not can_next))


class _BackBtn(ui.Button):
    def __init__(self, session: WizardSession):
        super().__init__(label="← Zurück", style=discord.ButtonStyle.secondary, row=4)
        self.session = session

    async def callback(self, interaction: discord.Interaction):
        self.session.state.prev_step()
        await self.session.refresh(interaction)


class _NextBtn(ui.Button):
    def __init__(self, session: WizardSession, disabled: bool = False):
        super().__init__(
            label="Weiter →", style=discord.ButtonStyle.primary,
            disabled=disabled, row=4,
        )
        self.session = session

    async def callback(self, interaction: discord.Interaction):
        self.session.state.next_step()
        await self.session.refresh(interaction)


class _CancelBtn(ui.Button):
    def __init__(self, session: WizardSession):
        super().__init__(label="✕ Abbrechen", style=discord.ButtonStyle.danger, row=4)
        self.session = session

    async def callback(self, interaction: discord.Interaction):
        await self.session.on_cancel(interaction)


# ─────────────────────────────────────────────────────────────────────────────
# SCHRITT 1 – ZIEL
# ─────────────────────────────────────────────────────────────────────────────

class GoalView(_BaseWizardView):
    def __init__(self, session: WizardSession):
        super().__init__(session)
        goals = load_goals()
        options = []
        for g in goals[:24]:
            options.append(discord.SelectOption(
                label=g["name"][:100],
                value=g["key"],
                emoji=g.get("emoji", "🎯"),
                default=(session.state.goal_key == g["key"]),
            ))
        options.append(discord.SelectOption(
            label="Eigenes Ziel eingeben...",
            value="__custom__",
            emoji="✏️",
            default=(session.state.goal_key == "__custom__"),
        ))
        self.add_item(_GoalSelect(session, options))
        self.add_nav(can_back=False, can_next=session.state.goal_key is not None)


class _GoalSelect(ui.Select):
    def __init__(self, session: WizardSession, options: list):
        super().__init__(placeholder="Ziel wählen...", options=options, row=0)
        self.session = session

    async def callback(self, interaction: discord.Interaction):
        value = self.values[0]
        s = self.session.state
        if value == "__custom__":
            s.goal_key = "__custom__"
            await interaction.response.send_modal(GoalCustomModal(self.session))
        else:
            goals = load_goals()
            goal  = next((g for g in goals if g["key"] == value), None)
            if goal:
                s.goal_key    = goal["key"]
                s.goal_label  = goal["name"]
                s.goal_emoji  = goal.get("emoji", "🎯")
                s.goal_custom = None
            await self.session.refresh(interaction)


class GoalCustomModal(ui.Modal, title="Eigenes Ziel eingeben"):
    goal_input = ui.TextInput(
        label="Ziel der Gruppe",
        placeholder="z.B. Leveln in Prontera Fields, Daily Quests, ...",
        max_length=100,
    )

    def __init__(self, session: WizardSession):
        super().__init__()
        self.session = session

    async def on_submit(self, interaction: discord.Interaction):
        self.session.state.goal_custom = self.goal_input.value.strip()
        await self.session.refresh_after_modal(interaction)


# ─────────────────────────────────────────────────────────────────────────────
# SCHRITT 2 – SPIELERANZAHL
# ─────────────────────────────────────────────────────────────────────────────

class PlayerCountView(_BaseWizardView):
    def __init__(self, session: WizardSession):
        super().__init__(session)
        options = [
            discord.SelectOption(
                label=f"{i} Spieler",
                value=str(i),
                default=(session.state.player_count == i),
            )
            for i in range(2, 13)
        ]
        self.add_item(_PlayerCountSelect(session, options))
        self.add_nav(can_back=True, can_next=session.state.player_count > 0)


class _PlayerCountSelect(ui.Select):
    def __init__(self, session: WizardSession, options: list):
        super().__init__(placeholder="Spieleranzahl wählen...", options=options, row=0)
        self.session = session

    async def callback(self, interaction: discord.Interaction):
        new_count = int(self.values[0])
        s = self.session.state
        if new_count != s.player_count:
            # Slot-Konfiguration und Mitglieder zurücksetzen
            s.player_count = new_count
            s.slot_configs.clear()
            s.prefilled_members.clear()
            s.clear_draft()
        await self.session.refresh(interaction)


# ─────────────────────────────────────────────────────────────────────────────
# SCHRITT 3 – SLOTS (iterativ)
# ─────────────────────────────────────────────────────────────────────────────

class SlotsView(_BaseWizardView):
    def __init__(self, session: WizardSession):
        super().__init__(session)
        s         = session.state
        remaining = s.slots_remaining

        # ── Row 0: Typ-Auswahl ────────────────────────────────────────────────
        type_options = [
            discord.SelectOption(
                label="Rolle  (Tank / Heiler / DD / Support / Beliebig)",
                value=SLOT_TYPE_ROLE,
                emoji="🎭",
                default=(s.draft_type == SLOT_TYPE_ROLE),
            ),
            discord.SelectOption(
                label="Klasse  (1. Job – Dieb, Magier, ...)",
                value=SLOT_TYPE_CLASS,
                emoji="⚔️",
                default=(s.draft_type == SLOT_TYPE_CLASS),
            ),
            discord.SelectOption(
                label="Freitext  (RP, Quest, Beliebig, ...)",
                value=SLOT_TYPE_FREE,
                emoji="✏️",
                default=(s.draft_type == SLOT_TYPE_FREE),
            ),
        ]
        # Deaktiviert wenn keine Slots mehr übrig und kein Draft aktiv
        self.add_item(_SlotTypeSelect(session, type_options, disabled=(remaining == 0 and not s.draft_type)))

        # ── Row 1: Item-Auswahl (abhängig vom Typ) ───────────────────────────
        if s.draft_type == SLOT_TYPE_ROLE:
            self.add_item(_SlotRoleSelect(session))
        elif s.draft_type == SLOT_TYPE_CLASS:
            self.add_item(_SlotClassSelect(session))
        elif s.draft_type == SLOT_TYPE_FREE:
            label = f"✏️ Text: \"{s.draft_free_text}\"" if s.draft_free_text else "✏️ Text eingeben"
            btn = _OpenModalBtn(label, SlotFreeTextModal(session), row=1)
            self.add_item(btn)

        # ── Row 2: Menge (nur wenn Typ + Item gewählt) ───────────────────────
        has_item = s.draft_key is not None or s.draft_free_text is not None
        if s.draft_type and has_item and remaining > 0:
            qty_options = [
                discord.SelectOption(
                    label=f"{i}×",
                    value=str(i),
                    default=(s.draft_quantity == i),
                )
                for i in range(1, remaining + 1)
            ]
            self.add_item(_SlotQuantitySelect(session, qty_options))

        # ── Row 3: Aktions-Buttons ────────────────────────────────────────────
        self.add_item(_AddSlotBtn(session, disabled=not s.draft_ready))
        if s.slot_configs:
            self.add_item(_RemoveLastSlotBtn(session))

        # ── Row 4: Navigation ─────────────────────────────────────────────────
        self.add_nav(can_back=True, can_next=(remaining == 0))


class _SlotTypeSelect(ui.Select):
    def __init__(self, session: WizardSession, options: list, disabled: bool = False):
        super().__init__(
            placeholder="1. Slot-Typ wählen...",
            options=options,
            row=0,
            disabled=disabled,
        )
        self.session = session

    async def callback(self, interaction: discord.Interaction):
        new_type = self.values[0]
        s = self.session.state
        if new_type != s.draft_type:
            s.clear_draft()
            s.draft_type = new_type
        await self.session.refresh(interaction)


class _SlotRoleSelect(ui.Select):
    def __init__(self, session: WizardSession):
        options = [
            discord.SelectOption(
                label=f"{v['emoji']} {v['name']}",
                value=k,
                default=(session.state.draft_key == k),
            )
            for k, v in ROLE_TYPES.items()
        ]
        super().__init__(placeholder="2. Rolle wählen...", options=options, row=1)
        self.session = session

    async def callback(self, interaction: discord.Interaction):
        s   = self.session.state
        key = self.values[0]
        role = ROLE_TYPES[key]
        s.draft_key      = key
        s.draft_display  = role["name"]
        s.draft_emoji    = role["emoji"]
        s.draft_quantity = None  # Menge zurücksetzen
        await self.session.refresh(interaction)


class _SlotClassSelect(ui.Select):
    def __init__(self, session: WizardSession):
        classes = load_classes()
        options = [
            discord.SelectOption(
                label=f"{c.get('emoji', '⚔️')} {c['name']}",
                value=c["key"],
                default=(session.state.draft_key == c["key"]),
            )
            for c in classes[:25]
        ]
        super().__init__(placeholder="2. Klasse wählen...", options=options, row=1)
        self.session = session

    async def callback(self, interaction: discord.Interaction):
        s   = self.session.state
        key = self.values[0]
        cls = next((c for c in load_classes() if c["key"] == key), None)
        if cls:
            s.draft_key      = cls["key"]
            s.draft_display  = cls["name"]
            s.draft_emoji    = cls.get("emoji", "⚔️")
            s.draft_quantity = None
        await self.session.refresh(interaction)


class _SlotQuantitySelect(ui.Select):
    def __init__(self, session: WizardSession, options: list):
        super().__init__(placeholder="3. Menge wählen...", options=options, row=2)
        self.session = session

    async def callback(self, interaction: discord.Interaction):
        self.session.state.draft_quantity = int(self.values[0])
        await self.session.refresh(interaction)


class _AddSlotBtn(ui.Button):
    def __init__(self, session: WizardSession, disabled: bool):
        super().__init__(
            label="+ Hinzufügen",
            style=discord.ButtonStyle.success,
            disabled=disabled,
            row=3,
        )
        self.session = session

    async def callback(self, interaction: discord.Interaction):
        self.session.state.commit_draft()
        await self.session.refresh(interaction)


class _RemoveLastSlotBtn(ui.Button):
    def __init__(self, session: WizardSession):
        super().__init__(label="🗑️ Letzten entfernen", style=discord.ButtonStyle.secondary, row=3)
        self.session = session

    async def callback(self, interaction: discord.Interaction):
        self.session.state.remove_last_slot()
        await self.session.refresh(interaction)


class _OpenModalBtn(ui.Button):
    """Universeller Button der ein Modal öffnet."""
    def __init__(self, label: str, modal: ui.Modal, row: int = 1):
        super().__init__(label=label, style=discord.ButtonStyle.secondary, row=row)
        self._modal = modal

    async def callback(self, interaction: discord.Interaction):
        await interaction.response.send_modal(self._modal)


class SlotFreeTextModal(ui.Modal, title="Slot Beschreibung"):
    text_input = ui.TextInput(
        label="Beschreibung des gesuchten Spielers",
        placeholder="z.B. RP-Teilnehmer, Beliebig, Quest-Begleiter, ...",
        max_length=50,
    )

    def __init__(self, session: WizardSession):
        super().__init__()
        self.session = session

    async def on_submit(self, interaction: discord.Interaction):
        text = self.text_input.value.strip()
        s = self.session.state
        s.draft_free_text = text
        s.draft_display   = text
        s.draft_emoji     = "✏️"
        s.draft_quantity  = None
        await self.session.refresh_after_modal(interaction)


# ─────────────────────────────────────────────────────────────────────────────
# SCHRITT 4 – MITGLIEDER
# ─────────────────────────────────────────────────────────────────────────────

class MembersView(_BaseWizardView):
    def __init__(self, session: WizardSession, slots: List[Dict]):
        super().__init__(session)
        s           = session.state
        free_slots  = s.free_slot_indices()
        filled_indices = s.occupied_slot_indices()

        # ── Row 0: Slot-Auswahl (nur freie Slots) ────────────────────────────
        if free_slots:
            options = [
                discord.SelectOption(
                    label=s.get_slot_label(i)[:100],
                    value=str(i),
                    default=(s.draft_member_slot == i),
                )
                for i in free_slots
            ]
            self.add_item(_MemberSlotSelect(session, options))

        # ── Row 1: Hinzufügen-Button ─────────────────────────────────────────
        can_add = s.draft_member_slot is not None and s.draft_member_slot in free_slots
        self.add_item(_AddMemberBtn(session, disabled=not can_add))

        # ── Row 2: Entfernen-Select (wenn Mitglieder vorhanden) ──────────────
        if s.prefilled_members:
            remove_options = [
                discord.SelectOption(
                    label=f"Slot {m['slot_index'] + 1}: {m['ingame_name']}",
                    value=str(m["slot_index"]),
                )
                for m in s.prefilled_members
            ]
            self.add_item(_RemoveMemberSelect(session, remove_options))

        # ── Row 4: Navigation ─────────────────────────────────────────────────
        self.add_nav(can_back=True, can_next=s.creator_added)


class _MemberSlotSelect(ui.Select):
    def __init__(self, session: WizardSession, options: list):
        super().__init__(placeholder="Slot auswählen...", options=options, row=0)
        self.session = session

    async def callback(self, interaction: discord.Interaction):
        self.session.state.draft_member_slot = int(self.values[0])
        await self.session.refresh(interaction)


class _AddMemberBtn(ui.Button):
    def __init__(self, session: WizardSession, disabled: bool):
        super().__init__(
            label="👤 Spieler zu Slot hinzufügen",
            style=discord.ButtonStyle.primary,
            disabled=disabled,
            row=1,
        )
        self.session = session

    async def callback(self, interaction: discord.Interaction):
        slot_index = self.session.state.draft_member_slot
        if slot_index is None:
            await interaction.response.defer()
            return
        await interaction.response.send_modal(AddMemberModal(self.session, slot_index))


class _RemoveMemberSelect(ui.Select):
    def __init__(self, session: WizardSession, options: list):
        super().__init__(
            placeholder="Mitglied entfernen...",
            options=options[:25],
            row=2,
        )
        self.session = session

    async def callback(self, interaction: discord.Interaction):
        slot_idx = int(self.values[0])
        s = self.session.state
        s.prefilled_members = [m for m in s.prefilled_members if m["slot_index"] != slot_idx]
        s.draft_member_slot = None
        await self.session.refresh(interaction)


class AddMemberModal(ui.Modal, title="Spieler hinzufügen"):
    ingame_name = ui.TextInput(
        label="In-Game Name",
        placeholder="Dein Charakter-Name in Ragnarok",
        max_length=50,
    )

    def __init__(self, session: WizardSession, slot_index: int):
        super().__init__()
        self.session     = session
        self.slot_index  = slot_index
        # Titel dynamisch setzen
        slot_label = session.state.get_slot_label(slot_index)
        self.title = f"Spieler für {slot_label}"

    async def on_submit(self, interaction: discord.Interaction):
        s    = self.session.state
        name = self.ingame_name.value.strip()
        is_creator = (interaction.user.id == s.creator_id)
        s.prefilled_members.append({
            "slot_index":  self.slot_index,
            "ingame_name": name,
            "is_creator":  is_creator,
            "user_id":     interaction.user.id if is_creator else None,
        })
        s.draft_member_slot = None
        await self.session.refresh_after_modal(interaction)


# ─────────────────────────────────────────────────────────────────────────────
# SCHRITT 5 – DATUM & ZEIT
# ─────────────────────────────────────────────────────────────────────────────

class DateTimeView(_BaseWizardView):
    """Datum & Zeit über Dropdowns – Discord hat keinen nativen Date-Picker."""
    def __init__(self, session: WizardSession):
        super().__init__(session)
        s = session.state
        from datetime import date as _date
        current_year = _date.today().year
        months = ["Januar","Februar","März","April","Mai","Juni",
                  "Juli","August","September","Oktober","November","Dezember"]

        # Row 0: Tag
        day_options = [
            discord.SelectOption(label=f"{d:02d}.", value=str(d), default=(s._dt_day == d))
            for d in range(1, 32)
        ]
        self.add_item(_DatePartSelect(session, day_options, "day", "📆 Tag wählen...", row=0))

        # Row 1: Monat + Jahr
        my_options = []
        for y in [current_year, current_year + 1]:
            for mi, mn in enumerate(months, 1):
                my_options.append(discord.SelectOption(
                    label=f"{mn} {y}", value=f"{mi:02d}.{y}",
                    default=(s._dt_month == mi and s._dt_year == y)
                ))
        self.add_item(_DatePartSelect(session, my_options[:25], "month_year", "📅 Monat & Jahr wählen...", row=1))

        # Row 2: Uhrzeit (30-Min-Schritte)
        time_opts = [
            discord.SelectOption(label=f"{h:02d}:{m:02d}", value=f"{h:02d}:{m:02d}",
                                 default=(s._dt_time == f"{h:02d}:{m:02d}"))
            for h in range(0, 24) for m in (0, 30)
        ]
        self.add_item(_DatePartSelect(session, time_opts[:25], "time", "🕐 Uhrzeit wählen...", row=2))

        if s.dt_str:
            self.add_item(_ClearDateTimeBtn(session))

        self.add_nav(can_back=True, can_next=True)


class _DatePartSelect(ui.Select):
    def __init__(self, session: WizardSession, options: list, part: str, placeholder: str, row: int):
        super().__init__(placeholder=placeholder, options=options, row=row)
        self.session = session
        self.part    = part

    async def callback(self, interaction: discord.Interaction):
        s = self.session.state
        val = self.values[0]
        if self.part == "day":
            s._dt_day = int(val)
        elif self.part == "month_year":
            m_str, y_str = val.split(".")
            s._dt_month = int(m_str)
            s._dt_year  = int(y_str)
        elif self.part == "time":
            s._dt_time = val
        # Zusammenbauen wenn alle Teile gesetzt
        if s._dt_day and s._dt_month and s._dt_year and s._dt_time:
            try:
                from datetime import datetime as _dtt
                _dtt(s._dt_year, s._dt_month, s._dt_day,
                     int(s._dt_time.split(":")[0]), int(s._dt_time.split(":")[1]))
                s.dt_str = f"{s._dt_day:02d}.{s._dt_month:02d}.{s._dt_year} {s._dt_time}"
            except ValueError:
                s.dt_str = None
        await self.session.refresh(interaction)


class _ClearDateTimeBtn(ui.Button):
    def __init__(self, session: WizardSession):
        super().__init__(label="✕ Datum entfernen", style=discord.ButtonStyle.secondary, row=3)
        self.session = session

    async def callback(self, interaction: discord.Interaction):
        s = self.session.state
        s.dt_str = None
        s._dt_day = s._dt_month = s._dt_year = s._dt_time = None
        s.recurrence = "none"
        await self.session.refresh(interaction)


# ─────────────────────────────────────────────────────────────────────────────
# SCHRITT 6 – WIEDERHOLUNG
# ─────────────────────────────────────────────────────────────────────────────

class RecurrenceView(_BaseWizardView):
    def __init__(self, session: WizardSession):
        super().__init__(session)
        options = [
            discord.SelectOption(
                label=label,
                value=key,
                default=(session.state.recurrence == key),
            )
            for key, label in RECURRENCE_OPTIONS.items()
        ]
        self.add_item(_RecurrenceSelect(session, options))
        self.add_nav(can_back=True, can_next=True)


class _RecurrenceSelect(ui.Select):
    def __init__(self, session: WizardSession, options: list):
        super().__init__(placeholder="Wiederholung wählen...", options=options, row=0)
        self.session = session

    async def callback(self, interaction: discord.Interaction):
        self.session.state.recurrence = self.values[0]
        await self.session.refresh(interaction)


# ─────────────────────────────────────────────────────────────────────────────
# SCHRITT 7 – KOMMENTAR
# ─────────────────────────────────────────────────────────────────────────────

class CommentView(_BaseWizardView):
    def __init__(self, session: WizardSession):
        super().__init__(session)
        s = session.state
        label = f"✏️ Kommentar ändern" if s.comment else "✏️ Kommentar hinzufügen"
        self.add_item(_OpenModalBtn(label, CommentModal(session), row=0))
        if s.comment:
            self.add_item(_ClearCommentBtn(session))
        self.add_nav(can_back=True, can_next=True)


class _ClearCommentBtn(ui.Button):
    def __init__(self, session: WizardSession):
        super().__init__(label="✕ Kommentar entfernen", style=discord.ButtonStyle.secondary, row=1)
        self.session = session

    async def callback(self, interaction: discord.Interaction):
        self.session.state.comment = None
        await self.session.refresh(interaction)


class CommentModal(ui.Modal, title="Kommentar"):
    comment_input = ui.TextInput(
        label="Kommentar",
        placeholder="Zusätzliche Infos zur Gruppe...",
        style=discord.TextStyle.paragraph,
        max_length=300,
        required=False,
    )

    def __init__(self, session: WizardSession):
        super().__init__()
        self.session = session

    async def on_submit(self, interaction: discord.Interaction):
        text = self.comment_input.value.strip()
        self.session.state.comment = text if text else None
        await self.session.refresh_after_modal(interaction)


# ─────────────────────────────────────────────────────────────────────────────
# SCHRITT 8 – LEVEL-ANFORDERUNG
# ─────────────────────────────────────────────────────────────────────────────

class LevelView(_BaseWizardView):
    def __init__(self, session: WizardSession):
        super().__init__(session)
        mode_options = [
            discord.SelectOption(label="Kein Level erforderlich", value="none",  default=(session.state.level_mode == "none")),
            discord.SelectOption(label="Mindest-Level (ab Level X)", value="min", default=(session.state.level_mode == "min")),
            discord.SelectOption(label="Level-Bereich (Level X–Y)",   value="range", default=(session.state.level_mode == "range")),
        ]
        self.add_item(_LevelModeSelect(session, mode_options))

        if session.state.level_mode in ("min", "range"):
            lbl = "🔢 Level eingeben"
            self.add_item(_OpenModalBtn(lbl, LevelModal(session), row=1))

        self.add_nav(can_back=True, can_next=True)


class _LevelModeSelect(ui.Select):
    def __init__(self, session: WizardSession, options: list):
        super().__init__(placeholder="Level-Anforderung wählen...", options=options, row=0)
        self.session = session

    async def callback(self, interaction: discord.Interaction):
        s = self.session.state
        s.level_mode = self.values[0]
        if s.level_mode == "none":
            s.level_min = s.level_max = None
        await self.session.refresh(interaction)


class LevelModal(ui.Modal, title="Level-Anforderung"):
    min_input = ui.TextInput(
        label="Mindest-Level",
        placeholder="z.B. 50",
        max_length=3,
    )
    max_input = ui.TextInput(
        label="Max-Level (nur bei Bereich, sonst leer)",
        placeholder="z.B. 70",
        max_length=3,
        required=False,
    )

    def __init__(self, session: WizardSession):
        super().__init__()
        self.session = session

    async def on_submit(self, interaction: discord.Interaction):
        s = self.session.state
        try:
            s.level_min = int(self.min_input.value.strip())
        except ValueError:
            await interaction.response.defer()
            return
        max_val = self.max_input.value.strip()
        if max_val:
            try:
                s.level_max = int(max_val)
                s.level_mode = "range"
            except ValueError:
                pass
        else:
            s.level_max  = None
            s.level_mode = "min"
        await self.session.refresh_after_modal(interaction)


# ─────────────────────────────────────────────────────────────────────────────
# SCHRITT 9 – VORSCHAU & BESTÄTIGEN
# ─────────────────────────────────────────────────────────────────────────────

class PreviewView(_BaseWizardView):
    def __init__(self, session: WizardSession):
        super().__init__(session)

        post_btn = ui.Button(
            label="📢 Posten",
            style=discord.ButtonStyle.success,
            row=4,
        )
        post_btn.session = session
        post_btn.callback = self._post_callback
        self.add_item(post_btn)

        self.add_item(_BackBtn(session))
        self.add_item(_CancelBtn(session))

    async def _post_callback(self, interaction: discord.Interaction):
        await self.session.on_complete(interaction, self.session.state)
