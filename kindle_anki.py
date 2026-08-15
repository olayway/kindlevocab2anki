#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.11"
# dependencies = ["anthropic>=0.70", "genanki>=0.13"]
# ///
"""Import Kindle Vocabulary Builder lookups into Anki as production cards.

Reads vocab.db from a mounted Kindle (or a local cache), generates a
context-aware English definition for each new word with the Claude API, and
creates one Anki note per lemma via AnkiConnect.

Pass --export deck.apkg to write an offline Anki package instead of talking to
a running Anki: state then lives in a local deck_state.json rather than the
deck, so the same sense-aware dedup runs with nothing installed but this script.

Default action is a dry run. Pass --apply to actually call Claude and write
notes (to Anki, or to the .apkg when --export is given).
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import sqlite3
import sys
import time
import unicodedata
import urllib.error
import urllib.request
from collections import OrderedDict, defaultdict
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
KINDLE_DB = Path("/Volumes/Kindle/system/vocabulary/vocab.db")
CACHE_DB = SCRIPT_DIR / "vocab.db"
SKIPPED_JSON = SCRIPT_DIR / "skipped.json"
STATE_JSON = SCRIPT_DIR / "deck_state.json"  # offline (--export) source of truth
ENV_FILE = SCRIPT_DIR / ".env"

ANKI_URL = "http://127.0.0.1:8765"
DECK_NAME = "English::Kindle"
MODEL_NAME = "Kindle Vocab"
TRANSLATION_FIELD = "Translation"  # populated only when --language is given
FIELDS = [
    "Stem",
    "Word",
    TRANSLATION_FIELD,
    "Definition",
    "Sentence",
    "Source",
    "LookupDate",
    "Lookups",
]
BLANK = "_____"

DEFAULT_MODEL = "claude-opus-5"
BATCH_SIZE = 40

# Fixed ids so genanki reuses the same note type / deck across runs and
# re-imports of a regenerated .apkg update in place instead of duplicating.
GENANKI_MODEL_ID = 1607392319
GENANKI_DECK_ID = 2059400110


# --------------------------------------------------------------------------
# errors
# --------------------------------------------------------------------------


class Fatal(Exception):
    """User-facing error: print the message and exit non-zero."""


# --------------------------------------------------------------------------
# vocab.db
# --------------------------------------------------------------------------


@dataclass
class Lookup:
    id: str
    stem: str
    word: str
    sentence: str
    title: str
    authors: str
    timestamp: int

    @property
    def source(self) -> str:
        if self.authors:
            return f"{self.title} — {self.authors}"
        return self.title

    @property
    def date(self) -> str:
        if not self.timestamp:
            return ""
        return datetime.fromtimestamp(self.timestamp / 1000, tz=timezone.utc).strftime(
            "%Y-%m-%d"
        )


def resolve_db(explicit: str | None) -> Path:
    """Pick a source vocab.db, copy it to the local cache, return the cache path.

    SQLite on a removable FAT volume shouldn't be opened in place, and the cache
    lets the tool re-run with the Kindle unplugged.
    """
    if explicit:
        source = Path(explicit).expanduser()
        if not source.exists():
            raise Fatal(f"--db path does not exist: {source}")
    elif KINDLE_DB.exists():
        source = KINDLE_DB
    elif CACHE_DB.exists():
        print(f"Kindle not mounted; using cached copy at {CACHE_DB}")
        return CACHE_DB
    else:
        raise Fatal(
            "No vocab.db found.\n"
            f"  Expected the Kindle at {KINDLE_DB}\n"
            f"  or a cached copy at {CACHE_DB}.\n"
            "  Plug in the Kindle, or pass --db PATH."
        )

    if source.resolve() != CACHE_DB.resolve():
        shutil.copy2(source, CACHE_DB)
        print(f"Copied {source} -> {CACHE_DB}")
    return CACHE_DB


def read_lookups(db_path: Path) -> list[Lookup]:
    uri = f"file:{db_path}?mode=ro"
    conn = sqlite3.connect(uri, uri=True)
    conn.row_factory = sqlite3.Row
    try:
        rows = conn.execute(
            """
            SELECT l.id     AS id,
                   w.stem   AS stem,
                   w.word   AS word,
                   l.usage  AS usage,
                   b.title  AS title,
                   b.authors AS authors,
                   l.timestamp AS ts
            FROM LOOKUPS l
            JOIN WORDS w      ON l.word_key = w.id
            LEFT JOIN BOOK_INFO b ON l.book_key = b.id
            WHERE w.lang = 'en'
            ORDER BY l.timestamp ASC
            """
        ).fetchall()
    finally:
        conn.close()

    out = []
    for r in rows:
        stem = (r["stem"] or "").strip()
        if not stem:
            continue
        out.append(
            Lookup(
                id=str(r["id"]),
                stem=stem,
                word=(r["word"] or stem).strip(),
                sentence=(r["usage"] or "").strip(),
                title=(r["title"] or "Unknown book").strip(),
                authors=(r["authors"] or "").strip(),
                timestamp=r["ts"] or 0,
            )
        )
    return out


def consumed_ids(notes_info: list[dict]) -> set[str]:
    """Union of every card's `Lookups` field — the ids already spoken for."""
    consumed: set[str] = set()
    for info in notes_info:
        value = ((info.get("fields") or {}).get("Lookups") or {}).get("value", "")
        consumed.update(part.strip() for part in value.split(",") if part.strip())
    return consumed


