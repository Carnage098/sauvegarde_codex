from __future__ import annotations

import asyncio
import html as html_lib
import json
import logging
import re
import unicodedata
from datetime import datetime
from typing import Any, Iterable
from urllib.parse import urljoin, urlparse, urlunparse
from xml.etree import ElementTree

import aiohttp
from bs4 import BeautifulSoup, Tag
from playwright.async_api import (
    Browser,
    Playwright,
    TimeoutError as PlaywrightTimeoutError,
    async_playwright,
)

from models.article import CodexArticle

LOGGER = logging.getLogger(__name__)

CODEX_BASE_URL = "https://codexygo.fr/"
ARTICLE_PATH_PREFIX = "/article/"
CATEGORY_PATH_MARKER = "/categorie/"

# Page d'article stable utilisée uniquement comme source de découverte de secours.
# Les pages d'articles Codex affichent une section « Articles récents » rendue
# côté serveur, même lorsque l'accueil est chargé dynamiquement.
DISCOVERY_ARTICLE_URLS: tuple[str, ...] = (
    "https://codexygo.fr/article/bienvenue-1/",
)

CATEGORY_ALIASES: dict[str, str] = {
    "actualite": "Actualités",
    "actualites": "Actualités",
    "ocg tcg": "OCG / TCG",
    "tcg ocg": "OCG / TCG",
    "ocg": "OCG / TCG",
    "tcg": "OCG / TCG",
    "rush duel": "Rush Duel",
    "dossier": "Dossiers",
    "dossiers": "Dossiers",
    "recap": "Récap",
    "focus": "Focus",
    "ruling": "Rulings",
    "rulings": "Rulings",
    "codex": "Codex",
}

CATEGORY_SLUGS: dict[str, str] = {
    "actualites": "Actualités",
    "ocg-tcg": "OCG / TCG",
    "rush-duel": "Rush Duel",
    "dossiers": "Dossiers",
    "recap": "Récap",
    "focus": "Focus",
    "rulings": "Rulings",
    "codex": "Codex",
}


