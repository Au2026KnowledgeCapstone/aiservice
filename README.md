# aiservice

The AI execution tier for BlindSpot. It receives a job from BlindSpotBE,
drives a browser (or an equivalent verification engine), and reports outcomes
back. It holds no product database, no user accounts, and no authorization
model — those belong to BlindSpotBE. The only storage this service owns is
operational: which jobs are in flight, and what has not yet been delivered
back to BE.

`ENGINE` picks which verification engine runs: one of `mvp`, `graph`, `stub`.
Unknown values fail at startup.

## Running

```sh
python3.12 -m venv .venv
.venv/bin/pip install -e '.[test,engine]'
.venv/bin/python -m pytest -q
```

Copy `.env.example` to `.env` and adjust as needed.

## Layout

```
app/
  core/       # configuration (the ENGINE flag), logging, service auth
engines/
  base.py     # the Engine protocol — the contract every engine satisfies
  stub/       # deterministic engine; proves the round trip before a browser exists
  graph/      # reserved — crawl/graph engine, not implemented yet
```

An engine never touches the database, the filesystem, the network policy, or
an AI SDK directly — it only sees what `engines/base.py` hands it.
