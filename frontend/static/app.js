const API = "/api/datasets";

const STEPS = [
  { id: "upload", title: "Загрузка датасета", sub: "ZIP → распаковка" },
  { id: "stats", title: "Статистика", sub: "число, размеры, warnings" },
  { id: "select", title: "Отбор кадров", sub: "вперёд/назад, approve" },
  { id: "dart", title: "Настройка DART", sub: "prompt, confidence, mode" },
  { id: "autolabel", title: "Авторазметка", sub: "прогресс, ошибки" },
  { id: "export", title: "Экспорт в CVAT", sub: "формат, импорт" },
  { id: "results", title: "Результаты", sub: "JSON, preview, ошибки" },
];

const state = {
  datasetId: null,
  unlocked: new Set(["upload"]),
  currentScreen: "upload",
  approvedImages: [],
  autolabelPollTimer: null,
  currentFrameId: null,
};

async function apiFetch(path, opts = {}) {
  const res = await fetch(path, opts);
  if (!res.ok) {
    let msg = `HTTP ${res.status}`;
    try {
      const data = await res.json();
      if (data && data.error && data.error.message) {
        msg = data.error.message;
      } else if (data && typeof data.detail === "string") {
        msg = data.detail;
      } else if (data && data.detail && data.detail.message) {
        msg = data.detail.message;
      }
    } catch (_) {
      /* noop */
    }
    throw new Error(msg);
  }
  if (res.status === 204) return null;
  const text = await res.text();
  return text ? JSON.parse(text) : null;
}

function apiPostJSON(path, body) {
  return apiFetch(path, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body || {}),
  });
}

function el(id) {
  return document.getElementById(id);
}

function clearEl(container) {
  container.replaceChildren();
}

function makeEl(tag, opts = {}) {
  const node = document.createElement(tag);
  if (opts.className) node.className = opts.className;
  if (opts.text !== undefined) node.textContent = opts.text;
  if (opts.attrs) {
    for (const [key, value] of Object.entries(opts.attrs)) {
      if (value !== undefined && value !== null) node.setAttribute(key, value);
    }
  }
  return node;
}

function safeHttpUrl(url) {
  if (!url) return null;
  try {
    const parsed = new URL(url, window.location.href);
    if (parsed.protocol === "http:" || parsed.protocol === "https:") {
      return parsed.href;
    }
  } catch (_) {
    /* noop */
  }
  return null;
}

function showError(container, message) {
  clearEl(container);
  container.appendChild(makeEl("div", { className: "error-banner", text: message }));
}

function showEmpty(container, message) {
  clearEl(container);
  container.appendChild(makeEl("div", { className: "empty-state", text: message }));
}

async function checkHealth() {
  const dot = el("statusDot");
  const text = el("healthStatus");
  try {
    const res = await fetch("/health");
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    const data = await res.json();
    dot.classList.remove("fail");
    dot.classList.add("ok");
    text.textContent = `Backend: ${data.service || "ok"} ${data.version || ""}`.trim();
  } catch (e) {
    dot.classList.remove("ok");
    dot.classList.add("fail");
    text.textContent = "Backend пока не отвечает";
  }
}

function renderRail() {
  const nav = el("stepsNav");
  clearEl(nav);
  STEPS.forEach((step, idx) => {
    const btn = document.createElement("button");
    btn.className = "step-link";
    btn.disabled = !state.unlocked.has(step.id);
    if (step.id === state.currentScreen) btn.classList.add("active");
    if (isStepDone(step.id)) btn.classList.add("done");

    const num = makeEl("span", { className: "step-num", text: String(idx + 1) });
    const textWrap = makeEl("span", { className: "step-text" });
    textWrap.appendChild(makeEl("span", { className: "step-title", text: step.title }));
    textWrap.appendChild(makeEl("span", { className: "step-sub", text: step.sub }));

    btn.appendChild(num);
    btn.appendChild(textWrap);
    btn.addEventListener("click", () => goToScreen(step.id));
    nav.appendChild(btn);
  });
  el("datasetBadge").textContent = state.datasetId
    ? `dataset: ${state.datasetId}`
    : "dataset: —";
}

function isStepDone(stepId) {
  const order = STEPS.map((s) => s.id);
  return order.indexOf(stepId) < order.indexOf(state.currentScreen);
}

function unlock(stepId) {
  state.unlocked.add(stepId);
}

