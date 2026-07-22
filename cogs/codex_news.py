from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone

import discord
from discord import app_commands
from discord.ext import commands, tasks

from config import Settings
from models.article import CodexArticle
from models.index_report import IndexReport
from repositories.article_repository import ArticleRepository
from services.codex_client import CodexClient
from services.embed_factory import CodexEmbedFactory
from services.library_indexer import CodexLibraryIndexer
from views.article_view import ArticleLinkView

LOGGER = logging.getLogger(__name__)


class CodexNews(commands.Cog):
    codex = app_commands.Group(
        name="codex",
        description="Actualités et bibliothèque d'articles Codex YGO.",
    )

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        self.settings: Settings = bot.settings  # type: ignore[attr-defined]
        self.client = CodexClient()
        self.repository = ArticleRepository(self.settings.database_path)
        self.indexer = CodexLibraryIndexer(self.client, self.repository)
        self.check_lock = asyncio.Lock()
        self.index_lock = asyncio.Lock()
        self._reseed_completed = False

        self.article_check_loop.change_interval(
            minutes=self.settings.check_interval_minutes
        )
        self.archive_sync_loop.change_interval(
            hours=self.settings.archive_sync_hours
        )

    @property
    def reseed_enabled(self) -> bool:
        return bool(getattr(self.settings, "reseed_on_start", False))

    @property
    def first_run_mode(self) -> str:
        return str(getattr(self.settings, "first_run_mode", "seed"))

    async def cog_load(self) -> None:
        await self.client.open()
        await self.repository.connect()
        self.article_check_loop.start()
        if self.settings.archive_auto_sync:
            self.archive_sync_loop.start()

    async def cog_unload(self) -> None:
        self.article_check_loop.cancel()
        self.archive_sync_loop.cancel()
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

    async def _send_automatic_article(self, article: CodexArticle) -> discord.Message:
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

    async def _upsert_homepage_articles(
        self,
        urls: list[str],
        *,
        seed_all: bool,
    ) -> int:
        existing_urls = await self.repository.existing_urls(urls)
        target_urls = await self.repository.urls_needing_metadata(urls)
        articles = await self.client.fetch_articles(
            target_urls,
            concurrency=self.settings.archive_concurrency,
        )

        for article in articles:
            is_new = article.url not in existing_urls
            await self.repository.upsert(
                article,
                announced=True if seed_all else (False if is_new else None),
            )
            existing_urls.add(article.url)

        return len(articles)

    async def check_for_new_articles(self) -> int:
        async with self.check_lock:
            urls = await self.client.fetch_homepage_article_urls()
            if not urls:
                LOGGER.warning(
                    "Aucun article Codex YGO trouvé par les différentes méthodes."
                )
                return 0

            database_is_empty = await self.repository.is_empty()
            reseed_now = self.reseed_enabled and not self._reseed_completed
            seed_now = database_is_empty and self.first_run_mode == "seed"

            if reseed_now or seed_now:
                fetched_count = await self._upsert_homepage_articles(
                    urls,
                    seed_all=True,
                )
                self._reseed_completed = self._reseed_completed or reseed_now

                if reseed_now:
                    LOGGER.info(
                        "%s article(s) resynchronisé(s) avec leurs métadonnées "
                        "sans publication. Remets CODEX_RESEED_ON_START=false.",
                        fetched_count,
                    )
                else:
                    LOGGER.info(
                        "%s article(s) existant(s) ajoutés à la bibliothèque "
                        "sans publication.",
                        fetched_count,
                    )
                return 0

            await self._upsert_homepage_articles(urls, seed_all=False)

            pending_articles = await self.repository.pending(
                self.settings.max_articles_per_check
            )
            if not pending_articles:
                LOGGER.info("Aucun nouvel article Codex YGO détecté.")
                return 0

            sent_count = 0
            for record in pending_articles:
                try:
                    message = await self._send_automatic_article(record.article)
                    await self.repository.mark_announced(record.id, message.id)
                    sent_count += 1
                    LOGGER.info(
                        "Article publié : %s [%s]",
                        record.article.title,
                        record.article.category_path,
                    )
                    await asyncio.sleep(2)
                except Exception:
                    LOGGER.exception(
                        "Échec de publication de %s",
                        record.article.url,
                    )

            return sent_count

    async def sync_library(
        self,
        *,
        force_refresh: bool = False,
    ) -> IndexReport:
        async with self.index_lock:
            return await self.indexer.sync(
                scroll_rounds=self.settings.archive_scroll_rounds,
                max_pages_per_category=(
                    self.settings.archive_max_pages_per_category
                ),
                concurrency=self.settings.archive_concurrency,
                force_refresh=force_refresh,
            )

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

    @tasks.loop(hours=24, reconnect=True)
    async def archive_sync_loop(self) -> None:
        try:
            report = await self.sync_library()
            LOGGER.info(
                "Synchronisation automatique de la bibliothèque terminée : "
                "%s article(s) connus.",
                await self.repository.count(),
            )
            if report.failed_articles:
                LOGGER.warning(
                    "%s article(s) n'ont pas pu être lus pendant l'indexation.",
                    report.failed_articles,
                )
        except Exception:
            LOGGER.exception("Échec de la synchronisation des archives Codex YGO.")

    @archive_sync_loop.before_loop
    async def before_archive_sync_loop(self) -> None:
        await self.bot.wait_until_ready()
        # Laisse la vérification légère des nouveautés passer en premier.
        await asyncio.sleep(45)

    @codex.command(name="latest", description="Affiche le dernier article de Codex YGO.")
    async def latest(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer(ephemeral=True)

        try:
            article = await self.client.fetch_latest_article()
            if article is None:
                await interaction.followup.send(
                    "Aucun article n'a été trouvé sur Codex YGO.",
                    ephemeral=True,
                )
                return

            existing = await self.repository.get_by_url(article.url)
            await self.repository.upsert(
                article,
                announced=None if existing else True,
            )
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

    @codex.command(
        name="article",
        description="Recherche un article enregistré et l'envoie dans ce salon.",
    )
    @app_commands.describe(
        article="Commence à taper le titre ou quelques mots-clés"
    )
    @app_commands.checks.cooldown(1, 8.0, key=lambda interaction: interaction.user.id)
    async def article(
        self,
        interaction: discord.Interaction,
        article: str,
    ) -> None:
        record = None
        if article.isdigit():
            record = await self.repository.get_by_id(int(article))

        if record is None:
            matches = await self.repository.search(article, limit=1)
            record = matches[0] if matches else None

        if record is None:
            await interaction.response.send_message(
                "Aucun article correspondant n'est enregistré. "
                "Utilise `/codex index` si la bibliothèque n'a pas encore été remplie.",
                ephemeral=True,
            )
            return

        await interaction.response.send_message(
            embed=CodexEmbedFactory.build(record.article),
            view=ArticleLinkView(record.article),
        )

    @article.autocomplete("article")
    async def article_autocomplete(
        self,
        interaction: discord.Interaction,
        current: str,
    ) -> list[app_commands.Choice[str]]:
        del interaction
        records = await self.repository.search(current, limit=25)
        choices: list[app_commands.Choice[str]] = []

        for record in records:
            category = (
                record.article.categories[-1]
                if record.article.categories
                else "Non classé"
            )
            label = f"{record.article.title} • {category}"
            choices.append(
                app_commands.Choice(
                    name=label[:100],
                    value=str(record.id),
                )
            )
        return choices

    @codex.command(
        name="search",
        description="Affiche les meilleurs résultats de la bibliothèque Codex.",
    )
    @app_commands.describe(
        recherche="Titre, carte, produit ou mots-clés",
        categorie="Filtre facultatif par catégorie",
    )
    async def search(
        self,
        interaction: discord.Interaction,
        recherche: str,
        categorie: str | None = None,
    ) -> None:
        await interaction.response.defer(ephemeral=True)
        records = await self.repository.search(
            recherche,
            category=categorie,
            limit=10,
        )

        if not records:
            await interaction.followup.send(
                "Aucun résultat dans la bibliothèque.",
                ephemeral=True,
            )
            return

        lines: list[str] = []
        for index, record in enumerate(records, start=1):
            published_at = record.article.published_at
            if published_at and published_at.tzinfo is None:
                published_at = published_at.replace(tzinfo=timezone.utc)
            date_text = (
                discord.utils.format_dt(published_at, "d")
                if published_at
                else "date inconnue"
            )
            lines.append(
                f"**{index}. [{record.article.title}]({record.article.url})**\n"
                f"`#{record.id}` • {record.article.category_path} • {date_text}"
            )

        embed = discord.Embed(
            title=f"🔎 Résultats pour « {recherche[:80]} »",
            description="\n\n".join(lines)[:4_096],
            colour=discord.Colour.blurple(),
        )
        embed.set_footer(
            text="Utilise /codex article et sélectionne le titre pour l'envoyer."
        )
        await interaction.followup.send(embed=embed, ephemeral=True)

    @search.autocomplete("categorie")
    async def category_autocomplete(
        self,
        interaction: discord.Interaction,
        current: str,
    ) -> list[app_commands.Choice[str]]:
        del interaction
        folded_current = current.casefold().strip()
        stats = await self.repository.category_stats()
        return [
            app_commands.Choice(name=f"{name} ({total})"[:100], value=name)
            for name, total in stats
            if not folded_current or folded_current in name.casefold()
        ][:25]

    @codex.command(
        name="categories",
        description="Affiche les catégories et le nombre d'articles enregistrés.",
    )
    async def categories(self, interaction: discord.Interaction) -> None:
        stats = await self.repository.category_stats()
        if not stats:
            await interaction.response.send_message(
                "Aucune catégorie n'est encore enregistrée.",
                ephemeral=True,
            )
            return

        description = "\n".join(
            f"• **{name}** : {total} article(s)" for name, total in stats
        )
        embed = discord.Embed(
            title="📚 Catégories de la bibliothèque Codex",
            description=description[:4_096],
            colour=discord.Colour.teal(),
        )
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @codex.command(
        name="index",
        description="Indexe les archives et catégories de Codex YGO.",
    )
    @app_commands.checks.has_permissions(manage_guild=True)
    async def index(self, interaction: discord.Interaction) -> None:
        if self.index_lock.locked():
            await interaction.response.send_message(
                "Une indexation est déjà en cours.",
                ephemeral=True,
            )
            return

        await interaction.response.defer(ephemeral=True, thinking=True)
        try:
            # La commande manuelle force la relecture des métadonnées des
            # articles déjà présents. Cela reconstruit les catégories et
            # l'index de recherche après une migration depuis l'ancienne base.
            report = await self.sync_library(force_refresh=True)
            total = await self.repository.count()
            await interaction.followup.send(
                "✅ **Indexation terminée**\n"
                f"• URL découvertes : **{report.discovered_urls}**\n"
                f"• Nouveaux articles : **{report.inserted_articles}**\n"
                f"• Articles enrichis : **{report.updated_articles}**\n"
                f"• Échecs de lecture : **{report.failed_articles}**\n"
                f"• Total dans la bibliothèque : **{total}**",
                ephemeral=True,
            )
        except Exception:
            LOGGER.exception("Échec de /codex index")
            await interaction.followup.send(
                "L'indexation a échoué. Consulte les logs Railway.",
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
            existing = await self.repository.get_by_url(article.url)
            await self.repository.upsert(
                article,
                announced=None if existing else True,
            )
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
        pending_count = await self.repository.pending_count()
        category_count = len(await self.repository.category_stats())

        embed = discord.Embed(
            title="Statut du module Codex YGO",
            colour=discord.Colour.gold(),
            timestamp=datetime.now(timezone.utc),
        )
        embed.add_field(
            name="Salon principal",
            value=f"<#{self.settings.codex_channel_id}>",
            inline=False,
        )
        embed.add_field(
            name="Vérification nouveautés",
            value=f"Toutes les {self.settings.check_interval_minutes} min",
            inline=True,
        )
        embed.add_field(
            name="Articles enregistrés",
            value=str(article_count),
            inline=True,
        )
        embed.add_field(
            name="Catégories",
            value=str(category_count),
            inline=True,
        )
        embed.add_field(
            name="Annonces en attente",
            value=str(pending_count),
            inline=True,
        )
        embed.add_field(
            name="Boucle nouveautés",
            value="Active" if self.article_check_loop.is_running() else "Arrêtée",
            inline=True,
        )
        embed.add_field(
            name="Indexation automatique",
            value=(
                f"Toutes les {self.settings.archive_sync_hours} h"
                if self.archive_sync_loop.is_running()
                else "Désactivée"
            ),
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
        elif isinstance(error, app_commands.CommandOnCooldown):
            message = (
                "Patiente encore "
                f"**{error.retry_after:.1f} seconde(s)** avant de renvoyer un article."
            )
        else:
            LOGGER.error("Erreur de commande Codex : %r", error, exc_info=error)
            message = "Une erreur inattendue est survenue."

        if interaction.response.is_done():
            await interaction.followup.send(message, ephemeral=True)
        else:
            await interaction.response.send_message(message, ephemeral=True)


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(CodexNews(bot))
