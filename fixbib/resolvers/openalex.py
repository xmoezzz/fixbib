from __future__ import annotations

from typing import Any

import httpx

from fixbib.model import BibCandidate, BibInputEntry
from fixbib.normalizer import normalize_doi
from fixbib.resolvers.base import Resolver
from fixbib.resolvers.common import DEFAULT_USER_AGENT, candidate_from_fields


class OpenAlexResolver(Resolver):
    name = "openalex-search"

    def __init__(
        self,
        api_key: str,
        api_base: str = "https://api.openalex.org/works",
        timeout: float = 15.0,
        max_results: int = 5,
    ) -> None:
        self.api_key = api_key
        self.api_base = api_base
        self.timeout = timeout
        self.max_results = max_results

    def can_resolve(self, entry: BibInputEntry) -> bool:
        return bool(self.api_key and entry.fields.get("title"))

    def resolve(self, entry: BibInputEntry) -> list[BibCandidate]:
        if not self.can_resolve(entry):
            return []
        try:
            with httpx.Client(follow_redirects=True, timeout=self.timeout) as client:
                response = client.get(
                    self.api_base,
                    params={
                        "api_key": self.api_key,
                        "search": entry.fields.get("title", ""),
                        "per-page": self.max_results,
                        "select": "id,doi,title,publication_year,type,authorships,primary_location,biblio",
                    },
                    headers={"User-Agent": DEFAULT_USER_AGENT, "Accept": "application/json"},
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
                    source_priority=65,
                    source_kind="bibliographic_index",
                    source_family="openalex",
                )
            ]

        candidates: list[BibCandidate] = []
        for work in payload.get("results", [])[: self.max_results]:
            fields, entry_type = _openalex_to_fields(work)
            candidates.append(
                candidate_from_fields(
                    entry=entry,
                    source=self.name,
                    source_url=fields.get("url") or work.get("id"),
                    fields=fields,
                    source_priority=65,
                    entry_type=entry_type,
                    canonical_id=work.get("id"),
                    extra_evidence=["openalex_title_search"],
                    source_kind="bibliographic_index",
                    source_family="openalex",
                )
            )
        return candidates


def _openalex_to_fields(work: dict[str, Any]) -> tuple[dict[str, str], str]:
    fields: dict[str, str] = {}
    if work.get("title"):
        fields["title"] = str(work["title"])
    if work.get("publication_year"):
        fields["year"] = str(work["publication_year"])
    if work.get("doi"):
        fields["doi"] = normalize_doi(str(work["doi"]))

    authors: list[str] = []
    for authorship in work.get("authorships", []) or []:
        name = str((authorship.get("author") or {}).get("display_name") or "")
        if name:
            authors.append(name)
    if authors:
        fields["author"] = " and ".join(authors)

    location = work.get("primary_location") or {}
    source = location.get("source") or {}
    venue = str(source.get("display_name") or "")
    work_type = str(work.get("type") or "")
    entry_type = "article"
    if work_type in {"proceedings-article", "book-chapter"}:
        entry_type = "inproceedings" if work_type == "proceedings-article" else "incollection"
        if venue:
            fields["booktitle"] = venue
    elif venue:
        fields["journal"] = venue
    if location.get("landing_page_url"):
        fields["url"] = str(location["landing_page_url"])

    biblio = work.get("biblio") or {}
    for source_key, target_key in (
        ("volume", "volume"),
        ("issue", "number"),
        ("first_page", "first_page"),
        ("last_page", "last_page"),
    ):
        if biblio.get(source_key):
            fields[target_key] = str(biblio[source_key])
    if fields.get("first_page") and fields.get("last_page"):
        fields["pages"] = f"{fields.pop('first_page')}--{fields.pop('last_page')}"
    elif fields.get("first_page"):
        fields["pages"] = fields.pop("first_page")
    fields.pop("last_page", None)
    return fields, entry_type
