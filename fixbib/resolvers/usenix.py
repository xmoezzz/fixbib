from __future__ import annotations

import re
from urllib.parse import urljoin, urlparse

import httpx
from bs4 import BeautifulSoup

from fixbib.model import BibCandidate, BibInputEntry
from fixbib.resolvers.base import Resolver
from fixbib.resolvers.common import DEFAULT_USER_AGENT, candidates_from_bibtex

BIB_BLOCK_RE = re.compile(r"@(article|inproceedings|proceedings|misc|book|incollection)\s*[({]", re.I)


class UsenixResolver(Resolver):
    name = "usenix-bibtex"

    def __init__(self, timeout: float = 15.0) -> None:
        self.timeout = timeout

    def can_resolve(self, entry: BibInputEntry) -> bool:
        return "usenix.org" in urlparse(entry.fields.get("url", "")).netloc.lower()

    def resolve(self, entry: BibInputEntry) -> list[BibCandidate]:
        page_url = entry.fields.get("url", "").strip()
        if not page_url:
            return []
        try:
            with httpx.Client(follow_redirects=True, timeout=self.timeout) as client:
                page = client.get(page_url, headers={"User-Agent": DEFAULT_USER_AGENT, "Accept": "text/html"})
                page.raise_for_status()
                soup = BeautifulSoup(page.text, "html.parser")
                for link in soup.find_all("a", href=True):
                    href = str(link.get("href") or "")
                    if "biblio/export/bibtex" in href.lower():
                        export_url = urljoin(str(page.url), href)
                        response = client.get(export_url, headers={"User-Agent": DEFAULT_USER_AGENT})
                        response.raise_for_status()
                        bundle = candidates_from_bibtex(
                            entry=entry,
                            source=self.name,
                            source_url=str(response.url),
                            text=response.text,
                            source_priority=105,
                            extra_evidence=["usenix_export_link", "publisher_native_bibtex"],
                            source_kind="publisher_native_export",
                            source_family="usenix",
                        )
                        if bundle:
                            return bundle

                inline = _extract_inline_bibtex(page.text)
                if inline:
                    return candidates_from_bibtex(
                        entry=entry,
                        source="usenix-inline-bibtex",
                        source_url=str(page.url),
                        text=inline,
                        source_priority=100,
                        extra_evidence=["usenix_inline_bibtex", "publisher_native_bibtex"],
                        source_kind="publisher_native_export",
                        source_family="usenix",
                    )
        except httpx.HTTPError as exc:
            return [
                BibCandidate(
                    source=self.name,
                    source_url=page_url,
                    bibtex=None,
                    fields={},
                    confidence="not_found",
                    score=0.0,
                    evidence=[f"request_failed={type(exc).__name__}: {exc}"],
                    source_priority=105,
                    source_kind="publisher_native_export",
                    source_family="usenix",
                )
            ]
        return []


def _extract_inline_bibtex(html: str) -> str | None:
    soup = BeautifulSoup(html, "html.parser")
    for node in soup.find_all(["pre", "code", "textarea"]):
        text = node.get_text("\n", strip=True)
        if BIB_BLOCK_RE.search(text):
            return text
    return None
