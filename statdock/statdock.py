"""
statdock.py – Statdocks: Voice-Channel, dessen Name automatisch eine Zahl anzeigt

Ein "Statdock" ist ein (meist gesperrter) Voice-Channel, dessen NAME regelmäßig
vom Bot aktualisiert wird, um eine Zahl anzuzeigen – z. B. die Mitgliederzahl oder
die Anzahl der Spieler mit bestimmten Rollen.

Zählmodi:
  total  – alle Server-Mitglieder (wie "All Members: 4885")
  roles  – Mitglieder, die IRGENDEINE der angegebenen Rollen besitzen
           (dedupliziert: wer mehrere der Rollen hat, wird nur einmal gezählt)

Befehle sind Slash-Commands unter /statdock und nur für Admins ("Server verwalten")
sichtbar und nutzbar:
  /statdock create    – neuen gesperrten Voice-Channel anlegen + als Gesamt-Dock registrieren
  /statdock addtotal  – bestehenden Channel als Gesamt-Dock registrieren
  /statdock addroles  – als Rollen-Dock registrieren (bis zu 6 Rollen)
  /statdock name      – Anzeigenamen/Vorlage ändern
  /statdock roles     – Rollen eines Rollen-Docks neu setzen
  /statdock remove    – Dock entfernen (Channel bleibt bestehen)
  /statdock list      – alle Docks anzeigen
  /statdock refresh   – sofortiges Update erzwingen
  /statdock lock      – bestehenden Channel sperren (sichtbar, nicht betretbar)

Die Vorlage darf den Platzhalter {count} enthalten (z. B. "🟢 Ragnarok X: {count}").
Fehlt der Platzhalter, wird die Zahl als "Name: {count}" angehängt.

Hinweis: Für den Rollen-Modus muss das Members-Intent aktiv sein (bei Red standardmäßig an).
"""

import logging
from typing import List, Optional

import discord
from discord import app_commands
from discord.ext import tasks
from redbot.core import Config, commands
from redbot.core.bot import Red

log = logging.getLogger("red.dark-cogs.statdock")

# Discord limitiert Channel-Umbenennungen auf 2 pro 10 Minuten pro Channel.
# 6 Minuten pro Zyklus (und nur bei Änderung umbenennen) bleibt sicher darunter.
UPDATE_INTERVAL_SECONDS = 360


