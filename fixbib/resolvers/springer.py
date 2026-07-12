from __future__ import annotations

from urllib.parse import quote

import httpx

from fixbib.model import BibCandidate, BibInputEntry
from fixbib.normalizer import find_entry_doi
from fixbib.resolvers.base import Resolver
from fixbib.resolvers.common import DEFAULT_USER_AGENT, candidates_from_bibtex


class SpringerResolver(Resolver):
    name = "springer-bibtex"

    def __init__(
        self,
        base_url: str = "https://citation-needed.springer.com/v2/references",
        timeout: float = 15.0,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout

    def can_resolve(self, entry: BibInputEntry) -> bool:
        doi = find_entry_doi(entry.fields)
        url = entry.fields.get("url", "").lower()
        return bool((doi and doi.startswith("10.1007/")) or "springer.com" in url)

    def resolve(self, entry: BibInputEntry) -> list[BibCandidate]:
        doi = find_entry_doi(entry.fields)
        if not doi:
            return []
        url = f"{self.base_url}/{quote(doi, safe='/:;()')}"
        try:
            with httpx.Client(follow_redirects=True, timeout=self.timeout) as client:
                response = client.get(
                    url,
                    params={"format": "bibtex", "flavour": "citation"},
                    headers={"User-Agent": DEFAULT_USER_AGENT, "Accept": "application/x-bibtex,text/plain"},
                )
                response.raise_for_status()
        except httpx.HTTPError as exc:
            return [
                BibCandidate(
                    source=self.name,
                    source_url=url,
                    bibtex=None,
                    fields={"doi": doi},
                    confidence="not_found",
                    score=0.0,
                    evidence=[f"request_failed={type(exc).__name__}: {exc}"],
                    source_priority=110,
                    canonical_id=doi,
                    source_kind="publisher_native_export",
                    source_family="springer",
                )
            ]
        return candidates_from_bibtex(
            entry=entry,
            source=self.name,
            source_url=str(response.url),
            text=response.text,
            source_priority=110,
            canonical_id=doi,
            extra_evidence=["springer_deterministic_export", "publisher_native_bibtex"],
            source_kind="publisher_native_export",
            source_family="springer",
        )
