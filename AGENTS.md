# Repository Guidelines

# Internalize this
Before touching code in this repository, a coder/quant analyst/quant developer should internalize these operational constraints. The runtime entrypoint is main_live.py, and all live behavior derives from env + YAML merge. Read configs/strategy_config.yaml before changing risk, timing, or instrument values; careless edits can silently alter leverage assumptions and order cadence. Strategy logic, execution flow, and dry-run guard are in strategy/deepseek_strategy.py; this is the primary file for signal-to-order behavior, including warmup, timer analysis loop, and bracket construction. LLM prompt construction and parse/validation are in utils/deepseek_client.py; any schema drift here can break trading decisions or degrade to fallback logic. Indicator computation and readiness assumptions are in indicators/technical_manager.py, so backtest/live parity depends on consistent bar formatting and initialization windows. For operational workflow, align with AGENTS.md, quick boot docs in QUICKSTART.md, and test philosophy in docs/TESTING_GUIDE.md. Use run_quick_test.py and quick_test_main.py for rapid verification loops, but treat them as execution smoke checks, not statistical validation. Finally, never claim strategy quality without reproducible backtests and explicit cost/slippage assumptions; dry-run only proves integration correctness, not edge.

## Project Structure & Module Organization
`main_live.py` orchestrates Binance adapters, environment loading, and the Nautilus `TradingNode` that runs `DeepSeekAIStrategy`. Strategy logic sits in `strategy/deepseek_strategy.py`, indicator math in `indicators/technical_manager.py`, integrations (DeepSeek, sentiment, Telegram) in `utils/`, and durable configs in `configs/` (`strategy_config.yaml` for defaults, `.env` for secrets). Operational scripts (`start_trader.sh`, `restart_trader.sh`, `check_strategy_status.sh`, `run_quick_test.py`) live at the repo root, while runtime artifacts are written to `logs/` and can be tailed per session.

## Build, Test, and Development Commands
- `python3.10 -m venv venv && source venv/bin/activate` – provision the supported interpreter.
- `pip install -r requirements.txt` – install NautilusTrader, Binance adapters, and tooling; rerun when requirements change.
- `python main_live.py` – start the production profile using `.env` overrides; monitor with `tail -f logs/trader*.log`.
- `python run_quick_test.py` – launch the 1-minute quick test harness with reduced indicator periods for fast validation loops.
- `./start_trader.sh | ./restart_trader.sh | ./stop_trader.sh` – manage background sessions with PID bookkeeping instead of manual nohup handling.

## Coding Style & Naming Conventions
Use PEP 8, 4-space indentation, liberal type hints, and descriptive docstrings similar to `main_live.py`. Modules/functions stay `snake_case`, classes `PascalCase`, config keys `UPPER_SNAKE_CASE`, and YAML keys `lower_snake_case`. Keep shared helpers in `utils/` so imports remain aligned with `pyrightconfig.json`; run `pyright` (basic mode) before review to catch interface drift.

## Testing Guidelines
Exercise every behavior change in quick-test mode (`python quick_test_main.py` or `python run_quick_test.py`) and confirm logs show clean startup plus at least one signal cycle. Messaging work requires `python test_telegram.py <bot_token> <chat_id>` to validate send capability and `python test_command_listener.py` to prove inbound command handling. When editing risk sizing or indicator math, capture indicator dumps or equity deltas inside the PR so reviewers can gauge coverage.

## Commit & Pull Request Guidelines
Branch off `main` using `feature/<topic>` or `fix/<issue>` as documented in `GIT_WORKFLOW.md`. Commits follow `<type>: <summary>` (feat, fix, docs, refactor, etc.) and include a short body outlining rationale and `Testing: …`. PRs must summarize the change, list config/env deltas, attach representative log snippets, and link issues via `Fixes #id`. Avoid force pushes mid-review; instead add follow-up commits or coordinate with reviewers.

## Security & Configuration Tips
Never commit secrets—`.env` stays local with `chmod 600`. Document any new environment variable or YAML knob in README/QUICKSTART, and keep sanitized defaults under `configs/` so operators can reproduce your setup. Rotate Binance, DeepSeek, and Telegram credentials immediately if they leak through logs or screenshots.
