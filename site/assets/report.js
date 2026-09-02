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

  /* ---------- prev / next from the archive index ----------
     The routine fills "Previous report" at publish time and leaves "Next
     report" disabled, because the next report does not exist yet and nothing
     ever comes back to fill it in. So a reader working forward through the
     archive -- or from one lesson to the next -- hit a dead link on every
     page. The index knows the order, so ask it at read time: lessons step
     through lessons, news editions step through the paper. On a lesson the
     middle link goes to the Academy (the top bar still leads home). Any
     failure leaves the routine-written links exactly as they were. */
  (function enhanceNav() {
    var nav = main.querySelector(".report-nav");
    if (!nav || !meta || !meta.path || !window.fetch) return;
    var root = location.pathname.split("/reports/")[0] + "/";
    fetch(root + "reports/index.json", { cache: "no-cache" })
      .then(function (r) { return r.ok ? r.json() : null; })
      .then(function (list) {
        if (!Array.isArray(list)) return;
        var lesson = meta.slot === "learn";
        var seq = list.filter(function (e) { return e && (e.slot === "learn") === lesson; });
        var i = -1;
        for (var k = 0; k < seq.length; k++) {
          if (seq[k] && seq[k].path === meta.path) { i = k; break; }
        }
        if (i < 0) return;
        var prev = seq[i + 1], next = seq[i - 1];   /* the index is newest first */
        var links = nav.querySelectorAll("a");
        if (links.length < 2) return;
        function set(a, target, label) {
          a.textContent = label;
          if (!target || !target.path) {
            a.removeAttribute("href");
            a.classList.add("is-disabled");
            a.setAttribute("aria-disabled", "true");
            return;
          }
          a.setAttribute("href", root + target.path);
          a.setAttribute("title", target.title || "");
          a.classList.remove("is-disabled");
          if (!a.className) a.removeAttribute("class");
          a.removeAttribute("aria-disabled");
        }
        set(links[0], prev, lesson ? "\u2190 Previous lesson" : "\u2190 Previous report");
        set(links[links.length - 1], next, lesson ? "Next lesson \u2192" : "Next report \u2192");
        if (lesson && links.length === 3) {
          links[1].setAttribute("href", root + "academy.html");
          links[1].textContent = "The Academy";
        }
      })
      .catch(function () { /* keep the links the routine wrote */ });
  })();

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
  var READ_SELECTOR = "h1, h2, h3, h4, p, li, blockquote, figcaption, dt, dd";
  var SKIP_CLOSEST = ".board, .colophon, .report-nav, .top-bar, .audio-bar, " +
                     "table, .data-table, .health-footer, .meta-grid, .story-tags, " +
                     ".story-sourceline, .sources-list, .paper-dateline";

  var blocks = [];

  /* Notation, spoken. "(1 + y)<sup>−n</sup>" flattens to "(1 + y)−n" and the
     voice says "minus n" for "to the power of minus n"; a formula plate's
     raw .expr line is translated symbol by symbol, and a lesson that supplies
     its own spoken form (<p class="expr-spoken">) wins over the translation.
     Mirrors .github/scripts/make_audio.py. */
  var SPOKEN = [
    [/≈/g, " approximately equals "], [/≠/g, " is not equal to "],
    [/≤/g, " is less than or equal to "], [/≥/g, " is greater than or equal to "],
    [/→/g, " goes to "], [/∞/g, " infinity "], [/√/g, " the square root of "],
    [/∂/g, " partial "], [/Δ/g, " delta "], [/Σ/g, " the sum of "], [/∫/g, " the integral of "],
    [/π/g, " pi "], [/[·×]/g, " times "], [/−/g, " minus "],
    [/²/g, " squared "], [/³/g, " cubed "], [/′/g, " prime "],
    [/\^/g, " to the power of "], [/=/g, " equals "], [/\+/g, " plus "], [/\//g, " over "]
  ];
  function spokenText(node, math) {
    var clone = node.cloneNode(true);
    Array.prototype.forEach.call(clone.querySelectorAll("sup"), function (s) {
      s.textContent = " to the power of " + s.textContent + " ";
    });
    Array.prototype.forEach.call(clone.querySelectorAll("sub"), function (s) {
      s.textContent = " sub " + s.textContent + " ";
    });
    var t = clone.textContent || "";
    if (math) SPOKEN.forEach(function (p) { t = t.replace(p[0], p[1]); });
    return t.replace(/\s+/g, " ").trim();
  }

  function collect() {
    blocks = [];
    var nodes = main.querySelectorAll(READ_SELECTOR);
    var lastDt = "";
    Array.prototype.forEach.call(nodes, function (node) {
      if (node.closest(SKIP_CLOSEST)) return;
      /* Skip anything inside a collapsed <details> — if the reader hasn't
         opened the expanded analysis, they didn't ask to hear it either. */
      var det = node.closest("details");
      if (det && !det.open) return;
      if (node.offsetParent === null && node.getClientRects().length === 0) return;
      var text;
      if (node.classList.contains("expr")) {
        var plate = node.closest(".formula");
        if (plate && plate.querySelector(".expr-spoken")) return;   /* authored form wins */
        text = spokenText(node, true);
      } else {
        text = spokenText(node, false);
      }
      /* Symbol lists read as "P, the price of the bond", not two clipped blocks. */
      if (node.tagName === "DT") { lastDt = text; return; }
      if (node.tagName === "DD") { text = (lastDt ? lastDt + ", " : "") + text; lastDt = ""; }
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
