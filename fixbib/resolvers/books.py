from __future__ import annotations

from typing import Any

import httpx

from fixbib.inventory import find_entry_isbn
from fixbib.model import BibCandidate, BibInputEntry
from fixbib.resolvers.base import Resolver
from fixbib.resolvers.common import DEFAULT_USER_AGENT, candidate_from_fields

BOOK_TYPES = {"book", "inbook", "incollection", "proceedings"}


class OpenLibraryBookResolver(Resolver):
    name = "openlibrary-book"

    def __init__(
        self,
        api_base: str = "https://openlibrary.org",
        timeout: float = 15.0,
        max_results: int = 5,
    ) -> None:
        self.api_base = api_base.rstrip("/")
        self.timeout = timeout
        self.max_results = max_results

    def can_resolve(self, entry: BibInputEntry) -> bool:
        return entry.entry_type in BOOK_TYPES and bool(find_entry_isbn(entry.fields) or entry.fields.get("title"))

    def resolve(self, entry: BibInputEntry) -> list[BibCandidate]:
        isbn = find_entry_isbn(entry.fields)
        if isbn:
            exact = self._resolve_isbn(entry, isbn)
            if exact:
                return exact
        return self._search_title(entry)

    def _resolve_isbn(self, entry: BibInputEntry, isbn: str) -> list[BibCandidate]:
        url = f"{self.api_base}/api/books"
        try:
            with httpx.Client(follow_redirects=True, timeout=self.timeout) as client:
                response = client.get(
                    url,
                    params={"bibkeys": f"ISBN:{isbn}", "format": "json", "jscmd": "data"},
                    headers={"User-Agent": DEFAULT_USER_AGENT, "Accept": "application/json"},
                )
                response.raise_for_status()
                payload = response.json()
        except (httpx.HTTPError, ValueError) as exc:
            return [_failure(self.name, url, exc, 82)]
        record = payload.get(f"ISBN:{isbn}") if isinstance(payload, dict) else None
        if not isinstance(record, dict):
            return []
        fields = _openlibrary_data_to_fields(record, isbn)
        return [
            candidate_from_fields(
                entry=entry,
                source="openlibrary-isbn",
                source_url=str(record.get("url") or response.url),
                fields=fields,
                source_priority=82,
                entry_type="book",
                canonical_id=f"ISBN:{isbn}",
                extra_evidence=["openlibrary_exact_isbn"],
                source_kind="book_catalog",
                source_family="openlibrary",
            )
        ]

    def _search_title(self, entry: BibInputEntry) -> list[BibCandidate]:
        title = entry.fields.get("title", "").strip()
        if not title:
            return []
        url = f"{self.api_base}/search.json"
        params: dict[str, str | int] = {"title": title, "limit": self.max_results}
        author = entry.fields.get("author", "").split(" and ", 1)[0].strip()
        if author:
            params["author"] = author
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
            return [_failure(self.name, url, exc, 58)]

        candidates: list[BibCandidate] = []
        for doc in (payload.get("docs") or [])[: self.max_results]:
            if not isinstance(doc, dict):
                continue
            fields = _openlibrary_search_to_fields(doc)
            candidates.append(
                candidate_from_fields(
                    entry=entry,
                    source="openlibrary-title-search",
                    source_url=f"{self.api_base}{doc.get('key', '')}" if doc.get("key") else str(response.url),
                    fields=fields,
                    source_priority=58,
                    entry_type="book",
                    canonical_id=str(doc.get("key") or "") or None,
                    extra_evidence=["openlibrary_book_title_search"],
                    source_kind="book_catalog",
                    source_family="openlibrary",
                )
            )
        return candidates


