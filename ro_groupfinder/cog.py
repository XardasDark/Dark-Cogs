"""
cog.py – Haupt-Cog des RO Group Finders

Commands:
  /gruppe erstellen           → Startet den Wizard (nur im konfigurierten Channel)
  /gruppe suchen [klasse]     → Zeigt offene Gruppen die eine Klasse suchen
  /gruppe liste               → Zeigt alle aktiven Gruppen (Ephemeral)
  /kuhring channel #channel   → Setzt den erlaubten Gruppen-Channel (Admin)
  /kuhring info               → Zeigt aktuelle Servereinstellungen (Admin)
  /kuhring erinnerung [min]   → Stellt ein wann Erinnerungen gesendet werden (Admin)
  /kuhring cleanup [tage]     → Stellt die Ablaufzeit für Gruppen ein (Admin)
  /kuhring timeout [min]      → Stellt den Wartelisten-Timeout ein (Admin)

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
  manage_delete:<msg_id>            → Gruppe löschen (mit Bestätigung)
  manage_remove_member:<msg_id>     → Spieler entfernen (Select-Callback)
  manage_back:<msg_id>              → Zurück zum Hauptmenü
  manage_delete_confirm:<msg_id>    → Löschung bestätigen
  manage_delete_cancel:<msg_id>     → Löschung abbrechen
  edit_select:<msg_id>              → Bearbeitungs-Option ausgewählt
"""

import discord
from discord import app_commands
from discord.ext import commands
from discord import ui
from typing import Optional, Dict
from datetime import datetime, timezone

