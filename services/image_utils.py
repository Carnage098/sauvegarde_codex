from __future__ import annotations

import html
import re
from urllib.parse import parse_qs, unquote, urljoin, urlparse


_RESIZED_FILENAME = re.compile(
    r"-(?:\d{2,5}x\d{2,5}|scaled)(?=(?:\.[a-z0-9]+){1,2}$)",
    flags=re.IGNORECASE,
)


def canonical_image_key(url: str) -> str:
    """Retourne une identité stable pour reconnaître une même image.

    Les paramètres de redimensionnement, les proxies Next.js et les suffixes
    WordPress comme ``-768x432`` ne doivent pas créer de faux visuels distincts.
    """
    decoded_url = html.unescape(url.strip())
    parsed = urlparse(decoded_url)

    if parsed.path.rstrip("/").casefold().endswith("/_next/image"):
        proxied_values = parse_qs(parsed.query).get("url")
        if proxied_values:
            proxied_url = urljoin(decoded_url, unquote(proxied_values[0]))
            return canonical_image_key(proxied_url)

    hostname = (parsed.hostname or "").casefold()
    if hostname.startswith("www."):
        hostname = hostname[4:]

    path = unquote(parsed.path).casefold()
    path = re.sub(r"/+", "/", path)
    path = _RESIZED_FILENAME.sub("", path).rstrip("/")
    return f"{hostname}{path}"


def unique_image_urls(
    urls: tuple[str, ...] | list[str],
    *,
    excluded_urls: tuple[str, ...] | list[str] = (),
) -> tuple[str, ...]:
    """Déduplique des URLs en conservant leur ordre d'apparition."""
    seen = {canonical_image_key(url) for url in excluded_urls if url}
    output: list[str] = []

    for url in urls:
        key = canonical_image_key(url)
        if not key or key in seen:
            continue
        seen.add(key)
        output.append(url)

    return tuple(output)

