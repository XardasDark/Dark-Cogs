"""
feedback.py – kurze Zufriedenheits-Bewertung nach dem Schließen

Der Ersteller bekommt (optional) eine DM mit einer 1–5-Sterne-Auswahl. Die Wahl
wird mit Bezug zur Guild/Kategorie gespeichert und kann später für Statistiken
ausgewertet werden.

Die View ist kurzlebig (DM, eigener Callback). Da DM-Interaktionen keine Guild
haben, trägt die View die nötige guild_id/category selbst.
"""

from typing import Optional

import discord

from .constants import COLOR_INFO, COLOR_TICKET

STAR_OPTIONS = [
    ("5", "⭐⭐⭐⭐⭐ Sehr zufrieden"),
    ("4", "⭐⭐⭐⭐ Zufrieden"),
    ("3", "⭐⭐⭐ Geht so"),
    ("2", "⭐⭐ Unzufrieden"),
    ("1", "⭐ Sehr unzufrieden"),
]


def build_feedback_dm(guild_name: str, label: str) -> discord.Embed:
    return discord.Embed(
        title="⭐ Wie zufrieden warst du mit dem Support?",
        description=(f"Dein Ticket in **{label}** auf **{guild_name}** wurde geschlossen.\n"
                     "Dein Feedback hilft uns, besser zu werden – wähle einfach unten aus."),
        color=COLOR_INFO)


class FeedbackView(discord.ui.View):
    def __init__(self, cog, guild_id: int, user_id: int, ticket_id: str,
                 category_key: str, label: str):
        super().__init__(timeout=86400)  # 24 h Zeit zum Bewerten
        self.cog = cog
        self.guild_id = guild_id
        self.user_id = user_id
        self.ticket_id = ticket_id
        self.category_key = category_key
        self.label = label

        select = discord.ui.Select(
            placeholder="Bewertung abgeben …", min_values=1, max_values=1,
            options=[discord.SelectOption(label=text, value=val) for val, text in STAR_OPTIONS])
        select.callback = self._on_select
        self.add_item(select)

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        return interaction.user.id == self.user_id

    async def _on_select(self, interaction: discord.Interaction) -> None:
        rating = int(interaction.data["values"][0])
        await self.cog.store_feedback(
            self.guild_id, self.user_id, self.ticket_id, self.category_key, self.label, rating)
        self.clear_items()
        self.stop()
        await interaction.response.edit_message(
            embed=discord.Embed(
                title="✅ Danke für dein Feedback!",
                description=f"Du hast **{'⭐' * rating}** vergeben.",
                color=COLOR_TICKET),
            view=None)
