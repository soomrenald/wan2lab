#!/usr/bin/env bash
set -euo pipefail

# Download only the first approved Wan family. Each artifact is fetched from an
# immutable Hugging Face revision and accepted only after byte/hash validation.

WORKSPACE_ROOT="${WAN2LAB_WORKSPACE_ROOT:-/workspace}"
COMFY_ROOT="${WORKSPACE_ROOT}/ComfyUI"

log() {
  printf '[wan2lab-models] %s\n' "$*"
}

download_model() {
  local url="$1"
  local destination="$2"
  local expected_bytes="$3"
  local expected_sha256="$4"
  local actual_bytes
  local actual_sha256
  local auth_args=()

  mkdir -p "$(dirname "${destination}")"
  if [[ -n "${HF_TOKEN:-}" ]]; then
    auth_args=(--header "Authorization: Bearer ${HF_TOKEN}")
  fi

  if [[ -f "${destination}" ]]; then
    actual_bytes="$(stat --format='%s' "${destination}")"
    actual_sha256="$(sha256sum "${destination}" | cut -d ' ' -f 1)"
    if [[ "${actual_bytes}" == "${expected_bytes}" &&
      "${actual_sha256}" == "${expected_sha256}" ]]; then
      log "Already verified: $(basename "${destination}")"
      return
    fi
    printf 'Existing artifact failed validation; refusing to overwrite: %s\n' \
      "${destination}" >&2
    exit 2
  fi

  log "Downloading $(basename "${destination}")"
  curl --fail --location --retry 5 --retry-all-errors \
    --continue-at - "${auth_args[@]}" --output "${destination}.partial" "${url}"
  actual_bytes="$(stat --format='%s' "${destination}.partial")"
  actual_sha256="$(sha256sum "${destination}.partial" | cut -d ' ' -f 1)"
  if [[ "${actual_bytes}" != "${expected_bytes}" ]]; then
    printf 'Byte count mismatch for %s: expected %s, got %s\n' \
      "${destination}" "${expected_bytes}" "${actual_bytes}" >&2
    exit 2
  fi
  if [[ "${actual_sha256}" != "${expected_sha256}" ]]; then
    printf 'SHA-256 mismatch for %s: expected %s, got %s\n' \
      "${destination}" "${expected_sha256}" "${actual_sha256}" >&2
    exit 2
  fi
  mv "${destination}.partial" "${destination}"
  log "Verified ${destination}"
}

main() {
  if [[ ! -d "${COMFY_ROOT}" ]]; then
    printf 'ComfyUI is not installed at %s; run bootstrap_cli_lab.sh first.\n' \
      "${COMFY_ROOT}" >&2
    exit 2
  fi

  download_model \
    "https://huggingface.co/Kijai/WanVideo_comfy_fp8_scaled/resolve/033a4e487f60220b3d6e469599a6aebc46e13cee/TI2V/Wan2_2-TI2V-5B_fp8_e4m3fn_scaled_KJ.safetensors" \
    "${COMFY_ROOT}/models/diffusion_models/Wan2_2-TI2V-5B_fp8_e4m3fn_scaled_KJ.safetensors" \
    5277255650 \
    a83f54a2450d5471e5721e59ab556afa2d8793e30280713e3796b254c5286b48
  download_model \
    "https://huggingface.co/Kijai/WanVideo_comfy/resolve/8260d429d19fd7a72304cad059160b95d843913f/Wan2_2_VAE_bf16.safetensors" \
    "${COMFY_ROOT}/models/vae/Wan2_2_VAE_bf16.safetensors" \
    1409401152 \
    0e913a2ca571c75fcb63385a8edadcca73454af5842596cb1ad11e4142590996
  download_model \
    "https://huggingface.co/Kijai/WanVideo_comfy/resolve/8260d429d19fd7a72304cad059160b95d843913f/umt5-xxl-enc-fp8_e4m3fn.safetensors" \
    "${COMFY_ROOT}/models/text_encoders/umt5-xxl-enc-fp8_e4m3fn.safetensors" \
    6731333792 \
    3fe5173588270c22505d4f9158bb1644b78331b8614206a97e92760b960c9ffa
}

main "$@"
