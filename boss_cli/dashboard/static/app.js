const state = {
  selected: new Set(),
  candidates: [],
  visibleCandidateIds: [],
  templateId: null,
  senderRunning: false,
};

const $ = (id) => document.getElementById(id);

async function api(path, options = {}) {
  const response = await fetch(path, {
    headers: { "Content-Type": "application/json" },
    ...options,
  });
  const payload = await response.json();
  if (!payload.ok) throw new Error(payload.error?.message || "Request failed");
  return payload.data;
}

function post(path, body = {}) {
  return api(path, { method: "POST", body: JSON.stringify(body) });
}

function toast(message) {
  const el = $("toast");
  el.textContent = message;
  el.classList.add("visible");
  window.clearTimeout(toast._timer);
  toast._timer = window.setTimeout(() => el.classList.remove("visible"), 3600);
}

function escapeHtml(value) {
  return String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#039;");
}

function statusPill(value) {
  const text = value || "none";
  return `<span class="status ${escapeHtml(text)}">${escapeHtml(text.replaceAll("_", " "))}</span>`;
}

function actionLabel(type) {
  if (type === "exchange_wechat") return "Request WeChat";
  if (type === "send_message" || type === "send_template") return "Send message";
  return type || "Unknown";
}

async function refreshAll() {
  const [health, candidates, queue, events, templates] = await Promise.all([
    api("/api/health"),
    api("/api/candidates"),
    api("/api/queue"),
    api("/api/events"),
    api("/api/templates"),
  ]);
  const available = new Set(candidates.map((candidate) => Number(candidate.id)));
  state.selected = new Set(Array.from(state.selected).filter((id) => available.has(id)));
  renderHealth(health);
  renderCandidates(candidates);
  renderQueue(queue);
  renderEvents(events);
  if (!state.templateId && templates[0]) loadTemplate(templates[0]);
}

function loadTemplate(template) {
  state.templateId = Number(template.id);
  $("templateId").textContent = String(template.id);
  $("templateName").value = template.name || "dashboard_message";
  $("messageBody").value = template.body || "";
}

function renderHealth(health) {
  state.senderRunning = Boolean(health.sender?.running);
  $("dbPath").textContent = health.db || "Local workflow database";
  $("accountMetric").textContent = health.active_account_id || "none";
  $("pausedMetric").textContent = health.paused ? "Yes" : "No";
  $("runtimeState").textContent = state.senderRunning ? "Queue running" : "Idle";
  $("runtimeState").classList.toggle("running", state.senderRunning);
  $("candidateMetric").textContent = health.candidate_count || 0;
  $("queuedMetric").textContent = health.queue?.queued || 0;
  $("verifiedMetric").textContent = health.queue?.verified || 0;
  $("failedMetric").textContent = (health.queue?.failed_retryable || 0) + (health.queue?.failed_terminal || 0);
  $("startSenderBtn").disabled = state.senderRunning;
}

function filteredCandidates(candidates) {
  const filter = $("candidateFilter").value.trim().toLowerCase();
  if (!filter) return candidates;
  return candidates.filter((candidate) => [
    candidate.name_redacted,
    candidate.job_name,
    candidate.last_message_preview,
    candidate.current_stage,
    candidate.latest_action_status,
  ].join(" ").toLowerCase().includes(filter));
}

function renderCandidates(candidates) {
  state.candidates = candidates;
  const rows = filteredCandidates(candidates);
  state.visibleCandidateIds = rows
    .filter((candidate) => !candidate.do_not_contact && !candidate.inactive_at)
    .map((candidate) => Number(candidate.id));
  $("candidateRows").innerHTML = rows.map((candidate) => {
    const id = Number(candidate.id);
    const disabled = candidate.do_not_contact || candidate.inactive_at;
    const checked = state.selected.has(id) ? "checked" : "";
    return `
      <tr class="${disabled ? "disabled-row" : ""}">
        <td><input type="checkbox" data-candidate-id="${id}" ${checked} ${disabled ? "disabled" : ""}></td>
        <td><strong>${escapeHtml(candidate.name_redacted || "-")}</strong><div class="muted">#${id}</div></td>
        <td>${escapeHtml(candidate.job_name || "-")}</td>
        <td>${escapeHtml(candidate.do_not_contact ? "do not contact" : candidate.current_stage || "-")}</td>
        <td class="message-cell">${escapeHtml(candidate.last_message_preview || "-")}</td>
        <td>${statusPill(candidate.latest_action_status || "none")}</td>
      </tr>`;
  }).join("");
  $("selectionCount").textContent = String(state.selected.size);
}

