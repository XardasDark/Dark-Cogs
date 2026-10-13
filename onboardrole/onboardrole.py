"""
onboardrole.py – Vergibt Rollen nach abgeschlossenem Onboarding / Regel-Zustimmung.

Erkennung:
  Discord markiert ein Mitglied, das noch im Onboarding/Regel-Gate hängt, mit
  `pending = True`. Sobald es die Regeln bestätigt bzw. das Onboarding abschließt,
  setzt Discord `pending = False` und schickt `GUILD_MEMBER_UPDATE`
  (on_member_update). Genau diesen Übergang nutzt dieser Cog als Signal.

Voraussetzungen:
  - Community-Server mit Membership Screening / Onboarding
  - privilegierter „Server Members"-Intent
  - Bot-Rolle über den zu vergebenden Rollen (Rechte „Rollen verwalten")

Befehle (Admin, „Rollen verwalten"):
  /onboard setup <rolle>            – Rolle setzen + aktivieren
  /onboard rolle-add <rolle>        – weitere Rolle hinzufügen
  /onboard rolle-remove <rolle>     – Rolle entfernen
  /onboard toggle                   – aktivieren/deaktivieren
  /onboard log [kanal]              – Log-Kanal setzen (leer = aus)
  /onboard willkommen [text] [kanal]– Willkommensnachricht (leer = aus; kanal leer = DM)
  /onboard info                     – Konfiguration + Systemcheck
  /onboard test <mitglied>          – Trockentest (prüft nur, vergibt nichts)
"""

import logging
from typing import List, Optional

import discord
from discord import app_commands
from redbot.core import Config, commands
from redbot.core.bot import Red

log = logging.getLogger("red.dark-cogs.onboardrole")

COLOR_INFO = 0x5865F2
COLOR_OK = 0x00C853
COLOR_ERROR = 0xFF5252


