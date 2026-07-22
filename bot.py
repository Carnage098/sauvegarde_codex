from __future__ import annotations

import logging

import discord
from discord.ext import commands

from config import Settings
from core.logging_setup import configure_logging

LOGGER = logging.getLogger(__name__)
COGS = ("cogs.codex_news",)


class CodexBot(commands.Bot):
    def __init__(self, settings: Settings) -> None:
        intents = discord.Intents.default()
        super().__init__(command_prefix=commands.when_mentioned, intents=intents)
        self.settings = settings

    async def setup_hook(self) -> None:
        for extension in COGS:
            await self.load_extension(extension)
            LOGGER.info("Cog chargé : %s", extension)

        if self.settings.discord_guild_id:
            guild = discord.Object(id=self.settings.discord_guild_id)
            self.tree.copy_global_to(guild=guild)
            synced = await self.tree.sync(guild=guild)
            LOGGER.info(
                "%s commande(s) synchronisée(s) sur le serveur de développement.",
                len(synced),
            )
        else:
            synced = await self.tree.sync()
            LOGGER.info("%s commande(s) globale(s) synchronisée(s).", len(synced))

    async def on_ready(self) -> None:
        if self.user:
            LOGGER.info("Connecté en tant que %s (%s)", self.user, self.user.id)


if __name__ == "__main__":
    settings = Settings.from_env()
    configure_logging(settings.log_level)
    bot = CodexBot(settings)
    bot.run(settings.discord_token, log_handler=None)
