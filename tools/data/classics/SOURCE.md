# Classics source policy

This directory stores source manifests, locked source snapshots, reviewed entries,
and generated audit data for classical-text vocabulary.

The runtime dictionaries must only be generated from reviewed `entries.tsv` rows.
A source with a missing revision, license, or SHA-256 is intentionally not eligible
for import.

## Imported sources

- `wikisource-luoshen-fu` — 曹植《洛神赋》. Synchronized from
  `https://zh.wikisource.org/wiki/洛神賦` (canonical page; the simplified-title
  page redirects there), revision `7908816`, retrieved 2026-08-19. Base editions
  recorded on the page: 昭明文選（四部叢刊本）卷19 and 曹子建集（四部叢刊本）卷3,
  both scan-backed via `Page:` namespaces. Page quality marker `Textquality|50%`
  (proofread). Text is available under CC BY-SA 4.0
  (https://creativecommons.org/licenses/by-sa/4.0/); keep this attribution notice
  and the share-alike license when redistributing the derived dictionary rows.

Primary-source policy:

- Prefer Chinese Wikisource pages with an identified base edition and ProofreadPage
  evidence.
- Record the exact page revision, retrieval time, source URL, base text, license,
  and SHA-256 before importing.
- Use Chinese Text Project, the National Library of China, and publisher databases
  for comparison only unless redistribution permission is separately documented.
- `chinese-poetry` may discover candidate works but is not a textual edition.

Candidate policy:

- Keep source snapshots separate from runtime candidates.
- Allow only reviewed, punctuation-free simplified entries from two to ten Han
  characters; entries of nine or ten characters require explicit review.
- Preserve a source locator and reviewed pronunciation for every emitted entry.
- Do not generate sliding-window n-grams, whole poems, chapters, or paragraphs.

File-level licensing is pending until every imported source is verified. Do not
claim a redistribution license for a generated dictionary whose source manifest is
still marked `pending-verification`.
