from __future__ import annotations

import json
import re
from urllib.parse import urljoin, urlparse

import httpx
from bs4 import BeautifulSoup

from fixbib.inventory import extract_entry_urls
from fixbib.model import BibCandidate, BibInputEntry
from fixbib.provenance import infer_source_family, page_source_kind
from fixbib.resolvers.base import Resolver
from fixbib.resolvers.common import (
    DEFAULT_USER_AGENT,
    candidates_from_bibtex,
    candidate_from_fields,
    infer_entry_type,
)
from fixbib.util_bibtex import decode_bibtex_data_uri

BIBTEX_RE = re.compile(
    r"@(article|inproceedings|proceedings|misc|book|incollection|inbook|phdthesis|mastersthesis|techreport|unpublished|manual)\s*[({]",
    re.I,
)
BLOCKED_PATTERNS = (
    "just a moment",
    "access denied",
    "unable to load page",
    "verify you are human",
    "browser verification",
    "checking your browser",
    "captcha",
)
MAX_EXPORT_LINKS = 12


class GenericCitationMetaResolver(Resolver):
    name = "generic-page-metadata"

    def __init__(self, timeout: float = 15.0, max_urls: int = 3) -> None:
        self.timeout = timeout
        self.max_urls = max_urls

    def can_resolve(self, entry: BibInputEntry) -> bool:
        return bool(extract_entry_urls(entry.fields))

    def resolve(self, entry: BibInputEntry) -> list[BibCandidate]:
        candidates: list[BibCandidate] = []
        for url in extract_entry_urls(entry.fields)[: self.max_urls]:
            candidates.extend(
                resolve_publication_url(
                    entry,
                    url,
                    source=self.name,
                    source_priority=35,
                    timeout=self.timeout,
                    extra_evidence=["entry_supplied_url"],
                    emit_probe=True,
                )
            )
        return candidates


