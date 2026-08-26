"""
cog.py – Haupt-Cog des RO Group Finders

Commands:
  /gruppe erstellen           → Startet den Wizard (nur im konfigurierten Channel)
  /gruppe suchen [klasse]     → Zeigt offene Gruppen die eine Klasse suchen
  /gruppe liste               → Zeigt alle aktiven Gruppen (Ephemeral)
  /gruppe channel #channel   → Setzt den erlaubten Gruppen-Channel (Admin)
  /gruppe info               → Zeigt aktuelle Servereinstellungen (Admin)
  /gruppe erinnerung [min]   → Stellt ein wann Erinnerungen gesendet werden (Admin)
  /gruppe cleanup [tage]     → Stellt die Ablaufzeit für Gruppen ein (Admin)
  /gruppe timeout [min]      → Stellt den Wartelisten-Timeout ein (Admin)

Persistent Interaction Handlers (Button/Select custom_id):
  group_join:<msg_id>               → Beitretens-Flow starten
  group_leave:<msg_id>              → Verlassens-Flow
  group_manage:<msg_id>             → Verwaltungsmenü öffnen
  join_slot_select:<msg_id>:<uid>   → Slot-Auswahl beim Beitreten
  join_class_select:<msg_id>:<uid>  → Klassen-Auswahl beim Beitreten
  join_confirm:<msg_id>:<uid>       → Beitritt bestätigen (öffnet Modal für Namen)
  join_cancel:<msg_id>:<uid>        → Beitritts-Flow abbrechen
  manage_members:<msg_id>           → Mitglieder-Verwaltung
  manage_edit:<msg_id>              → Bearbeitungs-Menü
  manage_transfer:<msg_id>          → Führung übergeben (Mitglied auswählen)
  manage_transfer_select:<msg_id>   → Neuen Gruppenführer bestätigen
  manage_delete:<msg_id>            → Gruppe löschen (mit Bestätigung)
  manage_remove_member:<msg_id>     → Spieler entfernen (Select-Callback)
  manage_back:<msg_id>              → Zurück zum Hauptmenü
  manage_delete_confirm:<msg_id>    → Löschung bestätigen
  manage_delete_cancel:<msg_id>     → Löschung abbrechen
  edit_select:<msg_id>              → Bearbeitungs-Option ausgewählt
"""

import discord
from discord import app_commands, ui
from redbot.core import commands
from typing import Optional, Dict
from datetime import datetime, timedelta, timezone

from .wizard import WizardSession, WizardState, build_state_from_group
from .data_manager import (
    get_group_channel,
    set_group_channel,
    set_forum_channel,
    set_guild_setting,
    get_guild_settings,
    get_guild_groups,
    get_group_by_message,
    save_group,
    delete_group,
    fill_slot,
    clear_slot,
    find_user_slot,
    get_open_slots,
    is_user_in_group,
    is_user_in_waitlist,
    add_to_waitlist,
    remove_from_waitlist,
    create_group,
    set_group_message_id,
    set_group_leader,
    update_group_fields,
    touch_group_activity,
    reset_slots,
    get_expired_snapshot,
    delete_expired_snapshot,
    find_group_by_public_id,
    parse_local_input,
    is_valid_timezone,
    load_goals,
    load_classes,
)
from .group_embed import (
    build_group_embed,
    build_group_action_view,
    build_manage_view,
    build_manage_members_view,
    build_edit_view,
    build_join_slot_view,
    build_transfer_view,
)
from .notifications import (
    notify_creator_join,
    notify_creator_leave,
    notify_creator_removed,
    notify_player_removed,
    notify_group_full,
    notify_group_deleted,
    notify_waitlist_joined,
    notify_edit,
)
from .scheduler import GroupScheduler
from .forum import (
    create_forum_post,
    close_forum_post,
    notify_join_in_forum,
    notify_leave_in_forum,
    notify_leader_change_in_forum,
)
from .constants import (
    COLOR_OPEN, COLOR_CLOSED, RECURRENCE_OPTIONS, ROLE_TYPES,
    DEFAULT_CLEANUP_DAYS, DEFAULT_REMINDER_MINUTES, DEFAULT_WAITLIST_TIMEOUT_MINUTES,
)


# ─────────────────────────────────────────────────────────────────────────────
# ACTIVE JOIN SESSIONS
# In-Memory: speichert den Status eines laufenden Beitritts-Flows
# Key: f"{guild_id}:{user_id}"
# ─────────────────────────────────────────────────────────────────────────────
_join_sessions: Dict[str, Dict] = {}


# ─────────────────────────────────────────────────────────────────────────────
# ACTIVE WIZARD SESSIONS
# Key: user_id (int)
# ─────────────────────────────────────────────────────────────────────────────
_wizard_sessions: Dict[int, WizardSession] = {}


# ─────────────────────────────────────────────────────────────────────────────
# HAUPT-COG
# ─────────────────────────────────────────────────────────────────────────────

