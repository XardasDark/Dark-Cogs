"""
Group Finder – Red-DiscordBot Cog
Spielübergreifendes Gruppen-System (Spiel-Preset pro Server wählbar)
"""

from redbot.core.bot import Red
from .cog import ROGroupFinder


async def setup(bot: Red) -> None:
    """Registriert den Cog beim Red-Bot."""
    await bot.add_cog(ROGroupFinder(bot))
