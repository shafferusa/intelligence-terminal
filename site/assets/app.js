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

  var SLOT_NAMES = {
    am: "Morning Brief", pm: "Closing Brief", sat: "Weekly Review",
    sun: "Week-Ahead Outlook", learn: "Learning Brief"
  };
  var MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];
  var DAYS = ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"];

  /* Edition filter for the archive. Three editions land every weekday, so a
     reader looking for last Tuesday's lesson, or only the closing briefs,
     should not have to scan the whole list. Persisted like the theme. */
  var FILTERS = [
    { key: "all",     label: "All",      slots: null },
    { key: "learn",   label: "Learning", slots: ["learn"] },
    { key: "am",      label: "Morning",  slots: ["am"] },
    { key: "pm",      label: "Closing",  slots: ["pm"] },
    { key: "weekend", label: "Weekend",  slots: ["sat", "sun"] }
  ];
  var FILTER_KEY = "lt-filter";
  var activeFilter = "all";

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

  function pad(n) { return (n < 10 ? "0" : "") + n; }
  function parseDate(iso) { var p = iso.split("-"); return new Date(+p[0], +p[1] - 1, +p[2]); }
  function todayIso() {
    var d = new Date();
    return d.getFullYear() + "-" + pad(d.getMonth() + 1) + "-" + pad(d.getDate());
  }
  function fmtDate(iso) {
    var dt = parseDate(iso);
    return DAYS[dt.getDay()] + " " + MONTHS[dt.getMonth()] + " " + dt.getDate() + ", " + dt.getFullYear();
  }
  function weekLabel(iso) {
    var dt = parseDate(iso);
    dt.setDate(dt.getDate() - ((dt.getDay() + 6) % 7)); /* back to Monday */
    return "Week of " + MONTHS[dt.getMonth()] + " " + dt.getDate() + ", " + dt.getFullYear();
  }

  /* Learning Briefs carry their curriculum position as the first headline
     ("Day 13 of 150 · Mathematics: ..."). It is the only place the index
     records it, and it is what makes the lessons navigable as a sequence. */
  function lessonDay(entry) {
    if (!entry || entry.slot !== "learn" || !Array.isArray(entry.headlines)) return null;
    var m = /^Day\s+(\d+)\s+of\s+(\d+)(?:\s*·\s*([^:]+))?/i.exec(String(entry.headlines[0] || ""));
    if (!m) return null;
    return { day: +m[1], total: +m[2], subject: (m[3] || "").trim() };
  }

  function metaLine(entry) {
    var d = lessonDay(entry);
    return fmtDate(entry.date) + " · " + (SLOT_NAMES[entry.slot] || entry.slot) +
      (d ? " · Day " + d.day + " of " + d.total : "") +
      " · " + entry.reading_minutes + " min read";
  }
  function markRead(path) { readPaths.add(path); saveSet("lt-read", readPaths); }

  /* ---------- hero (the latest report, plus the rest of that day) ---------- */
  function renderHero(latest, sameDay) {
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

    /* By the closing brief there are three editions from the same day. The
       morning brief and the lesson used to drop straight into the archive
       list below the search box, where they read as yesterday's news. */
    if (sameDay.length) {
      var more = el("div", { class: "hero-more" });
      more.appendChild(el("p", {
        class: "hero-kicker",
        text: latest.date === todayIso() ? "Also today" : "Also that day"
      }));
      var ul = el("ul", { class: "report-list" });
      sameDay.forEach(function (e) { ul.appendChild(renderRow(e)); });
      more.appendChild(ul);
      card.appendChild(more);
    }
  }

  /* ---------- the Academy strip (curriculum position) ---------- */
  function renderAcademyStrip() {
    var mount = doc.getElementById("academy-strip");
    if (!mount) return;
    var latestLearn = null;
    for (var i = 0; i < entries.length; i++) {
      if (entries[i].slot === "learn") { latestLearn = entries[i]; break; }
    }
    var d = lessonDay(latestLearn);
    if (!d) { mount.hidden = true; return; }
    mount.textContent = "";
    var link = el("a", { class: "academy-link", href: "./academy.html" }, [
      el("span", { class: "academy-kicker", text: "The Academy" }),
      el("span", { class: "academy-progress",
        text: "Day " + d.day + " of " + d.total + (d.subject ? " · " + d.subject : "") }),
      el("span", { class: "academy-cta", text: "Every lesson, in order →" })
    ]);
    mount.appendChild(link);
    mount.hidden = false;
  }

  /* ---------- archive list, grouped by week ---------- */
  function currentFilter() {
    for (var i = 0; i < FILTERS.length; i++) if (FILTERS[i].key === activeFilter) return FILTERS[i];
    return FILTERS[0];
  }

  function renderArchive() {
    var mount = doc.getElementById("archive");
    if (!mount) return;
    mount.textContent = "";
    var f = currentFilter();
    /* The latest day's editions are already in the hero card above; repeating
       them as the first archive rows made every page look like it had
       published twice. A filter is a different question ("show me every
       lesson"), so then the list is complete. */
    var pool = (filterBookmarks || f.slots) ? entries : entries.slice(sameDayCount());
    var list = pool.filter(function (e) {
      if (f.slots && f.slots.indexOf(e.slot) < 0) return false;
      if (filterBookmarks && !bookmarks.has(e.path)) return false;
      return true;
    });
    if (!list.length) {
      mount.appendChild(el("p", {
        class: "muted",
        text: filterBookmarks
          ? "No bookmarked reports yet — tap the star next to any report to save it here."
          : (f.slots
              ? "No " + f.label.toLowerCase() + " editions in the archive yet."
              : (entries.length
                  ? "Nothing earlier yet — the latest report is above."
                  : "No reports published yet. The first scheduled run will appear here."))
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

  /* How many entries at the top of the index share the latest date. */
  function sameDayCount() {
    if (!entries.length) return 0;
    var n = 0;
    while (n < entries.length && entries[n].date === entries[0].date) n++;
    return n;
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
      renderAll();
    });
    row.appendChild(bm);
    return row;
  }

  function renderAll() {
    if (entries.length) renderHero(entries[0], entries.slice(1, sameDayCount()));
    renderArchive();
  }

  function updateFilterBtn() {
    var btn = doc.getElementById("bm-filter");
    if (!btn) return;
    btn.hidden = bookmarks.size === 0 && !filterBookmarks;
    btn.setAttribute("aria-pressed", String(filterBookmarks));
    btn.textContent = filterBookmarks ? "Showing bookmarks" : "Bookmarked only";
  }

  /* ---------- edition filter chips ---------- */
  function initFilters() {
    var mount = doc.getElementById("edition-filter");
    if (!mount) return;
    var saved = lsGet(FILTER_KEY);
    if (FILTERS.some(function (f) { return f.key === saved; })) activeFilter = saved;
    mount.textContent = "";
    FILTERS.forEach(function (f) {
      var b = el("button", {
        class: "chip-btn", type: "button", "data-filter": f.key,
        "aria-pressed": String(f.key === activeFilter), text: f.label
      });
      b.addEventListener("click", function () {
        activeFilter = f.key;
        if (activeFilter === "all") lsDel(FILTER_KEY); else lsSet(FILTER_KEY, activeFilter);
        Array.prototype.forEach.call(mount.querySelectorAll(".chip-btn"), function (c) {
          c.setAttribute("aria-pressed", String(c.getAttribute("data-filter") === activeFilter));
        });
        renderArchive();
      });
      mount.appendChild(b);
    });
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
  initFilters();
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
      if (!entries.length) {
        var hs = doc.getElementById("hero-status");
        if (hs) hs.textContent = "No reports published yet — the first scheduled run will appear here.";
      }
      renderAll();
      renderAcademyStrip();
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
