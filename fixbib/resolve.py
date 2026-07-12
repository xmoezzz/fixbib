from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from urllib.parse import urlparse

from .cache import DoiCache
from .inventory import inspect_entry
from .merge import merge_candidate_into_entry
from .model import ACADEMIC_TYPES, BibCandidate, BibInputEntry, ResolveResult
from .provenance import infer_source_family, source_authority
from .normalizer import (
    find_entry_doi,
    first_author_last_name,
    is_preprint_entry,
    normalize_doi,
    normalize_year,
    norm_text,
)
from .resolvers.acl import AclAnthologyResolver
from .resolvers.acm import AcmPageFallbackResolver, AcmResolver
from .resolvers.arxiv import ArxivResolver
from .resolvers.books import GoogleBooksResolver, OpenLibraryBookResolver
from .resolvers.crossref import CrossrefResolver
from .resolvers.dblp import DblpResolver
from .resolvers.doi import DoiBibtexResolver
from .resolvers.landing import DoiLandingPageResolver
from .resolvers.ieee import IeeeResolver
from .resolvers.openalex import OpenAlexResolver
from .resolvers.page import GenericCitationMetaResolver
from .resolvers.semantic_scholar import SemanticScholarResolver
from .resolvers.springer import SpringerResolver
from .resolvers.usenix import UsenixResolver


