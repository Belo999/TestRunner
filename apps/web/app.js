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
};

async function fetchJson(url, options = {}) {
  const response = await fetch(url, {
    headers: { "Content-Type": "application/json" },
    ...options,
  });

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

function pill(value) {
  return `<span class="pill ${escapeHtml(value)}">${escapeHtml(value)}</span>`;
}

function renderHealth() {
  const dependencies = Object.values(state.health.dependencies);
  const ready = dependencies.filter((item) => ["ok", "reachable", "configured"].includes(item.status)).length;
  document.querySelector("#health").textContent = `${state.health.status.toUpperCase()} - ${ready}/${dependencies.length} dependencies`;
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

function renderRuns() {
  const list = document.querySelector("#runsList");
  list.innerHTML = state.runs
    .map(
      (run) => `
        <article class="run">
          <div class="run-header">
            <div>
              <strong>${escapeHtml(run.name)}</strong>
              <p>${escapeHtml(run.project_name)} / ${escapeHtml(run.environment_name)} / ${escapeHtml(run.load_profile)}</p>
            </div>
            <div class="pill-row">
              ${pill(run.engine)}
              ${pill(run.status)}
              ${pill(run.quality_gate)}
            </div>
          </div>
          <div class="run-body">
            <span>${escapeHtml(run.target_vusers)} users</span>
            <span>${escapeHtml(run.duration_minutes)} min</span>
            <span>Risk ${escapeHtml(run.risk_score)}</span>
            <span>${escapeHtml(run.pool_name || "No pool")}</span>
          </div>
          <p>${escapeHtml(run.ai_summary)}</p>
          <div class="actions inline">
            <button type="button" data-action="approve" data-id="${run.id}">Approve</button>
            <button type="button" data-action="start" data-id="${run.id}">Start</button>
            <button type="button" data-action="complete" data-id="${run.id}">Complete</button>
            <button type="button" class="secondary" data-action="cancel" data-id="${run.id}">Cancel</button>
          </div>
        </article>
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
            <p>${escapeHtml(pool.region)} / ${escapeHtml(pool.engines.join(", "))}</p>
          </div>
          <div class="right">
            ${pill(pool.status)}
            <small>${escapeHtml(pool.current_reservation)} / ${escapeHtml(pool.max_vusers)} users</small>
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

async function createRun() {
  const scenario = state.scenarios[Math.floor(Math.random() * state.scenarios.length)] || state.scenarios[0];
  const environment = state.environments.find((item) => item.name === "performance") || state.environments[0];
  const targetVusers = scenario.engine === "Playwright" ? 800 : 2400;
  await fetchJson("/api/runs", {
    method: "POST",
    body: JSON.stringify({
      projectId: scenario.project_id,
      scenarioId: scenario.id,
      environmentId: environment.id,
      name: `${scenario.name} - Controlled Run`,
      targetVusers,
      durationMinutes: 35,
      loadProfile: `Ramp to ${targetVusers} users over 15 minutes, hold for 20 minutes`,
    }),
  });
  await initialize();
}

async function runAction(action, id) {
  const payload = action === "approve" ? { reviewer: "performance-lead", reason: "Approved from dashboard." } : {};
  await fetchJson(`/api/runs/${id}/${action}`, {
    method: "POST",
    body: JSON.stringify(payload),
  });
  await initialize();
}

async function initialize() {
  const [health, dashboard, projects, scenarios, runs, pools, environments, results, policies, ai, audit] = await Promise.all([
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

  renderHealth();
  renderMetrics();
  renderRuns();
  renderPools();
  renderEnvironments();
  renderResults();
  renderAi();
  renderPolicies();
  renderAudit();
}

document.querySelector("#createRunButton").addEventListener("click", () => {
  createRun().catch((error) => alert(error.message));
});

document.querySelector("#refreshButton").addEventListener("click", () => {
  initialize().catch((error) => alert(error.message));
});

document.querySelector("#runsList").addEventListener("click", (event) => {
  const button = event.target.closest("button[data-action]");
  if (!button) {
    return;
  }
  runAction(button.dataset.action, button.dataset.id).catch((error) => alert(error.message));
});

initialize().catch((error) => {
  document.querySelector("#health").textContent = "API unavailable";
  console.error(error);
});
