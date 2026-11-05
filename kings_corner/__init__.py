"""Kings Corners — Bicycle's rules, a table for two to four, a Python server.

The engine (``kings_corner.engine``) is the whole game in one object;
``kings_corner.view`` is the only place that decides what leaves the server;
``kings_corner.server`` is the HTTP/SSE transport; ``public/`` is the client.
"""

from .engine import (
    CORNER_PILES,
    DEALS,
    HAND,
    MAX_SEATS,
    MIN_SEATS,
    OPEN_PILES,
    PILES,
    RANKS,
    SUITS,
    RuleError,
    State,
    apply,
    build_deck,
    builds,
    can_move,
    can_play,
    card_value,
    create_match,
    is_red,
    label,
    legal_actions,
    make_rng,
    shuffle,
)
from .view import view, view_for

__all__ = [
    "CORNER_PILES", "DEALS", "HAND", "MAX_SEATS", "MIN_SEATS", "OPEN_PILES", "PILES",
    "RANKS", "SUITS", "RuleError", "State", "apply", "build_deck", "builds", "can_move",
    "can_play", "card_value", "create_match", "is_red", "label", "legal_actions",
    "make_rng", "shuffle", "view", "view_for",
]
__version__ = "0.1.0"
