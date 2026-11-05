"""Kings Corners, for people. A room, a seat per person, and one authoritative game.

    python -m kings_corner.server [port]       # default 5175

Design, in one paragraph: the server owns the deck.  Clients are thin — they
receive their own view (``view.py``) and post actions.  There is no host, a table
is 2 to 4 people, the first deal waits until every seat is taken, and nothing on
the wire is a card somebody is not allowed to see.

Transport is Server-Sent Events for pushes and a poll fallback, because a tunnel
that buffers a stream still has to let people play.  Standard library only: no
ASGI, no framework, one thread per connection.
"""

from __future__ import annotations

import json
import os
import socket
import sys
import threading
import time
import traceback
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlparse

from .engine import DEALS, MAX_SEATS, MIN_SEATS, RuleError, apply, create_match
from .view import view

ROOT = Path(__file__).resolve().parent.parent
WEB_ROOT = ROOT / "public"

TYPES = {
    ".html": "text/html; charset=utf-8",
    ".css": "text/css; charset=utf-8",
    ".js": "text/javascript; charset=utf-8",
    ".mjs": "text/javascript; charset=utf-8",
    ".json": "application/json; charset=utf-8",
    ".svg": "image/svg+xml",
    ".png": "image/png",
    ".webp": "image/webp",
    ".ico": "image/x-icon",
}

CODE_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"
TOKEN_ALPHABET = "abcdefghijkmnopqrstuvwxyz23456789"
ROOM_TTL_S = 8 * 60 * 60
PEER_TIMEOUT_S = 20.0
HEARTBEAT_S = 8.0

# A proxy in front of the game will sit on a streamed response until it has a useful
# amount to forward. Two kilobytes of comment per message buys ~1s updates through the
# tunnel instead of waiting for the next heartbeat; a card game sends so few messages
# that the cost is invisible.
FLUSH_PAD = ": " + ("\u00b7" * 2000) + "\n\n"

rooms: dict[str, "Room"] = {}
rooms_lock = threading.RLock()


class Peer:
    """One seat's delivery channel: a live SSE stream, a queue, or both."""

    def __init__(self) -> None:
        self.out = None  # the wfile of the open stream, if any
        self.lock = threading.Lock()
        self.queue: list[dict] = []
        self.polling = False
        self.last_poll = 0.0

    def write(self, text: str) -> bool:
        out = self.out
        if out is None:
            return False
        with self.lock:
            if self.out is None:
                return False
            try:
                out.write(text.encode("utf-8"))
                out.flush()
                return True
            except (BrokenPipeError, ConnectionResetError, OSError, ValueError):
                self.out = None
                return False

    def close(self) -> None:
        out = self.out
        self.out = None
        if out is not None:
            try:
                with self.lock:
                    out.flush()
            except Exception:
                pass


class Seat:
    def __init__(self) -> None:
        self.token: str | None = None
        self.name: str | None = None
        self.peer = Peer()
        self.joined = False


class Room:
    def __init__(self, code: str, size: int, deals: int = DEALS) -> None:
        self.code = code
        self.size = size
        self.deals = deals
        self.seats = [Seat() for _ in range(size)]
        self.game = None
        self.touched = time.monotonic()


def make_id(alphabet: str, length: int) -> str:
    # os.urandom, not random: a token is a seat's only credential.
    raw = os.urandom(length)
    return "".join(alphabet[b % len(alphabet)] for b in raw[:length])


def make_code() -> str:
    for _ in range(100):
        code = make_id(CODE_ALPHABET, 6)
        if code not in rooms:
            return code
    raise RuntimeError("could not allocate a room code")


def clean_name(raw, fallback: str) -> str:
    text = "".join(ch for ch in str(raw if raw is not None else "") if ch.isprintable() and ch not in "<>")
    text = text.strip()[:20]
    return text if len(text) >= 2 else fallback


def make_room(size: int, deals: int = DEALS) -> Room:
    return Room(make_code(), size, deals)


# ── delivery ────────────────────────────────────────────────────────────


def seat_live(room: Room, i: int) -> bool:
    if i >= len(room.seats):
        return False
    peer = room.seats[i].peer
    if peer.out is not None:
        return True
    return peer.polling and (time.monotonic() - peer.last_poll) < PEER_TIMEOUT_S


def deliver(room: Room, seat: int, message: dict) -> bool:
    if seat >= len(room.seats):
        return False
    peer = room.seats[seat].peer
    frame = f"{FLUSH_PAD}data: {json.dumps(message, ensure_ascii=False)}\n\n"
    if peer.write(frame):
        return True
    if len(peer.queue) < 200:
        peer.queue.append(message)
    return False


