"""
RO Group Finder – Red-DiscordBot Cog
Ragnarok Zero: Global | Gruppen-System
"""

from .cog import ROGroupFinder


async def setup(bot):
    """Registriert den Cog beim Red-Bot."""
    cog = ROGroupFinder(bot)
    await bot.add_cog(cog)