def build_existing_index(notes_info: list[dict]) -> dict[str, list[dict]]:
    """Group existing cards by lowercased stem for dedup context.

    List position is the `index` handed to Claude; each entry also keeps the
    `note_id` and current `lookups` value so an `existing` verdict can append
    to that card afterwards.
    """
    index: dict[str, list[dict]] = defaultdict(list)
    for info in notes_info:
        fields = info.get("fields") or {}

        def value(name: str) -> str:
            return (fields.get(name) or {}).get("value", "")

        stem = value("Stem").strip().lower()
        if not stem:
            continue
        index[stem].append(
            {
                "note_id": info.get("noteId"),
                "headword": value("Word"),
                "definition": value("Definition"),
                "lookups": value("Lookups"),
            }
        )
    return dict(index)


def determine_new_lookups(
    lookups: list[Lookup], notes_info: list[dict], skipped_ids: set[str]
) -> list[Lookup]:
    """Lookups still needing a card: id neither consumed by a card nor skipped."""
    consumed = consumed_ids(notes_info)
    return [
        lk for lk in lookups if lk.id not in consumed and lk.id not in skipped_ids
    ]


def matches_books(lk: Lookup, patterns: list[str]) -> bool:
    if not patterns:
        return True
    title = lk.title.lower()
    return any(p.lower() in title for p in patterns)


# --------------------------------------------------------------------------
# text helpers
# --------------------------------------------------------------------------


def blank_out(sentence: str, span, word: str, stem: str) -> str:
    """Replace the answer with a blank; try Claude's span(s), then word, then stem.

    `span` is the exact surface span Claude asked us to hide. It may be a
    single string (a contiguous expression like "make off") or a list of
    strings for a separable phrasal verb split by its object ("tie ... up" in
    "she tied her hair up" → ["tied", "up"]). Every piece must match verbatim,
    or we fall back rather than emit a half-blanked sentence. If nothing
    matches, leave the sentence intact rather than mangle it.
    """
    parts = [span] if isinstance(span, str) else list(span or [])
    parts = [p for p in parts if p]
    if parts:
        # Exact surface form(s) — match verbatim, no inflection expansion.
        blanked = sentence
        for part in parts:
            pattern = re.compile(rf"\b{re.escape(part)}\b", re.IGNORECASE)
            blanked, n = pattern.subn(BLANK, blanked)
            if not n:
                break  # all-or-nothing: fall through to word/stem
        else:
            return blanked
    for candidate in (word, stem):
        if not candidate:
            continue
        # Fallback: expand to catch inflections ("afflict" -> "afflicted").
        pattern = re.compile(rf"\b{re.escape(candidate)}\w*\b", re.IGNORECASE)
        blanked, n = pattern.subn(BLANK, sentence)
        if n:
            return blanked
    return sentence


def book_tag(title: str) -> str:
    slug = unicodedata.normalize("NFKD", title).encode("ascii", "ignore").decode()
    slug = re.sub(r"[^a-zA-Z0-9]+", "-", slug).strip("-").lower()
    return f"book::{slug or 'unknown'}"


def group_by_stem(lookups: list[Lookup]) -> "OrderedDict[str, list[Lookup]]":
    """Group lookups by lowercased stem, preserving first-seen order."""
    groups: "OrderedDict[str, list[Lookup]]" = OrderedDict()
    for lk in lookups:
        groups.setdefault(lk.stem.lower(), []).append(lk)
    return groups


def build_group_payload(
    stem: str, lookups: list[Lookup], existing_index: dict[str, list[dict]]
) -> dict:
    """One stem-group as sent to Claude: contexts + indexed existing cards."""
    existing = [
        {"index": i, "headword": e["headword"], "definition": e["definition"]}
        for i, e in enumerate(existing_index.get(stem.lower(), []))
    ]
    return {
        "stem": stem,
        "contexts": [
            {
                "lookup_id": lk.id,
                "sentence": lk.sentence,
                "book": lk.title,
                "timestamp": lk.timestamp,
            }
            # Earliest first, so the first context mapping to a card is its
            # primary (matches `build_notes`, which blanks the earliest lookup).
            for lk in sorted(lookups, key=lambda l: l.timestamp)
        ],
        "existing": existing,
    }


