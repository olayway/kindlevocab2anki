"""End-to-end dry run: real sqlite (temp vocab.db) + faked Anki, no LLM.

Exercises main()'s read/report path against the actual Kindle schema:
resolve_db -> read_lookups -> fetch_notes_for_stems -> determine_new_lookups
-> report/preview. Self-contained so it runs on a fresh clone.
"""

import sqlite3

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
