from __future__ import annotations

import math
from datetime import timezone

import discord

from models.article_record import ArticleRecord


class SearchResultsView(discord.ui.View):
    """Pagination privée pour une liste de résultats Codex."""

    def __init__(
        self,
        *,
        requester_id: int,
        records: list[ArticleRecord],
        title: str,
        empty_footer: str,
        page_size: int = 5,
    ) -> None:
        super().__init__(timeout=300)
        self.requester_id = requester_id
        self.records = records
        self.title = title
        self.empty_footer = empty_footer
        self.page_size = max(1, page_size)
        self.page = 0
        self._refresh_buttons()

    @property
    def page_count(self) -> int:
        return max(1, math.ceil(len(self.records) / self.page_size))

    def _page_records(self) -> list[ArticleRecord]:
        start = self.page * self.page_size
        return self.records[start : start + self.page_size]

    @staticmethod
    def _record_line(position: int, record: ArticleRecord) -> str:
        published_at = record.article.published_at
        if published_at and published_at.tzinfo is None:
            published_at = published_at.replace(tzinfo=timezone.utc)
        date_text = (
            discord.utils.format_dt(published_at, "d")
            if published_at
            else "date inconnue"
        )
        premium = " • ⭐ Premium" if record.article.is_premium else ""
        return (
            f"**{position}. [{record.article.title}]({record.article.url})**\n"
            f"`#{record.id}` • {record.article.category_path} • {date_text}{premium}"
        )

    def build_embed(self) -> discord.Embed:
        start = self.page * self.page_size
        lines = [
            self._record_line(start + index, record)
            for index, record in enumerate(self._page_records(), start=1)
        ]
        embed = discord.Embed(
            title=self.title[:256],
            description="\n\n".join(lines)[:4_096],
            colour=discord.Colour.blurple(),
        )
        embed.set_footer(
            text=(
                f"Page {self.page + 1}/{self.page_count} • "
                f"{len(self.records)} résultat(s) • {self.empty_footer}"
            )[:2_048]
        )
        return embed

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id == self.requester_id:
            return True
        await interaction.response.send_message(
            "Seule la personne ayant lancé la recherche peut utiliser ces boutons.",
            ephemeral=True,
        )
        return False

    def _refresh_buttons(self) -> None:
        self.previous_button.disabled = self.page <= 0
        self.next_button.disabled = self.page >= self.page_count - 1

    async def _show_page(self, interaction: discord.Interaction) -> None:
        self._refresh_buttons()
        await interaction.response.edit_message(embed=self.build_embed(), view=self)

    @discord.ui.button(
        label="Précédent",
        emoji="◀️",
        style=discord.ButtonStyle.secondary,
    )
    async def previous_button(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button,
    ) -> None:
        del button
        self.page = max(0, self.page - 1)
        await self._show_page(interaction)

    @discord.ui.button(
        label="Suivant",
        emoji="▶️",
        style=discord.ButtonStyle.primary,
    )
    async def next_button(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button,
    ) -> None:
        del button
        self.page = min(self.page_count - 1, self.page + 1)
        await self._show_page(interaction)

