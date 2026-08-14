#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.11"
# dependencies = ["anthropic>=0.70"]
# ///
"""Import Kindle Vocabulary Builder lookups into Anki as production cards.

Reads vocab.db from a mounted Kindle (or a local cache), generates a
context-aware English definition for each new word with the Claude API, and
creates one Anki note per lemma via AnkiConnect.

Default action is a dry run. Pass --apply to actually call Claude and write
notes to Anki.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import sqlite3
import sys
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
ENV_FILE = SCRIPT_DIR / ".env"

ANKI_URL = "http://127.0.0.1:8765"
DECK_NAME = "English::Kindle"
MODEL_NAME = "Kindle Vocab"
FIELDS = [
    "Stem",
    "Word",
    "Polish",
    "Definition",
    "Sentence",
    "Source",
    "LookupDate",
    "Lookups",
]
BLANK = "_____"

DEFAULT_MODEL = "claude-opus-5"
BATCH_SIZE = 40


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
            for lk in lookups
        ],
        "existing": existing,
    }


@dataclass
class BuildResult:
    notes: list[dict]  # addNotes payloads, one per new card
    existing: list[dict]  # {"card_index", "lookup_id"} links to record
    junk: list[dict]  # {"lookup_id", "reason"} for skipped.json


def build_notes(stem: str, lookups: list[Lookup], response: dict) -> BuildResult:
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
        card = new_cards[idx]
        primary = min(lks, key=lambda l: l.timestamp)
        notes.append(
            {
                "deckName": DECK_NAME,
                "modelName": MODEL_NAME,
                "fields": {
                    "Stem": stem,
                    "Word": card["headword"],
                    "Polish": card.get("polish", ""),
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
                },
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
) -> tuple[int, int, int]:
    """Cluster new lookups in batches and write the outcomes to Anki.

    Returns (added, updated, junked). `skipped` is mutated and persisted after
    every batch so an interrupted run resumes cleanly.
    """
    stem_items = list(group_by_stem(new_lookups).items())
    added = updated = junked = 0

    for i in range(0, len(stem_items), batch_size):
        batch = stem_items[i : i + batch_size]
        payloads = [
            build_group_payload(stem, lks, existing_index) for stem, lks in batch
        ]
        responses = cluster_groups(client, model, payloads)

        for stem, lks in batch:
            response = responses.get(stem)
            if response is None:
                print(f"  ! no response for stem {stem!r} — leaving for next run")
                continue
            result = build_notes(stem, lks, response)

            if result.notes:
                anki("addNotes", notes=result.notes)
                added += len(result.notes)

            entries = existing_index.get(stem, [])
            for link in result.existing:
                idx = link["card_index"]
                if not isinstance(idx, int) or not 0 <= idx < len(entries):
                    print(f"  ! {stem!r}: bad existing index {idx} — skipping")
                    continue
                record_existing_link(entries[idx], link["lookup_id"])
                updated += 1

            for j in result.junk:
                skipped[j["lookup_id"]] = j["reason"]
                junked += 1

        save_skipped(skipped)

    return added, updated, junked


# --------------------------------------------------------------------------
# AnkiConnect
# --------------------------------------------------------------------------


def anki(action: str, **params):
    payload = json.dumps({"action": action, "version": 6, "params": params}).encode()
    req = urllib.request.Request(
        ANKI_URL, data=payload, headers={"Content-Type": "application/json"}
    )
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            body = json.load(resp)
    except urllib.error.URLError as exc:
        raise Fatal(
            f"Cannot reach AnkiConnect at {ANKI_URL} ({exc}).\n"
            "  Start Anki and make sure the AnkiConnect add-on is installed."
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
.polish { font-size: 20px; color: #444; margin-top: 0.2em; }
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
{{#Polish}}<div class="polish">{{Polish}}</div>{{/Polish}}
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
    if os.environ.get("ANTHROPIC_API_KEY"):
        return
    if not ENV_FILE.exists():
        return
    for line in ENV_FILE.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        os.environ.setdefault(key.strip(), value.strip().strip("\"'"))


CLUSTER_SYSTEM_PROMPT = """\
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
entry in THIS group's `new_cards` that it maps to.
  - "existing": this lookup means the same as one of the `existing` cards. Set \