def presence_of(room: Room) -> list[dict]:
    return [
        {"joined": s.joined and bool(s.name), "live": seat_live(room, i), "name": s.name}
        for i, s in enumerate(room.seats)
    ]


def broadcast(room: Room) -> None:
    """Push the right view to the right player. Every seat gets its own redaction."""
    presence = presence_of(room)
    for i, seat in enumerate(room.seats):
        if not seat.joined:
            continue
        extra = {
            "code": room.code,
            "seatCount": room.size,
            "deals": room.deals,
            "presence": presence,
            "started": bool(room.game),
            "waiting": sum(1 for s in room.seats if not s.joined),
        }
        if not room.game:
            deliver(room, i, {"type": "lobby", "you": i, **extra})
            continue
        deliver(room, i, {"type": "view", **extra, "view": view(room.game, i)})


def seat_for_token(room: Room | None, token: str | None) -> int:
    if room is None or not token:
        return -1
    for i, s in enumerate(room.seats):
        if s.token == token:
            return i
    return -1


# ── http plumbing ───────────────────────────────────────────────────────


def lan_address() -> str:
    try:
        return socket.gethostbyname(socket.gethostname())
    except OSError:
        return "localhost"


def public_url() -> str | None:
    """The link the lobby shows. KINGS_CORNERS_PUBLIC_URL wins, then ./public-url.txt."""
    env = os.environ.get("KINGS_CORNERS_PUBLIC_URL")
    if env:
        return env.strip() or None
    try:
        text = (ROOT / "public-url.txt").read_text(encoding="utf-8").strip()
        return text or None
    except OSError:
        return None


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.0"  # SSE is read-until-close; every response ends on its own
    server_version = "kings-corner/0.1"

    # quieter than the default one-line-per-request; still useful when chasing a bug
    def log_message(self, fmt: str, *args) -> None:  # noqa: A003
        if os.environ.get("KINGS_CORNERS_VERBOSE"):
            sys.stderr.write(f"[kings] {self.address_string()} {fmt % args}\n")

    # ── responses ──
    def json(self, status: int, body: dict) -> None:
        payload = json.dumps(body, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def read_body(self, limit: int = 16 * 1024) -> dict:
        length = int(self.headers.get("Content-Length") or 0)
        if length > limit:
            raise ValueError("body too large")
        raw = self.rfile.read(length) if length else b""
        text = raw.decode("utf-8", "replace")
        if not text.strip():
            return {}
        return json.loads(text)

    def do_GET(self) -> None:  # noqa: N802
        self.dispatch("GET")

    def do_POST(self) -> None:  # noqa: N802
        self.dispatch("POST")

    def dispatch(self, method: str) -> None:
        parsed = urlparse(self.path)
        query = {k: v[0] for k, v in parse_qs(parsed.query, keep_blank_values=True).items()}
        if parsed.path.startswith("/api/"):
            try:
                if self.handle_api(method, parsed.path, query):
                    return
                self.json(404, {"error": "unknown endpoint"})
            except (BrokenPipeError, ConnectionResetError):
                pass
            except Exception as exc:  # noqa: BLE001
                traceback.print_exc()
                try:
                    self.json(500, {"error": str(exc)})
                except Exception:
                    pass
            return
        self.serve_static(parsed.path)

    def serve_static(self, path: str) -> None:
        try:
            rel = unquote(path).lstrip("/") or "index.html"
            target = (WEB_ROOT / rel).resolve()
            if not str(target).startswith(str(WEB_ROOT.resolve())):
                self.send_response(403)
                self.send_header("Content-Length", "9")
                self.end_headers()
                self.wfile.write(b"Forbidden")
                return
            body = target.read_bytes()
        except (OSError, ValueError):
            body = b"Not found"
            self.send_response(404)
            self.send_header("Content-Type", "text/plain")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        self.send_response(200)
        self.send_header("Content-Type", TYPES.get(target.suffix, "application/octet-stream"))
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    # ── api ──
    def handle_api(self, method: str, path: str, query: dict) -> bool:
        room = rooms.get(query.get("room") or "")

        if path == "/api/room" and method == "POST":
            try:
                size = int(query.get("seats") or 3)
            except ValueError:
                size = 3
            size = max(MIN_SEATS, min(MAX_SEATS, size))
            try:
                deals = int(query.get("deals") or DEALS)
            except ValueError:
                deals = DEALS
            deals = max(1, min(20, deals))
            fresh = make_room(size, deals)
            rooms[fresh.code] = fresh
            token = make_id(TOKEN_ALPHABET, 24)
            fresh.seats[0] = Seat()
            fresh.seats[0].token = token
            fresh.seats[0].name = clean_name(query.get("name"), "Player 1")
            fresh.seats[0].joined = True
            broadcast(fresh)
            self.json(200, {
                "code": fresh.code,
                "token": token,
                "seat": 0,
                "seats": size,
                "deals": deals,
                "publicUrl": public_url(),
                "lan": lan_address(),
                "port": self.server.port,  # type: ignore[attr-defined]
            })
            return True

        if path == "/api/join" and method == "POST":
            if room is None:
                self.json(404, {"error": "no such room"})
                return True
            if room.game:
                # Reclaiming an existing seat beats locking someone out of their own reload.
                existing = seat_for_token(room, query.get("token"))
                if existing >= 0:
                    room.seats[existing].name = clean_name(query.get("name"), room.seats[existing].name)
                    broadcast(room)
                    self.json(200, {"code": room.code, "token": room.seats[existing].token, "seat": existing})
                    return True
                self.json(409, {"error": "that game has already started"})
                return True
            wanted = seat_for_token(room, query.get("token")) if query.get("token") else -1
            seat_index = wanted if wanted >= 0 else next((i for i, s in enumerate(room.seats) if not s.joined), -1)
            if seat_index < 0:
                self.json(409, {"error": "that table is full"})
                return True

            token = room.seats[seat_index].token if wanted >= 0 else make_id(TOKEN_ALPHABET, 24)
            peer = room.seats[seat_index].peer
            seat = Seat()
            seat.token = token
            seat.name = clean_name(query.get("name"), f"Player {seat_index + 1}")
            seat.peer = peer
            seat.joined = True
            room.seats[seat_index] = seat
            room.touched = time.monotonic()
            broadcast(room)
            self.json(200, {"code": room.code, "token": token, "seat": seat_index})
            return True

        if path == "/api/deal" and method == "POST":
            if room is None:
                self.json(404, {"error": "no such room"})
                return True
            seat = seat_for_token(room, query.get("token"))
            if seat < 0:
                self.json(403, {"error": "not at this table"})
                return True
            if room.game:
                self.json(409, {"error": "already dealt"})
                return True
            if any(not s.joined for s in room.seats):
                self.json(409, {"error": "every seat has to be filled before the deal"})
                return True
            room.game = create_match(seats=[s.name for s in room.seats], deals=room.deals)
            broadcast(room)
            self.json(200, {"ok": True, "deal": room.game.deal, "deals": room.deals})
            return True

        if path == "/api/action" and method == "POST":
            if room is None:
                self.json(404, {"error": "no such room"})
                return True
            seat = seat_for_token(room, query.get("token"))
            if seat < 0:
                self.json(403, {"error": "not at this table"})
                return True
            if not room.game:
                self.json(409, {"error": "no deal has been dealt yet"})
                return True
            try:
                body = self.read_body()
            except ValueError as exc:
                self.json(400, {"error": str(exc)})
                return True
            try:
                apply(room.game, seat, body)
            except RuleError as exc:
                self.json(400, {"error": str(exc)})
                return True
            except Exception:  # noqa: BLE001
                traceback.print_exc()
                self.json(500, {"error": "internal error"})
                return True
            room.touched = time.monotonic()
            broadcast(room)
            self.json(200, {"ok": True})
            return True

        # One-shot state for a seat, without touching its stream. A freshly opened SSE
        # connection can sit in a proxy's buffer for a moment; this makes the table appear
        # immediately instead of after the first push or heartbeat.
        if path == "/api/state" and method == "GET":
            if room is None:
                self.json(404, {"error": "no such room"})
                return True
            seat = seat_for_token(room, query.get("token"))
            if seat < 0:
                self.json(403, {"error": "not at this table"})
                return True
            # Asking for your state is proof of life: the heartbeat is the transport now,
            # so without this every player not holding an SSE stream looks "away".
            peer = room.seats[seat].peer
            if peer.out is None:
                peer.polling = True
                peer.last_poll = time.monotonic()
            extra = self._extra(room)
            if not room.game:
                self.json(200, {"type": "lobby", "you": seat, **extra})
                return True
            self.json(200, {"type": "view", **extra, "view": view(room.game, seat)})
            return True

        if path == "/api/status" and method == "GET":
            if room is None:
                self.json(404, {"error": "no such room"})
                return True
            self.json(200, {
                "code": room.code,
                "seats": presence_of(room),
                "started": bool(room.game),
                "deal": room.game.deal if room.game else None,
            })
            return True

        if path == "/api/stream" and method == "GET":
            if room is None:
                self.json(404, {"error": "no such room"})
                return True
            seat = seat_for_token(room, query.get("token"))
            if seat < 0:
                self.json(403, {"error": "not at this table"})
                return True
            self.stream(room, seat)
            return True

        if path == "/api/poll" and method == "GET":
            if room is None:
                self.json(404, {"error": "no such room"})
                return True
            seat = seat_for_token(room, query.get("token"))
            if seat < 0:
                self.json(403, {"error": "not at this table"})
                return True
            peer = room.seats[seat].peer
            was_live = seat_live(room, seat)
            peer.close()
            peer.polling = True
            peer.last_poll = time.monotonic()
            messages = peer.queue[:]
            peer.queue.clear()
            if not was_live:
                broadcast(room)
            self.json(200, {"messages": messages})
            return True

        return False

    @staticmethod
    def _extra(room: Room) -> dict:
        return {
            "code": room.code,
            "seatCount": room.size,
            "deals": room.deals,
            "presence": presence_of(room),
            "started": bool(room.game),
            "waiting": sum(1 for s in room.seats if not s.joined),
        }

    def stream(self, room: Room, seat: int) -> None:
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream; charset=utf-8")
        self.send_header("Cache-Control", "no-cache, no-transform")
        self.send_header("X-Accel-Buffering", "no")
        self.end_headers()
        self.wfile.write(b"retry: 2000\n\n")
        # Two kilobytes of comment: some proxies will not forward a chunked response
        # until it has a useful amount to send, which turns a live table into a blank one.
        self.wfile.write((": " + "\u00b7" * 4000 + "\n\n").encode("utf-8"))
        self.wfile.flush()

        peer = room.seats[seat].peer
        if peer.out is not None and peer.out is not self.wfile:
            # The same seat opened twice — usually the same person in two tabs. The older
            # stream is told, so that window can say it is not the one playing any more.
            peer.write(f'data: {json.dumps({"type": "seat-taken"})}\n\n')
            peer.close()
        peer.out = self.wfile
        peer.polling = False
        queued = peer.queue[:]
        peer.queue.clear()
        for message in queued:
            peer.write(f"data: {json.dumps(message, ensure_ascii=False)}\n\n")

        broadcast(room)
        try:
            while True:
                time.sleep(HEARTBEAT_S)
                if not peer.write(": ping\n\n"):
                    break
        except (BrokenPipeError, ConnectionResetError, OSError):
            pass
        finally:
            if peer.out is self.wfile:
                peer.out = None
            broadcast(room)


class KingsServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True

    def __init__(self, addr, handler, port: int):
        super().__init__(addr, handler)
        self.port = port


def reap_peers() -> None:
    """A poller that stops polling is offline, even though it never closed anything."""
    while True:
        time.sleep(5)
        with rooms_lock:
            for room in list(rooms.values()):
                changed = False
                for seat in room.seats:
                    peer = seat.peer
                    if peer.polling and peer.out is None and (time.monotonic() - peer.last_poll) > PEER_TIMEOUT_S:
                        peer.polling = False
                        changed = True
                if changed:
                    broadcast(room)


def reap_rooms() -> None:
    while True:
        time.sleep(30 * 60)
        now = time.monotonic()
        with rooms_lock:
            for code, room in list(rooms.items()):
                any_live = any(seat_live(room, i) for i in range(len(room.seats)))
                if not any_live and (now - room.touched) > ROOM_TTL_S:
                    del rooms[code]


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    port = 5175
    host = "0.0.0.0"
    if argv and not argv[0].startswith("-"):
        port = int(argv.pop(0))
    if "--port" in argv:
        port = int(argv[argv.index("--port") + 1])
    if "--host" in argv:
        host = argv[argv.index("--host") + 1]

    server = KingsServer((host, port), Handler, port)
    threading.Thread(target=reap_peers, daemon=True).start()
    threading.Thread(target=reap_rooms, daemon=True).start()
    print(f"Kings Corners \u2192 http://localhost:{port}  (pid {os.getpid()}, {MIN_SEATS}-{MAX_SEATS} seats)", flush=True)
    print(f"  same table on this network \u2192 http://{lan_address()}:{port}/", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
