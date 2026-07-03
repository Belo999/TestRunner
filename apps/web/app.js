const state = {
  dashboard: null,
  health: null,
  projects: [],
  scenarios: [],
  runs: [],
  pools: [],
  environments: [],
  results: [],
  policies: [],
  ai: [],
  audit: [],
  trends: null,
  schedules: [],
  liveRuns: [],
  authToken: null,
  authUser: null,
};

// Section Navigation
function initNavigation() {
  const navItems = document.querySelectorAll('.nav-item');
  const sections = document.querySelectorAll('.section');

  navItems.forEach(item => {
    item.addEventListener('click', (e) => {
      e.preventDefault();
      const sectionId = item.getAttribute('data-section');

      navItems.forEach(n => n.classList.remove('active'));
      item.classList.add('active');

      sections.forEach(s => s.classList.remove('active'));
      const targetSection = document.getElementById(sectionId);
      if (targetSection) {
        targetSection.classList.add('active');
      }
    });
  });

  // Handle hash navigation
  if (window.location.hash) {
    const sectionId = window.location.hash.slice(1);
    const navItem = document.querySelector(`[data-section="${sectionId}"]`);
    if (navItem) navItem.click();
  }
}

// Role Management
const ROLES = {
  admin: { label: "Administrator", initials: "AD", permissions: ["create", "read", "update", "delete", "approve", "execute", "manage_users", "view_audit", "manage_policies"] },
  performance_lead: { label: "Performance Lead", initials: "PL", permissions: ["create", "read", "update", "approve", "execute", "view_audit"] },
  engineer: { label: "Test Engineer", initials: "EN", permissions: ["create", "read", "update", "execute"] },
  viewer: { label: "Stakeholder Viewer", initials: "VW", permissions: ["read"] },
};

let currentUser = { role: "engineer", ...ROLES.engineer };

function switchRole(role) {
  currentUser = { role, ...ROLES[role] };
  document.body.className = `role-${role}`;
  document.querySelector("#userAvatar").textContent = currentUser.initials;
  document.querySelector("#userName").textContent = currentUser.label;
  applyPermissions();
  initialize();
}

function applyPermissions() {
  const perms = currentUser.permissions;
  document.querySelectorAll("[data-form]").forEach((btn) => {
    const form = btn.getAttribute("data-form");
    if (form === "user" && !perms.includes("manage_users")) {
      btn.style.display = "none";
    } else if (["project", "scenario", "pool", "environment", "schedule"].includes(form) && !perms.includes("create")) {
      btn.style.display = "none";
    } else {
      btn.style.display = "";
    }
  });
  document.querySelectorAll("[data-action]").forEach((btn) => {
    const action = btn.getAttribute("data-action");
    if (["approve", "cancel"].includes(action) && !perms.includes("approve")) {
      btn.style.display = "none";
    } else if (action === "start" && !perms.includes("execute")) {
      btn.style.display = "none";
    } else if (action === "baseline" && !perms.includes("approve")) {
      btn.style.display = "none";
    } else {
      btn.style.display = "";
    }
  });
  document.querySelectorAll("[data-delete]").forEach((btn) => {
    btn.style.display = perms.includes("delete") ? "" : "none";
  });
  document.querySelectorAll("[data-edit]").forEach((btn) => {
    btn.style.display = perms.includes("update") ? "" : "none";
  });
}

async function fetchJson(url, options = {}) {
  const headers = { "Content-Type": "application/json", ...options.headers };
  if (state.authToken) {
    headers["Authorization"] = "Bearer " + state.authToken;
  }
  const response = await fetch(url, { ...options, headers });
  if (!response.ok) {
    const body = await response.json().catch(() => ({}));
    throw new Error(body.error || `Request failed: ${response.status}`);
  }
  return response.json();
}

function escapeHtml(value) {
  return String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#039;");
}

function statusBadge(status) {
  return `<span class="status status-${escapeHtml(status)}">${escapeHtml(status)}</span>`;
}

function engineBadge(engine) {
  return `<span class="engine ${escapeHtml(engine.toLowerCase())}">${escapeHtml(engine)}</span>`;
}

function pill(value, tone) {
  const normalized = String(value ?? "").toLowerCase();
  let cls = tone || "blue";
  if (!tone) {
    if (["critical", "failed", "error", "unhealthy", "cancelled"].includes(normalized)) cls = "red";
    else if (["healthy", "ready", "completed", "passed", "approved", "info"].includes(normalized)) cls = "green";
    else if (["warning", "pending", "scheduled", "queued"].includes(normalized)) cls = "yellow";
  }
  return `<span class="pill pill-${cls}">${escapeHtml(value)}</span>`;
}

function renderHealth() {
  if (!state.health?.dependencies) return;
  const dependencies = Object.values(state.health.dependencies);
  const ready = dependencies.filter((item) => ["ok", "reachable", "configured"].includes(item.status)).length;
  const statusEl = document.querySelector("#health");
  if (!statusEl) return;
  const allOk = ready === dependencies.length;
  statusEl.className = `status ${allOk ? "status-completed" : "status-pending"}`;
  statusEl.textContent = `${ready}/${dependencies.length} systems online`;
}

// Chart instances
let trendChartInstance = null;
let statusChartInstance = null;
let engineChartInstance = null;
let qualityChartInstance = null;

const chartColors = {
  blue: '#00a3e0',
  green: '#28a745',
  red: '#dc3545',
  amber: '#ffc107',
  purple: '#6f42c1',
  teal: '#20c997',
  pink: '#e83e8c',
  gray: '#6c757d',
};

function renderCharts() {
  renderTrendChart();
  renderStatusChart();
  renderEngineChart();
  renderQualityChart();
}

function renderTrendChart() {
  const ctx = document.getElementById('trendChart');
  if (!ctx) return;

  const completedRuns = state.runs
    .filter(r => r.status === 'completed' && r.result)
    .slice(-10)
    .reverse();

  if (completedRuns.length === 0) {
    ctx.parentElement.innerHTML = '<p style="color:var(--text-secondary);text-align:center;padding:40px;">No completed runs with results yet.</p>';
    return;
  }

  const labels = completedRuns.map(r => `#${r.id}`);
  const p95Data = completedRuns.map(r => r.result?.p95_ms || 0);
  const slaData = completedRuns.map(r => r.sla_p95_ms || 450);

  if (trendChartInstance) trendChartInstance.destroy();
  trendChartInstance = new Chart(ctx, {
    type: 'line',
    data: {
      labels: labels,
      datasets: [
        {
          label: 'p95 Latency (ms)',
          data: p95Data,
          borderColor: chartColors.blue,
          backgroundColor: chartColors.blue + '20',
          fill: true,
          tension: 0.3,
        },
        {
          label: 'SLA Threshold',
          data: slaData,
          borderColor: chartColors.red,
          borderDash: [5, 5],
          fill: false,
          pointRadius: 0,
        },
      ],
    },
    options: {
      responsive: true,
      maintainAspectRatio: false,
      plugins: {
        legend: { position: 'bottom' },
      },
      scales: {
        y: {
          beginAtZero: true,
          title: { display: true, text: 'ms' },
        },
      },
    },
  });
}

function renderStatusChart() {
  const ctx = document.getElementById('statusChart');
  if (!ctx) return;

  const statusCounts = {};
  state.runs.forEach(r => {
    statusCounts[r.status] = (statusCounts[r.status] || 0) + 1;
  });

  const labels = Object.keys(statusCounts);
  const data = Object.values(statusCounts);
  const colors = labels.map(s => {
    if (s === 'completed') return chartColors.green;
    if (s === 'failed') return chartColors.red;
    if (s === 'running') return chartColors.blue;
    if (s === 'cancelled') return chartColors.gray;
    return chartColors.amber;
  });

  if (statusChartInstance) statusChartInstance.destroy();
  statusChartInstance = new Chart(ctx, {
    type: 'doughnut',
    data: {
      labels: labels.map(l => l.charAt(0).toUpperCase() + l.slice(1)),
      datasets: [{ data, backgroundColor: colors, borderWidth: 2 }],
    },
    options: {
      responsive: true,
      maintainAspectRatio: false,
      plugins: {
        legend: { position: 'right' },
      },
    },
  });
}

function renderEngineChart() {
  const ctx = document.getElementById('engineChart');
  if (!ctx) return;

  const engineCounts = {};
  state.runs.forEach(r => {
    engineCounts[r.engine] = (engineCounts[r.engine] || 0) + 1;
  });

  const labels = Object.keys(engineCounts);
  const data = Object.values(engineCounts);
  const colors = [chartColors.purple, chartColors.red, chartColors.amber, chartColors.teal, chartColors.pink];

  if (engineChartInstance) engineChartInstance.destroy();
  engineChartInstance = new Chart(ctx, {
    type: 'pie',
    data: {
      labels,
      datasets: [{ data, backgroundColor: colors.slice(0, labels.length), borderWidth: 2 }],
    },
    options: {
      responsive: true,
      maintainAspectRatio: false,
      plugins: {
        legend: { position: 'right' },
      },
    },
  });
}

function renderQualityChart() {
  const ctx = document.getElementById('qualityChart');
  if (!ctx) return;

  const gateCounts = {};
  state.runs.forEach(r => {
    const gate = r.quality_gate || 'not_evaluated';
    gateCounts[gate] = (gateCounts[gate] || 0) + 1;
  });

  const labels = Object.keys(gateCounts);
  const data = Object.values(gateCounts);
  const colors = labels.map(g => {
    if (g === 'passed') return chartColors.green;
    if (g === 'failed') return chartColors.red;
    return chartColors.gray;
  });

  if (qualityChartInstance) qualityChartInstance.destroy();
  qualityChartInstance = new Chart(ctx, {
    type: 'doughnut',
    data: {
      labels: labels.map(l => l.charAt(0).toUpperCase() + l.slice(1)),
      datasets: [{ data, backgroundColor: colors, borderWidth: 2 }],
    },
    options: {
      responsive: true,
      maintainAspectRatio: false,
      plugins: {
        legend: { position: 'right' },
      },
    },
  });
}

