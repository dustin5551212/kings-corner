"""The rules, in Python idiom — one behaviour per test."""

from __future__ import annotations

import pytest

from kings_corner.engine import (
    CORNER_PILES,
    HAND,
    PILES,
    RuleError,
    State,
    apply,
    builds,
    can_move,
    can_play,
    create_match,
    is_bot,
    legal_actions,
    make_rng,
)


def C(rank: str, suit: str) -> dict:
    return {"rank": rank, "suit": suit}


def make_state(hands, piles, stock=None, phase="play", turn=0, deals=3, seats=2):
    """A table built by hand, so a test can say exactly which card is where."""
    state = State(
        seats=[{"name": f"P{i + 1}", "won": 0} for i in range(seats)],
        deals=deals,
        dealer=seats - 1,
        turn=turn,
        phase=phase,
        rng=make_rng(1),
    )
    state.hands = [list(h) for h in hands]
    state.piles = [list(p) for p in piles] + [[] for _ in range(len(PILES) - len(piles))]
    state.stock = list(stock or [])
    return state


def table(**over):
    """A legal, quiet table: four originals topped with aces, four empty corners."""
    hands = over.pop("hands", [[C("K", "\u2660")], [C("K", "\u2665")]])
    piles = over.pop("piles", [[C("A", "\u2660")], [C("A", "\u2665")], [C("A", "\u2666")], [C("A", "\u2663")]])
    return make_state(hands, piles, **over)


# ── the deal ────────────────────────────────────────────────────────────


def test_a_deal_is_seven_each_four_face_up_and_the_rest_in_the_stock():
    for seats in (2, 3, 4):
        state = create_match(seats=seats, seed=11)
        assert [len(h) for h in state.hands] == [HAND] * seats
        assert [len(p) for p in state.piles[:4]] == [1, 1, 1, 1]
        assert [p for p in state.piles[4:]] == [[], [], [], []]
        assert len(state.stock) == 52 - HAND * seats - 4

        cards = [c for hand in state.hands for c in hand] + [c for p in state.piles for c in p] + state.stock
        labels = {c["rank"] + c["suit"] for c in cards}
        assert len(cards) == 52 and len(labels) == 52


def test_a_new_deal_starts_with_a_draw_and_the_player_left_of_the_dealer():
    state = create_match(seats=3, seed=5)
    assert state.phase == "draw"
    assert state.turn == (state.dealer + 1) % 3
    assert legal_actions(state, state.turn) == [{"type": "draw"}]
    assert legal_actions(state, (state.turn + 1) % 3) == []


# ── what builds on what ─────────────────────────────────────────────────


def test_cards_build_down_in_rank_in_the_other_colour():
    assert builds(C("9", "\u2665"), C("10", "\u2660"))
    assert builds(C("9", "\u2660"), C("10", "\u2665"))
    assert not builds(C("9", "\u2660"), C("10", "\u2660"))  # same colour
    assert not builds(C("8", "\u2665"), C("10", "\u2660"))  # a gap
    assert not builds(C("10", "\u2665"), C("9", "\u2660"))  # upside down
    assert not builds(C("K", "\u2665"), C("A", "\u2660"))  # nothing builds on an ace


def test_moving_a_pile_needs_its_bottom_card_to_fit():
    state = table(piles=[[C("7", "\u2660"), C("6", "\u2665"), C("5", "\u2660")], [C("8", "\u2665")]])
    assert can_move(state, 0, 1)  # 7\u2660 sits on 8\u2665; the rest comes along
    assert not can_move(state, 1, 0)  # 8\u2665 does not sit on 5\u2660
    apply(state, 0, {"type": "move", "from": 0, "to": 1})
    assert state.piles[0] == []
    assert [len(state.piles[1]), state.piles[1][-1]] == [4, C("5", "\u2660")]


# ── the corners ──────────────────────────────────────────────────────────


def test_only_a_king_starts_a_corner():
    state = table(hands=[[C("Q", "\u2665"), C("K", "\u2663")], []])
    assert can_play(state, C("K", "\u2663"), CORNER_PILES[0])
    assert not can_play(state, C("Q", "\u2665"), CORNER_PILES[0])
    with pytest.raises(RuleError, match="king"):
        apply(state, 0, {"type": "play", "card": 0, "pile": CORNER_PILES[0]})


