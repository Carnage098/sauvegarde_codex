from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime


@dataclass(frozen=True, slots=True)
class CodexArticle:
    title: str
    url: str
    description: str | None = None
    image_url: str | None = None
    author: str | None = None
    categories: tuple[str, ...] = ()
    published_at: datetime | None = None
    is_premium: bool = False

    @property
    def category_path(self) -> str:
        return " › ".join(self.categories) if self.categories else "Non classé"