class GoogleBooksResolver(Resolver):
    name = "google-books"

    def __init__(
        self,
        api_key: str,
        api_base: str = "https://www.googleapis.com/books/v1/volumes",
        timeout: float = 15.0,
        max_results: int = 5,
    ) -> None:
        self.api_key = api_key
        self.api_base = api_base
        self.timeout = timeout
        self.max_results = max_results

    def can_resolve(self, entry: BibInputEntry) -> bool:
        return bool(self.api_key and entry.entry_type in BOOK_TYPES and entry.fields.get("title"))

    def resolve(self, entry: BibInputEntry) -> list[BibCandidate]:
        isbn = find_entry_isbn(entry.fields)
        if isbn:
            query = f"isbn:{isbn}"
        else:
            title = entry.fields.get("title", "").strip()
            author = entry.fields.get("author", "").split(" and ", 1)[0].strip()
            query = f'intitle:"{title}"'
            if author:
                query += f' inauthor:"{author}"'
        try:
            with httpx.Client(follow_redirects=True, timeout=self.timeout) as client:
                response = client.get(
                    self.api_base,
                    params={
                        "q": query,
                        "printType": "books",
                        "maxResults": self.max_results,
                        "key": self.api_key,
                    },
                    headers={"User-Agent": DEFAULT_USER_AGENT, "Accept": "application/json"},
                )
                response.raise_for_status()
                payload = response.json()
        except (httpx.HTTPError, ValueError) as exc:
            return [_failure(self.name, self.api_base, exc, 62)]

        candidates: list[BibCandidate] = []
        for item in (payload.get("items") or [])[: self.max_results]:
            if not isinstance(item, dict):
                continue
            info = item.get("volumeInfo") or {}
            fields = _google_books_to_fields(info)
            candidates.append(
                candidate_from_fields(
                    entry=entry,
                    source="google-books-search",
                    source_url=str(info.get("infoLink") or item.get("selfLink") or response.url),
                    fields=fields,
                    source_priority=62,
                    entry_type="book",
                    canonical_id=str(item.get("id") or "") or None,
                    extra_evidence=["google_books_volume_search"],
                    source_kind="book_catalog",
                    source_family="google-books",
                )
            )
        return candidates


def _openlibrary_data_to_fields(record: dict[str, Any], isbn: str) -> dict[str, str]:
    fields: dict[str, str] = {"isbn": isbn}
    if record.get("title"):
        fields["title"] = str(record["title"])
    authors = [str(x.get("name") or "") for x in record.get("authors", []) if x.get("name")]
    if authors:
        fields["author"] = " and ".join(authors)
    publishers = [str(x.get("name") or "") for x in record.get("publishers", []) if x.get("name")]
    if publishers:
        fields["publisher"] = publishers[0]
    if record.get("publish_date"):
        fields["year"] = str(record["publish_date"])
    if record.get("url"):
        fields["url"] = str(record["url"])
    if record.get("number_of_pages"):
        fields["pages"] = str(record["number_of_pages"])
    return fields


def _openlibrary_search_to_fields(doc: dict[str, Any]) -> dict[str, str]:
    fields: dict[str, str] = {}
    if doc.get("title"):
        fields["title"] = str(doc["title"])
    authors = [str(x) for x in (doc.get("author_name") or []) if x]
    if authors:
        fields["author"] = " and ".join(authors)
    publishers = [str(x) for x in (doc.get("publisher") or []) if x]
    if publishers:
        fields["publisher"] = publishers[0]
    if doc.get("first_publish_year"):
        fields["year"] = str(doc["first_publish_year"])
    isbns = [str(x) for x in (doc.get("isbn") or []) if x]
    if isbns:
        fields["isbn"] = next((x for x in isbns if len(x.replace("-", "")) == 13), isbns[0])
    return fields


def _google_books_to_fields(info: dict[str, Any]) -> dict[str, str]:
    fields: dict[str, str] = {}
    if info.get("title"):
        title = str(info["title"])
        if info.get("subtitle"):
            title += f": {info['subtitle']}"
        fields["title"] = title
    authors = [str(x) for x in (info.get("authors") or []) if x]
    if authors:
        fields["author"] = " and ".join(authors)
    for source_key, target_key in (
        ("publisher", "publisher"),
        ("publishedDate", "year"),
        ("infoLink", "url"),
        ("pageCount", "pages"),
    ):
        if info.get(source_key):
            fields[target_key] = str(info[source_key])
    identifiers = info.get("industryIdentifiers") or []
    isbn13 = next((str(x.get("identifier")) for x in identifiers if x.get("type") == "ISBN_13"), "")
    isbn10 = next((str(x.get("identifier")) for x in identifiers if x.get("type") == "ISBN_10"), "")
    if isbn13 or isbn10:
        fields["isbn"] = isbn13 or isbn10
    return fields


def _failure(source: str, url: str, exc: Exception, priority: int) -> BibCandidate:
    return BibCandidate(
        source=source,
        source_url=url,
        bibtex=None,
        fields={},
        confidence="not_found",
        score=0.0,
        evidence=[f"request_failed={type(exc).__name__}: {exc}"],
        source_priority=priority,
        source_kind="book_catalog",
        source_family=source,
    )
