let currentFileId = null;
let sourceNaturalW = 0;
let sourceNaturalH = 0;
let sourceUrl = null;

/** Display scale: CSS pixels per source pixel. */
let cropScale = 1;
let cropOffsetX = 0;
let cropOffsetY = 0;
let cropZoom = 1;

let prepareTimer = null;
let dragging = false;
let dragStartX = 0;
let dragStartY = 0;
let dragStartOffsetX = 0;
let dragStartOffsetY = 0;

const MEDIA_PIXELS = {
  "4x6": [1200, 1800],
  "5x7": [1500, 2100],
  "6x8": [1800, 2400],
};

const dropzone = document.getElementById("dropzone");
const fileInput = document.getElementById("file-input");
const pickBtn = document.getElementById("pick-btn");
const previewSection = document.getElementById("preview-section");
const preview = document.getElementById("preview");
const previewFrame = document.getElementById("preview-frame");
const previewWrap = document.getElementById("preview-wrap");
const meta = document.getElementById("meta");
const printBtn = document.getElementById("print-btn");
const message = document.getElementById("message");
const printerStatusEl = document.getElementById("printer-status");
const refreshStatus = document.getElementById("refresh-status");
const cropEditor = document.getElementById("crop-editor");
const cropFrame = document.getElementById("crop-frame");
const sourceImg = document.getElementById("source-img");
const cropZoomInput = document.getElementById("crop-zoom");
const historyList = document.getElementById("history-list");
const historyEmpty = document.getElementById("history-empty");
const clearHistoryBtn = document.getElementById("clear-history");
const queueList = document.getElementById("queue-list");
const queueEmpty = document.getElementById("queue-empty");
const toggleLogsBtn = document.getElementById("toggle-logs");
const clearLogsBtn = document.getElementById("clear-logs");
const copyLastJobLogBtn = document.getElementById("copy-last-job-log");
const printLogsEl = document.getElementById("print-logs");

let logsVisible = false;

function setMessage(text, kind = "") {
  message.textContent = text;
  message.className = "message" + (kind ? ` ${kind}` : "");
}

function currentMedia() {
  return document.getElementById("media").value;
}

function currentFit() {
  return document.getElementById("fit").value;
}

function currentOrientation() {
  return document.getElementById("orientation").value;
}

function targetPrintPixels() {
  const [w, h] = MEDIA_PIXELS[currentMedia()] || [1200, 1800];
  return currentOrientation() === "landscape" ? [h, w] : [w, h];
}

function isManualCrop() {
  return currentFit() === "manual";
}

function updateMeta(data, filename) {
  const name = filename || data.filename || "Image";
  const orig =
    data.original_width != null
      ? ` (from ${data.original_width}×${data.original_height})`
      : "";
  const orient = data.orientation || currentOrientation();
  meta.textContent = `${name} — print ${data.width}×${data.height}px, ${data.media}, ${orient}, ${data.fit}${orig}`;
}

const PRINT_BOX_MAX_HEIGHT = 420;

function sizeAspectBox(el, tw, th) {
  if (!el) return;
  const parent = el.parentElement;
  const containerW = parent && parent.clientWidth > 0 ? parent.clientWidth : 400;
  const maxH = Math.min(PRINT_BOX_MAX_HEIGHT, Math.floor(window.innerHeight * 0.55));
  const aspect = tw / th;

  let height = maxH;
  let width = height * aspect;
  if (width > containerW) {
    width = containerW;
    height = width / aspect;
  }

  el.style.width = `${Math.round(width)}px`;
  el.style.height = `${Math.round(height)}px`;
  el.style.aspectRatio = `${tw} / ${th}`;
}

function updatePrintAspects() {
  const [tw, th] = targetPrintPixels();
  sizeAspectBox(cropFrame, tw, th);
  sizeAspectBox(previewFrame, tw, th);
}

function frameSize() {
  return { w: cropFrame.clientWidth, h: cropFrame.clientHeight };
}

function clampOffsets() {
  const { w: fw, h: fh } = frameSize();
  const imgW = sourceNaturalW * cropScale;
  const imgH = sourceNaturalH * cropScale;
  const minX = Math.min(0, fw - imgW);
  const minY = Math.min(0, fh - imgH);
  cropOffsetX = Math.max(minX, Math.min(0, cropOffsetX));
  cropOffsetY = Math.max(minY, Math.min(0, cropOffsetY));
}

