from functools import partial  # Add this import for partial function
from discord import app_commands
from discord.ext import commands
import discord
from datetime import datetime, timedelta
from redbot.core import commands
from redbot.core.bot import Red
from redbot.core.utils import chat_formatting as cf
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
import humanize
import json
from discord.ui import Button, View

class BDOGroupFinder(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        with open("red-cogs/bdogroupfinder/templates.json", "r", encoding="utf-8") as f:
            self.templates = json.load(f)

    # Registriere das Cog
    @commands.Cog.listener()
    async def on_ready(self):
        print("BDOGroupFinder Cog ist bereit.")
        
    async def get_custom_emoji(self, emoji_id):
        # Fetch the emoji from the bot's guilds
        for guild in self.bot.guilds:
            emoji = discord.utils.get(guild.emojis, id=int(emoji_id))
            if emoji:
                return emoji
        return None

    @app_commands.command(name="lfg_create", description="Erstelle eine neue Gruppensuche.")
    async def lfg_create(self, interaction: discord.Interaction):
        # Sende Nachricht an User
        try:
            await interaction.response.send_message("Die Gruppensuche wurde gestartet. Bitte prüfe deine DMs.", ephemeral=True)
        except discord.Forbidden:
            await interaction.followup.send("Ich konnte dir keine DM senden. Bitte aktiviere deine Direktnachrichten.", ephemeral=True)

        embed = discord.Embed(
            title="Gruppensuche starten",
            description="Wähle ein Template aus der Dropdown-Liste aus.",
            color=discord.Color.blue()
        )
        # Generate dropdown options with custom emojis
        options = []
        for template in self.templates["templates"]:
            emoji = await self.get_custom_emoji(template["emoji_id"]) or "🔥"  # Default emoji
            options.append(discord.SelectOption(
                label=template["name"],
                emoji=emoji,  # Use the custom emoji
                value=str(template["id"])
            ))
        
        dropdown = discord.ui.Select(placeholder="Wähle ein Template aus...", options=options)

        
        view = discord.ui.View()
        view.add_item(dropdown)

        await interaction.user.send(embed=embed, view=view)
        
    async def callback(self, interaction: discord.Interaction):
        selected_template = int(self.values[0])
        await interaction.response.send_message(f"Du hast das Template mit der ID {selected_template} gewählt.", ephemeral=True)