class CodexClient:
    def __init__(self) -> None:
        self._session: aiohttp.ClientSession | None = None
        self._playwright: Playwright | None = None
        self._browser: Browser | None = None
        self._browser_lock = asyncio.Lock()

    async def open(self) -> None:
        if self._session and not self._session.closed:
            return

        self._session = aiohttp.ClientSession(
            timeout=aiohttp.ClientTimeout(total=30),
            headers={
                # Codex YGO peut servir une page différente aux User-Agent de bots.
                # On utilise donc des en-têtes proches d'un navigateur classique.
                "User-Agent": (
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/126.0.0.0 Safari/537.36"
                ),
                "Accept": (
                    "text/html,application/xhtml+xml,application/xml;q=0.9,"
                    "image/avif,image/webp,*/*;q=0.8"
                ),
                "Accept-Language": "fr-FR,fr;q=0.9,en;q=0.7",
                "Cache-Control": "no-cache",
                "Pragma": "no-cache",
            },
        )

    async def close(self) -> None:
        if self._browser is not None:
            await self._browser.close()
            self._browser = None

        if self._playwright is not None:
            await self._playwright.stop()
            self._playwright = None

        if self._session and not self._session.closed:
            await self._session.close()
        self._session = None

    def _require_session(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            raise RuntimeError("La session HTTP Codex n'est pas ouverte.")
        return self._session

    async def _get_text(self, url: str) -> str:
        session = self._require_session()
        last_error: Exception | None = None

        for attempt in range(3):
            try:
                async with session.get(url, allow_redirects=True) as response:
                    if response.status == 429:
                        retry_after = response.headers.get("Retry-After", "5")
                        try:
                            delay = min(float(retry_after), 60.0)
                        except ValueError:
                            delay = 5.0
                        await asyncio.sleep(delay)
                        continue

                    response.raise_for_status()
                    return await response.text()
            except (aiohttp.ClientError, asyncio.TimeoutError) as exc:
                last_error = exc
                if attempt < 2:
                    await asyncio.sleep(2**attempt)

        raise RuntimeError(f"Impossible de récupérer {url}") from last_error

    async def _ensure_browser(self) -> Browser:
        if self._browser is not None and self._browser.is_connected():
            return self._browser

        async with self._browser_lock:
            if self._browser is not None and self._browser.is_connected():
                return self._browser

            if self._playwright is None:
                self._playwright = await async_playwright().start()

            self._browser = await self._playwright.chromium.launch(
                headless=True,
                args=[
                    "--disable-dev-shm-usage",
                    "--no-sandbox",
                ],
            )
            return self._browser

    async def _fetch_rendered_homepage_urls(self) -> list[str]:
        """Rend l'accueil avec Chromium pour exécuter le JavaScript du site."""
        browser = await self._ensure_browser()
        page = await browser.new_page(
            locale="fr-FR",
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/126.0.0.0 Safari/537.36"
            ),
        )

        try:
            await page.goto(
                CODEX_BASE_URL,
                wait_until="domcontentloaded",
                timeout=30_000,
            )

            try:
                await page.wait_for_selector(
                    'a[href*="/article/"]',
                    timeout=15_000,
                )
            except PlaywrightTimeoutError:
                # Le contenu peut arriver tardivement sans déclencher le sélecteur.
                await page.wait_for_timeout(3_000)

            rendered_html = await page.content()
            urls = self._extract_article_urls_from_html(rendered_html)

            if urls:
                LOGGER.info(
                    "%s lien(s) d'article trouvé(s) après rendu JavaScript.",
                    len(urls),
                )
            else:
                LOGGER.warning(
                    "Le rendu JavaScript de l'accueil ne contient toujours aucun lien d'article."
                )

            return urls
        finally:
            await page.close()

    @staticmethod
    def normalize_article_url(href: str) -> str | None:
        absolute_url = urljoin(CODEX_BASE_URL, href)
        parsed = urlparse(absolute_url)
        hostname = (parsed.hostname or "").lower()

        if hostname not in {"codexygo.fr", "www.codexygo.fr"}:
            return None
        if not parsed.path.startswith(ARTICLE_PATH_PREFIX):
            return None

        normalized_path = parsed.path.rstrip("/") + "/"
        normalized = parsed._replace(
            scheme="https",
            netloc="codexygo.fr",
            path=normalized_path,
            params="",
            query="",
            fragment="",
        )
        return urlunparse(normalized)

    async def fetch_homepage_article_urls(self) -> list[str]:
        """
        Récupère les articles récents avec plusieurs stratégies.

        Codex YGO peut placer les URLs dans des balises HTML, dans des données
        JSON JavaScript, ou ne les exposer que dans un sitemap. Le bot essaye
        donc ces méthodes dans cet ordre afin d'éviter les faux résultats vides.
        """
        html = await self._get_text(CODEX_BASE_URL)
        urls = self._extract_article_urls_from_html(html)

        if urls:
            LOGGER.info(
                "%s lien(s) d'article trouvé(s) dans la page d'accueil.",
                len(urls),
            )
            return urls

        soup = BeautifulSoup(html, "html.parser")
        page_title = soup.title.get_text(" ", strip=True) if soup.title else "sans titre"
        body_preview = soup.get_text(" ", strip=True)[:300].casefold()

        if any(marker in body_preview for marker in ("cloudflare", "just a moment", "captcha")):
            LOGGER.warning(
                "Codex YGO a probablement renvoyé une page de protection anti-bot "
                "(titre=%r, taille=%s).",
                page_title,
                len(html),
            )
        else:
            LOGGER.warning(
                "Aucun article trouvé directement dans l'accueil "
                "(titre=%r, taille=%s). Tentative via les catégories et sitemaps.",
                page_title,
                len(html),
            )

        # Le HTML brut de Codex peut ne contenir aucun lien : on exécute alors
        # le JavaScript de la page dans Chromium avant de tenter les autres secours.
        try:
            rendered_urls = await self._fetch_rendered_homepage_urls()
        except Exception:
            LOGGER.exception(
                "Échec du rendu JavaScript de l'accueil Codex YGO."
            )
            rendered_urls = []

        if rendered_urls:
            return rendered_urls

        # Certaines versions du site chargent aussi les catégories en JavaScript.
        # On garde néanmoins leur HTML brut comme solution de secours légère.
        category_urls = (
            "https://codexygo.fr/categorie/actualites/ocg-tcg/",
            "https://codexygo.fr/categorie/actualites/rush-duel/",
            "https://codexygo.fr/categorie/dossiers/recap/",
            "https://codexygo.fr/categorie/dossiers/focus/",
            "https://codexygo.fr/categorie/dossiers/rulings/",
        )

        combined: list[str] = []
        seen: set[str] = set()

        for category_url in category_urls:
            try:
                category_html = await self._get_text(category_url)
            except Exception:
                LOGGER.debug(
                    "Impossible de lire la catégorie %s",
                    category_url,
                    exc_info=True,
                )
                continue

            for url in self._extract_article_urls_from_html(category_html):
                if url not in seen:
                    seen.add(url)
                    combined.append(url)

        if combined:
            LOGGER.info(
                "%s lien(s) d'article trouvé(s) via les catégories.",
                len(combined),
            )
            return combined

        # Dernier secours avant les sitemaps : une page d'article permanente.
        # Codex YGO affiche une section « Articles récents » sur les pages
        # d'articles, ce qui permet d'obtenir les nouveaux liens même si
        # l'accueil et les catégories sont hydratés en JavaScript.
        discovery_urls = await self._fetch_discovery_article_urls()
        if discovery_urls:
            LOGGER.info(
                "%s lien(s) d'article trouvé(s) via une page d'article de secours.",
                len(discovery_urls),
            )
            return discovery_urls

        sitemap_urls = await self._fetch_sitemap_article_urls()
        if sitemap_urls:
            LOGGER.info(
                "%s lien(s) d'article trouvé(s) via un sitemap.",
                len(sitemap_urls),
            )
            return sitemap_urls

        return []

    async def _fetch_discovery_article_urls(self) -> list[str]:
        collected: list[str] = []
        seen: set[str] = set()

        for discovery_url in DISCOVERY_ARTICLE_URLS:
            try:
                raw_html = await self._get_text(discovery_url)
            except Exception:
                LOGGER.debug(
                    "Impossible de lire la page de découverte %s",
                    discovery_url,
                    exc_info=True,
                )
                continue

            urls = self._extract_recent_article_urls_from_html(raw_html)
            if not urls:
                # Le HTML du site peut changer : on garde l'extracteur général
                # comme solution de repli.
                urls = self._extract_article_urls_from_html(raw_html)

            normalized_discovery = self.normalize_article_url(discovery_url)
            for url in urls:
                if url == normalized_discovery or url in seen:
                    continue
                seen.add(url)
                collected.append(url)

            if collected:
                break

        return collected

    @classmethod
    def _extract_recent_article_urls_from_html(cls, raw_html: str) -> list[str]:
        """Extrait en priorité les liens de la section « Articles récents »."""
        decoded_html = html_lib.unescape(raw_html)
        decoded_html = (
            decoded_html
            .replace(r"\u002F", "/")
            .replace(r"\u002f", "/")
            .replace(r"\u003A", ":")
            .replace(r"\u003a", ":")
        )
        decoded_html = re.sub(r"\\+/", "/", decoded_html)
        soup = BeautifulSoup(decoded_html, "html.parser")

        def folded(value: str) -> str:
            normalized = unicodedata.normalize("NFKD", value)
            return "".join(
                char for char in normalized if not unicodedata.combining(char)
            ).casefold()

        heading: Tag | None = None
        for element in soup.find_all(["h2", "h3", "h4", "h5", "strong", "div"]):
            if not isinstance(element, Tag):
                continue
            text = re.sub(r"\s+", " ", element.get_text(" ", strip=True))
            if text and "articles recents" in folded(text) and len(text) <= 80:
                heading = element
                break

        candidates: list[str] = []

        if heading is not None:
            # Cherche le plus petit parent contenant plusieurs liens d'articles.
            for parent in [heading, *list(heading.parents)[:6]]:
                if not isinstance(parent, Tag):
                    continue
                hrefs = [
                    anchor.get("href")
                    for anchor in parent.find_all("a", href=True)
                    if isinstance(anchor.get("href"), str)
                    and "/article/" in str(anchor.get("href"))
                ]
                if len(hrefs) >= 2:
                    candidates.extend(str(href) for href in hrefs)
                    break

            # Certains thèmes placent les cartes dans les frères qui suivent le titre.
            if not candidates:
                sibling = heading.find_next_sibling()
                scanned = 0
                while isinstance(sibling, Tag) and scanned < 8:
                    for anchor in sibling.find_all("a", href=True):
                        href = anchor.get("href")
                        if isinstance(href, str) and "/article/" in href:
                            candidates.append(href)
                    sibling = sibling.find_next_sibling()
                    scanned += 1

        if not candidates:
            return []

        urls: list[str] = []
        seen: set[str] = set()
        for candidate in candidates:
            normalized = cls.normalize_article_url(candidate)
            if normalized and normalized not in seen:
                seen.add(normalized)
                urls.append(normalized)

        return urls

    @classmethod
    def _extract_article_urls_from_html(cls, raw_html: str) -> list[str]:
        """Extrait les URLs depuis le HTML visible et les scripts JSON intégrés."""
        decoded_html = html_lib.unescape(raw_html)
        decoded_html = (
            decoded_html
            .replace(r"\u002F", "/")
            .replace(r"\u002f", "/")
            .replace(r"\u003A", ":")
            .replace(r"\u003a", ":")
        )
        # Accepte aussi les JSON doublement échappés : \\/article\\/.
        decoded_html = re.sub(r"\\+/", "/", decoded_html)

        candidates: list[str] = []
        soup = BeautifulSoup(decoded_html, "html.parser")

        # Première méthode : les véritables liens HTML.
        for anchor in soup.find_all("a", href=True):
            href = anchor.get("href")
            if isinstance(href, str):
                candidates.append(href)

        # Deuxième méthode : URLs stockées dans __NEXT_DATA__, JSON-LD, scripts, etc.
        article_pattern = re.compile(
            r"(?:(?:https?:)?//(?:www\.)?codexygo\.fr)?"
            r"/article/[^\s\"'<>\\?#]+",
            flags=re.IGNORECASE,
        )
        candidates.extend(match.group(0) for match in article_pattern.finditer(decoded_html))

        urls: list[str] = []
        seen: set[str] = set()

        for candidate in candidates:
            normalized = cls.normalize_article_url(candidate)
            if normalized and normalized not in seen:
                seen.add(normalized)
                urls.append(normalized)

        return urls

    async def _fetch_sitemap_article_urls(self) -> list[str]:
        """Essaye les emplacements de sitemap courants sans ajouter de dépendance."""
        sitemap_candidates = (
            "https://codexygo.fr/sitemap.xml",
            "https://codexygo.fr/sitemap_index.xml",
            "https://codexygo.fr/article-sitemap.xml",
            "https://codexygo.fr/post-sitemap.xml",
        )

        collected: list[str] = []
        seen: set[str] = set()

        async def parse_sitemap(url: str, *, allow_children: bool) -> list[str]:
            try:
                xml_text = await self._get_text(url)
                root = ElementTree.fromstring(xml_text)
            except Exception:
                LOGGER.debug("Sitemap indisponible : %s", url, exc_info=True)
                return []

            values: list[str] = []
            child_sitemaps: list[str] = []

            for element in root.iter():
                if not element.tag.lower().endswith("loc") or not element.text:
                    continue

                location = element.text.strip()
                if location.lower().endswith(".xml"):
                    child_sitemaps.append(location)
                    continue

                normalized = self.normalize_article_url(location)
                if normalized:
                    values.append(normalized)

            if allow_children:
                # Limite le nombre de requêtes en cas de très gros index de sitemaps.
                relevant_children = [
                    child
                    for child in child_sitemaps
                    if any(word in child.casefold() for word in ("article", "post", "page"))
                ] or child_sitemaps

                for child in relevant_children[:10]:
                    values.extend(await parse_sitemap(child, allow_children=False))

            return values

        for sitemap_url in sitemap_candidates:
            for article_url in await parse_sitemap(sitemap_url, allow_children=True):
                if article_url not in seen:
                    seen.add(article_url)
                    collected.append(article_url)

            if collected:
                break

        return collected

    async def fetch_article(self, url: str) -> CodexArticle:
        html = await self._get_text(url)
        return self.parse_article_html(url, html)

    @classmethod
    def parse_article_html(cls, url: str, html: str) -> CodexArticle:
        soup = BeautifulSoup(html, "html.parser")

        title = (
            cls._meta_content(soup, property_name="og:title")
            or cls._meta_content(soup, name="twitter:title")
            or cls._heading_text(soup, "h1")
            or cls._title_text(soup)
            or "Nouvel article Codex YGO"
        )
        title = cls._clean_title(title)[:256]

        description = (
            cls._meta_content(soup, property_name="og:description")
            or cls._meta_content(soup, name="description")
            or cls._first_article_paragraph(soup)
        )
        description = cls._clean_description(description)

        image_url = (
            cls._meta_content(soup, property_name="og:image")
            or cls._meta_content(soup, name="twitter:image")
        )
        if image_url:
            image_url = urljoin(url, image_url)

        author = (
            cls._meta_content(soup, name="author")
            or cls._meta_content(soup, property_name="article:author")
            or cls._json_ld_author(soup)
        )

        categories = cls._extract_categories(soup)
        published_at = cls._extract_published_at(soup)
        is_premium = cls._detect_premium(soup)

        return CodexArticle(
            title=title,
            url=url,
            description=description,
            image_url=image_url,
            author=author,
            categories=categories,
            published_at=published_at,
            is_premium=is_premium,
        )

    @staticmethod
    def _meta_content(
        soup: BeautifulSoup,
        *,
        property_name: str | None = None,
        name: str | None = None,
    ) -> str | None:
        attrs: dict[str, str] = {}
        if property_name:
            attrs["property"] = property_name
        elif name:
            attrs["name"] = name
        else:
            return None

        element = soup.find("meta", attrs=attrs)
        if not isinstance(element, Tag):
            return None

        content = element.get("content")
        return content.strip() if isinstance(content, str) and content.strip() else None

    @staticmethod
    def _heading_text(soup: BeautifulSoup, tag_name: str) -> str | None:
        element = soup.find(tag_name)
        if not isinstance(element, Tag):
            return None
        text = element.get_text(" ", strip=True)
        return text or None

    @staticmethod
    def _title_text(soup: BeautifulSoup) -> str | None:
        if not isinstance(soup.title, Tag):
            return None
        text = soup.title.get_text(" ", strip=True)
        return text or None

    @staticmethod
    def _clean_title(title: str) -> str:
        cleaned = re.sub(r"\s+", " ", title).strip()
        for prefix in ("CodexYGO | ", "Codex YGO | "):
            if cleaned.startswith(prefix):
                cleaned = cleaned[len(prefix) :]
        for suffix in (" | CodexYGO", " - CodexYGO", " | Codex YGO"):
            if cleaned.endswith(suffix):
                cleaned = cleaned[: -len(suffix)]
        return cleaned.strip()

    @staticmethod
    def _clean_description(description: str | None) -> str | None:
        if not description:
            return None
        cleaned = re.sub(r"\s+", " ", description).strip()
        if not cleaned:
            return None
        return cleaned[:1_000] + ("…" if len(cleaned) > 1_000 else "")

    @staticmethod
    def _first_article_paragraph(soup: BeautifulSoup) -> str | None:
        container = soup.find("article") or soup.find("main")
        if not isinstance(container, Tag):
            return None

        for paragraph in container.find_all("p"):
            text = paragraph.get_text(" ", strip=True)
            if len(text) >= 40:
                return text
        return None

    @classmethod
    def _extract_categories(cls, soup: BeautifulSoup) -> tuple[str, ...]:
        candidates: list[list[str]] = []

        # 1. JSON-LD : le moyen le plus sémantique lorsqu'il est présent.
        for data in cls._json_ld_objects(soup):
            section = data.get("articleSection") if isinstance(data, dict) else None
            values = cls._to_string_list(section)
            if values:
                candidates.append(values)

        # 2. Métadonnée Open Graph / article.
        meta_section = cls._meta_content(soup, property_name="article:section")
        if meta_section:
            candidates.append([meta_section])

        # 3. Liens de catégories placés dans l'article ou son fil d'Ariane.
        article_container = cls._main_article_container(soup)
        category_paths: list[list[str]] = []
        for anchor in article_container.select('a[href*="/categorie/"]'):
            href = anchor.get("href")
            if not isinstance(href, str):
                continue
            categories = cls._categories_from_category_url(href)
            if categories:
                category_paths.append(list(categories))

        if category_paths:
            # Le chemin le plus profond contient normalement catégorie + sous-catégorie.
            category_paths.sort(key=len, reverse=True)
            candidates.append(category_paths[0])

        # 4. Classes CSS ou rel=category, utiles sur certains moteurs de site.
        taxonomy_texts: list[str] = []
        for element in article_container.select(
            '[rel~="category"], .category, .categories, .post-category, .article-category'
        ):
            text = element.get_text(" ", strip=True)
            if text:
                taxonomy_texts.append(text)
        if taxonomy_texts:
            candidates.append(taxonomy_texts)

        normalized_candidates: list[tuple[str, ...]] = []
        for candidate in candidates:
            normalized = cls._normalize_category_sequence(candidate)
            if normalized:
                normalized_candidates.append(normalized)

        if not normalized_candidates:
            return ()

        # La séquence la plus précise est privilégiée, tout en gardant l'ordre.
        normalized_candidates.sort(key=len, reverse=True)
        return normalized_candidates[0]

    @staticmethod
    def _main_article_container(soup: BeautifulSoup) -> Tag:
        heading = soup.find("h1")
        if isinstance(heading, Tag):
            parent = heading.find_parent("article") or heading.find_parent("main")
            if isinstance(parent, Tag):
                return parent

        article = soup.find("article") or soup.find("main") or soup.body
        return article if isinstance(article, Tag) else soup

    @classmethod
    def _categories_from_category_url(cls, href: str) -> tuple[str, ...]:
        absolute = urljoin(CODEX_BASE_URL, href)
        path = urlparse(absolute).path
        if CATEGORY_PATH_MARKER not in path:
            return ()

        remainder = path.split(CATEGORY_PATH_MARKER, 1)[1]
        slugs = [slug for slug in remainder.split("/") if slug]
        return tuple(
            CATEGORY_SLUGS.get(slug.lower(), slug.replace("-", " ").title())
            for slug in slugs
        )

    @classmethod
    def _normalize_category_sequence(cls, values: Iterable[str]) -> tuple[str, ...]:
        result: list[str] = []

        for raw_value in values:
            # Une valeur peut contenir un chemin ou plusieurs séparateurs.
            pieces = re.split(r"\s*(?:›|>|/|\||,)\s*", raw_value)
            for piece in pieces:
                category = cls._canonical_category(piece)
                if category and category not in result:
                    result.append(category)

        # Reconstitue la catégorie parente connue lorsque seule la sous-catégorie existe.
        if result and result[0] in {"OCG / TCG", "Rush Duel"}:
            result.insert(0, "Actualités")
        elif result and result[0] in {"Récap", "Focus", "Rulings"}:
            result.insert(0, "Dossiers")

        return tuple(result)

    @staticmethod
    def _fold_text(value: str) -> str:
        normalized = unicodedata.normalize("NFKD", value)
        ascii_text = "".join(char for char in normalized if not unicodedata.combining(char))
        ascii_text = ascii_text.casefold().replace("&", " ")
        ascii_text = re.sub(r"[^a-z0-9]+", " ", ascii_text)
        return re.sub(r"\s+", " ", ascii_text).strip()

    @classmethod
    def _canonical_category(cls, value: str) -> str | None:
        folded = cls._fold_text(value)
        if not folded:
            return None

        if folded in CATEGORY_ALIASES:
            return CATEGORY_ALIASES[folded]

        # Accepte seulement une valeur courte afin de ne pas transformer une phrase en catégorie.
        if len(folded.split()) <= 4 and len(value.strip()) <= 40:
            return value.strip().title()
        return None

    @classmethod
    def _json_ld_objects(cls, soup: BeautifulSoup) -> list[dict[str, Any]]:
        objects: list[dict[str, Any]] = []

        for script in soup.find_all("script", attrs={"type": "application/ld+json"}):
            raw = script.string or script.get_text(strip=True)
            if not raw:
                continue
            try:
                payload = json.loads(raw)
            except json.JSONDecodeError:
                continue

            def collect(value: Any) -> None:
                if isinstance(value, dict):
                    objects.append(value)
                    graph = value.get("@graph")
                    if isinstance(graph, list):
                        for item in graph:
                            collect(item)
                elif isinstance(value, list):
                    for item in value:
                        collect(item)

            collect(payload)

        return objects

    @staticmethod
    def _to_string_list(value: Any) -> list[str]:
        if isinstance(value, str):
            return [value]
        if isinstance(value, list):
            return [item for item in value if isinstance(item, str)]
        return []

    @classmethod
    def _json_ld_author(cls, soup: BeautifulSoup) -> str | None:
        for data in cls._json_ld_objects(soup):
            author = data.get("author")
            if isinstance(author, str):
                return author.strip() or None
            if isinstance(author, dict):
                name = author.get("name")
                if isinstance(name, str) and name.strip():
                    return name.strip()
            if isinstance(author, list):
                names = [
                    item.get("name", "").strip()
                    for item in author
                    if isinstance(item, dict) and isinstance(item.get("name"), str)
                ]
                names = [name for name in names if name]
                if names:
                    return ", ".join(names)
        return None

    @classmethod
    def _extract_published_at(cls, soup: BeautifulSoup) -> datetime | None:
        raw_values: list[str] = []

        for property_name in ("article:published_time", "og:published_time"):
            value = cls._meta_content(soup, property_name=property_name)
            if value:
                raw_values.append(value)

        time_element = soup.find("time")
        if isinstance(time_element, Tag):
            datetime_value = time_element.get("datetime")
            if isinstance(datetime_value, str):
                raw_values.append(datetime_value)

        for data in cls._json_ld_objects(soup):
            for key in ("datePublished", "dateCreated"):
                value = data.get(key)
                if isinstance(value, str):
                    raw_values.append(value)

        for raw_value in raw_values:
            try:
                return datetime.fromisoformat(raw_value.strip().replace("Z", "+00:00"))
            except ValueError:
                continue
        return None

    @staticmethod
    def _detect_premium(soup: BeautifulSoup) -> bool:
        meta_value = CodexClient._meta_content(soup, name="robots") or ""
        body_text = soup.get_text(" ", strip=True).casefold()
        return "premium" in body_text[:2_000] or "noindex" in meta_value.casefold()
