from __future__ import annotations

from calendar import month_name
import re
from typing import Any
from urllib.parse import quote, urlparse

import httpx
from bs4 import BeautifulSoup

from fixbib.inventory import extract_entry_urls
from fixbib.model import BibCandidate, BibInputEntry
from fixbib.normalizer import find_entry_doi, normalize_doi
from fixbib.resolvers.base import Resolver
from fixbib.resolvers.common import (
    BROWSER_USER_AGENT,
    DEFAULT_USER_AGENT,
    candidate_from_fields,
    first_nonempty,
)
from fixbib.resolvers.page import resolve_publication_url


class AcmResolver(Resolver):
    """Resolve ACM records through ACM's native citation export service.

    The public article page is often protected by an anti-bot challenge, while
    ACM's citation export endpoint is the same endpoint used by reference
    managers. The export service is attempted first and the HTML page remains a
    best-effort fallback for page metadata and diagnostics.
    """

    name = "acm-native-export"

    def __init__(
        self,
        base_url: str = "https://dl.acm.org/doi",
        export_url: str | None = "https://dl.acm.org/action/exportCiteProcCitation",
        timeout: float = 15.0,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.export_url = export_url or ""
        self.timeout = timeout
        self.transport = transport

    def can_resolve(self, entry: BibInputEntry) -> bool:
        if not self.export_url:
            return False
        doi = find_entry_doi(entry.fields) or ""
        if doi.startswith("10.1145/"):
            return True
        return any("dl.acm.org" in urlparse(url).netloc.lower() for url in extract_entry_urls(entry.fields))

    def resolve(self, entry: BibInputEntry) -> list[BibCandidate]:
        doi = find_entry_doi(entry.fields)
        urls = [
            url
            for url in extract_entry_urls(entry.fields)
            if "dl.acm.org" in urlparse(url).netloc.lower()
        ]
        if doi and doi.startswith("10.1145/"):
            page_url = f"{self.base_url}/{quote(doi, safe='/:;()')}"
            if page_url not in urls:
                urls.insert(0, page_url)

        if doi and doi.startswith("10.1145/"):
            return self._resolve_native_export(entry, doi)
        return []

    def _resolve_native_export(self, entry: BibInputEntry, doi: str) -> list[BibCandidate]:
        page_url = f"{self.base_url}/{quote(doi, safe='/:;()')}"
        request_evidence: list[str] = []
        landing_html = ""
        try:
            with httpx.Client(
                follow_redirects=True,
                timeout=self.timeout,
                transport=self.transport,
                headers={"User-Agent": BROWSER_USER_AGENT},
            ) as client:
                # ACM's citation endpoint is designed for calls made from an
                # article page. Establish a same-origin session first. The page
                # may still return a challenge, but cookies set on that response
                # can be required by the subsequent export request.
                try:
                    landing = client.get(
                        page_url,
                        headers={
                            "Accept": "text/html,application/xhtml+xml,*/*;q=0.1",
                            "Referer": "https://dl.acm.org/",
                        },
                    )
                    request_evidence.extend(
                        [
                            f"acm_landing_status={landing.status_code}",
                            f"acm_landing_final_url={landing.url}",
                        ]
                    )
                    if landing.status_code < 400:
                        landing_html = landing.text
                except httpx.HTTPError as exc:
                    request_evidence.append(
                        f"acm_landing_failed={type(exc).__name__}: {exc}"
                    )

                response = client.post(
                    self.export_url,
                    data={
                        "targetFile": "custom-bibtex",
                        "format": "bibTex",
                        "dois": doi,
                    },
                    headers={
                        "Accept": "application/json,text/plain,*/*;q=0.1",
                        "Referer": page_url,
                        "Origin": "https://dl.acm.org",
                        "X-Requested-With": "XMLHttpRequest",
                        "Sec-Fetch-Site": "same-origin",
                        "Sec-Fetch-Mode": "cors",
                        "Sec-Fetch-Dest": "empty",
                    },
                )
                request_evidence.extend(
                    [
                        f"acm_export_status={response.status_code}",
                        f"acm_export_content_type={response.headers.get('content-type', '')}",
                    ]
                )
                response.raise_for_status()
                payload = response.json()
        except (httpx.HTTPError, ValueError, TypeError) as exc:
            status = ""
            body_preview = ""
            if isinstance(exc, httpx.HTTPStatusError):
                status = str(exc.response.status_code)
                body_preview = _safe_response_preview(exc.response.text)
            return [
                BibCandidate(
                    source=self.name,
                    source_url=self.export_url,
                    bibtex=None,
                    fields={"doi": doi},
                    confidence="not_found",
                    score=0.0,
                    evidence=[item for item in request_evidence + [
                        f"acm_native_export_failed={type(exc).__name__}: {exc}",
                        f"acm_native_export_http_status={status}" if status else "",
                        f"acm_native_export_body_preview={body_preview}" if body_preview else "",
                        "publisher_native_export_unavailable",
                    ] if item],
                    source_priority=115,
                    canonical_id=doi,
                    source_kind="probe",
                    source_family="acm",
                )
            ]

        item = _find_csl_item(payload, doi)
        if not item:
            return [
                BibCandidate(
                    source=self.name,
                    source_url=str(response.url),
                    bibtex=None,
                    fields={"doi": doi},
                    confidence="not_found",
                    score=0.0,
                    evidence=["acm_export_response_missing_requested_doi"],
                    source_priority=115,
                    canonical_id=doi,
                    source_kind="probe",
                    source_family="acm",
                )
            ]

        fields, entry_type = _csl_to_fields(item, doi)
        if landing_html and _enrich_fields_from_acm_page(fields, landing_html):
            request_evidence.append("acm_page_rich_metadata")
        candidate = candidate_from_fields(
            entry=entry,
            source=self.name,
            source_url=str(response.url),
            fields=fields,
            source_priority=115,
            entry_type=entry_type,
            canonical_id=doi,
            extra_evidence=[
                *request_evidence,
                "acm_export_citeproc_endpoint",
                "publisher_native_export",
                "resolved_by_exact_doi",
            ],
            source_kind="publisher_native_export",
            source_family="acm",
        )
        return [candidate]


def _safe_response_preview(text: str, limit: int = 240) -> str:
    cleaned = " ".join((text or "").split())
    return cleaned[:limit]


def _enrich_fields_from_acm_page(fields: dict[str, str], html: str) -> bool:
    """Add fields that ACM exposes on the article page, not in CSL JSON.

    ACM's own reference-manager integration obtains the CSL record from the
    export endpoint, but reads the abstract, page count, tags, and expanded
    journal title from the HTML document. Keep these additions conservative and
    add-only so the export response remains the primary metadata record.
    """

    soup = BeautifulSoup(html, "html.parser")
    changed = False

    if not fields.get("abstract"):
        paragraphs = soup.select("div.article__abstract p, div.abstractSection p")
        abstract = "\n\n".join(
            node.get_text(" ", strip=True) for node in paragraphs if node.get_text(" ", strip=True)
        ).strip()
        if abstract and abstract.lower() != "no abstract available.":
            fields["abstract"] = abstract
            changed = True

    if not fields.get("keywords"):
        tags = [
            node.get_text(" ", strip=True)
            for node in soup.select("div.tags-widget a")
            if node.get_text(" ", strip=True)
        ]
        if tags:
            fields["keywords"] = ", ".join(tags)
            changed = True

    if not fields.get("numpages"):
        node = soup.select_one("div.pages-info span")
        if node:
            text = node.get_text(" ", strip=True)
            match = re.search(r"\b(\d+)\b", text)
            if match:
                fields["numpages"] = match.group(1)
                changed = True

    journal_meta = soup.select_one('meta[name="citation_journal_title"]')
    if journal_meta and fields.get("journal"):
        expanded = str(journal_meta.get("content") or "").strip()
        if expanded and len(expanded) > len(fields["journal"]):
            fields["journal"] = expanded
            changed = True

    return changed



class AcmPageFallbackResolver(Resolver):
    """Best-effort ACM HTML/BibTeX-link fallback for non-exportable pages."""

    name = "acm-dl-fallback"

    def __init__(self, base_url: str = "https://dl.acm.org/doi", timeout: float = 15.0) -> None:
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout

    def can_resolve(self, entry: BibInputEntry) -> bool:
        doi = find_entry_doi(entry.fields) or ""
        if doi.startswith("10.1145/"):
            return True
        return any("dl.acm.org" in urlparse(url).netloc.lower() for url in extract_entry_urls(entry.fields))

    def resolve(self, entry: BibInputEntry) -> list[BibCandidate]:
        urls = [
            url
            for url in extract_entry_urls(entry.fields)
            if "dl.acm.org" in urlparse(url).netloc.lower()
        ]
        doi = find_entry_doi(entry.fields)
        if doi and doi.startswith("10.1145/"):
            generated = f"{self.base_url}/{quote(doi, safe='/:;()')}"
            if generated not in urls:
                urls.insert(0, generated)
        candidates: list[BibCandidate] = []
        for url in urls[:2]:
            candidates.extend(
                resolve_publication_url(
                    entry,
                    url,
                    source=self.name,
                    source_priority=42,
                    timeout=self.timeout,
                    extra_evidence=["acm_dl_best_effort_fallback"],
                    emit_probe=True,
                )
            )
        return candidates


def _find_csl_item(payload: Any, doi: str) -> dict[str, Any] | None:
    target = normalize_doi(doi)

    def visit(value: Any) -> dict[str, Any] | None:
        if isinstance(value, dict):
            for key, item in value.items():
                if normalize_doi(str(key)) == target and isinstance(item, dict):
                    return item
            own_doi = normalize_doi(str(value.get("DOI") or value.get("doi") or ""))
            if own_doi == target and any(key in value for key in ("title", "author", "container-title")):
                return value
            for child in value.values():
                found = visit(child)
                if found:
                    return found
        elif isinstance(value, list):
            for child in value:
                found = visit(child)
                if found:
                    return found
        return None

    return visit(payload)


def _csl_to_fields(item: dict[str, Any], doi: str) -> tuple[dict[str, str], str]:
    fields: dict[str, str] = {}

    def put(name: str, value: Any) -> None:
        text = first_nonempty(value).strip()
        if text:
            fields[name] = text

    put("title", item.get("title"))
    item_type = str(item.get("type") or "").lower().replace("_", "-")
    container = first_nonempty(item.get("container-title") or item.get("containerTitle"))
    if item_type in {"paper-conference", "conference-paper", "proceedings-article"}:
        entry_type = "inproceedings"
        put("booktitle", container)
    elif item_type in {"chapter", "book-chapter", "entry", "book-section"}:
        entry_type = "incollection"
        put("booktitle", container)
    elif item_type in {"book", "monograph", "edited-book"}:
        entry_type = "book"
    elif item_type in {"thesis"}:
        entry_type = "phdthesis"
    else:
        entry_type = "article"
        put("journal", container)

    authors = _csl_names(item.get("author"))
    editors = _csl_names(item.get("editor"))
    if authors:
        fields["author"] = authors
    if editors:
        fields["editor"] = editors

    mapping = {
        "publisher": "publisher",
        "publisher-place": "address",
        "page": "pages",
        "volume": "volume",
        "issue": "number",
        "number": "number",
        "ISBN": "isbn",
        "ISSN": "issn",
        "URL": "url",
        "abstract": "abstract",
        "event-place": "location",
        "collection-title": "series",
        "number-of-pages": "numpages",
        "page-count": "numpages",
        "article-number": "articleno",
        "keyword": "keywords",
    }
    for source_key, target_key in mapping.items():
        value = item.get(source_key)
        if target_key == "keywords" and isinstance(value, list):
            value = ", ".join(str(x).strip() for x in value if str(x).strip())
        put(target_key, value)

    fields["doi"] = normalize_doi(str(item.get("DOI") or item.get("doi") or doi)) or doi
    if not fields.get("url"):
        fields["url"] = f"https://doi.org/{fields['doi']}"

    year, month, day = _csl_date(item)
    if year:
        fields["year"] = year
    if month:
        fields["month"] = month
    if day:
        fields["day"] = day
    return fields, entry_type


def _csl_names(value: Any) -> str:
    if not isinstance(value, list):
        return ""
    names: list[str] = []
    for person in value:
        if not isinstance(person, dict):
            text = str(person).strip()
            if text:
                names.append(text)
            continue
        literal = str(person.get("literal") or "").strip()
        family = str(person.get("family") or "").strip()
        given = str(person.get("given") or "").strip()
        if literal:
            names.append(literal)
        elif family and given:
            names.append(f"{family}, {given}")
        elif family or given:
            names.append(family or given)
    return " and ".join(names)


def _csl_date(item: dict[str, Any]) -> tuple[str, str, str]:
    for key in ("issued", "published", "event-date", "created"):
        value = item.get(key)
        if not isinstance(value, dict):
            continue
        parts = value.get("date-parts")
        if not parts or not isinstance(parts, list) or not parts[0]:
            continue
        first = parts[0]
        year = str(first[0]) if len(first) >= 1 and first[0] else ""
        month = ""
        day = ""
        if len(first) >= 2 and first[1]:
            try:
                month = month_name[int(first[1])]
            except (ValueError, IndexError, TypeError):
                month = str(first[1])
        if len(first) >= 3 and first[2]:
            day = str(first[2])
        return year, month, day
    return "", "", ""
