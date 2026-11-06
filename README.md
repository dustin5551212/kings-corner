# Kings Corner

A table for two to four, six-card hands and eight piles: four originals in a cross,
four corners that only a king may open. Built to the rules on
[bicyclecards.com](https://bicyclecards.com/how-to-play/kings-corner), in Python, with
the same felt, gold and Georgia as `professional-500` and `golf`.

```
Kings Corner  ·  a table for two to four
  engine.py    the rules
  view.py      the redaction boundary (other hands are a count, never cards)
  server.py    rooms, seats, SSE + poll fallback
  policy.py    the deterministic bot behind the CLI and the tests
  public/      the browser client
```

## Run

**Play it now:** <http://jensen-shifted.tun.ply.gg:7251> — the same table, served from this
machine through a playit.gg tunnel, so it works from a phone. On the machine itself it is
<http://localhost:5175>.

```sh
.venv/bin/python -m kings_corner.server        # → http://localhost:5175
.venv/bin/python -m kings_corner.server 5176   # another port
kings-corner --seats 3 --seed 42               # headless match, printed
kings-corner --seats 4 --seed 7 --deals 3 --trace
kings-corner-serve 5176                        # wrapper: server from anywhere
```

Installed on this machine as a user service — `kings-corner.service` on **:5175**
(`systemctl --user status kings-corner`). Ports in use: 5173 professional-500, 5174
golf, 5175 this.

Open the page, name yourself, pick two to four seats and three to seven deals, then
send the invite link round. The first hand waits for every seat; anyone can deal.

Standard library only. Development needs `pytest`:

```sh
uv venv --python /usr/bin/python3 .venv
uv pip install --python .venv/bin/python pytest
.venv/bin/python -m pytest                      # 39 tests
.venv/bin/python tools/e2e_api.py http://127.0.0.1:5175 --seats 3 --deals 3
```

## The rules, as this table plays them

Bicycle's, with the two house decisions that a computer table has to make written
down rather than assumed:

- **Draw first.** A turn is: take one card from the stock, then play as many cards as
  you like, then end the turn. With an empty stock the turn starts with the plays.
- **Build down, red on black.** A card goes on a card one rank higher and the other
  colour. Aces are low, so nothing goes on an ace.
- **Kings open the corners.** Only a king may start NW, NE, SE or SW. A king that was
  dealt to N, E, S or W may be moved into an empty corner — and only from an original
  pile. (Corner to corner is refused: a king pile can never be built on, so allowing
  it would let one king shuttle between two empty corners for ever.)
- **A cleared original takes any card** from your hand.
- **A whole pile moves** onto another pile if the *bottom* card of the moving pile sits
  on the destination's top. Bicycle's wording ("if the top card fits") is loose; the
  bottom is what the rest of the world plays, and it is the only reading that keeps a
  pile a single descending sequence.
- **First hand empty wins the deal.** Play stops there — nobody gets a last turn.
- **A match is five deals by default** (three to seven in the lobby). Most deals won
  takes the match; a tie is shared. Bicycle describes a single game, so the match is
  this table's own wrapper around it.
- **A dead deal.** With the stock empty, a full round of turns in which nobody plays
  anything ends the deal with no winner.
- Bicycle also allows "any valid card or sequence" on an empty foundation spot. This
  table reads that as a single card from your hand — a pile moving onto an empty
  original would make the empty spaces a place to shuffle piles back and forth.

## What is proved, not just written

| Check | Command | Result |
|---|---|---|
| Rules, in Python idiom | `.venv/bin/python -m pytest tests/test_engine.py` | 19 tests |
| Redaction boundary | `... tests/test_view.py` | 4 tests, including a leak sweep over every seat of full matches |
| Joined system | `... tests/test_system.py` | 52 distinct cards at every step, same seed → same match, every bot action accepted |
| Transport | `... tests/test_server.py` | 7 tests: rooms, deals, refusals, polling, a real SSE stream |
| Headless match | `... tests/test_cli.py` | scorecard, determinism, tracing |
| A careless client | `... tests/test_system.py::test_a_naive_picker_cannot_loop_forever` | first-legal-action play never loops |
| Black box, over HTTP | `tools/e2e_api.py` | full matches driven through join/deal/action/state |

`test_a_naive_picker_cannot_loop_forever` exists because it caught a real bug: a king
could be moved from one empty corner to another, so a client that always takes the
first legal action ping-ponged one king for ever. A rule that can undo itself is a rule
that never ends a game.

The leak sweep is the one to keep green. `view.py` is the only place that decides what
leaves the server: your own hand in full, everybody else as a count, the stock as a
number, and a log that says "drew a card" without naming it. Build views from scratch
there rather than deleting fields off an engine state.

## Things worth knowing before editing

- **`apply` is the whole rulebook.** One function, one action per call, `RuleError` for
  anything illegal; the server turns that into a 400 and a test into a failure.
- **The stock's order never leaves the server** — `stock` on the wire is a count. The
  engine keeps the order because that is what makes a seeded match reproducible.
- **camelCase on the wire** (`endedBy`, `deals`, `legal`) because the browser reads it;
  snake_case inside the engine.
- **`public/` is the only UI.** Two screens of state (lobby, table) plus an overlay;
  selection (`S.sel`) lives outside the render so the 1.5s heartbeat cannot wipe it.
- **The heartbeat is the transport.** The EventSource is opened and helps on a LAN, but
  nothing depends on it: a tunnel that buffers the stream would otherwise show a table
  that never moves.
- **`isolation:isolate` on `.pilecards` and `z-index:60` on `#overlay`** are load
  bearing: pile cards carry their own z-index, so without them the deal summary paints
  under the table.

## Not covered

- **No bots at the table.** `policy.py` plays headless matches and drives the tests; the
  server fills seats with people, as golf does. A "play against the house" seat would be
  the next feature, not a fix.
- **No persistence.** A restart loses open rooms — room codes live in memory, and the
  match is deliberately short enough for that to be fine.
- **One thread per connection** (`ThreadingHTTPServer`). Right for four people, wrong
  for four hundred.
- **No scoring.** Bicycle's version has no penalty points, so this table counts deals
  won instead of inventing a points system nobody asked for. If you want the Pagat
  scoring variant (king 10, others 1, play to 50), that is a rule change in
  `engine.py::_end_deal`, not a layer on top.