function goToScreen(id) {
  state.currentScreen = id;
  document.querySelectorAll(".screen").forEach((s) => {
    s.classList.toggle("active", s.dataset.screen === id);
  });
  renderRail();
  if (id === "stats") loadStats();
  if (id === "dart") {
    populatePreviewImageSelect();
    loadDartSettings();
  }
}

function initUploadScreen() {
  const dropzone = el("dropzone");
  const input = el("zipInput");

  dropzone.addEventListener("click", () => input.click());
  dropzone.addEventListener("keydown", (e) => {
    if (e.key === "Enter" || e.key === " ") input.click();
  });
  ["dragenter", "dragover"].forEach((ev) =>
    dropzone.addEventListener(ev, (e) => {
      e.preventDefault();
      dropzone.classList.add("drag");
    })
  );
  ["dragleave", "drop"].forEach((ev) =>
    dropzone.addEventListener(ev, (e) => {
      e.preventDefault();
      dropzone.classList.remove("drag");
    })
  );
  dropzone.addEventListener("drop", (e) => {
    const file = e.dataTransfer.files[0];
    if (file) uploadZip(file);
  });
  input.addEventListener("change", () => {
    if (input.files[0]) uploadZip(input.files[0]);
  });

  el("toStats").addEventListener("click", () => goToScreen("stats"));
}

function uploadZip(file) {
  if (!file.name.toLowerCase().endsWith(".zip")) {
    showError(el("uploadError"), "Нужен файл в формате .zip");
    return;
  }
  clearEl(el("uploadError"));
  el("dropzoneText").textContent = file.name;

  const progressWrap = el("uploadProgressWrap");
  const bar = el("uploadProgressBar");
  const text = el("uploadProgressText");
  progressWrap.hidden = false;
  bar.style.width = "0%";
  text.textContent = "0%";

  const form = new FormData();
  form.append("file", file);

  const xhr = new XMLHttpRequest();
  xhr.open("POST", `${API}/upload`);

  xhr.upload.addEventListener("progress", (e) => {
    if (!e.lengthComputable) return;
    const pct = Math.round((e.loaded / e.total) * 100);
    bar.style.width = `${pct}%`;
    text.textContent = pct < 100 ? `${pct}%` : "Распаковка на сервере…";
  });

  xhr.onload = () => {
    if (xhr.status < 200 || xhr.status >= 300) {
      let msg = `HTTP ${xhr.status}`;
      try {
        const data = JSON.parse(xhr.responseText);
        if (data.error && data.error.message) {
          msg = data.error.message;
        } else if (typeof data.detail === "string") {
          msg = data.detail;
        } else if (data.detail && data.detail.message) {
          msg = data.detail.message;
        }
      } catch (_) {
        /* noop */
      }
      showError(el("uploadError"), `Не удалось загрузить датасет: ${msg}`);
      progressWrap.hidden = true;
      return;
    }
    let data = {};
    try {
      data = JSON.parse(xhr.responseText);
    } catch (_) {
      /* noop */
    }
    const dataset = data.dataset || data;
    state.datasetId = dataset.id || dataset.dataset_id;
    if (!state.datasetId) {
      showError(el("uploadError"), "Backend не вернул идентификатор датасета");
      progressWrap.hidden = true;
      return;
    }
    state.approvedImages = [];
    state.currentFrameId = null;
    text.textContent = `Готово: ${dataset.image_count ?? "?"} изображений`;
    bar.style.width = "100%";
    unlock("stats");
    el("toStats").disabled = false;
    renderRail();
  };

  xhr.onerror = () => {
    showError(el("uploadError"), "Сетевая ошибка при загрузке ZIP");
    progressWrap.hidden = true;
  };

  xhr.send(form);
}

async function loadStats() {
  if (!state.datasetId) return;
  const body = el("statsBody");
  showEmpty(body, "Загружаем статистику…");
  try {
    const response = await apiFetch(`${API}/${state.datasetId}/stats`);
    renderStats({ ...response.stats, warnings: response.warnings || [] });
    unlock("select");
    el("toSelect").disabled = false;
    renderRail();
  } catch (e) {
    showError(body, `Не удалось получить статистику: ${e.message}`);
  }
}