`card_index` to that existing entry's `index`; create no new card for it.
  - "junk": not a word worth learning — a proper noun, a person/place name, a \
foreign word, a typo, or an OCR artefact. Set `card_index` to -1 and give a \
short `reason`.

Clustering rules:
  - Distinct meanings of the same stem are DIFFERENT cards (polysemy → sense \
split): "bank" (river) and "bank" (money) are two cards.
  - Multiple lookups of the SAME sense share ONE card — give them the same \
`card_index` and emit a single `new_cards` entry.
  - If a lookup's sense is really part of a multi-word expression or phrasal \
verb (e.g. the stem "make" used as "make off with"), set that card's \
`headword` to the whole expression, not the bare stem. Otherwise `headword` is \
the ordinary dictionary form of the word.

Each `new_cards` entry has:
  - `headword`: the word or expression the learner must recall (the answer).
  - `definition`: one concise English definition (~5-20 words) of the sense \
actually used, matching its part of speech. Monolingual English only. Do NOT \
restate the headword, any inflected form of it, or an obvious cognate — the \
learner must guess it from the definition. No examples, etymology, or labels.
  - `polish`: a Polish translation of that same sense (shown only on the back, \
so it carries no guessing constraint).
  - `span`: a list of the exact substrings, copied VERBATIM from the primary \
context's sentence, to blank out on the card front. Each must occur \
character-for-character in that sentence. Usually one piece (`["make off"]`). \
For a separable phrasal verb split by its object, give each piece separately so \
the object stays visible: "she tied her hair up" for the sense "tie up" → \
`["tied", "up"]`.

Echo each group's `stem`. Return exactly one result per input group and exactly \
one assignment per input context.
"""

CLUSTER_SCHEMA = {
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
                            "properties": {
                                "headword": {"type": "string"},
                                "definition": {"type": "string"},
                                "polish": {"type": "string"},
                                "span": {
                                    "type": "array",
                                    "items": {"type": "string"},
                                },
                            },
                            "required": ["headword", "definition", "polish", "span"],
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


def cluster_groups(client, model: str, groups: list[dict], effort: str | None = None):
    """Send stem-groups to Claude; return {stem: {stem, new_cards, assignments}}.

    `effort` is omitted unless given — cheap models reject the parameter.
    """
    output_config = {"format": {"type": "json_schema", "schema": CLUSTER_SCHEMA}}
    if effort:
        output_config["effort"] = effort
    response = client.messages.create(
        model=model,
        max_tokens=16000,
        system=[
            {
                "type": "text",
                "text": CLUSTER_SYSTEM_PROMPT,
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
    args = parser.parse_args(argv)

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
        if args.apply:
            ensure_model()
            ensure_deck()
            ids = anki("findNotes", query=f'deck:"{DECK_NAME}"')
            if ids:
                anki("deleteNotes", notes=ids)
            save_skipped({})
            print(f"--reset: deleted {len(ids)} note(s), cleared skipped.json")
        else:
            print("--reset: would delete all deck notes and clear skipped.json")

    skipped = {} if (args.reset and args.apply) else load_skipped()
    stems = sorted({lk.stem.lower() for lk in selected})
    notes_info = [] if (args.reset and args.apply) else fetch_notes_for_stems(stems)
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

    ensure_model()
    ensure_deck()
    client = anthropic.Anthropic()

    print(f"\nClustering {len(new_lookups)} lookup(s) with {args.model}…")
    added, updated, junked = apply_new_cards(
        client,
        args.model,
        new_lookups,
        existing_index,
        skipped,
        args.batch_size,
    )
    save_skipped(skipped)

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
