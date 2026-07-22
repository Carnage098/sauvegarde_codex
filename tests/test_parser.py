from __future__ import annotations

import unittest

from services.codex_client import CodexClient


class CodexParserTests(unittest.TestCase):
    def test_category_from_category_link(self) -> None:
        html = """
        <html>
          <head>
            <meta property="og:title" content="Une annonce importante | CodexYGO">
            <meta property="og:description" content="Description de test">
            <meta property="og:image" content="/images/article.jpg">
          </head>
          <body>
            <main>
              <article>
                <h1>Une annonce importante</h1>
                <a href="/categorie/actualites/ocg-tcg/">OCG / TCG</a>
              </article>
            </main>
          </body>
        </html>
        """

        article = CodexClient.parse_article_html(
            "https://codexygo.fr/article/test-1/",
            html,
        )

        self.assertEqual(article.title, "Une annonce importante")
        self.assertEqual(article.categories, ("Actualités", "OCG / TCG"))
        self.assertEqual(
            article.image_url,
            "https://codexygo.fr/images/article.jpg",
        )

    def test_category_from_json_ld(self) -> None:
        html = """
        <html>
          <head>
            <script type="application/ld+json">
              {
                "@type": "Article",
                "headline": "Test rulings",
                "articleSection": ["Dossiers", "Rulings"],
                "author": {"@type": "Person", "name": "Joeri_sama"},
                "datePublished": "2026-07-20T18:00:00+02:00"
              }
            </script>
          </head>
          <body><article><h1>Test rulings</h1></article></body>
        </html>
        """

        article = CodexClient.parse_article_html(
            "https://codexygo.fr/article/test-2/",
            html,
        )

        self.assertEqual(article.categories, ("Dossiers", "Rulings"))
        self.assertEqual(article.author, "Joeri_sama")
        self.assertIsNotNone(article.published_at)

    def test_url_normalization(self) -> None:
        self.assertEqual(
            CodexClient.normalize_article_url(
                "https://www.codexygo.fr/article/exemple-10/?utm_source=test#fin"
            ),
            "https://codexygo.fr/article/exemple-10/",
        )
        self.assertIsNone(
            CodexClient.normalize_article_url("https://example.com/article/test/")
        )

    def test_article_urls_from_javascript_data(self) -> None:
        html = r'''
        <html><head><script>
        window.__DATA__ = {"url":"https:\\/\\/codexygo.fr\\/article\\/test-123\\/"};
        </script></head><body></body></html>
        '''
        urls = CodexClient._extract_article_urls_from_html(html)
        self.assertEqual(
            urls,
            ["https://codexygo.fr/article/test-123/"],
        )

    def test_recent_section_urls(self) -> None:
        html = """
        <html><body>
          <article><a href="/article/ancien-lien-1/">Lien dans le texte</a></article>
          <aside>
            <h3>Articles récents</h3>
            <div>
              <a href="/article/nouveau-516/">Nouveau</a>
              <a href="https://codexygo.fr/article/nouveau-515/">Deuxième</a>
            </div>
          </aside>
        </body></html>
        """
        urls = CodexClient._extract_recent_article_urls_from_html(html)
        self.assertEqual(
            urls,
            [
                "https://codexygo.fr/article/nouveau-516/",
                "https://codexygo.fr/article/nouveau-515/",
            ],
        )


if __name__ == "__main__":
    unittest.main()
