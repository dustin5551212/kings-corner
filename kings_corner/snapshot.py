"""A canonical, JSON-safe snapshot of an engine state.

Two things are deliberately included that a casual summary would leave out: the
*order* of the stock (deck consumption is where a rules change shows up first) and
the legal actions of every seat, not just the one to move.
"""

from __future__ import annotations

from .engine import State, legal_actions


def snapshot(state: State) -> dict:
    return {
        "deal": state.deal,
        "deals": state.deals,
        "dealer": state.dealer,
        "turn": state.turn,
        "phase": state.phase,
        "acted": state.acted,
        "passes": state.passes,
        "over": state.over,
        "endedBy": state.ended_by,
        "winner": state.winner,
        "seats": [{"name": s["name"], "won": s["won"]} for s in state.seats],
        "hands": [[c["rank"] + c["suit"] for c in hand] for hand in state.hands],
        "piles": [[c["rank"] + c["suit"] for c in pile] for pile in state.piles],
        "stock": [c["rank"] + c["suit"] for c in state.stock],
        "log": [{"text": e["text"], "hl": e["hl"], "deal": e["deal"]} for e in state.log],
        "legal": [legal_actions(state, i) for i in range(len(state.seats))],
    }
