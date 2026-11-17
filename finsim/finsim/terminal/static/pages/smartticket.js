/* The Smart Ticket (#/smart, Ctrl+F9).  FinSim 1.0.7.2, simplified on the owner's decision of 2026-11-11 (r1072 SIMPLIFY_PLAN.md
 * section 3): type what you want to trade in your own words; FinSim fills one standard order ticket per order and sends nothing itself.
 *
 * - The box #smart-text (1-3 lines): Enter reads it (POST /api/smart-ticket/parse: the fixed grammar, pure - it writes, fetches and
 *   subscribes nothing; no language model), Shift+Enter starts a new line, Esc clears it; the Parse button; examples with fictional
 *   names.  The text goes to the server only on Enter, Parse, a picked alternative or after a look-up the owner asked for.
 * - The reading: the words FinSim could not read are underlined; the instruction's flags (red !! blocks, amber ! look at it first,
 *   grey i how it was read) open their text, their alternatives and their action in place.
 * - The tickets: one card per order (#smart-cards, each card [data-i]) in the order written: book and account (another book than the
 *   selected one is said), the instrument in words, side, quantity, type, limit, stop or trail, time in force, GTD date, "sold as
 *   covered" (a covered call's claim), and its flags with their alternatives and actions (look it up, load the chain, the product's own
 *   ticket).  Orders written together (a strategy, a covered call that buys its shares, a collar, a roll, a group word, parentheses)
 *   sit under their group: its words, its check or its red flag, and the order to send them in (simplify round 5: worked out from the
 *   sides the cards hold now, by the server's rank - an in-place side edit moves it); an option strategy the order ticket's
 *   Strategies tab has a template for offers "Strategies tab" (one all-or-none order is built there, by the owner).
 * - In-place edits change that card only (no new reading): side among its kind's sides, quantity, type, prices, trail, time in force,
 *   GTD date, the covered tick (unticked: a plain call).  A new reading matches each card with the card the owner last saw: first one
 *   that reads exactly as before (every field read the same, even when the words around it changed or an order before it is gone):
 *   its edits, its opened mark and its notes stay; else one with the same words, book named and instrument: each edit on it is
 *   dropped and said on that card.  An edit on a card the new reading has no match for is said above the cards: an edit is never
 *   dropped unsaid.  A field that differs from the matched card as the owner last saw it shows "was X" (an account as "book .
 *   account").  Simplify round 2: a card's notes (the edits dropped on it, its "was" marks) stay - in the draft, across a navigation
 *   and through the next readings that read the card as before - until the owner edits that field or opens the card; the note above
 *   the cards stays through the next readings of the same text (a navigation's restore, Enter, a look-up, "Read for the <book>").
 * - The reading's book: the text is read for the book of the last reading (an order that names no book stays in it) until New ticket,
 *   even when another book is selected since (opening an order of another book selects that book): the page says so, and "Read for
 *   the <selected book>" reads it for the selected book (its cards then say what changed).
 * - Open in ticket (one card, one click; a click right after typing in a card is kept: the cards are painted again only once the
 *   pointer is up) hands the card's order to FinSim's order ticket docked on its account page (fresh: true, so
 *   nothing of an order typed there before rides along), and only when the reading is not in flight, the box holds the text last read,
 *   the draft was read again since it was restored, the instruction has no red flag and no unread word, the card and its group have no
 *   red flag, its order is complete and the order ticket takes its kind.  The owner previews and sends it there, exactly as an order
 *   typed there (its own preview, Submit, moved-price and large-order questions and lost-answer handling).  The card then says "opened
 *   in the ticket at HH:MM": the page never reads orders, so it never says that an order was sent.  Simplify round 8: a click acts
 *   here only as a click of its own (the second click of a double click never does), and the rest of the gesture that handed an order
 *   over (a double click's second click, its double click) is swallowed before the account page sees it - it never lands on that
 *   ticket's Submit or on a Markets row that would fill the ticket with another instrument.
 * - A product's own ticket (the card's "Open the FX desk", "Open the bond ticket" ...: a new trade there, prefilled with the side, the
 *   amount and the account) opens on the same gates less the order ticket's own (simplify round 5): the reading holds, the instruction
 *   has no red flag and no unread word, the card has no red flag but the one naming its own ticket (and "how many?" when no amount was
 *   typed: that ticket asks for it), its group none; a typed term that ticket cannot take is the card's red flag OWN_TICKET_TERMS.
 *   Held, the button is disabled with the reason beside it.
 * - Alternatives replace the flag's words in the text as read, then the text is read again; they apply only while the box holds
 *   exactly the text last read (else: press Enter to read the text again, then pick).
 * - The draft (the text, its reading and its book, the edits, the notes of dropped edits, the "was" marks and the opened marks) is kept in
 *   this browser only (T.store 'smart.draft.v2'), one per browser window (simplify round 3: in the window's sessionStorage; another
 *   window never saves over it; a new window starts from a copy of the draft saved last, said);
 *   restored, it says when it was read and opens nothing until the text is read again.  New ticket clears it (this window's); nothing
 *   else does.
 * Never here: a send, submit or send-all button, a confirm of its own, a request id, a request to an order, preview, cancel, replace
 * or strategies route, a PATCH, PUT or DELETE.  The page's only POST is the parse, and T.ticket.open is called only by one card's or
 * one group's click.  The dock never opens here (no account route).  Nothing here computes a price or an amount.  ASCII only.
 */
