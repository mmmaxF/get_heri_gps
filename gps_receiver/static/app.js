const $ = (id) => document.getElementById(id);

const labels = {
  running: "取得中",
  stopped: "停止中",
  error: "エラー",
};

let isRunning = false;
const defaultChannelsByDevice = new Map();
let telopConfig = null;
let telopDrag = null;
let pendingConfig = null;
let sampleRate = 48000;

function buildInputCommand(device, channels) {
  return device ? `arecord -D ${device} -f S16_LE -r ${sampleRate} -c ${channels} -t raw` : "";
}

function formConfig() {
  const device = $("input_device").value;
  const channels = Number($("input_channels").value);
  return {
    mode: "command",
    gps_channel: Number($("gps_channel").value),
    input_channels: channels,
    input_device: device,
    input_command: buildInputCommand(device, channels),
    test_capture_dir: $("test_capture_dir").value,
    output_csv: $("output_csv").value,
    reverse_geocoder_url: $("reverse_geocoder_url").value,
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
  pendingConfig = formConfig();
  try {
    const res = await postJson("/api/config", pendingConfig);
    if (res.status) update(res.status);
  } finally {
    pendingConfig = null;
  }
}

async function start() {
  if (!$("input_device").value) {
    setError("入力デバイスを接続してから開始してください");
    return;
  }
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

function placeLabel(geocode) {
  if (!geocode) return "";
  if (geocode.address_label) return geocode.address_label;
  const parts = [geocode.prefecture, geocode.city].filter(Boolean);
  return parts.join("");
}

function syncCommandPreview() {
  const device = $("input_device").value;
  const channels = Number($("input_channels").value);
  $("input_command").value = buildInputCommand(device, channels);
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
    const devices = data.devices || [];
    if (devices.length === 0) {
      const opt = document.createElement("option");
      opt.value = "";
      opt.textContent = "認識された入力デバイスなし";
      select.appendChild(opt);
      select.value = "";
      syncCommandPreview();
      setError("入力デバイスが認識されていません");
      return;
    }
    setError("");
    for (const item of devices) {
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
    rows.innerHTML = `<tr class="empty-row"><td colspan="6">受信待ち</td></tr>`;
    return;
  }
  for (const row of recent) {
    const tr = document.createElement("tr");
    tr.innerHTML = `
      <td>${row.time || ""}</td>
      <td>${row.lat || ""}</td>
      <td>${row.lon || ""}</td>
      <td>${row.alt || ""}</td>
      <td>${placeLabel(row.geocode) || ""}</td>
      <td>${shortSource(row.source)}</td>
    `;
    rows.appendChild(tr);
  }
}

function update(payload) {
  const cfg = pendingConfig || payload.config || {};
  sampleRate = Number(payload.sample_rate || sampleRate || 48000);
  isRunning = Boolean(payload.running);
  setStatus(payload);
  setError(payload.error || "");

  $("decoded").textContent = payload.decoded_count ?? 0;
  $("samples").textContent = payload.total_samples ?? 0;
  $("channel").textContent = `CH${cfg.gps_channel || 2}`;
  $("csvPath").textContent = cfg.output_csv || "";
  const geocodeOk = payload.geocode_success_count ?? 0;
  const geocodeErr = payload.geocode_error_count ?? 0;
  $("geocodeStatus").textContent = geocodeOk > 0 ? `${geocodeOk}件` : geocodeErr > 0 ? "未接続" : "待機中";

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
  $("latestPlace").textContent = placeLabel(latest?.geocode || payload.latest_geocode) || "-";

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

function setTelopError(message) {
  $("telopError").textContent = message || "";
}

function syncKeyMatteValue() {
  $("telop_key_background_opacity_value").textContent = `${$("telop_key_background_opacity").value}%`;
}

function syncVMatteValue() {
  $("telop_background_opacity_value").textContent = `${$("telop_background_opacity").value}%`;
}

async function getJson(url) {
  const res = await fetch(url);
  const data = await res.json();
  if (!res.ok || data.ok === false) throw new Error(data.error || "request failed");
  return data;
}

async function loadTelopDevices() {
  const data = await getJson("/api/telop/output-devices");
  for (const id of ["telop_v_output", "telop_key_output"]) {
    const select = $(id);
    const current = select.value;
    select.innerHTML = "";
    for (const item of data.devices || []) {
      const opt = document.createElement("option");
      opt.value = item.id;
      opt.textContent = item.label;
      select.appendChild(opt);
    }
    if ([...select.options].some((opt) => opt.value === current)) select.value = current;
  }
}

async function loadTelopFonts() {
  const data = await getJson("/api/telop/fonts");
  const select = $("telop_font_family");
  const current = select.value;
  select.innerHTML = "";
  for (const item of data.fonts || []) {
    const opt = document.createElement("option");
    opt.value = item.id;
    opt.textContent = item.source === "custom" ? `${item.label} / 追加` : item.label;
    select.appendChild(opt);
  }
  if ([...select.options].some((opt) => opt.value === current)) select.value = current;
}

function setTelopForm(cfg) {
  telopConfig = cfg;
  $("telop_v_output").value = cfg.v_output || "";
  $("telop_key_output").value = cfg.key_output || "";
  $("telop_resolution").value = `${cfg.format?.width || 1920}x${cfg.format?.height || 1080}`;
  $("telop_frame_rate").value = cfg.format?.frame_rate || "59.94i";
  $("telop_pixel_format").value = cfg.format?.pixel_format || "yuv8";
  $("telop_key_mode").value = cfg.format?.key_mode || "matte";
  if ([...$("telop_font_family").options].some((opt) => opt.value === cfg.font_family)) {
    $("telop_font_family").value = cfg.font_family;
  } else if ($("telop_font_family").options.length > 0) {
    $("telop_font_family").selectedIndex = 0;
  }
  $("telop_text_align").value = cfg.text_align || "center";
  $("telop_font_size").value = cfg.font_size || 72;
  $("telop_text_color").value = cfg.text_color || "#ffffff";
  $("telop_background_color").value = cfg.background_color || "#000000";
  $("telop_background_opacity").value = Math.round((cfg.background_opacity ?? 0.35) * 100);
  $("telop_stroke_color").value = cfg.stroke_color || "#000000";
  $("telop_stroke_width").value = cfg.stroke_width ?? 6;
  $("telop_key_background_opacity").value = Math.round((cfg.key_background_opacity ?? cfg.background_opacity ?? 0.35) * 100);
  syncVMatteValue();
  syncKeyMatteValue();
  updateTelopBox();
}

function telopFormConfig() {
  const [width, height] = $("telop_resolution").value.split("x").map(Number);
  const box = telopConfig?.box || { x: 120, y: 820, width: 900, height: 120, scale: 1 };
  return {
    v_output: $("telop_v_output").value,
    key_output: $("telop_key_output").value,
    format: {
      width,
      height,
      frame_rate: $("telop_frame_rate").value,
      pixel_format: $("telop_pixel_format").value,
      key_mode: $("telop_key_mode").value,
      safe_area: true,
    },
    font_family: $("telop_font_family").value,
    text_align: $("telop_text_align").value,
    font_size: Number($("telop_font_size").value),
    text_color: $("telop_text_color").value,
    background_color: $("telop_background_color").value,
    background_opacity: Number($("telop_background_opacity").value) / 100,
    stroke_color: $("telop_stroke_color").value,
    stroke_width: Number($("telop_stroke_width").value),
    key_background_opacity: Number($("telop_key_background_opacity").value) / 100,
    box,
  };
}

async function loadTelopConfig() {
  const cfg = await getJson("/api/telop/config");
  setTelopForm(cfg);
}

async function applyTelopConfig() {
  setTelopError("");
  const cfg = telopFormConfig();
  const res = await postJson("/api/telop/config", cfg);
  setTelopForm(res.config || cfg);
  refreshTelopPreview();
}

function refreshTelopPreview() {
  const stamp = Date.now();
  $("telopPreviewV").src = `/api/telop/preview/v.png?t=${stamp}`;
  $("telopPreviewKey").src = `/api/telop/preview/key.png?t=${stamp}`;
  updateTelopBox();
}

function stageScale() {
  const cfg = telopConfig || telopFormConfig();
  const rect = $("telopStage").getBoundingClientRect();
  return {
    sx: rect.width / (cfg.format?.width || 1920),
    sy: rect.height / (cfg.format?.height || 1080),
  };
}

function updateTelopBox() {
  if (!telopConfig) return;
  const box = telopConfig.box || {};
  const { sx, sy } = stageScale();
  const el = $("telopBox");
  el.style.left = `${(box.x || 0) * sx}px`;
  el.style.top = `${(box.y || 0) * sy}px`;
  el.style.width = `${(box.width || 100) * sx}px`;
  el.style.height = `${(box.height || 50) * sy}px`;
}

function startTelopDrag(event) {
  if (!telopConfig) return;
  event.preventDefault();
  const rect = $("telopBox").getBoundingClientRect();
  const resize = event.clientX > rect.right - 18 && event.clientY > rect.bottom - 18;
  telopDrag = {
    resize,
    startX: event.clientX,
    startY: event.clientY,
    box: { ...telopConfig.box },
  };
  window.addEventListener("mousemove", moveTelopDrag);
  window.addEventListener("mouseup", stopTelopDrag);
}

function moveTelopDrag(event) {
  if (!telopDrag || !telopConfig) return;
  const { sx, sy } = stageScale();
  const dx = (event.clientX - telopDrag.startX) / sx;
  const dy = (event.clientY - telopDrag.startY) / sy;
  const fmt = telopConfig.format || { width: 1920, height: 1080 };
  const next = { ...telopDrag.box };
  if (telopDrag.resize) {
    next.width = Math.max(80, Math.min(fmt.width - next.x, telopDrag.box.width + dx));
    next.height = Math.max(40, Math.min(fmt.height - next.y, telopDrag.box.height + dy));
  } else {
    next.x = Math.max(0, Math.min(fmt.width - next.width, telopDrag.box.x + dx));
    next.y = Math.max(0, Math.min(fmt.height - next.height, telopDrag.box.y + dy));
  }
  telopConfig.box = next;
  updateTelopBox();
}

async function stopTelopDrag() {
  if (!telopDrag) return;
  telopDrag = null;
  window.removeEventListener("mousemove", moveTelopDrag);
  window.removeEventListener("mouseup", stopTelopDrag);
  await applyTelopConfig().catch((e) => setTelopError(e.message));
}

async function refreshTelopStatus() {
  try {
    const st = await getJson("/api/telop/status");
    $("telopStatus").textContent = st.running ? `出力中: ${st.latest_text || "-"}` : `停止中: ${st.latest_text || "-"}`;
  } catch (e) {
    $("telopStatus").textContent = "未接続";
  }
}

$("saveBtn").addEventListener("click", () => applyConfig().catch((e) => setError(e.message)));
$("startBtn").addEventListener("click", () => start().catch((e) => setError(e.message)));
$("stopBtn").addEventListener("click", () => stop().catch((e) => setError(e.message)));
$("input_device").addEventListener("change", () => {
  applyDeviceDefaultChannels();
  applyConfig().catch((e) => setError(e.message));
});
$("input_channels").addEventListener("input", syncCommandPreview);
$("telopApplyBtn").addEventListener("click", () => applyTelopConfig().catch((e) => setTelopError(e.message)));
$("telopStartBtn").addEventListener("click", async () => {
  try {
    await applyTelopConfig();
    await postJson("/api/telop/start");
    await refreshTelopStatus();
  } catch (e) {
    setTelopError(e.message);
  }
});
$("telopStopBtn").addEventListener("click", async () => {
  try {
    await postJson("/api/telop/stop");
    await refreshTelopStatus();
  } catch (e) {
    setTelopError(e.message);
  }
});
for (const id of ["telop_resolution", "telop_frame_rate", "telop_pixel_format", "telop_key_mode", "telop_font_family", "telop_text_align", "telop_font_size", "telop_text_color", "telop_background_color", "telop_background_opacity", "telop_stroke_color", "telop_stroke_width", "telop_key_background_opacity"]) {
  $(id).addEventListener("change", () => applyTelopConfig().catch((e) => setTelopError(e.message)));
}
$("telop_background_opacity").addEventListener("input", syncVMatteValue);
$("telop_key_background_opacity").addEventListener("input", syncKeyMatteValue);
$("telopBox").addEventListener("mousedown", startTelopDrag);
window.addEventListener("resize", updateTelopBox);

loadDevices();
loadTelopDevices()
  .then(loadTelopFonts)
  .then(loadTelopConfig)
  .then(refreshTelopPreview)
  .then(refreshTelopStatus)
  .catch((e) => setTelopError(e.message));
setInterval(() => {
  refreshTelopPreview();
  refreshTelopStatus();
}, 5000);
connect();
