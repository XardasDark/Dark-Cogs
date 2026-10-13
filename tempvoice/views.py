"""
views.py – UI des TempVoice-Cogs

Enthält:
  - build_interface_embed(): das Embed über dem persistenten Steuer-Panel
  - TempVoicePanel: die persistente View mit allen Steuer-Buttons
    (feste custom_ids → übersteht Neustarts, geroutet über ihre Callbacks)
  - Modals (Name, Limit) und Auswahl-Views (Erlauben, Rauswerfen,
    Übertragen, Bitrate, Region) als kurzlebige, ephemere Hilfs-UI
"""

from typing import TYPE_CHECKING, List, Optional

import discord

from . import constants as C
from . import controls

if TYPE_CHECKING:
    from .cog import TempVoice


# ─────────────────────────────────────────────────────────────────────────────
# INTERFACE-EMBED
# ─────────────────────────────────────────────────────────────────────────────

def build_interface_embed(guild: discord.Guild) -> discord.Embed:
    embed = discord.Embed(
        title="🎛️ TempVoice-Steuerung",
        description=(
            "Bediene den Voice-Channel, in dem du dich **gerade befindest**.\n"
            "Betrete zuerst den Erstellen-Channel, um einen eigenen Channel zu bekommen."
        ),
        color=C.COLOR_INFO,
    )
    embed.add_field(
        name="Funktionen",
        value=(
            f"{C.BTN_RENAME[1]} **{C.BTN_RENAME[0]}** – Channel umbenennen\n"
            f"{C.BTN_LIMIT[1]} **{C.BTN_LIMIT[0]}** – Mitglieder-Limit (0 = unbegrenzt)\n"
            f"{C.BTN_LOCK[1]} **{C.BTN_LOCK[0]}** – Beitritt (de)aktivieren\n"
            f"{C.BTN_HIDE[1]} **{C.BTN_HIDE[0]}** – Sichtbarkeit umschalten\n"
            f"{C.BTN_PERMIT[1]} **{C.BTN_PERMIT[0]}** – User/Rolle Zugang geben (hebt Sperre auf)\n"
            f"{C.BTN_KICK[1]} **{C.BTN_KICK[0]}** – Mitglied entfernen & aussperren\n"
            f"{C.BTN_CLAIM[1]} **{C.BTN_CLAIM[0]}** – Besitz übernehmen (wenn Besitzer weg)\n"
            f"{C.BTN_TRANSFER[1]} **{C.BTN_TRANSFER[0]}** – Besitz an anderes Mitglied geben\n"
            f"{C.BTN_BITRATE[1]} **{C.BTN_BITRATE[0]}** – Audio-Qualität\n"
            f"{C.BTN_REGION[1]} **{C.BTN_REGION[0]}** – Voice-Region\n"
            f"{C.BTN_WAITING[1]} **{C.BTN_WAITING[0]}** – Warteraum an/aus (Bot holt Wartende rein)"
        ),
        inline=False,
    )
    embed.set_footer(text="Deine Einstellungen werden gemerkt und beim nächsten Channel wieder angewendet.")
    return embed


# ─────────────────────────────────────────────────────────────────────────────
# PERSISTENTES PANEL
# ─────────────────────────────────────────────────────────────────────────────

