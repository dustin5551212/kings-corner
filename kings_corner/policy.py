"""A decent, deterministic bot — good enough to reach every code path.

Used by the test suites and by ``python -m kings_corner``.  The policy is greedy
and honest about it: dump kings into empty corners, play anything that fits,
consolidate piles to free a foundation, then use a freed foundation for a card
with no other home.  It never buries a king, because a king at the bottom of a
pile is the only thing that can still open a corner.
"""

from __future__ import annotations

from .engine import CORNER_PILES, OPEN_PILES, PILES, State, can_move, can_play


def choose_action(state: State, seat: int) -> dict | None:
    if state.phase == "done":
        return None if state.over else {"type": "next-deal"}
    if state.turn != seat:
        return None
    if state.phase == "draw":
        return {"type": "draw"}

    hand = state.hands[seat]

    # 1. A king opens a corner: the one move that adds somewhere new to build.
    for i, card in enumerate(hand):
        if card["rank"] != "K":
            continue
        for pile in CORNER_PILES:
            if not state.piles[pile]:
                return {"type": "play", "card": i, "pile": pile}

    # 2. Anything that fits an existing pile. The hand shrinks, so this is always good.
    for i, card in enumerate(hand):
        for pile in range(len(PILES)):
            if state.piles[pile] and can_play(state, card, pile):
                return {"type": "play", "card": i, "pile": pile}

    # 3. Consolidate: every move empties a pile, and an empty original takes any card.
    # Only original piles can move — a pile with a king at the bottom has nowhere to go
    # (nothing builds on a king) except an empty corner, which step 1 has covered.
    for source in OPEN_PILES:
        if not state.piles[source]:
            continue
        for target in range(len(PILES)):
            if can_move(state, source, target):
                return {"type": "move", "from": source, "to": target}

    # 4. A cleared original pile takes any card at all.
    for pile in OPEN_PILES:
        if state.piles[pile]:
            continue
        if hand:
            # Lowest card first: a king is worth keeping for a corner if one frees up.
            index = min(range(len(hand)), key=lambda i: (hand[i]["rank"] == "K", i))
            return {"type": "play", "card": index, "pile": pile}

    return {"type": "end-turn"}