function applyCropTransform() {
  sourceImg.style.width = `${sourceNaturalW * cropScale}px`;
  sourceImg.style.height = `${sourceNaturalH * cropScale}px`;
  sourceImg.style.transform = `translate(${cropOffsetX}px, ${cropOffsetY}px)`;
}

function coverScaleForFrame() {
  const { w: fw, h: fh } = frameSize();
  return Math.max(fw / sourceNaturalW, fh / sourceNaturalH);
}

function initCropLayout() {
  if (!sourceNaturalW || !sourceNaturalH) return;
  updatePrintAspects();
  const { w: fw, h: fh } = frameSize();
  if (fw < 1 || fh < 1) return;

  const coverScale = coverScaleForFrame();
  cropScale = coverScale * cropZoom;
  const imgW = sourceNaturalW * cropScale;
  const imgH = sourceNaturalH * cropScale;
  cropOffsetX = (fw - imgW) / 2;
  cropOffsetY = (fh - imgH) / 2;
  clampOffsets();
  applyCropTransform();
}

function applySavedCrop(crop) {
  if (!crop || !sourceNaturalW) return;
  updatePrintAspects();
  const { w: fw, h: fh } = frameSize();
  if (fw < 1 || fh < 1) return;

  const coverScale = coverScaleForFrame();
  cropScale = fw / crop.width;
  cropZoom = cropScale / coverScale;
  cropZoomInput.value = String(Math.min(4, Math.max(1, cropZoom)));
  cropOffsetX = -crop.x * cropScale;
  cropOffsetY = -crop.y * cropScale;
  clampOffsets();
  applyCropTransform();
}

function computeCropRect() {
  const { w: fw, h: fh } = frameSize();
  const srcX = -cropOffsetX / cropScale;
  const srcY = -cropOffsetY / cropScale;
  const srcW = fw / cropScale;
  const srcH = fh / cropScale;

  let x = Math.round(Math.max(0, srcX));
  let y = Math.round(Math.max(0, srcY));
  let width = Math.round(Math.min(srcW, sourceNaturalW - x));
  let height = Math.round(Math.min(srcH, sourceNaturalH - y));
  width = Math.max(1, width);
  height = Math.max(1, height);

  return { x, y, width, height };
}

function schedulePrepare() {
  if (prepareTimer) clearTimeout(prepareTimer);
  prepareTimer = setTimeout(() => {
    refreshPreview().catch((err) => setMessage(err.message || String(err), "err"));
  }, 180);
}

function preparePayload() {
  const payload = {
    file_id: currentFileId,
    media: currentMedia(),
    fit: currentFit(),
    orientation: currentOrientation(),
  };
  if (isManualCrop()) {
    payload.crop = computeCropRect();
  }
  return payload;
}

function humanPrinterState(state) {
  const labels = {
    idle: "Ready",
    ready: "Ready",
    printing: "Printing",
    disabled: "Disabled",
    missing: "Not configured",
    unknown: "Unknown",
  };
  return labels[state] || String(state || "Unknown");
}

function summarizeBackend(backend) {
  if (!backend) return null;
  if (backend.error) return backend.error;
  const text = (backend.text || "").trim();
  if (!text) return null;
  if (/READY/i.test(text)) return "Ready to print";
  if (/DOOR OPEN/i.test(text)) return "Door open";
  if (/LOAD PAPER|NOPAPER/i.test(text)) return "Load paper";
  if (/RIBBON|NORIBBON/i.test(text)) return "Load ribbon";
  const line = text.split("\n").find((l) => l.trim()) || text;
  return line.length > 100 ? `${line.slice(0, 97)}…` : line;
}