@dataclass
class BuildResult:
    notes: list[dict]  # addNotes payloads, one per new card
    existing: list[dict]  # {"card_index", "lookup_id"} links to record
    junk: list[dict]  # {"lookup_id", "reason"} for skipped.json


def build_notes(
    stem: str, lookups: list[Lookup], response: dict, translate: bool = False
) -> BuildResult:
    """Turn Claude's per-group response into note payloads + outcomes.

    `new` assignments sharing a `card_index` collapse into one card; the
    earliest-timestamp lookup is the primary (its sentence/source/date), and
    every assigned id is joined into `Lookups`.
    """
    by_id = {lk.id: lk for lk in lookups}
    new_cards = response.get("new_cards", [])

    card_lookups: "OrderedDict[int, list[Lookup]]" = OrderedDict()
    existing: list[dict] = []
    junk: list[dict] = []
    for a in response.get("assignments", []):
        lk = by_id.get(a["lookup_id"])
        if lk is None:
            continue
        if a["verdict"] == "new":
            card_lookups.setdefault(a["card_index"], []).append(lk)
        elif a["verdict"] == "existing":
            existing.append(
                {"card_index": a["card_index"], "lookup_id": a["lookup_id"]}
            )
        elif a["verdict"] == "junk":
            junk.append({"lookup_id": a["lookup_id"], "reason": a.get("reason", "")})

    notes = []
    for idx, lks in card_lookups.items():
        if not isinstance(idx, int) or not 0 <= idx < len(new_cards):
            # A "new" verdict pointing at a card_index the model never emitted.
            # Skip it (rather than crash the whole batch); the affected lookups
            # go unrecorded and are retried on the next run.
            print(f"  ! {stem!r}: bad card_index {idx} — skipping")
            continue
        card = new_cards[idx]
        primary = min(lks, key=lambda l: l.timestamp)
        fields = {
            "Stem": stem,
            "Word": card["headword"],
            "Definition": card["definition"],
            "Sentence": blank_out(
                primary.sentence,
                card.get("span", []),
                primary.word,
                primary.stem,
            ),
            "Source": primary.source,
            "LookupDate": primary.date,
            "Lookups": ",".join(lk.id for lk in lks),
        }
        if translate:
            fields[TRANSLATION_FIELD] = card.get("translation", "")
        notes.append(
            {
                "deckName": DECK_NAME,
                "modelName": MODEL_NAME,
                "fields": fields,
                "tags": ["kindle", book_tag(primary.title)],
                "options": {"allowDuplicate": True},
            }
        )

    return BuildResult(notes=notes, existing=existing, junk=junk)


def apply_new_cards(
    client,
    model: str,
    new_lookups: list[Lookup],
    existing_index: dict[str, list[dict]],
    skipped: dict,
    batch_size: int = 40,
    language: str | None = None,
    sink: "Sink | None" = None,
) -> tuple[int, int, int]:
    """Cluster new lookups in batches and write the outcomes through `sink`.

    Returns (added, updated, junked). `skipped` is mutated and persisted after
    every batch so an interrupted run resumes cleanly. `sink` defaults to the
    live AnkiConnect backend; pass an `ApkgSink` to write an offline package.
    """
    if sink is None:
        sink = LiveSink()
    stem_items = list(group_by_stem(new_lookups).items())
    added = updated = junked = 0

    for i in range(0, len(stem_items), batch_size):
        batch = stem_items[i : i + batch_size]
        payloads = [
            build_group_payload(stem, lks, existing_index) for stem, lks in batch
        ]
        responses = cluster_groups(client, model, payloads, language=language)

        for stem, lks in batch:
            response = responses.get(stem)
            if response is None:
                print(f"  ! no response for stem {stem!r} — leaving for next run")
                continue
            result = build_notes(stem, lks, response, translate=bool(language))

            if result.notes:
                sink.add_notes(result.notes)
                added += len(result.notes)

            entries = existing_index.get(stem, [])
            for link in result.existing:
                idx = link["card_index"]
                if not isinstance(idx, int) or not 0 <= idx < len(entries):
                    print(f"  ! {stem!r}: bad existing index {idx} — skipping")
                    continue
                sink.link_existing(entries[idx], link["lookup_id"])
                updated += 1

            for j in result.junk:
                skipped[j["lookup_id"]] = j["reason"]
                junked += 1

        save_skipped(skipped)

    sink.finalize()
    return added, updated, junked


# --------------------------------------------------------------------------
# AnkiConnect
# --------------------------------------------------------------------------


# Read-only actions are safe to retry after a dropped connection. Mutating ones
# are NOT: a reset can arrive after Anki already applied the request, so a retry
# would double-add. Those fail fast — a resumed run skips already-written notes.
_RETRYABLE_ACTIONS = frozenset(
    {"findNotes", "notesInfo", "deckNames", "modelNames", "modelFieldNames"}
)


