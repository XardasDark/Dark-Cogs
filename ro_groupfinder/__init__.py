"""
RO Group Finder – Red-DiscordBot Cog
Ragnarok Zero: Global | Gruppen-System
"""

from redbot.core.bot import Red
from .cog import ROGroupFinder


async def setup(bot: Red) -> None:
    """Registriert den Cog beim Red-Bot."""
    await bot.add_cog(ROGroupFinder(bot))
