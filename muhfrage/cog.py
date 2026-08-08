"""
cog.py – Haupt-Cog von Muhfrage

Befehle:
  /muhfrage-setup rolle|kanal|info        (Admin, Server verwalten)
  /muhfrage erstellen [name]              (Manager) – Builder-Panel
  /muhfrage vorlage <typ> [name]          (Manager) – aus Vorlage + Builder
  /muhfrage teilnehmer <id>               (Manager) – Teilnehmer-Allowlist
  /muhfrage starten <id> [#kanal]         (Manager) – veröffentlichen
  /muhfrage beenden <id>                  (Manager) – schließen + auswerten
  /muhfrage ergebnisse <id>               (Manager) – Ergebnisse ansehen
  /muhfrage export <id>                   (Manager) – TXT/CSV-Export
  /muhfrage liste                         (Manager) – Übersicht
  /muhfrage bearbeiten <id>               (Manager) – Entwurf bearbeiten
  /muhfrage loeschen <id>                 (Manager) – löschen
  /muhfrage teilnehmen <id>               (alle) – abstimmen

Der persistente „Teilnehmen"-Button trägt die custom_id
`muhfrage_join:<slug>` und wird über on_interaction geroutet (übersteht Neustarts).
"""

import logging
from datetime import datetime, timezone
from functools import partial
from typing import Any, Dict, List, Optional

import discord
from discord import app_commands
from redbot.core import Config, commands
from redbot.core.bot import Red

from . import models, results, templates, views
from .builder import BuilderPanel, TitleModal
from .participation import ParticipationView
from .storage import SurveyStore

log = logging.getLogger("red.dark-cogs.muhfrage")


def is_manager():
    """Check: Server-verwalten ODER konfigurierte Manager-Rolle."""
    async def predicate(ctx: commands.Context) -> bool:
        if ctx.guild is None:
            return False
        if ctx.author.guild_permissions.manage_guild:
            return True
        role_id = await ctx.cog.store.get_manager_role_id(ctx.guild)
        if role_id and any(r.id == role_id for r in ctx.author.roles):
            return True
        raise commands.CheckFailure("not_manager")
    return commands.check(predicate)


