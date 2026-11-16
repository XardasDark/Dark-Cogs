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
    PANEL_OPEN, TICKET_CLAIM, TICKET_CLOSE, TICKET_CONFIRM,
    COLOR_PANEL, COLOR_TICKET, COLOR_PENDING, COLOR_CLOSED,
    STATUS_OPEN, STATUS_CLOSE_REQUESTED, STATUS_CLOSED, STATUS_LABELS,
    KIND_GROUP,
)


def _status_color(status: str) -> int:
    return {
        STATUS_OPEN: COLOR_TICKET,
        STATUS_CLOSE_REQUESTED: COLOR_PENDING,
        STATUS_CLOSED: COLOR_CLOSED,
    }.get(status, COLOR_TICKET)


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
            desc = f" – {node['description']}" if node.get("description") else ""
            lines.append(f"{emoji}**{node['name']}**{desc}")
            if node.get("kind") == KIND_GROUP:
                children = node.get("children") or {}
                if children:
                    sub = []
                    for child in children.values():
                        cem = (child.get("emoji") + " ") if child.get("emoji") else ""
                        sub.append(f"{cem}{child['name']}")
                    lines.append("　└ " + " · ".join(sub))
        embed.add_field(name="Kategorien", value="\n".join(lines)[:1024], inline=False)
    if guild.icon:
        embed.set_thumbnail(url=guild.icon.url)
    embed.set_footer(text="Dein Anliegen landet in einem privaten Channel.")
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
        color=_status_color(ticket["status"]),
        timestamp=discord.utils.utcnow(),
    )
    opener_mention = opener.mention if opener else f"<@{ticket['opener_id']}>"
    embed.add_field(name="Ticket", value=f"`{ticket['id']}`", inline=True)
    embed.add_field(name="Ersteller", value=opener_mention, inline=True)
    embed.add_field(name="Status", value=STATUS_LABELS.get(ticket["status"], ticket["status"]), inline=True)

    claimed_by = ticket.get("claimed_by")
    if claimed_by:
        embed.add_field(name="Übernommen von", value=f"<@{claimed_by}>", inline=True)

    for label, value in (ticket.get("answers") or {}).items():
        if value:
            embed.add_field(name=label, value=str(value)[:1024], inline=False)

    embed.set_footer(text="Nutze die Buttons unten, um das Ticket zu verwalten.")
    return embed


def build_ticket_controls(status: str) -> discord.ui.View:
    """
    Persistente Controls im Ticket-Channel.
    - open:            [Übernehmen] [Schließen]
    - close_requested: [Übernehmen] [Schließen bestätigen]
    - closed:          (keine aktiven Buttons)
    """
    view = discord.ui.View(timeout=None)
    if status == STATUS_CLOSED:
        return view

    view.add_item(discord.ui.Button(
        label="Übernehmen",
        emoji="🙋",
        style=discord.ButtonStyle.secondary,
        custom_id=TICKET_CLAIM,
    ))

    if status == STATUS_CLOSE_REQUESTED:
        view.add_item(discord.ui.Button(
            label="Schließen bestätigen",
            emoji="🔒",
            style=discord.ButtonStyle.danger,
            custom_id=TICKET_CONFIRM,
        ))
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
