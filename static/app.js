const $ = (id) => document.getElementById(id);

const labels = {
  running: "取得中",
  stopped: "停止中",
  error: "エラー",
};

let isRunning = false;
const defaultChannelsByDevice = new Map();

function formConfig() {
  const device = $("input_device").value;
  const channels = Number($("input_channels").value);
  return {
    mode: "command",
    gps_channel: Number($("gps_channel").value),
    input_channels: channels,
    input_device: device,
    input_command: `arecord -D ${device} -f S16_LE -r 48000 -c ${channels} -t raw`,
    test_capture_dir: $("test_capture_dir").value,
    output_csv: $("output_csv").value,
  };
}

async function postJson(url, body = {}) {
  const res = await fetch(url, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  const data = await res.json();
  if (!res.ok || data.ok === false) {
    throw new Error(data.error || "request failed");
  }
  return data;
}

function setError(message) {
  $("errorText").textContent = message || "";
}

async function applyConfig() {
  setError("");
  syncCommandPreview();
  await postJson("/api/config", formConfig());
}

async function start() {
  if (!isRunning) await applyConfig();
  await postJson("/api/start");
}

async function stop() {
  setError("");
  await postJson("/api/stop");
}

function shortSource(source) {
  if (!source) return "";
  const parts = source.split("/");
  return parts.length > 2 ? parts.slice(-2).join("/") : source;
}

function syncCommandPreview() {
  const device = $("input_device").value;
  const channels = Number($("input_channels").value);
  $("input_command").value = `arecord -D ${device} -f S16_LE -r 48000 -c ${channels} -t raw`;
}

function applyDeviceDefaultChannels() {
  const device = $("input_device").value;
  const channels = defaultChannelsByDevice.get(device) || 2;
  $("input_channels").value = channels;
  syncCommandPreview();
}

async function loadDevices() {
  try {
    const res = await fetch("/api/devices");
    const data = await res.json();
    const select = $("input_device");
    const current = select.value;
    select.innerHTML = "";
    defaultChannelsByDevice.clear();
    for (const item of data.devices || []) {
      const opt = document.createElement("option");
      opt.value = item.device;
      opt.textContent = item.label;
      select.appendChild(opt);
      defaultChannelsByDevice.set(item.device, item.default_channels || 2);
    }
    if ([...select.options].some((opt) => opt.value === current)) {
      select.value = current;
    }
    applyDeviceDefaultChannels();
  } catch (e) {
    setError("録音デバイス一覧を取得できませんでした");
  }
}

function setStatus(payload) {
  const el = $("runStatus");
  const status = payload.status || (payload.running ? "running" : "stopped");
  el.textContent = labels[status] || status;
  el.className = "run-pill";
  if (payload.running) el.classList.add("running");
  if (status === "error") el.classList.add("error");
}

function updateRows(recent) {
  const rows = $("rows");
  rows.innerHTML = "";
  if (!recent || recent.length === 0) {
    rows.innerHTML = `<tr class="empty-row"><td colspan="5">受信待ち</td></tr>`;
    return;
  }
  for (const row of recent) {
    const tr = document.createElement("tr");
    tr.innerHTML = `
      <td>${row.time || ""}</td>
      <td>${row.lat || ""}</td>
      <td>${row.lon || ""}</td>
      <td>${row.alt || ""}</td>
      <td>${shortSource(row.source)}</td>
    `;
    rows.appendChild(tr);
  }
}

function update(payload) {
  const cfg = payload.config || {};
  isRunning = Boolean(payload.running);
  setStatus(payload);
  setError(payload.error || "");

  $("decoded").textContent = payload.decoded_count ?? 0;
  $("samples").textContent = payload.total_samples ?? 0;
  $("channel").textContent = `CH${cfg.gps_channel || 2}`;
  $("csvPath").textContent = cfg.output_csv || "";

  for (const [key, val] of Object.entries(cfg)) {
    const el = $(key);
    if (el && document.activeElement !== el) el.value = val;
  }
  if (cfg.input_device && $("input_device").value !== cfg.input_device) {
    const hasDevice = [...$("input_device").options].some((opt) => opt.value === cfg.input_device);
    if (hasDevice) $("input_device").value = cfg.input_device;
  }
  if (document.activeElement !== $("input_command")) syncCommandPreview();
  $("mode").value = "command";

  const latest = payload.latest;
  $("latestTime").textContent = latest?.time || "まだ受信していません";
  $("latestLon").textContent = latest?.lon || "-";
  $("latestLat").textContent = latest?.lat || "-";
  $("latestAlt").textContent = latest?.alt ? `${latest.alt} m` : "-";

  updateRows(payload.recent);

  $("startBtn").textContent = isRunning ? "取得中" : "開始";
  $("startBtn").disabled = isRunning;
  $("saveBtn").disabled = isRunning;
}

function connect() {
  const proto = location.protocol === "https:" ? "wss" : "ws";
  const ws = new WebSocket(`${proto}://${location.host}/ws`);
  ws.onmessage = (event) => update(JSON.parse(event.data));
  ws.onclose = () => setTimeout(connect, 1000);
}

$("saveBtn").addEventListener("click", () => applyConfig().catch((e) => setError(e.message)));
$("startBtn").addEventListener("click", () => start().catch((e) => setError(e.message)));
$("stopBtn").addEventListener("click", () => stop().catch((e) => setError(e.message)));
$("input_device").addEventListener("change", applyDeviceDefaultChannels);
$("input_channels").addEventListener("input", syncCommandPreview);

loadDevices();
connect();