def test_a_king_dealt_to_an_original_pile_may_move_to_an_empty_corner():
    state = table(piles=[[C("K", "\u2660")], [C("A", "\u2665")], [C("A", "\u2666")], [C("A", "\u2663")]])
    assert can_move(state, 0, CORNER_PILES[0])
    apply(state, 0, {"type": "move", "from": 0, "to": CORNER_PILES[0]})
    assert state.piles[0] == [] and state.piles[CORNER_PILES[0]] == [C("K", "\u2660")]


def test_a_corner_pile_cannot_shuffle_to_another_empty_corner():
    state = table(piles=[[C("A", "\u2660")], [C("A", "\u2665")], [C("A", "\u2666")], [C("A", "\u2663")],
                         [C("K", "\u2660")]])
    assert not can_move(state, 4, 5)
    assert can_move(state, 0, 4) is False  # an ace is not a king


def test_a_pile_that_does_not_start_with_a_king_cannot_fill_a_corner():
    state = table(piles=[[C("Q", "\u2660"), C("J", "\u2665")], [C("A", "\u2663")]])
    assert not can_move(state, 0, CORNER_PILES[0])
    with pytest.raises(RuleError, match="king"):
        apply(state, 0, {"type": "move", "from": 0, "to": CORNER_PILES[0]})


def test_a_cleared_original_pile_takes_any_card_from_the_hand():
    state = table(hands=[[C("4", "\u2665")], []], piles=[[], [C("A", "\u2665")]])
    assert can_play(state, C("4", "\u2665"), 0)
    apply(state, 0, {"type": "play", "card": 0, "pile": 0})
    assert state.piles[0] == [C("4", "\u2665")] and state.hands[0] == []


# ── a turn ──────────────────────────────────────────────────────────────


def test_the_draw_comes_first_and_only_once():
    state = table(hands=[[C("K", "\u2660")], []], stock=[C("2", "\u2663"), C("3", "\u2663")])
    state.phase = "draw"
    with pytest.raises(RuleError, match="draw first"):
        apply(state, 0, {"type": "play", "card": 0, "pile": CORNER_PILES[0]})
    apply(state, 0, {"type": "draw"})
    assert state.phase == "play" and len(state.hands[0]) == 2
    with pytest.raises(RuleError, match="already drawn"):
        apply(state, 0, {"type": "draw"})
    apply(state, 0, {"type": "play", "card": 0, "pile": CORNER_PILES[0]})
    apply(state, 0, {"type": "end-turn"})
    assert state.turn == 1 and state.phase == "draw"


def test_playing_does_not_end_the_turn_but_ending_it_does():
    state = table(hands=[[C("K", "\u2660"), C("Q", "\u2665")], []])
    apply(state, 0, {"type": "play", "card": 0, "pile": CORNER_PILES[0]})
    assert state.turn == 0
    assert {"type": "play", "card": 0, "pile": CORNER_PILES[0]} in legal_actions(state, 0)
    apply(state, 0, {"type": "end-turn"})
    assert state.turn == 1


def test_you_cannot_play_out_of_turn_or_onto_the_wrong_card():
    state = table(hands=[[C("2", "\u2660")], [C("K", "\u2665")]])
    with pytest.raises(RuleError, match="not your turn"):
        apply(state, 1, {"type": "play", "card": 0, "pile": CORNER_PILES[0]})
    with pytest.raises(RuleError, match="does not go on"):
        apply(state, 0, {"type": "play", "card": 0, "pile": 0})
    with pytest.raises(RuleError, match="no such pile"):
        apply(state, 0, {"type": "play", "card": 0, "pile": 99})


# ── ending a deal ───────────────────────────────────────────────────────


