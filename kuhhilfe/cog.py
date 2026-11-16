"""
cog.py – KuhHilfe: Support-/Ticket-System mit Kategorie-Routing

Ablauf:
  * Ein Panel-Embed mit persistentem Button steht in einem Support-Channel.
  * Klick → ephemerer Wizard (Kategorie wählen → kategorie-eigenes Modal).
  * Ergebnis → privater Channel (Name = verschleierte Ticket-ID), in dem nur der
    Ersteller und die der Kategorie zugewiesenen Rollen/User Zugriff haben; die
    Zuständigen werden gepingt.
  * Schließen: Support schließt direkt; der Ersteller kann nur die Schließung
    anfragen – effektiv geschlossen/gelöscht wird erst nach Support-Bestätigung.
  * Beim Schließen optional Transcript ins Archiv; der Channel wird nach einer
    konfigurierbaren Verzögerung (Default 5 Min) automatisch gelöscht.

Persistente Buttons tragen statische custom_ids und werden zentral über
on_interaction geroutet (übersteht Neustarts; keine View-Registrierung nötig).
"""

import asyncio
import logging
import time
from typing import Any, Dict, List, Optional

import discord
from discord import app_commands
from redbot.core import Config, commands
from redbot.core.bot import Red

from . import views, tickets
from .storage import KuhHilfeStore
from .wizard import CategorySelectView, TicketModal
from .constants import (
    PANEL_OPEN, TICKET_CLAIM, TICKET_CLOSE, TICKET_CONFIRM,
    COLOR_INFO, COLOR_ERROR, COLOR_CLOSED,
    STATUS_OPEN, STATUS_CLOSE_REQUESTED, STATUS_CLOSED,
    DEFAULT_MAX_OPEN, DEFAULT_DELETE_DELAY,
    FIELD_STYLE_LABELS, MAX_FIELDS,
)

log = logging.getLogger("red.kuhhilfe")

_DELETE_LOOP_INTERVAL = 30  # Sekunden zwischen den Lösch-Durchläufen


