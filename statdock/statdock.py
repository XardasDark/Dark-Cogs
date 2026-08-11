"""
statdock.py – Statdocks: Voice-Channel, dessen Name automatisch eine Zahl anzeigt

Ein "Statdock" ist ein (meist gesperrter) Voice-Channel, dessen NAME regelmäßig
vom Bot aktualisiert wird, um eine Zahl anzuzeigen – z. B. die Mitgliederzahl oder
die Anzahl der Spieler mit bestimmten Rollen.

Zählmodi:
  total  – alle Server-Mitglieder (wie "All Members: 4885")
  roles  – Mitglieder, die IRGENDEINE der angegebenen Rollen besitzen
           (dedupliziert: wer mehrere der Rollen hat, wird nur einmal gezählt)

Befehle (Admin / "Server verwalten"):
  [p]statdock create <name...>            – neuen gesperrten Voice-Channel anlegen + als Gesamt-Dock registrieren
  [p]statdock addtotal <#channel> <name>  – bestehenden Channel als Gesamt-Dock registrieren
  [p]statdock addroles <#channel> <name> <@rolle...> – als Rollen-Dock registrieren
  [p]statdock name <#channel> <name...>   – Anzeigenamen/Vorlage ändern
  [p]statdock roles <#channel> <@rolle...> – Rollen eines Rollen-Docks neu setzen
  [p]statdock remove <#channel>           – Dock entfernen (Channel bleibt bestehen)
  [p]statdock list                        – alle Docks anzeigen
  [p]statdock refresh                     – sofortiges Update erzwingen

Die Vorlage darf den Platzhalter {count} enthalten (z. B. "🟢 Ragnarok X: {count}").
Fehlt der Platzhalter, wird die Zahl als "Name: {count}" angehängt.

Hinweis: Für den Rollen-Modus muss das Members-Intent aktiv sein (bei Red standardmäßig an).
"""

import logging
from typing import Dict, List, Optional