function renderMetrics() {
  const counts = state.dashboard.counts;
  document.querySelector("#projectCount").textContent = counts.projects;
  document.querySelector("#scenarioCount").textContent = counts.scenarios;
  document.querySelector("#runCount").textContent = counts.runs;
  document.querySelector("#insightCount").textContent = counts.insights;
  document.querySelector("#policyCount").textContent = counts.policies;
  document.querySelector("#riskCount").textContent = counts.criticalInsights;
}

function renderSummaryStats() {
  const completedRuns = state.runs.filter(r => r.status === 'completed' && r.result);
  if (completedRuns.length === 0) {
    document.querySelector("#avgP95").textContent = "-";
    document.querySelector("#avgApdex").textContent = "-";
    document.querySelector("#passRate").textContent = "-";
    document.querySelector("#totalRuns").textContent = state.runs.length;
    return;
  }

  const avgP95 = Math.round(completedRuns.reduce((sum, r) => sum + (r.result?.p95_ms || 0), 0) / completedRuns.length);
  const avgApdex = (completedRuns.reduce((sum, r) => sum + (r.result?.apdex || 0), 0) / completedRuns.length).toFixed(2);
  const passed = completedRuns.filter(r => r.quality_gate === 'passed').length;
  const passRate = Math.round((passed / completedRuns.length) * 100);

  document.querySelector("#avgP95").textContent = `${avgP95}ms`;
  document.querySelector("#avgApdex").textContent = avgApdex;
  document.querySelector("#passRate").textContent = `${passRate}%`;
  document.querySelector("#totalRuns").textContent = state.runs.length;
}

// Run Filters
let runFilters = { engine: "", status: "", search: "" };

async function applyRunFilters() {
  runFilters.engine = document.querySelector("#filterEngine").value;
  runFilters.status = document.querySelector("#filterStatus").value;
  runFilters.search = document.querySelector("#filterSearch").value;
  try {
    const params = new URLSearchParams();
    if (runFilters.engine) params.set("engine", runFilters.engine);
    if (runFilters.status) params.set("status", runFilters.status);
    if (runFilters.search) params.set("search", runFilters.search);
    const data = await fetchJson(`/api/runs?${params.toString()}`);
    state.runs = data.runs;
    renderRuns();
  } catch (e) {
    console.error("Filter failed:", e);
  }
}

function clearRunFilters() {
  document.querySelector("#filterEngine").value = "";
  document.querySelector("#filterStatus").value = "";
  document.querySelector("#filterSearch").value = "";
  runFilters = { engine: "", status: "", search: "" };
  initialize();
}

function renderRuns() {
  const list = document.querySelector("#runsList");
  list.innerHTML = state.runs
    .map(
      (run) => {
        const isRunning = run.status === 'running';
        const hasResults = run.result && run.result.p95_ms;
        const executionBadge = run.execution_id
          ? `<span class="pill running" title="Container: ${run.execution_id.substring(0, 12)}">executing</span>`
          : "";
        return `
        <div class="run-card ${isRunning ? 'run-card-active' : ''}">
          <div class="run-header">
            <div>
              <div class="run-title">
                <span class="run-id">#${run.id}</span> ${escapeHtml(run.name)}
              </div>
              <div class="run-meta">
                ${escapeHtml(run.project_name)} / ${escapeHtml(run.environment_name)}
                ${run.created_at ? ` / ${new Date(run.created_at).toLocaleDateString()}` : ''}
              </div>
            </div>
            <div class="run-badges">
              ${engineBadge(run.engine)}
              ${statusBadge(run.status)}
              ${statusBadge(run.quality_gate)}
              ${executionBadge}
            </div>
          </div>
          <div class="run-stats">
            <div class="run-stat">
              <span class="run-stat-label">Users</span>
              <span class="run-stat-value">${escapeHtml(run.target_vusers)}</span>
            </div>
            <div class="run-stat">
              <span class="run-stat-label">Duration</span>
              <span class="run-stat-value">${escapeHtml(run.duration_minutes)}m</span>
            </div>
            <div class="run-stat">
              <span class="run-stat-label">Risk</span>
              <span class="run-stat-value">${escapeHtml(run.risk_score)}</span>
            </div>
            <div class="run-stat">
              <span class="run-stat-label">Pool</span>
              <span class="run-stat-value">${escapeHtml(run.pool_name || "None")}</span>
            </div>
            ${hasResults ? `
            <div class="run-stat">
              <span class="run-stat-label">p95</span>
              <span class="run-stat-value">${run.result.p95_ms}ms</span>
            </div>
            <div class="run-stat">
              <span class="run-stat-label">Errors</span>
              <span class="run-stat-value">${run.result.error_rate}%</span>
            </div>
            ` : ''}
          </div>
          <div class="run-actions">
            ${run.status === "pending_approval" || run.status === "draft" ? `<button type="button" class="btn btn-sm btn-secondary" data-action="approve" data-id="${run.id}">Approve</button>` : ""}
            ${["ready", "approved"].includes(run.status) ? `<button type="button" class="btn btn-sm btn-primary" data-action="start" data-id="${run.id}">Start</button>` : ""}
            ${run.status === "completed" && !run.is_baseline ? `<button type="button" class="btn btn-sm btn-secondary" data-action="baseline" data-id="${run.id}">Set Baseline</button>` : ""}
            ${["running", "queued", "approved", "ready"].includes(run.status) ? `<button type="button" class="btn btn-sm btn-secondary" data-action="cancel" data-id="${run.id}">Cancel</button>` : ""}
          </div>
        </div>
      `;
      }
    )
    .join("");

  const select = document.querySelector("#logRunSelect");
  const currentVal = select.value;
  select.innerHTML = '<option value="">Select a run...</option>';
  for (const run of state.runs) {
    const opt = document.createElement("option");
    opt.value = run.id;
    opt.textContent = `#${run.id} ${run.name} (${run.engine} / ${run.status})`;
    select.appendChild(opt);
  }
  if (currentVal) select.value = currentVal;
}

function renderProjects() {
  document.querySelector("#projectList").innerHTML = state.projects
    .map(
      (project) => `
        <div class="entity-card">
          <div class="entity-card-header">
            <span class="entity-card-title">${escapeHtml(project.name)}</span>
            ${statusBadge(project.risk_tier)}
          </div>
          <div class="entity-card-meta">${escapeHtml(project.owner)} / ${escapeHtml(project.business_unit)}</div>
          <div class="entity-actions">
            <button type="button" class="btn-edit" data-edit="project" data-id="${project.id}">Edit</button>
            <button type="button" class="btn-delete" data-delete="projects" data-id="${project.id}">Delete</button>
          </div>
        </div>
      `
    )
    .join("");
}

function renderScenarios() {
  document.querySelector("#scenarioList").innerHTML = state.scenarios
    .map(
      (scenario) => `
        <div class="entity-card">
          <div class="entity-card-header">
            <span class="entity-card-title">${escapeHtml(scenario.name)}</span>
            ${engineBadge(scenario.engine)}
          </div>
          <div class="scenario-meta">
            <span>${escapeHtml(scenario.test_type)}</span>
            <span>SLA p95: ${escapeHtml(scenario.sla_p95_ms)} ms</span>
            <span>Max error: ${escapeHtml(scenario.max_error_rate)}%</span>
          </div>
          <div class="entity-card-meta">${escapeHtml(scenario.workload_mix)}</div>
          <div class="entity-actions">
            <button type="button" class="btn-edit" data-edit="scenario" data-id="${scenario.id}">Edit</button>
            <button type="button" class="btn-delete" data-delete="scenarios" data-id="${scenario.id}">Delete</button>
          </div>
        </div>
      `
    )
    .join("");
}

function renderPools() {
  document.querySelector("#poolList").innerHTML = state.pools
    .map(
      (pool) => `
        <article class="mini">
          <div>
            <strong>${escapeHtml(pool.name)}</strong>
            <p>${escapeHtml(pool.region)} / ${escapeHtml(Array.isArray(pool.engines) ? pool.engines.join(", ") : pool.engines)}</p>
          </div>
          <div class="right">
            ${pill(pool.status)}
            <small>${escapeHtml(pool.current_reservation)} / ${escapeHtml(pool.max_vusers)} users</small>
          </div>
          <div class="entity-actions">
            <button type="button" class="btn-edit" data-edit="pool" data-id="${pool.id}">Edit</button>
            <button type="button" class="btn-delete" data-delete="pools" data-id="${pool.id}">Delete</button>
          </div>
        </article>
      `
    )
    .join("");
}

function renderEnvironments() {
  document.querySelector("#environmentList").innerHTML = state.environments
    .map(
      (environment) => `
        <article class="mini">
          <div>
            <strong>${escapeHtml(environment.name)}</strong>
            <p>${escapeHtml(environment.region)} / ${escapeHtml(environment.classification)} / residency ${escapeHtml(environment.data_residency)}</p>
          </div>
          <div class="right">
            ${pill(environment.readiness_status)}
            <small>${environment.service_virtualization_enabled ? "Virtualization on" : "Virtualization off"}</small>
          </div>
          <div class="entity-actions">
            <button type="button" class="btn-edit" data-edit="environment" data-id="${environment.id}">Edit</button>
            <button type="button" class="btn-delete" data-delete="environments" data-id="${environment.id}">Delete</button>
          </div>
        </article>
      `
    )
    .join("");
}

