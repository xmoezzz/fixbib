from __future__ import annotations

from typing import Any

import httpx

from fixbib.model import BibCandidate, BibInputEntry
from fixbib.resolvers.base import Resolver
from fixbib.resolvers.common import DEFAULT_USER_AGENT, candidate_from_fields


class SemanticScholarResolver(Resolver):
    name = "semantic-scholar-search"

    def __init__(
        self,
        api_key: str | None = None,
        api_base: str = "https://api.semanticscholar.org/graph/v1/paper/search",
        timeout: float = 15.0,
        max_results: int = 5,
        enabled: bool = False,
    ) -> None:
        self.api_key = api_key
        self.api_base = api_base
        self.timeout = timeout
        self.max_results = max_results
        self.enabled = enabled

    def can_resolve(self, entry: BibInputEntry) -> bool:
        return bool(self.enabled and entry.fields.get("title"))

    def resolve(self, entry: BibInputEntry) -> list[BibCandidate]:
        if not self.can_resolve(entry):
            return []
        headers = {"User-Agent": DEFAULT_USER_AGENT, "Accept": "application/json"}
        if self.api_key:
            headers["x-api-key"] = self.api_key
        try:
            with httpx.Client(follow_redirects=True, timeout=self.timeout) as client:
                response = client.get(
                    self.api_base,
                    params={
                        "query": entry.fields.get("title", ""),
                        "limit": self.max_results,
                        "fields": "paperId,title,authors,year,venue,url,externalIds,journal,publicationTypes",
                    },
                    headers=headers,
                )
                response.raise_for_status()
                payload = response.json()
        except (httpx.HTTPError, ValueError) as exc:
            return [
                BibCandidate(
                    source=self.name,
                    source_url=self.api_base,
                    bibtex=None,
                    fields={},
                    confidence="not_found",
                    score=0.0,
                    evidence=[f"request_failed={type(exc).__name__}: {exc}"],
                    source_priority=60,
                    source_kind="bibliographic_index",
                    source_family="semantic-scholar",
                )
            ]

        candidates: list[BibCandidate] = []
        for paper in payload.get("data", [])[: self.max_results]:
            fields, entry_type = _s2_to_fields(paper)
            candidates.append(
                candidate_from_fields(
                    entry=entry,
                    source=self.name,
                    source_url=fields.get("url") or paper.get("url"),
                    fields=fields,
                    source_priority=60,
                    entry_type=entry_type,
                    canonical_id=paper.get("paperId"),
                    extra_evidence=["semantic_scholar_title_search"],
                    source_kind="bibliographic_index",
                    source_family="semantic-scholar",
                )
            )
        return candidates


def _s2_to_fields(paper: dict[str, Any]) -> tuple[dict[str, str], str]:
    fields: dict[str, str] = {}
    for key in ("title", "year", "url"):
        if paper.get(key):
            fields[key] = str(paper[key])
    authors = [str(a.get("name") or "") for a in paper.get("authors", []) if a.get("name")]
    if authors:
        fields["author"] = " and ".join(authors)
    external_ids = paper.get("externalIds") or {}
    if external_ids.get("DOI"):
        fields["doi"] = str(external_ids["DOI"])
    if external_ids.get("ArXiv"):
        fields["eprint"] = str(external_ids["ArXiv"])
        fields["archiveprefix"] = "arXiv"

    journal = paper.get("journal") or {}
    venue = str(journal.get("name") or paper.get("venue") or "")
    publication_types = {str(x).lower() for x in paper.get("publicationTypes", []) or []}
    entry_type = "article"
    if "conference" in publication_types:
        entry_type = "inproceedings"
        if venue:
            fields["booktitle"] = venue
    elif venue:
        fields["journal"] = venue
    if journal.get("volume"):
        fields["volume"] = str(journal["volume"])
    if journal.get("pages"):
        fields["pages"] = str(journal["pages"])
    return fields, entry_type
