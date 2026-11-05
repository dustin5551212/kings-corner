"""Kings Corners — the rules, with no UI and no network in them.

Ruleset: Bicycle's *Kings Corner* (bicyclecards.com/how-to-play/kings-corner).

Eight piles on the table: four originals (N, E, S, W) and four corners (NW, NE,
SE, SW).  Cards build down in rank, red on black and black on red.  Only a king
may begin a corner.  Empty an original pile and any card from your hand may start
it again.  A turn is: draw one, then play as many cards as you like.  The first
player to empty their hand wins the deal.

The whole game lives in a :class:`State` so a server can hold it, redact it per
player (``view.py``), and a test can play ten thousand deals through it without a
browser.  Nothing here knows about HTML, HTTP or seats that can go offline.

Naming: internal fields are snake_case; the wire format in ``view.py`` is
camelCase, matching the design system the other tables use.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any, Callable

RANKS = ["A", "2", "3", "4", "5", "6", "7", "8", "9", "10", "J", "Q", "K"]
SUITS = ["\u2660", "\u2665", "\u2666", "\u2663"]

#: The eight piles, in the order the wire and the client index them.
PILES = ["N", "E", "S", "W", "NW", "NE", "SE", "SW"]
#: The four piles that were dealt face up.  Empty, any card may start them.
OPEN_PILES = (0, 1, 2, 3)
#: The four diagonal corners.  Only a king may start them.
CORNER_PILES = (4, 5, 6, 7)

HAND = 7
MIN_SEATS = 2
MAX_SEATS = 4
#: Deals in a match.  The winner is whoever takes most of them.
DEALS = 5


class RuleError(Exception):
    """An illegal move. The server turns this into a refusal; a test into a failure."""


def card_value(rank: str) -> int:
    """Ace low (1) through king (13) — the build order."""
    return RANKS.index(rank) + 1


def is_red(suit: str) -> bool:
    return suit == "\u2665" or suit == "\u2666"


def label(card: dict | None) -> str:
    return card["rank"] + card["suit"] if card else ""


def card(rank: str, suit: str) -> dict:
    return {"rank": rank, "suit": suit}


def build_deck() -> list[dict]:
    return [card(rank, suit) for suit in SUITS for rank in RANKS]


def _imul32(a: int, b: int) -> int:
    """Math.imul: the low 32 bits of a two's-complement 32-bit multiply."""
    return (a * b) & 0xFFFFFFFF


def make_rng(seed: int | None = None) -> Callable[[], float]:
    """mulberry32 — small, fast and seedable, so a failing test can be replayed."""
    a = (int(time.time() * 1000) if seed is None else int(seed)) & 0xFFFFFFFF

    def rng() -> float:
        nonlocal a
        a = (a + 0x6D2B79F5) & 0xFFFFFFFF
        t = a
        t = _imul32(t ^ (t >> 15), (t | 1) & 0xFFFFFFFF)
        t = (t ^ ((t + _imul32((t ^ (t >> 7)) & 0xFFFFFFFF, (t | 61) & 0xFFFFFFFF)) & 0xFFFFFFFF)) & 0xFFFFFFFF
        return ((t ^ (t >> 14)) & 0xFFFFFFFF) / 4294967296.0

    return rng


def shuffle(deck: list[dict], rng: Callable[[], float]) -> list[dict]:
    for i in range(len(deck) - 1, 0, -1):
        j = int(rng() * (i + 1))
        deck[i], deck[j] = deck[j], deck[i]
    return deck


@dataclass
class State:
    seats: list[dict] = field(default_factory=list)
    deal: int = 1
    deals: int = DEALS
    dealer: int = 0
    turn: int = 0
    phase: str = "draw"  # "draw" | "play" | "done"
    stock: list[dict] = field(default_factory=list)
    piles: list[list[dict]] = field(default_factory=list)
    hands: list[list[dict]] = field(default_factory=list)
    #: True once the player to move has played or moved something this turn.
    acted: bool = False
    #: Consecutive turn-ends in which nobody played anything.
    passes: int = 0
    winner: int | None = None
    #: Why the deal ended: "out" (a hand emptied) or "impasse" (nobody could move).
    ended_by: str | None = None
    over: bool = False
    log: list[dict] = field(default_factory=list)
    # A function does not survive JSON, so it is not part of the surface state.
    rng: Callable[[], float] | None = None


def _push_log(state: State, text: str, hl: bool = False) -> None:
    state.log.insert(0, {"text": text, "hl": bool(hl), "deal": state.deal})
    if len(state.log) > 7:
        state.log.pop()


