/* Logan's Daily Newspaper — CFA Math course (data-slot="cfa", SPEC Sec.0e).

   Logan asked for a course that confirms he understands, so every lesson
   grades itself on the page, at once, with no reply to send anywhere:
     - number questions: he works the problem (usually on the calculator) and
       types the result; it is right if it is within the stated tolerance.
       A miss shows a hint and allows one more try; a second miss shows the
       full solution.
     - multiple choice (three options, the exam's format): one try, then
       every option's reason is shown, right and wrong.
     - explain-it-back: he writes a sentence, opens the model answer and
       rates himself (Got it / Partly / Not yet).
   "First try" is what counts toward mastery (80% or better): a lesson he
   only got right on the second go goes on the redo list on cfa.html.
   Results stay on this device (localStorage key cfa-l1:day:N).

   Also here: the BA II Plus / TI-84 Plus switch on calculator walkthroughs
   (both shown by default, remembered per device), and the course page's
   progress, redo list and formula flashcards.

   Progressive enhancement: with JS off the questions, hints and solutions
   are still readable (solutions sit in closed <details>). */
(function () {
  "use strict";

  var COURSE = "cfa-l1";
  var MASTERY = 0.8;
  var doc = document;

  function lsGet(k) { try { return localStorage.getItem(k); } catch (e) { return null; } }
  function lsSet(k, v) { try { localStorage.setItem(k, v); } catch (e) {} }
  function lsDel(k) { try { localStorage.removeItem(k); } catch (e) {} }
  function each(list, fn) { Array.prototype.forEach.call(list, fn); }
  function el(tag, cls, text) {
    var n = doc.createElement(tag);
    if (cls) n.className = cls;
    if (text != null) n.textContent = text;
    return n;
  }
  function loadDay(day) {
    try { return JSON.parse(lsGet(COURSE + ":day:" + day) || "{}") || {}; } catch (e) { return {}; }
  }

  /* ---------- calculator switch ---------- */
  var CALCS = [["both", "Both"], ["ba", "BA II Plus"], ["ti", "TI-84 Plus"]];
  function applyCalc(p) {
    doc.documentElement.setAttribute("data-calc", p);
    each(doc.querySelectorAll(".calc-toggle button"), function (b) {
      b.setAttribute("aria-pressed", String(b.getAttribute("data-calc") === p));
    });
  }
  var calcs = doc.querySelectorAll(".calc");
  if (calcs.length) {
    each(calcs, function (c) {
      var bar = el("div", "calc-toggle");
      bar.setAttribute("role", "group");
      bar.setAttribute("aria-label", "Show keystrokes for");
      CALCS.forEach(function (o) {
        var b = el("button", null, o[1]);
        b.type = "button";
        b.setAttribute("data-calc", o[0]);
        b.addEventListener("click", function () { lsSet("cfa-calc", o[0]); applyCalc(o[0]); });
        bar.appendChild(b);
      });
      c.insertBefore(bar, c.firstChild);
    });
    var pref = lsGet("cfa-calc");
    applyCalc(pref === "ba" || pref === "ti" ? pref : "both");
  }

  /* ---------- graded questions ---------- */
  var lesson = doc.querySelector("[data-cfa-day]");
  if (lesson) gradeLesson(lesson);

  function parseNum(s) {
    s = String(s == null ? "" : s).trim()
      .replace(/[−–—]/g, "-")
      .replace(/[\s,$€£%]/g, "")
      .replace(/^\((.*)\)$/, "-$1");
    if (!/^[-+]?(\d+\.?\d*|\.\d+)(e[-+]?\d+)?$/i.test(s)) return NaN;
    return parseFloat(s);
  }

  function numberOk(q, v) {
    var ans = parseFloat(q.getAttribute("data-answer"));
    var tol = parseFloat(q.getAttribute("data-tol"));
    if (!isFinite(tol)) tol = Math.max(Math.abs(ans) * 0.001, 0.005);
    var cands = [v];
    if (q.hasAttribute("data-signless")) { cands = [Math.abs(v)]; ans = Math.abs(ans); }
    /* A percent answer typed as a decimal (0.0525 for 5.25%) also counts. */
    if (q.hasAttribute("data-pct")) cands.push(cands[0] * 100);
    return cands.some(function (c) { return Math.abs(c - ans) <= tol + 1e-12; });
  }

  function gradeLesson(root) {
    var day = root.getAttribute("data-cfa-day");
    var KEY = COURSE + ":day:" + day;
    var st = loadDay(day);
    st.q = st.q || {};
    var qs = root.querySelectorAll(".cq[data-q]");
    if (!qs.length) return;

    function save() {
      var s = summary();
      st.score = s.score; st.total = s.total; st.answered = s.answered;
      st.mastered = s.answered === s.total && s.total > 0 && s.score / s.total >= MASTERY;
      st.ts = new Date().toISOString();
      lsSet(KEY, JSON.stringify(st));
      renderScore(s);
    }

    function summary() {
      var score = 0, total = 0, answered = 0, warm = 0, warmOk = 0;
      each(qs, function (q) {
        var id = q.getAttribute("data-q");
        var r = st.q[id] || {};
        var isWarm = !!q.closest(".cfa-warmup");
        if (isWarm) { warm++; if (r.done && r.first) warmOk++; return; }
        total++;
        if (!r.done) return;
        answered++;
        if (q.getAttribute("data-type") === "explain") score += r.rating || 0;
        else if (r.first) score += 1;
      });
      return { score: score, total: total, answered: answered, warm: warm, warmOk: warmOk };
    }

    var scoreBox = root.querySelector(".cfa-score");
    function renderScore(s) {
      if (!scoreBox) return;
      scoreBox.textContent = "";
      scoreBox.appendChild(el("b", null, "Your result"));
      if (!s.answered) {
        scoreBox.appendChild(el("p", null, "Answer the " + s.total +
          " check questions above. Getting " + Math.ceil(MASTERY * s.total) +
          " or more right the first time marks this lesson mastered."));
      } else {
        var pct = Math.round(100 * s.score / s.total);
        var line = el("p", "cfa-score-line",
          fmt(s.score) + " of " + s.total + " right the first time (" + pct + "%)" +
          (s.answered < s.total ? " · " + (s.total - s.answered) + " still to answer" : ""));
        scoreBox.appendChild(line);
        if (s.answered === s.total) {
          var ok = s.score / s.total >= MASTERY;
          scoreBox.appendChild(el("p", ok ? "verdict ok" : "verdict redo", ok
            ? "Mastered. You have this one."
            : "Not yet mastered. Re-read the part your misses point to and redo this lesson in a day or two; it stays on your redo list on the course page until you score 80%."));
        }
      }
      if (s.warm) scoreBox.appendChild(el("p", "cfa-score-warm",
        "Warm-up (earlier lessons): " + s.warmOk + " of " + s.warm + " right the first time."));
      var row = el("p", "cfa-score-links");
      var hub = el("a", null, "Course page: progress, redo list, formula cards");
      hub.href = "../../../cfa.html";
      row.appendChild(hub);
      row.appendChild(doc.createTextNode(" · "));
      var reset = el("button", "linklike", "Start this lesson's questions over");
      reset.type = "button";
      reset.addEventListener("click", function () {
        lsDel(KEY);
        location.reload();
      });
      row.appendChild(reset);
      scoreBox.appendChild(row);
    }
    function fmt(n) { return (Math.round(n * 2) / 2).toString(); }

    each(qs, function (q) { setup(q); });
    save();

    function feedback(q) {
      var f = q.querySelector(".cq-feedback");
      if (!f) {
        f = el("p", "cq-feedback");
        f.setAttribute("role", "status");
        var anchor = q.querySelector(".cq-input, .cq-opts, .cq-explain");
        if (anchor && anchor.nextSibling) anchor.parentNode.insertBefore(f, anchor.nextSibling);
        else q.appendChild(f);
      }
      return f;
    }
    function say(q, cls, text) {
      var f = feedback(q);
      f.className = "cq-feedback " + cls;
      f.textContent = text;
    }
    function reveal(q, sel) {
      each(q.querySelectorAll(sel), function (n) {
        n.hidden = false;
        if (n.tagName === "DETAILS") n.open = true;
      });
    }

    function setup(q) {
      var id = q.getAttribute("data-q");
      var type = q.getAttribute("data-type");
      var r = st.q[id] = st.q[id] || {};
      /* Hide the solution until he has had his go (it is readable with JS off). */
      each(q.querySelectorAll(".cq-solution"), function (s) { if (!r.done) s.hidden = true; });
      each(q.querySelectorAll(".cq-hint"), function (s) { if (!r.hinted) s.hidden = true; });
      if (type === "num") setupNum(q, r);
      else if (type === "mc") setupMc(q, r);
      else if (type === "explain") setupExplain(q, r);
    }

    function setupNum(q, r) {
      var input = q.querySelector("input");
      var btn = q.querySelector(".cq-check");
      if (!input || !btn) return;
      function finish(ok) {
        r.done = true;
        input.disabled = true; btn.disabled = true;
        var shown = q.getAttribute("data-show") || q.getAttribute("data-answer");
        say(q, ok ? "ok" : "miss", ok
          ? (r.first ? "Correct." : "Correct on the second try (counts as a miss for mastery).")
          : "Not this time. The answer is " + shown + ". Walk through the solution below.");
        reveal(q, ".cq-solution");
        save();
      }
      if (r.val != null) input.value = r.val;
      if (r.done) { finish(r.ok); return; }
      if (r.tries > 0) say(q, "miss", "Not quite. Use the hint and try once more.");
      function check() {
        var v = parseNum(input.value);
        if (isNaN(v)) { say(q, "info", "Type just a number, e.g. 1234.56 or -7.5"); return; }
        r.val = input.value;
        r.tries = (r.tries || 0) + 1;
        var ok = numberOk(q, v);
        if (ok) { r.ok = true; r.first = r.tries === 1; finish(true); return; }
        if (r.tries === 1) {
          say(q, "miss", "Not quite. Use the hint and try once more.");
          r.hinted = true;
          reveal(q, ".cq-hint");
          save();
        } else { r.ok = false; r.first = false; finish(false); }
      }
      btn.addEventListener("click", check);
      input.addEventListener("keydown", function (e) { if (e.key === "Enter") { e.preventDefault(); check(); } });
    }

    function setupMc(q, r) {
      var btn = q.querySelector(".cq-check");
      var radios = q.querySelectorAll('input[type="radio"]');
      var right = q.getAttribute("data-answer");
      function finish() {
        r.done = true;
        each(radios, function (x) {
          x.disabled = true;
          if (x.value === r.val) x.checked = true;
          var li = x.closest("li");
          if (!li) return;
          if (x.value === right) li.classList.add("is-right");
          else if (x.value === r.val) li.classList.add("is-wrong");
        });
        if (btn) btn.disabled = true;
        say(q, r.first ? "ok" : "miss", r.first ? "Correct." : "Not this time. The answer is " + right + ".");
        reveal(q, ".cq-why, .cq-solution");
        save();
      }
      if (r.done) { finish(); return; }
      each(q.querySelectorAll(".cq-why"), function (w) { w.hidden = true; });
      if (!btn) return;
      btn.addEventListener("click", function () {
        var c = q.querySelector('input[type="radio"]:checked');
        if (!c) { say(q, "info", "Pick one option first."); return; }
        r.val = c.value; r.tries = 1; r.ok = r.first = c.value === right;
        finish();
      });
    }

    function setupExplain(q, r) {
      var ta = q.querySelector("textarea");
      var btn = q.querySelector(".cq-check");
      if (ta && r.text) ta.value = r.text;
      if (ta) ta.addEventListener("input", function () { r.text = ta.value; lsSet(KEY, JSON.stringify(st)); });
      var rate = el("div", "cq-rate");
      rate.hidden = true;
      rate.appendChild(el("span", null, "Compared with the model answer, you had it:"));
      [["Got it", 1], ["Partly", 0.5], ["Not yet", 0]].forEach(function (o) {
        var b = el("button", null, o[0]);
        b.type = "button";
        b.setAttribute("data-rating", String(o[1]));
        b.addEventListener("click", function () {
          r.rating = o[1]; r.done = true; r.first = o[1] === 1;
          mark();
          save();
        });
        rate.appendChild(b);
      });
      var sol = q.querySelector(".cq-solution");
      if (sol && sol.nextSibling) sol.parentNode.insertBefore(rate, sol.nextSibling);
      else q.appendChild(rate);
      function mark() {
        each(rate.querySelectorAll("button"), function (b) {
          b.setAttribute("aria-pressed", String(r.done && parseFloat(b.getAttribute("data-rating")) === r.rating));
        });
      }
      function open() {
        r.opened = true;
        reveal(q, ".cq-solution");
        rate.hidden = false;
        if (btn) btn.disabled = true;
      }
      if (r.opened || r.done) { open(); mark(); }
      if (btn) btn.addEventListener("click", function () {
        open();
        lsSet(KEY, JSON.stringify(st));
      });
    }
  }

  /* ---------- course page (cfa.html) ---------- */
  var hub = doc.getElementById("cfa-hub");
  if (hub) loadHub(hub);

  function getJSON(url) {
    return fetch(url, { cache: "no-cache" }).then(function (r) {
      if (!r.ok) throw new Error(url + " " + r.status);
      return r.json();
    });
  }

  function loadHub(root) {
    Promise.all([getJSON("./cfa.json"), getJSON("./reports/index.json").catch(function () { return []; })])
      .then(function (res) { renderHub(root, res[0], res[1]); })
      .catch(function () {
        root.textContent = "";
        root.appendChild(el("p", "cfa-hub-error", "The course plan could not be loaded. Reload to try again."));
      });
  }

  function renderHub(root, plan, index) {
    var released = {};
    (index || []).forEach(function (e) {
      if (e.slot !== "cfa") return;
      var m = /day-(\d+)\.html$/.exec(e.path || "");
      if (m) released[+m[1]] = e;
    });
    var days = plan.days || [];
    var total = plan.total_days || days.length;
    var nReleased = 0, nMastered = 0, redo = [];
    days.forEach(function (d) {
      if (!released[d.day]) return;
      nReleased++;
      var s = loadDay(d.day);
      if (s.mastered) nMastered++;
      else if (s.answered) redo.push(d);
    });

    root.textContent = "";
    var stats = el("div", "cfa-stats");
    [[nReleased + " of " + total, "lessons out"], [String(nMastered), "mastered"], [String(redo.length), "to redo"]]
      .forEach(function (p) {
        var c = el("div", "cfa-stat");
        c.appendChild(el("span", "cfa-stat-n", p[0]));
        c.appendChild(el("span", "cfa-stat-l", p[1]));
        stats.appendChild(c);
      });
    root.appendChild(stats);

    var latest = null;
    for (var i = days.length - 1; i >= 0; i--) if (released[days[i].day]) { latest = days[i]; break; }
    if (latest) {
      var p = el("p", "cfa-today");
      p.appendChild(doc.createTextNode("Latest: "));
      var a = el("a", null, "Day " + latest.day + " · " + latest.title);
      a.href = "./" + released[latest.day].path;
      p.appendChild(a);
      root.appendChild(p);
    }

    /* Redo list: answered but under 80% first-try. */
    var rs = el("section", "cfa-redo");
    rs.appendChild(el("h2", null, "Redo list"));
    if (!redo.length) rs.appendChild(el("p", "muted", "Nothing to redo. A lesson lands here when you finish its questions under 80% right the first time."));
    else {
      var ul = el("ul");
      redo.forEach(function (d) {
        var s = loadDay(d.day);
        var li = el("li");
        var a = el("a", null, "Day " + d.day + " · " + d.title);
        a.href = "./" + released[d.day].path;
        li.appendChild(a);
        li.appendChild(doc.createTextNode(" — " + (Math.round(2 * s.score) / 2) + " of " + s.total + " first try"));
        ul.appendChild(li);
      });
      rs.appendChild(ul);
    }
    root.appendChild(rs);

    /* Formula cards from every released lesson: tap to flip, mark known. */
    var cards = [];
    days.forEach(function (d) {
      if (!released[d.day]) return;
      (d.memorize || []).forEach(function (m, j) {
        if (m && m.front && m.back) cards.push({ id: d.day + "." + j, day: d.day, front: m.front, back: m.back });
      });
    });
    var fv = el("section", "cfa-vault");
    fv.appendChild(el("h2", null, "Formula cards"));
    if (!cards.length) fv.appendChild(el("p", "muted", "Cards appear here as lessons come out."));
    else renderCards(fv, cards);
    root.appendChild(fv);

    /* The whole plan, by week. */
    var ps = el("section", "cfa-plan");
    ps.appendChild(el("h2", null, "The 90 days"));
    var list = el("ol", "cfa-days");
    days.forEach(function (d) {
      var li = el("li", "cfa-day" + (d.kind && d.kind !== "lesson" ? " is-" + d.kind : ""));
      var head = el("span", "cfa-day-n", "Day " + d.day);
      li.appendChild(head);
      var t;
      if (released[d.day]) { t = el("a", "cfa-day-t", d.title); t.href = "./" + released[d.day].path; }
      else t = el("span", "cfa-day-t", d.title);
      li.appendChild(t);
      li.appendChild(el("span", "cfa-day-area", d.area || ""));
      var s = released[d.day] ? loadDay(d.day) : null;
      var chip = !released[d.day] ? ["upcoming", "Upcoming"]
        : s && s.mastered ? ["ok", "Mastered"]
        : s && s.answered ? ["redo", "Redo"]
        : ["new", "Not started"];
      li.appendChild(el("span", "cfa-chip " + chip[0], chip[1]));
      list.appendChild(li);
    });
    ps.appendChild(list);
    root.appendChild(ps);
  }

  function renderCards(mount, cards) {
    var KNOWN = COURSE + ":known";
    var known = {};
    try { known = JSON.parse(lsGet(KNOWN) || "{}") || {}; } catch (e) { known = {}; }
    var queue = cards.filter(function (c) { return !known[c.id]; });
    if (!queue.length) queue = cards.slice();
    var i = 0;
    var box = el("div", "cfa-card");
    var meta = el("p", "cfa-card-meta");
    var front = el("p", "cfa-card-front");
    var back = el("p", "cfa-card-back");
    var row = el("div", "cfa-card-actions");
    var flip = el("button", null, "Show the answer");
    var gotIt = el("button", null, "I knew it");
    var again = el("button", null, "Not yet");
    [flip, gotIt, again].forEach(function (b) { b.type = "button"; row.appendChild(b); });
    box.appendChild(meta); box.appendChild(front); box.appendChild(back); box.appendChild(row);
    mount.appendChild(el("p", "muted", cards.length + " cards from the lessons so far. Cards you mark as known drop out until every card is known."));
    mount.appendChild(box);
    function show() {
      var c = queue[i % queue.length];
      meta.textContent = "Card " + ((i % queue.length) + 1) + " of " + queue.length + " · from Day " + c.day;
      front.textContent = c.front;
      back.textContent = c.back;
      back.hidden = true;
      flip.hidden = false; gotIt.hidden = true; again.hidden = true;
    }
    flip.addEventListener("click", function () { back.hidden = false; flip.hidden = true; gotIt.hidden = false; again.hidden = false; });
    gotIt.addEventListener("click", function () {
      known[queue[i % queue.length].id] = true;
      lsSet(KNOWN, JSON.stringify(known));
      queue.splice(i % queue.length, 1);
      if (!queue.length) { known = {}; lsDel(KNOWN); queue = cards.slice(); i = 0; }
      show();
    });
    again.addEventListener("click", function () { i++; show(); });
    show();
  }
})();