function renderQueue(queue) {
  let messages = 0;
  let wechat = 0;
  $("queueRows").innerHTML = queue.map((item) => {
    if (item.action_type === "exchange_wechat") wechat += 1;
    else messages += 1;
    return `
      <tr>
        <td>${statusPill(item.status)}</td>
        <td><strong>${escapeHtml(actionLabel(item.action_type))}</strong></td>
        <td>${escapeHtml(item.name_redacted || "-")}<div class="muted">#${escapeHtml(item.candidate_id)}</div></td>
        <td>${item.template_name ? `${escapeHtml(item.template_name)}:${escapeHtml(item.template_version || "")}` : "-"}</td>
        <td>${escapeHtml(item.attempts || 0)}</td>
        <td>${escapeHtml(item.last_error_code || "")}</td>
      </tr>`;
  }).join("");
  $("messageActionMetric").textContent = String(messages);
  $("wechatActionMetric").textContent = String(wechat);
}

function renderEvents(events) {
  $("eventRows").innerHTML = events.slice(0, 80).map((event) => `
    <li>
      <div><strong>${escapeHtml(event.event_type)}</strong><time>${escapeHtml(event.created_at)}</time></div>
      <p>${escapeHtml(event.summary)}</p>
    </li>`).join("");
}

async function runAction(label, fn) {
  try {
    const result = await fn();
    toast(`${label} completed`);
    await refreshAll();
    return result;
  } catch (error) {
    toast(`${label} failed: ${error.message}`);
    throw error;
  }
}

function selectedActions() {
  return {
    sendMessage: $("sendMessageAction").checked,
    requestWechat: $("requestWechatAction").checked,
  };
}

function updateActionFields() {
  $("messageFields").hidden = !$("sendMessageAction").checked;
}

$("refreshBtn").addEventListener("click", () => runAction("Refresh", refreshAll));
$("syncBtn").addEventListener("click", () => runAction("Sync", () => post("/api/sync", {
  limit: Number($("syncLimit").value || 100),
  label_id: Number($("syncLabel").value || 0),
  enc_job_id: $("syncJob").value.trim(),
  history_budget: Number($("historyBudget").value || 20),
})));
$("pauseBtn").addEventListener("click", () => runAction("Pause", () => post("/api/sender/pause", { reason: "operator" })));
$("resumeBtn").addEventListener("click", () => runAction("Resume", () => post("/api/sender/resume")));
$("stopBtn").addEventListener("click", () => runAction("Stop", () => post("/api/sender/stop")));
$("startSenderBtn").addEventListener("click", () => {
  const maxActions = Number($("maxActions").value || 10);
  if (!window.confirm(`Run up to ${maxActions} queued actions now?`)) return;
  runAction("Queue run", () => post("/api/sender/start", {
    confirmed: true,
    max_actions: maxActions,
    delay_seconds: Number($("delaySeconds").value || 60),
    engine: "camoufox",
  }));
});
$("saveTemplateBtn").addEventListener("click", () => runAction("Message approval", async () => {
  const template = await post("/api/templates", {
    name: $("templateName").value.trim() || "dashboard_message",
    body: $("messageBody").value,
    approved: true,
  });
  loadTemplate(template);
  return template;
}));
$("enqueueBtn").addEventListener("click", () => {
  const actions = selectedActions();
  if (!actions.sendMessage && !actions.requestWechat) return toast("Select at least one action");
  if (actions.sendMessage && !state.templateId) return toast("Approve a message first");
  if (!state.selected.size) return toast("Select candidates first");
  const names = [actions.sendMessage ? "message" : "", actions.requestWechat ? "WeChat request" : ""].filter(Boolean).join(" + ");
  if (!window.confirm(`Queue ${names} for ${state.selected.size} candidates?`)) return;
  runAction("Batch queue", () => post("/api/enqueue", {
    confirmed: true,
    candidate_ids: Array.from(state.selected),
    template_id: actions.sendMessage ? state.templateId : null,
    send_message: actions.sendMessage,
    request_wechat: actions.requestWechat,
  }));
});
$("selectVisibleBtn").addEventListener("click", () => {
  for (const id of state.visibleCandidateIds) state.selected.add(id);
  renderCandidates(state.candidates);
});
$("clearSelectionBtn").addEventListener("click", () => {
  state.selected.clear();
  renderCandidates(state.candidates);
});
$("candidateFilter").addEventListener("input", () => renderCandidates(state.candidates));
$("candidateRows").addEventListener("change", (event) => {
  const id = Number(event.target.dataset.candidateId);
  if (!id) return;
  if (event.target.checked) state.selected.add(id);
  else state.selected.delete(id);
  $("selectionCount").textContent = String(state.selected.size);
});
$("sendMessageAction").addEventListener("change", updateActionFields);

updateActionFields();
refreshAll().catch((error) => toast(`Dashboard load failed: ${error.message}`));
window.setInterval(() => {
  if (state.senderRunning) refreshAll().catch(() => {});
}, 5000);
