"""
cog.py – Haupt-Cog von TempVoice

Admin richtet einen "Channel erstellen"-Voice-Channel ein. Betritt ihn ein
Mitglied, erstellt der Bot einen eigenen temporären Voice-Channel, zieht das
Mitglied hinein und macht es zum Besitzer. Gesteuert wird über ein festes,
persistentes Button-Panel in einem Textkanal (#voice-steuerung). Leere Channel
werden automatisch gelöscht. Pro Mitglied wird ein Einstellungs-Profil gemerkt
und beim nächsten Channel wieder angewendet.

Befehle (alle deutsch):
  /tempvoice setup                     (Admin) – Kategorie, Generator & Panel anlegen
  /tempvoice generator <voice>         (Admin) – Generator-Channel hinzufügen/entfernen
  /tempvoice kategorie [kategorie]     (Admin) – Zielkategorie für neue Channel
  /tempvoice interface [#kanal]        (Admin) – Steuer-Panel (neu) posten
  /tempvoice defaults ...              (Admin) – Standardwerte für neue Channel
  /tempvoice info                      (Admin) – aktuelle Konfiguration
  /tempvoice profil                    (alle)  – eigenes Profil anzeigen
  /tempvoice reset                     (alle)  – eigenes Profil zurücksetzen
  /tempvoice gesperrte                 (alle)  – Sperrliste ansehen & entsperren
"""

import asyncio
import logging
from datetime import datetime, timezone
from typing import Callable, Dict, Optional

import discord
from discord import app_commands
from redbot.core import Config, commands
from redbot.core.bot import Red

from . import constants as C
from . import controls, views
from .storage import TempVoiceStore, default_settings

log = logging.getLogger("red.dark-cogs.tempvoice")


