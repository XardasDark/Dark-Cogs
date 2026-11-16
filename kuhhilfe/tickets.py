"""
tickets.py – Channel-Handling & Transcript für KuhHilfe

Erstellt den privaten Ticket-Channel mit passenden Berechtigungen (nur Ersteller
und die zugewiesenen Rollen/User sehen ihn) und erzeugt beim Schließen ein
Text-Transcript des Verlaufs.
"""

import datetime
import io
from typing import Any, Dict, Optional

import discord


def build_overwrites(guild: discord.Guild, opener: discord.Member,
                     category: Dict[str, Any]) -> Dict[discord.abc.Snowflake, discord.PermissionOverwrite]:
    """@everyone ausgesperrt; Ersteller + zugewiesene Rollen/User bekommen Zugriff."""
    allow = discord.PermissionOverwrite(
        view_channel=True, send_messages=True, read_message_history=True,
        attach_files=True, embed_links=True,
    )
    overwrites: Dict[discord.abc.Snowflake, discord.PermissionOverwrite] = {
        guild.default_role: discord.PermissionOverwrite(view_channel=False),
        opener: allow,
    }

    # Bot selbst – braucht volle Kontrolle über den Channel
    me = guild.me
    if me is not None:
        overwrites[me] = discord.PermissionOverwrite(
            view_channel=True, send_messages=True, read_message_history=True,
            manage_channels=True, manage_messages=True,
            attach_files=True, embed_links=True,
        )

    for rid in category.get("role_ids", []):
        role = guild.get_role(rid)
        if role is not None:
            overwrites[role] = allow

    for uid in category.get("user_ids", []):
        member = guild.get_member(uid)
        if member is not None:
            overwrites[member] = allow

    return overwrites


async def create_ticket_channel(guild: discord.Guild, opener: discord.Member,
                                category: Dict[str, Any], ticket_id: str,
                                parent_category_id: Optional[int]) -> discord.TextChannel:
    """Erstellt den privaten Channel (Name = Ticket-ID) unter der Ticket-Kategorie."""
    parent: Optional[discord.CategoryChannel] = None
    if parent_category_id:
        ch = guild.get_channel(parent_category_id)
        if isinstance(ch, discord.CategoryChannel):
            parent = ch

    overwrites = build_overwrites(guild, opener, category)
    reason = f"KuhHilfe-Ticket {ticket_id} von {opener} ({opener.id})"
    topic = f"Ticket {ticket_id} · {category.get('name', '')} · Ersteller: {opener}"
    try:
        return await guild.create_text_channel(
            name=ticket_id, category=parent, overwrites=overwrites, topic=topic, reason=reason)
    except discord.Forbidden:
        raise
    except discord.HTTPException:
        # Häufigster Fall: die Ziel-Kanalkategorie ist voll (max. 50 Channels).
        # Fallback: Channel ohne Kanalkategorie (oben in der Liste) erstellen.
        if parent is None:
            raise
        return await guild.create_text_channel(
            name=ticket_id, category=None, overwrites=overwrites, topic=topic, reason=reason)


async def generate_transcript(channel: discord.TextChannel,
                              ticket: Dict[str, Any]) -> discord.File:
    """Baut ein einfaches Text-Transcript des gesamten Channel-Verlaufs."""
    lines = [
        f"Transcript – Ticket {ticket.get('id')}",
        f"Kategorie:  {ticket.get('category_key')}",
        f"Channel:    #{channel.name} ({channel.id})",
        f"Erstellt:   {datetime.datetime.utcnow().isoformat()}Z (Export)",
        "=" * 60,
        "",
    ]
    async for msg in channel.history(limit=None, oldest_first=True):
        ts = msg.created_at.strftime("%Y-%m-%d %H:%M:%S")
        author = f"{msg.author} ({msg.author.id})"
        content = msg.content or ""
        lines.append(f"[{ts}] {author}:")
        if content:
            lines.append(content)
        for emb in msg.embeds:
            if emb.title:
                lines.append(f"    [Embed] {emb.title}")
            if emb.description:
                lines.append(f"    {emb.description}")
            for field in emb.fields:
                lines.append(f"    {field.name}: {field.value}")
        for att in msg.attachments:
            lines.append(f"    [Anhang] {att.filename} – {att.url}")
        lines.append("")

    data = "\n".join(lines).encode("utf-8")
    return discord.File(io.BytesIO(data), filename=f"transcript-{ticket.get('id')}.txt")
