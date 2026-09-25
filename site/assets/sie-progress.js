/* SIE Program — progress page (site/reports/sie/progress.html).
   Reads the quiz history that report.js records in localStorage
   ("sie-progress") and turns it into:
     - a summary of every quiz and exam taken,
     - the review deck (spaced repetition: a missed question returns about
       1, 3 and 7 days after the miss; two right in a row retires it from the
       schedule; "Final review" brings back everything ever missed),
     - the weakness tracker (Strong / Adequate / Weak / Very Weak per topic),
     - the error log (Topic | Question type | My mistake | Correct rule | Why).
   Everything lives in this browser on this device. Backup/restore moves it.
   Depends on SIEStore and SIEGrade from report.js (loaded first). */
(function () {
  "use strict";
  if (typeof SIEStore === "undefined" || typeof SIEGrade === "undefined") return;
  var root = document.getElementById("sie-progress");
  if (!root) return;
  var topicsTag = document.getElementById("sie-topics");
  var TOPICS = [];
  try { TOPICS = JSON.parse(topicsTag.textContent); } catch (e) { TOPICS = []; }
  var TOPIC = {};
  TOPICS.forEach(function (t) { TOPIC[t.id] = t; });
  var el = SIEGrade.el;
  var DAY = 86400000;
  var INTERVALS = [1, 3, 7];
  var TAGNAME = { rule: "Rule-based", concept: "Conceptual", careless: "Careless" };
  var TYPENAME = { rule: "Rule", concept: "Concept", calc: "Calculation" };

  function clear(n) { while (n.firstChild) n.removeChild(n.firstChild); }
  function section(id) { return document.getElementById(id); }
  function fmtDate(iso) { var d = new Date(iso); return isNaN(d) ? "" : d.toLocaleDateString(undefined, { month: "short", day: "numeric" }); }

  /* ---------- review scheduling ---------- */
  function reviewsSinceMiss(rec) {
    if (!rec.lastMiss) return 0;
    return rec.history.filter(function (h) { return h.review && h.ts > rec.lastMiss; }).length;
  }
  function dueInfo(rec) {
    if (!rec.lastMiss) return null;
    if ((rec.streak || 0) >= 2) return { retired: true };
    var n = reviewsSinceMiss(rec);
    if (n >= INTERVALS.length) return { retired: true };
    var at = new Date(rec.lastMiss).getTime() + INTERVALS[n] * DAY;
    return { at: at, due: Date.now() >= at, step: n };
  }

  /* ---------- ratings ---------- */
  function rating(answers) {
    var n = answers.length;
    if (!n) return "Untested";
    var last = answers.slice(-10), right = last.filter(Boolean).length, acc = right / last.length;
    var last5 = answers.slice(-5), miss5 = last5.filter(function (x) { return !x; }).length;
    if (acc < 0.5 || miss5 >= 3) return "Very Weak";
    if (acc < 0.7) return "Weak";
    if (acc < 0.85) return "Adequate";
    if (n >= 4 && answers[n - 1] && answers[n - 2]) return "Strong";
    return "Adequate";
  }
  var ORDER = { "Very Weak": 0, "Weak": 1, "Adequate": 2, "Strong": 3, "Untested": 4 };

  /* ---------- summary ---------- */
  function renderSummary(d) {
    var box = section("sie-summary");
    clear(box);
    if (!d.attempts.length) {
      box.appendChild(el("p", null, "No quizzes graded on this device yet. Take a day's quiz and tap “Grade my answers” — your results appear here."));
      return;
    }
    var totalQ = 0, totalR = 0;
    d.attempts.forEach(function (a) { totalQ += a.total; totalR += a.correct; });
    box.appendChild(el("p", "quiz-score-head", totalR + " / " + totalQ + " correct across " + d.attempts.length + " graded quiz" + (d.attempts.length === 1 ? "" : "zes") + " (" + Math.round(100 * totalR / totalQ) + "%)"));
    var wrap = el("div", "table-wrap"), t = el("table", "data-table");
    var th = el("thead"), hr = el("tr");
    ["Day", "Kind", "Score", "Date"].forEach(function (h) { hr.appendChild(el("th", null, h)); });
    th.appendChild(hr); t.appendChild(th);
    var tb = el("tbody");
    d.attempts.slice().reverse().forEach(function (a) {
      var tr = el("tr");
      var link = el("a", null, "Day " + a.day);
      link.href = "day-" + ("0" + a.day).slice(-2) + ".html";
      var td = el("td"); td.appendChild(link); tr.appendChild(td);
      tr.appendChild(el("td", null, a.kind === "exam" ? "Practice exam" : "Quiz"));
      var pct = Math.round(100 * a.correct / a.total);
      tr.appendChild(el("td", null, a.correct + "/" + a.total + " (" + pct + "%)" + (a.kind === "exam" ? (pct >= 70 ? " · pass" : " · below 70") : "")));
      tr.appendChild(el("td", null, fmtDate(a.ts)));
      tb.appendChild(tr);
    });
    t.appendChild(tb); wrap.appendChild(t); box.appendChild(wrap);
  }

  /* ---------- review deck ---------- */
  function missedRecords(d) {
    return Object.keys(d.q).map(function (id) { var r = d.q[id]; r.id = id; return r; })
      .filter(function (r) { return r.lastMiss && r.stem && r.options; });
  }
  function renderDeck(d) {
    var box = section("sie-deck");
    clear(box);
    var missed = missedRecords(d);
    var due = missed.filter(function (r) { var i = dueInfo(r); return i && i.due; });
    var upcoming = missed.filter(function (r) { var i = dueInfo(r); return i && !i.retired && !i.due; });
    var retired = missed.filter(function (r) { var i = dueInfo(r); return i && i.retired; });
    if (!missed.length) {
      box.appendChild(el("p", null, "Nothing to review yet. Every question you miss lands here and comes back about 1, 3 and 7 days later; two right in a row retires it."));
      return;
    }
    box.appendChild(el("p", null, due.length + " due now · " + upcoming.length + " scheduled later · " + retired.length + " retired (answered right twice in a row)."));
    var next = upcoming.map(function (r) { return dueInfo(r).at; }).sort()[0];
    if (!due.length && next) box.appendChild(el("p", null, "Next one comes due " + new Date(next).toLocaleString(undefined, { weekday: "short", month: "short", day: "numeric", hour: "numeric", minute: "2-digit" }) + "."));
    var row = el("div", "answer-actions");
    var b1 = el("button", null, "Review what's due (" + due.length + ")");
    b1.type = "button"; b1.disabled = !due.length;
    var b2 = el("button", "btn-ghost", "Final review: all " + missed.length + " ever missed");
    b2.type = "button";
    row.appendChild(b1); row.appendChild(b2);
    box.appendChild(row);
    var mount = el("div");
    box.appendChild(mount);
    b1.addEventListener("click", function () { startReview(mount, shuffle(due.slice())); });
    b2.addEventListener("click", function () { startReview(mount, shuffle(missed.slice())); });
  }
  function shuffle(a) { for (var i = a.length - 1; i > 0; i--) { var j = Math.floor(Math.random() * (i + 1)); var t = a[i]; a[i] = a[j]; a[j] = t; } return a; }

  function startReview(mount, recs) {
    clear(mount);
    var quiz = el("section", "quiz review-set");
    quiz.appendChild(el("h3", null, "Review set · " + recs.length + " question" + (recs.length === 1 ? "" : "s")));
    quiz.appendChild(el("p", "quiz-howto", "Each is the exact question you missed. Answer, then grade."));
    var ol = el("ol", "quiz-list");
    recs.forEach(function (r, i) {
      var li = el("li", "quiz-q");
      var stem = el("p", "quiz-stem");
      String(r.stem).split("\n").forEach(function (line, k) { if (k) stem.appendChild(el("br")); stem.appendChild(document.createTextNode(line)); });
      li.appendChild(stem);
      var ul = el("ul", "quiz-opts");
      ["A", "B", "C", "D"].forEach(function (L) {
        if (r.options[L] == null) return;
        var lab = el("label"), inp = el("input");
        inp.type = "radio"; inp.name = "rv" + i; inp.value = L;
        lab.appendChild(inp); lab.appendChild(document.createTextNode(" "));
        lab.appendChild(el("b", null, L)); lab.appendChild(document.createTextNode(" " + r.options[L]));
        var oli = el("li"); oli.appendChild(lab); ul.appendChild(oli);
      });
      li.appendChild(ul);
      li.appendChild(el("p", "review-origin", "From Day " + r.day + " · " + ((TOPIC[r.topic] || {}).name || r.topic)));
      ol.appendChild(li);
    });
    quiz.appendChild(ol);
    quiz.appendChild(el("div", "answer-bar"));
    mount.appendChild(quiz);
    SIEGrade.wire(quiz, ol.querySelectorAll(".quiz-q"), recs.map(function (r) {
      return { id: r.id, day: r.day, topic: r.topic, section: r.section, type: r.type, answer: r.answer, rule: r.rule, explanation: r.explanation };
    }), { isReview: true, kind: "review", progressHref: "#sie-tracker", onGraded: function () { renderAll(false); } });
    quiz.scrollIntoView({ behavior: "smooth", block: "start" });
  }

  /* ---------- tracker ---------- */
  function renderTracker(d) {
    var box = section("sie-tracker");
    clear(box);
    var per = {};
    Object.keys(d.q).forEach(function (id) {
      var r = d.q[id];
      (r.history || []).forEach(function (h) { (per[r.topic] = per[r.topic] || []).push(h); });
    });
    var rows = TOPICS.map(function (t) {
      var hs = (per[t.id] || []).slice().sort(function (a, b) { return a.ts < b.ts ? -1 : 1; });
      var ans = hs.map(function (h) { return h.ok; });
      var right = ans.filter(Boolean).length;
      return { t: t, n: ans.length, right: right, r: rating(ans) };
    });
    rows.sort(function (a, b) { return ORDER[a.r] - ORDER[b.r] || (a.n && b.n ? a.right / a.n - b.right / b.n : 0); });
    var tested = rows.filter(function (x) { return x.n; }).length;
    box.appendChild(el("p", null, tested + " of " + TOPICS.length + " topics tested so far. Weakest first; the rating uses your last 10 answers on each topic."));
    var wrap = el("div", "table-wrap"), t = el("table", "data-table");
    var th = el("thead"), hr = el("tr");
    ["Topic", "Rating", "Right", "Section"].forEach(function (h) { hr.appendChild(el("th", null, h)); });
    th.appendChild(hr); t.appendChild(th);
    var tb = el("tbody");
    rows.forEach(function (x) {
      var tr = el("tr");
      tr.appendChild(el("td", null, x.t.name));
      var td = el("td"); td.appendChild(el("span", "rating " + x.r.toLowerCase().replace(" ", "-"), x.r)); tr.appendChild(td);
      tr.appendChild(el("td", null, x.n ? x.right + "/" + x.n : "—"));
      tr.appendChild(el("td", null, x.t.section));
      tb.appendChild(tr);
    });
    t.appendChild(tb); wrap.appendChild(t); box.appendChild(wrap);
  }

  /* ---------- error log ---------- */
  function renderLog(d) {
    var box = section("sie-errors");
    clear(box);
    var missed = missedRecords(d).sort(function (a, b) { return a.lastMiss < b.lastMiss ? 1 : -1; });
    if (!missed.length) { box.appendChild(el("p", null, "No misses recorded yet.")); return; }
    var counts = { rule: 0, concept: 0, careless: 0 };
    missed.forEach(function (r) { if (r.tag) counts[r.tag]++; });
    box.appendChild(el("p", null, missed.length + " questions missed · " + counts.rule + " rule-based · " + counts.concept + " conceptual · " + counts.careless + " careless."));
    missed.forEach(function (r) {
      var card = el("div", "error-row");
      var top = el("p", "error-head");
      top.appendChild(el("b", null, (TOPIC[r.topic] || {}).name || r.topic));
      top.appendChild(document.createTextNode(" · " + (TYPENAME[r.type] || r.type || "") + " · Day " + r.day + " · " + fmtDate(r.lastMiss)));
      card.appendChild(top);
      var mine = el("p");
      mine.appendChild(el("b", null, "My mistake: "));
      mine.appendChild(document.createTextNode(r.lastChosen ? "chose " + r.lastChosen + " — " + (r.options[r.lastChosen] || "") : "left it blank"));
      card.appendChild(mine);
      var rule = el("p");
      rule.appendChild(el("b", null, "Correct rule: "));
      rule.appendChild(document.createTextNode((r.rule || "") + " (Answer " + r.answer + ": " + (r.options[r.answer] || "") + ")"));
      card.appendChild(rule);
      var why = el("p");
      why.appendChild(el("b", null, "Why I missed it: "));
      var sel = el("select");
      [["", "not tagged"], ["rule", "Rule-based"], ["concept", "Conceptual"], ["careless", "Careless"]].forEach(function (o) {
        var op = el("option", null, o[1]); op.value = o[0]; if ((r.tag || "") === o[0]) op.selected = true; sel.appendChild(op);
      });
      sel.addEventListener("change", function () {
        var dd = SIEStore.load(); if (dd.q[r.id]) { dd.q[r.id].tag = sel.value || undefined; SIEStore.save(dd); }
      });
      why.appendChild(sel);
      card.appendChild(why);
      box.appendChild(card);
    });
  }

  /* ---------- backup ---------- */
  function renderBackup() {
    var box = section("sie-backup");
    clear(box);
    box.appendChild(el("p", null, "Progress is stored in this browser only. Safari and the Home Screen app keep separate copies. To move it, copy a backup here and restore it there."));
    var row = el("div", "answer-actions");
    var copy = el("button", "btn-ghost", "Copy backup"); copy.type = "button";
    var restore = el("button", "btn-ghost", "Restore from backup"); restore.type = "button";
    var reset = el("button", "btn-ghost", "Erase all progress"); reset.type = "button";
    row.appendChild(copy); row.appendChild(restore); row.appendChild(reset);
    box.appendChild(row);
    var area = el("textarea", "backup-area"); area.rows = 4; area.hidden = true;
    box.appendChild(area);
    copy.addEventListener("click", function () {
      var txt = JSON.stringify(SIEStore.load());
      area.hidden = false; area.value = txt; area.select();
      if (navigator.clipboard) navigator.clipboard.writeText(txt).then(function () { copy.textContent = "Copied"; }, function () {});
    });
    restore.addEventListener("click", function () {
      if (area.hidden) { area.hidden = false; area.value = ""; area.placeholder = "Paste a backup here, then tap Restore again"; area.focus(); return; }
      try {
        var d = JSON.parse(area.value);
        if (!d || !d.q || !d.attempts) throw new Error("bad");
        if (!window.confirm("Replace the progress on this device with the pasted backup?")) return;
        SIEStore.save(d); renderAll(true);
      } catch (e) { window.alert("That doesn't look like an SIE progress backup."); }
    });
    reset.addEventListener("click", function () {
      if (!window.confirm("Erase every quiz result, the tracker and the review deck on this device?")) return;
      try { localStorage.removeItem("sie-progress"); } catch (e) {}
      renderAll(true);
    });
  }

  function renderAll(withDeck) {
    var d = SIEStore.load();
    renderSummary(d);
    if (withDeck !== false) renderDeck(d);
    renderTracker(d);
    renderLog(d);
  }
  renderAll(true);
  renderBackup();
})();
