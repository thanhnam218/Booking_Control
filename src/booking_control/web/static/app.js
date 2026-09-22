/* Booking Control - tien ich chung cho moi trang.
 *  - Gan CSRF token vao moi form POST va moi fetch() ghi du lieu
 *  - Chuong thong bao: hoi server moi 60s, bat Desktop Notification cho muc WARNING/CRITICAL
 */
(function () {
  "use strict";
  var meta = document.querySelector('meta[name="csrf"]');
  var CSRF = meta ? meta.getAttribute("content") : "";
  window.BC = window.BC || {};
  window.BC.csrf = CSRF;

  function addCsrf(form) {
    if ((form.getAttribute("method") || "").toLowerCase() !== "post") return;
    if (form.querySelector('input[name="_csrf"]')) return;
    var i = document.createElement("input");
    i.type = "hidden"; i.name = "_csrf"; i.value = CSRF;
    form.appendChild(i);
  }
  document.querySelectorAll("form").forEach(addCsrf);
  document.addEventListener("submit", function (e) { addCsrf(e.target); }, true);

  window.BC.api = function (url, body, method) {
    return fetch(url, {
      method: method || (body === undefined ? "GET" : "POST"),
      headers: { "Content-Type": "application/json", "Accept": "application/json", "X-CSRF-Token": CSRF },
      body: body === undefined ? undefined : JSON.stringify(body),
      credentials: "same-origin"
    }).then(function (r) {
      return r.json().catch(function () { return {}; }).then(function (data) {
        if (r.status === 401) { window.location = "/login?next=" + encodeURIComponent(location.pathname + location.search); }
        if (!r.ok) { var err = new Error(data.error || ("Loi " + r.status)); err.status = r.status; err.data = data; throw err; }
        return data;
      });
    });
  };

  // ---- Chuong thong bao + Desktop Notification ---------------------------
  var bell = document.getElementById("bell-count");
  if (!bell) return;
  var KEY = "bc-last-notif";
  var last = 0;
  try { last = parseInt(localStorage.getItem(KEY) || "0", 10) || 0; } catch (e) { /* private mode */ }

  function poll() {
    fetch("/api/notifications/unread?since=" + last, { headers: { "Accept": "application/json" }, credentials: "same-origin" })
      .then(function (r) { return r.ok ? r.json() : null; })
      .then(function (d) {
        if (!d) return;
        bell.textContent = d.count; bell.hidden = !d.count;
        if (d.fresh && d.fresh.length && "Notification" in window && Notification.permission === "granted" && last) {
          d.fresh.slice(0, 3).forEach(function (n) {
            var note = new Notification(n.title, { body: n.body || "", tag: "bc-" + n.id });
            note.onclick = function () { window.focus(); if (n.shipment) location.href = "/shipments/" + n.shipment; };
          });
        }
        if (d.latest_id) { last = Math.max(last, d.latest_id); try { localStorage.setItem(KEY, String(last)); } catch (e) { /* bo qua */ } }
      })
      .catch(function () { /* mat mang tam thoi - thu lai lan sau */ });
  }
  poll();
  setInterval(poll, 60000);

  var askBtn = document.getElementById("ask-notify");
  if (askBtn && "Notification" in window) {
    askBtn.hidden = Notification.permission !== "default";
    askBtn.addEventListener("click", function () { Notification.requestPermission().then(function () { askBtn.hidden = true; }); });
  }
})();
