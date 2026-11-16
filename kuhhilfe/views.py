"""
views.py – Embed- & View-Builder für KuhHilfe (reine Funktionen)

Baut das Support-Panel (persistenter Button), das Ticket-Embed mit den
Wizard-Antworten und die persistenten Ticket-Controls (Übernehmen/Schließen).
Alle persistenten Komponenten tragen statische custom_ids und werden zentral
über on_interaction im Cog geroutet (übersteht Neustarts).
"""

from typing import Any, Dict, List, Optional

import discord

from .constants import (
    PANEL_OPEN, TICKET_CLAIM, TICKET_CLOSE, TICKET_CONFIRM, TICKET_REOPEN, TICKET_MANAGE,
    COLOR_PANEL, COLOR_TICKET, COLOR_PENDING, COLOR_CLOSED, COLOR_INFO,
    STATUS_OPEN, STATUS_CLOSE_REQUESTED, STATUS_CLOSED, STATUS_LABELS,
    STAGE_IN_PROGRESS, STAGE_WAITING, STAGE_LABELS,
)


def _ticket_color(status: str, stage=None) -> int:
    """Embed-Farbe: Lebenszyklus hat Vorrang; bei offenen Tickets färbt die Stufe mit."""
    if status == STATUS_CLOSED:
        return COLOR_CLOSED
    if status == STATUS_CLOSE_REQUESTED:
        return COLOR_PENDING
    # offen → Bearbeitungs-Stufe bestimmt die Farbe
    if stage == STAGE_IN_PROGRESS:
        return COLOR_INFO       # 🔵 blau
    if stage == STAGE_WAITING:
        return COLOR_PENDING    # 🟡 gelb
    return COLOR_TICKET         # 🟢 grün


def _status_label(status: str, stage=None) -> str:
    """Ein einziger Status-Indikator: Lebenszyklus hat Vorrang, sonst die Stufe."""
    if status in (STATUS_CLOSED, STATUS_CLOSE_REQUESTED):
        return STATUS_LABELS[status]
    if stage and stage in STAGE_LABELS:
        return STAGE_LABELS[stage]
    return STATUS_LABELS[STATUS_OPEN]


def status_label(status: str, stage=None) -> str:
    """Öffentlicher Alias (für Übersichten/Dashboards)."""
    return _status_label(status, stage)


# ─────────────────────────────────────────────────────────────────────────────
# SUPPORT-PANEL (öffentliche Nachricht mit persistentem Button)
# ─────────────────────────────────────────────────────────────────────────────
def build_panel_embed(guild: discord.Guild, categories: Dict[str, Dict[str, Any]],
                      title: Optional[str] = None,
                      description: Optional[str] = None) -> discord.Embed:
    embed = discord.Embed(
        title=title or "🎫 Support & Anliegen",
        description=description or (
            "Du hast eine Frage, ein Problem oder ein Anliegen?\n"
            "Klick auf **Support anfragen** und wähle die passende Kategorie. "
            "Wir leiten dich an die richtigen Ansprechpartner weiter."
        ),
        color=COLOR_PANEL,
    )
    if categories:
        lines = []
        for node in categories.values():
            emoji = (node.get("emoji") + " ") if node.get("emoji") else ""
            desc = f" - {node['description']}" if node.get("description") else ""
            lines.append(f"{emoji}**{node['name']}**{desc}")
        embed.add_field(name="Kategorien", value="\n".join(lines)[:1024], inline=False)
    if guild.icon:
        embed.set_thumbnail(url=guild.icon.url)
    return embed


def build_panel_view(disabled: bool = False) -> discord.ui.View:
    """Persistenter Panel-Button (Callback läuft über on_interaction)."""
    view = discord.ui.View(timeout=None)
    view.add_item(discord.ui.Button(
        label="Support anfragen",
        emoji="🎫",
        style=discord.ButtonStyle.primary,
        custom_id=PANEL_OPEN,
        disabled=disabled,
    ))
    return view


