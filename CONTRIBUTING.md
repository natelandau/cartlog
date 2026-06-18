# Contributing to cartlog

Thanks for your interest in improving cartlog. This guide covers how to set up a
development environment, the checks your changes must pass, and how to format
commits and pull requests so they're accepted without back-and-forth.

## Prerequisites

You need:

- Python 3.14 or newer
- [uv](https://docs.astral.sh/uv/) for Python dependencies and tooling
- Node.js and npm for the web UI's Tailwind CSS build
- An [Anthropic API key](https://console.anthropic.com/) if you want to run the
  receipt parser locally

## Set up your environment

1. Fork the repository and clone your fork:

   ```bash
   git clone https://github.com/YOUR_USERNAME/cartlog.git
   cd cartlog
   ```

2. Install the Python and frontend dependencies:

   ```bash
   uv sync
   npm install
   ```

3. Create your local config and add your API key:

   ```bash
   cp .env.sample .env.secret
   ```

   Open `.env.secret` and set your LLM provider's API key (e.g. `ANTHROPIC_API_KEY`). The file
   is gitignored, so your key never gets committed.

4. Confirm the setup works by running the test suite:

   ```bash
   uv run duty test
   ```

## Run the app while you work

Start the server in development mode, which reloads templates and rebuilds CSS
when you edit them:

```bash
uv run duty dev
```

The web UI is at [http://localhost:8000](http://localhost:8000). For the full
list of CLI commands, run `uv run cartlog --help`.

## Build the Docker image

End users run the prebuilt image from the GitHub Container Registry
(`ghcr.io/natelandau/cartlog`), so you don't need Docker for day-to-day work, as
`uv run duty dev` is faster. When you change the `Dockerfile`, `compose.yaml`, or
anything that affects the container, build and run the image from source to check
it:

```bash
docker compose up --build
```

The `build: .` line in `compose.yaml` makes this build the image locally instead
of pulling the published one, then starts it with the same configuration end
users get. The CI "Test Docker Build" workflow runs the same build (multi-arch)
plus a few smoke tests on every change to those files.

## Check your changes

Run the linters and tests before you push. The same checks run in CI, so
running them locally first saves a round trip.

```bash
uv run duty lint   # ruff, ty, typos, and the pre-commit hooks
uv run duty test   # the pytest suite
```

`uv run duty lint` runs the formatter, the [ruff](https://docs.astral.sh/ruff/)
linter, the [ty](https://github.com/astral-sh/ty) type checker, and a spell
check. Fix anything it reports. The project treats type errors and lint
warnings as failures, so a clean run is required before your change can merge.

## Commit your work

cartlog uses [Conventional Commits](https://www.conventionalcommits.org/). A
pre-commit hook and a CI check both validate the format, and your pull request
title is checked too. A commit header looks like this:

```
<type>(<scope>): <subject>
```

The type must be one of: `build`, `ci`, `docs`, `feat`, `fix`, `perf`,
`refactor`, `style`, or `test`. There is no `chore` type.

Follow these rules for the subject:

- Use the imperative mood: "add price chart", not "added price chart"
- Don't capitalize the first letter
- Don't end with a period
- Keep the whole header to 70 characters or fewer

The scope is optional. When you include one, name the area you changed, such as
`web`, `db`, `ingest`, or `parsing`. For example:

```
feat(web): add a price-history chart to the product page
fix(ingest): retry a stalled parse job instead of failing it
docs: clarify the database path setting
```

Use the commit body to explain why you made the change, not what the diff
already shows.

### A note on the pre-commit hooks

The hooks type-check and test the whole project on every commit. If you're
making a series of work-in-progress commits, you can skip the hooks with
`git commit --no-verify`. Before the final commit of your change, run the full
suite across all files and get it green:

```bash
uv run prek run --all-files
```

## Open a pull request

1. Push your branch to your fork and open a pull request against `main`.
2. Give the pull request a title that follows the same Conventional Commits
   format as a commit header. The CI title check rejects anything else.
3. Describe what the change does and why. Link any related issue.
4. Make sure the CI checks pass. They run the tests, the linters, and a Docker
   build smoke test.

A maintainer reviews open pull requests and merges them once the checks pass
and the change looks good. Thanks for contributing.