function renderStats(stats) {
  const body = el("statsBody");
  clearEl(body);

  const extensions = stats.extensions || {};
  const warnings = stats.warnings || [];

  const tiles = [
    { num: stats.image_count ?? "—", label: "изображений" },
    {
      num: stats.min_size
        ? `${stats.min_size.width}×${stats.min_size.height}`
        : "—",
      label: "мин. разрешение",
    },
    {
      num: stats.max_size
        ? `${stats.max_size.width}×${stats.max_size.height}`
        : "—",
      label: "макс. разрешение",
    },
    {
      num: stats.common_resolutions && stats.common_resolutions.length
        ? stats.common_resolutions[0].resolution
        : "—",
      label: "частое разрешение",
    },
  ];

  const statGrid = makeEl("div", { className: "stat-grid" });
  tiles.forEach((t) => {
    const tile = makeEl("div", { className: "stat-tile" });
    tile.appendChild(makeEl("div", { className: "num", text: String(t.num) }));
    tile.appendChild(makeEl("div", { className: "label", text: t.label }));
    statGrid.appendChild(tile);
  });
  body.appendChild(statGrid);

  body.appendChild(
    makeEl("h3", {
      className: "section-title",
      text: "Расширения файлов",
      attrs: { style: "margin-top:20px;" },
    })
  );

  const tagList = makeEl("div", { className: "tag-list" });
  const extEntries = Object.entries(extensions);
  if (extEntries.length) {
    extEntries.forEach(([ext, count]) => {
      tagList.appendChild(makeEl("span", { className: "tag", text: `${ext}: ${count}` }));
    });
  } else {
    tagList.appendChild(makeEl("span", { className: "hint", text: "нет данных" }));
  }
  body.appendChild(tagList);

  if (warnings.length) {
    body.appendChild(
      makeEl("h3", {
        className: "section-title",
        text: "Предупреждения",
        attrs: { style: "margin-top:20px;" },
      })
    );
    const list = makeEl("ul", { className: "warning-list" });
    warnings.forEach((w) => {
      list.appendChild(makeEl("li", { text: String(w) }));
    });
    body.appendChild(list);
  }
}

async function repInit() {
  const n = parseInt(el("repN").value, 10) || 1;
  el("approvedTarget").textContent = n;
  try {
    const data = await apiPostJSON(`${API}/${state.datasetId}/representative/init`, {
      target_count: n,
    });
    el("repCard").hidden = false;
    renderFrame(data);
  } catch (e) {
    showError(el("repCard"), `Не удалось начать отбор: ${e.message}`);
    el("repCard").hidden = false;
  }
}

async function refreshCurrentFrame() {
  try {
    const data = await apiFetch(`${API}/${state.datasetId}/representative/current`);
    renderFrame(data);
  } catch (e) {
    el("framePlaceholder").hidden = false;
    el("framePlaceholder").textContent = `Ошибка: ${e.message}`;
  }
}

function renderFrame(data) {
  const img = data.current_image || {};
  const imageEl = el("frameImage");
  const placeholder = el("framePlaceholder");
  const safeSrc = safeHttpUrl(img.url);

  if (safeSrc) {
    imageEl.src = safeSrc;
    imageEl.hidden = false;
    placeholder.hidden = true;
  } else {
    imageEl.hidden = true;
    imageEl.removeAttribute("src");
    placeholder.hidden = false;
    placeholder.textContent = "нет изображения";
  }

  el("frameFilename").textContent = img.filename || img.id || "—";

  const pill = el("frameApprovedPill");
  const approveBtn = el("frameApprove");
  const unapproveBtn = el("frameUnapprove");
  if (img.approved) {
    pill.textContent = "отобрано";
    pill.classList.add("approved");
    approveBtn.hidden = true;
    unapproveBtn.hidden = false;
  } else {
    pill.textContent = "не отобрано";
    pill.classList.remove("approved");
    approveBtn.hidden = false;
    unapproveBtn.hidden = true;
  }

  el("approvedCount").textContent = data.approved_count ?? state.approvedImages.length;
  if (data.target_count) el("approvedTarget").textContent = data.target_count;

  state.currentFrameId = img.id || null;
  const approvedIds = data.approved_image_ids || [];
  state.approvedImages = approvedIds.map((imageId) => {
    const previous = state.approvedImages.find((item) => item.id === imageId);
    const filename = imageId === img.id ? img.filename : previous && previous.filename;
    return { id: imageId, filename: filename || imageId };
  });

  const approvedCount = data.approved_count ?? 0;
  el("framePrev").disabled = !data.can_go_prev;
  el("frameNext").disabled = !data.can_go_next;
  el("toDart").disabled = !data.completed;
  if (data.completed) unlock("dart");
  renderRail();
}

