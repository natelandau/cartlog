[![Automated Tests](https://github.com/natelandau/cartlog/actions/workflows/automated-tests.yml/badge.svg)](https://github.com/natelandau/cartlog/actions/workflows/automated-tests.yml) [![codecov](https://codecov.io/gh/natelandau/cartlog/graph/badge.svg?token=QdFXvrhoP5)](https://codecov.io/gh/natelandau/cartlog)

# cartlog

Scan your receipts, let cartlog read them for you, and track what you buy and what it costs over time.

Drop a photo or PDF of a receipt into cartlog and it picks out the store, date, and every item, then sorts each one into a category. Once your receipts are saved, you can chart a product's price history, compare prices across stores, and see where your money goes by category, all from your browser or the command line.

Plenty of apps track all of your spending. cartlog is built for one job instead: following the cost of the things you buy again and again, like groceries.

## Features

cartlog takes a receipt photo and gives you back spending you can search and chart:

- Turn a photo or PDF of a receipt into itemized data, read for you by your chosen LLM provider
- Sort every item into a category automatically, and recheck anything it could not place
- Upload receipts and fix anything that needs a second look in your browser, or re-read a receipt from its saved image when a parse needs another pass
- Drop receipts into a synced watch folder and have them imported automatically, no upload step
- Keep using the app while your receipts are read in the background
- Chart a product's price history, compare prices across stores by normalized unit price, and total your spending by category
- Capture each item's size and unit so unit-price comparisons stay honest, repairing garbled sizes from the receipt text and, when a size is missing, inferring it from the same product on other receipts (gap-filling can use the assist model, a billable pass)
- See sizes and unit prices in your preferred measurement system, switchable between imperial and metric with the in-app units toggle
- Export your line items to CSV or JSON, filtered by date, store, or category, from the browser or the command line
- See what each receipt cost to read, so your LLM spend stays visible
- Keep all your data in a single file, with no separate database to install or maintain

## Requirements

You need an API key for your chosen LLM provider (Anthropic by default) and one of two ways to run cartlog:

- An API key for your LLM provider - [Anthropic](https://console.anthropic.com/) by default (required to parse receipts)
- Either Docker, or Python 3.14+ with [uv](https://docs.astral.sh/uv/) for a local install

## Quick start with Docker

Running in Docker is the fastest way to get cartlog up. It pulls a prebuilt, multi-architecture image (amd64 and arm64) from the GitHub Container Registry at [`ghcr.io/natelandau/cartlog`](https://github.com/natelandau/cartlog/pkgs/container/cartlog), so there is nothing to build. Everything runs in a single container, and your receipts and data are saved on disk so they survive restarts and upgrades.

1. Clone this repository and change into it (this gives you `compose.yaml` and the sample config):

    ```bash
    git clone https://github.com/natelandau/cartlog.git
    cd cartlog
    ```

2. Create your secret config from the sample and add your credentials:

    ```bash
    cp .env.sample .env.secret
    ```

    Open `.env.secret` and fill in two required values:

    - `CARTLOG_SECRET_KEY` - a random string that signs your session cookies and CSRF tokens. Generate one with `openssl rand -hex 32`.
    - The API key for your chosen LLM provider (e.g. `ANTHROPIC_API_KEY`).

    Optionally set `CARTLOG_PARSE_MODEL` / `CARTLOG_ASSIST_MODEL` to switch providers. Every other value is optional.

3. Pull the image and start the container in the background:

    ```bash
    docker compose pull
    docker compose up -d
    ```

4. Open the web UI at [http://localhost:8000](http://localhost:8000). cartlog walks you through a short setup wizard to create the first admin account, then you can upload a receipt.

To follow the logs, run `docker compose logs -f`. To stop cartlog, run `docker compose down`. Your data persists in the `cartlog-data` volume between restarts.

`compose.yaml` tracks the `latest` tag, which always points at the newest release. To pin a specific version instead, set the image to a release tag such as `ghcr.io/natelandau/cartlog:0.3`. To upgrade later, pull the newer image and recreate the container:

```bash
docker compose pull
docker compose up -d
```

### File ownership (PUID and PGID)

The container writes the database and receipt images as an unprivileged user. To make those files match a user on your host, set `PUID` and `PGID` in `.env.secret` to your user's IDs (find them with `id -u` and `id -g`). The container chowns the data volume to those IDs on startup, so this also works on an existing volume.

### Docker configuration

The container reads its configuration from the environment and from `.env.secret`. An exported environment variable always overrides the file. `compose.yaml` sets these defaults, which you can change there or override in `.env.secret`:

| Variable          | Default         | Description                                            |
| ----------------- | --------------- | ------------------------------------------------------ |
| `PUID` / `PGID`   | `1000` / `1000` | User and group that own the data volume                |
| `TZ`              | `Etc/UTC`       | Container timezone, e.g. `America/New_York`            |
| `CARTLOG_HOST`    | `0.0.0.0`       | Interface the web server binds to inside the container |
| `CARTLOG_PORT`    | `8000`          | Port the web server listens on                         |
| `CARTLOG_WORKERS` | `1`             | How many receipts to read at the same time             |

The published port is mapped in `compose.yaml`. To serve cartlog on a different host port, change the `ports` mapping (for example `"9000:8000"`).

### Health check

cartlog answers a health endpoint at `/healthz` so Docker, an uptime monitor, or an orchestrator can tell whether a running container actually works. A `GET /healthz` returns HTTP `200` with `{"status": "ok", ...}` when the app is healthy, and HTTP `503` with `{"status": "unhealthy", ...}` when it is not. The body also reports each check:

- `database`: the SQLite database answers a query
- `migrations`: the schema is upgraded to the latest version
- `worker`: at least one receipt-processing worker is running

The endpoint needs no authentication, so it works even before you create the first account. Check it by hand with `curl`:

```bash
curl -fsS http://localhost:8000/healthz
```

To have Docker watch the container, add a healthcheck to the `cartlog` service in `compose.yaml` (the image already ships with `curl`):

```yaml
healthcheck:
  test: ["CMD", "curl", "-fsS", "http://localhost:8000/healthz"]
  interval: 30s
  timeout: 5s
  retries: 3
  start_period: 20s
```

`curl -f` exits non-zero on the `503` response, so Docker marks the container `unhealthy` when a check fails.

## Local install

For development, or to run cartlog without Docker, install it with uv.

1. Install dependencies, including the frontend toolchain for the web UI:

    ```bash
    uv sync
    npm install
    ```

2. Create and edit your config:

    ```bash
    cp .env.sample .env.secret
    ```

    Set two required values in `.env.secret`:

    - `CARTLOG_SECRET_KEY` - a random string that signs your session cookies and CSRF tokens. Generate one with `openssl rand -hex 32`.
    - The API key for your chosen LLM provider (e.g. `ANTHROPIC_API_KEY`).

    Optionally set `CARTLOG_PARSE_MODEL` / `CARTLOG_ASSIST_MODEL` to point at a different provider or model.

3. Start the web server and worker:

    ```bash
    uv run cartlog serve
    ```

    The web UI is at [http://localhost:8000](http://localhost:8000). On the first launch, cartlog walks you through a short setup wizard to create the first admin account. Pass `--host`, `--port`, or `--workers` to change how it runs.

## First run and accounts

cartlog requires user accounts. On a fresh install, the app opens a setup wizard at `/setup` where you create the first admin account. After that, `/setup` is locked and all admin work happens inside the web UI.

### Secret key (required)

`CARTLOG_SECRET_KEY` must be set before the app starts. cartlog fails fast at startup with a clear error if it is missing. Generate a value with:

```bash
openssl rand -hex 32
```

Add it to `.env.secret`:

```bash
CARTLOG_SECRET_KEY=<your-generated-value>
```

### Roles

cartlog has three roles. Higher roles inherit all permissions from lower ones.

| Role | What they can do |
|---|---|
| Viewer | Browse, search, view analytics, export data, change their own password |
| Editor | Everything a Viewer can do, plus upload, edit, and delete receipts; manage stores, products, categories, and merges; mint and revoke their own API tokens |
| Admin | Everything an Editor can do, plus create and manage user accounts, set roles, reset passwords, configure app settings and integrations |

Anonymous visitors (not signed in) can browse and search by default. See [Public read access](#public-read-access) below.

### Public read access

The admin settings page at `/admin/settings` has a toggle called **Allow anonymous read**. When it is on (the default), anyone who can reach the app can browse receipts, search, view analytics, and export data without signing in. Editing and admin actions always require a sign-in.

Turn it off to require a sign-in for everything, which is useful when the app is reachable outside a trusted network.

### API tokens for the Apple Shortcut

The Apple Shortcut uploads receipts directly from your device. It needs an API token to authenticate.

To mint a token:

1. Sign in as an Editor or Admin.
2. Go to your account area and open **API tokens**.
3. Create a new token and give it a name (for example, "iPhone shortcut").
4. Copy the token. It is shown once and cannot be retrieved later.

Send the token in the request header as either:

```
Authorization: Bearer <your-token>
X-Cartlog-Token: <your-token>
```

The integrations page in the web UI shows the exact upload URL and header the Shortcut needs.

### Password recovery

If you forget your password, ask another Admin to reset it. When an Admin resets a password, the app generates a temporary password shown once, and the account is flagged to require a new password on next login.

If you are the only Admin and cannot sign in, you can recover access by stopping the app, emptying the `users` table in the SQLite database, and restarting. With no users, the setup wizard re-opens at `/setup` so you can create a new admin.

> **Note:** Keep at least two Admin accounts to avoid this scenario.

## Send receipts with Apple Shortcuts

You can send receipt images or PDFs directly from the share sheet on iPhone, iPad, or Mac to cartlog without installing any app. The built-in Shortcuts app handles the upload.

The Shortcut authenticates using an API token minted by an Editor or Admin account. See [API tokens for the Apple Shortcut](#api-tokens-for-the-apple-shortcut) for how to create one.

Open **Admin -> Integrations** (at `/admin/integrations`) in the web UI and tap **Install the Shortcut**. The integrations page shows the upload URL and the API token header the Shortcut must send. When you add the Shortcut, it asks once for your cartlog URL and token. cartlog just needs to be reachable from the device.

Once the Shortcut is installed, open a receipt in Photos or Files, tap the share button, and run the Shortcut. The receipt appears in cartlog within a few seconds.

## Configuration

All settings are read from the environment and from `.env.secret`, with environment variables taking precedence. Two settings are required: `CARTLOG_SECRET_KEY` and the API key for your chosen LLM provider. All others are optional. See [.env.sample](.env.sample) for the full list with descriptions.

cartlog is provider-agnostic. For how to point it at Anthropic, OpenAI, Gemini, an API router like OpenRouter, or a local model, see [Choosing an LLM provider](#choosing-an-llm-provider).

| Variable                                     | Default                      | Description                                                                    |
| -------------------------------------------- | ---------------------------- | ------------------------------------------------------------------------------ |
| `CARTLOG_SECRET_KEY`                         | (none, required)             | Signs sessions and CSRF tokens. Generate with `openssl rand -hex 32`           |
| `ANTHROPIC_API_KEY` (or provider equivalent) | (none, required)             | API key for your chosen provider; read by Pydantic AI                          |
| `CARTLOG_COOKIE_SECURE`                      | `true`                       | Send the session cookie only over HTTPS. Set `false` for plain-HTTP LAN/dev   |
| `CARTLOG_SESSION_LIFETIME_DAYS`              | `14`                         | Absolute session lifetime in days                                              |
| `CARTLOG_SESSION_IDLE_TIMEOUT_DAYS`          | `7`                          | Idle timeout in days; sessions expire if inactive for this long                |
| `CARTLOG_PARSE_MODEL`                        | `anthropic:claude-opus-4-8`  | Provider-prefixed model that reads your receipts                               |
| `CARTLOG_ASSIST_MODEL`                       | `anthropic:claude-haiku-4-5` | Cheaper secondary model for text-only passes (structured output, no vision)    |
| `CARTLOG_DATABASE_URL`                       | `cartlog.db`                 | Where to store your data file (the folder must exist)                          |
| `CARTLOG_IMAGE_STORAGE_DIR`                  | `receipt_images`             | Where to keep copies of your receipt images                                    |
| `CARTLOG_BACKUP_DIR`                         | (none, optional)             | Default `cartlog backup` destination; created if missing. Unset uses the cwd   |
| `CARTLOG_REVIEW_CONFIDENCE_THRESHOLD`        | `0.7`                        | Receipts cartlog is unsure about are flagged for you to review                 |

For `CARTLOG_DATABASE_URL`, give a plain path to where you want the data file, such as `cartlog.db` or `/app/data.db`. cartlog checks the folder exists when it starts and handles the rest, so most people never need to change the default.

## Backups

`cartlog backup` saves your whole dataset to one compressed archive: the SQLite database and every receipt image. You can run it while the app is serving. cartlog snapshots the database with SQLite's `VACUUM INTO`, so the copy stays consistent even while receipts are being ingested.

Create a backup:

```bash
uv run cartlog backup
```

This writes a timestamped file such as `cartlog-backup-20260620-143000.tar.gz` to the current directory and prints its path. Pass `--output` (or `-o`) to choose a directory or an exact filename:

```bash
uv run cartlog backup --output /backups
```

To make a directory the standing default, set `CARTLOG_BACKUP_DIR`; runs without `--output` land there instead of the current directory. An explicit `--output` always wins.

```bash
CARTLOG_BACKUP_DIR=/backups uv run cartlog backup
```

You can also download a backup from the web UI: open **Admin → Settings → Backup** and click **Download backup**. cartlog builds the archive and streams it straight to your browser (it is never written to `CARTLOG_BACKUP_DIR`).

The archive uses the same layout no matter where your database and images live:

```text
cartlog-backup-20260620-143000.tar.gz
├── cartlog.db
└── receipt_images/
```

### Back up a Docker deployment

Run the command inside the running container, then copy the archive out. The command prints the archive path, so use that exact name in the copy step:

```bash
docker compose exec cartlog cartlog backup --output /data
docker compose cp cartlog:/data/cartlog-backup-20260620-143000.tar.gz ./
```

### Restore from a backup

The archive layout matches the container's `/data` directory, so a restore is an extract into a fresh data directory. With the app stopped, run:

```bash
mkdir -p /data
tar -xzf cartlog-backup-20260620-143000.tar.gz -C /data
```

Start cartlog with `CARTLOG_DATABASE_URL=/data/cartlog.db` and `CARTLOG_IMAGE_STORAGE_DIR=/data/receipt_images` (the Docker image's defaults), and it continues from where the backup was taken.

## Choosing an LLM provider

cartlog reads receipts through [Pydantic AI](https://ai.pydantic.dev/), so you can switch providers without touching code. It ships with support for Anthropic, OpenAI, and Google Gemini, plus any OpenAI-compatible endpoint, which covers API routers like [OpenRouter](https://openrouter.ai/) and local servers like [Ollama](https://ollama.com/). Switching takes two steps: set the model with `CARTLOG_PARSE_MODEL` and `CARTLOG_ASSIST_MODEL`, then supply that provider's API key under its own variable name.

Model values use a `provider:model` format. Set both variables in `.env.secret`, alongside the matching key:

```bash
# Anthropic (the default)
ANTHROPIC_API_KEY=sk-ant-...
CARTLOG_PARSE_MODEL=anthropic:claude-opus-4-8
CARTLOG_ASSIST_MODEL=anthropic:claude-haiku-4-5

# OpenAI
OPENAI_API_KEY=sk-...
CARTLOG_PARSE_MODEL=openai:gpt-5.2

# Google Gemini
GEMINI_API_KEY=...
CARTLOG_PARSE_MODEL=google:gemini-2.5-pro

# OpenRouter (any OpenAI-compatible router works the same way)
OPENROUTER_API_KEY=sk-or-...
CARTLOG_PARSE_MODEL=openrouter:anthropic/claude-3.5-sonnet
```

`CARTLOG_PARSE_MODEL` is the primary model: it reads the receipt image or PDF and does the heavy extraction, so it must support vision (image input), structured output, and PDF documents. `CARTLOG_ASSIST_MODEL` is a cheaper secondary model that handles focused follow-up passes over already-extracted text, so it needs structured output only and works purely from text (no vision). A small, fast model fits well there (the Anthropic default pairs Opus for parsing with Haiku as the assist model). The two variables can use different providers. For exact model-id syntax, see the [Pydantic AI models documentation](https://ai.pydantic.dev/models/).

Local and self-hosted models work through that same OpenAI-compatible path. They are held to the same capability requirements below, which many small local models do not meet. To use a provider cartlog does not bundle (for example Cohere or Bedrock), add its [Pydantic AI extra](https://ai.pydantic.dev/models/) to the install and rebuild.

> **Important:** `CARTLOG_PARSE_MODEL` must support image (vision) input and structured output, because cartlog hands it the receipt picture and asks for a typed result. To read PDF receipts, it must also accept PDF documents. `CARTLOG_ASSIST_MODEL` needs structured output only, since it works from already-extracted text with no vision required. Point either variable at a model that lacks these capabilities and ingestion fails: the parse step errors and the receipt is flagged for review. Current frontier models from Anthropic, OpenAI, and Google meet all three requirements; many smaller and local models do not. Test one receipt before switching your whole setup.

## Contributing

Want to help improve cartlog? See [CONTRIBUTING.md](CONTRIBUTING.md) for how to set up a development environment, run the checks, and open a pull request.

## License

Released under the [MIT License](LICENSE).
