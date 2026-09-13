from __future__ import annotations

import unittest

from services.image_utils import canonical_image_key, unique_image_urls


class ImageUtilsTests(unittest.TestCase):
    def test_resized_and_queried_versions_share_the_same_key(self) -> None:
        original = "https://codexygo.fr/images/carte.png.webp"
        resized = "https://www.codexygo.fr/images/carte-768x432.png.webp?width=768&q=80"
        self.assertEqual(
            canonical_image_key(original),
            canonical_image_key(resized),
        )

    def test_next_image_proxy_uses_the_original_asset_identity(self) -> None:
        original = "https://codexygo.fr/images/carte.webp"
        proxied = (
            "https://codexygo.fr/_next/image?"
            "url=%2Fimages%2Fcarte.webp&w=1200&q=75"
        )
        self.assertEqual(
            canonical_image_key(original),
            canonical_image_key(proxied),
        )

    def test_unique_urls_preserve_only_the_first_version(self) -> None:
        urls = (
            "https://codexygo.fr/images/a.webp?width=1200",
            "https://codexygo.fr/images/a.webp?width=600",
            "https://codexygo.fr/images/b.webp",
        )
        self.assertEqual(
            unique_image_urls(urls),
            (urls[0], urls[2]),
        )


if __name__ == "__main__":
    unittest.main()