class ResolverPipeline:
    """Evidence-ordered bibliography resolution pipeline.

    Stages are intentionally separated so that exact identifiers, publisher
    exports, bibliographic indexes, and arbitrary web pages do not carry the
    same evidentiary weight.
    """

    def __init__(
        self,
        *,
        doi_base_url: str = "https://doi.org",
        crossref_api_base: str = "https://api.crossref.org",
        dblp_api_base: str = "https://dblp.org/search/publ/api",
        springer_base_url: str = "https://citation-needed.springer.com/v2/references",
        acl_base_url: str = "https://aclanthology.org",
        arxiv_bibtex_base: str = "https://arxiv.org/bibtex",
        arxiv_api_base: str = "https://export.arxiv.org/api/query",
        acm_base_url: str = "https://dl.acm.org/doi",
        acm_export_url: str | None = None,
        ieee_base_url: str = "https://ieeexplore.ieee.org",
        openlibrary_api_base: str = "https://openlibrary.org",
        google_books_api_key: str | None = None,
        google_books_api_base: str = "https://www.googleapis.com/books/v1/volumes",
        openalex_api_key: str | None = None,
        openalex_api_base: str = "https://api.openalex.org/works",
        semantic_scholar_enabled: bool = False,
        semantic_scholar_api_key: str | None = None,
        semantic_scholar_api_base: str = "https://api.semanticscholar.org/graph/v1/paper/search",
        timeout: float = 15.0,
        thorough: bool = False,
        page_fallback: bool = True,
        doi_landing: bool = True,
        acm_fallback: bool = True,
        discovered_page_fallback: bool = True,
        max_discovered_urls: int = 4,
        max_discovered_dois: int = 3,
        cache_enabled: bool = True,
        cache_dir: Path | None = None,
    ) -> None:
        self.thorough = thorough
        self.page_fallback = page_fallback
        self.doi_landing_enabled = doi_landing
        self.acm_fallback_enabled = acm_fallback
        self.discovered_page_fallback = discovered_page_fallback
        self.max_discovered_urls = max_discovered_urls
        self.max_discovered_dois = max_discovered_dois
        self.cache = DoiCache(enabled=cache_enabled, root=cache_dir)

        self.doi_landing_resolver = DoiLandingPageResolver(
            base_url=doi_base_url,
            timeout=timeout,
        )
        self.usenix_resolver = UsenixResolver(timeout=timeout)
        self.exact_resolvers = [
            # Deterministic source exports.
            AclAnthologyResolver(base_url=acl_base_url, timeout=timeout),
            SpringerResolver(base_url=springer_base_url, timeout=timeout),
            ArxivResolver(
                bibtex_base=arxiv_bibtex_base,
                api_base=arxiv_api_base,
                timeout=timeout,
                title_fallback=False,
            ),
            self.usenix_resolver,
            # Exact scholarly identifiers.
            DoiBibtexResolver(base_url=doi_base_url, timeout=timeout),
            CrossrefResolver(
                api_base=crossref_api_base,
                timeout=timeout,
                mode="exact",
            ),
        ]
        self.direct_page_resolver = GenericCitationMetaResolver(timeout=timeout)
        effective_acm_export_url = acm_export_url
        if effective_acm_export_url is None and "dl.acm.org" in urlparse(acm_base_url).netloc.lower():
            effective_acm_export_url = "https://dl.acm.org/action/exportCiteProcCitation"
        self.publisher_resolvers = [
            AcmResolver(
                base_url=acm_base_url,
                export_url=effective_acm_export_url,
                timeout=timeout,
            ),
            IeeeResolver(
                base_url=ieee_base_url,
                doi_base_url=doi_base_url,
                timeout=timeout,
            ),
        ]
        self.acm_page_resolver = AcmPageFallbackResolver(
            base_url=acm_base_url,
            timeout=timeout,
        )

        self.book_resolvers = [
            OpenLibraryBookResolver(api_base=openlibrary_api_base, timeout=timeout),
        ]
        if google_books_api_key:
            self.book_resolvers.append(
                GoogleBooksResolver(
                    api_key=google_books_api_key,
                    api_base=google_books_api_base,
                    timeout=timeout,
                )
            )

        self.search_resolvers = [
            # DBLP is preferred for CS; Crossref is the broad publisher-deposited fallback.
            DblpResolver(api_base=dblp_api_base, timeout=timeout),
            CrossrefResolver(
                api_base=crossref_api_base,
                timeout=timeout,
                mode="search",
            ),
        ]
        if openalex_api_key:
            self.search_resolvers.append(
                OpenAlexResolver(
                    api_key=openalex_api_key,
                    api_base=openalex_api_base,
                    timeout=timeout,
                )
            )
        if semantic_scholar_enabled:
            self.search_resolvers.append(
                SemanticScholarResolver(
                    api_key=semantic_scholar_api_key,
                    api_base=semantic_scholar_api_base,
                    timeout=timeout,
                    enabled=True,
                )
            )

        self.candidate_doi_resolvers = [
            DoiBibtexResolver(base_url=doi_base_url, timeout=timeout),
            CrossrefResolver(
                api_base=crossref_api_base,
                timeout=timeout,
                mode="exact",
            ),
        ]
        self.preprint_search_resolver = ArxivResolver(
            bibtex_base=arxiv_bibtex_base,
            api_base=arxiv_api_base,
            timeout=timeout,
            title_fallback=True,
        )

    def resolve_one(self, entry: BibInputEntry, auto: str = "exact") -> ResolveResult:
        inventory = inspect_entry(entry)
        diagnostics: list[str] = []
        if entry.duplicate_key:
            diagnostics.append("duplicate_key: automatic replacement disabled")
        if entry.duplicate_fields:
            diagnostics.append("duplicate_fields: review original entry")
        if entry.entry_type not in ACADEMIC_TYPES and not is_preprint_entry(entry.entry_type, entry.fields):
            diagnostics.append("non_academic_entry: metadata may be reported but will not be replaced")
        if not inventory.has_doi_or_url:
            diagnostics.append("no_doi_or_url: entry has neither a DOI nor a web URL")
        if not inventory.has_any_locator:
            diagnostics.append("no_stable_locator: no DOI, URL, ISBN, or arXiv identifier")

        all_candidates: list[BibCandidate] = []

        # Stage 1: physically open the DOI as an HTML link. This records the
        # final publisher URL and may expose a direct BibTeX exporter.
        if self.doi_landing_enabled:
            self._run_resolver(
                all_candidates, "doi_landing", self.doi_landing_resolver, entry, diagnostics
            )

        # Stage 2: deterministic exports and exact identifier APIs. Run all
        # applicable exact sources so conflicts can be detected.
        for resolver in self.exact_resolvers:
            self._run_resolver(
                all_candidates, "exact_identifier", resolver, entry, diagnostics
            )

        # Stage 3: publisher-native exporters are enrichment sources, not mere
        # identity fallbacks. Run them even when Crossref already produced an
        # exact DOI match; otherwise rich fields such as ISBN, abstract,
        # keywords, page count, location, and series can never be recovered.
        for resolver in self.publisher_resolvers:
            if isinstance(resolver, AcmResolver) and not self.acm_fallback_enabled:
                continue
            self._run_resolver(
                all_candidates, "publisher_native_export", resolver, entry, diagnostics
            )

        strong_after_exact = _has_strong_candidate(all_candidates)

        # Stage 4: inspect URLs already present in the BibTeX entry. This is how
        # obscure conference sites with their own citation exporter are handled.
        if self.page_fallback and (self.thorough or not strong_after_exact):
            self._run_resolver(
                all_candidates, "entry_url", self.direct_page_resolver, entry, diagnostics
            )

        # Stage 4: book-specific indexes. ISBN is stronger than title search;
        # the individual resolver enforces that order internally.
        if entry.entry_type in {"book", "inbook", "incollection", "proceedings"} and (
            self.thorough or not _has_strong_candidate(all_candidates)
        ):
            for resolver in self.book_resolvers:
                self._run_resolver(
                    all_candidates, "book_lookup", resolver, entry, diagnostics
                )

        # Stage 5: broad bibliographic indexes when direct evidence is missing.
        if self.thorough or not _has_strong_candidate(all_candidates) or _only_preprint_strong_candidates(all_candidates):
            for resolver in self.search_resolvers:
                self._run_resolver(
                    all_candidates, "bibliographic_search", resolver, entry, diagnostics
                )

        # Stage 6: take high-confidence identifiers discovered by searches and
        # resolve them through exact DOI sources. This converts a search hit into
        # independently verifiable evidence instead of trusting the search alone.
        discovered_dois = _candidate_dois(all_candidates, inventory.doi)
        for doi in discovered_dois[: self.max_discovered_dois]:
            derived = replace(entry, fields={**entry.fields, "doi": doi})
            if self.doi_landing_enabled:
                self._run_resolver(
                    all_candidates,
                    "candidate_doi_landing",
                    self.doi_landing_resolver,
                    derived,
                    diagnostics,
                )
            for resolver in self.candidate_doi_resolvers:
                self._run_resolver(
                    all_candidates,
                    "candidate_identifier_enrichment",
                    resolver,
                    derived,
                    diagnostics,
                )
            for resolver in self.publisher_resolvers:
                if isinstance(resolver, AcmResolver) and not self.acm_fallback_enabled:
                    continue
                self._run_resolver(
                    all_candidates,
                    "candidate_publisher_native_export",
                    resolver,
                    derived,
                    diagnostics,
                )

        # Stage 7: publisher and conference pages discovered from index results.
        # This includes official conference sites that expose .bib links but are
        # too obscure to warrant a hard-coded adapter.
        discovered_urls = _candidate_urls(all_candidates, inventory.urls)[: self.max_discovered_urls]

        # A DBLP/index match can reveal the official USENIX page even when the
        # original entry has no URL. Re-run URL-driven native adapters on those
        # discovered pages regardless of whether an index candidate is already
        # strong; otherwise publisher enrichment is permanently skipped.
        for url in discovered_urls:
            derived = replace(entry, fields={**entry.fields, "url": url})
            self._run_resolver(
                all_candidates,
                "discovered_publisher_native_export",
                self.usenix_resolver,
                derived,
                diagnostics,
            )

        if self.discovered_page_fallback and self.page_fallback and (
            self.thorough or not _has_strong_candidate(all_candidates)
        ):
            for url in discovered_urls:
                derived = replace(entry, fields={**entry.fields, "url": url})
                self._run_resolver(
                    all_candidates,
                    "discovered_publication_page",
                    self.direct_page_resolver,
                    derived,
                    diagnostics,
                )

        # Stage 8: if ACM's native export service did not yield a record,
        # inspect the ACM page for a direct citation link or metadata. Keep this
        # as a separate stage so audit reports never label a page scrape as a
        # native exporter.
        if self.acm_fallback_enabled and not _has_family_native_export(all_candidates, "acm"):
            self._run_resolver(
                all_candidates, "publisher_fallback", self.acm_page_resolver, entry, diagnostics
            )

        # Stage 9: arXiv title search is never generic. It only runs when the
        # original entry already identifies itself as a preprint.
        if self.thorough or not _has_strong_candidate(all_candidates):
            self._run_resolver(
                all_candidates,
                "preprint_search",
                self.preprint_search_resolver,
                entry,
                diagnostics,
            )

        recovery_candidate = _find_verified_identifier_recovery(entry, all_candidates)
        if recovery_candidate is not None:
            if "identifier_recovery_verified" not in recovery_candidate.evidence:
                recovery_candidate.evidence.append("identifier_recovery_verified")
            diagnostics.append(
                "identifier_recovery_verified: original identifier will be corrected "
                f"to {normalize_doi(recovery_candidate.fields.get('doi', ''))}"
            )

        ambiguity_messages = detect_bundle_ambiguities(all_candidates)
        conflict_messages = detect_candidate_conflicts(all_candidates)
        if recovery_candidate is not None:
            # A verified recovery necessarily produces two DOI values in the
            # candidate pool: the incorrect original DOI and the independently
            # recovered DOI. That is not an unresolved candidate conflict.
            conflict_messages = [
                message
                for message in conflict_messages
                if not message.startswith("candidate_doi_conflict:")
            ]
        original_conflict = any(
            candidate.bibtex
            and candidate.confidence == "conflict"
            and candidate.score >= 0.42
            for candidate in all_candidates
        )
        if original_conflict and recovery_candidate is None:
            conflict_messages.append(
                "original_identifier_conflict: a resolved record disagrees with the original DOI or ISBN"
            )
        diagnostics.extend(ambiguity_messages)
        diagnostics.extend(conflict_messages)
        if not ambiguity_messages and not conflict_messages:
            corroborate_candidates(all_candidates)
        selected = recovery_candidate or select_best_candidate(all_candidates)
        if selected is not None:
            diagnostics.append(f"selected_source_kind={selected.source_kind}")
            diagnostics.append(f"selected_source_family={_source_family(selected)}")
            if selected.source_kind == "registry_transform":
                diagnostics.append(
                    "registry_transform_selected_as_fallback: identity is verified, but formatting is not publisher-native"
                )
        diagnostics.extend(_publisher_probe_diagnostics(all_candidates))
        if conflict_messages and selected is not None:
            action = "report_only_conflict"
        elif ambiguity_messages and selected is not None:
            action = "report_only_ambiguous"
        else:
            action = decide_action(entry, selected, auto)

        applied_bibtex = None
        merge_report = None
        if action == "replace" and selected is not None:
            supplements = _supplemental_candidates_for(selected, all_candidates)
            merged = merge_candidate_into_entry(
                entry,
                selected,
                supplemental_candidates=supplements,
            )
            if merged.changed:
                applied_bibtex = merged.bibtex
                merge_report = merged.to_jsonable()
            else:
                # Exact metadata can verify an entry without forcing a noisy
                # rewrite when all semantic values are already equivalent.
                action = "verified_no_change"
                merge_report = merged.to_jsonable()

        return ResolveResult(
            key=entry.key,
            original=entry,
            candidates=all_candidates,
            selected=selected,
            diagnostics=diagnostics,
            action=action,
            applied_bibtex=applied_bibtex,
            merge_report=merge_report,
        )

    def _run_resolver(
        self,
        target: list[BibCandidate],
        stage: str,
        resolver: object,
        entry: BibInputEntry,
        diagnostics: list[str],
    ) -> None:
        """Run one resolver as a failure-isolated pipeline stage."""

        name = str(getattr(resolver, "name", type(resolver).__name__))
        try:
            can_resolve = bool(resolver.can_resolve(entry))  # type: ignore[attr-defined]
        except Exception as exc:
            diagnostics.append(
                f"resolver_can_resolve_error[{stage}/{name}]={type(exc).__name__}: {exc}"
            )
            return
        if not can_resolve:
            return
        doi = find_entry_doi(entry.fields) or ""
        cacheable = bool(doi and _cacheable_stage(stage))
        if cacheable:
            cached = self.cache.load(doi=doi, resolver=resolver, entry=entry)
            if cached is not None:
                diagnostics.append(f"doi_cache_hit[{stage}/{name}]={doi}")
                self._extend(target, stage, cached)
                return

        try:
            candidates = resolver.resolve(entry)  # type: ignore[attr-defined]
            if candidates is None:
                raise TypeError("resolver returned None instead of a candidate list")
            if not isinstance(candidates, list):
                raise TypeError(
                    f"resolver returned {type(candidates).__name__}, expected list"
                )
            if cacheable:
                if self.cache.store(doi=doi, resolver=resolver, candidates=candidates):
                    diagnostics.append(f"doi_cache_write[{stage}/{name}]={doi}")
        except Exception as exc:
            diagnostics.append(
                f"resolver_error[{stage}/{name}]={type(exc).__name__}: {exc}"
            )
            candidates = [
                BibCandidate(
                    source=name,
                    source_url=None,
                    bibtex=None,
                    fields={},
                    confidence="not_found",
                    score=0.0,
                    evidence=[
                        f"resolver_exception={type(exc).__name__}",
                        "resolver_failure_isolated",
                    ],
                    source_priority=0,
                )
            ]
        self._extend(target, stage, candidates)

    @staticmethod
    def _extend(target: list[BibCandidate], stage: str, candidates: list[BibCandidate]) -> None:
        for candidate in candidates:
            candidate.stage = stage
            if not candidate.source_family:
                candidate.source_family = infer_source_family(candidate.source, candidate.source_url)
            marker = f"pipeline_stage={stage}"
            if marker not in candidate.evidence:
                candidate.evidence.append(marker)
            kind_marker = f"source_kind={candidate.source_kind}"
            if kind_marker not in candidate.evidence:
                candidate.evidence.append(kind_marker)
            family_marker = f"source_family={candidate.source_family}"
            if family_marker not in candidate.evidence:
                candidate.evidence.append(family_marker)
            target.append(candidate)



