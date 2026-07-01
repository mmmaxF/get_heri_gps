const $ = (id) => document.getElementById(id);

const labels = {
  running: "取得中",
  stopped: "停止中",
  error: "エラー",
};

let captureAgentInitialized = false;

async function agentRequest(path, options = {}) {
  const proxyPath = path.replace(/^\/api/, "/api/capture-agent");
  const response = await fetch(proxyPath, options);
  const data = await response.json();
  if (!response.ok || data.ok === false) {
    throw new Error(data.error || "capture-agentの操作に失敗しました");
  }
  return data;
}

function rebuildAgentGpsChannels(selected = 4) {
  const channels = Number($("agentChannels").value || 4);
  const select = $("agentGpsChannel");
  select.innerHTML = "";
  for (let channel = 1; channel <= channels; channel += 1) {
    const option = document.createElement("option");
    option.value = String(channel);
    option.textContent = `CH${channel}`;
    select.appendChild(option);
  }
  select.value = String(Math.min(Number(selected || 4), channels));
}

async function loadAgentDevices(selected = "") {
  const data = await agentRequest("/api/devices");
  const select = $("agentDevice");
  const configured = selected || select.value;
  select.innerHTML = "";
  for (const item of data.devices || []) {
    const option = document.createElement("option");
    option.value = item.device;
    option.textContent = item.label;
    select.appendChild(option);
  }
  if (configured && ![...select.options].some((option) => option.value === configured)) {
    const option = document.createElement("option");
    option.value = configured;
    option.textContent = `${configured}（現在の設定）`;
    select.appendChild(option);
  }
  if (configured) select.value = configured;
  if (select.options.length === 0) {
    const option = document.createElement("option");
    option.value = "";
    option.textContent = "認識された音声デバイスなし";
    select.appendChild(option);
  }
}

function agentFormConfig() {
  return {
    CAPTURE_DEVICE: $("agentDevice").value,
    INPUT_CHANNELS: $("agentChannels").value,
    GPS_CHANNEL: $("agentGpsChannel").value,
    GPS_RECEIVER_HOST: $("agentReceiverHost").value,
    GPS_RECEIVER_PCM_PORT: $("agentReceiverPort").value,
    SAMPLE_RATE: $("agentSampleRate").value,
    SAMPLE_FORMAT: "S16_LE",
  };
}

function updateAgent(status) {
  const config = status.config || {};
  const running = Boolean(status.running);
  const destination = `${config.GPS_RECEIVER_HOST || "-"}:${config.GPS_RECEIVER_PCM_PORT || "-"}`;
  $("agentStatus").textContent = running ? `音声送信中 → ${destination}` : `停止中（送信先 ${destination}）`;
  $("agentStartBtn").disabled = running;
  $("agentStopBtn").disabled = !running;
  $("agentSaveBtn").disabled = running;
  $("agentRefreshBtn").disabled = running;
  for (const id of [
    "agentDevice",
    "agentChannels",
    "agentGpsChannel",
    "agentReceiverHost",
    "agentReceiverPort",
    "agentSampleRate",
  ]) {
    $(id).disabled = running;
  }
  const logs = status.logs || [];
  $("agentLog").textContent = logs.length ? logs[logs.length - 1] : "";
  if (!captureAgentInitialized) {
    $("agentChannels").value = config.INPUT_CHANNELS || "4";
    rebuildAgentGpsChannels(config.GPS_CHANNEL || 4);
    $("agentReceiverHost").value = config.GPS_RECEIVER_HOST || "127.0.0.1";
    $("agentReceiverPort").value = config.GPS_RECEIVER_PCM_PORT || "9010";
    $("agentSampleRate").value = config.SAMPLE_RATE || "48000";
    loadAgentDevices(config.CAPTURE_DEVICE || "").catch((error) => {
      $("agentStatus").textContent = error.message;
    });
    captureAgentInitialized = true;
  }
}

async function refreshAgentStatus() {
  try {
    updateAgent(await agentRequest("/api/status"));
  } catch (error) {
    $("agentStatus").textContent = "制御APIに接続できません";
    $("agentLog").textContent = error.message;
  }
}

function setServiceStatus(id, ok, text) {
  const element = $(id);
  element.textContent = text;
  element.className = ok ? "ok" : "error";
}

