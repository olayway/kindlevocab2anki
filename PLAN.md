# Kindle → Anki: sense-aware rebuild

## Goal
Move card identity from **stem** to **sense/expression**, so one Kindle lemma can
yield multiple cards — a distinct card per meaning (polysemy) and per phrasal
verb / expression (which Kindle can't look up directly). Claude reads each
lookup's context, promotes single-word lookups to expression headwords when
warranted, clusters same-sense lookups, and dedups against cards that already
exist.

## Locked decisions
| # | Decision | Choice |
|---|---|---|
| 1 | Polysemy | Sense-split: distinct meanings → distinct cards |
| 2 | Dedup mechanism | Claude judges each new lookup against that stem's existing cards |
| 3 | Cross-run tracking | Anki `Lookups` field = source of truth for cards; `skipped.json` for junk only |
| 4 | Stem index | Hidden `Stem` field; clean rebuild, no migration |
| 5 | Blank-out | Claude returns the exact surface span to blank |
| 6 | Request shape | Batched multi-stem calls (~40 stems/call) |
| 7 | Anki dup guard | `allowDuplicate: True` — our pipeline owns dedup |

## Note type (clean rebuild)
Fields: `["Stem", "Word", "Polish", "Definition", "Sentence", "Source", "LookupDate", "Lookups"]`

- `Stem` — origin lemma ("make"); hidden index, not on templates.
- `Word` — the card's headword/answer ("make off", or "bank"); shown on back.
- `Lookups` — comma-joined `LOOKUPS.id`s that produced this card; hidden provenance + tracking key.
- `Polish` / `Definition` / `Sentence` / `Source` / `LookupDate` — as today.
- Templates unchanged in spirit: **front** = definition + blanked sentence; **back** = `+ Word + Polish + Source·Date`. `Stem` and `Lookups` never rendered.

## Pipeline flow

**1. Read (`read_lookups`)**
Add `id` (= `LOOKUPS.id`) to the `Lookup` dataclass. **Delete `first_per_stem`** — every lookup flows through, ordered by timestamp. All contexts survive.

**2. Filter**
Stopword filter and `--book` apply at stem level, unchanged (so "the" ×10 is dropped before Claude).

**3. Determine new lookups (scoped, deck-size-independent)**
- Collect the stems present in the surviving lookups.
- Query Anki, chunked ~100 stems/call:
  `findNotes deck:English::Kindle (Stem:s1 OR Stem:s2 OR …)` → `notesInfo`.
- From that one pull build both:
  - `consumed: set[LOOKUPS.id]` — union of every card's `Lookups` field.
  - `existing: dict[stem → list[{headword, definition}]]` — for dedup context.
- Also load `skipped.json` (`set[LOOKUPS.id]`).
- **New lookups** = lookups whose id ∉ `consumed` and ∉ `skipped`.
- `--reset` flag: delete all deck notes + clear `skipped.json`, then everything is new.

**4. Group new lookups by stem.**

**5. Batch ~40 stem-groups per Claude call.** Input per group:
```json
{ "stem": "...",
  "contexts": [{"lookup_id": "...", "sentence": "...", "book": "...", "timestamp": 0}],
  "existing": [{"index": 0, "headword": "...", "definition": "..."}] }
```

**6. Claude returns per group:**
```json
{ "stem": "...",
  "new_cards": [{"headword": "...", "definition": "...", "polish": "...", "span": "..."}],
  "assignments": [{"lookup_id": "...", "verdict": "new|existing|junk",
                   "card_index": 0, "reason": "..."}] }
```
- `new` → `card_index` into `new_cards`; two lookups sharing an index = one shared card, distinct indices = own cards.
- `existing` → `card_index` into `existing`; no new note, lookup is now consumed by that card.
- `junk` → proper noun/typo/OCR; goes to `skipped.json` with `reason`.

**7. Build notes** — one per `new_card`:
- `Stem`=lemma, `Word`=headword, `Definition`, `Polish`.
- `Sentence`=`blank_out(primary.sentence, span)`; `blank_out` first tries Claude's `span`, falls back to word/stem.
- `Source`/`LookupDate` from **primary = earliest-timestamp lookup** assigned to the card.
- `Lookups` = comma-joined ids of **all** lookups assigned to the card.
- `addNotes(..., allowDuplicate=True)`.

**8. Record outcomes**
- `existing` verdicts: append their lookup id to the matched card's `Lookups` field via `updateNoteFields` (keeps provenance complete and prevents re-processing).
- `junk` verdicts: add ids to `skipped.json`.
- `new` verdicts: already recorded in step 7's `Lookups`.
- Save `skipped.json`.

## Removed
`first_per_stem`, `existing_words`, `processed.json`, `--backfill-polish`, `TRANSLATE_PROMPT`, `TRANSLATE_SCHEMA`, and the line-707 `known` union.

## Prompt rewrite
`SYSTEM_PROMPT` + `RESPONSE_SCHEMA` become the nested clustering task: for each
stem-group, (a) decide per lookup whether the sense is single-word or part of an
expression and set `headword` accordingly, (b) cluster same-sense new lookups
into shared cards, (c) match against `existing` (→ `existing`), (d) reject
non-words (→ `junk`), (e) return the verbatim `span` to blank. Definition/Polish
rules carry over unchanged.

## Accepted trade-offs
- Shared-across-books card shows only the primary book as `Source`.
- Cross-run sense matching uses `headword + definition` only (add example sentence later if it mis-matches).
- Rare Claude nondeterminism could mint a near-dup on a later run; no guard beyond the match step.

## Self-heal / reset behavior
Delete or edit a card in Anki → its lookups auto-reprocess next run (they leave
`consumed`). `--reset` wipes the deck and `skipped.json` for a full rebuild.
