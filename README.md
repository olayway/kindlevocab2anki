# Kindle Vocabulary to Anki

Turns the words you look up while reading on a Kindle into Anki flashcards — one card per **sense**, not per word. Claude reads the sentence each lookup appeared in, splits a word's distinct meanings into separate cards, builds cards for the phrasal verb or idiom a word belongs to, and merges repeat lookups of the same sense.

You see a definition and the sentence with the answer blanked out; you recall the word. The back confirms it — and, if you turn on translations, adds a
translation of that same sense into your native language.

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

Translations are **off by default** and configurable: pass `--language French` (or set `TRANSLATION_LANGUAGE` in `.env`) and cards get a translation into that language. It's deliberately on the **back only** — putting it on the front would turn recall into translation; here it just confirms you landed on the right sense once you've already committed to an answer. Leave it off and the cards stay fully monolingual.

## Sense-aware, not word-aware

Kindle records one _lookup_ per tap — the lemma, the surface form, and the sentence. This tool does not collapse those to one card per lemma. Instead Claude clusters them:

- **Polysemy → sense split.** `bank` met at a river and `bank` met downtown become **two** cards, each with its own definition, sentence, and (if enabled) translation. This holds against cards **already in the deck** too: if you've carded `sordid` as "morally wrong" and later look it up meaning "squalid", that's a new card, not a match to the old one.
- **Base-form headwords.** The word to recall is stored in its dictionary form — verbs as the bare infinitive ("outdid" → **outdo**), nouns singular — never the inflected shape it happened to wear in the sentence.
- **Expression promotion.** A tap on `make` inside "they _made off_ with it" becomes a card whose headword is **make off** — the expression Kindle can't look up directly.
- **Same sense → one card.** Three lookups of the same meaning share a single card; the earliest sentence is the one shown, and every lookup is recorded on the card as provenance.
- **Junk is dropped.** Proper nouns, names, foreign words, and OCR artefacts are rejected and remembered so they aren't re-judged next run.

## Requirements

