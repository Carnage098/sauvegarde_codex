from __future__ import annotations

import discord

from models.article import CodexArticle


class ArticleLinkView(discord.ui.View):
    def __init__(self, article: CodexArticle) -> None:
        super().__init__(timeout=None)
        self.add_item(
            discord.ui.Button(
                label="Lire l'article",
                emoji="🔗",
                style=discord.ButtonStyle.link,
                url=article.url,
            )
        )