def anki(action: str, retries: int = 3, **params):
    payload = json.dumps({"action": action, "version": 6, "params": params}).encode()
    attempt = 0
    while True:
        req = urllib.request.Request(
            ANKI_URL, data=payload, headers={"Content-Type": "application/json"}
        )
        try:
            with urllib.request.urlopen(req, timeout=60) as resp:
                body = json.load(resp)
            break
        except OSError as exc:
            # A mid-response reset/timeout comes through raw (not wrapped in
            # URLError, which only covers connection setup), so catch OSError.
            transient = isinstance(exc, (ConnectionError, TimeoutError))
            if transient and action in _RETRYABLE_ACTIONS and attempt < retries:
                attempt += 1
                print(
                    f"  ! AnkiConnect {action} dropped ({exc}); "
                    f"retry {attempt}/{retries}"
                )
                time.sleep(attempt)  # linear backoff: 1s, 2s, 3s
                continue
            if isinstance(exc, urllib.error.URLError) or isinstance(
                exc, ConnectionRefusedError
            ):
                hint = "Start Anki and make sure the AnkiConnect add-on is installed."
            else:
                hint = "Progress is saved; re-run to resume where it stopped."
            raise Fatal(
                f"AnkiConnect {action} failed at {ANKI_URL} ({exc}).\n  {hint}"
            ) from exc
    if body.get("error"):
        raise Fatal(f"AnkiConnect error on {action}: {body['error']}")
    return body["result"]


def fetch_notes_for_stems(stems: list[str], chunk: int = 100) -> list[dict]:
    """notesInfo for every deck card whose Stem is in `stems` (chunked query)."""
    if not stems:
        return []
    note_ids: list[int] = []
    for i in range(0, len(stems), chunk):
        terms = " OR ".join(f'"Stem:{s}"' for s in stems[i : i + chunk])
        query = f'deck:"{DECK_NAME}" ({terms})'
        note_ids.extend(anki("findNotes", query=query))
    if not note_ids:
        return []
    return anki("notesInfo", notes=sorted(set(note_ids)))


def record_existing_link(entry: dict, lookup_id: str) -> None:
    """Append `lookup_id` to an existing card's Lookups field (idempotent)."""
    ids = [p.strip() for p in entry.get("lookups", "").split(",") if p.strip()]
    if lookup_id not in ids:
        ids.append(lookup_id)
    anki(
        "updateNoteFields",
        note={"id": entry["note_id"], "fields": {"Lookups": ",".join(ids)}},
    )


CARD_CSS = """\
.card {
  font-family: -apple-system, Helvetica, sans-serif;
  font-size: 20px;
  text-align: left;
  color: #222;
  background: #fff;
  padding: 1em;
}
.definition { font-size: 22px; }
.sentence { color: #555; font-style: italic; margin-top: 0.8em; }
.word { font-size: 28px; font-weight: 600; }
.translation { font-size: 20px; color: #444; margin-top: 0.2em; }
.source { color: #888; font-size: 14px; margin-top: 1em; }
"""

CARD_FRONT = """\
<div class="definition">{{Definition}}</div>
{{#Sentence}}<div class="sentence">{{Sentence}}</div>{{/Sentence}}
"""

CARD_BACK = """\
{{FrontSide}}
<hr id=answer>
<div class="word">{{Word}}</div>
{{#Translation}}<div class="translation">{{Translation}}</div>{{/Translation}}
<div class="source">{{Source}}{{#LookupDate}} · {{LookupDate}}{{/LookupDate}}</div>
"""


def ensure_model() -> None:
    if MODEL_NAME in anki("modelNames"):
        return
    anki(
        "createModel",
        modelName=MODEL_NAME,
        inOrderFields=FIELDS,
        css=CARD_CSS,
        isCloze=False,
        cardTemplates=[
            {"Name": "Production", "Front": CARD_FRONT, "Back": CARD_BACK}
        ],
    )
    print(f"Created note type {MODEL_NAME!r}")


def ensure_deck() -> None:
    if DECK_NAME not in anki("deckNames"):
        anki("createDeck", deck=DECK_NAME)
        print(f"Created deck {DECK_NAME!r}")


# --------------------------------------------------------------------------
# sinks — where apply_new_cards sends its two side effects
# --------------------------------------------------------------------------


class Sink:
    """Write target for clustered outcomes. `add_notes` commits new cards;
    `link_existing` records that a lookup belongs to a card already present;
    `finalize` flushes anything buffered. The live backend talks to Anki now;
    the offline one buffers and writes an .apkg on finalize.
    """

    def add_notes(self, notes: list[dict]) -> None:
        raise NotImplementedError

    def link_existing(self, entry: dict, lookup_id: str) -> None:
        raise NotImplementedError

    def finalize(self) -> None:
        pass


