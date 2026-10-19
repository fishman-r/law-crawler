/* 法规库存和批次下载独立于状态轮询；原文下载交给浏览器，避免在内存中接收 ZIP。 */
(() => {
  "use strict";

  const $ = (id) => document.getElementById(id);
  const pageSize = 50;
  const integer = new Intl.NumberFormat("zh-CN", { maximumFractionDigits: 0 });
  const decimal = new Intl.NumberFormat("zh-CN", { maximumFractionDigits: 1 });
  const countryNames = { Indonesia: "印度尼西亚", Canada: "加拿大", EU: "欧盟", Germany: "德国", France: "法国", Netherlands: "荷兰", Italy: "意大利", China: "中国", Singapore: "新加坡", Malaysia: "马来西亚", Thailand: "泰国", Vietnam: "越南", Philippines: "菲律宾", UK: "英国", USA: "美国" };
  const reviewNames = { not_reviewed: "未审查", unreviewed: "未审查", pending: "待审查", needs_review: "需人工审查", title_type_number_year_match: "标题、类型、编号、年份相符", unknown_no_extractable_text_first_two_pages: "前两页无可提取文字，待核验", catalogue_body_type_conflict: "目录与正文类型冲突，待核验", passed: "正文审查通过", pass: "正文审查通过", failed: "正文审查未通过", rejected: "正文审查未通过" };
  const library = { country: null, query: "", countries: [], files: [], total: null, nextOffset: 0, loading: false, error: null, warnings: [], request: 0, controller: null, searchTimer: null, retryAppend: false, selectedId: null, previewRequest: 0, previewController: null };
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
  function identity(file) {
    const info = file.identity && typeof file.identity === "object" ? file.identity : {};
    return [text(info.type, ""), info.number ? `No. ${text(info.number, "")}` : "", text(info.year, "")].filter(Boolean).join(" · ");
  }
  function title(file) { return text(file.title, identity(file) || text(file.name, "法规标题未记录")); }
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
    return safePath(file[`${action}_url`], `/api/library/files/${file.id}/${action}`);
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

  function renderCountries() {
    const allCount = library.countries.reduce((sum, item) => sum + number(item.file_count), 0);
    const entries = [{ name: null, file_count: allCount }, ...library.countries];
    $("library-countries").replaceChildren(...entries.map((item) => {
      const selected = item.name === library.country;
      const button = node("button", `library-country${selected ? " is-selected" : ""}`); button.type = "button";
      button.setAttribute("aria-pressed", String(selected)); button.title = item.name || "全部法域";
      button.append(node("span", "", item.name ? countryLabel(item.name) : "全部法域"), node("span", "library-country-count", count(item.file_count)));
      button.addEventListener("click", () => {
        if (library.country === item.name) return;
        window.clearTimeout(library.searchTimer); library.country = item.name; loadLibrary();
      }); return button;
    }));
  }
  function renderFileList() {
    $("library-scope").textContent = library.country ? countryLabel(library.country) : "全部法域";
    $("library-count").textContent = library.loading && !library.files.length ? "正在读取法规文件…" : library.total === null ? "尚未取得文件清单" : `共 ${count(library.total)} 份${library.query ? ` · 搜索“${library.query}”` : ""}`;
    $("library-file-list").setAttribute("aria-busy", String(library.loading));
    $("library-refresh").disabled = library.loading;
    notice($("library-error"), library.error ? [library.error] : []);
    notice($("library-warnings"), library.warnings);
    const target = $("library-file-list");
    if (!library.files.length) {
      target.replaceChildren(library.loading ? empty("正在读取法规文件", "文件清单单独读取，不影响会话与批次状态。", true) : library.error ? empty("文件清单暂不可用", "请重试读取，或检查观测服务连接。") : empty(library.query ? "没有符合条件的文件" : "此法域暂无法规原文", library.query ? "可以缩短关键词，或切换法域重新搜索。" : "元数据采集记录不会作为原文文件列在这里。"));
    } else {
      target.replaceChildren(...library.files.map((file) => {
        const button = node("button", `library-file${file.id === library.selectedId ? " is-selected" : ""}`); button.type = "button"; button.setAttribute("aria-pressed", String(file.id === library.selectedId));
        const heading = node("div", "library-file-heading"); const fileTitle = node("span", "library-file-title", title(file)); fileTitle.title = title(file);
        heading.append(node("span", "file-format", format(file)), fileTitle);
        const description = node("div", "library-file-identity", [countryLabel(file.country), identity(file)].filter(Boolean).join(" · "));
        const name = node("div", "library-file-name mono", text(file.name)); name.title = text(file.name);
        const bottom = node("div", "library-file-bottom"); bottom.append(node("span", "", bytes(file.size_bytes)), node("span", "", text(file.published_at, "公布日未记录")));
        button.append(heading, description, name, bottom); button.addEventListener("click", () => selectFile(file)); return button;
      }));
    }
    $("library-page-note").textContent = library.total === null ? "每次读取 50 份" : `已显示 ${count(library.files.length)} / ${count(library.total)} 份`;
    $("library-more").hidden = Boolean(library.error) || library.total === null || library.nextOffset >= library.total;
    $("library-more").disabled = library.loading;
    $("library-more").textContent = library.loading ? "正在读取…" : "再显示 50 份 ↓";
    $("library-retry").hidden = !library.error;
  }
  function clearSelection() {
    library.selectedId = null; library.previewRequest++; library.previewController?.abort(); library.previewController = null;
    $("library-file-detail").replaceChildren(empty("选择一份法规原文", "选择文件后可查看预览、法规信息和下载入口。"));
  }
  async function loadLibrary(append = false) {
    library.controller?.abort(); const controller = new AbortController(); library.controller = controller;
    const token = ++library.request; const country = library.country; const query = library.query;
    const offset = append ? library.nextOffset : 0;
    if (!append) { library.files = []; library.total = null; library.nextOffset = 0; library.warnings = []; clearSelection(); }
    library.error = null; library.loading = true; library.retryAppend = append;
    renderCountries(); renderFileList();
    const params = new URLSearchParams({ offset: String(offset), limit: String(pageSize) });
    if (country) params.set("country", country);
    if (query) params.set("q", query);
    try {
      const data = await request(`/api/library?${params}`, controller);
      if (token !== library.request || country !== library.country || query !== library.query) return;
      if (!Array.isArray(data.files) || !Array.isArray(data.countries) || (data.country || null) !== country) throw new Error("文件清单与当前筛选不匹配，请重试。");
      const files = data.files.filter((file) => file && typeof file === "object" && /^[A-Za-z0-9_-]{1,4096}$/.test(file.id || ""));
      const merged = append ? [...library.files, ...files] : files;
      library.files = merged.filter((file, index) => merged.findIndex((item) => item.id === file.id) === index);
      library.countries = data.countries.filter((item) => item && typeof item.name === "string" && item.name);
      library.total = number(data.total); library.nextOffset = number(data.offset) + data.files.length;
      library.warnings = warningMessages(data.warnings);
      if (files.length !== data.files.length) library.warnings.push("部分文件标识无效，未展示；可刷新文件清单后重试。");
      if (!data.files.length && offset < library.total) library.warnings.push("文件清单已变化，请刷新以重新读取。");
    } catch (error) { if (token === library.request && country === library.country && query === library.query) library.error = error.message; }
    finally { if (token === library.request && country === library.country && query === library.query) { library.loading = false; renderCountries(); renderFileList(); } }
  }

  function selectFile(file) {
    library.selectedId = file.id; library.previewRequest++; library.previewController?.abort(); library.previewController = null;
    renderFileList();
    const heading = node("div", "library-detail-heading"); heading.append(node("div", "detail-kicker", "REGULATION FILE"), node("h3", "break-word", title(file)), node("p", "library-detail-subtitle", [countryLabel(file.country), identity(file)].filter(Boolean).join(" · ")));
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
    facts.append(fact("法规类型", text(info.type, "未记录")), fact("编号", text(info.number, "未记录")), fact("年份", text(info.year, "未记录")), fact("公布日期", text(file.published_at, "未记录")), fact("正文审查", reviewNames[file.body_review] || text(file.body_review, "未记录 / 未审查")), fact("文件格式 / 大小", `${format(file)} · ${bytes(file.size_bytes)}`));
    const filename = node("details", "library-file-metadata"); filename.append(node("summary", "", "完整文件名与库存路径"));
    const originalName = node("p", "mono break-word", text(file.name)); originalName.title = text(file.name);
    filename.append(originalName, node("p", "mono break-word muted", text(file.path, "库存路径未记录")));
    if (original) filename.append(node("p", "break-word", `原始响应：${text(original.name, "文件名未记录")} · ${bytes(original.size_bytes)}`));
    const preview = node("div", "library-preview"); preview.id = "library-preview";
    $("library-file-detail").replaceChildren(heading, actions, facts, filename, preview);
    renderPreview(file, previewUrl, preview);
  }
  function previewMessage(target, titleText, description, file, url) {
    const box = empty(titleText, description);
    const retry = node("button", "text-button", "重新载入预览 ↻"); retry.type = "button";
    retry.addEventListener("click", () => renderPreview(file, url, target)); box.append(retry); target.replaceChildren(box);
  }
  async function renderPreview(file, url, target) {
    library.previewController?.abort(); const token = ++library.previewRequest;
    const current = () => token === library.previewRequest && file.id === library.selectedId;
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
  $("library-refresh").addEventListener("click", () => { window.clearTimeout(library.searchTimer); loadLibrary(); });
  $("library-more").addEventListener("click", () => { if (!library.loading) loadLibrary(true); });
  $("library-retry").addEventListener("click", () => loadLibrary(library.retryAppend));
  $("library-search").addEventListener("input", () => {
    window.clearTimeout(library.searchTimer); library.request++; library.controller?.abort();
    library.query = $("library-search").value.trim(); library.loading = true; library.error = null; library.warnings = []; library.files = []; library.total = null; library.nextOffset = 0;
    clearSelection(); renderFileList(); library.searchTimer = window.setTimeout(() => loadLibrary(), 300);
  });
  renderBatchFiles(); loadLibrary();
})();