_CACHEABLE_STAGES = {
    "doi_landing",
    "exact_identifier",
    "publisher_native_export",
    "candidate_doi_landing",
    "candidate_identifier_enrichment",
    "candidate_publisher_native_export",
    "publisher_fallback",
}


def _cacheable_stage(stage: str) -> bool:
    return stage in _CACHEABLE_STAGES

def select_best_candidate(candidates: list[BibCandidate]) -> BibCandidate | None:
    usable = [
        candidate
        for candidate in candidates
        if candidate.bibtex and candidate.confidence in {"exact", "high", "low"}
    ]
    if not usable:
        conflicts = [
            candidate
            for candidate in candidates
            if candidate.bibtex and candidate.confidence == "conflict"
        ]
        if not conflicts:
            return None
        conflicts.sort(
            key=lambda candidate: (candidate.score, source_authority(candidate), candidate.source_priority),
            reverse=True,
        )
        return conflicts[0]
    confidence_rank = {"exact": 3, "high": 2, "low": 1, "not_found": 0, "conflict": -1}

    # A registry transform is evidence-only. It must not block an exact/high
    # writable source merely because the transform repeats the input DOI and is
    # therefore labeled exact. This is essential when a DBLP/publisher record
    # correctly identifies the formal publication while a transform renders an
    # unrelated or preprint-shaped record.
    writable_strong = [
        candidate
        for candidate in usable
        if candidate.source_kind != "registry_transform"
        and candidate.confidence in {"exact", "high"}
    ]
    pool = writable_strong or usable
    pool.sort(
        key=lambda candidate: (
            confidence_rank[candidate.confidence],
            0 if _candidate_is_preprint(candidate) else 1,
            source_authority(candidate),
            candidate.source_priority,
            candidate.score,
        ),
        reverse=True,
    )
    return pool[0]


