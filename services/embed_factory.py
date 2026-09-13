from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone

import discord

from models.article import CodexArticle
from services.codex_client import CODEX_BASE_URL
from services.image_utils import unique_image_urls


@dataclass(frozen=True, slots=True)
class EmbedStyle:
    label: str
    emoji: str
    colour: int


CATEGORY_STYLES: dict[str, EmbedStyle] = {
    "OCG / TCG": EmbedStyle("Actualités • OCG / TCG", "📰", 0x3498DB),
    "Rush Duel": EmbedStyle("Actualités • Rush Duel", "⚡", 0xE74C3C),
    "Récap": EmbedStyle("Dossiers • Récap", "📚", 0x9B59B6),
    "Focus": EmbedStyle("Dossiers • Focus", "🔍", 0xE67E22),
    "Rulings": EmbedStyle("Dossiers • Rulings", "⚖️", 0x2ECC71),
    "Codex": EmbedStyle("Codex • Guide de règles", "📘", 0x1ABC9C),
    "Actualités": EmbedStyle("Actualités", "📢", 0x2980B9),
    "Dossiers": EmbedStyle("Dossiers", "📖", 0x8E44AD),
}

DEFAULT_STYLE = EmbedStyle("Article Codex YGO", "✨", 0x5865F2)
DISCORD_MAX_EMBEDS_PER_MESSAGE = 10
STYLE_PRIORITY = (
    "Rulings",
    "Focus",
    "Récap",
    "Rush Duel",
    "OCG / TCG",
    "Codex",
    "Actualités",
    "Dossiers",
)


class CodexEmbedFactory:
    @staticmethod
    def style_for(article: CodexArticle) -> EmbedStyle:
        for category in STYLE_PRIORITY:
            if category in article.categories:
                return CATEGORY_STYLES[category]
        return DEFAULT_STYLE

    @classmethod
    def build(cls, article: CodexArticle) -> discord.Embed:
        style = cls.style_for(article)
        description = article.description or (
            "Un nouvel article vient d'être publié sur Codex YGO."
        )

        embed = discord.Embed(
            title=f"{style.emoji} {article.title}"[:256],
            url=article.url,
            description=description[:4_096],
            colour=discord.Colour(style.colour),
            timestamp=article.published_at or datetime.now(timezone.utc),
        )

        author_name = f"Codex YGO • {style.label}"
        if article.is_premium:
            author_name += " • Premium"

        embed.set_author(name=author_name, url=CODEX_BASE_URL)

        if article.image_url:
            embed.set_image(url=article.image_url)

        embed.add_field(
            name="Catégorie",
            value=f"{style.emoji} {article.category_path}",
            inline=True,
        )

        if article.author:
            embed.add_field(name="Auteur", value=article.author[:1_024], inline=True)

        embed.add_field(
            name="Lire l'article",
            value=f"[Ouvrir sur Codex YGO]({article.url})",
            inline=False,
        )

        footer = article.category_path if article.categories else "Nouvel article"
        embed.set_footer(text=f"Codex YGO • {footer}"[:2_048])
        return embed

    @classmethod
    def build_all(
        cls,
        article: CodexArticle,
    ) -> list[discord.Embed]:
        """Construit l'embed principal puis une galerie de visuels internes."""
        embeds = [cls.build(article)]
        image_urls = unique_image_urls(
            article.content_image_urls,
            excluded_urls=(article.image_url,) if article.image_url else (),
        )
        if not image_urls:
            return embeds

        style = cls.style_for(article)
        total = len(image_urls)
        for position, image_url in enumerate(image_urls, start=1):
            image_embed = discord.Embed(
                url=article.url,
                colour=discord.Colour(style.colour),
            )
            image_embed.set_image(url=image_url)
            image_embed.set_footer(
                text=f"Visuel {position}/{total} • {article.title}"[:2_048]
            )
            embeds.append(image_embed)

        return embeds

    @classmethod
    def build_batches(cls, article: CodexArticle) -> list[list[discord.Embed]]:
        """Répartit tous les visuels selon la limite de 10 embeds de Discord."""
        embeds = cls.build_all(article)
        return [
            embeds[start : start + DISCORD_MAX_EMBEDS_PER_MESSAGE]
            for start in range(0, len(embeds), DISCORD_MAX_EMBEDS_PER_MESSAGE)
        ]
