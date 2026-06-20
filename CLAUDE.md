# cartlog

cartlog scans grocery receipts, parses them into structured data via Pydantic AI (provider-agnostic), and stores them for price and spend analysis. Python 3.14, FastAPI web UI, SQLite. It is a web-only service; the `cartlog` command runs and hosts the app, plus a `backup` command to export the data.

## Running the app

`cartlog serve` is the single entrypoint: it runs Alembic migrations, seeds categories, starts the FastAPI server, and runs the in-process ingestion worker pool. There is no separate migrate/web/worker step.

```bash
uv run cartlog serve   # web server + workers + migrations
uv run duty dev        # dev mode: templates and CSS reload on change
```

The `duty` task runner wraps common workflows. Invoke it as `uv run duty <task>` (NOT `uv duty`, which fails): `dev`, `lint`, `test`, `build`, `update`.

## Architecture

- `src/cartlog/cli.py` — Typer CLI exposing `serve` (web server + workers + migrations) and `backup` (export the database and receipt images to a single tar.gz).
- `src/cartlog/backup.py` — `create_backup()` snapshots the SQLite database with `VACUUM INTO` and bundles it with the receipt images into one tar.gz (`cartlog.db` + `receipt_images/`). Destination precedence: explicit `output` > `CARTLOG_BACKUP_DIR` > current directory. The web admin Settings page (`Admin → Settings → Backup`) builds into a temp dir and streams the archive to the browser instead of writing it to disk.
- `src/cartlog/web/` — FastAPI app factory (`app.py`), routers, Jinja templates, Tailwind/daisyUI assets.
- `src/cartlog/ingest/` — upload queue and worker pipeline; web uploads enqueue jobs that workers parse.
- `src/cartlog/parsing/` — Pydantic AI vision parser and category classifier.
- `src/cartlog/db/` — SQLAlchemy models, session factory, and seed data. Migrations live in the top-level `alembic/`.
- `src/cartlog/bootstrap.py` — `prepare_runtime()` runs migrations and seeding; called by `serve`.

## Configuration

Settings load from environment variables and `.env.secret` (see `config.py` and `.env.sample`). Exported env vars override the file. `CARTLOG_DATABASE_URL` accepts a bare filesystem path (e.g. `cartlog.db`); the `sqlite:///` prefix is added and the directory is verified at startup.

The LLM provider is selected via `CARTLOG_PARSE_MODEL` and `CARTLOG_CLASSIFY_MODEL`, which take provider-prefixed model strings (e.g. `anthropic:claude-opus-4-8`, `openai:gpt-4o`). Credentials are supplied via each provider's native env var (`ANTHROPIC_API_KEY`, `OPENAI_API_KEY`, `GEMINI_API_KEY`), not a `CARTLOG_`-prefixed key. Pydantic AI resolves the provider and reads the key automatically.

## Frontend / CSS

The web UI uses Tailwind CSS v4 and daisyUI v5. `cartlog serve` compiles `web/static/app.css` at startup, which needs the Node toolchain, so run `npm install` first. Pass `serve --skip-css-build` to serve a prebuilt stylesheet without Node (the Docker image does this). `app.css` is gitignored.

## Development tooling

- **Always use `ty` as the type checker** (never mypy, pyright, or pyre):
    ```bash
    uv run ty check src/
    ```
- **Always use `ruff` as the linter and formatter** (never flake8, pylint, black, or isort):
    ```bash
    uv run ruff check --config pyproject.toml
    uv run ruff format --config pyproject.toml src/ tests/
    ```
- Run the test suite with pytest:
    ```bash
    uv run pytest
    ```

## Python 3.14 syntax notes

- **Parenthesis-free `except` (PEP 758):** Python 3.14 allows `except ValueError, KeyError:`
  with no parentheses, and ruff's formatter may rewrite `except (A, B):` into that form. This
  is NOT the old Python 2 `except A, B` (bind-to-name) syntax. It is semantically identical to
  `except (A, B):` and catches both exception types. Do not "fix" it back to parentheses and do
  not flag it as a bug in review.

## Committing during development

The pre-commit hooks (`prek`) type-check and test the **whole project** on every commit
(`ty` and `pytest` run with `pass_filenames: false`, and `fail_fast: true`). This makes it
impossible to land an intentionally-transient intermediate commit (e.g. a refactor whose
follow-up edits live in a separate commit) while the tree is briefly inconsistent.

When that happens during development, it is OK to commit with `--no-verify` to skip the
hooks. The only requirement: run the full hook suite across all files and get it green
**before the final commit** of the change:

```bash
uv run prek run --all-files
```
