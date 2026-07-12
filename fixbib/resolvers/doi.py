from __future__ import annotations

from urllib.parse import quote, urlparse

import httpx

from fixbib.model import BibCandidate, BibInputEntry
from fixbib.normalizer import find_entry_doi
from fixbib.provenance import infer_source_family
from fixbib.resolvers.base import Resolver
from fixbib.resolvers.common import DEFAULT_USER_AGENT, candidates_from_bibtex


class DoiBibtexResolver(Resolver):
    """Fetch a BibTeX representation through DOI content negotiation.

    This endpoint usually delegates to a registration agency such as Crossref.
    The returned BibTeX is a generated schema transformation, not necessarily a
    publisher-native export. It therefore has lower formatting authority than
    publisher BibTeX or structured registry JSON.
    """

    name = "doi-content-negotiation"

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
        try:
            with httpx.Client(follow_redirects=True, timeout=self.timeout) as client:
                response = client.get(
                    url,
                    headers={
                        "Accept": "application/x-bibtex",
                        "User-Agent": DEFAULT_USER_AGENT,
                    },
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
                    source_priority=70,
                    canonical_id=doi,
                    source_kind="registry_transform",
                    source_family="doi",
                )
            ]

        source, family = _transform_backend(str(response.url))
        evidence = [
            "resolved_by_exact_doi",
            f"content_negotiation_backend={family}",
            "metadata_representation=generated_bibtex",
            "not_publisher_native_export",
        ]
        candidates = candidates_from_bibtex(
            entry=entry,
            source=source,
            source_url=str(response.url),
            text=response.text,
            source_priority=70,
            canonical_id=doi,
            extra_evidence=evidence,
            source_kind="registry_transform",
            source_family=family,
        )
        if candidates:
            return candidates
        return [
            BibCandidate(
                source=source,
                source_url=str(response.url),
                bibtex=None,
                fields={"doi": doi},
                confidence="not_found",
                score=0.0,
                evidence=evidence + ["response_contained_no_bibtex_entries"],
                source_priority=70,
                canonical_id=doi,
                source_kind="registry_transform",
                source_family=family,
            )
        ]


def _transform_backend(url: str) -> tuple[str, str]:
    host = urlparse(url).netloc.lower()
    if "crossref.org" in host:
        return "crossref-transform", "crossref"
    if "datacite.org" in host:
        return "datacite-transform", "datacite"
    family = infer_source_family("doi-content-negotiation", url)
    return "doi-content-negotiation", family