async function frameApprove() {
  if (!state.currentFrameId) return;
  const imageId = state.currentFrameId;
  const filename = el("frameFilename").textContent;
  try {
    const data = await apiPostJSON(`${API}/${state.datasetId}/representative/approve`);
    renderFrame(data);
    const approved = state.approvedImages.find((item) => item.id === imageId);
    if (approved) {
      approved.filename = filename;
    }
  } catch (e) {
    showError(el("repCard"), `Не удалось одобрить кадр: ${e.message}`);
  }
}

async function frameUnapprove() {
  if (!state.currentFrameId) return;
  try {
    const data = await apiPostJSON(`${API}/${state.datasetId}/representative/unapprove`);
    renderFrame(data);
  } catch (e) {
    showError(el("repCard"), `Не удалось снять отбор: ${e.message}`);
  }
}

async function frameStep(direction) {
  try {
    const data = await apiPostJSON(
      `${API}/${state.datasetId}/representative/${direction}`
    );
    renderFrame(data);
  } catch (e) {
    showError(el("repCard"), `Не удалось переключить кадр: ${e.message}`);
  }
}

function populatePreviewImageSelect() {
  const select = el("dartPreviewImage");
  clearEl(select);
  if (!state.approvedImages.length) {
    const opt = document.createElement("option");
    opt.textContent = "нет отобранных кадров — вернитесь на шаг 3";
    opt.disabled = true;
    select.appendChild(opt);
    return;
  }
  state.approvedImages.forEach((img) => {
    const opt = document.createElement("option");
    opt.value = img.id;
    opt.textContent = img.filename || img.id;
    select.appendChild(opt);
  });
}

function dartCurrentSettings() {
  return {
    prompt: el("dartPrompt").value.trim(),
    confidence: parseFloat(el("dartConfidence").value),
    mode: el("dartMode").value,
    show_overlay: el("dartOverlay").checked,
  };
}

async function loadDartSettings() {
  if (!state.datasetId) return;
  try {
    const settings = await apiFetch(`${API}/${state.datasetId}/dart/settings`);
    el("dartPrompt").value = settings.prompt || "";
    el("dartConfidence").value = settings.confidence ?? 0.35;
    el("dartConfidenceVal").textContent = Number(
      settings.confidence ?? 0.35
    ).toFixed(2);
    el("dartMode").value = settings.mode === "bbox" ? settings.mode : "bbox";
    el("dartOverlay").checked = settings.show_overlay !== false;
  } catch (e) {
    showError(el("previewObjects"), `Не удалось загрузить настройки DART: ${e.message}`);
  }
}

async function dartPreview() {
  const imageId = el("dartPreviewImage").value;
  if (!imageId) {
    showError(el("previewObjects"), "Сначала отберите хотя бы один кадр на шаге 3");
    return;
  }
  const settings = dartCurrentSettings();
  if (!settings.prompt) {
    showError(el("previewObjects"), "Укажите prompt перед preview");
    return;
  }

  const btn = el("dartPreviewBtn");
  btn.disabled = true;
  btn.textContent = "Запускаем DART…";

  try {
    const result = await apiPostJSON(`${API}/${state.datasetId}/dart/preview`, {
      image_id: imageId,
      ...settings,
    });
    renderDartPreview(result);
    unlock("autolabel");
    el("toAutolabel").disabled = false;
    renderRail();
  } catch (e) {
    showError(el("previewObjects"), `DART preview не удался: ${e.message}`);
  } finally {
    btn.disabled = false;
    btn.textContent = "Сделать preview";
  }
}

function renderDartPreview(result) {
  const img = el("previewImage");
  const placeholder = el("previewPlaceholder");
  const safeSrc = safeHttpUrl(result.preview_url);
  if (safeSrc) {
    img.src = safeSrc;
    img.hidden = false;
    placeholder.hidden = true;
  }

  const objects = (result.result && result.result.objects) || [];
  const container = el("previewObjects");
  clearEl(container);

  if (!objects.length) {
    container.appendChild(
      makeEl("div", {
        className: "hint",
        text: `Объекты не найдены (status: ${result.status || "?"})`,
      })
    );
    return;
  }

  const tagList = makeEl("div", { className: "tag-list" });
  objects.forEach((o) => {
    const pct = Math.round((o.confidence || 0) * 100);
    tagList.appendChild(makeEl("span", { className: "tag", text: `${o.label} · ${pct}%` }));
  });
  container.appendChild(tagList);

  const count = result.objects_count ?? objects.length;
  container.appendChild(
    makeEl("div", {
      className: "hint",
      attrs: { style: "margin-top:6px;" },
      text: `Найдено объектов: ${count}`,
    })
  );
}

