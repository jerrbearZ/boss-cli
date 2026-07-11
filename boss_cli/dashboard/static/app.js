const state = {
  selected: new Set(),
  candidates: [],
  templateId: null,
};

const $ = (id) => document.getElementById(id);

async function api(path, options = {}) {
  const response = await fetch(path, {
    headers: { "Content-Type": "application/json" },
    ...options,
  });
  const payload = await response.json();
  if (!payload.ok) {
    throw new Error(payload.error?.message || "Request failed");
  }
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
  toast._timer = window.setTimeout(() => el.classList.remove("visible"), 3200);
}

function statusPill(value) {
  const text = value || "none";
  return `<span class="status ${escapeHtml(text)}">${escapeHtml(text)}</span>`;
}

function escapeHtml(value) {
  return String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#039;");
}

async function refreshAll() {
  const [health, candidates, queue, events, templates] = await Promise.all([
    api("/api/health"),
    api("/api/candidates"),
    api("/api/queue"),
    api("/api/events"),
    api("/api/templates"),
  ]);
  renderHealth(health);
  renderCandidates(candidates);
  renderQueue(queue);
  renderEvents(events);
  if (!state.templateId && templates[0]) {
    state.templateId = templates[0].id;
    $("templateId").textContent = String(state.templateId);
    $("templateName").value = templates[0].name || "dashboard_message";
    $("messageBody").value = templates[0].body || "";
  }
}

function renderHealth(health) {
  $("dbPath").textContent = health.db || "Local workflow control";
  $("pausedMetric").textContent = health.paused ? "yes" : "no";
  $("senderMetric").textContent = health.sender?.running ? "running" : "idle";
  $("candidateMetric").textContent = health.candidate_count || 0;
  $("queuedMetric").textContent = health.queue?.queued || 0;
  $("verifiedMetric").textContent = health.queue?.verified || 0;
  $("failedMetric").textContent = (health.queue?.failed_retryable || 0) + (health.queue?.failed_terminal || 0);
}

function renderCandidates(candidates) {
  state.candidates = candidates;
  const filter = $("candidateFilter").value.trim().toLowerCase();
  const rows = candidates.filter((candidate) => {
    if (!filter) return true;
    return [
      candidate.name_redacted,
      candidate.job_name,
      candidate.last_message_preview,
      candidate.current_stage,
      candidate.latest_action_status,
    ].join(" ").toLowerCase().includes(filter);
  });
  $("candidateRows").innerHTML = rows.map((candidate) => {
    const id = Number(candidate.id);
    const checked = state.selected.has(id) ? "checked" : "";
    return `
      <tr>
        <td><input type="checkbox" data-candidate-id="${id}" ${checked}></td>
        <td>${escapeHtml(candidate.name_redacted || "-")}<div class="muted">#${id}</div></td>
        <td>${escapeHtml(candidate.job_name || "-")}</td>
        <td>${escapeHtml(candidate.current_stage || "-")}</td>
        <td>${escapeHtml(candidate.last_message_preview || "-")}</td>
        <td>${statusPill(candidate.latest_action_status || "none")}</td>
      </tr>
    `;
  }).join("");
  $("selectionCount").textContent = String(state.selected.size);
}

function renderQueue(queue) {
  $("queueRows").innerHTML = queue.map((item) => `
    <tr>
      <td>${statusPill(item.status)}</td>
      <td>${escapeHtml(item.name_redacted || "-")}<div class="muted">candidate ${escapeHtml(item.candidate_id)}</div></td>
      <td>${escapeHtml(item.template_name || "-")}:${escapeHtml(item.template_version || "")}</td>
      <td>${escapeHtml(item.attempts || 0)}</td>
      <td>${escapeHtml(item.last_error_code || "")}</td>
    </tr>
  `).join("");
}

function renderEvents(events) {
  $("eventRows").innerHTML = events.slice(0, 80).map((event) => `
    <li>
      <strong>${escapeHtml(event.event_type)}</strong>
      <span class="muted">${escapeHtml(event.created_at)}</span>
      <div>${escapeHtml(event.summary)}</div>
    </li>
  `).join("");
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

$("refreshBtn").addEventListener("click", () => runAction("Refresh", refreshAll));
$("syncBtn").addEventListener("click", () => runAction("Sync", () => post("/api/sync", {
  limit: Number($("syncLimit").value || 100),
  label_id: Number($("syncLabel").value || 0),
  enc_job_id: $("syncJob").value.trim(),
})));
$("pauseBtn").addEventListener("click", () => runAction("Pause", () => post("/api/sender/pause", { reason: "operator" })));
$("resumeBtn").addEventListener("click", () => runAction("Resume", () => post("/api/sender/resume")));
$("stopBtn").addEventListener("click", () => runAction("Stop", () => post("/api/sender/stop")));
$("startSenderBtn").addEventListener("click", () => runAction("Start sender", () => post("/api/sender/start", {
  max_actions: Number($("maxActions").value || 5),
  delay_seconds: Number($("delaySeconds").value || 60),
  engine: "camoufox",
})));
$("saveTemplateBtn").addEventListener("click", () => runAction("Approve template", async () => {
  const template = await post("/api/templates", {
    name: $("templateName").value.trim() || "dashboard_message",
    body: $("messageBody").value,
    approved: true,
  });
  state.templateId = template.id;
  $("templateId").textContent = String(template.id);
  return template;
}));
$("enqueueBtn").addEventListener("click", () => runAction("Enqueue", () => {
  if (!state.templateId) throw new Error("Approve a template first");
  if (!state.selected.size) throw new Error("Select candidates first");
  return post("/api/enqueue", {
    candidate_ids: Array.from(state.selected),
    template_id: state.templateId,
  });
}));
$("selectVisibleBtn").addEventListener("click", () => {
  for (const candidate of state.candidates) state.selected.add(Number(candidate.id));
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

refreshAll();
