"""The production-card lifecycle: pure suspend/promote decisions, and the
reconcile_production I/O wrapper that applies them via AnkiConnect.

Production cards are born suspended and auto-promoted (unsuspended) once their
recognition sibling reaches a threshold. The pure decision function carries the
safety-critical `reps > 0` never-touch rule, so it is exercised directly.
"""

import pytest

import kindle_anki
from kindle_anki import Fatal, plan_production_reconcile, threshold_met

# Anki card `type`: 0=new, 1=learning, 2=review, 3=relearning. `queue == -1`
# is suspended. These helpers build the minimal card dicts the planner reads.


def recog(type=0, reps=0, interval=0):
    return {"type": type, "reps": reps, "interval": interval}


def prod(id=1, reps=0, queue=0):
    return {"id": id, "reps": reps, "queue": queue}


# --- threshold_met -------------------------------------------------------


def test_seen_needs_one_rep():
    assert not threshold_met(recog(reps=0), "seen")
    assert threshold_met(recog(reps=1), "seen")


def test_graduated_needs_review_type():
    # In learning (type 1) is not graduated; review (type 2) is.
    assert not threshold_met(recog(type=1, reps=3), "graduated")
    assert threshold_met(recog(type=2, reps=3), "graduated")


def test_mature_needs_review_type_and_long_interval():
    assert not threshold_met(recog(type=2, interval=20), "mature")
    assert threshold_met(recog(type=2, interval=21), "mature")
    # A long interval on a non-review card does not count.
    assert not threshold_met(recog(type=1, interval=99), "mature")


def test_unknown_threshold_is_fatal():
    with pytest.raises(Fatal):
        threshold_met(recog(), "bogus")


# --- plan_production_reconcile -------------------------------------------


def test_new_note_suspends_its_production_card():
    # Both cards brand new: sibling hasn't hit threshold, production is live →
    # suspend it (this is how production cards are "born suspended").
    pairs = [(recog(reps=0), prod(id=7, reps=0, queue=0))]
    suspend, unsuspend = plan_production_reconcile(pairs, "graduated")
    assert suspend == [7]
    assert unsuspend == []


def test_graduated_sibling_promotes_suspended_production_card():
    pairs = [(recog(type=2, reps=4), prod(id=7, reps=0, queue=-1))]
    suspend, unsuspend = plan_production_reconcile(pairs, "graduated")
    assert suspend == []
    assert unsuspend == [7]


def test_already_correct_states_are_left_alone():
    # Suspended + not-yet-met, and live + already-promoted: both no-ops. This is
    # what makes a second run idempotent.
    pairs = [
        (recog(reps=0), prod(id=1, reps=0, queue=-1)),          # correctly suspended
        (recog(type=2, reps=4), prod(id=2, reps=0, queue=0)),   # correctly promoted
    ]
    suspend, unsuspend = plan_production_reconcile(pairs, "graduated")
    assert suspend == []
    assert unsuspend == []


def test_started_production_card_is_never_touched():
    # SAFETY: a production card with reps > 0 has been studied — never suspend or
    # unsuspend it, regardless of the sibling's state or its own queue.
    pairs = [
        (recog(reps=0), prod(id=1, reps=2, queue=0)),           # would-be suspend
        (recog(type=2, reps=4), prod(id=2, reps=1, queue=-1)),  # would-be promote
    ]
    suspend, unsuspend = plan_production_reconcile(pairs, "graduated")
    assert suspend == []
    assert unsuspend == []


def test_threshold_choice_changes_promotion():
    # Sibling seen once but still in learning: promoted under `seen`, held under
    # `graduated`.
    pairs = [(recog(type=1, reps=1), prod(id=9, reps=0, queue=-1))]
    assert plan_production_reconcile(pairs, "seen")[1] == [9]
    assert plan_production_reconcile(pairs, "graduated")[1] == []


# --- reconcile_production (I/O wrapper over the pure planner) -------------


def _fake_anki(monkeypatch, cards, *, existing_decks=()):
    """Stub anki(): serve cards for findCards/cardsInfo and record mutations.
    `cards` is the cardsInfo payload (each dict has cardId, note, ord, ...).
    """
    calls = []

    def fake(action, **params):
        calls.append((action, params))
        if action == "deckNames":
            return list(existing_decks)
        if action == "findCards":
            return [c["cardId"] for c in cards]
        if action == "cardsInfo":
            return cards
        return None

    monkeypatch.setattr(kindle_anki, "anki", fake)
    return calls


def _card(cardId, note, ord, *, type=0, reps=0, queue=0, interval=0, deckName="D::Kindle"):
    return {
        "cardId": cardId,
        "note": note,
        "ord": ord,
        "type": type,
        "reps": reps,
        "queue": queue,
        "interval": interval,
        "deckName": deckName,
    }


def test_reconcile_pairs_by_note_and_promotes(monkeypatch):
    # note 100: recognition graduated, production suspended+untouched → promote.
    cards = [
        _card(1, note=100, ord=0, type=2, reps=5),
        _card(2, note=100, ord=1, reps=0, queue=-1, deckName="D::Kindle::Production"),
    ]
    calls = _fake_anki(monkeypatch, cards, existing_decks=["D::Kindle::Production"])
    kindle_anki.reconcile_production("D::Kindle", "graduated")
    unsuspend = next((p for a, p in calls if a == "unsuspend"), None)
    assert unsuspend is not None and unsuspend["cards"] == [2]
    assert not any(a == "suspend" for a, _ in calls)


def test_reconcile_moves_production_card_into_subdeck(monkeypatch):
    # A freshly created production card still sits in the main deck → move it to
    # the ::Production subdeck.
    cards = [
        _card(1, note=100, ord=0),
        _card(2, note=100, ord=1, deckName="D::Kindle"),
    ]
    calls = _fake_anki(monkeypatch, cards, existing_decks=["D::Kindle::Production"])
    kindle_anki.reconcile_production("D::Kindle", "graduated")
    change = next((p for a, p in calls if a == "changeDeck"), None)
    assert change is not None
    assert change["cards"] == [2] and change["deck"] == "D::Kindle::Production"


def test_reconcile_creates_subdeck_when_missing(monkeypatch):
    cards = [_card(1, note=100, ord=0), _card(2, note=100, ord=1)]
    calls = _fake_anki(monkeypatch, cards, existing_decks=[])
    kindle_anki.reconcile_production("D::Kindle", "graduated")
    created = [p["deck"] for a, p in calls if a == "createDeck"]
    assert "D::Kindle::Production" in created


def test_reconcile_ignores_notes_without_a_production_card(monkeypatch):
    # Empty-card-gated note: only the recognition card exists. Nothing to do.
    cards = [_card(1, note=100, ord=0, type=2, reps=5)]
    calls = _fake_anki(monkeypatch, cards, existing_decks=["D::Kindle::Production"])
    kindle_anki.reconcile_production("D::Kindle", "graduated")
    assert not any(a in {"suspend", "unsuspend", "changeDeck"} for a, _ in calls)