def test_empty_your_hand_and_the_deal_is_yours():
    state = table(hands=[[C("K", "\u2660")], [C("3", "\u2665"), C("4", "\u2660")]])
    apply(state, 0, {"type": "play", "card": 0, "pile": CORNER_PILES[0]})
    assert state.phase == "done"
    assert state.winner == 0 and state.ended_by == "out"
    assert state.seats[0]["won"] == 1
    assert legal_actions(state, 1) == [{"type": "next-deal"}]


def test_a_full_round_of_nothing_with_an_empty_stock_is_an_impasse():
    # Nobody can move: every pile is topped with an ace, and no corner is empty.
    state = table(
        hands=[[C("5", "\u2665")], [C("4", "\u2660")]],
        piles=[
            [C("A", "\u2660")], [C("A", "\u2665")], [C("A", "\u2666")], [C("A", "\u2663")],
            [C("K", "\u2660")], [C("K", "\u2665")], [C("K", "\u2666")], [C("K", "\u2663")],
        ],
    )
    assert state.stock == []
    assert [a for a in legal_actions(state, 0) if a["type"] in ("play", "move")] == []
    apply(state, 0, {"type": "end-turn"})
    assert state.phase == "play"
    apply(state, 1, {"type": "end-turn"})
    assert state.phase == "done"
    assert state.ended_by == "impasse" and state.winner is None
    assert [s["won"] for s in state.seats] == [0, 0]


def test_passes_are_cleared_by_a_play():
    state = table(
        hands=[[C("5", "\u2665")], [C("2", "\u2665"), C("9", "\u2663")]],
        piles=[[C("3", "\u2660")], [C("A", "\u2665")], [C("A", "\u2666")], [C("A", "\u2663")],
               [C("2", "\u2660")], [C("2", "\u2665")], [C("2", "\u2666")], [C("2", "\u2663")]],
    )
    apply(state, 0, {"type": "end-turn"})
    assert state.passes == 1 and state.turn == 1
    apply(state, 1, {"type": "play", "card": 0, "pile": 0})
    assert state.passes == 0 and state.turn == 1
    apply(state, 1, {"type": "end-turn"})
    assert state.passes == 0 and state.phase != "done"


def test_a_draw_with_an_empty_stock_is_refused():
    state = table(hands=[[C("5", "\u2665")], []], phase="draw")
    with pytest.raises(RuleError, match="stock is empty"):
        apply(state, 0, {"type": "draw"})


def test_the_match_ends_after_the_agreed_number_of_deals():
    state = table(hands=[[C("K", "\u2660")], [C("3", "\u2665")]], deals=1)
    apply(state, 0, {"type": "play", "card": 0, "pile": CORNER_PILES[0]})
    assert state.over is True
    assert legal_actions(state, 0) == []
    with pytest.raises(RuleError, match="match is over"):
        apply(state, 0, {"type": "next-deal"})


def test_between_deals_any_seat_may_deal_the_next_one():
    state = create_match(seats=2, seed=3, deals=3)
    state.phase = "done"
    state.deal = 1
    apply(state, 1, {"type": "next-deal"})
    assert state.deal == 2 and state.phase == "draw"


def test_a_seat_can_be_a_cpu():
    state = create_match(seats=[{"name": "Ada"}, {"name": "CPU 1", "bot": True}], seed=2)
    assert [s["name"] for s in state.seats] == ["Ada", "CPU 1"]
    assert [s["bot"] for s in state.seats] == [False, True]
    assert is_bot(state, 1) and not is_bot(state, 0)
    # A seat number that is not a seat is not a CPU either.
    assert is_bot(state, 9) is False


def test_a_mixed_table_still_deals_seven_each():
    """The people/CPU mix is a seating question; the deal does not care."""
    state = create_match(
        seats=[{"name": "Ada"}, {"name": "CPU 1", "bot": True}, {"name": "CPU 2", "bot": True}],
        seed=8,
    )
    assert [len(h) for h in state.hands] == [HAND] * 3
    assert len(state.stock) == 52 - HAND * 3 - 4


def test_a_seat_count_outside_two_to_four_is_refused():
    with pytest.raises(RuleError, match="2 to 4"):
        create_match(seats=1, seed=1)
    with pytest.raises(RuleError, match="2 to 4"):
        create_match(seats=5, seed=1)
