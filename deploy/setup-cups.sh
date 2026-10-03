#!/usr/bin/env bash
# One-time CUPS + Gutenprint setup for Sony UP-DR200 on Debian/Raspberry Pi OS.
set -euo pipefail

PRINTER_NAME="${PRINTER_NAME:-UP-DR200}"

if [[ "${EUID:-$(id -u)}" -ne 0 ]]; then
  echo "Run with sudo: sudo bash deploy/setup-cups.sh" >&2
  exit 1
fi

export DEBIAN_FRONTEND=noninteractive
apt-get update -qq
apt-get install -y \
  cups \
  cups-client \
  cups-bsd \
  printer-driver-gutenprint \
  libusb-1.0-0

systemctl enable --now cups

BACKEND=""
for candidate in gutenprint53+usb gutenprint52+usb; do
  if [[ -x "/usr/lib/cups/backend/${candidate}" ]]; then
    BACKEND="${candidate}"
    break
  fi
done

if [[ -z "${BACKEND}" ]]; then
  echo "ERROR: No gutenprint USB backend found under /usr/lib/cups/backend/." >&2
  echo "Install printer-driver-gutenprint or build selphy_print from upstream." >&2
  exit 1
fi

echo "Using CUPS backend: ${BACKEND}"

if ! lsusb | grep -qi '054c:035f'; then
  echo "WARNING: Sony UP-DR200 (054c:035f) not seen on USB. Plug in the printer and re-run." >&2
fi

echo "Discovered device URIs (gutenprint):"
lpinfo -v 2>/dev/null | grep -i gutenprint || true

DEVICE_URI=""
while IFS= read -r line; do
  if echo "${line}" | grep -qi 'updr200\|up-dr200\|sony-updr200'; then
    DEVICE_URI="${line#* }"
    break
  fi
done < <(lpinfo -v 2>/dev/null | grep -i gutenprint || true)

if [[ -z "${DEVICE_URI}" ]]; then
  while IFS= read -r line; do
    if echo "${line}" | grep -qi '054c\|sony'; then
      DEVICE_URI="${line#* }"
      break
    fi
  done < <(lpinfo -v 2>/dev/null | grep -i gutenprint || true)
fi

if [[ -z "${DEVICE_URI}" ]]; then
  echo "ERROR: Could not find gutenprint URI for UP-DR200. Run: lpinfo -v | grep -i gutenprint" >&2
  exit 1
fi

echo "Device URI: ${DEVICE_URI}"

PPD=""
while IFS= read -r line; do
  if echo "${line}" | grep -qi 'up-dr200\|up_dr200'; then
    PPD="${line%% *}"
    break
  fi
done < <(lpinfo -m 2>/dev/null | grep -i 'sony.*dr200\|up.dr200' || true)

if [[ -z "${PPD}" ]]; then
  PPD="$(lpinfo -m 2>/dev/null | grep -i 'gutenprint.*sony' | grep -i dr200 | head -1 | awk '{print $1}' || true)"
fi

if [[ -z "${PPD}" ]]; then
  echo "ERROR: Could not find Gutenprint PPD for UP-DR200. Run: lpinfo -m | grep -i dr200" >&2
  exit 1
fi

echo "PPD: ${PPD}"

if lpstat -p "${PRINTER_NAME}" &>/dev/null; then
  echo "Updating existing queue ${PRINTER_NAME}..."
  lpadmin -p "${PRINTER_NAME}" -E -v "${DEVICE_URI}" -m "${PPD}" -o printer-is-shared=false
else
  echo "Creating queue ${PRINTER_NAME}..."
  lpadmin -p "${PRINTER_NAME}" -E -v "${DEVICE_URI}" -m "${PPD}" -o printer-is-shared=false
fi

cupsaccept "${PRINTER_NAME}" 2>/dev/null || true
cupsenable "${PRINTER_NAME}"

lpoptions -d "${PRINTER_NAME}" 2>/dev/null || lpadmin -d "${PRINTER_NAME}"

echo ""
lpstat -p "${PRINTER_NAME}" -l || lpstat -p "${PRINTER_NAME}"
echo ""
echo "Setup complete. Test with:"
echo "  lp -d ${PRINTER_NAME} /usr/share/cups/data/testprint"
echo ""
echo "Optional status (replace URI if needed):"
echo "  BACKEND=sonyupd /usr/lib/cups/backend/${BACKEND} -s '${DEVICE_URI}'"
