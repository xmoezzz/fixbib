# Publisher sample capture analysis

## Capture outcome

- Samples: **14**
- Direct BibTeX captured: **3** (Springer chapter, ACL Anthology, arXiv)
- Recoverable/partially usable pages: **4** (Springer article, USENIX, NDSS, RCSI/OJS)
- Blocked/challenge pages: **7** (2 ACM, IEEE, Elsevier, OpenReview, MDPI, SIAM)

| Source | Identifier | Outcome | Evidence | Adapter decision |
|---|---|---|---|---|
| ACM conference | 10.1145/3324884.3416645 | blocked | 403 + Cloudflare “Just a moment…” | Use DOI/Crossref/DBLP; do not scrape ACM DL |
| ACM journal | 10.1145/3660817 | blocked | 403 + Cloudflare “Just a moment…” | Use DOI/Crossref/DBLP; do not scrape ACM DL |
| IEEE | 10.1109/SP61157.2025.00190 | blocked | IEEE “Unable to Load Page”; browser response 202/418 | Use DOI/Crossref/DBLP or IEEE metadata API |
| Springer chapter | 10.1007/978-3-540-78800-3_24 | success | Direct citation-needed.springer.com BibTeX response captured | Implement deterministic Springer DOI exporter |
| Springer article | 10.1007/s10958-024-07424-2 | partial | Full metadata present; citation-needed links visible, but no BibTeX captured | Derive/fetch BibTeX endpoint from DOI, then verify |
| Elsevier/ScienceDirect | 10.1016/j.jss.2022.111269 | blocked | Redirect succeeds, ScienceDirect returns 403 | Use DOI/Crossref; publisher page only fallback |
| USENIX | AddressSanitizer | recoverable | BibTeX is inline; direct /biblio/export/bibtex/180957 link visible | Fix href-based capture; no JS required |
| ACL Anthology | 2024.findings-acl.625 | success | Complete BibTeX already in initial HTML | Static HTML adapter; preserve original key on replacement |
| NDSS | Large Language Model guided Protocol Fuzzing | partial | Paper page accessible; authors/title in body; no scholarly BibTeX metadata | Use DOI/Crossref first; page parser only fallback |
| OpenReview | Q3qAsZAEZw | blocked | Redirected to browser verification challenge | Use OpenReview API, not browser scraping |
| arXiv | 2512.21238 | success | XHR https://arxiv.org/bibtex/2512.21238 captured | Direct arXiv BibTeX endpoint |
| MDPI | 10.3390/electronics13132657 | blocked | 403 Access Denied | Use DOI/Crossref; avoid publisher scraping |
| SIAM | 10.1137/0201010 | blocked | 403 + Cloudflare challenge | Use DOI/Crossref; avoid publisher scraping |
| RCSI/OJS | 176654 | recoverable | Highwire citation_* metadata and captureCite URL present | OJS adapter or construct canonical BibTeX from metadata |

## Confirmed deterministic export paths

### Springer chapter
```text
https://citation-needed.springer.com/v2/references/<DOI>?format=bibtex&flavour=citation
```
The sample returned a complete `@InProceedings` entry.

### arXiv
```text
https://arxiv.org/bibtex/<ARXIV_ID>
```
The sample returned the same BibTeX captured from the rendered page.

### USENIX
```text
https://www.usenix.org/biblio/export/bibtex/<NODE_ID>
```
The direct export link was present in the page. The BibTeX was also already inline.

### ACL Anthology
The complete BibTeX is embedded in the initial article HTML; JavaScript is not required.

## Required crawler fixes

1. Replace `saved` with semantic statuses: `bibtex_captured`, `metadata_only`, `blocked`, `challenge`, `failed`.
2. Remove the generic click on text exactly matching `Download`; it downloaded a Springer PDF and targeted an RCSI XML link.
3. Match citation links by both link text and `href`; USENIX uses text `Download` with `href` containing `/bibtex/`.
4. Deduplicate BibTeX by normalized content hash; ACL and Springer were saved twice.
5. Detect anti-bot pages from status/title/body before attempting citation interaction.
6. Prefer direct API/export adapters; launch Playwright only when structured metadata and deterministic endpoints fail.
7. Preserve the original BibTeX key after parsing the exported entry.

## Resolver order implied by the samples

```text
DOI content negotiation / Crossref
→ DBLP for computer-science publications
→ source-specific deterministic exporter
   Springer / arXiv / USENIX / ACL / OpenReview API
→ citation_* and JSON-LD metadata
→ browser capture as the final fallback
```
