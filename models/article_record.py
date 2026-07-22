from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from models.article import CodexArticle


@dataclass(frozen=True, slots=True)
class ArticleRecord:
    id: int
    article: CodexArticle
    announced: bool
    detected_at: datetime | None = None
    updated_at: datetime | None = None