class LiveSink(Sink):
    """Write straight to a running Anki over AnkiConnect (the default)."""

    def add_notes(self, notes: list[dict]) -> None:
        anki("addNotes", notes=notes)

    def link_existing(self, entry: dict, lookup_id: str) -> None:
        record_existing_link(entry, lookup_id)


# --------------------------------------------------------------------------
# offline export — deck_state.json + a genanki .apkg
# --------------------------------------------------------------------------


def load_state() -> list[dict]:
    """Card records from prior --export runs; [] when the file is absent.

    Each record is a card: its fields plus the lookup ids it consumed. It is
    the offline stand-in for the live deck, so `card_id` doubles as the note id
    handed to build_existing_index and the value link_existing targets.
    """
    if not STATE_JSON.exists():
        return []
    try:
        data = json.loads(STATE_JSON.read_text())
    except json.JSONDecodeError as exc:
        raise Fatal(f"{STATE_JSON} is not valid JSON ({exc}). Fix or delete it.")
    if not isinstance(data, list):
        raise Fatal(f"{STATE_JSON} must contain a JSON array of card records.")
    return data


def save_state(cards: list[dict]) -> None:
    STATE_JSON.write_text(json.dumps(cards, indent=2, ensure_ascii=False) + "\n")


def card_id_of(note: dict) -> str:
    """Stable identity for a card: its first (earliest-assigned) lookup id.

    A lookup belongs to exactly one card and this id is never reordered as more
    lookups are linked, so it stays unique and stable across runs.
    """
    return note["fields"]["Lookups"].split(",")[0]


def state_to_notes_info(cards: list[dict]) -> list[dict]:
    """Adapt stored card records into the notesInfo shape the read-side logic
    (consumed_ids / build_existing_index / determine_new_lookups) expects, so
    the offline path reuses it unchanged. `card_id` stands in for `noteId`.
    """
    return [
        {
            "noteId": card["card_id"],
            "fields": {name: {"value": card["fields"].get(name, "")} for name in FIELDS},
        }
        for card in cards
    ]


class ApkgSink(Sink):
    """Buffer new cards, mutate offline state, write an .apkg on finalize.

    Seeded with the card records loaded from deck_state.json so `link_existing`
    can append to a prior card. The .apkg is a full cumulative snapshot of every
    card in the state — stable GUIDs make re-importing it update the deck in
    place rather than duplicate — so one file is always the whole deck.
    """

    def __init__(self, out_path: Path, state: list[dict]):
        self.out_path = out_path
        self.state = state
        self.by_id = {c["card_id"]: c for c in state}

    def add_notes(self, notes: list[dict]) -> None:
        for note in notes:
            cid = card_id_of(note)
            record = {
                "card_id": cid,
                "fields": dict(note["fields"]),
                "tags": list(note.get("tags", [])),
            }
            self.state.append(record)
            self.by_id[cid] = record

    def link_existing(self, entry: dict, lookup_id: str) -> None:
        record = self.by_id.get(entry["note_id"])
        if record is None:  # pragma: no cover - guarded by build_existing_index
            return
        ids = [p for p in record["fields"].get("Lookups", "").split(",") if p]
        if lookup_id not in ids:
            ids.append(lookup_id)
        record["fields"]["Lookups"] = ",".join(ids)

    def finalize(self) -> None:
        save_state(self.state)
        write_apkg(self.out_path, self.state)


def build_genanki_model():
    """genanki model mirroring the AnkiConnect note type, so both paths
    produce visually identical cards from the same fields/template/CSS."""
    try:
        import genanki
    except ImportError as exc:  # pragma: no cover - uv installs this
        raise Fatal(f"The genanki package is unavailable: {exc}") from exc
    return genanki.Model(
        GENANKI_MODEL_ID,
        MODEL_NAME,
        fields=[{"name": name} for name in FIELDS],
        templates=[{"name": "Production", "qfmt": CARD_FRONT, "afmt": CARD_BACK}],
        css=CARD_CSS,
    )


def write_apkg(out_path: Path, notes: list[dict]) -> None:
    """Write card records to an .apkg at `out_path`.

    Each record needs `fields` (keyed by FIELDS) and optional `tags`; the GUID
    is derived from the card's first lookup id so re-imports stay idempotent.
    """
    import genanki

    model = build_genanki_model()
    deck = genanki.Deck(GENANKI_DECK_ID, DECK_NAME)
    for note in notes:
        fields = note["fields"]
        deck.add_note(
            genanki.Note(
                model=model,
                fields=[fields.get(name, "") for name in FIELDS],
                tags=note.get("tags", []),
                guid=genanki.guid_for(card_id_of(note)),
            )
        )
    out_path.parent.mkdir(parents=True, exist_ok=True)
    genanki.Package(deck).write_to_file(str(out_path))


# --------------------------------------------------------------------------
# skipped.json — junk lookup ids we won't re-judge
# --------------------------------------------------------------------------


