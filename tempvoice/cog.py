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
from .storage import TempVoiceStore

log = logging.getLogger("red.dark-cogs.tempvoice")


class TempVoice(commands.Cog):
    """🔊 TempVoice – temporäre Voice-Channel, die User selbst erstellen & steuern."""

    def __init__(self, bot: Red):
        self.bot = bot
        self.config = Config.get_conf(self, identifier=0x7E_4_0_17_01, force_registration=True)
        self.config.register_guild(
            generators={},               # {gen_id: {category_id, name, limit, bitrate, region, locked, hidden}}
            interface_channel_id=None,
            interface_message_id=None,
            temp_channels={},
        )
        self.config.register_member(profiles={})   # {gen_id: partial-override}
        self.store = TempVoiceStore(self.config)
        self._delete_tasks: Dict[int, asyncio.Task] = {}
        self._pending_knocks: set = set()  # (channel_id, knocker_id)
        self._pending_waits: set = set()   # (main_channel_id, waiter_id)

    async def cog_load(self) -> None:
        # Persistente Views registrieren (übersteht Neustarts)
        self.bot.add_view(views.TempVoicePanel(self))
        self.bot.add_view(views.KnockView(self))
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
                    await self._cleanup_waiting_room(guild, int(cid_str))
                    await self.store.remove_temp_channel(guild, int(cid_str))
                elif not channel.members:
                    await self._cleanup_waiting_room(guild, int(cid_str))
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

        # Nur echte Channel-Wechsel auswerten – nicht Mute/Deafen/Video-Umschaltungen,
        # die ebenfalls on_voice_state_update auslösen (sonst Mehrfach-Erstellung + Spam).
        joined = after.channel is not None and (
            before.channel is None or before.channel.id != after.channel.id)

        # 1) Betritt der User den Generator-Channel → neuen Channel erstellen
        if joined and await self.store.is_generator(guild, after.channel.id):
            await self._create_temp_channel(member, after.channel)

        # 1b) Betritt der User einen Warteraum → Besitzer benachrichtigen
        if joined:
            main_id, owner_id = await self.store.find_main_by_waiting(guild, after.channel.id)
            if main_id is not None:
                await self._handle_waiter(member, after.channel, main_id, owner_id)

        # 2) Hat der User einen Channel verlassen → aufräumen
        left = before.channel
        if left is not None and (after.channel is None or after.channel.id != left.id):
            if await self.store.is_temp_channel(guild, left.id):
                await self._revoke_guest(left, member)
                self._schedule_empty_check(left)
            else:
                # Warteraum verlassen → offene Anfrage verwerfen
                main_id2, _ = await self.store.find_main_by_waiting(guild, left.id)
                if main_id2 is not None:
                    self._pending_waits.discard((main_id2, member.id))

    async def _revoke_guest(self, channel: discord.VoiceChannel,
                            member: discord.Member) -> None:
        """Entzieht einem per Anklopfen reingelassenen Gast den Zugang beim Verlassen."""
        guests = await self.store.get_guests(channel.guild, channel.id)
        if member.id in guests:
            try:
                await channel.set_permissions(member, overwrite=None,
                                              reason="TempVoice: Gast hat den Channel verlassen")
            except discord.HTTPException:
                pass
            await self.store.remove_guest(channel.guild, channel.id, member.id)

    async def _cleanup_waiting_room(self, guild: discord.Guild, main_channel_id: int) -> None:
        """Löscht den Warteraum eines Haupt-Channels (falls vorhanden) und löst die Verknüpfung."""
        wr_id = await self.store.get_waiting_room_id(guild, main_channel_id)
        if wr_id:
            wr = guild.get_channel(wr_id)
            if wr is not None:
                try:
                    await wr.delete(reason="TempVoice: Warteraum entfernt")
                except discord.HTTPException:
                    pass
            await self.store.set_waiting_room(guild, main_channel_id, None)

    async def _create_temp_channel(self, member: discord.Member,
                                   generator: discord.VoiceChannel) -> None:
        guild = member.guild
        gen_id = generator.id
        settings = await self.store.effective_settings(member, guild, gen_id)
        gen_cfg = await self.store.get_generator(guild, gen_id) or {}

        # Kategorie bestimmen: Generator-Kategorie, sonst Kategorie des Generator-Channels
        cat_id = gen_cfg.get("category_id")
        category = guild.get_channel(cat_id) if cat_id else None
        if not isinstance(category, discord.CategoryChannel):
            category = generator.category

        # Name auflösen ({user}/{count}/{game})
        count = await self.store.count_channels_for_generator(guild, gen_id) + 1
        name = C.resolve_channel_name(settings.get("name"), member, count,
                                      C.current_game(member))

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
            guild, channel.id, member.id, datetime.now(timezone.utc).isoformat(),
            generator_id=gen_id)

        # Anklopf-Button in den Text-Chat des neuen Channels posten
        try:
            knock_msg = await channel.send(embed=views.build_knock_embed(),
                                           view=views.KnockView(self))
            try:
                await knock_msg.pin(reason="TempVoice: Anklopf-Button")
            except discord.HTTPException:
                pass
        except discord.HTTPException:
            pass

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
                await self._cleanup_waiting_room(guild, channel.id)
                await self.store.remove_temp_channel(guild, channel.id)
                return
            if not current.members:
                await self._cleanup_waiting_room(guild, channel.id)
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
                await self._cleanup_waiting_room(channel.guild, channel.id)
                await self.store.remove_temp_channel(channel.guild, channel.id)
            else:
                # War es ein Warteraum? Dann nur die Verknüpfung lösen.
                main_id, _ = await self.store.find_main_by_waiting(channel.guild, channel.id)
                if main_id is not None:
                    await self.store.set_waiting_room(channel.guild, main_id, None)

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
    # ANKLOPFEN
    # ─────────────────────────────────────────────────────────────────────────

    async def handle_knock(self, interaction: discord.Interaction) -> None:
        """Ein Nicht-Besitzer bittet um Einlass in einen (gesperrten) Channel."""
        channel = interaction.channel
        guild = interaction.guild
        if guild is None or not isinstance(channel, discord.VoiceChannel) \
                or not await self.store.is_temp_channel(guild, channel.id):
            await interaction.response.send_message(
                "❌ Anklopfen ist hier nicht möglich.", ephemeral=True)
            return

        knocker = interaction.user
        owner_id = await self.store.get_owner_id(guild, channel.id)
        if knocker.id == owner_id:
            await interaction.response.send_message(
                "ℹ️ Das ist dein eigener Channel.", ephemeral=True)
            return
        if channel.permissions_for(knocker).connect:
            await interaction.response.send_message(
                "✅ Du darfst bereits beitreten – tritt dem Channel einfach bei.", ephemeral=True)
            return

        key = (channel.id, knocker.id)
        if key in self._pending_knocks:
            await interaction.response.send_message(
                "⏳ Du hast bereits angeklopft. Bitte warte auf eine Antwort.", ephemeral=True)
            return

        owner = guild.get_member(owner_id) if owner_id else None
        self._pending_knocks.add(key)
        req = views.KnockRequestView(self, owner_id, knocker, channel.id)
        owner_mention = owner.mention if owner else "Der Besitzer"
        try:
            msg = await channel.send(
                f"{owner_mention} ✋ {knocker.mention} möchte deinem gesperrten Channel beitreten.",
                view=req)
            req.message = msg
        except discord.HTTPException:
            self._pending_knocks.discard(key)
            await interaction.response.send_message(
                "❌ Konnte die Anfrage nicht senden.", ephemeral=True)
            return
        await interaction.response.send_message(
            "✋ Du hast angeklopft. Warte, bis der Besitzer dich reinlässt.", ephemeral=True)

    async def resolve_knock(self, interaction: discord.Interaction,
                            view: "views.KnockRequestView", accept: bool) -> None:
        """Besitzer lässt einen Anklopfenden rein oder lehnt ab."""
        if interaction.user.id != view.owner_id:
            await interaction.response.send_message(
                "❌ Nur der Besitzer des Channels kann das entscheiden.", ephemeral=True)
            return

        guild = interaction.guild
        knocker = view.knocker
        self._pending_knocks.discard((view.channel_id, knocker.id))
        view.stop()

        channel = guild.get_channel(view.channel_id)
        if channel is None:
            await interaction.response.edit_message(
                content="Der Channel existiert nicht mehr.", view=None)
            return

        if not accept:
            await interaction.response.edit_message(
                content=f"❌ Anfrage von {knocker.display_name} abgelehnt.", view=None)
            return

        try:
            await channel.set_permissions(knocker, view_channel=True, connect=True,
                                          reason="TempVoice: Anklopfen angenommen")
        except discord.HTTPException:
            await interaction.response.edit_message(
                content="❌ Konnte den Zugang nicht gewähren (fehlende Rechte).", view=None)
            return
        await self.store.add_guest(guild, channel.id, knocker.id)

        # Falls der Bot darf und der Gast gerade woanders verbunden ist → direkt reinziehen
        if knocker.voice and knocker.voice.channel and knocker.voice.channel.id != channel.id:
            try:
                await knocker.move_to(channel, reason="TempVoice: Anklopfen angenommen")
            except discord.HTTPException:
                pass

        await interaction.response.edit_message(
            content=f"✅ {knocker.mention} wurde reingelassen und darf jetzt beitreten.", view=None)

    # ─────────────────────────────────────────────────────────────────────────
    # WARTERAUM
    # ─────────────────────────────────────────────────────────────────────────

    async def toggle_waiting_room(self, interaction: discord.Interaction,
                                  channel: discord.VoiceChannel) -> None:
        """Erstellt oder löscht den optionalen Warteraum des eigenen Channels."""
        guild = interaction.guild
        wr_id = await self.store.get_waiting_room_id(guild, channel.id)
        if wr_id and guild.get_channel(wr_id) is not None:
            await self._cleanup_waiting_room(guild, channel.id)
            await interaction.response.send_message("🗑️ Warteraum gelöscht.", ephemeral=True)
            return

        try:
            waiting = await guild.create_voice_channel(
                C.waiting_room_name(channel.name), category=channel.category,
                reason="TempVoice: Warteraum erstellt")
        except discord.HTTPException:
            await interaction.response.send_message(
                "❌ Konnte den Warteraum nicht erstellen.", ephemeral=True)
            return
        # Direkt unter den Haupt-Channel sortieren (best effort)
        try:
            await waiting.edit(position=channel.position + 1)
        except discord.HTTPException:
            pass
        await self.store.set_waiting_room(guild, channel.id, waiting.id)

        hinweis = ""
        if not guild.me.guild_permissions.move_members:
            hinweis = ("\n⚠️ **Achtung:** Mir fehlt das Recht **Mitglieder verschieben** – "
                       "ich kann Wartende aktuell nicht reinholen. Bitte gib dem Bot dieses Recht.")
        await interaction.response.send_message(
            f"⏳ Warteraum erstellt: {waiting.mention}\n"
            f"Wer dort beitritt, wird dir hier im Channel-Chat gemeldet – du holst ihn per Button rein "
            f"(der Bot übernimmt das Verschieben).{hinweis}", ephemeral=True)

    async def _handle_waiter(self, member: discord.Member, waiting_room: discord.VoiceChannel,
                             main_id: int, owner_id: Optional[int]) -> None:
        """Ein Mitglied hat einen Warteraum betreten → Besitzer im Haupt-Chat benachrichtigen."""
        if member.id == owner_id:
            return
        guild = member.guild
        main_channel = guild.get_channel(main_id)
        if main_channel is None:
            return
        key = (main_id, member.id)
        if key in self._pending_waits:
            return
        self._pending_waits.add(key)
        owner = guild.get_member(owner_id) if owner_id else None
        owner_mention = owner.mention if owner else "Der Besitzer"
        view = views.WaitingAdmitView(self, owner_id, member, main_id, waiting_room.id)
        try:
            msg = await main_channel.send(
                f"{owner_mention} ⏳ {member.mention} wartet im Warteraum.", view=view)
            view.message = msg
        except discord.HTTPException:
            self._pending_waits.discard(key)

    async def resolve_wait(self, interaction: discord.Interaction,
                           view: "views.WaitingAdmitView", admit: bool) -> None:
        """Besitzer holt einen Wartenden rein (Bot verschiebt) oder schickt ihn weg."""
        if interaction.user.id != view.owner_id:
            await interaction.response.send_message(
                "❌ Nur der Besitzer des Channels kann das entscheiden.", ephemeral=True)
            return

        guild = interaction.guild
        waiter = view.waiter
        self._pending_waits.discard((view.main_id, waiter.id))
        view.stop()

        main_channel = guild.get_channel(view.main_id)
        if main_channel is None:
            await interaction.response.edit_message(
                content="Der Channel existiert nicht mehr.", view=None)
            return

        if not admit:
            # Aus dem Warteraum entfernen (best effort)
            waiting = guild.get_channel(view.waiting_room_id)
            if waiter.voice and waiter.voice.channel and waiting is not None \
                    and waiter.voice.channel.id == waiting.id:
                try:
                    await waiter.move_to(None, reason="TempVoice: Warteraum-Anfrage abgelehnt")
                except discord.HTTPException:
                    pass
            await interaction.response.edit_message(
                content=f"❌ {waiter.display_name} wurde weggeschickt.", view=None)
            return

        if not (waiter.voice and waiter.voice.channel):
            await interaction.response.edit_message(
                content=f"❌ {waiter.display_name} ist nicht mehr im Warteraum.", view=None)
            return
        try:
            await waiter.move_to(main_channel, reason="TempVoice: vom Besitzer reingeholt")
        except discord.Forbidden:
            await interaction.response.edit_message(
                content="❌ Mir fehlt das Recht **Mitglieder verschieben** – ich kann niemanden "
                        "reinholen. Bitte gib dem Bot dieses Recht.", view=None)
            return
        except discord.HTTPException:
            await interaction.response.edit_message(
                content="❌ Konnte den Gast nicht reinholen.", view=None)
            return
        await interaction.response.edit_message(
            content=f"✅ {waiter.mention} wurde reingeholt.", view=None)

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
        if not guild.me.guild_permissions.manage_channels:
            await ctx.send("❌ Mir fehlt die Berechtigung **Kanäle verwalten**.")
            return

        category = await guild.create_category(
            C.SETUP_CATEGORY_NAME, reason="TempVoice: Setup")
        generator = await guild.create_voice_channel(
            C.SETUP_GENERATOR_NAME, category=category, reason="TempVoice: Setup")
        interface = await guild.create_text_channel(
            C.SETUP_INTERFACE_NAME, category=category, reason="TempVoice: Setup")

        await self.store.add_generator(guild, generator.id, category_id=category.id,
                                       name=C.DEFAULT_NAME_TEMPLATE)

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
            f"Weitere Generatoren (z. B. pro Spiel) legst du mit `/tempvoice generator-add` an. 🔊")

    @tempvoice.command(name="generator-add",
                       description="Macht einen Voice-Channel zum Generator mit eigenen Einstellungen")
    @commands.has_permissions(manage_guild=True)
    @app_commands.describe(
        channel="Der Voice-Channel, der als „Channel erstellen\" dient",
        kategorie="Kategorie für die erstellten Channel (leer = Kategorie des Generators)",
        name="Namensvorlage: {user} {count} {game} (z. B. „{user} – Aion\")",
        limit="Standard-Mitgliederlimit (0 = unbegrenzt)",
        bitrate="Standard-Bitrate in kbps",
        gesperrt="Neue Channel standardmäßig gesperrt?",
        verborgen="Neue Channel standardmäßig verborgen?",
    )
    async def tv_generator_add(self, ctx: commands.Context, channel: discord.VoiceChannel,
                               kategorie: Optional[discord.CategoryChannel] = None,
                               name: Optional[str] = None, limit: Optional[int] = None,
                               bitrate: Optional[int] = None, gesperrt: Optional[bool] = None,
                               verborgen: Optional[bool] = None) -> None:
        if await self.store.is_generator(ctx.guild, channel.id):
            await ctx.send(f"ℹ️ {channel.mention} ist bereits ein Generator. "
                           f"Nutze `/tempvoice generator-edit` zum Ändern.")
            return
        settings = self._gen_settings(ctx.guild, kategorie, name, limit, bitrate,
                                      gesperrt, verborgen)
        await self.store.add_generator(ctx.guild, channel.id, **settings)
        await ctx.send(f"✅ {channel.mention} ist jetzt ein **Generator**.")

    @tempvoice.command(name="generator-edit",
                       description="Ändert die Einstellungen eines Generators")
    @commands.has_permissions(manage_guild=True)
    @app_commands.describe(
        channel="Der Generator-Channel",
        kategorie="Kategorie für die erstellten Channel",
        name="Namensvorlage: {user} {count} {game}",
        limit="Standard-Mitgliederlimit (0 = unbegrenzt)",
        bitrate="Standard-Bitrate in kbps",
        gesperrt="Neue Channel standardmäßig gesperrt?",
        verborgen="Neue Channel standardmäßig verborgen?",
    )
    async def tv_generator_edit(self, ctx: commands.Context, channel: discord.VoiceChannel,
                                kategorie: Optional[discord.CategoryChannel] = None,
                                name: Optional[str] = None, limit: Optional[int] = None,
                                bitrate: Optional[int] = None, gesperrt: Optional[bool] = None,
                                verborgen: Optional[bool] = None) -> None:
        if not await self.store.is_generator(ctx.guild, channel.id):
            await ctx.send(f"❌ {channel.mention} ist kein Generator. "
                           f"Lege ihn zuerst mit `/tempvoice generator-add` an.")
            return
        changes = self._gen_settings(ctx.guild, kategorie, name, limit, bitrate,
                                     gesperrt, verborgen)
        if not changes:
            await ctx.send("ℹ️ Keine Werte angegeben.")
            return
        await self.store.update_generator(ctx.guild, channel.id, **changes)
        await ctx.send(f"✅ Einstellungen von {channel.mention} aktualisiert.")

    @tempvoice.command(name="generator-remove", description="Entfernt einen Generator")
    @commands.has_permissions(manage_guild=True)
    @app_commands.describe(channel="Der Generator-Channel")
    async def tv_generator_remove(self, ctx: commands.Context,
                                  channel: discord.VoiceChannel) -> None:
        if not await self.store.is_generator(ctx.guild, channel.id):
            await ctx.send(f"ℹ️ {channel.mention} ist kein Generator.")
            return
        await self.store.remove_generator(ctx.guild, channel.id)
        await ctx.send(f"✅ {channel.mention} ist **kein** Generator mehr "
                       f"(der Voice-Channel selbst bleibt bestehen).")

    @tempvoice.command(name="generator-list", description="Listet alle Generatoren mit Einstellungen")
    @commands.has_permissions(manage_guild=True)
    async def tv_generator_list(self, ctx: commands.Context) -> None:
        gens = await self.store.all_generators(ctx.guild)
        if not gens:
            await ctx.send("ℹ️ Es gibt noch keine Generatoren. Lege welche mit "
                           "`/tempvoice setup` oder `/tempvoice generator-add` an.")
            return
        embed = discord.Embed(title="➕ TempVoice-Generatoren", color=C.COLOR_INFO)
        for gid, cfg in gens.items():
            chan = ctx.guild.get_channel(int(gid))
            cat = ctx.guild.get_channel(cfg.get("category_id")) if cfg.get("category_id") else None
            limit_txt = "unbegrenzt" if not cfg.get("limit") else str(cfg["limit"])
            embed.add_field(
                name=(chan.name if chan else f"Gelöscht ({gid})"),
                value=(f"Kategorie: {cat.name if cat else '_(des Generators)_'}\n"
                       f"Name: `{cfg.get('name')}`\n"
                       f"Limit: {limit_txt} · Bitrate: {(cfg.get('bitrate') or 0)//1000} kbps\n"
                       f"Gesperrt: {'ja' if cfg.get('locked') else 'nein'} · "
                       f"Verborgen: {'ja' if cfg.get('hidden') else 'nein'}"),
                inline=False)
        await ctx.send(embed=embed)

    def _gen_settings(self, guild, kategorie, name, limit, bitrate, gesperrt, verborgen) -> dict:
        """Baut ein Settings-Dict aus den (optionalen) Command-Optionen (None = unverändert)."""
        changes: Dict[str, object] = {}
        if kategorie is not None:
            changes["category_id"] = kategorie.id
        if name is not None:
            changes["name"] = name.strip()[:C.MAX_CHANNEL_NAME]
        if limit is not None:
            changes["limit"] = max(0, min(limit, C.MAX_USER_LIMIT))
        if bitrate is not None:
            changes["bitrate"] = max(C.MIN_BITRATE, min(bitrate * 1000, int(guild.bitrate_limit)))
        if gesperrt is not None:
            changes["locked"] = gesperrt
        if verborgen is not None:
            changes["hidden"] = verborgen
        return changes

    @tempvoice.command(name="interface",
                       description="Postet das Steuer-Panel oder aktualisiert das bestehende")
    @commands.has_permissions(manage_guild=True)
    @app_commands.describe(kanal="Zielkanal (leer = bestehendes Panel aktualisieren bzw. aktueller Kanal)")
    async def tv_interface(self, ctx: commands.Context,
                           kanal: Optional[discord.TextChannel] = None) -> None:
        guild = ctx.guild
        if_chan_id, if_msg_id = await self.store.get_interface(guild)

        # Bestehendes Panel an Ort und Stelle aktualisieren, sofern kein anderer Zielkanal gewünscht ist
        if if_chan_id and if_msg_id and (kanal is None or kanal.id == if_chan_id):
            chan = guild.get_channel(if_chan_id)
            if chan is not None:
                try:
                    msg = await chan.fetch_message(if_msg_id)
                    await msg.edit(embed=views.build_interface_embed(guild),
                                   view=views.TempVoicePanel(self))
                    await ctx.send(f"✅ Steuer-Panel in {chan.mention} aktualisiert.")
                    return
                except discord.NotFound:
                    pass  # Nachricht wurde gelöscht → neu posten
                except discord.HTTPException:
                    pass

        # Neu posten
        target = kanal or ctx.channel
        msg = await target.send(
            embed=views.build_interface_embed(guild), view=views.TempVoicePanel(self))
        try:
            await msg.pin(reason="TempVoice: Steuer-Panel")
        except discord.HTTPException:
            pass
        await self.store.set_interface(guild, target.id, msg.id)
        await ctx.send(f"✅ Steuer-Panel wurde in {target.mention} gepostet.")

    @tempvoice.command(name="info", description="Zeigt die aktuelle TempVoice-Konfiguration")
    @commands.has_permissions(manage_guild=True)
    async def tv_info(self, ctx: commands.Context) -> None:
        guild = ctx.guild
        gen_ids = await self.store.get_generator_ids(guild)
        if_chan_id, _ = await self.store.get_interface(guild)
        temps = await self.store.all_temp_channels(guild)

        generators = ", ".join(c.mention for c in
                               (guild.get_channel(i) for i in gen_ids) if c) or "_(keine)_"
        interface = guild.get_channel(if_chan_id) if if_chan_id else None

        embed = discord.Embed(title="⚙️ TempVoice-Konfiguration", color=C.COLOR_INFO)
        embed.add_field(name=f"Generatoren ({len(gen_ids)})", value=generators, inline=False)
        embed.add_field(name="Steuer-Panel",
                        value=interface.mention if interface else "_(nicht gesetzt)_", inline=False)
        embed.add_field(name="Aktive temporäre Channel", value=str(len(temps)), inline=False)
        embed.set_footer(text="Einstellungen je Generator: /tempvoice generator-list")
        await ctx.send(embed=embed)

    # ─────────────────────────────────────────────────────────────────────────
    # BEFEHLE – MITGLIEDER
    # ─────────────────────────────────────────────────────────────────────────

    @tempvoice.command(name="profil",
                       description="Zeigt deine gespeicherten Einstellungen je Generator")
    async def tv_profil(self, ctx: commands.Context) -> None:
        profiles = await self.store.all_profiles(ctx.author)
        if not profiles:
            await ctx.send("ℹ️ Du hast noch keine gespeicherten Einstellungen. Sie entstehen "
                           "automatisch, sobald du einen Channel erstellst und anpasst.",
                           ephemeral=True)
            return
        embed = discord.Embed(title="🔊 Deine TempVoice-Einstellungen", color=C.COLOR_INFO)
        for gid, prof in profiles.items():
            chan = ctx.guild.get_channel(int(gid)) if gid != "0" else None
            title = chan.name if chan else (f"Generator {gid}" if gid != "0" else "Allgemein")
            parts = []
            if "name" in prof:
                parts.append(f"Name: `{prof['name']}`")
            if "limit" in prof:
                parts.append(f"Limit: {'unbegrenzt' if not prof['limit'] else prof['limit']}")
            if "bitrate" in prof:
                parts.append(f"Bitrate: {prof['bitrate']//1000} kbps")
            if "region" in prof:
                parts.append(f"Region: {C.REGION_NAMES.get(prof['region'], prof['region'] or 'Automatisch')}")
            if prof.get("locked"):
                parts.append("🔒 gesperrt")
            if prof.get("hidden"):
                parts.append("👁️ verborgen")
            if prof.get("reject"):
                parts.append(f"🚫 {len(prof['reject'])} gesperrt")
            embed.add_field(name=title, value="\n".join(parts) or "_(Standard)_", inline=False)
        await ctx.send(embed=embed, ephemeral=True)

    @tempvoice.command(name="reset",
                       description="Setzt deine gespeicherten Einstellungen (alle Generatoren) zurück")
    async def tv_reset(self, ctx: commands.Context) -> None:
        await self.store.clear_profiles(ctx.author)
        await ctx.send("✅ Deine gespeicherten Einstellungen wurden zurückgesetzt. Neue Channel "
                       "starten wieder mit den Generator-Standardwerten.", ephemeral=True)

    @tempvoice.command(name="gesperrte", description="Zeigt deine Sperrliste und entsperrt Mitglieder")
    async def tv_gesperrte(self, ctx: commands.Context) -> None:
        profiles = await self.store.all_profiles(ctx.author)
        reject = sorted({uid for prof in profiles.values() for uid in prof.get("reject", [])})
        if not reject:
            await ctx.send("ℹ️ Deine Sperrliste ist leer.", ephemeral=True)
            return
        view = _UnblockView(self, ctx.author, reject)
        await ctx.send("🚫 **Deine Sperrliste** (über alle Generatoren) – wähle Mitglieder zum "
                       "Entsperren:", view=view, ephemeral=True)

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
        # Aus den Sperrlisten ALLER Generator-Profile entfernen
        profiles = await self.cog.store.all_profiles(self.owner)
        for gid, prof in profiles.items():
            reject = prof.get("reject", [])
            new_reject = [i for i in reject if i not in to_unblock]
            if new_reject != reject:
                gen_id = None if gid == "0" else int(gid)
                await self.cog.store.update_profile(self.owner, gen_id, reject=new_reject)

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