function renderPrinterStatus(data) {
  if (!printerStatusEl) return;

  const state = data.state || "unknown";
  const stateClass = ["idle", "ready", "printing"].includes(state)
    ? state === "printing"
      ? "printing"
      : "ok"
    : ["disabled", "missing"].includes(state)
      ? "warn"
      : "neutral";

  printerStatusEl.innerHTML = "";
  printerStatusEl.className = `printer-status-card state-${stateClass}`;

  const titleRow = document.createElement("div");
  titleRow.className = "printer-status-title-row";

  const dot = document.createElement("span");
  dot.className = "printer-status-dot";
  dot.setAttribute("aria-hidden", "true");

  const title = document.createElement("div");
  title.className = "printer-status-name";
  title.textContent = data.printer || "Printer";

  const badge = document.createElement("span");
  badge.className = `printer-state-badge ${stateClass}`;
  badge.textContent = humanPrinterState(state);

  titleRow.appendChild(dot);
  titleRow.appendChild(title);
  titleRow.appendChild(badge);

  const lines = document.createElement("ul");
  lines.className = "printer-status-lines";

  if (!data.cups_available) {
    lines.appendChild(makeStatusLine("CUPS", "Not available — install cups-client"));
  } else {
    const backendMsg = summarizeBackend(data.backend);
    if (backendMsg) {
      lines.appendChild(makeStatusLine("Device", backendMsg));
    } else if (data.detail) {
      const short = data.detail.split("\n")[0].trim();
      if (short) lines.appendChild(makeStatusLine("CUPS", short));
    }
  }

  printerStatusEl.appendChild(titleRow);
  if (lines.childElementCount) printerStatusEl.appendChild(lines);
}

function makeStatusLine(label, value) {
  const li = document.createElement("li");
  const strong = document.createElement("span");
  strong.className = "printer-status-label";
  strong.textContent = label;
  li.appendChild(strong);
  li.appendChild(document.createTextNode(value));
  return li;
}

let printerStatusInFlight = false;
let printerStatusLoaded = false;

async function refreshPrinterStatus() {
  if (!printerStatusEl || printerStatusInFlight) return;
  printerStatusInFlight = true;
  try {
    const res = await fetch("/api/status");
    if (!res.ok) {
      throw new Error(res.statusText || `HTTP ${res.status}`);
    }
    const data = await res.json();
    renderPrinterStatus(data);
    printerStatusLoaded = true;
  } catch (err) {
    if (!printerStatusLoaded) {
      printerStatusEl.className = "printer-status-card state-warn";
      printerStatusEl.textContent = `Could not load status: ${err.message || err}`;
    }
  } finally {
    printerStatusInFlight = false;
  }
}

if (refreshStatus) {
  refreshStatus.addEventListener("click", refreshPrinterStatus);
}
refreshPrinterStatus();

function formatLogTime(iso) {
  try {
    return new Date(iso).toLocaleString();
  } catch (_) {
    return iso || "";
  }
}

function formatLogExtra(entry) {
  const skip = new Set(["ts", "level", "event", "message"]);
  const parts = [];
  for (const [key, value] of Object.entries(entry)) {
    if (skip.has(key) || value == null) continue;
    if (typeof value === "object") {
      parts.push(`${key}: ${JSON.stringify(value)}`);
    } else {
      parts.push(`${key}: ${value}`);
    }
  }
  return parts.join("\n");
}

function formatLogEntryPlain(entry) {
  const head = `${formatLogTime(entry.ts)} [${(entry.level || "info").toUpperCase()}] ${entry.event || "event"}: ${entry.message || ""}`;
  const extra = formatLogExtra(entry);
  return extra ? `${head}\n  ${extra.replace(/\n/g, "\n  ")}` : head;
}

async function resolveLastQueueId() {
  try {
    const qRes = await fetch("/api/queue?limit=1");
    const qData = await qRes.json();
    if (qData.jobs?.[0]?.queue_id) {
      return qData.jobs[0].queue_id;
    }
  } catch (_) {
    /* fall through */
  }
  const logRes = await fetch("/api/logs?limit=500");
  const logData = await logRes.json();
  const entries = logData.entries || [];
  for (let i = entries.length - 1; i >= 0; i--) {
    if (entries[i].queue_id) return entries[i].queue_id;
  }
  return null;
}