def load_skipped() -> dict:
    """junk lookup_id -> reason. Empty dict when the file is absent."""
    if not SKIPPED_JSON.exists():
        return {}
    try:
        return json.loads(SKIPPED_JSON.read_text())
    except json.JSONDecodeError as exc:
        raise Fatal(f"{SKIPPED_JSON} is not valid JSON ({exc}). Fix or delete it.")


def save_skipped(data: dict) -> None:
    SKIPPED_JSON.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n")


# --------------------------------------------------------------------------
# Claude
# --------------------------------------------------------------------------


def load_env() -> None:
    if not ENV_FILE.exists():
        return
    for line in ENV_FILE.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        os.environ.setdefault(key.strip(), value.strip().strip("\"'"))


def resolve_language(cli_value: str | None) -> str | None:
    """Target translation language: CLI flag, else TRANSLATION_LANGUAGE in .env.

    Returns None (no translation) when unset or explicitly "none".
    """
    value = (cli_value or os.environ.get("TRANSLATION_LANGUAGE") or "").strip()
    if not value or value.lower() == "none":
        return None
    return value


def cluster_system_prompt(language: str | None = None) -> str:
    """System prompt for the clustering call.

    A `translation` field is requested only when `language` is given; with no
    language the cards stay monolingual.
    """
    translation_bullet = (
        f"  - `translation`: a {language} translation of that same sense (shown "
        "only on the back, so it carries no guessing constraint).\n"
        if language
        else ""
    )
    return f"""\
You cluster a language learner's vocabulary lookups into flashcards.

You receive a JSON array of stem-groups. Each group is one lemma (`stem`) the \
learner looked up, with:
  - `contexts`: the individual lookups, each with a `lookup_id`, the `sentence` \
it appeared in, the `book`, and a `timestamp`.
  - `existing`: cards already in the deck for this stem, each with an `index`, \
a `headword`, and a `definition`. May be empty.

For each group, decide the fate of every context and return any new cards.

Per context, choose a `verdict`:
  - "new": this sense needs a new card. Set `card_index` to the index of the \
entry in THIS group's `new_cards` that it maps to. When several contexts share \
ONE sense, they are ALL "new" with the SAME `card_index` (and you emit that card \
once) — never mark the second one "existing" to mean "same as one I just made".
  - "existing": this lookup maps to one of the pre-existing cards in the \
`existing` array above — meaning it uses the SAME HEADWORD in the SAME SENSE. \
`existing` NEVER refers to a card you are creating now in `new_cards`; if the \
`existing` array is empty, no context can be "existing". Both must hold. The card teaches the learner to \
recall one specific word or expression, so an "existing" match means the lookup \
would be answered by that SAME headword. Two things break a match, each on its \
own:
    - Different meaning, same word → "new" (compare each existing card's \
`definition`, not just its `headword`): existing "sordid" = "morally wrong" and \
a lookup meaning "dirty/squalid" is "new".
    - Different headword, even if the meaning is the same or nearly so → "new". \
A synonym is a different headword: a "couch" lookup is "new" against an existing \
"sofa" card. A bare stem is a different headword from a multi-word expression \
that merely shares that stem: a plain "follow" lookup is "new" against a "follow \
about" card.
Match only when the lookup itself uses that same word or expression in that same \
sense. Set `card_index` to that existing entry's `index`; create no new card for \
it.
  - "junk": not a word worth learning — a proper noun, a person/place name, a \
foreign word, a typo, or an OCR artefact. Set `card_index` to -1 and give a \
short `reason`.

Clustering rules:
  - Distinct meanings of the same stem are DIFFERENT cards (polysemy → sense \
split): "bank" (river) and "bank" (money) are two cards. This applies to \
`existing` cards too: if a lookup uses a different sense than every existing \
card for that stem, it is "new" — e.g. existing "sordid" = "morally wrong" and a \
lookup meaning "dirty/squalid" is a new card, not "existing".
  - Multiple lookups of the SAME sense share ONE card — give them the same \
`card_index` and emit a single `new_cards` entry. The card's PRIMARY context is \
the FIRST of the contexts that map to it (they are given earliest-first); draw \
the card's `span` from that sentence (see below).
  - If a lookup's sense is really part of a multi-word expression or phrasal \
verb (e.g. the stem "make" used as "make off with"), set that card's \
`headword` to the whole expression, not the bare stem. Otherwise `headword` is \
the ordinary dictionary form of the word: verbs as the bare infinitive/base form \
(e.g. "outdid" → "outdo", "ran off" → "run off"), nouns singular, never an \
inflected form as it appeared in the sentence.

Each `new_cards` entry has:
  - `headword`: the word or expression the learner must recall (the answer).
  - `definition`: one concise English definition (~5-20 words) of the sense \
actually used, matching its part of speech. Monolingual English only. Do NOT \
restate the headword, any inflected form of it, or an obvious cognate — the \
learner must guess it from the definition. No examples, etymology, or labels.
{translation_bullet}  - `span`: a list of the exact substrings, copied VERBATIM from the PRIMARY \
context's sentence (the FIRST context mapping to this card), to blank out on the \
card front. Each must occur character-for-character in that sentence — when a \
card collapses several lookups, use the first mapped context's wording, not a \
later context's inflection. Usually one piece (`["make off"]`). \
For a separable phrasal verb split by its object, give each piece separately so \
the object stays visible: "she tied her hair up" for the sense "tie up" → \
`["tied", "up"]`.

Echo each group's `stem`. Return exactly one result per input group and exactly \
one assignment per input context.
"""

