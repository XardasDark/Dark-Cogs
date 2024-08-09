        
import discord

from redbot.core import commands, app_commands

class MyCog(commands.Cog):
    """My custom cog"""

    def __init__(self, bot):
        self.bot = bot

    @commands.command()
    async def mycom(self, ctx):
        """This does stuff!"""
        # Your code will go here
        await ctx.send("I can do stuff!")
        
    @app_commands.command()
    async def hello(self, interaction: discord.Interaction):
        await interaction.response.send_message("Hello World!", ephemeral=True)
        
    def create_embed(self, title, description, location_value, loot_value, image_url):
        """Helper function to create an embedded message."""
        embed = discord.Embed(
            title=title,
            url="https://garmoth.com/boss-timer",  # Constant URL
            description=description,
            color=0xff9600  # Constant color
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

    @commands.command()
    async def embed(self, ctx):
        """Sends a customized embedded message."""
        title = "Garmoth ist erschienen!"
        description = "Garmoth's Gebrüll hallt durch Garmoth's Nest"
        location_value = "Garmoth's Nest, Drieghan\n[Öffne Karte](https://www.blackdesertfoundry.com/map/?lat=-47.68018294648414&lng=8.415527343750002&M=Garmoth#7/-47.372/8.690)"
        loot_value = "Garmoth's Herz <:garmothheart:1199730542325796934>"
        image_url = "https://raw.githubusercontent.com/XardasDark/Dark-Cogs/main/media/img/garmoth.jpg"
        
        title = "Karanda ist erschienen!"
        description = "Hoch oben auf dem Bergkamm befehlen Karandas Flügel den Harpyien zu brüllen"
        location_value = "Höchster Gipfel des Karanda Kammes im nordöstlichen Calpheon\n[Öffne Karte](https://www.blackdesertfoundry.com/map/?lat=-13.944729974920167&lng=-4.910888671875001&M=Karanda#6/-13.635/-5.251)"
        loot_value = "Karandas Herz\nLöwenzahn Erweckungswaffe"
        image_url = "https://raw.githubusercontent.com/XardasDark/Dark-Cogs/main/media/img/karanda.jpg"
        
        title = "Kutum ist erschienen!"
        description = "Das Herz des alten Kutum schlägt in der Scharlachsandkammer"
        location_value = "Am Boden der Scharlachsandkammer nordöstlich vom Felß-Außenposten\n[Öffne Karte](https://www.blackdesertfoundry.com/map/?lat=-2.284550660236957&lng=69.89501953125001&M=Kutum#7/-2.136/69.521)"
        loot_value = "Kutums Herz\nKutum Sekundärwaffe"
        image_url = "https://raw.githubusercontent.com/XardasDark/Dark-Cogs/main/media/img/kutum.jpg"
        
        title = "Kzarka ist erschienen!"
        description = "Das Gebrüll von Kzarka, dem Herrn der Verderbnis, bringt ganz Serendia zum Beben"
        location_value = "In den Tiefen des Serendia-Schreins im Süden von Serendia\n[Öffne Karte](https://www.blackdesertfoundry.com/map/?lat=-36.738884124394296&lng=16.040039062500004&M=Kzarka#7/-36.858/15.194)"
        loot_value = "Kzarka Hauptwaffe"
        image_url = "https://raw.githubusercontent.com/XardasDark/Dark-Cogs/main/media/img/kzarka.jpg"
        
        embed = self.create_embed(title, description, location_value, loot_value, image_url)
        await ctx.send(embed=embed)
