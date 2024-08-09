import discord
from redbot.core import commands, app_commands
from redbot.core.bot import Red
from redbot.core.utils import chat_formatting as cf
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from datetime import datetime


class MyCog(commands.Cog):
    """My custom cog to send embedded messages at specific times."""

    def __init__(self, bot: Red):
        self.bot = bot
        self.channel_id = 1199322485297000528
        self.scheduler = AsyncIOScheduler()
        self.schedule_jobs()
        self.scheduler.start()

    def schedule_jobs(self):
        """Schedule all the jobs for different bosses."""
        # Garmoth
        self.scheduler.add_job(self.send_garmoth_embed, CronTrigger(hour=14, minute=0, timezone="CET"))
        self.scheduler.add_job(self.send_garmoth_embed, CronTrigger(hour=23, minute=15, timezone="CET"))

        # Karanda
        self.scheduler.add_job(self.send_karanda_embed, CronTrigger(day_of_week='mon', hour=0, minute=15, timezone="CET"))
        self.scheduler.add_job(self.send_karanda_embed, CronTrigger(day_of_week='mon', hour=2, minute=0, timezone="CET"))
        self.scheduler.add_job(self.send_karanda_embed, CronTrigger(day_of_week='tue', hour=0, minute=15, timezone="CET"))
        self.scheduler.add_job(self.send_karanda_embed, CronTrigger(day_of_week='tue', hour=19, minute=0, timezone="CET"))
        self.scheduler.add_job(self.send_karanda_embed, CronTrigger(day_of_week='wed', hour=2, minute=0, timezone="CET"))
        self.scheduler.add_job(self.send_karanda_embed, CronTrigger(day_of_week='wed', hour=9, minute=0, timezone="CET"))
        self.scheduler.add_job(self.send_karanda_embed, CronTrigger(day_of_week='wed', hour=22, minute=15, timezone="CET"))
        self.scheduler.add_job(self.send_karanda_embed, CronTrigger(day_of_week='fri', hour=0, minute=15, timezone="CET"))
        self.scheduler.add_job(self.send_karanda_embed, CronTrigger(day_of_week='fri', hour=5, minute=0, timezone="CET"))
        self.scheduler.add_job(self.send_karanda_embed, CronTrigger(day_of_week='fri', hour=12, minute=0, timezone="CET"))
        self.scheduler.add_job(self.send_karanda_embed, CronTrigger(day_of_week='sat', hour=0, minute=15, timezone="CET"))
        self.scheduler.add_job(self.send_karanda_embed, CronTrigger(day_of_week='sat', hour=19, minute=0, timezone="CET"))

        # Kutum
        self.scheduler.add_job(self.send_kutum_embed, CronTrigger(day_of_week='mon', hour=0, minute=15, timezone="CET"))
        self.scheduler.add_job(self.send_kutum_embed, CronTrigger(day_of_week='mon', hour=16, minute=0, timezone="CET"))
        self.scheduler.add_job(self.send_kutum_embed, CronTrigger(day_of_week='tue', hour=2, minute=0, timezone="CET"))
        self.scheduler.add_job(self.send_kutum_embed, CronTrigger(day_of_week='tue', hour=12, minute=0, timezone="CET"))
        self.scheduler.add_job(self.send_kutum_embed, CronTrigger(day_of_week='wed', hour=0, minute=15, timezone="CET"))
        self.scheduler.add_job(self.send_kutum_embed, CronTrigger(day_of_week='wed', hour=16, minute=0, timezone="CET"))
        self.scheduler.add_job(self.send_kutum_embed, CronTrigger(day_of_week='thu', hour=2, minute=0, timezone="CET"))
        self.scheduler.add_job(self.send_kutum_embed, CronTrigger(day_of_week='thu', hour=9, minute=0, timezone="CET"))
        self.scheduler.add_job(self.send_kutum_embed, CronTrigger(day_of_week='thu', hour=19, minute=0, timezone="CET"))
        self.scheduler.add_job(self.send_kutum_embed, CronTrigger(day_of_week='fri', hour=9, minute=0, timezone="CET"))
        self.scheduler.add_job(self.send_kutum_embed, CronTrigger(day_of_week='fri', hour=22, minute=15, timezone="CET"))
        self.scheduler.add_job(self.send_kutum_embed, CronTrigger(day_of_week='sat', hour=9, minute=0, timezone="CET"))
        self.scheduler.add_job(self.send_kutum_embed, CronTrigger(day_of_week='sun', hour=0, minute=15, timezone="CET"))
        self.scheduler.add_job(self.send_kutum_embed, CronTrigger(day_of_week='sun', hour=5, minute=0, timezone="CET"))

        # Kzarka
        self.scheduler.add_job(self.send_kzarka_embed, CronTrigger(day_of_week='mon', hour=5, minute=0, timezone="CET"))
        self.scheduler.add_job(self.send_kzarka_embed, CronTrigger(day_of_week='mon', hour=9, minute=0, timezone="CET"))
        self.scheduler.add_job(self.send_kzarka_embed, CronTrigger(day_of_week='mon', hour=22, minute=15, timezone="CET"))
        self.scheduler.add_job(self.send_kzarka_embed, CronTrigger(day_of_week='tue', hour=5, minute=0, timezone="CET"))
        self.scheduler.add_job(self.send_kzarka_embed, CronTrigger(day_of_week='wed', hour=0, minute=15, timezone="CET"))
        self.scheduler.add_job(self.send_kzarka_embed, CronTrigger(day_of_week='wed', hour=5, minute=0, timezone="CET"))
        self.scheduler.add_job(self.send_kzarka_embed, CronTrigger(day_of_week='wed', hour=22, minute=15, timezone="CET"))
        self.scheduler.add_job(self.send_kzarka_embed, CronTrigger(day_of_week='thu', hour=16, minute=0, timezone="CET"))
        self.scheduler.add_job(self.send_kzarka_embed, CronTrigger(day_of_week='fri', hour=0, minute=15, timezone="CET"))
        self.scheduler.add_job(self.send_kzarka_embed, CronTrigger(day_of_week='fri', hour=19, minute=0, timezone="CET"))
        self.scheduler.add_job(self.send_kzarka_embed, CronTrigger(day_of_week='sat', hour=19, minute=0, timezone="CET"))
        self.scheduler.add_job(self.send_kzarka_embed, CronTrigger(day_of_week='sun', hour=2, minute=0, timezone="CET"))
        self.scheduler.add_job(self.send_kzarka_embed, CronTrigger(day_of_week='sun', hour=12, minute=0, timezone="CET"))
        self.scheduler.add_job(self.send_kzarka_embed, CronTrigger(day_of_week='sun', hour=22, minute=15, timezone="CET"))

    async def send_garmoth_embed(self):
        """Send Garmoth embed message to the specified channel."""
        channel = self.bot.get_channel(self.channel_id)
        if channel:
            title = "Garmoth ist erschienen!"
            description = "Garmoth's Gebrüll hallt durch Garmoth's Nest"
            location_value = "Garmoth's Nest, Drieghan\n[Öffne Karte](https://www.blackdesertfoundry.com/map/?lat=-47.68018294648414&lng=8.415527343750002&M=Garmoth#7/-47.372/8.690)"
            loot_value = "Garmoth's Herz <:garmothheart:1199730542325796934>"
            image_url = "https://raw.githubusercontent.com/XardasDark/Dark-Cogs/main/media/img/garmoth.jpg"
            embed = self.create_embed(title, description, location_value, loot_value, image_url)
            await channel.send(embed=embed)

    async def send_karanda_embed(self):
        """Send Karanda embed message to the specified channel."""
        channel = self.bot.get_channel(self.channel_id)
        if channel:
            title = "Karanda ist erschienen!"
            description = "Hoch oben auf dem Bergkamm befehlen Karandas Flügel den Harpyien zu brüllen"
            location_value = "Höchster Gipfel des Karanda Kammes im nordöstlichen Calpheon\n[Öffne Karte](https://www.blackdesertfoundry.com/map/?lat=-13.944729974920167&lng=-4.910888671875001&M=Karanda#6/-13.635/-5.251)"
            loot_value = "Karandas Herz\nLöwenzahn Erweckungswaffe"
            image_url = "https://raw.githubusercontent.com/XardasDark/Dark-Cogs/main/media/img/karanda.jpg"
            embed = self.create_embed(title, description, location_value, loot_value, image_url)
            await channel.send(embed=embed)

    async def send_kutum_embed(self):
        """Send Kutum embed message to the specified channel."""
        channel = self.bot.get_channel(self.channel_id)
        if channel:
            title = "Kutum ist erschienen!"
            description = "Das Herz des alten Kutum schlägt in der Scharlachsandkammer"
            location_value = "Am Boden der Scharlachsandkammer nordöstlich vom Felß-Außenposten\n[Öffne Karte](https://www.blackdesertfoundry.com/map/?lat=-2.284550660236957&lng=69.89501953125001&M=Kutum#7/-2.136/69.521)"
            loot_value = "Kutums Herz\nKutum Sekundärwaffe"
            image_url = "https://raw.githubusercontent.com/XardasDark/Dark-Cogs/main/media/img/kutum.jpg"
            embed = self.create_embed(title, description, location_value, loot_value, image_url)
            await channel.send(embed=embed)

    async def send_kzarka_embed(self):
        """Send Kzarka embed message to the specified channel."""
        channel = self.bot.get_channel(self.channel_id)
        if channel:
            title = "Kzarka ist erschienen!"
            description = "Das Gebrüll von Kzarka, dem Herrn der Verderbnis, bringt ganz Serendia zum Beben"
            location_value = "In den Tiefen des Serendia-Schreins im Süden von Serendia\n[Öffne Karte](https://www.blackdesertfoundry.com/map/?lat=-36.738884124394296&lng=16.040039062500004&M=Kzarka#7/-36.858/15.194)"
            loot_value = "Kzarka Hauptwaffe"
            image_url = "https://raw.githubusercontent.com/XardasDark/Dark-Cogs/main/media/img/kzarka.jpg"
            embed = self.create_embed(title, description, location_value, loot_value, image_url)
            await channel.send(embed=embed)

    def create_embed(self, title, description, location_value, loot_value, image_url):
        """Helper function to create an embedded message."""
        embed = discord.Embed(
            title=title,
            url="https://garmoth.com/boss-timer",
            description=description,
            color=0xff9600
        )
        embed.set_author(
            name="Boss Timer", 
            icon_url="https://raw.githubusercontent.com/XardasDark/Dark-Cogs/main/media/img/hourglass.png"
        )
        embed.add_field(
            name="Standort 📍", 
            value=location_value, 
            inline=False
        )
        embed.add_field(
            name="Beute 💰", 
            value=loot_value, 
            inline=False
        )
        embed.set_image(url=image_url)
        return embed