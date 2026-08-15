# Kindle Vocabulary → Anki

Turns the words you look up while reading on a Kindle into Anki flashcards —
one card per **sense**, not per word. Claude reads the sentence each lookup
appeared in, splits a word's distinct meanings into separate cards, promotes a
lookup to the phrasal verb or expression it really belongs to, and merges
repeat lookups of the same sense.

You see a definition and the sentence with the answer blanked out; you recall
the word. The back confirms it with a Polish translation of that same sense.

```
┌─────────────────────────────────────────────────────────┐
│  to cause suffering or trouble to; to distress          │   front
│  physically or mentally                                 │
│                                                         │
│  There has been a revolution in medicine concerning     │
│  how we think about the diseases that now _____ us.     │
├─────────────────────────────────────────────────────────┤
│  afflict                                                │   back
│  dotykać, trapić, nękać                                 │
│  Why Zebras Don't Get Ulcers — Sapolsky · 2026-06-09    │
└─────────────────────────────────────────────────────────┘
```

The Polish is deliberately on the **back only**. Putting it on the front would
turn recall into translation; here it just confirms you landed on the right
sense once you've already committed to an answer.

## Sense-aware, not word-aware

Kindle records one *lookup* per tap — the lemma, the surface form, and the
sentence. This tool does not collapse those to one card per lemma. Instead
Claude clusters them:

- **Polysemy → sense split.** `bank` met at a river and `bank` met downtown
  become **two** cards, each with its own definition, sentence, and Polish. This
  holds against cards **already in the deck** too: if you've carded `sordid` as
  "morally wrong" and later look it up meaning "squalid", that's a new card, not
  a match to the old one.
- **Base-form headwords.** The word to recall is stored in its dictionary form —
  verbs as the bare infinitive ("outdid" → **outdo**), nouns singular — never
  the inflected shape it happened to wear in the sentence.
- **Expression promotion.** A tap on `make` inside "they *made off* with it"
  becomes a card whose headword is **make off** — the expression Kindle can't
  look up directly.
- **Same sense → one card.** Three lookups of the same meaning share a single
  card; the earliest sentence is the one shown, and every lookup is recorded on
  the card as provenance.
- **Junk is dropped.** Proper nouns, names, foreign words, and OCR artefacts
  are rejected and remembered so they aren't re-judged next run.

## Requirements