async function copyLastJobLogToClipboard() {
  const queueId = await resolveLastQueueId();
  if (!queueId) {
    setMessage("No print job found to copy logs for.", "err");
    return;
  }

  const logRes = await fetch(`/api/logs?limit=2000&queue_id=${encodeURIComponent(queueId)}`);
  const logData = await logRes.json();
  const entries = logData.entries || [];
  if (!entries.length) {
    setMessage("No log lines for the last print job.", "err");
    return;
  }

  const header = `Print job log — queue_id: ${queueId}\n${"=".repeat(40)}\n`;
  const body = entries.map(formatLogEntryPlain).join("\n\n");
  const text = header + body + "\n";

  try {
    await navigator.clipboard.writeText(text);
    setMessage(`Copied ${entries.length} log line(s) for last job to clipboard.`, "ok");
  } catch (_) {
    const ta = document.createElement("textarea");
    ta.value = text;
    ta.setAttribute("readonly", "");
    ta.style.position = "fixed";
    ta.style.left = "-9999px";
    document.body.appendChild(ta);
    ta.select();
    const ok = document.execCommand("copy");
    document.body.removeChild(ta);
    if (ok) {
      setMessage(`Copied ${entries.length} log line(s) for last job to clipboard.`, "ok");
    } else {
      setMessage("Could not copy to clipboard. Use Show logs and copy manually.", "err");
    }
  }
}

function renderPrintLogs(entries) {
  if (!printLogsEl) return;
  printLogsEl.innerHTML = "";
  if (!entries.length) {
    printLogsEl.textContent = "No log entries yet.";
    return;
  }
  const newestFirst = [...entries].reverse();
  for (const entry of newestFirst) {
    const line = document.createElement("div");
    line.className = `print-log-line level-${entry.level || "info"}`;

    const head = document.createElement("div");
    head.innerHTML = `<span class="print-log-meta">${formatLogTime(entry.ts)}</span> ` +
      `<span class="print-log-event">${entry.event || "event"}</span> ` +
      `<span class="print-log-meta">[${(entry.level || "info").toUpperCase()}]</span> ` +
      `${entry.message || ""}`;

    line.appendChild(head);
    const extra = formatLogExtra(entry);
    if (extra) {
      const ex = document.createElement("div");
      ex.className = "print-log-extra";
      ex.textContent = extra;
      line.appendChild(ex);
    }
    printLogsEl.appendChild(line);
  }
}

async function loadPrintLogs() {
  if (!logsVisible || !printLogsEl) return;
  try {
    const res = await fetch("/api/logs?limit=250");
    const data = await res.json();
    renderPrintLogs(data.entries || []);
  } catch (err) {
    printLogsEl.textContent = `Failed to load logs: ${err.message || err}`;
  }
}

if (toggleLogsBtn) {
  toggleLogsBtn.addEventListener("click", () => {
    logsVisible = !logsVisible;
    printLogsEl.classList.toggle("hidden", !logsVisible);
    clearLogsBtn.classList.toggle("hidden", !logsVisible);
    toggleLogsBtn.textContent = logsVisible ? "Hide logs" : "Show logs";
    toggleLogsBtn.setAttribute("aria-expanded", logsVisible ? "true" : "false");
    if (logsVisible) loadPrintLogs();
  });
}

if (clearLogsBtn) {
  clearLogsBtn.addEventListener("click", async () => {
    if (!confirm("Clear all print logs?")) return;
    await fetch("/api/logs", { method: "DELETE" });
    loadPrintLogs();
  });
}

if (clearHistoryBtn) {
  clearHistoryBtn.addEventListener("click", () => {
    clearAllHistory().catch((err) => setMessage(err.message || String(err), "err"));
  });
}

if (copyLastJobLogBtn) {
  copyLastJobLogBtn.addEventListener("click", () => {
    copyLastJobLogToClipboard().catch((err) =>
      setMessage(err.message || String(err), "err")
    );
  });
}

function formatHistoryWhen(iso) {
  if (!iso) return "Not printed";
  try {
    return new Date(iso).toLocaleString();
  } catch (_) {
    return iso;
  }
}

function settingsSummary(settings) {
  if (!settings) return "";
  const parts = [settings.media, settings.orientation, settings.fit];
  if (settings.copies != null) parts.push(`${settings.copies}×`);
  return parts.join(" · ");
}

function resetEditorIfFileRemoved(fileId) {
  if (currentFileId !== fileId) return;
  currentFileId = null;
  sourceUrl = null;
  previewSection.classList.add("hidden");
  setMessage("", "ok");
}