class OnboardRole(commands.Cog):
    """🎓 OnboardRole – Rollen automatisch nach dem Onboarding vergeben."""

    def __init__(self, bot: Red):
        self.bot = bot
        self.config = Config.get_conf(self, identifier=0x0B_0A_4D_01, force_registration=True)
        self.config.register_guild(
            enabled=False,
            role_ids=[],
            log_channel_id=None,
            welcome_text=None,          # None = keine Willkommensnachricht
            welcome_channel_id=None,    # None = per DM, sonst Kanal-ID
        )

    # ─────────────────────────────────────────────────────────────────────────
    # LISTENER
    # ─────────────────────────────────────────────────────────────────────────

    @commands.Cog.listener()
    async def on_member_update(self, before: discord.Member, after: discord.Member) -> None:
        # Nur der Übergang „pending: True → False" ist der Onboarding-Abschluss
        if before.pending == after.pending or after.pending:
            return
        await self._complete(after)

    @commands.Cog.listener()
    async def on_member_join(self, member: discord.Member) -> None:
        # Server ohne Screening / bereits verifizierte Rejoins: pending ist direkt False
        if member.pending:
            return
        await self._complete(member)

    async def _complete(self, member: discord.Member) -> None:
        if member.bot or member.guild is None:
            return
        conf = self.config.guild(member.guild)
        if not await conf.enabled():
            return

        role_ids: List[int] = await conf.role_ids()
        roles = self._assignable_roles(member.guild, role_ids)
        already = {r.id for r in member.roles}
        to_add = [r for r in roles if r.id not in already]
        if not to_add:
            return

        try:
            await member.add_roles(*to_add, reason="OnboardRole: Onboarding abgeschlossen")
        except discord.Forbidden:
            await self._log(member.guild,
                            f"⚠️ Konnte {member.mention} keine Rolle geben – mir fehlen Rechte "
                            f"oder meine Rolle steht zu niedrig.")
            return
        except discord.HTTPException:
            log.exception("OnboardRole: Rollenvergabe fehlgeschlagen")
            return

        await self._send_welcome(member)
        role_mentions = ", ".join(r.mention for r in to_add)
        await self._log(member.guild,
                        f"✅ {member.mention} hat das Onboarding abgeschlossen → {role_mentions}")

    # ─────────────────────────────────────────────────────────────────────────
    # HILFEN
    # ─────────────────────────────────────────────────────────────────────────

    def _assignable_roles(self, guild: discord.Guild, role_ids: List[int]) -> List[discord.Role]:
        """Rollen, die es noch gibt und die der Bot vergeben darf (unter seiner Top-Rolle)."""
        top = guild.me.top_role
        out = []
        for rid in role_ids:
            role = guild.get_role(rid)
            if role is not None and not role.is_default() and role < top:
                out.append(role)
        return out

    def _format(self, template: str, member: discord.Member) -> str:
        return (template
                .replace("{user}", member.mention)
                .replace("{username}", member.display_name)
                .replace("{server}", member.guild.name)
                .replace("{count}", str(member.guild.member_count or 0)))

    async def _send_welcome(self, member: discord.Member) -> None:
        conf = self.config.guild(member.guild)
        text = await conf.welcome_text()
        if not text:
            return
        content = self._format(text, member)
        chan_id = await conf.welcome_channel_id()
        try:
            if chan_id:
                channel = member.guild.get_channel(chan_id)
                if channel is not None:
                    await channel.send(content)
            else:
                await member.send(content)
        except discord.HTTPException:
            pass  # DMs zu / fehlende Rechte – nicht kritisch

    async def _log(self, guild: discord.Guild, message: str) -> None:
        chan_id = await self.config.guild(guild).log_channel_id()
        if not chan_id:
            return
        channel = guild.get_channel(chan_id)
        if channel is None:
            return
        try:
            await channel.send(message, allowed_mentions=discord.AllowedMentions.none())
        except discord.HTTPException:
            pass

    # ─────────────────────────────────────────────────────────────────────────
    # BEFEHLE
    # ─────────────────────────────────────────────────────────────────────────

    @commands.hybrid_group(name="onboard", description="Onboarding-Rollen verwalten (nur Admins)")
    @commands.guild_only()
    @commands.has_permissions(manage_guild=True)
    @app_commands.default_permissions(manage_guild=True)
    async def onboard(self, ctx: commands.Context) -> None:
        if ctx.invoked_subcommand is None:
            await ctx.send_help(ctx.command)

    @onboard.command(name="setup", description="Setzt die zu vergebende Rolle und aktiviert den Cog")
    @commands.has_permissions(manage_guild=True)
    async def ob_setup(self, ctx: commands.Context, rolle: discord.Role) -> None:
        warn = self._role_warning(ctx.guild, rolle)
        await self.config.guild(ctx.guild).role_ids.set([rolle.id])
        await self.config.guild(ctx.guild).enabled.set(True)
        await ctx.send(f"✅ Aktiviert. Nach dem Onboarding bekommen Mitglieder {rolle.mention}.{warn}")

    @onboard.command(name="rolle-add", description="Fügt eine weitere zu vergebende Rolle hinzu")
    @commands.has_permissions(manage_guild=True)
    async def ob_role_add(self, ctx: commands.Context, rolle: discord.Role) -> None:
        async with self.config.guild(ctx.guild).role_ids() as ids:
            if rolle.id in ids:
                await ctx.send(f"ℹ️ {rolle.mention} ist bereits eingetragen.")
                return
            ids.append(rolle.id)
        await ctx.send(f"✅ {rolle.mention} wird jetzt zusätzlich vergeben.{self._role_warning(ctx.guild, rolle)}")

    @onboard.command(name="rolle-remove", description="Entfernt eine zu vergebende Rolle")
    @commands.has_permissions(manage_guild=True)
    async def ob_role_remove(self, ctx: commands.Context, rolle: discord.Role) -> None:
        async with self.config.guild(ctx.guild).role_ids() as ids:
            if rolle.id not in ids:
                await ctx.send(f"ℹ️ {rolle.mention} war nicht eingetragen.")
                return
            ids.remove(rolle.id)
        await ctx.send(f"✅ {rolle.mention} wird nicht mehr vergeben.")

    @onboard.command(name="toggle", description="Aktiviert oder deaktiviert die Rollenvergabe")
    @commands.has_permissions(manage_guild=True)
    async def ob_toggle(self, ctx: commands.Context) -> None:
        current = await self.config.guild(ctx.guild).enabled()
        await self.config.guild(ctx.guild).enabled.set(not current)
        await ctx.send("✅ OnboardRole ist jetzt **aktiv**." if not current
                       else "⏸️ OnboardRole ist jetzt **deaktiviert**.")

    @onboard.command(name="log", description="Setzt den Log-Kanal (leer = aus)")
    @app_commands.describe(kanal="Zielkanal für Logs (leer = Logging aus)")
    @commands.has_permissions(manage_guild=True)
    async def ob_log(self, ctx: commands.Context,
                     kanal: Optional[discord.TextChannel] = None) -> None:
        await self.config.guild(ctx.guild).log_channel_id.set(kanal.id if kanal else None)
        await ctx.send(f"✅ Log-Kanal: {kanal.mention}" if kanal else "✅ Logging deaktiviert.")

    @onboard.command(name="willkommen",
                     description="Willkommensnachricht setzen (leer = aus; Kanal leer = per DM)")
    @app_commands.describe(
        text="Nachricht. Platzhalter: {user} {username} {server} {count}. Leer = deaktivieren.",
        kanal="Kanal für die Nachricht (leer = per DM an das Mitglied)",
    )
    @commands.has_permissions(manage_guild=True)
    async def ob_welcome(self, ctx: commands.Context, text: Optional[str] = None,
                         kanal: Optional[discord.TextChannel] = None) -> None:
        conf = self.config.guild(ctx.guild)
        if not text:
            await conf.welcome_text.set(None)
            await ctx.send("✅ Willkommensnachricht deaktiviert.")
            return
        await conf.welcome_text.set(text)
        await conf.welcome_channel_id.set(kanal.id if kanal else None)
        ziel = kanal.mention if kanal else "per **DM**"
        vorschau = self._format(text, ctx.author)
        await ctx.send(f"✅ Willkommensnachricht gesetzt ({ziel}).\nVorschau:\n> {vorschau}",
                       allowed_mentions=discord.AllowedMentions.none())

    @onboard.command(name="info", description="Zeigt die Konfiguration und einen Systemcheck")
    @commands.has_permissions(manage_guild=True)
    async def ob_info(self, ctx: commands.Context) -> None:
        guild = ctx.guild
        conf = self.config.guild(guild)
        enabled = await conf.enabled()
        role_ids = await conf.role_ids()
        log_id = await conf.log_channel_id()
        welcome_text = await conf.welcome_text()
        welcome_chan = await conf.welcome_channel_id()

        roles = ", ".join(r.mention for r in
                          (guild.get_role(i) for i in role_ids) if r) or "_(keine)_"
        log_chan = guild.get_channel(log_id) if log_id else None

        embed = discord.Embed(title="🎓 OnboardRole – Konfiguration",
                              color=COLOR_OK if enabled else COLOR_ERROR)
        embed.add_field(name="Status", value="🟢 aktiv" if enabled else "🔴 deaktiviert", inline=False)
        embed.add_field(name="Rollen", value=roles, inline=False)
        embed.add_field(name="Log-Kanal", value=log_chan.mention if log_chan else "_(aus)_", inline=False)
        if welcome_text:
            ziel = (guild.get_channel(welcome_chan).mention
                    if welcome_chan and guild.get_channel(welcome_chan) else "per DM")
            embed.add_field(name="Willkommen", value=f"{ziel}\n> {welcome_text}", inline=False)
        else:
            embed.add_field(name="Willkommen", value="_(aus)_", inline=False)

        # Systemcheck
        checks = []
        checks.append(("Server Members-Intent", self.bot.intents.members))
        checks.append(("Community-Server (Onboarding)", "COMMUNITY" in guild.features))
        checks.append(("Rechte „Rollen verwalten\"", guild.me.guild_permissions.manage_roles))
        too_high = [guild.get_role(i) for i in role_ids
                    if guild.get_role(i) and guild.get_role(i) >= guild.me.top_role]
        checks.append(("Bot-Rolle über Ziel-Rollen", not too_high))
        check_txt = "\n".join(f"{'✅' if ok else '❌'} {name}" for name, ok in checks)
        embed.add_field(name="Systemcheck", value=check_txt, inline=False)
        if too_high:
            embed.set_footer(text="Tipp: Schiebe die Bot-Rolle in den Servereinstellungen höher.")
        await ctx.send(embed=embed)

    @onboard.command(name="test", description="Trockentest: prüft die Einrichtung für ein Mitglied")
    @commands.has_permissions(manage_guild=True)
    async def ob_test(self, ctx: commands.Context, mitglied: discord.Member) -> None:
        guild = ctx.guild
        conf = self.config.guild(guild)
        if not await conf.enabled():
            await ctx.send("🔴 OnboardRole ist **deaktiviert** – es würde nichts passieren.")
            return
        role_ids = await conf.role_ids()
        assignable = self._assignable_roles(guild, role_ids)
        blocked = [guild.get_role(i) for i in role_ids
                   if guild.get_role(i) and guild.get_role(i) not in assignable]
        would_add = [r for r in assignable if r.id not in {x.id for x in mitglied.roles}]

        lines = []
        lines.append(f"Würde vergeben: {', '.join(r.mention for r in would_add) or '— (nichts)'}")
        if blocked:
            lines.append(f"⚠️ Nicht vergebbar (zu hoch/gelöscht): "
                         f"{', '.join(r.mention for r in blocked if r)}")
        if any(r.id in {x.id for x in mitglied.roles} for r in assignable):
            lines.append("ℹ️ Mitglied hat bereits (einen Teil) der Rollen.")
        await ctx.send("🧪 **Trockentest**\n" + "\n".join(lines),
                       allowed_mentions=discord.AllowedMentions.none())

    # ─────────────────────────────────────────────────────────────────────────
    # HILFSFUNKTION
    # ─────────────────────────────────────────────────────────────────────────

    def _role_warning(self, guild: discord.Guild, role: discord.Role) -> str:
        if role >= guild.me.top_role:
            return ("\n⚠️ **Achtung:** Diese Rolle steht **über oder gleich** meiner höchsten "
                    "Rolle – ich kann sie nicht vergeben. Bitte schiebe meine Bot-Rolle höher.")
        if not guild.me.guild_permissions.manage_roles:
            return "\n⚠️ **Achtung:** Mir fehlt die Berechtigung **Rollen verwalten**."
        return ""

    async def cog_command_error(self, ctx: commands.Context, error) -> None:
        error = getattr(error, "original", error)
        if isinstance(error, commands.MissingPermissions):
            await ctx.send("❌ Du benötigst die Berechtigung **Server verwalten**.")
            return
        if isinstance(error, commands.NoPrivateMessage):
            await ctx.send("❌ Dieser Befehl funktioniert nur auf einem Server.")
            return
        log.exception("Fehler in OnboardRole-Befehl", exc_info=error)
        try:
            await ctx.send("❌ Ein unerwarteter Fehler ist aufgetreten.")
        except discord.DiscordException:
            pass
