---
status: current
authority: reference
owner: project maintainers
updated: 2026-09-10
applies_to: src/cli/
---

# CLI Package

Typer CLI application, bootstrap dependency wiring, and the programmatic Agent entry point.

The Typer command surface lives in `src/cli/main.py`. It is user-facing rather than a library API,
so its commands, arguments, output and exit codes are documented once in
[CLI Reference](../../../user-guide/cli/index.md) and are not duplicated here. This page indexes
the programmatic entry points that Python code — including an Agent SDK integration — calls
directly.

## Bootstrap

::: src.cli.bootstrap
    options:
      show_root_heading: false

## Agent API

::: src.cli.agent_api
    options:
      show_root_heading: false
