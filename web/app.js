(() => {
  "use strict";

  const API_BASE = "/api/v1";
  const STYLE_FILE_MAX_BYTES = 1024 * 1024;
  const REQUEST_TIMEOUTS = Object.freeze({
    json: 30000,
    upload: 120000,
    render: 120000
  });
  const ZOOM_STEPS = [0.25, 0.33, 0.5, 0.67, 0.8, 1, 1.25, 1.5, 2, 2.5, 3];
  const DEFAULT_PAGES = ["A0", "A1", "A2", "A3", "A4", "A5", "B3", "B4", "B5", "8开", "16开", "信纸", "法律纸", "21x29.7"];
  const DEFAULT_FORMATS = ["png", "pdf", "svg"];
  const DEFAULT_PATTERN_ROW_HEIGHT_MM = Object.freeze({
    min: 1,
    max: 10,
    default: 2.5,
    step: 0.1
  });
  const DEFAULT_CATEGORIES = ["基本花纹", "沉积岩", "松散堆积物", "侵入岩", "火山熔岩", "火山碎屑岩", "变质岩", "蚀变岩", "构造岩", "混合岩", "脉岩"];
  const DEFAULT_SHAPES = [
    "小点", "中点", "大点", "密点", "疏点", "实心圆", "空心圆", "小空心圆", "大空心圆",
    "横线", "细密横线", "疏横线", "虚横线", "点划横线", "竖线", "密竖线", "虚竖线",
    "右斜线", "左斜线", "缓右斜线", "陡右斜线", "密右斜线", "虚斜线", "交叉斜线", "网格",
    "十字", "叉号", "星号", "上三角", "空上三角", "下三角", "空下三角", "实方块", "空方块",
    "实菱形", "空菱形", "短横", "短竖", "V形", "Y形", "波浪线", "密波浪线", "波状双线",
    "波状虚线", "砖纹", "斜砖纹", "双线砖纹", "砾石椭圆", "角砾三角", "X形", "⊥形",
    "⊤形", "工形", "Γ形", "人字形", "V字形", "S形", "贝壳形", "梳形", "方框加点", "三点簇", "空白"
  ];
  const DEFAULT_WIDTHS = {
    "地层单位": [0.6, 5],
    "厚度": [0.4, 3],
    "连接带": [0.2, 1.2],
    "柱状图": [1, 8],
    "备注": [0.8, 6],
    "图例": [3, 6]
  };
  const STRATA_LABELS = {
    geochron: "地质年代",
    chrono: "年代地层",
    litho: "岩石地层"
  };
  const KIND_LABELS = {
    column: "综合地层柱状图",
    section: "地层剖面图"
  };
  const SHEET_LABELS = {
    patterns: "岩性花纹一览",
    shapes: "基本图形一览",
    catalog: "国标图例"
  };

  const byId = (id) => document.getElementById(id);
  const all = (selector, root = document) => Array.from(root.querySelectorAll(selector));

  const dom = {
    modeButtons: all(".mode-button"),
    chartWorkspace: byId("chart-workspace"),
    libraryWorkspace: byId("library-workspace"),
    documentKind: byId("document-kind"),
    documentName: byId("document-name"),
    exportFormat: byId("export-format"),
    exportDpi: byId("export-dpi"),
    exportButton: byId("export-button"),
    statusText: byId("status-text"),
    statusMeta: byId("status-meta"),
    dataFile: byId("data-file"),
    dropzone: byId("data-dropzone"),
    emptyOpen: byId("empty-open-button"),
    uploadLimit: byId("upload-limit"),
    sourceSummary: byId("source-summary"),
    sourceName: byId("source-name"),
    sourceMeta: byId("source-meta"),
    reloadDocument: byId("reload-document"),
    closeDocument: byId("close-document"),
    chartControls: byId("chart-controls"),
    chartTitle: byId("chart-title"),
    chartFont: byId("chart-font"),
    chartFontSize: byId("chart-font-size"),
    chartPatternRowHeight: byId("chart-pattern-row-height"),
    columnPageControls: byId("column-page-controls"),
    sectionPageControls: byId("section-page-controls"),
    columnControls: byId("column-controls"),
    columnPage: byId("column-page"),
    pageList: byId("page-list"),
    columnLandscape: byId("column-landscape"),
    columnScale: byId("column-scale"),
    columnToScale: byId("column-to-scale"),
    columnRemark: byId("column-remark"),
    columnLegend: byId("column-legend"),
    columnUnitVertical: byId("column-unit-vertical"),
    sectionVe: byId("section-ve"),
    strataSection: byId("strata-section"),
    strataControls: byId("strata-controls"),
    widthControls: byId("width-controls"),
    resetWidths: byId("reset-widths"),
    chartPreviewMeta: byId("chart-preview-meta"),
    sheetButtons: all("[data-sheet-kind]"),
    sheetPanel: byId("library-viewport"),
    categoryWrap: byId("catalog-category-wrap"),
    category: byId("catalog-category"),
    styleFile: byId("style-file"),
    styleUpload: byId("style-upload-button"),
    styleDownload: byId("style-download-button"),
    styleSummary: byId("style-summary"),
    patternName: byId("pattern-name"),
    patternLithology: byId("pattern-lithology"),
    lithologyList: byId("lithology-list"),
    patternColorPicker: byId("pattern-color-picker"),
    patternFace: byId("pattern-face"),
    patternTile: byId("pattern-tile"),
    patternRows: byId("pattern-rows"),
    addPatternRow: byId("add-pattern-row"),
    patternPreviewImage: byId("pattern-preview-image"),
    patternPreviewPlaceholder: byId("pattern-preview-placeholder"),
    applyPattern: byId("apply-pattern"),
    helpButton: byId("help-button"),
    helpDialog: byId("help-dialog"),
    helpClose: byId("help-close"),
    helpDone: byId("help-done")
  };

  const state = {
    mode: "chart",
    capabilities: null,
    document: null,
    documentSource: null,
    uploadController: null,
    uploadRevision: 0,
    uploadBusy: false,
    exporting: false,
    libraryInitialised: false,
    dirtyControls: new Set(),
    style: {patterns: {}, lithology: {}, aliases: {}},
    styleName: "",
    styleVersion: 0,
    chart: {
      timer: null,
      controller: null,
      revision: 0,
      valid: false,
      busy: false
    },
    sheet: {
      kind: "patterns",
      controller: null,
      revision: 0,
      valid: false,
      busy: false,
      requestedKey: "",
      previewKey: "",
      cache: new Map()
    },
    styleImport: {
      controller: null,
      revision: 0,
      busy: false
    },
    pattern: {
      rows: [
        {shape: "横线", height: "1"},
        {shape: "中点", height: "1"},
        {shape: "空心圆", height: "2"}
      ],
      timer: null,
      controller: null,
      revision: 0,
      valid: false,
      objectUrl: ""
    }
  };

  class ApiError extends Error {
    constructor(message, code = "request_failed", status = 0) {
      super(message);
      this.name = "ApiError";
      this.code = code;
      this.status = status;
    }
  }

  class FigureViewer {
    constructor(prefix) {
      this.prefix = prefix;
      this.viewport = byId(`${prefix}-viewport`);
      this.canvas = byId(`${prefix}-canvas`);
      this.empty = byId(`${prefix}-empty`);
      this.loading = byId(`${prefix}-loading`);
      this.image = byId(`${prefix}-image`);
      this.label = byId(`${prefix}-zoom-label`);
      this.zoom = 1;
      this.naturalWidth = 0;
      this.naturalHeight = 0;
      this.objectUrl = "";

      this.viewport.addEventListener("wheel", (event) => {
        if (event.ctrlKey) {
          event.preventDefault();
          this.step(event.deltaY < 0 ? 1 : -1);
        } else if (event.shiftKey && Math.abs(event.deltaY) > Math.abs(event.deltaX)) {
          event.preventDefault();
          this.viewport.scrollLeft += event.deltaY;
        }
      }, {passive: false});
    }

    get hasImage() {
      return Boolean(this.naturalWidth && this.objectUrl);
    }

    setLoading(on) {
      this.loading.hidden = !on;
      this.viewport.setAttribute("aria-busy", on ? "true" : "false");
    }

    async setBlob(blob, alt, fit = false, isCurrent = () => true) {
      const nextUrl = URL.createObjectURL(blob);
      const previousUrl = this.objectUrl;
      let decodedWidth = 0;
      let decodedHeight = 0;
      try {
        await new Promise((resolve, reject) => {
          const probe = new Image();
          probe.onload = () => {
            decodedWidth = probe.naturalWidth;
            decodedHeight = probe.naturalHeight;
            resolve();
          };
          probe.onerror = () => reject(new Error("服务器返回的预览图无法显示"));
          probe.src = nextUrl;
        });
      } catch (error) {
        URL.revokeObjectURL(nextUrl);
        throw error;
      }
      if (!isCurrent()) {
        URL.revokeObjectURL(nextUrl);
        return false;
      }
      this.objectUrl = nextUrl;
      this.image.src = nextUrl;
      this.naturalWidth = decodedWidth;
      this.naturalHeight = decodedHeight;
      this.image.alt = alt;
      this.empty.hidden = true;
      this.canvas.hidden = false;
      if (previousUrl) URL.revokeObjectURL(previousUrl);
      if (fit) {
        requestAnimationFrame(() => this.fit());
      } else {
        this.applyZoom();
      }
      return true;
    }

    clear() {
      if (this.objectUrl) URL.revokeObjectURL(this.objectUrl);
      this.objectUrl = "";
      this.naturalWidth = 0;
      this.naturalHeight = 0;
      this.image.removeAttribute("src");
      this.canvas.hidden = true;
      this.empty.hidden = false;
      this.zoom = 1;
      this.label.value = "100%";
      this.label.textContent = "100%";
    }

    setZoom(value, minimum = ZOOM_STEPS[0]) {
      const next = Math.max(minimum, Math.min(ZOOM_STEPS[ZOOM_STEPS.length - 1], Number(value) || 1));
      this.zoom = next;
      this.applyZoom();
    }

    step(direction) {
      const current = this.zoom;
      const next = direction > 0
        ? ZOOM_STEPS.find((value) => value > current + 0.001)
        : [...ZOOM_STEPS].reverse().find((value) => value < current - 0.001);
      if (next !== undefined) this.setZoom(next);
    }

    fit() {
      if (!this.hasImage) return;
      const availableWidth = Math.max(50, this.viewport.clientWidth - 50);
      const availableHeight = Math.max(50, this.viewport.clientHeight - 50);
      // “适应窗口”应确实容纳整张长图；25% 只是手动缩放档位下限。
      this.setZoom(
        Math.min(availableWidth / this.naturalWidth,
                 availableHeight / this.naturalHeight),
        0.02
      );
      this.viewport.scrollTo({top: 0, left: 0});
    }

    actual() {
      this.setZoom(1);
    }

    applyZoom() {
      if (!this.hasImage) return;
      this.image.style.width = `${Math.max(1, Math.round(this.naturalWidth * this.zoom))}px`;
      this.image.style.height = `${Math.max(1, Math.round(this.naturalHeight * this.zoom))}px`;
      const text = `${Math.round(this.zoom * 100)}%`;
      this.label.value = text;
      this.label.textContent = text;
    }

    destroy() {
      if (this.objectUrl) URL.revokeObjectURL(this.objectUrl);
    }
  }

  const viewers = {
    chart: new FigureViewer("chart"),
    library: new FigureViewer("library")
  };

  function getAny(object, keys, fallback = undefined) {
    if (!object || typeof object !== "object") return fallback;
    for (const key of keys) {
      if (Object.prototype.hasOwnProperty.call(object, key) && object[key] !== undefined) {
        return object[key];
      }
    }
    return fallback;
  }

  function isPlainObject(value) {
    return Boolean(value) && typeof value === "object" && !Array.isArray(value);
  }

  function normaliseChoices(raw, fallback = []) {
    if (Array.isArray(raw)) {
      return raw.map((item) => {
        if (Array.isArray(item)) {
          return {value: String(item[0]), label: String(item[1] ?? item[0])};
        }
        if (isPlainObject(item)) {
          const value = getAny(item, ["value", "key", "id", "name"], "");
          const label = getAny(item, ["label", "title", "name", "text"], value);
          return {value: String(value), label: String(label)};
        }
        return {value: String(item), label: String(item)};
      }).filter((item) => item.value);
    }
    if (isPlainObject(raw)) {
      return Object.entries(raw).map(([key, value]) => {
        if (typeof value === "string") return {value: key, label: value};
        if (isPlainObject(value)) {
          return {
            value: String(getAny(value, ["value", "key"], key)),
            label: String(getAny(value, ["label", "title", "name"], key))
          };
        }
        return {value: key, label: key};
      });
    }
    return normaliseChoices(fallback, []);
  }

  function normaliseStringList(raw, fallback = []) {
    if (Array.isArray(raw)) {
      return raw.map((item) => isPlainObject(item)
        ? String(getAny(item, ["value", "key", "name", "label"], ""))
        : String(item)).filter(Boolean);
    }
    if (isPlainObject(raw)) return Object.keys(raw);
    return [...fallback];
  }

  function normaliseWidths(raw) {
    const source = isPlainObject(raw) ? raw : DEFAULT_WIDTHS;
    return Object.entries(source).map(([name, limits]) => {
      let min;
      let max;
      if (Array.isArray(limits)) {
        [min, max] = limits;
      } else if (isPlainObject(limits)) {
        min = getAny(limits, ["min", "minimum", "lo"], "");
        max = getAny(limits, ["max", "maximum", "hi"], "");
      }
      return {name, min, max};
    });
  }

  function normaliseCapabilities(raw = {}) {
    const upload = getAny(raw, ["upload"], {});
    const render = getAny(raw, ["render"], {});
    const sheets = getAny(raw, ["sheets"], {});
    const pattern = getAny(raw, ["pattern", "patterns"], {});
    const dpi = getAny(render, ["dpi"], {});
    const patternRowHeight = getAny(
      render,
      ["pattern_row_height_mm", "patternRowHeightMm"],
      {}
    );
    const formats = normaliseStringList(
      getAny(render, ["formats", "export_formats", "exportFormats"], getAny(raw, ["formats"], DEFAULT_FORMATS)),
      DEFAULT_FORMATS
    ).map((value) => value.toLowerCase());
    const fonts = normaliseChoices(
      getAny(render, ["fonts", "font_families", "fontFamilies"], getAny(raw, ["fonts"], [{value: "", label: "自动"}])),
      [{value: "", label: "自动"}]
    );
    if (!fonts.some((item) => item.value === "")) fonts.unshift({value: "", label: "自动"});
    const strata = normaliseChoices(
      getAny(render, ["strata", "strata_categories", "strataCategories"], Object.keys(STRATA_LABELS)),
      Object.keys(STRATA_LABELS)
    ).map((item) => ({...item, label: STRATA_LABELS[item.value] || item.label}));
    return {
      upload: {
        formats: normaliseStringList(getAny(upload, ["formats", "extensions"], ["csv", "xlsx", "xlsm"]), ["csv", "xlsx", "xlsm"])
          .map((value) => value.toLowerCase().replace(/^\./, "")),
        maxBytes: Number(getAny(upload, ["max_bytes", "maxBytes"], 0)) || 0
      },
      render: {
        formats: formats.length ? formats : DEFAULT_FORMATS,
        dpi: {
          min: Number(getAny(dpi, ["min", "minimum"], 72)) || 72,
          max: Number(getAny(dpi, ["max", "maximum"], 600)) || 600,
          default: Number(getAny(dpi, ["default", "value"], 300)) || 300
        },
        patternRowHeightMm: {
          min: Number(getAny(patternRowHeight, ["min", "minimum"], DEFAULT_PATTERN_ROW_HEIGHT_MM.min)) || DEFAULT_PATTERN_ROW_HEIGHT_MM.min,
          max: Number(getAny(patternRowHeight, ["max", "maximum"], DEFAULT_PATTERN_ROW_HEIGHT_MM.max)) || DEFAULT_PATTERN_ROW_HEIGHT_MM.max,
          default: Number(getAny(patternRowHeight, ["default", "value"], DEFAULT_PATTERN_ROW_HEIGHT_MM.default)) || DEFAULT_PATTERN_ROW_HEIGHT_MM.default,
          step: Number(getAny(patternRowHeight, ["step", "increment"], DEFAULT_PATTERN_ROW_HEIGHT_MM.step)) || DEFAULT_PATTERN_ROW_HEIGHT_MM.step
        },
        pages: normaliseChoices(getAny(render, ["pages", "page_sizes", "pageSizes"], DEFAULT_PAGES), DEFAULT_PAGES),
        fonts,
        thickModes: normaliseChoices(getAny(render, ["thick_modes", "thickModes"], [
          {value: "group", label: "按组总厚度"},
          {value: "layer", label: "逐层厚度"},
          {value: "depth", label: "地层深度"}
        ])),
        strata,
        widths: normaliseWidths(getAny(render, ["width_limits", "widthLimits"], DEFAULT_WIDTHS))
      },
      sheets: {
        kinds: normaliseStringList(getAny(sheets, ["kinds"], ["patterns", "shapes", "catalog"]), ["patterns", "shapes", "catalog"]),
        categories: normaliseStringList(getAny(sheets, ["categories", "catalog_categories", "catalogCategories"], DEFAULT_CATEGORIES), DEFAULT_CATEGORIES)
      },
      pattern: {
        enabled: Boolean(getAny(raw, ["pattern_preview", "patternPreview"], true)),
        shapes: normaliseStringList(
          getAny(pattern, ["shapes", "basic_shapes", "basicShapes"], getAny(raw, ["basic_shapes", "basicShapes"], DEFAULT_SHAPES)),
          DEFAULT_SHAPES
        )
      }
    };
  }

  async function responseError(response) {
    let message = `请求失败（HTTP ${response.status}）`;
    let code = "request_failed";
    try {
      const payload = await response.json();
      const error = getAny(payload, ["error"], payload);
      message = String(getAny(error, ["message", "detail"], message));
      code = String(getAny(error, ["code"], code));
    } catch (_error) {
      // 非 JSON 错误响应保留 HTTP 状态说明。
    }
    return new ApiError(message, code, response.status);
  }

  async function requestResource(url, options, timeoutMs, consume) {
    const externalSignal = options?.signal;
    const controller = new AbortController();
    let timedOut = false;
    const forwardAbort = () => controller.abort();
    if (externalSignal?.aborted) controller.abort();
    else externalSignal?.addEventListener("abort", forwardAbort, {once: true});
    const timer = window.setTimeout(() => {
      timedOut = true;
      controller.abort();
    }, timeoutMs);
    try {
      const response = await fetch(url, {...options, signal: controller.signal});
      if (!response.ok) throw await responseError(response);
      return await consume(response);
    } catch (error) {
      // Keep an explicit caller abort as AbortError so revision-based callers
      // can silently discard it; only the internal deadline becomes an error.
      if (timedOut && !externalSignal?.aborted) {
        throw new ApiError(`请求超时（${Math.round(timeoutMs / 1000)} 秒）`, "request_timeout", 0);
      }
      throw error;
    } finally {
      window.clearTimeout(timer);
      externalSignal?.removeEventListener("abort", forwardAbort);
    }
  }

  async function requestJson(url, options = {}, timeoutMs = REQUEST_TIMEOUTS.json) {
    return requestResource(url, options, timeoutMs, async (response) => {
      if (response.status === 204) return null;
      return response.json();
    });
  }

  async function requestBlob(url, options = {}, timeoutMs = REQUEST_TIMEOUTS.render) {
    return requestResource(url, options, timeoutMs, async (response) => {
      const blob = await response.blob();
      if (!blob.size) throw new ApiError("服务器返回了空文件", "empty_artifact", response.status);
      return {blob, filename: dispositionFilename(response.headers.get("content-disposition"))};
    });
  }

  function dispositionFilename(header) {
    if (!header) return "";
    const encoded = header.match(/filename\*=UTF-8''([^;]+)/i);
    if (encoded) {
      try { return decodeURIComponent(encoded[1]); } catch (_error) { return encoded[1]; }
    }
    const plain = header.match(/filename="?([^";]+)"?/i);
    return plain ? plain[1] : "";
  }

  function jsonRequest(body, signal) {
    return {
      method: "POST",
      headers: {"Content-Type": "application/json"},
      body: JSON.stringify(body),
      signal
    };
  }

  function setStatus(message, type = "normal") {
    dom.statusText.textContent = message;
    dom.statusText.classList.toggle("status-error", type === "error");
    dom.statusText.classList.toggle("status-warning", type === "warning");
    dom.statusText.classList.toggle("status-success", type === "success");
  }

  function errorMessage(error) {
    if (error instanceof ApiError) return error.message;
    if (error && error.message) return String(error.message);
    return "发生未知错误";
  }

  function formatBytes(bytes) {
    if (!Number.isFinite(bytes) || bytes <= 0) return "";
    if (bytes < 1024 * 1024) return `${Math.ceil(bytes / 1024)} KB`;
    return `${(bytes / 1024 / 1024).toFixed(bytes < 10 * 1024 * 1024 ? 1 : 0)} MB`;
  }

  function appendOption(parent, value, label) {
    const option = document.createElement("option");
    option.value = String(value);
    option.textContent = String(label);
    parent.append(option);
    return option;
  }

  function fillSelect(select, choices, selected = "") {
    select.replaceChildren();
    for (const choice of choices) appendOption(select, choice.value, choice.label);
    if (choices.some((choice) => choice.value === selected)) select.value = selected;
  }

  function markControlDirty(key) {
    state.dirtyControls.add(key);
  }

  function fieldIsDirty(key) {
    return state.dirtyControls.has(key);
  }

  function setFieldInvalid(control, invalid) {
    if (!control) return;
    if (invalid) control.setAttribute("aria-invalid", "true");
    else control.removeAttribute("aria-invalid");
  }

  function applyCapabilities(caps) {
    state.capabilities = caps;

    const formatChoices = caps.render.formats
      .filter((format) => DEFAULT_FORMATS.includes(format))
      .map((format) => ({value: format, label: format.toUpperCase()}));
    const exportFormat = fieldIsDirty("export-format") ? dom.exportFormat.value : "png";
    fillSelect(dom.exportFormat, formatChoices.length ? formatChoices : DEFAULT_FORMATS.map((value) => ({value, label: value.toUpperCase()})), exportFormat);
    dom.exportDpi.min = String(caps.render.dpi.min);
    dom.exportDpi.max = String(caps.render.dpi.max);
    if (!fieldIsDirty("export-dpi")) dom.exportDpi.value = String(caps.render.dpi.default);

    const patternRowHeight = caps.render.patternRowHeightMm;
    dom.chartPatternRowHeight.min = String(patternRowHeight.min);
    dom.chartPatternRowHeight.max = String(patternRowHeight.max);
    dom.chartPatternRowHeight.step = String(patternRowHeight.step);
    if (!fieldIsDirty("chart-pattern-row-height") && !state.document) {
      dom.chartPatternRowHeight.value = String(patternRowHeight.default);
    }

    dom.pageList.replaceChildren();
    for (const page of caps.render.pages) appendOption(dom.pageList, page.value, page.label);
    if (!fieldIsDirty("column-page") && !state.document) {
      dom.columnPage.value = caps.render.pages.some((item) => item.value === "A4") ? "A4" : (caps.render.pages[0]?.value || "A4");
    }
    const chartFont = fieldIsDirty("chart-font") || state.document ? dom.chartFont.value : "";
    fillSelect(dom.chartFont, caps.render.fonts, chartFont);

    for (const mode of caps.render.thickModes) {
      const input = document.querySelector(`input[name="thick-mode"][value="${cssEscape(mode.value)}"]`);
      const label = input?.nextElementSibling;
      if (label) label.textContent = mode.label;
    }

    buildWidthControls();
    const category = fieldIsDirty("catalog-category")
      ? dom.category.value
      : (caps.sheets.categories[1] || caps.sheets.categories[0] || "");
    fillSelect(dom.category, caps.sheets.categories.map((value) => ({value, label: value})), category);
    if (state.libraryInitialised && !fieldIsDirty("pattern-rows")) renderPatternRows();

    const limit = caps.upload.maxBytes ? `单个文件不超过 ${formatBytes(caps.upload.maxBytes)}` : "程序将自动识别柱状图或剖面图";
    dom.uploadLimit.textContent = limit;
  }

  function cssEscape(value) {
    if (window.CSS && typeof window.CSS.escape === "function") return window.CSS.escape(String(value));
    return String(value).replace(/["\\]/g, "\\$&");
  }

  function buildWidthControls() {
    const widths = state.capabilities?.render.widths || normaliseWidths(DEFAULT_WIDTHS);
    const previous = new Map(all("[data-width-key]", dom.widthControls).map((input) => [input.dataset.widthKey, input.value]));
    dom.widthControls.replaceChildren();
    for (const item of widths) {
      const row = document.createElement("label");
      row.className = "width-row";

      const text = document.createElement("span");
      text.className = "width-label";
      text.textContent = item.name === "图例" ? "图例项宽度" : item.name;
      if (item.min !== "" && item.max !== "" && item.min !== undefined && item.max !== undefined) {
        const range = document.createElement("small");
        range.textContent = `${item.min}–${item.max} cm`;
        text.append(range);
      }

      const input = document.createElement("input");
      input.type = "number";
      input.step = "0.1";
      input.inputMode = "decimal";
      input.placeholder = "默认";
      input.dataset.widthKey = item.name;
      input.setAttribute("data-render-control", "");
      if (item.min !== "" && item.min !== undefined) input.min = String(item.min);
      if (item.max !== "" && item.max !== undefined) input.max = String(item.max);
      if (previous.has(item.name)) input.value = previous.get(item.name);

      row.append(text, input);
      dom.widthControls.append(row);
    }
  }

  async function loadCapabilities() {
    try {
      const payload = await requestJson(`${API_BASE}/capabilities`);
      applyCapabilities(normaliseCapabilities(payload));
      if (!state.uploadBusy && !state.document && !state.sheet.busy && !state.styleImport.busy) {
        setStatus("绘图服务已就绪。请选择数据文件，或打开内置示例。", "success");
      }
    } catch (error) {
      applyCapabilities(normaliseCapabilities());
      if (!state.uploadBusy && !state.document && !state.sheet.busy && !state.styleImport.busy) {
        setStatus(`暂时无法读取服务能力：${errorMessage(error)}。仍可尝试上传数据。`, "error");
      }
    }
  }

  function setMode(mode) {
    if (!['chart', 'library'].includes(mode)) return;
    state.mode = mode;
    dom.chartWorkspace.hidden = mode !== "chart";
    dom.libraryWorkspace.hidden = mode !== "library";
    for (const button of dom.modeButtons) {
      const active = button.dataset.mode === mode;
      button.classList.toggle("active", active);
      if (active) button.setAttribute("aria-current", "page");
      else button.removeAttribute("aria-current");
    }
    updateHeader();
    updateExportButton();
    if (mode === "library") initialiseLibrary();
  }

  function initialiseLibrary() {
    if (!state.libraryInitialised) {
      state.libraryInitialised = true;
      renderPatternRows();
      schedulePatternPreview(0);
    }
    void ensureSheetPreview();
  }

  function updateHeader() {
    if (state.mode === "library") {
      dom.documentKind.textContent = "岩性花纹库";
      dom.documentName.textContent = state.styleName || "";
      return;
    }
    if (!state.document) {
      dom.documentKind.textContent = "尚未载入数据";
      dom.documentName.textContent = "";
      return;
    }
    dom.documentKind.textContent = KIND_LABELS[state.document.kind] || state.document.kind || "图件";
    dom.documentName.textContent = state.document.filename || "";
  }

  function updateExportButton() {
    const valid = state.mode === "chart" ? Boolean(state.document && state.chart.valid) : state.sheet.valid;
    dom.exportButton.textContent = state.mode === "chart" ? "导出图片" : "导出一览表";
    dom.exportButton.disabled = !valid || state.exporting;
  }

  function normaliseDocument(payload) {
    const unitColumns = getAny(payload, ["unit_columns", "unitColumns", "available_unit_columns", "availableUnitColumns"], []);
    return {
      id: String(getAny(payload, ["id", "document_id", "documentId"], "")),
      filename: String(getAny(payload, ["filename", "file_name", "fileName", "name"], "数据文件")),
      kind: String(getAny(payload, ["kind", "chart_type", "chartType", "type"], "")),
      summary: getAny(payload, ["summary", "meta"], ""),
      lithologies: normaliseStringList(getAny(payload, ["lithologies", "lithology_names", "lithologyNames"], [])),
      unitColumns: Array.isArray(unitColumns) ? unitColumns.map((item) => {
        if (typeof item === "string") return {key: item, label: item, category: ""};
        if (Array.isArray(item)) return {key: String(item[0]), label: String(item[1] ?? item[0]), category: String(item[2] ?? "")};
        return {
          key: String(getAny(item, ["key", "id", "value"], "")),
          label: String(getAny(item, ["label", "head", "name", "title"], getAny(item, ["key"], ""))),
          category: String(getAny(item, ["category", "cat", "group"], ""))
        };
      }).filter((item) => item.label) : [],
      defaults: getAny(payload, ["options", "defaults", "default_options", "defaultOptions"], {})
    };
  }

  function documentSummary(documentMeta) {
    if (!documentMeta) return "";
    const summary = documentMeta.summary;
    if (typeof summary === "string") return summary;
    if (!isPlainObject(summary)) return KIND_LABELS[documentMeta.kind] || "已载入";
    if (documentMeta.kind === "column") {
      const layers = getAny(summary, ["layers", "layer_count", "layerCount", "count"], "");
      const total = getAny(summary, ["total_thickness", "totalThickness", "total", "thickness"], "");
      const parts = [];
      if (layers !== "") parts.push(`${layers} 层`);
      if (total !== "") parts.push(`总厚 ${total} m`);
      return parts.length ? `柱状图 · ${parts.join(" · ")}` : "综合地层柱状图";
    }
    const holes = getAny(summary, ["holes", "hole_count", "holeCount", "count"], "");
    const layers = getAny(summary, ["layers", "layer_count", "layerCount"], "");
    const parts = [];
    if (holes !== "") parts.push(`${holes} 个钻孔`);
    if (layers !== "") parts.push(`${layers} 层`);
    return parts.length ? `剖面图 · ${parts.join(" · ")}` : "地层剖面图";
  }

  function validateUpload(file) {
    if (!file) throw new Error("没有选择文件");
    const extension = file.name.includes(".") ? file.name.split(".").pop().toLowerCase() : "";
    const allowed = state.capabilities?.upload.formats || ["csv", "xlsx", "xlsm"];
    if (!allowed.includes(extension)) throw new Error(`不支持 .${extension || "未知"} 文件，请选择 CSV、XLSX 或 XLSM`);
    const maxBytes = state.capabilities?.upload.maxBytes || 0;
    if (maxBytes && file.size > maxBytes) throw new Error(`文件大小超过 ${formatBytes(maxBytes)} 限制`);
  }

  async function openFile(file) {
    try {
      validateUpload(file);
    } catch (error) {
      setStatus(errorMessage(error), "error");
      return;
    }
    state.uploadController?.abort();
    state.uploadRevision += 1;
    const revision = state.uploadRevision;
    const controller = new AbortController();
    state.uploadController = controller;
    setUploadBusy(true);
    setStatus(`正在上传并解析：${file.name}`);
    const previousId = state.document?.id || "";
    try {
      const payload = await requestJson(`${API_BASE}/documents?filename=${encodeURIComponent(file.name)}`, {
        method: "POST",
        body: file,
        signal: controller.signal
      }, REQUEST_TIMEOUTS.upload);
      const meta = normaliseDocument(payload);
      if (controller.signal.aborted || revision !== state.uploadRevision) {
        if (meta.id) void deleteDocument(meta.id, true);
        return;
      }
      await installDocument(meta);
      state.documentSource = {type: "file", file};
      updateDocumentUi();
      if (previousId && previousId !== state.document.id) void deleteDocument(previousId, true);
    } catch (error) {
      if (error.name !== "AbortError" && revision === state.uploadRevision) {
        setStatus(`数据读取失败：${errorMessage(error)}`, "error");
      }
    } finally {
      if (state.uploadController === controller && revision === state.uploadRevision) {
        state.uploadController = null;
        setUploadBusy(false);
        dom.dataFile.value = "";
      }
    }
  }

  async function openExample(kind) {
    state.uploadController?.abort();
    state.uploadRevision += 1;
    const revision = state.uploadRevision;
    const controller = new AbortController();
    state.uploadController = controller;
    setUploadBusy(true);
    setStatus(`正在载入${kind === "column" ? "柱状图" : "剖面图"}示例…`);
    const previousId = state.document?.id || "";
    try {
      const payload = await requestJson(`${API_BASE}/examples/${kind}`, {method: "POST", signal: controller.signal});
      const meta = normaliseDocument(payload);
      if (controller.signal.aborted || revision !== state.uploadRevision) {
        if (meta.id) void deleteDocument(meta.id, true);
        return;
      }
      await installDocument(meta);
      state.documentSource = {type: "example", kind};
      updateDocumentUi();
      if (previousId && previousId !== state.document.id) void deleteDocument(previousId, true);
    } catch (error) {
      if (error.name !== "AbortError" && revision === state.uploadRevision) {
        setStatus(`示例载入失败：${errorMessage(error)}`, "error");
      }
    } finally {
      if (state.uploadController === controller && revision === state.uploadRevision) {
        state.uploadController = null;
        setUploadBusy(false);
      }
    }
  }

  function setUploadBusy(busy) {
    state.uploadBusy = busy;
    dom.dropzone.setAttribute("aria-busy", busy ? "true" : "false");
    dom.dropzone.setAttribute("aria-disabled", busy ? "true" : "false");
    dom.dropzone.tabIndex = busy ? -1 : 0;
    dom.dataFile.disabled = busy;
    dom.emptyOpen.disabled = busy;
    dom.reloadDocument.disabled = busy || !state.documentSource;
    for (const button of all("[data-example]")) button.disabled = busy;
  }

  async function installDocument(meta) {
    if (!meta.id || !["column", "section"].includes(meta.kind)) {
      throw new Error("服务器返回的文档元数据不完整");
    }
    state.document = meta;
    state.chart.controller?.abort();
    if (state.chart.timer) clearTimeout(state.chart.timer);
    state.chart.valid = false;
    state.chart.busy = false;
    state.chart.revision += 1;
    viewers.chart.setLoading(false);
    viewers.chart.clear();
    resetDocumentControls(meta);
    updateDocumentUi();
    updateLithologyList();
    setMode("chart");
    setStatus(`已载入：${meta.filename}，正在生成预览…`);
    scheduleChartRender(0, true);
  }

  function resetDocumentControls(meta) {
    const defaults = isPlainObject(meta.defaults) ? meta.defaults : {};
    dom.chartTitle.value = String(getAny(defaults, ["title"], meta.kind === "column" ? "综合地层柱状图" : "地层剖面图"));
    dom.chartFont.value = String(getAny(defaults, ["font"], "") || "");
    dom.chartFontSize.value = String(getAny(defaults, ["font_size", "fontSize"], 8));
    const patternRowHeightDefault = state.capabilities?.render.patternRowHeightMm?.default
      ?? DEFAULT_PATTERN_ROW_HEIGHT_MM.default;
    dom.chartPatternRowHeight.value = String(getAny(
      defaults,
      ["pattern_row_height_mm", "patternRowHeightMm"],
      patternRowHeightDefault
    ));
    setFieldInvalid(dom.chartPatternRowHeight, false);
    dom.columnPage.value = String(getAny(defaults, ["page"], "A4"));
    dom.columnLandscape.checked = Boolean(getAny(defaults, ["landscape"], false));
    dom.columnScale.value = getAny(defaults, ["scale"], "") ?? "";
    dom.columnToScale.checked = Boolean(getAny(defaults, ["to_scale", "toScale"], false));
    const thickMode = String(getAny(defaults, ["thick_mode", "thickMode"], "group"));
    const thickInput = document.querySelector(`input[name="thick-mode"][value="${cssEscape(thickMode)}"]`)
      || document.querySelector('input[name="thick-mode"][value="group"]');
    if (thickInput) thickInput.checked = true;
    dom.columnRemark.value = getAny(defaults, ["show_remark", "showRemark"], null) === false ? "hidden" : "auto";
    dom.columnLegend.checked = Boolean(getAny(defaults, ["show_legend", "showLegend"], false));
    dom.columnUnitVertical.checked = Boolean(getAny(defaults, ["unit_vertical", "unitVertical"], false));
    dom.sectionVe.value = getAny(defaults, ["ve"], "") ?? "";
    for (const input of all("[data-width-key]", dom.widthControls)) input.value = "";
    buildStrataControls(meta.unitColumns);
  }

  function updateDocumentUi() {
    const meta = state.document;
    const loaded = Boolean(meta);
    dom.chartControls.disabled = !loaded;
    dom.sourceSummary.hidden = !loaded;
    dom.reloadDocument.disabled = state.uploadBusy || !state.documentSource;
    if (loaded) {
      dom.sourceName.textContent = meta.filename;
      dom.sourceMeta.textContent = documentSummary(meta);
      dom.chartPreviewMeta.textContent = documentSummary(meta);
      dom.columnPageControls.hidden = meta.kind !== "column";
      dom.columnControls.hidden = meta.kind !== "column";
      dom.sectionPageControls.hidden = meta.kind !== "section";
      dom.statusMeta.textContent = documentSummary(meta);
    } else {
      dom.sourceName.textContent = "";
      dom.sourceMeta.textContent = "";
      dom.chartPreviewMeta.textContent = "打开一份数据开始绘图";
      dom.columnPageControls.hidden = true;
      dom.columnControls.hidden = true;
      dom.sectionPageControls.hidden = true;
      dom.statusMeta.textContent = "";
    }
    updateHeader();
    updateExportButton();
  }

  function buildStrataControls(columns) {
    dom.strataControls.replaceChildren();
    const groups = new Map();
    for (const column of columns || []) {
      const category = column.category || "other";
      if (!groups.has(category)) groups.set(category, []);
      groups.get(category).push(column);
    }
    dom.strataSection.hidden = groups.size === 0;
    const capabilityLabels = new Map((state.capabilities?.render.strata || []).map((item) => [item.value, item.label]));
    for (const [category, items] of groups) {
      const group = document.createElement("div");
      group.className = "strata-group";

      const parentLabel = document.createElement("label");
      parentLabel.className = "check-line";
      const parentInput = document.createElement("input");
      parentInput.type = "checkbox";
      parentInput.checked = true;
      parentInput.dataset.strataCategory = category;
      parentInput.setAttribute("data-render-control", "");
      const parentText = document.createElement("span");
      parentText.textContent = capabilityLabels.get(category) || STRATA_LABELS[category] || category;
      parentLabel.append(parentInput, parentText);

      const children = document.createElement("div");
      children.className = "unit-children";
      for (const item of items) {
        const childLabel = document.createElement("label");
        childLabel.className = "check-line";
        const childInput = document.createElement("input");
        childInput.type = "checkbox";
        childInput.checked = true;
        childInput.dataset.unitLabel = item.label;
        childInput.dataset.unitCategory = category;
        childInput.setAttribute("data-render-control", "");
        const childText = document.createElement("span");
        childText.textContent = item.label;
        childLabel.append(childInput, childText);
        children.append(childLabel);
      }
      group.append(parentLabel, children);
      dom.strataControls.append(group);
    }
  }

  function syncStrataChildren(category) {
    const parent = all("[data-strata-category]", dom.strataControls).find((input) => input.dataset.strataCategory === category);
    for (const input of all("[data-unit-category]", dom.strataControls)) {
      if (input.dataset.unitCategory === category) input.disabled = !parent?.checked;
    }
  }

  async function closeCurrentDocument() {
    if (!state.document) return;
    const id = state.document.id;
    state.document = null;
    state.documentSource = null;
    state.chart.controller?.abort();
    if (state.chart.timer) clearTimeout(state.chart.timer);
    state.chart.valid = false;
    state.chart.busy = false;
    state.chart.revision += 1;
    viewers.chart.setLoading(false);
    viewers.chart.clear();
    updateDocumentUi();
    updateLithologyList();
    setStatus("文档已关闭。");
    await deleteDocument(id, true);
  }

  async function reloadCurrentDocument() {
    if (state.uploadBusy || !state.documentSource) return;
    const source = state.documentSource;
    if (source.type === "file" && source.file) {
      await openFile(source.file);
    } else if (source.type === "example" && source.kind) {
      await openExample(source.kind);
    }
  }

  async function deleteDocument(id, silent = false) {
    if (!id) return;
    try {
      await requestJson(`${API_BASE}/documents/${encodeURIComponent(id)}`, {method: "DELETE"});
    } catch (error) {
      if (!silent) setStatus(`关闭文档失败：${errorMessage(error)}`, "error");
    }
  }

  function numberOrText(input, emptyValue = null) {
    const raw = String(input.value).trim();
    if (!raw) return emptyValue;
    const value = Number(raw);
    return Number.isFinite(value) ? value : raw;
  }

  function validateNumber(control, label, minimum, maximum, {optional = false, integer = false, valueText = null} = {}) {
    const raw = valueText === null ? String(control.value).trim() : String(valueText).trim();
    if (optional && !raw) {
      setFieldInvalid(control, false);
      return null;
    }
    const value = Number(raw);
    const invalid = !Number.isFinite(value) || value < minimum || value > maximum || (integer && !Number.isInteger(value));
    setFieldInvalid(control, invalid);
    if (invalid) {
      const integerHint = integer ? "整数" : "数字";
      throw new Error(`${label}应为 ${minimum}–${maximum} 之间的${integerHint}`);
    }
    return value;
  }

  function collectRenderOptions() {
    if (!state.document) throw new Error("尚未载入数据");
    validateNumber(dom.chartFontSize, "字号", 6, 12);
    const patternRowHeight = state.capabilities?.render.patternRowHeightMm
      || DEFAULT_PATTERN_ROW_HEIGHT_MM;
    const options = {
      title: dom.chartTitle.value.trim(),
      font: dom.chartFont.value || null,
      font_size: numberOrText(dom.chartFontSize, 8),
      pattern_row_height_mm: validateNumber(
        dom.chartPatternRowHeight,
        "花纹层厚",
        patternRowHeight.min,
        patternRowHeight.max
      )
    };
    if (state.document.kind === "section") {
      options.ve = validateNumber(dom.sectionVe, "垂直夸大", 0.1, 50, {optional: true});
      return options;
    }

    const scaleText = dom.columnScale.value.trim().replace(/^1\s*[:：]\s*/, "");
    const scaleValue = validateNumber(dom.columnScale, "比例尺", 1, 100000, {optional: true, integer: true, valueText: scaleText});
    options.page = dom.columnPage.value.trim() || "A4";
    options.landscape = dom.columnLandscape.checked;
    options.scale = scaleText ? (Number.isFinite(scaleValue) ? scaleValue : scaleText) : null;
    options.to_scale = dom.columnToScale.checked;
    options.thick_mode = document.querySelector('input[name="thick-mode"]:checked')?.value || "group";
    options.show_remark = dom.columnRemark.value === "hidden" ? false : null;
    options.show_legend = dom.columnLegend.checked;
    options.unit_vertical = dom.columnUnitVertical.checked;

    const categoryInputs = all("[data-strata-category]", dom.strataControls);
    const selectedCategories = categoryInputs.filter((input) => input.checked).map((input) => input.dataset.strataCategory);
    options.strata = categoryInputs.length && selectedCategories.length !== categoryInputs.length ? selectedCategories : null;
    const selectedSet = new Set(selectedCategories);
    const hiddenUnits = all("[data-unit-label]", dom.strataControls)
      .filter((input) => selectedSet.has(input.dataset.unitCategory) && !input.checked)
      .map((input) => input.dataset.unitLabel);
    options.hide_units = hiddenUnits.length ? hiddenUnits : null;

    const widths = {};
    for (const input of all("[data-width-key]", dom.widthControls)) {
      if (!input.value.trim()) continue;
      widths[input.dataset.widthKey] = numberOrText(input, null);
    }
    options.widths = widths;
    return options;
  }

  function previewDpi() {
    const dpi = state.capabilities?.render.dpi || {min: 72, max: 600};
    // 144 DPI keeps 0.1–0.3 mm geological symbols legible on screen while
    // remaining far below the formal-export default of 300 DPI.
    return Math.max(dpi.min, Math.min(dpi.max, 144));
  }

  function scheduleChartRender(delay = 350, fit = false) {
    if (!state.document) return;
    state.chart.revision += 1;
    const revision = state.chart.revision;
    state.chart.valid = false;
    state.chart.controller?.abort();
    if (state.chart.timer) clearTimeout(state.chart.timer);
    updateExportButton();
    state.chart.timer = window.setTimeout(() => {
      state.chart.timer = null;
      void renderChart(revision, fit);
    }, delay);
  }

  async function renderChart(revision, fit = false) {
    const meta = state.document;
    if (!meta || revision !== state.chart.revision) return;
    const controller = new AbortController();
    state.chart.controller = controller;
    state.chart.busy = true;
    viewers.chart.setLoading(true);
    setStatus(`正在绘制${KIND_LABELS[meta.kind] || "图件"}…`);
    try {
      const body = {
        format: "png",
        dpi: previewDpi(),
        download: false,
        options: collectRenderOptions(),
        style: state.style
      };
      const {blob} = await requestBlob(`${API_BASE}/documents/${encodeURIComponent(meta.id)}/render`, jsonRequest(body, controller.signal));
      if (controller.signal.aborted || revision !== state.chart.revision || state.document?.id !== meta.id) return;
      const installed = await viewers.chart.setBlob(
        blob,
        `${KIND_LABELS[meta.kind] || "地层图件"}预览`,
        fit || !viewers.chart.hasImage,
        () => revision === state.chart.revision && state.document?.id === meta.id
      );
      if (!installed) return;
      if (revision !== state.chart.revision) return;
      state.chart.valid = true;
      setStatus(`已绘制${KIND_LABELS[meta.kind] || "图件"}：${meta.filename}`, "success");
    } catch (error) {
      if (error.name !== "AbortError" && revision === state.chart.revision) {
        state.chart.valid = false;
        setStatus(`绘图失败：${errorMessage(error)}。已保留上一张成功预览，导出已暂停。`, "error");
      }
    } finally {
      if (state.chart.controller === controller) state.chart.controller = null;
      if (revision === state.chart.revision) {
        state.chart.busy = false;
        viewers.chart.setLoading(false);
        updateExportButton();
      }
    }
  }

  function validatedExportDpi() {
    const limits = state.capabilities?.render.dpi || {min: 72, max: 600, default: 300};
    return Math.round(validateNumber(dom.exportDpi, "DPI", limits.min, limits.max, {integer: true}));
  }

  async function exportCurrent() {
    if (state.exporting) return;
    const format = dom.exportFormat.value;
    let dpi;
    try {
      dpi = validatedExportDpi();
    } catch (error) {
      setStatus(errorMessage(error), "error");
      dom.exportDpi.focus();
      return;
    }
    if (!DEFAULT_FORMATS.includes(format)) {
      setStatus("不支持所选导出格式", "error");
      return;
    }
    if (state.mode === "chart") {
      await exportChart(format, dpi);
    } else {
      await exportSheet(format, dpi);
    }
  }

  async function exportChart(format, dpi) {
    if (!state.document || !state.chart.valid) {
      setStatus("当前参数还没有成功生成预览，暂不能导出。", "warning");
      return;
    }
    // Keep the export bound to the document that the user clicked on.  The
    // active document can be closed or replaced while the server is drawing.
    const document = state.document;
    state.exporting = true;
    updateExportButton();
    setStatus(`正在生成 ${format.toUpperCase()} 文件…`);
    try {
      const body = {
        format,
        dpi,
        download: true,
        options: collectRenderOptions(),
        style: state.style
      };
      const result = await requestBlob(`${API_BASE}/documents/${encodeURIComponent(document.id)}/render`, jsonRequest(body));
      const base = fileBase(document.filename) || (document.kind === "column" ? "综合地层柱状图" : "地层剖面图");
      downloadBlob(result.blob, result.filename || `${base}.${format}`);
      setStatus(`已生成：${base}.${format}`, "success");
    } catch (error) {
      setStatus(`导出失败：${errorMessage(error)}`, "error");
    } finally {
      state.exporting = false;
      updateExportButton();
    }
  }

  function currentSheetCategory() {
    return state.sheet.kind === "catalog" ? dom.category.value : null;
  }

  function sheetKey(kind = state.sheet.kind, category = currentSheetCategory(), styleVersion = state.styleVersion) {
    return `${styleVersion}:${kind}:${category || ""}`;
  }

  async function postSheetBlob(kind, category, format, dpi, style, signal) {
    return requestBlob(`${API_BASE}/sheets/render`, jsonRequest({
      kind,
      category: kind === "catalog" ? category : null,
      format,
      dpi,
      style
    }, signal));
  }

  function selectSheet(kind) {
    if (!["patterns", "shapes", "catalog"].includes(kind)) return;
    state.sheet.kind = kind;
    dom.categoryWrap.hidden = kind !== "catalog";
    for (const button of dom.sheetButtons) {
      const active = button.dataset.sheetKind === kind;
      button.classList.toggle("active", active);
      button.setAttribute("aria-selected", active ? "true" : "false");
      button.tabIndex = active ? 0 : -1;
      if (active) dom.sheetPanel.setAttribute("aria-labelledby", button.id);
    }
    state.sheet.valid = false;
    updateExportButton();
    void ensureSheetPreview();
  }

  async function ensureSheetPreview(force = false) {
    if (!state.libraryInitialised) return;
    const kind = state.sheet.kind;
    const category = currentSheetCategory();
    const key = sheetKey(kind, category);
    if (!force && state.sheet.valid && state.sheet.previewKey === key) return;
    if (!force && state.sheet.busy && state.sheet.requestedKey === key) return;

    // Every new desired preview, including a cache hit, owns a revision.  This
    // prevents an older network failure from invalidating the cached result.
    state.sheet.revision += 1;
    const revision = state.sheet.revision;
    state.sheet.controller?.abort();
    state.sheet.controller = null;
    state.sheet.requestedKey = key;
    state.sheet.valid = false;
    state.sheet.busy = true;
    viewers.library.setLoading(true);
    updateExportButton();

    let controller = null;
    try {
      const cached = !force ? state.sheet.cache.get(key) : null;
      if (cached) {
        let installed = false;
        try {
          installed = await viewers.library.setBlob(
            cached,
            `${SHEET_LABELS[kind] || "花纹表"}预览`,
            !viewers.library.hasImage,
            () => revision === state.sheet.revision && sheetKey() === key
          );
        } catch (_error) {
          if (revision !== state.sheet.revision) return;
          state.sheet.cache.delete(key);
        }
        if (installed) {
          if (revision !== state.sheet.revision || sheetKey() !== key) return;
          state.sheet.previewKey = key;
          state.sheet.valid = true;
          const suffix = kind === "catalog" && category ? ` · ${category}` : "";
          setStatus(`${SHEET_LABELS[kind] || "花纹一览"}${suffix}`, "success");
          return;
        }
      }

      controller = new AbortController();
      state.sheet.controller = controller;
      setStatus(`正在生成${SHEET_LABELS[kind] || "花纹一览"}…`);
      const {blob} = await postSheetBlob(kind, category, "png", previewDpi(), state.style, controller.signal);
      if (controller.signal.aborted || revision !== state.sheet.revision) return;
      const installed = await viewers.library.setBlob(
        blob,
        `${SHEET_LABELS[kind] || "花纹表"}预览`,
        !viewers.library.hasImage,
        () => revision === state.sheet.revision && sheetKey() === key
      );
      if (!installed || revision !== state.sheet.revision || sheetKey() !== key) return;
      state.sheet.cache.set(key, blob);
      state.sheet.previewKey = key;
      state.sheet.valid = true;
      const suffix = kind === "catalog" && category ? ` · ${category}` : "";
      setStatus(`${SHEET_LABELS[kind] || "花纹一览"}${suffix}`, "success");
    } catch (error) {
      if (error.name !== "AbortError" && revision === state.sheet.revision) {
        state.sheet.valid = false;
        setStatus(`一览表生成失败：${errorMessage(error)}。已保留上一张成功预览，导出已暂停。`, "error");
      }
    } finally {
      if (controller && state.sheet.controller === controller) state.sheet.controller = null;
      if (revision === state.sheet.revision) {
        state.sheet.busy = false;
        viewers.library.setLoading(false);
        updateExportButton();
      }
    }
  }

  async function exportSheet(format, dpi) {
    if (!state.sheet.valid) {
      setStatus("当前一览表还没有成功生成，暂不能导出。", "warning");
      return;
    }
    state.exporting = true;
    updateExportButton();
    const kind = state.sheet.kind;
    const category = currentSheetCategory();
    setStatus(`正在生成 ${format.toUpperCase()} 一览表…`);
    try {
      const result = await postSheetBlob(kind, category, format, dpi, state.style);
      const base = kind === "catalog" ? `国标图例·${category || "分类"}` : SHEET_LABELS[kind];
      downloadBlob(result.blob, result.filename || `${base}.${format}`);
      setStatus(`已生成：${base}.${format}`, "success");
    } catch (error) {
      setStatus(`导出失败：${errorMessage(error)}`, "error");
    } finally {
      state.exporting = false;
      updateExportButton();
    }
  }

  function fileBase(filename) {
    return String(filename || "").replace(/\.[^.]+$/, "").replace(/[\\/:*?"<>|\u0000-\u001f]/g, "_").trim();
  }

  function safeFilename(filename) {
    return String(filename || "download")
      .replace(/[\\/:*?"<>|\u0000-\u001f]/g, "_")
      .replace(/^\.+/, "")
      .trim() || "download";
  }

  function downloadBlob(blob, filename) {
    const url = URL.createObjectURL(blob);
    const link = document.createElement("a");
    link.href = url;
    link.download = safeFilename(filename);
    document.body.append(link);
    link.click();
    link.remove();
    window.setTimeout(() => URL.revokeObjectURL(url), 1000);
  }

  function buildPatternSpec() {
    const tile = Number(dom.patternTile.value);
    const tileInvalid = !Number.isFinite(tile) || tile < 0.3 || tile > 20;
    setFieldInvalid(dom.patternTile, tileInvalid);
    if (tileInvalid) throw new Error("瓦片高度应在 0.3–20 之间");
    if (!state.pattern.rows.length) throw new Error("花纹至少需要一行");
    const rows = [];
    const heights = [];
    for (const [index, row] of state.pattern.rows.entries()) {
      const height = Number(row.height);
      const control = dom.patternRows.querySelector(`[data-pattern-index="${index}"][data-pattern-field="height"]`);
      const invalid = !Number.isFinite(height) || height < 0.1 || height > 20;
      setFieldInvalid(control, invalid);
      if (invalid) throw new Error(`第 ${index + 1} 行高度应在 0.1–20 之间`);
      rows.push(row.shape || "空白");
      heights.push(height);
    }
    return [{type: "rows", spacing: tile, heights, rows}];
  }

  function validFace() {
    const face = dom.patternFace.value.trim();
    const invalid = !/^#[0-9a-fA-F]{6}$/.test(face);
    setFieldInvalid(dom.patternFace, invalid);
    if (invalid) throw new Error("底色请使用 #RRGGBB 格式");
    return face;
  }

  function schedulePatternPreview(delay = 350) {
    state.pattern.revision += 1;
    const revision = state.pattern.revision;
    state.pattern.valid = false;
    dom.applyPattern.disabled = true;
    state.pattern.controller?.abort();
    if (state.pattern.timer) clearTimeout(state.pattern.timer);
    state.pattern.timer = window.setTimeout(() => {
      state.pattern.timer = null;
      void renderPatternPreview(revision);
    }, delay);
  }

  async function renderPatternPreview(revision, returnBlob = false) {
    let spec;
    let face;
    try {
      spec = buildPatternSpec();
      face = validFace();
    } catch (error) {
      if (revision === state.pattern.revision) {
        dom.patternPreviewPlaceholder.textContent = errorMessage(error);
        dom.patternPreviewPlaceholder.hidden = false;
        state.pattern.valid = false;
        dom.applyPattern.disabled = true;
      }
      if (returnBlob) throw error;
      return null;
    }
    const controller = new AbortController();
    state.pattern.controller = controller;
    dom.patternPreviewPlaceholder.textContent = "正在生成预览…";
    dom.patternPreviewPlaceholder.hidden = !dom.patternPreviewImage.hidden;
    try {
      const result = await requestBlob(`${API_BASE}/patterns/preview`, jsonRequest({spec, face, dpi: previewDpi()}, controller.signal));
      if (controller.signal.aborted || revision !== state.pattern.revision) return null;
      setPatternPreviewBlob(result.blob, revision);
      state.pattern.valid = true;
      dom.applyPattern.disabled = false;
      return returnBlob ? result.blob : null;
    } catch (error) {
      if (error.name !== "AbortError" && revision === state.pattern.revision) {
        dom.patternPreviewPlaceholder.textContent = `预览失败：${errorMessage(error)}`;
        dom.patternPreviewPlaceholder.hidden = false;
        state.pattern.valid = false;
        dom.applyPattern.disabled = true;
      }
      if (returnBlob) throw error;
      return null;
    } finally {
      if (state.pattern.controller === controller) state.pattern.controller = null;
    }
  }

  function setPatternPreviewBlob(blob, revision = state.pattern.revision) {
    const url = URL.createObjectURL(blob);
    const previous = state.pattern.objectUrl;
    const probe = new Image();
    probe.onload = () => {
      if (revision !== state.pattern.revision) {
        URL.revokeObjectURL(url);
        return;
      }
      dom.patternPreviewImage.src = url;
      dom.patternPreviewImage.hidden = false;
      dom.patternPreviewPlaceholder.hidden = true;
      if (previous) URL.revokeObjectURL(previous);
      state.pattern.objectUrl = url;
    };
    probe.onerror = () => {
      URL.revokeObjectURL(url);
      dom.patternPreviewPlaceholder.textContent = "预览图无法显示";
      dom.patternPreviewPlaceholder.hidden = false;
    };
    probe.src = url;
  }

  function renderPatternRows() {
    const shapes = state.capabilities?.pattern.shapes?.length ? state.capabilities.pattern.shapes : DEFAULT_SHAPES;
    dom.patternRows.replaceChildren();
    state.pattern.rows.forEach((row, index) => {
      const wrapper = document.createElement("div");
      wrapper.className = "pattern-row";

      const select = document.createElement("select");
      select.dataset.patternIndex = String(index);
      select.dataset.patternField = "shape";
      select.setAttribute("aria-label", `第 ${index + 1} 行基本图形`);
      const values = shapes.includes(row.shape) ? shapes : [row.shape, ...shapes];
      for (const shape of values) appendOption(select, shape, shape);
      select.value = row.shape;

      const height = document.createElement("input");
      height.type = "number";
      height.min = "0.1";
      height.max = "20";
      height.step = "0.1";
      height.value = row.height;
      height.dataset.patternIndex = String(index);
      height.dataset.patternField = "height";
      height.setAttribute("aria-label", `第 ${index + 1} 行高度`);

      const actions = document.createElement("div");
      actions.className = "row-actions";
      const actionItems = [
        {action: "up", text: "↑", label: `上移第 ${index + 1} 行`, disabled: index === 0},
        {action: "down", text: "↓", label: `下移第 ${index + 1} 行`, disabled: index === state.pattern.rows.length - 1},
        {action: "remove", text: "×", label: `删除第 ${index + 1} 行`, disabled: state.pattern.rows.length <= 1}
      ];
      for (const item of actionItems) {
        const button = document.createElement("button");
        button.type = "button";
        button.textContent = item.text;
        button.dataset.patternAction = item.action;
        button.dataset.patternIndex = String(index);
        button.classList.toggle("remove", item.action === "remove");
        button.disabled = item.disabled;
        button.setAttribute("aria-label", item.label);
        actions.append(button);
      }
      wrapper.append(select, height, actions);
      dom.patternRows.append(wrapper);
    });
  }

  function handlePatternRowsEvent(event) {
    const control = event.target.closest("[data-pattern-field]");
    if (!control) return;
    const index = Number(control.dataset.patternIndex);
    const field = control.dataset.patternField;
    if (!state.pattern.rows[index] || !["shape", "height"].includes(field)) return;
    markControlDirty("pattern-rows");
    state.pattern.rows[index][field] = control.value;
    schedulePatternPreview();
  }

  function handlePatternRowAction(event) {
    const button = event.target.closest("[data-pattern-action]");
    if (!button) return;
    const index = Number(button.dataset.patternIndex);
    const action = button.dataset.patternAction;
    if (!state.pattern.rows[index]) return;
    markControlDirty("pattern-rows");
    if (action === "remove" && state.pattern.rows.length > 1) {
      state.pattern.rows.splice(index, 1);
    } else if (action === "up" && index > 0) {
      [state.pattern.rows[index - 1], state.pattern.rows[index]] = [state.pattern.rows[index], state.pattern.rows[index - 1]];
    } else if (action === "down" && index < state.pattern.rows.length - 1) {
      [state.pattern.rows[index + 1], state.pattern.rows[index]] = [state.pattern.rows[index], state.pattern.rows[index + 1]];
    } else {
      return;
    }
    renderPatternRows();
    schedulePatternPreview();
  }

  function safeStyleSection(value, name) {
    if (value === undefined || value === null) return {};
    if (!isPlainObject(value)) throw new Error(`样式的 ${name} 段必须是 JSON 对象`);
    const output = {};
    for (const [key, item] of Object.entries(value)) {
      if (["__proto__", "prototype", "constructor"].includes(key)) continue;
      output[key] = item;
    }
    return output;
  }

  function normaliseStyle(payload) {
    if (!isPlainObject(payload)) throw new Error("样式文件顶层必须是 JSON 对象");
    return {
      patterns: safeStyleSection(getAny(payload, ["patterns"], {}), "patterns"),
      lithology: safeStyleSection(getAny(payload, ["lithology"], {}), "lithology"),
      aliases: safeStyleSection(getAny(payload, ["aliases"], {}), "aliases")
    };
  }

  function cloneStyle(style = state.style) {
    return JSON.parse(JSON.stringify(style));
  }

  function setStyleImportBusy(busy) {
    state.styleImport.busy = busy;
    dom.styleUpload.disabled = busy;
    dom.styleFile.disabled = busy;
    dom.styleUpload.setAttribute("aria-busy", busy ? "true" : "false");
  }

  async function importStyle(file) {
    if (!file) return;
    state.styleImport.revision += 1;
    const revision = state.styleImport.revision;
    state.styleImport.controller?.abort();
    if (file.size > STYLE_FILE_MAX_BYTES) {
      state.styleImport.controller = null;
      setStyleImportBusy(false);
      setStatus(`样式未载入：文件超过 ${formatBytes(STYLE_FILE_MAX_BYTES)} 限制。`, "error");
      dom.styleFile.value = "";
      return;
    }

    const controller = new AbortController();
    state.styleImport.controller = controller;
    setStyleImportBusy(true);
    setStatus(`正在读取样式：${file.name}…`);
    try {
      const text = await file.text();
      if (revision !== state.styleImport.revision || controller.signal.aborted) return;
      const candidate = normaliseStyle(JSON.parse(text));
      setStatus(`正在校验样式：${file.name}…`);
      const {blob} = await postSheetBlob("patterns", null, "png", previewDpi(), candidate, controller.signal);
      if (revision !== state.styleImport.revision || controller.signal.aborted) return;
      commitStyle(candidate, file.name);
      const key = sheetKey("patterns", null);
      state.sheet.cache.set(key, blob);
      if (state.libraryInitialised) void ensureSheetPreview();
      setStatus(`已载入并校验样式：${file.name}`, "success");
    } catch (error) {
      if (error.name !== "AbortError" && revision === state.styleImport.revision) {
        setStatus(`样式未载入：${errorMessage(error)}。当前样式保持不变。`, "error");
      }
    } finally {
      if (state.styleImport.controller === controller && revision === state.styleImport.revision) {
        state.styleImport.controller = null;
        setStyleImportBusy(false);
        dom.styleFile.value = "";
        updateExportButton();
      }
    }
  }

  function commitStyle(style, name = "") {
    state.style = style;
    state.styleName = name;
    state.styleVersion += 1;
    state.sheet.controller?.abort();
    state.sheet.revision += 1;
    state.sheet.valid = false;
    state.sheet.busy = false;
    state.sheet.requestedKey = "";
    state.sheet.previewKey = "";
    state.sheet.cache.clear();
    viewers.library.setLoading(false);
    updateStyleSummary();
    updateLithologyList();
    updateHeader();
    updateExportButton();
    if (state.document) scheduleChartRender(0, false);
  }

  function updateStyleSummary() {
    const patterns = Object.keys(state.style.patterns || {}).length;
    const lithologies = Object.keys(state.style.lithology || {}).length;
    const aliases = Object.keys(state.style.aliases || {}).length;
    if (!patterns && !lithologies && !aliases) {
      dom.styleSummary.textContent = "当前使用内置样式，尚无自定义项。";
      return;
    }
    const source = state.styleName ? `${state.styleName} · ` : "";
    dom.styleSummary.textContent = `${source}${patterns} 个花纹，${lithologies} 个岩性设置，${aliases} 个别名。`;
  }

  function updateLithologyList() {
    const names = new Set(state.document?.lithologies || []);
    for (const name of Object.keys(state.style.lithology || {})) names.add(name);
    dom.lithologyList.replaceChildren();
    for (const name of names) appendOption(dom.lithologyList, name, name);
  }

  async function applyPattern() {
    const name = dom.patternName.value.trim() || "我的花纹";
    if (name.startsWith("_") || ["__proto__", "prototype", "constructor"].includes(name)) {
      setStatus("该花纹名称不可用", "error");
      return;
    }
    let spec;
    let face;
    try {
      spec = buildPatternSpec();
      face = validFace();
    } catch (error) {
      setStatus(errorMessage(error), "error");
      return;
    }

    state.pattern.revision += 1;
    const revision = state.pattern.revision;
    state.pattern.controller?.abort();
    dom.applyPattern.disabled = true;
    setStatus(`正在校验花纹“${name}”…`);
    try {
      const result = await requestBlob(`${API_BASE}/patterns/preview`, jsonRequest({spec, face, dpi: previewDpi()}));
      if (revision !== state.pattern.revision) return;
      setPatternPreviewBlob(result.blob, revision);
      const candidate = cloneStyle();
      candidate.patterns[name] = spec;
      const lithology = dom.patternLithology.value.trim();
      if (lithology) {
        if (["__proto__", "prototype", "constructor"].includes(lithology)) throw new Error("该岩性名称不可用");
        candidate.lithology[lithology] = {color: face, pattern: name};
        candidate.aliases[lithology] = lithology;
      }
      commitStyle(candidate, state.styleName);
      state.pattern.valid = true;
      dom.applyPattern.disabled = false;
      void ensureSheetPreview(true);
      setStatus(`已应用花纹“${name}”${lithology ? ` → 岩性“${lithology}”` : ""}，相关预览正在更新。`, "success");
    } catch (error) {
      if (revision === state.pattern.revision) {
        state.pattern.valid = false;
        dom.applyPattern.disabled = false;
        setStatus(`花纹无法应用：${errorMessage(error)}`, "error");
      }
    }
  }

  function downloadStyle() {
    const content = `${JSON.stringify(state.style, null, 2)}\n`;
    downloadBlob(new Blob([content], {type: "application/json;charset=utf-8"}), "strat_style.json");
    setStatus("已生成样式文件：strat_style.json", "success");
  }

  function handleChartControl(event) {
    if (!state.document || !event.target.closest("[data-render-control]")) return;
    if (event.target.id) markControlDirty(event.target.id);
    if (event.target.dataset.widthKey) markControlDirty("widths");
    const category = event.target.dataset.strataCategory;
    if (category) syncStrataChildren(category);
    scheduleChartRender();
  }

  function bindEvents() {
    for (const button of dom.modeButtons) button.addEventListener("click", () => setMode(button.dataset.mode));
    for (const button of dom.sheetButtons) button.addEventListener("click", () => selectSheet(button.dataset.sheetKind));
    for (const button of dom.sheetButtons) {
      button.addEventListener("keydown", (event) => {
        const current = dom.sheetButtons.indexOf(button);
        let next = null;
        if (event.key === "ArrowRight" || event.key === "ArrowDown") next = (current + 1) % dom.sheetButtons.length;
        else if (event.key === "ArrowLeft" || event.key === "ArrowUp") next = (current - 1 + dom.sheetButtons.length) % dom.sheetButtons.length;
        else if (event.key === "Home") next = 0;
        else if (event.key === "End") next = dom.sheetButtons.length - 1;
        if (next === null) return;
        event.preventDefault();
        const target = dom.sheetButtons[next];
        selectSheet(target.dataset.sheetKind);
        target.focus();
      });
    }
    dom.category.addEventListener("change", () => {
      markControlDirty("catalog-category");
      state.sheet.valid = false;
      updateExportButton();
      void ensureSheetPreview();
    });

    dom.exportButton.addEventListener("click", () => void exportCurrent());
    dom.exportFormat.addEventListener("change", () => markControlDirty("export-format"));
    dom.exportDpi.addEventListener("input", () => {
      markControlDirty("export-dpi");
      if (dom.exportDpi.checkValidity()) setFieldInvalid(dom.exportDpi, false);
    });
    dom.emptyOpen.addEventListener("click", () => {
      if (!state.uploadBusy) dom.dataFile.click();
    });
    dom.dropzone.addEventListener("click", () => {
      if (!state.uploadBusy) dom.dataFile.click();
    });
    dom.dropzone.addEventListener("keydown", (event) => {
      if (!state.uploadBusy && (event.key === "Enter" || event.key === " ")) {
        event.preventDefault();
        dom.dataFile.click();
      }
    });
    dom.dataFile.addEventListener("change", () => void openFile(dom.dataFile.files?.[0]));
    for (const eventName of ["dragenter", "dragover"]) {
      dom.dropzone.addEventListener(eventName, (event) => {
        event.preventDefault();
        if (event.dataTransfer) event.dataTransfer.dropEffect = "copy";
        dom.dropzone.classList.add("dragover");
      });
    }
    for (const eventName of ["dragleave", "drop"]) {
      dom.dropzone.addEventListener(eventName, (event) => {
        event.preventDefault();
        dom.dropzone.classList.remove("dragover");
      });
    }
    dom.dropzone.addEventListener("drop", (event) => {
      if (!state.uploadBusy) void openFile(event.dataTransfer?.files?.[0]);
    });
    for (const button of all("[data-example]")) {
      button.addEventListener("click", () => {
        if (!state.uploadBusy) void openExample(button.dataset.example);
      });
    }
    dom.closeDocument.addEventListener("click", () => void closeCurrentDocument());
    dom.reloadDocument.addEventListener("click", () => void reloadCurrentDocument());

    dom.chartControls.addEventListener("input", handleChartControl);
    dom.chartControls.addEventListener("change", handleChartControl);
    dom.resetWidths.addEventListener("click", () => {
      for (const input of all("[data-width-key]", dom.widthControls)) input.value = "";
      scheduleChartRender(0);
    });

    for (const button of all("[data-zoom-target]")) {
      button.addEventListener("click", () => {
        const viewer = viewers[button.dataset.zoomTarget];
        if (!viewer) return;
        if (button.dataset.zoomStep) viewer.step(Number(button.dataset.zoomStep));
        if (button.dataset.zoomAction === "fit") viewer.fit();
        if (button.dataset.zoomAction === "actual") viewer.actual();
      });
    }

    for (const button of all("[data-panel]")) {
      button.addEventListener("click", () => {
        const workspace = button.dataset.panel === "chart" ? dom.chartWorkspace : dom.libraryWorkspace;
        const collapsed = workspace.classList.toggle("panel-collapsed");
        button.setAttribute("aria-expanded", collapsed ? "false" : "true");
        button.textContent = collapsed ? "显示面板" : (button.dataset.panel === "chart" ? "属性" : "设计器");
      });
    }

    dom.styleUpload.addEventListener("click", () => {
      if (!state.styleImport.busy) dom.styleFile.click();
    });
    dom.styleFile.addEventListener("change", () => void importStyle(dom.styleFile.files?.[0]));
    dom.styleDownload.addEventListener("click", downloadStyle);

    dom.patternColorPicker.addEventListener("input", () => {
      dom.patternFace.value = dom.patternColorPicker.value;
      schedulePatternPreview();
    });
    dom.patternFace.addEventListener("input", () => {
      const value = dom.patternFace.value.trim();
      if (/^#[0-9a-fA-F]{6}$/.test(value)) dom.patternColorPicker.value = value;
      schedulePatternPreview();
    });
    dom.patternTile.addEventListener("input", () => schedulePatternPreview());
    dom.patternRows.addEventListener("input", handlePatternRowsEvent);
    dom.patternRows.addEventListener("change", handlePatternRowsEvent);
    dom.patternRows.addEventListener("click", handlePatternRowAction);
    dom.addPatternRow.addEventListener("click", () => {
      markControlDirty("pattern-rows");
      state.pattern.rows.push({shape: state.capabilities?.pattern.shapes?.[0] || "横线", height: "1"});
      renderPatternRows();
      schedulePatternPreview();
    });
    dom.applyPattern.addEventListener("click", () => void applyPattern());

    dom.helpButton.addEventListener("click", openHelp);
    dom.helpClose.addEventListener("click", closeHelp);
    dom.helpDone.addEventListener("click", closeHelp);
    dom.helpDialog.addEventListener("click", (event) => {
      if (event.target === dom.helpDialog) closeHelp();
    });

    document.addEventListener("keydown", (event) => {
      if (!(event.ctrlKey || event.metaKey)) return;
      if (event.key.toLowerCase() === "o") {
        event.preventDefault();
        if (!state.uploadBusy) dom.dataFile.click();
      } else if (event.key.toLowerCase() === "e") {
        event.preventDefault();
        void exportCurrent();
      } else if (event.key === "1") {
        event.preventDefault();
        setMode("chart");
      } else if (event.key === "2") {
        event.preventDefault();
        setMode("library");
      } else if (event.key === "0") {
        event.preventDefault();
        viewers[state.mode].fit();
      }
    });

    window.addEventListener("pagehide", (event) => {
      // A page kept in the back-forward cache must retain its document.  On a
      // real unload, release the bounded server-side slot without delaying
      // navigation; the repository TTL remains the crash/offline fallback.
      if (event.persisted) return;
      const id = state.document?.id;
      state.document = null;
      state.documentSource = null;
      if (id) {
        void fetch(`${API_BASE}/documents/${encodeURIComponent(id)}`, {
          method: "DELETE",
          keepalive: true,
          credentials: "same-origin"
        }).catch(() => {});
      }
      viewers.chart.destroy();
      viewers.library.destroy();
      if (state.pattern.objectUrl) URL.revokeObjectURL(state.pattern.objectUrl);
    });
  }

  function openHelp() {
    if (typeof dom.helpDialog.showModal === "function") dom.helpDialog.showModal();
    else dom.helpDialog.setAttribute("open", "");
  }

  function closeHelp() {
    if (typeof dom.helpDialog.close === "function") dom.helpDialog.close();
    else dom.helpDialog.removeAttribute("open");
  }

  async function init() {
    bindEvents();
    applyCapabilities(normaliseCapabilities());
    updateDocumentUi();
    updateStyleSummary();
    updateLithologyList();
    await loadCapabilities();
  }

  void init();
})();
