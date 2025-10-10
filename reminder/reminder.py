import asyncio
import discord
from redbot.core import commands, app_commands
from redbot.core.bot import Red
from redbot.core.utils import chat_formatting as cf
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from datetime import datetime, timedelta
import humanize


class Reminder(commands.Cog):
    """My custom cog to send embedded messages at specific times."""

    def __init__(self, bot: Red):
        self.bot = bot
        
    async def initialize(self):
        job_defaults = {
            "coalesce": True,  # Multiple missed triggers within the grace time will only fire once
            "max_instances": 1,  # This is probably way too high, should likely only be one
            "misfire_grace_time": 15,  # 15 seconds ain't much, but it's honest work
            "replace_existing": True,  # Very important for persistent data
        }
        self.scheduler.configure(job_defaults=job_defaults)
        self.schedule_jobs()  # Schedule all boss notifications
        self.scheduler.start()
        self.jobs = {}  # Store jobs by boss name
        
    
    
        
    """     async def cog_unload(self):
        if hasattr(self, 'scheduler') and self.scheduler is not None:
            try:
                if self.scheduler.get_jobs():
                    await self.scheduler.remove_all_jobs()  # Remove all scheduled jobs
            except Exception as e:
                print(f"Error while removing jobs: {e}")
            try:
                #await self.scheduler.shutdown()  # Shutdown the scheduler
                pass
            except Exception as e:
                print(f"Error while shutting down scheduler: {e}")
        else:
            print("Scheduler was not initialized or already cleaned up.")

        try:
            await self.bot.shutdown()  # Shutdown the bot
        except Exception as e:
            print(f"Error while shutting down bot: {e}") """
            
    @app_commands.command()
    @app_commands.describe(channel="The channel you want to mention")
    async def mentionchannel(self, interaction: discord.Interaction, channel: discord.abc.GuildChannel):
        await interaction.response.send_message(f"That channel is {channel.mention}", ephemeral=True)

    reminder = app_commands.Group(name="reminder", description="Werde erinnert")
    
    reminder.command(name="Zeitpunkt", description="Wann möchtest du erinnert werden?")
    @app_commands.describe(timedate="Wann möchtest du erinnert werden?!")
    @app_commands.choices(timedate=[
        app_commands.Choice(name="In 5m", value="5m"),
        app_commands.Choice(name="In 12h", value="12h"),
        app_commands.Choice(name="In 14 Tagen", value="14d"),
    ])
    async def remindertimedate(self, interaction: discord.Interaction):
        await interaction.response.send_message(f"Du hast {} als Zeitpunkt ausgewählt", ephemeral=True)
        
            
    # / Command reminder Zeit in s m h d w oder in DD.MM mit/ohne HH:mm oder in DD.MM.YYYY mit HH:mm als 1. Parameter 
    # und ein Textfeld als 2. Parameter
    
    # Rückmeldung als embedded ob Befehl so korrekt ausgeführt. Wenn falsch dann private Nachricht, wenn korrekt dann für alle sichtbar
    
    # Reminder Logic
    
    # Reminder Nachricht als embedded in dem Channel in dem die Nachricht ausgeführt wurde
    
    # Bot Shutdown Logic