def _find_verified_identifier_recovery(
    entry: BibInputEntry,
    candidates: list[BibCandidate],
) -> BibCandidate | None:
    """Find a uniquely supported replacement for an incorrect input DOI.

    Merely resolving the input DOI is insufficient because the identifier may
    point to another paper. Recovery is permitted only when a different DOI is
    backed by a near-exact title match, matching year and first author, and a
    deterministic writable source (or independent source corroboration).
    """

    original_doi = find_entry_doi(entry.fields) or ""
    if not original_doi:
        return None

    eligible: list[BibCandidate] = []
    for candidate in candidates:
        if not candidate.bibtex or candidate.confidence not in {"exact", "high"}:
            continue
        if candidate.source_kind in {"probe", "registry_transform"}:
            continue
        doi = normalize_doi(candidate.fields.get("doi", ""))
        if not doi or doi == original_doi:
            continue
        evidence = set(candidate.evidence)
        title_scores: list[float] = []
        for item in evidence:
            if item.startswith("title_similarity="):
                try:
                    title_scores.append(float(item.split("=", 1)[1]))
                except ValueError:
                    pass
        if not title_scores or max(title_scores) < 98.0:
            continue
        if "year_match" not in evidence or "first_author_match" not in evidence:
            continue
        deterministic = candidate.source_kind in {
            "publisher_native_export",
            "repository_native_export",
            "bibliographic_index_export",
            "registry_metadata",
        }
        corroborated = any(
            item.startswith("corroborated_by_independent_sources=")
            for item in evidence
        )
        if not deterministic and not corroborated:
            continue
        eligible.append(candidate)

    if not eligible:
        return None

    # Multiple different recovered DOI values are ambiguous and must remain a
    # report-only conflict.
    recovered_dois = {
        normalize_doi(candidate.fields.get("doi", "")) for candidate in eligible
    }
    if len(recovered_dois) != 1:
        return None
    eligible.sort(
        key=lambda candidate: (
            source_authority(candidate),
            candidate.source_priority,
            candidate.score,
        ),
        reverse=True,
    )
    return eligible[0]


