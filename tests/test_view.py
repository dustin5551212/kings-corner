"""The redaction boundary: one hand is yours, everybody else is a count.

The leak sweep is the real test — it plays whole matches and refuses to let a card
that is in somebody's hand (or still in the stock) appear anywhere in a view.
"""

from __future__ import annotations

import json

from kings_corner.engine import PILES, apply, create_match, legal_actions
from kings_corner.policy import choose_action
from kings_corner.view import view


def play_out(state, limit=20000):
    """Advance the engine one action at a time, yielding after every action."""
    steps = 0
    while not state.over:
        steps += 1
        assert steps < limit, "the match stalled"
        action = choose_action(state, state.turn)
        if action is None:
            break
        apply(state, state.turn, action)
        yield state


def test_your_hand_is_the_only_hand_on_the_wire():
    state = create_match(seats=3, seed=9)
    mine = view(state, 1)
    assert len(mine["hand"]) == 7
    assert [o["count"] for o in mine["others"]] == [7, 7]
    assert {o["seat"] for o in mine["others"]} == {0, 2}
    for other in mine["others"]:
        assert "cards" not in other and "hand" not in other
    assert isinstance(mine["stock"], int)
    assert [p["label"] for p in mine["piles"]] == PILES
    assert [p["corner"] for p in mine["piles"]] == [False] * 4 + [True] * 4


def test_the_legal_list_is_the_engine_s():
    state = create_match(seats=2, seed=4)
    for seat in range(2):
        assert view(state, seat)["legal"] == legal_actions(state, seat)


def test_no_view_leaks_a_card_that_is_not_public():
    for seats in (2, 3, 4):
        state = create_match(seats=seats, seed=100 + seats, deals=2)
        for _ in play_out(state):
            hidden = {
                seat: {c["rank"] + c["suit"] for c in state.hands[seat]} for seat in range(seats)
            }
            stock = {c["rank"] + c["suit"] for c in state.stock}
            for seat in range(seats):
                text = json.dumps(view(state, seat), ensure_ascii=False)
                for other, cards in hidden.items():
                    if other == seat:
                        continue
                    for card in cards:
                        assert card not in text, f"seat {seat}'s view shows {card} from seat {other}"
                for card in stock:
                    assert card not in text, f"seat {seat}'s view shows the stock card {card}"


def test_a_drawn_card_is_never_named_in_the_log():
    state = create_match(seats=2, seed=21)
    apply(state, state.turn, {"type": "draw"})
    assert "drew a card" in state.log[0]["text"]
    drawn = state.hands[state.turn][-1]
    assert drawn["rank"] + drawn["suit"] not in state.log[0]["text"]
