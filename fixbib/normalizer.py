from __future__ import annotations

import html
import re
from urllib.parse import unquote

DOI_RE = re.compile(r"\b10\.\d{4,9}/[-._;()/:A-Z0-9]+\b", re.I)
ARXIV_RE = re.compile(r"(?:arxiv[:/ ]|abs/|pdf/)?((?:\d{4}\.\d{4,5}|[a-z-]+/\d{7})(?:v\d+)?)", re.I)


def normalize_doi(value: str) -> str:
    value = unquote((value or "").strip())
    value = re.sub(r"^https?://(?:dx\.)?doi\.org/", "", value, flags=re.I)
    value = re.sub(r"^doi:\s*", "", value, flags=re.I)
    return value.strip().rstrip(".,;)").lower()


def find_doi_in_text(value: str) -> str | None:
    match = DOI_RE.search(value or "")
    return normalize_doi(match.group(0)) if match else None


def find_entry_doi(fields: dict[str, str]) -> str | None:
    for key in ("doi", "url", "howpublished", "note"):
        doi = find_doi_in_text(fields.get(key, ""))
        if doi:
            return doi
    return None


def normalize_arxiv_id(value: str) -> str:
    match = ARXIV_RE.search(value or "")
    if not match:
        return ""
    return re.sub(r"v\d+$", "", match.group(1), flags=re.I)


def find_entry_arxiv_id(fields: dict[str, str]) -> str:
    eprint = fields.get("eprint", "")
    archive = fields.get("archiveprefix", "").lower()
    if eprint and (archive == "arxiv" or re.fullmatch(r"(?:\d{4}\.\d{4,5}|[a-z-]+/\d{7})(?:v\d+)?", eprint.strip(), re.I)):
        arxiv_id = normalize_arxiv_id(eprint)
        if arxiv_id:
            return arxiv_id
    for key in ("url", "note", "howpublished"):
        value = fields.get(key, "")
        lowered = value.lower()
        if "arxiv" not in lowered and "arxiv.org/abs/" not in lowered and "arxiv.org/pdf/" not in lowered:
            continue
        arxiv_id = normalize_arxiv_id(value)
        if arxiv_id:
            return arxiv_id
    return ""


def norm_text(value: str) -> str:
    value = html.unescape(value or "")
    value = re.sub(r"\\[a-zA-Z]+\*?(?:\[[^]]*\])?\s*", " ", value)
    value = value.replace("{", "").replace("}", "")
    value = value.lower()
    value = re.sub(r"[^a-z0-9]+", " ", value)
    return " ".join(value.split())


def first_author_last_name(author_field: str) -> str:
    if not author_field:
        return ""
    first = re.split(r"\s+and\s+", author_field, maxsplit=1, flags=re.I)[0].strip()
    if not first:
        return ""
    if "," in first:
        return norm_text(first.split(",", 1)[0])
    parts = norm_text(first).split()
    return parts[-1] if parts else ""


def normalize_year(value: str) -> str:
    match = re.search(r"(?:19|20)\d{2}", value or "")
    return match.group(0) if match else ""


def is_preprint_entry(entry_type: str, fields: dict[str, str]) -> bool:
    archive = fields.get("archiveprefix", "").lower()
    combined = " ".join(
        fields.get(key, "") for key in ("url", "note", "howpublished", "journal")
    ).lower()
    return bool(
        find_entry_arxiv_id(fields)
        or archive == "arxiv"
        or "arxiv" in combined
        or "preprint" in combined
        or (entry_type == "misc" and fields.get("eprint"))
    )
