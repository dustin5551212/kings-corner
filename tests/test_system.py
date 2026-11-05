"""The joined system: a whole match through the engine and the bot.

Conservation is the assertion that catches almost everything — 52 distinct cards
on the table at every instant, never duplicated, never lost.
"""

from __future__ import annotations

import json

from kings_corner.engine import DEALS, apply, create_match, legal_actions
from kings_corner.policy import choose_action
from kings_corner.snapshot import snapshot


def run(seats: int, seed: int, deals: int = 3) -> tuple:
    state = create_match(seats=seats, seed=seed, deals=deals)
    trail = [snapshot(state)]
    steps = 0
    while not state.over:
        steps += 1
        assert steps < 50000, f"seed {seed} never finished"
        action = choose_action(state, state.turn)
        assert action is not None
        apply(state, state.turn, action)
        trail.append(snapshot(state))
        assert_fifty_two_cards(state)
    return state, trail


def assert_fifty_two_cards(state) -> None:
    cards = [c for hand in state.hands for c in hand]
    cards += [c for pile in state.piles for c in pile]
    cards += state.stock
    labels = [c["rank"] + c["suit"] for c in cards]
    assert len(labels) == 52, f"{len(labels)} cards on the table"
    assert len(set(labels)) == 52, "a card is in two places at once"


def test_every_match_conserves_all_fifty_two_cards_and_finishes():
    for seats in (2, 3, 4):
        for seed in range(1, 13):
            state, trail = run(seats, seed)
            assert state.over
            assert state.deal == state.deals
            assert len(trail) > 10
            assert sum(s["won"] for s in state.seats) <= state.deals


def test_the_same_seed_is_the_same_match_twice():
    a, trail_a = run(3, 4242)
    b, trail_b = run(3, 4242)
    assert json.dumps(trail_a, sort_keys=True) == json.dumps(trail_b, sort_keys=True)
    assert [s["won"] for s in a.seats] == [s["won"] for s in b.seats]


def test_a_different_seed_is_a_different_match():
    _, one = run(3, 1)
    _, two = run(3, 2)
    assert json.dumps(one[3], sort_keys=True) != json.dumps(two[3], sort_keys=True)


def test_the_bot_never_gets_stuck_between_phases():
    """Every action the bot returns must be one the engine accepts."""
    for seats in (2, 3, 4):
        for seed in range(1, 40):
            state = create_match(seats=seats, seed=seed, deals=2)
            guard = 0
            while not state.over:
                guard += 1
                assert guard < 50000
                action = choose_action(state, state.turn)
                if action is None:
                    break
                apply(state, state.turn, action)  # RuleError fails the test
            assert state.deal == state.deals


def test_a_naive_picker_cannot_loop_forever():
    """Always take the first legal action — the shape of a careless client.

    If the rules ever allow a move that undoes itself, this is what catches it: a
    table that pings a king between two empty corners never ends.
    """
    for seats in (2, 3, 4):
        for seed in range(1, 9):
            state = create_match(seats=seats, seed=seed, deals=2)
            for _ in range(30000):
                actions = legal_actions(state, state.turn)
                assert actions, "a turn with nothing legal on it"
                apply(state, state.turn, actions[0])
                if state.over:
                    break
            else:
                raise AssertionError(f"seed {seed} / {seats} seats never finished")


def test_default_match_length_is_five_deals():
    assert DEALS == 5
    assert create_match(seats=2, seed=1).deals == 5