async function removeHistoryItem(fileId, event) {
  if (event) {
    event.preventDefault();
    event.stopPropagation();
  }
  try {
    const res = await fetch(`/api/history/${encodeURIComponent(fileId)}`, {
      method: "DELETE",
    });
    const data = await res.json().catch(() => ({}));
    if (!res.ok) {
      throw new Error(data.detail || "Could not remove history item");
    }
    resetEditorIfFileRemoved(fileId);
    await loadHistory();
  } catch (err) {
    setMessage(err.message || String(err), "err");
  }
}

async function clearAllHistory() {
  if (!confirm("Remove all entries from print history? Photos on disk are kept.")) return;
  try {
    const res = await fetch("/api/history", { method: "DELETE" });
    const data = await res.json().catch(() => ({}));
    if (!res.ok) {
      throw new Error(data.detail || "Could not clear history");
    }
    await loadHistory();
    setMessage("History cleared.", "ok");
  } catch (err) {
    setMessage(err.message || String(err), "err");
  }
}

function renderHistory(items) {
  historyList.innerHTML = "";
  historyEmpty.classList.toggle("hidden", items.length > 0);
  if (clearHistoryBtn) {
    clearHistoryBtn.classList.toggle("hidden", items.length === 0);
  }

  for (const item of items) {
    const li = document.createElement("li");
    li.className = "history-entry";

    const btn = document.createElement("button");
    btn.type = "button";
    btn.className = "history-item" + (item.file_id === currentFileId ? " active" : "");
    btn.dataset.fileId = item.file_id;

    const removeBtn = document.createElement("button");
    removeBtn.type = "button";
    removeBtn.className = "history-remove";
    removeBtn.setAttribute("aria-label", "Remove from history");
    removeBtn.title = "Remove from history";
    removeBtn.textContent = "×";
    removeBtn.addEventListener("click", (event) => removeHistoryItem(item.file_id, event));

    const img = document.createElement("img");
    img.src = (item.thumb_url || item.preview_url) + "?t=" + (item.printed_at || item.uploaded_at || "");
    img.alt = "";

    const body = document.createElement("div");
    body.className = "history-item-body";

    const title = document.createElement("div");
    title.className = "history-item-title";
    title.textContent = item.filename || "Photo";

    const metaLine = document.createElement("div");
    metaLine.className = "history-item-meta";
    const printed = item.print_count > 0 ? `Printed ${item.print_count}×` : "Uploaded only";
    metaLine.textContent = `${printed}\n${settingsSummary(item.last_settings)}\n${formatHistoryWhen(item.printed_at || item.uploaded_at)}`;

    body.appendChild(title);
    body.appendChild(metaLine);
    btn.appendChild(img);
    btn.appendChild(body);
    btn.addEventListener("click", () => loadFromHistory(item.file_id));
    li.appendChild(btn);
    li.appendChild(removeBtn);
    historyList.appendChild(li);
  }
}

async function loadHistory() {
  try {
    const res = await fetch("/api/history?limit=100");
    const data = await res.json();
    renderHistory(data.items || []);
  } catch (err) {
    console.error(err);
  }
}

function formatQueueStatus(status) {
  return (status || "unknown").replace(/_/g, " ");
}

function renderQueue(jobs) {
  queueList.innerHTML = "";
  queueEmpty.classList.toggle("hidden", jobs.length > 0);

  for (const job of jobs) {
    const li = document.createElement("li");
    li.className = "queue-item";

    const header = document.createElement("div");
    header.className = "queue-item-header";

    const title = document.createElement("div");
    title.className = "queue-item-title";
    title.textContent = job.filename || "Photo";

    const badge = document.createElement("span");
    badge.className = `queue-status ${job.status || "queued"}`;
    badge.textContent = formatQueueStatus(job.status);

    header.appendChild(title);
    header.appendChild(badge);

    const meta = document.createElement("div");
    meta.className = "queue-item-meta";
    const settings = job.settings || {};
    const parts = [settings.media, settings.orientation, settings.fit];
    if (settings.copies != null) parts.push(`${settings.copies}×`);
    let line = parts.filter(Boolean).join(" · ");
    if (job.cups_job_id) line += `\nCUPS #${job.cups_job_id}`;
    if (job.error) line += `\n${job.error}`;
    meta.textContent = line;

    li.appendChild(header);
    li.appendChild(meta);
    queueList.appendChild(li);
  }
}

