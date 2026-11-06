"""The transport, in process: rooms, seats, refusals, polling and the SSE stream."""

from __future__ import annotations

import json
import threading
import time
import urllib.error
import urllib.request

import pytest

from kings_corner.engine import create_match
from kings_corner.server import Handler, KingsServer, Room, apply_bots, step_bot


@pytest.fixture()
def server():
    srv = KingsServer(("127.0.0.1", 0), Handler, 0)
    thread = threading.Thread(target=srv.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{srv.server_address[1]}"
    srv.shutdown()
    srv.server_close()


def call(base: str, path: str, method: str = "GET", body: dict | None = None):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(base + path, method=method, data=data)
    if data:
        req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=10) as res:
            return res.status, json.loads(res.read() or b"{}")
    except urllib.error.HTTPError as err:
        return err.code, json.loads(err.read() or b"{}")


def make_table(base: str, names=("Ada", "Bert", "Cass")):
    status, created = call(base, f"/api/room?name={names[0]}&seats={len(names)}&deals=3", "POST")
    assert status == 200, created
    players = [(created["token"], created["seat"])]
    for name in names[1:]:
        status, joined = call(base, f"/api/join?room={created['code']}&name={name}&token=", "POST")
        assert status == 200, joined
        players.append((joined["token"], joined["seat"]))
    return created["code"], players


def test_static_client_is_served(server):
    with urllib.request.urlopen(server + "/", timeout=10) as res:
        body = res.read()
        assert res.status == 200
        assert "text/html" in res.headers["Content-Type"]
    assert b"<html" in body.lower()


def test_a_room_cannot_deal_until_every_seat_is_filled(server):
    status, created = call(server, "/api/room?name=Ada&seats=3", "POST")
    assert status == 200
    code = created["code"]
    assert len(code) == 6 and code.isalnum()
    assert created["deals"] == 5  # default match length

    status, body = call(server, f"/api/deal?room={code}&token={created['token']}", "POST")
    assert status == 409 and "every seat" in body["error"]

    call(server, f"/api/join?room={code}&name=Bert&token=", "POST")
    call(server, f"/api/join?room={code}&name=Cass&token=", "POST")
    status, full = call(server, f"/api/join?room={code}&name=Late&token=", "POST")
    assert status == 409 and "full" in full["error"]

    status, body = call(server, f"/api/deal?room={code}&token={created['token']}", "POST")
    assert status == 200 and body["ok"] is True and body["deal"] == 1

    status, body = call(server, f"/api/deal?room={code}&token={created['token']}", "POST")
    assert status == 409 and "already dealt" in body["error"]


def test_state_and_status_describe_the_same_table(server):
    code, players = make_table(server)
    token, seat = players[0]
    call(server, f"/api/deal?room={code}&token={token}", "POST")

    status, state = call(server, f"/api/state?room={code}&token={token}")
    assert status == 200
    assert state["type"] == "view"
    assert state["view"]["you"] == seat
    assert state["view"]["deal"] == 1
    assert state["deals"] == 3
    assert len(state["view"]["hand"]) == 7
    assert [p["label"] for p in state["view"]["piles"]] == ["N", "E", "S", "W", "NW", "NE", "SE", "SW"]

    status, info = call(server, f"/api/status?room={code}")
    assert status == 200
    # Asking for your state is proof of life for a polling client.
    assert info["seats"][seat]["live"] is True
    assert info["started"] is True and info["deal"] == 1


def test_actions_are_refused_with_the_engine_rule_errors(server):
    code, players = make_table(server)
    call(server, f"/api/deal?room={code}&token={players[0][0]}", "POST")

    _, state = call(server, f"/api/state?room={code}&token={players[0][0]}")
    turn = state["view"]["turn"]
    other = players[(turn + 1) % len(players)][0]

    status, body = call(server, f"/api/action?room={code}&token={other}", "POST", {"type": "draw"})
    assert status == 400 and "not your turn" in body["error"]

    status, body = call(server, f"/api/action?room={code}&token=nonsense", "POST", {"type": "draw"})
    assert status == 403

    status, body = call(server, f"/api/action?room={code}&token={players[turn][0]}", "POST", {"type": "wat"})
    assert status == 400 and "unknown action" in body["error"]

    # The first thing a turn is, is a draw: playing first is refused by name.
    status, body = call(
        server, f"/api/action?room={code}&token={players[turn][0]}", "POST",
        {"type": "play", "card": 0, "pile": 0},
    )
    assert status == 400 and "draw first" in body["error"]

    status, body = call(server, "/api/nope")
    assert status == 404 and body["error"] == "unknown endpoint"