class ROGroupFinder(commands.Cog):
    """RO Group Finder – Gruppensuche für Ragnarok Zero: Global"""

    def __init__(self, bot: commands.Bot):
        self.bot       = bot
        self.scheduler = GroupScheduler(bot)

    # ── Lifecycle ─────────────────────────────────────────────────────────────

    async def cog_load(self) -> None:
        self.scheduler.start()

    async def cog_unload(self) -> None:
        self.scheduler.stop()

    # ─────────────────────────────────────────────────────────────────────────
    # COMMANDS  (hybrid = Prefix UND Slash)
    # ─────────────────────────────────────────────────────────────────────────

    # ── /gruppe ───────────────────────────────────────────────────────────────

    @commands.hybrid_group(name="gruppe", description="RO Gruppen-System")
    @commands.guild_only()
    async def gruppe(self, ctx: commands.Context) -> None:
        if ctx.invoked_subcommand is None:
            await ctx.send_help(ctx.command)

    @gruppe.command(name="erstellen", description="Erstelle eine neue Gruppenanfrage")
    @commands.guild_only()
    async def gruppe_erstellen(self, ctx: commands.Context) -> None:
        if not ctx.interaction:
            await ctx.send("\u274c Dieser Befehl funktioniert nur als Slash-Command: `/gruppe erstellen`")
            return

        interaction = ctx.interaction
        guild_id    = interaction.guild_id
        channel_id  = interaction.channel_id
        allowed_ch  = get_group_channel(guild_id)

        if allowed_ch and channel_id != allowed_ch:
            ch      = interaction.guild.get_channel(allowed_ch)
            mention = ch.mention if ch else f"<#{allowed_ch}>"
            await interaction.response.send_message(
                f"\u274c Gruppen k\u00f6nnen nur in {mention} erstellt werden.",
                ephemeral=True,
            )
            return

        if interaction.user.id in _wizard_sessions:
            del _wizard_sessions[interaction.user.id]

        state = WizardState(
            guild_id     = guild_id,
            channel_id   = channel_id,
            creator_id   = interaction.user.id,
            creator_name = interaction.user.display_name,
        )
        session = WizardSession(
            state       = state,
            on_complete = self._wizard_complete,
            on_cancel   = self._wizard_cancel,
        )
        _wizard_sessions[interaction.user.id] = session
        await session.start(interaction)

    async def _wizard_complete(
        self, interaction: discord.Interaction, state: WizardState
    ) -> None:
        # Nutzereingabe gilt als lokale Guild-Zeit → wird als UTC gespeichert.
        dt = parse_local_input(state.dt_str, state.guild_id) if state.dt_str else None

        group = create_group(
            guild_id       = state.guild_id,
            channel_id     = state.channel_id,
            creator_id     = state.creator_id,
            creator_name   = state.creator_name,
            creator_ingame = next(
                (m["ingame_name"] for m in state.prefilled_members if m["is_creator"]), None
            ),
            goal           = state.goal_key or "",
            goal_custom    = state.goal_custom,
            player_count   = state.player_count,
            slots          = state.expand_slots(),
            dt             = dt,
            recurrence     = state.recurrence,
            comment        = state.comment,
            level_min      = state.level_min,
            level_max      = state.level_max,
        )
        group["level_mode"] = state.level_mode

        for member in state.prefilled_members:
            slot_idx = member["slot_index"]
            slots    = group["slots"]
            if slot_idx < len(slots):
                slot = slots[slot_idx]
                slot["filled_by_id"]     = member.get("user_id") or state.creator_id
                slot["filled_by_name"]   = state.creator_name if member["is_creator"] else member["ingame_name"]
                slot["filled_by_ingame"] = member["ingame_name"]
                slot["filled_class"]     = slot["display_name"]
                slot["filled_emoji"]     = slot["emoji"]

        channel = interaction.guild.get_channel(state.channel_id)
        if not channel:
            await interaction.response.send_message(
                "\u274c Der Gruppen-Channel konnte nicht gefunden werden.", ephemeral=True
            )
            return

        group["message_id"] = 0
        message = await channel.send(embed=build_group_embed(group), view=build_group_action_view(group))
        set_group_message_id(group, message.id)
        save_group(state.guild_id, group)

        # Forum-Diskussionspost erstellen (best effort) und Embed mit Link aktualisieren.
        await create_forum_post(self.bot, group)
        save_group(state.guild_id, group)
        await message.edit(embed=build_group_embed(group), view=build_group_action_view(group))

        if interaction.user.id in _wizard_sessions:
            del _wizard_sessions[interaction.user.id]

        await interaction.response.edit_message(
            content="\u2705 **Deine Gruppenanfrage wurde gepostet!**",
            embed=None,
            view=None,
        )

    async def _wizard_cancel(self, interaction: discord.Interaction) -> None:
        if interaction.user.id in _wizard_sessions:
            del _wizard_sessions[interaction.user.id]
        await interaction.response.edit_message(
            content="\u274c Gruppen-Erstellung abgebrochen.",
            embed=None,
            view=None,
        )

    @gruppe.command(name="suchen", description="Suche offene Gruppen nach Klasse/Rolle")
    @commands.guild_only()
    async def gruppe_suchen(self, ctx: commands.Context, klasse: Optional[str] = None) -> None:
        groups  = get_guild_groups(ctx.guild.id)
        results = []

        for group in groups.values():
            if group.get("status") not in ("open", "full"):
                continue
            if klasse:
                kl_lower   = klasse.lower()
                open_slots = get_open_slots(group)
                match = any(
                    kl_lower in s.get("display_name", "").lower() or
                    kl_lower in (s.get("class_key") or "").lower() or
                    kl_lower in (s.get("role_key") or "").lower()
                    for s in open_slots
                )
                if not match:
                    continue
            results.append(group)

        term = f" f\u00fcr **{klasse}**" if klasse else ""
        if not results:
            await self._reply(ctx, f"Keine offenen Gruppen{term} gefunden.")
            return

        embeds = [build_group_embed(g) for g in results[:5]]
        await self._reply(ctx, f"**{len(results)} Gruppe(n) gefunden:**", embeds=embeds)

    @gruppe.command(name="liste", description="Zeige alle aktiven Gruppen")
    @commands.guild_only()
    async def gruppe_liste(self, ctx: commands.Context) -> None:
        groups = get_guild_groups(ctx.guild.id)
        active = [g for g in groups.values() if g.get("status") in ("open", "full")]

        if not active:
            await self._reply(ctx, "Es gibt derzeit keine aktiven Gruppen.")
            return

        lines = []
        for g in active[:20]:
            filled  = sum(1 for s in g.get("slots", []) if s.get("filled_by_id"))
            total   = g.get("player_count", "?")
            goal    = g.get("goal_custom") or g.get("goal") or "?"
            creator = g.get("creator_name", "?")
            icon    = "\U0001f7e2" if g.get("status") == "open" else "\U0001f7e1"
            lines.append(f"{icon} **{goal}** \u2013 {creator} ({filled}/{total})")

        embed = discord.Embed(
            title=f"\u2694\ufe0f Aktive Gruppen ({len(active)})",
            description="\n".join(lines),
            color=COLOR_OPEN,
        )
        await self._reply(ctx, embed=embed)

    @gruppe.command(name="kopieren", description="Erstelle eine Gruppe per ID erneut (mit Bearbeitung vor dem Posten)")
    @commands.guild_only()
    async def gruppe_kopieren(self, ctx: commands.Context, gruppen_id: str) -> None:
        if not ctx.interaction:
            await ctx.send("❌ Dieser Befehl funktioniert nur als Slash-Command: `/gruppe kopieren`")
            return

        interaction = ctx.interaction
        guild_id    = interaction.guild_id

        source = find_group_by_public_id(guild_id, gruppen_id)
        if not source:
            await interaction.response.send_message(
                f"❌ Keine Gruppe mit der ID **{gruppen_id}** gefunden.\n"
                f"Die ID findest du im Fußzeilentext des Gruppen-Posts (z.B. `ID: a1b2c3d4`).",
                ephemeral=True,
            )
            return

        # Ziel-Channel: konfigurierter Gruppen-Channel, sonst der der Quell-Gruppe
        channel_id = get_group_channel(guild_id) or source.get("channel_id")
        if not channel_id or not interaction.guild.get_channel(channel_id):
            await interaction.response.send_message(
                "❌ Der Gruppen-Channel konnte nicht gefunden werden. "
                "Bitte lege ihn mit `/gruppe-setup channel` fest.",
                ephemeral=True,
            )
            return

        if interaction.user.id in _wizard_sessions:
            del _wizard_sessions[interaction.user.id]

        state = build_state_from_group(
            source,
            guild_id     = guild_id,
            channel_id   = channel_id,
            creator_id   = interaction.user.id,
            creator_name = interaction.user.display_name,
        )
        session = WizardSession(
            state       = state,
            on_complete = self._wizard_complete,
            on_cancel   = self._wizard_cancel,
        )
        _wizard_sessions[interaction.user.id] = session
        await session.start(interaction)

    # ── Admin-Befehle /gruppe-setup ────────────────────
    @commands.hybrid_group(name="gruppe-setup", description="RO Gruppen-Einstellungen (nur Admins)")
    @commands.guild_only()
    @commands.admin()
    @app_commands.default_permissions(manage_guild=True)
    async def gruppe_setup(self, ctx: commands.Context) -> None:
        if ctx.invoked_subcommand is None:
            await ctx.send_help(ctx.command)

    @gruppe_setup.command(name="channel", description="Legt den Channel f\u00fcr Gruppenanfragen fest")
    @commands.guild_only()
    @commands.admin()
    async def gruppe_config_channel(self, ctx: commands.Context, channel: discord.TextChannel) -> None:
        set_group_channel(ctx.guild.id, channel.id)

        note = ""
        if get_guild_settings(ctx.guild.id).get("readonly_enforced"):
            ok = await self._apply_readonly(channel)
            note = (
                "\n\U0001f512 Der Channel wurde **read-only** gesetzt \u2013 normale User "
                "k\u00f6nnen keine Nachrichten mehr schreiben, die `/gruppe`-Befehle bleiben nutzbar."
                if ok else
                "\n\u26a0\ufe0f Read-only konnte nicht gesetzt werden (fehlende Rechte?). "
                "Bitte pr\u00fcfe die Berechtigungen des Bots."
            )
        await self._reply(ctx, f"\u2705 Gruppen-Channel auf {channel.mention} gesetzt.{note}")

    @gruppe_setup.command(name="forum", description="Legt den Forum-Channel f\u00fcr Diskussionsposts fest")
    @commands.guild_only()
    @commands.admin()
    async def gruppe_config_forum(self, ctx: commands.Context, forum: discord.ForumChannel) -> None:
        set_forum_channel(ctx.guild.id, forum.id)
        await self._reply(
            ctx,
            f"\u2705 Forum-Channel auf {forum.mention} gesetzt. F\u00fcr jede neue Gruppe wird "
            f"dort automatisch ein Diskussionsbeitrag erstellt.",
        )

    @gruppe_setup.command(name="forumschliessen", description="Tage nach Abschluss bis der Forum-Post geschlossen wird")
    @commands.guild_only()
    @commands.admin()
    async def gruppe_config_forumschliessen(self, ctx: commands.Context, tage: int) -> None:
        if tage < 1 or tage > 90:
            await self._reply(ctx, "\u274c Wert muss zwischen 1 und 90 Tagen liegen.")
            return
        set_guild_setting(ctx.guild.id, "forum_close_days", tage)
        await self._reply(
            ctx,
            f"\u2705 Forum-Posts werden **{tage} Tage** nach Abschluss der Gruppe geschlossen "
            f"(nicht gel\u00f6scht).",
        )

    @gruppe_setup.command(name="readonly", description="Read-only-Modus f\u00fcr den Gruppen-Channel an/aus")
    @commands.guild_only()
    @commands.admin()
    async def gruppe_config_readonly(self, ctx: commands.Context, status: str) -> None:
        val = status.strip().lower()
        if val in ("an", "on", "true", "ja", "1"):
            enabled = True
        elif val in ("aus", "off", "false", "nein", "0"):
            enabled = False
        else:
            await self._reply(ctx, "\u274c Bitte **an** oder **aus** angeben.")
            return

        set_guild_setting(ctx.guild.id, "readonly_enforced", enabled)

        channel_id = get_group_channel(ctx.guild.id)
        channel    = ctx.guild.get_channel(channel_id) if channel_id else None
        applied    = None
        if isinstance(channel, discord.TextChannel):
            applied = await (self._apply_readonly(channel) if enabled else self._clear_readonly(channel))

        if enabled:
            extra = f" {channel.mention} ist jetzt read-only." if applied else ""
            await self._reply(ctx, f"\u2705 Read-only-Modus **aktiviert**.{extra}")
        else:
            extra = f" Die Schreibsperre in {channel.mention} wurde aufgehoben." if applied else ""
            await self._reply(ctx, f"\u2705 Read-only-Modus **deaktiviert**.{extra}")

    @gruppe_setup.command(name="info", description="Zeigt die aktuelle Konfiguration")
    @commands.guild_only()
    @commands.admin()
    async def gruppe_config_info(self, ctx: commands.Context) -> None:
        s     = get_guild_settings(ctx.guild.id)
        ch    = ctx.guild.get_channel(s.get("group_channel_id") or 0)
        forum = ctx.guild.get_channel(s.get("forum_channel_id") or 0)

        embed = discord.Embed(title="\u2699\ufe0f RO Group Finder \u2013 Konfiguration", color=COLOR_OPEN)
        embed.add_field(name="\U0001f4cc Gruppen-Channel",    value=ch.mention if ch else "Nicht gesetzt", inline=False)
        embed.add_field(name="\U0001f4ac Forum-Channel",      value=forum.mention if forum else "Nicht gesetzt", inline=False)
        embed.add_field(name="\U0001f512 Read-only",          value="An" if s.get("readonly_enforced") else "Aus", inline=True)
        embed.add_field(name="\U0001f4d5 Forum schlie\u00dfen", value=f"Nach {s['forum_close_days']} Tagen (Abschluss)", inline=True)
        embed.add_field(name="\u23f0 Erinnerung",              value=f"{s['reminder_minutes']} Min. vor Start",  inline=True)
        embed.add_field(name="\U0001f5d1\ufe0f Cleanup",      value=f"Nach {s['cleanup_days']} Tagen Inaktivit\u00e4t", inline=True)
        embed.add_field(name="\u26a0\ufe0f Vorwarnung",        value=f"{s['warning_days']} Tage vor Ablauf",      inline=True)
        embed.add_field(name="\u23f3 Wartelisten-Timeout",     value=f"{s['waitlist_timeout_minutes']} Minuten",  inline=True)
        embed.add_field(name="\ud83c\udf0d Zeitzone",                value=s.get("timezone", "Europe/Berlin"),          inline=True)
        await self._reply(ctx, embed=embed)

    @gruppe_setup.command(name="erinnerung", description="Stellt ein wann Erinnerungen gesendet werden")
    @commands.guild_only()
    @commands.admin()
    async def gruppe_config_erinnerung(self, ctx: commands.Context, minuten: int) -> None:
        if minuten < 5 or minuten > 1440:
            await self._reply(ctx, "\u274c Wert muss zwischen 5 und 1440 Minuten liegen.")
            return
        set_guild_setting(ctx.guild.id, "reminder_minutes", minuten)
        await self._reply(ctx, f"\u2705 Erinnerungen werden **{minuten} Minuten** vor Start gesendet.")

    @gruppe_setup.command(name="cleanup", description="Stellt die Ablaufzeit f\u00fcr inaktive Gruppen ein")
    @commands.guild_only()
    @commands.admin()
    async def gruppe_config_cleanup(self, ctx: commands.Context, tage: int) -> None:
        if tage < 1 or tage > 90:
            await self._reply(ctx, "\u274c Wert muss zwischen 1 und 90 Tagen liegen.")
            return
        set_guild_setting(ctx.guild.id, "cleanup_days", tage)
        await self._reply(ctx, f"\u2705 Gruppen laufen nach **{tage} Tagen** Inaktivit\u00e4t ab.")

    @gruppe_setup.command(name="warnung", description="Stellt ein wie viele Tage vor Ablauf der Ersteller gewarnt wird")
    @commands.guild_only()
    @commands.admin()
    async def gruppe_config_warnung(self, ctx: commands.Context, tage: int) -> None:
        settings = get_guild_settings(ctx.guild.id)
        if tage < 1 or tage >= settings["cleanup_days"]:
            await self._reply(
                ctx,
                f"\u274c Wert muss zwischen 1 und {settings['cleanup_days'] - 1} Tagen liegen "
                f"(kleiner als die Ablaufzeit von {settings['cleanup_days']} Tagen).",
            )
            return
        set_guild_setting(ctx.guild.id, "warning_days", tage)
        await self._reply(ctx, f"\u2705 Der Ersteller wird **{tage} Tage** vor Ablauf vorgewarnt.")

    @gruppe_setup.command(name="zeitzone", description="Setzt die Zeitzone f\u00fcr Termine (z.B. Europe/Berlin)")
    @commands.guild_only()
    @commands.admin()
    async def gruppe_config_zeitzone(self, ctx: commands.Context, zeitzone: str) -> None:
        zeitzone = zeitzone.strip()
        if not is_valid_timezone(zeitzone):
            await self._reply(
                ctx,
                "\u274c Unbekannte Zeitzone. Bitte einen **IANA-Namen** angeben, z.B. "
                "`Europe/Berlin`, `Europe/London` oder `America/New_York`.",
            )
            return
        set_guild_setting(ctx.guild.id, "timezone", zeitzone)
        await self._reply(
            ctx,
            f"\u2705 Zeitzone auf **{zeitzone}** gesetzt. Termin-Eingaben werden ab jetzt "
            f"in dieser Zone interpretiert.",
        )

    @gruppe_setup.command(name="timeout", description="Stellt den Wartelisten-Timeout ein")
    @commands.guild_only()
    @commands.admin()
    async def gruppe_config_timeout(self, ctx: commands.Context, minuten: int) -> None:
        if minuten < 5 or minuten > 120:
            await self._reply(ctx, "\u274c Wert muss zwischen 5 und 120 Minuten liegen.")
            return
        set_guild_setting(ctx.guild.id, "waitlist_timeout_minutes", minuten)
        await self._reply(ctx, f"\u2705 Wartelisten-Timeout auf **{minuten} Minuten** gesetzt.")

    @gruppe_config_channel.error
    @gruppe_config_forum.error
    @gruppe_config_forumschliessen.error
    @gruppe_config_readonly.error
    @gruppe_config_info.error
    @gruppe_config_erinnerung.error
    @gruppe_config_cleanup.error
    @gruppe_config_warnung.error
    @gruppe_config_zeitzone.error
    @gruppe_config_timeout.error
    async def admin_error(self, ctx: commands.Context, error) -> None:
        # Reds admin()-Check wirft CheckFailure (nicht MissingPermissions).
        # NoPrivateMessage (guild_only) ist ebenfalls eine CheckFailure-Unterklasse
        # und wird hier absichtlich nicht mit der Admin-Meldung überschrieben.
        if isinstance(error, commands.NoPrivateMessage):
            return
        if isinstance(error, (commands.MissingPermissions, commands.CheckFailure)):
            await self._reply(
                ctx,
                "\u274c Nur die **@Admin-Rolle** (bzw. Server-/Bot-Owner) darf diese "
                "Einstellungen \u00e4ndern.",
            )

    async def _reply(
        self,
        ctx:     commands.Context,
        content: str = "",
        *,
        embed:   Optional[discord.Embed] = None,
        embeds:  Optional[list]          = None,
    ) -> None:
        kwargs: dict = {}
        if content:
            kwargs["content"] = content
        if embed:
            kwargs["embed"] = embed
        if embeds:
            kwargs["embeds"] = embeds
        if ctx.interaction:
            kwargs["ephemeral"] = True
            if ctx.interaction.response.is_done():
                await ctx.interaction.followup.send(**kwargs)
            else:
                await ctx.interaction.response.send_message(**kwargs)
        else:
            await ctx.send(**kwargs)

        # ─────────────────────────────────────────────────────────────────────────
    # INTERACTION HANDLER
    # Alle Button/Select-Callbacks werden über on_interaction geroutet.
    # ─────────────────────────────────────────────────────────────────────────

    @commands.Cog.listener("on_interaction")
    async def on_interaction(self, interaction: discord.Interaction) -> None:
        if interaction.type != discord.InteractionType.component:
            return

        custom_id = interaction.data.get("custom_id", "")
        parts     = custom_id.split(":")

        if len(parts) < 2:
            return

        action  = parts[0]

        # ── DM-Aktionen (Vorwarnung / Ablauf) ─────────────────────────────────
        # Diese Buttons stecken in DMs, daher ist interaction.guild_id None und
        # die guild_id/group_id sind in der custom_id kodiert.
        if action == "group_extend":
            await self._handle_group_extend(interaction, parts)
            return
        if action == "group_recreate":
            await self._handle_group_recreate(interaction, parts)
            return

        msg_id  = int(parts[1]) if parts[1].isdigit() else None

        # ── Dispatcher ────────────────────────────────────────────────────────
        handlers = {
            "group_join":             self._handle_join,
            "group_leave":            self._handle_leave,
            "group_finish":           self._handle_finish,
            "group_manage":           self._handle_manage,
            "join_slot_select":       self._handle_join_slot_select,
            "join_class_select":      self._handle_join_class_select,
            "join_confirm":           self._handle_join_confirm,
            "join_cancel":            self._handle_join_cancel,
            "manage_members":         self._handle_manage_members,
            "manage_edit":            self._handle_manage_edit,
            "manage_transfer":        self._handle_manage_transfer,
            "manage_transfer_select": self._handle_manage_transfer_select,
            "manage_delete":          self._handle_manage_delete,
            "manage_remove_member":   self._handle_manage_remove_member,
            "manage_back":            self._handle_manage_back,
            "manage_delete_confirm":  self._handle_manage_delete_confirm,
            "manage_delete_cancel":   self._handle_manage_delete_cancel,
            "edit_select":            self._handle_edit_select,
        }

        handler = handlers.get(action)
        if handler and msg_id:
            await handler(interaction, msg_id, parts)

    # ─────────────────────────────────────────────────────────────────────────
    # BEITRETEN-FLOW
    # ─────────────────────────────────────────────────────────────────────────

    async def _handle_join(
        self, interaction: discord.Interaction, msg_id: int, parts: list
    ) -> None:
        group = get_group_by_message(interaction.guild_id, msg_id)
        if not group:
            await interaction.response.send_message("❌ Gruppe nicht gefunden.", ephemeral=True)
            return

        user_id = interaction.user.id

        if is_user_in_group(group, user_id):
            await interaction.response.send_message(
                "ℹ️ Du bist bereits in dieser Gruppe.", ephemeral=True
            )
            return

        if is_user_in_waitlist(group, user_id):
            await interaction.response.send_message(
                "ℹ️ Du bist bereits auf der Warteliste.", ephemeral=True
            )
            return

        open_slots = get_open_slots(group)

        if not open_slots:
            # Auf Warteliste setzen
            await interaction.response.send_modal(
                WaitlistJoinModal(group, user_id, self.scheduler)
            )
            return

        # Session anlegen
        session_key = f"{interaction.guild_id}:{user_id}"
        _join_sessions[session_key] = {
            "msg_id":     msg_id,
            "guild_id":   interaction.guild_id,
            "slot_index": open_slots[0]["slot_index"] if len(open_slots) == 1 else None,
            "class_display": None,
            "class_emoji":   None,
        }

        view = build_join_slot_view(group, user_id)
        if not view:
            await interaction.response.send_message("❌ Keine Slots verfügbar.", ephemeral=True)
            return

        embed = discord.Embed(
            title="🟢 Gruppe beitreten",
            description=(
                f"**{group.get('goal_custom') or group.get('goal', '?')}**\n\n"
                "Wähle deinen Slot und deine Klasse aus."
            ),
            color=COLOR_OPEN,
        )
        await interaction.response.send_message(embed=embed, view=view, ephemeral=True)

    async def _handle_join_slot_select(
        self, interaction: discord.Interaction, msg_id: int, parts: list
    ) -> None:
        user_id     = interaction.user.id
        session_key = f"{interaction.guild_id}:{user_id}"
        if session_key not in _join_sessions:
            await interaction.response.defer()
            return

        selected = interaction.data["values"][0]
        _join_sessions[session_key]["slot_index"] = int(selected)
        await interaction.response.defer()

    async def _handle_join_class_select(
        self, interaction: discord.Interaction, msg_id: int, parts: list
    ) -> None:
        user_id     = interaction.user.id
        session_key = f"{interaction.guild_id}:{user_id}"
        if session_key not in _join_sessions:
            await interaction.response.defer()
            return

        value = interaction.data["values"][0]
        # Format: "role:dd" oder "class:dieb"
        vtype, vkey = value.split(":", 1)

        if vtype == "role":
            role = ROLE_TYPES.get(vkey, {})
            _join_sessions[session_key]["class_display"] = role.get("name", vkey)
            _join_sessions[session_key]["class_emoji"]   = role.get("emoji", "❓")
        else:
            classes = load_classes()
            cls = next((c for c in classes if c["key"] == vkey), None)
            if cls:
                _join_sessions[session_key]["class_display"] = cls["name"]
                _join_sessions[session_key]["class_emoji"]   = cls.get("emoji", "⚔️")

        await interaction.response.defer()

    async def _handle_join_confirm(
        self, interaction: discord.Interaction, msg_id: int, parts: list
    ) -> None:
        user_id     = interaction.user.id
        session_key = f"{interaction.guild_id}:{user_id}"
        session     = _join_sessions.get(session_key)

        if not session:
            await interaction.response.send_message("❌ Session abgelaufen.", ephemeral=True)
            return

        if not session.get("class_display"):
            await interaction.response.send_message(
                "⚠️ Bitte wähle erst deine Klasse aus.", ephemeral=True
            )
            return

        await interaction.response.send_modal(JoinConfirmModal(session_key, self))

    async def _handle_join_cancel(
        self, interaction: discord.Interaction, msg_id: int, parts: list
    ) -> None:
        session_key = f"{interaction.guild_id}:{interaction.user.id}"
        _join_sessions.pop(session_key, None)
        await interaction.response.edit_message(
            content="❌ Abgebrochen.", embed=None, view=None
        )

    async def _complete_join(
        self,
        interaction: discord.Interaction,
        session_key: str,
        ingame_name: str,
    ) -> None:
        """Finalisiert den Beitritts-Flow nach Modal-Submit."""
        session = _join_sessions.pop(session_key, None)
        if not session:
            return

        guild_id  = session["guild_id"]
        msg_id    = session["msg_id"]
        group     = get_group_by_message(guild_id, msg_id)

        if not group:
            await interaction.followup.send("❌ Gruppe nicht gefunden.", ephemeral=True)
            return

        slot_index    = session.get("slot_index")
        class_display = session.get("class_display", "?")
        class_emoji   = session.get("class_emoji", "❓")
        user_id       = interaction.user.id
        username      = interaction.user.display_name

        # Wenn kein Slot gewählt → ersten offenen nehmen
        if slot_index is None:
            open_slots = get_open_slots(group)
            if not open_slots:
                await interaction.followup.send("❌ Keine Slots mehr verfügbar.", ephemeral=True)
                return
            slot_index = open_slots[0]["slot_index"]

        success = fill_slot(
            group        = group,
            slot_index   = slot_index,
            user_id      = user_id,
            username     = username,
            ingame_name  = ingame_name,
            filled_class = class_display,
            filled_emoji = class_emoji,
        )

        if not success:
            await interaction.followup.send("❌ Slot ist bereits belegt.", ephemeral=True)
            return

        touch_group_activity(group)
        save_group(guild_id, group)

        # Ersteller benachrichtigen
        await notify_creator_join(
            self.bot, group, ingame_name, class_display, class_emoji, slot_index
        )

        # Beigetretenen Spieler im Forum-Thread pingen (findet so sofort den
        # richtigen Diskussionsbeitrag – auch bei vielen Beiträgen).
        await notify_join_in_forum(
            self.bot, group, user_id,
            ingame_name=ingame_name, class_display=class_display, class_emoji=class_emoji,
        )

        # Embed aktualisieren
        channel = interaction.guild.get_channel(group["channel_id"])
        if channel:
            try:
                message = await channel.fetch_message(msg_id)
                await message.edit(
                    embed=build_group_embed(group),
                    view=build_group_action_view(group),
                )
            except Exception:
                pass

        # Gruppe voll?
        if not get_open_slots(group):
            await notify_group_full(self.bot, group)

        await interaction.followup.send(
            f"✅ Du bist der Gruppe beigetreten als **{class_emoji} {class_display}**!",
            ephemeral=True,
        )

    # ─────────────────────────────────────────────────────────────────────────
    # VERLASSEN
    # ─────────────────────────────────────────────────────────────────────────

    async def _handle_leave(
        self, interaction: discord.Interaction, msg_id: int, parts: list
    ) -> None:
        group = get_group_by_message(interaction.guild_id, msg_id)
        if not group:
            await interaction.response.send_message("❌ Gruppe nicht gefunden.", ephemeral=True)
            return

        user_id = interaction.user.id

        # Gruppenersteller kann nicht verlassen
        if user_id == group.get("creator_id"):
            await interaction.response.send_message(
                "❌ Als Gruppenersteller kannst du die Gruppe nicht verlassen. "
                "Nutze ⚙️ **Verwalten** → 🗑️ **Gruppe löschen** um sie aufzulösen.",
                ephemeral=True,
            )
            return

        # Warteliste zuerst prüfen
        if is_user_in_waitlist(group, user_id):
            remove_from_waitlist(group, user_id)
            touch_group_activity(group)
            save_group(interaction.guild_id, group)
            await interaction.response.send_message(
                "✅ Du wurdest von der Warteliste entfernt.", ephemeral=True
            )
            return

        slot_index = find_user_slot(group, user_id)
        if slot_index is None:
            await interaction.response.send_message(
                "ℹ️ Du bist nicht in dieser Gruppe.", ephemeral=True
            )
            return

        # Bestätigungs-View
        view = _LeaveConfirmView(group, slot_index, self)
        await interaction.response.send_message(
            "❓ Möchtest du die Gruppe wirklich verlassen?",
            view=view,
            ephemeral=True,
        )

    async def complete_leave(
        self,
        interaction: discord.Interaction,
        group:       Dict,
        slot_index:  int,
    ) -> None:
        """Führt das Verlassen durch nach Bestätigung."""
        guild_id = interaction.guild_id
        msg_id   = group["message_id"]

        player_name = (
            group["slots"][slot_index].get("filled_by_ingame")
            or group["slots"][slot_index].get("filled_by_name", "?")
        )

        clear_slot(group, slot_index)
        touch_group_activity(group)
        save_group(guild_id, group)

        await notify_creator_leave(self.bot, group, player_name, slot_index)
        await notify_leave_in_forum(self.bot, group, player_name)

        # Embed aktualisieren
        channel = interaction.guild.get_channel(group["channel_id"])
        if channel:
            try:
                message = await channel.fetch_message(msg_id)
                await message.edit(
                    embed=build_group_embed(group),
                    view=build_group_action_view(group),
                )
            except Exception:
                pass

        # Nächsten Wartelisten-Spieler benachrichtigen
        await self.scheduler.notify_next_waitlist_public(group)

        await interaction.response.edit_message(
            content="✅ Du hast die Gruppe verlassen.", view=None
        )

    # ─────────────────────────────────────────────────────────────────────────
    # VERWALTUNG
    # ─────────────────────────────────────────────────────────────────────────

    async def _is_leader_or_admin(
        self, interaction: discord.Interaction, group: Dict
    ) -> bool:
        """
        True wenn der Nutzer der aktuelle Gruppenführer ODER ein Server-Admin ist
        (Server-Owner, Discord-Rechte Administrator/Server verwalten, oder Reds
        Admin-/Owner-Modell).
        """
        user = interaction.user
        if user.id == group.get("creator_id"):
            return True
        if interaction.guild and interaction.guild.owner_id == user.id:
            return True
        perms = getattr(user, "guild_permissions", None)
        if perms and (perms.administrator or perms.manage_guild):
            return True
        try:
            if await self.bot.is_owner(user) or await self.bot.is_admin(user):
                return True
        except Exception:
            pass
        return False

    async def _handle_manage(
        self, interaction: discord.Interaction, msg_id: int, parts: list
    ) -> None:
        group = get_group_by_message(interaction.guild_id, msg_id)
        if not group:
            await interaction.response.send_message("❌ Gruppe nicht gefunden.", ephemeral=True)
            return

        if not await self._is_leader_or_admin(interaction, group):
            await interaction.response.send_message(
                "❌ Nur der Gruppenführer oder ein Admin kann die Gruppe verwalten.", ephemeral=True
            )
            return

        embed = discord.Embed(
            title="⚙️ Gruppe verwalten",
            description=f"**{group.get('goal_custom') or group.get('goal', '?')}**",
            color=COLOR_OPEN,
        )
        await interaction.response.send_message(
            embed=embed, view=build_manage_view(group), ephemeral=True
        )

    async def _handle_finish(
        self, interaction: discord.Interaction, msg_id: int, parts: list
    ) -> None:
        """Gruppenersteller markiert die Gruppe als abgeschlossen."""
        group = get_group_by_message(interaction.guild_id, msg_id)
        if not group:
            await interaction.response.send_message("❌ Gruppe nicht gefunden.", ephemeral=True)
            return

        if interaction.user.id != group.get("creator_id"):
            await interaction.response.send_message(
                "❌ Nur der Gruppenersteller kann die Gruppe abschließen.", ephemeral=True
            )
            return

        view = _FinishConfirmView(group, self)
        await interaction.response.send_message(
            "✅ Gruppe als abgeschlossen markieren? "
            "Alle Mitglieder werden benachrichtigt. Der Beitrag bleibt zur Ansicht "
            "im Channel erhalten und wird nicht gelöscht.",
            view=view,
            ephemeral=True,
        )

    async def complete_finish(
        self, interaction: discord.Interaction, group: dict
    ) -> None:
        """
        Markiert die Gruppe als abgeschlossen.
        Der Post bleibt erhalten (Status 'finished', Buttons deaktiviert) und
        wird nicht gelöscht. Der Scheduler räumt 'finished'-Gruppen nicht ab.
        """
        from .notifications import notify_group_finished
        guild_id  = interaction.guild_id
        msg_id    = group.get("message_id")

        # Status setzen und speichern (bleibt erhalten)
        group["status"] = "finished"

        # Forum-Diskussionspost wird nach der konfigurierten Frist vom Scheduler
        # geschlossen (nicht gelöscht). Deadline hier setzen.
        if group.get("forum_thread_id"):
            settings = get_guild_settings(guild_id)
            close_at = datetime.now(timezone.utc) + timedelta(days=settings["forum_close_days"])
            group["forum_close_at"] = close_at.isoformat()
            group["forum_closed"]   = False

        save_group(guild_id, group)

        # Alle Mitglieder benachrichtigen
        await notify_group_finished(self.bot, group)

        # Post aktualisieren (abgeschlossen-Ansicht, Buttons deaktiviert)
        await self._refresh_group_message(group)

        await interaction.response.edit_message(
            content="✅ **Gruppe wurde abgeschlossen. GG!** Der Beitrag bleibt erhalten.",
            view=None,
        )

    async def _handle_manage_members(
        self, interaction: discord.Interaction, msg_id: int, parts: list
    ) -> None:
        group = get_group_by_message(interaction.guild_id, msg_id)
        if not group:
            await interaction.response.defer()
            return

        view = build_manage_members_view(group)
        if not view.children:
            await interaction.response.edit_message(
                content="ℹ️ Es sind noch keine Mitglieder beigetreten.",
                embed=None,
                view=build_manage_view(group),
            )
            return

        embed = discord.Embed(
            title="👥 Mitglieder verwalten",
            description="Wähle einen Spieler zum Entfernen:",
            color=COLOR_OPEN,
        )
        await interaction.response.edit_message(embed=embed, view=view)

    async def _handle_manage_remove_member(
        self, interaction: discord.Interaction, msg_id: int, parts: list
    ) -> None:
        group = get_group_by_message(interaction.guild_id, msg_id)
        if not group:
            await interaction.response.defer()
            return

        slot_index  = int(interaction.data["values"][0])
        slot        = next((s for s in group["slots"] if s["slot_index"] == slot_index), None)
        player_id   = slot["filled_by_id"] if slot else None
        player_name = (slot.get("filled_by_ingame") or slot.get("filled_by_name", "?")) if slot else "?"

        clear_slot(group, slot_index)
        touch_group_activity(group)
        save_group(interaction.guild_id, group)

        # Spieler benachrichtigen
        if player_id:
            await notify_player_removed(self.bot, group, player_id)

        await notify_creator_removed(self.bot, group, player_name, slot_index)
        await notify_leave_in_forum(self.bot, group, player_name, removed=True)

        # Nächsten Wartelisten-Spieler benachrichtigen
        await self.scheduler.notify_next_waitlist_public(group)

        # Embed aktualisieren
        channel = interaction.guild.get_channel(group["channel_id"])
        if channel:
            try:
                message = await channel.fetch_message(msg_id)
                await message.edit(
                    embed=build_group_embed(group),
                    view=build_group_action_view(group),
                )
            except Exception:
                pass

        # View neu bauen
        view = build_manage_members_view(group)
        embed = discord.Embed(
            title="👥 Mitglieder verwalten",
            description=f"✅ **{player_name}** wurde entfernt.",
            color=COLOR_OPEN,
        )
        await interaction.response.edit_message(embed=embed, view=view)

    async def _handle_manage_edit(
        self, interaction: discord.Interaction, msg_id: int, parts: list
    ) -> None:
        group = get_group_by_message(interaction.guild_id, msg_id)
        if not group:
            await interaction.response.defer()
            return

        embed = discord.Embed(
            title="✏️ Gruppe bearbeiten",
            description="Was möchtest du ändern?",
            color=COLOR_OPEN,
        )
        await interaction.response.edit_message(embed=embed, view=build_edit_view(group))

    async def _handle_edit_select(
        self, interaction: discord.Interaction, msg_id: int, parts: list
    ) -> None:
        group  = get_group_by_message(interaction.guild_id, msg_id)
        if not group:
            await interaction.response.defer()
            return

        field = interaction.data["values"][0]
        modal_map = {
            "datetime":   EditDateTimeModal,
            "recurrence": None,  # Select-basiert, wird separat behandelt
            "comment":    EditCommentModal,
            "level":      EditLevelModal,
            "goal":       EditGoalModal,
        }

        modal_cls = modal_map.get(field)
        if modal_cls:
            await interaction.response.send_modal(modal_cls(group, self))
        elif field == "recurrence":
            await interaction.response.send_message(
                "Wiederholung:", view=EditRecurrenceView(group, self), ephemeral=True
            )

    async def _handle_manage_transfer(
        self, interaction: discord.Interaction, msg_id: int, parts: list
    ) -> None:
        group = get_group_by_message(interaction.guild_id, msg_id)
        if not group:
            await interaction.response.defer()
            return

        view = build_transfer_view(group)
        has_candidates = any(isinstance(c, ui.Select) for c in view.children)
        if not has_candidates:
            await interaction.response.edit_message(
                content="ℹ️ Es gibt keine anderen Mitglieder, an die du die Führung übergeben könntest.",
                embed=None,
                view=build_manage_view(group),
            )
            return

        embed = discord.Embed(
            title="👑 Führung übergeben",
            description="Wähle das Mitglied, das die neue Gruppenführung übernehmen soll.",
            color=COLOR_OPEN,
        )
        await interaction.response.edit_message(content=None, embed=embed, view=view)

    async def _handle_manage_transfer_select(
        self, interaction: discord.Interaction, msg_id: int, parts: list
    ) -> None:
        group = get_group_by_message(interaction.guild_id, msg_id)
        if not group:
            await interaction.response.defer()
            return

        slot_index = int(interaction.data["values"][0])
        slot = next((s for s in group["slots"] if s["slot_index"] == slot_index), None)
        if not slot or not slot.get("filled_by_id"):
            await interaction.response.edit_message(
                content="❌ Mitglied nicht gefunden.",
                embed=None,
                view=build_manage_view(group),
            )
            return

        old_leader_name = group.get("creator_name", "?")
        new_id     = slot["filled_by_id"]
        new_name   = slot.get("filled_by_name") or slot.get("filled_by_ingame") or "?"
        new_ingame = slot.get("filled_by_ingame")

        set_group_leader(group, new_id, new_name, new_ingame)
        touch_group_activity(group)
        save_group(interaction.guild_id, group)

        # Gruppen-Post aktualisieren (zeigt neuen Ersteller) + Forum benachrichtigen
        await self._refresh_group_message(group)
        await notify_leader_change_in_forum(
            self.bot, group, new_id, new_name, old_leader_name
        )

        await interaction.response.edit_message(
            content=f"✅ Die Gruppenführung wurde an **{new_name}** übergeben.",
            embed=None,
            view=None,
        )

    async def _handle_manage_delete(
        self, interaction: discord.Interaction, msg_id: int, parts: list
    ) -> None:
        group = get_group_by_message(interaction.guild_id, msg_id)
        if not group:
            await interaction.response.defer()
            return

        confirm_view = _DeleteConfirmView(group, self)
        await interaction.response.edit_message(
            content="⚠️ **Sicher?** Diese Aktion kann nicht rückgängig gemacht werden.",
            embed=None,
            view=confirm_view,
        )

    async def _handle_manage_delete_confirm(
        self, interaction: discord.Interaction, msg_id: int, parts: list
    ) -> None:
        group = get_group_by_message(interaction.guild_id, msg_id)
        if not group:
            await interaction.response.defer()
            return

        await self._delete_group(interaction, group)

    async def _handle_manage_delete_cancel(
        self, interaction: discord.Interaction, msg_id: int, parts: list
    ) -> None:
        group = get_group_by_message(interaction.guild_id, msg_id)
        if not group:
            await interaction.response.defer()
            return

        embed = discord.Embed(
            title="⚙️ Gruppe verwalten",
            description=f"**{group.get('goal_custom') or group.get('goal', '?')}**",
            color=COLOR_OPEN,
        )
        await interaction.response.edit_message(
            content=None, embed=embed, view=build_manage_view(group)
        )

    async def _handle_manage_back(
        self, interaction: discord.Interaction, msg_id: int, parts: list
    ) -> None:
        group = get_group_by_message(interaction.guild_id, msg_id)
        if not group:
            await interaction.response.defer()
            return

        embed = discord.Embed(
            title="⚙️ Gruppe verwalten",
            description=f"**{group.get('goal_custom') or group.get('goal', '?')}**",
            color=COLOR_OPEN,
        )
        await interaction.response.edit_message(
            content=None, embed=embed, view=build_manage_view(group)
        )

    async def _delete_group(
        self, interaction: discord.Interaction, group: Dict
    ) -> None:
        """Führt die Löschung durch: Benachrichtigungen → Post löschen → JSON-Eintrag entfernen."""
        guild_id  = interaction.guild_id
        msg_id    = group.get("message_id")

        # Alle Beteiligten benachrichtigen
        await notify_group_deleted(
            self.bot, group, reason="manuell vom Ersteller gelöscht"
        )

        # Forum-Diskussionspost sofort schließen (bleibt erhalten, nicht gelöscht).
        await close_forum_post(self.bot, group)

        # Discord-Post löschen
        channel = interaction.guild.get_channel(group["channel_id"])
        if channel and msg_id:
            try:
                message = await channel.fetch_message(msg_id)
                await message.delete()
            except Exception:
                pass

        # Aus JSON entfernen
        delete_group(guild_id, msg_id)

        await interaction.response.edit_message(
            content="🗑️ **Gruppe wurde gelöscht.**", embed=None, view=None
        )

    async def apply_group_edit(
        self,
        interaction: discord.Interaction,
        group:       Dict,
        changes:     Dict,
    ) -> None:
        """
        Hilfsmethode: Wendet Änderungen an, speichert, aktualisiert Embed,
        benachrichtigt Mitglieder.
        """
        update_group_fields(group, **changes)
        touch_group_activity(group)
        save_group(interaction.guild_id, group)

        # Embed im Channel aktualisieren
        channel = interaction.guild.get_channel(group["channel_id"])
        if channel:
            try:
                message = await channel.fetch_message(group["message_id"])
                await message.edit(
                    embed=build_group_embed(group),
                    view=build_group_action_view(group),
                )
            except Exception:
                pass

        await notify_edit(self.bot, group, changes)

    # ─────────────────────────────────────────────────────────────────────────
    # DM-AKTIONEN: SUCHE AKTIV HALTEN / ERNEUT SUCHEN
    # ─────────────────────────────────────────────────────────────────────────

    async def _handle_group_extend(
        self, interaction: discord.Interaction, parts: list
    ) -> None:
        """
        Vorwarnungs-Button "Suche aktiv halten": setzt den Inaktivitäts-Timer
        zurück, damit die Gruppe weiter bestehen bleibt.
        custom_id-Format: group_extend:<guild_id>:<message_id>
        """
        if len(parts) < 3 or not parts[1].isdigit() or not parts[2].isdigit():
            return

        guild_id = int(parts[1])
        msg_id   = int(parts[2])
        group    = get_group_by_message(guild_id, msg_id)

        if not group or group.get("status") in ("expired", "closed", "finished"):
            await interaction.response.edit_message(
                content="ℹ️ Diese Gruppensuche existiert nicht mehr.",
                embed=None, view=None,
            )
            return

        if interaction.user.id != group.get("creator_id"):
            await interaction.response.send_message(
                "❌ Nur der Ersteller kann die Suche aktiv halten.", ephemeral=True
            )
            return

        touch_group_activity(group)
        save_group(guild_id, group)

        # Post im Channel aktualisieren (Footer zeigt neues Ablaufdatum)
        await self._refresh_group_message(group)

        expires = group.get("expires_at", "")[:10]
        await interaction.response.edit_message(
            content=(
                f"✅ **Deine Suche bleibt aktiv.**\n"
                f"Sie läuft nun frühestens am **{expires}** ab, sofern keine "
                f"weitere Aktivität stattfindet."
            ),
            embed=None, view=None,
        )

    async def _handle_group_recreate(
        self, interaction: discord.Interaction, parts: list
    ) -> None:
        """
        Ablauf-Button "Erneut suchen": postet eine abgelaufene Gruppe mit
        denselben Einstellungen erneut.
        custom_id-Format: group_recreate:<guild_id>:<group_id>
        """
        if len(parts) < 3 or not parts[1].isdigit():
            return

        guild_id = int(parts[1])
        group_id = parts[2]
        snapshot = get_expired_snapshot(group_id)

        if not snapshot:
            await interaction.response.edit_message(
                content="ℹ️ Diese Suche ist nicht mehr verfügbar und kann nicht erneut erstellt werden.",
                embed=None, view=None,
            )
            return

        if interaction.user.id != snapshot.get("creator_id"):
            await interaction.response.send_message(
                "❌ Nur der ursprüngliche Ersteller kann diese Suche erneut erstellen.",
                ephemeral=True,
            )
            return

        # Channel holen (DM → kein interaction.guild verfügbar)
        channel_id = snapshot["channel_id"]
        channel    = self.bot.get_channel(channel_id)
        if channel is None:
            try:
                channel = await self.bot.fetch_channel(channel_id)
            except Exception:
                await interaction.response.edit_message(
                    content="❌ Der Gruppen-Channel konnte nicht gefunden werden.",
                    embed=None, view=None,
                )
                return

        # Termin nur übernehmen wenn er in der Zukunft liegt
        dt = None
        dt_str = snapshot.get("datetime")
        if dt_str:
            try:
                parsed = datetime.fromisoformat(dt_str)
                if parsed.tzinfo is None:
                    parsed = parsed.replace(tzinfo=timezone.utc)
                if parsed > datetime.now(timezone.utc):
                    dt = parsed
            except ValueError:
                pass

        new_group = create_group(
            guild_id       = guild_id,
            channel_id     = channel_id,
            creator_id     = snapshot["creator_id"],
            creator_name   = snapshot.get("creator_name", interaction.user.display_name),
            creator_ingame = snapshot.get("creator_ingame"),
            goal           = snapshot.get("goal", ""),
            goal_custom    = snapshot.get("goal_custom"),
            player_count   = snapshot["player_count"],
            slots          = reset_slots(snapshot["slots"]),
            dt             = dt,
            recurrence     = snapshot.get("recurrence", "none"),
            comment        = snapshot.get("comment"),
            level_min      = snapshot.get("level_min"),
            level_max      = snapshot.get("level_max"),
        )
        new_group["level_mode"] = snapshot.get("level_mode", "none")

        # Ersteller wieder in seinen ursprünglichen Slot setzen (falls vorhanden)
        creator_id = snapshot["creator_id"]
        old_slot = next(
            (s for s in snapshot.get("slots", []) if s.get("filled_by_id") == creator_id),
            None,
        )
        if old_slot is not None:
            idx = old_slot["slot_index"]
            if idx < len(new_group["slots"]):
                slot = new_group["slots"][idx]
                slot["filled_by_id"]     = creator_id
                slot["filled_by_name"]   = old_slot.get("filled_by_name")
                slot["filled_by_ingame"] = old_slot.get("filled_by_ingame")
                slot["filled_class"]     = old_slot.get("filled_class") or slot["display_name"]
                slot["filled_emoji"]     = old_slot.get("filled_emoji") or slot["emoji"]

        # Posten
        try:
            new_group["message_id"] = 0
            message = await channel.send(
                embed=build_group_embed(new_group),
                view=build_group_action_view(new_group),
            )
            set_group_message_id(new_group, message.id)
            save_group(guild_id, new_group)

            # Forum-Diskussionspost erstellen (best effort).
            await create_forum_post(self.bot, new_group)
            save_group(guild_id, new_group)
            await message.edit(
                embed=build_group_embed(new_group),
                view=build_group_action_view(new_group),
            )
        except Exception:
            await interaction.response.edit_message(
                content="❌ Die Suche konnte nicht erneut erstellt werden.",
                embed=None, view=None,
            )
            return

        # Snapshot verbrauchen
        delete_expired_snapshot(group_id)

        msg_url = f"https://discord.com/channels/{guild_id}/{channel_id}/{message.id}"
        await interaction.response.edit_message(
            content=(
                f"✅ **Deine Suche wurde erneut erstellt!**\n"
                f"🔗 [Hier ansehen]({msg_url})"
            ),
            embed=None, view=None,
        )

    # ─────────────────────────────────────────────────────────────────────────
    # READ-ONLY-ENFORCEMENT
    # ─────────────────────────────────────────────────────────────────────────

    async def _apply_readonly(self, channel: discord.TextChannel) -> bool:
        """
        Setzt den Gruppen-Channel read-only für normale User:
          - @everyone darf ansehen, aber nicht schreiben / Threads erstellen
          - der Bot behält explizit Schreibrechte (postet die Gruppen-Embeds)
        Gibt True bei Erfolg zurück, False bei fehlenden Rechten.
        """
        guild = channel.guild
        try:
            await channel.set_permissions(
                guild.default_role,
                send_messages=False,
                create_public_threads=False,
                create_private_threads=False,
                send_messages_in_threads=False,
                view_channel=True,
                reason="RO Group Finder: Gruppen-Channel read-only",
            )
            await channel.set_permissions(
                guild.me,
                send_messages=True,
                reason="RO Group Finder: Bot behält Schreibrechte",
            )
            return True
        except discord.Forbidden:
            return False
        except discord.HTTPException:
            return False

    async def _clear_readonly(self, channel: discord.TextChannel) -> bool:
        """Hebt die read-only-Overwrites für @everyone wieder auf."""
        guild = channel.guild
        try:
            await channel.set_permissions(
                guild.default_role,
                overwrite=None,
                reason="RO Group Finder: read-only aufgehoben",
            )
            return True
        except (discord.Forbidden, discord.HTTPException):
            return False

    async def _refresh_group_message(self, group: Dict) -> None:
        """Aktualisiert Embed + View des Gruppen-Posts im Channel (best effort)."""
        channel_id = group.get("channel_id")
        msg_id     = group.get("message_id")
        if not channel_id or not msg_id:
            return
        channel = self.bot.get_channel(channel_id)
        if channel is None:
            try:
                channel = await self.bot.fetch_channel(channel_id)
            except Exception:
                return
        try:
            message = await channel.fetch_message(msg_id)
            await message.edit(
                embed=build_group_embed(group),
                view=build_group_action_view(group),
            )
        except Exception:
            pass