def _start_deal(state: State) -> None:
    """Seven cards each, four face up in the cross, the rest face down in the middle."""
    rng = state.rng
    assert rng is not None  # create_match always installs one
    deck = shuffle(build_deck(), rng)

    state.hands = [[deck.pop() for _ in range(HAND)] for _ in state.seats]
    # North, East, South, West — one card each, face up. The corners start empty.
    state.piles = [[deck.pop()] if i < len(OPEN_PILES) else [] for i in range(len(PILES))]
    state.stock = deck

    state.dealer = (state.dealer + 1) % len(state.seats)
    state.turn = (state.dealer + 1) % len(state.seats)
    # Bicycle: draw first. An empty stock means the turn starts with the plays.
    state.phase = "draw" if state.stock else "play"
    state.acted = False
    state.passes = 0
    state.winner = None
    state.ended_by = None
    state.log = []
    _push_log(state, f"deal {state.deal} — {state.seats[state.turn]['name']} leads", True)


def create_match(seats: Any = None, seed: int | None = None, deals: int = DEALS) -> State:
    if isinstance(seats, (list, tuple)):
        count = len(seats)
        names = [
            s if isinstance(s, str) else (s.get("name") if isinstance(s, dict) else None) or f"Player {i + 1}"
            for i, s in enumerate(seats)
        ]
    else:
        count = int(seats or 0)
        names = [f"Player {i + 1}" for i in range(count)]
    if count < MIN_SEATS or count > MAX_SEATS:
        raise RuleError(f"kings corners needs {MIN_SEATS} to {MAX_SEATS} players, got {count}")
    if int(deals) < 1:
        raise RuleError("a match needs at least one deal")

    state = State(
        seats=[{"name": name, "won": 0} for name in names],
        deal=1,
        deals=int(deals),
        dealer=count - 1,
        turn=0,
        phase="draw",
        rng=make_rng(seed),
    )
    _start_deal(state)
    return state


# ── what builds on what ──────────────────────────────────────────────────


def builds(card: dict, top: dict) -> bool:
    """One rank lower and the other colour — red on black, black on red."""
    return card_value(card["rank"]) == card_value(top["rank"]) - 1 and is_red(card["suit"]) != is_red(top["suit"])


def can_play(state: State, card_: dict, pile: int) -> bool:
    """A card from your hand onto one pile."""
    cards = state.piles[pile]
    if not cards:
        # A corner must be begun with a king; a cleared original takes anything.
        return card_["rank"] == "K" if pile in CORNER_PILES else True
    return builds(card_, cards[-1])


def can_move(state: State, source: int, target: int) -> bool:
    """A whole pile onto another pile.

    The moving pile goes as a block, so it is the *bottom* card that has to fit on
    the destination's top.  An empty corner may take a pile whose bottom is a king
    — this is how a king dealt to N, E, S or W reaches the corner it belongs in.
    """
    if source == target:
        return False
    src = state.piles[source]
    if not src:
        return False
    dst = state.piles[target]
    if not dst:
        # A king dealt to one of the four original piles may be carried to a corner.
        # Not a corner to a corner: a king pile's bottom never fits on anything, so
        # allowing that would let a table shuffle one king between empty corners for
        # ever, and the rules never ask for it.
        return target in CORNER_PILES and source in OPEN_PILES and src[0]["rank"] == "K"
    return builds(src[0], dst[-1])


# ── the moves ────────────────────────────────────────────────────────────


def _play(state: State, seat: int, index: Any, pile: Any) -> None:
    hand = state.hands[seat]
    if isinstance(index, bool) or not isinstance(index, int) or not 0 <= index < len(hand):
        raise RuleError(f"no such card: {index}")
    if isinstance(pile, bool) or not isinstance(pile, int) or not 0 <= pile < len(PILES):
        raise RuleError(f"no such pile: {pile}")
    card_ = hand[index]
    if not can_play(state, card_, pile):
        if not state.piles[pile]:
            raise RuleError("a corner can only be started with a king" if pile in CORNER_PILES else "that pile is empty")
        raise RuleError(f"{label(card_)} does not go on {label(state.piles[pile][-1])}")

    hand.pop(index)
    state.piles[pile].append(card_)
    state.acted = True
    state.passes = 0
    _push_log(state, f"{state.seats[seat]['name']} played {label(card_)} on {PILES[pile]}")

    if not hand:
        _end_deal(state, winner=seat, reason="out")


