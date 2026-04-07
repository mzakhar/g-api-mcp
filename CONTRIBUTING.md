# Contributing

## Dev setup

```bash
git clone https://github.com/mzakhar/g-api-mcp.git
cd g-api-mcp
pip install -e ".[dev]"
```

You need a `client_secrets.json` in the project root (see [Setup in README](README.md#setup)) to run any tool that hits the Google APIs. Tests that don't require live credentials run without it.

## Running tests

```bash
pytest
```

## Project layout

```
src/g_api_mcp/
  server.py       # MCP server entry point, tool registration
  auth.py         # OAuth2 credential manager (keyring-backed)
  envelope.py     # Shared JSON response envelope builder
  sync.py         # tasks_sync_to_vault tool + CLI (optional vault feature)
auth_setup.py     # One-time OAuth2 consent flow
scripts/          # Helper scripts (Windows Task Scheduler registration)
tests/
```

## Making changes

- Keep tool handlers thin — business logic in separate functions
- Every tool response must go through `build_envelope()` or `error_envelope()`
- Add or update tests for any new tools or sync logic
- Run `pytest` before opening a PR

## Pull requests

- Use a descriptive title and fill in the PR template
- Link any related issues with `Closes #N`
- One logical change per PR

## Vault sync is optional

`tasks_sync_to_vault` and `g-api-mcp-sync` require a `sync-config.json` (see `sync-config.example.json`). All other tools work without it. Changes to the sync feature should not break the other 33 tools.
