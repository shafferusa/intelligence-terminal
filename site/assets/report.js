/* Logan's Daily Newspaper — report page behavior.
   Listen-to-text via the Web Speech API. No dependencies, no network, no build.

   Everything here is progressive enhancement: with JS off, or on a browser
   without speechSynthesis, the audio bar stays hidden and the report reads
   normally. The bar is injected by script (never present in the HTML source)
   so a non-speaking browser never shows a dead control.

   Platform realities this works around, all of them real and all of them
   undocumented in the spec:
     - iOS Safari silently truncates long utterances -> speak sentence-sized
       chunks, never whole sections.
     - Chrome desktop stops speaking after ~15s -> pause/resume heartbeat.
     - getVoices() is empty until the async voiceschanged event -> wait for it.
     - iOS requires the FIRST speak() to happen inside a user gesture -> the
       play button starts synchronously, no awaits before speak().
     - speechSynthesis survives navigation on some builds -> cancel on unload.
*/
(function () {
  "use strict";

  var main = document.querySelector("main");
  if (!main) return;

  /* ---------- real audio, preferred ----------
     A generated MP3 lives on GitHub Releases at a predictable URL. It beats
     Web Speech decisively on iOS: lock-screen playback, background playback,
     CarPlay, a scrub bar, and a voice that doesn't sound like a kiosk. So we
     try it FIRST and only fall back to speech when it isn't there (the audio
     job runs a couple of minutes behind the page, and can fail entirely).

     No HEAD request: a cross-origin preflight would need CORS headers the
     release CDN doesn't send. Instead we just point an <audio> element at it
     and listen for the error event, which needs no CORS at all. */

  var REPO_AUDIO = "https://github.com/shafferusa/intelligence-terminal/releases/download/";

  function reportMeta() {
    var tag = document.getElementById("report-meta");
    if (!tag) return null;
    try { return JSON.parse(tag.textContent); } catch (e) { return null; }
  }

  var meta = reportMeta();

  /* Candidate URLs, best first.

     The release URL used to be the only one, and on iPhone it never worked:
     release assets are served as application/octet-stream with a
     Content-Disposition of attachment. Chrome sniffs past that and plays it
     anyway, which is why this looked fine when tested on a desktop. iOS
     Safari trusts the declared type, refuses the element, and the page
     dropped to browser speech -- on the only device the audio exists for.

     So the build stages recent MP3s into the site itself, where Pages serves
     them as audio/mpeg, inline, same-origin. The release URL stays as a
     second chance for archive pages older than the staging window. */
  function audioUrls() {
    if (!meta || !meta.date || !meta.slot) return [];
    var name = meta.date + "-" + meta.slot;
    /* Report pages sit at <root>/reports/YYYY/MM/x.html, so derive the site
       root from the path rather than guessing a depth of "../../..". */
    var root = location.pathname.split("/reports/")[0] + "/";
    return [
      root + "audio/" + name + ".mp3",
      REPO_AUDIO + "audio-" + name + "/" + name + ".mp3"
    ];
  }

  /* Takes the element that already probed the URL rather than making a new
     one, so the file's metadata isn't fetched twice. */
  function mountNativePlayer(audio) {
    var wrap = document.createElement("div");
    wrap.className = "audio-bar";

    audio.controls = true;
    audio.className = "audio-native";

    var label = document.createElement("span");
    label.className = "audio-status";
    label.textContent = "Listen";

    wrap.appendChild(label);
    wrap.appendChild(audio);

    var anchor = main.querySelector(".paper-head");
    if (anchor && anchor.parentNode) anchor.parentNode.insertBefore(wrap, anchor.nextSibling);
    else main.insertBefore(wrap, main.firstChild);

    /* Lock-screen / CarPlay metadata. */
    if ("mediaSession" in navigator && meta) {
      try {
        navigator.mediaSession.metadata = new window.MediaMetadata({
          title: meta.title || document.title,
          artist: "Logan's Daily Newspaper",
          album: meta.date || ""
        });
      } catch (e) { /* optional */ }
    }

    /* Remember position across visits, like a podcast app. */
    var POS = "lt-audio-time:" + location.pathname;
    audio.addEventListener("loadedmetadata", function () {
      var t = parseFloat(localStorage.getItem(POS) || "");
      if (!isNaN(t) && t > 5 && t < audio.duration - 5) audio.currentTime = t;
    });
    audio.addEventListener("timeupdate", function () {
      if (audio.currentTime > 5) {
        try { localStorage.setItem(POS, String(audio.currentTime)); } catch (e) {}
      }
    });
    audio.addEventListener("ended", function () {
      try { localStorage.removeItem(POS); } catch (e) {}
    });

    return wrap;
  }

  /* Synthesis takes ~9 minutes, so the MP3 lands well after the Telegram push
     that sent the reader here. Rather than leaving early readers on speech
     for the whole report, keep probing quietly and upgrade when it appears --
     but ONLY while speech is idle, because swapping the player out from under
     someone mid-sentence would be worse than the thing it fixes. */
  function sameOrigin(url) {
    return url.indexOf("http") !== 0;
  }

  /* Does this URL have a playable file behind it?

     Same-origin candidates are checked with a one-byte fetch rather than by
     watching an <audio> element for loadedmetadata. iOS defers media loading
     aggressively -- preload="metadata" is a hint it feels free to ignore
     until a user gesture -- so an element probe there can simply never fire
     and time out on a file that is present and fine. A fetch always answers.

     The cross-origin release URL cannot be fetched (no CORS headers on the
     release CDN), so it keeps the element probe as a best effort. */
  function probe(url, ok, fail) {
    if (sameOrigin(url) && window.fetch) {
      fetch(url, { method: "GET", headers: { Range: "bytes=0-0" } })
        .then(function (r) { (r.status === 206 || r.ok) ? ok() : fail(); })
        .catch(fail);
      return;
    }
    var el = document.createElement("audio");
    el.preload = "metadata";
    var settled = false;
    function once(fn) {
      return function () { if (!settled) { settled = true; fn(); } };
    }
    el.addEventListener("loadedmetadata", once(ok));
    el.addEventListener("error", once(fail));
    setTimeout(once(fail), 6000);
    el.src = url;
  }

  /* Walk the candidates in order, then report failure once all are out. */
  function tryAudio(urls, onMissing, attempt) {
    var i = 0;
    (function next() {
      if (i >= urls.length) { onMissing(attempt); return; }
      var url = urls[i++];
      probe(url, function () {
        var speech = window.speechSynthesis;
        if (attempt > 0 && speech && (speech.speaking || speech.pending)) {
          return;                        /* listening already -- don't disturb */
        }
        var old = main.querySelector(".audio-bar");
        if (old && old.parentNode) old.parentNode.removeChild(old);
        var audio = document.createElement("audio");
        audio.preload = "metadata";
        /* Mount before setting src, so the resume-position listener inside
           is attached before loadedmetadata can fire. */
        mountNativePlayer(audio);
        audio.src = url;
      }, next);
    })();
  }

  var urls = audioUrls();
  if (urls.length) {
    tryAudio(urls, function onMissing(attempt) {
      if (attempt === 0) startSpeechPlayer();
      /* Re-check every 90s for ~12 minutes, then stop asking. */
      if (attempt < 8) {
        setTimeout(function () { tryAudio(urls, onMissing, attempt + 1); }, 90000);
      }
    }, 0);
  } else {
    startSpeechPlayer();
  }

  /* ---------- Web Speech fallback ---------- */

  function startSpeechPlayer() {
  var synth = window.speechSynthesis;
  if (!synth || typeof window.SpeechSynthesisUtterance !== "function") return;

  var RATES = [0.9, 1, 1.15, 1.3, 1.5, 1.75];
  var STORE_KEY = "lt-audio-pos:" + location.pathname;
  var RATE_KEY = "lt-audio-rate";

  function lsGet(k) { try { return localStorage.getItem(k); } catch (e) { return null; } }
  function lsSet(k, v) { try { localStorage.setItem(k, v); } catch (e) {} }
  function lsDel(k) { try { localStorage.removeItem(k); } catch (e) {} }

  /* ---------- build the reading list ---------- */

  /* Elements whose text is worth hearing, in document order. Tables, the
     colophon, navigation and collapsed <details> are skipped: a screen-read
     of a 25-row market table is noise, not listening. */
  var READ_SELECTOR = "h1, h2, h3, h4, p, li, blockquote, figcaption";
  var SKIP_CLOSEST = ".board, .colophon, .report-nav, .top-bar, .audio-bar, " +
                     "table, .data-table, .health-footer, .meta-grid, .story-tags, " +
                     ".story-sourceline, .sources-list, .paper-dateline, .answer-bar";

  var blocks = [];

  function collect() {
    blocks = [];
    var nodes = main.querySelectorAll(READ_SELECTOR);
    Array.prototype.forEach.call(nodes, function (node) {
      if (node.closest(SKIP_CLOSEST)) return;
      /* Skip anything inside a collapsed <details> — if the reader hasn't
         opened the expanded analysis, they didn't ask to hear it either. */
      var det = node.closest("details");
      if (det && !det.open) return;
      if (node.offsetParent === null && node.getClientRects().length === 0) return;
      var text = (node.textContent || "").replace(/\s+/g, " ").trim();
      if (text.length < 2) return;
      blocks.push({ node: node, text: text });
    });
    return blocks.length;
  }

  /* Sentence-sized chunks. iOS drops the tail of anything long, so cap hard
     and split on sentence boundaries, then on commas, then on raw length. */
  function chunk(text) {
    var MAX = 220;
    var out = [];
    var parts = text.match(/[^.!?;:]+[.!?;:]+["')\]]*\s*|[^.!?;:]+$/g) || [text];
    var buf = "";
    parts.forEach(function (p) {
      p = p.trim();
      if (!p) return;
      if (p.length > MAX) {
        if (buf) { out.push(buf); buf = ""; }
        var sub = p.split(/,\s+/);
        var b2 = "";
        sub.forEach(function (s) {
          if ((b2 + " " + s).trim().length > MAX) {
            if (b2) out.push(b2);
            while (s.length > MAX) { out.push(s.slice(0, MAX)); s = s.slice(MAX); }
            b2 = s;
          } else { b2 = (b2 ? b2 + ", " : "") + s; }
        });
        if (b2) out.push(b2);
        return;
      }
      if ((buf + " " + p).trim().length > MAX) { out.push(buf); buf = p; }
      else { buf = (buf ? buf + " " + p : p); }
    });
    if (buf) out.push(buf);
    return out.length ? out : [text];
  }

  /* ---------- voice selection ---------- */

  var voice = null;

  function pickVoice() {
    var voices = synth.getVoices() || [];
    if (!voices.length) return null;
    var en = voices.filter(function (v) { return /^en(-|_|$)/i.test(v.lang || ""); });
    var pool = en.length ? en : voices;
    /* British news register first, to sit near en-GB-ThomasNeural -- this is
       only ever reached when the MP3 is genuinely unavailable, and "the iOS
       lady" (Samantha, the US default) is the sound of the thing being
       broken. Daniel and Arthur are the en-GB voices iOS actually ships. */
    var preferred = ["Daniel", "Arthur", "Oliver", "Serena",
                     "Microsoft Ryan", "Microsoft Thomas", "Google UK English Male",
                     "Ava", "Allison", "Samantha"];
    for (var i = 0; i < preferred.length; i++) {
      for (var j = 0; j < pool.length; j++) {
        if ((pool[j].name || "").indexOf(preferred[i]) === 0) return pool[j];
      }
    }
    var local = pool.filter(function (v) { return v.localService; });
    return (local[0] || pool[0]);
  }

  voice = pickVoice();
  if (synth.onvoiceschanged !== undefined) {
    synth.addEventListener("voiceschanged", function () {
      if (!voice) voice = pickVoice();
    });
  }

  /* ---------- UI ---------- */

  var bar = document.createElement("div");
  bar.className = "audio-bar";
  bar.setAttribute("role", "group");
  bar.setAttribute("aria-label", "Listen to this report");

  function mkBtn(cls, label, aria) {
    var b = document.createElement("button");
    b.type = "button";
    b.className = cls;
    b.textContent = label;
    if (aria) b.setAttribute("aria-label", aria);
    return b;
  }

  var playBtn = mkBtn("audio-btn", "▶ Listen", "Play the report aloud");
  var backBtn = mkBtn("audio-btn-ghost", "↶", "Back one paragraph");
  var fwdBtn  = mkBtn("audio-btn-ghost", "↷", "Forward one paragraph");
  var rateBtn = mkBtn("audio-btn-ghost", "1×", "Change speed");
  var stopBtn = mkBtn("audio-btn-ghost", "■", "Stop and clear position");
  var status  = document.createElement("span");
  status.className = "audio-status";
  status.setAttribute("aria-live", "polite");

  bar.appendChild(playBtn);
  bar.appendChild(backBtn);
  bar.appendChild(fwdBtn);
  bar.appendChild(status);
  bar.appendChild(rateBtn);
  bar.appendChild(stopBtn);

  /* ---------- state ---------- */

  var idx = 0;            /* index into blocks */
  var queue = [];         /* chunks of the current block */
  var qi = 0;             /* index into queue */
  var playing = false;
  var stopping = false;   /* distinguishes a deliberate cancel from an error */
  var rate = parseFloat(lsGet(RATE_KEY)) || 1;
  var heartbeat = null;

  function fmtStatus() {
    if (!blocks.length) { status.textContent = ""; return; }
    var pct = Math.min(100, Math.round((idx / blocks.length) * 100));
    status.textContent = playing
      ? pct + "% · " + (blocks.length - idx) + " left"
      : (idx > 0 ? "Paused at " + pct + "%" : "~" + estMinutes() + " min listen");
  }

  function estMinutes() {
    var words = 0;
    blocks.forEach(function (b) { words += b.text.split(" ").length; });
    return Math.max(1, Math.round(words / (165 * rate)));
  }

  function highlight(on) {
    document.querySelectorAll(".is-speaking").forEach(function (n) {
      n.classList.remove("is-speaking");
    });
    if (on && blocks[idx]) {
      blocks[idx].node.classList.add("is-speaking");
      var r = blocks[idx].node.getBoundingClientRect();
      if (r.top < 60 || r.bottom > window.innerHeight - 40) {
        blocks[idx].node.scrollIntoView({ block: "center", behavior: "smooth" });
      }
    }
  }

  /* Chrome stops speaking after ~15s of a long queue; a pause/resume tick
     keeps the engine alive. Harmless on engines that don't need it. */
  function startHeartbeat() {
    stopHeartbeat();
    heartbeat = setInterval(function () {
      if (!playing) return;
      if (synth.speaking && !synth.paused) { synth.pause(); synth.resume(); }
    }, 10000);
  }
  function stopHeartbeat() { if (heartbeat) { clearInterval(heartbeat); heartbeat = null; } }

  function speakCurrent() {
    if (!playing) return;
    if (idx >= blocks.length) { finish(); return; }
    if (qi >= queue.length) {
      idx++;
      qi = 0;
      if (idx >= blocks.length) { finish(); return; }
      queue = chunk(blocks[idx].text);
      highlight(true);
      fmtStatus();
      lsSet(STORE_KEY, String(idx));
    }
    var u = new SpeechSynthesisUtterance(queue[qi]);
    if (voice) { u.voice = voice; u.lang = voice.lang; }
    u.rate = rate;
    u.pitch = 1;
    u.onend = function () {
      if (!playing) return;
      qi++;
      speakCurrent();
    };
    u.onerror = function (e) {
      /* "interrupted"/"canceled" are what a deliberate stop looks like. */
      if (stopping || !playing) return;
      if (e && (e.error === "interrupted" || e.error === "canceled")) return;
      qi++;
      if (qi < queue.length || idx < blocks.length - 1) speakCurrent();
      else finish();
    };
    synth.speak(u);
  }

  function play() {
    if (!blocks.length && !collect()) {
      status.textContent = "Nothing to read";
      return;
    }
    if (!voice) voice = pickVoice();
    /* A <details> closing between sessions can shrink the list under a saved
       or held position; clamp before touching blocks[idx]. */
    if (idx >= blocks.length) idx = 0;
    playing = true;
    stopping = false;
    playBtn.textContent = "⏸ Pause";
    playBtn.setAttribute("aria-label", "Pause");
    if (!queue.length || qi >= queue.length) {
      queue = chunk(blocks[idx].text);
      qi = 0;
    }
    highlight(true);
    fmtStatus();
    startHeartbeat();
    speakCurrent();
  }

  function pause() {
    playing = false;
    stopping = true;
    synth.cancel();          /* more reliable than pause() across engines */
    stopping = false;
    stopHeartbeat();
    playBtn.textContent = "▶ Resume";
    playBtn.setAttribute("aria-label", "Resume");
    qi = 0;                  /* restart the current paragraph on resume */
    highlight(false);
    fmtStatus();
  }

  function finish() {
    playing = false;
    stopHeartbeat();
    synth.cancel();
    idx = 0; qi = 0; queue = [];
    lsDel(STORE_KEY);
    playBtn.textContent = "▶ Listen";
    playBtn.setAttribute("aria-label", "Play the report aloud");
    highlight(false);
    status.textContent = "Finished";
  }

  function jump(delta) {
    var was = playing;
    if (was) { stopping = true; synth.cancel(); stopping = false; }
    idx = Math.max(0, Math.min(blocks.length - 1, idx + delta));
    queue = chunk(blocks[idx].text);
    qi = 0;
    lsSet(STORE_KEY, String(idx));
    highlight(true);
    fmtStatus();
    if (was) speakCurrent();
  }

  playBtn.addEventListener("click", function () {
    if (playing) pause(); else play();
  });
  backBtn.addEventListener("click", function () { if (blocks.length || collect()) jump(-1); });
  fwdBtn.addEventListener("click", function () { if (blocks.length || collect()) jump(1); });
  stopBtn.addEventListener("click", finish);
  rateBtn.addEventListener("click", function () {
    var i = RATES.indexOf(rate);
    rate = RATES[(i + 1) % RATES.length];
    lsSet(RATE_KEY, String(rate));
    rateBtn.textContent = (rate === 1 ? "1" : String(rate)) + "×";
    fmtStatus();
    if (playing) {           /* apply immediately to the next chunk */
      stopping = true; synth.cancel(); stopping = false;
      qi = 0;
      speakCurrent();
    }
  });

  window.addEventListener("beforeunload", function () {
    stopping = true;
    synth.cancel();
  });
  /* iOS fires pagehide rather than beforeunload when leaving a PWA page. */
  window.addEventListener("pagehide", function () {
    stopping = true;
    synth.cancel();
  });

  /* ---------- mount ---------- */

  collect();
  var anchor = main.querySelector(".paper-head");
  if (anchor && anchor.parentNode) anchor.parentNode.insertBefore(bar, anchor.nextSibling);
  else main.insertBefore(bar, main.firstChild);

  rateBtn.textContent = (rate === 1 ? "1" : String(rate)) + "×";

  /* Offer to resume where the reader stopped. */
  var saved = parseInt(lsGet(STORE_KEY) || "", 10);
  if (!isNaN(saved) && saved > 0 && saved < blocks.length) {
    idx = saved;
    playBtn.textContent = "▶ Resume";
  }
  fmtStatus();

  /* Opening a <details> adds readable content; recollect so it can be heard,
     preserving the current position by node identity. */
  main.addEventListener("toggle", function () {
    var currentNode = blocks[idx] && blocks[idx].node;
    collect();
    if (currentNode) {
      for (var i = 0; i < blocks.length; i++) {
        if (blocks[i].node === currentNode) { idx = i; break; }
      }
    }
    if (!playing) fmtStatus();
  }, true);

  } /* end startSpeechPlayer */
})();

/* ---------- SIE Program: self-grading quizzes ----------
   All 30 SIE editions were published at once (2026-09-25), so no routine is
   waiting to grade replies. Each quiz page carries its answer key in a JSON
   block (#quiz-key) that is never rendered; nothing is shown until Logan taps
   "Grade my answers". Grading reveals every explanation, lets him tag each miss
   (rule / concept / careless), and records the result in this browser's
   localStorage under "sie-progress", which progress.html turns into the
   weakness tracker, the error log and the spaced-repetition review deck.
   Without JS the questions still read normally; there is simply no grading. */
var SIEStore = (function () {
  "use strict";
  var KEY = "sie-progress";
  function load() {
    var d = null;
    try { d = JSON.parse(localStorage.getItem(KEY) || "null"); } catch (e) { d = null; }
    if (!d || typeof d !== "object") d = {};
    d.attempts = d.attempts || [];
    d.q = d.q || {};
    return d;
  }
  function save(d) { try { localStorage.setItem(KEY, JSON.stringify(d)); return true; } catch (e) { return false; } }
  return { load: load, save: save };
})();

var SIEGrade = (function () {
  "use strict";
  var SECTIONS = { "1": "Capital markets", "2": "Products & risks", "3": "Trading, accounts & prohibited acts", "4": "Regulatory framework" };
  var TAGS = [["rule", "Didn't know the rule"], ["concept", "Misread the concept"], ["careless", "Careless"]];

  function el(tag, cls, text) {
    var n = document.createElement(tag);
    if (cls) n.className = cls;
    if (text != null) n.textContent = text;
    return n;
  }

  /* Wire one quiz. opts: {items, key (array aligned with items), onGraded(results), record (bool)} */
  function wire(quiz, items, key, opts) {
    opts = opts || {};
    var storeKey = opts.storeKey;
    var saved = {};
    if (storeKey) { try { saved = JSON.parse(localStorage.getItem(storeKey) || "{}") || {}; } catch (e) { saved = {}; } }
    Array.prototype.forEach.call(items, function (li, i) {
      var v = saved[i + 1];
      if (!v) return;
      var input = li.querySelector('input[type="radio"][value="' + v + '"]');
      if (input) input.checked = true;
    });

    var bar = quiz.querySelector(".answer-bar");
    if (!bar) { bar = el("div", "answer-bar"); quiz.appendChild(bar); }
    bar.textContent = "";
    var count = el("p", "answer-count");
    var actions = el("div", "answer-actions");
    var gradeBtn = el("button", null, "Grade my answers");
    gradeBtn.type = "button";
    actions.appendChild(gradeBtn);
    var timerBtn = null, timerOut = null, timerId = null;
    if (opts.timerMinutes) {
      timerBtn = el("button", "btn-ghost", "Start " + Math.floor(opts.timerMinutes / 60) + ":" + ("0" + opts.timerMinutes % 60).slice(-2) + " timer");
      timerBtn.type = "button";
      timerOut = el("span", "quiz-timer");
      actions.appendChild(timerBtn);
      actions.appendChild(timerOut);
    }
    var retakeBtn = el("button", "btn-ghost", "Clear and retake");
    retakeBtn.type = "button";
    retakeBtn.hidden = true;
    actions.appendChild(retakeBtn);
    bar.appendChild(count);
    bar.appendChild(actions);

    var panel = el("div", "quiz-score");
    panel.hidden = true;
    quiz.insertBefore(panel, quiz.querySelector(".quiz-list"));

    function answers() {
      var out = {};
      Array.prototype.forEach.call(items, function (li, i) {
        var c = li.querySelector('input[type="radio"]:checked');
        if (c) out[i + 1] = c.value;
      });
      return out;
    }
    function renderCount() {
      var n = Object.keys(answers()).length;
      count.textContent = n + " of " + items.length + " answered";
      if (storeKey) { try { localStorage.setItem(storeKey, JSON.stringify(answers())); } catch (e) {} }
    }
    quiz.addEventListener("change", function (ev) { if (ev.target && ev.target.type === "radio") renderCount(); });
    renderCount();

    if (timerBtn) {
      timerBtn.addEventListener("click", function () {
        var end = Date.now() + opts.timerMinutes * 60000;
        timerBtn.hidden = true;
        function tick() {
          var left = Math.max(0, end - Date.now());
          var m = Math.floor(left / 60000), sec = Math.floor(left % 60000 / 1000);
          timerOut.textContent = left ? ("Time left " + m + ":" + ("0" + sec).slice(-2)) : "Time is up — grade now";
          if (!left) { clearInterval(timerId); timerOut.classList.add("is-up"); }
        }
        tick();
        timerId = setInterval(tick, 1000);
      });
    }

    function grade(record) {
      var chosen = answers();
      var unanswered = items.length - Object.keys(chosen).length;
      if (record && unanswered && !window.confirm(unanswered + " question(s) unanswered. Grade anyway? Unanswered count as wrong.")) return;
      if (timerId) clearInterval(timerId);
      var res = { correct: 0, total: items.length, bySection: {}, misses: [] };
      var d = record ? SIEStore.load() : null;
      var ts = new Date().toISOString();
      Array.prototype.forEach.call(items, function (li, i) {
        var k = key[i];
        var pick = chosen[i + 1] || null;
        var ok = pick === k.answer;
        if (ok) res.correct++;
        var sec = String(k.section || "?");
        var b = res.bySection[sec] || [0, 0];
        b[0] += ok ? 1 : 0; b[1] += 1; res.bySection[sec] = b;

        li.classList.remove("is-right", "is-wrong");
        li.classList.add(ok ? "is-right" : "is-wrong");
        Array.prototype.forEach.call(li.querySelectorAll('input[type="radio"]'), function (inp) {
          inp.disabled = true;
          var lab = inp.closest("label");
          lab.classList.remove("opt-right", "opt-wrong");
          if (inp.value === k.answer) lab.classList.add("opt-right");
          else if (inp.value === pick) lab.classList.add("opt-wrong");
        });
        var old = li.querySelector(".quiz-explain");
        if (old) old.parentNode.removeChild(old);
        var box = el("div", "quiz-explain");
        var head = el("p");
        var verdict = el("b", null, ok ? "Correct. " : (pick ? "You chose " + pick + ". " : "Not answered. "));
        head.appendChild(verdict);
        head.appendChild(document.createTextNode("Answer: " + k.answer + "."));
        box.appendChild(head);
        if (k.rule) { var r = el("p", "quiz-rule"); r.appendChild(el("b", null, "Rule: ")); r.appendChild(document.createTextNode(k.rule)); box.appendChild(r); }
        box.appendChild(el("p", null, k.explanation || ""));
        li.appendChild(box);

        if (d) {
          var stemEl = li.querySelector(".quiz-stem");
          var rec = d.q[k.id] || { day: k.day, topic: k.topic, section: sec, type: k.type, history: [] };
          rec.history.push({ ts: ts, ok: ok, chosen: pick, review: !!opts.isReview });
          if (!ok) {
            var optsText = {};
            Array.prototype.forEach.call(li.querySelectorAll(".quiz-opts label"), function (lab) {
              var inp = lab.querySelector("input");
              optsText[inp.value] = lab.textContent.replace(/^\s*[A-D]\s*/, "").trim();
            });
            rec.stem = stemEl ? stemEl.innerText : "";
            rec.options = optsText;
            rec.answer = k.answer; rec.rule = k.rule; rec.explanation = k.explanation;
            rec.lastMiss = ts; rec.lastChosen = pick;
            rec.streak = 0;
          } else if (rec.lastMiss) {
            rec.streak = (rec.streak || 0) + 1;
          }
          d.q[k.id] = rec;
        }
        if (!ok) {
          res.misses.push(k.id);
          var tagRow = el("div", "miss-tags");
          tagRow.appendChild(el("span", null, "Why did you miss it? "));
          TAGS.forEach(function (t) {
            var btn = el("button", "tag-btn", t[1]);
            btn.type = "button";
            btn.setAttribute("data-tag", t[0]);
            btn.addEventListener("click", function () {
              var dd = SIEStore.load();
              if (dd.q[k.id]) { dd.q[k.id].tag = t[0]; SIEStore.save(dd); }
              Array.prototype.forEach.call(tagRow.querySelectorAll(".tag-btn"), function (x) { x.classList.toggle("is-on", x === btn); });
              renderPanel();
            });
            tagRow.appendChild(btn);
          });
          box.appendChild(tagRow);
        }
      });
      if (d) {
        if (!opts.isReview) d.attempts.push({ day: opts.day, kind: opts.kind, ts: ts, correct: res.correct, total: res.total, bySection: res.bySection });
        SIEStore.save(d);
      }
      lastRes = res;
      renderPanel();
      gradeBtn.hidden = true;
      retakeBtn.hidden = false;
      if (storeKey) { try { localStorage.setItem(storeKey + ":graded", "1"); } catch (e) {} }
      panel.scrollIntoView({ behavior: "smooth", block: "start" });
      if (opts.onGraded) opts.onGraded(res);
    }

    var lastRes = null;
    function renderPanel() {
      if (!lastRes) return;
      var res = lastRes;
      panel.hidden = false;
      panel.textContent = "";
      var pct = res.total ? Math.round(100 * res.correct / res.total) : 0;
      var h = el("p", "quiz-score-head", res.correct + " / " + res.total + " (" + pct + "%)");
      panel.appendChild(h);
      if (opts.kind === "exam") panel.appendChild(el("p", null, pct >= 70 ? "Above the 70 passing line." : "Below the 70 passing line."));
      var secs = Object.keys(res.bySection).sort();
      var ul = el("ul");
      var best = null, worst = null;
      secs.forEach(function (s) {
        var b = res.bySection[s], p = b[1] ? b[0] / b[1] : 0;
        ul.appendChild(el("li", null, (SECTIONS[s] || "Section " + s) + ": " + b[0] + "/" + b[1] + " (" + Math.round(100 * p) + "%)"));
        if (!best || p > best[1]) best = [s, p];
        if (!worst || p < worst[1]) worst = [s, p];
      });
      panel.appendChild(ul);
      if (secs.length > 1 && best && worst && best[0] !== worst[0]) {
        panel.appendChild(el("p", null, "Strongest: " + SECTIONS[best[0]] + ". Weakest: " + SECTIONS[worst[0]] + "."));
      }
      if (res.misses.length) {
        var d = SIEStore.load(), c = { rule: 0, concept: 0, careless: 0 }, untagged = 0;
        res.misses.forEach(function (id) { var t = d.q[id] && d.q[id].tag; if (t) c[t]++; else untagged++; });
        panel.appendChild(el("p", null, "Your misses: " + c.rule + " rule-based · " + c.concept + " conceptual · " + c.careless + " careless" + (untagged ? " · " + untagged + " not yet tagged (tag them below each explanation)" : "") + "."));
      }
      var link = el("a", null, "Open your progress and review deck →");
      link.href = opts.progressHref || "progress.html";
      var lp = el("p"); lp.appendChild(link); panel.appendChild(lp);
    }

    gradeBtn.addEventListener("click", function () { grade(true); });
    retakeBtn.addEventListener("click", function () {
      Array.prototype.forEach.call(items, function (li) {
        li.classList.remove("is-right", "is-wrong");
        var x = li.querySelector(".quiz-explain"); if (x) x.parentNode.removeChild(x);
        Array.prototype.forEach.call(li.querySelectorAll('input[type="radio"]'), function (inp) {
          inp.disabled = false; inp.checked = false;
          inp.closest("label").classList.remove("opt-right", "opt-wrong");
        });
      });
      panel.hidden = true; lastRes = null;
      gradeBtn.hidden = false; retakeBtn.hidden = true;
      if (storeKey) { try { localStorage.removeItem(storeKey); localStorage.removeItem(storeKey + ":graded"); } catch (e) {} }
      renderCount();
      if (timerBtn) { timerBtn.hidden = false; timerOut.textContent = ""; timerOut.classList.remove("is-up"); }
      quiz.scrollIntoView({ behavior: "smooth", block: "start" });
    });

    /* Coming back to a quiz already graded on this device: show the graded
       view again without recording a second attempt. */
    if (storeKey) {
      var was = null;
      try { was = localStorage.getItem(storeKey + ":graded"); } catch (e) { was = null; }
      if (was) grade(false);
    }
  }

  return { wire: wire, el: el, SECTIONS: SECTIONS };
})();

(function () {
  "use strict";
  var quiz = document.querySelector(".quiz[data-quiz-day]");
  var keyTag = document.getElementById("quiz-key");
  if (!quiz || !keyTag) return;
  var key;
  try { key = JSON.parse(keyTag.textContent); } catch (e) { return; }
  var items = quiz.querySelectorAll(".quiz-q");
  if (!items.length || items.length !== key.questions.length) return;
  var day = quiz.getAttribute("data-quiz-day");
  SIEGrade.wire(quiz, items, key.questions.map(function (q) { q.day = +day; return q; }), {
    storeKey: "sie-answers:" + day,
    day: +day,
    kind: key.kind,
    timerMinutes: key.kind === "exam" ? 105 : 0,
    progressHref: "progress.html"
  });
})();

/* ---------- SIE Study Guide progress ----------
   One checkbox per chapter ("I've worked through Day N"), remembered on this
   device, with a tick against the chapter in the contents. Pure convenience:
   with JS off or storage blocked the guide reads exactly the same. */
(function () {
  "use strict";
  var chapters = document.querySelectorAll(".guide-chapter[id]");
  if (!chapters.length) return;
  var KEY = "sie-guide-done";
  var done = {};
  try { done = JSON.parse(localStorage.getItem(KEY) || "{}") || {}; } catch (e) { done = {}; }
  function save() { try { localStorage.setItem(KEY, JSON.stringify(done)); } catch (e) {} }
  function mark(id) {
    var link = document.querySelector('.guide-toc a[href="#' + id + '"]');
    if (link && link.parentNode) link.parentNode.classList.toggle("done", !!done[id]);
  }
  Array.prototype.forEach.call(chapters, function (ch) {
    var id = ch.id;
    var h = ch.querySelector("h2");
    var label = document.createElement("label");
    label.className = "guide-done";
    var box = document.createElement("input");
    box.type = "checkbox";
    box.checked = !!done[id];
    label.appendChild(box);
    label.appendChild(document.createTextNode("I've worked through " + (h ? h.textContent.split(" · ")[0] : "this chapter")));
    ch.appendChild(label);
    box.addEventListener("change", function () {
      if (box.checked) done[id] = true; else delete done[id];
      save();
      mark(id);
    });
    mark(id);
  });
})();

/* ---------- SIE pages: mark tables that need a sideways swipe ----------
   Most SIE tables wrap to the screen. The few that cannot (a seven-column
   option chain) get wrapped in a positioned host with a right-edge fade and
   a "Swipe for more" line, both removed once the reader reaches the end.
   Without JS the table still scrolls; it just has no cue. */
(function () {
  "use strict";
  var paper = document.querySelector('.paper[data-slot="sie"]');
  if (!paper) return;
  function setup() {
    Array.prototype.forEach.call(paper.querySelectorAll(".table-wrap"), function (wrap) {
      if (wrap.parentNode.classList.contains("table-fade-host")) return;
      if (wrap.scrollWidth <= wrap.clientWidth + 2) return;
      var host = document.createElement("div");
      host.className = "table-fade-host";
      wrap.parentNode.insertBefore(host, wrap);
      host.appendChild(wrap);
      var fade = document.createElement("div");
      fade.className = "table-fade";
      host.appendChild(fade);
      var hint = document.createElement("p");
      hint.className = "swipe-hint";
      hint.textContent = "Swipe the table for more →";
      host.parentNode.insertBefore(hint, host.nextSibling);
      function check() {
        host.classList.toggle("at-end", wrap.scrollLeft + wrap.clientWidth >= wrap.scrollWidth - 4);
      }
      wrap.addEventListener("scroll", check, { passive: true });
      check();
    });
  }
  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", setup);
  else setup();
})();