def decide_action(entry: BibInputEntry, selected: BibCandidate | None, auto: str) -> str:
    if selected is None:
        return "report_only"
    if entry.duplicate_key:
        return "report_only_duplicate_key"
    if entry.entry_type not in ACADEMIC_TYPES and not is_preprint_entry(entry.entry_type, entry.fields):
        return "report_only_non_academic"
    if auto == "none":
        return "report_only"
    # Registry content-negotiation output is generated by a schema converter.
    # It can confirm that the DOI resolved, but it is not authoritative enough
    # to write or supplement bibliographic fields.
    if selected.source_kind == "registry_transform":
        return "report_only_registry_transform"
    if auto == "exact" and selected.confidence == "exact":
        return "replace"
    if auto == "verified" and (
        selected.confidence == "exact" or _is_verified_high_candidate(selected)
    ):
        return "replace"
    if auto == "high" and selected.confidence in {"exact", "high"}:
        return "replace"
    return "report_only"


def _is_verified_high_candidate(candidate: BibCandidate) -> bool:
    """Accept only high matches backed by authoritative or corroborated evidence."""

    if candidate.confidence != "high":
        return False
    evidence = set(candidate.evidence)
    if any(item.startswith("corroborated_by_independent_sources=") for item in evidence):
        return True
    deterministic = any(
        marker in evidence
        for marker in {
            "dblp_deterministic_bib_endpoint",
            "springer_deterministic_export",
            "acl_deterministic_bib_endpoint",
            "exact_arxiv_id",
            "usenix_export_link",
        }
    )
    if not deterministic or candidate.source_priority < 90:
        return False
    title_values = []
    for item in evidence:
        if item.startswith("title_similarity="):
            try:
                title_values.append(float(item.split("=", 1)[1]))
            except ValueError:
                pass
    return (
        bool(title_values)
        and max(title_values) >= 98.0
        and "year_match" in evidence
        and "first_author_match" in evidence
    )


