#!/usr/bin/env bash
set -euo pipefail

# Bootstrap a disposable, SSH-first Wan2Lab CLI environment on a RunPod Pod.
# Run inside a CUDA/PyTorch Pod whose persistent volume is mounted at /workspace.

WORKSPACE_ROOT="${WAN2LAB_WORKSPACE_ROOT:-/workspace}"
WAN2LAB_REF="${WAN2LAB_REF:?Set WAN2LAB_REF to an immutable wan2lab commit SHA.}"
K2CORE_REF="${K2CORE_REF:-a82b0b32a891e19eac5c5f6e35f8a9bfb715f9dc}"
COMFYUI_REF="${COMFYUI_REF:-285a98944c397a4a81f15ac63d69fa3dbc0a27b9}"
WAN_WRAPPER_REF="${WAN_WRAPPER_REF:-088128b224242e110d3906c6750e9a3a348a659b}"
VHS_REF="${VHS_REF:-4ee72c065db22c9d96c2427954dc69e7b908444b}"

WAN2LAB_REPO_URL="${WAN2LAB_REPO_URL:-https://github.com/soomrenald/wan2lab.git}"
K2CORE_REPO_URL="${K2CORE_REPO_URL:-https://github.com/soomrenald/k2core.git}"
COMFYUI_REPO_URL="${COMFYUI_REPO_URL:-https://github.com/comfyanonymous/ComfyUI.git}"
WAN_WRAPPER_REPO_URL="${WAN_WRAPPER_REPO_URL:-https://github.com/kijai/ComfyUI-WanVideoWrapper.git}"
VHS_REPO_URL="${VHS_REPO_URL:-https://github.com/Kosinkadink/ComfyUI-VideoHelperSuite.git}"

log() {
  printf '[wan2lab-bootstrap] %s\n' "$*"
}

require_root() {
  if [[ "$(id -u)" -ne 0 ]]; then
    printf 'This bootstrap must run as root inside the RunPod container.\n' >&2
    exit 2
  fi
}

install_system_dependencies() {
  local missing=()
  local command_name
  for command_name in curl ffmpeg git tmux; do
    if ! command -v "${command_name}" >/dev/null 2>&1; then
      missing+=("${command_name}")
    fi
  done
  if ((${#missing[@]} == 0)); then
    return
  fi
  log "Installing missing system tools: ${missing[*]}"
  export DEBIAN_FRONTEND=noninteractive
  apt-get update
  apt-get install --no-install-recommends -y ca-certificates curl ffmpeg git tmux
}

require_python_312() {
  local python_bin
  python_bin="${WAN2LAB_PYTHON:-python3}"
  if ! command -v "${python_bin}" >/dev/null 2>&1; then
    printf 'Python command not found: %s\n' "${python_bin}" >&2
    exit 2
  fi
  "${python_bin}" - <<'PY'
import sys

if sys.version_info[:2] != (3, 12):
    raise SystemExit(
        f"Wan2Lab requires Python 3.12; found {sys.version.split()[0]}"
    )
PY
}

sync_repo() {
  local name="$1"
  local url="$2"
  local revision="$3"
  local destination="$4"
  local fresh_clone=false

  if [[ -e "${destination}" && ! -d "${destination}/.git" ]]; then
    printf '%s exists but is not a Git checkout: %s\n' "${name}" "${destination}" >&2
    exit 2
  fi
  if [[ ! -d "${destination}/.git" ]]; then
    log "Cloning ${name}"
    git clone --filter=blob:none --no-checkout "${url}" "${destination}"
    fresh_clone=true
  fi
  if [[ "${fresh_clone}" != true &&
    -n "$(git -C "${destination}" status --porcelain --untracked-files=no)" ]]; then
    printf 'Refusing to replace modified tracked files in %s\n' "${destination}" >&2
    exit 2
  fi
  log "Pinning ${name} to ${revision}"
  git -C "${destination}" fetch --depth 1 origin "${revision}"
  git -C "${destination}" checkout --detach --force FETCH_HEAD
  git -C "${destination}" submodule update --init --recursive
}

install_python_dependencies() {
  local python_bin="${WAN2LAB_PYTHON:-python3}"
  local venv_dir="${WORKSPACE_ROOT}/wan2lab-venv"
  if [[ ! -x "${venv_dir}/bin/python" ]]; then
    log "Creating Python environment at ${venv_dir}"
    "${python_bin}" -m venv --system-site-packages "${venv_dir}"
  fi
  "${venv_dir}/bin/python" -m pip install --upgrade pip wheel
  "${venv_dir}/bin/python" -m pip install \
    -r "${WORKSPACE_ROOT}/ComfyUI/requirements.txt" \
    -r "${WORKSPACE_ROOT}/ComfyUI/custom_nodes/ComfyUI-WanVideoWrapper/requirements.txt" \
    -r "${WORKSPACE_ROOT}/ComfyUI/custom_nodes/ComfyUI-VideoHelperSuite/requirements.txt"
  "${venv_dir}/bin/python" -m pip install -e "${WORKSPACE_ROOT}/k2core"
  "${venv_dir}/bin/python" -m pip install -e \
    "${WORKSPACE_ROOT}/wan2lab/packages/wan2core"
}

write_runtime_record() {
  local record_dir="${WORKSPACE_ROOT}/wan2lab-runtime"
  mkdir -p "${record_dir}"
  {
    printf 'created_utc=%s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)"
    printf 'wan2lab=%s\n' "$(git -C "${WORKSPACE_ROOT}/wan2lab" rev-parse HEAD)"
    printf 'k2core=%s\n' "$(git -C "${WORKSPACE_ROOT}/k2core" rev-parse HEAD)"
    printf 'comfyui=%s\n' "$(git -C "${WORKSPACE_ROOT}/ComfyUI" rev-parse HEAD)"
    printf 'wan_wrapper=%s\n' "$(
      git -C "${WORKSPACE_ROOT}/ComfyUI/custom_nodes/ComfyUI-WanVideoWrapper" rev-parse HEAD
    )"
    printf 'video_helper_suite=%s\n' "$(
      git -C "${WORKSPACE_ROOT}/ComfyUI/custom_nodes/ComfyUI-VideoHelperSuite" rev-parse HEAD
    )"
    printf 'python=%s\n' "$(
      "${WORKSPACE_ROOT}/wan2lab-venv/bin/python" -c \
        'import platform; print(platform.python_version())'
    )"
    printf 'torch=%s\n' "$(
      "${WORKSPACE_ROOT}/wan2lab-venv/bin/python" -c \
        'import torch; print(torch.__version__)'
    )"
  } >"${record_dir}/versions.env"
}

