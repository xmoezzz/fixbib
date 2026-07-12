# FixBib

**Verify, repair, and audit LLM-hallucinated BibTeX citations against authoritative scholarly sources.**

FixBib is a command-line tool for checking BibTeX bibliographies against publisher metadata, scholarly repositories, bibliographic indexes, and DOI registries. It treats every local citation as unverified input, preserves existing citation keys, repairs metadata field by field, prefers formal publications over preprints, detects identifier conflicts, and produces a detailed JSON audit for every decision.

```bash
fixbib references.bib
```

## Features

- Verifies every bibliography entry, including entries that already contain a DOI or URL.
- Detects citations whose DOI resolves to a different title, author list, or publication.
- Preserves citation keys so existing LaTeX `\cite{...}` commands remain valid.
- Prefers formal conference and journal versions over corresponding preprints.
- Queries publisher exporters, repositories, bibliographic indexes, and DOI registries.
- Repairs entries field by field without deleting useful local metadata.
- Adds verified metadata such as DOI, URL, ISBN, ISSN, abstract, keywords, pages, article number, venue, and publisher.
- Normalizes page ranges, removes empty fields, and corrects common BibTeX field-mapping errors.
- Caches DOI-based source responses under `~/.bibfix_cache`.
- Supports a fully uncached run through `--no-cache`.
- Writes a field-level JSON audit containing queried sources, candidates, conflicts, preserved fields, and applied changes.

## Requirements

- Python 3.10 or later
- Internet access for online metadata verification

## Installation

### Install from source

```bash
git clone <repository-url>
cd fixbib
python3 -m pip install .
```

Verify the installation:

```bash
fixbib --version
```

### Install with pipx

```bash
git clone <repository-url>
cd fixbib
pipx install .
```

### Install a wheel

```bash
python3 -m pip install fixbib-<version>-py3-none-any.whl
```

To replace an existing installation:

```bash
python3 -m pip install --force-reinstall \
  fixbib-<version>-py3-none-any.whl
```

### Development installation

```bash
python3 -m pip install -e '.[dev]'
```

Run the test suite:

```bash
python3 -m pytest -q
```

## Quick start

Run FixBib on a bibliography:

```bash
fixbib references.bib
```

The input file is left unchanged. FixBib writes:

```text
references.fixed.bib
references.fixbib.json
```

- `references.fixed.bib` contains the repaired bibliography.
- `references.fixbib.json` contains the complete verification and repair audit.

Choose a custom output path:

```bash
fixbib references.bib -o checked-references.bib
```

## Update a file in place

```bash
fixbib references.bib --in-place
```

FixBib creates a backup before replacing the input:

```text
references.bib
references.bib.bak
references.fixbib.json
```

## Cache

FixBib stores reusable DOI-source responses in:

```text
~/.bibfix_cache
```

The cache stores remote metadata candidates, not completed local BibTeX entries. A cache hit still triggers comparison against the current title, authors, year, DOI, ISBN, and publication type. Candidate confidence and identifier conflicts are recalculated for the current entry.

Force a completely fresh run:

```bash
fixbib references.bib --no-cache
```

`--no-cache` disables both cache reads and cache writes.

## Automatic update modes

The default policy is:

```bash
fixbib references.bib --auto verified
```

Available modes:

```bash
fixbib references.bib --auto none
fixbib references.bib --auto exact
fixbib references.bib --auto verified
fixbib references.bib --auto high
```

- `none`: query and audit entries without changing the BibTeX output.
- `exact`: apply only identifier-backed updates.
- `verified`: apply updates supported by sufficiently strong bibliographic evidence.
- `high`: permit additional high-confidence bibliographic matches.

Conflicting, ambiguous, malformed, duplicate-key, and non-academic records are not automatically replaced.

## Local inspection

Inspect a bibliography without network access:

```bash
fixbib references.bib --inspect
```

This can identify issues such as:

- malformed BibTeX;
- duplicate citation keys;
- duplicate identifiers;
- missing DOI or URL fields;
- empty fields;
- unsupported entry structures.

## Dry run

Perform verification without writing the repaired `.bib` file:

```bash
fixbib references.bib --dry-run
```

## Detailed terminal output

Show every entry in the result table:

```bash
fixbib references.bib --table all
```

Disable the JSON audit:

```bash
fixbib references.bib --no-report
```

## Thorough mode

Normal runs execute all applicable DOI, publisher, repository, page, bibliographic-index, and registry checks.

For additional fallback passes:

```bash
fixbib references.bib --thorough
```

A completely fresh thorough run can be requested with:

```bash
fixbib references.bib --thorough --no-cache
```

## Verification process

For each entry, FixBib runs the applicable stages of the following pipeline:

