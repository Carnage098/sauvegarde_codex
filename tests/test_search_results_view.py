from __future__ import annotations

import unittest
from datetime import datetime, timezone

from models.article import CodexArticle
from models.article_record import ArticleRecord
from views.search_results_view import SearchResultsView


def make_record(article_id: int, *, premium: bool = False) -> ArticleRecord:
    return ArticleRecord(
        id=article_id,
        article=CodexArticle(
            title=f"Article {article_id}",
            url=f"https://codexygo.fr/article/test-{article_id}/",
            categories=("Actualités", "OCG / TCG"),
            published_at=datetime(2026, 9, article_id, tzinfo=timezone.utc),
            is_premium=premium,
        ),
        announced=True,
    )


class SearchResultsViewTests(unittest.TestCase):
    def test_pagination_and_buttons(self) -> None:
        view = SearchResultsView(
            requester_id=42,
            records=[make_record(index) for index in range(1, 13)],
            title="Résultats",
            empty_footer="Aide",
            page_size=5,
        )

        self.assertEqual(view.page_count, 3)
        self.assertTrue(view.previous_button.disabled)
        self.assertFalse(view.next_button.disabled)
        self.assertIn("Article 1", view.build_embed().description)
        self.assertNotIn("Article 6", view.build_embed().description)

        view.page = 2
        view._refresh_buttons()
        self.assertFalse(view.previous_button.disabled)
        self.assertTrue(view.next_button.disabled)
        self.assertIn("Page 3/3", view.build_embed().footer.text)

    def test_premium_is_visible(self) -> None:
        view = SearchResultsView(
            requester_id=42,
            records=[make_record(1, premium=True)],
            title="Résultats",
            empty_footer="Aide",
        )
        self.assertIn("Premium", view.build_embed().description)


if __name__ == "__main__":
    unittest.main()