import discord
from discord.ext import tasks
from redbot.core import Config, commands
from redbot.core.bot import Red
from redbot.core.utils import chat_formatting as cf

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

    @commands.guild_only()
    @commands.admin_or_permissions(manage_guild=True)
    @commands.group(name="statdock", aliases=["statdocks"])
    async def statdock(self, ctx: commands.Context) -> None:
        """Statdocks verwalten (Voice-Channel mit Live-Zähler im Namen)."""

    @statdock.command(name="create")
    async def sd_create(self, ctx: commands.Context, *, name: str) -> None:
        """Legt einen neuen, gesperrten Voice-Channel an und zeigt dort die Gesamt-Mitgliederzahl.

        Beispiel: `[p]statdock create 👥 All Members: {count}`
        """
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
            await ctx.send("❌ Mir fehlt die Berechtigung, Channels zu erstellen (`Kanäle verwalten`).")
            return
        await self._set_dock(ctx.guild, channel.id, template=name, mode="total", roles=[])
        await ctx.send(f"✅ Gesamt-Dock erstellt: {channel.mention}")

    @statdock.command(name="addtotal")
    async def sd_addtotal(
        self, ctx: commands.Context, channel: discord.VoiceChannel, *, name: str
    ) -> None:
        """Registriert einen bestehenden Voice-Channel als Gesamt-Mitglieder-Dock.

        Beispiel: `[p]statdock addtotal #stats 👥 All Members: {count}`
        """
        await self._set_dock(ctx.guild, channel.id, template=name, mode="total", roles=[])
        await self._update_dock(ctx.guild, channel.id, await self._get_dock(ctx.guild, channel.id))
        await ctx.send(f"✅ {channel.mention} zählt jetzt alle Mitglieder.")

    @statdock.command(name="addroles")
    async def sd_addroles(
        self,
        ctx: commands.Context,
        channel: discord.VoiceChannel,
        name: str,
        *roles: discord.Role,
    ) -> None:
        """Registriert einen Voice-Channel als Rollen-Dock (zählt Mitglieder mit einer der Rollen).

        Der Name muss in Anführungszeichen stehen, danach folgen die Rollen.
        Beispiel: `[p]statdock addroles #ragx "🟢 Ragnarok X: {count}" @RolleA @RolleB @RolleC`
        """
        if not roles:
            await ctx.send("❌ Gib mindestens eine Rolle an.")
            return
        role_ids = list(dict.fromkeys(r.id for r in roles))  # dedupliziert, Reihenfolge erhalten
        await self._set_dock(ctx.guild, channel.id, template=name, mode="roles", roles=role_ids)
        await self._update_dock(ctx.guild, channel.id, await self._get_dock(ctx.guild, channel.id))
        role_mentions = ", ".join(r.mention for r in roles)
        await ctx.send(f"✅ {channel.mention} zählt jetzt Mitglieder mit: {role_mentions}")

    @statdock.command(name="name", aliases=["template"])
    async def sd_name(
        self, ctx: commands.Context, channel: discord.VoiceChannel, *, name: str
    ) -> None:
        """Ändert die Namensvorlage eines Docks (Platzhalter: {count})."""
        dock = await self._get_dock(ctx.guild, channel.id)
        if dock is None:
            await ctx.send("❌ Dieser Channel ist kein Statdock.")
            return
        dock["template"] = name
        await self._set_dock(ctx.guild, channel.id, **dock)
        await self._update_dock(ctx.guild, channel.id, dock)
        await ctx.send("✅ Vorlage aktualisiert.")

    @statdock.command(name="roles")
    async def sd_roles(
        self, ctx: commands.Context, channel: discord.VoiceChannel, *roles: discord.Role
    ) -> None:
        """Setzt die Rollen eines Rollen-Docks neu."""
        dock = await self._get_dock(ctx.guild, channel.id)
        if dock is None:
            await ctx.send("❌ Dieser Channel ist kein Statdock.")
            return
        if not roles:
            await ctx.send("❌ Gib mindestens eine Rolle an.")
            return
        dock["mode"] = "roles"
        dock["roles"] = list(dict.fromkeys(r.id for r in roles))
        await self._set_dock(ctx.guild, channel.id, **dock)
        await self._update_dock(ctx.guild, channel.id, dock)
        await ctx.send("✅ Rollen aktualisiert.")

    @statdock.command(name="remove", aliases=["delete", "del"])
    async def sd_remove(self, ctx: commands.Context, channel: discord.VoiceChannel) -> None:
        """Entfernt ein Dock aus der Verwaltung (der Channel selbst bleibt bestehen)."""
        async with self.config.guild(ctx.guild).docks() as docks:
            if str(channel.id) not in docks:
                await ctx.send("❌ Dieser Channel ist kein Statdock.")
                return
            del docks[str(channel.id)]
        await ctx.send("✅ Dock entfernt. Den Channel kannst du bei Bedarf manuell löschen.")

    @statdock.command(name="list")
    async def sd_list(self, ctx: commands.Context) -> None:
        """Zeigt alle konfigurierten Docks."""
        docks = await self.config.guild(ctx.guild).docks()
        if not docks:
            await ctx.send("Es sind keine Statdocks konfiguriert.")
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
        await ctx.send("\n".join(lines))

    @statdock.command(name="refresh", aliases=["update"])
    async def sd_refresh(self, ctx: commands.Context) -> None:
        """Erzwingt ein sofortiges Update aller Docks dieses Servers."""
        await self._update_guild(ctx.guild)
        await ctx.send("🔄 Docks aktualisiert.")

    @statdock.command(name="lock")
    async def sd_lock(self, ctx: commands.Context, channel: discord.VoiceChannel) -> None:
        """Sperrt einen bestehenden Voice-Channel: sichtbar für alle, aber nicht betretbar.

        Praktisch für Channels, die per `addtotal`/`addroles` registriert wurden.
        """
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
            await ctx.send("❌ Mir fehlt die Berechtigung, die Channel-Rechte zu ändern (`Kanäle verwalten`).")
            return
        await ctx.send(f"🔒 {channel.mention} ist jetzt sichtbar, aber nicht betretbar.")

    # ── Config-Helfer ─────────────────────────────────────────────────────────

    async def _set_dock(
        self, guild: discord.Guild, channel_id: int, *, template: str, mode: str, roles: List[int]
    ) -> None:
        async with self.config.guild(guild).docks() as docks:
            docks[str(channel_id)] = {"template": template, "mode": mode, "roles": roles}

    async def _get_dock(self, guild: discord.Guild, channel_id: int) -> Optional[dict]:
        docks = await self.config.guild(guild).docks()
        return docks.get(str(channel_id))