# ─────────────────────────────────────────────────────────────────────────────
# LEAVE CONFIRM VIEW
# ─────────────────────────────────────────────────────────────────────────────

class _LeaveConfirmView(ui.View):
    def __init__(self, group: Dict, slot_index: int, cog: ROGroupFinder):
        super().__init__(timeout=60)
        self.group      = group
        self.slot_index = slot_index
        self.cog        = cog

    @ui.button(label="✅ Ja, verlassen", style=discord.ButtonStyle.danger)
    async def confirm(self, interaction: discord.Interaction, button: ui.Button):
        await self.cog.complete_leave(interaction, self.group, self.slot_index)

    @ui.button(label="✕ Abbrechen", style=discord.ButtonStyle.secondary)
    async def cancel(self, interaction: discord.Interaction, button: ui.Button):
        await interaction.response.edit_message(content="Abgebrochen.", view=None)


# ─────────────────────────────────────────────────────────────────────────────
# FINISH CONFIRM VIEW
# ─────────────────────────────────────────────────────────────────────────────

class _FinishConfirmView(ui.View):
    def __init__(self, group: Dict, cog: ROGroupFinder):
        super().__init__(timeout=60)
        self.group = group
        self.cog   = cog

    @ui.button(label="✅ Ja, abschließen", style=discord.ButtonStyle.primary)
    async def confirm(self, interaction: discord.Interaction, button: ui.Button):
        await self.cog.complete_finish(interaction, self.group)

    @ui.button(label="✕ Abbrechen", style=discord.ButtonStyle.secondary)
    async def cancel(self, interaction: discord.Interaction, button: ui.Button):
        await interaction.response.edit_message(content="Abgebrochen.", view=None)


