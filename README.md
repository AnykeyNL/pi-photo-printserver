# pi-photo-printserver

A small, self-hosted **photo print server for the Raspberry Pi**.

Plug a photo printer into the Pi over USB, open a web page from any phone, tablet or laptop on your network, drop in a photo, pick the paper size and orientation, crop it if you like, and press **Print**. No drivers or apps to install on the client, and no cloud.

It is built around **CUPS + Gutenprint**, which means it works with many **older dye-sublimation photo printers** that no longer have working drivers on modern Windows or macOS. The reference setup is a **Sony UP-DR200**, but any printer Gutenprint supports can be used by pointing the queue at it.

> Status: working daily-driver for a UP-DR200 on a Raspberry Pi. Other printers will need a one-line queue change (see [Other printers](#other-printers)).

## Features

- **Web UI** that works on desktop and mobile (responsive single-column layout on phones).
- **Upload** JPEG, PNG or WebP; originals are kept so you can reprint later.
- **Media sizes** 4×6, 5×7 and 6×8 with **portrait / landscape** orientation.
- **Auto cover**, **auto letterbox**, or **manual crop** (drag + zoom) that matches the paper aspect.
- **Background print queue**: queue several photos and keep working; switching photos never cancels a job.
- **Print history** with thumbnails and last-used settings; one-click reprint; remove single entries or clear all.
- **Printer status** card (idle / printing / door open / out of paper when the backend reports it).
- **Detailed per-job logs** (prepare → `lp` → CUPS state → device result) with "copy last job log" for troubleshooting.
- Correct **CUPS job completion tracking**: a job is only marked *completed* when the device accepts it, not when `lp` returns.
- Runs as a **systemd user service** and starts automatically at boot.

## How it works

```
Browser ──► FastAPI (:8080) ──► Pillow (resize / crop / rotate)
                                      │
                                      ▼
                             lp -d <queue> -o PageSize=… ──► CUPS ──► Gutenprint ──► USB printer
```

- Uploads are stored under `data/uploads/` as `{id}-source.*`; print-ready JPEGs are generated per job under `data/uploads/jobs/`.
- History lives in `data/history.json`, the event log in `data/print.log.jsonl`.
- The print worker polls `lpstat` until CUPS reports the job finished and reads the device status line so `canceled-at-device` is surfaced as a failure instead of a silent "completed".

## Hardware

- Raspberry Pi (tested on Raspberry Pi OS, 64-bit, Python 3.11+). Any Debian-based Pi image should work.
- A USB photo printer supported by Gutenprint. Tested: **Sony UP-DR200** (USB ID `054c:035f`).
- Matching paper + ribbon. The media size chosen in the UI must match what is loaded.

## Installation on the Pi

### 1. Get the code onto the Pi

```bash
git clone https://github.com/AnykeyNL/pi-photo-printserver.git ~/print-server
cd ~/print-server
```

### 2. One-time printer / CUPS setup (needs sudo)

```bash
sudo bash deploy/setup-cups.sh
```

This installs CUPS and Gutenprint, finds the printer on USB, creates a queue named **`UP-DR200`**, enables it, and prints a smoke-test command. If you cloned on Windows first, make sure the script has LF line endings (`sed -i 's/\r$//' deploy/*.sh`).

Verify:

```bash
lsusb | grep -i 054c          # printer visible on USB
lpstat -p UP-DR200            # queue exists and is idle
lp -d UP-DR200 /usr/share/cups/data/testprint
```

### 3. Install and start the web app

Either run it directly on the Pi:

```bash
cd ~/print-server
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
mkdir -p ~/.config/systemd/user
cp deploy/print-server.service ~/.config/systemd/user/
systemctl --user daemon-reload
systemctl --user enable --now print-server.service
sudo loginctl enable-linger "$USER"      # start at boot without a login
```

…or deploy from another machine over SSH, which does all of the above (including the linger step) for you:

```bash
REMOTE=pi@raspberrypi.local ./deploy/deploy.sh
```

Then open **`http://<pi-hostname-or-ip>:8080/`** from any device on your LAN.

### Updating

```bash
cd ~/print-server && git pull
.venv/bin/pip install -r requirements.txt
systemctl --user restart print-server
```

or simply re-run `deploy/deploy.sh`.

## Other printers

The server itself is printer-agnostic; the printer-specific bits are:

| What | Where | Notes |
|------|-------|-------|
| CUPS queue name | `PRINT_SERVER_PRINTER` env var / `deploy/print-server.service` | Default `UP-DR200` |
| Queue creation | `deploy/setup-cups.sh` | Adjust the USB ID and PPD search strings |
| `PageSize` tokens | `app/cups_options.py` | Taken from your printer's PPD (`lpoptions -p <queue> -l`) |
| Print resolution | `app/imaging.py` (`CUPS_DPI`) and `Resolution=` in `app/cups_options.py` | Must match the PPD's native resolution |

Steps: create a queue for your printer in CUPS, run `lpoptions -p <queue> -l`, copy the `PageSize` values for the paper you use into `MEDIA_PAGE_SIZE`, and set `CUPS_DPI` / `Resolution` to the printer's native DPI.

## Media sizes

| UI label | Preview (300 dpi) | Sent to printer (334 dpi, UP-DR200) |
|----------|-------------------|--------------------------------------|
| 4×6      | 1200×1800         | 1336×2004                            |
| 5×7      | 1500×2100         | 1670×2338                            |
| 6×8      | 1800×2400         | 2004×2672                            |

Landscape prints are rendered wide×tall and sent with `orientation-requested=4`; CUPS/Gutenprint handles the rotation for the printer.

## Configuration

All settings are environment variables (set them in the systemd unit if you change them):

| Variable | Default | Purpose |
|----------|---------|---------|
| `PRINT_SERVER_HOST` | `0.0.0.0` | Bind address |
| `PRINT_SERVER_PORT` | `8080` | HTTP port |
| `PRINT_SERVER_PRINTER` | `UP-DR200` | CUPS queue name |
| `PRINT_SERVER_UPLOAD_DIR` | `./data/uploads` | Where uploads and job files live |
| `PRINT_SERVER_HISTORY_PATH` | `./data/history.json` | History store |
| `PRINT_SERVER_HISTORY_MAX` | `500` | Max history entries |
| `PRINT_SERVER_PRINT_LOG` | `./data/print.log.jsonl` | Event log |
| `PRINT_SERVER_MAX_BYTES` | `26214400` | Max upload size (25 MB) |

## API

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/` | Web UI |
| `POST` | `/api/upload` | Multipart image (+ optional `media`, `fit`, `orientation`) |
| `POST` | `/api/prepare` | Re-render preview: `file_id`, `media`, `orientation`, `fit`, optional `crop {x,y,width,height}` |
| `GET` | `/api/files/{id}` | Preview JPEG |
| `GET` | `/api/files/{id}/source` | Original upload |
| `POST` | `/api/print` | Queue a print job; returns `queue_id` immediately |
| `GET` | `/api/queue` | Recent / active print jobs |
| `GET` | `/api/queue/{queue_id}` | One queue job |
| `GET` | `/api/history` | Uploaded / printed photos with last settings |
| `GET` | `/api/history/{id}` | One history entry |
| `DELETE` | `/api/history/{id}` | Remove one history entry (file is kept) |
| `DELETE` | `/api/history` | Clear history |
| `GET` | `/api/logs` | Print event log (`?limit=`, `?queue_id=`, `?level=`) |
| `DELETE` | `/api/logs` | Clear the event log |
| `GET` | `/api/status` | Printer / CUPS / USB backend status |
| `GET` | `/api/jobs/{id}` | Raw CUPS job lookup |

## Troubleshooting

- **Job says "completed" but nothing printed** — open *Show logs* in the UI or run `lpstat -W completed -l -o`. A `Job data length mismatch` / `canceled-at-device` status means the raster didn't match the paper: check that the UI media size matches the loaded ribbon and that `PageSize`/`Resolution` in `app/cups_options.py` match your PPD.
- **Printer status shows "Could not load status"** — the service is down or restarting: `systemctl --user status print-server`.
- **Service doesn't start after reboot** — `loginctl show-user $USER -p Linger` must say `Linger=yes`; run `sudo loginctl enable-linger $USER`.
- **No Gutenprint USB backend** — `ls /usr/lib/cups/backend/ | grep gutenprint`. On older distros you may need to build [selphy_print](https://git.shaftnet.org/gitea/slp/selphy_print) from source.
- CUPS logs: `/var/log/cups/error_log`.

## Security

The server is intentionally **open on your LAN with no authentication** — anyone on the network can upload and print (and use your paper and ribbon). Do not expose port 8080 to the internet. If you need access control, put it behind a reverse proxy (nginx, Caddy, Tailscale, …).

## Local development

```bash
python3 -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install -r requirements.txt
uvicorn app.main:app --reload --host 127.0.0.1 --port 8080
```

The UI, upload, crop and history all work without a printer. Actual printing requires CUPS and a configured queue on the machine running the server.

## Project layout

```
app/
  main.py          FastAPI routes
  imaging.py       Resize / crop / fit for preview and print rasters
  cups_options.py  Media → PageSize / orientation / Gutenprint options
  printer.py       lp / lpstat wrappers, job tracking, device status
  print_queue.py   Background worker + per-job logging
  print_log.py     Persistent JSONL event log
  history.py       Upload / print history store
  paths.py         File-id validation and storage paths
  config.py        Environment configuration
static/            app.js, style.css
templates/         index.html
deploy/
  setup-cups.sh    One-time CUPS + Gutenprint + queue setup
  deploy.sh        SSH deploy: sync, venv, systemd user unit, linger
  print-server.service
```
