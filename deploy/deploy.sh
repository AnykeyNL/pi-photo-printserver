#!/usr/bin/env bash
# Sync this repo to a Raspberry Pi over SSH, install deps, and (re)start the
# user systemd service so it runs at boot.
#
# Usage:
#   REMOTE=pi@raspberrypi.local ./deploy/deploy.sh
#   REMOTE=myhost REMOTE_DIR=print-server ./deploy/deploy.sh
set -euo pipefail

REMOTE="${REMOTE:-${1:-}}"
REMOTE_DIR="${REMOTE_DIR:-print-server}"

if [[ -z "${REMOTE}" ]]; then
  echo "Usage: REMOTE=<user@host> $0   (or: $0 <user@host>)" >&2
  echo "Example: REMOTE=pi@raspberrypi.local $0" >&2
  exit 1
fi
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"

echo "Syncing ${ROOT} -> ${REMOTE}:~/${REMOTE_DIR}/"
rsync -avz --delete \
  --exclude '.venv' \
  --exclude 'data/uploads/*' \
  --exclude '__pycache__' \
  --exclude '.git' \
  "${ROOT}/" "${REMOTE}:~/${REMOTE_DIR}/"

echo "Installing Python deps and systemd user unit on ${REMOTE}..."
ssh "${REMOTE}" bash -s -- "${REMOTE_DIR}" <<'REMOTE_SCRIPT'
set -euo pipefail
DIR="$1"
cd ~/"${DIR}"

python3 -m venv .venv
.venv/bin/pip install -q -U pip
.venv/bin/pip install -q -r requirements.txt

mkdir -p data/uploads

UNIT_SRC=deploy/print-server.service
UNIT_DEST=~/.config/systemd/user/print-server.service
mkdir -p ~/.config/systemd/user
cp "${UNIT_SRC}" "${UNIT_DEST}"

systemctl --user daemon-reload
systemctl --user enable print-server.service
systemctl --user restart print-server.service

USER_NAME="$(whoami)"
if loginctl show-user "${USER_NAME}" -p Linger 2>/dev/null | grep -q 'Linger=yes'; then
  echo "Linger already enabled for ${USER_NAME} (user services start at boot)."
else
  echo "Enabling linger for ${USER_NAME} so print-server starts at boot without login..."
  if sudo -n loginctl enable-linger "${USER_NAME}" 2>/dev/null; then
    echo "Linger enabled."
  elif sudo loginctl enable-linger "${USER_NAME}"; then
    echo "Linger enabled."
  else
    echo ""
    echo "ERROR: Could not enable linger (needs passwordless sudo or an interactive SSH session)."
    echo "Run once on the Pi: sudo loginctl enable-linger ${USER_NAME}"
    exit 1
  fi
fi

if ! systemctl --user is-enabled print-server.service >/dev/null 2>&1; then
  echo "ERROR: print-server.service is not enabled for boot."
  exit 1
fi

systemctl --user --no-pager status print-server.service || true
REMOTE_SCRIPT

echo ""
echo "Done. Open http://$(ssh "${REMOTE}" hostname -I 2>/dev/null | awk '{print $1}'):8080/"