(function () {
  'use strict';
  var T = window.T;
  var PARSE = '/api/smart-ticket/parse';
  var STORE = 'smart.draft.v2';                               /* the draft of 1.0.7.2's first build ('smart.draft') is never read */
  var MAX_CHARS = 2000;
  var SIDES = { CASH: ['BUY', 'SELL', 'SELL_SHORT', 'BUY_TO_COVER'], OPTION: ['BTO', 'STO', 'BTC', 'STC'], FUTURE: ['BUY', 'SELL'] };
  var SIDE_WORDS = { BUY: 'BUY', SELL: 'SELL', SELL_SHORT: 'SELL SHORT', BUY_TO_COVER: 'BUY TO COVER', BTO: 'BTO', STO: 'STO', BTC: 'BTC', STC: 'STC' };
  var SIDE_TITLE = { BUY: 'buy', SELL: 'sell', SELL_SHORT: 'sell short (a locate and a borrow come first)', BUY_TO_COVER: 'buy to cover', BTO: 'buy to open',
                     STO: 'sell to open', BTC: 'buy to close', STC: 'sell to close' };
  var TYPES = [['MARKET', 'MKT'], ['LIMIT', 'LMT'], ['STOP', 'STP'], ['STOP_LIMIT', 'STP LMT'], ['TRAILING_STOP', 'TRAIL']];
  var TIFS = [['DAY', 'DAY'], ['GTC', 'GTC'], ['GTD', 'GTD'], ['NEXT_SESSION', 'next session']];
  var SEV = { blocker: 0, check: 1, info: 2 };
  var SEV_MARK = { blocker: '!! ', check: '! ', info: 'i ' };
  var SEV_TITLE = { blocker: 'blocks this order', check: 'look at this first', info: 'how it was read' };
  /* the kinds the order ticket takes (the others have their own ticket: their card offers it as an action) */
  var TICKET_TAKES = { EQUITY: 1, ETF: 1, ADR: 1, REIT: 1, PREFERRED: 1, OPTION: 1, FUTURE: 1 };
  /* the fields of an order a card hands over, compared for "was X", for an edit kept on a new reading and for the opened mark */
  var FIELDS = ['account_id', 'instrument_id', 'side', 'qty', 'order_type', 'limit_px', 'stop_px', 'trail_amt', 'trail_pct', 'tif', 'gtd_date', 'covered'];
  var FIELD_WORDS = { account_id: 'account', instrument_id: 'instrument', side: 'side', qty: 'qty', order_type: 'type', limit_px: 'limit', stop_px: 'stop',
                      trail_amt: 'trail', trail_pct: 'trail %', tif: 'TIF', gtd_date: 'GTD date', covered: 'covered' };
  /* fictional names only (ACME, WIDG): a click puts the example in the box; nothing is read until Enter or Parse */
  var EXAMPLES = ['buy 200 ACME limit 225, sell 2 ACME Dec 18 2026 240 calls, and buy 50 WIDG at market in the Fund book',
                  'buy 1 ACME Dec 18 2026 240/250 call spread',
                  'covered call 200 ACME Dec 18 2026 240',
                  'in the Fund book: buy 100 ACME; buy 100 WIDG gtc'];
  var st = null;                                               /* the mounted page's state (null while another page shows) */
  var ui = null;
  /* simplify round 1: an edit's repaint waits while a pointer is down, so the click that took the focus out of the box (Open in ticket
     right after typing) lands on the button it was pressed on, never on a repainted one */
  var pointerDown = false, repaintDue = null;
  function pointerDone() {
    pointerDown = false;
    var S = repaintDue; repaintDue = null;
    if (S) { setTimeout(function () { paintKeepFocus(S); }, 0); }      /* after the click */
  }
  document.addEventListener('pointerdown', function () { pointerDown = true; }, true);
  document.addEventListener('pointerup', pointerDone, true);
  document.addEventListener('pointercancel', pointerDone, true);
  window.addEventListener('blur', pointerDone);
  /* simplify round 8 (R-1): the rest of the gesture that handed an order over is dropped.  A hand-off moves to the account's page, whose
     docked ticket (or the FX desk, the bond or the private ticket) then lies under the pointer: its Submit armed once the preview
     answers, a Markets row whose double click fills the ticket with another instrument.  The second click of a double click (a third
     ...) would land there.  From a hand-off until the next press that starts a new gesture (a click count of 1), every press, release,
     click and double click with a click count above 1 is swallowed before any handler sees it (capture, on the window) */
  var handGesture = false;
  function dropGestureRest(e) {
    if (!handGesture) { return; }
    if (!(e.detail > 1)) { if (e.type === 'mousedown') { handGesture = false; } return; }
    e.preventDefault(); e.stopImmediatePropagation();
  }
  ['mousedown', 'mouseup', 'click', 'dblclick'].forEach(function (t) { window.addEventListener(t, dropGestureRest, true); });

  /* ---------------------------------------------------------------- small helpers -------------------------------- */
  function esc(s) { return T.esc(s == null ? '' : s); }
  function clone(o) { return o == null ? o : JSON.parse(JSON.stringify(o)); }
  function sorted(o) {
    if (Array.isArray(o)) { return o.map(sorted); }
    if (o && typeof o === 'object') { var out = {}; Object.keys(o).sort().forEach(function (k) { out[k] = sorted(o[k]); }); return out; }
    return o;
  }
  function sigOf(o) { return o == null ? null : JSON.stringify(sorted(o)); }
  function same(a, b) { return String(a == null ? '' : a) === String(b == null ? '' : b); }
  function bookQ(book) { return book ? '?book=' + encodeURIComponent(book) : ''; }
  function plural(n, one, many) { return n + ' ' + (n === 1 ? one : (many || one + 's')); }
  function short(s, n) { s = String(s == null ? '' : s); return s.length > n ? s.slice(0, n - 3).replace(/\s+\S*$/, '') + ' ...' : s; }
  function kindGroup(kind) { var k = String(kind || '').toUpperCase(); return k === 'OPTION' ? 'OPTION' : (k === 'FUTURE' ? 'FUTURE' : 'CASH'); }
  function isPx(v) { return v != null && /^\d+(\.\d+)?$/.test(String(v)) && Number(v) > 0; }
  function cleanPx(v) { return String(v == null ? '' : v).trim().replace(/^\$/, '').replace(/,/g, ''); }
  function bookName(code) {
    var b = (T.state.books || []).filter(function (x) { return x && x.book_code === code; })[0];
    return b ? (b.name || b.book_code) : (code || 'no book');
  }
  function nowHm() { return T.fmt.hm(Math.floor(T.clock.now())); }
  function instOf(leg) { return (leg && leg.instrument) || {}; }
  function sevOf(f) { return SEV[f && f.severity] != null ? f.severity : 'info'; }
  function bySeverity(list) {
    return (list || []).map(function (f, i) { return { f: f, i: i }; })
      .sort(function (a, b) { return (SEV[sevOf(a.f)] - SEV[sevOf(b.f)]) || (a.i - b.i); }).map(function (x) { return x.f; });
  }
  function firstBlocker(list) { for (var i = 0; i < (list || []).length; i++) { if (list[i] && list[i].severity === 'blocker') { return list[i]; } } return null; }
  function instrBlocker(S) { return S.parsed ? firstBlocker(S.parsed.flags || []) : null; }

  /* ---------------------------------------------------------------- the cards ------------------------------------ */
  /* what the server read for an order: the leg's fields; the claim of a covered call is on its request */
  function readOf(leg) {
    var i = instOf(leg); var q = leg.request || {};
    return { account_id: leg.account_id || null, instrument_id: q.instrument_id || i.id || null, side: leg.side || null, qty: leg.qty,
             order_type: leg.order_type || null, limit_px: leg.limit_px, stop_px: leg.stop_px, trail_amt: leg.trail_amt, trail_pct: leg.trail_pct,
             tif: leg.tif || null, gtd_date: leg.gtd_date || null, covered: !!q.covered };
  }
  /* an order as the ticket would take it: the fields its type does not use dropped; the claim only on a call sold to open */
  function normOrder(r) {
    if (/^\d+$/.test(String(r.qty))) { r.qty = Number(r.qty); }
    var t = r.order_type;
    if (t !== 'LIMIT' && t !== 'STOP_LIMIT') { delete r.limit_px; }
    if (t !== 'STOP' && t !== 'STOP_LIMIT') { delete r.stop_px; }
    if (t !== 'TRAILING_STOP') { delete r.trail_amt; delete r.trail_pct; }
    if (r.tif !== 'GTD') { delete r.gtd_date; }
    if (r.side !== 'STO' || !r.covered) { delete r.covered; }
    Object.keys(r).forEach(function (k) { if (r[k] == null || r[k] === '') { delete r[k]; } });
    return r;
  }
  function effWith(c, edits) {
    var r = clone(c.read); var e = edits || {};
    Object.keys(e).forEach(function (k) { r[k] = e[k]; });
    return normOrder(r);
  }
  /* the order a card would hand over now: what was read, with the owner's edits on top */
  function effOf(S, c) { return effWith(c, S.edits[c.key]); }
  /* the book the words name for an order ('' when they name none and the reading's book was taken: BOOK_SELECTED) */
  function typedBook(leg) {
    return (leg.flags || []).some(function (f) { return f && f.code === 'BOOK_SELECTED'; }) ? '' : String(leg.book || '');
  }
  /* one card per order of the reading, in the order written.  Its key: the words, the book they name and the n-th time those words
     appear with that book (unique in a reading: the draft keys the edits and the opened marks by it); readSig: every field read */
  function cardsOf(p) {
    var groups = {}; var seen = {};
    (p.combos || []).forEach(function (g) { if (g && g.id != null) { groups[String(g.id)] = g; } });
    return (p.legs || []).map(function (leg, i) {
      var place = String(leg.clause || '') + '|' + typedBook(leg);
      seen[place] = (seen[place] || 0) + 1;
      var read = readOf(leg);
      return { i: i, n: Number(leg.n), leg: leg, place: place, key: place + '|' + seen[place], read: read, readSig: sigOf(read),
               group: leg.combo != null ? (groups[String(leg.combo)] || null) : null, was: {}, dropped: [] };
    });
  }
  /* a new reading (simplify round 1): each card is matched with the card the owner last saw.  First a card that reads exactly as
     before - every field read the same: the same card, else the first other one (the words around it may have changed, an order
     before it may be gone) - and it keeps the owner's edits and its opened mark.  Then a card with the same words, the same book
     named and the same instrument, read otherwise: each edit on it is dropped and said on that card.  "was X": a field that differs
     from the matched card as the owner last saw it (a card matched with none has none).  An edit on a card the new reading has no
     match for is said above the cards (S.lost): an edit is never dropped unsaid.  Simplify round 2: a card that reads as before keeps
     the notes it showed (dropped edits, "was" marks) until the owner edits that field or opens it; again: the same text read again,
     so the note above the cards keeps what it said */
  function adopt(S, p, again) {
    var old = S.cards || [], oldEdits = S.edits || {}, oldOpened = S.opened || {};
    var cards = cardsOf(p), used = [], match = [], edits = {}, opened = {}, lost = [];
    function pass(test) {
      cards.forEach(function (c, i) {
        if (match[i]) { return; }
        for (var k = 0; k < old.length; k++) { if (!used[k] && test(old[k], c)) { used[k] = true; match[i] = old[k]; return; } }
      });
    }
    pass(function (o, c) { return o.key === c.key && o.readSig === c.readSig; });
    pass(function (o, c) { return o.readSig === c.readSig; });
    pass(function (o, c) { return o.place === c.place && same(o.read.instrument_id, c.read.instrument_id); });
    cards.forEach(function (c, i) {
      var o = match[i]; if (!o) { return; }
      var e = oldEdits[o.key]; var seen = effWith(o, e);
      if (o.readSig === c.readSig) {
        if (e) { edits[c.key] = e; }
        c.dropped = clone(o.dropped || []); c.was = clone(o.was || {});
      } else if (e) {
        Object.keys(e).forEach(function (k) { c.dropped.push({ field: k, edit: e[k], now: c.read[k] }); });
      }
      var now = effWith(c, edits[c.key]);
      var op = oldOpened[o.key]; if (op && op.sig === sigOf(now)) { opened[c.key] = op; }
      FIELDS.forEach(function (k) { if (!same(seen[k], now[k])) { c.was[k] = seen[k] == null ? '' : seen[k]; } });
    });
    old.forEach(function (o, k) {
      var e = oldEdits[o.key]; if (used[k] || !e) { return; }
      Object.keys(e).forEach(function (f) { lost.push({ n: o.n, clause: String(o.leg.clause || ''), field: f, edit: e[f] }); });
    });
    S.cards = cards; S.edits = edits; S.opened = opened; S.lost = (again ? S.lost || [] : []).concat(lost);
  }
  function sidesOf(c) { return SIDES[kindGroup(instOf(c.leg).kind)] || SIDES.CASH; }
  function orderValidity(c, r) {
    if (!r.side || sidesOf(c).indexOf(r.side) < 0) { return 'choose the side'; }
    if (!/^\d+$/.test(String(r.qty)) || Number(r.qty) <= 0) { return 'enter a whole quantity above 0'; }
    var t = r.order_type;
    if ((t === 'LIMIT' || t === 'STOP_LIMIT') && !isPx(r.limit_px)) { return 'enter a limit price'; }
    if ((t === 'STOP' || t === 'STOP_LIMIT') && !isPx(r.stop_px)) { return 'enter a stop price'; }
    if (t === 'TRAILING_STOP' && !isPx(r.trail_amt) && !isPx(r.trail_pct)) { return 'enter a trail amount, or a percent ending in %'; }
    if (r.tif === 'GTD' && !/^\d{4}-\d{2}-\d{2}$/.test(String(r.gtd_date || ''))) { return 'pick the GTD date'; }
    return null;
  }
  /* the reading itself: in flight, restored and not read again, or the text in the box changed since it was read (null: it holds) */
  function readBlock(S) {
    if (S.parsing) { return 'still reading the text'; }
    if (!S.parsed) { return 'nothing read yet: type what you want to trade and press Enter'; }
    if (S.restored) { return 'restored from this browser: press Enter to read the text again first (orders may have been sent since)'; }
    if (ui && ui.bar.value !== S.parsedFrom) { return 'the text changed since it was read: press Enter to read it again'; }
    return null;
  }
  /* the reading as a whole: it holds, and the instruction has no red flag and no unread word (null: no card is held by it) */
  function textBlock(S) {
    var why = readBlock(S); if (why) { return why; }
    var ib = instrBlocker(S); if (ib) { return 'the instruction has a red flag: ' + (ib.text || 'see the chips under the text'); }
    if ((S.parsed.unparsed || []).length) { return 'FinSim could not read the underlined words: change or remove them, then press Enter'; }
    return null;
  }
  /* why a card cannot open in the ticket now (null: it can) - every gate of 7b634e2's submit, kept */
  function openBlock(S, c) {
    var why = textBlock(S); if (why) { return why; }
    var b = firstBlocker(c.leg.flags); if (b) { return b.text || 'a red flag'; }
    var gb = c.group ? firstBlocker(c.group.flags) : null; if (gb) { return 'its group has a red flag: ' + (gb.text || 'see the group'); }
    if (!c.leg.request) { return 'not complete: see its flags'; }
    var o = effOf(S, c);
    var v = orderValidity(c, o); if (v) { return v; }
    var k = String(instOf(c.leg).kind || '').toUpperCase();
    if (!TICKET_TAKES[k]) { return 'the order ticket does not take ' + (k ? k.toLowerCase() : 'this kind of') + ' orders: use its own ticket'; }
    if (!o.account_id || !o.instrument_id) { return 'no account or instrument read'; }
    return null;
  }
  /* simplify round 5: why a card's "Open the FX desk" (bond, private-markets ...) cannot open the product's own ticket now (null: it
     can) - the gates of Open in ticket less the order ticket's own: the reading holds, the instruction has no red flag and no unread
     word, the card has no red flag but the one that names its own ticket and MISSING_QTY (no amount typed: that ticket opens with its
     amount box empty and asks for it there - nothing typed is dropped; a typed term it cannot take is a red flag of its own:
     OWN_TICKET_TERMS) and its group has none (words asking for one order hold every order of the group) */
  var OWN_OPENS_ANYWAY = { KIND_OWN_TICKET: 1, MISSING_QTY: 1 };
  function ownBlock(S, c) {
    var why = textBlock(S); if (why) { return why; }
    if (!c) { return 'open it from its order\'s card'; }
    var b = firstBlocker((c.leg.flags || []).filter(function (f) { return f && !OWN_OPENS_ANYWAY[f.code]; })); if (b) { return b.text || 'a red flag'; }
    /* simplify round 6 (R-5): a hand-off with no account opens nothing (openOwnTicket): held with the reason, never pressable (the
       server's ACCOUNT_NOT_IN_BOOK names the account the book lacks) */
    var own = (c.leg.flags || []).filter(function (f) { return f && f.code === 'KIND_OWN_TICKET' && f.action && f.action.ticket; })[0];
    if (own && !(own.action.ticket.account_id || c.leg.account_id)) { return 'no account of this book opens its ticket: trade it in a book that has one'; }
    var gb = c.group ? firstBlocker(c.group.flags) : null; if (gb) { return 'its group has a red flag: ' + (gb.text || 'see the group'); }
    return null;
  }
  /* simplify round 5: the order to send a group's tickets in, from the sides its cards hold NOW (an in-place side edit moves it), by
     the server's rank (smart_ticket._send_rank): purchases first (shares, then options), then sales to close, sales to open, share
     sales and short sales, an order with no side last; ties in the order written */
  var SEND_RANK = { BUY: 0, BUY_TO_COVER: 0, BTO: 1, BTC: 1, STC: 2, STO: 3, SELL: 4, SELL_SHORT: 4 };
  function sendOrder(S, g) {
    var id = String(g && g.id);
    return (S.cards || []).filter(function (x) { return x.group && String(x.group.id) === id; }).map(function (x) {
      var side = effOf(S, x).side;
      return { n: Number(x.n), r: Object.prototype.hasOwnProperty.call(SEND_RANK, side) ? SEND_RANK[side] : 5 };
    }).sort(function (a, b) { return (a.r - b.r) || (a.n - b.n); }).map(function (x) { return x.n; });
  }
  function canEdit(S, c) { return !!(c && c.leg.request && !S.parsing); }

  /* ---------------------------------------------------------------- the draft (this browser window only) ---------- */
  /* simplify round 3: each browser window keeps its OWN draft, in its sessionStorage (its navigations and reloads keep it; a window
     opened as a copy of this one starts with a copy), so another window showing the Smart Ticket never saves its draft over this
     one's: no edit, note or "opened" mark of this window is dropped by another.  T.store STORE holds what the windows share: the
     draft saved last in this browser (a new window starts from a copy of it, said) and the token of its drafts (gen: when it is
     removed - site data cleared - every window's draft is dropped with it, and a window's draft from before is never read).
     Where sessionStorage cannot be used (a page with no origin) the window's draft lives as long as the page. */
  var WIN_KEY = 'finsim.smart.draft.window';
  var winDraft = null;                                        /* this page's own copy, when sessionStorage cannot be used */
  function shared() { var m = T.store.get(STORE, null); return m && m.v === 3 && typeof m.gen === 'string' && m.gen ? m : null; }
  function ownGet() {
    try { var v = window.sessionStorage.getItem(WIN_KEY); return v ? JSON.parse(v) : null; } catch (e) { return winDraft; }
  }
  function ownSet(d) {
    winDraft = d;
    try { window.sessionStorage.setItem(WIN_KEY, JSON.stringify(d)); } catch (e) { /* this page keeps it (winDraft) */ }
  }
  /* the notes each card shows (the edits a reading dropped on it, its "was" marks), by card key: kept in the draft (simplify round 2) */
  function notesOf(S) {
    var out = {};
    (S.cards || []).forEach(function (c) { if (c.dropped.length || Object.keys(c.was).length) { out[c.key] = { dropped: c.dropped, was: c.was }; } });
    return out;
  }
  function saveState(S) {
    if (!S) { return; }
    var m = shared() || { v: 3, gen: Date.now().toString(36) + Math.random().toString(36).slice(2, 10) };
    var d = { v: 3, gen: m.gen, text: S.text, parsedFrom: S.parsedFrom, parsed: S.parsed, edits: S.edits, opened: S.opened, lost: S.lost, notes: notesOf(S),
              saved: Date.now() };
    ownSet(d);                                                /* this window's draft: the only one it restores */
    m.last = d;
    T.store.set(STORE, m);                                    /* the draft saved last in this browser: where a new window starts */
  }
  function restore(S) {
    var m = shared(); if (!m) { return false; }               /* nothing saved since this browser's drafts were cleared */
    var d = ownGet();
    S.copied = false;
    if (!d || d.v !== 3 || d.gen !== m.gen) { d = m.last; S.copied = true; }      /* a new window: a copy of the draft saved last */
    if (!d || d.v !== 3) { return false; }
    S.text = typeof d.text === 'string' ? d.text : ''; S.savedAt = d.saved || null;
    if (d.parsed && Array.isArray(d.parsed.legs) && typeof d.parsedFrom === 'string') {
      S.parsed = d.parsed; S.parsedFrom = d.parsedFrom; S.restored = true;
      S.cards = cardsOf(S.parsed);
      var keys = {}; S.cards.forEach(function (c) { keys[c.key] = c; });
      var ed = d.edits && typeof d.edits === 'object' ? d.edits : {}, op = d.opened && typeof d.opened === 'object' ? d.opened : {};
      Object.keys(ed).forEach(function (k) { if (keys[k] && ed[k] && typeof ed[k] === 'object') { S.edits[k] = ed[k]; } });
      Object.keys(op).forEach(function (k) { if (keys[k] && op[k] && op[k].sig === sigOf(effOf(S, keys[k]))) { S.opened[k] = op[k]; } });
      S.lost = Array.isArray(d.lost) ? d.lost.filter(function (x) { return x && typeof x === 'object' && typeof x.field === 'string'; }) : [];
      var nt = d.notes && typeof d.notes === 'object' ? d.notes : {};
      S.cards.forEach(function (c) {
        var x = nt[c.key]; if (!x || typeof x !== 'object') { return; }
        if (Array.isArray(x.dropped)) { c.dropped = x.dropped.filter(function (y) { return y && typeof y === 'object' && typeof y.field === 'string'; }); }
        if (x.was && typeof x.was === 'object') { FIELDS.forEach(function (k) { if (Object.prototype.hasOwnProperty.call(x.was, k)) { c.was[k] = x.was[k]; } }); }
      });
    }
    return true;
  }

  /* ---------------------------------------------------------------- reading the text ------------------------------ */
  /* the text is read for the book of the last reading (an order that names no book stays there) until New ticket; forBook: the
     owner's "Read for the <selected book>" */
  function parse(textOverride, forBook) {
    var S = st; if (!S || S.dead || !ui) { return; }
    var text = textOverride != null ? String(textOverride) : ui.bar.value;
    if (!text.trim()) { say(S, 'type what you want to trade first (a fictional example: buy 100 ACME limit 50)', 'err'); return; }
    if (T.modulesKnown() && !T.hasModule('smart_ticket')) { say(S, 'the Smart Ticket\'s reader is not in this build: nothing can be read', 'err'); return; }
    var seq = ++S.pseq;
    var book = forBook || (S.parsed && S.parsed.selected_book) || T.book.code || null;
    S.parsing = true; say(S, 'reading ...'); paint(S);
    var body = { text: text }; if (book) { body.book = book; }
    T.api.post(PARSE + bookQ(book), body, { retries: 0 }).then(function (ans) {
      if (S.dead || seq !== S.pseq) { return; }
      S.parsing = false;
      if (!ans || typeof ans !== 'object' || !Array.isArray(ans.legs)) { say(S, 'the reader gave an answer this page cannot show', 'err'); paint(S); return; }
      adopt(S, ans, text === S.parsedFrom);
      S.parsed = ans; S.parsedFrom = text; S.text = ui.bar.value; S.restored = false; S.expand = null;
      saveState(S); paint(S);
      var c = ans.counts || {}; var n = Number(c.legs != null ? c.legs : ans.legs.length);
      say(S, 'read ' + plural(n, 'order') + (c.combos ? ' (' + plural(Number(c.combos), 'group') + ' of separate tickets)' : '') +
          (c.blockers ? ': ' + plural(Number(c.blockers), 'red flag') + ' to fix (click a chip)' : (c.checks ? ': ' + plural(Number(c.checks), 'amber flag') + ' to look at' : '')) +
          '. Open each order in the ticket and send it there; nothing is sent from this page.', c.blockers ? 'err' : 'ok');
    }, function (e) {
      if (S.dead || seq !== S.pseq) { return; }
      S.parsing = false; paint(S);
      var msg = 'could not read it: ' + T.api.describe(e);
      say(S, msg, 'err'); T.ui.toast(msg, 'err', 5000);
      console.warn('smart ticket parse', e && e.status, e && e.code);
    }).then(null, function (x) {                              /* an answer this page failed to show: said, never a page stuck reading */
      console.error('smart ticket: the answer could not be shown', x);
      if (!S.dead && seq === S.pseq) { S.parsing = false; S.parsed = null; S.cards = []; paint(S); say(S, 'the answer could not be shown: ' + (x && x.message || x), 'err'); }
    });
  }
  /* an alternative replaces the words its flag names (the choice's span, else the flag's, else its order's or group's words) in the text
     as read, then the text is read again - only while the box holds exactly the text last read: the server checked the alternative
     against that text (every other order keeps its reading), never against words changed since */
  function choose(S, f, ch, span) {
    if (S.parsing) { say(S, 'still reading the text: pick once it is read', 'err'); return; }
    var read = S.parsed && typeof S.parsed.text === 'string' ? S.parsed.text : null;
    if (read == null || ui.bar.value !== S.parsedFrom) { say(S, 'the text changed since it was read: press Enter to read the text again, then pick', 'err'); paint(S); return; }
    var sp = Array.isArray(ch.span) ? ch.span : (Array.isArray(f.span) ? f.span : span);
    var a = sp ? Number(sp[0]) : -1, b = sp ? Number(sp[1]) : -1;
    if (!(a >= 0 && b >= a && b <= read.length) || typeof ch.clause !== 'string') { say(S, 'this alternative has no place in the text to go to', 'err'); return; }
    var next = read.slice(0, a) + ch.clause + read.slice(b);
    ui.bar.value = next; S.text = next;
    say(S, 'alternative "' + short(ch.label || ch.clause, 60) + '" put in the text; reading it again ...');
    parse(next);
  }
  function runAction(S, c, a, g) {
    if (!a) { return; }
    if (a.kind === 'resolve' && a.symbol) {
      say(S, 'looking ' + a.symbol + ' up ...');
      T.api.getReady('/api/instruments/resolve', { symbol: a.symbol }, 12).then(function () {
        if (!S.dead) { say(S, a.symbol + ' found; reading the text again ...', 'ok'); parse(); }
      }, function (e) { if (!S.dead) { say(S, 'the look-up of ' + a.symbol + ' failed: ' + T.api.describe(e), 'err'); } });
    } else if (a.kind === 'load_chain') {
      var id = a.instrument_id || (c && (instOf(c.leg).underlying_id || instOf(c.leg).id));
      if (!id) { say(S, 'no underlying named for the chain', 'err'); return; }
      say(S, 'loading the ' + id + ' option chain (Cboe, delayed) ...');
      T.api.getReady('/api/instruments/' + encodeURIComponent(id) + '/chain', null, 30).then(function () {
        if (!S.dead) { say(S, 'the ' + id + ' chain is loaded; reading the text again ...', 'ok'); parse(); }
      }, function (e) { if (!S.dead) { say(S, 'the ' + id + ' chain did not load: ' + T.api.describe(e), 'err'); } });
    } else if (a.kind === 'open_ticket') {
      openOwnTicket(S, c, a.ticket || {});
    } else if (a.kind === 'strategies_tab') {
      openStrategies(S, a, g);
    }
  }

  /* ---------------------------------------------------------------- the hand-off to the order ticket -------------- */
  /* the docked ticket of the account's page, filled as a NEW order (fresh: true: nothing of an order typed there before rides along);
     it opens once that page has rendered (the dock never opens here); the owner previews and sends there - nothing is sent here */
  function handTicket(S, acct, fields) {
    var f = Object.assign({}, fields || {}, { fresh: true, account_id: acct });
    var off = T.state.on('route', function (route) { off(); if (route && route.account === acct) { T.ticket.open(f); } });
    handGesture = true;                                       /* simplify round 8: the rest of this click's gesture never reaches that page */
    T.router.go(T.router.account(acct, 'markets'));
  }
  function openCard(S, c) {
    var why = openBlock(S, c); if (why) { say(S, '#' + c.n + ': ' + why, 'err'); paint(S); return; }
    var o = effOf(S, c);
    var refused = T.accountRefusal(o.account_id); if (refused) { say(S, refused, 'err'); return; }
    var ins = instOf(c.leg);
    var f = { instrument_id: o.instrument_id, kind: ins.kind || undefined, side: o.side, qty: o.qty, order_type: o.order_type, tif: o.tif };
    ['limit_px', 'stop_px', 'trail_amt', 'trail_pct', 'gtd_date'].forEach(function (k) { if (o[k] != null) { f[k] = o[k]; } });
    /* simplify round 4: the instrument's facts as the server read them (an option's right, underlying, expiry, strike; the lot), so the
       ticket knows a call before its own look-up answers - a call sold as covered on any listed root keeps its claim on the first open */
    if (ins.id && String(ins.id) === String(o.instrument_id)) {
      ['right', 'underlying_id', 'expiry', 'strike', 'lot'].forEach(function (k) { if (ins[k] != null && ins[k] !== '') { f[k] = ins[k]; } });
    }
    if (o.covered) { f.covered = true; }
    S.opened[c.key] = { at: Math.floor(T.clock.now()), sig: sigOf(o) };
    c.dropped = []; c.was = {};                               /* simplify round 2: opened with these terms - the owner has seen its notes */
    S.text = ui.bar.value; saveState(S);
    handTicket(S, o.account_id, f);
  }
  /* "Strategies tab" of a group (or of its flag): the ticket of the group's account on its underlying, the hedge panel on its Strategies
     tab - one all-or-none order of options is built there by the owner, from its templates.  Simplify round 7: held as every hand-off
     is by the instruction (textBlock: the reading holds, no red flag of the instruction, no unread word - an account or book the owner
     wrote and FinSim did not read never opens the ticket of another); the group's own flags (words asking for one order, separate
     tickets, a net price) are what the tab is for and never hold it */
  function openStrategies(S, a, g) {
    var why = textBlock(S); if (why) { say(S, why, 'err'); paint(S); return; }
    var acct = (a && a.account_id) || (g && g.account_id) || null, und = (a && a.instrument_id) || (g && g.underlying_id) || null;
    if (!acct || !und) { say(S, 'this group names no single account and underlying for the Strategies tab', 'err'); return; }
    var refused = T.accountRefusal(acct); if (refused) { say(S, refused, 'err'); return; }
    S.text = ui.bar.value; saveState(S);
    handTicket(S, acct, { instrument_id: und, hedge: true, hedge_tab: 'strategies' });
  }
  /* the product's own ticket (a bond, an FX pair, a swap ...), prefilled with what was read - only on a reading Open in ticket would
     open (ownBlock, simplify round 5: before, unread words, a red flag of the card, of its group or of the instruction never held it) */
  function openOwnTicket(S, c, fields) {
    var why = ownBlock(S, c); if (why) { say(S, (c ? '#' + c.n + ': ' : '') + why, 'err'); paint(S); return; }
    var acct = fields.account_id || (c && c.leg.account_id) || null;
    if (!acct) { say(S, 'this order has no account yet: open the account page and use its ticket', 'err'); return; }
    var refused = T.accountRefusal(acct); if (refused) { say(S, refused, 'err'); return; }
    S.text = ui.bar.value; saveState(S);
    handTicket(S, acct, fields);
  }

  /* ---------------------------------------------------------------- in-place edits -------------------------------- */
  function onEdit(e) {
    var t = e.target; var S = st;
    if (!S || S.dead || !t || !t.dataset || !t.dataset.edit) { return; }
    var el = t.closest('[data-i]'); var c = el ? S.cards[Number(el.dataset.i)] : null;
    if (!c || !canEdit(S, c)) { paint(S); return; }
    var f = t.dataset.edit; var v = t.type === 'checkbox' ? !!t.checked : String(t.value == null ? '' : t.value).trim();
    var x = Object.assign({}, S.edits[c.key] || {});
    if (f === 'side') {
      if (sidesOf(c).indexOf(v) < 0) { paint(S); return; }
      x.side = v;
    } else if (f === 'qty') { x.qty = /^\d+$/.test(v) ? Number(v) : v; }
    else if (f === 'order_type') { x.order_type = v; }
    else if (f === 'limit_px' || f === 'stop_px') { x[f] = cleanPx(v); }
    else if (f === 'trail') {
      var pct = /%$/.test(v); var n = cleanPx(v.replace(/%$/, ''));
      x.trail_amt = pct ? null : n; x.trail_pct = pct ? n : null;
    } else if (f === 'tif') { x.tif = v; }
    else if (f === 'gtd_date') { x.gtd_date = v; }
    else if (f === 'covered') { x.covered = v; }
    else { paint(S); return; }
    Object.keys(x).forEach(function (k) { if (same(x[k], c.read[k])) { delete x[k]; } });   /* back to what was read: no edit */
    if (Object.keys(x).length) { S.edits[c.key] = x; } else { delete S.edits[c.key]; }
    (f === 'trail' ? ['trail_amt', 'trail_pct'] : [f]).forEach(function (k) { delete c.was[k]; });
    c.dropped = c.dropped.filter(function (d) { return d.field !== f && !(f === 'trail' && /^trail_/.test(d.field)); });
    delete S.opened[c.key];                                   /* the terms opened in the ticket are not these any more */
    saveState(S);
    if (pointerDown) { repaintDue = S; }                      /* a press took the focus out of the box: painted once the pointer is up */
    else { setTimeout(function () { paintKeepFocus(S); }, 0); }   /* after the focus has moved on (a Tab out of the box) */
    say(S, '#' + c.n + ' edited (no new reading): open it in the ticket when it is ready');
  }

  /* ---------------------------------------------------------------- painting -------------------------------------- */
  function say(S, text, cls) {
    if (!S || S.dead || !ui) { return; }
    ui.status.textContent = text || ''; ui.status.className = 'smart-status' + (cls ? ' ' + cls : '');
  }
  function readbackHtml(p) {
    var text = String(p.text == null ? '' : p.text);
    var spans = (p.unparsed || []).map(function (u) { return u && u.span; }).filter(function (s) { return Array.isArray(s) && s[0] >= 0 && s[1] > s[0] && s[1] <= text.length; })
      .sort(function (a, b) { return a[0] - b[0]; });
    var h = '', at = 0;
    spans.forEach(function (s) { if (s[0] < at) { return; } h += esc(text.slice(at, s[0])) + '<u class="smart-unparsed" title="FinSim could not read these words">' + esc(text.slice(s[0], s[1])) + '</u>'; at = s[1]; });
    return h + esc(text.slice(at));
  }
  function chipHtml(f, w, fi) {
    var sev = sevOf(f);
    var more = (f.choices && f.choices.length) || f.action ? ' ...' : '';
    return '<button type="button" class="smart-chip sev-' + sev + '" data-act="flag" data-w="' + esc(w) + '" data-f="' + esc(fi) + '" title="' +
      esc(SEV_TITLE[sev] + ': ' + (f.text || '') + (more ? ' (click for the alternatives)' : '')) + '">' + esc(SEV_MARK[sev] + short(f.text || '', 38) + more) + '</button>';
  }
  /* the chips of a list of flags (sorted by severity); the grey ones without an alternative or action fold into one "i N" chip */
  function chipsHtml(list, w) {
    var h = '', infos = 0;
    list.forEach(function (f, i) { if (f.severity === 'info' && !(f.choices && f.choices.length) && !f.action) { infos++; return; } h += chipHtml(f, w, i); });
    if (infos) { h += '<button type="button" class="smart-chip sev-info" data-act="flag" data-w="' + esc(w) + '" data-f="info" title="how it was read (click)">i ' + infos + '</button>'; }
    return h;
  }
  function flagsOf(S, w) {
    if (w === 'top') { return S.parsed ? bySeverity(S.parsed.flags) : []; }
    if (w.charAt(0) === 'c') { var c = S.cards[Number(w.slice(1))]; return c ? bySeverity(c.leg.flags) : []; }
    if (w.charAt(0) === 'g') { var g = groupOf(S, w.slice(1)); return g ? bySeverity(g.flags) : []; }
    return [];
  }
  function groupOf(S, id) {
    var list = (S.parsed && S.parsed.combos) || [];
    for (var i = 0; i < list.length; i++) { if (list[i] && String(list[i].id) === String(id)) { return list[i]; } }
    return null;
  }
  /* the open flag of a card, a group or the instruction: its text, its alternatives (each one replaces the words and reads the text
     again; nothing is applied by itself; only while the box holds the text last read) and its action */
  function panelHtml(S, w, extra) {
    var x = S.expand; if (!x || x.w !== w) { return ''; }
    var list = flagsOf(S, w);
    if (x.f === 'info') {
      var h0 = '<div class="smart-flagbox sev-info">';
      list.forEach(function (f) { if (f.severity === 'info') { h0 += '<div>' + esc(f.text || '') + '</div>'; } });
      return h0 + (extra || '') + '</div>';
    }
    var f = list[Number(x.f)]; if (!f) { return ''; }
    var sev = sevOf(f); var stale = !!readBlock(S) && !S.restored;
    var h = '<div class="smart-flagbox sev-' + sev + '"><div>' + esc(f.text || '') + '</div>';
    var ch = (f.choices || []).filter(function (c) { return c && typeof c.clause === 'string'; });
    if (ch.length) {
      h += '<div class="smart-choices"><div class="muted">alternatives (each one replaces these words in the text and reads it again):</div>';
      ch.forEach(function (c, k) {
        h += '<div><button type="button" class="btn small" data-act="choice" data-w="' + esc(w) + '" data-f="' + esc(x.f) + '" data-c="' + k + '"' +
          (stale ? ' disabled title="the text changed since it was read: press Enter to read the text again, then pick"' : '') + '>' + esc(c.label || c.clause) + '</button> ' +
          '<span class="muted mono">' + esc(short(c.clause, 120)) + '</span></div>';
      });
      h += '</div>';
    }
    if (f.action && f.action.label) {
      /* simplify round 5: the product's own ticket is held as Open in ticket is (ownBlock), the reason beside the button; simplify
         round 7: the Strategies tab as the group's button is (textBlock) */
      var held = f.action.kind === 'open_ticket' ? ownBlock(S, w.charAt(0) === 'c' ? S.cards[Number(w.slice(1))] : null)
        : (f.action.kind === 'strategies_tab' ? textBlock(S) : null);
      h += '<div><button type="button" class="btn small" data-act="action" data-w="' + esc(w) + '" data-f="' + esc(x.f) + '"' +
        (held ? ' disabled title="' + esc(held) + '"' : '') + '>' + esc(f.action.label) + '</button>' +
        (held ? ' <span class="smart-why">' + esc(short(held, 110)) + '</span>' : '') + '</div>';
    }
    return h + (extra || '') + '</div>';
  }
  function valWords(k, v) {
    if (v == null || v === '') { return '-'; }
    if (k === 'side') { return SIDE_WORDS[v] || String(v); }
    if (k === 'covered') { return v ? 'sold as covered' : 'a plain call'; }
    if (k === 'order_type') { var t = TYPES.filter(function (x) { return x[0] === v; })[0]; return t ? t[1] : String(v); }
    if (k === 'account_id') { var ob = typeof T.accountOwner === 'function' ? T.accountOwner(v) : null; return (ob ? (ob.name || ob.book_code) + ' . ' : '') + T.accountName(v); }
    return String(v);
  }
  function wasHtml(c, keys) {
    var out = '';
    keys.forEach(function (k) { if (Object.prototype.hasOwnProperty.call(c.was, k)) { out += ' <span class="smart-was" title="as you last saw this order">was ' + esc(valWords(k, c.was[k])) + '</span>'; } });
    return out;
  }
  function selectHtml(field, list, value, label) {
    var h = '<select data-edit="' + field + '" aria-label="' + esc(label) + '">';
    if (!list.some(function (x) { return x[0] === value; })) { h += '<option value="" selected>choose</option>'; }
    list.forEach(function (x) { h += '<option value="' + esc(x[0]) + '"' + (x[0] === value ? ' selected' : '') + (x[2] ? ' title="' + esc(x[2]) + '"' : '') + '>' + esc(x[1]) + '</option>'; });
    return h + '</select>';
  }
  function inputHtml(field, value, cls, label, mode) {
    return '<input class="' + cls + '" data-edit="' + field + '" type="text" inputmode="' + (mode || 'decimal') + '" value="' + esc(value == null ? '' : value) + '" aria-label="' + esc(label) + '">';
  }
  function fieldHtml(label, inner, was) { return '<label class="smart-f"><span class="muted">' + esc(label) + '</span> ' + inner + (was || '') + '</label>'; }
  function fieldsHtml(S, c, o) {
    var t = o.order_type; var ed = canEdit(S, c); var h = '';
    var trail = o.trail_pct != null ? o.trail_pct + '%' : (o.trail_amt != null ? o.trail_amt : '');
    if (ed) {
      h += fieldHtml('side', selectHtml('side', sidesOf(c).map(function (s) { return [s, SIDE_WORDS[s], SIDE_TITLE[s]]; }), o.side, 'side of order ' + c.n), wasHtml(c, ['side']));
      h += fieldHtml('qty', inputHtml('qty', o.qty, 'smart-qty', 'quantity of order ' + c.n, 'numeric'), wasHtml(c, ['qty']));
      h += fieldHtml('type', selectHtml('order_type', TYPES, t, 'order type of order ' + c.n), wasHtml(c, ['order_type']));
      if (t === 'LIMIT' || t === 'STOP_LIMIT') { h += fieldHtml('limit', inputHtml('limit_px', o.limit_px, 'smart-px', 'limit price of order ' + c.n), wasHtml(c, ['limit_px'])); }
      if (t === 'STOP' || t === 'STOP_LIMIT') { h += fieldHtml('stop', inputHtml('stop_px', o.stop_px, 'smart-px', 'stop price of order ' + c.n), wasHtml(c, ['stop_px'])); }
      if (t === 'TRAILING_STOP') { h += fieldHtml('trail', inputHtml('trail', trail, 'smart-px', 'trail amount, or a percent ending in %, of order ' + c.n), wasHtml(c, ['trail_amt', 'trail_pct'])); }
      h += fieldHtml('TIF', selectHtml('tif', TIFS, o.tif, 'time in force of order ' + c.n), wasHtml(c, ['tif']));
      if (o.tif === 'GTD') { h += fieldHtml('GTD date', '<input type="date" data-edit="gtd_date" value="' + esc(o.gtd_date || '') + '" aria-label="good till date of order ' + c.n + '">', wasHtml(c, ['gtd_date'])); }
    } else {
      var px = (t === 'LIMIT' || t === 'STOP_LIMIT') && o.limit_px != null ? ' limit ' + o.limit_px : '';
      var sp = (t === 'STOP' || t === 'STOP_LIMIT') && o.stop_px != null ? ' stop ' + o.stop_px : (t === 'TRAILING_STOP' && trail !== '' ? ' trail ' + trail : '');
      h += '<span class="smart-f mono">' + esc(valWords('order_type', t) + px + sp + ' . ' + (o.tif || '-') + (o.tif === 'GTD' && o.gtd_date ? ' ' + o.gtd_date : '')) + '</span>' +
        wasHtml(c, ['side', 'qty', 'order_type', 'limit_px', 'stop_px', 'trail_amt', 'trail_pct', 'tif', 'gtd_date']);
    }
    if (c.read.covered && o.side === 'STO') {
      var und = instOf(c.leg).underlying_id || 'its';
      h += '<label class="smart-f smart-covered"><input type="checkbox" data-edit="covered"' + (o.covered ? ' checked' : '') + (ed ? '' : ' disabled') + ' aria-label="sold as covered"> ' +
        esc(o.covered ? 'sold as covered: the ticket places it only while ' + und + ' shares held in this account are free, and keeps them for the call until it is closed'
                      : 'a plain call (tick to sell it as covered by ' + und + ' shares held free in this account)') + '</label>' + wasHtml(c, ['covered']);
    }
    return h;
  }
  function cardHtml(S, c) {
    var leg = c.leg; var i = instOf(leg); var o = effOf(S, c); var why = openBlock(S, c);
    var other = !!(leg.book && T.book.code && leg.book !== T.book.code);
    var acct = leg.account_name || (leg.account_id ? T.accountName(leg.account_id) : 'no account');
    var opened = S.opened[c.key]; var edited = !!S.edits[c.key];
    var blocked = !!firstBlocker(leg.flags);
    var order = c.group ? sendOrder(S, c.group) : [];          /* simplify round 5: from the sides the cards hold now */
    var pos = order.indexOf(Number(c.n));
    var h = '<div class="smart-card' + (blocked ? ' smart-card-blocked' : '') + (opened ? ' smart-card-opened' : '') + '" data-i="' + c.i + '">';
    h += '<div class="smart-card-head"><span class="smart-num">#' + esc(c.n) + '</span> ' +
      (o.side ? '<b class="side-' + esc(o.side) + '" title="' + esc(SIDE_TITLE[o.side] || '') + '">' + esc(SIDE_WORDS[o.side] || o.side) + '</b> ' : '') +
      (o.qty != null ? esc(T.fmt.qty(o.qty)) + ' ' : '') + '<span class="smart-inst">' + esc(i.words || i.symbol || i.id || '?') + '</span>' +
      (i.listed === false && i.id ? ' <span class="muted" title="not confirmed listed: the ticket\'s preview checks it">(unverified)</span>' : '') +
      (c.group ? ' <span class="smart-tag" title="written together with other orders: separate tickets, each goes and fills on its own">' + esc(c.group.id) +
        (pos >= 0 ? ' . send ' + (pos + 1) + ' of ' + order.length : '') + '</span>' : '') +
      (edited ? ' <span class="smart-tag" title="changed on this card (no new reading)">edited</span>' : '') +
      (opened ? ' <span class="smart-opened" title="handed to the order ticket with these terms; whether it was sent is in Pending Trades">opened in the ticket at ' + esc(T.fmt.hm(opened.at)) + '</span>' : '') + '</div>';
    h += '<div class="smart-where' + (other ? ' smart-other' : '') + '" title="' + esc((leg.account_id || '') + (other ? ' - not the selected book' : '')) + '">' +
      esc((leg.book_name || bookName(leg.book)) + ' . ' + acct) + (other ? ' (not the selected book)' : '') + wasHtml(c, ['account_id', 'instrument_id']) + '</div>';
    h += '<div class="smart-clause muted" title="' + esc(leg.clause || '') + '">you wrote "' + esc(short(leg.clause || '', 90)) + '"</div>';
    h += '<div class="smart-fields">' + fieldsHtml(S, c, o) + '</div>';
    c.dropped.forEach(function (d) {
      h += '<div class="smart-dropped">your edit: ' + esc(FIELD_WORDS[d.field] || d.field) + ' ' + esc(valWords(d.field, d.edit)) + ' - dropped, read again as ' + esc(valWords(d.field, d.now)) + '</div>';
    });
    var chips = chipsHtml(bySeverity(leg.flags), 'c' + c.i);
    if (chips) { h += '<div class="smart-flags">' + chips + '</div>'; }
    h += panelHtml(S, 'c' + c.i, leg.inferred && leg.inferred.length && S.expand && S.expand.f === 'info' ? '<div class="muted">filled in by FinSim: ' + esc(leg.inferred.join('; ')) + '</div>' : '');
    h += '<div class="smart-card-foot"><button type="button" class="btn small primary" data-act="open" data-w="c' + c.i + '"' + (why ? ' disabled' : '') +
      ' title="' + esc(why || 'fill a new order in the order ticket of ' + T.accountName(o.account_id) + ': preview and send it there') + '">Open in ticket</button>' +
      (why ? ' <span class="smart-why">' + esc(short(why, 110)) + '</span>' : '') + '</div>';
    return h + '</div>';
  }
  function strategiesActionOf(g) {
    var list = (g && g.flags) || [];
    for (var i = 0; i < list.length; i++) { if (list[i] && list[i].action && list[i].action.kind === 'strategies_tab') { return list[i].action; } }
    return null;
  }
  function groupHtml(S, g, cards) {
    var w = 'g' + g.id; var tab = strategiesActionOf(g); var why = tab ? textBlock(S) : null;     /* simplify round 7: the hand-offs' gate */
    var order = sendOrder(S, g).map(function (n) { return '#' + n; }).join(', then ');   /* simplify round 5: the sides held now */
    var h = '<div class="smart-group" data-g="' + esc(g.id) + '"><div class="smart-group-head"><b>' + esc(g.id) + '</b> ' +
      esc((g.strategy ? 'the ' + g.strategy : 'orders written together') + ': "' + short(g.clause || '', 90) + '" . ' + plural(cards.length, 'separate ticket') +
          (order ? ' . send them in this order: ' + order : '')) + '</div>';
    var chips = chipsHtml(bySeverity(g.flags), w);
    h += '<div class="smart-flags">' + chips + (tab ? ' <button type="button" class="btn small" data-act="strategies" data-w="' + esc(w) + '"' + (why ? ' disabled' : '') +
      ' title="' + esc(why || 'the order ticket\'s Strategies tab on ' + (tab.instrument_id || '') + ': one all-or-none order of options, built there') + '">Strategies tab</button>' : '') + '</div>';
    h += panelHtml(S, w, '');
    h += '<div class="smart-grid smart-group-cards">' + cards.map(function (c) { return cardHtml(S, c); }).join('') + '</div>';
    return h + '</div>';
  }
  function paintCards(S) {
    var h = '', done = {};
    (S.cards || []).forEach(function (c) {
      if (!c.group) { h += cardHtml(S, c); return; }
      var id = String(c.group.id); if (done[id]) { return; }
      done[id] = true;
      h += groupHtml(S, c.group, S.cards.filter(function (x) { return x.group && String(x.group.id) === id; }));
    });
    var lost = S.lost || [];
    ui.lost.hidden = !lost.length;
    ui.lost.textContent = lost.length ? 'dropped with the new reading: ' + lost.map(function (d) {
      return 'your edit on #' + d.n + ' ("' + short(d.clause, 60) + '"): ' + (FIELD_WORDS[d.field] || d.field) + ' ' + valWords(d.field, d.edit);
    }).join('; ') + ' - no order of the new reading reads as that one did, nor has its words' : '';
    ui.cards.innerHTML = h || '<div class="empty">' + esc(S.parsed ? 'nothing to fill: the text named no order' : 'nothing read yet: type what you want to trade above and press Enter') + '</div>';
    S.gate = readBlock(S);
  }
  function paintHead(S) {
    var n = (S.cards || []).length; var k = Object.keys(S.opened || {}).length;
    ui.sub.textContent = 'selected book: ' + bookName(T.book.code) + ' . ' + (S.parsed ? plural(n, 'order') + ' read' + (k ? ' . ' + k + ' opened in the ticket' : '') : 'nothing read yet') +
      ' . the Smart Ticket sends nothing itself';
  }
  function paintRead(S) {
    var p = S.parsed;
    T.clear(ui.readback); T.clear(ui.iflags);
    if (!p) { ui.readback.hidden = true; ui.iflags.hidden = true; return; }
    ui.readback.hidden = false;
    var c = p.counts || {};
    ui.readback.appendChild(T.h('div', { class: 'smart-read-head' }, 'read' + (p.as_of ? ' at ' + T.fmt.time(p.as_of) + ' ET' : '') + ' for the ' + bookName(p.selected_book) +
      ' (grammar ' + (p.grammar || '?') + '): ' + plural(Number(c.legs != null ? c.legs : (p.legs || []).length), 'order') + (c.combos ? ', ' + plural(Number(c.combos), 'group') : '') +
      ' . ' + plural(Number(c.blockers || 0), 'red flag') + ' . ' + plural(Number(c.checks || 0), 'amber flag')));
    ui.readback.appendChild(T.h('div', { class: 'smart-read-text', html: readbackHtml(p) }));
    var chips = chipsHtml(bySeverity(p.flags), 'top');
    ui.iflags.hidden = !chips;
    ui.iflags.innerHTML = chips + panelHtml(S, 'top', '');
  }
  function paintNotes(S) {
    var dirty = !!(S.parsed && !S.parsing && ui.bar.value !== S.parsedFrom);
    ui.dirty.hidden = !dirty;
    ui.restored.hidden = !(S.restored && S.parsed);
    if (S.restored && S.parsed) {
      ui.restored.textContent = 'read at ' + (S.parsed.as_of ? T.fmt.hm(S.parsed.as_of) : '--:--') + ': orders may have been sent since; each ticket\'s own preview says what holds now - press Enter to read again';
    }
    ui.parseBtn.disabled = !!S.parsing;
    var rb = S.parsed ? S.parsed.selected_book : null, sb = T.book.code || null;
    ui.book.hidden = !(rb && sb && rb !== sb);
    T.clear(ui.book);
    if (!ui.book.hidden) {
      ui.book.appendChild(document.createTextNode('the last reading was for the ' + bookName(rb) + '; the ' + bookName(sb) + ' is selected now (opening an order of ' +
        'another book selects that book). Enter reads the text again for the ' + bookName(rb) + ': an order that names no book stays there. '));
      ui.book.appendChild(T.h('button', { type: 'button', class: 'btn small', id: 'smart-rebook', 'data-act': 'rebook', disabled: S.parsing ? true : null,
        title: 'read the text for the ' + bookName(sb) + ' instead: an order that names no book goes there (its card says what changed)' }, 'Read for the ' + bookName(sb)));
    }
  }
  /* the gates follow the box: what the text in it allows is painted again only when it changes (typing never rebuilds the cards) */
  function paintActions(S) {
    if (!S || S.dead || !ui) { return; }
    paintNotes(S);
    if (readBlock(S) !== S.gate) { paintRead(S); paintCards(S); }
  }
  function paint(S) {
    if (!S || S.dead || !ui) { return; }
    paintHead(S); paintRead(S); paintCards(S); paintNotes(S);
  }
  /* repaint and put the cursor back in the box it was in (an edit repaints its card's fields) */
  function paintKeepFocus(S) {
    if (!S || S.dead || !ui) { return; }
    var a = document.activeElement; var card = a && ui.cards.contains(a) ? a.closest('[data-i]') : null;
    var at = card && a.dataset ? { i: card.dataset.i, edit: a.dataset.edit || null, act: a.dataset.act || null } : null;
    paint(S);
    if (!at) { return; }
    var sel = '[data-i="' + at.i + '"] ' + (at.edit ? '[data-edit="' + at.edit + '"]' : (at.act ? '[data-act="' + at.act + '"]' : ''));
    var el = at.edit || at.act ? ui.cards.querySelector(sel) : null;
    if (el) { try { el.focus({ preventScroll: true }); } catch (e) { /* focus is a convenience */ } }
  }
  function onClick(e) {
    var S = st; if (!S || S.dead || !ui) { return; }
    if (e.detail > 1) { return; }                             /* simplify round 8: one click acts; the second of a double click never does */
    var b = e.target.closest('[data-act]'); if (!b || !ui.page.contains(b) || b.disabled) { return; }
    var act = b.dataset.act, w = b.dataset.w || '';
    var c = w.charAt(0) === 'c' ? S.cards[Number(w.slice(1))] : null;
    var g = w.charAt(0) === 'g' ? groupOf(S, w.slice(1)) : (c ? c.group : null);
    if (act === 'rebook') { parse(null, T.book.code || null); return; }
    if (act === 'open') { if (c) { handOff(e, b, function () { openCard(S, c); }); } return; }
    if (act === 'strategies') { if (g) { handOff(e, b, function () { openStrategies(S, strategiesActionOf(g), g); }); } return; }
    if (act === 'flag') {
      var fx = b.dataset.f;
      S.expand = S.expand && S.expand.w === w && String(S.expand.f) === String(fx) ? null : { w: w, f: fx };
      paint(S); return;
    }
    var f = flagsOf(S, w)[Number(b.dataset.f)]; if (!f) { return; }
    if (act === 'choice') {
      var ch = (f.choices || []).filter(function (x) { return x && typeof x.clause === 'string'; })[Number(b.dataset.c)];
      var span = c ? c.leg.span : (w.charAt(0) === 'g' && g ? g.span : null);
      if (ch) { choose(S, f, ch, span); }
      return;
    }
    if (act === 'action' && f.action) { handOff(e, b, function () { runAction(S, c, f.action, w.charAt(0) === 'g' ? g : null); }); }
  }
  /* simplify round 10 (the guard): each hand-off button (a card's Open in ticket, a group's Strategies tab, a flag's Open the FX desk /
     the bond or private ticket) acts through T.guard as its own control - a second activation of it inside the double-click time (an Enter
     pressed twice on it, a key held down) hands nothing over again, said; another card's button is its own; it sends nothing itself (the
     ticket it fills has its own Submit) */
  function handOff(e, b, run) {
    return T.guard('smart.handoff:' + (b.dataset.act || '') + ':' + (b.dataset.w || '') + ':' + (b.dataset.f || ''), e, run,
                   { label: String(b.textContent || 'the hand-off').trim(), not: 'not handed over', act: 'open it' });
  }

  /* ---------------------------------------------------------------- the page -------------------------------------- */
  function newTicketNow() {
    var S = st; if (!S || S.dead) { return; }
    S.pseq++; S.parsing = false; S.parsed = null; S.parsedFrom = null; S.cards = []; S.edits = {}; S.opened = {}; S.lost = []; S.restored = false; S.expand = null;
    S.text = ''; ui.bar.value = '';
    saveState(S);                                             /* simplify round 3: this window's draft only; another window keeps its own */
    paint(S); say(S, 'a new ticket: the text, its reading and your edits are cleared');
    try { ui.bar.focus(); } catch (e) { /* focus is a convenience */ }
  }
  function render(el, route) {
    unmount();
    void route;
    var S = st = { dead: false, text: '', parsed: null, parsedFrom: null, cards: [], edits: {}, opened: {}, lost: [], pseq: 0, parsing: false, restored: false,
                   expand: null, gate: null, savedAt: null, copied: false };
    var page = T.h('div', { class: 'smart-page' }); el.appendChild(page);
    ui = { page: page };
    ui.newBtn = T.h('button', { class: 'btn', id: 'smart-new', title: 'clear the text, its reading and your edits', onclick: newTicketNow }, 'New ticket');
    var head = T.pageHead(page, 'Smart Ticket', 'ticket', [ui.newBtn]);
    ui.sub = head.querySelector('.sub');
    ui.bar = T.h('textarea', { id: 'smart-text', rows: '3', maxlength: String(MAX_CHARS), spellcheck: 'false', autocomplete: 'off', 'data-search': '1',
                               'aria-label': 'what you want to trade', placeholder: 'type what you want to trade, e.g. buy 200 ACME limit 225, sell 2 ACME Dec 18 2026 240 calls' });
    ui.parseBtn = T.h('button', { class: 'btn primary', id: 'smart-parse', title: 'read the instruction (Enter in the box); nothing is sent' }, 'Parse');
    ui.parseBtn.addEventListener('click', function () { parse(); });
    page.appendChild(T.h('div', { class: 'smart-bar' }, ui.bar, ui.parseBtn));
    page.appendChild(T.h('div', { class: 'hint' }, 'Enter reads it, Shift+Enter starts a new line, Esc clears the box. Several orders: separate them with a comma, "and", ";" or a new line. ' +
      'FinSim fills one order ticket per order; open each in the ticket and send it there with its own preview. The Smart Ticket sends nothing itself.'));
    ui.dirty = T.h('div', { class: 'smart-warn', id: 'smart-dirty', hidden: true }, 'the text changed since it was read: press Enter to read it again');
    page.appendChild(ui.dirty);
    ui.restored = T.h('div', { class: 'smart-warn', id: 'smart-restored', hidden: true });
    page.appendChild(ui.restored);
    ui.book = T.h('div', { class: 'smart-warn', id: 'smart-book', hidden: true });
    page.appendChild(ui.book);
    page.appendChild(T.h('div', { class: 'smart-examples' }, T.h('span', null, 'examples (fictional names):'), EXAMPLES.map(function (x) {
      return T.h('a', { href: '#/smart', title: 'put this example in the box; nothing is read until you press Enter', onclick: function (e) { e.preventDefault(); ui.bar.value = x; S.text = x; paintActions(S); try { ui.bar.focus(); } catch (er) { /* ignore */ } } }, x);
    })));
    ui.readback = T.h('div', { class: 'smart-readback', id: 'smart-readback', hidden: true }); page.appendChild(ui.readback);
    ui.iflags = T.h('div', { class: 'smart-iflags', id: 'smart-iflags', hidden: true }); page.appendChild(ui.iflags);
    ui.status = T.h('div', { class: 'smart-status', id: 'smart-status', role: 'status' }); page.appendChild(ui.status);
    ui.lost = T.h('div', { class: 'smart-warn', id: 'smart-lost', hidden: true }); page.appendChild(ui.lost);
    ui.cards = T.h('div', { class: 'smart-grid', id: 'smart-cards' }); page.appendChild(ui.cards);
    page.appendChild(T.h('div', { class: 'smart-actions muted' }, '!! blocks the order . ! look at it first . i how it was read . each order is sent from the order ticket, after its own preview'));
    page.appendChild(T.convNote(['SMART_TICKET']));
    ui.bar.addEventListener('keydown', function (e) {
      if (e.isComposing) { return; }
      var plain = !e.shiftKey && !e.ctrlKey && !e.altKey && !e.metaKey;
      if (e.key === 'Enter' && plain) { e.preventDefault(); parse(); return; }
      if ((e.key === 'Escape' || e.key === 'Esc') && plain && ui.bar.value) { e.preventDefault(); ui.bar.value = ''; S.text = ''; saveState(S); paintActions(S); say(S, 'the box is cleared (the cards stay until you read a new text or start a new ticket)'); }
    });
    ui.bar.addEventListener('input', function () { S.text = ui.bar.value; paintActions(S); });
    page.addEventListener('click', onClick);
    ui.cards.addEventListener('change', onEdit);
    var restored = restore(S);
    ui.bar.value = S.text || '';
    if (!T.modulesKnown() || T.hasModule('smart_ticket')) {
      var when = S.savedAt ? 'saved ' + T.fmt.hm(Math.floor(S.savedAt / 1000)) + ' ET' : '';
      say(S, restored && S.parsed ? 'restored from this browser' + (S.copied ? ' (a new window: a copy of the draft saved last' + (when ? ', ' + when : '') + ')' : (when ? ' (' + when + ')' : '')) +
        ': press Enter to read it again before opening an order' : '');
    }
    else { say(S, 'the Smart Ticket\'s reader is not in this build: nothing can be read', 'err'); }
    paint(S);
    try { ui.bar.focus({ preventScroll: true }); } catch (e) { /* focus is a convenience */ }
  }
  function unmount() {
    var S = st; if (!S) { return; }
    if (ui) { S.text = ui.bar.value; }
    saveState(S);
    S.dead = true;
    st = null; ui = null;
  }
  T.pages.smart = { render: render, unmount: unmount };
})();