function renderResults() {
  document.querySelector("#resultList").innerHTML = state.results
    .slice(0, 8)
    .map(
      (result) => `
        <article class="result-card">
          <strong>Run ${escapeHtml(result.run_id)}</strong>
          <dl>
            <div><dt>p95</dt><dd>${escapeHtml(result.p95_ms)} ms</dd></div>
            <div><dt>Error</dt><dd>${escapeHtml(result.error_rate)}%</dd></div>
            <div><dt>RPS</dt><dd>${escapeHtml(result.throughput_rps)}</dd></div>
            <div><dt>Apdex</dt><dd>${escapeHtml(result.apdex)}</dd></div>
          </dl>
        </article>
      `
    )
    .join("");
}

function renderAi() {
  document.querySelector("#aiList").innerHTML = state.ai
    .map(
      (item) => `
        <article class="recommendation">
          <div class="run-header">
            <strong>${escapeHtml(item.area)}</strong>
            ${pill(item.severity)}
          </div>
          <p>${escapeHtml(item.insight)}</p>
          <small>${escapeHtml(item.recommendation)}</small>
        </article>
      `
    )
    .join("");
}

function renderPolicies() {
  document.querySelector("#policyList").innerHTML = state.policies
    .map(
      (policy) => `
        <article class="mini">
          <div>
            <strong>${escapeHtml(policy.name)}</strong>
            <p>${escapeHtml(policy.rule)}</p>
          </div>
          <div class="right">
            ${pill(policy.severity)}
            <small>${escapeHtml(policy.scope)}</small>
          </div>
          <div class="entity-actions">
            <button type="button" class="btn-edit" data-edit="policy" data-id="${policy.id}">Edit</button>
            <button type="button" class="btn-delete" data-delete="policies" data-id="${policy.id}">Delete</button>
          </div>
        </article>
      `
    )
    .join("");
}

function renderAudit() {
  document.querySelector("#auditList").innerHTML = state.audit
    .slice(0, 16)
    .map(
      (event) => `
        <article class="audit-row">
          <strong>${escapeHtml(event.action)}</strong>
          <small>${escapeHtml(event.entity_type)} #${escapeHtml(event.entity_id || "-")} / ${escapeHtml(event.created_at)}</small>
        </article>
      `
    )
    .join("");
}

let liveRefreshInterval = null;

function renderLive() {
  const section = document.querySelector("#live");
  if (!state.liveRuns.length) {
    section.style.display = "none";
    if (liveRefreshInterval) {
      clearInterval(liveRefreshInterval);
      liveRefreshInterval = null;
    }
    return;
  }
  section.style.display = "";
  document.querySelector("#liveStatus").textContent = `${state.liveRuns.length} active`;

  const list = document.querySelector("#liveList");
  list.innerHTML = state.liveRuns
    .map((run) => {
      const elapsed = run.elapsedSeconds || 0;
      const totalSeconds = (run.durationMinutes || 1) * 60;
      const progress = run.progress || Math.min(100, Math.round((elapsed / totalSeconds) * 100));
      const mins = Math.floor(elapsed / 60);
      const secs = elapsed % 60;

      return `
        <article class="live-run">
          <div class="run-header">
            <div>
              <strong>${escapeHtml(run.name)}</strong>
              <p>${escapeHtml(run.scenario_name)} / ${escapeHtml(run.engine)} / ${escapeHtml(run.environment_name)}</p>
            </div>
            <div class="pill-row">
              ${pill(run.engine)}
              <span class="pill running">running</span>
              <span class="pill ${run.containerRunning ? "healthy" : "warning"}">${run.containerStatus}</span>
            </div>
          </div>
          <div class="progress-bar">
            <div class="progress-fill" style="width:${progress}%"></div>
            <span class="progress-text">${progress}% - ${mins}m ${String(secs).padStart(2, "0")}s elapsed</span>
          </div>
          <div class="run-body">
            <span>${run.targetVusers} users</span>
            <span>${run.durationMinutes} min target</span>
            <span>Correlation: ${escapeHtml(run.correlationId)}</span>
            ${run.containerCpu ? `<span>CPU: ${escapeHtml(run.containerCpu)}</span>` : ""}
            ${run.containerMemory ? `<span>Mem: ${escapeHtml(run.containerMemory)}</span>` : ""}
          </div>
          ${run.result ? `
          <div class="live-metrics">
            <div class="trend-metric"><small>p95</small><strong>${run.result.p95_ms}ms</strong></div>
            <div class="trend-metric"><small>RPS</small><strong>${run.result.throughput_rps}</strong></div>
            <div class="trend-metric"><small>Errors</small><strong>${run.result.error_rate}%</strong></div>
            <div class="trend-metric"><small>Apdex</small><strong>${run.result.apdex}</strong></div>
          </div>
          ` : ""}
          <p class="live-summary">${escapeHtml(run.aiSummary || "")}</p>
        </article>
      `;
    })
    .join("");
}

function startLiveMonitoring() {
  if (liveRefreshInterval) return;
  liveRefreshInterval = setInterval(async () => {
    try {
      const data = await fetchJson("/api/runs/active");
      state.liveRuns = data.runs;
      renderLive();
    } catch (e) {
      console.error("Live refresh failed:", e);
    }
  }, 3000);
}

function populateCompareSelects() {
  const completedRuns = state.runs.filter((r) => r.status === "completed" && r.result);
  const options = completedRuns
    .map((r) => `<option value="${r.id}">#${r.id} ${escapeHtml(r.name)} (${r.engine})</option>`)
    .join("");
  document.querySelector("#compareRun1").innerHTML = '<option value="">Baseline run...</option>' + options;
  document.querySelector("#compareRun2").innerHTML = '<option value="">Current run...</option>' + options;
}

async function compareRuns() {
  const id1 = document.querySelector("#compareRun1").value;
  const id2 = document.querySelector("#compareRun2").value;
  if (!id1 || !id2) {
    alert("Select two runs to compare");
    return;
  }
  if (id1 === id2) {
    alert("Select two different runs");
    return;
  }
  const container = document.querySelector("#compareResult");
  container.innerHTML = '<p style="color:var(--muted)">Loading comparison...</p>';

  try {
    const data = await fetchJson(`/api/runs/compare?ids=${id2},${id1}`);
    renderComparison(data);
  } catch (e) {
    container.innerHTML = `<p style="color:var(--red)">Error: ${escapeHtml(e.message)}</p>`;
  }
}

function renderComparison(data) {
  const container = document.querySelector("#compareResult");
  if (!data.comparisons || !data.comparisons.length) {
    container.innerHTML = '<p style="color:var(--muted)">No comparable metrics found.</p>';
    return;
  }

  const baseline = data.baseline;
  const current = data.current;

  let html = `
    <div class="compare-header">
      <div class="compare-run">
        <span class="pill">Baseline</span>
        <strong>#${baseline.id} ${escapeHtml(baseline.name)}</strong>
        <small>${escapeHtml(baseline.engine)} / ${baseline.createdAt}</small>
      </div>
      <div class="compare-run">
        <span class="pill">Current</span>
        <strong>#${current.id} ${escapeHtml(current.name)}</strong>
        <small>${escapeHtml(current.engine)} / ${current.createdAt}</small>
      </div>
    </div>
    <div class="compare-metrics">
  `;

  for (const comp of data.comparisons) {
    const metricLabels = {
      p50_ms: "p50 Latency",
      p95_ms: "p95 Latency",
      p99_ms: "p99 Latency",
      throughput_rps: "Throughput",
      error_rate: "Error Rate",
      apdex: "Apdex",
    };
    const units = {
      p50_ms: "ms", p95_ms: "ms", p99_ms: "ms",
      throughput_rps: "rps", error_rate: "%", apdex: "",
    };
    const label = metricLabels[comp.metric] || comp.metric;
    const unit = units[comp.metric] || "";
    const improved = comp.improved;
    const deltaClass = comp.delta === 0 ? "" : (improved ? "trend-down" : "trend-up");
    const deltaSign = comp.delta > 0 ? "+" : "";

    html += `
      <div class="compare-metric">
        <small>${label}</small>
        <div class="compare-values">
          <span class="compare-baseline">${comp.values[comp.values.length - 1]}${unit}</span>
          <span class="compare-arrow">→</span>
          <span class="compare-current">${comp.values[0]}${unit}</span>
        </div>
        <span class="${deltaClass}">${deltaSign}${comp.deltaPercent}%</span>
      </div>
    `;
  }

  html += `</div>`;

  const gateClass = current.qualityGate === "passed" ? "healthy" : "critical";
  html += `
    <div class="compare-summary">
      <span class="pill ${gateClass}">${current.qualityGate}</span>
      <span>Risk: ${current.riskScore || "-"}</span>
    </div>
  `;

  container.innerHTML = html;
}

const DAY_NAMES = ['Sunday', 'Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday', 'Saturday'];

function renderLoadProfiles() {
  const list = document.querySelector("#loadProfileList");
  if (!list) return;

  fetchJson("/api/load-profiles")
    .then((data) => {
      list.innerHTML = data.profiles
        .map((p) => `
          <div class="template-card" style="border-left-color: var(--accent)">
            <h3>${escapeHtml(p.name)}</h3>
            <p>${escapeHtml(p.description)}</p>
            <div class="template-meta">
              <span>Use: ${escapeHtml(p.useCase)}</span>
            </div>
            <code style="display:block;margin-top:8px;padding:8px;background:#f8f9fa;border-radius:4px;font-size:12px;">${escapeHtml(p.pattern)}</code>
          </div>
        `)
        .join("");
    })
    .catch((e) => console.error("Failed to load profiles:", e));
}