async function loadQueue() {
  try {
    const res = await fetch("/api/queue?limit=50");
    const data = await res.json();
    renderQueue(data.jobs || []);
  } catch (err) {
    console.error(err);
  }
}

function applySettingsToForm(settings) {
  if (!settings) return;
  document.getElementById("media").value = settings.media || "4x6";
  document.getElementById("orientation").value = settings.orientation || "portrait";
  document.getElementById("fit").value = settings.fit || "cover";
  if (settings.copies != null) {
    document.getElementById("copies").value = String(settings.copies);
  }
}

async function loadFromHistory(fileId) {
  try {
    setMessage("Loading…");
    const res = await fetch(`/api/history/${fileId}`);
    const data = await res.json().catch(() => ({}));
    if (!res.ok) {
      throw new Error(data.detail || "Could not load history item");
    }
    applySettingsToForm(data.last_settings);
    applyServerImage(data, data.filename);

    previewSection.classList.remove("hidden");
    if (data.last_settings?.fit === "manual" && data.last_settings.crop) {
      cropEditor.classList.remove("hidden");
      previewWrap.classList.add("hidden");
      sourceImg.src = data.source_url + "?t=" + Date.now();
      sourceImg.onload = () => {
        applySavedCrop(data.last_settings.crop);
        schedulePrepare();
      };
    } else {
      syncCropUi();
    }
    await loadHistory();
    setMessage("Loaded from history.", "ok");
  } catch (err) {
    setMessage(err.message || String(err), "err");
  }
}

function applyServerImage(data, filename) {
  currentFileId = data.file_id;
  sourceNaturalW = data.original_width;
  sourceNaturalH = data.original_height;
  sourceUrl = data.source_url;
  cropZoom = 1;
  cropZoomInput.value = "1";
  updateMeta(
    {
      filename,
      original_width: data.original_width,
      original_height: data.original_height,
      width: data.original_width,
      height: data.original_height,
      media: document.getElementById("media").value,
      fit: document.getElementById("fit").value,
      orientation: document.getElementById("orientation").value,
    },
    filename
  );
}

loadHistory();
loadQueue();
setInterval(() => {
  loadQueue();
  if (logsVisible) loadPrintLogs();
}, 2500);
setInterval(refreshPrinterStatus, 10000);

async function refreshPreview() {
  if (!currentFileId) return;
  if (isManualCrop() && (!sourceNaturalW || !sourceNaturalH)) return;

  setMessage("Updating print preview…");
  const res = await fetch("/api/prepare", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(preparePayload()),
  });
  const data = await res.json().catch(() => ({}));
  if (!res.ok) {
    const detail = data.detail;
    const text =
      typeof detail === "string"
        ? detail
        : Array.isArray(detail)
          ? detail.map((d) => d.msg || JSON.stringify(d)).join("; ")
          : "Could not resize for printer";
    throw new Error(text);
  }
  updatePrintAspects();
  preview.src = data.preview_url + "?t=" + Date.now();
  if (!isManualCrop()) {
    previewWrap.classList.remove("hidden");
  }
  updateMeta(data);
  setMessage("Ready to print.", "ok");
}

function syncCropUi() {
  if (isManualCrop()) {
    cropEditor.classList.remove("hidden");
    previewWrap.classList.add("hidden");
    if (sourceUrl) {
      sourceImg.src = sourceUrl + "?t=" + Date.now();
    }
    cropZoom = Number(cropZoomInput.value) || 1;
    requestAnimationFrame(() => {
      initCropLayout();
      schedulePrepare();
    });
  } else {
    cropEditor.classList.add("hidden");
    previewWrap.classList.remove("hidden");
    refreshPreview().catch((err) => setMessage(err.message || String(err), "err"));
  }
}

async function uploadFile(file) {
  setMessage("Uploading…");
  const form = new FormData();
  form.append("file", file);
  form.append("media", currentMedia());
  form.append("fit", currentFit());
  form.append("orientation", currentOrientation());

  const res = await fetch("/api/upload", { method: "POST", body: form });
  const data = await res.json().catch(() => ({}));
  if (!res.ok) {
    throw new Error(data.detail || res.statusText || "Upload failed");
  }

  applyServerImage(data, file.name);
  previewSection.classList.remove("hidden");
  updateMeta(data, file.name);
  syncCropUi();
  loadHistory();
}

