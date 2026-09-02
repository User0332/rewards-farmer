#!/usr/bin/env bash
# Microsoft Rewards Farmer — headless daily run.
# Cron entry (crontab -e): 30 8 * * * cd /home/ellioth/microsoft-rewards/rewards-farmer && ./run.sh >> /home/ellioth/logs/rewards.log 2>&1
set -euo pipefail
cd "$(dirname "$0")"
export PYTHONPATH=src

# --- Pre-flight: make sure the Ollama service is reachable (LLM-assisted queries) ---
OLLAMA_URL="${OLLAMA_HOST:-http://localhost:11434}"
if ! curl -fsS --max-time 5 "${OLLAMA_URL}/api/tags" >/dev/null 2>&1; then
	echo "[INFO] Ollama not responding at ${OLLAMA_URL} — attempting to start user service..."
	# systemctl --user needs these under cron (no session env there)
	export XDG_RUNTIME_DIR="${XDG_RUNTIME_DIR:-/run/user/$(id -u)}"
	export DBUS_SESSION_BUS_ADDRESS="${DBUS_SESSION_BUS_ADDRESS:-unix:path=${XDG_RUNTIME_DIR}/bus}"
	systemctl --user start ollama.service 2>/dev/null \
		|| sudo -n systemctl start ollama.service 2>/dev/null \
		|| true
	for _ in $(seq 1 15); do
		if curl -fsS --max-time 2 "${OLLAMA_URL}/api/tags" >/dev/null 2>&1; then break; fi
		sleep 2
	done
fi
if curl -fsS --max-time 5 "${OLLAMA_URL}/api/tags" >/dev/null 2>&1; then
	if ! curl -fsS --max-time 5 "${OLLAMA_URL}/api/tags" 2>/dev/null | grep -q 'llama3.2'; then
		echo "[WARNING] Ollama is up but model llama3.2 is missing — run: ollama pull llama3.2"
	fi
	echo "[INFO] Ollama pre-flight OK (${OLLAMA_URL})"
else
	echo "[WARNING] Ollama still unreachable — farmer continues without LLM-assisted queries."
fi

exec .venv/bin/python src/main.py --headless
