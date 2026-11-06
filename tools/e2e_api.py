"""Black-box check of the running server, over HTTP only.

    .venv/bin/python tools/e2e_api.py http://127.0.0.1:5175 --seats 3 --deals 3
    .venv/bin/python tools/e2e_api.py http://127.0.0.1:5175 --seats 4 --bots 3

It opens a room, fills every seat, deals, and then plays the whole match by picking
from each view's own ``legal`` list — so it proves the wire contract (join, deal,
action, state) and the rules together, without importing a line of the engine.

With ``--bots N`` only the first seat is a person: the CPUs play the rest the way the
server plays them, and this script just waits for its own turn.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
import urllib.error
import urllib.request

def call(base, path, method="GET", body=None):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(base + path, method=method, data=data)
    if data:
        req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=15) as res:
            return res.status, json.loads(res.read() or b"{}")
    except urllib.error.HTTPError as err:
        return err.code, json.loads(err.read() or b"{}")


def pick(view):
    """One action, from what the server itself says is legal."""
    legal = view["legal"]
    for kind in ("play", "move", "draw", "end-turn", "next-deal"):
        for action in legal:
            if action["type"] == kind:
                return action
    raise SystemExit(f"no legal action to take: {legal}")


def play_match(base, seats, deals, bots=0):
    status, room = call(base, f"/api/room?name=Player1&seats={seats}&bots={bots}&deals={deals}", "POST")
    assert status == 200, room
    code = room["code"]
    #: seat → token, for the seats this script drives. A CPU seat has neither.
    tokens = {room["seat"]: room["token"]}
    if not bots:
        for i in range(1, seats):
            status, joined = call(base, f"/api/join?room={code}&name=Player{i + 1}&token=", "POST")
            assert status == 200, joined
            tokens[joined["seat"]] = joined["token"]
    print(f"  room {code}: {seats} seats ({bots} cpu), {deals} deals")

    status, dealt = call(base, f"/api/deal?room={code}&token={tokens[0]}", "POST")
    assert status == 200 and dealt["ok"], dealt

    seen_deals = {1}
    steps = 0
    winner = None
    while True:
        steps += 1
        assert steps < 40000, "the match stalled"
        status, state = call(base, f"/api/state?room={code}&token={tokens[0]}")
        assert status == 200, state
        view = state["view"]
        seen_deals.add(view["deal"])
        if view["phase"] == "done":
            if view["over"]:
                winner = view["winner"]
                break
            # Between deals any seat may move the match on.
            status, answer = call(base, f"/api/action?room={code}&token={tokens[0]}", "POST",
                                  {"type": "next-deal"})
            assert status == 200, answer
            continue
        if view["turn"] not in tokens:
            # A CPU's turn: the server plays it, so wait for the table to come back.
            time.sleep(0.1)
            continue
        # Ask the seat whose turn it is: `legal` is written for the viewer only.
        token = tokens[view["turn"]]
        status, mine = call(base, f"/api/state?room={code}&token={token}")
        assert status == 200, mine
        assert mine["view"]["turn"] == view["turn"]
        status, answer = call(base, f"/api/action?room={code}&token={token}", "POST", pick(mine["view"]))
        assert status == 200, answer

    final = call(base, f"/api/state?room={code}&token={tokens[0]}")[1]["view"]
    assert final["over"] is True
    assert final["deal"] == deals, (final["deal"], deals)
    assert seen_deals == set(range(1, deals + 1)), seen_deals
    won = [s["won"] for s in final["seats"]]
    assert sum(won) <= deals
    print(f"  {steps} actions, deals {sorted(seen_deals)}, deals won {won}, last winner {winner}")
    return code, final


def main(argv=None):
    parser = argparse.ArgumentParser(description="Drive a Kings Corners server over HTTP.")
    parser.add_argument("base", nargs="?", default="http://127.0.0.1:5175")
    parser.add_argument("--seats", type=int, default=3)
    parser.add_argument("--bots", type=int, default=0, help="CPUs at the table; 0 fills with people")
    parser.add_argument("--deals", type=int, default=3)
    parser.add_argument("--rooms", type=int, default=1)
    args = parser.parse_args(argv)

    base = args.base.rstrip("/")
    for i in range(args.rooms):
        print(f"room {i + 1}/{args.rooms}")
        play_match(base, args.seats, args.deals, args.bots)

    # One refusal, because a server that accepts everything proves nothing.
    status, room = call(base, "/api/room?name=Solo&seats=2", "POST")
    status, body = call(base, f"/api/deal?room={room['code']}&token={room['token']}", "POST")
    assert status == 409 and "every seat" in body["error"], body
    status, body = call(base, f"/api/action?room={room['code']}&token=nope", "POST", {"type": "draw"})
    assert status == 403, body
    print("\nPASS — full match over HTTP, refusals included")
    return 0


if __name__ == "__main__":
    sys.exit(main())