| | |
|---|---|
| **uv** | `brew install uv` — runs the script and its dependencies; nothing to install manually |
| **Anki** | running, with the [AnkiConnect](https://ankiweb.net/shared/info/2055492159) add-on, listening on `127.0.0.1:8765` |
| **Anthropic API key** | in `.env` as `ANTHROPIC_API_KEY=...` (mode `600`, git-ignored) |
| **Kindle** | connected by USB and mounted at `/Volumes/Kindle`, at least once |

## Quick start

```sh
# 1. See what would be imported — no Claude calls, nothing written
uv run kindle_anki.py

# 2. Try a handful of real cards first
uv run kindle_anki.py --apply --limit 10

# 3. Import the rest
uv run kindle_anki.py --apply
```

The dry run is the default on purpose: it costs nothing on the Claude side and
shows you the per-book breakdown before you commit. (It does query Anki to work
out what's already handled, so Anki must be running even for a dry run.)

## Options

| Flag | Effect |
|---|---|
| *(none)* | Dry run. Prints the breakdown and sample cards. No Claude calls, no writes. |
| `--apply` | Cluster with Claude and add notes to Anki. |
| `--reset` | Delete every note in the deck and clear `skipped.json`, then reimport everything from scratch. With `--apply` it actually deletes; without, it just says what it would do. |
| `--limit N` | Process at most N new lookups this run. Use it to sample cost and quality. |
| `--book SUB` | Only lookups from books whose title contains `SUB` (case-insensitive). Repeatable; matches any. |
| `--db PATH` | Read a specific `vocab.db` instead of auto-detecting. |
| `--model ID` | Claude model (default `claude-opus-5`). |
| `--batch-size N` | Stem-groups per API request (default 40). |

```sh
uv run kindle_anki.py --book "1984" --book "Zebras" --apply
```

## How it works

```
Kindle vocab.db ──copy──▶ ./vocab.db ──▶ every en lookup (with its id)
                                              │
                        book filter ──────────┤
                                              ▼
                 query Anki by Stem ──▶ consumed ids + existing cards
                                              │
              drop consumed + skipped ────────┤
                                              ▼
                            stem-groups (batches of 40) ──▶ Claude
                                              │  new / existing / junk
                                              ▼
                            AnkiConnect ──▶ English::Kindle
```

**1. Get the database.** `vocab.db` is copied off the Kindle into the project
directory before anything reads it — SQLite on a removable FAT volume shouldn't
be opened in place. The copy doubles as a cache, so you can re-run with the
Kindle unplugged.

**2. Read every lookup.** Each `LOOKUPS` row becomes a `Lookup` carrying its
`id`, lemma (`stem`), surface form, sentence, book, and timestamp. Nothing is
collapsed — all the contexts survive so Claude can see the different senses.

**3. Filter.** The `--book` filter applies at the lookup level.

**4. Work out what's new — before spending any money.** The deck is queried by
`Stem`, chunked ~100 stems per call. From that one pull the tool builds the set
of **consumed** lookup ids (the union of every card's hidden `Lookups` field)
and the **existing** cards per stem (for dedup context). A lookup is *new* only
if its id is neither consumed nor in `skipped.json`.

**5. Cluster.** New lookups are grouped by stem and sent to Claude in batches.
For each group Claude returns new cards (headword, definition, Polish, and the
exact span to blank) plus a verdict for every lookup: **new** (maps to one of
the new cards), **existing** (same sense as a card already in the deck), or
**junk**. Structured outputs pin the response to a schema so it can't come back
malformed. The system prompt is marked for prompt caching.

**6. Write to Anki.** One note per new card, with `allowDuplicate: true` — this
pipeline owns dedup, so Anki's own duplicate guard is turned off. `existing`
verdicts append the lookup id to the matched card's `Lookups`; `junk` verdicts
go to `skipped.json`.

## What gets created in Anki

On the first `--apply` the script creates, if missing:

- **Deck** `English::Kindle`
- **Note type** `Kindle Vocab` with fields
  `Stem`, `Word`, `Polish`, `Definition`, `Sentence`, `Source`, `LookupDate`,
  `Lookups`, and a single **Production** card template (definition + blanked
  sentence on the front; word, Polish, and source on the back).

`Stem` and `Lookups` are hidden bookkeeping fields — never rendered on a card.
`Stem` is the lemma index used to find a stem's cards quickly; `Lookups` is the
comma-joined list of the `LOOKUPS.id`s that produced or were absorbed by the
card, and is the source of truth for what's already handled.

Each note is tagged `kindle` plus `book::<slug>`, e.g.
`book::why-zebras-don-t-get-ulcers`, so you get a per-book tag tree in the
browser.

### Blanking

Claude returns the exact surface span(s) to hide — a **list** of substrings, so
a `make off` card blanks the whole expression, not just `make`. A separable
phrasal verb split by its object ("she *tied* her hair *up*") gives each piece
separately, `["tied", "up"]`, so the object between them stays visible. Every
piece is matched verbatim; if any doesn't occur the tool falls back to the
inflected form and then the lemma, and if none match it leaves the sentence
intact rather than mangle it.

## State and re-runs

The script is safe to run repeatedly; it only ever adds what's missing.

**The `Lookups` field is the source of truth.** Because every processed lookup
id is recorded on the card it produced (or the card that absorbed it), re-runs
cost nothing for lookups you already have — and the pipeline **self-heals**:
delete or edit a card in Anki and its lookup ids leave the consumed set, so
they're reprocessed on the next run.

**`skipped.json`** (git-ignored) is the only local state, and holds junk only:

```json
{ "a1b2c3": "proper noun", "d4e5f6": "ocr artefact" }
```

Keyed by `LOOKUPS.id` → reason, so junk isn't paid to be re-judged every run.
It's rewritten after every batch, so an interrupted run resumes cleanly.
`--reset` clears it (and the deck) for a full rebuild.

## Cost

Rough order of magnitude for ~400 lookups at `claude-opus-5` prices: **a couple
of dollars**, in about ten requests. Run `--apply --limit 40` first and check
the actual spend in the Anthropic console before doing the rest. Cheaper:
`--model claude-sonnet-5`, or a smaller `--batch-size` if you see truncation.

## Tests

Pure logic, the Anki HTTP boundary, and the filesystem are covered by fast
offline tests; the Claude clustering step has a few real-API behavioral evals
that are **deselected by default** (they cost money):

```sh
# fast, free, offline — pure logic + mocked Anki + real sqlite/tmp files
uv run --with pytest pytest

# behavioral evals against the real API (cheap model), opt-in
uv run --with pytest --with anthropic pytest -m llm
```

The evals use `claude-haiku-4-5` and assert loose properties so they survive
model nondeterminism while still catching a prompt or schema regression: a
proper noun is `junk`, two senses split into two cards with verbatim spans, a
different sense of a word already in the deck becomes a new card, an inflected
verb's headword comes back as the infinitive, and a phrasal verb is promoted to
its expression headword.

## Troubleshooting

**`Cannot reach AnkiConnect at http://127.0.0.1:8765`**
Anki isn't running, or AnkiConnect isn't installed. Anki must stay open for the
whole run — including dry runs, which query it for what's already handled.

**`No vocab.db found`**
Plug the Kindle in and confirm it mounts at `/Volumes/Kindle`, or pass
`--db PATH` to a copy you already have.

**`ANTHROPIC_API_KEY is not set`**
Add it to `.env` in this directory as `ANTHROPIC_API_KEY=sk-ant-...`.

**A card came out wrong, or a word was mis-clustered**
Delete the card in Anki. Its lookup ids leave the consumed set and get
reprocessed on the next `--apply`. To rebuild the whole deck, use `--reset`.

## Files

| | |
|---|---|
| `kindle_anki.py` | the whole tool — one file, PEP 723 inline dependencies |
| `tests/` | pytest suite (`pytest.ini` sets it up; `-m llm` for the API evals) |
| `.env` | `ANTHROPIC_API_KEY` (git-ignored, mode 600) |
| `vocab.db` | cached copy of the Kindle database (git-ignored) |
| `skipped.json` | junk lookup ids → reason (git-ignored) |

## A note on backups

Anki keeps automatic backups in
`~/Library/Application Support/Anki2/<profile>/backups/` and logs deleted notes
to `deleted.txt` in the same folder. If an import goes wrong — especially after
`--reset` — restore with **File → Import** on the newest `.colpkg` from before
the mistake. Worth knowing before you bulk-edit a few hundred new cards.