|                       |                                                                                                                                                                                                                                           |
| --------------------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| **uv**                | `brew install uv` — runs the script and its dependencies; nothing to install manually                                                                                                                                                     |
| **Anki**              | running, with the [AnkiConnect](https://ankiweb.net/shared/info/2055492159) add-on, listening on `127.0.0.1:8765` — _or_ skip both and use `--export deck.apkg` to write a package you import by hand ([Offline export](#offline-export)) |
| **Anthropic API key** | in `.env` as `ANTHROPIC_API_KEY=...` (mode `600`, git-ignored)                                                                                                                                                                            |
| **Kindle**            | with **Vocabulary Builder** switched on, connected by USB and mounted at `/Volumes/Kindle` at least once                                                                                                                                  |

## First-time setup

Do these once, in order, before the quick start below.

1. **Install uv.** `brew install uv`. Nothing else to install — the script declares its own dependencies and uv fetches them on first run.
2. **Start Anki with AnkiConnect.** Install the [AnkiConnect](https://ankiweb.net/shared/info/2055492159) add-on (Tools → Add-ons → Get Add-ons → code `2055492159`, then restart Anki) and leave Anki **running** — it's needed even for the dry run.
3. **Add your API key.** Create `.env` in this directory with `ANTHROPIC_API_KEY=sk-ant-...` (only needed for `--apply`). Optionally add `TRANSLATION_LANGUAGE=Polish` for back-of-card translations.
4. **Connect the Kindle by USB** and confirm it mounts at `/Volumes/Kindle` in Finder. The first run copies `vocab.db` off it into this directory as a cache, so later runs work with the Kindle unplugged. If it never appears there, pass `--db PATH` to a copy you have.

## Quick start

With the Kindle plugged in (or a cached `vocab.db` already in this directory):

```sh
# 1. See what would be imported — no Claude calls, nothing written
uv run kindle_anki.py

# 2. Try a handful of real cards first
uv run kindle_anki.py --apply --limit 10

# 3. Import the rest
uv run kindle_anki.py --apply
```

The dry run is the default on purpose: it costs nothing on the Claude side and shows you the per-book breakdown before you commit. (It does query Anki to work out what's already handled, so Anki must be running even for a dry run.)

## Options

| Flag                 | Effect                                                                                                                                                                     |
| -------------------- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| _(none)_             | Dry run. Prints the breakdown and sample cards. No Claude calls, no writes.                                                                                                |
| `--apply`            | Cluster with Claude and add notes to Anki.                                                                                                                                 |
| `--reset`            | Delete every note in the deck and clear `skipped.json`, then reimport everything from scratch. With `--apply` it actually deletes; without, it just says what it would do. |
| `--limit N`          | Process at most N new lookups this run. Use it to sample cost and quality.                                                                                                 |
| `--book SUB`         | Only lookups from books whose title contains `SUB` (case-insensitive). Repeatable; matches any.                                                                            |
| `--db PATH`          | Read a specific `vocab.db` instead of auto-detecting.                                                                                                                      |
| `--model ID`         | Claude model (default `claude-opus-5`).                                                                                                                                    |
| `--batch-size N`     | Stem-groups per API request (default 40).                                                                                                                                  |
| `--language NAME`    | Add a back-of-card translation in this language, e.g. `French`. Default: none (monolingual). Falls back to `TRANSLATION_LANGUAGE` in `.env`.                               |
| `--export FILE.apkg` | Offline mode. Write cards to an Anki package file instead of a running Anki; state lives in `deck_state.json`. See [Offline export](#offline-export).                      |

```sh
uv run kindle_anki.py --book "1984" --book "Zebras" --apply
```

## Offline export

Don't want to run Anki with AnkiConnect — or want to card on a phone with no desktop Anki at all? Pass `--export deck.apkg` and the tool writes a standard Anki package you import by hand (**File → Import**) instead of talking to a running Anki:

```sh
# dry run, offline — same per-book breakdown, reads deck_state.json
uv run kindle_anki.py --export deck.apkg

# cluster with Claude and write the package
uv run kindle_anki.py --export deck.apkg --apply
```

Everything that makes the live path sense-aware works identically here — the only thing that changes is where state lives. With no running deck to query, **`deck_state.json`** becomes the source of truth: it records every card and the lookup ids it consumed, so re-runs still skip what you already have, split senses, and promote expressions exactly as before. It's the offline stand-in for the `Lookups` field the live path reads off your cards.

The `.apkg` is a **cumulative snapshot** of the whole deck, not a per-run diff.  Each card carries a stable GUID derived from its first lookup, so **re-importing a regenerated package updates cards in place — it never duplicates them.** Import the latest `deck.apkg` after each `--apply` and your deck stays in sync.

The cards are byte-for-byte the same note type, template, and CSS as the live path, so switching between the two is seamless. Anthropic API key requirements are unchanged; Anki and AnkiConnect are simply not needed until import time.

> [!NOTE]
> Offline mode can't self-heal on manual edits the way the live path does: if you delete a card _in Anki_, `deck_state.json` doesn't know, so that lookup won't come back on the next run. Edit `deck_state.json` (or `--reset`) if you need to force a rebuild.

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

**1. Get the database.** `vocab.db` is copied off the Kindle into the project directory before anything reads it — SQLite on a removable FAT volume shouldn't be opened in place. The copy doubles as a cache, so you can re-run with the Kindle unplugged.

**2. Read every lookup.** Each `LOOKUPS` row becomes a `Lookup` carrying its `id`, lemma (`stem`), surface form, sentence, book, and timestamp. Nothing is collapsed — all the contexts survive so Claude can see the different senses.

**3. Filter.** The `--book` filter applies at the lookup level.

**4. Work out what's new — before spending any money.** The deck is queried by `Stem`, chunked ~100 stems per call. From that one pull the tool builds the set of **consumed** lookup ids (the union of every card's hidden `Lookups` field) and the **existing** cards per stem (for dedup context). A lookup is _new_ only if its id is neither consumed nor in `skipped.json`.

**5. Cluster.** New lookups are grouped by stem and sent to Claude in batches.  For each group Claude returns new cards (headword, definition, the exact span to blank, and — when `--language` is set — a translation) plus a verdict for every lookup: **new** (maps to one of the new cards), **existing** (same sense as a card already in the deck), or **junk**. Structured outputs pin the response to a schema so it can't come back malformed. The system prompt is marked for prompt caching.

**6. Write to Anki.** One note per new card, with `allowDuplicate: true` — this pipeline owns dedup, so Anki's own duplicate guard is turned off. `existing` verdicts append the lookup id to the matched card's `Lookups`; `junk` verdicts go to `skipped.json`.

## What gets created in Anki

On the first `--apply` the script creates, if missing:

- **Deck** `English::Kindle`
- **Note type** `Kindle Vocab` with fields `Stem`, `Word`, `Translation`, `Definition`, `Sentence`, `Source`, `LookupDate`, `Lookups`, and a single **Production** card template (definition - blanked sentence on the front; word, translation, and source on the back). The `Translation` field stays empty unless you run with `--language`, and the template hides it when empty.

`Stem` and `Lookups` are hidden bookkeeping fields — never rendered on a card.
`Stem` is the lemma index used to find a stem's cards quickly; `Lookups` is the comma-joined list of the `LOOKUPS.id`s that produced or were absorbed by the card, and is the source of truth for what's already handled.

Each note is tagged `kindle` plus `book::<slug>`, e.g.  `book::why-zebras-don-t-get-ulcers`, so you get a per-book tag tree in the browser.

### Blanking

Claude returns the exact surface span(s) to hide — a **list** of substrings, so a `make off` card blanks the whole expression, not just `make`. A separable phrasal verb split by its object ("she _tied_ her hair _up_") gives each piece separately, `["tied", "up"]`, so the object between them stays visible. Every piece is matched verbatim; if any doesn't occur the tool falls back to the inflected form and then the lemma, and if none match it leaves the sentence intact rather than mangle it.

## State and re-runs

The script is safe to run repeatedly; it only ever adds what's missing.

**The `Lookups` field is the source of truth.** Because every processed lookup id is recorded on the card it produced (or the card that absorbed it), re-runs cost nothing for lookups you already have — and the pipeline **self-heals**: delete or edit a card in Anki and its lookup ids leave the consumed set, so they're reprocessed on the next run.

That self-heal is also the catch for **a card you want gone** — say a word you already know that slipped in via a Kindle misclick. Deleting it won't stick: its lookup id leaves the consumed set and the card comes back next run. **Suspend the note instead.** Dedup is content-based and never reads a card's suspended state, so the suspended card keeps claiming its lookup id — it stays out of reviews and never regenerates. For a permanent, deck-independent skip, add its `Lookups` id to `skipped.json` (below) and then delete it.

**`skipped.json`** (git-ignored) is the only local state on the live path, and holds junk only (offline `--export` adds one more, `deck_state.json`, described in [Offline export](#offline-export)):

```json
{ "a1b2c3": "proper noun", "d4e5f6": "ocr artefact" }
```

Keyed by `LOOKUPS.id` → reason, so junk isn't paid to be re-judged every run.  It's rewritten after every batch, so an interrupted run resumes cleanly.  `--reset` clears it (and the deck) for a full rebuild.

## Cost

Rough order of magnitude for ~400 lookups at `claude-opus-5` prices: **a couple of dollars**, in about ten requests. Run `--apply --limit 40` first and check the actual spend in the Anthropic console before doing the rest. Cheaper: `--model claude-sonnet-5`, or a smaller `--batch-size` if you see truncation.

## Tests

Pure logic, the Anki HTTP boundary, and the filesystem are covered by fast offline tests; the Claude clustering step has a few real-API behavioral evals that are **deselected by default** (they cost money):

```sh
# fast, free, offline — pure logic + mocked Anki + real sqlite/tmp files
# (add --with genanki to also exercise the real .apkg write; it's skipped otherwise)
uv run --with pytest --with genanki pytest

# behavioral evals against the real API (cheap model), opt-in
uv run --with pytest --with anthropic pytest -m llm
```

The evals use `claude-haiku-4-5` and assert loose properties so they survive model nondeterminism while still catching a prompt or schema regression: a proper noun is `junk`, two senses split into two cards with verbatim spans, a different sense of a word already in the deck becomes a new card, an inflected verb's headword comes back as the infinitive, and a phrasal verb is promoted to its expression headword.

## Troubleshooting

**`Cannot reach AnkiConnect at http://127.0.0.1:8765`**
Anki isn't running, or AnkiConnect isn't installed. Anki must stay open for the whole run — including dry runs, which query it for what's already handled.

**`No vocab.db found`**
Plug the Kindle in and confirm it mounts at `/Volumes/Kindle`, or pass `--db PATH` to a copy you already have.

**`ANTHROPIC_API_KEY is not set`**
Add it to `.env` in this directory as `ANTHROPIC_API_KEY=sk-ant-...`.

**A card came out wrong, or a word was mis-clustered**
Delete the card in Anki. Its lookup ids leave the consumed set and get reprocessed on the next `--apply`. To rebuild the whole deck, use `--reset`.

## Files

|                   |                                                                                             |
| ----------------- | ------------------------------------------------------------------------------------------- |
| `kindle_anki.py`  | the whole tool — one file, PEP 723 inline dependencies                                      |
| `tests/`          | pytest suite (`pytest.ini` sets it up; `-m llm` for the API evals)                          |
| `.env`            | `ANTHROPIC_API_KEY` (required) plus optional `TRANSLATION_LANGUAGE` (git-ignored, mode 600) |
| `vocab.db`        | cached copy of the Kindle database (git-ignored)                                            |
| `skipped.json`    | junk lookup ids → reason (git-ignored)                                                      |
| `deck_state.json` | offline deck state for `--export`: cards + their consumed lookup ids (git-ignored)          |

## A note on backups

Anki keeps automatic backups in `~/Library/Application Support/Anki2/<profile>/backups/` and logs deleted notes to `deleted.txt` in the same folder. If an import goes wrong — especially after `--reset` — restore with **File → Import** on the newest `.colpkg` from before the mistake. Worth knowing before you bulk-edit a few hundred new cards.
