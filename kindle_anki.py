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
PROCESSED_JSON = SCRIPT_DIR / "processed.json"
ENV_FILE = SCRIPT_DIR / ".env"

ANKI_URL = "http://127.0.0.1:8765"
DECK_NAME = "English::Kindle"
MODEL_NAME = "Kindle Vocab"
FIELDS = ["Word", "Definition", "Sentence", "Source", "LookupDate"]
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
            SELECT w.stem   AS stem,
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
                stem=stem,
                word=(r["word"] or stem).strip(),
                sentence=(r["usage"] or "").strip(),
                title=(r["title"] or "Unknown book").strip(),
                authors=(r["authors"] or "").strip(),
                timestamp=r["ts"] or 0,
            )
        )
    return out


def first_per_stem(lookups: list[Lookup]) -> "OrderedDict[str, Lookup]":
    """One note per lemma; the first sentence encountered wins."""
    by_stem: OrderedDict[str, Lookup] = OrderedDict()
    for lk in lookups:
        by_stem.setdefault(lk.stem.lower(), lk)
    return by_stem


def matches_books(lk: Lookup, patterns: list[str]) -> bool:
    if not patterns:
        return True
    title = lk.title.lower()
    return any(p.lower() in title for p in patterns)


# --------------------------------------------------------------------------
# text helpers
# --------------------------------------------------------------------------


def blank_out(sentence: str, word: str, stem: str) -> str:
    """Replace the looked-up form with a blank; fall back to the stem.

    If neither matches, leave the sentence intact rather than mangle it.
    """
    for candidate in (word, stem):
        if not candidate:
            continue
        pattern = re.compile(rf"\b{re.escape(candidate)}\w*\b", re.IGNORECASE)
        blanked, n = pattern.subn(BLANK, sentence)
        if n:
            return blanked
    return sentence


def book_tag(title: str) -> str:
    slug = unicodedata.normalize("NFKD", title).encode("ascii", "ignore").decode()
    slug = re.sub(r"[^a-zA-Z0-9]+", "-", slug).strip("-").lower()
    return f"book::{slug or 'unknown'}"


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


def existing_words() -> set[str]:
    """Every Word value already in Anki for our note type / deck."""
    words: set[str] = set()
    queries = []
    if MODEL_NAME in anki("modelNames"):
        queries.append(f'note:"{MODEL_NAME}"')
    if DECK_NAME in anki("deckNames"):
        queries.append(f'deck:"{DECK_NAME}"')
    note_ids: set[int] = set()
    for q in queries:
        note_ids.update(anki("findNotes", query=q))
    if not note_ids:
        return words
    for info in anki("notesInfo", notes=sorted(note_ids)):
        field = (info.get("fields") or {}).get("Word")
        if field and field.get("value"):
            words.add(field["value"].strip().lower())
    return words


# --------------------------------------------------------------------------
# processed.json
# --------------------------------------------------------------------------


def load_processed() -> dict:
    if not PROCESSED_JSON.exists():
        return {"added": {}, "skipped": {}}
    try:
        data = json.loads(PROCESSED_JSON.read_text())
    except json.JSONDecodeError as exc:
        raise Fatal(f"{PROCESSED_JSON} is not valid JSON ({exc}). Fix or delete it.")
    data.setdefault("added", {})
    data.setdefault("skipped", {})
    return data


def save_processed(data: dict) -> None:
    PROCESSED_JSON.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n")


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


SYSTEM_PROMPT = """\
You write dictionary definitions for a language learner's flashcards.

You receive a JSON list of words looked up in a book. Each entry has the lemma \
(`stem`), the inflected form as it appeared (`word`), and the sentence it was \
met in (`sentence`).

For each entry, return one object with:
  - `stem`: echo the stem exactly as given.
  - `status`: "ok" if you can define it, "skip" if it is a proper noun, a \
foreign word, a typo, an OCR artefact, or otherwise not a dictionary word.
  - `definition`: for "ok", a single concise English definition (roughly 5-20 \
words) of the sense actually used in that sentence, matching that part of \
speech. Monolingual English only. Do not restate or include the word itself, \
any inflected form of it, or an obvious cognate — the learner must guess the \
word from the definition. Do not add examples, etymology, or labels. Empty \
string for "skip".
  - `reason`: for "skip", a few words saying why. Empty string for "ok".

Return exactly one object per input entry, in the same order.
"""

RESPONSE_SCHEMA = {
    "type": "object",
    "properties": {
        "definitions": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "stem": {"type": "string"},
                    "status": {"type": "string", "enum": ["ok", "skip"]},
                    "definition": {"type": "string"},
                    "reason": {"type": "string"},
                },
                "required": ["stem", "status", "definition", "reason"],
                "additionalProperties": False,
            },
        }
    },
    "required": ["definitions"],
    "additionalProperties": False,
}


