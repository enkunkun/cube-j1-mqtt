# cube-j1-mqtt Development Guidelines

Auto-generated from all feature plans. Last updated: 2026-06-19

## Active Technologies

- Python 2.7（Cube J1 ターゲット）、テストは Python 3.11+ + Python 2.7 stdlib のみ（`logging`, `logging.handlers`, `json`, `socket`, `struct`, `termios`, `select`, `threading`, `time`）。テスト側は `pytest`（host のみ） (001-bridge-observability)

## Project Structure

```text
src/
tests/
```

## Commands

cd src && pytest && ruff check .

## Code Style

Python 2.7（Cube J1 ターゲット）、テストは Python 3.11+: Follow standard conventions

## Recent Changes

- 001-bridge-observability: Added Python 2.7（Cube J1 ターゲット）、テストは Python 3.11+ + Python 2.7 stdlib のみ（`logging`, `logging.handlers`, `json`, `socket`, `struct`, `termios`, `select`, `threading`, `time`）。テスト側は `pytest`（host のみ）

<!-- MANUAL ADDITIONS START -->
<!-- MANUAL ADDITIONS END -->