class KuhHilfe(commands.Cog):
    """🐄 KuhHilfe – Support-Tickets mit Kategorie-Routing an die richtigen Ansprechpartner."""

    def __init__(self, bot: Red):
        self.bot = bot
        self.config = Config.get_conf(self, identifier=0xC0FFEE_4B48, force_registration=True)
        self.config.register_guild(
            panel_channel_id=None,
            panel_message_id=None,
            ticket_category_id=None,
            transcript_channel_id=None,
            max_open_per_user=DEFAULT_MAX_OPEN,
            delete_delay_seconds=DEFAULT_DELETE_DELAY,
            categories={},
            tickets={},
        )
        self.store = KuhHilfeStore(self.config)
        self._delete_task: Optional[asyncio.Task] = None

    async def cog_load(self) -> None:
        # Autocomplete für die Kategorie-Key-Parameter registrieren
        try:
            for cmd in (self.cat_remove, self.cat_rolle_add, self.cat_rolle_remove,
                        self.cat_user_add, self.cat_user_remove,
                        self.feld_add, self.feld_remove, self.feld_liste):
                cmd.autocomplete("key")(self._ac_cat)
        except Exception:  # pragma: no cover – Komfort
            log.debug("Autocomplete konnte nicht registriert werden", exc_info=True)
        self._delete_task = self.bot.loop.create_task(self._delete_loop())

    def cog_unload(self) -> None:
        if self._delete_task:
            self._delete_task.cancel()

    # ─────────────────────────────────────────────────────────────────────────
    # HILFSFUNKTIONEN
    # ─────────────────────────────────────────────────────────────────────────
    async def _reply(self, ctx: commands.Context, content: str = "", *,
                     embed: Optional[discord.Embed] = None) -> None:
        kwargs: Dict[str, Any] = {}
        if content:
            kwargs["content"] = content
        if embed is not None:
            kwargs["embed"] = embed
        if ctx.interaction:
            kwargs["ephemeral"] = True
            if ctx.interaction.response.is_done():
                await ctx.interaction.followup.send(**kwargs)
            else:
                await ctx.interaction.response.send_message(**kwargs)
        else:
            await ctx.send(**kwargs)

    async def _ac_cat(self, interaction: discord.Interaction,
                      current: str) -> List[app_commands.Choice]:
        out: List[app_commands.Choice] = []
        if interaction.guild is None:
            return out
        cats = await self.store.list_categories(interaction.guild)
        cur = current.lower()
        for key, cat in cats.items():
            if cur in key.lower() or cur in cat.get("name", "").lower():
                out.append(app_commands.Choice(name=f"{cat.get('name', key)} ({key})", value=key))
            if len(out) >= 25:
                break
        return out

    @staticmethod
    def is_support_for(member: discord.Member, category: Dict[str, Any]) -> bool:
        """Darf dieses Mitglied das Ticket der Kategorie verwalten?"""
        if member.guild_permissions.manage_guild:
            return True
        role_ids = set(category.get("role_ids", []))
        if role_ids and any(r.id in role_ids for r in member.roles):
            return True
        return member.id in category.get("user_ids", [])

    # ═════════════════════════════════════════════════════════════════════════
    # INTERACTION-ROUTER (persistente Buttons)
    # ═════════════════════════════════════════════════════════════════════════
    @commands.Cog.listener("on_interaction")
    async def on_interaction(self, interaction: discord.Interaction) -> None:
        if interaction.type != discord.InteractionType.component:
            return
        custom_id = (interaction.data or {}).get("custom_id", "")
        if interaction.guild is None:
            return
        try:
            if custom_id == PANEL_OPEN:
                await self._start_wizard(interaction)
            elif custom_id == TICKET_CLAIM:
                await self._handle_claim(interaction)
            elif custom_id == TICKET_CLOSE:
                await self._handle_close(interaction)
            elif custom_id == TICKET_CONFIRM:
                await self._handle_confirm_close(interaction)
        except discord.DiscordException:
            log.exception("Fehler bei KuhHilfe-Interaktion (%s)", custom_id)

    # ─────────────────────────────────────────────────────────────────────────
    # WIZARD
    # ─────────────────────────────────────────────────────────────────────────
    async def _start_wizard(self, interaction: discord.Interaction) -> None:
        guild = interaction.guild
        cats = await self.store.list_categories(guild)
        if not cats:
            await interaction.response.send_message(
                embed=discord.Embed(
                    description="⚠️ Der Support ist noch nicht eingerichtet. Bitte wende dich an das Team.",
                    color=COLOR_ERROR),
                ephemeral=True)
            return

        if not await self._within_limit(interaction):
            return

        if len(cats) == 1:
            category = next(iter(cats.values()))
            await interaction.response.send_modal(TicketModal(self, category))
        else:
            view = CategorySelectView(self, cats, interaction.user.id)
            await interaction.response.send_message(
                embed=discord.Embed(
                    description="Wähle die passende Kategorie für dein Anliegen:",
                    color=COLOR_INFO),
                view=view, ephemeral=True)

    async def _within_limit(self, interaction: discord.Interaction) -> bool:
        settings = await self.store.get_settings(interaction.guild)
        limit = settings.get("max_open_per_user", DEFAULT_MAX_OPEN) or 0
        if limit <= 0:
            return True
        open_count = await self.store.count_open_for_user(interaction.guild, interaction.user.id)
        if open_count >= limit:
            await interaction.response.send_message(
                embed=discord.Embed(
                    description=(f"⚠️ Du hast bereits **{open_count}** offene(s) Ticket(s). "
                                 f"Bitte schließe zuerst ein bestehendes (max. {limit})."),
                    color=COLOR_ERROR),
                ephemeral=True)
            return False
        return True

    async def finalize_ticket(self, interaction: discord.Interaction,
                              category: Dict[str, Any], answers: Dict[str, str]) -> None:
        """Wird vom Modal aufgerufen: erstellt Channel + Ticket + pingt Zuständige."""
        guild = interaction.guild
        opener = interaction.user

        # Limit erneut prüfen (Zeit zwischen Panel-Klick und Absenden)
        settings = await self.store.get_settings(guild)
        limit = settings.get("max_open_per_user", DEFAULT_MAX_OPEN) or 0
        if limit > 0 and await self.store.count_open_for_user(guild, opener.id) >= limit:
            await interaction.response.send_message(
                "⚠️ Du hast bereits zu viele offene Tickets.", ephemeral=True)
            return

        await interaction.response.defer(ephemeral=True, thinking=True)

        ticket_id = await self.store.generate_ticket_id(guild)
        try:
            channel = await tickets.create_ticket_channel(
                guild, opener, category, ticket_id, settings.get("ticket_category_id"))
        except discord.Forbidden:
            await interaction.followup.send(
                "❌ Mir fehlen die Rechte, einen Ticket-Channel zu erstellen "
                "(**Kanäle verwalten**). Bitte melde das dem Team.", ephemeral=True)
            return
        except discord.HTTPException:
            log.exception("Ticket-Channel konnte nicht erstellt werden")
            await interaction.followup.send(
                "❌ Der Ticket-Channel konnte nicht erstellt werden.", ephemeral=True)
            return

        ticket = await self.store.create_ticket(
            guild, ticket_id, channel.id, opener.id, category["key"], answers)

        embed = views.build_ticket_embed(ticket, category, opener, guild)
        content = views.build_ping_content(category, opener)
        await channel.send(
            content=content,
            embed=embed,
            view=views.build_ticket_controls(STATUS_OPEN),
            allowed_mentions=discord.AllowedMentions(roles=True, users=True, everyone=False),
        )

        await interaction.followup.send(
            embed=discord.Embed(
                description=f"✅ Dein Ticket wurde erstellt: {channel.mention}\n"
                            f"Ticket-ID: `{ticket_id}`",
                color=COLOR_INFO),
            ephemeral=True)

    # ─────────────────────────────────────────────────────────────────────────
    # TICKET-AKTIONEN
    # ─────────────────────────────────────────────────────────────────────────
    async def _load_ticket_ctx(self, interaction: discord.Interaction):
        """Lädt (ticket, category) zum Channel der Interaktion oder meldet Fehler."""
        ticket = await self.store.get_ticket(interaction.guild, interaction.channel_id)
        if not ticket:
            await interaction.response.send_message(
                "❌ Zu diesem Channel gibt es kein aktives Ticket.", ephemeral=True)
            return None, None
        category = await self.store.get_category(interaction.guild, ticket["category_key"])
        if not category:
            # Kategorie gelöscht – minimaler Ersatz, damit Schließen weiter funktioniert
            category = {"key": ticket["category_key"], "name": ticket["category_key"],
                        "role_ids": [], "user_ids": [], "fields": []}
        return ticket, category

    async def _handle_claim(self, interaction: discord.Interaction) -> None:
        ticket, category = await self._load_ticket_ctx(interaction)
        if not ticket:
            return
        if not self.is_support_for(interaction.user, category):
            await interaction.response.send_message(
                "❌ Nur zuständige Support-Mitglieder können ein Ticket übernehmen.", ephemeral=True)
            return
        if ticket.get("claimed_by"):
            await interaction.response.send_message(
                f"ℹ️ Dieses Ticket wurde bereits von <@{ticket['claimed_by']}> übernommen.",
                ephemeral=True)
            return

        ticket = await self.store.update_ticket(
            interaction.guild, interaction.channel_id, claimed_by=interaction.user.id)
        opener = interaction.guild.get_member(ticket["opener_id"])
        embed = views.build_ticket_embed(ticket, category, opener, interaction.guild)
        await interaction.response.edit_message(
            embed=embed, view=views.build_ticket_controls(ticket["status"]))
        await interaction.channel.send(f"🙋 {interaction.user.mention} kümmert sich jetzt um dieses Ticket.")

    async def _handle_close(self, interaction: discord.Interaction) -> None:
        ticket, category = await self._load_ticket_ctx(interaction)
        if not ticket:
            return
        if ticket["status"] == STATUS_CLOSED:
            await interaction.response.send_message("ℹ️ Dieses Ticket ist bereits geschlossen.", ephemeral=True)
            return

        if self.is_support_for(interaction.user, category):
            await self._effective_close(interaction, ticket, category)
            return

        # Nicht-Support (z. B. Ersteller): nur Schließung anfragen
        ticket = await self.store.update_ticket(
            interaction.guild, interaction.channel_id, status=STATUS_CLOSE_REQUESTED)
        opener = interaction.guild.get_member(ticket["opener_id"])
        embed = views.build_ticket_embed(ticket, category, opener, interaction.guild)
        await interaction.response.edit_message(
            embed=embed, view=views.build_ticket_controls(STATUS_CLOSE_REQUESTED))
        ping = views.build_ping_content(category, interaction.user)
        await interaction.channel.send(
            content=ping,
            embed=discord.Embed(
                description=(f"🔒 {interaction.user.mention} möchte dieses Ticket schließen.\n"
                             "Ein Support-Mitglied muss die Schließung über **Schließen bestätigen** freigeben."),
                color=COLOR_INFO),
            allowed_mentions=discord.AllowedMentions(roles=True, users=True, everyone=False),
        )

    async def _handle_confirm_close(self, interaction: discord.Interaction) -> None:
        ticket, category = await self._load_ticket_ctx(interaction)
        if not ticket:
            return
        if not self.is_support_for(interaction.user, category):
            await interaction.response.send_message(
                "❌ Nur zuständige Support-Mitglieder können die Schließung bestätigen.", ephemeral=True)
            return
        await self._effective_close(interaction, ticket, category)

    async def _effective_close(self, interaction: discord.Interaction,
                               ticket: Dict[str, Any], category: Dict[str, Any]) -> None:
        guild = interaction.guild
        channel = interaction.channel

        ticket = await self.store.update_ticket(
            guild, channel.id, status=STATUS_CLOSED,
            closed_at=int(time.time()), closed_by=interaction.user.id)

        opener = guild.get_member(ticket["opener_id"])
        embed = views.build_ticket_embed(ticket, category, opener, guild)
        await interaction.response.edit_message(
            embed=embed, view=views.build_ticket_controls(STATUS_CLOSED))

        # Transcript ins Archiv (optional)
        settings = await self.store.get_settings(guild)
        transcript_channel_id = settings.get("transcript_channel_id")
        if transcript_channel_id:
            archive = guild.get_channel(transcript_channel_id)
            if isinstance(archive, (discord.TextChannel, discord.Thread)):
                try:
                    transcript = await tickets.generate_transcript(channel, ticket)
                    summary = discord.Embed(
                        title=f"📄 Transcript · Ticket {ticket['id']}",
                        color=COLOR_CLOSED, timestamp=discord.utils.utcnow())
                    summary.add_field(name="Kategorie", value=category.get("name", ticket["category_key"]), inline=True)
                    summary.add_field(
                        name="Ersteller",
                        value=(opener.mention if opener else f"<@{ticket['opener_id']}>"), inline=True)
                    summary.add_field(name="Geschlossen von", value=interaction.user.mention, inline=True)
                    await archive.send(embed=summary, file=transcript)
                except discord.DiscordException:
                    log.exception("Transcript konnte nicht gespeichert werden")

        delay = settings.get("delete_delay_seconds", DEFAULT_DELETE_DELAY) or 0
        if delay <= 0:
            await self._delete_ticket_channel(guild, channel, ticket)
        else:
            minutes = max(1, round(delay / 60))
            await channel.send(embed=discord.Embed(
                description=(f"✅ Ticket von {interaction.user.mention} geschlossen. "
                             f"Dieser Channel wird in **{minutes} Min** automatisch gelöscht."),
                color=COLOR_CLOSED))

    async def _delete_ticket_channel(self, guild: discord.Guild,
                                     channel: Optional[discord.abc.GuildChannel],
                                     ticket: Dict[str, Any]) -> None:
        await self.store.delete_ticket(guild, ticket["channel_id"])
        if channel is not None:
            try:
                await channel.delete(reason=f"KuhHilfe-Ticket {ticket['id']} geschlossen")
            except discord.DiscordException:
                log.debug("Ticket-Channel %s konnte nicht gelöscht werden", ticket["channel_id"])

    # ─────────────────────────────────────────────────────────────────────────
    # LÖSCH-SCHEDULER (löscht geschlossene Tickets nach Ablauf der Verzögerung)
    # ─────────────────────────────────────────────────────────────────────────
    async def _delete_loop(self) -> None:
        await self.bot.wait_until_red_ready()
        while True:
            try:
                await self._sweep_closed()
            except asyncio.CancelledError:
                raise
            except Exception:  # pragma: no cover
                log.exception("Fehler im KuhHilfe-Lösch-Loop")
            await asyncio.sleep(_DELETE_LOOP_INTERVAL)

    async def _sweep_closed(self) -> None:
        now = int(time.time())
        for guild in self.bot.guilds:
            settings = await self.store.get_settings(guild)
            delay = settings.get("delete_delay_seconds", DEFAULT_DELETE_DELAY) or 0
            tickets_map = await self.store.all_tickets(guild)
            for ticket in list(tickets_map.values()):
                if ticket.get("status") != STATUS_CLOSED:
                    continue
                closed_at = ticket.get("closed_at") or 0
                if now - closed_at < delay:
                    continue
                channel = guild.get_channel(ticket["channel_id"])
                await self._delete_ticket_channel(guild, channel, ticket)

    # ═════════════════════════════════════════════════════════════════════════
    # ADMIN-BEFEHLE
    # ═════════════════════════════════════════════════════════════════════════
    @commands.hybrid_group(name="kuhhilfe", description="KuhHilfe – Support-System einrichten")
    @commands.guild_only()
    @commands.has_permissions(manage_guild=True)
    @app_commands.default_permissions(manage_guild=True)
    async def kuhhilfe(self, ctx: commands.Context) -> None:
        if ctx.invoked_subcommand is None:
            await ctx.send_help(ctx.command)

    # ── Setup ────────────────────────────────────────────────────────────────
    @kuhhilfe.command(name="panel", description="Postet das Support-Panel in einen Channel")
    @app_commands.describe(channel="Channel, in dem das Panel mit dem Button erscheinen soll")
    async def panel_cmd(self, ctx: commands.Context, channel: discord.TextChannel) -> None:
        cats = await self.store.list_categories(ctx.guild)
        embed = views.build_panel_embed(ctx.guild, cats)
        try:
            msg = await channel.send(embed=embed, view=views.build_panel_view())
        except discord.Forbidden:
            await self._reply(ctx, f"❌ Ich darf in {channel.mention} nicht schreiben.")
            return
        await self.store.set_panel(ctx.guild, channel.id, msg.id)
        await self._reply(ctx, f"✅ Support-Panel in {channel.mention} gepostet.")

    @kuhhilfe.command(name="panel-aktualisieren", description="Aktualisiert das bestehende Panel")
    async def panel_refresh(self, ctx: commands.Context) -> None:
        settings = await self.store.get_settings(ctx.guild)
        ch_id, msg_id = settings.get("panel_channel_id"), settings.get("panel_message_id")
        channel = ctx.guild.get_channel(ch_id) if ch_id else None
        if not channel or not msg_id:
            await self._reply(ctx, "❌ Es ist noch kein Panel gesetzt. Nutze `/kuhhilfe panel`.")
            return
        cats = await self.store.list_categories(ctx.guild)
        embed = views.build_panel_embed(ctx.guild, cats)
        try:
            msg = await channel.fetch_message(msg_id)
            await msg.edit(embed=embed, view=views.build_panel_view())
        except discord.NotFound:
            msg = await channel.send(embed=embed, view=views.build_panel_view())
            await self.store.set_panel(ctx.guild, channel.id, msg.id)
        await self._reply(ctx, "✅ Panel aktualisiert.")

    @kuhhilfe.command(name="ticketkategorie",
                      description="Discord-Kategorie, unter der Ticket-Channels erstellt werden")
    @app_commands.describe(kategorie="Channel-Kategorie für neue Ticket-Channels")
    async def set_ticket_category(self, ctx: commands.Context,
                                  kategorie: discord.CategoryChannel) -> None:
        await self.store.set_ticket_category(ctx.guild, kategorie.id)
        await self._reply(ctx, f"✅ Neue Tickets werden unter **{kategorie.name}** erstellt.")

    @kuhhilfe.command(name="archiv",
                      description="Channel für Transcripts beim Schließen (ohne Angabe: deaktivieren)")
    @app_commands.describe(channel="Archiv-Channel für Transcripts (leer = aus)")
    async def set_archive(self, ctx: commands.Context,
                          channel: Optional[discord.TextChannel] = None) -> None:
        await self.store.set_transcript_channel(ctx.guild, channel.id if channel else None)
        if channel:
            await self._reply(ctx, f"✅ Transcripts werden beim Schließen in {channel.mention} gespeichert.")
        else:
            await self._reply(ctx, "✅ Transcript-Archiv deaktiviert.")

    @kuhhilfe.command(name="limit",
                      description="Max. gleichzeitig offene Tickets pro Nutzer (0 = unbegrenzt)")
    @app_commands.describe(anzahl="Anzahl (0 = unbegrenzt)")
    async def set_limit(self, ctx: commands.Context, anzahl: int) -> None:
        await self.store.set_max_open(ctx.guild, anzahl)
        txt = "unbegrenzt" if anzahl <= 0 else str(anzahl)
        await self._reply(ctx, f"✅ Offene Tickets pro Nutzer: **{txt}**.")

    @kuhhilfe.command(name="loeschzeit",
                      description="Minuten bis zum Löschen eines geschlossenen Channels (Default 5)")
    @app_commands.describe(minuten="Minuten bis zum Löschen (0 = sofort)")
    async def set_delete_delay(self, ctx: commands.Context, minuten: int) -> None:
        await self.store.set_delete_delay(ctx.guild, max(0, minuten) * 60)
        txt = "sofort" if minuten <= 0 else f"{minuten} Min"
        await self._reply(ctx, f"✅ Geschlossene Channels werden **{txt}** nach dem Schließen gelöscht.")

    @kuhhilfe.command(name="status", description="Zeigt die aktuelle KuhHilfe-Konfiguration")
    async def status_cmd(self, ctx: commands.Context) -> None:
        s = await self.store.get_settings(ctx.guild)
        cats = await self.store.list_categories(ctx.guild)

        def _ch(cid):
            ch = ctx.guild.get_channel(cid) if cid else None
            return ch.mention if ch else "_(nicht gesetzt)_"

        embed = discord.Embed(title="⚙️ KuhHilfe – Einstellungen", color=COLOR_INFO)
        embed.add_field(name="Panel-Channel", value=_ch(s.get("panel_channel_id")), inline=True)
        embed.add_field(name="Ticket-Kategorie", value=_ch(s.get("ticket_category_id")), inline=True)
        embed.add_field(name="Archiv", value=_ch(s.get("transcript_channel_id")), inline=True)
        limit = s.get("max_open_per_user", DEFAULT_MAX_OPEN)
        embed.add_field(name="Limit/Nutzer", value=("unbegrenzt" if not limit else str(limit)), inline=True)
        embed.add_field(name="Löschzeit",
                        value=f"{round((s.get('delete_delay_seconds') or 0) / 60)} Min", inline=True)
        embed.add_field(name="Kategorien", value=str(len(cats)), inline=True)
        if cats:
            lines = []
            for cat in cats.values():
                roles = len(cat.get("role_ids", []))
                users = len(cat.get("user_ids", []))
                fields = len(cat.get("fields", []))
                lines.append(f"`{cat['key']}` · {cat['name']} – {roles} Rolle(n), "
                             f"{users} User, {fields} Feld(er)")
            embed.add_field(name="Details", value="\n".join(lines)[:1024], inline=False)
        await self._reply(ctx, embed=embed)

    # ── Kategorien ─────────────────────────────────────────────────────────────
    @kuhhilfe.command(name="kategorie-add", description="Legt eine neue Support-Kategorie an")
    @app_commands.describe(name="Name der Kategorie", emoji="Optionales Emoji",
                           beschreibung="Optionale Kurzbeschreibung")
    async def cat_add(self, ctx: commands.Context, name: str,
                      emoji: Optional[str] = None, beschreibung: Optional[str] = None) -> None:
        cat = await self.store.add_category(ctx.guild, name, emoji, beschreibung)
        if not cat:
            await self._reply(ctx, "❌ Name ungültig oder eine Kategorie mit diesem Key existiert bereits.")
            return
        await self._reply(
            ctx,
            f"✅ Kategorie **{cat['name']}** (`{cat['key']}`) angelegt – mit den Standard-Feldern "
            "*Betreff* & *Beschreibung*.\nWeise nun Rollen/User zu (`/kuhhilfe rolle-add`) und "
            "passe die Felder an (`/kuhhilfe feld-add`).")

    @kuhhilfe.command(name="kategorie-remove", description="Löscht eine Support-Kategorie")
    @app_commands.describe(key="Key der Kategorie")
    async def cat_remove(self, ctx: commands.Context, key: str) -> None:
        ok = await self.store.remove_category(ctx.guild, key)
        await self._reply(ctx, f"✅ Kategorie `{key}` gelöscht." if ok
                          else f"❌ Keine Kategorie mit Key `{key}`.")

    @kuhhilfe.command(name="kategorie-liste", description="Listet alle Kategorien")
    async def cat_list(self, ctx: commands.Context) -> None:
        cats = await self.store.list_categories(ctx.guild)
        if not cats:
            await self._reply(ctx, "ℹ️ Es sind noch keine Kategorien angelegt.")
            return
        embed = discord.Embed(title="📂 Support-Kategorien", color=COLOR_INFO)
        for cat in cats.values():
            roles = " ".join(f"<@&{r}>" for r in cat.get("role_ids", [])) or "_keine_"
            users = " ".join(f"<@{u}>" for u in cat.get("user_ids", [])) or "_keine_"
            fields = ", ".join(f["label"] for f in cat.get("fields", [])) or "_keine_"
            emoji = (cat.get("emoji") + " ") if cat.get("emoji") else ""
            embed.add_field(
                name=f"{emoji}{cat['name']}  ·  `{cat['key']}`",
                value=f"Rollen: {roles}\nUser: {users}\nFelder: {fields}",
                inline=False)
        await self._reply(ctx, embed=embed)

    # ── Rollen / User zuweisen ─────────────────────────────────────────────────
    @kuhhilfe.command(name="rolle-add", description="Weist einer Kategorie eine zuständige Rolle zu")
    @app_commands.describe(key="Key der Kategorie", rolle="Zuständige Rolle")
    async def cat_rolle_add(self, ctx: commands.Context, key: str, rolle: discord.Role) -> None:
        res = await self.store.toggle_role(ctx.guild, key, rolle.id, add=True)
        if res is None:
            await self._reply(ctx, f"❌ Keine Kategorie mit Key `{key}`.")
            return
        await self._reply(ctx, f"✅ {rolle.mention} ist jetzt für `{key}` zuständig.")

    @kuhhilfe.command(name="rolle-remove", description="Entfernt eine Rolle aus einer Kategorie")
    @app_commands.describe(key="Key der Kategorie", rolle="Rolle")
    async def cat_rolle_remove(self, ctx: commands.Context, key: str, rolle: discord.Role) -> None:
        res = await self.store.toggle_role(ctx.guild, key, rolle.id, add=False)
        if res is None:
            await self._reply(ctx, f"❌ Keine Kategorie mit Key `{key}`.")
            return
        await self._reply(ctx, f"✅ {rolle.mention} ist nicht mehr für `{key}` zuständig.")

    @kuhhilfe.command(name="user-add", description="Weist einer Kategorie einen zuständigen User zu")
    @app_commands.describe(key="Key der Kategorie", user="Zuständiger User")
    async def cat_user_add(self, ctx: commands.Context, key: str, user: discord.Member) -> None:
        res = await self.store.toggle_user(ctx.guild, key, user.id, add=True)
        if res is None:
            await self._reply(ctx, f"❌ Keine Kategorie mit Key `{key}`.")
            return
        await self._reply(ctx, f"✅ {user.mention} ist jetzt für `{key}` zuständig.")

    @kuhhilfe.command(name="user-remove", description="Entfernt einen User aus einer Kategorie")
    @app_commands.describe(key="Key der Kategorie", user="User")
    async def cat_user_remove(self, ctx: commands.Context, key: str, user: discord.Member) -> None:
        res = await self.store.toggle_user(ctx.guild, key, user.id, add=False)
        if res is None:
            await self._reply(ctx, f"❌ Keine Kategorie mit Key `{key}`.")
            return
        await self._reply(ctx, f"✅ {user.mention} ist nicht mehr für `{key}` zuständig.")

    # ── Modal-Felder ───────────────────────────────────────────────────────────
    @kuhhilfe.command(name="feld-add", description="Fügt einer Kategorie ein Wizard-Feld hinzu")
    @app_commands.describe(key="Key der Kategorie", label="Beschriftung des Feldes",
                           stil="Kurz oder Lang", pflicht="Muss ausgefüllt werden?",
                           max_laenge="Maximale Zeichenzahl", platzhalter="Optionaler Platzhaltertext")
    @app_commands.choices(stil=[
        app_commands.Choice(name="Kurz (einzeilig)", value="short"),
        app_commands.Choice(name="Lang (mehrzeilig)", value="paragraph"),
    ])
    async def feld_add(self, ctx: commands.Context, key: str, label: str,
                       stil: str = "short", pflicht: bool = True,
                       max_laenge: int = 1000, platzhalter: Optional[str] = None) -> None:
        err = await self.store.add_field(ctx.guild, key, label, stil, pflicht, platzhalter, max_laenge)
        if err:
            await self._reply(ctx, f"❌ {err}")
            return
        await self._reply(ctx, f"✅ Feld **{label}** zu `{key}` hinzugefügt "
                               f"({FIELD_STYLE_LABELS.get(stil, stil)}, "
                               f"{'Pflicht' if pflicht else 'optional'}).")

    @kuhhilfe.command(name="feld-remove", description="Entfernt ein Feld einer Kategorie (Position)")
    @app_commands.describe(key="Key der Kategorie", position="Position laut /kuhhilfe feld-liste (ab 1)")
    async def feld_remove(self, ctx: commands.Context, key: str, position: int) -> None:
        removed = await self.store.remove_field(ctx.guild, key, position)
        if not removed:
            await self._reply(ctx, f"❌ Kategorie `{key}` oder Position {position} nicht gefunden.")
            return
        await self._reply(ctx, f"✅ Feld **{removed['label']}** aus `{key}` entfernt.")

    @kuhhilfe.command(name="feld-liste", description="Zeigt die Felder einer Kategorie")
    @app_commands.describe(key="Key der Kategorie")
    async def feld_liste(self, ctx: commands.Context, key: str) -> None:
        cat = await self.store.get_category(ctx.guild, key)
        if not cat:
            await self._reply(ctx, f"❌ Keine Kategorie mit Key `{key}`.")
            return
        fields = cat.get("fields", [])
        if not fields:
            await self._reply(ctx, f"ℹ️ Kategorie `{key}` hat noch keine Felder.")
            return
        lines = []
        for i, f in enumerate(fields, start=1):
            lines.append(
                f"**{i}.** {f['label']} · {FIELD_STYLE_LABELS.get(f.get('style'), f.get('style'))} · "
                f"{'Pflicht' if f.get('required') else 'optional'} · max {f.get('max_length')}")
        embed = discord.Embed(
            title=f"📝 Felder · {cat['name']}",
            description="\n".join(lines),
            color=COLOR_INFO)
        embed.set_footer(text=f"{len(fields)}/{MAX_FIELDS} Feldern belegt")
        await self._reply(ctx, embed=embed)

    # ─────────────────────────────────────────────────────────────────────────
    # FEHLER-HANDLER
    # ─────────────────────────────────────────────────────────────────────────
    async def cog_command_error(self, ctx: commands.Context, error) -> None:
        error = getattr(error, "original", error)
        if isinstance(error, commands.MissingPermissions):
            await self._reply(ctx, "❌ Du benötigst die Berechtigung **Server verwalten**.")
            return
        if isinstance(error, commands.NoPrivateMessage):
            await self._reply(ctx, "❌ Dieser Befehl funktioniert nur auf einem Server.")
            return
        if isinstance(error, commands.BadArgument):
            await self._reply(ctx, f"❌ Ungültige Eingabe: {error}")
            return
        log.exception("Fehler in KuhHilfe-Befehl", exc_info=error)
        try:
            await self._reply(ctx, "❌ Ein unerwarteter Fehler ist aufgetreten.")
        except discord.DiscordException:
            pass