async function autolabelStart() {
  el("autolabelStart").disabled = true;
  el("autolabelStop").disabled = false;
  clearEl(el("autolabelErrors"));
  try {
    const startRequest = apiPostJSON(`${API}/${state.datasetId}/autolabel/start`);
    pollAutolabelStatus();
    await startRequest;
  } catch (e) {
    clearInterval(state.autolabelPollTimer);
    showError(el("autolabelErrors"), `Не удалось запустить авторазметку: ${e.message}`);
    el("autolabelStart").disabled = false;
    el("autolabelStop").disabled = true;
  }
}

async function autolabelStop() {
  try {
    await apiPostJSON(`${API}/${state.datasetId}/autolabel/stop`);
  } catch (e) {
    showError(el("autolabelErrors"), `Не удалось остановить: ${e.message}`);
  }
}

function pollAutolabelStatus() {
  clearInterval(state.autolabelPollTimer);
  state.autolabelPollTimer = setInterval(async () => {
    try {
      const data = await apiFetch(`${API}/${state.datasetId}/autolabel/status`);
      renderAutolabelStatus(data);
      if (["completed", "failed", "stopped"].includes(data.status)) {
        clearInterval(state.autolabelPollTimer);
        el("autolabelStart").disabled = false;
        el("autolabelStop").disabled = true;
        if (data.status === "completed") {
          unlock("export");
          el("toExport").disabled = false;
          renderRail();
        }
      }
    } catch (e) {
      clearInterval(state.autolabelPollTimer);
      showError(el("autolabelErrors"), `Ошибка получения статуса: ${e.message}`);
      el("autolabelStart").disabled = false;
      el("autolabelStop").disabled = true;
    }
  }, 1500);
}

function renderAutolabelStatus(data) {
  const progress = {
    done: data.processed_images ?? 0,
    total: data.total_images ?? 0,
  };
  const pct = progress.total ? Math.round((progress.done / progress.total) * 100) : 0;
  el("autolabelProgressBar").style.width = `${pct}%`;
  el("autolabelProgressText").textContent = `${progress.done} / ${progress.total}`;

  const errors = data.errors || [];
  const container = el("autolabelErrors");
  clearEl(container);
  if (!errors.length) {
    if ((data.failed_images || 0) > 0) {
      container.appendChild(
        makeEl("div", {
          className: "hint",
          text: `Ошибок обработки: ${data.failed_images}. Подробности доступны в результатах.`,
        })
      );
    }
    return;
  }

  container.appendChild(
    makeEl("h3", {
      className: "section-title",
      attrs: { style: "margin-top:16px;" },
      text: `Ошибки (${errors.length})`,
    })
  );
  const list = makeEl("ul", { className: "warning-list" });
  errors.forEach((e) => {
    list.appendChild(
      makeEl("li", { text: `${e.image_id || "?"}: ${e.message || "неизвестная ошибка"}` })
    );
  });
  container.appendChild(list);
}

async function exportBuild() {
  const format = el("exportFormat").value;
  const status = el("exportStatus");
  clearEl(status);
  status.appendChild(makeEl("div", { className: "hint", text: `Готовим экспорт (${format})…` }));
  try {
    const data = await apiPostJSON(`${API}/${state.datasetId}/cvat/export`, { format });
    clearEl(status);
    status.appendChild(makeEl("div", { className: "pill approved", text: "Экспорт готов" }));
    const archiveUrl = safeHttpUrl(data.archive_url);
    const row = makeEl("div", { className: "link-row", attrs: { style: "margin-top:10px;" } });
    row.appendChild(makeEl("span", { text: "YOLO-архив" }));
    if (archiveUrl) {
      row.appendChild(
        makeEl("a", {
          text: "Открыть архив",
          attrs: { href: archiveUrl, target: "_blank", rel: "noopener" },
        })
      );
    }
    status.appendChild(row);
    status.appendChild(
      makeEl("div", {
        className: "hint",
        attrs: { style: "margin-top:8px;" },
        text: "Импортируйте архив вручную в локальный CVAT.",
      })
    );
    el("exportImport").disabled = false;
    unlock("results");
    renderRail();
  } catch (e) {
    showError(status, `Не удалось подготовить экспорт: ${e.message}`);
  }
}

async function exportImport() {
  window.open("http://localhost:8080", "_blank", "noopener");
}

