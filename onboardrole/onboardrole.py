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
  /onboard willkommen <an|aus> [kanal] – Willkommens-Embed (kanal leer = DM)
  /onboard onboarding-dm [text]     – Onboarding-Nachricht per DM (leer = aus)
  /onboard info                     – Konfiguration + Systemcheck
  /onboard test <mitglied>          – Trockentest (prüft nur, vergibt nichts)

Willkommens-Embed: zeigt automatisch den Avatar des neuen Mitglieds als Thumbnail
und ein zufällig gewähltes Banner-Bild aus dem Banner-Channel.
"""

import logging
import random
from typing import Dict, List, Optional

import discord
from discord import app_commands
from redbot.core import Config, commands
from redbot.core.bot import Red

log = logging.getLogger("red.dark-cogs.onboardrole")

COLOR_INFO = 0x5865F2
COLOR_OK = 0x00C853
COLOR_ERROR = 0xFF5252

# Willkommens-Embed
WELCOME_COLOR = 11570519
WELCOME_DESCRIPTION = (
    "Willkommen !\n\n"
    "Schön, dass du dabei bist, {member}!\n\n"
    "Mach's dir gemütlich, schau dich ein bisschen um und viel Spaß beim gemeinsamen Zocken."
)

# Banner: zufälliges Bild aus diesem Channel (enthält ausschließlich Banner-Nachrichten).
BANNER_CHANNEL_ID = 1561495949023514665
BANNER_HISTORY_LIMIT = 100


class OnboardRole(commands.Cog):
    """🎓 OnboardRole – Rollen automatisch nach dem Onboarding vergeben."""

    def __init__(self, bot: Red):
        self.bot = bot
        self.config = Config.get_conf(self, identifier=0x0B_0A_4D_01, force_registration=True)
        self.config.register_guild(
            enabled=False,
            role_ids=[],
            log_channel_id=None,
            welcome_enabled=False,      # Willkommens-Embed an/aus
            welcome_channel_id=None,    # None = per DM, sonst Kanal-ID
            onboarding_dm_text=None,    # None = keine Onboarding-DM
            last_banner_message_id=None,  # verhindert dasselbe Banner 2x hintereinander
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
        await self._send_onboarding_dm(member)
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

    @staticmethod
    def _banner_from_message(msg: discord.Message) -> Optional[str]:
        """Bild-URL aus einer Banner-Nachricht (Anhang bevorzugt, sonst Embed-Bild)."""
        for att in msg.attachments:
            if att.content_type is None or att.content_type.startswith("image"):
                return att.url
        for emb in msg.embeds:
            if emb.image and emb.image.url:
                return emb.image.url
        return None

    async def _pick_banner_url(self, guild: discord.Guild) -> Optional[str]:
        """Holt zufällig ein Banner-Bild aus dem Banner-Channel.

        Vermeidet dasselbe Banner zweimal hintereinander (pro Server gemerkt) und
        liest die Attachment-URL pro Aufruf frisch, damit die signierte CDN-URL
        nicht abgelaufen ist.
        """
        channel = guild.get_channel(BANNER_CHANNEL_ID)
        if channel is None:
            try:
                channel = await self.bot.fetch_channel(BANNER_CHANNEL_ID)
            except discord.HTTPException:
                return None
        if not isinstance(channel, (discord.TextChannel, discord.Thread)):
            return None

        # Alle Banner-Nachrichten einsammeln (message_id -> url).
        banners: Dict[int, str] = {}
        try:
            async for msg in channel.history(limit=BANNER_HISTORY_LIMIT):
                url = self._banner_from_message(msg)
                if url:
                    banners[msg.id] = url
        except discord.HTTPException:
            return None
        if not banners:
            return None

        conf = self.config.guild(guild)
        last_id = await conf.last_banner_message_id()
        candidates = [mid for mid in banners if mid != last_id] or list(banners)
        chosen_id = random.choice(candidates)
        await conf.last_banner_message_id.set(chosen_id)
        return banners[chosen_id]

    async def _send_welcome(self, member: discord.Member) -> None:
        conf = self.config.guild(member.guild)
        if not await conf.welcome_enabled():
            return

        embed = discord.Embed(
            description=WELCOME_DESCRIPTION.format(member=member.mention),
            color=discord.Color(WELCOME_COLOR),
        )
        embed.set_thumbnail(url=member.display_avatar.url)
        banner_url = await self._pick_banner_url(member.guild)
        if banner_url:
            embed.set_image(url=banner_url)

        chan_id = await conf.welcome_channel_id()
        try:
            if chan_id:
                channel = member.guild.get_channel(chan_id)
                if channel is not None:
                    # Ping gehört in den content – Mentions im Embed benachrichtigen nicht.
                    await channel.send(
                        content=member.mention,
                        embed=embed,
                        allowed_mentions=discord.AllowedMentions(users=True),
                    )
            else:
                await member.send(embed=embed)
        except discord.HTTPException:
            pass  # DMs zu / fehlende Rechte – nicht kritisch

    async def _send_onboarding_dm(self, member: discord.Member) -> None:
        text = await self.config.guild(member.guild).onboarding_dm_text()
        if not text:
            return
        try:
            await member.send(self._format(text, member))
        except discord.HTTPException:
            pass  # DMs zu – nicht kritisch

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
                     description="Willkommens-Embed an/aus und wo (Kanal leer = per DM)")
    @app_commands.describe(
        aktiv="Willkommens-Embed anzeigen (True) oder abschalten (False)",
        kanal="Kanal für die Nachricht (leer = per DM an das Mitglied)",
    )
    @commands.has_permissions(manage_guild=True)
    async def ob_welcome(self, ctx: commands.Context, aktiv: bool,
                         kanal: Optional[discord.TextChannel] = None) -> None:
        conf = self.config.guild(ctx.guild)
        await conf.welcome_enabled.set(aktiv)
        if not aktiv:
            await ctx.send("⏸️ Willkommens-Embed deaktiviert.")
            return
        await conf.welcome_channel_id.set(kanal.id if kanal else None)
        ziel = kanal.mention if kanal else "per **DM**"
        await ctx.send(
            f"✅ Willkommens-Embed aktiviert ({ziel}). "
            f"Avatar als Thumbnail + zufälliges Banner werden automatisch gesetzt.",
            allowed_mentions=discord.AllowedMentions.none(),
        )

    @onboard.command(name="onboarding-dm",
                     description="Onboarding-Nachricht per DM an neue Mitglieder (leer = aus)")
    @app_commands.describe(
        text="DM-Text. Platzhalter: {user} {username} {server} {count}. Leer = deaktivieren.",
    )
    @commands.has_permissions(manage_guild=True)
    async def ob_onboarding_dm(self, ctx: commands.Context, text: Optional[str] = None) -> None:
        conf = self.config.guild(ctx.guild)
        if not text:
            await conf.onboarding_dm_text.set(None)
            await ctx.send("⏸️ Onboarding-DM deaktiviert.")
            return
        await conf.onboarding_dm_text.set(text)
        vorschau = self._format(text, ctx.author)
        await ctx.send(f"✅ Onboarding-DM gesetzt.\nVorschau:\n> {vorschau}",
                       allowed_mentions=discord.AllowedMentions.none())

    @onboard.command(name="info", description="Zeigt die Konfiguration und einen Systemcheck")
    @commands.has_permissions(manage_guild=True)
    async def ob_info(self, ctx: commands.Context) -> None:
        guild = ctx.guild
        conf = self.config.guild(guild)
        enabled = await conf.enabled()
        role_ids = await conf.role_ids()
        log_id = await conf.log_channel_id()
        welcome_enabled = await conf.welcome_enabled()
        welcome_chan = await conf.welcome_channel_id()
        onboarding_dm = await conf.onboarding_dm_text()

        roles = ", ".join(r.mention for r in
                          (guild.get_role(i) for i in role_ids) if r) or "_(keine)_"
        log_chan = guild.get_channel(log_id) if log_id else None

        embed = discord.Embed(title="🎓 OnboardRole – Konfiguration",
                              color=COLOR_OK if enabled else COLOR_ERROR)
        embed.add_field(name="Status", value="🟢 aktiv" if enabled else "🔴 deaktiviert", inline=False)
        embed.add_field(name="Rollen", value=roles, inline=False)
        embed.add_field(name="Log-Kanal", value=log_chan.mention if log_chan else "_(aus)_", inline=False)
        if welcome_enabled:
            ziel = (guild.get_channel(welcome_chan).mention
                    if welcome_chan and guild.get_channel(welcome_chan) else "per DM")
            embed.add_field(name="Willkommen", value=f"🟢 Embed {ziel}", inline=False)
        else:
            embed.add_field(name="Willkommen", value="_(aus)_", inline=False)
        embed.add_field(
            name="Onboarding-DM",
            value=f"🟢 aktiv\n> {onboarding_dm}" if onboarding_dm else "_(aus)_",
            inline=False,
        )

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
