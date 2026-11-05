"""The transport, in process: rooms, seats, refusals, polling and the SSE stream."""

from __future__ import annotations

import json
import threading
import time
import urllib.error
import urllib.request

import pytest

from kings_corner.server import KingsServer, Handler


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
