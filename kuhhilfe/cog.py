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
import io
import json
import logging
import time
from typing import Any, Dict, List, Optional

import discord
from discord import app_commands
from redbot.core import Config, commands
from redbot.core.bot import Red

from . import views, tickets, admin_ui, presets, manage, feedback
from .storage import KuhHilfeStore
from .wizard import WizardSelectView, make_modal
from .util import resolve_emoji
from .constants import (
    PANEL_OPEN, TICKET_CLAIM, TICKET_CLOSE, TICKET_CONFIRM, TICKET_REOPEN, TICKET_MANAGE,
    COLOR_INFO, COLOR_ERROR, COLOR_CLOSED, COLOR_TICKET, COLOR_PENDING, COLOR_NOTICE,
    STATUS_OPEN, STATUS_CLOSE_REQUESTED, STATUS_CLOSED, STAGE_LABELS, STAGE_IN_PROGRESS,
    DEFAULT_MAX_OPEN, DEFAULT_DELETE_DELAY, DEFAULT_CONFIRM_DELETE,
    FIELD_STYLE_LABELS, MAX_FIELDS, CATEGORY_PATH_SEP,
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
            confirm_delete_seconds=DEFAULT_CONFIRM_DELETE,
            modlog_channel_id=None,
            dashboard_channel_id=None,
            dashboard_message_id=None,
            inactivity_hours=0,
            inactivity_warn_hours=0,
            cooldown_seconds=0,
            blacklist=[],
            cooldowns={},
            dm_transcript=False,
            feedback_enabled=False,
            feedback=[],
            stats={"opened": 0, "closed": 0, "by_category": {}, "claims": {}},
            categories={},
            tickets={},
        )
        self.store = KuhHilfeStore(self.config)
        self._delete_task: Optional[asyncio.Task] = None
        self._ticket_channels: set = set()  # Channel-IDs aktiver Tickets (für on_message)

    async def cog_load(self) -> None:
        # Autocomplete für die Pfad-/Gruppen-Parameter registrieren
        try:
            for cmd in (self.cat_remove, self.cat_rolle_add, self.cat_rolle_remove,
                        self.cat_user_add, self.cat_user_remove,
                        self.feld_add, self.feld_remove, self.feld_liste):
                cmd.autocomplete("kategorie")(self._ac_leaf)
            for cmd in (self.cat_add, self.gruppe_remove):
                cmd.autocomplete("gruppe")(self._ac_group)
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

    def _autodelete(self, interaction: discord.Interaction, seconds: int,
                    message: Optional[discord.Message] = None) -> None:
        """Löscht eine ephemere Nachricht nach `seconds` Sekunden (0 = nie)."""
        if not seconds or seconds <= 0:
            return

        async def _run() -> None:
            try:
                await asyncio.sleep(seconds)
                if message is not None:
                    await message.delete()
                else:
                    await interaction.delete_original_response()
            except discord.HTTPException:
                pass  # Nachricht schon weg oder Interaktions-Token abgelaufen

        self.bot.loop.create_task(_run())

    async def _notice(self, channel: discord.abc.Messageable, description: str,
                      ping: Optional[str] = None) -> None:
        """Einheitlicher Bot-Hinweis im Ticket-Channel (Embed mit fester Farbe).

        Mit `ping` wird zusätzlich ein Benachrichtigungs-Text (Mentions) gesendet.
        """
        kwargs: Dict[str, Any] = {
            "embed": discord.Embed(description=description, color=COLOR_NOTICE)}
        if ping:
            kwargs["content"] = ping
            kwargs["allowed_mentions"] = discord.AllowedMentions(
                roles=True, users=True, everyone=False)
        await channel.send(**kwargs)

    async def _modlog(self, guild: discord.Guild, description: str,
                      color: int = COLOR_INFO) -> None:
        """Postet ein Ereignis in den konfigurierten Protokoll-Channel (falls gesetzt)."""
        settings = await self.store.get_settings(guild)
        cid = settings.get("modlog_channel_id")
        if not cid:
            return
        channel = guild.get_channel(cid)
        if not isinstance(channel, (discord.TextChannel, discord.Thread)):
            return
        try:
            await channel.send(embed=discord.Embed(
                description=description, color=color, timestamp=discord.utils.utcnow()))
        except discord.DiscordException:
            log.debug("Mod-Log konnte nicht geschrieben werden", exc_info=True)

    async def _ac_leaf(self, interaction: discord.Interaction,
                       current: str) -> List[app_commands.Choice]:
        """Autocomplete über alle finalen Kategorien (als Pfad)."""
        out: List[app_commands.Choice] = []
        if interaction.guild is None:
            return out
        cur = current.lower()
        for path, leaf, parent in await self.store.iter_leaves(interaction.guild):
            label = (f"{parent['name']} › {leaf['name']}" if parent else leaf["name"])
            if cur in path.lower() or cur in label.lower():
                out.append(app_commands.Choice(name=f"{label} ({path})"[:100], value=path))
            if len(out) >= 25:
                break
        return out

    async def _ac_group(self, interaction: discord.Interaction,
                        current: str) -> List[app_commands.Choice]:
        """Autocomplete über alle Gruppen (Oberkategorien)."""
        out: List[app_commands.Choice] = []
        if interaction.guild is None:
            return out
        cur = current.lower()
        for key, grp in await self.store.iter_groups(interaction.guild):
            if cur in key.lower() or cur in grp.get("name", "").lower():
                out.append(app_commands.Choice(name=f"{grp.get('name', key)} ({key})"[:100], value=key))
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

    async def _is_staff(self, member: discord.Member) -> bool:
        """Support (irgendeiner Kategorie zugewiesen) oder Admin?"""
        if member.guild_permissions.manage_guild:
            return True
        for _path, leaf, _parent in await self.store.iter_leaves(member.guild):
            if self.is_support_for(member, leaf):
                return True
        return False

    # ─────────────────────────────────────────────────────────────────────────
    # LIVE-DASHBOARD (selbst-aktualisierende Übersicht in einem Channel)
    # ─────────────────────────────────────────────────────────────────────────
    async def build_dashboard_embeds(self, guild: discord.Guild) -> List[discord.Embed]:
        all_t = await self.store.all_tickets(guild)
        st = await self.store.get_stats(guild)
        fb = await self.store.get_feedback(guild)

        open_items = []
        for t in all_t.values():
            if t.get("status") == STATUS_CLOSED:
                continue
            _, label = await self.store.resolve_leaf(guild, t["category_key"])
            open_items.append((t, label or t.get("category_label") or t["category_key"]))
        open_items.sort(key=lambda x: x[0].get("created_at", 0))

        # 1) Statistik
        stat = discord.Embed(title="📊 Support - Überblick", color=COLOR_INFO,
                             timestamp=discord.utils.utcnow())
        stat.add_field(name="Aktuell offen", value=str(len(open_items)), inline=True)
        stat.add_field(name="Erstellt (gesamt)", value=str(st.get("opened", 0)), inline=True)
        stat.add_field(name="Geschlossen (gesamt)", value=str(st.get("closed", 0)), inline=True)
        bc = st.get("by_category", {})
        if bc:
            rows = sorted(bc.items(), key=lambda kv: kv[1].get("opened", 0), reverse=True)[:5]
            lines = []
            for path, c in rows:
                _, label = await self.store.resolve_leaf(guild, path)
                lines.append(f"**{label or path}**: {c.get('opened', 0)}↗ · {c.get('closed', 0)}✓")
            stat.add_field(name="Top-Kategorien", value="\n".join(lines)[:1024], inline=False)
        ratings = [f.get("rating", 0) for f in fb if f.get("rating")]
        if ratings:
            avg = sum(ratings) / len(ratings)
            stat.add_field(name="Feedback", value=f"Ø **{avg:.2f}** aus {len(ratings)} Bewertungen", inline=False)
        stat.set_footer(text="Aktualisiert sich automatisch")

        # 2) Offene Tickets
        tickets_embed = discord.Embed(title="🎫 Offene Tickets", color=COLOR_TICKET)
        if not open_items:
            tickets_embed.description = "_Aktuell keine offenen Tickets._"
        else:
            lines = []
            for t, label in open_items[:25]:
                ch = guild.get_channel(t["channel_id"])
                link = ch.mention if ch else f"`{t['id']}`"
                status = views.status_label(t["status"], t.get("stage"))
                claimed = f" · 🙋 <@{t['claimed_by']}>" if t.get("claimed_by") else ""
                lines.append(f"{link} · {label} · <@{t['opener_id']}> · {status}{claimed} · "
                             f"<t:{t.get('created_at', 0)}:R>")
            tickets_embed.description = "\n".join(lines)[:4000]
            if len(open_items) > 25:
                tickets_embed.set_footer(text=f"… und {len(open_items) - 25} weitere")
        return [stat, tickets_embed]

    async def _refresh_dashboard(self, guild: discord.Guild) -> None:
        settings = await self.store.get_settings(guild)
        cid = settings.get("dashboard_channel_id")
        if not cid:
            return
        channel = guild.get_channel(cid)
        if not isinstance(channel, (discord.TextChannel, discord.Thread)):
            return
        embeds = await self.build_dashboard_embeds(guild)
        mid = settings.get("dashboard_message_id")
        if mid:
            try:
                await channel.get_partial_message(mid).edit(embeds=embeds)
                return
            except discord.NotFound:
                pass  # Nachricht wurde gelöscht → neu posten
            except discord.DiscordException:
                return
        try:
            msg = await channel.send(embeds=embeds)
            await self.store.set_dashboard(guild, channel.id, msg.id)
        except discord.DiscordException:
            log.debug("Dashboard konnte nicht gepostet werden", exc_info=True)

    async def setup_dashboard(self, guild: discord.Guild,
                              channel: Optional[discord.TextChannel]) -> None:
        if channel is None:
            await self.store.set_dashboard(guild, None, None)
            return
        await self.store.set_dashboard(guild, channel.id, None)
        await self._refresh_dashboard(guild)

    # ─────────────────────────────────────────────────────────────────────────
    # DASHBOARD: offene Tickets (für Support & Admins)
    # ─────────────────────────────────────────────────────────────────────────
    @commands.hybrid_command(name="kuhhilfe-tickets",
                      description="Übersicht offener Support-Tickets (für Support & Admins)")
    @commands.guild_only()
    @app_commands.describe(meine="Nur Tickets, für die du zuständig bist / die du übernommen hast")
    async def tickets_overview(self, ctx: commands.Context, meine: bool = False) -> None:
        if not await self._is_staff(ctx.author):
            await self._reply(ctx, "❌ Dieser Befehl ist für Support-Mitglieder und Admins.")
            return
        all_t = await self.store.all_tickets(ctx.guild)
        items = []
        for t in all_t.values():
            if t.get("status") == STATUS_CLOSED:
                continue
            leaf, label = await self.store.resolve_leaf(ctx.guild, t["category_key"])
            if leaf is None:
                label = t.get("category_label") or t["category_key"]
                leaf = {"role_ids": [], "user_ids": []}
            if meine and not (t.get("claimed_by") == ctx.author.id
                              or self.is_support_for(ctx.author, leaf)):
                continue
            items.append((t, label))
        items.sort(key=lambda x: x[0].get("created_at", 0))

        title = "🎫 Meine offenen Tickets" if meine else "🎫 Offene Tickets"
        if not items:
            await self._reply(ctx, embed=discord.Embed(
                title=title, description="_Keine offenen Tickets._", color=COLOR_INFO))
            return
        lines = []
        for t, label in items[:25]:
            ch = ctx.guild.get_channel(t["channel_id"])
            link = ch.mention if ch else f"`{t['id']}`"
            status = views.status_label(t["status"], t.get("stage"))
            claimed = f" · 🙋 <@{t['claimed_by']}>" if t.get("claimed_by") else ""
            lines.append(f"{link} · `{t['id']}` · {label} · <@{t['opener_id']}> · "
                         f"{status}{claimed} · <t:{t.get('created_at', 0)}:R>")
        embed = discord.Embed(title=title, description="\n".join(lines)[:4000], color=COLOR_INFO)
        embed.set_footer(text=f"{len(items)} offen" + (f" · zeige 25" if len(items) > 25 else ""))
        await self._reply(ctx, embed=embed)

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
            elif custom_id == TICKET_REOPEN:
                await self._handle_reopen(interaction)
            elif custom_id == TICKET_MANAGE:
                await self._handle_manage(interaction)
        except discord.DiscordException:
            log.exception("Fehler bei KuhHilfe-Interaktion (%s)", custom_id)

    @commands.Cog.listener("on_guild_channel_delete")
    async def on_guild_channel_delete(self, channel: discord.abc.GuildChannel) -> None:
        """Räumt den Ticket-Datensatz auf, wenn ein Ticket-Channel (manuell) gelöscht wird."""
        guild = getattr(channel, "guild", None)
        if guild is None:
            return
        ticket = await self.store.get_ticket(guild, channel.id)
        if not ticket:
            return
        await self.store.delete_ticket(guild, channel.id)
        self._ticket_channels.discard(channel.id)
        # Nur loggen, wenn der Channel noch offen/aktiv war (geschlossene werden ohnehin gelöscht)
        if ticket.get("status") != STATUS_CLOSED:
            await self._modlog(
                guild,
                f"🗑️ Ticket `{ticket['id']}` entfernt und Channel **#{channel.name}** wurde gelöscht.",
                color=COLOR_CLOSED)
        await self._refresh_dashboard(guild)

    @commands.Cog.listener("on_message")
    async def on_message(self, message: discord.Message) -> None:
        """Hält die Inaktivitäts-Uhr aktuell: jede menschliche Nachricht im Ticket zählt als Aktivität."""
        if message.guild is None or message.author.bot:
            return
        if message.channel.id not in self._ticket_channels:
            return
        ticket = await self.store.get_ticket(message.guild, message.channel.id)
        if not ticket:
            self._ticket_channels.discard(message.channel.id)
            return
        await self.store.update_ticket(
            message.guild, message.channel.id,
            last_activity=int(time.time()), inactivity_warned=False)

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
            settings = await self.store.get_settings(guild)
            self._autodelete(interaction, settings.get("confirm_delete_seconds", DEFAULT_CONFIRM_DELETE))
            return

        if not await self._can_open(interaction):
            return

        settings = await self.store.get_settings(guild)
        cd = settings.get("confirm_delete_seconds", DEFAULT_CONFIRM_DELETE)

        top = list(cats.items())
        if len(top) == 1:
            key, node = top[0]
            await self._descend_single(interaction, key, node, cd)
        else:
            entries = [(key, node) for key, node in top]
            view = WizardSelectView(self, entries, interaction.user.id,
                                    "Wähle die passende Kategorie für dein Anliegen …")
            await interaction.response.send_message(
                embed=discord.Embed(
                    description="Wähle die passende Kategorie für dein Anliegen:",
                    color=COLOR_INFO),
                view=view, ephemeral=True)
            self._autodelete(interaction, cd)

    async def _descend_single(self, interaction: discord.Interaction,
                              path: str, node: Dict[str, Any], cd: int = 0) -> None:
        """Nur eine oberste Ebene vorhanden → direkt passend weiterleiten."""
        if not self.store.is_group(node):
            await interaction.response.send_modal(make_modal(self, node, path))
            return
        children = [
            (f"{path}{CATEGORY_PATH_SEP}{ck}", child)
            for ck, child in (node.get("children") or {}).items()
        ]
        if not children:
            await interaction.response.send_message(
                embed=discord.Embed(
                    description="⚠️ Der Support ist noch nicht vollständig eingerichtet.",
                    color=COLOR_ERROR),
                ephemeral=True)
            self._autodelete(interaction, cd)
            return
        if len(children) == 1:
            child_path, leaf = children[0]
            await interaction.response.send_modal(make_modal(self, leaf, child_path))
            return
        view = WizardSelectView(self, children, interaction.user.id,
                                "Wähle die passende Unterkategorie …")
        emoji = (node.get("emoji") + " ") if node.get("emoji") else ""
        await interaction.response.send_message(
            embed=discord.Embed(
                description=f"{emoji}**{node['name']}** - wähle die passende Unterkategorie:",
                color=COLOR_INFO),
            view=view, ephemeral=True)
        self._autodelete(interaction, cd)

    @staticmethod
    def _fmt_duration(seconds: int) -> str:
        seconds = max(0, int(seconds))
        if seconds < 60:
            return f"{seconds} Sek."
        minutes, sec = divmod(seconds, 60)
        if minutes < 60:
            return f"{minutes} Min." + (f" {sec} Sek." if sec else "")
        hours, minutes = divmod(minutes, 60)
        return f"{hours} Std." + (f" {minutes} Min." if minutes else "")

    async def _can_open(self, interaction: discord.Interaction) -> bool:
        """Prüft Blacklist, offene-Tickets-Limit und Cooldown. Meldet ggf. ephemer."""
        guild, user = interaction.guild, interaction.user
        settings = await self.store.get_settings(guild)
        cd_display = settings.get("confirm_delete_seconds", DEFAULT_CONFIRM_DELETE)

        async def reject(desc: str) -> bool:
            await interaction.response.send_message(
                embed=discord.Embed(description=desc, color=COLOR_ERROR), ephemeral=True)
            self._autodelete(interaction, cd_display)
            return False

        # 1) Blacklist
        if user.id in (settings.get("blacklist") or []):
            return await reject("🚫 Du bist aktuell vom Support ausgeschlossen und kannst kein "
                                "Ticket öffnen.")
        # 2) Limit offener Tickets
        limit = settings.get("max_open_per_user", DEFAULT_MAX_OPEN) or 0
        if limit > 0:
            open_count = await self.store.count_open_for_user(guild, user.id)
            if open_count >= limit:
                return await reject(
                    f"⚠️ Du hast bereits **{open_count}** offene(s) Ticket(s). "
                    f"Bitte schließe zuerst ein bestehendes (max. {limit}).")
        # 3) Cooldown
        cooldown = settings.get("cooldown_seconds") or 0
        if cooldown > 0:
            last = (settings.get("cooldowns") or {}).get(str(user.id), 0)
            remaining = cooldown - (int(time.time()) - last)
            if remaining > 0:
                return await reject(
                    f"⏳ Bitte warte noch **{self._fmt_duration(remaining)}**, bevor du ein "
                    "neues Ticket öffnest.")
        return True

    async def finalize_ticket(self, interaction: discord.Interaction,
                              path: str, answers: Dict[str, str]) -> None:
        """Wird vom Modal aufgerufen: erstellt Channel + Ticket + pingt Zuständige."""
        guild = interaction.guild
        opener = interaction.user

        leaf, label = await self.store.resolve_leaf(guild, path)
        if leaf is None:
            await interaction.response.send_message(
                "❌ Diese Kategorie existiert nicht mehr. Bitte starte erneut.", ephemeral=True)
            return

        # Blacklist/Limit/Cooldown erneut prüfen (Zeit zwischen Panel-Klick und Absenden)
        if not await self._can_open(interaction):
            return
        settings = await self.store.get_settings(guild)

        await interaction.response.defer(ephemeral=True, thinking=True)

        ticket_id = await self.store.generate_ticket_id(guild)
        try:
            channel = await tickets.create_ticket_channel(
                guild, opener, leaf, ticket_id, settings.get("ticket_category_id"))
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
            guild, ticket_id, channel.id, opener.id, path, label, answers)

        embed = views.build_ticket_embed(ticket, leaf, opener, guild, label)
        content = views.build_ping_content(leaf, opener)
        control_msg = await channel.send(
            content=content,
            embed=embed,
            view=views.build_ticket_controls(STATUS_OPEN),
            allowed_mentions=discord.AllowedMentions(roles=True, users=True, everyone=False),
        )
        await self.store.update_ticket(guild, channel.id, message_id=control_msg.id)
        self._ticket_channels.add(channel.id)
        await self.store.record_creation(guild, opener.id, int(time.time()))
        await self.store.bump_opened(guild, path)
        await self._refresh_dashboard(guild)

        confirm = await interaction.followup.send(
            embed=discord.Embed(
                description=f"✅ Dein Ticket wurde erstellt: {channel.mention}",
                color=COLOR_INFO),
            ephemeral=True, wait=True)
        self._autodelete(interaction, settings.get("confirm_delete_seconds", DEFAULT_CONFIRM_DELETE),
                         confirm)
        await self._modlog(
            guild, f"🎫 Ticket `{ticket_id}` geöffnet von {opener.mention} · "
                   f"**{label}** · {channel.mention}", color=COLOR_TICKET)

    # ─────────────────────────────────────────────────────────────────────────
    # TICKET-AKTIONEN
    # ─────────────────────────────────────────────────────────────────────────
    async def _load_ticket_ctx(self, interaction: discord.Interaction):
        """Lädt (ticket, leaf, label) zum Channel der Interaktion oder meldet Fehler."""
        ticket = await self.store.get_ticket(interaction.guild, interaction.channel_id)
        if not ticket:
            await interaction.response.send_message(
                "❌ Zu diesem Channel gibt es kein aktives Ticket.", ephemeral=True)
            return None, None, None
        # message_id nachtragen (Tickets von vor dem Einbau dieser ID), damit das
        # Aktualisieren der Steuernachricht aus dem Verwalten-Panel funktioniert.
        if not ticket.get("message_id") and interaction.message is not None:
            updated = await self.store.update_ticket(
                interaction.guild, interaction.channel_id, message_id=interaction.message.id)
            ticket = updated or ticket
        leaf, label = await self.store.resolve_leaf(interaction.guild, ticket["category_key"])
        if leaf is None:
            # Kategorie gelöscht – minimaler Ersatz, damit Schließen weiter funktioniert
            label = ticket.get("category_label") or ticket["category_key"]
            leaf = {"key": ticket["category_key"], "name": label,
                    "role_ids": [], "user_ids": [], "fields": []}
        return ticket, leaf, label

    async def _handle_claim(self, interaction: discord.Interaction) -> None:
        ticket, leaf, label = await self._load_ticket_ctx(interaction)
        if not ticket:
            return
        if not self.is_support_for(interaction.user, leaf):
            await interaction.response.send_message(
                "❌ Nur zuständige Support-Mitglieder können ein Ticket übernehmen.", ephemeral=True)
            return
        current = ticket.get("claimed_by")
        if current == interaction.user.id:
            await interaction.response.send_message(
                "ℹ️ Du bearbeitest dieses Ticket bereits.", ephemeral=True)
            return

        # Übernehmen (auch von einem anderen Bearbeiter) setzt die Stufe auf „In Bearbeitung" (→ blau)
        ticket = await self.store.update_ticket(
            interaction.guild, interaction.channel_id,
            claimed_by=interaction.user.id, stage=STAGE_IN_PROGRESS)
        opener = interaction.guild.get_member(ticket["opener_id"])
        embed = views.build_ticket_embed(ticket, leaf, opener, interaction.guild, label)
        await interaction.response.edit_message(
            embed=embed, view=views.build_ticket_controls(ticket["status"]))
        if current:
            await self._notice(
                interaction.channel,
                f"🔁 {interaction.user.mention} hat dieses Ticket von <@{current}> übernommen.")
            await self._modlog(
                interaction.guild,
                f"🔁 Ticket `{ticket['id']}` übernommen von {interaction.user.mention} "
                f"(vorher <@{current}>).", color=COLOR_INFO)
        else:
            await self._notice(
                interaction.channel,
                f"🙋 {interaction.user.mention} kümmert sich jetzt um dieses Ticket.")
            await self._modlog(
                interaction.guild,
                f"🙋 Ticket `{ticket['id']}` übernommen von {interaction.user.mention}.",
                color=COLOR_INFO)
        await self.store.bump_claim(interaction.guild, interaction.user.id)
        await self._refresh_dashboard(interaction.guild)

    async def _handle_close(self, interaction: discord.Interaction) -> None:
        ticket, leaf, label = await self._load_ticket_ctx(interaction)
        if not ticket:
            return
        if ticket["status"] == STATUS_CLOSED:
            await interaction.response.send_message("ℹ️ Dieses Ticket ist bereits geschlossen.", ephemeral=True)
            return

        if self.is_support_for(interaction.user, leaf):
            await self._effective_close(interaction, ticket, leaf, label)
            return

        # Nicht-Support (z. B. Ersteller): nur Schließung anfragen
        ticket = await self.store.update_ticket(
            interaction.guild, interaction.channel_id, status=STATUS_CLOSE_REQUESTED)
        opener = interaction.guild.get_member(ticket["opener_id"])
        embed = views.build_ticket_embed(ticket, leaf, opener, interaction.guild, label)
        await interaction.response.edit_message(
            embed=embed, view=views.build_ticket_controls(STATUS_CLOSE_REQUESTED))
        ping = views.build_ping_content(leaf, interaction.user)
        await self._notice(
            interaction.channel,
            f"🔒 {interaction.user.mention} möchte dieses Ticket schließen.\n"
            "Ein Support-Mitglied muss die Schließung über **Schließen bestätigen** freigeben.",
            ping=ping)
        await self._modlog(
            interaction.guild,
            f"🟡 Ticket `{ticket['id']}`: Schließung angefragt von {interaction.user.mention}.",
            color=COLOR_PENDING)

    async def _handle_confirm_close(self, interaction: discord.Interaction) -> None:
        ticket, leaf, label = await self._load_ticket_ctx(interaction)
        if not ticket:
            return
        if not self.is_support_for(interaction.user, leaf):
            await interaction.response.send_message(
                "❌ Nur zuständige Support-Mitglieder können die Schließung bestätigen.", ephemeral=True)
            return
        await self._effective_close(interaction, ticket, leaf, label)

    async def _handle_reopen(self, interaction: discord.Interaction) -> None:
        ticket, leaf, label = await self._load_ticket_ctx(interaction)
        if not ticket:
            return
        if not self.is_support_for(interaction.user, leaf):
            await interaction.response.send_message(
                "❌ Nur zuständige Support-Mitglieder können ein Ticket wieder öffnen.", ephemeral=True)
            return
        if ticket["status"] == STATUS_OPEN:
            await interaction.response.send_message("ℹ️ Dieses Ticket ist bereits offen.", ephemeral=True)
            return

        # Status zurück auf offen → der Lösch-Scheduler fasst es nicht mehr an
        ticket = await self.store.update_ticket(
            interaction.guild, interaction.channel_id,
            status=STATUS_OPEN, closed_at=None, closed_by=None)
        opener = interaction.guild.get_member(ticket["opener_id"])
        embed = views.build_ticket_embed(ticket, leaf, opener, interaction.guild, label)
        await interaction.response.edit_message(
            embed=embed, view=views.build_ticket_controls(STATUS_OPEN))
        await self._notice(
            interaction.channel,
            f"♻️ {interaction.user.mention} hat dieses Ticket wieder geöffnet.")
        await self._modlog(
            interaction.guild,
            f"♻️ Ticket `{ticket['id']}` wieder geöffnet von {interaction.user.mention}.",
            color=COLOR_TICKET)
        await self._refresh_dashboard(interaction.guild)

    # ─────────────────────────────────────────────────────────────────────────
    # VERWALTEN-PANEL (Status · Nutzer +/- · Umleiten)
    # ─────────────────────────────────────────────────────────────────────────
    async def _refresh_ticket_message(self, guild: discord.Guild,
                                      channel: discord.abc.GuildChannel,
                                      ticket: Dict[str, Any]) -> None:
        """Aktualisiert die Steuernachricht des Tickets (Embed + Controls)."""
        leaf, label = await self.store.resolve_leaf(guild, ticket["category_key"])
        if leaf is None:
            label = ticket.get("category_label") or ticket["category_key"]
            leaf = {"name": label, "role_ids": [], "user_ids": [], "fields": []}
        opener = guild.get_member(ticket["opener_id"])
        embed = views.build_ticket_embed(ticket, leaf, opener, guild, label)
        mid = ticket.get("message_id")
        if mid:
            try:
                await channel.get_partial_message(mid).edit(
                    embed=embed, view=views.build_ticket_controls(ticket["status"]))
            except discord.DiscordException:
                pass

    async def _handle_manage(self, interaction: discord.Interaction) -> None:
        ticket, leaf, _ = await self._load_ticket_ctx(interaction)
        if not ticket:
            return
        if not self.is_support_for(interaction.user, leaf):
            await interaction.response.send_message(
                "❌ Nur zuständige Support-Mitglieder können ein Ticket verwalten.", ephemeral=True)
            return
        embed, view = await manage.build_manage(
            self, interaction.guild, interaction.channel_id, ticket, interaction.user.id)
        await interaction.response.send_message(embed=embed, view=view, ephemeral=True)

    async def _mgmt_ticket(self, interaction: discord.Interaction):
        """Lädt das Ticket zum aktuellen Channel oder meldet Fehler (für Panel-Aktionen).

        Erwartet, dass die Interaktion bereits via defer() bestätigt wurde.
        """
        ticket = await self.store.get_ticket(interaction.guild, interaction.channel_id)
        if not ticket:
            await interaction.followup.send(
                "❌ Zu diesem Channel gibt es kein aktives Ticket mehr.", ephemeral=True)
        return ticket

    async def _mgmt_refresh_panel(self, interaction: discord.Interaction,
                                  ticket: Dict[str, Any]) -> None:
        embed, view = await manage.build_manage(
            self, interaction.guild, interaction.channel_id, ticket, interaction.user.id)
        await interaction.edit_original_response(embed=embed, view=view)

    async def mgmt_set_stage(self, interaction: discord.Interaction, stage) -> None:
        await interaction.response.defer()
        ticket = await self._mgmt_ticket(interaction)
        if not ticket:
            return
        ticket = await self.store.update_ticket(interaction.guild, interaction.channel_id, stage=stage)
        channel = interaction.channel
        await self._refresh_ticket_message(interaction.guild, channel, ticket)
        label = STAGE_LABELS.get(stage, "🟢 Offen")
        await self._notice(channel, f"🔧 Status: **{label}**")
        await self._modlog(interaction.guild,
                           f"🔧 Ticket `{ticket['id']}`: Status → {label} (von {interaction.user.mention}).",
                           color=COLOR_INFO)
        await self._mgmt_refresh_panel(interaction, ticket)

    async def mgmt_add_members(self, interaction: discord.Interaction,
                               member_ids: List[int]) -> None:
        await interaction.response.defer()
        ticket = await self._mgmt_ticket(interaction)
        if not ticket:
            return
        channel = interaction.channel
        added = list(ticket.get("added_members") or [])
        newly = []
        for uid in member_ids:
            member = interaction.guild.get_member(uid)
            if member is None or member.id == ticket["opener_id"]:
                continue
            try:
                await channel.set_permissions(member, overwrite=tickets.member_allow(),
                                              reason="KuhHilfe: Nutzer zum Ticket hinzugefügt")
            except discord.DiscordException:
                continue
            if uid not in added:
                added.append(uid)
            newly.append(member)
        ticket = await self.store.update_ticket(interaction.guild, interaction.channel_id,
                                                added_members=added)
        await self._refresh_ticket_message(interaction.guild, channel, ticket)
        if newly:
            mentions = " ".join(m.mention for m in newly)
            await self._notice(
                channel,
                f"➕ {interaction.user.mention} hat {mentions} zum Ticket hinzugefügt.",
                ping=mentions)
            await self._modlog(interaction.guild,
                               f"➕ Ticket `{ticket['id']}`: {mentions} hinzugefügt (von {interaction.user.mention}).",
                               color=COLOR_INFO)
        await self._mgmt_refresh_panel(interaction, ticket)

    async def mgmt_remove_member(self, interaction: discord.Interaction, member_id: int) -> None:
        await interaction.response.defer()
        ticket = await self._mgmt_ticket(interaction)
        if not ticket:
            return
        channel = interaction.channel
        added = [u for u in (ticket.get("added_members") or []) if u != member_id]
        member = interaction.guild.get_member(member_id)
        if member is not None:
            try:
                await channel.set_permissions(member, overwrite=None,
                                              reason="KuhHilfe: Nutzer aus Ticket entfernt")
            except discord.DiscordException:
                pass
        ticket = await self.store.update_ticket(interaction.guild, interaction.channel_id,
                                                added_members=added)
        await self._refresh_ticket_message(interaction.guild, channel, ticket)
        await self._notice(
            channel, f"➖ {interaction.user.mention} hat <@{member_id}> aus dem Ticket entfernt.")
        await self._modlog(interaction.guild,
                           f"➖ Ticket `{ticket['id']}`: <@{member_id}> entfernt (von {interaction.user.mention}).",
                           color=COLOR_INFO)
        await self._mgmt_refresh_panel(interaction, ticket)

    async def mgmt_reassign(self, interaction: discord.Interaction, new_path: str) -> None:
        await interaction.response.defer()
        ticket = await self._mgmt_ticket(interaction)
        if not ticket:
            return
        new_leaf, new_label = await self.store.resolve_leaf(interaction.guild, new_path)
        if new_leaf is None:
            await interaction.followup.send("❌ Zielkategorie existiert nicht mehr.", ephemeral=True)
            return
        guild, channel = interaction.guild, interaction.channel
        opener = guild.get_member(ticket["opener_id"])
        added = ticket.get("added_members") or []
        # Zugriff neu aufbauen (alte Kategorie-Rollen/User raus, neue rein; Ersteller + Zusatz bleiben)
        if opener is not None:
            try:
                overwrites = tickets.build_overwrites(guild, opener, new_leaf, added)
                await channel.edit(overwrites=overwrites,
                                   reason=f"KuhHilfe: Ticket umgeleitet zu {new_label}")
            except discord.DiscordException:
                log.debug("Overwrites beim Umleiten nicht aktualisiert", exc_info=True)
        ticket = await self.store.update_ticket(interaction.guild, interaction.channel_id,
                                                category_key=new_path, category_label=new_label)
        await self._refresh_ticket_message(guild, channel, ticket)
        await self._notice(
            channel,
            f"🔀 Ticket umgeleitet nach **{new_label}** (von {interaction.user.mention}).")
        await self._modlog(guild,
                           f"🔀 Ticket `{ticket['id']}` umgeleitet nach **{new_label}** "
                           f"(von {interaction.user.mention}).", color=COLOR_INFO)
        await self._mgmt_refresh_panel(interaction, ticket)

    async def _count_closed(self, guild: discord.Guild, channel_id: int,
                            ticket: Dict[str, Any]) -> Dict[str, Any]:
        """Zählt ein Ticket genau einmal als 'geschlossen' (robust gegen Reopen/Close-Zyklen)."""
        if ticket.get("stats_closed_counted"):
            return ticket
        await self.store.bump_closed(guild, ticket["category_key"])
        return await self.store.update_ticket(guild, channel_id, stats_closed_counted=True) or ticket

    async def _effective_close(self, interaction: discord.Interaction,
                               ticket: Dict[str, Any], leaf: Dict[str, Any],
                               label: Optional[str]) -> None:
        guild = interaction.guild
        channel = interaction.channel

        ticket = await self.store.update_ticket(
            guild, channel.id, status=STATUS_CLOSED,
            closed_at=int(time.time()), closed_by=interaction.user.id)
        ticket = await self._count_closed(guild, channel.id, ticket)

        opener = guild.get_member(ticket["opener_id"])
        embed = views.build_ticket_embed(ticket, leaf, opener, guild, label)
        await interaction.response.edit_message(
            embed=embed, view=views.build_ticket_controls(STATUS_CLOSED))
        await self._modlog(
            guild, f"⚪ Ticket `{ticket['id']}` geschlossen von {interaction.user.mention} · "
                   f"**{label}**", color=COLOR_CLOSED)
        await self._maybe_feedback(guild, ticket)
        await self._post_close_outcome(guild, channel, ticket, f"von {interaction.user.mention}")

    async def _auto_close(self, guild: discord.Guild, channel: discord.abc.GuildChannel,
                          ticket: Dict[str, Any]) -> None:
        """Schließt ein Ticket automatisch (Inaktivität) – ohne Interaktion."""
        leaf, label = await self.store.resolve_leaf(guild, ticket["category_key"])
        if leaf is None:
            label = ticket.get("category_label") or ticket["category_key"]
            leaf = {"name": label, "role_ids": [], "user_ids": [], "fields": []}

        ticket = await self.store.update_ticket(
            guild, channel.id, status=STATUS_CLOSED,
            closed_at=int(time.time()), closed_by=None)
        ticket = await self._count_closed(guild, channel.id, ticket)
        opener = guild.get_member(ticket["opener_id"])
        embed = views.build_ticket_embed(ticket, leaf, opener, guild, label)
        mid = ticket.get("message_id")
        if mid:
            try:
                await channel.get_partial_message(mid).edit(
                    embed=embed, view=views.build_ticket_controls(STATUS_CLOSED))
            except discord.DiscordException:
                pass
        await self._modlog(
            guild, f"⏰ Ticket `{ticket['id']}` automatisch wegen Inaktivität geschlossen · "
                   f"**{label}**", color=COLOR_CLOSED)
        await self._maybe_feedback(guild, ticket)
        await self._post_close_outcome(guild, channel, ticket, "automatisch wegen Inaktivität")

    async def _post_close_outcome(self, guild: discord.Guild,
                                  channel: discord.abc.GuildChannel,
                                  ticket: Dict[str, Any], closer_text: str) -> None:
        """Postet den Schließ-Hinweis und löst je nach Löschzeit das Löschen aus.

        Das Transcript entsteht erst KURZ VOR dem Löschen (in _delete_ticket_channel),
        damit Schließen/Wieder-Öffnen keine Transcript-Dubletten erzeugt.
        """
        settings = await self.store.get_settings(guild)
        delay = settings.get("delete_delay_seconds", DEFAULT_DELETE_DELAY)
        if delay is None:
            await self._notice(
                channel,
                f"✅ Ticket {closer_text} geschlossen.")
        elif delay <= 0:
            await self._delete_ticket_channel(guild, channel, ticket)
        else:
            minutes = max(1, round(delay / 60))
            await self._notice(
                channel,
                f"✅ Ticket {closer_text} geschlossen. "
                f"Dieser Channel wird in **{minutes} Min** automatisch gelöscht.")
        await self._refresh_dashboard(guild)

    async def _maybe_feedback(self, guild: discord.Guild, ticket: Dict[str, Any]) -> None:
        """Schickt dem Ersteller (einmalig, optional) eine DM-Feedback-Abfrage."""
        settings = await self.store.get_settings(guild)
        if not settings.get("feedback_enabled") or ticket.get("feedback_requested"):
            return
        opener = guild.get_member(ticket["opener_id"])
        if opener is None or opener.bot:
            return
        _, label = await self.store.resolve_leaf(guild, ticket["category_key"])
        label = label or ticket.get("category_label") or ticket["category_key"]
        view = feedback.FeedbackView(self, guild.id, opener.id, ticket["id"],
                                     ticket["category_key"], label)
        try:
            await opener.send(embed=feedback.build_feedback_dm(guild.name, label), view=view)
        except discord.Forbidden:
            return  # DMs zu
        except discord.DiscordException:
            log.debug("Feedback-DM fehlgeschlagen", exc_info=True)
            return
        await self.store.update_ticket(guild, ticket["channel_id"], feedback_requested=True)

    async def store_feedback(self, guild_id: int, user_id: int, ticket_id: str,
                             category_key: str, label: str, rating: int) -> None:
        guild = self.bot.get_guild(guild_id)
        if guild is None:
            return
        await self.store.add_feedback(guild, {
            "ticket_id": ticket_id, "user_id": user_id, "category_key": category_key,
            "label": label, "rating": int(rating), "ts": int(time.time())})

    async def _write_transcript(self, guild: discord.Guild,
                                channel: discord.abc.GuildChannel,
                                ticket: Dict[str, Any]) -> None:
        """Erzeugt EINMAL ein Transcript – ins Archiv und/oder als DM an den Ersteller.

        Läuft unmittelbar vor dem Löschen des Channels.
        """
        settings = await self.store.get_settings(guild)
        archive_id = settings.get("transcript_channel_id")
        dm_opener = settings.get("dm_transcript")
        archive = guild.get_channel(archive_id) if archive_id else None
        if not isinstance(archive, (discord.TextChannel, discord.Thread)):
            archive = None
        if archive is None and not dm_opener:
            return

        _, label = await self.store.resolve_leaf(guild, ticket["category_key"])
        label = label or ticket.get("category_label") or ticket["category_key"]
        opener = guild.get_member(ticket["opener_id"])
        closed_by = ticket.get("closed_by")

        try:
            data, fname = await tickets.generate_transcript(channel, ticket)
        except discord.DiscordException:
            log.exception("Transcript konnte nicht erzeugt werden")
            return

        if archive is not None:
            summary = discord.Embed(
                title=f"📄 Transcript · Ticket {ticket['id']}",
                color=COLOR_CLOSED, timestamp=discord.utils.utcnow())
            summary.add_field(name="Kategorie", value=label, inline=True)
            summary.add_field(
                name="Ersteller",
                value=(opener.mention if opener else f"<@{ticket['opener_id']}>"), inline=True)
            if closed_by:
                summary.add_field(name="Geschlossen von", value=f"<@{closed_by}>", inline=True)
            try:
                await archive.send(embed=summary, file=discord.File(io.BytesIO(data), filename=fname))
            except discord.DiscordException:
                log.exception("Transcript konnte nicht ins Archiv geschrieben werden")

        if dm_opener and opener is not None:
            try:
                await opener.send(
                    embed=discord.Embed(
                        title=f"📄 Dein Ticket-Transcript · {label}",
                        description=f"Dein Ticket `{ticket['id']}` auf **{guild.name}** wurde geschlossen. "
                                    "Anbei das Protokoll des Gesprächs.",
                        color=COLOR_CLOSED),
                    file=discord.File(io.BytesIO(data), filename=fname))
            except discord.Forbidden:
                pass  # DMs des Nutzers sind zu
            except discord.DiscordException:
                log.debug("Transcript-DM fehlgeschlagen", exc_info=True)

    async def _delete_ticket_channel(self, guild: discord.Guild,
                                     channel: Optional[discord.abc.GuildChannel],
                                     ticket: Dict[str, Any]) -> None:
        # Transcript zuerst (solange der Channel noch existiert), dann löschen.
        if channel is not None:
            await self._write_transcript(guild, channel, ticket)
        await self.store.delete_ticket(guild, ticket["channel_id"])
        self._ticket_channels.discard(ticket["channel_id"])
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
        await self._populate_ticket_channels()
        while True:
            try:
                await self._sweep_closed()
                await self._sweep_inactive()
                await self._refresh_all_dashboards()
            except asyncio.CancelledError:
                raise
            except Exception:  # pragma: no cover
                log.exception("Fehler im KuhHilfe-Lösch-Loop")
            await asyncio.sleep(_DELETE_LOOP_INTERVAL)

    async def _refresh_all_dashboards(self) -> None:
        for guild in self.bot.guilds:
            try:
                await self._refresh_dashboard(guild)
            except Exception:  # pragma: no cover
                log.debug("Dashboard-Refresh fehlgeschlagen", exc_info=True)

    async def _populate_ticket_channels(self) -> None:
        """Baut das In-Memory-Set aktiver Ticket-Channel-IDs beim Start auf."""
        for guild in self.bot.guilds:
            try:
                tickets_map = await self.store.all_tickets(guild)
            except Exception:  # pragma: no cover
                continue
            for ticket in tickets_map.values():
                self._ticket_channels.add(ticket["channel_id"])

    async def _sweep_inactive(self) -> None:
        now = int(time.time())
        for guild in self.bot.guilds:
            settings = await self.store.get_settings(guild)
            inact_h = settings.get("inactivity_hours") or 0
            if inact_h <= 0:
                continue
            warn_h = settings.get("inactivity_warn_hours") or 0
            close_after = inact_h * 3600
            warn_after = (inact_h - warn_h) * 3600 if 0 < warn_h < inact_h else None
            tickets_map = await self.store.all_tickets(guild)
            for ticket in list(tickets_map.values()):
                if ticket.get("status") not in (STATUS_OPEN, STATUS_CLOSE_REQUESTED):
                    continue
                last = ticket.get("last_activity") or ticket.get("created_at") or now
                idle = now - last
                channel = guild.get_channel(ticket["channel_id"])
                if channel is None:
                    continue
                if idle >= close_after:
                    await self._auto_close(guild, channel, ticket)
                elif warn_after is not None and idle >= warn_after and not ticket.get("inactivity_warned"):
                    remaining_h = max(1, round((close_after - idle) / 3600))
                    opener = guild.get_member(ticket["opener_id"])
                    mention = opener.mention if opener else f"<@{ticket['opener_id']}>"
                    try:
                        await self._notice(
                            channel,
                            f"⏰ Hier war länger nichts los. Dieses Ticket wird in ca. "
                            f"**{remaining_h} Std.** automatisch geschlossen, wenn keine Antwort kommt. "
                            "Schreib einfach etwas, um es offen zu halten.",
                            ping=mention)
                        await self.store.update_ticket(guild, channel.id, inactivity_warned=True)
                    except discord.DiscordException:
                        log.debug("Inaktivitäts-Warnung fehlgeschlagen", exc_info=True)

    async def _sweep_closed(self) -> None:
        now = int(time.time())
        for guild in self.bot.guilds:
            settings = await self.store.get_settings(guild)
            delay = settings.get("delete_delay_seconds", DEFAULT_DELETE_DELAY)
            if delay is None:
                continue  # „nie löschen" → geschlossene Channels bleiben bestehen
            delay = max(0, delay)
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
    @commands.hybrid_group(name="kuhhilfe-setup", description="KuhHilfe - Support-System einrichten (nur Admins)")
    @commands.guild_only()
    @commands.has_permissions(manage_guild=True)
    @app_commands.default_permissions(manage_guild=True)
    async def setup_group(self, ctx: commands.Context) -> None:
        if ctx.invoked_subcommand is None:
            await ctx.send_help(ctx.command)

    # ── Unter-Gruppen (bündeln verwandte Befehle; Discord-Limit: 25 Children/Gruppe)
    @setup_group.group(name="gruppe", description="Oberkategorien (Gruppen) verwalten")
    async def gruppe_group(self, ctx: commands.Context) -> None:
        if ctx.invoked_subcommand is None:
            await ctx.send_help(ctx.command)

    @setup_group.group(name="kategorie", description="Finale Kategorien verwalten")
    async def kategorie_group(self, ctx: commands.Context) -> None:
        if ctx.invoked_subcommand is None:
            await ctx.send_help(ctx.command)

    @setup_group.group(name="rolle", description="Zuständige Rollen einer Kategorie verwalten")
    async def rolle_group(self, ctx: commands.Context) -> None:
        if ctx.invoked_subcommand is None:
            await ctx.send_help(ctx.command)

    @setup_group.group(name="user", description="Zuständige User einer Kategorie verwalten")
    async def user_group(self, ctx: commands.Context) -> None:
        if ctx.invoked_subcommand is None:
            await ctx.send_help(ctx.command)

    @setup_group.group(name="feld", description="Formular-Felder einer Kategorie verwalten")
    async def feld_group(self, ctx: commands.Context) -> None:
        if ctx.invoked_subcommand is None:
            await ctx.send_help(ctx.command)

    @setup_group.group(name="blacklist", description="Support-Sperren verwalten")
    async def blacklist_group(self, ctx: commands.Context) -> None:
        if ctx.invoked_subcommand is None:
            await ctx.send_help(ctx.command)

    # ── Panel-Helfer (von Menü & Slash-Befehlen genutzt) ───────────────────────
    async def post_panel(self, guild: discord.Guild,
                         channel: discord.TextChannel) -> discord.Message:
        cats = await self.store.list_categories(guild)
        embed = views.build_panel_embed(guild, cats)
        msg = await channel.send(embed=embed, view=views.build_panel_view())
        await self.store.set_panel(guild, channel.id, msg.id)
        return msg

    async def refresh_panel(self, guild: discord.Guild) -> bool:
        settings = await self.store.get_settings(guild)
        ch_id, msg_id = settings.get("panel_channel_id"), settings.get("panel_message_id")
        channel = guild.get_channel(ch_id) if ch_id else None
        if not channel or not msg_id:
            return False
        cats = await self.store.list_categories(guild)
        embed = views.build_panel_embed(guild, cats)
        try:
            msg = await channel.fetch_message(msg_id)
            await msg.edit(embed=embed, view=views.build_panel_view())
        except discord.NotFound:
            msg = await channel.send(embed=embed, view=views.build_panel_view())
            await self.store.set_panel(guild, channel.id, msg.id)
        return True

    # ── Setup ────────────────────────────────────────────────────────────────
    @setup_group.command(name="verwalten",
                      description="Öffnet das interaktive Verwaltungs-Menü (empfohlen)")
    async def verwalten(self, ctx: commands.Context) -> None:
        embed, view = await admin_ui.root_screen(self, ctx.guild, ctx.author.id)
        if ctx.interaction:
            await ctx.interaction.response.send_message(embed=embed, view=view, ephemeral=True)
        else:
            await ctx.send(embed=embed, view=view)

    @setup_group.command(name="panel", description="Postet das Support-Panel in einen Channel")
    @app_commands.describe(channel="Channel, in dem das Panel mit dem Button erscheinen soll")
    async def panel_cmd(self, ctx: commands.Context, channel: discord.TextChannel) -> None:
        try:
            await self.post_panel(ctx.guild, channel)
        except discord.Forbidden:
            await self._reply(ctx, f"❌ Ich darf in {channel.mention} nicht schreiben.")
            return
        await self._reply(ctx, f"✅ Support-Panel in {channel.mention} gepostet.")

    @setup_group.command(name="panel-aktualisieren", description="Aktualisiert das bestehende Panel")
    async def panel_refresh(self, ctx: commands.Context) -> None:
        if await self.refresh_panel(ctx.guild):
            await self._reply(ctx, "✅ Panel aktualisiert.")
        else:
            await self._reply(ctx, "❌ Es ist noch kein Panel gesetzt. Nutze `/kuhhilfe-setup panel`.")

    @setup_group.command(name="ticket-ablage",
                      description="Discord-Kanalkategorie, UNTER der die Ticket-Channels einsortiert werden")
    @app_commands.describe(ablage="Discord-Kanalkategorie (leer = ohne, Channels landen oben in der Liste)")
    async def set_ticket_category(self, ctx: commands.Context,
                                  ablage: Optional[discord.CategoryChannel] = None) -> None:
        await self.store.set_ticket_category(ctx.guild, ablage.id if ablage else None)
        if ablage:
            await self._reply(ctx, f"✅ Neue Ticket-Channels werden unter **{ablage.name}** einsortiert.")
        else:
            await self._reply(ctx, "✅ Ablage entfernt, neue Ticket-Channels landen ohne Kanalkategorie.")

    @setup_group.command(name="archiv",
                      description="Channel für Transcripts beim Schließen (ohne Angabe: deaktivieren)")
    @app_commands.describe(channel="Archiv-Channel für Transcripts (leer = aus)")
    async def set_archive(self, ctx: commands.Context,
                          channel: Optional[discord.TextChannel] = None) -> None:
        await self.store.set_transcript_channel(ctx.guild, channel.id if channel else None)
        if channel:
            await self._reply(ctx, f"✅ Transcripts werden beim Schließen in {channel.mention} gespeichert.")
        else:
            await self._reply(ctx, "✅ Transcript-Archiv deaktiviert.")

    @setup_group.command(name="modlog",
                      description="Protokoll-Channel für Ticket-Ereignisse (ohne Angabe: deaktivieren)")
    @app_commands.describe(channel="Channel für das Ticket-Protokoll (leer = aus)")
    async def set_modlog(self, ctx: commands.Context,
                         channel: Optional[discord.TextChannel] = None) -> None:
        await self.store.set_modlog_channel(ctx.guild, channel.id if channel else None)
        if channel:
            await self._reply(ctx, f"✅ Ticket-Ereignisse werden in {channel.mention} protokolliert.")
        else:
            await self._reply(ctx, "✅ Protokoll deaktiviert.")

    @setup_group.command(name="transcript-dm",
                      description="Dem Ersteller beim Schließen das Transcript per DM schicken")
    @app_commands.describe(aktiv="An = Transcript zusätzlich als DM an den Ersteller")
    async def set_dm_transcript(self, ctx: commands.Context, aktiv: bool) -> None:
        await self.store.set_dm_transcript(ctx.guild, aktiv)
        await self._reply(ctx, f"✅ Transcript-DM an den Ersteller ist jetzt **{'an' if aktiv else 'aus'}**.")

    @setup_group.command(name="feedback",
                      description="Ersteller nach dem Schließen per DM um eine Bewertung bitten")
    @app_commands.describe(aktiv="An = 1-5-Sterne-Abfrage per DM nach dem Schließen")
    async def set_feedback(self, ctx: commands.Context, aktiv: bool) -> None:
        await self.store.set_feedback_enabled(ctx.guild, aktiv)
        await self._reply(ctx, f"✅ Feedback-Abfrage nach dem Schließen ist jetzt **{'an' if aktiv else 'aus'}**.")

    @setup_group.command(name="statistik",
                      description="Ticket-Statistiken: Aufkommen, Top-Bearbeiter, Feedback")
    async def statistik(self, ctx: commands.Context) -> None:
        st = await self.store.get_stats(ctx.guild)
        fb = await self.store.get_feedback(ctx.guild)
        all_t = await self.store.all_tickets(ctx.guild)
        open_now = sum(1 for t in all_t.values() if t.get("status") != STATUS_CLOSED)

        embed = discord.Embed(title="📊 KuhHilfe - Statistik", color=COLOR_INFO)
        embed.add_field(name="Erstellt", value=str(st.get("opened", 0)), inline=True)
        embed.add_field(name="Geschlossen", value=str(st.get("closed", 0)), inline=True)
        embed.add_field(name="Aktuell offen", value=str(open_now), inline=True)

        bc = st.get("by_category", {})
        if bc:
            rows = sorted(bc.items(), key=lambda kv: kv[1].get("opened", 0), reverse=True)[:10]
            lines = []
            for path, c in rows:
                _, label = await self.store.resolve_leaf(ctx.guild, path)
                lines.append(f"**{label or path}**: {c.get('opened', 0)} erstellt · "
                             f"{c.get('closed', 0)} geschlossen")
            embed.add_field(name="Nach Kategorie", value="\n".join(lines)[:1024], inline=False)

        claims = st.get("claims", {})
        if claims:
            top = sorted(claims.items(), key=lambda kv: kv[1], reverse=True)[:10]
            embed.add_field(name="Top-Bearbeiter",
                            value="\n".join(f"<@{u}>: **{n}** übernommen" for u, n in top)[:1024],
                            inline=False)

        ratings = [f.get("rating", 0) for f in fb if f.get("rating")]
        if ratings:
            avg = sum(ratings) / len(ratings)
            dist = " ".join(f"{s}⭐:{ratings.count(s)}" for s in range(5, 0, -1))
            embed.add_field(name="Feedback",
                            value=f"Ø **{avg:.2f}** aus **{len(ratings)}** Bewertungen\n{dist}",
                            inline=False)
        await self._reply(ctx, embed=embed)

    @setup_group.command(name="statistik-reset",
                      description="Setzt die Statistik-Zähler & gespeicherten Feedback-Bewertungen zurück")
    async def statistik_reset(self, ctx: commands.Context) -> None:
        embed = discord.Embed(
            title="⚠️ Statistik zurücksetzen?",
            description=("Dadurch werden **alle Zähler** (erstellt / geschlossen / pro Kategorie / "
                         "Bearbeiter) **und die gespeicherten Feedback-Bewertungen** unwiderruflich "
                         "gelöscht. Offene Tickets bleiben unberührt."),
            color=COLOR_ERROR)
        view = _StatsResetView(self, ctx.author.id)
        if ctx.interaction:
            await ctx.interaction.response.send_message(embed=embed, view=view, ephemeral=True)
        else:
            await ctx.send(embed=embed, view=view)

    @setup_group.command(name="export",
                      description="Exportiert Kategorien & übertragbare Einstellungen als JSON")
    async def export_cfg(self, ctx: commands.Context) -> None:
        data = await self.store.export_data(ctx.guild)
        raw = json.dumps(data, indent=2, ensure_ascii=False).encode("utf-8")
        file = discord.File(io.BytesIO(raw), filename="kuhhilfe-config.json")
        if ctx.interaction:
            if ctx.interaction.response.is_done():
                await ctx.interaction.followup.send(file=file, ephemeral=True)
            else:
                await ctx.interaction.response.send_message(file=file, ephemeral=True)
        else:
            await ctx.send(file=file)

    @setup_group.command(name="import",
                      description="Importiert Kategorien & Einstellungen aus JSON (ersetzt Kategorien!)")
    @app_commands.describe(datei="JSON-Datei aus /kuhhilfe-setup export")
    async def import_cfg(self, ctx: commands.Context, datei: discord.Attachment) -> None:
        if not datei.filename.lower().endswith(".json"):
            await self._reply(ctx, "❌ Bitte eine `.json`-Datei aus dem Export anhängen.")
            return
        try:
            raw = await datei.read()
            data = json.loads(raw.decode("utf-8"))
        except Exception:
            await self._reply(ctx, "❌ Datei konnte nicht gelesen oder nicht als JSON geparst werden.")
            return
        if not isinstance(data, dict) or "categories" not in data:
            await self._reply(ctx, "❌ Ungültiges Format! Das sieht nicht nach einem KuhHilfe-Export aus.")
            return
        summary = await self.store.import_data(ctx.guild, data)
        await self.refresh_panel(ctx.guild)
        await self._reply(
            ctx,
            f"✅ Import übernommen: **{summary['categories']}** Kategorie-Einträge (oberste Ebene) und "
            f"**{summary['settings']}** Einstellungen.\n"
            "⚠️ Rollen/User/Channels sind serverspezifisch. ggf. über `/kuhhilfe-setup verwalten` neu zuweisen.")

    @setup_group.command(name="limit",
                      description="Max. gleichzeitig offene Tickets pro Nutzer (0 = unbegrenzt)")
    @app_commands.describe(anzahl="Anzahl (0 = unbegrenzt)")
    async def set_limit(self, ctx: commands.Context, anzahl: int) -> None:
        await self.store.set_max_open(ctx.guild, anzahl)
        txt = "unbegrenzt" if anzahl <= 0 else str(anzahl)
        await self._reply(ctx, f"✅ Offene Tickets pro Nutzer: **{txt}**.")

    @setup_group.command(name="cooldown",
                      description="Mindestabstand zwischen zwei neuen Tickets pro Nutzer (Sek., 0 = aus)")
    @app_commands.describe(sekunden="Sekunden Wartezeit zwischen Tickets (0 = aus)")
    async def set_cooldown(self, ctx: commands.Context, sekunden: int) -> None:
        await self.store.set_cooldown(ctx.guild, max(0, sekunden))
        if sekunden <= 0:
            await self._reply(ctx, "✅ Cooldown ist **aus**.")
        else:
            await self._reply(ctx, f"✅ Zwischen zwei Tickets müssen Nutzer jetzt "
                                   f"**{self._fmt_duration(sekunden)}** warten.")

    @blacklist_group.command(name="add",
                      description="Sperrt einen Nutzer vom Öffnen neuer Tickets")
    @app_commands.describe(user="Zu sperrender Nutzer")
    async def blacklist_add(self, ctx: commands.Context, user: discord.Member) -> None:
        changed = await self.store.set_blacklisted(ctx.guild, user.id, add=True)
        await self._reply(ctx, f"🚫 {user.mention} ist jetzt vom Support ausgeschlossen." if changed
                          else f"ℹ️ {user.mention} war bereits gesperrt.")

    @blacklist_group.command(name="remove", description="Hebt die Support-Sperre eines Nutzers auf")
    @app_commands.describe(user="Nutzer, dessen Sperre aufgehoben wird")
    async def blacklist_remove(self, ctx: commands.Context, user: discord.Member) -> None:
        changed = await self.store.set_blacklisted(ctx.guild, user.id, add=False)
        await self._reply(ctx, f"✅ Sperre für {user.mention} aufgehoben." if changed
                          else f"ℹ️ {user.mention} war nicht gesperrt.")

    @blacklist_group.command(name="liste", description="Zeigt die gesperrten Nutzer")
    async def blacklist_list(self, ctx: commands.Context) -> None:
        ids = await self.store.get_blacklist(ctx.guild)
        if not ids:
            await self._reply(ctx, "ℹ️ Es sind keine Nutzer gesperrt.")
            return
        mentions = ", ".join(f"<@{i}>" for i in ids)
        await self._reply(ctx, embed=discord.Embed(
            title="🚫 Gesperrte Nutzer", description=mentions[:4000], color=COLOR_ERROR))

    @setup_group.command(name="loeschzeit",
                      description="Minuten bis zum Löschen eines geschlossenen Channels (0 = sofort, negativ = nie)")
    @app_commands.describe(minuten="Minuten bis zum Löschen · 0 = sofort · negativ (z. B. -1) = nie löschen")
    async def set_delete_delay(self, ctx: commands.Context, minuten: int) -> None:
        if minuten < 0:
            await self.store.set_delete_delay(ctx.guild, None)
            await self._reply(ctx, "✅ Geschlossene Channels werden **nie** automatisch gelöscht "
                                   "(Transcript nur, wenn ein Channel tatsächlich gelöscht wird).")
            return
        await self.store.set_delete_delay(ctx.guild, minuten * 60)
        txt = "sofort" if minuten == 0 else f"{minuten} Min"
        await self._reply(ctx, f"✅ Geschlossene Channels werden **{txt}** nach dem Schließen gelöscht.")

    @setup_group.command(name="bestaetigungen",
                      description="Sekunden, bis ephemere Nutzer-Bestätigungen verschwinden (0 = aus)")
    @app_commands.describe(sekunden="Sekunden bis zum automatischen Ausblenden (0 = dauerhaft)")
    async def set_confirm_delete(self, ctx: commands.Context, sekunden: int) -> None:
        await self.store.set_confirm_delete(ctx.guild, max(0, sekunden))
        txt = "dauerhaft sichtbar" if sekunden <= 0 else f"nach {sekunden} Sek. ausgeblendet"
        await self._reply(ctx, f"✅ Nutzer-Bestätigungen (nur für den Nutzer sichtbar) werden **{txt}**.")

    @setup_group.command(name="inaktivitaet",
                      description="Offene Tickets bei Inaktivität automatisch schließen (0 = aus)")
    @app_commands.describe(stunden="Stunden ohne Aktivität bis zum automatischen Schließen (0 = aus)",
                           vorwarnung="Stunden vor dem Schließen warnen (0 = keine Vorwarnung)")
    async def set_inactivity(self, ctx: commands.Context, stunden: int, vorwarnung: int = 0) -> None:
        await self.store.set_inactivity_hours(ctx.guild, max(0, stunden))
        await self.store.set_inactivity_warn_hours(ctx.guild, max(0, vorwarnung))
        if stunden <= 0:
            await self._reply(ctx, "✅ Auto-Schließen bei Inaktivität ist **aus**.")
            return
        warn = (f", Vorwarnung **{vorwarnung} Std.** vorher"
                if 0 < vorwarnung < stunden else " (ohne Vorwarnung)")
        await self._reply(ctx, f"✅ Offene Tickets werden nach **{stunden} Std.** ohne Aktivität "
                               f"automatisch geschlossen{warn}.")

    @setup_group.command(name="status", description="Zeigt die aktuelle KuhHilfe-Konfiguration")
    async def status_cmd(self, ctx: commands.Context) -> None:
        s = await self.store.get_settings(ctx.guild)
        cats = await self.store.list_categories(ctx.guild)

        def _ch(cid):
            ch = ctx.guild.get_channel(cid) if cid else None
            return ch.mention if ch else "_(nicht gesetzt)_"

        embed = discord.Embed(title="⚙️ KuhHilfe - Einstellungen", color=COLOR_INFO)
        embed.add_field(name="Panel-Channel", value=_ch(s.get("panel_channel_id")), inline=True)
        embed.add_field(name="Ticket-Ablage", value=_ch(s.get("ticket_category_id")), inline=True)
        embed.add_field(name="Archiv", value=_ch(s.get("transcript_channel_id")), inline=True)
        embed.add_field(name="Protokoll", value=_ch(s.get("modlog_channel_id")), inline=True)
        limit = s.get("max_open_per_user", DEFAULT_MAX_OPEN)
        embed.add_field(name="Limit/Nutzer", value=("unbegrenzt" if not limit else str(limit)), inline=True)
        cooldown = s.get("cooldown_seconds") or 0
        embed.add_field(name="Cooldown",
                        value=("aus" if cooldown <= 0 else self._fmt_duration(cooldown)), inline=True)
        embed.add_field(name="Gesperrt", value=str(len(s.get("blacklist") or [])), inline=True)
        dd = s.get("delete_delay_seconds", DEFAULT_DELETE_DELAY)
        delay_txt = "nie" if dd is None else ("sofort" if dd <= 0 else f"{round(dd / 60)} Min")
        embed.add_field(name="Löschzeit", value=delay_txt, inline=True)
        cd = s.get("confirm_delete_seconds", DEFAULT_CONFIRM_DELETE)
        embed.add_field(name="Bestätigungen",
                        value=("dauerhaft" if not cd else f"{cd} Sek."), inline=True)
        inact = s.get("inactivity_hours") or 0
        warn_h = s.get("inactivity_warn_hours") or 0
        if inact <= 0:
            inact_txt = "aus"
        else:
            inact_txt = f"{inact} Std." + (f" (Warnung {warn_h}h vorher)" if 0 < warn_h < inact else "")
        embed.add_field(name="Auto-Schließen (Inaktiv.)", value=inact_txt, inline=True)
        embed.add_field(name="Transcript-DM", value=("an" if s.get("dm_transcript") else "aus"), inline=True)
        embed.add_field(name="Feedback", value=("an" if s.get("feedback_enabled") else "aus"), inline=True)
        leaves = await self.store.iter_leaves(ctx.guild)
        groups = await self.store.iter_groups(ctx.guild)
        embed.add_field(name="Struktur",
                        value=f"{len(groups)} Gruppe(n), {len(leaves)} Kategorie(n)", inline=True)
        if cats:
            lines = []
            for key, node in cats.items():
                if self.store.is_group(node):
                    children = node.get("children") or {}
                    lines.append(f"📦 `{key}` · {node['name']} - {len(children)} Unterkategorie(n)")
                    for ck, child in children.items():
                        lines.append(f"　└ `{key}/{ck}` · {child['name']} - "
                                     f"{len(child.get('role_ids', []))}R/"
                                     f"{len(child.get('user_ids', []))}U/"
                                     f"{len(child.get('fields', []))}F")
                else:
                    lines.append(f"🏷️ `{key}` · {node['name']} - "
                                 f"{len(node.get('role_ids', []))}R/"
                                 f"{len(node.get('user_ids', []))}U/"
                                 f"{len(node.get('fields', []))}F")
            embed.add_field(name="Details (R=Rollen, U=User, F=Felder)",
                            value="\n".join(lines)[:1024], inline=False)
        await self._reply(ctx, embed=embed)

    # ── Vorlage ────────────────────────────────────────────────────────────────
    @setup_group.command(name="vorlagen",
                      description="Fügt eine generische Standard-Struktur hinzu (Vorhandenes bleibt)")
    async def vorlagen(self, ctx: commands.Context) -> None:
        summary = await presets.apply_presets(self.store, ctx.guild)
        await self.refresh_panel(ctx.guild)
        if not summary["groups"] and not summary["categories"]:
            await self._reply(ctx, "ℹ️ Alle Standard-Einträge sind bereits vorhanden!")
            return
        extra = f" · {summary['skipped']} bereits vorhanden" if summary["skipped"] else ""
        await self._reply(
            ctx,
            f"✅ Standard-Vorlage geladen: **{summary['groups']}** Gruppe(n), "
            f"**{summary['categories']}** Kategorie(n) hinzugefügt{extra}.\n"
            "Weise den Kategorien jetzt Rollen/User zu - am einfachsten über "
            "`/kuhhilfe-setup verwalten`.")

    # ── Gruppen (Oberkategorien) ───────────────────────────────────────────────
    @gruppe_group.command(name="add",
                      description="Legt eine Oberkategorie an (bündelt Unterkategorien, z. B. 'Fragen zu einem Spiel')")
    @app_commands.describe(name="Name der Gruppe", emoji="Optionales Emoji",
                           beschreibung="Optionale Kurzbeschreibung")
    async def gruppe_add(self, ctx: commands.Context, name: str,
                        emoji: Optional[str] = None, beschreibung: Optional[str] = None) -> None:
        emoji_value, emoji_err = resolve_emoji(ctx.guild, emoji)
        grp = await self.store.add_group(ctx.guild, name, emoji_value, beschreibung)
        if not grp:
            await self._reply(ctx, "❌ Name ungültig oder ein Key mit diesem Namen existiert bereits.")
            return
        hint = f"\n⚠️ {emoji_err} (ohne Emoji gespeichert)" if emoji_err else ""
        await self._reply(
            ctx,
            f"✅ Gruppe **{grp['name']}** (`{grp['key']}`) angelegt.{hint}\n"
            f"Lege nun Unterkategorien an: `/kuhhilfe-setup kategorie add name:… gruppe:{grp['key']}`.")

    @gruppe_group.command(name="remove", description="Löscht eine Gruppe inklusive ihrer Unterkategorien")
    @app_commands.describe(gruppe="Key der Gruppe")
    async def gruppe_remove(self, ctx: commands.Context, gruppe: str) -> None:
        node = await self.store.get_node(ctx.guild, gruppe)
        if not node or not self.store.is_group(node):
            await self._reply(ctx, f"❌ Keine Gruppe mit Key `{gruppe}`.")
            return
        await self.store.remove_node(ctx.guild, gruppe)
        await self._reply(ctx, f"✅ Gruppe `{gruppe}` inkl. Unterkategorien gelöscht.")

    # ── Kategorien (finale Ebene) ───────────────────────────────────────────────
    @kategorie_group.command(name="add",
                      description="Legt eine finale Kategorie an - optional unter einer Gruppe")
    @app_commands.describe(name="Name der Kategorie", gruppe="Key der Gruppe (leer = Direkt-Kategorie)",
                           emoji="Optionales Emoji", beschreibung="Optionale Kurzbeschreibung")
    async def cat_add(self, ctx: commands.Context, name: str, gruppe: Optional[str] = None,
                      emoji: Optional[str] = None, beschreibung: Optional[str] = None) -> None:
        emoji_value, emoji_err = resolve_emoji(ctx.guild, emoji)
        leaf, result = await self.store.add_leaf(ctx.guild, name, gruppe, emoji_value, beschreibung)
        if leaf is None:
            await self._reply(ctx, f"❌ {result}")
            return
        hint = f"\n⚠️ {emoji_err} (ohne Emoji gespeichert)" if emoji_err else ""
        await self._reply(
            ctx,
            f"✅ Kategorie **{leaf['name']}** (`{result}`) angelegt - mit den Standard-Feldern "
            f"*Betreff* & *Beschreibung*.{hint}\nWeise Zuständige zu "
            f"(`/kuhhilfe-setup rolle add kategorie:{result} …`) und passe die Felder an "
            f"(`/kuhhilfe-setup feld add kategorie:{result} …`).")

    @kategorie_group.command(name="remove", description="Löscht eine finale Kategorie")
    @app_commands.describe(kategorie="Pfad der Kategorie (z. B. 'spiel-fragen/wow' oder 'server')")
    async def cat_remove(self, ctx: commands.Context, kategorie: str) -> None:
        node = await self.store.get_node(ctx.guild, kategorie)
        if not node or self.store.is_group(node):
            await self._reply(ctx, f"❌ Keine finale Kategorie unter `{kategorie}`. "
                                   "(Gruppen löschst du mit `/kuhhilfe-setup gruppe remove`.)")
            return
        await self.store.remove_node(ctx.guild, kategorie)
        await self._reply(ctx, f"✅ Kategorie `{kategorie}` gelöscht.")

    @kategorie_group.command(name="liste", description="Zeigt den Kategorie-Baum")
    async def cat_list(self, ctx: commands.Context) -> None:
        cats = await self.store.list_categories(ctx.guild)
        if not cats:
            await self._reply(ctx, "ℹ️ Es sind noch keine Kategorien angelegt.")
            return

        def _leaf_value(leaf: Dict[str, Any]) -> str:
            roles = " ".join(f"<@&{r}>" for r in leaf.get("role_ids", [])) or "_keine_"
            users = " ".join(f"<@{u}>" for u in leaf.get("user_ids", [])) or "_keine_"
            fields = ", ".join(f["label"] for f in leaf.get("fields", [])) or "_keine_"
            return f"Rollen: {roles}\nUser: {users}\nFelder: {fields}"

        embed = discord.Embed(title="📂 Kategorie-Baum", color=COLOR_INFO)
        for key, node in cats.items():
            emoji = (node.get("emoji") + " ") if node.get("emoji") else ""
            if self.store.is_group(node):
                children = node.get("children") or {}
                for ck, child in children.items():
                    cem = (child.get("emoji") + " ") if child.get("emoji") else ""
                    embed.add_field(
                        name=f"{emoji}{node['name']} › {cem}{child['name']}  ·  `{key}/{ck}`",
                        value=_leaf_value(child), inline=False)
                if not children:
                    embed.add_field(name=f"{emoji}{node['name']}  ·  `{key}` (Gruppe)",
                                    value="_(noch keine Unterkategorien)_", inline=False)
            else:
                embed.add_field(name=f"{emoji}{node['name']}  ·  `{key}`",
                                value=_leaf_value(node), inline=False)
        await self._reply(ctx, embed=embed)

    # ── Rollen / User zuweisen (an finalen Kategorien) ──────────────────────────
    @rolle_group.command(name="add", description="Weist einer Kategorie eine zuständige Rolle zu")
    @app_commands.describe(kategorie="Pfad der Kategorie (z. B. 'spiel-fragen/wow')", rolle="Zuständige Rolle")
    async def cat_rolle_add(self, ctx: commands.Context, kategorie: str, rolle: discord.Role) -> None:
        res = await self.store.toggle_role(ctx.guild, kategorie, rolle.id, add=True)
        if res is None:
            await self._reply(ctx, f"❌ Keine finale Kategorie unter `{kategorie}`.")
            return
        await self._reply(ctx, f"✅ {rolle.mention} ist jetzt für `{kategorie}` zuständig.")

    @rolle_group.command(name="remove", description="Entfernt eine Rolle aus einer Kategorie")
    @app_commands.describe(kategorie="Pfad der Kategorie", rolle="Rolle")
    async def cat_rolle_remove(self, ctx: commands.Context, kategorie: str, rolle: discord.Role) -> None:
        res = await self.store.toggle_role(ctx.guild, kategorie, rolle.id, add=False)
        if res is None:
            await self._reply(ctx, f"❌ Keine finale Kategorie unter `{kategorie}`.")
            return
        await self._reply(ctx, f"✅ {rolle.mention} ist nicht mehr für `{kategorie}` zuständig.")

    @user_group.command(name="add", description="Weist einer Kategorie einen zuständigen User zu")
    @app_commands.describe(kategorie="Pfad der Kategorie", user="Zuständiger User")
    async def cat_user_add(self, ctx: commands.Context, kategorie: str, user: discord.Member) -> None:
        res = await self.store.toggle_user(ctx.guild, kategorie, user.id, add=True)
        if res is None:
            await self._reply(ctx, f"❌ Keine finale Kategorie unter `{kategorie}`.")
            return
        await self._reply(ctx, f"✅ {user.mention} ist jetzt für `{kategorie}` zuständig.")

    @user_group.command(name="remove", description="Entfernt einen User aus einer Kategorie")
    @app_commands.describe(kategorie="Pfad der Kategorie", user="User")
    async def cat_user_remove(self, ctx: commands.Context, kategorie: str, user: discord.Member) -> None:
        res = await self.store.toggle_user(ctx.guild, kategorie, user.id, add=False)
        if res is None:
            await self._reply(ctx, f"❌ Keine finale Kategorie unter `{kategorie}`.")
            return
        await self._reply(ctx, f"✅ {user.mention} ist nicht mehr für `{kategorie}` zuständig.")

    # ── Modal-Felder ───────────────────────────────────────────────────────────
    @feld_group.command(name="add", description="Fügt einer Kategorie ein Wizard-Feld hinzu")
    @app_commands.describe(kategorie="Pfad der Kategorie (z. B. 'spiel-fragen/wow')", label="Beschriftung des Feldes",
                           stil="Kurz oder Lang", pflicht="Muss ausgefüllt werden?",
                           max_laenge="Maximale Zeichenzahl", platzhalter="Optionaler Platzhaltertext")
    @app_commands.choices(stil=[
        app_commands.Choice(name="Kurz (einzeilig)", value="short"),
        app_commands.Choice(name="Lang (mehrzeilig)", value="paragraph"),
    ])
    async def feld_add(self, ctx: commands.Context, kategorie: str, label: str,
                       stil: str = "short", pflicht: bool = True,
                       max_laenge: int = 1000, platzhalter: Optional[str] = None) -> None:
        err = await self.store.add_field(ctx.guild, kategorie, label, stil, pflicht, platzhalter, max_laenge)
        if err:
            await self._reply(ctx, f"❌ {err}")
            return
        await self._reply(ctx, f"✅ Feld **{label}** zu `{kategorie}` hinzugefügt "
                               f"({FIELD_STYLE_LABELS.get(stil, stil)}, "
                               f"{'Pflicht' if pflicht else 'optional'}).")

    @feld_group.command(name="remove", description="Entfernt ein Feld einer Kategorie (Position)")
    @app_commands.describe(kategorie="Pfad der Kategorie", position="Position laut /kuhhilfe-setup feld liste (ab 1)")
    async def feld_remove(self, ctx: commands.Context, kategorie: str, position: int) -> None:
        removed = await self.store.remove_field(ctx.guild, kategorie, position)
        if not removed:
            await self._reply(ctx, f"❌ Kategorie `{kategorie}` oder Position {position} nicht gefunden.")
            return
        await self._reply(ctx, f"✅ Feld **{removed['label']}** aus `{kategorie}` entfernt.")

    @feld_group.command(name="liste", description="Zeigt die Felder einer Kategorie")
    @app_commands.describe(kategorie="Pfad der Kategorie")
    async def feld_liste(self, ctx: commands.Context, kategorie: str) -> None:
        leaf, label = await self.store.resolve_leaf(ctx.guild, kategorie)
        if leaf is None:
            await self._reply(ctx, f"❌ Keine finale Kategorie unter `{kategorie}`.")
            return
        fields = leaf.get("fields", [])
        if not fields:
            await self._reply(ctx, f"ℹ️ Kategorie `{kategorie}` hat noch keine Felder.")
            return
        lines = []
        for i, f in enumerate(fields, start=1):
            lines.append(
                f"**{i}.** {f['label']} · {FIELD_STYLE_LABELS.get(f.get('style'), f.get('style'))} · "
                f"{'Pflicht' if f.get('required') else 'optional'} · max {f.get('max_length')}")
        embed = discord.Embed(
            title=f"📝 Felder · {label}",
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


class _StatsResetView(discord.ui.View):
    """Bestätigung für das Zurücksetzen der Statistik (ephemer, kurzlebig)."""

    def __init__(self, cog: "KuhHilfe", author_id: int):
        super().__init__(timeout=60)
        self.cog = cog
        self.author_id = author_id

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.author_id:
            await interaction.response.send_message(
                "Diese Abfrage gehört zu einer anderen Person.", ephemeral=True)
            return False
        return True

    @discord.ui.button(label="Ja, zurücksetzen", emoji="🗑️", style=discord.ButtonStyle.danger)
    async def confirm(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await self.cog.store.reset_stats(interaction.guild)
        await self.cog._refresh_dashboard(interaction.guild)
        self.clear_items()
        self.stop()
        await interaction.response.edit_message(
            embed=discord.Embed(
                description="✅ Statistik-Zähler und Feedback-Bewertungen wurden zurückgesetzt.",
                color=COLOR_INFO),
            view=None)

    @discord.ui.button(label="Abbrechen", emoji="✖️", style=discord.ButtonStyle.secondary)
    async def cancel(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        self.clear_items()
        self.stop()
        await interaction.response.edit_message(
            embed=discord.Embed(description="Abgebrochen - nichts geändert.", color=COLOR_INFO),
            view=None)