def define_batch(client, model: str, batch: list[Lookup]) -> dict[str, dict]:
    payload = [
        {
            "stem": lk.stem,
            "word": lk.word,
            "sentence": lk.sentence,
            "book": lk.title,
        }
        for lk in batch
    ]
    response = client.messages.create(
        model=model,
        max_tokens=16000,
        system=[
            {
                "type": "text",
                "text": SYSTEM_PROMPT,
                "cache_control": {"type": "ephemeral"},
            }
        ],
        output_config={
            "effort": "low",
            "format": {"type": "json_schema", "schema": RESPONSE_SCHEMA},
        },
        messages=[{"role": "user", "content": json.dumps(payload, ensure_ascii=False)}],
    )
    if response.stop_reason == "refusal":
        raise Fatal("Claude refused the request; nothing was recorded.")
    if response.stop_reason == "max_tokens":
        raise Fatal("Claude hit max_tokens mid-batch; try a smaller --batch-size.")

    text = next((b.text for b in response.content if b.type == "text"), "")
    data = json.loads(text)
    return {item["stem"].strip().lower(): item for item in data["definitions"]}


# --------------------------------------------------------------------------
# reporting
# --------------------------------------------------------------------------


def report(candidates: list[Lookup], label: str) -> None:
    by_book: dict[str, int] = defaultdict(int)
    for lk in candidates:
        by_book[lk.title] += 1
    print(f"\n{label}: {len(candidates)} word(s)")
    for title, count in sorted(by_book.items(), key=lambda kv: -kv[1]):
        print(f"  {count:>4}  {title}")


def preview(candidates: list[Lookup], n: int = 3) -> None:
    if not candidates:
        return
    print("\nSample cards (definitions are generated on --apply):")
    for lk in candidates[:n]:
        print(f"\n  Front: <definition of {lk.stem!r}>")
        print(f"         {blank_out(lk.sentence, lk.word, lk.stem)}")
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
        help="generate definitions and write notes to Anki (default: dry run)",
    )
    parser.add_argument(
        "--limit", type=int, help="process at most N new words this run"
    )
    parser.add_argument("--model", default=DEFAULT_MODEL, help="Claude model id")
    parser.add_argument("--batch-size", type=int, default=BATCH_SIZE)
    args = parser.parse_args(argv)

    db_path = resolve_db(args.db)
    lookups = read_lookups(db_path)
    if not lookups:
        raise Fatal(f"No English lookups found in {db_path}.")

    by_stem = first_per_stem(lookups)
    print(f"{len(lookups)} lookup(s), {len(by_stem)} distinct stem(s) in {db_path.name}")

    selected = [lk for lk in by_stem.values() if matches_books(lk, args.book)]
    if args.book:
        print(f"Book filter {args.book}: {len(selected)} stem(s) match")
    if not selected:
        raise Fatal("No words matched the book filter.")

    processed = load_processed()
    known = set(processed["added"]) | set(processed["skipped"]) | existing_words()

    candidates = [lk for lk in selected if lk.stem.lower() not in known]
    already = len(selected) - len(candidates)
    print(f"{already} already in Anki or recorded; {len(candidates)} new")

    if args.limit is not None:
        candidates = candidates[: args.limit]
        print(f"--limit {args.limit}: processing {len(candidates)}")

    report(candidates, "To import")

    if not args.apply:
        preview(candidates)
        print("\nDry run — nothing written. Re-run with --apply to commit.")
        return 0

    if not candidates:
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

    added = skipped = failed = 0
    batches = [
        candidates[i : i + args.batch_size]
        for i in range(0, len(candidates), args.batch_size)
    ]

    for index, batch in enumerate(batches, start=1):
        print(f"\nBatch {index}/{len(batches)} ({len(batch)} words) — asking Claude…")
        results = define_batch(client, args.model, batch)

        notes = []
        pending: list[Lookup] = []
        for lk in batch:
            item = results.get(lk.stem.lower())
            if item is None:
                print(f"  ! no definition returned for {lk.stem!r} — will retry next run")
                failed += 1
                continue
            if item["status"] == "skip" or not item["definition"].strip():
                processed["skipped"][lk.stem.lower()] = (
                    item.get("reason") or "no definition"
                )
                skipped += 1
                continue
            notes.append(
                {
                    "deckName": DECK_NAME,
                    "modelName": MODEL_NAME,
                    "fields": {
                        "Word": lk.stem,
                        "Definition": item["definition"].strip(),
                        "Sentence": blank_out(lk.sentence, lk.word, lk.stem),
                        "Source": lk.source,
                        "LookupDate": lk.date,
                    },
                    "tags": ["kindle", book_tag(lk.title)],
                    "options": {"allowDuplicate": False, "duplicateScope": "deck"},
                }
            )
            pending.append(lk)

        if notes:
            note_ids = anki("addNotes", notes=notes)
            stamp = datetime.now(timezone.utc).isoformat(timespec="seconds")
            for lk, note_id in zip(pending, note_ids):
                if note_id is None:
                    print(f"  ! Anki rejected {lk.stem!r} (duplicate?) — not recorded")
                    failed += 1
                    continue
                processed["added"][lk.stem.lower()] = stamp
                added += 1

        save_processed(processed)
        print(f"  added {added}, skipped {skipped}, failed {failed} so far")

    print(
        f"\nDone. {added} note(s) added to {DECK_NAME}, "
        f"{skipped} permanently skipped, {failed} left for the next run."
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