# ─────────────────────────────────────────────────────────────────────────────
# DELETE CONFIRM VIEW
# ─────────────────────────────────────────────────────────────────────────────

class _DeleteConfirmView(ui.View):
    def __init__(self, group: Dict, cog: ROGroupFinder):
        super().__init__(timeout=60)
        self.group = group
        self.cog   = cog
        msg_id     = str(group.get("message_id", ""))
        self.add_item(ui.Button(
            label="🗑️ Ja, löschen",
            style=discord.ButtonStyle.danger,
            custom_id=f"manage_delete_confirm:{msg_id}",
        ))
        self.add_item(ui.Button(
            label="✕ Abbrechen",
            style=discord.ButtonStyle.secondary,
            custom_id=f"manage_delete_cancel:{msg_id}",
        ))


# ─────────────────────────────────────────────────────────────────────────────
# JOIN CONFIRM MODAL (In-Game-Name eingeben)
# ─────────────────────────────────────────────────────────────────────────────

class JoinConfirmModal(ui.Modal, title="Beitreten bestätigen"):
    ingame_name = ui.TextInput(
        label="Dein In-Game-Name",
        placeholder="Dein Charakter-Name in Ragnarok",
        max_length=50,
    )

    def __init__(self, session_key: str, cog: ROGroupFinder):
        super().__init__()
        self.session_key = session_key
        self.cog         = cog

    async def on_submit(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        await self.cog._complete_join(
            interaction, self.session_key, self.ingame_name.value.strip()
        )


# ─────────────────────────────────────────────────────────────────────────────
# WAITLIST JOIN MODAL
# ─────────────────────────────────────────────────────────────────────────────

class WaitlistJoinModal(ui.Modal, title="Warteliste beitreten"):
    ingame_name = ui.TextInput(
        label="Dein In-Game-Name",
        placeholder="Dein Charakter-Name in Ragnarok",
        max_length=50,
    )
    class_input = ui.TextInput(
        label="Deine Klasse / Rolle",
        placeholder="z.B. Dieb, Magier, Heiler, ...",
        max_length=30,
    )

    def __init__(self, group: Dict, user_id: int, scheduler: GroupScheduler):
        super().__init__()
        self.group     = group
        self.user_id   = user_id
        self.scheduler = scheduler

    async def on_submit(self, interaction: discord.Interaction):
        position = add_to_waitlist(
            group         = self.group,
            user_id       = self.user_id,
            username      = interaction.user.display_name,
            ingame_name   = self.ingame_name.value.strip(),
            class_display = self.class_input.value.strip(),
            class_emoji   = "⚔️",
        )
        touch_group_activity(self.group)
        save_group(self.group["guild_id"], self.group)
        await notify_waitlist_joined(
            interaction.client, self.group, self.user_id, position
        )
        await interaction.response.send_message(
            f"⏳ Du bist auf **Platz {position}** der Warteliste.", ephemeral=True
        )


# ─────────────────────────────────────────────────────────────────────────────
# EDIT MODALS
# ─────────────────────────────────────────────────────────────────────────────

class EditDateTimeModal(ui.Modal, title="Datum & Zeit ändern"):
    date_input = ui.TextInput(label="Datum (TT.MM.JJJJ)", placeholder="17.11.2024", max_length=10)
    time_input = ui.TextInput(label="Uhrzeit (HH:MM)",    placeholder="21:00",      max_length=5)

    def __init__(self, group: Dict, cog: ROGroupFinder):
        super().__init__()
        self.group = group
        self.cog   = cog

    async def on_submit(self, interaction: discord.Interaction):
        raw    = f"{self.date_input.value.strip()} {self.time_input.value.strip()}"
        dt_utc = parse_local_input(raw, interaction.guild_id)
        if dt_utc is None:
            await interaction.response.send_message(
                "❌ Ungültiges Format. Bitte Datum als **TT.MM.JJJJ** und "
                "Uhrzeit als **HH:MM** eingeben (z.B. `24.12.2026` / `20:30`).",
                ephemeral=True,
            )
            return
        await self.cog.apply_group_edit(interaction, self.group, {"datetime": dt_utc.isoformat()})
        await interaction.response.send_message("✅ Datum & Zeit aktualisiert.", ephemeral=True)


class EditCommentModal(ui.Modal, title="Kommentar ändern"):
    comment = ui.TextInput(
        label="Kommentar", style=discord.TextStyle.paragraph,
        max_length=300, required=False,
    )

    def __init__(self, group: Dict, cog: ROGroupFinder):
        super().__init__()
        self.group = group
        self.cog   = cog

    async def on_submit(self, interaction: discord.Interaction):
        text = self.comment.value.strip()
        await self.cog.apply_group_edit(interaction, self.group, {"comment": text or None})
        await interaction.response.send_message("✅ Kommentar aktualisiert.", ephemeral=True)


class EditLevelModal(ui.Modal, title="Level-Anforderung ändern"):
    min_lvl = ui.TextInput(label="Mindest-Level", placeholder="50", max_length=3)
    max_lvl = ui.TextInput(label="Max-Level (leer = nur Mindest-Level)", max_length=3, required=False)

    def __init__(self, group: Dict, cog: ROGroupFinder):
        super().__init__()
        self.group = group
        self.cog   = cog

    async def on_submit(self, interaction: discord.Interaction):
        try:
            level_min = int(self.min_lvl.value.strip())
        except ValueError:
            await interaction.response.send_message("❌ Ungültiger Level-Wert.", ephemeral=True)
            return

        changes = {"level_min": level_min}
        max_val = self.max_lvl.value.strip()
        if max_val:
            try:
                changes["level_max"] = int(max_val)
            except ValueError:
                pass

        await self.cog.apply_group_edit(interaction, self.group, changes)
        await interaction.response.send_message("✅ Level-Anforderung aktualisiert.", ephemeral=True)


class EditGoalModal(ui.Modal, title="Ziel ändern"):
    goal = ui.TextInput(label="Neues Ziel", placeholder="z.B. Grind: Orc Village", max_length=100)

    def __init__(self, group: Dict, cog: ROGroupFinder):
        super().__init__()
        self.group = group
        self.cog   = cog

    async def on_submit(self, interaction: discord.Interaction):
        text = self.goal.value.strip()
        await self.cog.apply_group_edit(interaction, self.group, {"goal_custom": text, "goal": text})
        await interaction.response.send_message("✅ Ziel aktualisiert.", ephemeral=True)


class EditRecurrenceView(ui.View):
    def __init__(self, group: Dict, cog: ROGroupFinder):
        super().__init__(timeout=60)
        self.group = group
        self.cog   = cog
        options    = [
            discord.SelectOption(label=label, value=key)
            for key, label in RECURRENCE_OPTIONS.items()
        ]
        sel = ui.Select(placeholder="Wiederholung wählen...", options=options)
        sel.callback = self._callback
        self.add_item(sel)

    async def _callback(self, interaction: discord.Interaction):
        await self.cog.apply_group_edit(
            interaction, self.group, {"recurrence": interaction.data["values"][0]}
        )
        await interaction.response.send_message("✅ Wiederholung aktualisiert.", ephemeral=True)
