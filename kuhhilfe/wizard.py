"""
wizard.py – ephemerer Support-Wizard für KuhHilfe

Kurzlebige, nutzer-spezifische Komponenten (eigene Callbacks, keine Persistenz –
sie entstehen pro Klick neu):

* WizardSelectView – Auswahl einer Ebene im Kategorie-Baum. Wird eine Gruppe
  gewählt, ersetzt sie sich durch die Auswahl der Unterkategorien; wird eine
  finale Kategorie (Leaf) gewählt, öffnet sich deren Modal.
* TicketModal – dynamisch aus den Feldern der gewählten Leaf gebaut.
"""

from typing import Any, Dict, List, Tuple

import discord

from .constants import (
    WIZARD_CAT_SELECT, FIELD_STYLES, MAX_FIELDS, COLOR_ERROR, COLOR_INFO,
    CATEGORY_PATH_SEP,
)


def make_modal(cog, leaf: Dict[str, Any], path: str) -> "TicketModal":
    return TicketModal(cog, leaf, path)


class WizardSelectView(discord.ui.View):
    """Ephemere Auswahl einer Baum-Ebene (Gruppen und/oder finale Kategorien)."""

    def __init__(self, cog, entries: List[Tuple[str, Dict[str, Any]]],
                 author_id: int, placeholder: str):
        super().__init__(timeout=180)
        self.cog = cog
        self.author_id = author_id

        options: List[discord.SelectOption] = []
        for path, node in entries[:25]:
            desc = node.get("description")
            if cog.store.is_group(node):
                count = len(node.get("children") or {})
                desc = desc or f"{count} Unterkategorie(n)"
            options.append(discord.SelectOption(
                label=node["name"][:100],
                value=path,
                description=(desc[:100] if desc else None),
                emoji=node.get("emoji") or None,
            ))

        self.select = discord.ui.Select(
            placeholder=placeholder[:150],
            min_values=1, max_values=1,
            options=options,
            custom_id=WIZARD_CAT_SELECT,
        )
        self.select.callback = self._on_select
        self.add_item(self.select)

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.author_id:
            await interaction.response.send_message(
                "Diese Auswahl gehört zu einer anderen Person.", ephemeral=True)
            return False
        return True

    async def _on_select(self, interaction: discord.Interaction) -> None:
        path = self.select.values[0]
        node = await self.cog.store.get_node(interaction.guild, path)
        if node is None:
            await interaction.response.edit_message(
                embed=discord.Embed(
                    description="❌ Diese Kategorie existiert nicht mehr.", color=COLOR_ERROR),
                view=None)
            return

        # Finale Kategorie → direkt Modal öffnen
        if not self.cog.store.is_group(node):
            await interaction.response.send_modal(make_modal(self.cog, node, path))
            return

        # Gruppe → Unterkategorien anbieten
        children = [
            (f"{path}{CATEGORY_PATH_SEP}{ck}", child)
            for ck, child in (node.get("children") or {}).items()
        ]
        if not children:
            await interaction.response.edit_message(
                embed=discord.Embed(
                    description="⚠️ Diese Kategorie hat noch keine Unterkategorien. "
                                "Bitte wende dich direkt an das Team.",
                    color=COLOR_ERROR),
                view=None)
            return
        if len(children) == 1:
            child_path, child = children[0]
            await interaction.response.send_modal(make_modal(self.cog, child, child_path))
            return

        emoji = (node.get("emoji") + " ") if node.get("emoji") else ""
        view = WizardSelectView(self.cog, children, self.author_id,
                                "Wähle die passende Unterkategorie …")
        await interaction.response.edit_message(
            embed=discord.Embed(
                description=f"{emoji}**{node['name']}** – wähle die passende Unterkategorie:",
                color=COLOR_INFO),
            view=view)


class TicketModal(discord.ui.Modal):
    """Modal, das dynamisch aus den Feldern einer finalen Kategorie gebaut wird."""

    def __init__(self, cog, leaf: Dict[str, Any], path: str):
        title = f"Support: {leaf.get('name', '')}"[:45]
        super().__init__(title=title, timeout=600)
        self.cog = cog
        self.path = path
        self._inputs: List[discord.ui.TextInput] = []

        fields = (leaf.get("fields") or [])[:MAX_FIELDS]
        if not fields:
            fields = [{"label": "Dein Anliegen", "style": "paragraph",
                       "required": True, "placeholder": None, "max_length": 1000}]

        for field in fields:
            ti = discord.ui.TextInput(
                label=field["label"][:45],
                style=FIELD_STYLES.get(field.get("style"), discord.TextStyle.short),
                required=bool(field.get("required", True)),
                placeholder=(field.get("placeholder") or None),
                max_length=int(field.get("max_length") or 1000),
            )
            self._inputs.append(ti)
            self.add_item(ti)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        answers: Dict[str, str] = {}
        for ti in self._inputs:
            value = (ti.value or "").strip()
            if value:
                answers[ti.label] = value
        await self.cog.finalize_ticket(interaction, self.path, answers)

    async def on_error(self, interaction: discord.Interaction, error: Exception) -> None:
        try:
            msg = "❌ Beim Erstellen des Tickets ist ein Fehler aufgetreten."
            if interaction.response.is_done():
                await interaction.followup.send(msg, ephemeral=True)
            else:
                await interaction.response.send_message(msg, ephemeral=True)
        except discord.DiscordException:
            pass