async function loadResults() {
  const links = el("resultsLinks");
  const previews = el("resultsPreviews");
  const errorsBox = el("resultsErrors");
  showEmpty(links, "Загружаем результаты…");

  try {
    const data = await apiFetch(`${API}/${state.datasetId}/results`);
    renderResults(data, links, previews, errorsBox);
  } catch (e) {
    showError(links, `Не удалось получить результаты: ${e.message}`);
  }
}

function renderResults(data, links, previews, errorsBox) {
  clearEl(links);
  const fileEntries = [
    ["Аннотации (internal JSON)", data.annotations_url || data.annotations_json_url],
    ["Ошибки (errors.json)", data.errors_url || data.errors_json_url],
    ["CVAT export — YOLO", data.cvat_export && data.cvat_export.archive_url],
  ].filter(([, url]) => !!url);

  if (fileEntries.length) {
    fileEntries.forEach(([label, url]) => {
      const row = makeEl("div", { className: "link-row" });
      row.appendChild(makeEl("span", { text: label }));
      const safeUrl = safeHttpUrl(url);
      if (safeUrl) {
        row.appendChild(
          makeEl("a", { text: safeUrl, attrs: { href: safeUrl, target: "_blank", rel: "noopener" } })
        );
      } else {
        row.appendChild(makeEl("span", { text: String(url) }));
      }
      links.appendChild(row);
    });
  } else {
    showEmpty(links, "Файлов пока нет.");
  }

  clearEl(previews);
  const previewUrls = data.previews || data.preview_urls || [];
  const safePreviewUrls = previewUrls.map(safeHttpUrl).filter(Boolean);
  if (safePreviewUrls.length) {
    safePreviewUrls.forEach((u) => {
      const img = document.createElement("img");
      img.src = u;
      img.alt = "preview";
      img.loading = "lazy";
      previews.appendChild(img);
    });
  } else {
    showEmpty(previews, "Preview-изображений пока нет.");
  }

  clearEl(errorsBox);
  const errors = data.errors || [];
  if (errors.length) {
    const list = makeEl("ul", { className: "warning-list" });
    errors.forEach((e) => {
      const message = typeof e === "string" ? e : e.message || "";
      const imageId = typeof e === "string" ? "?" : e.image_id || "?";
      list.appendChild(makeEl("li", { text: `${imageId}: ${message}` }));
    });
    errorsBox.appendChild(list);
  } else {
    showEmpty(errorsBox, "Ошибок нет.");
  }
}

function wireNav() {
  el("toStats").addEventListener("click", () => goToScreen("stats"));
  el("backToUpload").addEventListener("click", () => goToScreen("upload"));

  el("toSelect").addEventListener("click", () => goToScreen("select"));
  el("backToStats").addEventListener("click", () => goToScreen("stats"));

  el("toDart").addEventListener("click", () => goToScreen("dart"));
  el("backToSelect").addEventListener("click", () => goToScreen("select"));

  el("toAutolabel").addEventListener("click", () => goToScreen("autolabel"));
  el("backToDart").addEventListener("click", () => goToScreen("dart"));

  el("toExport").addEventListener("click", () => goToScreen("export"));
  el("backToAutolabel").addEventListener("click", () => goToScreen("autolabel"));

  el("toResults").addEventListener("click", () => {
    goToScreen("results");
    loadResults();
  });
  el("backToExport").addEventListener("click", () => goToScreen("export"));
}

function wireScreenControls() {
  el("repInit").addEventListener("click", repInit);
  el("frameApprove").addEventListener("click", frameApprove);
  el("frameUnapprove").addEventListener("click", frameUnapprove);
  el("frameNext").addEventListener("click", () => frameStep("next"));
  el("framePrev").addEventListener("click", () => frameStep("prev"));

  el("dartConfidence").addEventListener("input", (e) => {
    el("dartConfidenceVal").textContent = parseFloat(e.target.value).toFixed(2);
  });
  el("dartPreviewBtn").addEventListener("click", dartPreview);

  el("autolabelStart").addEventListener("click", autolabelStart);
  el("autolabelStop").addEventListener("click", autolabelStop);

  el("exportBuild").addEventListener("click", exportBuild);
  el("exportImport").addEventListener("click", exportImport);
}

function init() {
  checkHealth();
  initUploadScreen();
  wireNav();
  wireScreenControls();
  renderRail();
  goToScreen("upload");
}

document.addEventListener("DOMContentLoaded", init);
