"""
manage.py – ephemeres Verwalten-Panel für ein Ticket (nur Support)

Bündelt Aktionen, die ein Support-Mitglied im Ticket ausführen kann:
* Bearbeitungs-Status setzen (In Bearbeitung / Wartet auf Nutzer / zurücksetzen)
* Nutzer zum Ticket hinzufügen / entfernen
* Ticket in eine andere Kategorie umleiten (Zugriff + Ping wechseln)

Die Select-Callbacks rufen Methoden am Cog auf; das Panel wird danach frisch
neu aufgebaut. Alle Komponenten sind kurzlebig (ephemer, pro Klick erzeugt).
"""

from typing import Any, Dict, List, Tuple

import discord

from .constants import (
    COLOR_INFO, STAGE_IN_PROGRESS, STAGE_WAITING, STAGE_LABELS, CATEGORY_PATH_SEP,
)


async def build_manage(cog, guild: discord.Guild, channel_id: int,
                       ticket: Dict[str, Any], author_id: int
                       ) -> Tuple[discord.Embed, discord.ui.View]:
    _, label = await cog.store.resolve_leaf(guild, ticket["category_key"])
    label = label or ticket.get("category_label") or ticket["category_key"]
    stage = ticket.get("stage")
    added = ticket.get("added_members") or []

    embed = discord.Embed(
        title=f"⚙️ Ticket verwalten · {ticket['id']}",
        description=(f"Kategorie: **{label}**\n"
                     f"Status: {STAGE_LABELS.get(stage, '—')}\n"
                     f"Hinzugefügt: " + (" ".join(f'<@{u}>' for u in added) if added else "_niemand_")),
        color=COLOR_INFO)
    embed.set_footer(text="Nur für dich sichtbar. Änderungen greifen sofort.")

    leaves = await cog.store.iter_leaves(guild)
    view = TicketManageView(cog, guild, channel_id, author_id, ticket, leaves)
    return embed, view


class TicketManageView(discord.ui.View):
    def __init__(self, cog, guild, channel_id: int, author_id: int,
                 ticket: Dict[str, Any], leaves: List):
        super().__init__(timeout=180)
        self.cog = cog
        self.guild = guild
        self.channel_id = channel_id
        self.author_id = author_id

        # Status
        current = ticket.get("stage")
        status_sel = discord.ui.Select(
            placeholder="Bearbeitungs-Status setzen …", min_values=1, max_values=1, row=0,
            options=[
                discord.SelectOption(label="Offen (zurücksetzen)", value="none", emoji="🟢",
                                     default=current is None),
                discord.SelectOption(label="In Bearbeitung", value=STAGE_IN_PROGRESS, emoji="🛠️",
                                     default=current == STAGE_IN_PROGRESS),
                discord.SelectOption(label="Wartet auf Nutzer", value=STAGE_WAITING, emoji="⏳",
                                     default=current == STAGE_WAITING),
            ])
        status_sel.callback = self._on_status
        self.add_item(status_sel)

        # Nutzer hinzufügen
        add_sel = discord.ui.UserSelect(
            placeholder="Nutzer zum Ticket hinzufügen …", min_values=1, max_values=10, row=1)
        add_sel.callback = self._on_add
        self.add_item(add_sel)

        # Nutzer entfernen (nur bereits hinzugefügte)
        added = ticket.get("added_members") or []
        if added:
            rem_opts = [discord.SelectOption(label=f"User {uid}", value=str(uid)) for uid in added[:25]]
            # Namen auflösen, wo möglich
            for opt in rem_opts:
                m = guild.get_member(int(opt.value))
                if m is not None:
                    opt.label = m.display_name[:100]
            rem_sel = discord.ui.Select(
                placeholder="Hinzugefügten Nutzer entfernen …", min_values=1, max_values=1,
                options=rem_opts, row=2)
            rem_sel.callback = self._on_remove
            self.add_item(rem_sel)

        # Umleiten
        options: List[discord.SelectOption] = []
        for path, leaf, parent in leaves[:25]:
            if path == ticket["category_key"]:
                continue
            lbl = (f"{parent['name']} › {leaf['name']}" if parent else leaf["name"])[:100]
            options.append(discord.SelectOption(label=lbl, value=path, emoji=leaf.get("emoji") or None))
        if options:
            move_sel = discord.ui.Select(
                placeholder="Ticket in andere Kategorie umleiten …", min_values=1, max_values=1,
                options=options, row=3)
            move_sel.callback = self._on_reassign
            self.add_item(move_sel)

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.author_id:
            await interaction.response.send_message(
                "Dieses Panel gehört zu einer anderen Person.", ephemeral=True)
            return False
        return True

    async def _on_status(self, interaction: discord.Interaction) -> None:
        value = interaction.data["values"][0]
        await self.cog.mgmt_set_stage(interaction, None if value == "none" else value)

    async def _on_add(self, interaction: discord.Interaction) -> None:
        ids = [int(v) for v in interaction.data.get("values", [])]
        await self.cog.mgmt_add_members(interaction, ids)

    async def _on_remove(self, interaction: discord.Interaction) -> None:
        await self.cog.mgmt_remove_member(interaction, int(interaction.data["values"][0]))

    async def _on_reassign(self, interaction: discord.Interaction) -> None:
        await self.cog.mgmt_reassign(interaction, interaction.data["values"][0])
