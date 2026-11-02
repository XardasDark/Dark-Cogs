"""
everyoneguard.py – Hinweis-Text, wenn Mitglieder ohne Freigabe @everyone/@here nutzen.

So funktioniert es:
  Ein Bot bekommt eine Nachricht erst, NACHDEM Discord sie verschickt hat. Einen
  @everyone-Ping kann man darum nicht nachträglich zurücknehmen. Deshalb setzt
  dieser Cog auf das native Discord-Verhalten:

    • Hat ein Mitglied NICHT das Recht „@everyone, @here und alle Rollen erwähnen",
      unterdrückt Discord den Ping von selbst – „@everyone" bleibt bloßer Text.
    • Dieser Cog erkennt diesen Versuch und schreibt unter die Nachricht einen
      konfigurierbaren Hinweis-Text. Die Original-Nachricht bleibt unverändert.

  Mitglieder mit einer freigegebenen Rolle (oder mit Admin-/Mention-Everyone-Recht)
  werden in Ruhe gelassen – ihr @everyone funktioniert normal und bekommt keinen
  Hinweis.

Voraussetzungen:
  - privilegierter „Message Content"-Intent (sonst ist message.content leer)
  - in den Servereinstellungen sollte die @everyone-Rolle das Recht
    „@everyone, @here und alle Rollen erwähnen" NICHT haben (sonst pingt Discord
    trotzdem – das kann kein Bot verhindern). `/everyoneguard info` prüft das.

Befehle (Admin, „Server verwalten"):
  /everyoneguard setup [rolle]      – aktivieren (+ optional erste erlaubte Rolle)
  /everyoneguard toggle             – aktivieren/deaktivieren
  /everyoneguard rolle-add <rolle>  – Rolle freigeben (@everyone erlaubt, kein Hinweis)
  /everyoneguard rolle-remove <rolle> – Rolle wieder sperren
  /everyoneguard text               – Info-Text bearbeiten (mehrzeilig, Rollen/Kanäle)
  /everyoneguard here <an|aus>      – auch @here behandeln (Standard: an)
  /everyoneguard embed <an|aus>     – Hinweis als Embed posten (erlaubt [Text](Link))
  /everyoneguard preview            – Vorschau des Hinweis-Textes
  /everyoneguard info               – Konfiguration + Systemcheck
"""

import logging
import time
from typing import Dict, List, Optional, Tuple

import discord
from discord import app_commands
from redbot.core import Config, commands
from redbot.core.bot import Red

log = logging.getLogger("red.dark-cogs.everyoneguard")

COLOR_INFO = 0x5865F2
COLOR_OK = 0x00C853
COLOR_ERROR = 0xFF5252

# Standard-Hinweis. Enthält selbst „@everyone" – das ist unkritisch, weil der Bot
# den Text immer mit AllowedMentions.none() postet (pingt also niemanden).
INFO_DEFAULT = "Für Mitglieder ist die Nutzung von @everyone als Server-Ping nicht freigegeben."

# Pro (Server, Kanal, Mitglied) wird höchstens alle X Sekunden ein Hinweis gepostet.
COOLDOWN_SECONDS = 15


class InfoTextModal(discord.ui.Modal, title="Info-Text bearbeiten"):
    """Mehrzeiliger Editor für den Hinweis-Text (nur über Slash erreichbar)."""

    def __init__(self, cog: "EveryoneGuard", guild: discord.Guild, current: str) -> None:
        super().__init__(timeout=600)
        self.cog = cog
        self.guild = guild
        self.text_input: discord.ui.TextInput = discord.ui.TextInput(
            label="Hinweis unter der @everyone-Nachricht",
            style=discord.TextStyle.paragraph,
            default=current,
            max_length=4000,
            required=True,
            placeholder="Mehrzeilig erlaubt. Rollen als <@&ROLLEN_ID>, Kanäle als <#KANAL_ID>.",
        )
        self.add_item(self.text_input)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        await self.cog.config.guild(self.guild).info_text.set(str(self.text_input.value))
        await interaction.response.send_message("✅ Info-Text gespeichert.", ephemeral=True)


