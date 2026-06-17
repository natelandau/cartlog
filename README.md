[![Automated Tests](https://github.com/natelandau/cartlog/actions/workflows/automated-tests.yml/badge.svg)](https://github.com/natelandau/cartlog/actions/workflows/automated-tests.yml) [![codecov](https://codecov.io/gh/natelandau/cartlog/graph/badge.svg?token=QdFXvrhoP5)](https://codecov.io/gh/natelandau/cartlog)

# cartlog

Scan your receipts, let cartlog read them for you, and track what you buy and what it costs over time.

Drop a photo or PDF of a receipt into cartlog and it picks out the store, date, and every item, then sorts each one into a category. Once your receipts are saved, you can chart a product's price history, compare prices across stores, and see where your money goes by category, all from your browser or the command line.

Plenty of apps track all of your spending. cartlog is built for one job instead: following the cost of the things you buy again and again, like groceries.

## Features

cartlog takes a receipt photo and gives you back spending you can search and chart:

- Turn a photo or PDF of a receipt into itemized data, read for you by your chosen LLM provider
- Sort every item into a category automatically, and recheck anything it could not place
- Upload receipts, fix anything that needs a second look, and browse spending charts in your browser
- Keep using the app while your receipts are read in the background
- See a product's price history, compare prices between stores, and total your spending by category
- Keep all your data in a single file, with no separate database to install or maintain

## Requirements

You need an API key for your chosen LLM provider (Anthropic by default) and one of two ways to run cartlog:

- An API key for your LLM provider - [Anthropic](https://console.anthropic.com/) by default (required to parse receipts)
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

    Open `.env.secret` and set the API key for your chosen provider (e.g. `ANTHROPIC_API_KEY`). Optionally set `CARTLOG_PARSE_MODEL` / `CARTLOG_CLASSIFY_MODEL` to switch providers. Every other value is optional.

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

    Set the API key for your chosen provider (e.g. `ANTHROPIC_API_KEY`) in `.env.secret`. Optionally set `CARTLOG_PARSE_MODEL` / `CARTLOG_CLASSIFY_MODEL` to point at a different provider or model.

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

All settings are read from the environment and from `.env.secret`, with environment variables taking precedence. Model-selection variables are prefixed `CARTLOG_`; provider credentials use each provider's own native variable name. Only the chosen provider's API key env var is required. See [.env.sample](.env.sample) for the full list with descriptions.

cartlog is provider-agnostic. For how to point it at Anthropic, OpenAI, Gemini, an API router like OpenRouter, or a local model, see [Choosing an LLM provider](#choosing-an-llm-provider).

| Variable                                     | Default                      | Description                                                    |
| -------------------------------------------- | ---------------------------- | -------------------------------------------------------------- |
| `ANTHROPIC_API_KEY` (or provider equivalent) | (none, required)             | API key for your chosen provider; read by Pydantic AI          |
| `CARTLOG_PARSE_MODEL`                        | `anthropic:claude-opus-4-8`  | Provider-prefixed model that reads your receipts               |
| `CARTLOG_CLASSIFY_MODEL`                     | `anthropic:claude-haiku-4-5` | Cheaper provider-prefixed model that tidies item categories    |
| `CARTLOG_DATABASE_URL`                       | `cartlog.db`                 | Where to store your data file (the folder must exist)          |
| `CARTLOG_IMAGE_STORAGE_DIR`                  | `receipt_images`             | Where to keep copies of your receipt images                    |
| `CARTLOG_REVIEW_CONFIDENCE_THRESHOLD`        | `0.7`                        | Receipts cartlog is unsure about are flagged for you to review |

For `CARTLOG_DATABASE_URL`, give a plain path to where you want the data file, such as `cartlog.db` or `/app/data.db`. cartlog checks the folder exists when it starts and handles the rest, so most people never need to change the default.

## Choosing an LLM provider

cartlog reads receipts through [Pydantic AI](https://ai.pydantic.dev/), so you can switch providers without touching code. It ships with support for Anthropic, OpenAI, and Google Gemini, plus any OpenAI-compatible endpoint, which covers API routers like [OpenRouter](https://openrouter.ai/) and local servers like [Ollama](https://ollama.com/). Switching takes two steps: set the model with `CARTLOG_PARSE_MODEL` and `CARTLOG_CLASSIFY_MODEL`, then supply that provider's API key under its own variable name.

Model values use a `provider:model` format. Set both variables in `.env.secret`, alongside the matching key:

```bash
# Anthropic (the default)
ANTHROPIC_API_KEY=sk-ant-...
CARTLOG_PARSE_MODEL=anthropic:claude-opus-4-8
CARTLOG_CLASSIFY_MODEL=anthropic:claude-haiku-4-5

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

`CARTLOG_PARSE_MODEL` reads the receipt image or PDF and does the heavy lifting. `CARTLOG_CLASSIFY_MODEL` only sorts product names into categories, so a smaller, cheaper model fits well there (the Anthropic default pairs Opus for parsing with Haiku for classifying). The two variables can use different providers. For exact model-id syntax, see the [Pydantic AI models documentation](https://ai.pydantic.dev/models/).

Local and self-hosted models work through that same OpenAI-compatible path. They are held to the same capability requirements below, which many small local models do not meet. To use a provider cartlog does not bundle (for example Cohere or Bedrock), add its [Pydantic AI extra](https://ai.pydantic.dev/models/) to the install and rebuild.

> **Important:** The parse model must support image (vision) input and structured output, because cartlog hands it the receipt picture and asks for a typed result. To read PDF receipts, the model must also accept PDF documents. The classify model needs structured output only, since it works from text. Point either variable at a model that lacks these capabilities and ingestion fails: the parse step errors and the receipt is flagged for review. Current frontier models from Anthropic, OpenAI, and Google meet all three requirements; many smaller and local models do not. Test one receipt before switching your whole setup.

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
