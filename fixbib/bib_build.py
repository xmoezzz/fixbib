from __future__ import annotations

import html
import re

FIELD_ORDER = [
    "author",
    "editor",
    "title",
    "booktitle",
    "journal",
    "series",
    "volume",
    "number",
    "edition",
    "pages",
    "articleno",
    "year",
    "month",
    "publisher",
    "address",
    "institution",
    "school",
    "isbn",
    "issn",
    "doi",
    "eprint",
    "archiveprefix",
    "primaryclass",
    "url",
]

EXCLUDED_CANONICAL_FIELDS = {
    "abstract",
    "keywords",
    "numpages",
    "issue_date",
    "biburl",
    "bibsource",
    "timestamp",
}


MONTH_MACROS = {
    "january": "jan", "jan": "jan",
    "february": "feb", "feb": "feb",
    "march": "mar", "mar": "mar",
    "april": "apr", "apr": "apr",
    "may": "may",
    "june": "jun", "jun": "jun",
    "july": "jul", "jul": "jul",
    "august": "aug", "aug": "aug",
    "september": "sep", "sept": "sep", "sep": "sep", "sep.": "sep",
    "october": "oct", "oct": "oct",
    "november": "nov", "nov": "nov",
    "december": "dec", "dec": "dec",
}


def escape_bib_value(value: str) -> str:
    value = html.unescape(str(value)).strip().replace("\r", " ").replace("\n", " ")
    value = " ".join(value.split())
    # Remote HTML/JSON metadata often contains raw TeX-special characters.
    # Preserve already escaped sequences while making generated BibTeX safe
    # for classic BibTeX as well as Biber.
    value = re.sub(r"(?<!\\)%", r"\\%", value)
    value = re.sub(r"(?<!\\)&", r"\\&", value)
    return value


def render_bib_field(field: str, value: str) -> str:
    cleaned = escape_bib_value(value)
    if field.lower() == "month":
        macro = MONTH_MACROS.get(cleaned.lower())
        if macro:
            return macro
    return "{" + cleaned + "}"


def build_bibtex_from_fields(entry_type: str, key: str, fields: dict[str, str]) -> str:
    entry_type = (entry_type or "misc").lower()
    lines = [f"@{entry_type}{{{key},"]
    emitted: set[str] = set()

    for field in FIELD_ORDER:
        value = fields.get(field)
        if value and field not in EXCLUDED_CANONICAL_FIELDS:
            emitted.add(field)
            lines.append(f"  {field} = {render_bib_field(field, value)},")

    for field in sorted(fields):
        if field in emitted or field in EXCLUDED_CANONICAL_FIELDS:
            continue
        value = fields[field]
        if value:
            lines.append(f"  {field} = {render_bib_field(field, value)},")

    lines.append("}")
    return "\n".join(lines)


def build_bibtex_preserving_fields(
    entry_type: str,
    key: str,
    fields: dict[str, str],
    field_order: list[str] | None = None,
) -> str:
    """Build an applied entry without dropping original or custom fields."""
    entry_type = (entry_type or "misc").lower()
    lines = [f"@{entry_type}{{{key},"]
    emitted: set[str] = set()
    requested_order = list(field_order or [])
    for field in FIELD_ORDER:
        if field not in requested_order:
            requested_order.append(field)
    for field in fields:
        if field not in requested_order:
            requested_order.append(field)

    for field in requested_order:
        if field not in fields or field in emitted:
            continue
        value = fields[field]
        emitted.add(field)
        # Empty fields already present in the user's entry are preserved too.
        # Remote empty fields are filtered before the merge and cannot be added.
        lines.append(f"  {field} = {render_bib_field(field, value)},")
    lines.append("}")
    return "\n".join(lines)
