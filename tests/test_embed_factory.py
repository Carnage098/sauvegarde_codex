from __future__ import annotations

import unittest

from models.article import CodexArticle
from services.embed_factory import CodexEmbedFactory


class CodexEmbedFactoryTests(unittest.TestCase):
    def test_build_batches_keeps_the_complete_gallery(self) -> None:
        article = CodexArticle(
            title="Nouvelles cartes",
            url="https://codexygo.fr/article/nouvelles-cartes/",
            image_url="https://codexygo.fr/images/couverture.webp",
            content_image_urls=tuple(
                f"https://codexygo.fr/images/visuel-{index}.webp"
                for index in range(1, 24)
            ),
        )

        batches = CodexEmbedFactory.build_batches(article)
        embeds = [embed for batch in batches for embed in batch]

        self.assertEqual([len(batch) for batch in batches], [10, 10, 4])
        self.assertEqual(len(embeds), 24)
        self.assertEqual(embeds[0].image.url, article.image_url)
        self.assertEqual(
            embeds[-1].image.url,
            "https://codexygo.fr/images/visuel-23.webp",
        )
        self.assertIn("Visuel 23/23", embeds[-1].footer.text)

    def test_build_all_hides_cover_and_resized_duplicates(self) -> None:
        article = CodexArticle(
            title="Article avec doublons",
            url="https://codexygo.fr/article/test/",
            image_url="https://codexygo.fr/images/carte.png.webp",
            content_image_urls=(
                "https://codexygo.fr/images/carte-768x432.png.webp?width=768",
                "https://codexygo.fr/images/autre.webp?width=1200",
                "https://codexygo.fr/images/autre.webp?width=600",
            ),
        )

        embeds = CodexEmbedFactory.build_all(article)

        self.assertEqual(len(embeds), 2)
        self.assertEqual(
            embeds[1].image.url,
            "https://codexygo.fr/images/autre.webp?width=1200",
        )


if __name__ == "__main__":
    unittest.main()
