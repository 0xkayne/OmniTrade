---
status: current
authority: reference
owner: project maintainers
updated: 2026-09-29
applies_to: src/cli/
---

# CLI Package

Typer CLI application, bootstrap dependency wiring, and the programmatic Agent entry point.

The Typer command surface lives in `src/cli/main.py`. It is user-facing rather than a library API,
so its commands, arguments, output and exit codes are documented once in
[CLI Reference](../../../user-guide/cli/index.md) and are not duplicated here. This page indexes
the programmatic entry points that Python code — including an Agent SDK integration — calls
directly.

## Configuration loading

The CLI boundary binds each venue’s effective network to its credentials file before
constructing adapters. Shared notification credentials are loaded independently.

::: src.cli.config
    options:
      show_root_heading: false

## Bootstrap

::: src.cli.bootstrap
    options:
      show_root_heading: false

## Agent API

::: src.cli.agent_api
    options:
      show_root_heading: false