function renderApplications() {
  const list = document.querySelector("#applicationList");
  if (!list) return;

  fetchJson("/api/applications")
    .then((data) => {
      if (!data.applications || data.applications.length === 0) {
        list.innerHTML = '<p style="color:var(--text-secondary);padding:12px;">No applications registered. Add an application to track its health.</p>';
        return;
      }

      list.innerHTML = data.applications
        .map((app) => `
          <div class="entity-card">
            <div class="entity-card-header">
              <span class="entity-card-title">${escapeHtml(app.name)}</span>
              <span class="status status-${app.health_status === 'healthy' ? 'completed' : app.health_status === 'unreachable' ? 'failed' : 'pending'}">${escapeHtml(app.health_status)}</span>
            </div>
            <div class="entity-card-meta">
              ${escapeHtml(app.endpoint)}
              ${app.team ? ` / Team: ${escapeHtml(app.team)}` : ''}
              ${app.environment ? ` / Env: ${escapeHtml(app.environment)}` : ''}
            </div>
            <div class="entity-actions">
              <button type="button" class="btn btn-sm btn-primary" onclick="checkAppHealth(${app.id})">Check Health</button>
              <button type="button" class="btn-edit" data-edit="application" data-id="${app.id}">Edit</button>
              <button type="button" class="btn-delete" data-delete="applications" data-id="${app.id}">Delete</button>
            </div>
          </div>
        `)
        .join("");
    })
    .catch((e) => console.error("Failed to load applications:", e));
}

async function checkAppHealth(appId) {
  try {
    const result = await fetchJson(`/api/applications/${appId}/health`);
    alert(`${result.name}: ${result.healthStatus} (${result.responseTimeMs}ms)`);
    renderApplications();
  } catch (e) {
    alert("Health check failed: " + e.message);
  }
}

function renderImpact() {
  const summaryEl = document.querySelector("#impactSummary");
  const listEl = document.querySelector("#impactList");
  if (!summaryEl || !listEl) return;

  fetchJson("/api/impact")
    .then((data) => {
      const riskClass = data.riskLevel === 'high' ? 'red' : data.riskLevel === 'medium' ? 'yellow' : 'green';
      summaryEl.innerHTML = `
        <span class="badge ${riskClass}">${data.riskLevel} risk</span>
        <span style="margin-left: 8px;">Score: ${data.impactScore}</span>
        <span style="margin-left: 16px; color: var(--text-secondary);">${data.summary}</span>
      `;

      if (data.recommendedTests.length === 0) {
        listEl.innerHTML = '<p style="color:var(--text-secondary);padding:12px;">No tests affected by recent changes.</p>';
        return;
      }

      listEl.innerHTML = data.recommendedTests
        .map((t) => `
          <div class="entity-card">
            <div class="entity-card-header">
              <span class="entity-card-title">#${t.id} ${escapeHtml(t.name)}</span>
              ${engineBadge(t.engine)}
            </div>
            <div class="entity-card-meta">
              ${escapeHtml(t.scenario_name)} / ${escapeHtml(t.environment_name)}
            </div>
            <div class="entity-actions">
              <button type="button" class="btn btn-sm btn-primary" onclick="rerunAffected(${t.scenario_id}, ${t.environment_id}, '${escapeHtml(t.engine)}')">Re-run</button>
            </div>
          </div>
        `)
        .join("");
    })
    .catch((e) => console.error("Failed to load impact analysis:", e));
}

async function rerunAffected(scenarioId, environmentId, engine) {
  try {
    await fetchJson("/api/runs", {
      method: "POST",
      body: JSON.stringify({
        scenarioId,
        environmentId,
        engine,
        name: `Impact Re-run - ${new Date().toISOString().split('T')[0]}`,
      }),
    });
    alert("Re-run created!");
    initialize();
  } catch (e) {
    alert("Failed: " + e.message);
  }
}

function renderWebhooks() {
  const list = document.querySelector("#webhookList");
  if (!list) return;

  fetchJson("/api/webhooks")
    .then((data) => {
      if (!data.webhooks || data.webhooks.length === 0) {
        list.innerHTML = '<p style="color:var(--text-secondary);padding:12px;">No webhooks configured. Add a webhook to receive notifications on run events.</p>';
        return;
      }

      list.innerHTML = data.webhooks
        .map((w) => `
          <div class="entity-card">
            <div class="entity-card-header">
              <span class="entity-card-title">${escapeHtml(w.name)}</span>
              <span class="status status-${w.enabled ? 'completed' : 'pending'}">${w.enabled ? 'enabled' : 'disabled'}</span>
            </div>
            <div class="entity-card-meta">
              URL: ${escapeHtml(w.url)}
            </div>
            <div class="entity-card-meta">
              Event: ${escapeHtml(w.event)} ${w.secret ? '/ Signed' : ''}
            </div>
            <div class="entity-actions">
              <button type="button" class="btn-edit" data-edit="webhook" data-id="${w.id}">Edit</button>
              <button type="button" class="btn-delete" data-delete="webhooks" data-id="${w.id}">Delete</button>
            </div>
          </div>
        `)
        .join("");
    })
    .catch((e) => console.error("Failed to load webhooks:", e));
}

function renderTemplates() {
  const list = document.querySelector("#templateList");
  if (!list) return;

  fetchJson("/api/templates")
    .then((data) => {
      list.innerHTML = data.templates
        .map((t) => `
          <div class="template-card" style="border-left-color: ${t.color}" onclick="createRunFromTemplate('${t.id}')">
            <h3>${escapeHtml(t.name)}</h3>
            <p>${escapeHtml(t.description)}</p>
            <div class="template-meta">
              <span>Type: ${escapeHtml(t.test_type)}</span>
              <span>Default: ${t.default_vusers} users / ${t.default_duration} min</span>
            </div>
          </div>
        `)
        .join("");
    })
    .catch((e) => console.error("Failed to load templates:", e));
}

async function createRunFromTemplate(templateId) {
  const template = (await fetchJson("/api/templates")).templates.find(t => t.id === templateId);
  if (!template) return;

  const scenarios = state.scenarios;
  const environments = state.environments;
  if (!scenarios.length || !environments.length) {
    alert("Create at least one scenario and environment first.");
    return;
  }

  const scenario = scenarios[0];
  const environment = environments.find(e => e.readiness_status === 'ready') || environments[0];

  const payload = {
    scenarioId: scenario.id,
    environmentId: environment.id,
    engine: scenario.engine,
    name: `${template.name} - ${scenario.name}`,
    targetVusers: template.default_vusers,
    durationMinutes: template.default_duration,
  };

  try {
    await fetchJson(`/api/templates/${templateId}/run`, { method: "POST", body: JSON.stringify(payload) });
    alert(`Run created from ${template.name} template!`);
    initialize();
  } catch (e) {
    alert("Failed to create run: " + e.message);
  }
}

function renderBaselines() {
  const list = document.querySelector("#baselineList");
  if (!list) return;

  fetchJson("/api/baselines")
    .then((data) => {
      if (!data.baselines || data.baselines.length === 0) {
        list.innerHTML = '<p style="color:var(--text-secondary);padding:12px;">No baselines approved yet. Set a run as baseline from the Execution section.</p>';
        return;
      }

      list.innerHTML = data.baselines
        .map((b) => `
          <div class="entity-card">
            <div class="entity-card-header">
              <span class="entity-card-title">#${b.id} ${escapeHtml(b.name)}</span>
              <span class="status status-completed">baseline</span>
            </div>
            <div class="entity-card-meta">
              ${escapeHtml(b.scenario_name)} / ${escapeHtml(b.engine)} / Approved by ${escapeHtml(b.baseline_approved_by || 'system')}
            </div>
            <div class="run-stats">
              <div class="run-stat"><span class="run-stat-label">p95</span><span class="run-stat-value">${b.p95_ms || 'N/A'}ms</span></div>
              <div class="run-stat"><span class="run-stat-label">Errors</span><span class="run-stat-value">${b.error_rate || 0}%</span></div>
              <div class="run-stat"><span class="run-stat-label">Throughput</span><span class="run-stat-value">${b.throughput_rps || 0} rps</span></div>
              <div class="run-stat"><span class="run-stat-label">Apdex</span><span class="run-stat-value">${b.apdex || 0}</span></div>
            </div>
          </div>
        `)
        .join("");
    })
    .catch((e) => console.error("Failed to load baselines:", e));
}

function renderWindows() {
  const list = document.querySelector("#windowList");
  const statusEl = document.querySelector("#windowStatus");
  if (!list) return;

  fetchJson("/api/execution-windows")
    .then((data) => {
      if (!data.windows || data.windows.length === 0) {
        list.innerHTML = '<p style="color:var(--text-secondary);padding:12px;">No execution windows configured. Tests can run at any time.</p>';
        statusEl.innerHTML = '<span class="badge green">No restrictions</span> Tests allowed at all times';
        return;
      }

      fetchJson("/api/execution-windows/check")
        .then((check) => {
          statusEl.innerHTML = check.allowed
            ? `<span class="badge green">Allowed</span> ${check.reason} (Hour: ${check.currentHour}, Day: ${DAY_NAMES[check.currentDay]})`
            : `<span class="badge red">Blocked</span> ${check.reason} (Hour: ${check.currentHour}, Day: ${DAY_NAMES[check.currentDay]})`;
        });

      list.innerHTML = data.windows
        .map((w) => `
          <div class="entity-card">
            <div class="entity-card-header">
              <span class="entity-card-title">${escapeHtml(w.name)}</span>
              <span class="status status-${w.type === 'blackout' ? 'failed' : 'completed'}">${escapeHtml(w.type)}</span>
            </div>
            <div class="entity-card-meta">
              ${w.day_of_week !== null ? DAY_NAMES[w.day_of_week] : 'Every day'} / ${w.start_hour}:00 - ${w.end_hour}:00
              ${w.environment_name ? ` / ${escapeHtml(w.environment_name)}` : ' / All environments'}
            </div>
            <div class="entity-actions">
              <button type="button" class="btn-edit" data-edit="execution_window" data-id="${w.id}">Edit</button>
              <button type="button" class="btn-delete" data-delete="execution-windows" data-id="${w.id}">Delete</button>
            </div>
          </div>
        `)
        .join("");
    })
    .catch((e) => console.error("Failed to load windows:", e));
}

