/* Logan's Daily Newspaper — index page behavior.
   No dependencies, no build step. Everything here is an optional enhancement:
   with JS disabled the page still shows the masthead, noscript notes, and footer.
   All paths are relative (the site serves under /intelligence-terminal/). */
(function () {
  "use strict";

  var doc = document;

  /* ---------- storage helpers (private-mode safe) ---------- */
  function lsGet(key) { try { return localStorage.getItem(key); } catch (e) { return null; } }
  function lsSet(key, val) { try { localStorage.setItem(key, val); } catch (e) {} }
  function lsDel(key) { try { localStorage.removeItem(key); } catch (e) {} }
  function loadSet(key) {
    try { return new Set(JSON.parse(lsGet(key) || "[]")); } catch (e) { return new Set(); }
  }
  function saveSet(key, set) { lsSet(key, JSON.stringify(Array.from(set))); }

  var readPaths = loadSet("lt-read");
  var bookmarks = loadSet("lt-bookmarks");
  var filterBookmarks = false;
  var entries = [];

  var SLOT_NAMES = { am: "Morning Brief", pm: "Closing Brief", sat: "Weekly Review", sun: "Week-Ahead Outlook" };
  var MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];
  var DAYS = ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"];

  /* ---------- tiny DOM builder (textContent only — no innerHTML for data) ---------- */
  function el(tag, attrs, children) {
    var node = doc.createElement(tag);
    if (attrs) Object.keys(attrs).forEach(function (k) {
      if (k === "text") node.textContent = attrs[k];
      else node.setAttribute(k, attrs[k]);
    });
    (children || []).forEach(function (c) { node.appendChild(c); });
    return node;
  }

  function parseDate(iso) { var p = iso.split("-"); return new Date(+p[0], +p[1] - 1, +p[2]); }
  function fmtDate(iso) {
    var dt = parseDate(iso);
    return DAYS[dt.getDay()] + " " + MONTHS[dt.getMonth()] + " " + dt.getDate() + ", " + dt.getFullYear();
  }
  function weekLabel(iso) {
    var dt = parseDate(iso);
    dt.setDate(dt.getDate() - ((dt.getDay() + 6) % 7)); /* back to Monday */
    return "Week of " + MONTHS[dt.getMonth()] + " " + dt.getDate() + ", " + dt.getFullYear();
  }
  function metaLine(entry) {
    return fmtDate(entry.date) + " · " + (SLOT_NAMES[entry.slot] || entry.slot) +
      " · " + entry.reading_minutes + " min read";
  }
  function markRead(path) { readPaths.add(path); saveSet("lt-read", readPaths); }

  /* ---------- hero (latest report) ---------- */
  function renderHero(latest) {
    var card = doc.querySelector(".hero-card");
    if (!card) return;
    card.textContent = "";
    card.appendChild(el("p", { class: "hero-kicker", text: "Latest report" }));
    var title = el("h2", { class: "hero-title" },
      [el("a", { href: "./" + latest.path, text: latest.title })]);
    card.appendChild(title);
    card.appendChild(el("p", { class: "hero-meta", text: metaLine(latest) }));
    card.appendChild(el("p", { class: "hero-summary", text: latest.summary }));
    var open = el("a", { class: "btn btn-primary", href: "./" + latest.path, text: "Open the report" });
    card.appendChild(open);
    [title.firstChild, open].forEach(function (a) {
      a.addEventListener("click", function () { markRead(latest.path); });
    });
  }

  /* ---------- archive list, grouped by week ---------- */
  function renderArchive() {
    var mount = doc.getElementById("archive");
    if (!mount) return;
    mount.textContent = "";
    var list = filterBookmarks
      ? entries.filter(function (e) { return bookmarks.has(e.path); })
      : entries;
    if (!list.length) {
      mount.appendChild(el("p", {
        class: "muted",
        text: filterBookmarks
          ? "No bookmarked reports yet — tap the star next to any report to save it here."
          : "No reports published yet. The first scheduled run will appear here."
      }));
      return;
    }
    var currentWeek = null, ul = null;
    list.forEach(function (entry) {
      var wk = weekLabel(entry.date);
      if (wk !== currentWeek) {
        currentWeek = wk;
        var sec = el("section", { class: "archive-week" }, [el("h3", { text: wk })]);
        ul = el("ul", { class: "report-list" });
        sec.appendChild(ul);
        mount.appendChild(sec);
      }
      ul.appendChild(renderRow(entry));
    });
  }

  function renderRow(entry) {
    var row = el("li", { class: "report-row" + (readPaths.has(entry.path) ? " is-read" : "") });
    row.appendChild(el("span", { class: "dot", "aria-hidden": "true" }));
    var link = el("a", { class: "row-title", href: "./" + entry.path, text: entry.title });
    link.addEventListener("click", function () { markRead(entry.path); row.classList.add("is-read"); });
    row.appendChild(el("div", { class: "row-main" },
      [link, el("p", { class: "row-meta", text: metaLine(entry) })]));
    var marked = bookmarks.has(entry.path);
    var bm = el("button", {
      class: "bm-btn", type: "button", "aria-pressed": String(marked),
      "aria-label": (marked ? "Remove bookmark: " : "Bookmark: ") + entry.title,
      text: marked ? "★" : "☆"
    });
    bm.addEventListener("click", function () {
      if (bookmarks.has(entry.path)) bookmarks.delete(entry.path); else bookmarks.add(entry.path);
      saveSet("lt-bookmarks", bookmarks);
      if (filterBookmarks && bookmarks.size === 0) filterBookmarks = false;
      updateFilterBtn();
      renderArchive();
    });
    row.appendChild(bm);
    return row;
  }

  function updateFilterBtn() {
    var btn = doc.getElementById("bm-filter");
    if (!btn) return;
    btn.hidden = bookmarks.size === 0 && !filterBookmarks;
    btn.setAttribute("aria-pressed", String(filterBookmarks));
    btn.textContent = filterBookmarks ? "Showing bookmarks" : "Bookmarked only";
  }

  /* ---------- theme toggle: auto -> light -> dark -> auto ---------- */
  var THEMES = ["auto", "light", "dark"];
  function applyTheme(mode) {
    if (mode === "light" || mode === "dark") {
      doc.documentElement.setAttribute("data-theme", mode);
      lsSet("lt-theme", mode);
    } else {
      doc.documentElement.removeAttribute("data-theme");
      lsDel("lt-theme");
    }
    var btn = doc.getElementById("theme-btn");
    if (btn) btn.textContent = "Theme: " + mode.charAt(0).toUpperCase() + mode.slice(1);
  }
  function initTheme() {
    var mode = lsGet("lt-theme");
    if (THEMES.indexOf(mode) < 0) mode = "auto";
    applyTheme(mode);
    var btn = doc.getElementById("theme-btn");
    if (btn) btn.addEventListener("click", function () {
      mode = THEMES[(THEMES.indexOf(mode) + 1) % THEMES.length];
      applyTheme(mode);
    });
  }

  /* ---------- text size: 3 steps (2 = default) ---------- */
  var SIZES = ["1", "2", "3"], SIZE_NAMES = { "1": "S", "2": "M", "3": "L" };
  function applySize(step) {
    if (step === "2") { doc.documentElement.removeAttribute("data-textsize"); lsDel("lt-textsize"); }
    else { doc.documentElement.setAttribute("data-textsize", step); lsSet("lt-textsize", step); }
    var btn = doc.getElementById("size-btn");
    if (btn) btn.textContent = "Text size: " + SIZE_NAMES[step];
  }
  function initSize() {
    var step = lsGet("lt-textsize");
    if (SIZES.indexOf(step) < 0) step = "2";
    applySize(step);
    var btn = doc.getElementById("size-btn");
    if (btn) btn.addEventListener("click", function () {
      step = SIZES[(SIZES.indexOf(step) + 1) % SIZES.length];
      applySize(step);
    });
  }

  /* ---------- Pagefind search (assets exist only after the Actions build) ---------- */
  function initSearch() {
    var mount = doc.getElementById("search");
    if (!mount) return;
    function unavailable() {
      mount.textContent = "";
      mount.appendChild(el("p", {
        class: "muted small",
        text: "Search becomes available after the next site build (Pagefind index not present)."
      }));
    }
    var css = el("link", { rel: "stylesheet", href: "./pagefind/pagefind-ui.css" });
    doc.head.appendChild(css); /* missing css is cosmetic only */
    var script = doc.createElement("script");
    script.src = "./pagefind/pagefind-ui.js";
    script.onload = function () {
      try {
        if (window.PagefindUI) new window.PagefindUI({ element: "#search", showSubResults: true, showImages: false });
        else unavailable();
      } catch (e) { unavailable(); }
    };
    script.onerror = unavailable; /* absent locally / before first build — never breaks the page */
    doc.head.appendChild(script);
  }

  /* ---------- boot (script is deferred, DOM is ready) ---------- */
  initTheme();
  initSize();
  initSearch();
  updateFilterBtn();
  var fbtn = doc.getElementById("bm-filter");
  if (fbtn) fbtn.addEventListener("click", function () {
    filterBookmarks = !filterBookmarks;
    updateFilterBtn();
    renderArchive();
  });

  fetch("./reports/index.json", { cache: "no-cache" })
    .then(function (r) { if (!r.ok) throw new Error("HTTP " + r.status); return r.json(); })
    .then(function (json) {
      entries = Array.isArray(json) ? json : [];
      if (entries.length) {
        renderHero(entries[0]);
      } else {
        var hs = doc.getElementById("hero-status");
        if (hs) hs.textContent = "No reports published yet — the first scheduled run will appear here.";
      }
      renderArchive();
    })
    .catch(function () {
      var hs = doc.getElementById("hero-status");
      if (hs) hs.textContent = "Could not load the report index.";
      var as = doc.getElementById("archive-status");
      if (as) as.textContent = "Could not load the archive (reports/index.json). Reload to retry.";
    });

  if ("serviceWorker" in navigator) {
    navigator.serviceWorker.register("./sw.js").catch(function () {});
  }
  try {
    if (navigator.storage && navigator.storage.persist) {
      navigator.storage.persist().catch(function () {});
    }
  } catch (e) { /* best effort */ }
})();