function renderServiceDetails(id, entries) {
  const details = $(id);
  details.innerHTML = "";
  for (const [label, value] of entries) {
    const group = document.createElement("div");
    const term = document.createElement("dt");
    const description = document.createElement("dd");
    if (["最新ログ", "CSV", "マルチビューアー"].includes(label)) {
      group.classList.add("wide-detail");
    }
    term.textContent = label;
    description.textContent = value == null || value === "" ? "-" : String(value);
    group.append(term, description);
    details.append(group);
  }
}

async function refreshSystemStatus() {
  try {
    const response = await fetch("/api/system/status");
    const status = await response.json();
    const capture = status.capture_agent || {};
    const receiver = status.gps_receiver || {};
    const geocoder = status.reverse_geocoder || {};
    setServiceStatus(
      "captureServiceStatus",
      capture.ok && capture.running,
      !capture.ok ? "制御APIに接続できません" : capture.running ? "音声送信中" : "停止中",
    );
    setServiceStatus(
      "receiverServiceStatus",
      receiver.ok,
      receiver.ok ? `稼働中・${receiver.input_status || "待機中"}` : "停止・異常",
    );
    setServiceStatus(
      "geocoderServiceStatus",
      geocoder.ok && geocoder.db_loaded,
      geocoder.ok && geocoder.db_loaded
        ? `稼働中・行政区域 ${Number(geocoder.area_count || 0).toLocaleString("ja-JP")}件`
        : "接続できません",
    );
    renderServiceDetails("captureServiceDetails", [
      ["入力", capture.input?.device],
      ["PCM", `${capture.input?.sample_format || "-"} / ${capture.input?.sample_rate || "-"} Hz`],
      ["チャンネル", `${capture.input?.channels || "-"}ch（GPS CH${capture.input?.gps_channel || "-"}）`],
      ["出力先", `${capture.output?.host || "-"}:${capture.output?.port || "-"}`],
      ["PID", capture.pid],
      ["最新ログ", capture.output?.last_log],
    ]);
    renderServiceDetails("receiverServiceDetails", [
      ["入力元", receiver.input?.client],
      ["入力形式", `${receiver.input?.sample_rate || "-"} Hz / ${receiver.input?.channels || "-"}ch`],
      ["対象", `GPS CH${receiver.input?.gps_channel || "-"}`],
      ["受信サンプル", Number(receiver.input?.total_samples || 0).toLocaleString("ja-JP")],
      ["GPS復調", `${Number(receiver.output?.decoded_count || 0).toLocaleString("ja-JP")}件`],
      ["最新座標", receiver.output?.lat && receiver.output?.lon ? `${receiver.output.lat}, ${receiver.output.lon}` : "-"],
      ["高度", receiver.output?.alt === "" ? "-" : `${receiver.output?.alt} m`],
      ["最新時刻", receiver.output?.latest_time],
      ["地名変換待ち", `${receiver.output?.geocode_queue || 0}件`],
      ["CSV", receiver.output?.csv],
    ]);
    const multiviewerText = geocoder.output?.multiviewer_sent
      ? "送信成功"
      : geocoder.output?.multiviewer_error
        ? `送信失敗: ${geocoder.output.multiviewer_error}`
        : "待機中";
    renderServiceDetails("geocoderServiceDetails", [
      ["入力時刻", geocoder.input?.time],
      ["入力座標", geocoder.input?.lat && geocoder.input?.lon ? `${geocoder.input.lat}, ${geocoder.input.lon}` : "-"],
      ["地名出力", geocoder.output?.address],
      ["行政コード", geocoder.output?.admin_code],
      ["区域DB", `${Number(geocoder.area_count || 0).toLocaleString("ja-JP")}件`],
      ["マルチビューアー", multiviewerText],
    ]);
    $("systemCheckedAt").textContent = `最終確認 ${formatClock(new Date())}`;
  } catch (error) {
    $("systemCheckedAt").textContent = "状態を取得できません";
  }
}

async function saveAgentConfig() {
  const data = await agentRequest("/api/config", {
    method: "POST",
    headers: {"Content-Type": "application/json"},
    body: JSON.stringify(agentFormConfig()),
  });
  updateAgent(data.status);
  return data;
}

