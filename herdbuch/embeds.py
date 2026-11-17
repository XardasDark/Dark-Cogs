"""
embeds.py – Embed-Builder gemäß der verbindlichen Embed-Spec

Hält die Darstellungslogik aus cog.py heraus. Alle Builder sind reine Funktionen.
Die Antrags-/Log-Embeds folgen 1:1 der abgestimmten Spec (Farben, Felder, Status).
"""

from typing import Optional

import discord

from . import constants as C


# ── Kleine Helfer für kurze Rückmeldungen ────────────────────────────────────

def ok(message: str) -> discord.Embed:
    return discord.Embed(description=f"✅ {message}", color=C.COLOR_BRAND)


def err(message: str) -> discord.Embed:
    return discord.Embed(description=f"❌ {message}", color=C.COLOR_DANGER)


def info(title: str, description: str = "") -> discord.Embed:
    return discord.Embed(title=title, description=description or None, color=C.COLOR_INFO)


# ── Mitgliedschaftsanträge ───────────────────────────────────────────────────

def application_new(*, member: discord.abc.User, character: str, clan_name: str,
                    message: Optional[str], app_id: str) -> discord.Embed:
    """UI-04 · Neuer Mitgliedschaftsantrag (im Orga-Channel, mit Genehmigen/Ablehnen)."""
    embed = discord.Embed(
        title="Neuer Mitgliedschaftsantrag",
        description="Ein Spieler möchte deiner Gilde auf diesem Discord zugeordnet werden." + C.PAD,
        color=C.COLOR_BRAND,
    )
    embed.add_field(name="Discord-Mitglied", value=member.mention, inline=True)
    embed.add_field(name="Ingame-Charakter", value=character, inline=True)
    embed.add_field(name="Gilde", value=clan_name, inline=True)
    embed.add_field(name="Nachricht", value=(message or "—") + C.PAD, inline=False)
    embed.add_field(name="Status", value=C.STATUS["open"], inline=False)
    embed.set_footer(text=f"Antrag #{app_id}")
    return embed


def application_approved(*, member: discord.abc.User, character: str,
                         moderator: discord.abc.User, app_id: str) -> discord.Embed:
    """UI-05 · Genehmigter Antrag (ersetzt das Ursprungs-Embed)."""
    embed = discord.Embed(
        title="Mitgliedschaftsantrag genehmigt",
        description="Die Gilden-Zugehörigkeit wurde bestätigt und die entsprechende "
                    "Gilden-Rolle vergeben." + C.PAD,
        color=C.COLOR_BRAND,
        timestamp=discord.utils.utcnow(),
    )
    embed.add_field(name="Discord-Mitglied", value=member.mention, inline=True)
    embed.add_field(name="Ingame-Charakter", value=character, inline=True)
    embed.add_field(name="Genehmigt durch", value=moderator.mention, inline=True)
    embed.add_field(name="Status", value=C.STATUS["approved"], inline=False)
    embed.set_footer(text=f"Antrag #{app_id}")
    return embed


def application_rejected(*, member: discord.abc.User, character: str,
                         moderator: discord.abc.User, reason: str,
                         app_id: str) -> discord.Embed:
    """UI-06 · Abgelehnter Antrag (ersetzt das Ursprungs-Embed)."""
    embed = discord.Embed(
        title="Mitgliedschaftsantrag abgelehnt",
        description="Die angefragte Gilden-Zugehörigkeit wurde nicht bestätigt. Es wurde "
                    "keine Gilden-Rolle vergeben." + C.PAD,
        color=C.COLOR_DANGER,
        timestamp=discord.utils.utcnow(),
    )
    embed.add_field(name="Discord-Mitglied", value=member.mention, inline=True)
    embed.add_field(name="Ingame-Charakter", value=character, inline=True)
    embed.add_field(name="Abgelehnt durch", value=moderator.mention, inline=True)
    embed.add_field(name="Begründung", value=(reason or "—") + C.PAD, inline=False)
    embed.add_field(name="Status", value=C.STATUS["rejected"], inline=False)
    embed.set_footer(text=f"Antrag #{app_id}")
    return embed


# ── Log-Embeds im Orga-Channel ───────────────────────────────────────────────

def member_added(*, member: discord.abc.User,
                 moderator: discord.abc.User) -> discord.Embed:
    """UI-09 · Direktaufnahme durch die Gildenorga."""
    embed = discord.Embed(
        title="Gildenmitglied aufgenommen",
        description="Ein Mitglied wurde durch die Gildenorganisation direkt zugeordnet und "
                    "hat die entsprechende Gilden-Rolle erhalten." + C.PAD,
        color=C.COLOR_BRAND,
        timestamp=discord.utils.utcnow(),
    )
    embed.add_field(name="Discord-Mitglied", value=member.mention, inline=True)
    embed.add_field(name="Aufgenommen durch", value=moderator.mention, inline=True)
    embed.add_field(name="Status", value=C.STATUS["added"], inline=False)
    embed.set_footer(text="Direktaufnahme")
    return embed


def member_removed(*, member: discord.abc.User, moderator: discord.abc.User,
                   reason: str) -> discord.Embed:
    """UI-10 · Entfernung durch die Gildenorga."""
    embed = discord.Embed(
        title="Gildenmitglied entfernt",
        description="Die Gilden-Zugehörigkeit eines Mitglieds wurde durch die Gildenorganisation "
                    "beendet. Die entsprechende Gilden-Rolle wurde entfernt." + C.PAD,
        color=C.COLOR_DANGER,
        timestamp=discord.utils.utcnow(),
    )
    embed.add_field(name="Discord-Mitglied", value=member.mention, inline=True)
    embed.add_field(name="Entfernt durch", value=moderator.mention, inline=True)
    embed.add_field(name="Grund", value=reason or "—", inline=False)
    embed.add_field(name="Status", value=C.STATUS["removed"], inline=False)
    return embed


def member_left(*, member: discord.abc.User) -> discord.Embed:
    """Freiwilliger Austritt – ausdrücklich als solcher gekennzeichnet."""
    embed = discord.Embed(
        title="Gildenmitglied ausgetreten",
        description="Ein Mitglied hat seine Gilden-Zugehörigkeit auf diesem Discord selbstständig "
                    "beendet. Die entsprechende Gilden-Rolle wurde entfernt." + C.PAD,
        color=C.COLOR_NEUTRAL,
        timestamp=discord.utils.utcnow(),
    )
    embed.add_field(name="Discord-Mitglied", value=member.mention, inline=True)
    embed.add_field(name="Status", value=C.STATUS["left"], inline=True)
    return embed


# ── Zentrale „Andere Gilde"-Anfrage ──────────────────────────────────────────

def guild_request(*, applicant: discord.abc.User, guild_name: str, leader: str,
                  message: Optional[str], has_image: bool) -> discord.Embed:
    """UI-12 · Neue Gildenanfrage im zentralen Admin-Channel."""
    embed = discord.Embed(
        title="Neue Gilde angefragt",
        description="Ein Spieler möchte die Aufnahme einer weiteren Gilde in die Community "
                    "beantragen." + C.PAD,
        color=C.COLOR_REQUEST,
    )
    embed.add_field(name="Antragsteller", value=applicant.mention, inline=True)
    embed.add_field(name="Ingame-Gildenname", value=guild_name, inline=True)
    embed.add_field(name="Ingame-Gildenleader", value=leader, inline=True)
    embed.add_field(name="Zusätzliche Nachricht", value=(message or "—") + C.PAD, inline=False)
    if has_image:
        embed.set_image(url="attachment://guild_info.png")
    return embed