main() {
  require_root
  install_system_dependencies
  require_python_312
  if ! command -v nvidia-smi >/dev/null 2>&1; then
    printf 'nvidia-smi is unavailable; use a CUDA-enabled RunPod image.\n' >&2
    exit 2
  fi

  mkdir -p "${WORKSPACE_ROOT}"
  sync_repo wan2lab "${WAN2LAB_REPO_URL}" "${WAN2LAB_REF}" \
    "${WORKSPACE_ROOT}/wan2lab"
  sync_repo k2core "${K2CORE_REPO_URL}" "${K2CORE_REF}" \
    "${WORKSPACE_ROOT}/k2core"
  sync_repo ComfyUI "${COMFYUI_REPO_URL}" "${COMFYUI_REF}" \
    "${WORKSPACE_ROOT}/ComfyUI"

  mkdir -p "${WORKSPACE_ROOT}/ComfyUI/custom_nodes"
  sync_repo ComfyUI-WanVideoWrapper "${WAN_WRAPPER_REPO_URL}" \
    "${WAN_WRAPPER_REF}" \
    "${WORKSPACE_ROOT}/ComfyUI/custom_nodes/ComfyUI-WanVideoWrapper"
  sync_repo ComfyUI-VideoHelperSuite "${VHS_REPO_URL}" "${VHS_REF}" \
    "${WORKSPACE_ROOT}/ComfyUI/custom_nodes/ComfyUI-VideoHelperSuite"

  mkdir -p \
    "${WORKSPACE_ROOT}/ComfyUI/input/wan2lab/remote" \
    "${WORKSPACE_ROOT}/ComfyUI/output/wan2lab/remote" \
    "${WORKSPACE_ROOT}/ComfyUI/models/diffusion_models" \
    "${WORKSPACE_ROOT}/ComfyUI/models/text_encoders" \
    "${WORKSPACE_ROOT}/ComfyUI/models/vae" \
    "${WORKSPACE_ROOT}/wan2lab-runtime/logs"

  install_python_dependencies
  write_runtime_record

  log "Bootstrap complete. Run the verifier next:"
  printf '  %s/wan2lab-venv/bin/python %s/wan2lab/scripts/runpod/verify_cli_lab.py\n' \
    "${WORKSPACE_ROOT}" "${WORKSPACE_ROOT}"
}

if [[ "${BASH_SOURCE[0]}" == "$0" ]]; then
  main "$@"
fi