function setError(message) {
  $("errorText").textContent = message || "";
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

function formatClock(date) {
  const parts = new Intl.DateTimeFormat("ja-JP", {
    timeZone: "Asia/Tokyo",
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
    hour12: false,
  }).formatToParts(date);
  const values = {};
  for (const part of parts) values[part.type] = part.value;
  return `${values.year}/${values.month}/${values.day} ${values.hour}:${values.minute}:${values.second}`;
}

function updateClock() {
  const el = $("currentClock");
  if (el) el.textContent = `現在時刻 ${formatClock(new Date())}`;
}

function setStatus(payload) {
  const el = $("runStatus");
  const status = payload.status || (payload.running ? "running" : "stopped");
  el.textContent = labels[status] || status;
  el.className = "run-pill";
  if (payload.running) el.classList.add("running");
  if (status === "error") el.classList.add("error");
}

function inputStatusLabel(payload) {
  const cfg = payload.config || {};
  const status = payload.input_status || "stopped";
  if (!payload.running) return "停止中";
  if (payload.socket_connected) return `接続中: ${payload.socket_client || ""}`;
  if (status === "waiting") return `Socket待受中 :${cfg.pcm_socket_port || 9010}`;
  if (status === "connected") return "入力中";
  if (status === "waiting") return "待機中";
  if (status === "error") return "入力エラー";
  return labels[status] || status;
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
  const cfg = payload.config || {};
  setStatus(payload);
  setError(payload.error || "");

  $("decoded").textContent = payload.decoded_count ?? 0;
  $("samples").textContent = payload.total_samples ?? 0;
  $("channel").textContent = `CH${cfg.gps_channel || 4}`;
  $("inputStatus").textContent = inputStatusLabel(payload);
  $("csvPath").textContent = cfg.output_csv || "";
  const geocodeOk = payload.geocode_success_count ?? 0;
  const geocodeErr = payload.geocode_error_count ?? 0;
  const queueSize = payload.geocode_queue_size ?? 0;
  $("geocodeStatus").textContent = geocodeOk > 0 ? `${geocodeOk}件 / 待ち${queueSize}` : geocodeErr > 0 ? `エラー / 待ち${queueSize}` : `待機中 / 待ち${queueSize}`;
  const mv = payload.latest?.geocode?.multiviewer || payload.latest_geocode?.multiviewer;
  if (mv?.sent) {
    $("multiviewerStatus").textContent = `送信OK: ${mv.text || ""}`;
  } else if (mv?.error) {
    $("multiviewerStatus").textContent = "送信エラー";
  } else if (mv?.skipped) {
    $("multiviewerStatus").textContent = `未送信: ${mv.reason || ""}`;
  } else {
    $("multiviewerStatus").textContent = "待機中";
  }

  const latest = payload.latest;
  $("latestTime").textContent = latest?.time || "まだ受信していません";
  $("latestLon").textContent = latest?.lon || "-";
  $("latestLat").textContent = latest?.lat || "-";
  $("latestAlt").textContent = latest?.alt ? `${latest.alt} m` : "-";
  $("latestPlace").textContent = placeLabel(latest?.geocode || payload.latest_geocode) || "-";

  updateRows(payload.recent);

}

function connect() {
  const proto = location.protocol === "https:" ? "wss" : "ws";
  const ws = new WebSocket(`${proto}://${location.host}/ws`);
  ws.onmessage = (event) => update(JSON.parse(event.data));
  ws.onclose = () => setTimeout(connect, 1000);
}

$("agentChannels").addEventListener("change", () => {
  rebuildAgentGpsChannels($("agentGpsChannel").value);
});
$("agentRefreshBtn").addEventListener("click", () => {
  loadAgentDevices().catch((error) => {
    $("agentStatus").textContent = error.message;
  });
});
$("agentSaveBtn").addEventListener("click", () => {
  saveAgentConfig().catch((error) => {
    $("agentStatus").textContent = error.message;
  });
});
$("agentStartBtn").addEventListener("click", async () => {
  try {
    await saveAgentConfig();
    const data = await agentRequest("/api/start", {method: "POST"});
    updateAgent(data.status);
  } catch (error) {
    $("agentStatus").textContent = error.message;
  }
});
$("agentStopBtn").addEventListener("click", async () => {
  try {
    const data = await agentRequest("/api/stop", {method: "POST"});
    updateAgent(data.status);
  } catch (error) {
    $("agentStatus").textContent = error.message;
  }
});

updateClock();
setInterval(updateClock, 1000);
connect();
setInterval(refreshAgentStatus, 1000);
refreshSystemStatus();
setInterval(refreshSystemStatus, 3000);