class Muhfrage(commands.Cog):
    """🐄 Muhfrage – flexible Umfragen & Abstimmungen mit Punktvergabe."""

    def __init__(self, bot: Red):
        self.bot = bot
        self.config = Config.get_conf(self, identifier=0xC0FFEE_4D01, force_registration=True)
        self.config.register_guild(
            manager_role_id=None,
            results_channel_id=None,
            surveys={},
            responses={},
        )
        self.store = SurveyStore(self.config)

    async def cog_load(self) -> None:
        # Autocomplete für die Slug-Parameter registrieren (bound methods → (interaction, current))
        try:
            self.muhfrage_teilnehmen.autocomplete("id")(self._ac_open)
            for cmd in (self.muhfrage_teilnehmer, self.muhfrage_starten, self.muhfrage_beenden,
                        self.muhfrage_ergebnisse, self.muhfrage_export, self.muhfrage_bearbeiten,
                        self.muhfrage_loeschen):
                cmd.autocomplete("id")(self._ac_all)
        except Exception:  # pragma: no cover – Autocomplete ist nur Komfort
            log.debug("Autocomplete konnte nicht registriert werden", exc_info=True)

    # ─────────────────────────────────────────────────────────────────────────
    # HILFSFUNKTIONEN
    # ─────────────────────────────────────────────────────────────────────────

    async def _reply(self, ctx: commands.Context, content: str = "", *,
                     embed: Optional[discord.Embed] = None,
                     files: Optional[List[discord.File]] = None) -> None:
        kwargs: dict = {}
        if content:
            kwargs["content"] = content
        if embed:
            kwargs["embed"] = embed
        if files:
            kwargs["files"] = files
        if ctx.interaction:
            kwargs["ephemeral"] = True
            if ctx.interaction.response.is_done():
                await ctx.interaction.followup.send(**kwargs)
            else:
                await ctx.interaction.response.send_message(**kwargs)
        else:
            await ctx.send(**kwargs)

    async def _get_survey(self, ctx: commands.Context, slug: str) -> Optional[Dict[str, Any]]:
        survey = await self.store.get_survey(ctx.guild, slug)
        if not survey:
            await self._reply(ctx, f"❌ Keine Umfrage mit der ID `{slug}` gefunden.")
            return None
        return survey

    async def _resolve_new_slug(self, ctx: commands.Context, name: Optional[str]) -> Optional[str]:
        """Ermittelt einen freien Slug; None + Fehlermeldung bei Kollision."""
        if name:
            slug = self.store.normalize_slug(name)
            if not slug:
                await self._reply(ctx, "❌ Ungültiger Name. Bitte Buchstaben/Zahlen verwenden.")
                return None
            if await self.store.slug_exists(ctx.guild, slug):
                await self._reply(ctx, f"❌ Eine Umfrage mit der ID `{slug}` existiert bereits.")
                return None
            return slug
        return await self.store.generate_slug(ctx.guild)

    async def update_public_message(self, guild: discord.Guild, slug: str) -> None:
        """Aktualisiert die veröffentlichte Nachricht (Teilnehmerzahl/Status)."""
        survey = await self.store.get_survey(guild, slug)
        if not survey or not survey.get("published"):
            return
        pub = survey["published"]
        channel = guild.get_channel(pub["channel_id"])
        if not channel:
            return
        try:
            msg = await channel.fetch_message(pub["message_id"])
            count = await self.store.response_count(guild, slug)
            await msg.edit(embed=views.build_public_embed(survey, count),
                           view=views.build_join_view(survey))
        except (discord.NotFound, discord.Forbidden, discord.HTTPException):
            pass

    async def _deliver_results(self, ctx: commands.Context, survey: Dict[str, Any]) -> None:
        responses = await self.store.get_responses(ctx.guild, survey["id"])
        embed = results.build_results_embed(survey, responses)

        if survey["results_visibility"] == "public":
            channel_id = survey.get("result_channel_id") or await self.store.get_results_channel_id(ctx.guild)
            channel = ctx.guild.get_channel(channel_id) if channel_id else ctx.channel
            try:
                await channel.send(embed=embed)
                await self._reply(ctx, f"✅ Ergebnisse in {channel.mention} veröffentlicht.")
            except (discord.Forbidden, discord.HTTPException):
                await self._reply(ctx, "⚠️ Konnte den Ergebnis-Kanal nicht erreichen – hier die Ergebnisse:",
                                  embed=embed)
        else:
            await self._reply(ctx, "📊 Ergebnisse (nur für dich sichtbar):", embed=embed)

    # ─────────────────────────────────────────────────────────────────────────
    # AUTOCOMPLETE
    # ─────────────────────────────────────────────────────────────────────────

    async def _ac_all(self, interaction: discord.Interaction, current: str) -> List[app_commands.Choice]:
        if not interaction.guild:
            return []
        surveys = await self.store.all_surveys(interaction.guild)
        cur = current.lower()
        out = []
        for slug, s in surveys.items():
            if cur in slug.lower() or cur in s.get("title", "").lower():
                out.append(app_commands.Choice(name=f"{slug} · {s.get('title','')[:70]}", value=slug))
        return out[:25]

    async def _ac_open(self, interaction: discord.Interaction, current: str) -> List[app_commands.Choice]:
        if not interaction.guild:
            return []
        surveys = await self.store.all_surveys(interaction.guild)
        cur = current.lower()
        out = []
        for slug, s in surveys.items():
            if s.get("status") != "open":
                continue
            if cur in slug.lower() or cur in s.get("title", "").lower():
                out.append(app_commands.Choice(name=f"{slug} · {s.get('title','')[:70]}", value=slug))
        return out[:25]

    # ─────────────────────────────────────────────────────────────────────────
    # SETUP-BEFEHLE (Admin)
    # ─────────────────────────────────────────────────────────────────────────

    @commands.hybrid_group(name="muhfrage-setup", description="Muhfrage-Einstellungen (nur Admins)")
    @commands.guild_only()
    @commands.has_permissions(manage_guild=True)
    @app_commands.default_permissions(manage_guild=True)
    async def setup_group(self, ctx: commands.Context) -> None:
        if ctx.invoked_subcommand is None:
            await ctx.send_help(ctx.command)

    @setup_group.command(name="rolle", description="Setzt die Rolle, die Umfragen verwalten darf")
    @commands.has_permissions(manage_guild=True)
    async def setup_rolle(self, ctx: commands.Context, rolle: Optional[discord.Role] = None) -> None:
        await self.store.set_manager_role_id(ctx.guild, rolle.id if rolle else None)
        if rolle:
            await self._reply(ctx, f"✅ Umfragen dürfen jetzt von {rolle.mention} verwaltet werden.")
        else:
            await self._reply(ctx, "✅ Manager-Rolle entfernt. Nur noch Admins verwalten Umfragen.")

    @setup_group.command(name="kanal", description="Setzt den Standard-Kanal für veröffentlichte Ergebnisse")
    @commands.has_permissions(manage_guild=True)
    async def setup_kanal(self, ctx: commands.Context, kanal: Optional[discord.TextChannel] = None) -> None:
        await self.store.set_results_channel_id(ctx.guild, kanal.id if kanal else None)
        if kanal:
            await self._reply(ctx, f"✅ Ergebnisse werden standardmäßig in {kanal.mention} veröffentlicht.")
        else:
            await self._reply(ctx, "✅ Standard-Ergebnis-Kanal entfernt.")

    @setup_group.command(name="info", description="Zeigt die aktuellen Einstellungen")
    @commands.has_permissions(manage_guild=True)
    async def setup_info(self, ctx: commands.Context) -> None:
        role_id = await self.store.get_manager_role_id(ctx.guild)
        chan_id = await self.store.get_results_channel_id(ctx.guild)
        role = ctx.guild.get_role(role_id) if role_id else None
        chan = ctx.guild.get_channel(chan_id) if chan_id else None
        embed = discord.Embed(title="⚙️ Muhfrage-Einstellungen", color=views.COLOR_INFO)
        embed.add_field(name="Manager-Rolle", value=role.mention if role else "_(nur Admins)_", inline=False)
        embed.add_field(name="Ergebnis-Kanal", value=chan.mention if chan else "_(Kanal des Befehls)_", inline=False)
        await self._reply(ctx, embed=embed)

    # ─────────────────────────────────────────────────────────────────────────
    # HAUPT-BEFEHLE
    # ─────────────────────────────────────────────────────────────────────────

    @commands.hybrid_group(name="muhfrage", description="Umfragen & Abstimmungen")
    @commands.guild_only()
    async def muhfrage(self, ctx: commands.Context) -> None:
        if ctx.invoked_subcommand is None:
            await ctx.send_help(ctx.command)

    # ── erstellen ─────────────────────────────────────────────────────────────

    @muhfrage.command(name="erstellen", description="Erstellt eine neue Umfrage (Builder)")
    @is_manager()
    async def muhfrage_erstellen(self, ctx: commands.Context, name: Optional[str] = None) -> None:
        if not ctx.interaction:
            await self._reply(ctx, "❌ Bitte nutze den Slash-Befehl `/muhfrage erstellen`.")
            return
        await ctx.interaction.response.send_modal(TitleModal(partial(self._finish_create, name)))

    async def _finish_create(self, name: Optional[str], interaction: discord.Interaction,
                             title: str, desc: str) -> None:
        # Slug ermitteln
        if name:
            slug = self.store.normalize_slug(name)
            if not slug or await self.store.slug_exists(interaction.guild, slug):
                await interaction.response.send_message(
                    "❌ Name ungültig oder bereits vergeben.", ephemeral=True)
                return
        else:
            slug = await self.store.generate_slug(interaction.guild)

        survey = models.new_survey(slug, title, desc, interaction.user.id,
                                   datetime.now(timezone.utc).isoformat())
        await self.store.save_survey(interaction.guild, survey)
        panel = BuilderPanel(self, interaction.guild, survey)
        await panel.start(interaction)

    # ── vorlage ───────────────────────────────────────────────────────────────

    @muhfrage.command(name="vorlage", description="Erstellt eine Umfrage aus einer Vorlage")
    @app_commands.describe(typ="Welche Vorlage?", name="Optionaler Name/ID der Umfrage")
    @app_commands.choices(typ=[
        app_commands.Choice(name=tpl["name"], value=key)
        for key, tpl in templates.TEMPLATES.items()
    ])
    @is_manager()
    async def muhfrage_vorlage(self, ctx: commands.Context, typ: str, name: Optional[str] = None) -> None:
        if not ctx.interaction:
            await self._reply(ctx, "❌ Bitte nutze den Slash-Befehl `/muhfrage vorlage`.")
            return
        if typ not in templates.TEMPLATES:
            await self._reply(ctx, "❌ Unbekannte Vorlage.")
            return
        slug = await self._resolve_new_slug(ctx, name)
        if slug is None:
            return
        survey = models.new_survey(slug, "", "", ctx.author.id,
                                   datetime.now(timezone.utc).isoformat())
        templates.apply_template(survey, typ)
        await self.store.save_survey(ctx.guild, survey)
        panel = BuilderPanel(self, ctx.guild, survey)
        await panel.start(ctx.interaction)

    # ── teilnehmer (Allowlist) ────────────────────────────────────────────────

    @muhfrage.command(name="teilnehmer", description="Legt fest, wer teilnehmen darf")
    @is_manager()
    async def muhfrage_teilnehmer(self, ctx: commands.Context, id: str) -> None:
        if not ctx.interaction:
            await self._reply(ctx, "❌ Bitte nutze den Slash-Befehl `/muhfrage teilnehmer`.")
            return
        survey = await self._get_survey(ctx, id)
        if not survey:
            return
        panel = BuilderPanel(self, ctx.guild, survey)
        panel.mode = "allow"
        await panel.start(ctx.interaction)

    # ── starten ───────────────────────────────────────────────────────────────

    @muhfrage.command(name="starten", description="Veröffentlicht eine Umfrage")
    @is_manager()
    async def muhfrage_starten(self, ctx: commands.Context, id: str,
                               kanal: Optional[discord.TextChannel] = None) -> None:
        survey = await self._get_survey(ctx, id)
        if not survey:
            return
        if not survey["questions"]:
            await self._reply(ctx, "❌ Diese Umfrage hat noch keine Fragen.")
            return
        if survey["status"] == "open":
            await self._reply(ctx, "ℹ️ Diese Umfrage läuft bereits.")
            return

        channel = kanal or ctx.channel
        survey["status"] = "open"
        count = await self.store.response_count(ctx.guild, id)
        try:
            msg = await channel.send(embed=views.build_public_embed(survey, count),
                                     view=views.build_join_view(survey))
        except discord.Forbidden:
            await self._reply(ctx, f"❌ Ich darf in {channel.mention} nicht schreiben.")
            return
        survey["published"] = {"channel_id": channel.id, "message_id": msg.id}
        await self.store.save_survey(ctx.guild, survey)
        await self._reply(ctx, f"✅ Umfrage `{id}` in {channel.mention} gestartet.")

    # ── beenden ───────────────────────────────────────────────────────────────

    @muhfrage.command(name="beenden", description="Beendet eine Umfrage und wertet sie aus")
    @is_manager()
    async def muhfrage_beenden(self, ctx: commands.Context, id: str) -> None:
        survey = await self._get_survey(ctx, id)
        if not survey:
            return
        if survey["status"] == "closed":
            await self._reply(ctx, "ℹ️ Diese Umfrage ist bereits beendet.")
            return
        survey["status"] = "closed"
        await self.store.save_survey(ctx.guild, survey)
        await self.update_public_message(ctx.guild, id)
        await self._deliver_results(ctx, survey)

    # ── ergebnisse ────────────────────────────────────────────────────────────

    @muhfrage.command(name="ergebnisse", description="Zeigt die aktuellen Ergebnisse")
    @is_manager()
    async def muhfrage_ergebnisse(self, ctx: commands.Context, id: str) -> None:
        survey = await self._get_survey(ctx, id)
        if not survey:
            return
        responses = await self.store.get_responses(ctx.guild, id)
        embed = results.build_results_embed(survey, responses)
        await self._reply(ctx, embed=embed)

    # ── export ────────────────────────────────────────────────────────────────

    @muhfrage.command(name="export", description="Exportiert die Rohdaten (TXT/CSV)")
    @is_manager()
    async def muhfrage_export(self, ctx: commands.Context, id: str) -> None:
        survey = await self._get_survey(ctx, id)
        if not survey:
            return
        responses = await self.store.get_responses(ctx.guild, id)
        if not responses:
            await self._reply(ctx, "ℹ️ Es liegen noch keine Antworten vor.")
            return
        files = results.export_files(survey, responses, ctx.guild)
        anon = " (anonymisiert)" if survey.get("anonymous") else ""
        await self._reply(ctx, f"📎 Export für `{id}`{anon}:", files=files)

    # ── liste ─────────────────────────────────────────────────────────────────

    @muhfrage.command(name="liste", description="Listet alle Umfragen")
    @is_manager()
    async def muhfrage_liste(self, ctx: commands.Context) -> None:
        surveys = await self.store.all_surveys(ctx.guild)
        counts = {slug: await self.store.response_count(ctx.guild, slug) for slug in surveys}
        await self._reply(ctx, embed=views.build_list_embed(surveys, counts))

    # ── bearbeiten ────────────────────────────────────────────────────────────

    @muhfrage.command(name="bearbeiten", description="Bearbeitet eine Umfrage im Builder")
    @is_manager()
    async def muhfrage_bearbeiten(self, ctx: commands.Context, id: str) -> None:
        if not ctx.interaction:
            await self._reply(ctx, "❌ Bitte nutze den Slash-Befehl `/muhfrage bearbeiten`.")
            return
        survey = await self._get_survey(ctx, id)
        if not survey:
            return
        if survey["status"] != "draft":
            await self._reply(ctx, "⚠️ Nur Entwürfe können vollständig bearbeitet werden. "
                                   "Nutze `/muhfrage teilnehmer` für die Allowlist.")
            return
        panel = BuilderPanel(self, ctx.guild, survey)
        await panel.start(ctx.interaction)

    # ── loeschen ──────────────────────────────────────────────────────────────

    @muhfrage.command(name="loeschen", description="Löscht eine Umfrage endgültig")
    @is_manager()
    async def muhfrage_loeschen(self, ctx: commands.Context, id: str) -> None:
        survey = await self._get_survey(ctx, id)
        if not survey:
            return
        view = _ConfirmDeleteView(self, ctx.guild, id)
        content = f"⚠️ Umfrage `{id}` (**{survey['title']}**) wirklich unwiderruflich löschen?"
        if ctx.interaction:
            await ctx.interaction.response.send_message(content, view=view, ephemeral=True)
        else:
            await ctx.send(content, view=view)

    # ── teilnehmen (öffentlich) ────────────────────────────────────────────────

    @muhfrage.command(name="teilnehmen", description="An einer Umfrage teilnehmen")
    async def muhfrage_teilnehmen(self, ctx: commands.Context, id: str) -> None:
        if not ctx.interaction:
            await self._reply(ctx, "❌ Bitte nutze den Slash-Befehl `/muhfrage teilnehmen`.")
            return
        await self._begin_participation(ctx.interaction, id)

    # ─────────────────────────────────────────────────────────────────────────
    # TEILNAHME-LOGIK
    # ─────────────────────────────────────────────────────────────────────────

    async def _begin_participation(self, interaction: discord.Interaction, slug: str) -> None:
        survey = await self.store.get_survey(interaction.guild, slug)
        if not survey:
            await interaction.response.send_message(
                f"❌ Keine Umfrage mit der ID `{slug}` gefunden.", ephemeral=True)
            return
        if survey["status"] != "open":
            await interaction.response.send_message(
                "❌ Diese Umfrage nimmt derzeit keine Antworten entgegen.", ephemeral=True)
            return
        member = interaction.user
        if not self.store.may_participate(survey, member):
            await interaction.response.send_message(
                "🔒 Du bist für diese Umfrage nicht berechtigt.", ephemeral=True)
            return
        already = await self.store.has_responded(interaction.guild, slug, member.id)
        if already and not survey.get("allow_change"):
            await interaction.response.send_message(
                "ℹ️ Du hast bereits abgestimmt und kannst deine Antwort nicht mehr ändern.", ephemeral=True)
            return
        existing = None
        if already and survey.get("allow_change"):
            responses = await self.store.get_responses(interaction.guild, slug)
            existing = responses.get(str(member.id))
        view = ParticipationView(self, interaction.guild, survey, member, existing)
        await view.start(interaction)

    # ─────────────────────────────────────────────────────────────────────────
    # INTERACTION-ROUTER  (persistenter Teilnehmen-Button)
    # ─────────────────────────────────────────────────────────────────────────

    @commands.Cog.listener("on_interaction")
    async def on_interaction(self, interaction: discord.Interaction) -> None:
        if interaction.type != discord.InteractionType.component:
            return
        custom_id = (interaction.data or {}).get("custom_id", "")
        prefix = views.JOIN_PREFIX + ":"
        if not custom_id.startswith(prefix):
            return
        slug = custom_id[len(prefix):]
        await self._begin_participation(interaction, slug)

    # ─────────────────────────────────────────────────────────────────────────
    # ZENTRALER FEHLER-HANDLER
    # ─────────────────────────────────────────────────────────────────────────

    async def cog_command_error(self, ctx: commands.Context, error) -> None:
        error = getattr(error, "original", error)
        if isinstance(error, commands.MissingPermissions):
            await self._reply(ctx, "❌ Du benötigst die Berechtigung **Server verwalten**.")
            return
        if isinstance(error, commands.CheckFailure):
            role_id = await self.store.get_manager_role_id(ctx.guild) if ctx.guild else None
            hinweis = (f" (Rolle <@&{role_id}> oder **Server verwalten**)"
                       if role_id else " (**Server verwalten**)")
            await self._reply(ctx, f"❌ Dir fehlt die Berechtigung, Umfragen zu verwalten.{hinweis}")
            return
        if isinstance(error, commands.NoPrivateMessage):
            await self._reply(ctx, "❌ Dieser Befehl funktioniert nur auf einem Server.")
            return
        log.exception("Fehler in Muhfrage-Befehl", exc_info=error)
        try:
            await self._reply(ctx, "❌ Ein unerwarteter Fehler ist aufgetreten.")
        except discord.DiscordException:
            pass


class _ConfirmDeleteView(discord.ui.View):
    def __init__(self, cog: Muhfrage, guild: discord.Guild, slug: str):
        super().__init__(timeout=60)
        self.cog = cog
        self.guild = guild
        self.slug = slug

    @discord.ui.button(label="Löschen", style=discord.ButtonStyle.danger, emoji="🗑️")
    async def confirm(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await self.cog.store.delete_survey(self.guild, self.slug)
        self.clear_items()
        await interaction.response.edit_message(content=f"🗑️ Umfrage `{self.slug}` gelöscht.", view=None)
        self.stop()

    @discord.ui.button(label="Abbrechen", style=discord.ButtonStyle.secondary)
    async def cancel(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        self.clear_items()
        await interaction.response.edit_message(content="Abgebrochen.", view=None)
        self.stop()