def resolve_publication_url(
    entry: BibInputEntry,
    url: str,
    *,
    source: str,
    source_priority: int,
    timeout: float,
    extra_evidence: list[str] | None = None,
    emit_probe: bool = False,
) -> list[BibCandidate]:
    evidence = list(extra_evidence or [])
    page_candidates: list[BibCandidate] = []
    try:
        with httpx.Client(follow_redirects=True, timeout=timeout) as client:
            response = client.get(
                url,
                headers={
                    "User-Agent": DEFAULT_USER_AGENT,
                    "Accept": "text/html,application/xhtml+xml,application/x-bibtex,text/plain;q=0.8,*/*;q=0.2",
                },
            )
            final_url = str(response.url)
            page_family = infer_source_family(source, final_url)
            content_type = str(response.headers.get("content-type") or "").lower()
            body = response.text
            evidence.extend(
                [
                    f"http_status={response.status_code}",
                    f"final_url={final_url}",
                    f"content_type={content_type.split(';', 1)[0]}",
                ]
            )

            if BIBTEX_RE.search(body[:200000]) and (
                "bibtex" in content_type or body.lstrip().startswith("@")
            ):
                return candidates_from_bibtex(
                    entry=entry,
                    source=f"{source}-direct-bibtex",
                    source_url=final_url,
                    text=body,
                    source_priority=source_priority + 10,
                    extra_evidence=evidence + ["url_returned_bibtex", "publisher_native_bibtex"],
                    source_kind=page_source_kind(final_url, has_native_bibtex=True),
                    source_family=page_family,
                )

            lowered = body[:20000].lower()
            blocked = response.status_code in {401, 403, 418, 429, 503} or any(
                pattern in lowered for pattern in BLOCKED_PATTERNS
            )
            if blocked:
                return [
                    BibCandidate(
                        source=source,
                        source_url=final_url,
                        bibtex=None,
                        fields={},
                        confidence="not_found",
                        score=0.0,
                        evidence=evidence + ["blocked_or_challenge_page"],
                        source_priority=source_priority,
                        source_kind="probe",
                        source_family=page_family,
                    )
                ]
            response.raise_for_status()
            soup = BeautifulSoup(body, "html.parser")

            # Citation links are untrusted inputs. They may be HTTP URLs, data
            # URIs, malformed data URIs, JavaScript pseudo-URLs, or unrelated
            # downloads. Never pass an unsupported scheme into the HTTP client.
            examined = 0
            for link in soup.find_all("a", href=True):
                href = str(link.get("href") or "").strip()
                text = " ".join(link.get_text(" ", strip=True).split()).lower()
                if not _looks_like_bibtex_link(href, text):
                    continue
                examined += 1
                if examined > MAX_EXPORT_LINKS:
                    break

                decoded, decode_error = decode_bibtex_data_uri(href)
                if decoded is not None:
                    page_candidates.extend(
                        candidates_from_bibtex(
                            entry=entry,
                            source=f"{source}-bibtex-data-uri",
                            source_url=_redacted_data_uri(href),
                            text=decoded,
                            source_priority=source_priority + 8,
                            extra_evidence=evidence + ["page_exposed_bibtex_data_uri", "publisher_native_bibtex"],
                            source_kind=page_source_kind(final_url, has_native_bibtex=True),
                            source_family=page_family,
                        )
                    )
                    if _has_strong_bibtex(page_candidates):
                        return page_candidates
                    continue

                parsed_href = urlparse(href)
                if parsed_href.scheme and parsed_href.scheme.lower() not in {"http", "https"}:
                    page_candidates.append(
                        BibCandidate(
                            source=f"{source}-bibtex-link",
                            source_url=_short_url(href),
                            bibtex=None,
                            fields={},
                            confidence="not_found",
                            score=0.0,
                            evidence=evidence
                            + [
                                f"unsupported_export_scheme={parsed_href.scheme.lower()}",
                                f"data_uri_decode={decode_error}",
                            ],
                            source_priority=source_priority,
                            source_kind="probe",
                            source_family=page_family,
                        )
                    )
                    continue

                export_url = urljoin(final_url, href)
                if urlparse(export_url).scheme.lower() not in {"http", "https"}:
                    continue
                try:
                    export = client.get(
                        export_url,
                        headers={
                            "User-Agent": DEFAULT_USER_AGENT,
                            "Accept": "application/x-bibtex,text/plain,*/*;q=0.1",
                        },
                    )
                except (httpx.HTTPError, ValueError) as exc:
                    page_candidates.append(
                        BibCandidate(
                            source=f"{source}-bibtex-link",
                            source_url=_short_url(export_url),
                            bibtex=None,
                            fields={},
                            confidence="not_found",
                            score=0.0,
                            evidence=evidence + [f"export_request_failed={type(exc).__name__}"],
                            source_priority=source_priority,
                            source_kind="probe",
                            source_family=page_family,
                        )
                    )
                    continue
                if export.is_success:
                    bundle = candidates_from_bibtex(
                        entry=entry,
                        source=f"{source}-bibtex-link",
                        source_url=str(export.url),
                        text=export.text,
                        source_priority=source_priority + 8,
                        extra_evidence=evidence + ["page_exposed_bibtex_export_link", "publisher_native_bibtex"],
                        source_kind=page_source_kind(final_url, has_native_bibtex=True),
                        source_family=page_family,
                    )
                    page_candidates.extend(bundle)
                    if _has_strong_bibtex(bundle):
                        return page_candidates

            inline = _find_inline_bibtex(soup)
            if inline:
                bundle = candidates_from_bibtex(
                    entry=entry,
                    source=f"{source}-inline-bibtex",
                    source_url=final_url,
                    text=inline,
                    source_priority=source_priority + 6,
                    extra_evidence=evidence + ["page_contained_inline_bibtex", "publisher_native_bibtex"],
                    source_kind=page_source_kind(final_url, has_native_bibtex=True),
                    source_family=page_family,
                )
                page_candidates.extend(bundle)
                if _has_strong_bibtex(bundle):
                    return page_candidates

            fields = extract_citation_meta(soup)
            metadata_source = "citation_meta"
            if not fields:
                fields = extract_json_ld(soup)
                metadata_source = "json_ld"
            if fields:
                page_candidates.append(
                    candidate_from_fields(
                        entry=entry,
                        source=source,
                        source_url=final_url,
                        fields=fields,
                        source_priority=source_priority,
                        entry_type=infer_entry_type(fields, entry.entry_type),
                        extra_evidence=evidence
                        + [metadata_source, f"domain={urlparse(final_url).netloc}"],
                        source_kind=page_source_kind(final_url),
                        source_family=page_family,
                    )
                )
                return page_candidates

            if page_candidates:
                return page_candidates
            if emit_probe:
                return [
                    BibCandidate(
                        source=source,
                        source_url=final_url,
                        bibtex=None,
                        fields={},
                        confidence="not_found",
                        score=0.0,
                        evidence=evidence + ["page_reachable_but_no_scholarly_metadata"],
                        source_priority=source_priority,
                        source_kind="probe",
                        source_family=page_family,
                    )
                ]
            return []
    except (httpx.HTTPError, ValueError) as exc:
        return [
            BibCandidate(
                source=source,
                source_url=_short_url(url),
                bibtex=None,
                fields={},
                confidence="not_found",
                score=0.0,
                evidence=evidence + [f"request_failed={type(exc).__name__}: {exc}"],
                source_priority=source_priority,
                source_kind="probe",
                source_family=infer_source_family(source, url),
            )
        ]