def test_a_whole_turn_through_the_api(server):
    code, players = make_table(server)
    call(server, f"/api/deal?room={code}&token={players[0][0]}", "POST")
    token, _seat = players[0]

    _, state = call(server, f"/api/state?room={code}&token={token}")
    turn = state["view"]["turn"]
    mover = players[turn][0]

    status, _ = call(server, f"/api/action?room={code}&token={mover}", "POST", {"type": "draw"})
    assert status == 200
    status, _ = call(server, f"/api/action?room={code}&token={mover}", "POST", {"type": "end-turn"})
    assert status == 200

    _, after = call(server, f"/api/state?room={code}&token={token}")
    assert after["view"]["turn"] != turn


def test_a_polling_seat_gets_its_view_queued(server):
    code, players = make_table(server)
    call(server, f"/api/deal?room={code}&token={players[0][0]}", "POST")

    status, body = call(server, f"/api/poll?room={code}&token={players[0][0]}")
    assert status == 200
    kinds = [m["type"] for m in body["messages"]]
    assert "view" in kinds, kinds

    # A seat that was not live when it polled gets one fresh broadcast queued (that is
    # how a player who opens the page sees the table immediately), so the drain takes a
    # poll or two. It must end empty, not grow.
    for _ in range(4):
        status, body = call(server, f"/api/poll?room={code}&token={players[0][0]}")
        if not body["messages"]:
            break
    assert body["messages"] == []


def test_the_sse_stream_pushes_a_view_then_heartbeats(server):
    code, players = make_table(server)
    token, _seat = players[0]

    frames: list[str] = []
    ready = threading.Event()

    def read_stream():
        with urllib.request.urlopen(f"{server}/api/stream?room={code}&token={token}", timeout=20) as res:
            assert res.status == 200
            assert "text/event-stream" in res.headers["Content-Type"]
            ready.set()
            buf = b""
            deadline = time.time() + 6
            while time.time() < deadline:
                chunk = res.read(1)
                if not chunk:
                    break
                buf += chunk
                while b"\n\n" in buf:
                    frame, buf = buf.split(b"\n\n", 1)
                    frames.append(frame.decode("utf-8", "replace"))

    thread = threading.Thread(target=read_stream, daemon=True)
    thread.start()
    assert ready.wait(5), "the stream never opened"

    # Deal while the stream is open: the lobby/view messages must arrive on it.
    call(server, f"/api/deal?room={code}&token={players[0][0]}", "POST")
    for _ in range(60):
        if any("data: " in f for f in frames):
            break
        time.sleep(0.1)

    data_frames = [f for f in frames if "data: " in f]
    assert data_frames, f"no data frames arrived; got {frames[:3]}"
    payload = json.loads(data_frames[-1].split("data: ", 1)[1])
    assert payload["type"] in ("lobby", "view")
    if payload["type"] == "view":
        assert payload["view"]["you"] == players[0][1]


# ── CPU seats ───────────────────────────────────────────────────────────


def solo_table(base: str, seats: int = 4, bots: int = 3, deals: int = 3):
    status, created = call(base, f"/api/room?name=Ada&seats={seats}&bots={bots}&deals={deals}", "POST")
    assert status == 200, created
    return created


def test_four_seats_and_three_cpus_is_a_table_for_one(server):
    created = solo_table(server)
    code, token = created["code"], created["token"]
    assert created["seats"] == 4 and created["bots"] == 3

    status, info = call(server, f"/api/status?room={code}")
    assert status == 200
    assert [s["bot"] for s in info["seats"]] == [False, True, True, True]
    assert [s["joined"] for s in info["seats"]] == [True] * 4
    # A CPU is at the table whether or not anybody holds a connection.
    assert all(s["live"] for s in info["seats"] if s["bot"])

    # Nobody else had to join: the deal goes ahead on one person's word.
    status, body = call(server, f"/api/deal?room={code}&token={token}", "POST")
    assert status == 200 and body["deal"] == 1

    status, state = call(server, f"/api/state?room={code}&token={token}")
    assert status == 200
    assert [s["bot"] for s in state["view"]["seats"]] == [False, True, True, True]
    assert all(o["bot"] for o in state["view"]["others"])
    assert len(state["view"]["others"]) == 3


def test_the_cpus_take_their_turns_and_hand_the_table_back(server):
    created = solo_table(server)
    code, token = created["code"], created["token"]
    call(server, f"/api/deal?room={code}&token={token}", "POST")

    _, first = call(server, f"/api/state?room={code}&token={token}")
    stock_before = first["view"]["stock"]
    assert first["view"]["turn"] != 0, "seat 0 is the person: a CPU should have led"

    # The CPU driver thread plays the three CPUs; the table comes back to the person
    # with nobody else to wait for. Six seconds is many times the 0.45s per action.
    deadline = time.time() + 8
    state = first
    while time.time() < deadline:
        _, state = call(server, f"/api/state?room={code}&token={token}")
        if state["view"]["turn"] == 0 and state["view"]["phase"] == "draw":
            break
        time.sleep(0.1)

    assert state["view"]["turn"] == 0, "the CPUs never gave the turn back"
    # Three CPUs each drew one, and the person has not acted yet.
    assert state["view"]["stock"] < stock_before
    assert any("CPU" in entry["text"] for entry in state["view"]["log"])