class Statdock(commands.Cog):
    """📊 Statdocks – zeigt Mitglieder-/Spielerzahlen im Namen eines Voice-Channels an."""

    def __init__(self, bot: Red):
        self.bot = bot
        self.config = Config.get_conf(self, identifier=0xDA54_D0C, force_registration=True)
        # docks = { channel_id(str): {"template": str, "mode": "total"|"roles", "roles": [role_id, ...]} }
        self.config.register_guild(docks={})

    async def cog_load(self) -> None:
        self.update_loop.start()

    async def cog_unload(self) -> None:
        self.update_loop.cancel()

    # ── Zähl-Logik ────────────────────────────────────────────────────────────

    @staticmethod
    def _count(guild: discord.Guild, dock: dict) -> int:
        mode = dock.get("mode", "total")
        if mode == "roles":
            role_ids = set(dock.get("roles", []))
            if not role_ids:
                return 0
            # Union: jedes Mitglied mit mind. einer der Rollen genau einmal zählen.
            members = {
                m.id
                for m in guild.members
                if any(r.id in role_ids for r in m.roles)
            }
            return len(members)
        return guild.member_count or 0

    @staticmethod
    def _render(template: str, count: int) -> str:
        if "{count}" in template:
            name = template.replace("{count}", str(count))
        else:
            name = f"{template}: {count}"
        # Channel-Namen dürfen max. 100 Zeichen lang sein.
        return name[:100]

    async def _update_dock(self, guild: discord.Guild, channel_id: int, dock: dict) -> None:
        channel = guild.get_channel(channel_id)
        if channel is None:
            return
        new_name = self._render(dock["template"], self._count(guild, dock))
        if channel.name == new_name:
            return  # spart Rate-Limit
        try:
            await channel.edit(name=new_name, reason="Statdock-Update")
        except discord.Forbidden:
            log.warning("Keine Berechtigung, um Channel %s in %s umzubenennen.", channel_id, guild.id)
        except discord.HTTPException as exc:
            log.warning("Statdock-Update fehlgeschlagen (%s/%s): %s", guild.id, channel_id, exc)

    async def _update_guild(self, guild: discord.Guild) -> None:
        docks = await self.config.guild(guild).docks()
        for channel_id_str, dock in docks.items():
            await self._update_dock(guild, int(channel_id_str), dock)

    # ── Update-Loop ───────────────────────────────────────────────────────────

    @tasks.loop(seconds=UPDATE_INTERVAL_SECONDS)
    async def update_loop(self) -> None:
        for guild in self.bot.guilds:
            try:
                await self._update_guild(guild)
            except Exception:  # noqa: BLE001 – Loop darf nie sterben
                log.exception("Fehler beim Statdock-Update für Guild %s", guild.id)

    @update_loop.before_loop
    async def _before_loop(self) -> None:
        await self.bot.wait_until_red_ready()

    # ── Befehle ───────────────────────────────────────────────────────────────

    @commands.hybrid_group(name="statdock", description="Statdocks verwalten (nur Admins)")
    @commands.guild_only()
    @commands.admin_or_permissions(manage_guild=True)
    @app_commands.default_permissions(manage_guild=True)
    async def statdock(self, ctx: commands.Context) -> None:
        """Statdocks verwalten (Voice-Channel mit Live-Zähler im Namen)."""

    @staticmethod
    def _collect_roles(*roles: Optional[discord.Role]) -> List[discord.Role]:
        """Filtert None heraus und dedupliziert unter Erhalt der Reihenfolge."""
        seen: List[discord.Role] = []
        seen_ids = set()
        for role in roles:
            if role is not None and role.id not in seen_ids:
                seen.append(role)
                seen_ids.add(role.id)
        return seen

    @statdock.command(name="create", description="Neuen gesperrten Voice-Channel als Gesamt-Dock anlegen")
    @app_commands.describe(name="Anzeigename, Platzhalter {count} (z. B. '👥 All Members: {count}')")
    async def sd_create(self, ctx: commands.Context, *, name: str) -> None:
        """Legt einen neuen, gesperrten Voice-Channel an und zeigt dort die Gesamt-Mitgliederzahl."""
        overwrites = {
            # @everyone: darf den Channel SEHEN, aber nicht BETRETEN.
            ctx.guild.default_role: discord.PermissionOverwrite(view_channel=True, connect=False),
            # Der Bot muss betreten/verwalten dürfen, um umbenennen zu können.
            ctx.guild.me: discord.PermissionOverwrite(
                view_channel=True, connect=True, manage_channels=True
            ),
        }
        display = self._render(name, self._count(ctx.guild, {"mode": "total"}))
        try:
            channel = await ctx.guild.create_voice_channel(
                name=display, overwrites=overwrites, reason=f"Statdock erstellt von {ctx.author}"
            )
        except discord.Forbidden:
            await ctx.send("❌ Mir fehlt die Berechtigung, Channels zu erstellen (`Kanäle verwalten`).", ephemeral=True)
            return
        await self._set_dock(ctx.guild, channel.id, template=name, mode="total", roles=[])
        await ctx.send(f"✅ Gesamt-Dock erstellt: {channel.mention}", ephemeral=True)

    @statdock.command(name="addtotal", description="Bestehenden Voice-Channel als Gesamt-Dock registrieren")
    @app_commands.describe(
        channel="Der Voice-Channel, der die Zahl anzeigt",
        name="Anzeigename, Platzhalter {count} (z. B. '👥 All Members: {count}')",
    )
    async def sd_addtotal(
        self, ctx: commands.Context, channel: discord.VoiceChannel, *, name: str
    ) -> None:
        """Registriert einen bestehenden Voice-Channel als Gesamt-Mitglieder-Dock."""
        await self._set_dock(ctx.guild, channel.id, template=name, mode="total", roles=[])
        await self._update_dock(ctx.guild, channel.id, await self._get_dock(ctx.guild, channel.id))
        await ctx.send(f"✅ {channel.mention} zählt jetzt alle Mitglieder.", ephemeral=True)

    @statdock.command(name="addroles", description="Voice-Channel als Rollen-Dock registrieren (bis zu 6 Rollen)")
    @app_commands.describe(
        channel="Der Voice-Channel, der die Zahl anzeigt",
        name="Anzeigename, Platzhalter {count} (z. B. '🟢 Ragnarok X: {count}')",
        rolle1="Erste Rolle (Pflicht)",
        rolle2="Weitere Rolle (optional)",
        rolle3="Weitere Rolle (optional)",
        rolle4="Weitere Rolle (optional)",
        rolle5="Weitere Rolle (optional)",
        rolle6="Weitere Rolle (optional)",
    )
    async def sd_addroles(
        self,
        ctx: commands.Context,
        channel: discord.VoiceChannel,
        name: str,
        rolle1: discord.Role,
        rolle2: Optional[discord.Role] = None,
        rolle3: Optional[discord.Role] = None,
        rolle4: Optional[discord.Role] = None,
        rolle5: Optional[discord.Role] = None,
        rolle6: Optional[discord.Role] = None,
    ) -> None:
        """Registriert einen Voice-Channel als Rollen-Dock (zählt Mitglieder mit einer der Rollen)."""
        roles = self._collect_roles(rolle1, rolle2, rolle3, rolle4, rolle5, rolle6)
        await self._set_dock(
            ctx.guild, channel.id, template=name, mode="roles", roles=[r.id for r in roles]
        )
        await self._update_dock(ctx.guild, channel.id, await self._get_dock(ctx.guild, channel.id))
        role_mentions = ", ".join(r.mention for r in roles)
        await ctx.send(f"✅ {channel.mention} zählt jetzt Mitglieder mit: {role_mentions}", ephemeral=True)

    @statdock.command(name="name", description="Anzeigename/Vorlage eines Docks ändern")
    @app_commands.describe(
        channel="Der Dock-Channel",
        name="Neuer Anzeigename, Platzhalter {count}",
    )
    async def sd_name(
        self, ctx: commands.Context, channel: discord.VoiceChannel, *, name: str
    ) -> None:
        """Ändert die Namensvorlage eines Docks (Platzhalter: {count})."""
        dock = await self._get_dock(ctx.guild, channel.id)
        if dock is None:
            await ctx.send("❌ Dieser Channel ist kein Statdock.", ephemeral=True)
            return
        dock["template"] = name
        await self._set_dock(ctx.guild, channel.id, **dock)
        await self._update_dock(ctx.guild, channel.id, dock)
        await ctx.send("✅ Vorlage aktualisiert.", ephemeral=True)

    @statdock.command(name="roles", description="Rollen eines Rollen-Docks neu setzen (bis zu 6)")
    @app_commands.describe(
        channel="Der Dock-Channel",
        rolle1="Erste Rolle (Pflicht)",
        rolle2="Weitere Rolle (optional)",
        rolle3="Weitere Rolle (optional)",
        rolle4="Weitere Rolle (optional)",
        rolle5="Weitere Rolle (optional)",
        rolle6="Weitere Rolle (optional)",
    )
    async def sd_roles(
        self,
        ctx: commands.Context,
        channel: discord.VoiceChannel,
        rolle1: discord.Role,
        rolle2: Optional[discord.Role] = None,
        rolle3: Optional[discord.Role] = None,
        rolle4: Optional[discord.Role] = None,
        rolle5: Optional[discord.Role] = None,
        rolle6: Optional[discord.Role] = None,
    ) -> None:
        """Setzt die Rollen eines Rollen-Docks neu."""
        dock = await self._get_dock(ctx.guild, channel.id)
        if dock is None:
            await ctx.send("❌ Dieser Channel ist kein Statdock.", ephemeral=True)
            return
        roles = self._collect_roles(rolle1, rolle2, rolle3, rolle4, rolle5, rolle6)
        dock["mode"] = "roles"
        dock["roles"] = [r.id for r in roles]
        await self._set_dock(ctx.guild, channel.id, **dock)
        await self._update_dock(ctx.guild, channel.id, dock)
        await ctx.send("✅ Rollen aktualisiert.", ephemeral=True)

    @statdock.command(name="remove", description="Dock aus der Verwaltung entfernen (Channel bleibt)")
    @app_commands.describe(channel="Der Dock-Channel")
    async def sd_remove(self, ctx: commands.Context, channel: discord.VoiceChannel) -> None:
        """Entfernt ein Dock aus der Verwaltung (der Channel selbst bleibt bestehen)."""
        async with self.config.guild(ctx.guild).docks() as docks:
            if str(channel.id) not in docks:
                await ctx.send("❌ Dieser Channel ist kein Statdock.", ephemeral=True)
                return
            del docks[str(channel.id)]
        await ctx.send("✅ Dock entfernt. Den Channel kannst du bei Bedarf manuell löschen.", ephemeral=True)

    @statdock.command(name="list", description="Alle konfigurierten Docks anzeigen")
    async def sd_list(self, ctx: commands.Context) -> None:
        """Zeigt alle konfigurierten Docks."""
        docks = await self.config.guild(ctx.guild).docks()
        if not docks:
            await ctx.send("Es sind keine Statdocks konfiguriert.", ephemeral=True)
            return
        lines = []
        for channel_id_str, dock in docks.items():
            channel = ctx.guild.get_channel(int(channel_id_str))
            where = channel.mention if channel else f"(gelöscht: {channel_id_str})"
            if dock.get("mode") == "roles":
                roles = ", ".join(
                    (r.name if (r := ctx.guild.get_role(rid)) else str(rid))
                    for rid in dock.get("roles", [])
                )
                detail = f"Rollen: {roles}"
            else:
                detail = "alle Mitglieder"
            count = self._count(ctx.guild, dock)
            lines.append(f"{where} → `{dock['template']}` [{detail}] = **{count}**")
        await ctx.send("\n".join(lines), ephemeral=True)

    @statdock.command(name="refresh", description="Sofortiges Update aller Docks erzwingen")
    async def sd_refresh(self, ctx: commands.Context) -> None:
        """Erzwingt ein sofortiges Update aller Docks dieses Servers."""
        await self._update_guild(ctx.guild)
        await ctx.send("🔄 Docks aktualisiert.", ephemeral=True)

    @statdock.command(name="lock", description="Channel sperren: sichtbar, aber nicht betretbar")
    @app_commands.describe(channel="Der Voice-Channel, der gesperrt werden soll")
    async def sd_lock(self, ctx: commands.Context, channel: discord.VoiceChannel) -> None:
        """Sperrt einen bestehenden Voice-Channel: sichtbar für alle, aber nicht betretbar."""
        try:
            await channel.set_permissions(
                ctx.guild.default_role,
                view_channel=True,
                connect=False,
                reason=f"Statdock gesperrt von {ctx.author}",
            )
            await channel.set_permissions(
                ctx.guild.me,
                view_channel=True,
                connect=True,
                manage_channels=True,
                reason="Statdock: Bot-Zugriff sicherstellen",
            )
        except discord.Forbidden:
            await ctx.send("❌ Mir fehlt die Berechtigung, die Channel-Rechte zu ändern (`Kanäle verwalten`).", ephemeral=True)
            return
        await ctx.send(f"🔒 {channel.mention} ist jetzt sichtbar, aber nicht betretbar.", ephemeral=True)

    # ── Config-Helfer ─────────────────────────────────────────────────────────

    async def _set_dock(
        self, guild: discord.Guild, channel_id: int, *, template: str, mode: str, roles: List[int]
    ) -> None:
        async with self.config.guild(guild).docks() as docks:
            docks[str(channel_id)] = {"template": template, "mode": mode, "roles": roles}

    async def _get_dock(self, guild: discord.Guild, channel_id: int) -> Optional[dict]:
        docks = await self.config.guild(guild).docks()
        return docks.get(str(channel_id))