def _move(state: State, seat: int, source: Any, target: Any) -> None:
    for value, what in ((source, "source"), (target, "target")):
        if isinstance(value, bool) or not isinstance(value, int) or not 0 <= value < len(PILES):
            raise RuleError(f"no such {what} pile: {value}")
    if not can_move(state, source, target):
        if not state.piles[source]:
            raise RuleError(f"pile {PILES[source]} is empty")
        raise RuleError(
            f"{PILES[source]} cannot go on {PILES[target]}"
            + (" — a corner needs a king at the bottom" if not state.piles[target] else "")
        )

    moving = state.piles[source]
    state.piles[source] = []
    state.piles[target].extend(moving)
    state.acted = True
    state.passes = 0
    _push_log(
        state,
        f"{state.seats[seat]['name']} moved {len(moving)} card{'s' if len(moving) != 1 else ''} "
        f"{PILES[source]} \u2192 {PILES[target]}",
    )


def _draw(state: State, seat: int) -> None:
    if state.phase != "draw":
        raise RuleError("you have already drawn this turn")
    if not state.stock:
        raise RuleError("the stock is empty")
    state.hands[seat].append(state.stock.pop())
    state.phase = "play"
    _push_log(state, f"{state.seats[seat]['name']} drew a card")


def _end_turn(state: State, seat: int) -> None:
    """Done playing: the next seat's turn starts with its draw."""
    if state.phase != "play":
        raise RuleError("draw first")
    # A full round of turns with nothing played and no stock left is an impasse.
    if state.acted:
        state.passes = 0
    else:
        state.passes += 1
        if not state.stock and state.passes >= len(state.seats):
            _end_deal(state, winner=None, reason="impasse")
            return
    state.turn = (state.turn + 1) % len(state.seats)
    state.phase = "draw" if state.stock else "play"
    state.acted = False


def _end_deal(state: State, winner: int | None, reason: str) -> None:
    state.phase = "done"
    state.ended_by = reason
    state.winner = winner
    if winner is not None:
        state.seats[winner]["won"] += 1
        _push_log(state, f"{state.seats[winner]['name']} went out — deal {state.deal} won", True)
    else:
        _push_log(state, f"no play left for anyone — deal {state.deal} is dead", True)
    state.over = state.deal >= state.deals
    if state.over:
        best = max(s["won"] for s in state.seats)
        winners = [s["name"] for s in state.seats if s["won"] == best]
        _push_log(state, f"match over — {' & '.join(winners)} with {best}", True)


def apply(state: State, seat: Any, action: Any) -> State:
    """Apply one action for one seat.

    Raises :class:`RuleError` on anything illegal — the server turns that into a
    refusal, a test turns it into a failure.
    """
    if not isinstance(action, dict) or not isinstance(action.get("type"), str):
        raise RuleError("no action")
    if isinstance(seat, bool) or not isinstance(seat, int) or seat < 0 or seat >= len(state.seats):
        raise RuleError("no such seat")

    # Between deals anyone at the table may move the game on; there is no host.
    if action["type"] == "next-deal":
        if state.phase != "done":
            raise RuleError("the deal is still being played")
        if state.over:
            raise RuleError("the match is over")
        state.deal += 1
        _start_deal(state)
        return state

    if state.phase == "done":
        raise RuleError("the deal is over")
    if state.turn != seat:
        raise RuleError("it is not your turn")

    kind = action["type"]
    if kind == "draw":
        _draw(state, seat)
    elif kind == "play":
        if state.phase != "play":
            raise RuleError("draw first")
        _play(state, seat, action.get("card"), action.get("pile"))
    elif kind == "move":
        if state.phase != "play":
            raise RuleError("draw first")
        _move(state, seat, action.get("from"), action.get("to"))
    elif kind == "end-turn":
        _end_turn(state, seat)
    else:
        raise RuleError(f"unknown action: {kind}")
    return state


def legal_actions(state: State, seat: int) -> list[dict]:
    """What this seat may do right now — the client mirrors this to light up the table."""
    if state.phase == "done":
        return [] if state.over else [{"type": "next-deal"}]
    if state.turn != seat:
        return []
    if state.phase == "draw":
        return [{"type": "draw"}]

    actions: list[dict] = []
    hand = state.hands[seat]
    for i, card_ in enumerate(hand):
        for p in range(len(PILES)):
            if can_play(state, card_, p):
                actions.append({"type": "play", "card": i, "pile": p})
    for source in range(len(PILES)):
        for target in range(len(PILES)):
            if can_move(state, source, target):
                actions.append({"type": "move", "from": source, "to": target})
    actions.append({"type": "end-turn"})
    return actions



