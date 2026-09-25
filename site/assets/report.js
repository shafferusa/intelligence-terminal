/* Logan's Daily Newspaper — report page behavior.

   Listen-to-text (Web Speech) and the generated-MP3 player were removed
   2026-09-25 at Logan's request ("no audio is needed"). This file still loads
   on every report, including the archive, and now carries only the SIE
   Program's quiz answer sheet and the SIE Study Guide progress ticks. Both
   are progressive enhancement: with JS off the pages read exactly the same.
*/

/* ---------- SIE Program quiz answer sheet ----------
   The SIE edition (data-slot="sie") ends with a quiz of real radio buttons.
   Answers are never on the page; Logan replies to the Telegram bot and the
   sie-inbox Action scores him from the key the routine stored. This turns
   his taps into the reply string ("SIE 5: BDAC-A…"), keeps it across reloads,
   and copies it in one tap. With JS off the radios still render and he types
   the letters himself — the instructions in the answer bar say how. */
(function () {
  "use strict";

  var quiz = document.querySelector(".quiz[data-quiz-day]");
  if (!quiz) return;

  var day = quiz.getAttribute("data-quiz-day");
  var items = quiz.querySelectorAll(".quiz-q");
  if (!day || !items.length) return;

  var KEY = "sie-answers:" + day;
  function lsGet(k) { try { return localStorage.getItem(k); } catch (e) { return null; } }
  function lsSet(k, v) { try { localStorage.setItem(k, v); } catch (e) {} }

  /* Restore earlier taps (same device). */
  var saved = {};
  try { saved = JSON.parse(lsGet(KEY) || "{}") || {}; } catch (e) { saved = {}; }
  Array.prototype.forEach.call(items, function (li, i) {
    var v = saved[String(i + 1)];
    if (!v) return;
    var input = li.querySelector('input[type="radio"][value="' + v + '"]');
    if (input) input.checked = true;
  });

  function current() {
    var letters = "";
    var map = {};
    var answered = 0;
    Array.prototype.forEach.call(items, function (li, i) {
      var c = li.querySelector('input[type="radio"]:checked');
      if (c) { letters += c.value; map[String(i + 1)] = c.value; answered++; }
      else letters += "-";
      /* Group in fives so a long string is checkable by eye. */
      if ((i + 1) % 5 === 0 && i + 1 < items.length) letters += " ";
    });
    return { text: "SIE " + day + ": " + letters, map: map, answered: answered };
  }

  var bar = quiz.querySelector(".answer-bar");
  if (!bar) {
    bar = document.createElement("div");
    bar.className = "answer-bar";
    quiz.appendChild(bar);
  }
  var count = document.createElement("p");
  var out = document.createElement("p");
  out.className = "answer-string";
  var actions = document.createElement("div");
  actions.className = "answer-actions";
  var copyBtn = document.createElement("button");
  copyBtn.type = "button";
  copyBtn.textContent = "Copy my answers";
  var open = document.createElement("a");
  open.className = "btn-link";
  open.href = "https://t.me/logannewspaperbot";
  open.textContent = "Open the bot";
  open.rel = "noopener";
  actions.appendChild(copyBtn);
  actions.appendChild(open);
  bar.insertBefore(actions, bar.firstChild);
  bar.insertBefore(out, bar.firstChild);
  bar.insertBefore(count, bar.firstChild);

  function render() {
    var c = current();
    count.textContent = c.answered + " of " + items.length + " answered";
    out.textContent = c.text;
    lsSet(KEY, JSON.stringify(c.map));
  }

  quiz.addEventListener("change", render);
  render();

  copyBtn.addEventListener("click", function () {
    var text = current().text;
    function done(ok) {
      copyBtn.textContent = ok ? "Copied — paste it to the bot" : "Select the line above and copy it";
      setTimeout(function () { copyBtn.textContent = "Copy my answers"; }, 2500);
    }
    if (navigator.clipboard && navigator.clipboard.writeText) {
      navigator.clipboard.writeText(text).then(function () { done(true); }, function () { done(false); });
    } else {
      try {
        var ta = document.createElement("textarea");
        ta.value = text;
        ta.setAttribute("readonly", "");
        ta.style.position = "absolute";
        ta.style.left = "-9999px";
        document.body.appendChild(ta);
        ta.select();
        var ok = document.execCommand("copy");
        document.body.removeChild(ta);
        done(ok);
      } catch (e) { done(false); }
    }
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
