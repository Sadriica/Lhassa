.PHONY: install install-health install-heartbeat install-privileged dashboard run dev check test lint clean

# First-time setup: install Python deps + build the dashboard
install:
	uv sync
	cd dashboard && npm install && npm run build

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

# Install the scoped NOPASSWD sudoers drop-in so lhassa can run admin commands as
# root (allowlist only). Needs sudo — you'll be prompted for your password.
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

# Run the test suite
test:
	uv run --extra dev python -m pytest

# Lint with ruff
lint:
	uv run --extra dev python -m ruff check .

clean:
	find . -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null || true
	rm -rf dashboard/dist
