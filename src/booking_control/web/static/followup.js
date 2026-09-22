/* Luoi Follow Up 47 cot - Jspreadsheet CE (MIT), chay offline.
 *
 * Nguyen tac:
 *  - Cot / quyen sua / cong thuc lay tu server (config/followup_template.yaml) -> khong hard-code.
 *  - Moi thay doi (go, dan tu Excel, keo dien, hoan tac) gom lai 0.6s roi gui 1 lan.
 *  - Server kiem quyen tung o, tinh lai cong thuc + trang thai, tra ve dong chuan -> cap nhat luoi.
 *  - Optimistic lock: gui kem version cua dong; nguoi khac vua sua -> server tra dong moi, khong ghi de.
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
  var USER_LABEL = {};
  (CFG.users || []).forEach(function (u) { USER_LABEL[u.name] = u.label; });

  var el = document.getElementById("grid");
  var stateEl = document.getElementById("save-state");
  var msgEl = document.getElementById("grid-msg");
  var countEl = document.getElementById("row-count");

  var sheet = null;
  var rowsByCode = {};     // code -> dong server (cells, v, status, manual...)
  var suppress = false;    // dang tu cap nhat luoi -> khong coi la User sua
  var dirty = {};          // code -> {field: true}
  var touchedFormula = {}; // code|field -> User go truc tiep vao o cong thuc
  var timer = null;
  var saving = false;

  // ------------------------------------------------------------------ tien ich
  function setState(text, cls) {
    stateEl.textContent = text;
    stateEl.className = "save-state " + (cls || "");
  }
  function showMsg(text) {
    if (!text) { msgEl.hidden = true; return; }
    msgEl.textContent = text; msgEl.hidden = false;
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
  function num(v) {
    if (v === null || v === undefined || v === "") return null;
    var n = parseFloat(String(v).replace(/,/g, ""));
    return isNaN(n) ? null : n;
  }
  function codeAt(y) { return sheet.options.data[y][CODE_COL]; }
  function yOf(code) {
    var d = sheet.options.data;
    for (var y = 0; y < d.length; y++) if (d[y][CODE_COL] === code) return y;
    return -1;
  }
  function sysValues(r) {
    return [USER_LABEL[r.assigned_to] || r.assigned_to || "—", r.status || "", r.deadline || ""];
  }
  function toGridRow(r) {
    var out = r.cells.map(function (v, i) { return display(COLS[i], v); });
    return out.concat(sysValues(r), [r.code]);
  }

  // ------------------------------------------------------------ cong thuc (JS)
  var F_HELPERS = {
    A: function (a, b) { return a === null || b === null ? null : a + b; },
    S: function (a, b) { return a === null || b === null ? null : a - b; },
    M: function (a, b) { return a === null || b === null ? null : a * b; },
    D: function (a, b) { return a === null || b === null || b === 0 ? null : a / b; },
    R: function (a, n) {
      if (a === null || n === null) return null;
      var p = Math.pow(10, n);
      return Math.sign(a) * Math.round(Math.abs(a) * p + 1e-9) / p; // ROUND cua Excel (0.5 ra xa 0)
    }
  };
  var compiled = {};
  COLS.forEach(function (c) {
    if (c.formula && c.formula.kind === "expr") {
      // Bieu thuc da duoc server kiem tra (chi so, + - * /, ROUND, ten cot) truoc khi gui xuong
      compiled[c.field] = new Function("F", "A", "S", "M", "D", "R", "return " + c.formula.code + ";");
    }
  });
  function evalFormula(col, get) {
    var f = col.formula;
    if (f.kind === "invoice_suffix") {
      var inv = String(get(f.source) || "").trim();
      if (!inv || inv.toUpperCase().indexOf(f.prefix.toUpperCase()) !== 0) return "";
      var tail = inv.slice(-f.last_digits);
      return /^\d+$/.test(tail) ? parseInt(tail, 10) + f.offset : "";
    }
    var v = compiled[col.field](function (name) { return num(get(name)); },
      F_HELPERS.A, F_HELPERS.S, F_HELPERS.M, F_HELPERS.D, F_HELPERS.R);
    if (v === null || !isFinite(v)) return "";
    return Math.round(v * 1e6) / 1e6;
  }
  // Cot cong thuc "song" = gia tri dang dung bang ket qua cong thuc (hoac dang trong).
  // Dong Excel go tay so NO (khong dung cong thuc) thi giu nguyen - giong logic o server.
  function liveFormulas(r) {
    var live = {};
    var get = function (field) { var i = FIELD_IDX[field]; return i === undefined ? null : r.cells[i]; };
    (CFG.formula_order || []).forEach(function (field) {
      var col = COLS[FIELD_IDX[field]];
      var cur = r.cells[FIELD_IDX[field]];
      if (r.origin === "WEB" || cur === null || cur === "" || norm(col, cur) === norm(col, evalFormula(col, get))) live[field] = true;
    });
    return live;
  }
  function recomputeRow(y) {
    var code = codeAt(y);
    var r = rowsByCode[code] || { cells: [], manual: [] };
    var manual = r.manual || [];
    var live = liveFormulas(r);
    var row = sheet.options.data[y];
    var get = function (field) { var i = FIELD_IDX[field]; return i === undefined ? null : row[i]; };
    suppress = true;
    try {
      (CFG.formula_order || []).forEach(function (field) {
        var i = FIELD_IDX[field];
        if (!live[field] || manual.indexOf(field) >= 0 || touchedFormula[code + "|" + field]) return;
        var v = evalFormula(COLS[i], get);
        if (norm(COLS[i], row[i]) !== norm(COLS[i], v)) sheet.setValueFromCoords(i, y, v, true);
      });
    } finally { suppress = false; }
  }

  // --------------------------------------------------------------- to mau dong
  function tint(hex, strength) {
    // Mau nen DAC (khong trong suot) - cot co dinh (freeze) de len cot khac khi cuon ngang
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
    if (!r) return;
    var tr = sheet.rows[y];
    tr.setAttribute("data-st", r.status || "");
    tr.title = r.reason || "";
    var rec = sheet.records[y];
    var manual = r.manual || [];
    for (var x = 0; x < N; x++) {
      var td = rec[x];
      if (!td) continue;
      td.classList.toggle("fx", !!COLS[x].formula && manual.indexOf(COLS[x].field) < 0);
      td.classList.toggle("manual", manual.indexOf(COLS[x].field) >= 0);
      td.classList.toggle("ro", !COLS[x].editable);
      td.classList.toggle("req", COLS[x].required && (sheet.options.data[y][x] === "" || sheet.options.data[y][x] === null));
    }
  }
  function paintAll() { for (var y = 0; y < sheet.rows.length; y++) paintRow(y); }

  // ------------------------------------------------------------ tai du lieu
  function query() {
    var p = new URLSearchParams(location.search);
    return p.toString();
  }
  function load() {
    setState("Đang tải...", "busy");
    return BC.api("/api/followup/rows?" + query()).then(function (d) {
      rowsByCode = {};
      d.rows.forEach(function (r) { rowsByCode[r.code] = r; });
      build(d.rows.map(toGridRow));
      countEl.textContent = d.rows.length + " dòng" + (d.truncated ? " (đã giới hạn " + d.limit + " — hãy lọc bớt)" : "");
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
    var height = Math.max(320, window.innerHeight - top - 40);
    sheet = jspreadsheet(el, {
      data: data.length ? data : [],
      minDimensions: [columns.length, 0],
      columns: columns,
      freezeColumns: CFG.freeze || 0,
      tableOverflow: true,
      tableWidth: "100%",
      tableHeight: height + "px",
      lazyLoading: data.length > 150,
      loadingSpin: true,
      columnSorting: true,
      search: true,
      filters: true,
      parseFormulas: false,
      stripHTML: true,
      autoIncrement: false,
      wordWrap: false,
      allowInsertRow: false, allowManualInsertRow: false, allowDeleteRow: false,
      allowInsertColumn: false, allowManualInsertColumn: false, allowDeleteColumn: false,
      allowRenameColumn: false, columnDrag: false, rowDrag: false, allowExport: false,
      text: { search: "Tìm", noRecordsFound: "Không có dòng nào", showingPage: "Trang {0}/{1}" },
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
      onundo: function () { if (!suppress) { diffAll(); schedule(); } },
      onredo: function () { if (!suppress) { diffAll(); schedule(); } },
      onsort: function () { paintAll(); }
    });
    paintAll();
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
        values[field] = y >= 0 ? sheet.options.data[y][x] : null;
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
      showMsg(errs.slice(0, 6).join("  •  "));
      setState(errs.length ? "Đã lưu (có " + errs.length + " lỗi)" : "Đã lưu ✓", errs.length ? "err" : "ok");
    }).catch(function (e) {
      setState("Lỗi lưu: " + e.message, "err");
      showMsg("Không lưu được: " + e.message + ". Dữ liệu chưa lưu vẫn còn trên màn hình — bấm Tải lại để đồng bộ.");
    }).then(function () { saving = false; });
  }

  function applyServerRow(r) {
    rowsByCode[r.code] = r;
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
    items.push({ title: "Hoàn tác", shortcut: "Ctrl + Z", onclick: function () { obj.undo(); } });
    items.push({ title: "Làm lại", shortcut: "Ctrl + Y", onclick: function () { obj.redo(); } });
    if (x !== null && x !== undefined) {
      items.push({ type: "line" });
      items.push({ title: "Sắp xếp tăng dần", onclick: function () { obj.orderBy(parseInt(x, 10), 0); } });
      items.push({ title: "Sắp xếp giảm dần", onclick: function () { obj.orderBy(parseInt(x, 10), 1); } });
    }
    return items;
  }

  // ------------------------------------------------------------------ nut bam
  function on(id, fn) { var b = document.getElementById(id); if (b) b.addEventListener("click", fn); }

  on("btn-reload", function () { dirty = {}; showMsg(""); load(); });

  on("btn-assign", function () {
    var to = document.getElementById("assign-user").value;
    var kind = document.getElementById("assign-kind").value;
    var codes = selectedCodes();
    if (!to) { showMsg("Chọn người cần giao trước."); return; }
    if (!codes.length) { showMsg("Chọn các dòng cần giao (bấm số dòng bên trái, giữ Shift để chọn nhiều)."); return; }
    setState("Đang giao " + codes.length + " lô...", "busy");
    BC.api("/api/followup/assign", { codes: codes, to_user: to === "__unassign__" ? "" : to, kind: kind })
      .then(function (res) {
        (res.rows || []).forEach(applyServerRow);
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
      sheet.insertRow(toGridRow(res.row), 0, true);
      paintRow(0);
      setState("Đã thêm " + (res.row.cells[FIELD_IDX.JO] || "") + " ✓", "ok");
    }).catch(function (err) {
      var box = document.getElementById("add-error");
      box.textContent = err.message; box.hidden = false;
    });
  });

  window.addEventListener("beforeunload", function (e) {
    if (Object.keys(dirty).length || saving) { e.preventDefault(); e.returnValue = ""; }
  });

  injectColorCss();
  load();
})();
