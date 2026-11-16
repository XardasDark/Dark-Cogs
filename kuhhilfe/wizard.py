"""
wizard.py – ephemerer Support-Wizard für KuhHilfe

Zwei kurzlebige, nutzer-spezifische Komponenten (eigene View-/Modal-Callbacks,
keine Persistenz nötig, da sie pro Klick frisch erzeugt werden):

* CategorySelectView – Auswahl der Kategorie (nur wenn es >1 Kategorie gibt)
* TicketModal        – aus den pro Kategorie konfigurierten Feldern gebaut
"""

from typing import Any, Dict, List

import discord

from .constants import WIZARD_CAT_SELECT, FIELD_STYLES, MAX_FIELDS, COLOR_ERROR


class CategorySelectView(discord.ui.View):
    """Ephemere Kategorie-Auswahl; öffnet nach der Wahl das passende Modal."""

    def __init__(self, cog, categories: Dict[str, Dict[str, Any]], author_id: int):
        super().__init__(timeout=180)
        self.cog = cog
        self.author_id = author_id

        options: List[discord.SelectOption] = []
        for cat in list(categories.values())[:25]:
            options.append(discord.SelectOption(
                label=cat["name"][:100],
                value=cat["key"],
                description=(cat.get("description") or None) and cat["description"][:100],
                emoji=cat.get("emoji") or None,
            ))

        self.select = discord.ui.Select(
            placeholder="Wähle die passende Kategorie …",
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
        key = self.select.values[0]
        category = await self.cog.store.get_category(interaction.guild, key)
        if not category:
            await interaction.response.send_message(
                embed=discord.Embed(
                    description="❌ Diese Kategorie existiert nicht mehr.",
                    color=COLOR_ERROR),
                ephemeral=True)
            return
        await interaction.response.send_modal(TicketModal(self.cog, category))


class TicketModal(discord.ui.Modal):
    """Modal, das dynamisch aus den Feldern einer Kategorie gebaut wird."""

    def __init__(self, cog, category: Dict[str, Any]):
        title = f"Support: {category.get('name', '')}"[:45]
        super().__init__(title=title, timeout=600)
        self.cog = cog
        self.category = category
        self._inputs: List[discord.ui.TextInput] = []

        fields = (category.get("fields") or [])[:MAX_FIELDS]
        if not fields:
            # Fallback, falls eine Kategorie ohne Felder konfiguriert wurde
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
        await self.cog.finalize_ticket(interaction, self.category, answers)

    async def on_error(self, interaction: discord.Interaction, error: Exception) -> None:
        try:
            msg = "❌ Beim Erstellen des Tickets ist ein Fehler aufgetreten."
            if interaction.response.is_done():
                await interaction.followup.send(msg, ephemeral=True)
            else:
                await interaction.response.send_message(msg, ephemeral=True)
        except discord.DiscordException:
            pass
