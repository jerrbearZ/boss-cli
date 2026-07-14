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

function escapeHtml(value) {
  return String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#039;");
}

function toast(message) {
  const element = $("toast");
  element.textContent = message;
  element.classList.add("visible");
  window.clearTimeout(toast.timer);
  toast.timer = window.setTimeout(() => element.classList.remove("visible"), 3500);
}

function pill(value) {
  const status = String(value || "unknown");
  return `<span class="pill ${escapeHtml(status)}">${escapeHtml(status.replaceAll("_", " "))}</span>`;
}

function displayTime(value) {
  if (!value) return "-";
  const date = new Date(value);
  return Number.isNaN(date.valueOf()) ? String(value) : date.toLocaleString();
}

function daemonDisplay(daemon) {
  if (!daemon) return { label: "Not started", state: "stopped" };
  if (daemon.status !== "running") return { label: "Stopped", state: "stopped" };
  const heartbeat = Date.parse(daemon.heartbeat_at || "");
  if (!Number.isFinite(heartbeat) || Date.now() - heartbeat > 5 * 60 * 1000) {
    return { label: "Heartbeat stale", state: "stale" };
  }
  return { label: "Running", state: "running" };
}

function renderHealth(health, queue) {
  const daemon = health.daemon;
  const display = daemonDisplay(daemon);
  const badge = $("daemonBadge");
  badge.textContent = display.label;
  badge.className = `daemon-badge ${display.state}`;
  $("dbPath").textContent = health.db || "Local workflow database";
  $("modeMetric").textContent = daemon ? (daemon.live_mode ? "Live" : "Dry") : "-";
  $("cycleMetric").textContent = daemon?.last_cycle_status || "-";
  $("selectedMetric").textContent = (health.automation?.selected || 0) + (health.automation?.dry_run || 0);
  $("reviewMetric").textContent = health.automation?.review || 0;
  $("accountMetric").textContent = health.active_account_id || "-";
  $("queueMetric").textContent = (health.queue?.queued || 0) + (health.queue?.failed_retryable || 0);
  $("sentMetric").textContent = health.delivery?.messages_verified || 0;
  $("wechatMetric").textContent = health.delivery?.wechat_verified || 0;
  $("heartbeatValue").textContent = displayTime(daemon?.heartbeat_at);
  $("nextPollValue").textContent = displayTime(daemon?.next_poll_at);
  $("lastRunValue").textContent = daemon?.last_run_id || "-";
  $("lastErrorValue").textContent = daemon?.last_error_code || "-";
  $("pauseValue").textContent = health.paused ? "Paused" : "Active";
  $("pauseBtn").disabled = health.paused;
  $("resumeBtn").disabled = !health.paused;
}

function renderTemplates(templates) {
  $("templateCount").textContent = `${templates.length} templates`;
  $("templateRows").innerHTML = templates.map((template) => {
    const retired = Boolean(template.retired_at);
    const status = retired ? "retired" : template.approved && template.active ? "approved" : "draft";
    return `<tr>
      <td><strong>${escapeHtml(template.name)}</strong><div class="muted">${escapeHtml(template.version)}</div></td>
      <td>${escapeHtml(template.selection_guidance || "-")}</td>
      <td class="message-cell">${escapeHtml(template.body)}</td>
      <td>${pill(status)}</td>
      <td class="row-actions">
        ${status === "draft" ? `<button data-approve="${template.id}" type="button">Approve</button>` : ""}
        ${status !== "retired" ? `<button data-retire="${template.id}" class="danger" type="button">Retire</button>` : ""}
      </td>
    </tr>`;
  }).join("");
}

function renderDecisions(decisions) {
  $("decisionCount").textContent = `${decisions.length} decisions`;
  $("decisionRows").innerHTML = decisions.map((decision) => `<tr>
    <td>${escapeHtml(displayTime(decision.created_at))}</td>
    <td><strong>${escapeHtml(decision.name_redacted || "-")}</strong><div class="muted">#${decision.candidate_id}</div></td>
    <td>${escapeHtml(decision.job_name || "-")}</td>
    <td>${pill(decision.outcome)}</td>
    <td>${decision.template_name ? `${escapeHtml(decision.template_name)}:${escapeHtml(decision.template_version)}` : "-"}</td>
    <td>${Math.round(Number(decision.confidence || 0) * 100)}%</td>
    <td>${escapeHtml(decision.reason_redacted || "-")}</td>
  </tr>`).join("");
}

function renderQueue(queue) {
  $("actionCount").textContent = `${queue.length} actions`;
  $("queueRows").innerHTML = queue.map((item) => `<tr>
    <td>${escapeHtml(displayTime(item.created_at))}</td>
    <td><strong>${escapeHtml(item.name_redacted || "-")}</strong><div class="muted">#${item.candidate_id}</div></td>
    <td>${item.action_type === "exchange_wechat" ? "WeChat request" : "Message"}</td>
    <td>${pill(item.status)}</td>
    <td>${item.template_name ? `${escapeHtml(item.template_name)}:${escapeHtml(item.template_version)}` : "-"}</td>
    <td>${escapeHtml(item.last_error_code || "-")}</td>
  </tr>`).join("");
}

function renderEvents(events) {
  $("eventRows").innerHTML = events.slice(0, 100).map((event) => `<li>
    <div><strong>${escapeHtml(event.event_type)}</strong><time>${escapeHtml(displayTime(event.created_at))}</time></div>
    <p>${escapeHtml(event.summary)}</p>
  </li>`).join("");
}

async function refreshAll() {
  const [health, templates, decisions, queue, events] = await Promise.all([
    api("/api/health"), api("/api/templates"), api("/api/decisions"), api("/api/queue"), api("/api/events"),
  ]);
  renderHealth(health, queue);
  renderTemplates(templates);
  renderDecisions(decisions);
  renderQueue(queue);
  renderEvents(events);
}

async function runAction(label, operation) {
  try {
    await operation();
    toast(`${label} completed`);
    await refreshAll();
  } catch (error) {
    toast(`${label} failed: ${error.message}`);
  }
}

$("refreshBtn").addEventListener("click", () => runAction("Refresh", refreshAll));
$("pauseBtn").addEventListener("click", () => runAction("Pause", () => post("/api/automation/pause", { reason: "operator" })));
$("resumeBtn").addEventListener("click", () => runAction("Resume", () => post("/api/automation/resume")));
$("saveDraftBtn").addEventListener("click", () => runAction("Draft save", async () => {
  await post("/api/templates", {
    name: $("templateName").value.trim() || "dashboard_message",
    version: $("templateVersion").value.trim(),
    selection_guidance: $("templateGuidance").value,
    body: $("templateBody").value,
  });
  $("templateVersion").value = "";
}));

$("templateRows").addEventListener("click", (event) => {
  const approveId = event.target.dataset.approve;
  const retireId = event.target.dataset.retire;
  if (approveId) {
    if (!window.confirm("Approve this exact reply for automatic sending?")) return;
    runAction("Template approval", () => post("/api/templates/approve", { template_id: Number(approveId) }));
  }
  if (retireId) {
    if (!window.confirm("Retire this template from future selection?")) return;
    runAction("Template retirement", () => post("/api/templates/retire", { template_id: Number(retireId) }));
  }
});

refreshAll().catch((error) => toast(`Dashboard load failed: ${error.message}`));
window.setInterval(() => refreshAll().catch(() => {}), 10000);