def cluster_schema(with_translation: bool = False) -> dict:
    """Structured-output schema for the clustering call.

    Each new card requires a `translation` field only when translating.
    """
    card_props = {
        "headword": {"type": "string"},
        "definition": {"type": "string"},
        "span": {"type": "array", "items": {"type": "string"}},
    }
    card_required = ["headword", "definition", "span"]
    if with_translation:
        card_props["translation"] = {"type": "string"}
        card_required.append("translation")
    return {
        "type": "object",
        "properties": {
            "groups": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "stem": {"type": "string"},
                        "new_cards": {
                            "type": "array",
                            "items": {
                                "type": "object",
                                "properties": card_props,
                                "required": card_required,
                                "additionalProperties": False,
                            },
                        },
                        "assignments": {
                            "type": "array",
                            "items": {
                                "type": "object",
                                "properties": {
                                    "lookup_id": {"type": "string"},
                                    "verdict": {
                                        "type": "string",
                                        "enum": ["new", "existing", "junk"],
                                    },
                                    "card_index": {"type": "integer"},
                                    "reason": {"type": "string"},
                                },
                                "required": [
                                    "lookup_id",
                                    "verdict",
                                    "card_index",
                                    "reason",
                                ],
                                "additionalProperties": False,
                            },
                        },
                    },
                    "required": ["stem", "new_cards", "assignments"],
                    "additionalProperties": False,
                },
            }
        },
        "required": ["groups"],
        "additionalProperties": False,
    }


def cluster_groups(
    client,
    model: str,
    groups: list[dict],
    effort: str | None = None,
    language: str | None = None,
):
    """Send stem-groups to Claude; return {stem: {stem, new_cards, assignments}}.

    `effort` is omitted unless given — cheap models reject the parameter.
    `language`, when set, asks for a translation field in that language.
    """
    schema = cluster_schema(with_translation=bool(language))
    output_config = {"format": {"type": "json_schema", "schema": schema}}
    if effort:
        output_config["effort"] = effort
    response = client.messages.create(
        model=model,
        max_tokens=16000,
        system=[
            {
                "type": "text",
                "text": cluster_system_prompt(language),
                "cache_control": {"type": "ephemeral"},
            }
        ],
        output_config=output_config,
        messages=[
            {"role": "user", "content": json.dumps(groups, ensure_ascii=False)}
        ],
    )
    if response.stop_reason == "refusal":
        raise Fatal("Claude refused the request; nothing was recorded.")
    if response.stop_reason == "max_tokens":
        raise Fatal("Claude hit max_tokens mid-batch; try a smaller batch.")
    text = next((b.text for b in response.content if b.type == "text"), "")
    data = json.loads(text)
    return {g["stem"].strip().lower(): g for g in data["groups"]}


# --------------------------------------------------------------------------
# reporting
# --------------------------------------------------------------------------


def report(candidates: list[Lookup], label: str) -> None:
    by_book: dict[str, int] = defaultdict(int)
    for lk in candidates:
        by_book[lk.title] += 1
    print(f"\n{label}: {len(candidates)} lookup(s)")
    for title, count in sorted(by_book.items(), key=lambda kv: -kv[1]):
        print(f"  {count:>4}  {title}")


def preview(candidates: list[Lookup], n: int = 3) -> None:
    if not candidates:
        return
    print("\nSample cards (definitions are generated on --apply):")
    for lk in candidates[:n]:
        print(f"\n  Front: <definition of {lk.stem!r}>")
        print(f"         {blank_out(lk.sentence, '', lk.word, lk.stem)}")
        print(f"  Back:  {lk.stem}   [{lk.source}]")


