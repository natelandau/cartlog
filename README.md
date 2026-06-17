# cartlog

Scan your grocery receipts, let cartlog read them for you, and track what you buy and what it costs over time.

Drop a photo or PDF of a receipt into cartlog and it picks out the store, date, and every item, then sorts each one into a category. Once your receipts are saved, you can chart a product's price history, compare prices across stores, and see where your money goes by category, all from your browser or the command line.

## Features

cartlog takes a receipt photo and gives you back spending you can search and chart:

- Turn a photo or PDF of a receipt into itemized data, read for you by Claude
- Sort every item into a category automatically, and recheck anything it could not place
- Upload receipts, fix anything that needs a second look, and browse spending charts in your browser
- Keep using the app while your receipts are read in the background
- See a product's price history, compare prices between stores, and total your spending by category
- Keep all your data in a single file, with no separate database to install or maintain

## Requirements

You need an Anthropic API key and one of two ways to run cartlog:

- An [Anthropic API key](https://console.anthropic.com/) (required to parse receipts)
- Either Docker, or Python 3.14+ with [uv](https://docs.astral.sh/uv/) for a local install

## Quick start with Docker

Running in Docker is the fastest way to get cartlog up. Everything runs in a single container, and your receipts and data are saved on disk so they survive restarts and upgrades.

1. Clone this repository and change into it:

   ```bash
   git clone https://github.com/natelandau/cartlog.git
   cd cartlog
   ```

2. Create your secret config from the sample and add your API key:

   ```bash
   cp .env.sample .env.secret
   ```

   Open `.env.secret` and set `CARTLOG_ANTHROPIC_API_KEY`. Every other value is optional.

3. Build and start the container:

   ```bash
   docker compose up --build
   ```

4. Open the web UI at [http://localhost:8000](http://localhost:8000) and upload a receipt.

To run it in the background, use `docker compose up --build -d`. To stop it, run `docker compose down`. Your data persists in the `cartlog-data` volume between restarts.

### File ownership (PUID and PGID)

The container writes the database and receipt images as an unprivileged user. To make those files match a user on your host, set `PUID` and `PGID` in `.env.secret` to your user's IDs (find them with `id -u` and `id -g`). The container chowns the data volume to those IDs on startup, so this also works on an existing volume.

### Docker configuration

The container reads its configuration from the environment and from `.env.secret`. An exported environment variable always overrides the file. `compose.yaml` sets these defaults, which you can change there or override in `.env.secret`:

| Variable          | Default         | Description                                            |
| ----------------- | --------------- | ------------------------------------------------------ |
| `PUID` / `PGID`   | `1000` / `1000` | User and group that own the data volume                |
| `CARTLOG_HOST`    | `0.0.0.0`       | Interface the web server binds to inside the container |
| `CARTLOG_PORT`    | `8000`          | Port the web server listens on                         |
| `CARTLOG_WORKERS` | `1`             | How many receipts to read at the same time             |

The published port is mapped in `compose.yaml`. To serve cartlog on a different host port, change the `ports` mapping (for example `"9000:8000"`).

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

   Set `CARTLOG_ANTHROPIC_API_KEY` in `.env.secret`.

3. Start the web server and worker:

   ```bash
   uv run cartlog serve
   ```

   The web UI is at [http://localhost:8000](http://localhost:8000). Pass `--host`, `--port`, or `--workers` to change how it runs.

## Command-line usage

The web UI covers everyday use, but every action is also available from the `cartlog` command. With Docker, run these inside the container, for example `docker compose exec cartlog cartlog query category-spend`.

Ingest one or more receipts from the command line:

```bash
uv run cartlog ingest receipt1.jpg receipt2.pdf
```

By default each receipt is read right away. Pass `--no-wait` to hand them off and have cartlog read them in the background instead.

Query your stored data:

```bash
# Show a product's price over time
uv run cartlog query price-history "bananas"

# Compare a product's price across stores, cheapest first
uv run cartlog query store-comparison "whole milk"

# Total spend by category
uv run cartlog query category-spend

# Search line items by text, product, store, or category
uv run cartlog query search "coffee"
```

Other commands:

- `cartlog worker` reads any waiting receipts on its own, without starting the web server.
- `cartlog receipts delete` removes a receipt and everything stored with it, including its image.
- `cartlog db seed` sets up the categories cartlog uses, adding any that are missing.

Run `uv run cartlog --help` or add `--help` to any command for full options.

## Configuration

All settings are read from the environment and from `.env.secret`, with environment variables taking precedence. Every variable is prefixed `CARTLOG_`. Only `CARTLOG_ANTHROPIC_API_KEY` is required. See [.env.sample](.env.sample) for the full list with descriptions.

| Variable                              | Default            | Description                                                  |
| ------------------------------------- | ------------------ | ----------------------------------------------------------- |
| `CARTLOG_ANTHROPIC_API_KEY`           | (none, required)   | Your Anthropic API key, used to read receipts               |
| `CARTLOG_ANTHROPIC_MODEL`             | `claude-opus-4-8`  | Which Claude model reads your receipts                      |
| `CARTLOG_RECLASSIFY_MODEL`            | `claude-haiku-4-5` | A cheaper model used to tidy up item categories             |
| `CARTLOG_DATABASE_URL`                | `cartlog.db`       | Where to store your data file (the folder must exist)       |
| `CARTLOG_IMAGE_STORAGE_DIR`           | `receipt_images`   | Where to keep copies of your receipt images                 |
| `CARTLOG_REVIEW_CONFIDENCE_THRESHOLD` | `0.7`              | Receipts cartlog is unsure about are flagged for you to review |

For `CARTLOG_DATABASE_URL`, give a plain path to where you want the data file, such as `cartlog.db` or `/app/data.db`. cartlog checks the folder exists when it starts and handles the rest, so most people never need to change the default.

## Development

This project uses uv for packaging, `ruff` for linting and formatting, and `ty` for type checking. The [duty](https://pawamoy.github.io/duty/) task runner wraps the common workflows.

```bash
uv sync            # install dependencies
uv run duty dev    # serve in development mode (templates and CSS reload on change)
uv run duty lint   # run ruff, ty, typos, and pre-commit hooks
uv run duty test   # run the test suite
```

The web UI is styled with Tailwind CSS v4 and daisyUI v5. `uv run cartlog serve` compiles the stylesheet on startup, so you rarely need to build it by hand, but `npm run build:css` produces it directly.

## License

Released under the [MIT License](LICENSE).