from .wizard import WizardSession, WizardState
from .data_manager import (
    get_group_channel,
    set_group_channel,
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
    update_group_fields,
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
    # SLASH-COMMANDS
    # ─────────────────────────────────────────────────────────────────────────

    gruppe = app_commands.Group(name="gruppe", description="RO Gruppen-System")
    kuhring = app_commands.Group(name="kuhring", description="RO Group Finder – Admin-Konfiguration")

    # ── /gruppe erstellen ─────────────────────────────────────────────────────

    @gruppe.command(name="erstellen", description="Erstelle eine neue Gruppenanfrage")
    async def gruppe_erstellen(self, interaction: discord.Interaction) -> None:
        guild_id    = interaction.guild_id
        channel_id  = interaction.channel_id
        allowed_ch  = get_group_channel(guild_id)

        # Channel-Prüfung
        if allowed_ch and channel_id != allowed_ch:
            ch = interaction.guild.get_channel(allowed_ch)
            mention = ch.mention if ch else f"<#{allowed_ch}>"
            await interaction.response.send_message(
                f"❌ Gruppen können nur in {mention} erstellt werden.",
                ephemeral=True,
            )
            return

        # Laufende Session beenden wenn vorhanden
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
        """Wird aufgerufen wenn der Wizard im letzten Schritt auf 'Posten' geklickt wird."""
        # Datum parsen
        dt = None
        if state.dt_str:
            try:
                dt = datetime.strptime(state.dt_str, "%d.%m.%Y %H:%M").replace(tzinfo=timezone.utc)
            except ValueError:
                pass

        # Level-Mode aus WizardState
        level_mode = state.level_mode if state.level_mode != "none" else None

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

        # level_mode im Group-Dict speichern
        group["level_mode"] = state.level_mode

        # Vorausgefüllte Mitglieder in Slots eintragen
        for member in state.prefilled_members:
            slot_idx = member["slot_index"]
            slots = group["slots"]
            if slot_idx < len(slots):
                slot = slots[slot_idx]
                slot["filled_by_id"]     = member.get("user_id") or state.creator_id
                slot["filled_by_name"]   = state.creator_name if member["is_creator"] else member["ingame_name"]
                slot["filled_by_ingame"] = member["ingame_name"]
                slot["filled_class"]     = slot["display_name"]
                slot["filled_emoji"]     = slot["emoji"]

        # Gruppen-Channel holen
        channel = interaction.guild.get_channel(state.channel_id)
        if not channel:
            await interaction.response.send_message(
                "❌ Der Gruppen-Channel konnte nicht gefunden werden.", ephemeral=True
            )
            return

        # Post senden
        group["message_id"] = 0  # Temporär für View-Build
        embed   = build_group_embed(group)
        view    = build_group_action_view(group)
        message = await channel.send(embed=embed, view=view)

        set_group_message_id(group, message.id)
        save_group(state.guild_id, group)

        # View mit echter Message-ID nochmal updaten
        await message.edit(view=build_group_action_view(group))

        # Wizard schließen
        if interaction.user.id in _wizard_sessions:
            del _wizard_sessions[interaction.user.id]

        await interaction.response.edit_message(
            content="✅ **Deine Gruppenanfrage wurde gepostet!**",
            embed=None,
            view=None,
        )

    async def _wizard_cancel(self, interaction: discord.Interaction) -> None:
        if interaction.user.id in _wizard_sessions:
            del _wizard_sessions[interaction.user.id]
        await interaction.response.edit_message(
            content="❌ Gruppen-Erstellung abgebrochen.",
            embed=None,
            view=None,
        )

    # ── /gruppe suchen ────────────────────────────────────────────────────────

    @gruppe.command(name="suchen", description="Suche offene Gruppen nach Klasse/Rolle")
    @app_commands.describe(klasse="Klasse oder Rolle nach der du suchst (optional)")
    async def gruppe_suchen(
        self, interaction: discord.Interaction, klasse: Optional[str] = None
    ) -> None:
        groups = get_guild_groups(interaction.guild_id)
        results = []

        for group in groups.values():
            if group.get("status") not in ("open", "full"):
                continue
            if klasse:
                kl_lower = klasse.lower()
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

        if not results:
            term = f" für **{klasse}**" if klasse else ""
            await interaction.response.send_message(
                f"Keine offenen Gruppen{term} gefunden.", ephemeral=True
            )
            return

        embeds = []
        for group in results[:5]:
            embeds.append(build_group_embed(group))

        await interaction.response.send_message(
            content=f"**{len(results)} Gruppe(n) gefunden:**",
            embeds=embeds,
            ephemeral=True,
        )

    # ── /gruppe liste ─────────────────────────────────────────────────────────

    @gruppe.command(name="liste", description="Zeige alle aktiven Gruppen")
    async def gruppe_liste(self, interaction: discord.Interaction) -> None:
        groups = get_guild_groups(interaction.guild_id)
        active = [g for g in groups.values() if g.get("status") in ("open", "full")]

        if not active:
            await interaction.response.send_message(
                "Es gibt derzeit keine aktiven Gruppen.", ephemeral=True
            )
            return

        lines = []
        for g in active[:20]:
            filled  = sum(1 for s in g.get("slots", []) if s.get("filled_by_id"))
            total   = g.get("player_count", "?")
            goal    = g.get("goal_custom") or g.get("goal") or "?"
            creator = g.get("creator_name", "?")
            status  = "🟢" if g.get("status") == "open" else "🟡"
            lines.append(f"{status} **{goal}** – {creator} ({filled}/{total} Spieler)")

        embed = discord.Embed(
            title=f"🗡️ Aktive Gruppen ({len(active)})",
            description="\n".join(lines),
            color=COLOR_OPEN,
        )
        await interaction.response.send_message(embed=embed, ephemeral=True)

    # ─────────────────────────────────────────────────────────────────────────
    # /kuhring – ADMIN COMMANDS
    # ─────────────────────────────────────────────────────────────────────────

    @kuhring.command(name="channel", description="Legt den Channel für Gruppenanfragen fest")
    @app_commands.describe(channel="Der Channel in dem Gruppen erstellt werden dürfen")
    @app_commands.checks.has_permissions(manage_guild=True)
    async def kuhring_channel(
        self, interaction: discord.Interaction, channel: discord.TextChannel
    ) -> None:
        set_group_channel(interaction.guild_id, channel.id)
        await interaction.response.send_message(
            f"✅ Gruppen-Channel auf {channel.mention} gesetzt.", ephemeral=True
        )

    @kuhring.command(name="info", description="Zeigt die aktuelle Konfiguration")
    @app_commands.checks.has_permissions(manage_guild=True)
    async def kuhring_info(self, interaction: discord.Interaction) -> None:
        s  = get_guild_settings(interaction.guild_id)
        ch = interaction.guild.get_channel(s.get("group_channel_id") or 0)

        embed = discord.Embed(title="⚙️ RO Group Finder – Konfiguration", color=COLOR_OPEN)
        embed.add_field(
            name="📌 Gruppen-Channel",
            value=ch.mention if ch else "Nicht gesetzt",
            inline=False,
        )
        embed.add_field(name="⏰ Erinnerung",         value=f"{s['reminder_minutes']} Minuten vor Start", inline=True)
        embed.add_field(name="🗑️ Cleanup",            value=f"Nach {s['cleanup_days']} Tagen",            inline=True)
        embed.add_field(name="⏳ Wartelisten-Timeout", value=f"{s['waitlist_timeout_minutes']} Minuten",   inline=True)
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @kuhring.command(name="erinnerung", description="Stellt ein wann Erinnerungen gesendet werden")
    @app_commands.describe(minuten="Minuten vor Gruppenstart (Standard: 30)")
    @app_commands.checks.has_permissions(manage_guild=True)
    async def kuhring_erinnerung(self, interaction: discord.Interaction, minuten: int) -> None:
        if minuten < 5 or minuten > 1440:
            await interaction.response.send_message("❌ Wert muss zwischen 5 und 1440 Minuten liegen.", ephemeral=True)
            return
        set_guild_setting(interaction.guild_id, "reminder_minutes", minuten)
        await interaction.response.send_message(
            f"✅ Erinnerungen werden jetzt **{minuten} Minuten** vor Gruppenstart gesendet.", ephemeral=True
        )

    @kuhring.command(name="cleanup", description="Stellt die Ablaufzeit für inaktive Gruppen ein")
    @app_commands.describe(tage="Tage nach denen Gruppen ablaufen (Standard: 14)")
    @app_commands.checks.has_permissions(manage_guild=True)
    async def kuhring_cleanup(self, interaction: discord.Interaction, tage: int) -> None:
        if tage < 1 or tage > 90:
            await interaction.response.send_message("❌ Wert muss zwischen 1 und 90 Tagen liegen.", ephemeral=True)
            return
        set_guild_setting(interaction.guild_id, "cleanup_days", tage)
        await interaction.response.send_message(
            f"✅ Gruppen laufen jetzt nach **{tage} Tagen** Inaktivität ab.", ephemeral=True
        )

    @kuhring.command(name="timeout", description="Stellt den Wartelisten-Timeout ein")
    @app_commands.describe(minuten="Minuten die ein Wartelisten-Spieler Zeit hat zu reagieren (Standard: 30)")
    @app_commands.checks.has_permissions(manage_guild=True)
    async def kuhring_timeout(self, interaction: discord.Interaction, minuten: int) -> None:
        if minuten < 5 or minuten > 120:
            await interaction.response.send_message("❌ Wert muss zwischen 5 und 120 Minuten liegen.", ephemeral=True)
            return
        set_guild_setting(interaction.guild_id, "waitlist_timeout_minutes", minuten)
        await interaction.response.send_message(
            f"✅ Wartelisten-Timeout auf **{minuten} Minuten** gesetzt.", ephemeral=True
        )

    # Fehlerbehandlung für fehlende Berechtigungen
    @kuhring_channel.error
    @kuhring_info.error
    @kuhring_erinnerung.error
    @kuhring_cleanup.error
    @kuhring_timeout.error
    async def admin_error(self, interaction: discord.Interaction, error) -> None:
        if isinstance(error, app_commands.MissingPermissions):
            await interaction.response.send_message(
                "❌ Du benötigst die Berechtigung **Server verwalten** für diesen Befehl.",
                ephemeral=True,
            )

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
        msg_id  = int(parts[1]) if parts[1].isdigit() else None

        # ── Dispatcher ────────────────────────────────────────────────────────
        handlers = {
            "group_join":             self._handle_join,
            "group_leave":            self._handle_leave,
            "group_manage":           self._handle_manage,
            "join_slot_select":       self._handle_join_slot_select,
            "join_class_select":      self._handle_join_class_select,
            "join_confirm":           self._handle_join_confirm,
            "join_cancel":            self._handle_join_cancel,
            "manage_members":         self._handle_manage_members,
            "manage_edit":            self._handle_manage_edit,
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

        save_group(guild_id, group)

        # Ersteller benachrichtigen
        await notify_creator_join(
            self.bot, group, ingame_name, class_display, class_emoji, slot_index
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

        # Warteliste zuerst prüfen
        if is_user_in_waitlist(group, user_id):
            remove_from_waitlist(group, user_id)
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
        save_group(guild_id, group)

        await notify_creator_leave(self.bot, group, player_name, slot_index)

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

    async def _handle_manage(
        self, interaction: discord.Interaction, msg_id: int, parts: list
    ) -> None:
        group = get_group_by_message(interaction.guild_id, msg_id)
        if not group:
            await interaction.response.send_message("❌ Gruppe nicht gefunden.", ephemeral=True)
            return

        if interaction.user.id != group["creator_id"]:
            await interaction.response.send_message(
                "❌ Nur der Gruppenersteller kann die Gruppe verwalten.", ephemeral=True
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
        save_group(interaction.guild_id, group)

        # Spieler benachrichtigen
        if player_id:
            await notify_player_removed(self.bot, group, player_id)

        await notify_creator_removed(self.bot, group, player_name, slot_index)

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
        new_dt = f"{self.date_input.value.strip()} {self.time_input.value.strip()}"
        await self.cog.apply_group_edit(interaction, self.group, {"datetime": new_dt})
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
