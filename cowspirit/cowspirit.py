import discord
from redbot.core import commands, app_commands
from redbot.core.bot import Red
from redbot.core.utils import chat_formatting as cf
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from datetime import datetime


class CowSpirit(commands.Cog):
    """My custom cog to send embedded messages at specific times."""

    def __init__(self, bot: Red):
        self.bot = bot
        self.channel_id = 1274113015427502192
        self.scheduler = AsyncIOScheduler()
        self.scheduler_running = False  # Track whether the scheduler is active
        self.schedule_jobs()
    
    @commands.command()    
    async def foo(self, ctx):
        """Description of myfirstcom visible with [p]help myfirstcom"""
        # Your code will go here
        await ctx.send("My first cog!")

    @commands.command()
    async def cowspirit(self, ctx):
        """Toggle the boss notification scheduler."""
        if self.scheduler_running:
            self.scheduler.pause()  # Pause the scheduler
            self.scheduler_running = False
            await ctx.send("Boss notifications deactivated.")
        else:
            self.scheduler.resume()  # Resume the scheduler
            self.scheduler_running = True
            await ctx.send("Boss notifications activated.")

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

        # Nouver
        self.scheduler.add_job(self.send_nouver_embed, CronTrigger(day_of_week='mon', hour=19, minute=15, timezone="CET"))
        self.scheduler.add_job(self.send_nouver_embed, CronTrigger(day_of_week='tue', hour=9, minute=0, timezone="CET"))
        self.scheduler.add_job(self.send_nouver_embed, CronTrigger(day_of_week='wed', hour=12, minute=0, timezone="CET"))
        self.scheduler.add_job(self.send_nouver_embed, CronTrigger(day_of_week='thu', hour=0, minute=15, timezone="CET"))
        self.scheduler.add_job(self.send_nouver_embed, CronTrigger(day_of_week='thu', hour=9, minute=0, timezone="CET"))
        self.scheduler.add_job(self.send_nouver_embed, CronTrigger(day_of_week='fri', hour=19, minute=0, timezone="CET"))
        self.scheduler.add_job(self.send_nouver_embed, CronTrigger(day_of_week='sat', hour=14, minute=0, timezone="CET"))
        self.scheduler.add_job(self.send_nouver_embed, CronTrigger(day_of_week='sun', hour=16, minute=0, timezone="CET"))

        # Offin
        self.scheduler.add_job(self.send_offin_embed, CronTrigger(day_of_week='mon', hour=14, minute=0, timezone="CET"))
        self.scheduler.add_job(self.send_offin_embed, CronTrigger(day_of_week='wed', hour=16, minute=0, timezone="CET"))
        self.scheduler.add_job(self.send_offin_embed, CronTrigger(day_of_week='sat', hour=12, minute=0, timezone="CET"))

        # Quint & Muraka
        self.scheduler.add_job(self.send_quint_muraka_embed, CronTrigger(day_of_week='tue', hour=22, minute=15, timezone="CET"))
        self.scheduler.add_job(self.send_quint_muraka_embed, CronTrigger(day_of_week='thu', hour=22, minute=15, timezone="CET"))
        
        # Vell
        self.scheduler.add_job(self.send_vell_embed, CronTrigger(day_of_week='wed', hour=19, minute=0, timezone="CET"))
        self.scheduler.add_job(self.send_vell_embed, CronTrigger(day_of_week='sun', hour=16, minute=0, timezone="CET"))


    async def send_garmoth_embed(self):
        """Send Garmoth embed message to the specified channel."""
        channel = self.bot.get_channel(self.channel_id)
        if channel:
            title = "Garmoth ist erschienen!"
            description = "Garmoth's Gebrüll hallt durch Garmoth's Nest"
            location_value = "Garmoth's Nest, Drieghan\n[Öffne Karte](https://www.blackdesertfoundry.com/map/?lat=-47.68018294648414&lng=8.415527343750002&M=Garmoth#7/-47.372/8.690)"
            loot_value = "Garmoth's Herz <:garmothheart:1199730542325796934>"
            image_url = "media/img/garmoth.jpg"
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
            image_url = "media/img/karanda.jpg"
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
            image_url = "media/img/kutum.jpg"
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
            image_url = "media/img/kzarka.jpg"
            embed = self.create_embed(title, description, location_value, loot_value, image_url)
            await channel.send(embed=embed)

    async def send_nouver_embed(self):
        """Send Nouver embed message to the specified channel."""
        channel = self.bot.get_channel(self.channel_id)
        if channel:
            title = "Nouver ist erschienen!"
            description = "Die Spuren von Nouver wurden nach einem heftigen Sandsturm entdeckt"
            location_value = "Südöstlich des Sandkornbasars, innerhalb der Wüste\n[Öffne Karte](https://www.blackdesertfoundry.com/map/?lat=-16.741427547003596&lng=90.96679687500001&M=Nouver#6/-16.815/88.484)"
            loot_value = "Nouver Sekundärwaffe"
            image_url = "media/img/nouver.jpg"
            embed = self.create_embed(title, description, location_value, loot_value, image_url)
            await channel.send(embed=embed)

    async def send_offin_embed(self):
        """Send Offin embed message to the specified channel."""
        channel = self.bot.get_channel(self.channel_id)
        if channel:
            title = "Offin ist erschienen!"
            description = "Offin ist erwacht. Offin absorbiert kontinuierlich die Energie der Geister"
            location_value = "Im Holo Wald nördlich von Grana\n[Öffne Karte](https://www.blackdesertfoundry.com/map/?lat=-50.02185841773444&lng=-39.39697265625001&M=Offin#7/-50.173/-39.518)"
            loot_value = "Offin Hauptwaffe"
            image_url = "media/img/offin.jpg"
            embed = self.create_embed(title, description, location_value, loot_value, image_url)
            await channel.send(embed=embed)

    async def send_quint_muraka_embed(self):
        """Send Quint & Muraka embed message to the specified channel."""
        channel = self.bot.get_channel(self.channel_id)
        if channel:
            title = "Quint und Muraka sind erschienen!"
            description = "Der König der Oger und der erste Troll sind dabei, der Welt zu erscheinen"
            location_value = "Quint: Erscheint westlich vom Quintenhügel/östlich vom Epheriaport. Muraka erscheint westlich vom Manshawald/Kaiasee\n[Öffne Karte - Quint](https://www.blackdesertfoundry.com/map/?lat=-17.5602465032949&lng=-25.598144531250004&M=Quint#7/-17.188/-25.576)\n[Öffne Karte - Muraka](https://www.blackdesertfoundry.com/map/?lat=-27.994401411046148&lng=-33.07983398437501&M=Muraka#7/-27.951/-33.102)"
            loot_value = "Ogerring / Mutantenverstärker"
            image_url = "media/img/quintmuraka.jpg"
            embed = self.create_embed(title, description, location_value, loot_value, image_url)
            await channel.send(embed=embed)
            
    async def send_vell_embed(self):
        """Send Vell embed message to the specified channel."""
        channel = self.bot.get_channel(self.channel_id)
        if channel:
            title = "Vell ist erschienen!"
            description = "Vell neutralisiert den Siegelstein mit seinem kataklysmischen Zorn"
            location_value = "Vell's Realm im nördlichen Meer\n[Öffne Karte](https://www.blackdesertfoundry.com/map/?lat=64.92819764459557&lng=8.096923828125002&M=Vell#4/49.07/-25.22)"
            loot_value = "Vell's Herz <:vell:1203017198491410462>\nVell's Konzentrierte Magie"
            image_url = "media/img/vell.jpg"
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
            icon_url="media/img/hourglass.png"
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