# --------------------------------------------------------------------------
# main
# --------------------------------------------------------------------------


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(
        description="Import Kindle Vocabulary Builder lookups into Anki."
    )
    parser.add_argument("--db", help="path to a vocab.db (default: auto-detect Kindle)")
    parser.add_argument(
        "--book",
        action="append",
        default=[],
        metavar="SUBSTRING",
        help="only import words from books whose title contains this "
        "(case-insensitive, repeatable)",
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="cluster with Claude and write notes to Anki (default: dry run)",
    )
    parser.add_argument(
        "--reset",
        action="store_true",
        help="delete all deck notes and clear skipped.json, then reimport all",
    )
    parser.add_argument(
        "--limit", type=int, help="process at most N new lookups this run"
    )
    parser.add_argument("--model", default=DEFAULT_MODEL, help="Claude model id")
    parser.add_argument("--batch-size", type=int, default=BATCH_SIZE)
    parser.add_argument(
        "--language",
        metavar="NAME",
        help="add a back-of-card translation in this language, e.g. 'French' "
        "(default: no translation; falls back to TRANSLATION_LANGUAGE in .env)",
    )
    parser.add_argument(
        "--export",
        metavar="FILE.apkg",
        help="offline mode: read/write state in deck_state.json and write cards "
        "to this Anki package instead of talking to a running Anki",
    )
    args = parser.parse_args(argv)
    export_path = Path(args.export).expanduser() if args.export else None
    offline = export_path is not None

    db_path = resolve_db(args.db)
    lookups = read_lookups(db_path)
    if not lookups:
        raise Fatal(f"No English lookups found in {db_path}.")
    print(f"{len(lookups)} lookup(s) in {db_path.name}")

    selected = [lk for lk in lookups if matches_books(lk, args.book)]
    if args.book:
        print(f"Book filter {args.book}: {len(selected)} lookup(s) match")
    if not selected:
        raise Fatal("No lookups matched the book filter.")

    if args.reset:
        if not args.apply:
            target = "deck_state.json" if offline else "all deck notes"
            print(f"--reset: would delete {target} and clear skipped.json")
        elif offline:
            existed = STATE_JSON.exists()
            save_state([])
            save_skipped({})
            print(f"--reset: cleared deck_state.json ({'was present' if existed else 'was empty'}) and skipped.json")
        else:
            ensure_model()
            ensure_deck()
            ids = anki("findNotes", query=f'deck:"{DECK_NAME}"')
            if ids:
                anki("deleteNotes", notes=ids)
            save_skipped({})
            print(f"--reset: deleted {len(ids)} note(s), cleared skipped.json")

    wiped = args.reset and args.apply
    skipped = {} if wiped else load_skipped()
    stems = sorted({lk.stem.lower() for lk in selected})
    if offline:
        state = [] if wiped else load_state()
        notes_info = state_to_notes_info(state)
    else:
        state = None
        notes_info = [] if wiped else fetch_notes_for_stems(stems)
    existing_index = build_existing_index(notes_info)

    new_lookups = determine_new_lookups(selected, notes_info, set(skipped))
    already = len(selected) - len(new_lookups)
    print(f"{already} lookup(s) already handled; {len(new_lookups)} new")

    if args.limit is not None:
        new_lookups = new_lookups[: args.limit]
        print(f"--limit {args.limit}: processing {len(new_lookups)}")

    report(new_lookups, "To import")

    if not args.apply:
        preview(new_lookups)
        print("\nDry run — nothing written. Re-run with --apply to commit.")
        return 0

    if not new_lookups:
        print("\nNothing to do.")
        return 0

    load_env()
    if not os.environ.get("ANTHROPIC_API_KEY"):
        raise Fatal(
            "ANTHROPIC_API_KEY is not set and was not found in .env.\n"
            "  Add it to .env or export it before running with --apply."
        )

    try:
        import anthropic
    except ImportError as exc:  # pragma: no cover - uv installs this
        raise Fatal(f"The anthropic package is unavailable: {exc}") from exc

    if offline:
        sink: Sink = ApkgSink(export_path, state)
    else:
        ensure_model()
        ensure_deck()
        sink = LiveSink()
    client = anthropic.Anthropic()

    language = resolve_language(args.language)
    lang_note = f", translating to {language}" if language else ""
    print(f"\nClustering {len(new_lookups)} lookup(s) with {args.model}{lang_note}…")
    added, updated, junked = apply_new_cards(
        client,
        args.model,
        new_lookups,
        existing_index,
        skipped,
        args.batch_size,
        language,
        sink=sink,
    )
    save_skipped(skipped)

    if offline:
        print(
            f"\nDone. {added} new card(s); {updated} lookup(s) linked to existing "
            f"cards, {junked} skipped as junk.\n"
            f"Wrote {len(state)} card(s) to {export_path} — import it into Anki "
            f"(File → Import). Re-importing updates in place, never duplicates."
        )
    else:
        print(
            f"\nDone. {added} card(s) added to {DECK_NAME}, "
            f"{updated} lookup(s) linked to existing cards, "
            f"{junked} skipped as junk."
        )
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main(sys.argv[1:]))
    except Fatal as err:
        print(f"\nerror: {err}", file=sys.stderr)
        sys.exit(1)
    except KeyboardInterrupt:
        print("\ninterrupted", file=sys.stderr)
        sys.exit(130)
