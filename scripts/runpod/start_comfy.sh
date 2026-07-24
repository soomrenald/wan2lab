#!/usr/bin/env bash
set -euo pipefail

WORKSPACE_ROOT="${WAN2LAB_WORKSPACE_ROOT:-/workspace}"
COMFY_ROOT="${WORKSPACE_ROOT}/ComfyUI"
PYTHON_BIN="${WORKSPACE_ROOT}/wan2lab-venv/bin/python"
SESSION_NAME="${WAN2LAB_COMFY_SESSION:-wan2lab-comfy}"
LOG_DIR="${WORKSPACE_ROOT}/wan2lab-runtime/logs"
PORT="${WAN2LAB_COMFY_PORT:-8188}"

if [[ ! -x "${PYTHON_BIN}" || ! -d "${COMFY_ROOT}" ]]; then
  printf 'Remote environment is incomplete; run bootstrap_cli_lab.sh first.\n' >&2
  exit 2
fi
if tmux has-session -t "${SESSION_NAME}" 2>/dev/null; then
  printf 'ComfyUI session already exists: %s\n' "${SESSION_NAME}"
  exit 0
fi

mkdir -p "${LOG_DIR}"
LOG_FILE="${LOG_DIR}/comfy-$(date -u +%Y%m%dT%H%M%SZ).log"
tmux new-session -d -s "${SESSION_NAME}" \
  "cd '${COMFY_ROOT}' && exec '${PYTHON_BIN}' main.py \
  --listen 127.0.0.1 --port '${PORT}' 2>&1 | tee '${LOG_FILE}'"

printf 'Started ComfyUI in tmux session %s (log: %s).\n' \
  "${SESSION_NAME}" "${LOG_FILE}"
printf 'From your workstation, tunnel it with:\n'
printf '  ssh -L %s:127.0.0.1:%s <runpod-ssh-target>\n' "${PORT}" "${PORT}"