class TempVoice(commands.Cog):
    """🔊 TempVoice – temporäre Voice-Channel, die User selbst erstellen & steuern."""

    def __init__(self, bot: Red):
        self.bot = bot
        self.config = Config.get_conf(self, identifier=0x7E_4_0_17_01, force_registration=True)
        self.config.register_guild(
            generator_ids=[],
            category_id=None,
            interface_channel_id=None,
            interface_message_id=None,
            channel_defaults=default_settings(),
            temp_channels={},
        )
        self.config.register_member(profile=None)
        self.store = TempVoiceStore(self.config)
        self._delete_tasks: Dict[int, asyncio.Task] = {}

    async def cog_load(self) -> None:
        # Persistente Panel-View registrieren (übersteht Neustarts)
        self.bot.add_view(views.TempVoicePanel(self))
        # Verwaiste/leere Channel aufräumen
        self.bot.loop.create_task(self._cleanup_orphans())

    def cog_unload(self) -> None:
        for task in self._delete_tasks.values():
            task.cancel()
        self._delete_tasks.clear()

    # ─────────────────────────────────────────────────────────────────────────
    # LIFECYCLE-HILFEN
    # ─────────────────────────────────────────────────────────────────────────

    async def _cleanup_orphans(self) -> None:
        await self.bot.wait_until_red_ready()
        for guild in self.bot.guilds:
            temps = await self.store.all_temp_channels(guild)
            for cid_str in list(temps):
                channel = guild.get_channel(int(cid_str))
                if channel is None:
                    await self.store.remove_temp_channel(guild, int(cid_str))
                elif not channel.members:
                    try:
                        await channel.delete(reason="TempVoice: leerer Channel beim Start")
                    except discord.HTTPException:
                        pass
                    await self.store.remove_temp_channel(guild, int(cid_str))

    # ─────────────────────────────────────────────────────────────────────────
    # VOICE-STATE: Erstellen & automatisches Löschen
    # ─────────────────────────────────────────────────────────────────────────

    @commands.Cog.listener()
    async def on_voice_state_update(self, member: discord.Member,
                                    before: discord.VoiceState,
                                    after: discord.VoiceState) -> None:
        if member.bot or member.guild is None:
            return
        guild = member.guild

        # 1) Betritt der User den Generator-Channel → neuen Channel erstellen
        if after.channel and await self.store.is_generator(guild, after.channel.id):
            await self._create_temp_channel(member, after.channel)

        # 2) Hat der User einen temporären Channel verlassen → ggf. aufräumen
        left = before.channel
        if left is not None and (after.channel is None or after.channel.id != left.id):
            if await self.store.is_temp_channel(guild, left.id):
                self._schedule_empty_check(left)

    async def _create_temp_channel(self, member: discord.Member,
                                   generator: discord.VoiceChannel) -> None:
        guild = member.guild
        settings = await self.store.effective_settings(member, guild)

        # Kategorie bestimmen
        cat_id = await self.store.get_category_id(guild)
        category = guild.get_channel(cat_id) if cat_id else None
        if not isinstance(category, discord.CategoryChannel):
            category = generator.category

        # Name aus Vorlage
        name = (settings.get("name") or C.DEFAULT_NAME_TEMPLATE)
        name = name.replace(C.NAME_PLACEHOLDER, member.display_name).strip()[:C.MAX_CHANNEL_NAME]
        if not name:
            name = member.display_name[:C.MAX_CHANNEL_NAME] or "Voice-Channel"

        bitrate = int(min(settings.get("bitrate") or C.DEFAULT_BITRATE, guild.bitrate_limit))
        bitrate = max(C.MIN_BITRATE, bitrate)
        limit = int(settings.get("limit") or 0)
        region = settings.get("region")
        overwrites = controls.build_overwrites(guild, member, settings)

        try:
            channel = await guild.create_voice_channel(
                name=name, category=category, user_limit=limit, bitrate=bitrate,
                rtc_region=region, overwrites=overwrites,
                reason=f"TempVoice: Channel für {member} erstellt",
            )
        except discord.HTTPException:
            # Fallback ohne optionale Felder (z. B. ungültige Region/Bitrate)
            try:
                channel = await guild.create_voice_channel(
                    name=name, category=category, overwrites=overwrites,
                    reason=f"TempVoice: Channel für {member} erstellt (Fallback)",
                )
            except discord.HTTPException:
                log.exception("Konnte TempVoice-Channel für %s nicht erstellen", member)
                return

        await self.store.add_temp_channel(
            guild, channel.id, member.id, datetime.now(timezone.utc).isoformat())

        try:
            await member.move_to(channel, reason="TempVoice: in eigenen Channel verschoben")
        except discord.Forbidden:
            # Mir fehlt das Recht „Mitglieder verschieben" → Channel behalten & Besitzer informieren
            await self._notify_cannot_move(member, channel)
        except discord.HTTPException:
            # User hat den Voice sofort wieder verlassen → leeren Channel entfernen
            self._schedule_empty_check(channel)

    async def _notify_cannot_move(self, member: discord.Member,
                                  channel: discord.VoiceChannel) -> None:
        """Weist den Besitzer darauf hin, dass dem Bot „Mitglieder verschieben" fehlt."""
        hinweis = (
            f"⚠️ Ich konnte dich nicht automatisch in deinen neuen Channel verschieben, "
            f"weil mir die Berechtigung **Mitglieder verschieben** fehlt.\n"
            f"Bitte tritt {channel.mention} **manuell** bei. Ein Admin kann dem Bot dieses "
            f"Recht geben, dann klappt es künftig automatisch."
        )
        # Bevorzugt in den Text-Chat des neuen Voice-Channels, sonst per DM
        try:
            await channel.send(f"{member.mention} {hinweis}")
            return
        except discord.HTTPException:
            pass
        try:
            await member.send(hinweis)
        except discord.HTTPException:
            pass

    def _schedule_empty_check(self, channel: discord.VoiceChannel) -> None:
        if channel.id in self._delete_tasks:
            return
        self._delete_tasks[channel.id] = self.bot.loop.create_task(
            self._delete_when_empty(channel))

    async def _delete_when_empty(self, channel: discord.VoiceChannel) -> None:
        try:
            await asyncio.sleep(C.EMPTY_DELETE_GRACE)
            guild = channel.guild
            current = guild.get_channel(channel.id)
            if current is None:
                await self.store.remove_temp_channel(guild, channel.id)
                return
            if not current.members:
                try:
                    await current.delete(reason="TempVoice: Channel leer")
                except discord.HTTPException:
                    pass
                await self.store.remove_temp_channel(guild, channel.id)
        except asyncio.CancelledError:
            pass
        finally:
            self._delete_tasks.pop(channel.id, None)

    @commands.Cog.listener()
    async def on_guild_channel_delete(self, channel: discord.abc.GuildChannel) -> None:
        if isinstance(channel, discord.VoiceChannel):
            if await self.store.is_temp_channel(channel.guild, channel.id):
                await self.store.remove_temp_channel(channel.guild, channel.id)

    # ─────────────────────────────────────────────────────────────────────────
    # HILFEN FÜR DIE VIEWS  (Besitz auflösen, Aktion ausführen)
    # ─────────────────────────────────────────────────────────────────────────

    async def resolve_owned(self, interaction: discord.Interaction) -> Optional[discord.VoiceChannel]:
        """Gibt den TempVoice-Channel zurück, in dem der User sitzt UND Besitzer ist.
        Andernfalls wird eine ephemere Fehlermeldung gesendet und None zurückgegeben."""
        member = interaction.user
        guild = interaction.guild
        vc = member.voice.channel if (member and member.voice) else None
        if vc is None or not await self.store.is_temp_channel(guild, vc.id):
            await interaction.response.send_message(
                "❌ Du befindest dich in keinem eigenen TempVoice-Channel. "
                "Betritt zuerst den **Channel erstellen**-Voice-Channel.", ephemeral=True)
            return None
        owner_id = await self.store.get_owner_id(guild, vc.id)
        if owner_id != member.id:
            await interaction.response.send_message(
                "❌ Nur der **Besitzer** kann diesen Channel steuern. "
                "Ist der Besitzer nicht mehr da, nutze 👑 **Übernehmen**.", ephemeral=True)
            return None
        return vc

    async def run_action(self, interaction: discord.Interaction, func: Callable,
                         channel: discord.VoiceChannel) -> None:
        """Für Buttons ohne weitere Eingabe (z. B. Sperren/Verbergen)."""
        await self.apply_submit(interaction, channel, func)

    async def apply_submit(self, interaction: discord.Interaction,
                           channel: discord.VoiceChannel, func: Callable, *args,
                           edit: bool = False) -> None:
        """Führt eine controls-Aktion aus und antwortet (ephemer oder per edit)."""
        owner = interaction.user
        try:
            message = await func(self.store, channel, owner, *args)
        except ValueError as e:
            message = f"❌ {e}"
        except discord.Forbidden:
            message = ("❌ Mir fehlen die Rechte für diese Aktion. "
                       "Bitte gib dem Bot **Kanäle verwalten** und **Mitglieder verschieben**.")
        except discord.HTTPException:
            log.exception("TempVoice-Aktion fehlgeschlagen")
            message = "❌ Ein unerwarteter Fehler ist aufgetreten."

        if edit:
            await interaction.response.edit_message(content=message, view=None)
        else:
            await interaction.response.send_message(message, ephemeral=True)

    async def claim(self, interaction: discord.Interaction) -> None:
        """👑 Übernehmen – nur wenn der bisherige Besitzer nicht mehr im Channel ist."""
        member = interaction.user
        guild = interaction.guild
        vc = member.voice.channel if (member and member.voice) else None
        if vc is None or not await self.store.is_temp_channel(guild, vc.id):
            await interaction.response.send_message(
                "❌ Du befindest dich in keinem TempVoice-Channel.", ephemeral=True)
            return
        owner_id = await self.store.get_owner_id(guild, vc.id)
        if owner_id == member.id:
            await interaction.response.send_message(
                "ℹ️ Du bist bereits der Besitzer dieses Channels.", ephemeral=True)
            return
        owner = guild.get_member(owner_id) if owner_id else None
        if owner is not None and owner in vc.members:
            await interaction.response.send_message(
                f"❌ Der Besitzer {owner.mention} ist noch im Channel. Übernehmen nicht möglich.",
                ephemeral=True)
            return
        await self.store.set_owner(guild, vc.id, member.id)
        try:
            await vc.set_permissions(member, view_channel=True, connect=True,
                                     reason="TempVoice: Besitz übernommen")
        except discord.HTTPException:
            pass
        await interaction.response.send_message(
            "👑 Du bist jetzt der **Besitzer** dieses Channels.", ephemeral=True)

    # ─────────────────────────────────────────────────────────────────────────
    # BEFEHLE – ADMIN
    # ─────────────────────────────────────────────────────────────────────────

    @commands.hybrid_group(name="tempvoice", description="Temporäre Voice-Channel verwalten")
    @commands.guild_only()
    async def tempvoice(self, ctx: commands.Context) -> None:
        if ctx.invoked_subcommand is None:
            await ctx.send_help(ctx.command)

    @tempvoice.command(name="setup", description="Legt Kategorie, Generator-Channel und Steuer-Panel an")
    @commands.has_permissions(manage_guild=True)
    @app_commands.default_permissions(manage_guild=True)
    async def tv_setup(self, ctx: commands.Context) -> None:
        guild = ctx.guild
        me = guild.me
        if not me.guild_permissions.manage_channels:
            await ctx.send("❌ Mir fehlt die Berechtigung **Kanäle verwalten**.")
            return

        category = await guild.create_category(
            C.SETUP_CATEGORY_NAME, reason="TempVoice: Setup")
        generator = await guild.create_voice_channel(
            C.SETUP_GENERATOR_NAME, category=category, reason="TempVoice: Setup")
        interface = await guild.create_text_channel(
            C.SETUP_INTERFACE_NAME, category=category, reason="TempVoice: Setup")

        await self.store.set_category_id(guild, category.id)
        await self.store.add_generator_id(guild, generator.id)

        msg = await interface.send(
            embed=views.build_interface_embed(guild), view=views.TempVoicePanel(self))
        try:
            await msg.pin(reason="TempVoice: Steuer-Panel")
        except discord.HTTPException:
            pass
        await self.store.set_interface(guild, interface.id, msg.id)

        await ctx.send(
            f"✅ **TempVoice ist eingerichtet!**\n"
            f"- Generator: {generator.mention} (hier beitreten → eigener Channel)\n"
            f"- Steuerung: {interface.mention}\n"
            f"Mitglieder können jetzt loslegen. 🔊")

    @tempvoice.command(name="generator",
                       description="Fügt einen Generator-Channel hinzu oder entfernt ihn")
    @commands.has_permissions(manage_guild=True)
    @app_commands.describe(channel="Der Voice-Channel, der als „Channel erstellen\" dient")
    async def tv_generator(self, ctx: commands.Context, channel: discord.VoiceChannel) -> None:
        if await self.store.is_generator(ctx.guild, channel.id):
            await self.store.remove_generator_id(ctx.guild, channel.id)
            await ctx.send(f"✅ {channel.mention} ist **kein** Generator-Channel mehr.")
        else:
            await self.store.add_generator_id(ctx.guild, channel.id)
            await ctx.send(f"✅ {channel.mention} ist jetzt ein **Generator-Channel**.")

    @tempvoice.command(name="kategorie",
                       description="Legt die Kategorie fest, in der neue Channel erstellt werden")
    @commands.has_permissions(manage_guild=True)
    @app_commands.describe(kategorie="Zielkategorie (leer = Kategorie des Generators)")
    async def tv_kategorie(self, ctx: commands.Context,
                           kategorie: Optional[discord.CategoryChannel] = None) -> None:
        await self.store.set_category_id(ctx.guild, kategorie.id if kategorie else None)
        if kategorie:
            await ctx.send(f"✅ Neue Channel werden in **{kategorie.name}** erstellt.")
        else:
            await ctx.send("✅ Neue Channel werden in der Kategorie des Generators erstellt.")

    @tempvoice.command(name="interface", description="Postet das Steuer-Panel in einem Textkanal")
    @commands.has_permissions(manage_guild=True)
    @app_commands.describe(kanal="Zielkanal (leer = aktueller Kanal)")
    async def tv_interface(self, ctx: commands.Context,
                           kanal: Optional[discord.TextChannel] = None) -> None:
        target = kanal or ctx.channel
        msg = await target.send(
            embed=views.build_interface_embed(ctx.guild), view=views.TempVoicePanel(self))
        try:
            await msg.pin(reason="TempVoice: Steuer-Panel")
        except discord.HTTPException:
            pass
        await self.store.set_interface(ctx.guild, target.id, msg.id)
        await ctx.send(f"✅ Steuer-Panel wurde in {target.mention} gepostet.")

    @tempvoice.command(name="defaults", description="Setzt Standardwerte für neu erstellte Channel")
    @commands.has_permissions(manage_guild=True)
    @app_commands.describe(
        name="Namensvorlage, {user} wird ersetzt (z. B. „{user}s Channel\")",
        limit="Standard-Mitgliederlimit (0 = unbegrenzt)",
        bitrate="Standard-Bitrate in kbps",
        gesperrt="Neue Channel standardmäßig gesperrt?",
        verborgen="Neue Channel standardmäßig verborgen?",
    )
    async def tv_defaults(self, ctx: commands.Context, name: Optional[str] = None,
                          limit: Optional[int] = None, bitrate: Optional[int] = None,
                          gesperrt: Optional[bool] = None,
                          verborgen: Optional[bool] = None) -> None:
        changes: Dict[str, object] = {}
        if name is not None:
            changes["name"] = name.strip()[:C.MAX_CHANNEL_NAME]
        if limit is not None:
            changes["limit"] = max(0, min(limit, C.MAX_USER_LIMIT))
        if bitrate is not None:
            changes["bitrate"] = max(C.MIN_BITRATE, min(bitrate * 1000, int(ctx.guild.bitrate_limit)))
        if gesperrt is not None:
            changes["locked"] = gesperrt
        if verborgen is not None:
            changes["hidden"] = verborgen
        if not changes:
            await ctx.send("ℹ️ Keine Werte angegeben. Nutze die Optionen des Befehls.")
            return
        await self.store.update_defaults(ctx.guild, **changes)
        await ctx.send("✅ Standardwerte aktualisiert.")

    @tempvoice.command(name="info", description="Zeigt die aktuelle TempVoice-Konfiguration")
    @commands.has_permissions(manage_guild=True)
    async def tv_info(self, ctx: commands.Context) -> None:
        guild = ctx.guild
        gen_ids = await self.store.get_generator_ids(guild)
        cat_id = await self.store.get_category_id(guild)
        if_chan_id, _ = await self.store.get_interface(guild)
        defaults = await self.store.get_defaults(guild)
        temps = await self.store.all_temp_channels(guild)

        generators = ", ".join(c.mention for c in
                               (guild.get_channel(i) for i in gen_ids) if c) or "_(keine)_"
        category = guild.get_channel(cat_id) if cat_id else None
        interface = guild.get_channel(if_chan_id) if if_chan_id else None

        embed = discord.Embed(title="⚙️ TempVoice-Konfiguration", color=C.COLOR_INFO)
        embed.add_field(name="Generator-Channel", value=generators, inline=False)
        embed.add_field(name="Kategorie",
                        value=category.mention if category else "_(Kategorie des Generators)_",
                        inline=False)
        embed.add_field(name="Steuer-Panel",
                        value=interface.mention if interface else "_(nicht gesetzt)_", inline=False)
        embed.add_field(name="Aktive temporäre Channel", value=str(len(temps)), inline=False)
        limit_txt = "unbegrenzt" if not defaults["limit"] else str(defaults["limit"])
        embed.add_field(
            name="Standardwerte",
            value=(f"Name: `{defaults['name']}`\n"
                   f"Limit: {limit_txt} · Bitrate: {defaults['bitrate'] // 1000} kbps\n"
                   f"Gesperrt: {'ja' if defaults['locked'] else 'nein'} · "
                   f"Verborgen: {'ja' if defaults['hidden'] else 'nein'}"),
            inline=False)
        await ctx.send(embed=embed)

    # ─────────────────────────────────────────────────────────────────────────
    # BEFEHLE – MITGLIEDER
    # ─────────────────────────────────────────────────────────────────────────

    @tempvoice.command(name="profil", description="Zeigt dein gespeichertes TempVoice-Profil")
    async def tv_profil(self, ctx: commands.Context) -> None:
        profile = await self.store.get_profile(ctx.author)
        if not profile:
            await ctx.send("ℹ️ Du hast noch kein Profil. Es entsteht automatisch, "
                           "sobald du einen Channel erstellst und anpasst.")
            return
        limit_txt = "unbegrenzt" if not profile.get("limit") else str(profile["limit"])
        region = C.REGION_NAMES.get(profile.get("region"), profile.get("region") or "Automatisch")
        permit = ", ".join(f"<@{i}>" if ctx.guild.get_member(i) else f"<@&{i}>"
                           for i in profile.get("permit", [])) or "–"
        reject = ", ".join(f"<@{i}>" for i in profile.get("reject", [])) or "–"
        embed = discord.Embed(title="🔊 Dein TempVoice-Profil", color=C.COLOR_INFO)
        embed.add_field(name="Name", value=f"`{profile.get('name')}`", inline=False)
        embed.add_field(name="Limit", value=limit_txt, inline=True)
        embed.add_field(name="Bitrate", value=f"{(profile.get('bitrate') or 0) // 1000} kbps", inline=True)
        embed.add_field(name="Region", value=region, inline=True)
        embed.add_field(name="Gesperrt", value="ja" if profile.get("locked") else "nein", inline=True)
        embed.add_field(name="Verborgen", value="ja" if profile.get("hidden") else "nein", inline=True)
        embed.add_field(name="✅ Erlaubt", value=permit, inline=False)
        embed.add_field(name="🚫 Gesperrt", value=reject, inline=False)
        await ctx.send(embed=embed, ephemeral=True)

    @tempvoice.command(name="reset", description="Setzt dein TempVoice-Profil auf die Standardwerte zurück")
    async def tv_reset(self, ctx: commands.Context) -> None:
        await self.store.clear_profile(ctx.author)
        await ctx.send("✅ Dein Profil wurde zurückgesetzt. Neue Channel starten wieder mit den "
                       "Server-Standardwerten.", ephemeral=True)

    @tempvoice.command(name="gesperrte", description="Zeigt deine Sperrliste und entsperrt Mitglieder")
    async def tv_gesperrte(self, ctx: commands.Context) -> None:
        profile = await self.store.get_profile(ctx.author)
        reject = list(profile.get("reject", [])) if profile else []
        if not reject:
            await ctx.send("ℹ️ Deine Sperrliste ist leer.", ephemeral=True)
            return
        view = _UnblockView(self, ctx.author, reject)
        await ctx.send("🚫 **Deine Sperrliste** – wähle Mitglieder zum Entsperren:",
                       view=view, ephemeral=True)

    # ─────────────────────────────────────────────────────────────────────────
    # FEHLER-HANDLER
    # ─────────────────────────────────────────────────────────────────────────

    async def cog_command_error(self, ctx: commands.Context, error) -> None:
        error = getattr(error, "original", error)
        if isinstance(error, commands.MissingPermissions):
            await ctx.send("❌ Du benötigst die Berechtigung **Server verwalten**.")
            return
        if isinstance(error, commands.NoPrivateMessage):
            await ctx.send("❌ Dieser Befehl funktioniert nur auf einem Server.")
            return
        log.exception("Fehler in TempVoice-Befehl", exc_info=error)
        try:
            await ctx.send("❌ Ein unerwarteter Fehler ist aufgetreten.")
        except discord.DiscordException:
            pass


