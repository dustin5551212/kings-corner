"""The redaction boundary. Everything a player is shown goes through here.

This module is the only place that decides what leaves the server, so "does the
table leak?" is one file to read and one test to run.

Kings Corners hides exactly one thing: the cards in everybody else's hand.  The
piles are public, the stock is a count, and the log never names a drawn card —
otherwise a draw would be a reveal.

Rule of thumb: build the view from scratch, never by deleting fields off the
engine state.  A field added to the engine later should be invisible here until
someone deliberately exposes it.

The wire format is camelCase (``endedBy``, ``won``, ``dealScore``) because the
browser client reads these keys.
"""

from __future__ import annotations

from .engine import PILES, State, legal_actions


def _public_card(card: dict) -> dict:
    return {"rank": card["rank"], "suit": card["suit"]}


def view(state: State, seat: int, extra: dict | None = None) -> dict:
    out = {
        "you": seat,
        # Your own hand, in order — index is what a play action names.
        "hand": [_public_card(c) for c in state.hands[seat]],
        # Everybody else is a count. Nothing else about a hand leaves the server.
        "others": [
            {"seat": i, "name": s["name"], "count": len(state.hands[i])}
            for i, s in enumerate(state.seats)
            if i != seat
        ],
        "seats": [
            {"seat": i, "name": s["name"], "won": s["won"]}
            for i, s in enumerate(state.seats)
        ],
        "piles": [
            {"id": i, "label": PILES[i], "corner": i >= len(PILES) // 2, "cards": [_public_card(c) for c in pile]}
            for i, pile in enumerate(state.piles)
        ],
        "deal": state.deal,
        "deals": state.deals,
        "dealer": state.dealer,
        "turn": state.turn,
        "phase": state.phase,
        "stock": len(state.stock),
        "acted": state.acted,
        "winner": state.winner,
        # "out" or "impasse": the client says which before it shows the deal result.
        "endedBy": state.ended_by,
        "over": state.over,
        "log": [{"text": e["text"], "hl": e["hl"], "deal": e["deal"]} for e in state.log],
        "legal": [dict(a) for a in legal_actions(state, seat)],
    }
    if extra:
        out.update(extra)
    return out


def view_for(state: State, seat: int, extra: dict | None = None) -> dict:
    """The same view with the seats filled in by the server (presence, names)."""
    return view(state, seat, extra)