def _source_family(candidate: BibCandidate) -> str:
    return candidate.source_family or infer_source_family(candidate.source, candidate.source_url)


def detect_bundle_ambiguities(candidates: list[BibCandidate]) -> list[str]:
    """Report multi-entry exports that contain more than one plausible target.

    A single publisher response is not independent corroboration. When two or
    more records from the same BibTeX bundle score as exact/high, automatic
    replacement must stop unless an identifier match has already reduced the
    plausible set to one record.
    """

    groups: dict[tuple[str, str | None], list[BibCandidate]] = {}
    for candidate in candidates:
        if not candidate.bibtex or candidate.confidence not in {"exact", "high"}:
            continue
        if not any(item.startswith("bibtex_bundle_size=") for item in candidate.evidence):
            continue
        groups.setdefault((candidate.source, candidate.source_url), []).append(candidate)

    messages: list[str] = []
    for (source, source_url), group in groups.items():
        if len(group) <= 1:
            continue
        # Exact DOI/arXiv matches should already be the sole plausible record;
        # if multiple remain, the export itself is ambiguous or duplicated.
        keys = [
            next((item.split("=", 1)[1] for item in candidate.evidence if item.startswith("exported_key=")), "")
            for candidate in group
        ]
        location = source_url or source
        messages.append(
            "ambiguous_bibtex_bundle: "
            f"{len(group)} plausible entries from {location}; exported keys={','.join(keys)}"
        )
    return messages


