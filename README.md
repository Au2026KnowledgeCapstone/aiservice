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

## Operational database

Set `DATABASE_URL` in `.env` to a PostgreSQL database owned by this service,
then apply the schema:

```sh
.venv/bin/alembic upgrade head
.venv/bin/alembic current
```

The initial migration creates `jobs` and `outbox`. Run migrations before
starting the API; table creation is not performed automatically at startup.

For local development with PostgreSQL tools installed, an isolated cluster
can live in the ignored `.local/` directory:

```sh
initdb -D .local/postgres -A trust --no-locale -E UTF8
pg_ctl -D .local/postgres -l .local/postgres.log -o '-h 127.0.0.1 -p 55432 -k /tmp' start
psql -h 127.0.0.1 -p 55432 -d postgres -v ON_ERROR_STOP=1 -c "CREATE ROLE aiservice LOGIN PASSWORD 'aiservice' NOSUPERUSER NOCREATEDB NOCREATEROLE;" -c 'CREATE DATABASE aiservice OWNER aiservice;'
```

Use `DATABASE_URL=postgresql+psycopg2://aiservice:aiservice@127.0.0.1:55432/aiservice`
in `.env`. This cluster uses trust authentication for local development only.
Initialize the cluster and create the role/database once. On subsequent runs,
use the `pg_ctl ... start` command above. Stop it with:

```sh
pg_ctl -D .local/postgres stop
```

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