# ─────────────────────────────────────────────────────────────────────────────
# TICKET-EMBED (im privaten Channel)
# ─────────────────────────────────────────────────────────────────────────────
def build_ticket_embed(ticket: Dict[str, Any], category: Dict[str, Any],
                       opener: Optional[discord.abc.User],
                       guild: discord.Guild,
                       label: Optional[str] = None) -> discord.Embed:
    emoji = (category.get("emoji") + " ") if category.get("emoji") else ""
    title = label or ticket.get("category_label") or category.get("name", ticket["category_key"])
    embed = discord.Embed(
        title=f"{emoji}{title}",
        color=_ticket_color(ticket["status"], ticket.get("stage")),
        timestamp=discord.utils.utcnow(),
    )
    opener_mention = opener.mention if opener else f"<@{ticket['opener_id']}>"
    embed.add_field(name="Ticket", value=f"`{ticket['id']}`", inline=True)
    embed.add_field(name="Ersteller", value=opener_mention, inline=True)
    embed.add_field(name="Status",
                    value=_status_label(ticket["status"], ticket.get("stage")), inline=True)

    claimed_by = ticket.get("claimed_by")
    if claimed_by:
        embed.add_field(name="Übernommen von", value=f"<@{claimed_by}>", inline=True)

    added = ticket.get("added_members") or []
    if added:
        embed.add_field(name="Hinzugefügt", value=" ".join(f"<@{u}>" for u in added)[:1024], inline=False)

    for label, value in (ticket.get("answers") or {}).items():
        if value:
            embed.add_field(name=label, value=str(value)[:1024], inline=False)

    embed.set_footer(text="Nutze die Buttons unten, um das Ticket zu verwalten.")
    return embed


def _reopen_button() -> discord.ui.Button:
    return discord.ui.Button(
        label="Wieder öffnen",
        emoji="♻️",
        style=discord.ButtonStyle.success,
        custom_id=TICKET_REOPEN,
    )


def build_ticket_controls(status: str) -> discord.ui.View:
    """
    Persistente Controls im Ticket-Channel.
    - open:            [Übernehmen] [Schließen]
    - close_requested: [Übernehmen] [Schließen bestätigen] [Wieder öffnen]
    - closed:          [Wieder öffnen]   (Support kann die Schließung rückgängig machen)
    """
    view = discord.ui.View(timeout=None)
    if status == STATUS_CLOSED:
        view.add_item(_reopen_button())
        return view

    view.add_item(discord.ui.Button(
        label="Übernehmen",
        emoji="🙋",
        style=discord.ButtonStyle.secondary,
        custom_id=TICKET_CLAIM,
    ))
    view.add_item(discord.ui.Button(
        label="Verwalten",
        emoji="⚙️",
        style=discord.ButtonStyle.secondary,
        custom_id=TICKET_MANAGE,
    ))

    if status == STATUS_CLOSE_REQUESTED:
        view.add_item(discord.ui.Button(
            label="Schließen bestätigen",
            emoji="🔒",
            style=discord.ButtonStyle.danger,
            custom_id=TICKET_CONFIRM,
        ))
        view.add_item(_reopen_button())
    else:
        view.add_item(discord.ui.Button(
            label="Schließen",
            emoji="🔒",
            style=discord.ButtonStyle.danger,
            custom_id=TICKET_CLOSE,
        ))
    return view


def build_ping_content(category: Dict[str, Any], opener: discord.abc.User) -> str:
    """Erzeugt den Ping-Text für die zuständigen Rollen/User + Ersteller."""
    mentions: List[str] = []
    for rid in category.get("role_ids", []):
        mentions.append(f"<@&{rid}>")
    for uid in category.get("user_ids", []):
        mentions.append(f"<@{uid}>")
    ping = " ".join(mentions)
    if ping:
        return f"{opener.mention} · {ping}"
    return opener.mention
