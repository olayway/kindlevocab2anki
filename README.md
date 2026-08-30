# Kindle Vocabulary to Anki

> [!NOTE]
> 🚧🐣 **Heads up - early version!** I built this to scratch my own itch, so right now it does exactly what _I_ needed and nothing more. But I've got a pile of ideas for making it better and more broadly useful - so expect things to grow, and behaviour to shift around. Hope you find it useful! 🤙

Turns the words you look up while reading on a Kindle into Anki flashcards — one card per **sense**, not per word. Claude reads the sentence each lookup appeared in, splits a word's distinct meanings into separate cards, builds cards for the phrasal verb or idiom a word belongs to, and merges repeat lookups of the same sense.

You see a definition and the sentence with the answer blanked out; you recall the word. The back confirms it — the word, the full sentence with the answer filled back in (in the exact inflected form it wore in the book, highlighted), and, if you turn on translations, a translation of that same sense into your native language.

![](definition-layout.png)

## Why not the existing Kindle → Anki tools?

Most work at the word level — one card per lookup, lemma on the front, a dictionary entry on the back. Without reading the sentence they can't tell `bank` (river) from `bank` (money), and can't promote a tap on `made` into a **make off** card. This tool reads the sentence with Claude and builds one card per _meaning_.

## Features

- **Sense-aware, not word-aware** — one card per _meaning_, not per lemma: polysemy is split into separate cards, phrasal verbs and idioms are promoted to their real headword, headwords are stored in dictionary form, repeat lookups of one sense merge, and junk is rejected. Kindle records raw taps; a good deck needs meanings, and only reading the sentence can tell them apart. → [Sense-aware, not word-aware](#sense-aware-not-word-aware)
- **Recall from context, not a word list** — every card shows a definition and the real sentence the word appeared in with the answer blanked out, so you recall the word from its meaning in context. The blank tracks the exact span, including split phrasal verbs (`["tied", "up"]` in "she _tied_ her hair _up_"), and falls back gracefully rather than mangling the sentence. → [Blanking](#blanking)
- **Study any language, gloss into yours** — pick the language you're learning with `--learning` (English, French, German, Spanish, Japanese ship in [`languages.yaml`](languages.yaml); adding one is a data entry, not a code change), and optionally add a translation into your native language on the back with `--translation`. The two are independent and compose freely. → [Languages](#languages)
- **Layouts for your level** — cards prompt with the learning-language definition by default; total beginners can flip to `--layout translation` to be prompted with their native word instead, since a definition you can't read yet isn't a prompt. Preview the exact look in a browser before building a deck. → [Card layout](#card-layout)
- **Recognition _and_ production** — `--production` adds a second, active-recall card to each note: it shows a sentence in your native language and asks you to produce the word in the language you're learning, with every other word pinned to your CEFR `--level` so only the target is hard. These cards are born suspended and unlock themselves once their recognition sibling graduates, so you meet a word before you're asked to produce it. → [Production cards](#production-cards)
- **Safe to re-run — it only adds what's missing** — the hidden `Lookups` field on each card is the source of truth, so repeat runs cost nothing for words you already have. Delete or edit a card in Anki and it's rebuilt next run (self-healing); to drop a card for good, suspend it. → [State and re-runs](#state-and-re-runs)
- **Works without a running Anki** — `--export deck.apkg` writes a standard Anki package you import by hand (or sync to a phone), with no AnkiConnect required. It's a cumulative, dedup-safe snapshot: re-importing updates cards in place instead of duplicating them. → [Offline export](#offline-export)

## Sense-aware, not word-aware

Kindle records one _lookup_ per tap — the lemma, the surface form, and the sentence. This tool does not collapse those to one card per lemma. Instead Claude clusters them:

- **Polysemy → sense split.** `bank` met at a river and `bank` met downtown become **two** cards, each with its own definition, sentence, and (if enabled) translation. This holds against cards **already in the deck** too: if you've carded `sordid` as "morally wrong" and later look it up meaning "squalid", that's a new card, not a match to the old one.
- **Base-form headwords.** The word to recall is stored in its dictionary form — verbs as the bare infinitive ("outdid" → **outdo**), nouns singular — never the inflected shape it happened to wear in the sentence.
- **Expression promotion.** A tap on `make` inside "they _made off_ with it" becomes a card whose headword is **make off** — the expression Kindle can't look up directly.
- **Same sense → one card.** Three lookups of the same meaning share a single card; the earliest sentence is the one shown, and every lookup is recorded on the card as provenance.
- **Junk is dropped.** Proper nouns, names, foreign words, and OCR artefacts are rejected and remembered so they aren't re-judged next run.

## Languages

Two independent axes control language:

- **`--learning CODE`** — the language you're **studying**. It gates which Kindle lookups are read (via `WORDS.lang`), sets the language the definitions are written in, and picks the base-form rules (how a headword is normalized) and the blanking behavior. Default `en`. Each run writes to a per-language deck (`English::Kindle`, `French::Kindle`, …).
- **`--translation NAME`** — your **native** language, glossed on the **back** of the card only. Optional and off by default.

Translations are **off by default**: leave `--translation` off and the cards stay fully monolingual in the learning language. Turn it on and they compose freely — `--learning fr --translation Polish` makes French cards (French headword, French definition, French sentence with the answer blanked) with a Polish gloss on the back.

The gloss sits on the **back only**. Putting it on the front would turn recall into translation; on the back it just confirms you landed on the right sense once you've already committed to an answer. Total beginners can flip this with `--layout translation`, which prompts with the native word on the front instead — see [Card layout](#card-layout).

```sh
# Study French, monolingual
uv run kindle_anki.py --learning fr --apply

# Study French, Polish on the back
uv run kindle_anki.py --learning fr --translation Polish --apply
```

### Mislabeled books

`--learning fr` reads only lookups Kindle tagged `fr` in `WORDS.lang` — and Kindle takes that tag from the **ebook's metadata language, not the dictionary you looked the word up with.** Some ebooks carry the wrong language: a French title can ship tagged `en`, so every word you look up in it lands under `en`, `--learning fr` finds nothing, and the run fails with `No French lookups found`.

`--force-lang` fixes this without touching the book or the database. It drops the `WORDS.lang` gate and reads the `--book` you name as your `--learning` language regardless of how Kindle tagged it:

```sh
# The "French Edition" file is tagged 'en'; read its lookups as French anyway
uv run kindle_anki.py --learning fr --book "Petit Prince" --force-lang --apply
```

`--force-lang` **requires `--book`** — the book filter is what scopes it. Without a scope it would relabel your entire lookup history under one language (turning every English lookup into a French card), so the run stops rather than guess. Keep the `--book` substring specific enough to match only the mislabeled title; run a separate `--force-lang` pass for each such book.

### Adding or tuning a language

Languages are **data, not code**: they live in [`languages.yaml`](languages.yaml) beside the script, keyed by their Kindle `WORDS.lang` code. `en`, `fr`, `de`, `es`, and `ja` ship in it. Adding one is a new entry — no source edits:

```yaml
nl: # the WORDS.lang code (also the SQL gate)
  name: Dutch # spliced into the prompt and the deck ("Dutch::Kindle")
  boundaries: true # blanking: match on word boundaries (\b…\b)
  ignore_case: true # blanking: match case-insensitively
  inflection: true # blanking: fall back to the inflected word/stem
  morphology: >- # prompt fragment: how to normalize a headword
    verbs to the infinitive; nouns singular; promote expressions to the whole.
```

The three booleans replace what used to be a fixed `spaced`/`cjk` strategy, so you can describe any script's matching directly. Spaced, cased, suffix-inflecting languages (English, French, German, Spanish, …) set all three `true`. Scripts with no word breaks or case (Japanese, Chinese, Thai) set all three `false`, so blanking matches the exact span as a plain substring. The file is validated on load — a missing field, a non-boolean flag, or a malformed file stops the run with a message naming the culprit.

## Card layout

Every card holds the same fields; the **layout** only decides which one prompts you on the front and which is revealed on the back. Pick it with `--layout`:

- **`definition`** (default) — front: the learning-language definition + the sentence with the answer blanked; back: the full sentence with the answer filled in and highlighted, the word, plus the native translation if `--translation` is set. You recall the word from a meaning stated in the language itself.
- **`translation`** — front: your **native** translation + the blanked sentence; back: the full sentence with the answer filled in and highlighted, the word, and its definition. For total beginners: a definition written in a language you can't read yet isn't a prompt, it's a second puzzle, so the native word does the prompting instead. Because the front shows the translation, this layout **requires `--translation`** (it errors out otherwise).

```sh
# Beginner French deck: Polish on the front, definition revealed on the back
uv run kindle_anki.py --learning fr --translation Polish --layout translation --apply
```

Both layouts use identical fields, notes, and CSS — switching is purely a matter of which side each gloss appears on.

**Re-laying-out an existing deck.** An **explicit `--layout` on the command line** (with `--apply`) re-pushes that layout's templates and CSS onto the existing note type in place — so every card re-renders in the new layout, keeping all your cards, scheduling, and review history.

```sh
# switch an existing deck to the translation layout, cards and history intact
uv run kindle_anki.py --translation Polish --layout translation --apply
```

**A normal run never touches your templates**, so hand-edits you make in Anki (Browse → Cards…) survive every import. Passing `--layout` is how you opt in to overwriting them with the built-in layout.

For an **`.apkg`** (`--export`) the model is rebuilt on every export anyway, so just re-export with the new `--layout` and re-import; Anki updates the GUID-stable cards in place rather than duplicating them.

### Previewing the templates in a browser

To iterate on how the cards look without building a deck, render them to an HTML page:

```sh
python3 preview_cards.py   # writes card_preview.html and opens it
```

`preview_cards.py` reads the **real** `CARD_CSS` and templates straight from `kindle_anki.py` and fills them with sample words using the same field substitution Anki does — so what you see is what Anki renders. It shows the front and back of both layouts across a few sample cards (including ones with no example sentence or no translation, to check that those sections collapse), the **production** card (rotating through its sentence pairs; a sample with no pairs is skipped, mirroring the empty-card gate), plus a dark-mode toggle. Edit `CARD_CSS` or the templates in `kindle_anki.py`, re-run, and refresh the tab.

## Production cards

The default card is **receptive**: it shows a definition (or your native word) and you recall the target word. `--production` adds a second, **active** card to the same note — the mirror direction. It shows a sentence in your **native** language with one word bolded, and you produce the equivalent in the language you're learning, in context and from memory:

```sh
# French deck with production practice; prompts in Polish, non-target words at B1
uv run kindle_anki.py --learning fr --translation Polish --production --level B1 --apply
```

`--production` **requires `--translation`** (the front is written in your native language) and a **CEFR `--level`** (`A1`–`C2`). The level is the difficulty dial: every word in the native sentence *except* the target is pinned at or below that level, so the only hard thing is the word you're drilling. `--level` falls back to a `level:` key on the learning language in [`languages.yaml`](languages.yaml).

Each note gets **three** native → target sentence pairs, and the card shows **one per calendar day**, rotated by an inline script that front and back compute identically — so the pair stays in sync with no stored state, and a word you see every day still varies. (A review that spans local midnight may show one pair's prompt and the next pair's answer; harmless, and it self-corrects on the next review.)

**A first-letter hint keeps you honest.** A production prompt is answerable with a synonym — which quietly defeats the point of drilling the specific word you looked up. So the front carries a hint keyed off the target word: its first letter revealed, the rest of each letter masked, and the total letter count, e.g. `c _ _ _ _ _ · 6 letters`. It's computed in the card's JavaScript from the word itself, so it needs no extra field and appears on every production card — including ones built before the hint existed, with no rebuild.

**Lifecycle — cards unlock themselves.** Being asked to *produce* a word you've only just met is discouraging, so production cards are **born suspended** and live in a `…::Kindle::Production` subdeck. A production card **unsuspends itself** once its recognition sibling reaches a threshold, set by `--promote-after`:

| `--promote-after` | Unlocks the production card once its recognition sibling… |
| ----------------- | --------------------------------------------------------- |
| `seen`            | has been reviewed at least once                           |
| `graduated`       | has left learning for the review queue **(default)**      |
| `mature`          | is in review with an interval of ≥ 21 days                |

The promotion sweep is **folded into a normal run** — there is no separate command. Any live `--production --apply` reconciles the whole deck: it moves new production cards into the subdeck, suspends the ones whose sibling isn't ready, and unsuspends the ones whose sibling just crossed the threshold. It's idempotent, so running it again does nothing. The one thing it **never** touches is a production card you've already started reviewing (`reps > 0`) — your scheduling is safe regardless of the threshold.

**Back catalogue.** Cards built before you turned on `--production` have no pairs. A live `--production --apply` **backfills** them: it finds notes missing production data, generates pairs from each card's existing word, definition, and book sentence, and fills them in. This runs even when there are no new lookups to import, respects `--limit` (so you can meter the cost), and is resumable — interrupt it and the next run picks up the rest.

**Offline.** `--export` can build production cards (they render from the same fields), but it **can't** suspend, move, or promote them — that's live-only. The tool prints a reminder; run a live `--production --apply` once after importing to reconcile their lifecycle.

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

| Flag                 | Effect                                                                                                                                                                                                                                                                                                                              |
| -------------------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| _(none)_             | Dry run. Prints the breakdown and sample cards. No Claude calls, no writes.                                                                                                                                                                                                                                                         |
| `--apply`            | Cluster with Claude and add notes to Anki.                                                                                                                                                                                                                                                                                          |
| `--reset`            | Delete every note in the deck and clear `skipped.json`, then reimport everything from scratch. With `--apply` it actually deletes; without, it just says what it would do.                                                                                                                                                          |
| `--limit N`          | Process at most N new lookups this run. Use it to sample cost and quality.                                                                                                                                                                                                                                                          |
| `--book SUB`         | Only lookups from books whose title contains `SUB` (case-insensitive). Repeatable; matches any.                                                                                                                                                                                                                                     |
| `--force-lang`       | Read `--book`'s lookups as your `--learning` language, ignoring the language Kindle stored for them. For ebooks whose metadata language is wrong, so their lookups never match the normal gate. **Requires `--book`.** See [Mislabeled books](#mislabeled-books).                                                                   |
| `--db PATH`          | Read a specific `vocab.db` instead of auto-detecting.                                                                                                                                                                                                                                                                               |
| `--model ID`         | Claude model (default `claude-sonnet-5`).                                                                                                                                                                                                                                                                                           |
| `--batch-size N`     | Stem-groups per API request (default 40).                                                                                                                                                                                                                                                                                           |
| `--learning CODE`    | Language you're studying, e.g. `fr`. Sets which lookups are read and how cards are built. Default `en`; falls back to `LEARNING_LANGUAGE` in `.env`. See [Languages](#languages).                                                                                                                                                   |
| `--translation NAME` | Add a back-of-card translation into your native language, e.g. `Polish`. Default: none (monolingual). Falls back to `TRANSLATION_LANGUAGE` in `.env`. Orthogonal to `--learning`.                                                                                                                                                   |
| `--layout NAME`      | Card layout: `definition` (default) prompts with the learning-language definition; `translation` prompts with your native translation on the front — for beginners who can't yet read a definition in the language. `translation` requires `--translation`. See [Card layout](#card-layout). |
| `--production`       | Also build active-production cards: a native-language sentence prompts you to produce the target word in context. Born suspended, promoted with their recognition sibling, and lives in a `…::Production` subdeck. **Requires `--translation` and `--level`.** See [Production cards](#production-cards).                              |
| `--level CEFR`       | CEFR level (`A1`–`C2`) that every non-target word in `--production` sentences is pinned to, so only the target is hard. Falls back to `level:` on the `--learning` entry in `languages.yaml`. Only used with `--production`.                                                                                                          |
| `--promote-after WHEN` | When a production card unsuspends: once its recognition sibling has been `seen`, `graduated` (default), or gone `mature` (interval ≥ 21 days). Live mode only.                                                                                                                                                                     |
| `--export FILE.apkg` | Offline mode. Write cards to an Anki package file instead of a running Anki; state lives in `deck_state.json`. See [Offline export](#offline-export).                                                                                                                                                                               |

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

The `.apkg` is a **cumulative snapshot** of the whole deck, not a per-run diff. Each card carries a stable GUID derived from its first lookup, so **re-importing a regenerated package updates cards in place — it never duplicates them.** Import the latest `deck.apkg` after each `--apply` and your deck stays in sync.

The cards are byte-for-byte the same note type, template, and CSS as the live path, so switching between the two is seamless. Anthropic API key requirements are unchanged; Anki and AnkiConnect are simply not needed until import time.

> [!NOTE]
> Offline mode can't self-heal on manual edits the way the live path does: if you delete a card _in Anki_, `deck_state.json` doesn't know, so that lookup won't come back on the next run. Edit `deck_state.json` (or `--reset`) if you need to force a rebuild.

## How it works

```
Kindle vocab.db ──copy──▶ ./vocab.db ──▶ every lookup in the learning lang (with its id)
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
                            AnkiConnect ──▶ <Language>::Kindle
```

**1. Get the database.** `vocab.db` is copied off the Kindle into the project directory before anything reads it — SQLite on a removable FAT volume shouldn't be opened in place. The copy doubles as a cache, so you can re-run with the Kindle unplugged.

**2. Read every lookup.** Each `LOOKUPS` row becomes a `Lookup` carrying its `id`, lemma (`stem`), surface form, sentence, book, and timestamp. Nothing is collapsed — all the contexts survive so Claude can see the different senses.

**3. Filter.** The `--book` filter applies at the lookup level.

**4. Work out what's new — before spending any money.** The deck is queried by `Stem`, chunked ~100 stems per call. From that one pull the tool builds the set of **consumed** lookup ids (the union of every card's hidden `Lookups` field) and the **existing** cards per stem (for dedup context). A lookup is _new_ only if its id is neither consumed nor in `skipped.json`.

**5. Cluster.** New lookups are grouped by stem and sent to Claude in batches. For each group Claude returns new cards (headword, definition, the exact span to blank, and — when `--translation` is set — a translation) plus a verdict for every lookup: **new** (maps to one of the new cards), **existing** (same sense as a card already in the deck), or **junk**. Structured outputs pin the response to a schema so it can't come back malformed. The system prompt is marked for prompt caching.

**6. Write to Anki.** One note per new card, with `allowDuplicate: true` — this pipeline owns dedup, so Anki's own duplicate guard is turned off. `existing` verdicts append the lookup id to the matched card's `Lookups`; `junk` verdicts go to `skipped.json`. With `--export`, the same outcomes are written to `deck_state.json` and an `.apkg` instead ([Offline export](#offline-export)).

**On a dropped connection.** AnkiConnect calls aren't all retried the same way. Read-only actions (`findNotes`, `notesInfo`, …) are retried with a short backoff, because re-asking a question is always safe. Mutating actions (`addNotes`, `updateNoteFields`, …) are **not** retried — a reset can arrive _after_ Anki already applied the change, so a blind retry could double-add a note. They fail fast instead; `skipped.json` is rewritten after every batch, so re-running simply resumes from what was already saved.

## What gets created in Anki

On the first `--apply` the script creates, if missing:

- **Deck** `<Language>::Kindle`, named for the learning language — `English::Kindle` by default, `French::Kindle` under `--learning fr`, and so on
- **Note type** `Kindle Vocab` with fields `Stem`, `Word`, `Translation`, `Definition`, `Sentence`, `SentenceFull`, `Source`, `LookupDate`, `Lookups`, and — for [production cards](#production-cards) — three native/target pairs `ProdNative1`/`ProdTarget1` … `ProdNative3`/`ProdTarget3`. It carries a **Recognition** card template and, gated on production data, a **Production** one. `Sentence` holds the blanked prompt; `SentenceFull` holds the same sentence with the answer filled back in and wrapped in `<b class="target">…</b>` for the back. The recognition front/back split follows `--layout`: by default (`definition`) the definition + blanked sentence prompt the front and the full sentence, word, translation, and source are revealed on the back; `--layout translation` flips the definition and translation so the native word prompts the front instead ([Card layout](#card-layout)). The production card is layout-independent (always native → target) and only appears on notes that carry pairs — a note without them generates no production card, which is also the gate the backfill fills. The `Translation` and `ProdNative*`/`ProdTarget*` fields stay empty unless you run with `--translation` / `--production`, and the templates hide what's empty. Fields and the production template are added to a pre-existing note type automatically on the next `--apply`; the recognition template was historically named `Production` and is renamed to `Recognition` in place, which is lossless (Anki keys review history to a template's position, not its name). A deck created before `SentenceFull` existed gains the field automatically on the next `--apply` run, and existing cards are backfilled — the original sentence is re-read from `vocab.db` via each card's `Lookups` id and re-highlighted (a card whose source lookup is no longer on the Kindle keeps the field empty and simply shows no sentence on its back).

`Stem` and `Lookups` are hidden bookkeeping fields — never rendered on a card.
`Stem` is the lemma index used to find a stem's cards quickly; `Lookups` is the comma-joined list of the `LOOKUPS.id`s that produced or were absorbed by the card, and is the source of truth for what's already handled.

Each note is tagged `kindle` plus `book::<slug>`, e.g. `book::why-zebras-don-t-get-ulcers`, so you get a per-book tag tree in the browser.

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

Keyed by `LOOKUPS.id` → reason, so junk isn't paid to be re-judged every run. It's rewritten after every batch, so an interrupted run resumes cleanly. `--reset` clears it (and the deck) for a full rebuild.

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
Plug the Kindle in and confirm it mounts at `/Volumes/Kindle`, or pass `--db PATH` to a copy you already have. If it prints `Kindle not mounted; using cached copy` and then can't find lookups, the Kindle isn't mounted and it fell back to a stale local `vocab.db`.

**`No <language> lookups found`**
The lookups exist but Kindle tagged them with a different `WORDS.lang` than you asked for — usually because the ebook's metadata language is wrong (e.g. a French title tagged `en`). Read them as your learning language with `--book "<title>" --force-lang`. See [Mislabeled books](#mislabeled-books).

**`ANTHROPIC_API_KEY is not set`**
Add it to `.env` in this directory as `ANTHROPIC_API_KEY=sk-ant-...`.

**A card came out wrong, or a word was mis-clustered**
Delete the card in Anki. Its lookup ids leave the consumed set and get reprocessed on the next `--apply`. To rebuild the whole deck, use `--reset`.

## Files

|                    |                                                                                                |
| ------------------ | ---------------------------------------------------------------------------------------------- |
| `kindle_anki.py`   | the whole tool — one file, PEP 723 inline dependencies                                         |
| `languages.yaml`   | learning-language profiles (name, blanking flags, morphology) — edit to add or tune a language |
| `preview_cards.py` | render the card templates to `card_preview.html` for previewing in a browser                   |
| `tests/`           | pytest suite (`pytest.ini` sets it up; `-m llm` for the API evals)                             |
| `.env`             | `ANTHROPIC_API_KEY` (required) plus optional `TRANSLATION_LANGUAGE` (git-ignored, mode 600)    |
| `vocab.db`         | cached copy of the Kindle database (git-ignored)                                               |
| `skipped.json`     | junk lookup ids → reason (git-ignored)                                                         |
| `deck_state.json`  | offline deck state for `--export`: cards + their consumed lookup ids (git-ignored)             |

## A note on backups

Anki keeps automatic backups in `~/Library/Application Support/Anki2/<profile>/backups/` and logs deleted notes to `deleted.txt` in the same folder. If an import goes wrong — especially after `--reset` — restore with **File → Import** on the newest `.colpkg` from before the mistake. Worth knowing before you bulk-edit a few hundred new cards.