function renderUsers() {
  const list = document.querySelector("#userList");
  if (!list) return;
  fetchJson("/api/users")
    .then((data) => {
      list.innerHTML = data.users
        .map(
          (user) => `
          <div class="user-card">
            <div class="user-card-header">
              <span class="user-card-title">${escapeHtml(user.display_name)}</span>
              ${pill(user.roleLabel || user.role)}
            </div>
            <div class="user-card-meta">@${escapeHtml(user.username)}</div>
            <div class="user-card-meta">${escapeHtml(user.email || "No email")}</div>
            <div class="user-card-actions">
              <button type="button" class="btn-edit" data-edit="user" data-id="${user.id}">Edit</button>
              ${user.username !== "admin" ? `<button type="button" class="btn-delete" data-delete="users" data-id="${user.id}">Delete</button>` : ""}
            </div>
          </div>
        `
        )
        .join("");
    })
    .catch((e) => console.error("Failed to load users:", e));
}

function renderSchedules() {
  const list = document.querySelector("#scheduleList");
  if (!state.schedules.length) {
    list.innerHTML = '<p style="color:var(--muted);padding:16px;">No schedules created yet. Click "+ Schedule" to set up recurring tests.</p>';
    return;
  }
  list.innerHTML = state.schedules
    .map((s) => {
      const enabled = s.enabled === 1;
      const nextRun = new Date(s.next_run_at);
      const now = new Date();
      const isOverdue = nextRun < now;
      return `
        <article class="mini">
          <div>
            <strong>${escapeHtml(s.name)}</strong>
            <p>${escapeHtml(s.scenario_name)} (${escapeHtml(s.engine)}) / ${escapeHtml(s.environment_name)}</p>
          </div>
          <div class="right">
            <span class="pill ${enabled ? (isOverdue ? "warning" : "healthy") : "maintenance"}">${enabled ? (isOverdue ? "overdue" : "active") : "disabled"}</span>
            <small>${escapeHtml(s.cron_expression)}</small>
            <small>${s.target_vusers} vusers / ${s.duration_minutes}m</small>
            <small>Next: ${nextRun.toLocaleString()}</small>
            ${s.last_run_at ? `<small>Last: ${new Date(s.last_run_at).toLocaleString()}</small>` : ""}
          </div>
          <div class="entity-actions">
            <button type="button" class="btn-edit" data-edit="schedule" data-id="${s.id}">Edit</button>
            <button type="button" class="btn-delete" data-delete="schedules" data-id="${s.id}">Delete</button>
          </div>
        </article>
      `;
    })
    .join("");
}

