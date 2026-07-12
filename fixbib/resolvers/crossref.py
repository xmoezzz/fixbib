from __future__ import annotations

from typing import Any
from urllib.parse import quote

import httpx

from fixbib.model import BibCandidate, BibInputEntry
from fixbib.normalizer import find_entry_doi
from fixbib.resolvers.base import Resolver
from fixbib.resolvers.common import DEFAULT_USER_AGENT, candidate_from_fields, first_nonempty


class CrossrefResolver(Resolver):
    name = "crossref"

    def __init__(
        self,
        api_base: str = "https://api.crossref.org",
        timeout: float = 15.0,
        rows: int = 5,
        mode: str = "both",
    ) -> None:
        self.api_base = api_base.rstrip("/")
        self.timeout = timeout
        self.rows = rows
        if mode not in {"both", "exact", "search"}:
            raise ValueError("mode must be both, exact, or search")
        self.mode = mode

    def can_resolve(self, entry: BibInputEntry) -> bool:
        doi = find_entry_doi(entry.fields)
        if self.mode == "exact":
            return doi is not None
        if self.mode == "search":
            return bool(entry.fields.get("title"))
        return bool(doi or entry.fields.get("title"))

    def resolve(self, entry: BibInputEntry) -> list[BibCandidate]:
        doi = None if self.mode == "search" else find_entry_doi(entry.fields)
        params: dict[str, str | int] = {}

        if doi:
            url = f"{self.api_base}/works/{quote(doi, safe='/:;()')}"
        else:
            url = f"{self.api_base}/works"
            query = entry.fields.get("title", "")
            author = entry.fields.get("author", "")
            year = entry.fields.get("year", "")
            params.update(
                {
                    "query.bibliographic": " ".join(x for x in (query, author, year) if x),
                    "rows": self.rows,
                    "select": "DOI,title,author,published,issued,container-title,type,page,volume,issue,publisher,URL,ISBN,ISSN,article-number",
                }
            )

        try:
            with httpx.Client(follow_redirects=True, timeout=self.timeout) as client:
                response = client.get(
                    url,
                    params=params,
                    headers={"User-Agent": DEFAULT_USER_AGENT, "Accept": "application/json"},
                )
                response.raise_for_status()
                payload = response.json()
        except (httpx.HTTPError, ValueError) as exc:
            return [
                BibCandidate(
                    source=self.name,
                    source_url=url,
                    bibtex=None,
                    fields={"doi": doi} if doi else {},
                    confidence="not_found",
                    score=0.0,
                    evidence=[f"request_failed={type(exc).__name__}: {exc}"],
                    source_priority=95 if doi else 75,
                    canonical_id=doi,
                    source_kind="registry_metadata",
                    source_family="crossref",
                )
            ]

        message = payload.get("message", {})
        records = [message] if doi else message.get("items", [])
        candidates: list[BibCandidate] = []
        for record in records[: self.rows]:
            fields, entry_type = _crossref_to_fields(record)
            record_doi = fields.get("doi") or doi
            candidates.append(
                candidate_from_fields(
                    entry=entry,
                    source="crossref-doi" if doi else "crossref-search",
                    source_url=fields.get("url") or str(response.url),
                    fields=fields,
                    source_priority=95 if doi else 75,
                    entry_type=entry_type,
                    canonical_id=record_doi,
                    extra_evidence=[
                        "exact_doi_lookup" if doi else "bibliographic_search",
                        "structured_registry_metadata",
                    ],
                    source_kind="registry_metadata",
                    source_family="crossref",
                )
            )
        return candidates


def _crossref_to_fields(record: dict[str, Any]) -> tuple[dict[str, str], str]:
    fields: dict[str, str] = {}
    title = first_nonempty(record.get("title"))
    if title:
        fields["title"] = title

    authors: list[str] = []
    for author in record.get("author", []) or []:
        family = str(author.get("family") or "").strip()
        given = str(author.get("given") or "").strip()
        literal = str(author.get("name") or "").strip()
        if literal:
            authors.append(literal)
        elif family and given:
            authors.append(f"{family}, {given}")
        elif family or given:
            authors.append(family or given)
    if authors:
        fields["author"] = " and ".join(authors)

    container = first_nonempty(record.get("container-title"))
    record_type = str(record.get("type") or "")
    entry_type = "article"
    if record_type == "proceedings-article":
        entry_type = "inproceedings"
        if container:
            fields["booktitle"] = container
    elif record_type in {"book-chapter", "reference-entry"}:
        entry_type = "incollection"
        if container:
            fields["booktitle"] = container
    elif record_type in {"book", "monograph", "edited-book", "reference-book"}:
        entry_type = "book"
        if container and not record.get("title"):
            fields["title"] = container
    elif record_type in {"proceedings", "book-series"}:
        entry_type = "proceedings"
        if container and not fields.get("title"):
            fields["title"] = container
    elif container:
        fields["journal"] = container

    year = _crossref_year(record)
    if year:
        fields["year"] = year

    mapping = {
        "page": "pages",
        "volume": "volume",
        "issue": "number",
        "publisher": "publisher",
        "DOI": "doi",
        "URL": "url",
        "article-number": "articleno",
    }
    for source_key, target_key in mapping.items():
        value = record.get(source_key)
        if value:
            fields[target_key] = first_nonempty(value)

    isbn = first_nonempty(record.get("ISBN"))
    issn = first_nonempty(record.get("ISSN"))
    if isbn:
        fields["isbn"] = isbn
    if issn:
        fields["issn"] = issn
    return fields, entry_type


def _crossref_year(record: dict[str, Any]) -> str:
    for key in ("published-print", "published-online", "published", "issued", "created"):
        value = record.get(key) or {}
        parts = value.get("date-parts") if isinstance(value, dict) else None
        if parts and parts[0]:
            return str(parts[0][0])
    return ""
