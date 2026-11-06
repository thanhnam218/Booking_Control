/* Bo cong thuc kieu Excel - ban JavaScript.
 *
 * Doc CUNG MOT cay AST ma server gui xuong (xem src/booking_control/followup/fx.py).
 * Khong eval(), khong new Function(): chi di tren cay va tinh, nen trinh duyet va may chu
 * luon cho cung ket qua, va cong thuc nguoi dung go khong the chay ma la.
 */
(function (global) {
  "use strict";

  var EPOCH = Date.UTC(1899, 11, 30);       // goc ngay thang cua Excel
  var DAY = 86400000;
  var DATE_RE = /^\d{4}-\d{2}-\d{2}/;

  function isDateStr(v) { return typeof v === "string" && DATE_RE.test(v.trim()); }

  function dateSerial(s) {
    var p = s.trim().slice(0, 10).split("-");
    return (Date.UTC(+p[0], +p[1] - 1, +p[2]) - EPOCH) / DAY;
  }

  function serialToStr(n) {
    var d = new Date(EPOCH + Math.round(n) * DAY);
    var m = String(d.getUTCMonth() + 1).padStart(2, "0");
    var day = String(d.getUTCDate()).padStart(2, "0");
    return d.getUTCFullYear() + "-" + m + "-" + day;
  }

  function toNum(v) {
    if (v === null || v === undefined || v === "") return null;
    if (typeof v === "boolean") return v ? 1 : 0;
    if (typeof v === "number") return isFinite(v) ? v : null;
    var s = String(v).trim();
    if (!s) return null;
    if (isDateStr(s)) return dateSerial(s);
    var n = parseFloat(s.replace(/,/g, ""));
    return isNaN(n) || !/^[-+]?[\d.,]+([eE][-+]?\d+)?$/.test(s.replace(/,/g, "")) ? null : n;
  }

  function toText(v) {
    if (v === null || v === undefined) return "";
    if (typeof v === "boolean") return v ? "TRUE" : "FALSE";
    if (typeof v === "number") {
      return Number.isInteger(v) ? String(v) : String(Math.round(v * 1e6) / 1e6);
    }
    return String(v);
  }

  function toBool(v) {
    if (typeof v === "boolean") return v;
    if (v === null || v === undefined || v === "") return false;
    var n = toNum(v);
    return n !== null ? !!n : !!String(v).trim();
  }

  /* ROUND cua Excel: 0.5 lam tron RA XA so 0.
   *
   * Lam tron tren CHUOI thap phan chu khong tren so nhi phan, de khop chinh xac voi
   * ban Python (dung Decimal(repr(x))). Neu tinh bang 2.345*100 thi may ra
   * 234.49999999999997 -> 2.34, con Excel va server deu cho 2.35.
   */
  function excelRound(x, n) {
    if (x === null || !isFinite(x)) return null;
    n = n | 0;
    var s = String(x);
    if (s.indexOf("e") >= 0 || s.indexOf("E") >= 0 || n < 0) {
      var p = Math.pow(10, n);
      var scaled = Math.abs(x) * p;
      var r = Math.floor(scaled + 0.5 + 1e-9);
      return (x < 0 ? -r : r) / p;
    }
    var neg = s.charAt(0) === "-";
    if (neg) s = s.slice(1);
    var dot = s.indexOf(".");
    if (dot < 0) return x;
    var frac = s.slice(dot + 1);
    if (frac.length <= n) return x;
    var keep = s.slice(0, dot) + frac.slice(0, n);
    var big = parseInt(keep, 10) + (frac.charCodeAt(n) - 48 >= 5 ? 1 : 0);
    var out = big / Math.pow(10, n);
    return neg ? -out : out;
  }

  function roundAway(x, digits, up) {
    var p = Math.pow(10, digits);
    var scaled = Math.abs(x) * p;
    var r = up ? Math.ceil(scaled - 1e-9) : Math.floor(scaled + 1e-9);
    return (x < 0 ? -r : r) / p;
  }

  function lettersBetween(a, b) {
    var ia = letterIndex(a), ib = letterIndex(b), out = [];
    if (ia > ib) { var t = ia; ia = ib; ib = t; }
    for (var i = ia; i <= ib; i++) out.push(indexLetter(i));
    return out;
  }
  function letterIndex(l) {
    var n = 0;
    l = String(l).toUpperCase();
    for (var i = 0; i < l.length; i++) n = n * 26 + (l.charCodeAt(i) - 64);
    return n;
  }
  function indexLetter(n) {
    var s = "";
    while (n > 0) { var r = (n - 1) % 26; s = String.fromCharCode(65 + r) + s; n = (n - 1 - r) / 26; }
    return s;
  }

  function flat(values) {
    var out = [];
    values.forEach(function (v) { if (Array.isArray(v)) out.push.apply(out, v); else out.push(v); });
    return out;
  }
  function nums(values) {
    var out = [];
    flat(values).forEach(function (v) { var n = toNum(v); if (n !== null) out.push(n); });
    return out;
  }

  /* ctx = { field: fn(name), letter: fn(letter), today: "YYYY-MM-DD" } */
  function evalNode(node, ctx) {
    switch (node.t) {
      case "num": case "str": case "bool": return node.v;
      case "field": return ctx.field(node.f);
      case "cell": return ctx.letter(node.c);
      case "range": return lettersBetween(node.a, node.b).map(ctx.letter);
      case "un": {
        var v = toNum(evalNode(node.x, ctx));
        if (v === null) return null;
        return node.o === "-" ? -v : v;
      }
      case "bin": return binary(node.o, evalNode(node.l, ctx), evalNode(node.r, ctx));
      case "call": return call(node.n, node.a, ctx);
      default: throw new Error("Cây công thức không hợp lệ");
    }
  }

  function binary(op, a, b) {
    if (op === "&") return toText(a) + toText(b);
    if (["=", "<>", "<", "<=", ">", ">="].indexOf(op) >= 0) {
      var na = toNum(a), nb = toNum(b), l, r;
      if (na !== null && nb !== null) { l = na; r = nb; }
      else { l = toText(a).toUpperCase(); r = toText(b).toUpperCase(); }
      switch (op) {
        case "=": return l === r;
        case "<>": return l !== r;
        case "<": return l < r;
        case "<=": return l <= r;
        case ">": return l > r;
        default: return l >= r;
      }
    }
    var x = toNum(a), y = toNum(b), out;
    if (x === null || y === null) return null;
    if (op === "+") out = x + y;
    else if (op === "-") out = x - y;
    else if (op === "*") out = x * y;
    else if (op === "/") { if (y === 0) return null; out = x / y; }
    else if (op === "^") { out = Math.pow(x, y); if (!isFinite(out)) return null; }
    else throw new Error("Phép tính không hỗ trợ: " + op);
    // Ngay +/- so  ->  van la ngay (giong Excel)
    if ((op === "+" || op === "-") && isDateStr(a) && !isDateStr(b)) return serialToStr(out);
    return out;
  }

  function call(name, argNodes, ctx) {
    if (name === "IF") {
      if (toBool(evalNode(argNodes[0], ctx))) return evalNode(argNodes[1], ctx);
      return argNodes.length > 2 ? evalNode(argNodes[2], ctx) : false;
    }
    if (name === "IFERROR") {
      var v;
      try { v = evalNode(argNodes[0], ctx); } catch (e) { return evalNode(argNodes[1], ctx); }
      return v === null || v === undefined ? evalNode(argNodes[1], ctx) : v;
    }
    var args = argNodes.map(function (a) { return evalNode(a, ctx); });
    var fl = flat(args);

    switch (name) {
      case "AND": return fl.every(toBool);
      case "OR": return fl.some(toBool);
      case "NOT": return !toBool(args[0]);
      case "SUM": case "MIN": case "MAX": case "AVERAGE": {
        var ns = nums(args);
        if (!ns.length) return name === "SUM" ? 0 : null;
        if (name === "SUM") return ns.reduce(function (a, b) { return a + b; }, 0);
        if (name === "MIN") return Math.min.apply(null, ns);
        if (name === "MAX") return Math.max.apply(null, ns);
        return ns.reduce(function (a, b) { return a + b; }, 0) / ns.length;
      }
      case "COUNT": return nums(args).length;
      case "COUNTA": return fl.filter(function (v) { return v !== null && v !== undefined && v !== ""; }).length;
      case "CONCAT": case "CONCATENATE": return fl.map(toText).join("");
    }

    var a0 = args.length ? toNum(args[0]) : null;
    switch (name) {
      case "ROUND": {
        var n0 = toNum(args[1]);
        return a0 === null || n0 === null ? null : excelRound(a0, n0 | 0);
      }
      case "ROUNDUP": case "ROUNDDOWN": {
        var n1 = toNum(args[1]);
        return a0 === null || n1 === null ? null : roundAway(a0, n1 | 0, name === "ROUNDUP");
      }
      case "INT": return a0 === null ? null : Math.floor(a0);
      case "ABS": return a0 === null ? null : Math.abs(a0);
      case "SQRT": return a0 === null || a0 < 0 ? null : Math.sqrt(a0);
      case "MOD": {
        var b1 = toNum(args[1]);
        return a0 === null || !b1 ? null : a0 - b1 * Math.floor(a0 / b1);
      }
      case "POWER": {
        var b2 = toNum(args[1]);
        if (a0 === null || b2 === null) return null;
        var pw = Math.pow(a0, b2);
        return isFinite(pw) ? pw : null;
      }
      case "CEILING": case "FLOOR": {
        var step = args.length > 1 ? toNum(args[1]) : 1;
        if (a0 === null || !step) return null;
        return name === "CEILING"
          ? Math.ceil(a0 / step - 1e-9) * step
          : Math.floor(a0 / step + 1e-9) * step;
      }
      case "LEN": return toText(args[0]).length;
      case "TRIM": return toText(args[0]).split(/\s+/).filter(Boolean).join(" ");
      case "UPPER": return toText(args[0]).toUpperCase();
      case "LOWER": return toText(args[0]).toLowerCase();
      case "LEFT": case "RIGHT": {
        var k = args.length > 1 ? (toNum(args[1]) | 0) : 1;
        var s = toText(args[0]);
        if (k <= 0) return "";
        return name === "LEFT" ? s.slice(0, k) : s.slice(-k);
      }
      case "MID": {
        var st = toNum(args[1]) | 0, ln = toNum(args[2]) | 0;
        var begin = Math.max(0, st - 1);
        return toText(args[0]).substr(begin, Math.max(0, ln));
      }
      case "VALUE": return toNum(args[0]);
      case "ISBLANK": return args[0] === null || args[0] === undefined || args[0] === "";
      case "ISNUMBER": return !isDateStr(args[0]) && toNum(args[0]) !== null;
      case "TODAY": return ctx.today;
      case "DATE": {
        var y = toNum(args[0]), mo = toNum(args[1]), d = toNum(args[2]);
        if (y === null || mo === null || d === null) return null;
        var dd = new Date(Date.UTC(y, mo - 1, d));
        return isNaN(dd.getTime()) ? null : serialToStr((dd.getTime() - EPOCH) / DAY);
      }
      case "YEAR": case "MONTH": case "DAY": {
        var sv = toNum(args[0]);
        if (sv === null) return null;
        var dt = new Date(EPOCH + Math.round(sv) * DAY);
        if (name === "YEAR") return dt.getUTCFullYear();
        return name === "MONTH" ? dt.getUTCMonth() + 1 : dt.getUTCDate();
      }
      case "DAYS": {
        var s1 = toNum(args[0]), s2 = toNum(args[1]);
        return s1 === null || s2 === null ? null : Math.round(s1 - s2);
      }
    }
    throw new Error("Chưa hỗ trợ hàm " + name + "()");
  }

  global.FX = {
    evalNode: evalNode, toNum: toNum, toText: toText, toBool: toBool,
    isDateStr: isDateStr, excelRound: excelRound,
    letterIndex: letterIndex, indexLetter: indexLetter
  };
})(window);
