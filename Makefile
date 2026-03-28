.PHONY: install dashboard run dev clean

# First-time setup: install Python deps + build the dashboard
install:
	uv sync
	cd dashboard && npm install && npm run build

# Rebuild the React dashboard (run after editing dashboard/src/)
dashboard:
	cd dashboard && npm run build

# Start Potts (voice + API server + scheduler)
run:
	uv run potts

# Start with debug mode on (shows live STT transcriptions)
dev:
	POTTS_DEBUG=1 POTTS_LOG_LEVEL=DEBUG uv run potts

# Dashboard hot-reload dev server (proxies API to :7860)
dashboard-dev:
	cd dashboard && npm run dev

clean:
	find . -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null || true
	rm -rf dashboard/dist