class EveryoneGuard(commands.Cog):
    """📢 EveryoneGuard – Hinweis, wenn Mitglieder ohne Freigabe @everyone nutzen."""

    def __init__(self, bot: Red):
        self.bot = bot
        self.config = Config.get_conf(self, identifier=0x45_56_47_01, force_registration=True)
        self.config.register_guild(
            enabled=False,
            allowed_role_ids=[],   # Rollen, die @everyone nutzen dürfen (kein Hinweis)
            info_text=INFO_DEFAULT,
            include_here=True,     # auch @here behandeln
            as_embed=False,        # Hinweis als Embed posten (erlaubt [Text](Link))
        )
        # (guild_id, channel_id, user_id) -> monotone Zeit des letzten Hinweises
        self._recent: Dict[Tuple[int, int, int], float] = {}

    # ─────────────────────────────────────────────────────────────────────────
    # LISTENER
    # ─────────────────────────────────────────────────────────────────────────

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message) -> None:
        if message.guild is None or message.author.bot or message.webhook_id is not None:
            return
        if not isinstance(message.author, discord.Member):
            return
        if message.type not in (discord.MessageType.default, discord.MessageType.reply):
            return

        conf = self.config.guild(message.guild)
        if not await conf.enabled():
            return

        include_here = await conf.include_here()
        if not self._mentions_everyone(message.content, include_here):
            return

        if await self._is_allowed(message.author):
            return

        if not self._cooldown_ok(message):
            return

        await self._post_info(message)

    # ─────────────────────────────────────────────────────────────────────────
    # LOGIK
    # ─────────────────────────────────────────────────────────────────────────

    @staticmethod
    def _mentions_everyone(content: str, include_here: bool) -> bool:
        """True, wenn der rohe Text @everyone (oder @here) enthält."""
        if "@everyone" in content:
            return True
        return include_here and "@here" in content

    async def _is_allowed(self, member: discord.Member) -> bool:
        """Darf dieses Mitglied @everyone nutzen (→ kein Hinweis)?"""
        perms = member.guild_permissions
        if perms.administrator or perms.mention_everyone:
            return True
        allowed_ids = set(await self.config.guild(member.guild).allowed_role_ids())
        return any(r.id in allowed_ids for r in member.roles)

    def _cooldown_ok(self, message: discord.Message) -> bool:
        key = (message.guild.id, message.channel.id, message.author.id)
        now = time.monotonic()
        last = self._recent.get(key, 0.0)
        if now - last < COOLDOWN_SECONDS:
            return False
        self._recent[key] = now
        # Alte Einträge gelegentlich aufräumen, damit der Dict nicht wächst.
        if len(self._recent) > 1000:
            cutoff = now - COOLDOWN_SECONDS
            self._recent = {k: v for k, v in self._recent.items() if v >= cutoff}
        return True

    def _format(self, template: str, member: discord.Member) -> str:
        return (template
                .replace("{user}", member.mention)
                .replace("{username}", member.display_name)
                .replace("{server}", member.guild.name))

    async def _build_payload(self, guild: discord.Guild, member: discord.Member) -> Optional[dict]:
        conf = self.config.guild(guild)
        text = (await conf.info_text() or "").strip()
        if not text:
            return None
        text = self._format(text, member)
        if await conf.as_embed():
            return {"embed": discord.Embed(description=text, color=COLOR_INFO)}
        return {"content": text}

    async def _post_info(self, message: discord.Message) -> None:
        payload = await self._build_payload(message.guild, message.author)
        if payload is None:
            return
        try:
            # AllowedMentions.none(): der Hinweis-Text darf niemanden pingen –
            # weder @everyone noch Rollen (z. B. Beispiel-Ping-Rollen im Text).
            await message.reply(
                mention_author=False,
                allowed_mentions=discord.AllowedMentions.none(),
                **payload,
            )
        except discord.HTTPException:
            # Original-Nachricht evtl. schon gelöscht oder fehlende Rechte – unkritisch.
            log.debug("EveryoneGuard: Hinweis konnte nicht gepostet werden", exc_info=True)

    # ─────────────────────────────────────────────────────────────────────────
    # BEFEHLE
    # ─────────────────────────────────────────────────────────────────────────

    @commands.hybrid_group(name="everyoneguard", aliases=["eguard"],
                           description="@everyone-Hinweis verwalten (nur Admins)")
    @commands.guild_only()
    @commands.has_permissions(manage_guild=True)
    @app_commands.default_permissions(manage_guild=True)
    async def everyoneguard(self, ctx: commands.Context) -> None:
        if ctx.invoked_subcommand is None:
            await ctx.send_help(ctx.command)

    @everyoneguard.command(name="setup",
                           description="Aktiviert den Cog (optional: erste erlaubte Rolle)")
    @app_commands.describe(rolle="Rolle, die @everyone nutzen darf (optional)")
    @commands.has_permissions(manage_guild=True)
    async def eg_setup(self, ctx: commands.Context,
                       rolle: Optional[discord.Role] = None) -> None:
        conf = self.config.guild(ctx.guild)
        await conf.enabled.set(True)
        if rolle is not None:
            async with conf.allowed_role_ids() as ids:
                if rolle.id not in ids:
                    ids.append(rolle.id)
        warn = self._everyone_perm_warning(ctx.guild)
        extra = f" Freigegeben: {rolle.mention}." if rolle else ""
        await ctx.send(
            f"✅ EveryoneGuard ist **aktiv**.{extra}\n"
            f"Tipp: Info-Text mit `/everyoneguard text`, erlaubte Rollen mit "
            f"`/everyoneguard rolle-add`.{warn}",
            allowed_mentions=discord.AllowedMentions.none(),
        )

    @everyoneguard.command(name="toggle", description="Aktiviert oder deaktiviert den Cog")
    @commands.has_permissions(manage_guild=True)
    async def eg_toggle(self, ctx: commands.Context) -> None:
        current = await self.config.guild(ctx.guild).enabled()
        await self.config.guild(ctx.guild).enabled.set(not current)
        await ctx.send("✅ EveryoneGuard ist jetzt **aktiv**." if not current
                       else "⏸️ EveryoneGuard ist jetzt **deaktiviert**.")

    @everyoneguard.command(name="rolle-add",
                           description="Gibt eine Rolle für @everyone frei (bekommt keinen Hinweis)")
    @commands.has_permissions(manage_guild=True)
    async def eg_role_add(self, ctx: commands.Context, rolle: discord.Role) -> None:
        async with self.config.guild(ctx.guild).allowed_role_ids() as ids:
            if rolle.id in ids:
                await ctx.send(f"ℹ️ {rolle.mention} ist bereits freigegeben.",
                               allowed_mentions=discord.AllowedMentions.none())
                return
            ids.append(rolle.id)
        await ctx.send(f"✅ {rolle.mention} darf jetzt @everyone nutzen.",
                       allowed_mentions=discord.AllowedMentions.none())

    @everyoneguard.command(name="rolle-remove",
                           description="Sperrt eine zuvor freigegebene Rolle wieder")
    @commands.has_permissions(manage_guild=True)
    async def eg_role_remove(self, ctx: commands.Context, rolle: discord.Role) -> None:
        async with self.config.guild(ctx.guild).allowed_role_ids() as ids:
            if rolle.id not in ids:
                await ctx.send(f"ℹ️ {rolle.mention} war nicht freigegeben.",
                               allowed_mentions=discord.AllowedMentions.none())
                return
            ids.remove(rolle.id)
        await ctx.send(f"✅ {rolle.mention} ist nicht mehr für @everyone freigegeben.",
                       allowed_mentions=discord.AllowedMentions.none())

    @everyoneguard.command(name="text",
                           description="Öffnet den mehrzeiligen Editor für den Hinweis-Text")
    @commands.has_permissions(manage_guild=True)
    async def eg_text(self, ctx: commands.Context) -> None:
        if ctx.interaction is None:
            await ctx.send(
                "ℹ️ Der mehrzeilige Editor geht nur per Slash-Befehl: `/everyoneguard text`."
            )
            return
        current = await self.config.guild(ctx.guild).info_text()
        await ctx.interaction.response.send_modal(InfoTextModal(self, ctx.guild, current))

    @everyoneguard.command(name="here", description="Auch @here behandeln (an/aus)")
    @app_commands.describe(aktiv="True = @here ebenfalls behandeln, False = nur @everyone")
    @commands.has_permissions(manage_guild=True)
    async def eg_here(self, ctx: commands.Context, aktiv: bool) -> None:
        await self.config.guild(ctx.guild).include_here.set(aktiv)
        await ctx.send("✅ @here wird jetzt **mitbehandelt**." if aktiv
                       else "✅ @here wird **ignoriert** – nur @everyone wird behandelt.")

    @everyoneguard.command(name="embed",
                           description="Hinweis als Embed posten (erlaubt [Text](Link))")
    @app_commands.describe(aktiv="True = als Embed, False = als normale Nachricht")
    @commands.has_permissions(manage_guild=True)
    async def eg_embed(self, ctx: commands.Context, aktiv: bool) -> None:
        await self.config.guild(ctx.guild).as_embed.set(aktiv)
        if aktiv:
            await ctx.send("✅ Hinweis wird als **Embed** gepostet. "
                           "Jetzt funktionieren auch Markdown-Links wie `[Text](https://…)`.")
        else:
            await ctx.send("✅ Hinweis wird als **normale Nachricht** gepostet.")

    @everyoneguard.command(name="preview", description="Zeigt eine Vorschau des Hinweis-Textes")
    @commands.has_permissions(manage_guild=True)
    async def eg_preview(self, ctx: commands.Context) -> None:
        payload = await self._build_payload(ctx.guild, ctx.author)
        if payload is None:
            await ctx.send("ℹ️ Es ist kein Info-Text gesetzt.")
            return
        await ctx.send("🔍 **Vorschau:**")
        await ctx.send(allowed_mentions=discord.AllowedMentions.none(), **payload)

    @everyoneguard.command(name="info", description="Zeigt die Konfiguration und einen Systemcheck")
    @commands.has_permissions(manage_guild=True)
    async def eg_info(self, ctx: commands.Context) -> None:
        guild = ctx.guild
        conf = self.config.guild(guild)
        enabled = await conf.enabled()
        role_ids: List[int] = await conf.allowed_role_ids()
        include_here = await conf.include_here()
        as_embed = await conf.as_embed()
        info_text = await conf.info_text()

        roles = ", ".join(r.mention for r in
                          (guild.get_role(i) for i in role_ids) if r) or "_(keine)_"

        embed = discord.Embed(title="📢 EveryoneGuard – Konfiguration",
                              color=COLOR_OK if enabled else COLOR_ERROR)
        embed.add_field(name="Status", value="🟢 aktiv" if enabled else "🔴 deaktiviert", inline=False)
        embed.add_field(name="Erlaubte Rollen", value=roles, inline=False)
        embed.add_field(name="@here mitbehandeln", value="✅ ja" if include_here else "❌ nein", inline=True)
        embed.add_field(name="Als Embed posten", value="✅ ja" if as_embed else "❌ nein", inline=True)
        snippet = (info_text or "")[:500] or "_(leer)_"
        embed.add_field(name="Info-Text", value=f">>> {snippet}", inline=False)

        # Systemcheck
        checks = [
            ("Message-Content-Intent", self.bot.intents.message_content),
            ("Recht „Nachrichten senden\"", guild.me.guild_permissions.send_messages),
            # Pingt @everyone für normale Mitglieder bereits nativ ins Leere?
            ("@everyone-Rolle ohne „Erwähnen\"-Recht",
             not guild.default_role.permissions.mention_everyone),
        ]
        check_txt = "\n".join(f"{'✅' if ok else '❌'} {name}" for name, ok in checks)
        embed.add_field(name="Systemcheck", value=check_txt, inline=False)
        if guild.default_role.permissions.mention_everyone:
            embed.set_footer(
                text="Achtung: Die @everyone-Rolle darf „@everyone erwähnen“ – Discord pingt dann "
                     "trotzdem. Entziehe dieses Recht in den Servereinstellungen, sonst hilft der "
                     "Hinweis nur optisch."
            )
        await ctx.send(embed=embed)

    # ─────────────────────────────────────────────────────────────────────────
    # HILFEN / FEHLER
    # ─────────────────────────────────────────────────────────────────────────

    def _everyone_perm_warning(self, guild: discord.Guild) -> str:
        if guild.default_role.permissions.mention_everyone:
            return ("\n⚠️ **Achtung:** Die @everyone-Rolle hat das Recht „@everyone erwähnen“. "
                    "Discord pingt dann trotzdem – entziehe das Recht in den Servereinstellungen, "
                    "sonst erscheint nur der Hinweis.")
        return ""

    async def cog_command_error(self, ctx: commands.Context, error) -> None:
        error = getattr(error, "original", error)
        if isinstance(error, commands.MissingPermissions):
            await ctx.send("❌ Du benötigst die Berechtigung **Server verwalten**.")
            return
        if isinstance(error, commands.NoPrivateMessage):
            await ctx.send("❌ Dieser Befehl funktioniert nur auf einem Server.")
            return
        log.exception("Fehler in EveryoneGuard-Befehl", exc_info=error)
        try:
            await ctx.send("❌ Ein unerwarteter Fehler ist aufgetreten.")
        except discord.DiscordException:
            pass
