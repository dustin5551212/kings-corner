"""The headless match: same seed, same scorecard, every time."""

from __future__ import annotations

import io

from kings_corner.cli import play


def test_a_headless_match_prints_a_scorecard():
    out = io.StringIO()
    assert play(seats=3, seed=42, deals=3, out=out) == 0
    text = out.getvalue()
    assert "seed 42, 3 seats, 3 deals" in text
    assert "Player 1" in text and "won" in text
    assert "wins with" in text or "share it" in text


def test_the_same_seed_prints_the_same_thing():
    first, second = io.StringIO(), io.StringIO()
    play(seats=2, seed=7, deals=2, out=first)
    play(seats=2, seed=7, deals=2, out=second)
    assert first.getvalue() == second.getvalue()


def test_trace_names_every_action():
    out = io.StringIO()
    play(seats=2, seed=3, deals=1, trace=True, out=out)
    assert "'type': 'draw'" in out.getvalue()
    assert "'type': 'end-turn'" in out.getvalue()
