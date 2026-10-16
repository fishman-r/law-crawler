/* 只读观测界面：仅读取状态 API，不接收或保存 API 密钥。 */
(() => {
  "use strict";

  const $ = (id) => document.getElementById(id);
  const initialParams = new URLSearchParams(window.location.search);
  const safeParam = (value) => /^[A-Za-z0-9][A-Za-z0-9._-]{0,199}$/.test(value || "") ? value : null;
  const state = {
    overview: null, overviewError: null, lastSuccess: null, refreshing: false,
    sessionId: safeParam(initialParams.get("session")), session: null, sessionError: null, sessionLoading: false, sessionRequest: 0,
    batchId: safeParam(initialParams.get("batch")), batch: null, batchError: null, batchLoading: false, batchRequest: 0,
    stateLimit: 100, sessionRenderKey: null, batchRenderKey: null, sessionListRenderKey: null, batchListRenderKey: null,
  };
  const numberFormat = new Intl.NumberFormat("zh-CN", { maximumFractionDigits: 1 });
  const dateFormat = new Intl.DateTimeFormat("zh-CN", { month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit", second: "2-digit", hour12: false });
  const shortTimeFormat = new Intl.DateTimeFormat("zh-CN", { hour: "2-digit", minute: "2-digit", second: "2-digit", hour12: false });
  const sessionStatuses = { running: ["进行中", "info"], idle: ["空闲", "neutral"], waiting: ["等待输入", "warning"], ended: ["已结束", "neutral"], unknown: ["未知", "neutral"] };
  const batchStatuses = { completed: ["已完成", "positive"], completed_with_errors: ["完成 · 有失败", "warning"], planned_only: ["仅完成计划", "info"], pending: ["待执行", "neutral"], unfinished: ["未完成", "warning"], unknown: ["未知", "neutral"] };
  const taskStatuses = { success: ["成功", "positive"], completed: ["成功", "positive"], failed: ["失败", "negative"], error: ["失败", "negative"], pending: ["待处理", "neutral"], running: ["未结束", "info"], skipped: ["已跳过", "neutral"], unknown: ["未知", "neutral"] };
  const eventNames = { SessionStart: "会话开始", SessionEnd: "会话结束", PreToolUse: "准备调用工具", PostToolUse: "工具调用完成", PostToolUseFailure: "工具调用失败", SubagentStart: "子代理启动", SubagentStop: "子代理结束", UserPromptSubmit: "收到用户输入", Notification: "会话通知", Stop: "本轮处理结束", PermissionRequest: "请求授权", TaskCompleted: "任务完成" };

  function text(value, fallback = "—") {
    return (typeof value === "string" && value.trim()) || (typeof value === "number" && Number.isFinite(value)) ? String(value) : fallback;
  }
  function num(value) { return Number.isFinite(Number(value)) && value !== null && value !== "" ? Math.max(0, Number(value)) : 0; }
  function count(value) { return numberFormat.format(num(value)); }
  function list(value) { return Array.isArray(value) ? value : []; }
  function node(tag, className, value) {
    const result = document.createElement(tag);
    if (className) result.className = className;
    if (value !== undefined) result.textContent = text(value, "");
    return result;
  }
  function date(value) {
    if (value === undefined || value === null || value === "") return null;
    const normalized = typeof value === "number" ? (value < 1e12 ? value * 1000 : value) : value;
    const result = new Date(normalized);
    return Number.isNaN(result.getTime()) ? null : result;
  }
  function timestamp(value) { const parsed = date(value); return parsed ? dateFormat.format(parsed) : "未记录"; }
  function relative(value) {
    const parsed = date(value);
    if (!parsed) return "时间未记录";
    const seconds = Math.max(0, (Date.now() - parsed.getTime()) / 1000);
    if (seconds < 60) return "刚刚更新";
    if (seconds < 3600) return `${Math.floor(seconds / 60)} 分钟前`;
    if (seconds < 86400) return `${Math.floor(seconds / 3600)} 小时前`;
    if (seconds < 604800) return `${Math.floor(seconds / 86400)} 天前`;
    return timestamp(value);
  }
  function shortId(value) { const id = text(value, "未知 ID"); return id.length > 24 ? `${id.slice(0, 12)}…${id.slice(-7)}` : id; }
  function badge(value, statuses, extraLabel) {
    const [label, tone] = extraLabel || statuses[value] || statuses.unknown;
    const result = node("span", `status-badge tone-${tone}`);
    result.append(node("span", "status-dot"), node("span", "", label));
    result.firstChild.setAttribute("aria-hidden", "true");
    if (!statuses[value] && value) result.title = text(value);
    return result;
  }
  function sessionBadge(session) {
    const exited = session.process_alive === false && ["running", "waiting", "idle"].includes(session.status);
    return badge(session.status, sessionStatuses, exited ? ["进程已退出", "neutral"] : null);
  }
  function emptyState(title, description, type = "empty", retry) {
    const wrapper = node("div", `empty-state${type === "error" ? " is-error" : ""}`);
    wrapper.append(node("div", type === "loading" ? "loading-ring" : "empty-symbol", type === "loading" ? "" : type === "error" ? "!" : "◎"));
    wrapper.firstChild.setAttribute("aria-hidden", "true");
    wrapper.append(node("h3", "", title), node("p", "", description));
    if (retry) { const button = node("button", "text-button", "重新读取 ↻"); button.type = "button"; button.addEventListener("click", retry); wrapper.append(button); }
    return wrapper;
  }
  function notice(target, messages) {
    const items = list(messages).map((item) => typeof item === "object" && item ? text(item.message || item.reason, "") : text(item, "")).filter(Boolean);
    target.hidden = !items.length;
    target.replaceChildren(...items.map((item) => node("p", "", item)));
  }
  function fact(label, value) { const wrapper = node("div"); wrapper.append(node("dt", "", label), node("dd", "", value)); return wrapper; }
  function sorted(items) { return [...list(items)].sort((a, b) => (date(b.updated_at)?.getTime() || 0) - (date(a.updated_at)?.getTime() || 0)); }
  function updateUrl() {
    const url = new URL(window.location.href);
    for (const [key, value] of [["session", state.sessionId], ["batch", state.batchId]]) {
      if (value) url.searchParams.set(key, value); else url.searchParams.delete(key);
    }
    window.history.replaceState(null, "", `${url.pathname}${url.search}${url.hash}`);
  }

  async function request(path) {
    const controller = new AbortController();
    const timeout = window.setTimeout(() => controller.abort(), 8000);
    try {
      const response = await fetch(path, { signal: controller.signal, cache: "no-store", credentials: "same-origin", headers: { Accept: "application/json" } });
      if (!response.ok) throw new Error(`服务返回 HTTP ${response.status}`);
      const data = await response.json();
      if (!data || typeof data !== "object" || Array.isArray(data)) throw new Error("服务返回了无法识别的数据");
      return data;
    } catch (error) {
      if (error.name === "AbortError") throw new Error("读取超时，请检查观测服务连接。");
      if (error instanceof SyntaxError) throw new Error("服务返回的内容不是有效 JSON。");
      if (error instanceof TypeError) throw new Error("暂时无法连接观测服务，请检查网络或 SSH 隧道。");
      throw error;
    } finally { window.clearTimeout(timeout); }
  }

  function renderConnection() {
    $("refresh-button").disabled = state.refreshing;
    $("refresh-button").classList.toggle("is-refreshing", state.refreshing);
    $("refresh-label").textContent = state.refreshing ? "刷新中" : "刷新";
    const detailError = state.sessionError || state.batchError;
    const tone = state.overviewError ? "negative" : detailError ? "warning" : state.lastSuccess ? "positive" : "neutral";
    $("connection-status").className = `status-badge tone-${tone}`;
    $("connection-label").textContent = state.overviewError ? "连接异常" : detailError ? "详情异常" : state.lastSuccess ? "连接正常" : "正在连接";
    $("last-updated").textContent = state.lastSuccess ? `上次读取 ${shortTimeFormat.format(state.lastSuccess)} · 每 5 秒刷新` : "每 5 秒自动刷新";
    notice($("connection-banner"), state.overviewError ? [`${state.overviewError} ${state.lastSuccess ? "当前保留上次读取的数据，可能已过期。" : "尚未取得工作区数据。"}`] : []);
  }

  function renderOverview() {
    const overview = state.overview;
    if (!overview) {
      if (state.overviewError) {
        $("workspace-name").textContent = "工作区暂不可用";
        $("session-list").replaceChildren(emptyState("无法读取会话", "请检查观测服务是否运行，以及本地转发连接是否正常。", "error", refresh));
        $("batch-list").replaceChildren(emptyState("无法读取批次", "恢复服务后可手动刷新，系统也会继续自动重试。", "error", refresh));
        $("setup-checks").replaceChildren(node("div", "muted small-text", "连接服务后才能检查部署状态。"));
        $("system-resources").replaceChildren(node("div", "muted small-text", "连接服务后才能读取主机资源。"));
      }
      return;
    }
    $("workspace-name").textContent = text(overview.workspace_name, "法规采集工作区");
    for (const [id, field] of [["metric-sessions", "sessions"], ["metric-active", "active_sessions"], ["metric-batches", "batches"], ["metric-finished", "finished_batches"]]) {
      $(id).textContent = overview.counts?.[field] === undefined ? "—" : count(overview.counts[field]);
    }
    notice($("overview-warnings"), overview.warnings);
    renderSessionList(); renderBatchList(); renderSetup(overview.setup || {}); renderResources(overview.host || {});
  }

  function renderSessionList() {
    const sessions = sorted(state.overview?.sessions);
    $("session-count").textContent = `${count(sessions.length)} 条记录`;
    const renderKey = JSON.stringify(sessions) + sessions.map((session) => relative(session.updated_at)).join("|");
    if (state.sessionListRenderKey === `${state.sessionId}|${renderKey}`) return;
    state.sessionListRenderKey = `${state.sessionId}|${renderKey}`;
    if (!sessions.length) {
      $("session-list").replaceChildren(emptyState("还没有会话记录", "先通过 SSH 启动 Claude Code。部署好 hooks 后，新会话的工具活动会记录在这里。"));
      return;
    }
    $("session-list").replaceChildren(...sessions.map((session) => {
      const button = node("button", `entity-button${session.id === state.sessionId ? " is-selected" : ""}`);
      button.type = "button"; button.setAttribute("aria-pressed", String(session.id === state.sessionId));
      const top = node("div", "entity-top"); const title = node("span", "entity-title mono", shortId(session.id)); title.title = text(session.id);
      top.append(title, sessionBadge(session));
      const bottom = node("div", "entity-bottom"); bottom.append(node("span", "", relative(session.updated_at)), node("span", "event-count", `${count(session.event_count)} 个事件`));
      const tool = text(session.current_tool, "当前无工具运行");
      const meta = node("div", "entity-meta", `${tool}${list(session.subagents).length ? ` · ${list(session.subagents).length} 个子代理` : ""}`); meta.title = tool;
      button.append(top, meta, bottom); button.addEventListener("click", () => selectSession(session.id)); return button;
    }));
  }

  async function selectSession(id) {
    if (state.sessionId !== id) { state.sessionId = id; state.session = null; state.sessionError = null; }
    updateUrl(); renderSessionList(); await fetchSession();
  }
  async function fetchSession() {
    if (!state.sessionId) { renderSessionDetail(); return; }
    const id = state.sessionId; const token = ++state.sessionRequest;
    state.sessionLoading = true; renderSessionDetail();
    try {
      const data = await request(`/api/sessions/${encodeURIComponent(id)}`);
      if (token !== state.sessionRequest || state.sessionId !== id) return;
      state.session = data; state.sessionError = null;
    } catch (error) { if (token === state.sessionRequest && state.sessionId === id) state.sessionError = error.message; }
    finally { if (token === state.sessionRequest && state.sessionId === id) { state.sessionLoading = false; renderSessionDetail(); renderConnection(); } }
  }

  function renderSessionDetail() {
    const target = $("session-detail"); const session = state.session;
    if (!session) {
      state.sessionRenderKey = null;
      if (state.sessionLoading) target.replaceChildren(emptyState("正在读取会话", "读取工具事件与子代理记录…", "loading"));
      else if (state.sessionError) target.replaceChildren(emptyState("会话详情暂不可用", state.sessionError, "error", fetchSession));
      else target.replaceChildren(emptyState("等待会话记录", "通过 SSH 启动 Claude Code 并接入 hooks 后，可在这里查看会话详情。"));
      return;
    }
    const renderKey = JSON.stringify(session) + text(state.sessionError, "");
    if (state.sessionRenderKey === renderKey) return;
    state.sessionRenderKey = renderKey;
    const parts = [];
    const heading = node("div", "detail-heading"); const name = node("div", "min-width-zero");
    name.append(node("div", "detail-kicker", "SESSION DETAIL"), node("h3", "mono break-word", text(session.id, state.sessionId)), node("p", "muted small-text", `最近活动 ${timestamp(session.updated_at)}`)); heading.append(name, sessionBadge(session)); parts.push(heading);
    if (state.sessionError) { const warning = node("div", "notice notice-error detail-notice"); notice(warning, [`${state.sessionError} 当前显示上次取得的详情。`]); parts.push(warning); }
    const facts = node("dl", "fact-grid");
    facts.append(fact("开始时间", timestamp(session.started_at)), fact("当前工具", text(session.current_tool, "无工具运行")), fact("进程检测", session.process_alive === true ? "进程在线" : session.process_alive === false ? "进程未运行" : "未接入进程检测"), fact("事件数量", `${count(session.event_count)} 个`)); parts.push(facts);
    const skills = node("section", "detail-section"); skills.append(node("h4", "", "已识别 Skill")); const tags = node("div", "tag-row");
    const skillNames = list(session.skill_names).filter((item) => typeof item === "string" && item);
    tags.append(...(skillNames.length ? skillNames.map((skill) => node("span", "skill-tag", skill)) : [node("span", "muted small-text", "尚未识别 skill 调用")])); skills.append(tags); parts.push(skills);
    const agents = list(session.subagents);
    if (agents.length) {
      const section = node("section", "detail-section"); section.append(node("h4", "", `子代理 · ${agents.length}`));
      for (const agent of agents.slice(0, 30)) {
        const row = node("div", "subagent-row"); const object = typeof agent === "object" && agent ? agent : { id: agent };
        row.append(node("span", "subagent-name mono", `${text(object.agent_type || object.type, "子代理")} · ${text(object.agent_id || object.id, "未记录 ID")}`));
        if (object.status) row.append(badge(object.status, sessionStatuses)); section.append(row);
      }
      if (agents.length > 30) section.append(node("p", "muted small-text", `另有 ${agents.length - 30} 个子代理记录。`));
      parts.push(section);
    }
    const eventsSection = node("section", "detail-section"); eventsSection.append(node("h4", "", "最近事件"));
    const events = [...list(session.events)].sort((a, b) => (date(b.at)?.getTime() || 0) - (date(a.at)?.getTime() || 0));
    if (!events.length) eventsSection.append(node("p", "muted small-text", "当前会话还没有事件记录。"));
    else {
      const timeline = node("ol", "timeline");
      for (const event of events.slice(0, 60)) {
        const failed = event.event === "PostToolUseFailure" || ["error", "failed"].includes(event.status);
        const item = node("li", `timeline-item${failed ? " is-error" : ""}`); const body = node("div");
        item.append(node("span", "timeline-dot")); item.firstChild.setAttribute("aria-hidden", "true");
        body.append(node("div", "timeline-title", eventNames[event.event] || text(event.event, "活动事件")));
        const metadata = [];
        if (event.tool_name) metadata.push(`工具 ${text(event.tool_name)}`);
        if (event.skill_name) metadata.push(`Skill ${text(event.skill_name)}`);
        if (event.agent_type || event.agent_id) metadata.push(`子代理 ${text(event.agent_type || event.agent_id)}`);
        if (event.status) metadata.push(`状态 ${text(event.status)}`);
        if (metadata.length) body.append(node("div", "timeline-description", metadata.join(" · ")));
        const time = node("time", "timeline-time", date(event.at) ? shortTimeFormat.format(date(event.at)) : "—"); time.title = timestamp(event.at);
        if (date(event.at)) time.dateTime = date(event.at).toISOString();
        item.append(body, time); timeline.append(item);
      }
      eventsSection.append(timeline);
      if (events.length > 60) eventsSection.append(node("p", "muted small-text", `显示最近 60 条，共 ${count(events.length)} 条事件。`));
    }
    parts.push(eventsSection); target.replaceChildren(...parts);
  }

  function renderBatchList() {
    const batches = sorted(state.overview?.batches);
    $("batch-count").textContent = `${count(batches.length)} 个批次`;
    const renderKey = JSON.stringify(batches) + batches.map((batch) => relative(batch.updated_at)).join("|");
    if (state.batchListRenderKey === `${state.batchId}|${renderKey}`) return;
    state.batchListRenderKey = `${state.batchId}|${renderKey}`;
    if (!batches.length) { $("batch-list").replaceChildren(emptyState("还没有采集批次", "先通过 SSH 运行 law-crawler skill。生成批次记录后会自动显示。")); return; }
    $("batch-list").replaceChildren(...batches.map((batch) => {
      const button = node("button", `entity-button${batch.id === state.batchId ? " is-selected" : ""}`); button.type = "button"; button.setAttribute("aria-pressed", String(batch.id === state.batchId));
      const top = node("div", "entity-top"); top.append(node("span", "entity-title", text(batch.jurisdiction, "未记录地区")), badge(batch.status, batchStatuses));
      const meta = node("div", "entity-meta mono", shortId(batch.id)); meta.title = text(batch.id);
      const bottom = node("div", "entity-bottom"); bottom.append(node("span", "", relative(batch.updated_at)), node("span", "", text(batch.mode, "未记录模式")));
      const summary = node("div", "entity-subline", `${count(batch.counts?.success)} 成功 · ${count(batch.counts?.failed)} 失败 · ${count(num(batch.counts?.pending) + num(batch.counts?.running))} 未收敛`);
      button.append(top, meta, summary, bottom); button.addEventListener("click", () => selectBatch(batch.id)); return button;
    }));
  }
  async function selectBatch(id) {
    if (state.batchId !== id) {
      state.batchId = id; state.batch = null; state.batchError = null; state.stateLimit = 100;
      $("state-search").value = ""; $("state-filter").value = "all";
    }
    updateUrl(); renderBatchList(); await fetchBatch();
  }
  async function fetchBatch() {
    window.dispatchEvent(new CustomEvent("observer:batch-selection", { detail: { batchId: state.batchId } }));
    if (!state.batchId) { renderBatchDetail(); return; }
    const id = state.batchId; const token = ++state.batchRequest;
    state.batchLoading = true; renderBatchDetail();
    try {
      const data = await request(`/api/batches/${encodeURIComponent(id)}`);
      if (token !== state.batchRequest || state.batchId !== id) return;
      state.batch = data; state.batchError = null;
    } catch (error) { if (token === state.batchRequest && state.batchId === id) state.batchError = error.message; }
    finally { if (token === state.batchRequest && state.batchId === id) { state.batchLoading = false; renderBatchDetail(); renderConnection(); } }
  }

  function renderBatchDetail() {
    const batch = state.batch; const placeholder = $("batch-placeholder"); const content = $("batch-detail-content");
    placeholder.hidden = Boolean(batch); content.hidden = !batch;
    if (!batch) {
      state.batchRenderKey = null;
      let empty;
      if (state.batchLoading) empty = emptyState("正在读取批次", "读取采集任务与每次尝试的结果…", "loading");
      else if (state.batchError) empty = emptyState("批次详情暂不可用", state.batchError, "error", fetchBatch);
      else empty = emptyState("等待采集批次", "在服务器通过 SSH 运行采集 skill 后，这里会显示批次及任务状态。");
      placeholder.replaceChildren(...empty.childNodes); placeholder.className = empty.className; return;
    }
    const renderKey = JSON.stringify(batch) + text(state.batchError, "");
    if (state.batchRenderKey === renderKey) return;
    state.batchRenderKey = renderKey;
    $("batch-detail-title").textContent = `${text(batch.jurisdiction, "未记录地区")} / ${text(batch.mode, "未记录模式")}`;
    $("batch-detail-id").textContent = text(batch.id, state.batchId);
    $("batch-detail-status").replaceChildren(badge(batch.status, batchStatuses));
    notice($("batch-detail-error"), state.batchError ? [`${state.batchError} 当前显示上次取得的详情。`] : []);
    notice($("batch-detail-warnings"), batch.warnings);
    $("batch-counts").replaceChildren(...[["success", "成功"], ["failed", "失败"], ["pending", "待处理"], ["running", "未结束"], ["skipped", "跳过"]].map(([field, label]) => {
      const item = node("div", `batch-stat ${field}`); item.append(node("span", "", label), node("strong", "", count(batch.counts?.[field]))); return item;
    }));
    const platforms = list(batch.platforms).map((item) => typeof item === "string" ? item : text(item.name || item.platform, "")).filter(Boolean);
    $("batch-facts").replaceChildren(fact("创建时间", timestamp(batch.created_at)), fact("最近更新", timestamp(batch.updated_at)), fact("当前 / 累计尝试", `${count(batch.attempt_number)} / ${count(batch.attempt_count)}`), fact("采集平台", platforms.length ? platforms.join("、") : "未记录"));
    const report = batch.report;
    $("batch-report").hidden = !report;
    if (report) {
      $("batch-report").replaceChildren(...[["报告结果", batchStatuses[report.result]?.[0] || text(report.result)], ["元数据", count(report.metadata_count)], ["独立文件", count(report.unique_files)], ["错误", count(report.error_count)], ["结束", timestamp(report.finished_at)]].map(([label, value]) => { const span = node("span", "", `${label} `); span.append(node("strong", "", value)); return span; }));
    }
    const attempts = list(batch.attempts);
    $("batch-attempts").hidden = !attempts.length;
    $("batch-attempts").replaceChildren(node("span", "", "执行尝试"), ...attempts.slice(-12).map((attempt) => {
      const label = batchStatuses[attempt.status]?.[0] || text(attempt.status, "未知");
      const span = node("span", "attempt-pill", `#${count(attempt.number)} ${label}`);
      span.title = `开始 ${timestamp(attempt.started_at)} · 结束 ${timestamp(attempt.finished_at)}`; return span;
    }));
    const errors = list(batch.errors).map((error) => typeof error === "object" && error ? text(error.reason || error.message || error.error, "") : text(error, "")).filter(Boolean);
    $("batch-errors").hidden = !errors.length;
    $("batch-errors").replaceChildren(...errors.slice(0, 5).map((error) => node("p", "", error)));
    if (errors.length > 5) $("batch-errors").append(node("p", "", `另有 ${errors.length - 5} 条错误，请查看服务器批次记录。`));
    renderStates();
  }

  function normalizedTaskStatus(status) { if (status === "completed") return "success"; if (status === "error") return "failed"; return taskStatuses[status] ? status : "unknown"; }
  function renderStates() {
    const states = list(state.batch?.states);
    const search = $("state-search").value.trim().toLowerCase(); const filter = $("state-filter").value;
    const matches = states.filter((item) => (filter === "all" || normalizedTaskStatus(item.status) === filter) && (!search || [item.key, item.reason, item.platform, item.country].some((value) => text(value, "").toLowerCase().includes(search))));
    $("task-count").textContent = `${count(states.length)} 项`;
    const visible = matches.slice(0, state.stateLimit);
    if (!visible.length) {
      const row = node("tr"); const cell = node("td", "table-empty", states.length ? "没有符合筛选条件的任务。" : "暂无任务状态记录。"); cell.colSpan = 4; row.append(cell); $("state-table-body").replaceChildren(row);
    } else $("state-table-body").replaceChildren(...visible.map((item) => {
      const row = node("tr"); const task = node("td"); task.append(node("div", "task-key mono", text(item.key)), node("div", "task-secondary", [item.country, item.platform].map((value) => text(value, "")).filter(Boolean).join(" · ") || "平台未记录"));
      const status = node("td"); status.append(badge(item.status, taskStatuses));
      const reason = node("td", "reason-cell"); reason.append(node("div", "", text(item.reason, "—")), node("div", "task-secondary", timestamp(item.checked_at)));
      row.append(task, status, reason, node("td", "number-cell", count(item.attempts))); return row;
    }));
    $("state-table-note").textContent = `显示 ${count(visible.length)} / ${count(matches.length)} 项${search || filter !== "all" ? ` · 全部 ${count(states.length)} 项` : ""}`;
    $("show-more-states").hidden = visible.length >= matches.length;
  }

  function renderSetup(setup) {
    const bool = (value, yes, no) => badge(value === true ? "yes" : value === false ? "no" : "unknown", { yes: [yes, "positive"], no: [no, "warning"], unknown: ["未检测", "neutral"] });
    const items = [["Claude Code", text(setup.claude_version, "未检测到版本")], ["采集 Skill", bool(setup.skill_installed, "已安装", "未安装")], ["模型服务商", text(setup.provider, "未配置")], ["当前模型", text(setup.model, "未配置")], ["API 密钥", bool(setup.api_key_configured, "已配置", "未配置")], ["会话 Hooks", bool(setup.hooks_installed, "已接入", "未接入")]];
    $("setup-checks").replaceChildren(...items.map(([label, value]) => { const item = node("div", "check-item"); const dd = node("dd"); dd.append(typeof value === "string" ? document.createTextNode(value) : value); item.append(node("dt", "", label), dd); return item; }));
  }
  function renderResources(host) {
    $("hostname").textContent = text(host.hostname, "主机名未记录");
    const memoryTotal = host.memory_available_mb == null ? 0 : num(host.memory_total_mb), available = num(host.memory_available_mb), diskTotal = host.disk_free_gb == null ? 0 : num(host.disk_total_gb), diskFree = num(host.disk_free_gb);
    const resource = (label, used, total, unit, color) => {
      const item = node("div", "resource-item"); const header = node("div", "resource-label"); const ratio = total ? Math.max(0, Math.min(100, used / total * 100)) : 0;
      header.append(node("span", "", label), node("strong", "", total ? `${numberFormat.format(used)} / ${numberFormat.format(total)} ${unit} · ${Math.round(ratio)}%` : "暂无数据"));
      const meter = node("div", "resource-meter"); const fill = node("div", `resource-fill ${color}${ratio >= 90 ? " is-danger" : ratio >= 75 ? " is-warning" : ""}`); fill.style.width = `${ratio}%`; meter.append(fill); item.append(header, meter); return item;
    };
    const uptime = num(host.uptime_seconds);
    const uptimeText = host.uptime_seconds == null ? "未记录" : uptime >= 86400 ? `${Math.floor(uptime / 86400)} 天 ${Math.floor(uptime % 86400 / 3600)} 小时` : uptime >= 3600 ? `${Math.floor(uptime / 3600)} 小时 ${Math.floor(uptime % 3600 / 60)} 分` : `${Math.floor(uptime / 60)} 分钟`;
    const facts = node("div", "system-facts");
    for (const [label, value] of [["1 分钟负载", host.load_1 == null ? "未记录" : text(host.load_1)], ["连续运行", uptimeText]]) { const item = node("div"); item.append(node("span", "", label), node("strong", "", value)); facts.append(item); }
    $("system-resources").replaceChildren(resource("内存使用", Math.max(0, memoryTotal - available), memoryTotal, "MB", "memory"), resource("磁盘使用", Math.max(0, diskTotal - diskFree), diskTotal, "GB", "disk"), facts);
  }

  async function refresh() {
    if (state.refreshing) return;
    state.refreshing = true; renderConnection();
    try {
      state.overview = await request("/api/overview"); state.overviewError = null; state.lastSuccess = new Date();
      const sessions = sorted(state.overview.sessions), batches = sorted(state.overview.batches);
      const nextSession = sessions.some((item) => item.id === state.sessionId) ? state.sessionId : sessions[0]?.id || null;
      const nextBatch = batches.some((item) => item.id === state.batchId) ? state.batchId : batches[0]?.id || null;
      if (nextSession !== state.sessionId) { state.sessionId = nextSession; state.session = null; state.sessionError = null; state.sessionRequest++; }
      if (nextBatch !== state.batchId) { state.batchId = nextBatch; state.batch = null; state.batchError = null; state.batchRequest++; state.stateLimit = 100; $("state-search").value = ""; $("state-filter").value = "all"; }
      updateUrl(); renderOverview();
      await Promise.allSettled([fetchSession(), fetchBatch()]);
    } catch (error) { state.overviewError = error.message; renderOverview(); }
    finally { state.refreshing = false; renderConnection(); }
  }

  $("refresh-button").addEventListener("click", refresh);
  for (const id of ["state-search", "state-filter"]) $(id).addEventListener(id === "state-search" ? "input" : "change", () => { state.stateLimit = 100; renderStates(); });
  $("show-more-states").addEventListener("click", () => { state.stateLimit += 100; renderStates(); });
  refresh();
  window.setInterval(refresh, 5000);
})();