async function handleFiles(files) {
  const file = files && files[0];
  if (!file) return;
  try {
    await uploadFile(file);
  } catch (err) {
    setMessage(err.message || String(err), "err");
  }
}

pickBtn.addEventListener("click", (e) => {
  e.stopPropagation();
  fileInput.click();
});

dropzone.addEventListener("click", () => fileInput.click());
fileInput.addEventListener("change", () => handleFiles(fileInput.files));

dropzone.addEventListener("dragover", (e) => {
  e.preventDefault();
  dropzone.classList.add("dragover");
});
dropzone.addEventListener("dragleave", () => dropzone.classList.remove("dragover"));
dropzone.addEventListener("drop", (e) => {
  e.preventDefault();
  dropzone.classList.remove("dragover");
  handleFiles(e.dataTransfer.files);
});

["media", "orientation", "fit"].forEach((id) => {
  document.getElementById(id).addEventListener("change", () => {
    updatePrintAspects();
    if (!currentFileId) return;
    syncCropUi();
  });
});

cropZoomInput.addEventListener("input", () => {
  if (!isManualCrop() || !sourceNaturalW) return;
  const prevScale = cropScale;
  const { w: fw, h: fh } = frameSize();
  const cx = fw / 2;
  const cy = fh / 2;
  const srcCx = (cx - cropOffsetX) / prevScale;
  const srcCy = (cy - cropOffsetY) / prevScale;

  cropZoom = Number(cropZoomInput.value) || 1;
  const coverScale = Math.max(fw / sourceNaturalW, fh / sourceNaturalH);
  cropScale = coverScale * cropZoom;
  cropOffsetX = cx - srcCx * cropScale;
  cropOffsetY = cy - srcCy * cropScale;
  clampOffsets();
  applyCropTransform();
  schedulePrepare();
});

function onPointerDown(e) {
  if (!isManualCrop()) return;
  dragging = true;
  cropFrame.classList.add("dragging");
  dragStartX = e.clientX;
  dragStartY = e.clientY;
  dragStartOffsetX = cropOffsetX;
  dragStartOffsetY = cropOffsetY;
  cropFrame.setPointerCapture(e.pointerId);
}

function onPointerMove(e) {
  if (!dragging) return;
  cropOffsetX = dragStartOffsetX + (e.clientX - dragStartX);
  cropOffsetY = dragStartOffsetY + (e.clientY - dragStartY);
  clampOffsets();
  applyCropTransform();
}

function onPointerUp(e) {
  if (!dragging) return;
  dragging = false;
  cropFrame.classList.remove("dragging");
  try {
    cropFrame.releasePointerCapture(e.pointerId);
  } catch (_) {
    /* ignore */
  }
  schedulePrepare();
}

cropFrame.addEventListener("pointerdown", onPointerDown);
cropFrame.addEventListener("pointermove", onPointerMove);
cropFrame.addEventListener("pointerup", onPointerUp);
cropFrame.addEventListener("pointercancel", onPointerUp);

window.addEventListener("resize", () => {
  updatePrintAspects();
  if (isManualCrop() && currentFileId) {
    initCropLayout();
    schedulePrepare();
  }
});

printBtn.addEventListener("click", async () => {
  if (!currentFileId) {
    setMessage("Upload an image first.", "err");
    return;
  }

  const body = {
    ...preparePayload(),
    copies: Number(document.getElementById("copies").value) || 1,
  };

  setMessage("Queuing print job…");

  try {
    const res = await fetch("/api/print", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
    const data = await res.json().catch(() => ({}));
    if (!res.ok) {
      const detail = data.detail;
      const text =
        typeof detail === "string"
          ? detail
          : Array.isArray(detail)
            ? detail.map((d) => d.msg).join("; ")
            : "Print failed";
      throw new Error(text);
    }
    setMessage(data.message || "Print queued — processing in background.", "ok");
    loadQueue();
    refreshPrinterStatus();
    loadPrintLogs();
  } catch (err) {
    setMessage(err.message || String(err), "err");
  }
});
