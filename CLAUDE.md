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
- `src/cartlog/parsing/` — Pydantic AI vision parser, category classifier, and a focused LLM size extractor (`size_extractor.py`, `build_size_extractor`) that recovers a package size from line text.
- `src/cartlog/units.py` — pure measure resolution: `resolve_line_measure` layers deterministic size extraction, OCR repair, per-each/count detection, and product-typical inference over `normalize_line_item`, recording provenance (`MeasureSource`).
- `src/cartlog/sizes/` — `extract.py` runs the LLM size extractor over lines that still lack a size, capped per line by `CARTLOG_MAX_SIZE_EXTRACT_ATTEMPTS`.
- `src/cartlog/db/` — SQLAlchemy models, session factory, and seed data. `backfill.py` holds the four-pass `normalize_existing_measures` (deterministic resolve, LLM size recovery, typical-size learning, inference) and runs at startup; it skips lines whose `measure_source` is `MeasureSource.MANUAL`. `apply_line_item_edit` (`receipts/service.py`, the search inline editor) pins `MANUAL` when a human edits a line's unit or size, so the backfill never overwrites that edit. Migrations live in the top-level `alembic/`.
- `src/cartlog/bootstrap.py` — `prepare_runtime()` runs migrations, seeding, and the size-normalization backfill (`normalize_existing_measures`); called by `serve`. The LLM size pass needs the assist model and is skipped when no key is configured.

## Configuration

Settings load from environment variables and `.env.secret` (see `config.py` and `.env.sample`). Exported env vars override the file. `CARTLOG_DATABASE_URL` accepts a bare filesystem path (e.g. `cartlog.db`); the `sqlite:///` prefix is added and the directory is verified at startup.

The LLM provider is selected via `CARTLOG_PARSE_MODEL` and `CARTLOG_ASSIST_MODEL`, which take provider-prefixed model strings (e.g. `anthropic:claude-opus-4-8`, `openai:gpt-4o`). `CARTLOG_PARSE_MODEL` is the primary model: it must support vision (image input), structured output, and PDF documents to read PDF receipts. `CARTLOG_ASSIST_MODEL` is a cheaper secondary model: it needs structured output only and works purely from text (no vision), so a small, fast model fits. Credentials are supplied via each provider's native env var (`ANTHROPIC_API_KEY`, `OPENAI_API_KEY`, `GEMINI_API_KEY`), not a `CARTLOG_`-prefixed key. Pydantic AI resolves the provider and reads the key automatically.

## Frontend / CSS

The web UI uses Tailwind CSS v4 and daisyUI v5. `cartlog serve` compiles `web/static/app.css` at startup, which needs the Node toolchain, so run `npm install` first. Pass `serve --skip-css-build` to serve a prebuilt stylesheet without Node (the Docker image does this). `app.css` is gitignored.

**Always verify front-end changes by driving the running app with Playwright** (do not rely on reading the template alone): confirm the rendered result, htmx interactions (form swaps, dropdowns, pills), and that no request returns a 4xx. The reusable harness lives in `tests/e2e/` — its `live_server` fixture stands up the app factory against a temp seeded DB (no LLM keys needed) and builds the CSS, and the `page` fixture yields a browser page. Add a browser test there and run it with `uv run duty e2e` (installs the Chromium binary, then runs `pytest -m e2e`). The `e2e` marker is deselected by default, so these stay out of `duty test`, `duty lint`, and the pre-commit hooks; a dedicated CI job runs them. For quick one-off exploration you can still `uv run --with playwright python <script>` against `uvicorn.run("cartlog.web.app:create_app", factory=True, ...)`.

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
- **Imports go at the top of the file, never inside a function or test body.** CI runs `uv run duty lint`, whose `ruff` step is `ruff check --no-fix src tests duties.py scripts` and enforces `PLC0415` more strictly than the prek pre-commit hook, so a function-local import that passes prek locally still fails CI. Run `uv run duty lint` before pushing front-end or test changes. The **only** acceptable `# noqa: PLC0415` is a genuine, documented need: breaking a circular import, or lazily loading a heavy/optional dependency with a measurable startup cost. Convenience or scoping is not a reason.
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