1. Parse the local BibTeX entry.
2. Extract DOI, URL, ISBN, arXiv identifier, title, authors, year, and venue.
3. Resolve DOI landing pages.
4. Query exact-identifier metadata sources.
5. Query supported publisher-native citation exporters.
6. Inspect URLs already present in the entry.
7. Search book and proceedings metadata where applicable.
8. Search bibliographic indexes such as DBLP.
9. Search DOI registries such as Crossref.
10. Recheck identifiers discovered during search.
11. Inspect discovered publisher and repository pages.
12. Compare all candidates against the local entry.
13. Select the strongest compatible source.
14. Merge verified fields without deleting useful local metadata.
15. Write the repaired BibTeX and JSON audit.

## Source authority

FixBib distinguishes between:

1. Publisher-native citation export
2. Repository-native citation export
3. Publisher-page metadata
4. Bibliographic-index export
5. Structured registry metadata
6. Bibliographic index
7. Registry-generated BibTeX
8. Page probes and discovery evidence

Publisher and repository sources may provide rich metadata such as abstracts, keywords, ISBN, ISSN, page count, article number, venue location, series, and publisher address.

Registry-generated BibTeX is treated as evidence only. It is not allowed to overwrite fields merely because it was generated from a valid DOI.

## Identifier conflict detection

FixBib does not assume that an existing DOI is correct.

A DOI may resolve successfully while belonging to another publication. FixBib compares the resolved candidate against:

- title similarity;
- first author;
- complete author list;
- publication year;
- publication type;
- ISBN and other available identifiers.

When a DOI resolves to metadata that conflicts with the local title and authors, FixBib reports an identifier conflict instead of replacing the entry with unrelated metadata. It may then continue searching by title, author, and year to locate the intended publication.

## Formal publications and preprints

FixBib attempts to distinguish between formal publications and preprints.

When a formal conference or journal version is available, it is preferred over the corresponding arXiv or CoRR record. Preprint metadata is not allowed to add redundant fields such as `journal = {CoRR}`, `volume = {abs/...}`, or an arXiv DataCite DOI to a verified formal publication.

For an arXiv-only entry, FixBib preserves appropriate fields such as:

```bibtex
@misc{example,
  eprint = {2501.01234},
  archiveprefix = {arXiv},
  primaryclass = {cs.SE},
  url = {https://arxiv.org/abs/2501.01234},
}
```

## Field-level repair

FixBib merges metadata field by field rather than replacing a complete entry blindly.

The merge process follows these rules:

- Citation keys are always preserved.
- Remote omission does not delete a useful local field.
- Empty fields are removed.
- DOI URLs are normalized to `https://doi.org/...`.
- Page ranges are normalized to BibTeX double-hyphen form.
- Equivalent title capitalization does not force replacement.
- Equivalent author formats are treated as the same author list.
- Rich fields are imported only from suitable authoritative sources.
- Article numbers are stored in `articleno`, not `number`.
- Issue identifiers remain in `number`.
- Exporter bookkeeping fields such as `timestamp`, `biburl`, `bibsource`, and `collection` are ignored.

## Audit report

The generated JSON report records the decision process for each entry, including:

- queried sources;
- candidates considered;
- selected source and confidence;
- title, author, and year agreement;
- DOI and identifier conflicts;
- cache hits and misses;
- publisher-page and HTTP failures;
- fields added or updated;
- local fields preserved;
- remote fields rejected;
- ambiguous matches;
- parser diagnostics.

The report also contains cache statistics:

```json
{
  "cache": {
    "enabled": true,
    "directory": "/home/user/.bibfix_cache",
    "hits": 120,
    "misses": 40,
    "writes": 35,
    "read_errors": 0,
    "write_errors": 0
  }
}
```

## Optional sources

Some optional sources require API keys:

```bash
export OPENALEX_API_KEY='...'
export GOOGLE_BOOKS_API_KEY='...'
export SEMANTIC_SCHOLAR_API_KEY='...'
```

Enable Semantic Scholar queries with:

```bash
fixbib references.bib --semantic-scholar
```

Google Scholar is not queried automatically because it does not provide a stable public metadata API suitable for this workflow.

## Supported source families

Depending on the publication and available identifiers, FixBib may query or inspect sources including:

- ACM Digital Library
- IEEE Xplore
- Springer
- USENIX
- ACL Anthology
- arXiv
- OpenReview
- DBLP
- Crossref
- DataCite
- Open Library
- Google Books
- OpenAlex
- Semantic Scholar
- publisher and repository publication pages

Not every source is queried for every entry. FixBib selects applicable resolvers according to the DOI, URL, entry type, and publication metadata.

## Limitations

Bibliographic databases and publisher websites are not always consistent.

Possible limitations include:

- publisher websites blocking automated requests;
- missing metadata in older proceedings;
- disagreement between Crossref, DBLP, and publisher records;
- inconsistent online-first and issue publication dates;
- publications without stable identifiers;
- incomplete book and workshop metadata;
- dynamically generated citation endpoints;
- temporary network or API failures.

FixBib prefers to report a conflict or unresolved entry rather than apply an unsupported correction.

## Development

Install development dependencies:

```bash
python3 -m pip install -e '.[dev]'
```

Run all tests:

```bash
python3 -m pytest -q
```

Run a specific test module:

```bash
python3 -m pytest tests/test_cache.py -q
```

## License

See the repository license file for details.
