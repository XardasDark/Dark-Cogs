import asyncio
import discord
from redbot.core import commands, app_commands
from redbot.core.bot import Red
from redbot.core.utils import chat_formatting as cf
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from datetime import datetime, timedelta
import humanize


class CowSpirit(commands.Cog):
    """My custom cog to send embedded messages at specific times."""

    def __init__(self, bot: Red):
        super().__init__()
        self.bot = bot
        self.channel_id = 1276521873030774804
        self.role_id = 1276530764883689618
        self.scheduler = AsyncIOScheduler()
        self.scheduler_running = True  # Track whether the scheduler is active


        
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
        
    
    
        
    async def cog_unload(self):
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
            print(f"Error while shutting down bot: {e}")

#     sched.add_job(myFunc, 'cron', minute='*', start_date=datetime(2023,1,1), end_date=datetime(2023,1,3))
# for job in sched.get_jobs():
#     print(job.next_run_time)
# sched.start()
    

    @commands.command()
    async def list_jobs(self, ctx):
        """Print all currently scheduled jobs."""
        jobs = self.scheduler.get_jobs()
        
        if jobs:
            job_info = "\n".join([f"ID: {job.id} | Next Run: {humanize.naturaltime(job.next_run_time)}" for job in jobs])
            await ctx.send(f"All upcoming jobs:\n```{job_info}```")
        else:
            await ctx.send("No jobs are currently scheduled.")
    
    @commands.command()
    async def list_jobs(self, ctx):
        jobs = self.scheduler.get_jobs()
        job_list = "\n".join([str(job) for job in jobs])
        await ctx.send(f"Current jobs:\n{job_list}")

    @commands.command()
    async def print_jo(self, ctx):
        """Print all currently scheduled jobs."""
        jobs = self.scheduler.get_jobs()
        if jobs:
            job_info = "\n".join([f"{job.id}: {job.next_run_time}" for job in jobs])
            await ctx.send(f"All jobs:\n{cf.box(job_info)}")
        else:
            await ctx.send("No jobs scheduled.")
        
    @commands.command()
    async def get_next(self, ctx):
        #super().remove_all_jobs()
        #
        # self.scheduler.
        if self.scheduler.running:
            print("Has started")
        await ctx.send("All jobs!")
    
    @commands.command()   
    async def foo(self, ctx):
        """Description of myfirstcom visible with [p]help myfirstcom"""
        # Your code will go here
        await ctx.send("My first cog!")

    @commands.group(name="cowspirit", invoke_without_command=True)
    async def cowspirit(self, ctx):
        """Base command for the CowSpirit cog."""
        await ctx.send("Use 'cowspirit schedule [true/false]' to manage the scheduler.")

    @cowspirit.command(name="schedule")
    async def schedule(self, ctx, status: str = None):
        """Manage the boss notification scheduler."""
        if not self.scheduler:
            await ctx.send("Scheduler is not initialized!")
            return

        if status is None:
            await ctx.send(f"The scheduler is currently {'activated' if self.scheduler_running else 'deactivated'}.")
        elif status.lower() == "true":
            if not self.scheduler_running:
                self.scheduler.resume()  # Resume the scheduler
                self.scheduler_running = True
                await ctx.send("Boss notifications activated.")
            else:
                await ctx.send("The scheduler is already activated.")
        elif status.lower() == "false":
            if self.scheduler_running:
                self.scheduler.pause()  # Pause the scheduler
                self.scheduler_running = False
                await ctx.send("Boss notifications deactivated.")
            else:
                await ctx.send("The scheduler is already deactivated.")
        elif status.lower() == "test":
            """Send a test message immediately to verify functionality."""
            channel = self.bot.get_channel(self.channel_id)
            #await channel.send("Guten Loot 🍀! " f"<@&{self.role_id}>")
            if channel:
                title = "Test ist erschienen!"
                description = "Test Desc"
                color = 0xff9600
                location_value = "Test, Drieghan\n[Öffne Karte](https://www.blackdesertfoundry.com/map/?lat=-47.68018294648414&lng=8.415527343750002&M=Garmoth#7/-47.372/8.690)"
                loot_value = "Vell's Herz <:garmothheart:1199730542325796934>"
                image_url = "https://raw.githubusercontent.com/XardasDark/Dark-Cogs/main/cowspirit/media/img/kzarka.jpg"
                embed = self.create_embed(title, description, color, location_value, loot_value, image_url)
                content = "Guten Loot 🍀 " f"<@&{self.role_id}>"
                await ctx.send(content=content, embed=embed)
        elif status.lower() == "remove_all":
            self.scheduler.remove_all_jobs()
            await ctx.send("All schedules removed!")
        else:
            # Invalid argument provided, display usage information
            await ctx.send("Invalid command. Use 'cowspirit schedule [true/false]' to activate or deactivate the scheduler.")

    def schedule_jobs(self):
        """Schedule all the jobs for different bosses."""
        
        # Garmoth
        self.schedule_boss_notifications("Garmoth", self.send_garmoth_embed, hour=14, minute=0)
        self.schedule_boss_notifications("Garmoth", self.send_garmoth_embed, days_of_week='mon', hour=23, minute=15)
        self.schedule_boss_notifications("Garmoth", self.send_garmoth_embed, days_of_week='tue', hour=23, minute=15)
        self.schedule_boss_notifications("Garmoth", self.send_garmoth_embed, days_of_week='wed', hour=23, minute=15)
        self.schedule_boss_notifications("Garmoth", self.send_garmoth_embed, days_of_week='thu', hour=23, minute=15)
        self.schedule_boss_notifications("Garmoth", self.send_garmoth_embed, days_of_week='fri', hour=23, minute=15)
        self.schedule_boss_notifications("Garmoth", self.send_garmoth_embed, days_of_week='sun', hour=23, minute=15)

        # Karanda
        self.schedule_boss_notifications("Karanda", self.send_karanda_embed, days_of_week='mon', hour=0, minute=15)
        self.schedule_boss_notifications("Karanda", self.send_karanda_embed, days_of_week='mon', hour=2, minute=0)
        self.schedule_boss_notifications("Karanda", self.send_karanda_embed, days_of_week='tue', hour=0, minute=15)
        self.schedule_boss_notifications("Karanda", self.send_karanda_embed, days_of_week='tue', hour=19, minute=0)
        self.schedule_boss_notifications("Karanda", self.send_karanda_embed, days_of_week='wed', hour=2, minute=0)
        self.schedule_boss_notifications("Karanda", self.send_karanda_embed, days_of_week='wed', hour=9, minute=0)
        self.schedule_boss_notifications("Karanda", self.send_karanda_embed, days_of_week='wed', hour=22, minute=15)
        self.schedule_boss_notifications("Karanda", self.send_karanda_embed, days_of_week='fri', hour=0, minute=15)
        self.schedule_boss_notifications("Karanda", self.send_karanda_embed, days_of_week='fri', hour=5, minute=0)
        self.schedule_boss_notifications("Karanda", self.send_karanda_embed, days_of_week='fri', hour=12, minute=0)
        self.schedule_boss_notifications("Karanda", self.send_karanda_embed, days_of_week='sat', hour=0, minute=15)
        self.schedule_boss_notifications("Karanda", self.send_karanda_embed, days_of_week='sat', hour=19, minute=0)

        # Kutum
        self.schedule_boss_notifications("Kutum", self.send_kutum_embed, days_of_week='mon', hour=0, minute=15)
        self.schedule_boss_notifications("Kutum", self.send_kutum_embed, days_of_week='mon', hour=16, minute=0)
        self.schedule_boss_notifications("Kutum", self.send_kutum_embed, days_of_week='tue', hour=2, minute=0)
        self.schedule_boss_notifications("Kutum", self.send_kutum_embed, days_of_week='tue', hour=12, minute=0)
        self.schedule_boss_notifications("Kutum", self.send_kutum_embed, days_of_week='wed', hour=0, minute=15)
        self.schedule_boss_notifications("Kutum", self.send_kutum_embed, days_of_week='wed', hour=16, minute=0)
        self.schedule_boss_notifications("Kutum", self.send_kutum_embed, days_of_week='thu', hour=2, minute=0)
        self.schedule_boss_notifications("Kutum", self.send_kutum_embed, days_of_week='thu', hour=9, minute=0)
        self.schedule_boss_notifications("Kutum", self.send_kutum_embed, days_of_week='thu', hour=19, minute=0)
        self.schedule_boss_notifications("Kutum", self.send_kutum_embed, days_of_week='fri', hour=9, minute=0)
        self.schedule_boss_notifications("Kutum", self.send_kutum_embed, days_of_week='fri', hour=22, minute=15)
        self.schedule_boss_notifications("Kutum", self.send_kutum_embed, days_of_week='sat', hour=9, minute=0)
        self.schedule_boss_notifications("Kutum", self.send_kutum_embed, days_of_week='sun', hour=0, minute=15)
        self.schedule_boss_notifications("Kutum", self.send_kutum_embed, days_of_week='sun', hour=5, minute=0)

        # Kzarka
        self.schedule_boss_notifications("Kzarka", self.send_kzarka_embed, days_of_week='mon', hour=5, minute=0)
        self.schedule_boss_notifications("Kzarka", self.send_kzarka_embed, days_of_week='mon', hour=9, minute=0)
        self.schedule_boss_notifications("Kzarka", self.send_kzarka_embed, days_of_week='mon', hour=22, minute=15)
        self.schedule_boss_notifications("Kzarka", self.send_kzarka_embed, days_of_week='tue', hour=5, minute=0)
        self.schedule_boss_notifications("Kzarka", self.send_kzarka_embed, days_of_week='wed', hour=0, minute=15)
        self.schedule_boss_notifications("Kzarka", self.send_kzarka_embed, days_of_week='wed', hour=5, minute=0)
        self.schedule_boss_notifications("Kzarka", self.send_kzarka_embed, days_of_week='wed', hour=22, minute=15)
        self.schedule_boss_notifications("Kzarka", self.send_kzarka_embed, days_of_week='thu', hour=16, minute=0)
        self.schedule_boss_notifications("Kzarka", self.send_kzarka_embed, days_of_week='fri', hour=0, minute=15)
        self.schedule_boss_notifications("Kzarka", self.send_kzarka_embed, days_of_week='fri', hour=19, minute=0)
        self.schedule_boss_notifications("Kzarka", self.send_kzarka_embed, days_of_week='fri', hour=22, minute=15)
        self.schedule_boss_notifications("Kzarka", self.send_kzarka_embed, days_of_week='sat', hour=19, minute=0)
        self.schedule_boss_notifications("Kzarka", self.send_kzarka_embed, days_of_week='sun', hour=2, minute=0)
        self.schedule_boss_notifications("Kzarka", self.send_kzarka_embed, days_of_week='sun', hour=12, minute=0)
        self.schedule_boss_notifications("Kzarka", self.send_kzarka_embed, days_of_week='sun', hour=22, minute=15)

        # Nouver
        self.schedule_boss_notifications("Nouver", self.send_nouver_embed, days_of_week='mon', hour=19, minute=0)
        self.schedule_boss_notifications("Nouver", self.send_nouver_embed, days_of_week='tue', hour=9, minute=0)
        self.schedule_boss_notifications("Nouver", self.send_nouver_embed, days_of_week='tue', hour=16, minute=0)
        self.schedule_boss_notifications("Nouver", self.send_nouver_embed, days_of_week='thu', hour=0, minute=15)
        self.schedule_boss_notifications("Nouver", self.send_nouver_embed, days_of_week='thu', hour=9, minute=0)
        self.schedule_boss_notifications("Nouver", self.send_nouver_embed, days_of_week='thu', hour=12, minute=0)
        self.schedule_boss_notifications("Nouver", self.send_nouver_embed, days_of_week='fri', hour=2, minute=0)
        self.schedule_boss_notifications("Nouver", self.send_nouver_embed, days_of_week='fri', hour=16, minute=0)
        self.schedule_boss_notifications("Nouver", self.send_nouver_embed, days_of_week='sat', hour=5, minute=0)
        self.schedule_boss_notifications("Nouver", self.send_nouver_embed, days_of_week='sat', hour=12, minute=0)
        self.schedule_boss_notifications("Nouver", self.send_nouver_embed, days_of_week='sun', hour=0, minute=15)
        self.schedule_boss_notifications("Nouver", self.send_nouver_embed, days_of_week='sun', hour=9, minute=0)
        self.schedule_boss_notifications("Nouver", self.send_nouver_embed, days_of_week='sun', hour=22, minute=15)

        # Offin
        self.schedule_boss_notifications("Offin", self.send_offin_embed, days_of_week='mon', hour=12, minute=0)
        self.schedule_boss_notifications("Offin", self.send_offin_embed, days_of_week='wed', hour=16, minute=0)
        self.schedule_boss_notifications("Offin", self.send_offin_embed, days_of_week='sat', hour=2, minute=0)

        # Quint & Muraka
        self.schedule_boss_notifications("Quint & Muraka", self.send_quint_muraka_embed, days_of_week='tue', hour=22, minute=15)
        self.schedule_boss_notifications("Quint & Muraka", self.send_quint_muraka_embed, days_of_week='thu', hour=22, minute=15)

        # Vell
        self.schedule_boss_notifications("Vell", self.send_vell_embed, days_of_week='wed', hour=19, minute=0)
        self.schedule_boss_notifications("Vell", self.send_vell_embed, days_of_week='sun', hour=16, minute=0)
        
        # Black Shadow
        # self.schedule_boss_notifications("Schwarzschatten", self.send_blackshadow_embed, days_of_week='sat', hour=16, minute=0)
        
        # Event Boss Targargo
        self.schedule_boss_notifications("Targargo", self.send_targargo_embed, hour=9, minute=30)
        self.schedule_boss_notifications("Targargo", self.send_targargo_embed, hour=17, minute=30)
        self.schedule_boss_notifications("Targargo", self.send_targargo_embed, hour=21, minute=45)
        
        # Uturi
        self.schedule_boss_notifications("Uturi", self.send_uturi_embed, days_of_week='mon', hour=00, minute=15)
        self.schedule_boss_notifications("Uturi", self.send_uturi_embed, days_of_week='mon', hour=16, minute=00)
        self.schedule_boss_notifications("Uturi", self.send_uturi_embed, days_of_week='mon', hour=22, minute=15)
        self.schedule_boss_notifications("Uturi", self.send_uturi_embed, days_of_week='tue', hour=19, minute=00)
        self.schedule_boss_notifications("Uturi", self.send_uturi_embed, days_of_week='wed', hour=22, minute=15)
        self.schedule_boss_notifications("Uturi", self.send_uturi_embed, days_of_week='thu', hour=16, minute=00)
        self.schedule_boss_notifications("Uturi", self.send_uturi_embed, days_of_week='thu', hour=22, minute=15)
        self.schedule_boss_notifications("Uturi", self.send_uturi_embed, days_of_week='fri', hour=16, minute=00)
        self.schedule_boss_notifications("Uturi", self.send_uturi_embed, days_of_week='fri', hour=22, minute=15)
        self.schedule_boss_notifications("Uturi", self.send_uturi_embed, days_of_week='sat', hour=16, minute=00)
        self.schedule_boss_notifications("Uturi", self.send_uturi_embed, days_of_week='sun', hour=00, minute=15)
        
        # Bulgasal
        self.schedule_boss_notifications("Bulgasal", self.send_bulgasal_embed, days_of_week='mon', hour=2, minute=00)
        self.schedule_boss_notifications("Bulgasal", self.send_bulgasal_embed, days_of_week='mon', hour=19, minute=00)
        self.schedule_boss_notifications("Bulgasal", self.send_bulgasal_embed, days_of_week='tue', hour=19, minute=00)
        self.schedule_boss_notifications("Bulgasal", self.send_bulgasal_embed, days_of_week='wed', hour=00, minute=15)
        self.schedule_boss_notifications("Bulgasal", self.send_bulgasal_embed, days_of_week='wed', hour=16, minute=00)
        self.schedule_boss_notifications("Bulgasal", self.send_bulgasal_embed, days_of_week='thu', hour=00, minute=15)
        self.schedule_boss_notifications("Bulgasal", self.send_bulgasal_embed, days_of_week='thu', hour=19, minute=00)
        self.schedule_boss_notifications("Bulgasal", self.send_bulgasal_embed, days_of_week='fri', hour=2, minute=00)
        self.schedule_boss_notifications("Bulgasal", self.send_bulgasal_embed, days_of_week='fri', hour=22, minute=15)
        self.schedule_boss_notifications("Bulgasal", self.send_bulgasal_embed, days_of_week='sat', hour=2, minute=00)
        self.schedule_boss_notifications("Bulgasal", self.send_bulgasal_embed, days_of_week='sat', hour=19, minute=00)
        self.schedule_boss_notifications("Bulgasal", self.send_bulgasal_embed, days_of_week='sun', hour=2, minute=00)
        
        # Sangun
        self.schedule_boss_notifications("Sangun", self.send_sangun_embed, days_of_week='mon', hour=22, minute=15)
        self.schedule_boss_notifications("Sangun", self.send_sangun_embed, days_of_week='tue', hour=2, minute=00)
        self.schedule_boss_notifications("Sangun", self.send_sangun_embed, days_of_week='tue', hour=22, minute=15)
        self.schedule_boss_notifications("Sangun", self.send_sangun_embed, days_of_week='wed', hour=22, minute=15)
        self.schedule_boss_notifications("Sangun", self.send_sangun_embed, days_of_week='thu', hour=2, minute=00)
        self.schedule_boss_notifications("Sangun", self.send_sangun_embed, days_of_week='thu', hour=19, minute=00)
        self.schedule_boss_notifications("Sangun", self.send_sangun_embed, days_of_week='fri', hour=00, minute=15)
        self.schedule_boss_notifications("Sangun", self.send_sangun_embed, days_of_week='sat', hour=00, minute=15)
        self.schedule_boss_notifications("Sangun", self.send_sangun_embed, days_of_week='sat', hour=19, minute=00)
        self.schedule_boss_notifications("Sangun", self.send_sangun_embed, days_of_week='sun', hour=2, minute=00)
        self.schedule_boss_notifications("Sangun", self.send_sangun_embed, days_of_week='sun', hour=22, minute=15)
        
        # Pig King
        self.schedule_boss_notifications("Pigking", self.send_pigking_embed, days_of_week='mon', hour=19, minute=00)
        self.schedule_boss_notifications("Pigking", self.send_pigking_embed, days_of_week='tue', hour=00, minute=15)
        self.schedule_boss_notifications("Pigking", self.send_pigking_embed, days_of_week='tue', hour=16, minute=00)
        self.schedule_boss_notifications("Pigking", self.send_pigking_embed, days_of_week='tue', hour=22, minute=15)
        self.schedule_boss_notifications("Pigking", self.send_pigking_embed, days_of_week='wed', hour=2, minute=00)
        self.schedule_boss_notifications("Pigking", self.send_pigking_embed, days_of_week='thu', hour=22, minute=15)
        self.schedule_boss_notifications("Pigking", self.send_pigking_embed, days_of_week='fri', hour=19, minute=00)
        self.schedule_boss_notifications("Pigking", self.send_pigking_embed, days_of_week='sat', hour=00, minute=15)
        self.schedule_boss_notifications("Pigking", self.send_pigking_embed, days_of_week='sat', hour=2, minute=00)
        self.schedule_boss_notifications("Pigking", self.send_pigking_embed, days_of_week='sat', hour=16, minute=00)
        self.schedule_boss_notifications("Pigking", self.send_pigking_embed, days_of_week='sun', hour=0, minute=15)
        self.schedule_boss_notifications("Pigking", self.send_pigking_embed, days_of_week='sun', hour=22, minute=15)
        
        # Event Zodd
        #self.schedule_boss_notifications("Zodd", self.send_zodd_embed, hour=10, minute=0)
        #self.schedule_boss_notifications("Zodd", self.send_zodd_embed, hour=18, minute=00)
        #self.schedule_boss_notifications("Zodd", self.send_zodd_embed, hour=23, minute=30)

    async def send_message_with_embed(self, channel, title, description, color, location_value, loot_value, image_url):
        """Send a message and embed to a specified channel."""
        if channel:
            embed = self.create_embed(title, description, color, location_value, loot_value, image_url)
            #content = "Guten Loot 🍀 " f"<@&{self.role_id}>"
            content = f"<@&{self.role_id}>"
            notification_message = await channel.send(content=content, embed=embed)
            
            # Wait for 30 minutes (1800 seconds)
            await asyncio.sleep(1800)
            
            # Delete the message after 30 minutes
            try:
                await notification_message.delete()
            except discord.NotFound:
                # The message may already be deleted or can't be found
                print("Message was already deleted.")
        else:
            # Log or handle the error where channel couldn't be fetched
            print("Failed to fetch the channel")

    async def send_garmoth_embed(self, title="Garmoth ist erschienen!"):
        """Send Garmoth embed message to the specified channel."""
        channel = self.bot.get_channel(self.channel_id)
        await self.send_message_with_embed(
            channel,
            title,
            "Garmoth's Gebrüll hallt durch Garmoth's Nest",
            0xC25811, # Fiery Orange
            "Garmoth's Nest, Drieghan\n[Öffne Karte](https://www.blackdesertfoundry.com/map/?lat=-47.68018294648414&lng=8.415527343750002&M=Garmoth#7/-47.372/8.690)",
            "Garmoth's Herz <:garmothheart:1199730542325796934>",
            "https://raw.githubusercontent.com/XardasDark/Dark-Cogs/main/cowspirit/media/img/garmoth.jpg"
        )

    async def send_karanda_embed(self, title="Karanda ist erschienen!"):
        """Send Karanda embed message to the specified channel."""
        channel = self.bot.get_channel(self.channel_id)
        await self.send_message_with_embed(
            channel,
            title,
            "Hoch oben auf dem Bergkamm befehlen Karandas Flügel den Harpyien zu brüllen",
            0x00d062, # Emerald Green
            "Höchster Gipfel des Karanda Kammes im nordöstlichen Calpheon\n[Öffne Karte](https://www.blackdesertfoundry.com/map/?lat=-13.944729974920167&lng=-4.910888671875001&M=Karanda#6/-13.635/-5.251)",
            "Karandas Herz\nLöwenzahn Erweckungswaffe",
            "https://raw.githubusercontent.com/XardasDark/Dark-Cogs/main/cowspirit/media/img/karanda.jpg"
        )
        
    async def send_kutum_embed(self, title="Kutum ist erschienen!"):
        """Send Kutum embed message to the specified channel."""
        channel = self.bot.get_channel(self.channel_id)
        await self.send_message_with_embed(
            channel,
            title,
            "Das Herz des alten Kutum schlägt in der Scharlachsandkammer",
            0xD7C6AB, # Sandstone Beige
            "Am Boden der Scharlachsandkammer nordöstlich vom Felß-Außenposten\n[Öffne Karte](https://www.blackdesertfoundry.com/map/?lat=-2.284550660236957&lng=69.89501953125001&M=Kutum#7/-2.136/69.521)",
            "Kutums Herz\nKutum Sekundärwaffe",
            "https://raw.githubusercontent.com/XardasDark/Dark-Cogs/main/cowspirit/media/img/kutum.jpg"
        )
        
    async def send_kzarka_embed(self, title="Kzarka ist erschienen!"):
        """Send Kzarka embed message to the specified channel."""
        channel = self.bot.get_channel(self.channel_id)
        await self.send_message_with_embed(
            channel,
            title,
            "Das Gebrüll von Kzarka, dem Herrn der Verderbnis, bringt ganz Serendia zum Beben",
            0x990000, # Crimson Red
            "In den Tiefen des Serendia-Schreins im Süden von Serendia\n[Öffne Karte](https://www.blackdesertfoundry.com/map/?lat=-36.738884124394296&lng=16.040039062500004&M=Kzarka#7/-36.858/15.194)",
            "Kzarka Hauptwaffe",
            "https://raw.githubusercontent.com/XardasDark/Dark-Cogs/main/cowspirit/media/img/kzarka.jpg"
        )

    async def send_nouver_embed(self, title="Nouver ist erschienen!"):
        """Send Nouver embed message to the specified channel."""
        channel = self.bot.get_channel(self.channel_id)
        await self.send_message_with_embed(
            channel,
            title,
            "Die Spuren von Nouver wurden nach einem heftigen Sandsturm entdeckt",
            0xFFC000, # Golden Yellow
            "Südöstlich des Sandkornbasars, innerhalb der Wüste\n[Öffne Karte](https://www.blackdesertfoundry.com/map/?lat=-16.741427547003596&lng=90.96679687500001&M=Nouver#6/-16.815/88.484)",
            "Nouver Sekundärwaffe",
            "https://raw.githubusercontent.com/XardasDark/Dark-Cogs/main/cowspirit/media/img/nouver.jpg"
        )

    async def send_offin_embed(self, title="Offin ist erschienen!"):
        """Send Offin embed message to the specified channel."""
        channel = self.bot.get_channel(self.channel_id)
        await self.send_message_with_embed(
            channel,
            title,
            "Offin ist erwacht. Offin absorbiert kontinuierlich die Energie der Geister",
            0x374f2f, # Forest Green
            "Im Holo Wald nördlich von Grana\n[Öffne Karte](https://www.blackdesertfoundry.com/map/?lat=-50.02185841773444&lng=-39.39697265625001&M=Offin#7/-50.173/-39.518)",
            "Offin Hauptwaffe\nMystische Feder",
            "https://raw.githubusercontent.com/XardasDark/Dark-Cogs/main/cowspirit/media/img/offin.jpg"
        )

    async def send_quint_muraka_embed(self, title="Quint und Muraka sind erschienen!"):
        """Send Quint & Muraka embed message to the specified channel."""
        channel = self.bot.get_channel(self.channel_id)
        await self.send_message_with_embed(
            channel,
            title,
            "Der König der Oger und der erste Troll sind dabei, der Welt zu erscheinen",
            0x5C4033, # Dark Brown
            "Quint: Erscheint westlich vom Quintenhügel/östlich vom Epheriaport. Muraka erscheint westlich vom Manshawald/Kaiasee\n[Öffne Karte - Quint](https://www.blackdesertfoundry.com/map/?lat=-17.5602465032949&lng=-25.598144531250004&M=Quint#7/-17.188/-25.576)\n[Öffne Karte - Muraka](https://www.blackdesertfoundry.com/map/?lat=-27.994401411046148&lng=-33.07983398437501&M=Muraka#7/-27.951/-33.102)",
            "Ogerring / Mutantenverstärker",
            "https://raw.githubusercontent.com/XardasDark/Dark-Cogs/main/cowspirit/media/img/quintmuraka.jpg"
        )
        
    async def send_vell_embed(self, title="Vell ist erschienen!"):
        """Send Vell embed message to the specified channel."""
        channel = self.bot.get_channel(self.channel_id)
        await self.send_message_with_embed(
            channel,
            title,
            "Vell neutralisiert den Siegelstein mit seinem kataklysmischen Zorn",
            0x0059b3, # Deep Ocean Blue
            "Vell's Realm im nördlichen Meer\n[Öffne Karte](https://www.blackdesertfoundry.com/map/?lat=64.92819764459557&lng=8.096923828125002&M=Vell#4/49.07/-25.22)",
            "Vell's Herz <:vell:1203017198491410462>\nVell's Konzentrierte Magie",
            "https://raw.githubusercontent.com/XardasDark/Dark-Cogs/main/cowspirit/media/img/vell.jpg"
        )
        
    async def send_zodd_embed(self, title="Event Boss Zodd ist erschienen!"):
        """Send Zodd embed message to the specified channel."""
        channel = self.bot.get_channel(self.channel_id)
        await self.send_message_with_embed(
            channel,
            title,
            "Nosferatu Zodd ist durch den Riss im Raum in dieser Welt gedrungen!\n[Öffne Eventseite](https://www.naeu.playblackdesert.com/de-DE/News/Detail?groupContentNo=7476&countryType=de-DE)",
            0x1B1212, # Licorice
            "Mitten in der Verwunschene Kultstätte\n[Öffne Karte](https://www.blackdesertfoundry.com/map/?lat=-44.134913443750726&lng=-13.996582031250002&M=Zodd)",
            "Behelit-Alchemiestein",
            "https://raw.githubusercontent.com/XardasDark/Dark-Cogs/main/cowspirit/media/img/zodd.jpg"
        )
        
    async def send_targargo_embed(self, title="Event Boss Targargo ist erschienen!"):
        """Send Targargo embed message to the specified channel."""
        channel = self.bot.get_channel(self.channel_id)
        await self.send_message_with_embed(
            channel,
            title,
            "Je größer der Körper, desto saftiger der Truthahn! Der Große Targargo und seine Targarga-Schar erscheint!\n[Öffne Eventseite](https://www.naeu.playblackdesert.com/de-DE/News/Detail?groupContentNo=7840&countryType=de-DE)",
            0xe4d5b7, # Beige
            "Nordöstlich von Velia. Unter Burg Cron\n[Öffne Karte](https://www.blackdesertfoundry.com/map/?lat=-7.623886853120049&lng=13.106689453125002&M=Targargo)",
            "Ausgestopfte Truthähne\nRat von Valks +100\nGoldenes Truthahnei",
            "https://raw.githubusercontent.com/XardasDark/Dark-Cogs/main/cowspirit/media/img/targargo.jpg"
        )
        
    async def send_blackshadow_embed(self, title="Schwarzschatten ist erschienen!"):
        """Send Black Shadow embed message to the specified channel."""
        channel = self.bot.get_channel(self.channel_id)
        await self.send_message_with_embed(
            channel,
            title,
            "Schwarzschatten, die Dunkelheit, die alles verschlingt, ist erschienen",
            0x000000, # Black
            "Vell's Realm im nördlichen Meer\n[Öffne Karte](https://www.blackdesertfoundry.com/map/?lat=-26.391869671769022&lng=-9.569091796875002&M=Schwarzschatten)",
            "Schwarzstern Rüstung\nMystische Feder",
            "https://raw.githubusercontent.com/XardasDark/Dark-Cogs/main/cowspirit/media/img/blackshadow.jpg"
        )

    async def send_uturi_embed(self, title="Uturi ist erschienen!"):
        """Send Uturi embed message to the specified channel."""
        channel = self.bot.get_channel(self.channel_id)
        await self.send_message_with_embed(
            channel,
            title,
            "Der Vogel hat einen Vogel!",
            0x023020, # Dark Green
            "Auf einer mittelgroßen Insel ganz im Norden von LOML\n[Öffne Karte](https://www.blackdesertfoundry.com/map/?lat=72.91318063340522&lng=-115.91674804687501&M=Uturi)",
            "Stuff",
            "https://raw.githubusercontent.com/XardasDark/Dark-Cogs/main/cowspirit/media/img/uturi.jpg"
        )

    async def send_bulgasal_embed(self, title="Bulgasal ist erschienen!"):
        """Send Bulgasal embed message to the specified channel."""
        channel = self.bot.get_channel(self.channel_id)
        await self.send_message_with_embed(
            channel,
            title,
            "Böser Dude ist böse!",
            0x4E1764, # Midnight Purple
            "In der Festung an der südwestlichen Küste von LOML\n[Öffne Karte](https://www.blackdesertfoundry.com/map/?lat=72.91318063340522&lng=-115.91674804687501&M=Bulgasal)",
            "Stuff",
            "https://raw.githubusercontent.com/XardasDark/Dark-Cogs/main/cowspirit/media/img/bulgasal.jpg"
        )

    async def send_sangun_embed(self, title="Sangun ist erschienen!"):
        """Send Sangun embed message to the specified channel."""
        channel = self.bot.get_channel(self.channel_id)
        await self.send_message_with_embed(
            channel,
            title,
            "Tiger macht Rawr Rawr.",
            0xC0C0C0, # Silver
            "Nördlich von Dalbeol in seiner Höhle\n[Öffne Karte](https://www.blackdesertfoundry.com/map/?lat=77.5964886481334&lng=-117.15820312500001&M=Sangun)",
            "Stuff",
            "https://raw.githubusercontent.com/XardasDark/Dark-Cogs/main/cowspirit/media/img/sangun.jpg"
        )
        
    async def send_pigking_embed(self, title="König der Aurumschweine ist erschienen!"):
        """Send Pigking embed message to the specified channel."""
        channel = self.bot.get_channel(self.channel_id)
        await self.send_message_with_embed(
            channel,
            title,
            "Großes wütendes Schwein.",
            0x990000, # Crimson Red
            "Mitten in der Verwunschene Kultstätte\n[Öffne Karte](https://www.blackdesertfoundry.com/map/?lat=72.91318063340522&lng=-115.91674804687501&M=Arumschweine)",
            "Stuff",
            "https://raw.githubusercontent.com/XardasDark/Dark-Cogs/main/cowspirit/media/img/pigking.jpg"
        )

    def create_embed(self, title, description, color, location_value, loot_value, image_url):
        """Helper function to create an embedded message."""
        embed = discord.Embed(
            title=title,
            url="https://garmoth.com/boss-timer",
            description=description,
            color=color
            #color=0xff9600
        )
        embed.set_author(
            name="Boss Timer", 
            icon_url="https://raw.githubusercontent.com/XardasDark/Dark-Cogs/main/cowspirit/media/img/hourglass.png"
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

    def schedule_boss_notifications(self, boss_name, send_method, days_of_week=None, hour=None, minute=None):
        """
        Schedules the boss notification along with 30-minute and 5-minute reminders.
        
        :param boss_name: The name of the boss (used for logging purposes).
        :param send_method: The method to be called to send the notification.
        :param days_of_week: Days of the week (e.g., 'mon', 'tue', etc.), can be None for daily scheduling.
        :param hour: The hour the boss spawns.
        :param minute: The minute the boss spawns.
        """
        # Schedule the main notification
        spawn_title = f"{boss_name} ist erschienen!"
        self.scheduler.add_job(send_method, CronTrigger(day_of_week=days_of_week, hour=hour, minute=minute, timezone="CET"), misfire_grace_time=60, args=[spawn_title])
        
        # Handle 30 minutes before
        pre_30min_title = f"{boss_name} wird in 30 Minuten erscheinen!"
        pre_30min_hour = hour
        pre_30min_minute = minute - 30
        if pre_30min_minute < 0:
            pre_30min_minute += 60
            pre_30min_hour -= 1
            if pre_30min_hour < 0:
                pre_30min_hour += 24
                if days_of_week != None:
                    days_of_week = self.adjust_day_of_week(days_of_week, -1)

        self.scheduler.add_job(send_method, CronTrigger(day_of_week=days_of_week, hour=pre_30min_hour, minute=pre_30min_minute, timezone="CET"), misfire_grace_time=60, args=[pre_30min_title])

        # Handle 5 minutes before
        pre_5min_title = f"{boss_name} wird in 5 Minuten erscheinen!"
        pre_5min_hour = hour
        pre_5min_minute = minute - 5
        if pre_5min_minute < 0:
            pre_5min_minute += 60
            pre_5min_hour -= 1
            if pre_5min_hour < 0:
                pre_5min_hour += 24
                if days_of_week != None:
                    days_of_week = self.adjust_day_of_week(days_of_week, -1)

        self.scheduler.add_job(send_method, CronTrigger(day_of_week=days_of_week, hour=pre_5min_hour, minute=pre_5min_minute, timezone="CET"), misfire_grace_time=60, args=[pre_5min_title])

        # Store the jobs by boss name
        #self.jobs[boss_name] = [thirty_min_before, five_min_before, main_announcement]

        print(f"Scheduled {boss_name} notifications: Main spawn at {hour}:{minute}, 30 min before at {pre_30min_hour}:{pre_30min_minute}, and 5 min before at {pre_5min_hour}:{pre_5min_minute}")

    def adjust_day_of_week(self, day_of_week, adjustment):
        """
        Adjust the day of the week, rolling over if necessary.
        
        :param day_of_week: Current day of the week (e.g., 'mon', 'tue', etc.).
        :param adjustment: Adjustment in days (-1 for previous day, +1 for next day).
        :return: Adjusted day of the week.
        """
        days = ['mon', 'tue', 'wed', 'thu', 'fri', 'sat', 'sun']
        index = days.index(day_of_week)
        new_index = (index + adjustment) % 7
        return days[new_index]
