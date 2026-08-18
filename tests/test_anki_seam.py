"""Functions that talk to AnkiConnect. The `anki()` HTTP funnel is the seam;
we monkeypatch it with a fake so no running Anki is needed.
"""

import pytest

import kindle_anki
from kindle_anki import Fatal

DECK = "English::Kindle"


class _FakeResp:
    def __init__(self, body: bytes):
        self._body = body

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def read(self):
        return self._body


def test_anki_retries_transient_reset_on_read_action(monkeypatch):
    # A dropped connection mid-response on a read-only action is retried and
    # eventually succeeds — a single reset must not kill the run.
    attempts = {"n": 0}

    def fake_urlopen(req, timeout=None):
        attempts["n"] += 1
        if attempts["n"] < 3:
            raise ConnectionResetError(54, "Connection reset by peer")
        return _FakeResp(b'{"result": 42, "error": null}')

    monkeypatch.setattr(kindle_anki.urllib.request, "urlopen", fake_urlopen)
    monkeypatch.setattr(kindle_anki.time, "sleep", lambda s: None)

    assert kindle_anki.anki("findNotes", query="x") == 42
    assert attempts["n"] == 3


def test_anki_does_not_retry_mutating_action(monkeypatch):
    # addNotes must NOT retry: the reset may have arrived after Anki applied the
    # request, so a retry would double-add. Fail fast with a resume hint.
    attempts = {"n": 0}

    def fake_urlopen(req, timeout=None):
        attempts["n"] += 1
        raise ConnectionResetError(54, "Connection reset by peer")

    monkeypatch.setattr(kindle_anki.urllib.request, "urlopen", fake_urlopen)
    monkeypatch.setattr(kindle_anki.time, "sleep", lambda s: None)

    with pytest.raises(Fatal) as exc:
        kindle_anki.anki("addNotes", notes=[])

    assert attempts["n"] == 1  # no retry
    assert "re-run to resume" in str(exc.value)


def test_fetch_notes_chunks_queries_and_aggregates(monkeypatch):
    queries = []

    def fake_anki(action, **params):
        if action == "findNotes":
            queries.append(params["query"])
            return [len(queries)]  # chunk 1 -> [1], chunk 2 -> [2]
        if action == "notesInfo":
            assert params["notes"] == [1, 2]
            return [{"noteId": n, "fields": {}} for n in params["notes"]]
        raise AssertionError(f"unexpected action {action}")

    monkeypatch.setattr(kindle_anki, "anki", fake_anki)

    info = kindle_anki.fetch_notes_for_stems(["a", "b", "c"], DECK, chunk=2)

    assert len(queries) == 2
    assert queries[0] == f'deck:"{DECK}" ("Stem:a" OR "Stem:b")'
    assert queries[1] == f'deck:"{DECK}" ("Stem:c")'
    assert [n["noteId"] for n in info] == [1, 2]


def test_fetch_notes_returns_empty_without_hitting_anki(monkeypatch):
    def boom(*a, **k):
        raise AssertionError("anki() should not be called for empty stems")

    monkeypatch.setattr(kindle_anki, "anki", boom)
    assert kindle_anki.fetch_notes_for_stems([], DECK) == []


def test_record_existing_link_appends_id_to_lookups(monkeypatch):
    captured = {}

    def fake_anki(action, **params):
        assert action == "updateNoteFields"
        captured.update(params)

    monkeypatch.setattr(kindle_anki, "anki", fake_anki)
    entry = {"note_id": 42, "headword": "bank", "definition": "x", "lookups": "L1,L2"}

    kindle_anki.record_existing_link(entry, "L9")

    assert captured["note"] == {"id": 42, "fields": {"Lookups": "L1,L2,L9"}}


def test_record_existing_link_is_idempotent(monkeypatch):
    captured = {}
    monkeypatch.setattr(
        kindle_anki, "anki", lambda action, **p: captured.update(p)
    )
    entry = {"note_id": 7, "headword": "x", "definition": "y", "lookups": "L1"}

    kindle_anki.record_existing_link(entry, "L1")

    assert captured["note"]["fields"]["Lookups"] == "L1"
