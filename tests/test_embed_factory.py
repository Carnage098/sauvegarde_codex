from __future__ import annotations

import unittest

from models.article import CodexArticle
from services.embed_factory import CodexEmbedFactory


class CodexEmbedFactoryTests(unittest.TestCase):
    def test_build_all_limits_the_gallery(self) -> None:
        article = CodexArticle(
            title="Nouvelles cartes",
            url="https://codexygo.fr/article/nouvelles-cartes/",
            image_url="https://codexygo.fr/images/couverture.webp",
            content_image_urls=tuple(
                f"https://codexygo.fr/images/visuel-{index}.webp"
                for index in range(1, 7)
            ),
        )

        embeds = CodexEmbedFactory.build_all(article, max_content_images=4)

        self.assertEqual(len(embeds), 5)
        self.assertEqual(embeds[0].image.url, article.image_url)
        self.assertEqual(
            embeds[-1].image.url,
            "https://codexygo.fr/images/visuel-4.webp",
        )
        self.assertIn("Visuel 4/4", embeds[-1].footer.text)

    def test_gallery_can_be_disabled(self) -> None:
        article = CodexArticle(
            title="Article",
            url="https://codexygo.fr/article/test/",
            content_image_urls=("https://codexygo.fr/images/visuel.webp",),
        )
        self.assertEqual(
            len(CodexEmbedFactory.build_all(article, max_content_images=0)),
            1,
        )


if __name__ == "__main__":
    unittest.main()