class TempVoicePanel(discord.ui.View):
    """Persistente View mit allen Steuer-Buttons (timeout=None)."""

    def __init__(self, cog: "TempVoice"):
        super().__init__(timeout=None)
        self.cog = cog

    # ── Zeile 0 ────────────────────────────────────────────────────────────────

    @discord.ui.button(label=C.BTN_RENAME[0], emoji=C.BTN_RENAME[1],
                       style=discord.ButtonStyle.primary, custom_id=C.CID_RENAME, row=0)
    async def btn_rename(self, interaction: discord.Interaction, button: discord.ui.Button):
        channel = await self.cog.resolve_owned(interaction)
        if channel is None:
            return
        await interaction.response.send_modal(RenameModal(self.cog, channel))

    @discord.ui.button(label=C.BTN_LIMIT[0], emoji=C.BTN_LIMIT[1],
                       style=discord.ButtonStyle.primary, custom_id=C.CID_LIMIT, row=0)
    async def btn_limit(self, interaction: discord.Interaction, button: discord.ui.Button):
        channel = await self.cog.resolve_owned(interaction)
        if channel is None:
            return
        await interaction.response.send_modal(LimitModal(self.cog, channel))

    @discord.ui.button(label=C.BTN_LOCK[0], emoji=C.BTN_LOCK[1],
                       style=discord.ButtonStyle.secondary, custom_id=C.CID_LOCK, row=0)
    async def btn_lock(self, interaction: discord.Interaction, button: discord.ui.Button):
        channel = await self.cog.resolve_owned(interaction)
        if channel is None:
            return
        await self.cog.run_action(interaction, controls.toggle_lock, channel)

    @discord.ui.button(label=C.BTN_HIDE[0], emoji=C.BTN_HIDE[1],
                       style=discord.ButtonStyle.secondary, custom_id=C.CID_HIDE, row=0)
    async def btn_hide(self, interaction: discord.Interaction, button: discord.ui.Button):
        channel = await self.cog.resolve_owned(interaction)
        if channel is None:
            return
        await self.cog.run_action(interaction, controls.toggle_hide, channel)

    # ── Zeile 1 ────────────────────────────────────────────────────────────────

    @discord.ui.button(label=C.BTN_PERMIT[0], emoji=C.BTN_PERMIT[1],
                       style=discord.ButtonStyle.success, custom_id=C.CID_PERMIT, row=1)
    async def btn_permit(self, interaction: discord.Interaction, button: discord.ui.Button):
        channel = await self.cog.resolve_owned(interaction)
        if channel is None:
            return
        await interaction.response.send_message(
            "Wähle einen User oder eine Rolle, die Zugang bekommen soll:",
            view=PermitSelectView(self.cog, channel), ephemeral=True)

    @discord.ui.button(label=C.BTN_KICK[0], emoji=C.BTN_KICK[1],
                       style=discord.ButtonStyle.danger, custom_id=C.CID_KICK, row=1)
    async def btn_kick(self, interaction: discord.Interaction, button: discord.ui.Button):
        channel = await self.cog.resolve_owned(interaction)
        if channel is None:
            return
        await interaction.response.send_message(
            "Wähle ein Mitglied, das rausgeworfen & ausgesperrt werden soll:",
            view=KickSelectView(self.cog, channel), ephemeral=True)

    @discord.ui.button(label=C.BTN_CLAIM[0], emoji=C.BTN_CLAIM[1],
                       style=discord.ButtonStyle.secondary, custom_id=C.CID_CLAIM, row=1)
    async def btn_claim(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self.cog.claim(interaction)

    @discord.ui.button(label=C.BTN_TRANSFER[0], emoji=C.BTN_TRANSFER[1],
                       style=discord.ButtonStyle.secondary, custom_id=C.CID_TRANSFER, row=1)
    async def btn_transfer(self, interaction: discord.Interaction, button: discord.ui.Button):
        channel = await self.cog.resolve_owned(interaction)
        if channel is None:
            return
        await interaction.response.send_message(
            "Wähle das Mitglied, das neuer Besitzer werden soll:",
            view=TransferSelectView(self.cog, channel), ephemeral=True)

    # ── Zeile 2 ────────────────────────────────────────────────────────────────

    @discord.ui.button(label=C.BTN_BITRATE[0], emoji=C.BTN_BITRATE[1],
                       style=discord.ButtonStyle.primary, custom_id=C.CID_BITRATE, row=2)
    async def btn_bitrate(self, interaction: discord.Interaction, button: discord.ui.Button):
        channel = await self.cog.resolve_owned(interaction)
        if channel is None:
            return
        await interaction.response.send_message(
            "Wähle die Bitrate (Audio-Qualität):",
            view=BitrateSelectView(self.cog, channel), ephemeral=True)

    @discord.ui.button(label=C.BTN_REGION[0], emoji=C.BTN_REGION[1],
                       style=discord.ButtonStyle.primary, custom_id=C.CID_REGION, row=2)
    async def btn_region(self, interaction: discord.Interaction, button: discord.ui.Button):
        channel = await self.cog.resolve_owned(interaction)
        if channel is None:
            return
        await interaction.response.send_message(
            "Wähle die Voice-Region:",
            view=RegionSelectView(self.cog, channel), ephemeral=True)

    @discord.ui.button(label=C.BTN_WAITING[0], emoji=C.BTN_WAITING[1],
                       style=discord.ButtonStyle.secondary, custom_id=C.CID_WAITING, row=2)
    async def btn_waiting(self, interaction: discord.Interaction, button: discord.ui.Button):
        channel = await self.cog.resolve_owned(interaction)
        if channel is None:
            return
        await self.cog.toggle_waiting_room(interaction, channel)


# ─────────────────────────────────────────────────────────────────────────────
# ANKLOPFEN  (für gesperrte Channel – liegt im Text-Chat des Voice-Channels)
# ─────────────────────────────────────────────────────────────────────────────

def build_knock_embed() -> discord.Embed:
    embed = discord.Embed(
        title="🔒 Gesperrter Channel?",
        description=(
            "Wenn dieser Channel gesperrt ist, kannst du hier **anklopfen**. "
            "Der Besitzer bekommt eine Anfrage und kann dich reinlassen – "
            "danach kannst du einfach beitreten."
        ),
        color=C.COLOR_INFO,
    )
    return embed


class KnockView(discord.ui.View):
    """Persistenter „Anklopfen\"-Button im Text-Chat eines Temp-Channels."""

    def __init__(self, cog: "TempVoice"):
        super().__init__(timeout=None)
        self.cog = cog

    @discord.ui.button(label=C.BTN_KNOCK[0], emoji=C.BTN_KNOCK[1],
                       style=discord.ButtonStyle.primary, custom_id=C.CID_KNOCK)
    async def btn_knock(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self.cog.handle_knock(interaction)


class KnockRequestView(discord.ui.View):
    """Kurzlebige Anfrage mit Reinlassen/Ablehnen – nur der Besitzer entscheidet."""

    def __init__(self, cog: "TempVoice", owner_id: int,
                 knocker: discord.Member, channel_id: int):
        super().__init__(timeout=C.KNOCK_REQUEST_TIMEOUT)
        self.cog = cog
        self.owner_id = owner_id
        self.knocker = knocker
        self.channel_id = channel_id
        self.message: Optional[discord.Message] = None

    @discord.ui.button(label=C.BTN_KNOCK_ACCEPT[0], emoji=C.BTN_KNOCK_ACCEPT[1],
                       style=discord.ButtonStyle.success)
    async def accept(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self.cog.resolve_knock(interaction, self, accept=True)

    @discord.ui.button(label=C.BTN_KNOCK_REJECT[0], emoji=C.BTN_KNOCK_REJECT[1],
                       style=discord.ButtonStyle.danger)
    async def reject(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self.cog.resolve_knock(interaction, self, accept=False)

    async def on_timeout(self) -> None:
        self.cog._pending_knocks.discard((self.channel_id, self.knocker.id))
        if self.message is not None:
            try:
                await self.message.edit(content="⌛ Die Anklopf-Anfrage ist abgelaufen.", view=None)
            except discord.HTTPException:
                pass


# ─────────────────────────────────────────────────────────────────────────────
# WARTERAUM  (optional – der Bot holt Wartende rein, der Besitzer braucht kein Ziehen-Recht)
# ─────────────────────────────────────────────────────────────────────────────

class WaitingAdmitView(discord.ui.View):
    """Kurzlebige Anfrage: Der Besitzer entscheidet, ob ein Wartender reingeholt wird."""

    def __init__(self, cog: "TempVoice", owner_id: int, waiter: discord.Member,
                 main_id: int, waiting_room_id: int):
        super().__init__(timeout=C.KNOCK_REQUEST_TIMEOUT)
        self.cog = cog
        self.owner_id = owner_id
        self.waiter = waiter
        self.main_id = main_id
        self.waiting_room_id = waiting_room_id
        self.message: Optional[discord.Message] = None

    @discord.ui.button(label=C.BTN_ADMIT[0], emoji=C.BTN_ADMIT[1],
                       style=discord.ButtonStyle.success)
    async def admit(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self.cog.resolve_wait(interaction, self, admit=True)

    @discord.ui.button(label=C.BTN_DISMISS[0], emoji=C.BTN_DISMISS[1],
                       style=discord.ButtonStyle.danger)
    async def dismiss(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self.cog.resolve_wait(interaction, self, admit=False)

    async def on_timeout(self) -> None:
        self.cog._pending_waits.discard((self.main_id, self.waiter.id))
        if self.message is not None:
            try:
                await self.message.edit(content="⌛ Die Warteraum-Anfrage ist abgelaufen.", view=None)
            except discord.HTTPException:
                pass


# ─────────────────────────────────────────────────────────────────────────────
# MODALS
# ─────────────────────────────────────────────────────────────────────────────

class RenameModal(discord.ui.Modal, title="Channel umbenennen"):
    def __init__(self, cog: "TempVoice", channel: discord.VoiceChannel):
        super().__init__()
        self.cog = cog
        self.channel = channel
        self.name_input = discord.ui.TextInput(
            label="Neuer Channel-Name",
            placeholder="z. B. Raid-Gruppe",
            max_length=C.MAX_CHANNEL_NAME,
            default=channel.name,
        )
        self.add_item(self.name_input)

    async def on_submit(self, interaction: discord.Interaction):
        await self.cog.apply_submit(interaction, self.channel, controls.rename,
                                    str(self.name_input.value))


class LimitModal(discord.ui.Modal, title="Mitglieder-Limit setzen"):
    def __init__(self, cog: "TempVoice", channel: discord.VoiceChannel):
        super().__init__()
        self.cog = cog
        self.channel = channel
        self.limit_input = discord.ui.TextInput(
            label="Max. Mitglieder (0 = unbegrenzt)",
            placeholder="z. B. 5",
            max_length=2,
            default=str(channel.user_limit or 0),
        )
        self.add_item(self.limit_input)

    async def on_submit(self, interaction: discord.Interaction):
        raw = str(self.limit_input.value).strip()
        if not raw.isdigit():
            await interaction.response.send_message(
                "❌ Bitte gib eine ganze Zahl ein (0 = unbegrenzt).", ephemeral=True)
            return
        await self.cog.apply_submit(interaction, self.channel, controls.set_limit, int(raw))


# ─────────────────────────────────────────────────────────────────────────────
# AUSWAHL-VIEWS  (ephemer, kurzlebig)
# ─────────────────────────────────────────────────────────────────────────────

class _ActionSelectView(discord.ui.View):
    """Basis für ephemere Auswahl-Views mit Besitzer-Kontext."""

    def __init__(self, cog: "TempVoice", channel: discord.VoiceChannel):
        super().__init__(timeout=120)
        self.cog = cog
        self.channel = channel


class PermitSelectView(_ActionSelectView):
    def __init__(self, cog: "TempVoice", channel: discord.VoiceChannel):
        super().__init__(cog, channel)
        self.select = discord.ui.MentionableSelect(
            placeholder="User oder Rolle wählen …", min_values=1, max_values=1)
        self.select.callback = self._callback
        self.add_item(self.select)

    async def _callback(self, interaction: discord.Interaction):
        target = self.select.values[0]
        await self.cog.apply_submit(interaction, self.channel, controls.permit, target,
                                    edit=True)


class KickSelectView(_ActionSelectView):
    def __init__(self, cog: "TempVoice", channel: discord.VoiceChannel):
        super().__init__(cog, channel)
        self.select = discord.ui.UserSelect(
            placeholder="Mitglied wählen …", min_values=1, max_values=1)
        self.select.callback = self._callback
        self.add_item(self.select)

    async def _callback(self, interaction: discord.Interaction):
        target = self.select.values[0]
        await self.cog.apply_submit(interaction, self.channel, controls.kick, target,
                                    edit=True)


class TransferSelectView(_ActionSelectView):
    def __init__(self, cog: "TempVoice", channel: discord.VoiceChannel):
        super().__init__(cog, channel)
        self.select = discord.ui.UserSelect(
            placeholder="Neuen Besitzer wählen …", min_values=1, max_values=1)
        self.select.callback = self._callback
        self.add_item(self.select)

    async def _callback(self, interaction: discord.Interaction):
        target = self.select.values[0]
        await self.cog.apply_submit(interaction, self.channel, controls.transfer, target,
                                    edit=True)


class BitrateSelectView(_ActionSelectView):
    def __init__(self, cog: "TempVoice", channel: discord.VoiceChannel):
        super().__init__(cog, channel)
        limit = int(channel.guild.bitrate_limit)
        options: List[discord.SelectOption] = []
        for step in C.BITRATE_STEPS:
            if step <= limit:
                options.append(discord.SelectOption(
                    label=f"{step // 1000} kbps", value=str(step),
                    default=(step == channel.bitrate)))
        if not options:
            options.append(discord.SelectOption(label="64 kbps", value="64000"))
        self.select = discord.ui.Select(placeholder="Bitrate wählen …", options=options)
        self.select.callback = self._callback
        self.add_item(self.select)

    async def _callback(self, interaction: discord.Interaction):
        value = int(self.select.values[0])
        await self.cog.apply_submit(interaction, self.channel, controls.set_bitrate, value,
                                    edit=True)


class RegionSelectView(_ActionSelectView):
    def __init__(self, cog: "TempVoice", channel: discord.VoiceChannel):
        super().__init__(cog, channel)
        options: List[discord.SelectOption] = []
        current = channel.rtc_region
        for code, name, emoji in C.REGIONS:
            options.append(discord.SelectOption(
                label=name, value=code or "auto", emoji=emoji,
                default=(code == current)))
        self.select = discord.ui.Select(placeholder="Region wählen …", options=options)
        self.select.callback = self._callback
        self.add_item(self.select)

    async def _callback(self, interaction: discord.Interaction):
        raw = self.select.values[0]
        region: Optional[str] = None if raw == "auto" else raw
        await self.cog.apply_submit(interaction, self.channel, controls.set_region, region,
                                    edit=True)
