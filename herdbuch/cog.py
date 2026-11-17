"""
cog.py – Haupt-Cog von Herdbuch

Phase 1: Zentrale Verwaltung durch die Serveradministration.
  /gildenadmin setup                 – Kategorie, Rollen- & Anfrage-Channel anlegen
  /gildenadmin erstellen             – Gilde komplett neu anlegen (Rollen + Channel)
  /gildenadmin registrieren          – Gilde aus bestehenden Rollen/Channel registrieren
  /gildenadmin bearbeiten            – Gildendaten ändern
  /gildenadmin deaktivieren          – Gilde ausblenden
  /gildenadmin aktivieren            – Gilde wieder einblenden
  /gildenadmin info                  – Konfiguration & Health-Check

Mitglieder- und Orga-Befehle (/gilde …), das öffentliche Button-Panel sowie der
Antrags-Workflow folgen in späteren Phasen.
"""

import logging
from typing import List, Optional

import discord
from discord import app_commands
from redbot.core import Config, commands
from redbot.core.bot import Red

from . import constants as C
from . import embeds as E
from .storage import HerdbuchStore

log = logging.getLogger("red.dark-cogs.herdbuch")


class Herdbuch(commands.Cog):
    """🐄 Herdbuch – zentrale Verwaltung von Gildenzugehörigkeiten."""

    __author__ = "XardasDark"
    __version__ = "0.1.0"

    def __init__(self, bot: Red):
        self.bot = bot
        self.config = Config.get_conf(self, identifier=0x48_45_52_44, force_registration=True)
        self.config.register_guild(
            clans={},
            applications={},
            guild_requests={},
            admin_category_id=None,
            roster_channel_id=None,
            roster_message_ids=[],
            central_channel_id=None,
            audit=[],
        )
        self.store = HerdbuchStore(self.config)

    # ─────────────────────────────────────────────────────────────────────────
    # HILFEN
    # ─────────────────────────────────────────────────────────────────────────

    def _bot_can_manage_role(self, guild: discord.Guild, role: discord.Role) -> bool:
        """True, wenn der Bot diese Rolle vergeben/entziehen kann (Hierarchie + Recht)."""
        return guild.me.guild_permissions.manage_roles and guild.me.top_role > role

    async def _ensure_admin_category(self, guild: discord.Guild) -> discord.CategoryChannel:
        """Liefert die Admin-Kategorie – legt sie (privat) an, falls noch nicht vorhanden."""
        cat_id = await self.store.get_admin_category(guild)
        category = guild.get_channel(cat_id) if cat_id else None
        if isinstance(category, discord.CategoryChannel):
            return category
        overwrites = {
            guild.default_role: discord.PermissionOverwrite(view_channel=False),
            guild.me: discord.PermissionOverwrite(view_channel=True, send_messages=True,
                                                  manage_channels=True),
        }
        category = await guild.create_category(
            C.SETUP_CATEGORY_NAME, overwrites=overwrites, reason="Herdbuch: Setup")
        await self.store.set_admin_category(guild, category.id)
        return category

    async def _resolve_clan(self, guild: discord.Guild, selector: str):
        """Löst einen Slug- oder Namens-String zu (slug, clan) auf."""
        clan = await self.store.get_clan(guild, selector)
        if clan is not None:
            return selector, clan
        slug = await self.store.find_by_name(guild, selector)
        if slug is not None:
            return slug, await self.store.get_clan(guild, slug)
        return None, None

    async def _clan_autocomplete(self, interaction: discord.Interaction,
                                 current: str) -> List[app_commands.Choice]:
        clans = await self.store.all_clans(interaction.guild)
        current = current.lower()
        return [
            app_commands.Choice(name=clan["name"], value=slug)
            for slug, clan in clans.items()
            if current in clan["name"].lower() or current in slug
        ][:25]

    # ─────────────────────────────────────────────────────────────────────────
    # BEFEHLSGRUPPE  /gildenadmin
    # ─────────────────────────────────────────────────────────────────────────

    @commands.hybrid_group(name="gildenadmin",
                           description="Zentrale Verwaltung der Gilden (nur Admins)")
    @commands.guild_only()
    @commands.admin_or_permissions(manage_guild=True)
    @app_commands.default_permissions(manage_guild=True)
    async def gildenadmin(self, ctx: commands.Context) -> None:
        if ctx.invoked_subcommand is None:
            await ctx.send_help(ctx.command)

    # ── setup ──────────────────────────────────────────────────────────────────
    @gildenadmin.command(name="setup",
                         description="Legt Kategorie, Rollen-Channel und Anfrage-Channel an")
    async def ga_setup(self, ctx: commands.Context) -> None:
        guild = ctx.guild
        if not (guild.me.guild_permissions.manage_channels
                and guild.me.guild_permissions.manage_roles):
            await ctx.send(embed=E.err(
                "Mir fehlen die Berechtigungen **Kanäle verwalten** und **Rollen verwalten**."))
            return

        category = await self._ensure_admin_category(guild)

        central_id = await self.store.get_central_channel(guild)
        central = guild.get_channel(central_id) if central_id else None
        if central is None:
            central = await guild.create_text_channel(
                C.SETUP_CENTRAL_NAME, category=category, reason="Herdbuch: Setup")
            await self.store.set_central_channel(guild, central.id)

        roster_id, _ = await self.store.get_roster(guild)
        roster = guild.get_channel(roster_id) if roster_id else None
        if roster is None:
            roster = await guild.create_text_channel(
                C.SETUP_ROSTER_NAME, reason="Herdbuch: Setup")
            await self.store.set_roster_channel(guild, roster.id)

        embed = E.info(
            "🐄 Herdbuch eingerichtet",
            f"- **Kategorie:** {category.mention}\n"
            f"- **Rollen-Channel (öffentlich):** {roster.mention}\n"
            f"- **Anfrage-Channel (privat):** {central.mention}\n\n"
            f"Lege jetzt deine erste Gilde an:\n"
            f"`/gildenadmin erstellen name:KuhMuhNisten` (Rollen + Channel neu)\n"
            f"oder `/gildenadmin registrieren …` (bestehende Rollen/Channel).")
        await ctx.send(embed=embed)

    # ── erstellen ───────────────────────────────────────────────────────────────
    @gildenadmin.command(name="erstellen",
                         description="Legt eine Gilde komplett neu an (Mitglieds- & Orga-Rolle + Channel)")
    @app_commands.describe(name="Anzeigename der Gilde (= Name der Mitgliedsrolle)",
                           emoji="Optionales Emoji für den Panel-Button")
    async def ga_erstellen(self, ctx: commands.Context, name: str,
                           emoji: Optional[str] = None) -> None:
        guild = ctx.guild
        name = name.strip()[:C.MAX_ROLE_NAME]
        if not name:
            await ctx.send(embed=E.err("Bitte gib einen Gildennamen an."))
            return
        if not (guild.me.guild_permissions.manage_channels
                and guild.me.guild_permissions.manage_roles):
            await ctx.send(embed=E.err(
                "Mir fehlen die Berechtigungen **Kanäle verwalten** und **Rollen verwalten**."))
            return
        if await self.store.find_by_name(guild, name) is not None:
            await ctx.send(embed=E.err(f"Eine Gilde namens **{name}** ist bereits registriert."))
            return

        clans = await self.store.all_clans(guild)
        slug = C.make_slug(name, clans.keys())
        category = await self._ensure_admin_category(guild)

        try:
            member_role = await guild.create_role(
                name=name, mentionable=True, reason=f"Herdbuch: Mitgliedsrolle für {name}")
            orga_role = await guild.create_role(
                name=f"{name} {C.ORGA_ROLE_SUFFIX}", mentionable=True,
                reason=f"Herdbuch: Orga-Rolle für {name}")
        except discord.Forbidden:
            await ctx.send(embed=E.err("Ich darf keine Rollen erstellen (fehlende Berechtigung)."))
            return
        except discord.HTTPException:
            log.exception("Rollen-Erstellung fehlgeschlagen")
            await ctx.send(embed=E.err("Die Rollen konnten nicht erstellt werden."))
            return

        overwrites = {
            guild.default_role: discord.PermissionOverwrite(view_channel=False),
            orga_role: discord.PermissionOverwrite(view_channel=True, send_messages=True,
                                                   read_message_history=True),
            guild.me: discord.PermissionOverwrite(view_channel=True, send_messages=True,
                                                  manage_messages=True, read_message_history=True),
        }
        try:
            channel = await guild.create_text_channel(
                C.admin_channel_name(name), category=category, overwrites=overwrites,
                reason=f"Herdbuch: Orga-Channel für {name}")
        except discord.HTTPException:
            log.exception("Orga-Channel-Erstellung fehlgeschlagen")
            # Rollen wieder aufräumen, damit keine Leichen entstehen
            for role in (member_role, orga_role):
                try:
                    await role.delete(reason="Herdbuch: Rollback nach fehlgeschlagener Erstellung")
                except discord.HTTPException:
                    pass
            await ctx.send(embed=E.err("Der Orga-Channel konnte nicht erstellt werden."))
            return

        await self.store.add_clan(
            guild, slug, name=name, emoji=emoji, member_role_id=member_role.id,
            orga_role_id=orga_role.id, admin_channel_id=channel.id)
        await self.store.add_audit(guild, clan=slug, action="clan_created",
                                   target_id=None, actor_id=ctx.author.id)

        embed = E.ok(f"Gilde **{name}** angelegt.")
        embed.add_field(name="Mitgliedsrolle", value=member_role.mention, inline=True)
        embed.add_field(name="Orga-Rolle", value=orga_role.mention, inline=True)
        embed.add_field(name="Orga-Channel", value=channel.mention, inline=True)
        embed.set_footer(text=f"Slug: {slug}")
        embed.description += (f"\n\nWeise jetzt die Orga-Mitglieder der Rolle {orga_role.mention} "
                              f"zu. Das öffentliche Button-Panel folgt in einer späteren Phase.")
        await ctx.send(embed=embed)

    # ── registrieren ─────────────────────────────────────────────────────────────
    @gildenadmin.command(name="registrieren",
                         description="Registriert eine Gilde aus bestehenden Rollen und Channel")
    @app_commands.describe(name="Anzeigename der Gilde", mitgliedsrolle="Bestehende Mitgliedsrolle",
                           orgarolle="Bestehende Orga-Rolle", channel="Privater Orga-Channel",
                           emoji="Optionales Emoji für den Panel-Button")
    async def ga_registrieren(self, ctx: commands.Context, name: str,
                              mitgliedsrolle: discord.Role, orgarolle: discord.Role,
                              channel: discord.TextChannel,
                              emoji: Optional[str] = None) -> None:
        guild = ctx.guild
        name = name.strip()[:C.MAX_ROLE_NAME]
        if not name:
            await ctx.send(embed=E.err("Bitte gib einen Gildennamen an."))
            return
        if mitgliedsrolle == orgarolle:
            await ctx.send(embed=E.err("Mitglieds- und Orga-Rolle müssen unterschiedlich sein."))
            return
        if await self.store.find_by_name(guild, name) is not None:
            await ctx.send(embed=E.err(f"Eine Gilde namens **{name}** ist bereits registriert."))
            return
        for role in (mitgliedsrolle, orgarolle):
            used = await self.store.role_in_use(guild, role.id)
            if used:
                await ctx.send(embed=E.err(
                    f"Die Rolle {role.mention} wird bereits von einer anderen Gilde verwendet."))
                return
        if await self.store.channel_in_use(guild, channel.id):
            await ctx.send(embed=E.err(
                f"Der Channel {channel.mention} ist bereits einer Gilde zugeordnet."))
            return

        clans = await self.store.all_clans(guild)
        slug = C.make_slug(name, clans.keys())
        await self.store.add_clan(
            guild, slug, name=name, emoji=emoji, member_role_id=mitgliedsrolle.id,
            orga_role_id=orgarolle.id, admin_channel_id=channel.id)
        await self.store.add_audit(guild, clan=slug, action="clan_registered",
                                   target_id=None, actor_id=ctx.author.id)

        embed = E.ok(f"Gilde **{name}** registriert.")
        embed.add_field(name="Mitgliedsrolle", value=mitgliedsrolle.mention, inline=True)
        embed.add_field(name="Orga-Rolle", value=orgarolle.mention, inline=True)
        embed.add_field(name="Orga-Channel", value=channel.mention, inline=True)
        embed.set_footer(text=f"Slug: {slug}")
        if not self._bot_can_manage_role(guild, mitgliedsrolle):
            embed.add_field(
                name="⚠️ Hinweis",
                value=(f"Meine höchste Rolle steht **nicht über** {mitgliedsrolle.mention} – "
                       f"ich kann die Mitgliedsrolle dann nicht vergeben. Bitte ziehe meine "
                       f"Bot-Rolle in der Rollen­liste höher."),
                inline=False)
        await ctx.send(embed=embed)

    # ── bearbeiten ───────────────────────────────────────────────────────────────
    @gildenadmin.command(name="bearbeiten", description="Ändert die Daten einer Gilde")
    @app_commands.describe(gilde="Welche Gilde?", name="Neuer Anzeigename",
                           emoji="Neues Emoji für den Panel-Button",
                           mitgliedsrolle="Neue Mitgliedsrolle", orgarolle="Neue Orga-Rolle",
                           channel="Neuer Orga-Channel")
    @app_commands.autocomplete(gilde=_clan_autocomplete)
    async def ga_bearbeiten(self, ctx: commands.Context, gilde: str,
                            name: Optional[str] = None, emoji: Optional[str] = None,
                            mitgliedsrolle: Optional[discord.Role] = None,
                            orgarolle: Optional[discord.Role] = None,
                            channel: Optional[discord.TextChannel] = None) -> None:
        guild = ctx.guild
        slug, clan = await self._resolve_clan(guild, gilde)
        if clan is None:
            await ctx.send(embed=E.err(f"Keine Gilde **{gilde}** gefunden."))
            return

        changes = {}
        if name:
            if await self.store.find_by_name(guild, name) not in (None, slug):
                await ctx.send(embed=E.err(f"Eine Gilde namens **{name}** existiert bereits."))
                return
            changes["name"] = name.strip()[:C.MAX_ROLE_NAME]
        if emoji is not None:
            changes["emoji"] = emoji or None
        for key, role in (("member_role_id", mitgliedsrolle), ("orga_role_id", orgarolle)):
            if role is not None:
                used = await self.store.role_in_use(guild, role.id)
                if used not in (None, slug):
                    await ctx.send(embed=E.err(
                        f"Die Rolle {role.mention} wird bereits von einer anderen Gilde genutzt."))
                    return
                changes[key] = role.id
        if channel is not None:
            used = await self.store.channel_in_use(guild, channel.id)
            if used not in (None, slug):
                await ctx.send(embed=E.err(
                    f"Der Channel {channel.mention} ist bereits einer Gilde zugeordnet."))
                return
            changes["admin_channel_id"] = channel.id

        if not changes:
            await ctx.send(embed=E.err("Du hast keine Änderungen angegeben."))
            return
        await self.store.update_clan(guild, slug, **changes)
        await self.store.add_audit(guild, clan=slug, action="clan_edited",
                                   target_id=None, actor_id=ctx.author.id)
        await ctx.send(embed=E.ok(f"Gilde **{changes.get('name', clan['name'])}** aktualisiert."))

    # ── deaktivieren / aktivieren ─────────────────────────────────────────────────
    @gildenadmin.command(name="deaktivieren",
                         description="Blendet eine Gilde aus (Rollen & Channel bleiben bestehen)")
    @app_commands.describe(gilde="Welche Gilde?")
    @app_commands.autocomplete(gilde=_clan_autocomplete)
    async def ga_deaktivieren(self, ctx: commands.Context, gilde: str) -> None:
        guild = ctx.guild
        slug, clan = await self._resolve_clan(guild, gilde)
        if clan is None:
            await ctx.send(embed=E.err(f"Keine Gilde **{gilde}** gefunden."))
            return
        await self.store.set_active(guild, slug, False)
        await self.store.add_audit(guild, clan=slug, action="clan_deactivated",
                                   target_id=None, actor_id=ctx.author.id)
        await ctx.send(embed=E.ok(
            f"Gilde **{clan['name']}** deaktiviert. Sie erscheint nicht mehr im Panel."))

    @gildenadmin.command(name="aktivieren", description="Blendet eine deaktivierte Gilde wieder ein")
    @app_commands.describe(gilde="Welche Gilde?")
    @app_commands.autocomplete(gilde=_clan_autocomplete)
    async def ga_aktivieren(self, ctx: commands.Context, gilde: str) -> None:
        guild = ctx.guild
        slug, clan = await self._resolve_clan(guild, gilde)
        if clan is None:
            await ctx.send(embed=E.err(f"Keine Gilde **{gilde}** gefunden."))
            return
        await self.store.set_active(guild, slug, True)
        await self.store.add_audit(guild, clan=slug, action="clan_activated",
                                   target_id=None, actor_id=ctx.author.id)
        await ctx.send(embed=E.ok(f"Gilde **{clan['name']}** wieder aktiviert."))

    # ── info ──────────────────────────────────────────────────────────────────────
    @gildenadmin.command(name="info", description="Zeigt Konfiguration und Health-Check")
    async def ga_info(self, ctx: commands.Context) -> None:
        guild = ctx.guild
        category = guild.get_channel(await self.store.get_admin_category(guild) or 0)
        roster_id, _ = await self.store.get_roster(guild)
        roster = guild.get_channel(roster_id) if roster_id else None
        central_id = await self.store.get_central_channel(guild)
        central = guild.get_channel(central_id) if central_id else None
        clans = await self.store.all_clans(guild)

        embed = E.info("⚙️ Herdbuch – Konfiguration")
        embed.add_field(name="Kategorie",
                        value=category.mention if category else "_(nicht gesetzt — `/gildenadmin setup`)_",
                        inline=False)
        embed.add_field(name="Rollen-Channel",
                        value=roster.mention if roster else "_(nicht gesetzt)_", inline=True)
        embed.add_field(name="Anfrage-Channel",
                        value=central.mention if central else "_(nicht gesetzt)_", inline=True)

        if not clans:
            embed.add_field(name="Gilden", value="_(noch keine — `/gildenadmin erstellen`)_",
                            inline=False)
            await ctx.send(embed=embed)
            return

        lines, warnings = [], []
        for slug, clan in clans.items():
            member_role = guild.get_role(clan["member_role_id"])
            orga_role = guild.get_role(clan["orga_role_id"])
            channel = guild.get_channel(clan["admin_channel_id"])
            marker = "🟢" if clan.get("active", True) else "⚫"
            emoji = f"{clan['emoji']} " if clan.get("emoji") else ""
            count = len(member_role.members) if member_role else 0
            lines.append(f"{marker} {emoji}**{clan['name']}** · {count} Mitglieder · "
                         f"{channel.mention if channel else '⚠️ Channel fehlt'}")
            if member_role is None:
                warnings.append(f"**{clan['name']}**: Mitgliedsrolle fehlt.")
            elif not self._bot_can_manage_role(guild, member_role):
                warnings.append(f"**{clan['name']}**: meine Rolle steht nicht über "
                                f"{member_role.mention} – Vergabe nicht möglich.")
            if orga_role is None:
                warnings.append(f"**{clan['name']}**: Orga-Rolle fehlt.")
            if channel is None:
                warnings.append(f"**{clan['name']}**: Orga-Channel fehlt.")

        embed.add_field(name=f"Gilden ({len(clans)})", value="\n".join(lines), inline=False)
        if warnings:
            embed.color = C.COLOR_DANGER
            embed.add_field(name="⚠️ Health-Check", value="\n".join(warnings), inline=False)
        await ctx.send(embed=embed)

    # ─────────────────────────────────────────────────────────────────────────
    # FEHLER-HANDLER
    # ─────────────────────────────────────────────────────────────────────────

    async def cog_command_error(self, ctx: commands.Context, error) -> None:
        error = getattr(error, "original", error)
        if isinstance(error, (commands.MissingPermissions, commands.CheckFailure)):
            await ctx.send(embed=E.err("Dir fehlt die Berechtigung **Server verwalten**."))
            return
        if isinstance(error, commands.NoPrivateMessage):
            await ctx.send(embed=E.err("Dieser Befehl funktioniert nur auf einem Server."))
            return
        if isinstance(error, discord.Forbidden):
            await ctx.send(embed=E.err("Mir fehlen die nötigen Berechtigungen für diese Aktion."))
            return
        log.exception("Fehler in Herdbuch-Befehl", exc_info=error)
        try:
            await ctx.send(embed=E.err("Ein unerwarteter Fehler ist aufgetreten."))
        except discord.DiscordException:
            pass