function renderTrends() {
  if (!state.trends) return;
  const t = state.trends;
  const s = t.summary;

  document.querySelector("#trendSummary").innerHTML = `
    <article><span>${s.totalCompletedRuns}</span><small>Completed Runs</small></article>
    <article><span>${Math.round(s.avgP95)}ms</span><small>Avg p95</small></article>
    <article><span>${s.avgErrorRate.toFixed(1)}%</span><small>Avg Error Rate</small></article>
    <article><span>${s.avgApdex.toFixed(2)}</span><small>Avg Apdex</small></article>
  `;

  const list = document.querySelector("#trendList");
  if (!t.scenarioTrends.length) {
    list.innerHTML = '<p style="color:var(--muted);padding:16px;">Complete runs with results to see trend analysis.</p>';
    return;
  }

  list.innerHTML = t.scenarioTrends
    .map((sc) => {
      const latest = sc.runs[0];
      const hasBaseline = sc.baseline !== null;
      const regressionCount = sc.regressions.length;
      const sparkline = sc.runs
        .slice(0, 10)
        .reverse()
        .map((r) => r.p95)
        .join(",");

      const barPoints = sc.runs
        .slice(0, 10)
        .reverse()
        .map((r, i) => {
          const x = 10 + (i * 80) / Math.max(sc.runs.length - 1, 1);
          const maxP95 = Math.max(...sc.runs.map((run) => run.p95), sc.slaP95);
          const y = 80 - (r.p95 / maxP95) * 60;
          return `${x},${y}`;
        })
        .join(" ");

      const slaY = 80 - (sc.slaP95 / Math.max(...sc.runs.map((run) => run.p95), sc.slaP95)) * 60;

      let regressionBadges = "";
      if (regressionCount > 0) {
        const critCount = sc.regressions.filter((r) => r.severity === "critical").length;
        const warnCount = regressionCount - critCount;
        if (critCount) regressionBadges += `<span class="pill critical">${critCount} critical</span>`;
        if (warnCount) regressionBadges += `<span class="pill warning">${warnCount} warning</span>`;
      }

      const gateColor = latest.qualityGate === "passed" ? "var(--green)" : "var(--red)";

      return `
        <article class="trend-card">
          <div class="run-header">
            <div>
              <strong>${escapeHtml(sc.scenarioName)}</strong>
              <p>${escapeHtml(sc.engine)} / SLA p95: ${sc.slaP95}ms / ${sc.runCount} runs</p>
            </div>
            <div class="pill-row">
              ${pill(sc.engine)}
              <span class="pill" style="background:${gateColor}15;color:${gateColor}">${latest.qualityGate}</span>
              ${regressionBadges}
            </div>
          </div>
          <div class="trend-metrics">
            <div class="trend-metric">
              <small>p95</small>
              <strong>${latest.p95}ms</strong>
              ${hasBaseline ? `<span class="${latest.p95 > sc.baseline.p95 ? "trend-up" : "trend-down"}">${latest.p95 > sc.baseline.p95 ? "+" : ""}${Math.round(((latest.p95 - sc.baseline.p95) / sc.baseline.p95) * 100)}%</span>` : ""}
            </div>
            <div class="trend-metric">
              <small>Errors</small>
              <strong>${latest.errorRate}%</strong>
              ${hasBaseline ? `<span class="${latest.errorRate > sc.baseline.errorRate ? "trend-up" : "trend-down"}">${latest.errorRate > sc.baseline.errorRate ? "+" : ""}${(latest.errorRate - sc.baseline.errorRate).toFixed(1)}pp</span>` : ""}
            </div>
            <div class="trend-metric">
              <small>Throughput</small>
              <strong>${latest.throughput}rps</strong>
              ${hasBaseline ? `<span class="${latest.throughput < sc.baseline.throughput ? "trend-up" : "trend-down"}">${latest.throughput < sc.baseline.throughput ? "" : "+"}${Math.round(((latest.throughput - sc.baseline.throughput) / sc.baseline.throughput) * 100)}%</span>` : ""}
            </div>
            <div class="trend-metric">
              <small>Apdex</small>
              <strong>${latest.apdex}</strong>
            </div>
          </div>
          ${sc.runs.length > 1 ? `
          <svg class="trend-sparkline" viewBox="0 0 100 90" preserveAspectRatio="none">
            <line x1="0" y1="${slaY}" x2="100" y2="${slaY}" stroke="var(--red)" stroke-width="0.5" stroke-dasharray="2,2" opacity="0.5"/>
            <text x="100" y="${slaY - 2}" fill="var(--red)" font-size="4" text-anchor="end" opacity="0.6">SLA</text>
            <polyline points="${barPoints}" fill="none" stroke="var(--teal)" stroke-width="1.5"/>
            ${sc.runs.slice(0, 10).reverse().map((r, i) => {
              const x = 10 + (i * 80) / Math.max(sc.runs.length - 1, 1);
              const maxP95 = Math.max(...sc.runs.map((run) => run.p95), sc.slaP95);
              const y = 80 - (r.p95 / maxP95) * 60;
              return `<circle cx="${x}" cy="${y}" r="2" fill="${r.qualityGate === "passed" ? "var(--teal)" : "var(--red)"}"/>`;
            }).join("")}
          </svg>
          ` : ""}
          ${sc.regressions.length ? `
          <div class="regression-list">
            ${sc.regressions.map((reg) => `
              <div class="regression-item ${reg.severity}">
                <strong>${escapeHtml(reg.metric)}</strong>
                <span>${escapeHtml(reg.message)}</span>
              </div>
            `).join("")}
          </div>
          ` : ""}
          <div class="run-body">
            ${sc.runs.slice(0, 5).map((r) => `<span title="${escapeHtml(r.runName)}">#${r.runId} p95=${r.p95}ms</span>`).join("")}
          </div>
        </article>
      `;
    })
    .join("");
}

/* ─── Modal & Form System ─── */

const FORMS = {
  project: {
    title: "Project",
    fields: [
      { name: "name", label: "Name", type: "text", required: true },
      { name: "owner", label: "Owner", type: "text", required: true },
      { name: "business_unit", label: "Business Unit", type: "text", required: true },
      { name: "risk_tier", label: "Risk Tier", type: "select", options: ["low", "medium", "high", "critical"], required: true },
    ],
  },
  environment: {
    title: "Environment",
    fields: [
      { name: "name", label: "Name", type: "text", required: true },
      { name: "region", label: "Region", type: "text", required: true },
      { name: "classification", label: "Classification", type: "text", required: true },
      { name: "readiness_status", label: "Readiness", type: "select", options: ["ready", "warning", "not_ready"] },
      { name: "service_virtualization_enabled", label: "Virtualization", type: "select", options: [{ value: 1, label: "On" }, { value: 0, label: "Off" }] },
      { name: "data_residency", label: "Data Residency", type: "text" },
    ],
  },
  scenario: {
    title: "Scenario",
    fields: [
      { name: "project_id", label: "Project", type: "select", optionsKey: "projects", required: true },
      { name: "name", label: "Name", type: "text", required: true },
      { name: "engine", label: "Engine", type: "select", options: ["JMeter", "k6", "Gatling", "Locust", "Playwright"], required: true },
      { name: "test_type", label: "Test Type", type: "select", options: ["load", "stress", "spike", "soak", "smoke", "browser"], required: true },
      { name: "workload_mix", label: "Workload Mix", type: "text", required: true },
      { name: "script_repository", label: "Script Repository", type: "text", required: true },
      { name: "target_endpoint", label: "Target Endpoint", type: "text", required: true },
      { name: "sla_p95_ms", label: "SLA p95 (ms)", type: "number", required: true },
      { name: "max_error_rate", label: "Max Error Rate (%)", type: "number", step: "0.1", required: true },
    ],
  },
  pool: {
    title: "Load Generator Pool",
    fields: [
      { name: "name", label: "Name", type: "text", required: true },
      { name: "region", label: "Region", type: "text", required: true },
      { name: "max_vusers", label: "Max Virtual Users", type: "number", required: true },
      { name: "engines", label: "Engines (comma-separated)", type: "text" },
      { name: "status", label: "Status", type: "select", options: ["healthy", "maintenance", "offline"] },
    ],
  },
  policy: {
    title: "Policy",
    fields: [
      { name: "name", label: "Name", type: "text", required: true },
      { name: "scope", label: "Scope", type: "select", options: ["execution", "result", "ai", "scheduling", "data"], required: true },
      { name: "rule", label: "Rule", type: "textarea", required: true },
      { name: "severity", label: "Severity", type: "select", options: ["blocking", "warning", "info"], required: true },
      { name: "enabled", label: "Enabled", type: "select", options: [{ value: 1, label: "Yes" }, { value: 0, label: "No" }] },
    ],
  },
  run: {
    title: "Create Test Run",
    fields: [
      { name: "scenarioId", label: "Scenario", type: "select", optionsKey: "scenarios", required: true },
      { name: "environmentId", label: "Environment", type: "select", optionsKey: "environments", required: true },
      { name: "name", label: "Run Name", type: "text", required: true },
      { name: "targetVusers", label: "Target Virtual Users", type: "number", required: true },
      { name: "durationMinutes", label: "Duration (minutes)", type: "number", required: true },
      { name: "loadProfile", label: "Load Profile", type: "textarea" },
    ],
  },
  schedule: {
    title: "Create Schedule",
    fields: [
      { name: "name", label: "Schedule Name", type: "text", required: true },
      { name: "scenario_id", label: "Scenario", type: "select", optionsKey: "scenarios", required: true },
      { name: "environment_id", label: "Environment", type: "select", optionsKey: "environments", required: true },
      { name: "target_vusers", label: "Target Virtual Users", type: "number", required: true },
      { name: "duration_minutes", label: "Duration (minutes)", type: "number", required: true },
      { name: "load_profile", label: "Load Profile", type: "textarea", required: true },
      { name: "cron_expression", label: "Cron (min hour day month weekday)", type: "text", required: true },
      { name: "enabled", label: "Enabled", type: "select", options: [{ value: 1, label: "Yes" }, { value: 0, label: "No" }] },
    ],
  },
  user: {
    title: "Create User",
    fields: [
      { name: "username", label: "Username", type: "text", required: true },
      { name: "display_name", label: "Display Name", type: "text", required: true },
      { name: "role", label: "Role", type: "select", options: ["admin", "performance_lead", "engineer", "viewer"], required: true },
      { name: "email", label: "Email", type: "text" },
    ],
  },
  execution_window: {
    title: "Create Execution Window",
    fields: [
      { name: "name", label: "Name", type: "text", required: true },
      { name: "type", label: "Type", type: "select", options: ["window", "blackout"], required: true },
      { name: "day_of_week", label: "Day of Week (0=Sun, 6=Sat, blank=any)", type: "number" },
      { name: "start_hour", label: "Start Hour (0-23)", type: "number", required: true },
      { name: "end_hour", label: "End Hour (0-23)", type: "number", required: true },
      { name: "environment_id", label: "Environment (blank=all)", type: "select", optionsKey: "environments" },
      { name: "enabled", label: "Enabled", type: "select", options: [{ value: 1, label: "Yes" }, { value: 0, label: "No" }] },
    ],
  },
  webhook: {
    title: "Create Webhook",
    fields: [
      { name: "name", label: "Name", type: "text", required: true },
      { name: "url", label: "URL", type: "text", required: true },
      { name: "event", label: "Event", type: "select", options: ["run.completed", "run.failed", "run.started"], required: true },
      { name: "secret", label: "Secret (optional)", type: "text" },
      { name: "enabled", label: "Enabled", type: "select", options: [{ value: 1, label: "Yes" }, { value: 0, label: "No" }] },
    ],
  },
  application: {
    title: "Register Application",
    fields: [
      { name: "name", label: "Application Name", type: "text", required: true },
      { name: "endpoint", label: "Health Check URL", type: "text", required: true },
      { name: "team", label: "Team", type: "text" },
      { name: "environment", label: "Environment", type: "text" },
      { name: "status", label: "Status", type: "select", options: ["active", "deprecated", "maintenance"] },
    ],
  },
};

let currentFormType = null;
let currentEditId = null;

function openModal(formType, editId = null) {
  currentFormType = formType;
  currentEditId = editId;
  const config = FORMS[formType];
  const modal = document.querySelector("#modal");
  const title = document.querySelector("#modalTitle");
  const form = document.querySelector("#entityForm");

  title.textContent = editId ? `Edit ${config.title}` : `Create ${config.title}`;

  let html = "";
  for (const field of config.fields) {
    let value = "";
    if (editId) {
      const source = state[formType === "pool" ? "pools" : formType === "run" ? "runs" : formType + "s"]?.find((e) => e.id == editId);
      if (source) value = source[field.name] ?? "";
    }
    html += `<label>${escapeHtml(field.label)}`;
    if (field.type === "select") {
      html += `<select name="${field.name}" ${field.required ? "required" : ""}>`;
      if (field.optionsKey) {
        const list = state[field.optionsKey] || [];
        for (const opt of list) {
          const optVal = opt.id;
          const optLabel = opt.name || opt.label || optVal;
          const sel = String(optVal) === String(value) ? "selected" : "";
          html += `<option value="${optVal}" ${sel}>${escapeHtml(optLabel)}</option>`;
        }
      } else {
        for (const opt of field.options) {
          if (typeof opt === "object") {
            const sel = String(opt.value) === String(value) ? "selected" : "";
            html += `<option value="${opt.value}" ${sel}>${escapeHtml(opt.label)}</option>`;
          } else {
            const sel = opt === value ? "selected" : "";
            html += `<option value="${opt}" ${sel}>${escapeHtml(opt)}</option>`;
          }
        }
      }
      html += `</select>`;
    } else if (field.type === "textarea") {
      html += `<textarea name="${field.name}" ${field.required ? "required" : ""}>${escapeHtml(value)}</textarea>`;
    } else {
      const step = field.step ? ` step="${field.step}"` : "";
      const numType = field.type === "number" ? ' type="number"' : ' type="text"';
      html += `<input name="${field.name}"${numType}${step} value="${escapeHtml(value)}" ${field.required ? "required" : ""}>`;
    }
    html += `</label>`;
  }
  html += `
    <div class="form-actions">
      <button type="button" class="secondary" id="formCancel">Cancel</button>
      <button type="submit">${editId ? "Save Changes" : "Create"}</button>
    </div>`;
  form.innerHTML = html;
  modal.classList.remove("hidden");

  document.querySelector("#formCancel").addEventListener("click", closeModal);
}

function closeModal() {
  document.querySelector("#modal").classList.add("hidden");
  currentFormType = null;
  currentEditId = null;
}

async function submitForm(event) {
  event.preventDefault();
  const form = event.target;
  const data = {};
  for (const el of form.elements) {
    if (!el.name) continue;
    let val = el.value;
    if (el.type === "number") val = Number(val);
    data[el.name] = val;
  }

  // For pools, convert comma-separated engines to JSON array
  if (currentFormType === "pool" && typeof data.engines === "string") {
    data.engines = JSON.stringify(data.engines.split(",").map((s) => s.trim()).filter(Boolean));
  }

  // For scenario, set projectId from selected project
  if (currentFormType === "scenario" && data.project_id) {
    data.project_id = Number(data.project_id);
  }

  // For schedule, convert ID fields to numbers
  if (currentFormType === "schedule") {
    if (data.scenario_id) data.scenario_id = Number(data.scenario_id);
    if (data.environment_id) data.environment_id = Number(data.environment_id);
    if (data.target_vusers) data.target_vusers = Number(data.target_vusers);
    if (data.duration_minutes) data.duration_minutes = Number(data.duration_minutes);
  }

  try {
    if (currentFormType === "run") {
      await fetchJson("/api/runs", { method: "POST", body: JSON.stringify(data) });
    } else if (currentEditId) {
      const endpoint = currentFormType === "pool" ? "pools" : currentFormType + "s";
      await fetchJson(`/api/${endpoint}/${currentEditId}`, { method: "PUT", body: JSON.stringify(data) });
    } else {
      const endpoint = currentFormType === "pool" ? "pools" : currentFormType + "s";
      await fetchJson(`/api/${endpoint}`, { method: "POST", body: JSON.stringify(data) });
    }
    closeModal();
    await initialize();
  } catch (error) {
    alert(error.message);
  }
}

async function deleteEntity(endpoint, id) {
  if (!confirm(`Delete this item?`)) return;
  try {
    await fetchJson(`/api/${endpoint}/${id}`, { method: "DELETE" });
    await initialize();
  } catch (error) {
    alert(error.message);
  }
}

/* ─── Run Actions ─── */

async function createRun() {
  openModal("run");
}

async function runAction(action, id) {
  if (action === "baseline") {
    await fetchJson(`/api/runs/${id}/baseline`, {
      method: "POST",
      body: JSON.stringify({ approved_by: state.authUser?.username || "admin" }),
    });
    await initialize();
    return;
  }
  const payload = action === "approve" ? { reviewer: "performance-lead", reason: "Approved from dashboard." } : {};
  await fetchJson(`/api/runs/${id}/${action}`, { method: "POST", body: JSON.stringify(payload) });
  await initialize();
}

/* ─── Admin Dashboard ─── */

let concurrentRunsChartInstance = null;
let projectLimitChartInstance = null;
let statusGaugeInstance = null;
let stateGaugeInstance = null;
let purposeGaugeInstance = null;
let typeGaugeInstance = null;

async function renderAdminDashboard() {
  let adminData = null;
  try {
    adminData = await fetchJson("/api/admin/stats");
  } catch (e) {
    console.warn("Admin stats unavailable:", e);
  }
  renderHostsGauges(adminData);
  renderAdminRuns();
  renderConcurrentRunsChart(adminData);
  renderProjectLimitChart(adminData);
}

function renderHostsGauges(adminData) {
  const hostsData = adminData?.hosts || { healthy: 0, unavailable: 0, total: 0, active: 0, idle: 0, maintenance: 0, load_generator: 0, controller: 0, monitoring: 0, physical: 0, cloud: 0, container: 0 };

  document.querySelector("#hostsCount").textContent = `(${hostsData.total})`;

  renderGaugeChart("statusGauge", hostsData.healthy, hostsData.total || 1, "#28a745", "#dc3545");
  renderGaugeChart("stateGauge", hostsData.active, hostsData.total || 1, "#00a3e0", "#ffc107");
  renderGaugeChart("purposeGauge", hostsData.load_generator, hostsData.total || 1, "#6f42c1", "#17a2b8");
  renderGaugeChart("typeGauge", hostsData.cloud, hostsData.total || 1, "#28a745", "#dc3545");
}

function renderGaugeChart(canvasId, value, total, color1, color2) {
  const canvas = document.getElementById(canvasId);
  if (!canvas) return;

  const ctx = canvas.getContext('2d');
  const centerX = canvas.width / 2;
  const centerY = canvas.height / 2;
  const radius = Math.min(centerX, centerY) - 10;
  const lineWidth = 12;

  // Clear canvas
  ctx.clearRect(0, 0, canvas.width, canvas.height);

  // Calculate percentage
  const percentage = total > 0 ? value / total : 0;
  const startAngle = -Math.PI / 2;
  const endAngle = startAngle + (2 * Math.PI * percentage);

  // Draw background arc
  ctx.beginPath();
  ctx.arc(centerX, centerY, radius, 0, 2 * Math.PI);
  ctx.strokeStyle = '#e9ecef';
  ctx.lineWidth = lineWidth;
  ctx.stroke();

  // Draw value arc
  ctx.beginPath();
  ctx.arc(centerX, centerY, radius, startAngle, endAngle);
  ctx.strokeStyle = color1;
  ctx.lineWidth = lineWidth;
  ctx.lineCap = 'round';
  ctx.stroke();

  // Draw percentage text
  ctx.fillStyle = '#2c3e50';
  ctx.font = 'bold 24px Inter';
  ctx.textAlign = 'center';
  ctx.textBaseline = 'middle';
  const percentText = Math.round(percentage * 100) + '%';
  ctx.fillText(percentText, centerX, centerY - 8);

  // Draw count text
  ctx.fillStyle = '#6c757d';
  ctx.font = '14px Inter';
  ctx.fillText(`${value}/${total}`, centerX, centerY + 12);
}

function renderAdminRuns() {
  const container = document.querySelector("#adminRunsList");
  if (!container) return;

  const finishedRuns = state.runs.filter(r => r.status === 'completed').slice(0, 10);
  const runningRuns = state.runs.filter(r => r.status === 'running');
  const scheduledRuns = state.runs.filter(r => r.status === 'ready' || r.status === 'pending');

  // Update tab counts
  const tabs = document.querySelectorAll('.runs-tab');
  if (tabs.length >= 3) {
    tabs[0].textContent = `Last Finished (${finishedRuns.length})`;
    tabs[1].textContent = `Currently Running (${runningRuns.length})`;
    tabs[2].textContent = `Scheduled (${scheduledRuns.length})`;
  }

  // Render finished runs by default
  renderRunsTable(container, finishedRuns);
}

function renderRunsTable(container, runs) {
  if (runs.length === 0) {
    container.innerHTML = '<p style="color:var(--text-secondary);padding:20px;text-align:center;">No runs to display</p>';
    return;
  }

  let html = `
    <div class="runs-table-container">
    <table class="runs-table">
      <thead>
        <tr>
          <th>Test Name</th>
          <th>Engine</th>
          <th>Project</th>
          <th>Status</th>
          <th>Last Updated</th>
        </tr>
      </thead>
      <tbody>
  `;

  runs.forEach(run => {
    const statusClass = run.status === 'completed' ? 'finished' : run.status === 'running' ? 'running' : 'scheduled';
    const statusText = run.status === 'completed' ? 'Finished' : run.status === 'running' ? 'Running' : run.status;
    const finishedTime = run.completed_at ? new Date(run.completed_at).toLocaleString() :
                        run.started_at ? new Date(run.started_at).toLocaleString() :
                        run.created_at ? new Date(run.created_at).toLocaleString() :
                        'N/A';

    html += `
      <tr>
        <td>
          <div class="run-name-cell">${escapeHtml(run.name)}</div>
          <div class="run-meta-cell">#${run.id} / ${escapeHtml(run.environment_name || "N/A")}</div>
        </td>
        <td>${engineBadge(run.engine)}</td>
        <td>${escapeHtml(run.project_name || 'N/A')}</td>
        <td><span class="run-status ${statusClass}">${escapeHtml(statusText)}</span></td>
        <td>${finishedTime}</td>
      </tr>
    `;
  });

  html += '</tbody></table></div>';
  container.innerHTML = html;
}

function renderConcurrentRunsChart(adminData) {
  const canvas = document.getElementById('concurrentRunsChart');
  if (!canvas) return;

  const concurrentData = adminData?.concurrentRuns || [];
  let labels = concurrentData.map(d => d.day);
  let data = concurrentData.map(d => d.count);

  if (labels.length === 0) {
    labels = ['No data'];
    data = [0];
  }

  if (concurrentRunsChartInstance) concurrentRunsChartInstance.destroy();

  concurrentRunsChartInstance = new Chart(canvas, {
    type: 'bar',
    data: {
      labels: labels,
      datasets: [{
        label: 'Concurrent Runs',
        data: data,
        backgroundColor: '#00a3e0',
        borderRadius: 4,
      }]
    },
    options: {
      responsive: true,
      maintainAspectRatio: false,
      plugins: {
        legend: { display: false }
      },
      scales: {
        y: {
          beginAtZero: true,
          ticks: {
            stepSize: 1
          }
        }
      }
    }
  });
}

function renderProjectLimitChart(adminData) {
  const canvas = document.getElementById('projectLimitChart');
  if (!canvas) return;

  const projectStats = adminData?.projectStats || [];
  const labels = projectStats.map(p => p.name);
  const limitData = projectStats.map(p => p.limit);
  const maxData = projectStats.map(p => p.runs);
  const avgData = projectStats.map(p => Math.round(p.runs * 0.7 * 10) / 10);

  if (projectLimitChartInstance) projectLimitChartInstance.destroy();

  projectLimitChartInstance = new Chart(canvas, {
    type: 'line',
    data: {
      labels: labels,
      datasets: [
        {
          label: 'Project Concurrency Limit',
          data: limitData,
          borderColor: '#00a3e0',
          backgroundColor: 'rgba(0, 163, 224, 0.1)',
          tension: 0.3,
          fill: true
        },
        {
          label: 'Max Concurrent',
          data: maxData,
          borderColor: '#dc3545',
          backgroundColor: 'rgba(220, 53, 69, 0.1)',
          tension: 0.3,
          fill: true
        },
        {
          label: 'Avg Concurrent',
          data: avgData,
          borderColor: '#ffc107',
          backgroundColor: 'rgba(255, 193, 7, 0.1)',
          tension: 0.3,
          fill: true
        }
      ]
    },
    options: {
      responsive: true,
      maintainAspectRatio: false,
      plugins: {
        legend: { display: false }
      },
      scales: {
        y: {
          beginAtZero: true
        }
      }
    }
  });
}

/* ─── Init ─── */

async function loginWithCredentials(username, password) {
  const response = await fetch("/api/auth/login", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ username, password }),
  });
  const data = await response.json();
  if (!response.ok) throw new Error(data.error || "Login failed");
  state.authToken = data.token;
  state.authUser = data.user;
  localStorage.setItem("mr_token", data.token);
  localStorage.setItem("mr_user", JSON.stringify(data.user));
  return data;
}

function logout() {
  state.authToken = null;
  state.authUser = null;
  localStorage.removeItem("mr_token");
  localStorage.removeItem("mr_user");
  showLoginScreen();
}

function showLoginScreen() {
  const loginOverlay = document.getElementById("loginOverlay");
  const appShell = document.getElementById("appShell");
  if (loginOverlay) loginOverlay.style.display = "flex";
  if (appShell) appShell.style.display = "none";
}

function hideLoginScreen() {
  const loginOverlay = document.getElementById("loginOverlay");
  const appShell = document.getElementById("appShell");
  if (loginOverlay) loginOverlay.style.display = "none";
  if (appShell) appShell.style.display = "";
}

async function tryRestoreSession() {
  const token = localStorage.getItem("mr_token");
  const userStr = localStorage.getItem("mr_user");
  if (!token || !userStr) return false;
  try {
    const resp = await fetch("/api/auth/me", {
      headers: { "Authorization": "Bearer " + token },
    });
    if (!resp.ok) return false;
    state.authToken = token;
    state.authUser = JSON.parse(userStr);
    return true;
  } catch {
    return false;
  }
}

function applyUserFromAuth() {
  if (!state.authUser) return;
  const role = state.authUser.role;
  currentUser = { role, ...ROLES[role] };
  document.body.className = "role-" + role;
  const avatarEl = document.querySelector("#userAvatar");
  const nameEl = document.querySelector("#userName");
  const selectEl = document.querySelector("#userSelect");
  if (avatarEl) avatarEl.textContent = currentUser.initials;
  if (nameEl) nameEl.textContent = state.authUser.display_name || currentUser.label;
  if (selectEl) selectEl.value = role;
}

async function initialize() {
  const [health, dashboard, projects, scenarios, runs, pools, environments, results, policies, ai, audit, trends, schedules] = await Promise.all([
    fetchJson("/api/health"),
    fetchJson("/api/dashboard"),
    fetchJson("/api/projects"),
    fetchJson("/api/scenarios"),
    fetchJson("/api/runs"),
    fetchJson("/api/pools"),
    fetchJson("/api/environments"),
    fetchJson("/api/results"),
    fetchJson("/api/policies"),
    fetchJson("/api/ai/recommendations"),
    fetchJson("/api/audit"),
    fetchJson("/api/trends"),
    fetchJson("/api/schedules"),
  ]);

  state.health = health;
  state.dashboard = dashboard;
  state.projects = projects.projects;
  state.scenarios = scenarios.scenarios;
  state.runs = runs.runs;
  state.pools = pools.pools;
  state.environments = environments.environments;
  state.results = results.results;
  state.policies = policies.policies;
  state.ai = ai.recommendations;
  state.audit = audit.events;
  state.trends = trends;
  state.schedules = schedules.schedules;

  renderHealth();
  renderMetrics();
  renderSummaryStats();
  renderRuns();
  renderProjects();
  renderScenarios();
  renderPools();
  renderEnvironments();
  renderResults();
  renderAi();
  renderPolicies();
  renderAudit();
  renderSchedules();
  renderWindows();
  renderBaselines();
  renderTemplates();
  renderWebhooks();
  renderImpact();
  renderApplications();
  renderLoadProfiles();
  renderTrends();
  renderUsers();
  renderCharts();
  renderAdminDashboard();
  applyPermissions();

  try {
    const activeData = await fetchJson("/api/runs/active");
    state.liveRuns = activeData.runs;
    renderLive();
    if (state.liveRuns.length) {
      startLiveMonitoring();
    }
  } catch (e) {
    console.error("Failed to fetch active runs:", e);
  }

  populateCompareSelects();
}

/* ─── Event Listeners ─── */

// Initialize navigation on page load
document.addEventListener('DOMContentLoaded', async () => {
  initNavigation();
  document.getElementById("logoutBtn")?.addEventListener("click", logout);
  const sessionRestored = await tryRestoreSession();
  if (sessionRestored) {
    applyUserFromAuth();
    hideLoginScreen();
    initialize().catch((error) => {
      console.error(error);
    });
  } else {
    showLoginScreen();
    const loginForm = document.getElementById("loginForm");
    if (loginForm) {
      loginForm.addEventListener("submit", async (e) => {
        e.preventDefault();
        const username = document.getElementById("loginUsername").value;
        const password = document.getElementById("loginPassword").value;
        const errorEl = document.getElementById("loginError");
        try {
          await loginWithCredentials(username, password);
          applyUserFromAuth();
          hideLoginScreen();
          initialize().catch((error) => {
            console.error(error);
          });
        } catch (err) {
          if (errorEl) {
            errorEl.textContent = err.message;
            errorEl.style.display = "block";
          }
        }
      });
    }
  }
});

// User role selector
document.querySelector("#userSelect").addEventListener("change", (e) => {
  switchRole(e.target.value);
});

document.querySelector("#createRunButton").addEventListener("click", () => {
  createRun().catch((error) => alert(error.message));
});

document.querySelector("#refreshButton").addEventListener("click", () => {
  initialize().catch((error) => alert(error.message));
});

document.querySelector("#filterApply").addEventListener("click", () => {
  applyRunFilters();
});

document.querySelector("#filterClear").addEventListener("click", () => {
  clearRunFilters();
});

document.querySelector("#filterSearch").addEventListener("keypress", (e) => {
  if (e.key === "Enter") applyRunFilters();
});

document.querySelector("#compareButton").addEventListener("click", () => {
  compareRuns().catch((error) => alert(error.message));
});

// Run actions (approve, start, complete, cancel)
document.querySelector("#runsList").addEventListener("click", (event) => {
  const button = event.target.closest("button[data-action]");
  if (!button) return;
  runAction(button.dataset.action, button.dataset.id).catch((error) => alert(error.message));
});

// Add entity buttons (+ Project, + Scenario, etc.)
document.querySelectorAll("[data-form]").forEach((btn) => {
  btn.addEventListener("click", () => openModal(btn.dataset.form));
});

// Edit buttons
document.addEventListener("click", (event) => {
  const editBtn = event.target.closest("[data-edit]");
  if (editBtn) {
    openModal(editBtn.dataset.edit, editBtn.dataset.id);
  }
});

// Delete buttons
document.addEventListener("click", (event) => {
  const deleteBtn = event.target.closest("[data-delete]");
  if (deleteBtn) {
    deleteEntity(deleteBtn.dataset.delete, deleteBtn.dataset.id).catch((error) => alert(error.message));
  }
});

// Modal close
document.querySelector("#modalClose").addEventListener("click", closeModal);
document.querySelector(".modal-backdrop").addEventListener("click", closeModal);

// Form submit
document.querySelector("#entityForm").addEventListener("submit", submitForm);

// Escape key closes modal
document.addEventListener("keydown", (event) => {
  if (event.key === "Escape" && !document.querySelector("#modal").classList.contains("hidden")) {
    closeModal();
  }
});

/* ─── Admin Dashboard Event Listeners ─── */

// Refresh admin dashboard
document.querySelector("#refreshAdminBtn")?.addEventListener("click", () => {
  initialize().catch((error) => alert(error.message));
});

// Runs tabs
document.querySelectorAll(".runs-tab").forEach(tab => {
  tab.addEventListener("click", () => {

    // Update active tab
    document.querySelectorAll(".runs-tab").forEach(t => t.classList.remove("active"));
    tab.classList.add("active");

    // Filter runs based on tab
    const tabType = tab.getAttribute("data-runs-tab");
    const container = document.querySelector("#adminRunsList") || document.querySelector("#adminRunResultsList");

    // Search within admin runs (client-side filter on top of tab filter)
    const adminSearchValue = (document.querySelector('#adminRunSearch')?.value || '').trim().toLowerCase();


    // Bind admin run search to active tab re-render (only once)
    const searchInput = document.querySelector('#adminRunSearch');
    if (searchInput && !searchInput.dataset.bound) {
      searchInput.dataset.bound = '1';
      searchInput.addEventListener('keypress', (e) => {
        if (e.key === 'Enter') {
          const activeTab = document.querySelector('.runs-tab.active');
          if (activeTab) activeTab.click();
        }
      });
      searchInput.addEventListener('input', () => {
        const activeTab = document.querySelector('.runs-tab.active');
        if (activeTab) activeTab.click();
      });
    }

    let runs = [];


    if (tabType === "finished") {
      runs = state.runs.filter(r => r.status === 'completed').slice(0, 10);
    } else if (tabType === "running") {
      runs = state.runs.filter(r => r.status === 'running');
    } else if (tabType === "scheduled") {
      runs = state.runs.filter(r => r.status === 'ready' || r.status === 'pending');
    }

    if (adminSearchValue) {
      runs = runs.filter(r => {
        const haystack = `${r.name || ''} ${r.project_name || ''} ${r.environment_name || ''} ${r.engine || ''}`.toLowerCase();
        return haystack.includes(adminSearchValue);
      });
    }

    renderRunsTable(container, runs);

  });
});

/* ─── Logs ─── */

async function loadLogs() {
  const runId = document.querySelector("#logRunSelect").value;
  if (!runId) {
    document.querySelector("#logOutput").textContent = "Select a run first.";
    return;
  }
  document.querySelector("#logOutput").textContent = "Loading logs...";
  try {
    const data = await fetchJson(`/api/runs/${runId}/logs`);
    document.querySelector("#logOutput").textContent = data.logs || "No logs available.";
  } catch (error) {
    document.querySelector("#logOutput").textContent = `Error: ${error.message}`;
  }
}

document.querySelector("#loadLogsButton").addEventListener("click", loadLogs);
