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
    if (["最新ログ", "CSV", "マルチビューアー", "E2Eヘルス", "E2E通知"].includes(label)) {
      group.classList.add("wide-detail");
    }
    term.textContent = label;
    description.textContent = value == null || value === "" ? "-" : String(value);
    group.append(term, description);
    details.append(group);
  }
}

function healthReasonLabel(reason) {
  return {
    png_output_dir_writable: "PNG出力先に書き込めない",
    latest_image_exists: "最新PNGがない",
    atem_enabled: "ATEM送信が無効",
    atem_host_configured: "ATEM IP未設定",
    upload_worker_alive: "送信ワーカー停止",
    upload_worker_not_stuck: "送信ワーカー詰まり",
    last_atem_send_success: "ATEM送信成功履歴なし",
    last_success_recent: "直近の送信成功が古い",
    png_generation: "PNG生成失敗",
  }[reason] || reason;
}

async function refreshSystemStatus() {
  try {
    const response = await fetch("/api/system/status");
    const status = await response.json();
    const e2e = status.e2e_health || {};
    const capture = status.capture_agent || {};
    const receiver = status.gps_receiver || {};
    const geocoder = status.reverse_geocoder || {};
    const atem = status.atem_output || {};
    const atemLatest = atem.latest || {};
    const atemSuper = atem.super_health || {};
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
    setServiceStatus(
      "atemServiceStatus",
      atem.ok,
      atem.ok
        ? atem.atem_enabled
          ? atemLatest.atem_sent
            ? "ATEM送信済み"
            : "PNG生成中・ATEM待機"
          : "PNG生成のみ"
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
      ["E2Eヘルス", e2e.ok ? `OK・${e2e.address || "-"}・${e2e.checked_at || ""}` : `NG・${e2e.error || e2e.status || "未確認"}`],
      ["E2E通知", e2e.notification?.enabled === false
        ? "無効"
        : e2e.notification?.configured
          ? `有効${e2e.notification?.last_sent_at ? `・最終通知 ${e2e.notification.last_sent_at}` : ""}${e2e.notification?.last_error ? `・通知エラー ${e2e.notification.last_error}` : ""}`
          : "SMTP未設定"],
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
    const atemText = atemLatest.error
      ? `エラー: ${atemLatest.error}`
      : atemLatest.skipped
        ? `未送信: ${atemLatest.reason || ""}`
        : atemLatest.sent
          ? "送信成功"
          : "待機中";
    const atemSuperText = atemSuper.ready
      ? atemSuper.visible_now
        ? "OK・現在スーパー表示中"
        : "OK・表示クリア中/空文字"
      : `NG: ${(atemSuper.reasons || []).map(healthReasonLabel).join(", ") || atemSuper.latest_error || "不明"}`;
    const atemProbe = atemSuper.active_probe || {};
    const atemProbeText = atemProbe.enabled === false
      ? "無効"
      : atemProbe.last_success_at
        ? `有効・最終成功 ${atemProbe.last_success_at}`
        : atemProbe.last_attempt_at
          ? `有効・最終試行 ${atemProbe.last_attempt_at}${atemProbe.last_error ? ` / ${atemProbe.last_error}` : ""}`
          : "有効・成功待ち";
    renderServiceDetails("atemServiceDetails", [
      ["ATEM接続", atem.atem_enabled ? `${atem.atem_host || "-"}（有効）` : "無効・PNG生成のみ"],
      ["スーパー可否", atemSuperText],
      ["自動ヘルス送信", atemProbeText],
      ["最新テキスト", atemLatest.text],
      ["更新時刻", atemLatest.updated_at],
      ["PNG", atem.image_exists ? "生成済み" : "未生成"],
      ["Preview", atem.image_exists ? "http://127.0.0.1:8030/api/preview.png" : "-"],
      ["ATEM送信", atemText],
    ]);
    $("systemCheckedAt").textContent = `最終確認 ${formatClock(new Date())}`;
  } catch (error) {
    $("systemCheckedAt").textContent = "状態を取得できません";
  }
}

async function restartContainer(containerName, button) {
  const status = $(`restartStatus-${containerName}`);
  const label = {
    "get-heri-gps": "get-heri-gps（UI/API本体）",
    "reverse-geocoder": "reverse-geocoder（地名変換）",
    "atem-output": "atem-output（ATEMテロップ送信）",
  }[containerName] || containerName;
  if (!confirm(`${label} コンテナを再起動します。一時的に処理が止まります。実行しますか？`)) {
    return;
  }
  button.disabled = true;
  status.textContent = "再起動中...";
  try {
    const response = await fetch(`/api/system/containers/${encodeURIComponent(containerName)}/restart`, {method: "POST"});
    const data = await response.json();
    if (!response.ok || data.ok === false) {
      throw new Error(data.error || "再起動に失敗しました");
    }
    status.textContent = "再起動を開始しました";
    setTimeout(refreshSystemStatus, 2500);
  } catch (error) {
    if (containerName === "get-heri-gps") {
      status.textContent = "再起動要求を送信しました";
      setTimeout(refreshSystemStatus, 4000);
    } else {
      status.textContent = error.message;
    }
  } finally {
    setTimeout(() => {
      button.disabled = false;
    }, 5000);
  }
}

async function runE2eHealthNow() {
  const button = $("e2eHealthBtn");
  const status = $("e2eHealthStatus");
  button.disabled = true;
  status.textContent = "確認中...";
  try {
    const response = await fetch("/api/system/health/e2e", {method: "POST"});
    const data = await response.json();
    if (!response.ok || data.ok === false) {
      throw new Error(data.error || "E2EヘルスNG");
    }
    status.textContent = `OK・${data.address || "-"}`;
    setTimeout(refreshSystemStatus, 500);
  } catch (error) {
    status.textContent = error.message;
    setTimeout(refreshSystemStatus, 500);
  } finally {
    setTimeout(() => {
      button.disabled = false;
    }, 3000);
  }
}

async function sendAtemFreeText(clearDisplay = false) {
  const input = $("atemFreeTextInput");
  const status = $("atemFreeTextStatus");
  const sendButton = $("atemFreeTextSendBtn");
  const clearButton = $("atemFreeTextClearBtn");
  const text = clearDisplay ? "" : input.value;
  if (!clearDisplay && !text.trim()) {
    status.textContent = "送信する文字を入力してください";
    return;
  }
  const confirmText = clearDisplay
    ? "ATEMスーパーをクリアします。実行しますか？"
    : `以下の文字をATEMへスーパーします。\n\n${text}\n\n実行しますか？`;
  if (!confirm(confirmText)) return;
  sendButton.disabled = true;
  clearButton.disabled = true;
  status.textContent = clearDisplay ? "クリア送信中..." : "送信中...";
  try {
    const response = await fetch("/api/system/atem/free-text", {
      method: "POST",
      headers: {"Content-Type": "application/json"},
      body: JSON.stringify({text, clear_display: clearDisplay}),
    });
    const data = await response.json();
    if (!response.ok || data.ok === false) {
      throw new Error(data.error || "ATEM送信に失敗しました");
    }
    status.textContent = data.upload_queued
      ? `送信待ちに入れました: ${data.text || "クリア"}`
      : data.sent
        ? `送信しました: ${data.text || "クリア"}`
        : `受付しました: ${data.text || "クリア"}`;
    setTimeout(refreshSystemStatus, 1500);
  } catch (error) {
    status.textContent = error.message;
  } finally {
    setTimeout(() => {
      sendButton.disabled = false;
      clearButton.disabled = false;
    }, 3000);
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
  const atem = (payload.latest?.geocode?.outputs || payload.latest_geocode?.outputs || [])
    .find((item) => item.name === "atem");
  if (atem?.sent) {
    $("atemStatus").textContent = `送信OK: ${atem.text || ""}`;
  } else if (atem?.error) {
    $("atemStatus").textContent = "送信エラー";
  } else if (atem?.skipped) {
    $("atemStatus").textContent = atem.reason === "ATEM_ENABLED=0" ? "PNG生成のみ" : "未送信";
  } else if (atem?.queued) {
    $("atemStatus").textContent = "送信待ち";
  } else {
    $("atemStatus").textContent = "待機中";
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
for (const button of document.querySelectorAll(".container-restart-btn")) {
  button.addEventListener("click", () => restartContainer(button.dataset.container, button));
}
$("e2eHealthBtn").addEventListener("click", runE2eHealthNow);
$("atemFreeTextSendBtn").addEventListener("click", () => sendAtemFreeText(false));
$("atemFreeTextClearBtn").addEventListener("click", () => sendAtemFreeText(true));

updateClock();
setInterval(updateClock, 1000);
connect();
setInterval(refreshAgentStatus, 2000);
refreshSystemStatus();
setInterval(refreshSystemStatus, 3000);
