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
from discord.ui import Button, View

class BDOGroupFinder(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.lfg_requests = {}  # Initialize LFG storage
        self.user_messages = {}  # To store user input messages for cleanup

    @app_commands.command(name="buttontest")
    async def buttontest(self, interaction: discord.Interaction):
        """A command to test buttons interaction"""

        # Embed for button testing
        embed = discord.Embed(
            title="Choose Your Action!",
            description="Press one of the buttons below to perform an action.",
            color=discord.Color.blue()
        )

        # Define the action callback for button presses
        async def button_callback(interaction: discord.Interaction, action_name: str):
            await interaction.response.send_message(f"You pressed {action_name}!", ephemeral=True)

        # Define the buttons with actions and add to View
        buttons = [
            ("Action 1", "⚔️", discord.ButtonStyle.primary, "Attack"),
            ("Action 2", "🛡️", discord.ButtonStyle.success, "Defend"),
            ("Action 3", "🍃", discord.ButtonStyle.secondary, "Heal"),
            ("Action 4", "🔥", discord.ButtonStyle.danger, "Cast Spell"),
            ("Action 5", "🏃", discord.ButtonStyle.primary, "Run Away"),
            ("Action 6", "💰", discord.ButtonStyle.success, "Collect Gold"),
            ("Action 7", "🔍", discord.ButtonStyle.secondary, "Search"),
            ("Action 8", "👀", discord.ButtonStyle.danger, "Look Around")
        ]

        # Add buttons to a view with partial for each button's callback
        view = View()
        for label, emoji, style, action in buttons:
            button = Button(label=label, style=style, emoji=emoji)
            button.callback = partial(button_callback, action_name=action)  # Set the action name
            view.add_item(button)

        # Send embed and view with buttons
        await interaction.response.send_message(embed=embed, view=view)

    @app_commands.command(name="lfg")
    async def lfg(self, interaction: discord.Interaction):
        """Slash command to start the LFG setup process."""
        await interaction.response.defer(ephemeral=True)
        self.user_messages[interaction.user.id] = []

        async def send_ephemeral(content):
            msg = await interaction.followup.send(content, ephemeral=True)
            self.user_messages[interaction.user.id].append(msg)
            return msg

        # Ask for title
        await send_ephemeral("Titel:")
        title_msg = await self.bot.wait_for('message', check=lambda m: m.author == interaction.user and m.channel == interaction.channel)
        title = title_msg.content
        self.user_messages[interaction.user.id].append(title_msg)

        # Ask for description
        await send_ephemeral("Beschreibung (optional, schreibe 'skip' um zu skippen):")
        desc_msg = await self.bot.wait_for('message', check=lambda m: m.author == interaction.user and m.channel == interaction.channel)
        description = desc_msg.content if desc_msg.content.lower() != 'skip' else None
        self.user_messages[interaction.user.id].append(desc_msg)

        # Ask for group type (temporary or permanent)
        group_type, event_date = await self.ask_group_type(interaction)

        # Ask for number of players needed
        await send_ephemeral("Nach wie vielen Spielern suchst du? (Dich eingeschlossen)")
        players_needed_msg = await self.bot.wait_for('message', check=lambda m: m.author == interaction.user and m.channel == interaction.channel)
        players_needed = int(players_needed_msg.content)
        self.user_messages[interaction.user.id].append(players_needed_msg)

        # Ask how many players are already in the group
        await send_ephemeral("Wie viele Spieler sind bereits in der Gruppe? (Dich eingeschlossen)")
        current_players_msg = await self.bot.wait_for('message', check=lambda m: m.author == interaction.user and m.channel == interaction.channel)
        current_players = int(current_players_msg.content)
        self.user_messages[interaction.user.id].append(current_players_msg)

        # Confirm details before posting
        await send_ephemeral("Here are the details of your LFG request. Type 'confirm' to post or 'cancel' to abort.")
        await send_ephemeral(f"**Title**: {title}\n"
                             f"**Description**: {description or 'No description'}\n"
                             f"**Group Type**: {group_type.capitalize()}\n"
                             f"**Players Needed**: {current_players}/{players_needed}\n"
                             f"{f'**Event Date**: {event_date}' if event_date else ''}")
        confirm_msg = await self.bot.wait_for('message', check=lambda m: m.author == interaction.user and m.channel == interaction.channel)
        self.user_messages[interaction.user.id].append(confirm_msg)

        if confirm_msg.content.lower() != "confirm":
            await interaction.followup.send("Gruppenerstellung abgebrochen:", ephemeral=True)
            await self.cleanup_messages(interaction.user.id)
            return

        # Create the embed
        embed = BDOGroupFinder.create_embed(title, description, group_type, players_needed, current_players, event_date)
        lfg_post = await interaction.channel.send(embed=embed)

        # Add the LFG request to the list and add a reaction for players to join
        self.lfg_requests[lfg_post.id] = {
            "author": interaction.user.id,
            "title": title,
            "desc": description,
            "group_type": group_type,
            "num_needed": players_needed,
            "current_players": current_players,
            "event_date": event_date,
        }
        await lfg_post.add_reaction("✅")

        # Cleanup messages after confirm
        await self.cleanup_messages(interaction.user.id)

    async def ask_group_type(self, interaction):
        """Ask whether the group is temporary or permanent using ephemeral messages"""
        
        # Get the current date and time
        now = datetime.now()
        # Add one hour to the current time
        future_time = now + timedelta(hours=1)
        # Format the date and time
        formatted_time = future_time.strftime("%d.%m.%Y %H:%M")
        
        await interaction.followup.send("Ist das eine temporäre oder permanente Anfrage? (Type 'temporary' or 'permanent')", ephemeral=True)
        group_type_msg = await self.bot.wait_for('message', check=lambda m: m.author == interaction.user and m.channel == interaction.channel)
        group_type = group_type_msg.content.lower()
        self.user_messages[interaction.user.id].append(group_type_msg)

        if group_type == "temporary":
            await interaction.followup.send(f"Enter the date and time for the activity (e.g., {formatted_time}):", ephemeral=True)
            date_msg = await self.bot.wait_for('message', check=lambda m: m.author == interaction.user and m.channel == interaction.channel)
            event_date = datetime.strptime(date_msg.content, "%d.%m.%Y %H:%M")
            self.user_messages[interaction.user.id].append(date_msg)
            return "temporary", event_date
        return "permanent", None

    @staticmethod
    def create_embed(title, description, group_type, num_needed, current_players, event_date=None):
        """Helper function to create the embedded message."""
        embed = discord.Embed(title=title, color=discord.Color.blue())  # Custom color to match

        # Add event information
        embed.add_field(name="Event Info:", value=f"📅 {event_date.strftime('%d.%m.%Y') if event_date else 'TBD'}\n⏰ {event_date.strftime('18:00')} - {event_date.strftime('19:00')} (Dauer)", inline=False)
        
        # Add description if available
        embed.add_field(name="Beschreibung:", value=description or "-", inline=False)
        
        # Add roles (You can customize these based on your game's roles)
        embed.add_field(name="Shai (1/1)", value="💉 Player1", inline=True)
        embed.add_field(name="DPS (3/4)", value="⚔️ Player1\n⚔️ Player2\n⚔️ Player3", inline=True)

        # Example of another role category
        #embed.add_field(name="POLYVALENT (3)", value="🎯 Player1\n🎯 Player2\n🎯 Player3", inline=True)

        # Footer for status info
        embed.set_footer(text=f"Angemeldet: {current_players}/{num_needed} - Event ID: 112733737692618782\nEvent Startzeit: {event_date.strftime('%d.%m.%Y %H:%M') if event_date else 'TBD'}")
        
        # Adding reaction icons for users to interact with
        embed.add_field(name="Options:", value="🛠️ Edit Event\n❌ Cancel Event", inline=False)

        return embed

    @commands.Cog.listener()
    async def on_reaction_add(self, reaction, user):
        """Handle players reacting to join the LFG post with ✅."""
        message = reaction.message
        if message.id in self.lfg_requests and user.id != self.bot.user.id:
            if str(reaction.emoji) == "✅":  # Only handle the ✅ reaction
                lfg_data = self.lfg_requests[message.id]
                if lfg_data["current_players"] < lfg_data["num_needed"]:
                    lfg_data["current_players"] += 1
                    updated_embed = BDOGroupFinder.create_embed(
                        lfg_data["title"], lfg_data["desc"], lfg_data["group_type"],
                        lfg_data["num_needed"], lfg_data["current_players"], lfg_data["event_date"]
                    )
                    await message.edit(embed=updated_embed)
                else:
                    await user.send("The group is already full!")

    @commands.Cog.listener()
    async def on_reaction_remove(self, reaction, user):
        """Handle players removing their ✅ reaction from the LFG post."""
        message = reaction.message
        if message.id in self.lfg_requests and user.id != self.bot.user.id:
            if str(reaction.emoji) == "✅":  # Only handle the ✅ reaction
                lfg_data = self.lfg_requests[message.id]
                if lfg_data["current_players"] > 0:
                    lfg_data["current_players"] -= 1
                    updated_embed = BDOGroupFinder.create_embed(
                        lfg_data["title"], lfg_data["desc"], lfg_data["group_type"],
                        lfg_data["num_needed"], lfg_data["current_players"], lfg_data["event_date"]
                    )
                    await message.edit(embed=updated_embed)
                else:
                    await user.send("No players left to remove!")


    async def cleanup_messages(self, user_id):
        """Delete all messages stored for a user after confirmation or cancellation."""
        if user_id in self.user_messages:
            for msg in self.user_messages[user_id]:
                try:
                    await msg.delete()
                except discord.NotFound:
                    pass
            del self.user_messages[user_id]