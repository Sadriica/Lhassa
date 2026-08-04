.PHONY: install quickstart install-health install-heartbeat install-telegram install-monitor install-privileged dashboard run dev check test test-integration cov lint clean

# First-time setup: install Python deps + build the dashboard.
# Installs the `voice` extra (pipecat/torch) — this is the laptop dev workflow
# (`make run`/`make dev` run the voice pipeline via main.py). Headless deploys
# (Docker/VPS) intentionally skip it — see doc/portability.md.
install:
	uv sync --extra voice
	cd dashboard && npm install && npm run build

# The 2-minute on-ramp: uv sync (no dashboard, no voice extra), ensure .env,
# detect a model backend (API key or Ollama), and print the next commands.
# This is the LIGHT path — `bash scripts/install.sh` is the always-on one.
quickstart:
	bash scripts/quickstart.sh

# Install + enable the LLM-free PC-state health timer (user unit, no sudo)
install-health:
	mkdir -p $(HOME)/.config/systemd/user
	cp scripts/systemd/lhassa-health.service scripts/systemd/lhassa-health.timer $(HOME)/.config/systemd/user/
	systemctl --user daemon-reload
	systemctl --user enable --now lhassa-health.timer
	@echo "Health checks scheduled. Status: systemctl --user status lhassa-health.timer"

# Install + enable the brain heartbeat timer (user unit, no sudo). Each beat
# advances the brain one step() tick — deterministic, zero tokens.
install-heartbeat:
	mkdir -p $(HOME)/.config/systemd/user
	cp scripts/systemd/lhassa-heartbeat.service scripts/systemd/lhassa-heartbeat.timer $(HOME)/.config/systemd/user/
	systemctl --user daemon-reload
	systemctl --user enable --now lhassa-heartbeat.timer
	@echo "Heartbeat scheduled. Status: systemctl --user status lhassa-heartbeat.timer"

# Install + enable the Telegram inbound poller (user unit, no sudo). Credential-
# gated: set LHASSA_TELEGRAM_BOT_TOKEN + LHASSA_TELEGRAM_CHAT_ID in .env first, or
# it logs once and exits without touching the network.
install-telegram:
	mkdir -p $(HOME)/.config/systemd/user
	cp scripts/systemd/lhassa-telegram.service $(HOME)/.config/systemd/user/
	systemctl --user daemon-reload
	systemctl --user enable --now lhassa-telegram.service
	@echo "Telegram poller enabled. Status: systemctl --user status lhassa-telegram.service"

# Install + enable the always-on $0 monitor watcher (user unit, no sudo). The unit
# ships LHASSA_MONITOR_ENABLED=1; idle ticks cost zero tokens.
install-monitor:
	mkdir -p $(HOME)/.config/systemd/user
	cp scripts/systemd/lhassa-monitor.service $(HOME)/.config/systemd/user/
	systemctl --user daemon-reload
	systemctl --user enable --now lhassa-monitor.service
	@echo "Monitor watcher enabled. Status: systemctl --user status lhassa-monitor.service"

# Install the scoped NOPASSWD sudoers drop-in so lhassa can run admin commands as
# root (allowlist only). Needs sudo — you'll be prompted for your password.
install-ollama:
	mkdir -p $(HOME)/.config/systemd/user
	cp scripts/systemd/ollama.service $(HOME)/.config/systemd/user/
	systemctl --user daemon-reload
	systemctl --user enable --now ollama.service
	@echo "Ollama server enabled. Pull the embedding model with: ollama pull nomic-embed-text"
	@echo "Then re-embed stored memory with: uv run lhassa memory reindex"

install-privileged:
	sudo bash scripts/install-privileged.sh

# Rebuild the React dashboard (run after editing dashboard/src/)
dashboard:
	cd dashboard && npm run build

# Start Lhassa (voice + API server + scheduler)
run:
	uv run python main.py

# Start with debug mode on (shows live STT transcriptions)
dev:
	LHASSA_DEBUG=1 LHASSA_LOG_LEVEL=DEBUG uv run python main.py

# Dashboard hot-reload dev server (proxies API to :7766)
dashboard-dev:
	cd dashboard && npm run dev

# Validate environment before running (no API calls)
check:
	uv run python scripts/check.py

# Run the test suite (dev + voice extras — the full suite covers the voice
# pipeline modules too; the VPS/headless image never needs them, see
# doc/portability.md).
test:
	uv run --extra dev --extra voice python -m pytest

# Real end-to-end integration tests (tests/test_integration_heartbeat.py):
# a live CLI subprocess (opencode/claude/codex, whichever is on PATH) and a
# real on-disk DB file, no provider/DB mocks. Excluded from `make test` by
# default (see pyproject.toml's addopts); run explicitly, and expect it to
# take longer and spend a little real token budget.
test-integration:
	uv run --extra dev python -m pytest -m integration -q

# Coverage baseline over the unit suite (respects pyproject's `-m "not
# integration"` addopts — same tests `make test` runs, just measured). A
# separate target on purpose: coverage instrumentation slows the run, so it
# never rides along with the fast default `make test`. HTML report in
# htmlcov/index.html.
cov:
	uv run --extra dev python -m pytest tests/ -q --cov=lhassa --cov-report=term-missing:skip-covered --cov-report=html

# Lint with ruff
lint:
	uv run --extra dev --extra voice python -m ruff check .

clean:
	find . -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null || true
	rm -rf dashboard/dist