def test_a_cpu_never_takes_the_last_seat_from_a_person(server):
    created = call(server, "/api/room?name=Ada&seats=2&bots=3", "POST")
    assert created[1]["bots"] == 1, "two seats can hold one CPU"

    created = call(server, "/api/room?name=Ada&seats=4&bots=1", "POST")
    code, token = created[1]["code"], created[1]["token"]
    assert created[1]["bots"] == 1
    call(server, f"/api/join?room={code}&name=Bert&token=", "POST")

    # Two people and four seats: three CPUs would leave nobody's chair, so the table
    # settles for two and says so in the count it returns.
    status, body = call(server, f"/api/bots?room={code}&token={token}&bots=3", "POST")
    assert status == 200 and body["bots"] == 2
    _, info = call(server, f"/api/status?room={code}")
    assert [s["bot"] for s in info["seats"]] == [False, False, True, True]


def test_a_cpu_seat_is_not_a_seat_a_person_can_take(server):
    created = solo_table(server, seats=2, bots=1)
    status, body = call(server, f"/api/join?room={created['code']}&name=Bert&token=", "POST")
    assert status == 409 and "full" in body["error"]


def test_cpus_can_be_added_and_removed_before_the_deal(server):
    status, created = call(server, "/api/room?name=Ada&seats=3&bots=0", "POST")
    assert status == 200 and created["bots"] == 0
    code, token = created["code"], created["token"]

    status, body = call(server, f"/api/bots?room={code}&token={token}&bots=2", "POST")
    assert status == 200 and body["bots"] == 2
    _, info = call(server, f"/api/status?room={code}")
    assert [s["bot"] for s in info["seats"]] == [False, True, True]

    status, body = call(server, f"/api/bots?room={code}&token={token}&bots=0", "POST")
    assert status == 200 and body["bots"] == 0
    _, info = call(server, f"/api/status?room={code}")
    assert [s["joined"] for s in info["seats"]] == [True, False, False]
    call(server, f"/api/join?room={code}&name=Bert&token=", "POST")

    # Somebody else's token is not a licence to rearrange the table.
    status, _ = call(server, f"/api/bots?room={code}&token=nonsense&bots=1", "POST")
    assert status == 403

    # Two people, three seats: one CPU fills the last chair and the deal can start.
    status, body = call(server, f"/api/bots?room={code}&token={token}&bots=1", "POST")
    assert status == 200 and body["bots"] == 1
    status, body = call(server, f"/api/deal?room={code}&token={token}", "POST")
    assert status == 200, body

    status, body = call(server, f"/api/bots?room={code}&token={token}&bots=2", "POST")
    assert status == 409 and "started" in body["error"]


def test_the_cpu_driver_plays_one_action_at_a_time_and_waits_for_a_person():
    """step_bot is the whole CPU: no HTTP, no thread, one action per call."""
    room = Room("TEST01", 2)
    apply_bots(room, 1)
    room.seats[0].token = "person"
    room.seats[0].name = "Ada"
    room.seats[0].joined = True
    room.game = create_match(seats=[{"name": s.name, "bot": s.bot} for s in room.seats], seed=5)
    game = room.game

    # The deal is led by seat 1: the CPU. It acts, then holds until its pause is up.
    assert game.turn == 1
    acted = 0
    room.bot_ready_at = 0
    while game.turn == 1 and game.phase != "done" and acted < 40:
        assert step_bot(room) is True
        acted += 1
        room.bot_ready_at = 0  # skip the thinking pause
    assert 1 <= acted < 40, f"the CPU took {acted} actions for one turn"
    assert game.turn == 0 or game.phase == "done", "the CPU should have ended its turn"

    # Now it is the person's turn: the driver must not touch the table.
    before = json.dumps([len(game.hands[0]), len(game.hands[1]), game.phase, game.turn])
    room.bot_ready_at = 0
    for _ in range(5):
        assert step_bot(room) is False
    assert before == json.dumps([len(game.hands[0]), len(game.hands[1]), game.phase, game.turn])


def test_a_cpu_does_not_act_while_its_pause_is_running():
    room = Room("TEST02", 2)
    apply_bots(room, 1)
    room.game = create_match(seats=[{"name": s.name, "bot": s.bot} for s in room.seats], seed=6)
    room.bot_ready_at = time.monotonic() + 60
    assert step_bot(room) is False

