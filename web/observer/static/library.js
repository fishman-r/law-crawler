/* 法规库存和批次下载独立于状态轮询；原文下载交给浏览器，避免在内存中接收 ZIP。 */
(() => {
  "use strict";

  const $ = (id) => document.getElementById(id);
  const classificationNames = { regulation: "法规", judicial_guidance: "司法解释 / 指导文件", administrative_decision: "行政决定", other: "其他官方材料", pending: "待核验" };
  const roleNames = { body: "正文", annex: "附件", related: "相关文件", pending: "用途待核验" };
  const integer = new Intl.NumberFormat("zh-CN", { maximumFractionDigits: 0 });
  const decimal = new Intl.NumberFormat("zh-CN", { maximumFractionDigits: 1 });
  const countryNames = { Indonesia: "印度尼西亚", Canada: "加拿大", EU: "欧盟", Germany: "德国", France: "法国", Netherlands: "荷兰", Italy: "意大利", China: "中国", Singapore: "新加坡", Malaysia: "马来西亚", Thailand: "泰国", Vietnam: "越南", Philippines: "菲律宾", UK: "英国", USA: "美国" };
  const reviewNames = { not_reviewed: "未审查", unreviewed: "未审查", pending: "待审查", needs_review: "需人工审查", title_type_number_year_match: "标题、类型、编号、年份相符", unknown_no_extractable_text_first_two_pages: "前两页无可提取文字，待核验", catalogue_body_type_conflict: "目录与正文类型冲突，待核验", passed: "正文审查通过", pass: "正文审查通过", failed: "正文审查未通过", rejected: "正文审查未通过" };
  const library = { country: "Indonesia", page: 1, pageSize: 20, classification: "all", sort: "published_desc", totalPages: 0, facets: [], summary: {}, archiveUrl: null, selectedFileId: null, query: "", countries: [], files: [], total: null, nextOffset: 0, loading: false, error: null, warnings: [], request: 0, controller: null, searchTimer: null, retryAppend: false, selectedId: null, previewRequest: 0, previewController: null };
  const batch = { id: undefined, request: 0, controller: null, data: null, loading: false, error: null };

  function text(value, fallback = "—") {
    return typeof value === "string" && value.trim() ? value : typeof value === "number" && Number.isFinite(value) ? String(value) : fallback;
  }
  function number(value) { return value !== null && value !== "" && Number.isFinite(Number(value)) ? Math.max(0, Number(value)) : 0; }
  function count(value) { return integer.format(number(value)); }
  function list(value) { return Array.isArray(value) ? value : []; }
  function node(tag, className, value) {
    const element = document.createElement(tag);
    if (className) element.className = className;
    if (value !== undefined) element.textContent = text(value, "");
    return element;
  }
  function bytes(value) {
    if (value === undefined || value === null || !Number.isFinite(Number(value))) return "大小未记录";
    let amount = number(value); let unit = 0;
    const units = ["B", "KB", "MB", "GB", "TB"];
    while (amount >= 1024 && unit < units.length - 1) { amount /= 1024; unit++; }
    return `${decimal.format(amount)} ${units[unit]}`;
  }
  function countryLabel(value) { return countryNames[value] || text(value, "法域未记录"); }
  const legalTypes = { "PERATURANOJK": "金融服务管理局规定", "SURATEDARANMENTERI": "部长通函", "SURATEDARANSEKRETARISJENDERALDIREKTURJENDERALKEPALABADAN": "秘书长／总局局长／机构负责人通函", "KEPUTUSANSEKRETARISJENDERALDIREKTURJENDERALKEPALABADAN": "秘书长／总局局长／机构负责人决定", "UNDANGUNDANG": "法律", "UU": "法律", "PERATURANPEMERINTAHPENGGANTIUNDANGUNDANG": "代行法律的政府条例", "PERPPU": "代行法律的政府条例", "PERATURANPEMERINTAH": "政府条例", "PP": "政府条例", "PERATURANPRESIDEN": "总统条例", "PERPRES": "总统条例", "KEPUTUSANPRESIDEN": "总统决定", "KEPPRES": "总统决定", "INSTRUKSIPRESIDEN": "总统指令", "INPRES": "总统指令", "PERATURANMENTERI": "部长规定", "PERMEN": "部长规定", "KEPUTUSANMENTERI": "部长决定", "KEPMEN": "部长决定", "PERATURANDAERAH": "地方条例", "PERDA": "地方条例", "PERATURANGUBERNUR": "省长条例", "PERGUB": "省长条例", "KEPUTUSANGUBERNUR": "省长决定", "PERATURANBUPATI": "县长条例", "PERBUP": "县长条例", "PERATURANWALIKOTA": "市长条例", "PERWALI": "市长条例", "PERATURANMAHKAMAHAGUNG": "最高法院条例", "PERMA": "最高法院条例", "SURATEDARANMAHKAMAHAGUNG": "最高法院通函", "SEMA": "最高法院通函", "KEPUTUSANKETUAMAHKAMAHAGUNG": "最高法院院长决定", "SURATKEPUTUSAN": "决定书" };
  function legalType(value) {
    const raw = text(value, ""); const key = raw.toUpperCase().replace(/[^A-Z]/g, "");
    if (legalTypes[key]) return legalTypes[key];
    for (const prefix of ["PERATURANMENTERI", "KEPUTUSANMENTERI", "PERATURANGUBERNUR", "PERATURANBUPATI", "PERATURANWALIKOTA", "PERATURANDAERAH"]) if (key.startsWith(prefix)) return legalTypes[prefix];
    return raw ? `文种待核验（${raw}）` : "文种待核验";
  }
  function chineseTitle(record) { const value = record.title_zh; return text(value && typeof value === "object" ? value.value : value, ""); }
  function identity(file) {
    const info = file.identity && typeof file.identity === "object" ? file.identity : {};
    return [legalType(info.type), info.year ? `${text(info.year, "")}年` : "年份待核验", info.number ? `第${text(info.number, "")}号` : "编号待核验"].filter(Boolean).join(" · ");
  }
  function title(file) { return chineseTitle(file) || identity(file) || "中文标题待翻译"; }
  function format(file) {
    if (file.media_type === "application/pdf") return "PDF";
    const extension = text(file.name, "").split(".").pop();
    return /^[A-Za-z0-9]{1,7}$/.test(extension) ? extension.toUpperCase() : file.preview_kind === "image" ? "图片" : file.preview_kind === "text" ? "文本" : "文件";
  }
  function warningMessages(value) {
    return list(value).map((item) => item && typeof item === "object" ? text(item.message || item.reason, "") : text(item, "")).filter(Boolean);
  }
  function notice(element, messages) {
    const lines = warningMessages(messages);
    element.hidden = !lines.length;
    element.replaceChildren(...lines.map((line) => node("p", "", line)));
  }
  function empty(titleText, description, loading = false) {
    const box = node("div", "empty-state");
    const symbol = node("div", loading ? "loading-ring" : "empty-symbol", loading ? "" : "▤"); symbol.setAttribute("aria-hidden", "true");
    box.append(symbol, node("h3", "", titleText), node("p", "", description)); return box;
  }
  function fact(label, value) { const item = node("div"); item.append(node("dt", "", label), node("dd", "", value)); return item; }

  // 只接受后端的同源根路径和约定端点，不把外站或任意文件路径放入 href/src。
  function safePath(value, expectedPath) {
    if (typeof value !== "string" || !value.startsWith("/") || value.startsWith("//") || /[\\\s]/.test(value)) return null;
    try {
      const url = new URL(value, window.location.origin);
      return url.origin === window.location.origin && !url.search && !url.hash && url.pathname === expectedPath ? url.pathname : null;
    } catch (_) { return null; }
  }
  function filePath(file, action) {
    if (!/^[A-Za-z0-9_-]{1,4096}$/.test(file.id || "")) return null;
    return safePath(file[`${action}_url`], `/api/regulations/files/${file.id}/${action}`) || safePath(file[`${action}_url`], `/api/library/files/${file.id}/${action}`);
  }
  function archivePath(value, id) { return safePath(value, `/api/batches/${encodeURIComponent(id)}/archive.zip`); }
  function link(label, href, download = false) {
    const anchor = node("a", "button button-secondary", label); anchor.href = href;
    if (download) anchor.setAttribute("download", "");
    else { anchor.target = "_blank"; anchor.rel = "noopener noreferrer"; }
    return anchor;
  }
  async function request(path, controller) {
    const timeout = window.setTimeout(() => controller.abort(), 15000);
    try {
      const response = await fetch(path, { signal: controller.signal, cache: "no-store", credentials: "same-origin", headers: { Accept: "application/json" } });
      if (!response.ok) throw new Error(`服务返回 HTTP ${response.status}，请重试。`);
      const data = await response.json();
      if (!data || typeof data !== "object" || Array.isArray(data)) throw new Error("服务返回了无法识别的数据，请重试。");
      return data;
    } catch (error) {
      if (error.name === "AbortError") throw new Error("读取超时，请检查连接后重试。");
      if (error instanceof SyntaxError) throw new Error("服务返回的内容不是有效 JSON，请重试。");
      if (error instanceof TypeError) throw new Error("暂时无法连接观测服务，请检查网络后重试。");
      throw error;
    } finally { window.clearTimeout(timeout); }
  }

  function renderBatchFiles() {
    const anchor = $("batch-archive-download");
    anchor.removeAttribute("href"); anchor.setAttribute("aria-disabled", "true"); anchor.tabIndex = -1;
    $("batch-files-refresh").disabled = batch.loading || !batch.id;
    notice($("batch-files-error"), batch.error ? [batch.error, "可重读文件清单后，再请求下载。"] : []);
    notice($("batch-files-warnings"), batch.data?.warnings);
    if (!batch.id) {
      $("batch-files-summary").textContent = "选择批次后可查看原文清单。";
      $("batch-download-note").textContent = "ZIP 由服务器准备，下载可能需要片刻。";
      return;
    }
    if (batch.loading) {
      $("batch-files-summary").textContent = "正在核对本批次成功保存的原文…";
      $("batch-download-note").textContent = "文件数量与大小确认后即可打包下载。";
      return;
    }
    if (!batch.data) {
      $("batch-files-summary").textContent = "本批次可下载原文尚未确认。";
      $("batch-download-note").textContent = "请重读文件清单后重试。";
      return;
    }
    const data = batch.data; const amount = number(data.file_count);
    $("batch-files-summary").textContent = `成功原文 ${count(amount)} 份 · ${bytes(data.total_bytes)}`;
    const href = amount ? archivePath(data.archive_url, batch.id) : null;
    if (!amount) {
      $("batch-download-note").textContent = /metadata/i.test(text(data.mode, "")) ? "本批次仅采集元数据，没有可打包的法规原文。" : "本批次暂没有可打包的原文；采集完成或文件恢复后可重读清单。";
    } else if (!href) {
      $("batch-download-note").textContent = "下载地址暂不可用，请重读文件清单后重试。";
    } else {
      anchor.href = href; anchor.removeAttribute("aria-disabled"); anchor.removeAttribute("tabindex");
      $("batch-download-note").textContent = "包含本批次原文、元数据与清单。服务器准备 ZIP 可能需要片刻；下载失败可重读清单后重试。";
    }
  }
  async function loadBatchFiles() {
    if (!batch.id) return;
    batch.controller?.abort(); const controller = new AbortController(); batch.controller = controller;
    const id = batch.id; const token = ++batch.request;
    batch.data = null; batch.error = null; batch.loading = true; renderBatchFiles();
    try {
      const data = await request(`/api/batches/${encodeURIComponent(id)}/files`, controller);
      if (batch.request !== token || batch.id !== id) return;
      if (data.batch_id !== id || !Array.isArray(data.files)) throw new Error("原文清单与当前批次不匹配，请重试。");
      batch.data = data;
    } catch (error) { if (batch.request === token && batch.id === id) batch.error = error.message; }
    finally { if (batch.request === token && batch.id === id) { batch.loading = false; renderBatchFiles(); } }
  }
  function selectBatch(id) {
    const nextId = typeof id === "string" && /^[A-Za-z0-9][A-Za-z0-9._-]{0,199}$/.test(id) ? id : null;
    if (nextId === batch.id) return;
    batch.controller?.abort(); batch.request++; batch.id = nextId; batch.data = null; batch.error = null; batch.loading = false;
    renderBatchFiles();
    if (nextId) loadBatchFiles();
  }

  function regulationArchive(value) {
    if (typeof value !== "string" || !value.startsWith("/") || value.startsWith("//") || /[\\\s]/.test(value)) return null;
    try {
      const url = new URL(value, window.location.origin);
      const allowed = new Set(["q", "page", "page_size", "classification", "sort", "token"]);
      return url.origin === window.location.origin && url.pathname === "/api/regulations/archive.zip" && !url.hash && [...url.searchParams.keys()].every((key) => allowed.has(key)) && url.searchParams.has("token") ? url.pathname + url.search : null;
    } catch (_) { return null; }
  }
  function renderCountries() {
    const button = node("button", "library-country is-selected"); button.type = "button"; button.setAttribute("aria-pressed", "true");
    button.append(node("span", "", "印度尼西亚"), node("span", "library-country-count", "已归档"));
    $("library-countries").replaceChildren(button);
    const category = $("library-category"); const current = library.classification;
    const options = [new Option("全部分类", "all")];
    for (const item of library.facets) if (item && item.value && item.value !== "all") options.push(new Option(`${text(item.label, classificationNames[item.value] || item.value)}（${count(item.count)}）`, item.value));
    if (current !== "all" && !options.some((item) => item.value === current)) options.push(new Option(classificationNames[current] || current, current));
    category.replaceChildren(...options); category.value = current;
    const choices = [{ value: "all", label: "全部材料", count: library.summary.records }, ...library.facets.filter((item) => item.value !== "all")];
    $("library-classifications").replaceChildren(...choices.map((item) => {
      const button = node("button", `library-classification${item.value === current ? " is-selected" : ""}`); button.type = "button"; button.dataset.classification = item.value; button.setAttribute("aria-pressed", String(item.value === current));
      button.append(node("span", "", text(item.label, classificationNames[item.value] || item.value)), node("span", "", item.count === undefined ? "—" : count(item.count)));
      button.addEventListener("click", () => { window.clearTimeout(library.searchTimer); library.classification = item.value; library.page = 1; loadLibrary(); }); return button;
    }));
  }
  function recordCounts(record) {
    const files = list(record.files); const roles = ["body", "annex", "related", "pending"].map((role) => [role, files.filter((file) => (file.role || "pending") === role).length]).filter((item) => item[1]);
    return `${count(files.length)} 份文件` + (roles.length ? ` · ${roles.map(([role, amount]) => `${roleNames[role]} ${count(amount)}`).join(" · ")}` : "");
  }
  function renderFileList() {
    $("library-scope").textContent = library.classification === "all" ? "全部已入库材料" : `${classificationNames[library.classification] || "分类"}检索结果`;
    const summary = library.summary;
    $("library-count").textContent = library.loading ? "正在读取法规库…" : library.total === null ? "尚未取得法规记录" : `检索结果 ${count(library.total)} 条 · 已入库记录 ${count(summary.records)} 条 · 其中已识别法规（有成功原件）${count(summary.regulations)} 部 · 身份待核验 ${count(summary.pending)} 条 · 原件 ${count(summary.unique_files)} 份`;
    $("library-file-list").setAttribute("aria-busy", String(library.loading)); $("library-refresh").disabled = library.loading;
    notice($("library-error"), library.error ? [library.error] : []); notice($("library-warnings"), library.warnings);
    const target = $("library-file-list");
    if (!library.files.length) target.replaceChildren(library.loading ? empty("正在读取法规库", "读取服务器成功归档的原件及其法规记录。", true) : library.error ? empty("法规库暂不可用", "请重试读取，或检查服务连接。") : empty("没有符合条件的法规", "可以缩短关键词，或选择全部分类重新检索。"));
    else target.replaceChildren(...library.files.map((record) => {
      const button = node("button", `library-file${record.id === library.selectedId ? " is-selected" : ""}`); button.type = "button"; button.setAttribute("aria-pressed", String(record.id === library.selectedId));
      const heading = node("div", "library-file-heading"); heading.append(node("span", "file-format", classificationNames[record.classification] || "待核验"), node("span", "library-file-title", chineseTitle(record) || identity(record)));
      const translation = node("span", "library-title-translation", chineseTitle(record) ? "中文参考译名 · 请与原文对照" : "中文标题待翻译");
      const details = node("div", "library-file-identity", identity(record));
      const original = node("p", "library-original-title", chineseTitle(record) ? `原文：${text(record.title, "未记录")}` : `原文标题（暂无中文译名）：${text(record.title, "未记录")}`);
      const tags = node("div", "library-record-counts", recordCounts(record));
      const bottom = node("div", "library-file-bottom"); bottom.append(node("span", "", record.identity_status === "identified" ? "身份已识别" : "身份待核验"), node("span", "", text(record.published_at, "公布日期待核验")));
      button.append(heading, translation, original, details, tags, bottom); button.addEventListener("click", () => selectRecord(record)); return button;
    }));
    $("library-page-note").textContent = library.total === null ? "每页读取法规记录" : library.total ? `第 ${count(library.page)} / ${count(library.totalPages)} 页 · 本页 ${count(library.files.length)} 条` : "暂无记录";
    $("library-previous").disabled = library.loading || library.page <= 1;
    $("library-more").hidden = false; $("library-more").disabled = library.loading || library.page >= library.totalPages; $("library-more").textContent = "下一页 →";
    $("library-retry").hidden = !library.error;
    const anchor = $("library-page-export"); const href = !library.loading && !library.error && library.files.length ? regulationArchive(library.archiveUrl) : null;
    anchor.removeAttribute("href"); anchor.setAttribute("aria-disabled", "true"); anchor.tabIndex = -1;
    if (href) { anchor.href = href; anchor.setAttribute("download", ""); anchor.removeAttribute("aria-disabled"); anchor.removeAttribute("tabindex"); }
    $("library-export-note").textContent = library.loading ? "读取完成后可打包当前页。" : "打包当前页关联的已归档原件，按文件内容去重，并附元数据清单；不会打包其他页。";
  }
  function clearSelection() {
    library.selectedId = null; library.selectedFileId = null; library.previewRequest++; library.previewController?.abort(); library.previewController = null;
    $("library-file-detail").replaceChildren(empty("选择一条法规", "查看法规身份、正文、附件、版本与下载入口；无法确认的关系标注待核验。"));
  }
  async function loadLibrary() {
    library.controller?.abort(); const controller = new AbortController(); library.controller = controller; const token = ++library.request;
    const params = new URLSearchParams({ q: library.query, page: String(library.page), page_size: String(library.pageSize), classification: library.classification, sort: library.sort });
    library.files = []; library.total = null; library.archiveUrl = null; library.warnings = []; clearSelection(); library.error = null; library.loading = true;
    renderCountries(); renderFileList();
    try {
      const data = await request(`/api/regulations?${params}`, controller); if (token !== library.request) return;
      if (!Array.isArray(data.items) || data.available === false) throw new Error("法规库尚未就绪，请刷新后重试。");
      library.files = data.items.filter((record) => record && /^[a-f0-9]{64}$/.test(record.id || ""));
      library.total = number(data.total); library.page = Math.max(1, number(data.page)); library.totalPages = number(data.total_pages);
      library.facets = list(data.facets?.classification); library.summary = data.summary || {}; library.archiveUrl = data.archive_url; library.warnings = warningMessages(data.warnings);
      if (library.files.length !== data.items.length) library.warnings.push("部分记录标识无效，未展示；请刷新后核验。");
    } catch (error) { if (token === library.request) library.error = error.message; }
    finally { if (token === library.request) { library.loading = false; renderCountries(); renderFileList(); } }
  }
  function selectRecord(record) {
    library.selectedId = record.id; library.selectedFileId = null; library.previewRequest++; library.previewController?.abort(); renderFileList();
    const heading = node("div", "library-detail-heading"); heading.append(node("div", "detail-kicker", "印度尼西亚 · 官方归档原件"), node("h3", "break-word", title(record)), node("p", "library-detail-subtitle", identity(record) || "法规身份待核验"));
    const labels = node("div", "library-record-counts"); labels.append(node("span", `library-record-tag${record.identity_status !== "identified" ? " is-pending" : ""}`, record.identity_status === "identified" ? "身份已识别 · 同一法规合并" : "身份待核验 · 暂不合并"), node("span", "library-record-tag", classificationNames[record.classification] || "待核验"), node("span", `library-record-tag${record.download_status !== "complete" ? " is-pending" : ""}`, record.download_status === "complete" ? "已下载" : "部分原件已下载"));
    const facts = node("dl", "fact-grid library-facts"); facts.append(fact("公布日期", text(record.published_at, "待核验")), fact("年份", text(record.year || record.identity?.year, "待核验")), fact("版本关系", record.version_status === "observed" ? `已记录 ${count(list(record.versions).length)} 个版本信息` : "待核验"));
    const files = node("div", "library-record-files"); files.append(node("h4", "", recordCounts(record)));
    const slot = node("div", "library-file-detail"); slot.id = "library-selected-file";
    const available = list(record.files).filter((file) => file && /^[a-f0-9]{64}$/.test(file.id || ""));
    for (const raw of available) {
      const file = { ...raw, title: record.title, title_zh: record.title_zh, country: "Indonesia", identity: record.identity, published_at: record.published_at, preview_kind: raw.preview_kind || (raw.media_type === "application/pdf" ? "pdf" : /^image\//.test(raw.media_type || "") ? "image" : null) };
      const button = node("button", "library-record-file"); button.type = "button";
      button.append(node("span", "", `${roleNames[file.role] || "用途待核验"} · ${text(file.name, "原始文件")} · ${file.version ? `版本 ${file.version}` : "版本待核验"}`), node("span", "", bytes(file.size_bytes)));
      button.addEventListener("click", () => { files.querySelectorAll("button").forEach((item) => item.classList.remove("is-selected")); button.classList.add("is-selected"); selectFile(file); }); files.append(button);
    }
    const original = node("p", "library-original-title", chineseTitle(record) ? `原文标题：${text(record.title, "未记录")}` : `原文标题（暂无中文译名）：${text(record.title, "未记录")}`);
    $("library-file-detail").replaceChildren(heading, node("p", "library-title-translation", chineseTitle(record) ? "中文参考译名，非官方译文；请与原文对照。" : "中文标题待翻译；当前显示中文法规身份。"), original, labels, facts, files, slot);
    if (!$("library-detail-dialog").open) $("library-detail-dialog").showModal();
    if (available.length) files.querySelector("button").click(); else slot.append(empty("暂无可下载文件", "该记录的文件清单可能已更新，请刷新法规库。"));
  }

  function selectFile(file) {
    library.selectedFileId = file.id; library.previewRequest++; library.previewController?.abort(); library.previewController = null;
    const heading = node("div", "library-detail-heading"); heading.append(node("div", "detail-kicker", "已归档文件"), node("h3", "break-word", title(file)), node("p", "library-detail-subtitle", [countryLabel(file.country), identity(file)].filter(Boolean).join(" · ")));
    const downloadUrl = filePath(file, "download"); const metadataUrl = filePath(file, "metadata"); const previewUrl = filePath(file, "preview");
    const actions = node("div", "library-detail-actions");
    if (downloadUrl) { const download = link(format(file) === "MD" ? "下载 Markdown ↓" : "下载原文 ↓", downloadUrl, true); download.className = "button button-primary"; actions.append(download); }
    const original = file.original && typeof file.original === "object" ? file.original : null;
    const originalUrl = original ? filePath(original, "download") : null;
    if (originalUrl) { const rawDownload = link("下载原始响应 ↓", originalUrl, true); rawDownload.title = `${text(original.name, "原始响应")} · ${bytes(original.size_bytes)}`; actions.append(rawDownload); }
    if (metadataUrl) actions.append(link("下载元数据", metadataUrl, true));
    if (previewUrl && ["pdf", "image"].includes(file.preview_kind)) actions.append(link("新窗口打开原文 ↗", previewUrl));
    if (!downloadUrl) actions.append(node("span", "muted small-text", "原文下载地址暂不可用，可刷新清单重试。"));
    const info = file.identity && typeof file.identity === "object" ? file.identity : {};
    const facts = node("dl", "fact-grid library-facts");
    facts.append(fact("法规类型", legalType(info.type)), fact("编号", text(info.number, "未记录")), fact("年份", text(info.year, "未记录")), fact("公布日期", text(file.published_at, "未记录")), fact("正文审查", reviewNames[file.body_review] || text(file.body_review, "未记录 / 未审查")), fact("文件格式 / 大小", `${format(file)} · ${bytes(file.size_bytes)}`));
    const filename = node("details", "library-file-metadata"); filename.append(node("summary", "", "完整文件名与库存路径"));
    const originalName = node("p", "mono break-word", text(file.name)); originalName.title = text(file.name);
    filename.append(originalName, node("p", "mono break-word muted", text(file.path, "库存路径未记录")));
    if (original) filename.append(node("p", "break-word", `原始响应：${text(original.name, "文件名未记录")} · ${bytes(original.size_bytes)}`));
    const preview = node("div", "library-preview"); preview.id = "library-preview";
    ($("library-selected-file") || $("library-file-detail")).replaceChildren(heading, actions, facts, filename, preview);
    renderPreview(file, previewUrl, preview);
  }
  function previewMessage(target, titleText, description, file, url) {
    const box = empty(titleText, description);
    const retry = node("button", "text-button", "重新载入预览 ↻"); retry.type = "button";
    retry.addEventListener("click", () => renderPreview(file, url, target)); box.append(retry); target.replaceChildren(box);
  }
  async function renderPreview(file, url, target) {
    library.previewController?.abort(); const token = ++library.previewRequest;
    const current = () => token === library.previewRequest && file.id === library.selectedFileId;
    target.replaceChildren();
    if (!url || !file.preview_kind) {
      target.append(empty("暂不支持内嵌预览", "可下载原文，在电脑上使用对应的阅读器打开。")); return;
    }
    if (file.preview_kind === "pdf" && file.media_type === "application/pdf") {
      const note = node("p", "library-preview-note", "PDF 由浏览器提供预览；预览空白时，可打开新窗口，或下载后用 PDF 阅读器查看。");
      const frame = node("iframe", "library-pdf"); frame.title = `${title(file)} PDF 预览`; frame.setAttribute("referrerpolicy", "no-referrer"); frame.src = url;
      frame.addEventListener("error", () => { if (current()) previewMessage(target, "PDF 预览暂不可用", "可以重试，或使用上方入口在新窗口打开、下载原文。", file, url); });
      target.append(note, frame); return;
    }
    if (file.preview_kind === "image" && ["image/png", "image/jpeg", "image/gif", "image/webp", "image/avif", "image/bmp"].includes(file.media_type)) {
      const image = node("img", "library-image"); image.alt = `${title(file)} 原文图片`; image.src = url;
      image.addEventListener("error", () => { if (current()) previewMessage(target, "图片预览暂不可用", "可以重试，或下载原文后查看。", file, url); }); target.append(image); return;
    }
    if (file.preview_kind !== "text") {
      target.append(empty("此格式请下载查看", "浏览器暂不支持内嵌显示此文件，可下载原文后阅读。")); return;
    }
    const controller = new AbortController(); library.previewController = controller;
    target.append(empty("正在读取文字预览", "正在读取已保存文件的文字内容…", true));
    try {
      const data = await request(url, controller);
      if (!current()) return;
      if (typeof data.text !== "string") throw new Error("服务返回的文字预览格式无法识别。");
      const content = node("pre", "library-text-preview", data.text.slice(0, 200000));
      const note = node("p", "library-preview-note", data.truncated || data.text.length > 200000 ? "当前显示部分文字；下载原文可查看完整内容。" : "以下为已保存文件的文字内容。");
      target.replaceChildren(note, data.text ? content : empty("文件没有可显示的文字", "可下载原文确认文件内容。"));
    } catch (error) { if (current()) previewMessage(target, "文字预览暂不可用", error.message, file, url); }
  }

  window.addEventListener("observer:batch-selection", (event) => selectBatch(event.detail?.batchId));
  $("batch-files-refresh").addEventListener("click", loadBatchFiles);
  $("batch-archive-download").addEventListener("click", (event) => {
    if (!event.currentTarget.hasAttribute("href")) { event.preventDefault(); return; }
    $("batch-download-note").textContent = "已请求浏览器下载。服务器准备 ZIP 可能需要片刻；若下载失败或文件缺失，请重读清单后重试。";
  });
  function routePage() {
    const active = window.location.hash === "#library";
    document.body.classList.toggle("library-mode", active);
    $("library").hidden = !active;
    document.querySelectorAll("#main > section:not(#library), #main > .page-footer").forEach((element) => { element.hidden = active; });
    document.querySelectorAll(".main-nav a").forEach((anchor) => {
      if (active && anchor.hash === "#library" || !active && anchor.hash === (window.location.hash || "#overview")) anchor.setAttribute("aria-current", "page"); else anchor.removeAttribute("aria-current");
    });
    if (!active && $("library-detail-dialog").open) $("library-detail-dialog").close();
    document.title = active ? "印度尼西亚法规库 · Law Crawler" : "采集观测台 · Law Crawler";
    if (active) { window.scrollTo(0, 0); $("library-title").focus({ preventScroll: true }); }
  }
  window.addEventListener("hashchange", routePage);
  $("library-detail-close").addEventListener("click", () => $("library-detail-dialog").close());
  $("library-detail-dialog").addEventListener("close", () => { const selected = document.querySelector(".library-file.is-selected"); if (document.body.classList.contains("library-mode")) selected?.focus({ preventScroll: true }); });
  $("library-search-form").addEventListener("submit", (event) => { event.preventDefault(); window.clearTimeout(library.searchTimer); library.query = $("library-search").value.trim(); library.page = 1; loadLibrary(); });
  routePage();
  $("library-refresh").addEventListener("click", () => { window.clearTimeout(library.searchTimer); loadLibrary(); });
  $("library-previous").addEventListener("click", () => { if (!library.loading && library.page > 1) { library.page--; loadLibrary(); } });
  $("library-more").addEventListener("click", () => { if (!library.loading && library.page < library.totalPages) { library.page++; loadLibrary(); } });
  $("library-retry").addEventListener("click", loadLibrary);
  for (const [id, field] of [["library-category", "classification"], ["library-sort", "sort"], ["library-page-size", "pageSize"]]) {
    $(id).addEventListener("change", () => { window.clearTimeout(library.searchTimer); library[field] = field === "pageSize" ? Number($(id).value) : $(id).value; library.page = 1; loadLibrary(); });
  }
  $("library-page-export").addEventListener("click", (event) => {
    if (!event.currentTarget.hasAttribute("href")) { event.preventDefault(); return; }
    $("library-export-note").textContent = "已请求浏览器下载当前页 ZIP；服务器核对原件后开始传输，包含文件清单和元数据。";
  });
  $("library-search").addEventListener("input", () => {
    window.clearTimeout(library.searchTimer); library.request++; library.controller?.abort(); library.query = $("library-search").value.trim(); library.page = 1;
    library.loading = true; library.error = null; library.warnings = []; library.files = []; library.total = null; library.archiveUrl = null;
    clearSelection(); renderFileList(); library.searchTimer = window.setTimeout(loadLibrary, 300);
  });
  $("library-sort").value = library.sort; $("library-category").value = "all";
  renderBatchFiles(); loadLibrary();
})();
