"""Play a whole match with no browser and no network. The cheapest end-to-end proof.

    python -m kings_corner --seed 42 --seats 3
    python -m kings_corner --seed 42 --seats 3 --trace      # every action, in order

Same seed, same run, same output — which is what makes a bug report reproducible.
"""

from __future__ import annotations

import argparse
import sys

from .engine import DEALS, MAX_SEATS, MIN_SEATS, PILES, RuleError, apply, create_match, label
from .policy import choose_action


def play(seats: int, seed: int, deals: int = DEALS, trace: bool = False, out=sys.stdout) -> int:
    state = create_match(seats=seats, seed=seed, deals=deals)
    results: list[dict] = []
    steps = 0
    while not state.over:
        steps += 1
        if steps > 200000:
            raise SystemExit("match did not finish — that is a bug")
        action = choose_action(state, state.turn)
        if action is None:
            break
        if trace:
            who = state.seats[state.turn]["name"]
            print(f"  deal {state.deal} {who}: {action}", file=out)
        apply(state, state.turn, action)
        if state.phase == "done" and (not results or results[-1]["deal"] != state.deal):
            results.append({
                "deal": state.deal,
                "winner": state.seats[state.winner]["name"] if state.winner is not None else None,
                "ended": state.ended_by,
                "hands": [len(h) for h in state.hands],
                "piles": [len(p) for p in state.piles],
            })

    names = [s["name"] for s in state.seats]
    print(f"seed {seed}, {seats} seats, {len(results)} deals, {steps} actions\n", file=out)
    print("deal  " + "  ".join(f"{n:>10}" for n in names) + "   outcome", file=out)
    for entry in results:
        cells = "  ".join(f"{('—' if e == 0 else e):>10}" for e in entry["hands"])
        who = entry["winner"] or "nobody"
        print(f"{entry['deal']:>4}  {cells}   {who} ({entry['ended']})", file=out)
    print("      " + "  ".join(f"{'-'*10:>10}" for _ in names) + "   cards left at the end", file=out)
    print("won   " + "  ".join(f"{s['won']:>10}" for s in state.seats), file=out)

    best = max(s["won"] for s in state.seats)
    winners = [names[i] for i, s in enumerate(state.seats) if s["won"] == best]
    verb = "wins" if len(winners) == 1 else "share it"
    print(f"\n{' & '.join(winners)} {verb} with {best} of {state.deals}", file=out)
    last = state.log[0]["text"] if state.log else ""
    print(f"last: {last}", file=out)
    print(f"table: {[PILES[i] + ':' + (label(p[-1]) if p else '—') for i, p in enumerate(state.piles)]}", file=out)
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="kings-corner", description="Play a Kings Corners match headlessly.")
    parser.add_argument("--seats", type=int, default=3, help=f"{MIN_SEATS}-{MAX_SEATS} players")
    parser.add_argument("--seed", type=int, default=42, help="same seed, same run")
    parser.add_argument("--deals", type=int, default=DEALS, help="deals in the match")
    parser.add_argument("--trace", action="store_true", help="print every action")
    args = parser.parse_args(argv)
    try:
        return play(args.seats, args.seed, args.deals, args.trace)
    except RuleError as exc:
        print(f"kings-corner: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