def _looks_like_bibtex_link(href: str, text: str) -> bool:
    lowered = href.lower()
    return bool(
        "bibtex" in lowered
        or lowered.endswith(".bib")
        or lowered.startswith(("data:application/x-bibtex", "application/x-bibtex", "/application/x-bibtex"))
        or ("citation" in lowered and "bib" in text)
        or text in {"bibtex", "download bibtex", "export bibtex", "cite in bibtex"}
    )


def _has_strong_bibtex(candidates: list[BibCandidate]) -> bool:
    return any(
        candidate.bibtex and candidate.confidence in {"exact", "high"}
        for candidate in candidates
    )


def _redacted_data_uri(value: str) -> str:
    media = value.split(",", 1)[0]
    return f"{media},<redacted>"


def _short_url(value: str, limit: int = 240) -> str:
    return value if len(value) <= limit else value[:limit] + "…"


def _find_inline_bibtex(soup: BeautifulSoup) -> str | None:
    for node in soup.find_all(["pre", "code", "textarea"]):
        text = node.get_text("\n", strip=True)
        if BIBTEX_RE.search(text):
            return text
    return None


def extract_citation_meta(soup: BeautifulSoup) -> dict[str, str]:
    raw: dict[str, list[str]] = {}
    for tag in soup.find_all("meta"):
        name = tag.get("name") or tag.get("property")
        content = tag.get("content")
        if not name or content is None:
            continue
        name = str(name).strip().lower()
        content = str(content).strip()
        if name.startswith("citation_") and content:
            raw.setdefault(name, []).append(content)

    fields: dict[str, str] = {}
    mapping = {
        "citation_title": "title",
        "citation_doi": "doi",
        "citation_conference_title": "booktitle",
        "citation_journal_title": "journal",
        "citation_volume": "volume",
        "citation_issue": "number",
        "citation_publisher": "publisher",
        "citation_isbn": "isbn",
        "citation_issn": "issn",
    }
    for source_key, target_key in mapping.items():
        if raw.get(source_key):
            fields[target_key] = raw[source_key][0]
    if raw.get("citation_author"):
        fields["author"] = " and ".join(raw["citation_author"])
    date_value = (
        raw.get("citation_publication_date")
        or raw.get("citation_date")
        or raw.get("citation_year")
        or [""]
    )[0]
    if date_value:
        fields["year"] = date_value[:4]
    if raw.get("citation_firstpage") and raw.get("citation_lastpage"):
        fields["pages"] = f"{raw['citation_firstpage'][0]}--{raw['citation_lastpage'][0]}"
    elif raw.get("citation_firstpage"):
        fields["pages"] = raw["citation_firstpage"][0]
    if raw.get("citation_abstract_html_url"):
        fields["url"] = raw["citation_abstract_html_url"][0]
    elif raw.get("citation_pdf_url"):
        fields["url"] = raw["citation_pdf_url"][0]
    return fields


def extract_json_ld(soup: BeautifulSoup) -> dict[str, str]:
    queue: list[object] = []
    for node in soup.find_all("script", attrs={"type": "application/ld+json"}):
        try:
            payload = json.loads(node.string or node.get_text())
        except (json.JSONDecodeError, TypeError):
            continue
        queue.extend(payload if isinstance(payload, list) else [payload])

    while queue:
        obj = queue.pop(0)
        if not isinstance(obj, dict):
            continue
        graph = obj.get("@graph")
        if isinstance(graph, list):
            queue.extend(graph)
        type_value = obj.get("@type")
        types = {type_value} if isinstance(type_value, str) else set(type_value or [])
        if not types.intersection(
            {"ScholarlyArticle", "Article", "TechArticle", "Chapter", "Book"}
        ):
            continue
        fields: dict[str, str] = {}
        if obj.get("headline") or obj.get("name"):
            fields["title"] = str(obj.get("headline") or obj.get("name"))
        authors = obj.get("author") or []
        if isinstance(authors, dict):
            authors = [authors]
        names = [
            str(author.get("name") or "")
            for author in authors
            if isinstance(author, dict) and author.get("name")
        ]
        if names:
            fields["author"] = " and ".join(names)
        if obj.get("datePublished"):
            fields["year"] = str(obj["datePublished"])[:4]
        identifier = obj.get("identifier")
        if isinstance(identifier, str) and identifier.lower().startswith("10."):
            fields["doi"] = identifier
        if obj.get("isbn"):
            fields["isbn"] = str(obj["isbn"])
        if obj.get("url"):
            fields["url"] = str(obj["url"])
        if obj.get("publisher"):
            publisher = obj["publisher"]
            if isinstance(publisher, dict):
                publisher = publisher.get("name")
            if publisher:
                fields["publisher"] = str(publisher)
        part_of = obj.get("isPartOf") or {}
        if isinstance(part_of, dict) and part_of.get("name"):
            fields["journal"] = str(part_of["name"])
        return fields
    return {}
