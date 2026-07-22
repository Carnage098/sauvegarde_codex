from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone

import discord
from discord import app_commands
from discord.ext import commands, tasks

from config import Settings
from models.article import CodexArticle
from repositories.article_repository import ArticleRepository
from services.codex_client import CodexClient
from services.embed_factory import CodexEmbedFactory
from views.article_view import ArticleLinkView

LOGGER = logging.getLogger(__name__)


class CodexNews(commands.Cog):
    codex = app_commands.Group(
        name="codex",
        description="Actualités et articles de Codex YGO.",
    )

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        self.settings: Settings = bot.settings  # type: ignore[attr-defined]
        self.client = CodexClient()
        self.repository = ArticleRepository(self.settings.database_path)
        self.check_lock = asyncio.Lock()
        self.article_check_loop.change_interval(
            minutes=self.settings.check_interval_minutes
        )

    async def cog_load(self) -> None:
        await self.client.open()
        await self.repository.connect()
        self.article_check_loop.start()

    async def cog_unload(self) -> None:
        self.article_check_loop.cancel()
        await self.client.close()
        await self.repository.close()

    async def _destination_channel(self) -> discord.TextChannel | discord.Thread:
        channel = self.bot.get_channel(self.settings.codex_channel_id)

        if channel is None:
            channel = await self.bot.fetch_channel(self.settings.codex_channel_id)

        if not isinstance(channel, (discord.TextChannel, discord.Thread)):
            raise RuntimeError(
                "CODEX_CHANNEL_ID doit correspondre à un salon textuel ou à un fil."
            )
        return channel

    async def _send_article(self, article: CodexArticle) -> discord.Message:
        channel = await self._destination_channel()
        content = None

        if self.settings.codex_ping_role_id:
            content = f"<@&{self.settings.codex_ping_role_id}>"

        return await channel.send(
            content=content,
            embed=CodexEmbedFactory.build(article),
            view=ArticleLinkView(article),
            allowed_mentions=discord.AllowedMentions(
                roles=True,
                users=False,
                everyone=False,
                replied_user=False,
            ),
        )

    async def check_for_new_articles(self) -> int:
        async with self.check_lock:
            urls = await self.client.fetch_homepage_article_urls()
            if not urls:
                LOGGER.warning("Aucun lien /article/ trouvé sur la page d'accueil.")
                return 0

            database_is_empty = await self.repository.is_empty()

            if database_is_empty and self.settings.first_run_mode == "seed":
                for url in urls:
                    await self.repository.seed_url(url)

                LOGGER.info(
                    "%s article(s) existant(s) enregistré(s) sans publication.",
                    len(urls),
                )
                return 0

            new_urls: list[str] = []
            for url in urls:
                if not await self.repository.contains(url):
                    new_urls.append(url)

            if not new_urls:
                return 0

            # La page d'accueil est généralement du plus récent au plus ancien.
            selected_urls = new_urls[: self.settings.max_articles_per_check]
            sent_count = 0

            for url in reversed(selected_urls):
                try:
                    article = await self.client.fetch_article(url)
                    message = await self._send_article(article)
                    await self.repository.save(
                        article,
                        discord_message_id=message.id,
                    )
                    sent_count += 1
                    LOGGER.info(
                        "Article publié : %s [%s]",
                        article.title,
                        article.category_path,
                    )
                    await asyncio.sleep(2)
                except Exception:
                    # L'article n'est pas sauvegardé : il sera retenté à la prochaine passe.
                    LOGGER.exception("Échec de publication de %s", url)

            return sent_count

    @tasks.loop(minutes=10, reconnect=True)
    async def article_check_loop(self) -> None:
        try:
            sent_count = await self.check_for_new_articles()
            if sent_count:
                LOGGER.info("%s nouvel/nouveaux article(s) publié(s).", sent_count)
        except Exception:
            LOGGER.exception("Erreur pendant la vérification automatique Codex YGO.")

    @article_check_loop.before_loop
    async def before_article_check_loop(self) -> None:
        await self.bot.wait_until_ready()

    @codex.command(name="latest", description="Affiche le dernier article de Codex YGO.")
    async def latest(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer(ephemeral=True)

        try:
            urls = await self.client.fetch_homepage_article_urls()
            if not urls:
                await interaction.followup.send(
                    "Aucun article n'a été trouvé sur Codex YGO.",
                    ephemeral=True,
                )
                return

            article = await self.client.fetch_article(urls[0])
            await interaction.followup.send(
                embed=CodexEmbedFactory.build(article),
                view=ArticleLinkView(article),
                ephemeral=True,
            )
        except Exception:
            LOGGER.exception("Échec de /codex latest")
            await interaction.followup.send(
                "Impossible de récupérer le dernier article pour le moment.",
                ephemeral=True,
            )

    @codex.command(name="check", description="Cherche immédiatement les nouveaux articles.")
    @app_commands.checks.has_permissions(manage_guild=True)
    async def check(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer(ephemeral=True)

        try:
            count = await self.check_for_new_articles()
            if count == 0:
                text = "Aucun nouvel article n'a été détecté."
            elif count == 1:
                text = "Un nouvel article a été publié."
            else:
                text = f"{count} nouveaux articles ont été publiés."
            await interaction.followup.send(text, ephemeral=True)
        except Exception:
            LOGGER.exception("Échec de /codex check")
            await interaction.followup.send(
                "La vérification a échoué. Consulte les logs du bot.",
                ephemeral=True,
            )

    @codex.command(name="preview", description="Prévisualise l'embed d'un article Codex YGO.")
    @app_commands.describe(url="Adresse complète d'un article Codex YGO")
    @app_commands.checks.has_permissions(manage_guild=True)
    async def preview(self, interaction: discord.Interaction, url: str) -> None:
        await interaction.response.defer(ephemeral=True)

        normalized_url = self.client.normalize_article_url(url)
        if not normalized_url:
            await interaction.followup.send(
                "Le lien doit appartenir à `codexygo.fr` et commencer par `/article/`.",
                ephemeral=True,
            )
            return

        try:
            article = await self.client.fetch_article(normalized_url)
            await interaction.followup.send(
                embed=CodexEmbedFactory.build(article),
                view=ArticleLinkView(article),
                ephemeral=True,
            )
        except Exception:
            LOGGER.exception("Échec de /codex preview pour %s", normalized_url)
            await interaction.followup.send(
                "Impossible de prévisualiser cet article.",
                ephemeral=True,
            )

    @codex.command(name="status", description="Affiche l'état du module Codex YGO.")
    @app_commands.checks.has_permissions(manage_guild=True)
    async def status(self, interaction: discord.Interaction) -> None:
        article_count = await self.repository.count()
        loop_running = self.article_check_loop.is_running()

        embed = discord.Embed(
            title="État du module Codex YGO",
            colour=discord.Colour.blurple(),
            timestamp=datetime.now(timezone.utc),
        )
        embed.add_field(
            name="Salon",
            value=f"<#{self.settings.codex_channel_id}>",
            inline=False,
        )
        embed.add_field(
            name="Intervalle",
            value=f"{self.settings.check_interval_minutes} min",
            inline=True,
        )
        embed.add_field(
            name="Articles connus",
            value=str(article_count),
            inline=True,
        )
        embed.add_field(
            name="Boucle automatique",
            value="Active" if loop_running else "Arrêtée",
            inline=True,
        )
        embed.add_field(
            name="Premier lancement",
            value=self.settings.first_run_mode,
            inline=True,
        )
        await interaction.response.send_message(embed=embed, ephemeral=True)

    async def cog_app_command_error(
        self,
        interaction: discord.Interaction,
        error: app_commands.AppCommandError,
    ) -> None:
        if isinstance(error, app_commands.MissingPermissions):
            message = "Tu dois avoir la permission **Gérer le serveur**."
        else:
            LOGGER.exception("Erreur de commande Codex", exc_info=error)
            message = "Une erreur inattendue est survenue."

        if interaction.response.is_done():
            await interaction.followup.send(message, ephemeral=True)
        else:
            await interaction.response.send_message(message, ephemeral=True)


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(CodexNews(bot))
