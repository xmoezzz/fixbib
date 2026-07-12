from __future__ import annotations

from urllib.parse import quote

from fixbib.model import BibCandidate, BibInputEntry
from fixbib.normalizer import find_entry_doi
from fixbib.resolvers.base import Resolver
from fixbib.resolvers.page import resolve_publication_url


class DoiLandingPageResolver(Resolver):
    """Open a DOI as a normal web link and inspect the final publication page."""

    name = "doi-landing-page"

    def __init__(self, base_url: str = "https://doi.org", timeout: float = 15.0) -> None:
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout

    def can_resolve(self, entry: BibInputEntry) -> bool:
        return find_entry_doi(entry.fields) is not None

    def resolve(self, entry: BibInputEntry) -> list[BibCandidate]:
        doi = find_entry_doi(entry.fields)
        if not doi:
            return []
        url = f"{self.base_url}/{quote(doi, safe='/:;()')}"
        return resolve_publication_url(
            entry,
            url,
            source=self.name,
            source_priority=88,
            timeout=self.timeout,
            extra_evidence=["doi_opened_as_html_landing_page", f"doi={doi}"],
            emit_probe=True,
        )