def detect_candidate_conflicts(candidates: list[BibCandidate]) -> list[str]:
    """Detect disagreement between independent sources, not within one bundle."""

    usable = [
        candidate
        for candidate in candidates
        if candidate.bibtex and candidate.confidence in {"exact", "high"}
    ]
    dois: dict[str, set[str]] = {}
    for candidate in usable:
        doi = normalize_doi(candidate.fields.get("doi", "")) if candidate.fields.get("doi") else ""
        if doi:
            dois.setdefault(doi, set()).add(_source_family(candidate))
    source_families = set().union(*dois.values()) if dois else set()
    if len(dois) > 1 and len(source_families) > 1:
        return ["candidate_doi_conflict: " + ", ".join(sorted(dois))]

    signatures: dict[tuple[str, str, str], set[str]] = {}
    for candidate in usable:
        signature = (
            norm_text(candidate.fields.get("title", "")),
            normalize_year(candidate.fields.get("year", "")),
            first_author_last_name(candidate.fields.get("author", "")),
        )
        if signature[0]:
            signatures.setdefault(signature, set()).add(_source_family(candidate))
    signature_families = set().union(*signatures.values()) if signatures else set()
    if len(signatures) > 1 and not dois and len(signature_families) > 1:
        return ["candidate_metadata_conflict: structured sources returned different title/year/author records"]
    return []

def corroborate_candidates(candidates: list[BibCandidate]) -> None:
    groups: dict[str, list[BibCandidate]] = {}
    for candidate in candidates:
        if not candidate.bibtex or candidate.confidence != "high":
            continue
        doi = normalize_doi(candidate.fields.get("doi", "")) if candidate.fields.get("doi") else ""
        if doi:
            groups.setdefault(doi, []).append(candidate)
    for _, group in groups.items():
        families = {_source_family(candidate) for candidate in group}
        if len(families) >= 2:
            for candidate in group:
                candidate.confidence = "exact"
                candidate.evidence.append(
                    "corroborated_by_independent_sources=" + ",".join(sorted(families))
                )


def _candidate_is_preprint(candidate: BibCandidate) -> bool:
    family = _source_family(candidate).lower()
    url = (candidate.source_url or "").lower()
    doi = normalize_doi(candidate.fields.get("doi", "")) if candidate.fields.get("doi") else ""
    journal = norm_text(candidate.fields.get("journal", ""))
    archive = norm_text(
        candidate.fields.get("archiveprefix", "")
        or candidate.fields.get("eprinttype", "")
    )
    return bool(
        family == "arxiv"
        or "arxiv.org" in url
        or "/journals/corr/" in url
        or doi.startswith("10.48550/arxiv.")
        or journal == "corr"
        or archive == "arxiv"
    )


