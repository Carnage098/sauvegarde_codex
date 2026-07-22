from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class IndexReport:
    discovered_urls: int = 0
    fetched_articles: int = 0
    inserted_articles: int = 0
    updated_articles: int = 0
    failed_articles: int = 0

    @property
    def changed_articles(self) -> int:
        return self.inserted_articles + self.updated_articles
