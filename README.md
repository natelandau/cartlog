[![Automated Tests](https://github.com/natelandau/cartlog/actions/workflows/automated-tests.yml/badge.svg)](https://github.com/natelandau/cartlog/actions/workflows/automated-tests.yml) [![codecov](https://codecov.io/gh/natelandau/cartlog/graph/badge.svg?token=QdFXvrhoP5)](https://codecov.io/gh/natelandau/cartlog)

# cartlog

Photograph a grocery receipt. cartlog reads it, itemizes it, and tracks what you pay over time.

Many apps track all of your spending. cartlog does one job instead. It follows the cost of the things you buy again and again, like groceries. You self-host it, and all of your data stays in a single SQLite file on your own machine.

## Features

### Read your receipts for you

- Turn a photo or a PDF into itemized data with the LLM provider you choose.
- Sort every item into a category.
- Discover each item's size and unit from the receipt text
- Categorize products into similar groups
- Create synonyms for products across stores that use different names for the same product
- Flag a low-confidence receipt for review, and re-read it from the saved image on demand.

### Show you what you pay

- Chart one product's unit price over time, across every store you shop at.
- Compare two stores item by item, by normalized unit price.
- Chart spending over time, stacked by category or filtered by store.
- Drill into category spending with a treemap, from category down to product.
- Rank the products that take the largest share of your spending.
- Open a dashboard with headline totals, a receipt activity heatmap, and your recent receipts.
- Switch every size and unit price between imperial and metric with one toggle.

### Keep the data clean

- Search every line item and edit it in place.
- Merge duplicate products and stores, and keep the merge as a rule for future receipts.
- Create, rename, and merge categories.

### Stay in control

- Add receipts from the browser, from the iOS and macOS share sheet, or from a watch folder.
- Export line items to CSV or JSON, filtered by date, store, or category.
- See what each receipt cost to read, so your LLM spend stays visible.
- Back up the database and every receipt image into one archive.
- Give each person a role: viewer, editor, or admin.

## Requirements

You need two things:

- An API key for an LLM provider. cartlog works with Anthropic, OpenAI, and Google Gemini, plus any OpenAI-compatible endpoint.
- Docker, or Python 3.14 or newer with [uv](https://docs.astral.sh/uv/).

## Quick start with Docker

Docker is the fastest way to run cartlog. The image is prebuilt for amd64 and arm64 at [`ghcr.io/natelandau/cartlog`](https://github.com/natelandau/cartlog/pkgs/container/cartlog), so there is nothing to compile.

1. Clone this repository, which gives you `compose.yaml` and the sample configuration:

    ```bash
    git clone https://github.com/natelandau/cartlog.git
    cd cartlog
    ```

2. Create your configuration file:

    ```bash
    cp .env.sample .env.secret
    ```

3. Open `.env.secret` and set two values:
    - `CARTLOG_SECRET_KEY`: a random string that signs sessions and CSRF tokens. Generate one with `openssl rand -hex 32`.
    - Your provider's API key, such as `ANTHROPIC_API_KEY`.

    Every other setting is optional, and `.env.sample` documents each one inline. A variable exported in the environment always overrides the file.

4. Start the container:

    ```bash
    docker compose pull
    docker compose up -d
    ```

5. Open [http://localhost:8000](http://localhost:8000) and complete the setup wizard.

The wizard creates the first admin account. After that, you can upload a receipt.

Your database and images live in the `cartlog-data` volume, so they survive restarts and upgrades. To upgrade, run `docker compose pull` and then `docker compose up -d`. `compose.yaml` tracks the `latest` tag. To pin a version instead, set the image to a release tag such as `ghcr.io/natelandau/cartlog:0.6`.

### Docker settings

`compose.yaml` sets these defaults. Change them there, or override them in `.env.secret`.

| Variable          | Default         | Description                                        |
| ----------------- | --------------- | -------------------------------------------------- |
| `PUID` / `PGID`   | `1000` / `1000` | User and group that own the data volume            |
| `TZ`              | `Etc/UTC`       | Container timezone, such as `America/New_York`     |
| `CARTLOG_HOST`    | `0.0.0.0`       | Interface the web server binds to in the container |
| `CARTLOG_PORT`    | `8000`          | Port the web server listens on                     |
| `CARTLOG_WORKERS` | `1`             | How many receipts cartlog reads at the same time   |

Set `PUID` and `PGID` to your host user's IDs so the database and images belong to that user. Find the IDs with `id -u` and `id -g`. The container corrects ownership of the data volume at startup, so this also works on a volume that already holds files.

To serve cartlog on a different host port, change the `ports` mapping in `compose.yaml`, for example `"9000:8000"`.

### Health check

`GET /healthz` needs no sign-in. It returns HTTP `200` when the database, the migrations, and the worker pool are all healthy, and HTTP `503` when one of them is not.

To let Docker watch the container, add this to the `cartlog` service in `compose.yaml`:

```yaml
healthcheck:
    test: ["CMD", "curl", "-fsS", "http://localhost:8000/healthz"]
    interval: 30s
    timeout: 5s
    retries: 3
    start_period: 20s
```

## Choose an LLM provider

cartlog reads receipts through [Pydantic AI](https://ai.pydantic.dev/), so you can change providers without touching code. Set the model in `.env.secret`, and supply that provider's own API key.

`CARTLOG_PARSE_MODEL` is the primary model. It reads the receipt image or PDF, so it must support vision, structured output, and PDF documents. `CARTLOG_ASSIST_MODEL` is a cheaper second model. It works from text that cartlog already extracted, so it needs structured output only. The two variables can name different providers.

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

# OpenRouter, or any OpenAI-compatible router
OPENROUTER_API_KEY=sk-or-...
CARTLOG_PARSE_MODEL=openrouter:anthropic/claude-3.5-sonnet
```

For the exact model-id syntax, see the [Pydantic AI models documentation](https://ai.pydantic.dev/models/). cartlog does not bundle every provider, for example Cohere and Bedrock. To use one, add its Pydantic AI extra to the install, then rebuild.

> **Note:** A parse model that lacks vision, structured output, or PDF support fails on every receipt, and cartlog flags each one for review. Current frontier models from Anthropic, OpenAI, and Google meet all three requirements. Many small and local models do not. Read one receipt as a test before you switch your whole setup.

## Add receipts

cartlog accepts images and PDFs through three channels. Every receipt is queued and read in the background, and the **Jobs** page shows progress.

### From the browser

Sign in as an editor or an admin, open **Upload**, and select one or more files.

### From an iPhone, iPad, or Mac

An Apple Shortcut sends receipts from the share sheet. There is no app to install.

1. Sign in as an editor or an admin.
2. Open your account area, then **API tokens**, and create a token.
3. Copy the token before you continue, because cartlog shows it one time only.
4. Open **Admin -> Integrations** and click **Install the Shortcut**.
5. Give the Shortcut your cartlog URL and the token when it asks.

To send a receipt, open it in Photos or Files, tap share, and run the Shortcut. cartlog must be reachable from the device.

The same token authenticates any HTTP client. Send it in either header:

```text
Authorization: Bearer YOUR_TOKEN
X-Cartlog-Token: YOUR_TOKEN
```

### From a watch folder

Open **Admin -> Settings** and set a watch folder. cartlog polls that folder and imports every image and PDF that appears in it. Each file then moves into a `processed` or a `failed` subfolder. Point a phone's cloud-sync folder at it, and receipts arrive with no upload step.

## Accounts and access

The setup wizard at `/setup` creates the first admin account on a fresh install. After that, `/setup` is locked and all account work happens in the web UI. Each higher role holds every permission of the roles under it.

| Role   | What they can do                                                                                                                       |
| ------ | -------------------------------------------------------------------------------------------------------------------------------------- |
| Viewer | Browse, search, view the analyses, export data, and change their own password                                                          |
| Editor | Everything a viewer can do, plus upload, edit, and delete receipts, manage stores, products, and categories, and mint their own tokens |
| Admin  | Everything an editor can do, plus manage accounts and roles, reset passwords, and change application settings                          |

**Allow anonymous read** on **Admin -> Settings** is on by default. Anyone who reaches the app can then browse, search, and export without a sign-in. Editing and admin work always need a sign-in. Turn the toggle off when the app is reachable outside a trusted network.

If you forget your password, ask another admin to reset it. cartlog shows a temporary password one time, and forces a new password at the next sign-in.

> **Note:** Keep at least two admin accounts. If your only admin cannot sign in, the sole way back is to stop the app, empty the `users` table in the database, and restart. The setup wizard then re-opens at `/setup`.

## Back up and restore

`cartlog backup` writes the database and every receipt image into one `.tar.gz`. Run it while the app serves traffic. cartlog snapshots the database with SQLite's `VACUUM INTO`, so the copy stays consistent even while a worker reads a receipt.

```bash
uv run cartlog backup
```

That writes a timestamped file such as `cartlog-backup-20260620-143000.tar.gz` to the current directory, and prints its path. Pass `--output` to choose a directory or an exact filename. To set a standing default, set `CARTLOG_BACKUP_DIR`. An explicit `--output` always wins.

```bash
uv run cartlog backup --output /backups
```

In Docker, run the command in the container and then copy the archive out:

```bash
docker compose exec cartlog cartlog backup --output /data
docker compose cp cartlog:/data/cartlog-backup-20260620-143000.tar.gz ./
```

You can also open **Admin -> Settings -> Backup** and click **Download backup**. cartlog builds the archive and streams it to your browser without writing it to disk.

Every archive uses the same layout:

```text
cartlog-backup-20260620-143000.tar.gz
├── cartlog.db
└── receipt_images/
```

That layout matches the container's `/data` directory, so a restore is one extract. Stop the app first:

```bash
mkdir -p /data
tar -xzf cartlog-backup-20260620-143000.tar.gz -C /data
```

Then start cartlog with `CARTLOG_DATABASE_URL=/data/cartlog.db` and `CARTLOG_IMAGE_STORAGE_DIR=/data/receipt_images`, which are the Docker image's defaults. cartlog continues from the point where you took the backup.

## Run without Docker

`cartlog serve` runs the migrations, the web server, and the ingestion workers in one process.

1. Install the Python and frontend dependencies:

    ```bash
    uv sync
    npm install
    ```

2. Create your configuration file, then set `CARTLOG_SECRET_KEY` and your provider's API key in it:

    ```bash
    cp .env.sample .env.secret
    ```

3. Start the app:

    ```bash
    uv run cartlog serve
    ```

4. Open [http://localhost:8000](http://localhost:8000) and complete the setup wizard.

`serve` binds `127.0.0.1` by default. To reach cartlog from another machine, pass `--host 0.0.0.0`. `--port` changes the port, and `--workers` changes how many receipts cartlog reads at the same time.

The app compiles its stylesheet at startup, which needs Node and npm. To serve a stylesheet that is already built, pass `--skip-css-build`.

## Contributing

To set up a development environment, run the checks, and open a pull request, see [CONTRIBUTING.md](CONTRIBUTING.md).

## License

Released under the [MIT License](LICENSE).
