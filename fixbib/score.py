from __future__ import annotations

from rapidfuzz import fuzz

from .inventory import find_entry_isbn, normalize_isbn
from .model import BibInputEntry, Confidence
from .normalizer import find_entry_doi, first_author_last_name, normalize_doi, normalize_year, norm_text


def score_candidate(original: BibInputEntry, candidate_fields: dict[str, str]) -> tuple[float, Confidence, list[str]]:
    evidence: list[str] = []
    score = 0.0

    original_title = norm_text(original.fields.get("title", ""))
    candidate_title = norm_text(candidate_fields.get("title", ""))
    title_sim = 0.0
    if original_title and candidate_title:
        title_sim = float(fuzz.token_set_ratio(original_title, candidate_title))
        evidence.append(f"title_similarity={title_sim:.1f}")
        score += 0.50 * title_sim / 100.0

    original_doi = find_entry_doi(original.fields) or ""
    candidate_doi = normalize_doi(candidate_fields.get("doi", "")) if candidate_fields.get("doi") else ""
    doi_mismatch = False
    if original_doi and candidate_doi:
        if original_doi == candidate_doi:
            score += 0.30
            evidence.append("doi_exact_match")
        else:
            doi_mismatch = True
            evidence.append(f"doi_mismatch={original_doi}!={candidate_doi}")

    original_isbn = find_entry_isbn(original.fields) or ""
    candidate_isbn = normalize_isbn(candidate_fields.get("isbn", "")) if candidate_fields.get("isbn") else ""
    if original_isbn and candidate_isbn:
        if original_isbn == candidate_isbn:
            score += 0.25
            evidence.append("isbn_exact_match")
        else:
            evidence.append(f"isbn_mismatch={original_isbn}!={candidate_isbn}")

    original_year = normalize_year(original.fields.get("year", ""))
    candidate_year = normalize_year(candidate_fields.get("year", ""))
    year_match = False
    if original_year and candidate_year:
        if original_year == candidate_year:
            year_match = True
            score += 0.08
            evidence.append("year_match")
        else:
            evidence.append(f"year_mismatch={original_year}!={candidate_year}")

    original_first_author = first_author_last_name(original.fields.get("author", ""))
    candidate_first_author = first_author_last_name(candidate_fields.get("author", ""))
    author_match = False
    if original_first_author and candidate_first_author:
        if original_first_author == candidate_first_author:
            author_match = True
            score += 0.12
            evidence.append("first_author_match")
        else:
            evidence.append(f"first_author_mismatch={original_first_author}!={candidate_first_author}")

    score = min(score, 1.0)
    if doi_mismatch:
        # An input DOI can be wrong. A different DOI returned by a title search
        # is therefore a recovery candidate when title, year, and first author
        # independently agree. It remains high (not exact) until the candidate
        # DOI is resolved through a deterministic publisher/registry adapter.
        metadata_match = bool(
            title_sim >= 96
            and (author_match or not original_first_author)
            and (year_match or not original_year)
        )
        if metadata_match:
            evidence.append("identifier_recovery_candidate")
            return max(score, 0.62), "high", evidence
        evidence.append("identifier_content_conflict=doi_and_metadata")
        return min(score, 0.49), "conflict", evidence

    if original_doi and candidate_doi and original_doi == candidate_doi:
        # A DOI match identifies the registry record, but the DOI stored in the
        # input may itself be wrong. Do not let identifier equality override a
        # strong title/author contradiction (for example, an IEEE DOI pointing
        # to an entirely different paper).
        if original_title and candidate_title:
            if title_sim < 60:
                evidence.append("identifier_content_conflict=title")
                return min(score, 0.59), "conflict", evidence
            if title_sim < 85 and original_first_author and candidate_first_author and not author_match:
                evidence.append("identifier_content_conflict=title_and_first_author")
                return min(score, 0.59), "conflict", evidence
        if title_sim >= 80 or not original_title:
            return score, "exact", evidence
        return score, "high", evidence

    if original_isbn and candidate_isbn and original_isbn == candidate_isbn:
        if title_sim >= 80 or not original_title:
            return score, "exact", evidence
        return score, "high", evidence

    # A title search alone must never be treated as identifier-exact. Even a
    # perfect bibliographic match remains high confidence until a deterministic
    # source adapter elevates it.
    if title_sim >= 96 and (author_match or not original_first_author) and (year_match or not original_year):
        return score, "high", evidence
    if score >= 0.62:
        return score, "high", evidence
    if score >= 0.42:
        return score, "low", evidence
    return score, "not_found", evidence
