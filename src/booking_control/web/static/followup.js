/* Luoi Follow Up 47 cot - Jspreadsheet CE (MIT), chay offline.
 *
 * Nguyen tac:
 *  - Cot / quyen sua / cong thuc lay tu server (config/followup_template.yaml) -> khong hard-code.
 *  - Moi thay doi (go, dan tu Excel, keo dien, hoan tac) gom lai 0.6s roi gui 1 lan.
 *  - Server kiem quyen tung o, tinh lai cong thuc + trang thai, tra ve dong chuan -> cap nhat luoi.
 *  - Optimistic lock: gui kem version cua dong; nguoi khac vua sua -> server tra dong moi, khong ghi de.
 *  - Nhieu nguoi dung chung: cu vai giay keo thay doi cua nguoi khac ve va bao ai dang sua dong nao.
 *
 * Thao tac kieu Excel desktop: loc tung cot, an/hien cot, thanh cong thuc, thanh trang thai
 * (SUM/AVG/COUNT vung chon), Ctrl+D / Ctrl+R dien xuong / sang phai, Ctrl+F tim, Ctrl+H thay the.
 */
(function () {
  "use strict";
  var CFG = window.GRID_CFG;
  var COLS = CFG.columns;
  var N = COLS.length;
  var SYS = [
    { title: "PHỤ TRÁCH", key: "assigned_to", width: 110 },
    { title: "TRẠNG THÁI", key: "status", width: 96 },
    { title: "HẠN", key: "deadline", width: 92 }
  ];
  var CODE_COL = N + SYS.length;
  var FIELD_IDX = {};
  COLS.forEach(function (c, i) { FIELD_IDX[c.field] = i; });
  var LETTER = {};      // chu cot Excel -> chi so cot tren luoi
  COLS.forEach(function (c, i) { LETTER[window.FX.indexLetter(i + 1)] = i; });
  var USER_LABEL = {};
  (CFG.users || []).forEach(function (u) { USER_LABEL[u.name] = u.label; });
  var TODAY = new Date().toISOString().slice(0, 10);
  var WARN_LABEL = {
    BOOKING_DEADLINE_WARNING: "⚠ Sắp quá hạn booking — chưa có booking",
    BOOKING_OVERDUE: "⚠ Chưa book mà đã qua ngày xuất"
  };

  var el = document.getElementById("grid");
  var stateEl = document.getElementById("save-state");
  var msgEl = document.getElementById("grid-msg");
  var countEl = document.getElementById("row-count");
  var statsEl = document.getElementById("sel-stats");
  var fxInput = document.getElementById("fx-input");
  var fxRef = document.getElementById("fx-ref");
  var peersEl = document.getElementById("peers");

  var sheet = null;
  var ALL = [];            // toan bo dong server tra ve, theo thu tu server
  var rowsByCode = {};
  var view = [];           // code cua cac dong dang hien (sau khi loc cot)
  var colFilter = {};      // chi so cot -> mang gia tri duoc chon
  var hiddenCols = {};     // chi so cot -> true
  var editors = {};        // code -> ten nguoi khac dang sua
  var lastSync = "";
  var suppress = false;
  var dirty = {};
  var touchedFormula = {};
  var timer = null;
  var saving = false;
  var selection = null;
  var pendingFx = {};      // code|field -> cong thuc nguoi dung vua go, cho gui len may chu

  // ------------------------------------------------------------------ tien ich
  function setState(text, cls) {
    stateEl.textContent = text;
    stateEl.className = "save-state " + (cls || "");
  }
  function showMsg(text, cls) {
    if (!text) { msgEl.hidden = true; return; }
    msgEl.textContent = text;
    msgEl.className = "flash " + (cls || "warn");
    msgEl.hidden = false;
  }
  function display(col, v) {
    if (v === null || v === undefined) return "";
    if (col.type === "date") return String(v).slice(0, 10);
    return v;
  }
  function norm(col, v) {
    if (v === null || v === undefined) return "";
    var s = String(v).trim();
    if (col.type === "date") return s.slice(0, 10);
    if (col.type === "number" || col.type === "int") {
      var n = parseFloat(s.replace(/,/g, ""));
      return isNaN(n) ? s : String(n);
    }
    return s;
  }
  function codeAt(y) {
    var d = sheet.options.data[y];
    return d ? d[CODE_COL] : null;
  }
  function yOf(code) {
    var d = sheet.options.data;
    for (var y = 0; y < d.length; y++) if (d[y][CODE_COL] === code) return y;
    return -1;
  }
  function sysValues(r) {
    return [USER_LABEL[r.assigned_to] || r.assigned_to || "—", r.status || "", r.deadline || ""];
  }
  function toGridRow(r) {
    return r.cells.map(function (v, i) { return display(COLS[i], v); })
      .concat(sysValues(r), [r.code]);
  }

  // ------------------------------------------------------------ cong thuc
  function ctxFor(getByIndex) {
    return {
      today: TODAY,
      field: function (name) {
        var i = FIELD_IDX[name];
        return i === undefined ? null : getByIndex(i);
      },
      letter: function (l) {
        var i = LETTER[l];
        return i === undefined ? null : getByIndex(i);
      }
    };
  }
  function evalTemplateFormula(col, ctx) {
    var f = col.formula;
    if (f.kind === "invoice_suffix") {
      var inv = String(ctx.field(f.source) || "").trim();
      if (!inv || inv.toUpperCase().indexOf(f.prefix.toUpperCase()) !== 0) return "";
      var tail = inv.slice(-f.last_digits);
      return /^\d+$/.test(tail) ? parseInt(tail, 10) + f.offset : "";
    }
    return tidy(window.FX.evalNode(f.node, ctx));
  }
  function tidy(v) {
    if (v === null || v === undefined) return "";
    if (typeof v === "number") {
      if (!isFinite(v)) return "";
      return Math.round(v * 1e6) / 1e6;
    }
    if (typeof v === "boolean") return v ? "TRUE" : "FALSE";
    return v;
  }
  /* Cot cong thuc "song" = gia tri dang dung bang ket qua cong thuc (hoac dang trong).
   * Dong Excel go tay so NO (khong dung cong thuc) thi giu nguyen - giong logic o server. */
  function liveFormulas(r) {
    var live = {};
    var ctx = ctxFor(function (i) { return r.cells[i]; });
    (CFG.formula_order || []).forEach(function (field) {
      var col = COLS[FIELD_IDX[field]];
      var cur = r.cells[FIELD_IDX[field]];
      if (r.origin === "WEB" || cur === null || cur === "" ||
          norm(col, cur) === norm(col, evalTemplateFormula(col, ctx))) live[field] = true;
    });
    return live;
  }
  function recomputeRow(y) {
    var code = codeAt(y);
    var r = rowsByCode[code] || { cells: [], manual: [], fx: {} };
    var manual = r.manual || [];
    var live = liveFormulas(r);
    var row = sheet.options.data[y];
    var ctx = ctxFor(function (i) { return row[i]; });
    suppress = true;
    try {
      // 1. Cot cong thuc co san cua template
      (CFG.formula_order || []).forEach(function (field) {
        var i = FIELD_IDX[field];
        if (!live[field] || manual.indexOf(field) >= 0 || touchedFormula[code + "|" + field]) return;
        if (r.fx && r.fx[field]) return;   // nguoi dung da go cong thuc rieng cho o nay
        var v = evalTemplateFormula(COLS[i], ctx);
        if (norm(COLS[i], row[i]) !== norm(COLS[i], v)) sheet.setValueFromCoords(i, y, v, true);
      });
      // 2. Cong thuc nguoi dung go thang vao o
      Object.keys(r.fx || {}).forEach(function (field) {
        var i = FIELD_IDX[field];
        if (i === undefined) return;
        var v;
        try { v = tidy(window.FX.evalNode(r.fx[field].node, ctx)); } catch (e) { return; }
        if (norm(COLS[i], row[i]) !== norm(COLS[i], v)) sheet.setValueFromCoords(i, y, v, true);
      });
    } finally { suppress = false; }
  }

  // --------------------------------------------------------------- to mau dong
  function tint(hex, strength) {
    var n = parseInt(hex.replace("#", ""), 16);
    var rgb = [(n >> 16) & 255, (n >> 8) & 255, n & 255].map(function (c) {
      return Math.round(255 - (255 - c) * strength);
    });
    return "rgb(" + rgb.join(",") + ")";
  }
  function injectColorCss() {
    var css = "";
    Object.keys(CFG.colors || {}).forEach(function (k) {
      css += '#grid .jexcel tbody tr[data-st="' + k + '"] > td:not(.jexcel_row){background-color:' + tint(CFG.colors[k], 0.3) + "}\n";
      css += '#grid .jexcel tbody tr[data-st="' + k + '"] > td.jexcel_row{box-shadow:inset 4px 0 0 ' + CFG.colors[k] + "}\n";
    });
    var s = document.createElement("style");
    s.textContent = css;
    document.head.appendChild(s);
  }
  function paintRow(y) {
    var code = codeAt(y);
    var r = rowsByCode[code];
    if (!r || !sheet.rows[y]) return;
    var tr = sheet.rows[y];
    tr.setAttribute("data-st", r.status || "");
    // Canh bao han booking la lop RIENG, khong phai mau trang thai (SRSv5.1 muc 16)
    tr.setAttribute("data-warn", r.warn || "");
    tr.classList.toggle("locked-by-peer", !!editors[code]);
    var warnText = r.warn ? (WARN_LABEL[r.warn] || r.warn) + "\n" : "";
    tr.title = (editors[code] ? "⚠ " + editors[code] + " đang sửa dòng này\n" : "")
      + warnText + (r.reason || "");
    var rec = sheet.records[y];
    var manual = r.manual || [];
    var fx = r.fx || {};
    for (var x = 0; x < N; x++) {
      var td = rec[x];
      if (!td) continue;
      var field = COLS[x].field;
      td.classList.toggle("fx", !!COLS[x].formula && manual.indexOf(field) < 0 && !fx[field]);
      td.classList.toggle("cellfx", !!fx[field]);
      td.classList.toggle("manual", manual.indexOf(field) >= 0 && !fx[field]);
      td.classList.toggle("ro", !COLS[x].editable);
      td.classList.toggle("req", COLS[x].required && (sheet.options.data[y][x] === "" || sheet.options.data[y][x] === null));
    }
  }
  function paintAll() { for (var y = 0; y < sheet.rows.length; y++) paintRow(y); }

  // ------------------------------------------------------- loc theo tung cot
  function distinctValues(colIdx) {
    var seen = {}, out = [];
    ALL.forEach(function (r) {
      if (!passesExcept(r, colIdx)) return;
      var v = display(COLS[colIdx], r.cells[colIdx]);
      var key = v === "" ? "\u0000" : String(v);   // khoa rieng cho o trong
      if (seen[key]) { seen[key].n++; return; }
      seen[key] = { v: String(v), n: 1 };
      out.push(seen[key]);
    });
    out.sort(function (a, b) { return a.v.localeCompare(b.v, "vi", { numeric: true }); });
    return out;
  }
  function passesExcept(r, skipIdx) {
    for (var k in colFilter) {
      var idx = parseInt(k, 10);
      if (idx === skipIdx) continue;
      var allowed = colFilter[k];
      if (!allowed) continue;
      var v = String(display(COLS[idx], r.cells[idx]));
      if (allowed.indexOf(v) < 0) return false;
    }
    return true;
  }
  function passes(r) { return passesExcept(r, -1); }

  function applyColumnFilters(keepScroll) {
    view = ALL.filter(passes).map(function (r) { return r.code; });
    var top = keepScroll && el.querySelector(".jexcel_content")
      ? el.querySelector(".jexcel_content").scrollTop : 0;
    build(view.map(function (c) { return toGridRow(rowsByCode[c]); }));
    if (top) {
      var box = el.querySelector(".jexcel_content");
      if (box) box.scrollTop = top;
    }
    updateCount();
    updateFilterMarks();
  }
  function updateCount() {
    var n = view.length, total = ALL.length;
    countEl.textContent = n === total
      ? total + " dòng"
      : n + " / " + total + " dòng (đang lọc)";
    if (pageInfo) countEl.appendChild(pageInfo.cloneNode(true));
  }
  function updateFilterMarks() {
    if (!sheet || !sheet.headers) return;
    for (var x = 0; x < N; x++) {
      var btn = sheet.headers[x] && sheet.headers[x].querySelector(".fu-fbtn");
      if (btn) btn.classList.toggle("on", !!colFilter[x]);
    }
  }

  var filterBox = null;
  function openFilter(colIdx, anchor) {
    closeFilter();
    var values = distinctValues(colIdx);
    var chosen = colFilter[colIdx];
    filterBox = document.createElement("div");
    filterBox.className = "fu-filterbox";
    filterBox.innerHTML =
      '<div class="ttl">' + COLS[colIdx].title + "</div>" +
      '<div class="srt"><button type="button" data-s="a">A → Z</button>' +
      '<button type="button" data-s="d">Z → A</button></div>' +
      '<input class="q" placeholder="Tìm trong cột...">' +
      '<label class="all"><input type="checkbox" class="ck-all"> (Chọn tất cả)</label>' +
      '<div class="list"></div>' +
      '<div class="act"><button type="button" class="ok">Lọc</button>' +
      '<button type="button" class="ghost clr">Bỏ lọc cột</button></div>';
    document.body.appendChild(filterBox);
    var rect = anchor.getBoundingClientRect();
    filterBox.style.left = Math.max(6, Math.min(rect.left, window.innerWidth - 272)) + "px";
    filterBox.style.top = (rect.bottom + window.scrollY + 2) + "px";

    var list = filterBox.querySelector(".list");
    values.forEach(function (item) {
      var lb = document.createElement("label");
      var on = !chosen || chosen.indexOf(item.v) >= 0;
      lb.innerHTML = '<input type="checkbox" ' + (on ? "checked" : "") + '>' +
        '<span class="v"></span><i>' + item.n + "</i>";
      lb.querySelector(".v").textContent = item.v === "" ? "(trống)" : item.v;
      lb.firstChild.value = item.v;
      list.appendChild(lb);
    });
    function boxes() { return Array.prototype.slice.call(list.querySelectorAll("input")); }
    var ckAll = filterBox.querySelector(".ck-all");
    ckAll.checked = !chosen;
    ckAll.addEventListener("change", function () {
      boxes().forEach(function (b) { if (b.parentNode.style.display !== "none") b.checked = ckAll.checked; });
    });
    filterBox.querySelector(".q").addEventListener("input", function () {
      var q = this.value.toLowerCase();
      boxes().forEach(function (b) {
        var text = b.parentNode.querySelector(".v").textContent.toLowerCase();
        b.parentNode.style.display = text.indexOf(q) >= 0 ? "" : "none";
      });
    });
    filterBox.querySelectorAll(".srt button").forEach(function (b) {
      b.addEventListener("click", function () {
        sortByColumn(colIdx, b.getAttribute("data-s") === "a");
        closeFilter();
      });
    });
    filterBox.querySelector(".ok").addEventListener("click", function () {
      var picked = boxes().filter(function (b) { return b.checked; }).map(function (b) { return b.value; });
      colFilter[colIdx] = picked.length === values.length ? null : picked;
      if (!colFilter[colIdx]) delete colFilter[colIdx];
      closeFilter();
      applyColumnFilters();
    });
    filterBox.querySelector(".clr").addEventListener("click", function () {
      delete colFilter[colIdx];
      closeFilter();
      applyColumnFilters();
    });
    setTimeout(function () { document.addEventListener("mousedown", outsideClose, true); }, 0);
  }
  function outsideClose(e) {
    if (filterBox && !filterBox.contains(e.target)) closeFilter();
  }
  function closeFilter() {
    document.removeEventListener("mousedown", outsideClose, true);
    if (filterBox) { filterBox.remove(); filterBox = null; }
  }
  function sortByColumn(colIdx, asc) {
    var col = COLS[colIdx];
    ALL.sort(function (a, b) {
      var x = a.cells[colIdx], y = b.cells[colIdx];
      var nx = window.FX.toNum(x), ny = window.FX.toNum(y);
      var r;
      if (nx !== null && ny !== null) r = nx - ny;
      else r = String(display(col, x)).localeCompare(String(display(col, y)), "vi", { numeric: true });
      return asc ? r : -r;
    });
    applyColumnFilters();
  }

  // ------------------------------------------------------------ an / hien cot
  function buildColumnPanel() {
    var box = document.getElementById("col-panel");
    if (!box) return;
    box.innerHTML = "";
    COLS.forEach(function (c, i) {
      var lb = document.createElement("label");
      lb.innerHTML = '<input type="checkbox" ' + (hiddenCols[i] ? "" : "checked") + '><span></span>';
      lb.querySelector("span").textContent = c.title;
      lb.firstChild.addEventListener("change", function () {
        if (this.checked) delete hiddenCols[i]; else hiddenCols[i] = true;
        if (this.checked) sheet.showColumn(i); else sheet.hideColumn(i);
        saveLayout();
      });
      box.appendChild(lb);
    });
  }
  function applyHiddenColumns() {
    Object.keys(hiddenCols).forEach(function (i) { sheet.hideColumn(parseInt(i, 10)); });
  }

  var LAYOUT_KEY = "bc-fu-layout";
  function saveLayout() {
    try {
      localStorage.setItem(LAYOUT_KEY, JSON.stringify({ hidden: Object.keys(hiddenCols) }));
    } catch (e) { /* che do rieng tu */ }
  }
  function loadLayout() {
    try {
      var v = JSON.parse(localStorage.getItem(LAYOUT_KEY) || "{}");
      (v.hidden || []).forEach(function (i) { hiddenCols[i] = true; });
    } catch (e) { /* bo qua */ }
  }

  // ------------------------------------------------------------ tai du lieu
  var pageInfo = null;
  function query() { return new URLSearchParams(location.search).toString(); }

  function load() {
    setState("Đang tải...", "busy");
    return BC.api("/api/followup/rows?" + query()).then(function (d) {
      ALL = d.rows;
      rowsByCode = {};
      ALL.forEach(function (r) { rowsByCode[r.code] = r; });
      lastSync = d.now || lastSync;
      pageInfo = null;
      if (d.pages > 1) {
        pageInfo = document.createElement("span");
        pageInfo.className = "pager";
        pageInfo.appendChild(document.createTextNode(" · trang " + d.page + "/" + d.pages + " "));
        [[d.page - 1, "‹ Trước"], [d.page + 1, "Sau ›"]].forEach(function (pg) {
          if (pg[0] < 1 || pg[0] > d.pages) return;
          var q = new URLSearchParams(location.search);
          q.set("page", pg[0]);
          var a = document.createElement("a");
          a.href = "?" + q.toString();
          a.textContent = pg[1];
          pageInfo.appendChild(a);
        });
      }
      applyColumnFilters();
      buildColumnPanel();
      setState("Sẵn sàng");
    }).catch(function (e) { setState("Lỗi tải: " + e.message, "err"); });
  }

  function build(data) {
    if (sheet) { jspreadsheet.destroy(el); el.innerHTML = ""; }
    var columns = COLS.map(function (c) {
      var col = {
        title: c.title, width: c.width, readOnly: !c.editable,
        type: c.type === "date" ? "calendar" : (c.type === "text" ? "text" : "numeric"),
        align: c.type === "number" || c.type === "int" ? "right" : "left"
      };
      if (c.type === "date") col.options = { format: "DD/MM/YYYY" };
      return col;
    }).concat(SYS.map(function (s) { return { title: s.title, width: s.width, readOnly: true, type: "text" }; }))
      .concat([{ title: "code", type: "hidden" }]);

    var top = el.getBoundingClientRect().top + window.scrollY;
    var height = Math.max(340, window.innerHeight - top - 54);
    sheet = jspreadsheet(el, {
      data: data.length ? data : [],
      minDimensions: [columns.length, 0],
      columns: columns,
      freezeColumns: CFG.freeze || 0,
      tableOverflow: true,
      tableWidth: "100%",
      tableHeight: height + "px",
      lazyLoading: data.length > 200,
      loadingSpin: true,
      columnSorting: true,
      search: false,
      filters: false,           // dung bo loc rieng o tieu de (co o tim + dem so dong)
      parseFormulas: false,
      stripHTML: true,
      autoIncrement: false,
      wordWrap: false,
      allowInsertRow: false, allowManualInsertRow: false, allowDeleteRow: false,
      allowInsertColumn: false, allowManualInsertColumn: false, allowDeleteColumn: false,
      allowRenameColumn: false, columnDrag: false, rowDrag: false, allowExport: false,
      text: { noRecordsFound: "Không có dòng nào" },
      contextMenu: contextMenu,
      onafterchanges: function (instance, records) {
        if (suppress) return;
        var rowsTouched = {};
        records.forEach(function (rec) {
          var x = parseInt(rec.x, 10), y = parseInt(rec.y, 10);
          if (x >= N) return;
          var code = codeAt(y);
          (dirty[code] = dirty[code] || {})[COLS[x].field] = true;
          if (COLS[x].formula) touchedFormula[code + "|" + COLS[x].field] = true;
          rowsTouched[y] = true;
        });
        Object.keys(rowsTouched).forEach(function (y) { recomputeRow(parseInt(y, 10)); });
        schedule();
      },
      onselection: function (inst, x1, y1, x2, y2) {
        selection = { x1: x1, y1: y1, x2: x2, y2: y2 };
        updateFormulaBar();
        updateStats();
      },
      onundo: function () { if (!suppress) { diffAll(); schedule(); } },
      onredo: function () { if (!suppress) { diffAll(); schedule(); } },
      onsort: function () { paintAll(); }
    });
    applyHiddenColumns();
    addFilterButtons();
    paintAll();
  }

  function addFilterButtons() {
    if (!sheet.headers) return;
    for (var x = 0; x < N; x++) {
      (function (idx) {
        var th = sheet.headers[idx];
        if (!th || th.querySelector(".fu-fbtn")) return;
        var b = document.createElement("span");
        b.className = "fu-fbtn";
        b.title = "Lọc / sắp xếp cột " + COLS[idx].title;
        b.textContent = "▾";
        b.addEventListener("mousedown", function (e) { e.stopPropagation(); });
        b.addEventListener("click", function (e) {
          e.stopPropagation();
          e.preventDefault();
          openFilter(idx, b);
        });
        th.appendChild(b);
      })(x);
    }
    updateFilterMarks();
  }

  // So sanh toan bo luoi voi du lieu server (dung sau hoan tac / lam lai)
  function diffAll() {
    var d = sheet.options.data;
    for (var y = 0; y < d.length; y++) {
      var code = d[y][CODE_COL];
      var r = rowsByCode[code];
      if (!r) continue;
      for (var x = 0; x < N; x++) {
        if (!COLS[x].editable || (COLS[x].formula && !touchedFormula[code + "|" + COLS[x].field])) continue;
        if (norm(COLS[x], d[y][x]) !== norm(COLS[x], display(COLS[x], r.cells[x]))) {
          (dirty[code] = dirty[code] || {})[COLS[x].field] = true;
        }
      }
    }
  }

  // ---------------------------------------------------- thanh cong thuc + thong ke
  function cellLabel(x, y) {
    return window.FX.indexLetter(x + 1) + (y + 1);
  }
  function updateFormulaBar() {
    if (!fxInput || !selection) return;
    var x = selection.x1, y = selection.y1;
    fxRef.textContent = x < N ? cellLabel(x, y) : "—";
    var code = codeAt(y);
    var r = rowsByCode[code];
    if (x >= N || !r) {
      fxInput.value = "";
      fxInput.disabled = true;
      fxInput.classList.remove("has-fx");
      return;
    }
    var field = COLS[x].field;
    var f = (r.fx || {})[field];
    fxInput.disabled = !COLS[x].editable;
    fxInput.classList.toggle("has-fx", !!f);
    fxInput.value = f ? "=" + f.src : String(sheet.options.data[y][x] === null ? "" : sheet.options.data[y][x]);
  }
  function commitFormulaBar() {
    if (!selection || !fxInput || fxInput.disabled) return;
    var x = selection.x1, y = selection.y1;
    if (x >= N) return;
    var raw = String(fxInput.value == null ? "" : fxInput.value);
    if (raw.trim().charAt(0) === "=") {
      // KHONG day chuoi cong thuc qua luoi: Jspreadsheet viet hoa moi thu sau dau '='
      // ([FinalCBM] -> [FINALCBM]). Gui thang len may chu, may chu tra ve gia tri da tinh.
      var code = codeAt(y);
      if (!code) return;
      pendingFx[code + "|" + COLS[x].field] = raw.trim();
      (dirty[code] = dirty[code] || {})[COLS[x].field] = true;
      setState("Đang tính công thức...", "busy");
      schedule();
      return;
    }
    sheet.setValueFromCoords(x, y, raw, false);
  }
  function updateStats() {
    if (!statsEl || !selection) return;
    var vals = [];
    for (var y = Math.min(selection.y1, selection.y2); y <= Math.max(selection.y1, selection.y2); y++) {
      for (var x = Math.min(selection.x1, selection.x2); x <= Math.max(selection.x1, selection.x2); x++) {
        if (x >= N) continue;
        var v = sheet.options.data[y] ? sheet.options.data[y][x] : null;
        if (v !== null && v !== undefined && v !== "") vals.push(v);
      }
    }
    var nums = vals.map(window.FX.toNum).filter(function (n) { return n !== null; });
    var parts = ["Đã chọn: " + vals.length];
    if (nums.length) {
      var sum = nums.reduce(function (a, b) { return a + b; }, 0);
      parts.push("Tổng: " + fmt(sum));
      parts.push("TB: " + fmt(sum / nums.length));
      parts.push("Nhỏ nhất: " + fmt(Math.min.apply(null, nums)));
      parts.push("Lớn nhất: " + fmt(Math.max.apply(null, nums)));
    }
    statsEl.textContent = parts.join("  ·  ");
  }
  function fmt(n) {
    var r = Math.round(n * 1e6) / 1e6;
    return r.toLocaleString("vi-VN", { maximumFractionDigits: 6 });
  }

  // ------------------------------------------------------------------- luu
  function schedule() {
    var n = Object.keys(dirty).length;
    if (!n) return;
    setState("Chưa lưu (" + n + " dòng)...", "busy");
    clearTimeout(timer);
    timer = setTimeout(save, 600);
  }

  function save() {
    if (saving) { timer = setTimeout(save, 400); return; }
    var codes = Object.keys(dirty);
    if (!codes.length) return;
    var changes = codes.map(function (code) {
      var y = yOf(code);
      var values = {};
      Object.keys(dirty[code]).forEach(function (field) {
        var x = FIELD_IDX[field];
        var fxRaw = pendingFx[code + "|" + field];
        values[field] = fxRaw !== undefined ? fxRaw
          : (y >= 0 ? sheet.options.data[y][x] : null);
      });
      return { code: code, v: (rowsByCode[code] || {}).v, values: values };
    });
    dirty = {};
    saving = true;
    setState("Đang lưu " + changes.length + " dòng...", "busy");
    BC.api("/api/followup/save", { changes: changes }).then(function (res) {
      (res.rows || []).forEach(applyServerRow);
      var errs = [];
      Object.keys(res.errors || {}).forEach(function (code) {
        var r = rowsByCode[code] || {};
        var jo = r.cells ? r.cells[FIELD_IDX.JO] : code;
        Object.keys(res.errors[code]).forEach(function (f) {
          var label = FIELD_IDX[f] !== undefined ? COLS[FIELD_IDX[f]].title : "";
          errs.push((jo || code) + (label ? " / " + label : "") + ": " + res.errors[code][f]);
        });
      });
      if (res.conflicts && res.conflicts.length) {
        errs.unshift(res.conflicts.length + " dòng vừa được người khác cập nhật — đã tải lại dòng đó, hãy nhập lại nếu cần.");
      }
      showMsg(errs.slice(0, 6).join("  •  "), "err");
      setState(errs.length ? "Đã lưu (có " + errs.length + " lỗi)" : "Đã lưu ✓", errs.length ? "err" : "ok");
      updateFormulaBar();
    }).catch(function (e) {
      setState("Lỗi lưu: " + e.message, "err");
      showMsg("Không lưu được: " + e.message + ". Dữ liệu chưa lưu vẫn còn trên màn hình — bấm Tải lại để đồng bộ.", "err");
    }).then(function () { saving = false; });
  }

  function applyServerRow(r, flash) {
    rowsByCode[r.code] = r;
    Object.keys(pendingFx).forEach(function (k) {
      if (k.indexOf(r.code + "|") === 0) delete pendingFx[k];
    });
    for (var i = 0; i < ALL.length; i++) if (ALL[i].code === r.code) { ALL[i] = r; break; }
    Object.keys(touchedFormula).forEach(function (k) { if (k.indexOf(r.code + "|") === 0) delete touchedFormula[k]; });
    var y = yOf(r.code);
    if (y < 0) return;
    var want = toGridRow(r);
    suppress = true;
    try {
      for (var x = 0; x < want.length - 1; x++) {
        var col = COLS[x] || { type: "text" };
        if (norm(col, sheet.options.data[y][x]) !== norm(col, want[x])) sheet.setValueFromCoords(x, y, want[x], true);
      }
    } finally { suppress = false; }
    paintRow(y);
    if (flash && sheet.rows[y]) {
      var tr = sheet.rows[y];
      tr.classList.remove("just-synced");
      void tr.offsetWidth;
      tr.classList.add("just-synced");
    }
  }

  // ------------------------------------- dung chung: keo thay doi + ai dang sua
  function pullChanges() {
    if (!lastSync || saving) return;
    var q = new URLSearchParams(location.search);
    q.set("since", lastSync);
    BC.api("/api/followup/changes?" + q.toString()).then(function (d) {
      lastSync = d.now || lastSync;
      var n = 0;
      (d.rows || []).forEach(function (r) {
        if (dirty[r.code]) return;                 // dang go do - khong de bi ghi de
        if (!rowsByCode[r.code]) return;           // dong moi: doi lan Tai lai
        applyServerRow(r, true);
        n++;
      });
      if (n) {
        var who = (d.by || []).map(function (b) { return USER_LABEL[b] || b; }).join(", ");
        showMsg(n + " dòng vừa được cập nhật" + (who ? " bởi " + who : "") + " — lưới đã tự đồng bộ.", "ok");
        setTimeout(function () { showMsg(""); }, 6000);
      }
    }).catch(function () { /* mat mang tam thoi */ });
  }

  function pingPresence() {
    var editing = "";
    if (selection && Object.keys(dirty).length) editing = codeAt(selection.y1) || "";
    else if (selection) editing = codeAt(selection.y1) || "";
    BC.api("/api/followup/presence", { codes: view.slice(0, 3000), editing: editing })
      .then(function (d) {
        editors = d.editors || {};
        if (peersEl) {
          var k = Object.keys(editors).length;
          peersEl.textContent = d.viewers
            ? d.viewers + " người khác đang mở trang này" + (k ? " · " + k + " dòng đang được sửa" : "")
            : "";
        }
        paintAll();
      }).catch(function () { /* bo qua */ });
  }

  // ------------------------------------------------------------- menu chuot phai
  function selectedCodes() {
    var ys = sheet.getSelectedRows(true);
    if (!ys.length && sheet.selectedCell) {
      var a = parseInt(sheet.selectedCell[1], 10), b = parseInt(sheet.selectedCell[3], 10);
      for (var y = Math.min(a, b); y <= Math.max(a, b); y++) ys.push(y);
    }
    return ys.map(codeAt).filter(Boolean);
  }
  function contextMenu(obj, x, y) {
    var items = [];
    if (y !== null && y !== undefined) {
      var code = codeAt(parseInt(y, 10));
      items.push({ title: "Mở chi tiết lô hàng (Why?)", onclick: function () { window.open("/shipments/" + code, "_blank"); } });
      items.push({ type: "line" });
    }
    items.push({ title: "Sao chép", shortcut: "Ctrl + C", onclick: function () { obj.copy(true); } });
    items.push({ title: "Điền xuống", shortcut: "Ctrl + D", onclick: fillDown });
    items.push({ title: "Điền sang phải", shortcut: "Ctrl + R", onclick: fillRight });
    items.push({ title: "Hoàn tác", shortcut: "Ctrl + Z", onclick: function () { obj.undo(); } });
    items.push({ title: "Làm lại", shortcut: "Ctrl + Y", onclick: function () { obj.redo(); } });
    if (x !== null && x !== undefined) {
      items.push({ type: "line" });
      items.push({ title: "Lọc / sắp xếp cột này", onclick: function () {
        openFilter(parseInt(x, 10), sheet.headers[parseInt(x, 10)]);
      } });
      items.push({ title: "Ẩn cột này", onclick: function () {
        var idx = parseInt(x, 10);
        hiddenCols[idx] = true; sheet.hideColumn(idx); buildColumnPanel(); saveLayout();
      } });
    }
    return items;
  }

  // ---------------------------------------------------------- dien xuong / sang
  function fillDown() { fill(false); }
  function fillRight() { fill(true); }
  function fill(toRight) {
    if (!selection) return;
    var x1 = Math.min(selection.x1, selection.x2), x2 = Math.max(selection.x1, selection.x2);
    var y1 = Math.min(selection.y1, selection.y2), y2 = Math.max(selection.y1, selection.y2);
    var changes = [];
    if (toRight) {
      for (var y = y1; y <= y2; y++) {
        var src = sheet.options.data[y][x1];
        for (var x = x1 + 1; x <= x2 && x < N; x++) {
          if (COLS[x].editable) changes.push([x, y, src]);
        }
      }
    } else {
      for (var xx = x1; xx <= x2 && xx < N; xx++) {
        if (!COLS[xx].editable) continue;
        var top = sheet.options.data[y1][xx];
        for (var yy = y1 + 1; yy <= y2; yy++) changes.push([xx, yy, top]);
      }
    }
    if (!changes.length) return;
    changes.forEach(function (c) { sheet.setValueFromCoords(c[0], c[1], c[2], false); });
  }

  // --------------------------------------------------------------- tim / thay the
  function findReplace(replaceMode) {
    var dlg = document.getElementById("dlg-find");
    dlg.querySelector(".rep-row").hidden = !replaceMode;
    dlg.showModal();
    dlg.querySelector('[name="find"]').focus();
  }
  function runFind(term, replaceWith, all) {
    if (!term) return 0;
    var t = term.toLowerCase();
    var hits = 0;
    var d = sheet.options.data;
    for (var y = 0; y < d.length; y++) {
      for (var x = 0; x < N; x++) {
        var v = d[y][x];
        if (v === null || v === undefined) continue;
        var s = String(v);
        if (s.toLowerCase().indexOf(t) < 0) continue;
        hits++;
        if (replaceWith === null) {
          if (hits === 1) {
            sheet.updateSelectionFromCoords(x, y, x, y);
            sheet.scrollTo && sheet.scrollTo(x, y);
            return 1;
          }
        } else if (COLS[x].editable) {
          var out = s.split(new RegExp(term.replace(/[.*+?^${}()|[\]\\]/g, "\\$&"), "gi")).join(replaceWith);
          sheet.setValueFromCoords(x, y, out, false);
          if (!all) return 1;
        }
      }
    }
    return hits;
  }

  // ------------------------------------------------------------------ nut bam
  function on(id, fn) { var b = document.getElementById(id); if (b) b.addEventListener("click", fn); }

  on("btn-reload", function () { dirty = {}; showMsg(""); load(); });
  on("btn-clear-filters", function () { colFilter = {}; applyColumnFilters(); });
  on("btn-fill-down", fillDown);
  on("btn-find", function () { findReplace(false); });
  on("btn-replace", function () { findReplace(true); });
  on("btn-cols", function () {
    var p = document.getElementById("col-panel-wrap");
    p.hidden = !p.hidden;
  });
  on("btn-show-all-cols", function () {
    Object.keys(hiddenCols).forEach(function (i) { sheet.showColumn(parseInt(i, 10)); });
    hiddenCols = {};
    buildColumnPanel();
    saveLayout();
  });
  on("btn-density", function () {
    document.getElementById("grid").classList.toggle("compact");
    this.textContent = document.getElementById("grid").classList.contains("compact")
      ? "Dòng thưa" : "Dòng sát";
  });

  on("btn-assign", function () {
    var to = document.getElementById("assign-user").value;
    var kind = document.getElementById("assign-kind").value;
    var codes = selectedCodes();
    if (!to) { showMsg("Chọn người cần giao trước."); return; }
    if (!codes.length) { showMsg("Chọn các dòng cần giao (bấm số dòng bên trái, giữ Shift để chọn nhiều)."); return; }
    setState("Đang giao " + codes.length + " lô...", "busy");
    BC.api("/api/followup/assign", { codes: codes, to_user: to === "__unassign__" ? "" : to, kind: kind })
      .then(function (res) {
        (res.rows || []).forEach(function (r) { applyServerRow(r); });
        showMsg("");
        setState("Đã giao " + res.changed + " lô ✓", "ok");
      }).catch(function (e) { setState("Lỗi: " + e.message, "err"); });
  });

  on("btn-archive", function () {
    var codes = selectedCodes();
    if (!codes.length) { showMsg("Chọn các dòng cần lưu trữ."); return; }
    if (!window.confirm("Lưu trữ " + codes.length + " lô? Lô lưu trữ ẩn khỏi lưới nhưng KHÔNG bị xóa.")) return;
    BC.api("/api/followup/archive", { codes: codes, archived: true }).then(function () { load(); });
  });

  var exp = document.getElementById("btn-export-filter");
  if (exp) exp.addEventListener("click", function (e) {
    e.preventDefault();
    var p = new URLSearchParams(location.search);
    p.set("scope", "filter");
    location.href = "/export/followup.xlsx?" + p.toString();
  });

  var dlg = document.getElementById("dlg-add");
  on("btn-add", function () { document.getElementById("add-error").hidden = true; dlg.showModal(); });
  on("add-cancel", function () { dlg.close(); });
  var form = document.getElementById("form-add");
  if (form) form.addEventListener("submit", function (e) {
    e.preventDefault();
    var fd = new FormData(form);
    var values = {}, assignTo = null;
    fd.forEach(function (v, k) { if (k === "assign_to") assignTo = v || null; else if (v !== "") values[k] = v; });
    BC.api("/api/followup/create", { values: values, assign_to: assignTo }).then(function (res) {
      dlg.close(); form.reset();
      rowsByCode[res.row.code] = res.row;
      ALL.unshift(res.row);
      applyColumnFilters();
      setState("Đã thêm " + (res.row.cells[FIELD_IDX.JO] || "") + " ✓", "ok");
    }).catch(function (err) {
      var box = document.getElementById("add-error");
      box.textContent = err.message; box.hidden = false;
    });
  });

  var findDlg = document.getElementById("dlg-find");
  if (findDlg) {
    findDlg.querySelector(".do-find").addEventListener("click", function () {
      var term = findDlg.querySelector('[name="find"]').value;
      var n = runFind(term, null, false);
      findDlg.querySelector(".res").textContent = n ? "Đã tới ô đầu tiên khớp." : "Không tìm thấy.";
    });
    findDlg.querySelector(".do-replace").addEventListener("click", function () {
      var term = findDlg.querySelector('[name="find"]').value;
      var rep = findDlg.querySelector('[name="rep"]').value;
      var n = runFind(term, rep, true);
      findDlg.querySelector(".res").textContent = "Đã thay " + n + " ô.";
    });
    findDlg.querySelector(".do-close").addEventListener("click", function () { findDlg.close(); });
  }

  if (fxInput) {
    fxInput.addEventListener("keydown", function (e) {
      if (e.key === "Enter") { e.preventDefault(); commitFormulaBar(); }
      if (e.key === "Escape") updateFormulaBar();
    });
    fxInput.addEventListener("blur", function () {
      if (selection) commitFormulaBar();
    });
  }

  document.addEventListener("keydown", function (e) {
    if (!sheet) return;
    var inField = /^(INPUT|TEXTAREA|SELECT)$/.test((e.target.tagName || "").toUpperCase());
    if ((e.ctrlKey || e.metaKey) && !e.shiftKey && !inField) {
      if (e.key === "d" || e.key === "D") { e.preventDefault(); fillDown(); }
      else if (e.key === "r" || e.key === "R") { e.preventDefault(); fillRight(); }
    }
    if ((e.ctrlKey || e.metaKey) && (e.key === "f" || e.key === "F")) { e.preventDefault(); findReplace(false); }
    if ((e.ctrlKey || e.metaKey) && (e.key === "h" || e.key === "H")) { e.preventDefault(); findReplace(true); }
  });

  window.addEventListener("beforeunload", function (e) {
    if (Object.keys(dirty).length || saving) { e.preventDefault(); e.returnValue = ""; }
  });

  loadLayout();
  injectColorCss();
  load().then(function () {
    setInterval(pullChanges, 7000);
    setInterval(pingPresence, 11000);
    pingPresence();
  });
})();
