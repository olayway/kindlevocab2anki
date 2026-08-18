"""End-to-end dry run: real sqlite (temp vocab.db) + faked Anki, no LLM.

Exercises main()'s read/report path against the actual Kindle schema:
resolve_db -> read_lookups -> fetch_notes_for_stems -> determine_new_lookups
-> report/preview. Self-contained so it runs on a fresh clone.
"""

import sqlite3

import pytest

import kindle_anki


def make_vocab_db(path):
    conn = sqlite3.connect(path)
    conn.executescript(
        """
        CREATE TABLE WORDS (id TEXT, word TEXT, stem TEXT, lang TEXT);
        CREATE TABLE BOOK_INFO (id TEXT, title TEXT, authors TEXT);
        CREATE TABLE LOOKUPS (id TEXT, word_key TEXT, book_key TEXT,
                              usage TEXT, timestamp INTEGER);
        """
    )
    conn.execute("INSERT INTO WORDS VALUES ('w1','banks','bank','en')")
    conn.execute("INSERT INTO WORDS VALUES ('w2','afflicted','afflict','en')")
    conn.execute("INSERT INTO WORDS VALUES ('w3','palabra','palabra','es')")  # non-en
    conn.execute("INSERT INTO BOOK_INFO VALUES ('b1','Some Book','An Author')")
    conn.executemany(
        "INSERT INTO LOOKUPS VALUES (?,?,?,?,?)",
        [
            ("L1", "w1", "b1", "The river banks flooded.", 1000),
            ("L2", "w2", "b1", "Diseases that afflict us.", 2000),
            ("L3", "w3", "b1", "una palabra", 3000),  # dropped: lang != en
        ],
    )
    conn.commit()
    conn.close()


def test_dry_run_reads_reports_and_writes_nothing(tmp_path, monkeypatch, capsys):
    db = tmp_path / "vocab.db"
    make_vocab_db(db)
    # resolve_db copies the source to CACHE_DB; point that at the tmp dir too.
    monkeypatch.setattr(kindle_anki, "CACHE_DB", tmp_path / "cache.db")

    def fake_anki(action, **params):
        assert action in {"findNotes", "notesInfo"}, f"dry run must not {action}"
        return []

    monkeypatch.setattr(kindle_anki, "anki", fake_anki)

    rc = kindle_anki.main(["--db", str(db)])

    assert rc == 0
    out = capsys.readouterr().out
    # 2 English lookups survive (the Spanish one is filtered by read_lookups).
    assert "2 lookup(s)" in out
    assert "0 lookup(s) already handled; 2 new" in out
    assert "Dry run — nothing written" in out


def make_mislabeled_db(path):
    """A French book whose words Kindle tagged 'en' (wrong ebook metadata),
    alongside a genuinely English book — the exact --force-lang scenario."""
    conn = sqlite3.connect(path)
    conn.executescript(
        """
        CREATE TABLE WORDS (id TEXT, word TEXT, stem TEXT, lang TEXT);
        CREATE TABLE BOOK_INFO (id TEXT, title TEXT, authors TEXT);
        CREATE TABLE LOOKUPS (id TEXT, word_key TEXT, book_key TEXT,
                              usage TEXT, timestamp INTEGER);
        """
    )
    conn.execute("INSERT INTO WORDS VALUES ('w1','serpent','serpent','en')")
    conn.execute("INSERT INTO WORDS VALUES ('w2','forêt','forêt','en')")
    conn.execute("INSERT INTO WORDS VALUES ('w3','banks','bank','en')")
    conn.execute("INSERT INTO BOOK_INFO VALUES ('b1','Le Petit Prince','Saint-Exupéry')")
    conn.execute("INSERT INTO BOOK_INFO VALUES ('b2','1984','Orwell')")
    conn.executemany(
        "INSERT INTO LOOKUPS VALUES (?,?,?,?,?)",
        [
            ("L1", "w1", "b1", "un serpent boa qui avalait.", 1000),
            ("L2", "w2", "b1", "la forêt vierge.", 2000),
            ("L3", "w3", "b2", "The river banks flooded.", 3000),
        ],
    )
    conn.commit()
    conn.close()


def test_force_lang_requires_book(tmp_path, monkeypatch):
    db = tmp_path / "vocab.db"
    make_mislabeled_db(db)
    monkeypatch.setattr(kindle_anki, "CACHE_DB", tmp_path / "cache.db")
    with pytest.raises(kindle_anki.Fatal, match="--force-lang needs --book"):
        kindle_anki.main(["--db", str(db), "--learning", "fr", "--force-lang"])


def test_force_lang_reads_mislabeled_book_only(tmp_path, monkeypatch, capsys):
    db = tmp_path / "vocab.db"
    make_mislabeled_db(db)
    monkeypatch.setattr(kindle_anki, "CACHE_DB", tmp_path / "cache.db")

    def fake_anki(action, **params):
        assert action in {"findNotes", "notesInfo"}, f"dry run must not {action}"
        return []

    monkeypatch.setattr(kindle_anki, "anki", fake_anki)

    rc = kindle_anki.main(
        ["--db", str(db), "--learning", "fr", "--force-lang", "--book", "Petit Prince"]
    )

    assert rc == 0
    out = capsys.readouterr().out
    # Only the two French-book lookups; the English '1984' banks is left out.
    assert "2 lookup(s) from ['Petit Prince'] read as French" in out
    assert "banks" not in out