class _UnblockView(discord.ui.View):
    """Ephemere View zum Entsperren einzelner Mitglieder aus der eigenen Sperrliste."""

    def __init__(self, cog: TempVoice, owner: discord.Member, reject_ids: list):
        super().__init__(timeout=120)
        self.cog = cog
        self.owner = owner
        guild = owner.guild
        options = []
        for uid in reject_ids[:25]:
            member = guild.get_member(uid)
            label = member.display_name if member else f"Unbekannt ({uid})"
            options.append(discord.SelectOption(label=label[:100], value=str(uid)))
        self.select = discord.ui.Select(
            placeholder="Zu entsperrende Mitglieder …", options=options,
            min_values=1, max_values=len(options))
        self.select.callback = self._callback
        self.add_item(self.select)

    async def _callback(self, interaction: discord.Interaction):
        to_unblock = {int(v) for v in self.select.values}
        profile = await self.cog.store.update_profile(self.owner)
        reject = [i for i in profile.get("reject", []) if i not in to_unblock]
        await self.cog.store.update_profile(self.owner, reject=reject)

        # Falls der Besitzer gerade in seinem Channel ist, Sperre dort auch live aufheben
        vc = self.owner.voice.channel if self.owner.voice else None
        if vc and await self.cog.store.is_temp_channel(self.owner.guild, vc.id) \
                and await self.cog.store.get_owner_id(self.owner.guild, vc.id) == self.owner.id:
            for uid in to_unblock:
                target = self.owner.guild.get_member(uid)
                if target:
                    try:
                        await vc.set_permissions(target, overwrite=None,
                                                 reason="TempVoice: entsperrt")
                    except discord.HTTPException:
                        pass

        mentions = ", ".join(f"<@{u}>" for u in to_unblock)
        await interaction.response.edit_message(
            content=f"✅ Entsperrt: {mentions}", view=None)