def _supplemental_candidates_for(
    selected: BibCandidate,
    candidates: list[BibCandidate],
) -> list[BibCandidate]:
    selected_doi = normalize_doi(selected.fields.get("doi", "")) if selected.fields.get("doi") else ""
    selected_signature = (
        norm_text(selected.fields.get("title", "")),
        normalize_year(selected.fields.get("year", "")),
        first_author_last_name(selected.fields.get("author", "")),
    )
    result: list[BibCandidate] = []
    for candidate in candidates:
        if candidate is selected or not candidate.bibtex:
            continue
        if candidate.confidence not in {"exact", "high"}:
            continue
        # Registry-generated BibTeX is evidence-only. It must never supply
        # month, series, author rendering, titles, or any other output field.
        if candidate.source_kind in {"probe", "registry_transform"}:
            continue
        # Once a formal conference/journal record is selected, a matching
        # arXiv/CoRR record must not inject preprint DOI, journal, volume,
        # eprint, or publisher fields into the formal citation.
        if not _candidate_is_preprint(selected) and _candidate_is_preprint(candidate):
            continue
        candidate_doi = normalize_doi(candidate.fields.get("doi", "")) if candidate.fields.get("doi") else ""
        if selected_doi:
            if candidate_doi != selected_doi:
                continue
        else:
            signature = (
                norm_text(candidate.fields.get("title", "")),
                normalize_year(candidate.fields.get("year", "")),
                first_author_last_name(candidate.fields.get("author", "")),
            )
            if not selected_signature[0] or signature != selected_signature:
                continue
        result.append(candidate)
    return result


def _publisher_probe_diagnostics(candidates: list[BibCandidate]) -> list[str]:
    messages: list[str] = []
    seen: set[tuple[str, str]] = set()
    for candidate in candidates:
        if candidate.source_kind != "probe":
            continue
        status = ""
        if "blocked_or_challenge_page" in candidate.evidence:
            status = "blocked"
        else:
            http_status = next(
                (item.split("=", 1)[1] for item in candidate.evidence if item.startswith("http_status=")),
                "",
            )
            if http_status:
                status = f"http_{http_status}"
        if not status:
            continue
        family = _source_family(candidate)
        key = (family, status)
        if key in seen:
            continue
        seen.add(key)
        messages.append(f"publisher_page_status[{family}]={status}")
    return messages


def _has_family_native_export(candidates: list[BibCandidate], family: str) -> bool:
    return any(
        candidate.bibtex
        and candidate.source_kind == "publisher_native_export"
        and _source_family(candidate) == family
        and candidate.confidence in {"exact", "high"}
        for candidate in candidates
    )


def _only_preprint_strong_candidates(candidates: list[BibCandidate]) -> bool:
    strong = [
        candidate
        for candidate in candidates
        if candidate.bibtex and candidate.confidence in {"exact", "high"}
        and candidate.source_kind != "registry_transform"
    ]
    return bool(strong) and all(_candidate_is_preprint(candidate) for candidate in strong)


def _has_strong_candidate(candidates: list[BibCandidate]) -> bool:
    return any(
        candidate.bibtex and candidate.confidence in {"exact", "high"}
        for candidate in candidates
    )


def _candidate_dois(candidates: list[BibCandidate], original_doi: str | None) -> list[str]:
    found: list[str] = []
    for candidate in sorted(candidates, key=lambda c: (c.score, c.source_priority), reverse=True):
        if candidate.confidence not in {"exact", "high"}:
            continue
        value = candidate.fields.get("doi", "")
        if not value:
            continue
        doi = normalize_doi(value)
        if doi and doi != original_doi and doi not in found:
            found.append(doi)
    return found


def _candidate_urls(candidates: list[BibCandidate], original_urls: tuple[str, ...]) -> list[str]:
    found: list[str] = []
    excluded = set(original_urls)
    for candidate in sorted(candidates, key=lambda c: (c.score, c.source_priority), reverse=True):
        values = [candidate.fields.get("url", ""), candidate.source_url or ""]
        for value in values:
            if not value or not value.startswith(("http://", "https://")):
                continue
            if value in excluded or value in found or _is_api_or_index_url(value):
                continue
            found.append(value)
    return found


def _is_api_or_index_url(url: str) -> bool:
    parsed = urlparse(url)
    host = parsed.netloc.lower()
    path = parsed.path.lower()
    if host in {
        "api.crossref.org",
        "api.openalex.org",
        "api.semanticscholar.org",
        "export.arxiv.org",
        "www.googleapis.com",
    }:
        return True
    if "dblp.org" in host and ("/search/" in path or path.endswith(".bib")):
        return True
    if "openlibrary.org" in host and (path.startswith("/api/") or path.endswith("search.json")):
        return True
    return False
