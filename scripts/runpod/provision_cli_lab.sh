#!/usr/bin/env bash
set -euo pipefail

# Cost-bearing RunPod creation is intentionally a two-stage operation:
# preview by default, and create only with an explicit billing acknowledgement.

RUNPODCTL="${RUNPODCTL:-runpodctl}"
GPU_ID="NVIDIA GeForce RTX 5090"
POD_NAME="wan2lab-cli"
IMAGE="runpod/pytorch:1.0.3-cu1281-torch291-ubuntu2404"
CLOUD_TYPE="SECURE"
VOLUME_GIB=250
CONTAINER_DISK_GIB=40
STOP_HOURS=8
CREATE=false
ACKNOWLEDGE_BILLING=false

usage() {
  cat <<'EOF'
Usage: provision_cli_lab.sh [options]

Options:
  --gpu-id ID               Exact RunPod GPU ID (default: NVIDIA GeForce RTX 5090)
  --name NAME               Pod name (default: wan2lab-cli)
  --cloud-type TYPE         SECURE or COMMUNITY (default: SECURE)
  --volume-gib N            Persistent /workspace volume size (default: 250)
  --container-disk-gib N    Ephemeral container disk size (default: 40)
  --stop-hours N            Automatic stop interval (default: 8)
  --create                  Execute the printed reservation
  --acknowledge-billing     Confirm that live inventory/price was reviewed
  -h, --help                Show this help

Without --create, this command is a non-billing preview.
EOF
}

while (($#)); do
  case "$1" in
    --gpu-id)
      GPU_ID="$2"
      shift 2
      ;;
    --name)
      POD_NAME="$2"
      shift 2
      ;;
    --cloud-type)
      CLOUD_TYPE="$2"
      shift 2
      ;;
    --volume-gib)
      VOLUME_GIB="$2"
      shift 2
      ;;
    --container-disk-gib)
      CONTAINER_DISK_GIB="$2"
      shift 2
      ;;
    --stop-hours)
      STOP_HOURS="$2"
      shift 2
      ;;
    --create)
      CREATE=true
      shift
      ;;
    --acknowledge-billing)
      ACKNOWLEDGE_BILLING=true
      shift
      ;;
    -h | --help)
      usage
      exit 0
      ;;
    *)
      printf 'Unknown option: %s\n' "$1" >&2
      usage >&2
      exit 2
      ;;
  esac
done

if ! command -v "${RUNPODCTL}" >/dev/null 2>&1; then
  printf 'runpodctl is not installed or not on PATH: %s\n' "${RUNPODCTL}" >&2
  exit 2
fi
if [[ "${CLOUD_TYPE}" != "SECURE" && "${CLOUD_TYPE}" != "COMMUNITY" ]]; then
  printf 'Invalid cloud type: %s\n' "${CLOUD_TYPE}" >&2
  exit 2
fi
for integer in "${VOLUME_GIB}" "${CONTAINER_DISK_GIB}" "${STOP_HOURS}"; do
  if [[ ! "${integer}" =~ ^[1-9][0-9]*$ ]]; then
    printf 'Storage and timeout values must be positive integers.\n' >&2
    exit 2
  fi
done

STOP_AFTER="$(date -u -d "+${STOP_HOURS} hours" +%Y-%m-%dT%H:%M:%SZ)"
COMMAND=(
  "${RUNPODCTL}" pod create
  --name "${POD_NAME}"
  --image "${IMAGE}"
  --gpu-id "${GPU_ID}"
  --gpu-count 1
  --cloud-type "${CLOUD_TYPE}"
  --container-disk-in-gb "${CONTAINER_DISK_GIB}"
  --volume-in-gb "${VOLUME_GIB}"
  --volume-mount-path /workspace
  --ports "22/tcp"
  --ssh
  --docker-args "sleep infinity"
  --stop-after "${STOP_AFTER}"
)

printf 'Live GPU inventory (review availability and price before creating):\n'
"${RUNPODCTL}" gpu list --include-unavailable -o json
printf '\nReservation command:\n'
printf ' %q' "${COMMAND[@]}"
printf '\n\nAutomatic stop: %s\n' "${STOP_AFTER}"
printf 'Persistent Pod volume: %s GiB at /workspace\n' "${VOLUME_GIB}"

if [[ "${CREATE}" != true ]]; then
  printf 'Preview only; no Pod was created.\n'
  exit 0
fi
if [[ "${ACKNOWLEDGE_BILLING}" != true ]]; then
  printf 'Refusing cost-bearing creation without --acknowledge-billing.\n' >&2
  exit 2
fi

"${COMMAND[@]}"
