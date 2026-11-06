/* The Smart Ticket (#/smart, Ctrl+F9; r1072 CHANGE_PLAN 5.1; FINSIM_TERMINAL_GAPS 3.1 / 3.3 / 3.6.3 / 3.8).  The owner's request of
 * 2026-11-05: send the app what you want to do, and it fills the tickets in - many orders in one instruction, options in the same one.
 *
 * The owner types an instruction; FinSim reads it with its fixed grammar (POST /api/smart-ticket/parse: pure - it writes, fetches and
 * subscribes nothing; no language model) into staged legs.  Nothing is sent until the owner confirms.
 * - The bar: 1-3 lines.  Enter reads it, Shift+Enter starts a new line, Esc clears it; the Parse button; examples with fictional
 *   names.  The text goes to the server only on Enter or Parse, never while typing; the words FinSim could not read are underlined.
 * - The leg grid (components/grid.js): select, #, combination, book . account, side, quantity, the instrument in words, type, limit,
 *   stop / trail, time in force, flags (red !! = blocks the leg, amber ! = look before sending, grey i = how it was read; a chip opens
 *   its text, the listed alternatives - each one replaces that leg's words in the instruction, which is then read again - and its
 *   action: look the symbol up, load the chain, open the product's own ticket), the server's preview and the result.
 * - In-place edits (side among the ones its kind takes, quantity, type, prices, time in force, GTD date) change that order only; an
 *   edit makes a new revision of the item, so its request id is never reused for other terms.  "ticket" moves a single leg to the
 *   docked ticket of its account page (prefilled; that leg is then left out of this ticket).
 * - Previews: an order through POST /api/orders/preview?book=<its book>; a combination through POST /api/strategies/preview?book=
 *   (key CUSTOM; asked again while its chain loads) plus its primary leg's order preview when it is sequential.  300 ms after an edit,
 *   at most two at a time, again every 15 s while the page is visible.  Every number on this page is the server's.
 * - Submit selected (the button or Ctrl+Shift+Enter) needs every selected item free of blockers in the parse and in its latest
 *   preview.  The confirm lists every order in words with its book (another book than the selected one is said) and the server's
 *   figures; an all-or-none combination says that its legs fill together at each leg's executable bid / ask, with no net limit in this
 *   version.  Back is focused; only "Send N orders" sends.
 * - The send loop: strictly one item at a time, in leg order: a fresh preview (a blocker stops), then POST /api/orders?book=<its book>
 *   with the request id st:<ticket>:<L|C><n>:r<revision>, smart {ticket, item, clause} (the words typed, kept with the command) and the
 *   id of the preview the CONFIRM listed (an order's, or a sequential combination's shares leg's; second review), so the server judges a
 *   price that moved since the owner read the confirm (PREVIEW_STALE: more than 0.5 % against the owner, or a preview older than 120 s).
 *   The first refusal stops the run: a moved price (PREVIEW_STALE) shows the server's diff with Accept & continue / Skip / Stop (Accept
 *   resends the same request id with the preview the dialog shows - third review: the server stores the 409's preview, so a price that
 *   moves again while the dialog is open asks again); a large order (CONFIRM_REQUIRED) is resent under the same id with
 *   confirm_large only when the owner clicks Confirm and send; any other refusal stops with its message.  A lost answer is looked up
 *   by its request id (a single order through T.api's reconcile, a combination by rid / rid:L1 / rid:H1) before anything else goes,
 *   and recorded as sent only when the orders there have the item's terms (a sequential package's option legs too: type, limit and
 *   size through their ratio; third review).
 * - "Cancel what this ticket sent": the open orders of this ticket (request ids st:<ticket>:...) in every account it used, listed and
 *   confirmed, then one POST /api/orders/cancel?book=<b> per book, every hedge leg listed before its primary (each by its own cancel).
 * The ticket id is made here (T.uuid, 12 hex characters), never from the text; an unchanged item resent is a replay ("already
 * submitted"), never a second order, and within one ticket an order already sent stays marked sent when the text is read again - by
 * its terms, or by its words in the same place when they now read as another order (a position it closed has moved): such an item is
 * shown as the order that went, unselected, and never offered again (1.0.7.2 review).  A 409 REQUEST_ID_REUSED on the ticket's own
 * request id (or a lost answer of a combination) is looked up in the account's orders: recorded as sent ("already submitted") only when
 * the order found there has this item's terms; otherwise (another window sent other terms under the same id) it is a refusal naming that
 * order, and the item's revision moves on, so sending it again places it under a new id (second review).  The
 * draft (text, parse, edits, revisions, results) is kept in this browser only (T.store 'smart.draft') and cleared once every selected
 * item is sent.  The dock never opens here (no account route), so the ticket's keys never act on this page.
 * Nothing here computes a price, an amount or a total.  ASCII only; no framework, no CDN.
 */
(function () {
  'use strict';
  var T = window.T;
  var PARSE = '/api/smart-ticket/parse';
  var STORE = 'smart.draft';
  var DEBOUNCE_MS = 300, REFRESH_MS = 15000, MAX_FLIGHT = 2, PKG_TRIES = 8, MAX_CHARS = 2000, BATCH_MAX = 500;
  var SIDES = { CASH: ['BUY', 'SELL', 'SELL_SHORT', 'BUY_TO_COVER'], OPTION: ['BTO', 'STO', 'BTC', 'STC'], FUTURE: ['BUY', 'SELL'] };
  var SIDE_WORDS = { BUY: 'BUY', SELL: 'SELL', SELL_SHORT: 'SELL SHORT', BUY_TO_COVER: 'BUY TO COVER', BTO: 'BTO', STO: 'STO', BTC: 'BTC', STC: 'STC' };
  var SIDE_TITLE = { BUY: 'buy', SELL: 'sell', SELL_SHORT: 'sell short (a locate and a borrow come first)', BUY_TO_COVER: 'buy to cover', BTO: 'buy to open',
                     STO: 'sell to open', BTC: 'buy to close', STC: 'sell to close' };
  var TYPES = [['MARKET', 'MKT'], ['LIMIT', 'LMT'], ['STOP', 'STP'], ['STOP_LIMIT', 'STP LMT'], ['TRAILING_STOP', 'TRAIL']];
  var TIFS = [['DAY', 'DAY'], ['GTC', 'GTC'], ['GTD', 'GTD'], ['NEXT_SESSION', 'next session']];
  var SEV = { blocker: 0, check: 1, info: 2 };
  var SEV_MARK = { blocker: '!! ', check: '! ', info: 'i ' };
  var SEV_TITLE = { blocker: 'blocks this order', check: 'look at this before sending', info: 'how it was read' };
  var AON_WORDS = 'all-or-none at each leg\'s executable bid / ask; no net limit in this version';
  /* fictional names only (ACME, WIDG): a click puts the example in the box; nothing is read until Enter or Parse */
  var EXAMPLES = ['buy 200 ACME limit 225, sell 2 ACME Dec 18 2026 240 calls, and buy 50 WIDG at market in the Fund book',
                  'buy 1 ACME Dec 18 2026 240/250 call spread',
                  'buy 200 ACME limit 225 and sell 2 ACME Dec 18 2026 240 calls as a package',
                  'in the Fund book: buy 100 ACME; buy 100 WIDG gtc'];
  var st = null;                                               /* the mounted page's state (null while another page shows) */
  var ui = null;

  /* ---------------------------------------------------------------- small helpers -------------------------------- */
  function esc(s) { return T.esc(s == null ? '' : s); }
  function clone(o) { return o == null ? o : JSON.parse(JSON.stringify(o)); }
  function sorted(o) {
    if (Array.isArray(o)) { return o.map(sorted); }
    if (o && typeof o === 'object') { var out = {}; Object.keys(o).sort().forEach(function (k) { out[k] = sorted(o[k]); }); return out; }
    return o;
  }
  function sigOf(o) { return o == null ? null : JSON.stringify(sorted(o)); }
  function newTicket() { return String(T.uuid()).replace(/-/g, '').toLowerCase().slice(0, 12); }
  function hex12() { return String(T.uuid()).replace(/-/g, '').toLowerCase().slice(0, 12); }
  function bookQ(book) { return book ? '?book=' + encodeURIComponent(book) : ''; }
  function wait(ms) { return new Promise(function (ok) { setTimeout(ok, Math.max(300, Math.min(5000, Number(ms) || 1500))); }); }
  function money(x) { return x == null || x === '' ? null : T.fmt.money(x); }
  function plural(n, one, many) { return n + ' ' + (n === 1 ? one : (many || one + 's')); }
  function short(s, n) { s = String(s == null ? '' : s); return s.length > n ? s.slice(0, n - 3).replace(/\s+\S*$/, '') + ' ...' : s; }
  function kindGroup(kind) { var k = String(kind || '').toUpperCase(); return k === 'OPTION' ? 'OPTION' : (k === 'FUTURE' ? 'FUTURE' : 'CASH'); }
  function isPx(v) { return v != null && /^\d+(\.\d+)?$/.test(String(v)) && Number(v) > 0; }
  function cleanPx(v) { return String(v == null ? '' : v).trim().replace(/^\$/, '').replace(/,/g, ''); }
  function bookName(code) {
    var b = (T.state.books || []).filter(function (x) { return x && x.book_code === code; })[0];
    return b ? (b.name || b.book_code) : (code || 'no book');
  }
  function detailsOf(e) { var P = T.components.preview; return P && P.detailsOf ? P.detailsOf(e) : ((e && e.details) || {}); }
  function blockerOf(e) {
    var P = T.components.preview;
    if (P && P.errorAsBlocker) { return P.errorAsBlocker(e); }
    return { code: (e && e.code) || 'ERROR', message: T.api.describe(e), details: detailsOf(e), http: (e && e.status) || 0 };
  }
  function kick() { ['orders', 'positions', 'account_detail', 'accounts', 'attention'].forEach(function (p) { T.poll.kick(p); }); }

  /* ---------------------------------------------------------------- the items (one order or one combination) ------ */
  /* the request an item would send now: the server's request with the owner's in-place edits on top (fields not used by the order
     type dropped, as the ticket does); a combination's edits change its package legs */
  function normOrder(r) {
    if (/^\d+$/.test(String(r.qty))) { r.qty = Number(r.qty); }
    var t = r.order_type;
    if (t !== 'LIMIT' && t !== 'STOP_LIMIT') { delete r.limit_px; }
    if (t !== 'STOP' && t !== 'STOP_LIMIT') { delete r.stop_px; }
    if (t !== 'TRAILING_STOP') { delete r.trail_amt; delete r.trail_pct; }
    if (r.tif !== 'GTD') { delete r.gtd_date; }
    if (r.side === 'SELL_SHORT') { r.borrow = true; } else { delete r.borrow; }
    Object.keys(r).forEach(function (k) { if (r[k] == null || r[k] === '') { delete r[k]; } });
    return r;
  }
  function effective(it) {
    if (!it.base) { return null; }
    var r = clone(it.base); var e = it.edits || {};
    if (it.kind === 'leg') {
      Object.keys(e).forEach(function (k) { r[k] = e[k]; });
      return normOrder(r);
    }
    var pl = r.package && Array.isArray(r.package.legs) ? r.package.legs : [];
    Object.keys(e.legs || {}).forEach(function (i) {
      var l = pl[Number(i)]; var x = e.legs[i]; if (!l || !x) { return; }
      Object.keys(x).forEach(function (k) { l[k] = x[k]; });
      if (/^\d+$/.test(String(l.qty))) { l.qty = Number(l.qty); }
      if (l.order_type && l.order_type !== 'LIMIT') { delete l.limit_px; }
      Object.keys(l).forEach(function (k) { if (l[k] == null || l[k] === '') { delete l[k]; } });
    });
    return r;
  }
  /* the request and its revision: an item whose terms changed (an edit, or another reading of the text) gets the next revision, so a
     request id is never reused for other terms; unchanged terms keep theirs (a resend is a replay, never a second order) */
  function stamp(S, it) {
    it.req = effective(it);
    it.sig = it.req ? sigOf(it.req) : null;
    var r = S.revs[it.key];
    if (!it.req) { it.rev = r ? r.rev : 0; return; }
    if (r && r.sig === it.sig) { it.rev = r.rev; return; }
    it.rev = r ? r.rev + 1 : 1;
    S.revs[it.key] = { rev: it.rev, sig: it.sig };
  }
  function ridOf(S, it) { return 'st:' + S.ticket + ':' + it.key + ':r' + it.rev; }
  /* a combination's legs in text order: the ones it lists and the ones that name it (a leg is never left off the grid) */
  function legsOf(parsed, c) {
    var want = (c.legs || []).map(Number);
    return (parsed.legs || []).filter(function (l) { return want.indexOf(Number(l.n)) >= 0 || (l.combo != null && String(l.combo) === String(c.id)); });
  }
  function newItem(S, kind, key, src, legs, prev, restored) {
    var it = { key: key, kind: kind, src: src, legs: legs, ns: legs.map(function (l) { return Number(l.n); }), base: src.request || null,
               book: src.book || (legs[0] && legs[0].book) || null, account_id: src.account_id || (legs[0] && legs[0].account_id) || null,
               fill_mode: kind === 'combo' ? src.fill_mode : null, edits: {}, sel: true, rev: 0, req: null, sig: null,
               pv: null, ppv: null, spec: null, err: null, pvSig: null, busy: false, again: false, timer: null, pend: 0, sent: null, result: null, moved: false };
    it.baseSig = sigOf(it.base);
    if (prev && prev.baseSig === it.baseSig) { it.edits = clone(prev.edits) || {}; it.sel = prev.sel; }
    else if (restored && restored.baseSigs && restored.baseSigs[key] === it.baseSig) {
      it.edits = (restored.edits && restored.edits[key]) || {}; it.sel = !(restored.sel && restored.sel[key] === false);
    }
    stamp(S, it);
    if (it.sig && S.moved[key] === it.sig) { it.moved = true; it.sel = false; }
    return it;
  }
  /* an item whose exact terms this ticket already sent is marked sent (its own key first, then a leg that moved in the text); then, by
     its WORDS (1.0.7.2 review R2): the same clause this ticket already sent stays sent even when it now reads as another order (the
     position it closed has moved, so "buy" no longer reads "buy to close"), shown as the order that went and never selectable again */
  function matchSent(S, it, used) {
    it.sent = null; it.reread = false;
    var hit = -1;
    if (it.sig) {
      S.sentLog.forEach(function (s, i) { if (hit < 0 && !used[i] && s.key === it.key && s.sig === it.sig) { hit = i; } });
      S.sentLog.forEach(function (s, i) { if (hit < 0 && !used[i] && s.sig === it.sig) { hit = i; } });
    }
    var words = clauseOf(it); var acct = (it.req && it.req.account_id) || it.account_id || null;
    if (hit < 0 && words) {                          /* the same words in the same account (another book or account reads as another order) */
      S.sentLog.forEach(function (s, i) { if (hit < 0 && !used[i] && s.key === it.key && s.clause === words && s.account_id === acct) { hit = i; } });
      S.sentLog.forEach(function (s, i) { if (hit < 0 && !used[i] && s.clause === words && s.account_id === acct) { hit = i; } });
    }
    if (hit >= 0) { used[hit] = true; it.sent = S.sentLog[hit]; it.sel = false; it.reread = it.sig !== it.sent.sig; }
  }
  /* the request a row shows: the one sent when the item went and now reads differently, else the item's own */
  function shownReq(it) {
    if (it.sent && it.reread && it.sent.sig) { try { return JSON.parse(it.sent.sig); } catch (e) { return it.req; } }
    return it.req;
  }
  function rebuild(S, restored) {
    var old = {}; S.items.forEach(function (it) { old[it.key] = it; clearTimeout(it.timer); });
    var p = S.parsed; var combos = {}; var items = []; var seen = {}; var memberOf = {};
    /* every leg belongs to exactly one item: the first combination that lists it or that it names, else its own; so every item has rows
       on the grid, and nothing can be selected or sent that is not shown */
    (p.combos || []).forEach(function (c) {
      if (!c || !c.id) { return; }
      combos[c.id] = c;
      legsOf(p, c).forEach(function (l) { if (memberOf[Number(l.n)] == null) { memberOf[Number(l.n)] = String(c.id); } });
    });
    (p.legs || []).forEach(function (leg) {
      var cid = memberOf[Number(leg.n)]; var c = cid != null ? combos[cid] : null;
      if (c) {
        if (seen[cid]) { return; }
        seen[cid] = true;
        items.push(newItem(S, 'combo', cid, c, (p.legs || []).filter(function (l) { return memberOf[Number(l.n)] === cid; }), old[cid], restored));
      } else {
        items.push(newItem(S, 'leg', 'L' + leg.n, leg, [leg], old['L' + leg.n], restored));
      }
    });
    S.items = items;
    var used = {};
    items.forEach(function (it) { matchSent(S, it, used); });
    var byN = {};
    items.forEach(function (it) { it.ns.forEach(function (n, i) { byN[n] = { item: it, first: i === 0, index: i }; }); });
    S.rows = (p.legs || []).map(function (leg) { var m = byN[Number(leg.n)] || {}; return { n: Number(leg.n), leg: leg, item: m.item || null, first: !!m.first, index: m.index || 0 }; })
      .filter(function (r) { return r.item; });
    S.queue = [];
  }
  function itemByKey(S, key) { for (var i = 0; i < S.items.length; i++) { if (S.items[i].key === key) { return S.items[i]; } } return null; }
  function rowByN(S, n) { for (var i = 0; i < S.rows.length; i++) { if (S.rows[i].n === n) { return S.rows[i]; } } return null; }
  function instOf(leg) { return (leg && leg.instrument) || {}; }
  /* the index of a combination row's leg in its package: the same place when the instrument agrees, else the one package leg of that
     instrument; -1 when it cannot be told (the row is then shown as read, never edited) */
  function pkgIndex(it, r) {
    var rq = shownReq(it);
    var pl = rq && rq.package && Array.isArray(rq.package.legs) ? rq.package.legs : null;
    if (!pl || !r) { return -1; }
    var want = (r.leg.request && r.leg.request.instrument_id) || instOf(r.leg).id;
    if (!want) { return -1; }
    if (pl[r.index] && pl[r.index].instrument_id === want) { return r.index; }
    var hit = -1, n = 0;
    pl.forEach(function (l, i) { if (l && l.instrument_id === want) { hit = i; n++; } });
    return n === 1 ? hit : -1;
  }
  function pkgLeg(it, r) { var i = pkgIndex(it, r); return i >= 0 ? shownReq(it).package.legs[i] : null; }
  /* the shares leg of a sequential combination (its primary): the row of the leg that is not an option */
  function primaryRow(it) {
    for (var i = 0; i < it.legs.length; i++) {
      if (kindGroup(instOf(it.legs[i]).kind) !== 'OPTION') { return { n: Number(it.legs[i].n), leg: it.legs[i], item: it, first: i === 0, index: i }; }
    }
    return null;
  }
  function primaryIndex(it) { var r = primaryRow(it); return r ? r.index : -1; }
  /* the order a row shows: the item's request for a single leg, its package leg for a combination, else what the parse read */
  function orderOf(r) {
    var it = r.item; var rq = shownReq(it);
    if (it.kind === 'leg' && rq) { return rq; }
    if (it.kind === 'combo') {
      var l = pkgLeg(it, r);
      if (l) { return { side: l.side, qty: l.qty, order_type: l.order_type || 'MARKET', limit_px: l.limit_px, tif: rq.tif }; }
    }
    var g = r.leg;
    return { side: g.side, qty: g.qty, order_type: g.order_type, limit_px: g.limit_px, stop_px: g.stop_px, trail_amt: g.trail_amt, trail_pct: g.trail_pct,
             tif: g.tif, gtd_date: g.gtd_date };
  }

  /* ---------------------------------------------------------------- flags, validity, readiness -------------------- */
  function itemFlags(it) {
    var out = [];
    it.legs.forEach(function (l) { (l.flags || []).forEach(function (f) { out.push(f); }); });
    if (it.kind === 'combo') { (it.src.flags || []).forEach(function (f) { out.push(f); }); }
    return out;
  }
  function firstBlocker(list) { for (var i = 0; i < list.length; i++) { if (list[i] && list[i].severity === 'blocker') { return list[i]; } } return null; }
  function instrBlocker(S) { return S.parsed ? firstBlocker(S.parsed.flags || []) : null; }
  function orderValidity(r) {
    if (!/^\d+$/.test(String(r.qty)) || Number(r.qty) <= 0) { return 'enter a whole quantity above 0'; }
    var t = r.order_type;
    if ((t === 'LIMIT' || t === 'STOP_LIMIT') && !isPx(r.limit_px)) { return 'enter a limit price'; }
    if ((t === 'STOP' || t === 'STOP_LIMIT') && !isPx(r.stop_px)) { return 'enter a stop price'; }
    if (t === 'TRAILING_STOP' && !isPx(r.trail_amt) && !isPx(r.trail_pct)) { return 'enter a trail amount, or a percent ending in %'; }
    if (r.tif === 'GTD' && !/^\d{4}-\d{2}-\d{2}$/.test(String(r.gtd_date || ''))) { return 'pick the GTD date'; }
    return null;
  }
  function validity(it) {
    if (!it.req) { return 'not ready to send: see its red flags'; }
    if (it.kind === 'leg') { return orderValidity(it.req); }
    var pl = (it.req.package && it.req.package.legs) || [];
    for (var i = 0; i < pl.length; i++) {
      if (!/^\d+$/.test(String(pl[i].qty)) || Number(pl[i].qty) <= 0) { return 'leg ' + (it.ns[i] || i + 1) + ': enter a whole quantity above 0'; }
      if (pl[i].order_type === 'LIMIT' && !isPx(pl[i].limit_px)) { return 'leg ' + (it.ns[i] || i + 1) + ': enter a limit price'; }
    }
    return pl.length ? null : 'the combination has no legs';
  }
  function pvBlockers(it) {
    if (it.err) { return [it.err]; }
    if (it.kind === 'leg') {
      var pv = it.pv; if (!pv) { return []; }
      var b = Array.isArray(pv.blockers) ? pv.blockers : [];
      return b.length || pv.ok !== false ? b : [{ code: 'NOT_OK', message: 'the preview does not accept this order' }];
    }
    var pp = it.ppv; var pb = pp && Array.isArray(pp.blockers) ? pp.blockers : [];
    return pb.length || !pp || pp.ok !== false ? pb : [{ code: 'NOT_OK', message: 'the primary leg\'s preview does not accept it' }];
  }
  function previewed(it) {
    if (it.pvSig !== it.sig || it.busy) { return false; }
    if (it.err) { return true; }
    if (it.kind === 'leg') { return !!it.pv; }
    return !!it.spec && (it.fill_mode !== 'SEQUENTIAL' || !!it.ppv);
  }
  /* why this item cannot go now (null: it can) */
  function notReady(S, it) {
    if (it.sent) { return 'already sent'; }
    if (it.moved) { return 'moved to the ticket'; }
    var b = firstBlocker(itemFlags(it)); if (b) { return b.text || 'a red flag'; }
    var v = validity(it); if (v) { return v; }
    if (!previewed(it)) { return it.pend ? 'waiting for its chain to load' : 'waiting for its preview'; }
    var pb = pvBlockers(it); if (pb.length) { return 'refused by its preview: ' + (pb[0].message || 'not accepted'); }
    return null;
  }
  function selectedItems(S) { return S.items.filter(function (it) { return it.sel && !it.sent && !it.moved; }); }
  /* why Submit selected cannot open the confirm now (null: it can) */
  function submitBlock(S) {
    if (S.sending) { return 'orders are being sent'; }
    if (S.confirming) { return 'the confirm is open'; }
    if (S.parsing) { return 'still reading the text'; }
    if (!S.parsed) { return 'nothing staged: type what you want to trade and press Enter'; }
    if (S.restored) { return 'restored from this browser: press Enter to read the text again before sending'; }
    if (ui && ui.bar.value !== S.parsedFrom) { return 'the text changed since it was read: press Enter to read it again'; }
    var ib = instrBlocker(S); if (ib) { return 'the instruction has a red flag: ' + (ib.text || 'see the chips under the text'); }
    var list = selectedItems(S);
    if (!list.length) { return 'select at least one order to send'; }
    for (var i = 0; i < list.length; i++) { var why = notReady(S, list[i]); if (why) { return list[i].key + ': ' + why; } }
    return null;
  }

  /* ---------------------------------------------------------------- the draft (this browser only) ----------------- */
  function saveState(S) {
    if (!S || S.cleared) { return; }                          /* every selected item was sent: the draft stays cleared until a new reading or edit */
    var edits = {}, sel = {}, baseSigs = {};
    S.items.forEach(function (it) { edits[it.key] = it.edits; sel[it.key] = it.sel; baseSigs[it.key] = it.baseSig; });
    T.store.set(STORE, { v: 1, ticket: S.ticket, text: S.text, parsedFrom: S.parsedFrom, parsed: S.parsed, revs: S.revs, sent: S.sentLog, moved: S.moved,
                         edits: edits, sel: sel, baseSigs: baseSigs, saved: Date.now() });
  }
  function restore(S) {
    var d = T.store.get(STORE, null);
    if (!d || d.v !== 1 || !/^[0-9a-f]{12}$/.test(String(d.ticket || ''))) { return false; }
    S.ticket = d.ticket; S.text = typeof d.text === 'string' ? d.text : ''; S.parsedFrom = typeof d.parsedFrom === 'string' ? d.parsedFrom : null;
    S.revs = d.revs && typeof d.revs === 'object' ? d.revs : {}; S.sentLog = Array.isArray(d.sent) ? d.sent : []; S.moved = d.moved && typeof d.moved === 'object' ? d.moved : {};
    S.savedAt = d.saved || null;
    if (d.parsed && Array.isArray(d.parsed.legs)) {
      S.parsed = d.parsed; S.restored = true;
      rebuild(S, { edits: d.edits || {}, sel: d.sel || {}, baseSigs: d.baseSigs || {} });
    }
    return true;
  }

  /* ---------------------------------------------------------------- previews ------------------------------------- */
  function previewLeg(book, req) { return T.api.post('/api/orders/preview' + bookQ(book), Object.assign({}, req, { confirm_large: false }), { retries: 1 }); }
  function primaryReq(it) {
    var l = pkgLeg(it, primaryRow(it)); if (!l) { return null; }
    var r = { account_id: it.req.account_id, instrument_id: l.instrument_id, side: l.side, qty: l.qty, order_type: l.order_type || 'MARKET', tif: it.req.tif || 'DAY' };
    if (r.order_type === 'LIMIT') { r.limit_px = l.limit_px; }
    if (r.side === 'SELL_SHORT') { r.borrow = true; }
    return r;
  }
  /* a combination's package preview: 200 the package, {pending} while its chain loads (asked again by the caller) */
  function pkgPreview(book, req) {
    var body = { account_id: req.account_id, key: 'CUSTOM', params: { legs: (req.package && req.package.legs) || [] } };
    return T.api.post('/api/strategies/preview' + bookQ(book), body, { retries: 1 });
  }
  /* one item's preview now: {pv} (an order) or {spec, ppv} (a combination) or {pending, retry_ms}; a refusal rejects */
  function previewItem(it) {
    var req = it.req;
    if (it.kind === 'leg') { return previewLeg(it.book, req).then(function (pv) { return pv && pv.pending ? { pending: true, retry_ms: pv.retry_ms } : { pv: pv }; }); }
    return pkgPreview(it.book, req).then(function (spec) {
      if (spec && spec.pending) { return { pending: true, retry_ms: spec.retry_ms }; }
      if (it.fill_mode !== 'SEQUENTIAL') { return { spec: spec, ppv: null }; }
      var pr = primaryReq(it);
      if (!pr) { return { spec: spec, ppv: { ok: false, blockers: [{ code: 'NO_PRIMARY', message: 'the sequential combination names no shares leg' }] } }; }
      return previewLeg(it.book, pr).then(function (ppv) { return { spec: spec, ppv: ppv }; });
    });
  }
  function schedule(S, it, now) {
    if (!it || it.sent || it.moved || S.dead) { return; }
    clearTimeout(it.timer);
    it.timer = setTimeout(function () { it.timer = null; enqueue(S, it); }, now ? 0 : DEBOUNCE_MS);
  }
  function enqueue(S, it) { if (S.dead) { return; } if (S.queue.indexOf(it) < 0) { S.queue.push(it); } pump(S); }
  function pump(S) {
    while (!S.dead && !S.sending && S.flight < MAX_FLIGHT && S.queue.length) {      /* the send loop takes its own fresh previews */
      var it = S.queue.shift();
      if (S.items.indexOf(it) < 0 || it.sent || it.moved) { continue; }
      if (it.busy) { it.again = true; continue; }
      if (!it.req || validity(it)) { it.pv = it.spec = it.ppv = it.err = null; it.pvSig = null; continue; }
      startPreview(S, it);
    }
    paint(S);
  }
  function startPreview(S, it) {
    var s = it.sig; var finished = false;
    S.flight++; it.busy = true;
    function done() {                                         /* once per preview, whatever happens: a slot is never leaked */
      if (finished) { return; }
      finished = true; S.flight--; it.busy = false;
      if (S.dead) { return; }
      if (it.again || s !== it.sig) { it.again = false; enqueue(S, it); return; }
      pump(S);
    }
    var p;
    try { p = previewItem(it); } catch (x) { p = Promise.reject(x); }
    p.then(function (out) {
      if (S.dead || s !== it.sig) { done(); return; }
      if (out.pending) {
        it.pend += 1;
        if (it.pend <= PKG_TRIES) { setTimeout(function () { if (!S.dead) { enqueue(S, it); } }, Math.max(500, Math.min(5000, Number(out.retry_ms) || 1500))); }
        else { it.err = { code: 'PENDING', message: 'the chain did not load: use Load the chain, then Preview all' }; it.pvSig = s; }
        done(); return;
      }
      it.pend = 0; it.err = null; it.pvSig = s; it.pvAt = Date.now();
      if (it.kind === 'leg') { it.pv = out.pv; } else { it.spec = out.spec; it.ppv = out.ppv; }
      done();
    }, function (e) {
      if (!S.dead && s === it.sig) { it.err = blockerOf(e); it.pv = it.spec = it.ppv = null; it.pvSig = s; it.pend = 0; }
      if (e && e.code !== 'NETWORK' && e.status >= 500) { console.error('smart ticket preview', it.key, e); }
      done();
    }).then(null, function (x) { console.error('smart ticket preview', it.key, x); done(); });
  }
  function refreshAll() {
    var S = st;
    if (S && !S.dead && !S.sending) { S.items.forEach(function (it) { if (!it.sent && !it.moved && it.req && !validity(it)) { enqueue(S, it); } }); }
    return Promise.resolve();
  }

  /* ---------------------------------------------------------------- reading the text ----------------------------- */
  function parse(textOverride) {
    var S = st; if (!S || S.dead || !ui) { return; }
    var text = textOverride != null ? String(textOverride) : ui.bar.value;
    if (!text.trim()) { say(S, 'type what you want to trade first (a fictional example: buy 100 ACME limit 50)', 'err'); return; }
    if (S.sending) { say(S, 'wait until the orders in progress are sent', 'err'); return; }
    if (S.confirming) { say(S, 'close the confirm first (Back)', 'err'); return; }
    if (T.modulesKnown() && !T.hasModule('smart_ticket')) { say(S, 'the Smart Ticket\'s reader is not in this build: nothing can be read', 'err'); return; }
    var seq = ++S.pseq;
    var book = T.book.code || null;
    S.parsing = true; say(S, 'reading ...'); paint(S);
    var body = { text: text }; if (book) { body.book = book; }
    T.api.post(PARSE + bookQ(book), body, { retries: 0 }).then(function (ans) {
      if (S.dead || seq !== S.pseq) { return; }
      S.parsing = false;
      if (!ans || typeof ans !== 'object' || !Array.isArray(ans.legs)) { say(S, 'the reader gave an answer this page cannot show', 'err'); paint(S); return; }
      if (ui.bar.value !== text) { ui.bar.value = text; }
      S.parsed = ans; S.parsedFrom = text; S.text = text; S.restored = false; S.detail = null; S.cleared = false;
      rebuild(S, null);
      S.items.forEach(function (it) { schedule(S, it, true); });
      saveState(S); paint(S);
      var c = ans.counts || {};
      say(S, 'read ' + plural(Number(c.legs != null ? c.legs : ans.legs.length), 'leg') + (c.combos ? ' (' + plural(Number(c.combos), 'combination') + ')' : '') +
          (c.blockers ? ': ' + plural(Number(c.blockers), 'red flag') + ' to fix (click a chip)' : (c.checks ? ': ' + plural(Number(c.checks), 'amber flag') + ' to look at' : '')) +
          '. Nothing is sent until you confirm.', c.blockers ? 'err' : 'ok');
    }, function (e) {
      if (S.dead || seq !== S.pseq) { return; }
      S.parsing = false; paint(S);
      var msg = 'could not read it: ' + T.api.describe(e);
      say(S, msg, 'err'); T.ui.toast(msg, 'err', 5000);
      console.warn('smart ticket parse', e && e.status, e && e.code);
    }).then(null, function (x) {                              /* an answer this page failed to show: said, never a page stuck reading */
      console.error('smart ticket: the answer could not be shown', x);
      if (!S.dead && seq === S.pseq) { S.parsing = false; S.parsed = null; S.items = []; S.rows = []; paint(S); say(S, 'the answer could not be shown: ' + (x && x.message || x), 'err'); }
    });
  }
  /* D-b: a choice replaces the words its flag names (the flag's span: the leg's clause, the combination's text, or both clauses of a
     cover hint) in the text as read, then the text is read again; a flag without a span falls back to its leg's (or combination's) words */
  function spanOf(S, r, f, ch) {
    if (ch && Array.isArray(ch.span)) { return ch.span; }
    if (Array.isArray(f.span)) { return f.span; }
    if (!r) { return null; }
    if (f.$combo) {
      var lo = null, hi = null;
      r.item.legs.forEach(function (l) { if (Array.isArray(l.span)) { lo = lo == null ? l.span[0] : Math.min(lo, l.span[0]); hi = hi == null ? l.span[1] : Math.max(hi, l.span[1]); } });
      return lo == null ? null : [lo, hi];
    }
    return Array.isArray(r.leg.span) ? r.leg.span : null;
  }
  function choose(S, r, f, ch) {
    var text = S.parsed && typeof S.parsed.text === 'string' ? S.parsed.text : ui.bar.value;
    var sp = spanOf(S, r, f, ch);
    var a = sp ? Number(sp[0]) : -1, b = sp ? Number(sp[1]) : -1;
    if (!(a >= 0 && b >= a && b <= text.length) || typeof ch.clause !== 'string') { say(S, 'this alternative has no place in the text to go to', 'err'); return; }
    var next = text.slice(0, a) + ch.clause + text.slice(b);
    ui.bar.value = next; S.text = next;
    say(S, 'alternative "' + short(ch.label || ch.clause, 60) + '" put in the text; reading it again ...');
    parse(next);
  }
  function runAction(S, r, a) {
    if (!a) { return; }
    if (a.kind === 'resolve' && a.symbol) {
      say(S, 'looking ' + a.symbol + ' up ...');
      T.api.getReady('/api/instruments/resolve', { symbol: a.symbol }, 12).then(function () {
        if (!S.dead) { say(S, a.symbol + ' found; reading the text again ...', 'ok'); parse(); }
      }, function (e) { if (!S.dead) { say(S, 'the look-up of ' + a.symbol + ' failed: ' + T.api.describe(e), 'err'); } });
    } else if (a.kind === 'load_chain') {
      var id = a.instrument_id || (r && (instOf(r.leg).underlying_id || instOf(r.leg).id));
      if (!id) { say(S, 'no underlying named for the chain', 'err'); return; }
      say(S, 'loading the ' + id + ' option chain (Cboe, delayed) ...');
      T.api.getReady('/api/instruments/' + encodeURIComponent(id) + '/chain', null, 30).then(function () {
        if (!S.dead) { say(S, 'the ' + id + ' chain is loaded; reading the text again ...', 'ok'); parse(); }
      }, function (e) { if (!S.dead) { say(S, 'the ' + id + ' chain did not load: ' + T.api.describe(e), 'err'); } });
    } else if (a.kind === 'open_ticket') {
      openTicket(S, r, a.ticket || {}, r ? r.item : null);
    }
  }
  /* the docked ticket of the order's account page, prefilled (it opens once that page has rendered: the dock never opens here) */
  function openTicket(S, r, fields, it) {
    var f = Object.assign({}, fields || {});
    var acct = f.account_id || (r && r.leg.account_id) || null;
    if (!acct) { say(S, 'this leg has no account yet: open the account page and use its ticket', 'err'); return; }
    f.account_id = acct;
    var why = T.accountRefusal(acct); if (why) { say(S, why, 'err'); return; }
    if (it && it.kind === 'leg' && it.sig) { it.moved = true; it.sel = false; S.moved[it.key] = it.sig; saveState(S); }
    var off = T.state.on('route', function (route) { off(); if (route && route.account === acct) { T.ticket.open(f); } });
    T.router.go(T.router.account(acct, 'markets'));
  }

  /* ---------------------------------------------------------------- in-place edits -------------------------------- */
  function canEdit(S, it) { return !!(it && it.base && !it.sent && !it.moved && !S.sending && !S.confirming); }
  function onChange(e) {
    var t = e.target; var S = st;
    if (!S || S.dead || !t || !t.dataset || !t.dataset.edit) { return; }
    var f = t.dataset.edit;
    if (f === 'sel') {
      var si = itemByKey(S, t.dataset.key);
      if (si && !si.sent && !si.moved && !S.sending) { si.sel = !!t.checked; saveState(S); }
      paint(S); return;
    }
    var r = rowByN(S, Number(t.dataset.n)); if (!r || !canEdit(S, r.item)) { paint(S); return; }
    var it = r.item; var v = String(t.value == null ? '' : t.value).trim();
    var target;
    if (it.kind === 'leg') { target = it.edits; }
    else {
      var pi = pkgIndex(it, r); if (pi < 0) { paint(S); return; }
      it.edits.legs = it.edits.legs || {}; target = it.edits.legs[pi] = it.edits.legs[pi] || {};
    }
    if (f === 'side') {
      var kind = kindGroup(instOf(r.leg).kind);
      if ((SIDES[kind] || SIDES.CASH).indexOf(v) < 0) { paint(S); return; }
      target.side = v;
    } else if (f === 'qty') { target.qty = /^\d+$/.test(v) ? Number(v) : v; }
    else if (f === 'order_type') { target.order_type = v; }
    else if (f === 'limit_px' || f === 'stop_px') { target[f] = cleanPx(v); }
    else if (f === 'trail') {
      var pct = /%$/.test(v); var n = cleanPx(v.replace(/%$/, ''));
      target.trail_amt = pct ? null : n; target.trail_pct = pct ? n : null;
    } else if (f === 'tif') { target.tif = v; }
    else if (f === 'gtd_date') { target.gtd_date = v; }
    else { paint(S); return; }
    stamp(S, it);
    it.pv = it.ppv = it.spec = it.err = null; it.pvSig = null; it.result = null; it.pend = 0; S.cleared = false;
    var used = {}; S.items.forEach(function (x) { matchSent(S, x, used); });   /* terms this ticket already sent show as sent, never go twice */
    schedule(S, it, false);
    saveState(S); paint(S);
    say(S, it.key + ' changed (revision ' + it.rev + '): previewing again');
  }

  /* ---------------------------------------------------------------- the grid's cells ------------------------------ */
  function sideCell(S, r) {
    var it = r.item; var o = orderOf(r); var side = o.side || '';
    var editable = canEdit(S, it) && (it.kind === 'leg' || !!pkgLeg(it, r));
    if (!editable) { return side ? '<span class="side-' + esc(side) + '" title="' + esc(SIDE_TITLE[side] || '') + '">' + esc(SIDE_WORDS[side] || side) + '</span>' : '<span class="muted">-</span>'; }
    var opts = SIDES[kindGroup(instOf(r.leg).kind)] || SIDES.CASH;
    var h = '<select data-edit="side" data-n="' + r.n + '" aria-label="side of leg ' + r.n + '" class="side-' + esc(side) + '">';
    if (opts.indexOf(side) < 0) { h += '<option value="" selected>choose</option>'; }
    opts.forEach(function (s) { h += '<option value="' + s + '"' + (s === side ? ' selected' : '') + ' title="' + esc(SIDE_TITLE[s] || '') + '">' + esc(SIDE_WORDS[s]) + '</option>'; });
    return h + '</select>';
  }
  function qtyCell(S, r) {
    var it = r.item; var o = orderOf(r);
    if (!(canEdit(S, it) && (it.kind === 'leg' || !!pkgLeg(it, r)))) { return o.qty == null ? '<span class="muted">-</span>' : esc(T.fmt.qty(o.qty)); }
    return '<input class="smart-qty" data-edit="qty" data-n="' + r.n + '" type="text" inputmode="numeric" value="' + esc(o.qty == null ? '' : o.qty) + '" aria-label="quantity of leg ' + r.n + '">';
  }
  function typeEditable(S, r) {
    var it = r.item; if (!canEdit(S, it)) { return false; }
    if (it.kind === 'leg') { return true; }
    return it.fill_mode === 'SEQUENTIAL' && r.index === primaryIndex(it) && !!pkgLeg(it, r);
  }
  function typeCell(S, r) {
    var o = orderOf(r); var t = o.order_type || 'MARKET';
    var lab = TYPES.filter(function (x) { return x[0] === t; })[0];
    if (!typeEditable(S, r)) { return esc(lab ? lab[1] : t) + (r.item.kind === 'combo' && r.item.fill_mode === 'AON' ? ' <span class="muted" title="' + esc(AON_WORDS) + '">aon</span>' : ''); }
    var list = r.item.kind === 'leg' ? TYPES : TYPES.slice(0, 2);
    var h = '<select data-edit="order_type" data-n="' + r.n + '" aria-label="order type of leg ' + r.n + '">';
    list.forEach(function (x) { h += '<option value="' + x[0] + '"' + (x[0] === t ? ' selected' : '') + '>' + x[1] + '</option>'; });
    return h + '</select>';
  }
  function pxInput(field, r, value, enabled, label) {
    return '<input class="smart-px" data-edit="' + field + '" data-n="' + r.n + '" type="text" inputmode="decimal" value="' + esc(value == null ? '' : value) + '"' +
      (enabled ? '' : ' disabled') + ' aria-label="' + esc(label) + ' of leg ' + r.n + '">';
  }
  /* a price box only where the order type uses it (another type: '-'; changing the type brings the box) */
  function limitCell(S, r) {
    var o = orderOf(r); var t = o.order_type;
    var uses = t === 'LIMIT' || t === 'STOP_LIMIT';
    if (!uses) { return '<span class="muted">-</span>'; }
    if (!typeEditable(S, r)) { return o.limit_px != null ? esc(o.limit_px) : '<span class="muted">-</span>'; }
    return pxInput('limit_px', r, o.limit_px, true, 'limit price');
  }
  function stopCell(S, r) {
    var o = orderOf(r); var t = o.order_type;
    var trail = t === 'TRAILING_STOP'; var uses = trail || t === 'STOP' || t === 'STOP_LIMIT';
    var val = trail ? (o.trail_pct != null ? o.trail_pct + '%' : o.trail_amt) : o.stop_px;
    if (!uses) { return '<span class="muted">-</span>'; }
    if (!(r.item.kind === 'leg' && canEdit(S, r.item))) { return val != null ? esc((trail ? 'trail ' : '') + val) : '<span class="muted">-</span>'; }
    return pxInput(trail ? 'trail' : 'stop_px', r, val, true, trail ? 'trail amount, or a percent ending in %' : 'stop price');
  }
  function tifCell(S, r) {
    var o = orderOf(r); var tif = o.tif || 'DAY';
    var lab = TIFS.filter(function (x) { return x[0] === tif; })[0];
    if (!(r.item.kind === 'leg' && canEdit(S, r.item))) { return esc((lab ? lab[1] : tif) + (tif === 'GTD' && o.gtd_date ? ' ' + o.gtd_date : '')); }
    var h = '<select data-edit="tif" data-n="' + r.n + '" aria-label="time in force of leg ' + r.n + '">';
    TIFS.forEach(function (x) { h += '<option value="' + x[0] + '"' + (x[0] === tif ? ' selected' : '') + '>' + x[1] + '</option>'; });
    h += '</select>';
    if (tif === 'GTD') { h += ' <input type="date" data-edit="gtd_date" data-n="' + r.n + '" value="' + esc(o.gtd_date || '') + '" aria-label="good till date of leg ' + r.n + '">'; }
    return h;
  }
  function instCell(r) {
    var i = instOf(r.leg);
    var words = i.words || i.symbol || i.id || '';
    var h = '<div class="clw smart-inst">' + esc(words || '?') + (i.listed === false ? ' <span class="muted" title="not confirmed listed: the preview checks it">(unverified)</span>' : '') + '</div>';
    var c = i.chain;
    if (c && typeof c === 'object') { h += '<div class="cl2" title="' + esc(c.reason || '') + '">chain ' + esc(c.quality || '') + (c.quote_time ? ' . ' + esc(T.fmt.hm(c.quote_time)) : '') + '</div>'; }
    return h;
  }
  function bookCell(S, r) {
    var g = r.leg; var other = !!(g.book && T.book.code && g.book !== T.book.code);
    var acct = g.account_name || (g.account_id ? T.accountName(g.account_id) : 'no account');
    return '<div class="' + (other ? 'smart-other' : '') + '" title="' + esc((g.account_id || '') + (other ? ' - not the selected book' : '')) + '">' + esc(g.book_name || bookName(g.book)) +
      (other ? ' (not selected)' : '') + '</div><div class="cl2">' + esc(acct) + '</div>';
  }
  function comboCell(r) {
    var it = r.item; if (it.kind !== 'combo') { return ''; }
    var mode = it.fill_mode === 'AON' ? 'all or none' : (it.fill_mode === 'SEQUENTIAL' ? 'shares first' : String(it.fill_mode || '').toLowerCase());
    return '<span title="' + esc((it.src.strategy ? it.src.strategy + ' . ' : '') + (it.fill_mode === 'AON' ? AON_WORDS : 'the shares are the primary; the options wait for them')) + '">' +
      esc(it.key + (r.first ? ' ' + mode : '')) + '</span>';
  }
  /* the flags of a row (its leg's; the combination's on its first row; sorted by severity) as {flag, combo} */
  function rowFlags(r) {
    var out = (r.leg.flags || []).map(function (f) { return { f: f, combo: false }; });
    if (r.first && r.item.kind === 'combo') { (r.item.src.flags || []).forEach(function (f) { out.push({ f: f, combo: true }); }); }
    out.sort(function (a, b) { return (SEV[a.f.severity] == null ? 3 : SEV[a.f.severity]) - (SEV[b.f.severity] == null ? 3 : SEV[b.f.severity]); });
    return out;
  }
  function chipHtml(f, attrs) {
    var sev = SEV[f.severity] != null ? f.severity : 'info';
    var more = (f.choices && f.choices.length) || f.action ? ' ...' : '';
    return '<button type="button" class="smart-chip sev-' + sev + '" ' + attrs + ' title="' + esc(SEV_TITLE[sev] + ': ' + (f.text || '') + (more ? ' (click for the alternatives)' : '')) + '">' +
      esc(SEV_MARK[sev] + short(f.text || '', 38) + more) + '</button>';
  }
  function flagsCell(r) {
    var list = rowFlags(r); var h = ''; var infos = [];
    list.forEach(function (x, i) { if (x.f.severity === 'info' && !(x.f.choices && x.f.choices.length) && !x.f.action) { infos.push(x.f); return; } h += chipHtml(x.f, 'data-act="flag" data-n="' + r.n + '" data-i="' + i + '"'); });
    if (infos.length) {
      h += '<button type="button" class="smart-chip sev-info" data-act="flag" data-n="' + r.n + '" data-i="info" title="' + esc('how it was read:\n' + infos.map(function (f) { return f.text || ''; }).join('\n')) + '">i ' + infos.length + '</button>';
    }
    return h || '<span class="muted">-</span>';
  }
  function execWords(pv) {
    var ex = pv.execution || {};
    if (ex.queued) { return 'queues: waiting for funds'; }
    if (ex.fill_now) { return 'fills now ' + T.fmt.qty(ex.fill_qty_now); }
    if (ex.activates_at) { return 'queued for the next session'; }
    return 'rests (no fill now)';
  }
  function pvLine(S, r) {
    var it = r.item;
    if (!r.first) { return '<span class="muted">with ' + esc(it.key) + '</span>'; }
    if (it.sent || it.moved) { return ''; }
    var b = firstBlocker(itemFlags(it));
    if (b) { return '<span class="neg">fix the red flag first</span>'; }
    var v = validity(it); if (v) { return '<span class="neg">' + esc(v) + '</span>'; }
    if (!previewed(it)) { return '<span class="muted">' + (it.pend ? 'waiting for the chain ...' : 'previewing ...') + '</span>'; }
    var pb = pvBlockers(it);
    if (pb.length) { return '<span class="neg" title="' + esc(pb.map(function (x) { return x.message || ''; }).join('\n')) + '">refused: ' + esc(short(pb[0].message || 'not accepted', 70)) + '</span>'; }
    var parts = [];
    if (it.kind === 'leg') {
      var pv = it.pv || {}; var ec = pv.economics || {}; var fu = pv.funding || {};
      parts.push(execWords(pv));
      if (money(ec.gross)) { parts.push('gross ' + money(ec.gross)); }
      if (money(ec.fee)) { parts.push('fee ' + money(ec.fee)); }
      if (fu.confirm_required) { parts.push('asks your confirm when sent (large order)'); }
    } else {
      var tt = (it.spec && it.spec.totals) || {};
      parts.push(it.fill_mode === 'AON' ? 'all or none' : 'shares first');
      if (money(tt.net_cash)) { parts.push('net cash ' + money(tt.net_cash)); }
      if (money(tt.fees)) { parts.push('fees ' + money(tt.fees)); }
      if (it.ppv) { parts.push('shares: ' + execWords(it.ppv)); }
    }
    return '<span class="smart-pv">' + esc(parts.join(' . ')) + '</span>';
  }
  function stageOf(status) {
    var s = String(status || '').toUpperCase();
    return { WORKING: 'working', PARTIAL: 'partial', FILLED: 'filled', CANCELLED: 'cancelled', EXPIRED: 'expired', REJECTED: 'rejected', SUSPENDED: 'queued', WAITING_PARENT: 'held' }[s] || 'queued';
  }
  function resultCell(S, r) {
    var it = r.item; if (!r.first) { return ''; }
    if (it.sent) {
      var s = it.sent; var id = s.order_id || s.package_id || 'sent';
      var tags = (s.replayed ? ' already submitted' : '') + (s.recovered ? ' recovered' : '') + (s.key !== it.key ? ' (sent as ' + s.key + ')' : '') +
        (it.reread ? ' (the same words now read differently: shown as sent, not offered again)' : '');
      return '<span class="smart-resbox"><a href="#/pending?account=' + encodeURIComponent(s.account_id || it.account_id || '') + '" title="' + esc('request ' + s.rid + ' . see it in Pending Trades') + '">' +
        esc(id) + '</a> <span class="stage-chip stage-' + stageOf(s.status) + '">' + esc(String(s.status || 'sent').toLowerCase().replace(/_/g, ' ')) + '</span>' +
        (tags ? ' <span class="muted">' + esc(tags) + '</span>' : '') + '</span>';
    }
    if (it.moved) { return '<span class="muted">moved to the ticket</span>'; }
    var h = '';
    if (it.result) { h += '<span class="smart-res-' + esc(it.result.state || '') + '" title="' + esc(it.result.text || '') + '">' + esc(short(it.result.text || '', 60)) + '</span> '; }
    if (it.kind === 'leg' && canEdit(S, it)) { h += '<button type="button" class="btn small" data-act="ticket" data-n="' + r.n + '" title="move this order to the ticket of its account page (prefilled; it is then left out of this ticket)">ticket</button>'; }
    return h ? '<span class="smart-resbox">' + h + '</span>' : '';
  }
  function byN(r) { return r.n; }
  function columns(S) {
    function c(key, label, cls, html, title, text) { return { key: key, label: label, cls: cls, html: html, title_h: title, sort: byN, text: text || function () { return ''; } }; }
    return [
      c('sel', 'sel', 'l', function (r) {
        var it = r.item;
        if (!r.first) { return '<span class="muted" title="part of ' + esc(it.key) + '">|</span>'; }
        if (it.sent || it.moved) { return '<span class="muted">-</span>'; }
        return '<input type="checkbox" data-edit="sel" data-key="' + esc(it.key) + '"' + (it.sel ? ' checked' : '') + (S.sending ? ' disabled' : '') + ' aria-label="send ' + esc(it.key) + '">';
      }, 'send it (a combination goes as one order)'),
      c('n', '#', 'num', function (r) { return esc(r.n); }, 'the leg in the order you wrote it', function (r) { return String(r.n); }),
      c('combo', 'combo', 'l', comboCell, 'the combination the leg belongs to'),
      c('book', 'book . account', 'l', function (r) { return bookCell(S, r); }, 'where the order goes'),
      c('side', 'side', 'l', function (r) { return sideCell(S, r); }, 'side'),
      c('qty', 'qty', 'num', function (r) { return qtyCell(S, r); }, 'quantity (shares, contracts)'),
      c('inst', 'instrument', 'l', instCell, 'the instrument in words'),
      c('type', 'type', 'l', function (r) { return typeCell(S, r); }, 'order type'),
      c('limit', 'limit', 'num', function (r) { return limitCell(S, r); }, 'your limit (as typed; the preview judges the tick)'),
      c('stop', 'stop / trail', 'num', function (r) { return stopCell(S, r); }, 'your stop, or the trail (amount, or a percent ending in %)'),
      c('tif', 'TIF', 'l', function (r) { return tifCell(S, r); }, 'time in force'),
      c('flags', 'flags', 'l', flagsCell, 'red !! blocks the order; amber ! look before sending; grey i how it was read'),
      c('pv', 'preview', 'l', function (r) { return pvLine(S, r); }, 'the server\'s preview: fills now, rests, queues, or why not'),
      c('res', 'result', 'l', function (r) { return resultCell(S, r); }, 'what the server answered when it was sent')
    ];
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
  function paintHead(S) {
    var sent = S.sentLog.length;
    ui.sub.textContent = 'ticket ' + S.ticket + ' . selected book: ' + bookName(T.book.code) + ' . ' +
      (sent ? plural(sent, 'order') + ' sent from it' : 'staged, nothing sent') + (S.sending ? ' . sending ...' : '');
  }
  function paintRead(S) {
    var p = S.parsed;
    T.clear(ui.readback); T.clear(ui.iflags);
    if (!p) { ui.readback.hidden = true; ui.iflags.hidden = true; ui.aon.hidden = true; return; }
    ui.readback.hidden = false;
    var c = p.counts || {};
    ui.readback.appendChild(T.h('div', { class: 'smart-read-head' }, 'as read' + (p.as_of ? ' at ' + T.fmt.time(p.as_of) + ' ET' : '') + ' for the ' + bookName(p.selected_book) +
      ' (grammar ' + (p.grammar || '?') + '): ' + plural(Number(c.legs != null ? c.legs : (p.legs || []).length), 'leg') + (c.combos ? ', ' + plural(Number(c.combos), 'combination') : '') +
      ' . ' + plural(Number(c.blockers || 0), 'red flag') + ' . ' + plural(Number(c.checks || 0), 'amber flag') + (S.restored ? ' . restored from this browser - press Enter to read it again' : '')));
    ui.readback.appendChild(T.h('div', { class: 'smart-read-text', html: readbackHtml(p) }));
    var fl = p.flags || [];
    ui.iflags.hidden = !fl.length;
    fl.forEach(function (f, i) {
      var b = T.h('span', { html: chipHtml(f, 'data-iflag="' + i + '"') });
      var btn = b.firstChild; btn.addEventListener('click', function () { openFlag(S, null, f, false); });
      ui.iflags.appendChild(btn);
    });
    ui.aon.hidden = !S.items.some(function (it) { return it.kind === 'combo' && it.fill_mode === 'AON'; });
  }
  function paintActions(S) {
    var why = submitBlock(S);
    var key = T.keys && T.keys.label ? T.keys.label('smart.submit') : '';
    ui.submitBtn.textContent = 'Submit selected' + (key ? ' (' + key + ')' : '');
    ui.submitBtn.disabled = !!why;
    ui.submitBtn.title = why || 'opens the confirm: every order in words; nothing is sent before "Send"';
    ui.previewBtn.disabled = S.sending || !S.items.length;
    ui.parseBtn.disabled = S.sending || S.parsing;
    ui.cancelBtn.disabled = !S.sentLog.length || S.sending;
    ui.newBtn.disabled = S.sending;
    var dirty = !!(S.parsed && ui.bar.value !== S.parsedFrom);
    ui.dirty.hidden = !dirty;
  }
  function paint(S) {
    if (!S || S.dead || !ui) { return; }
    paintHead(S); paintRead(S);
    if (S.grid) { S.grid.update(S.rows, { empty: S.parsed ? 'nothing staged: the text named no order' : 'nothing staged yet: type what you want to trade above and press Enter' }); }
    paintActions(S); paintDetail(S);
  }
  /* the detail under the grid: the clicked item's words as written, how they were read, its whole preview or its result */
  function paintDetail(S) {
    T.clear(ui.detail);
    var it = S.detail ? itemByKey(S, S.detail) : null;
    if (!it) { return; }
    var P = T.components.preview;
    var box = T.h('div', { class: 'panel smart-detail' });
    box.appendChild(T.h('h2', null, it.key + (it.kind === 'combo' ? ' combination' : ' order') + ' . revision ' + it.rev, T.h('small', null, 'request ' + ridOf(S, it)),
      T.h('button', { class: 'btn small', style: 'margin-left:8px', onclick: function () { S.detail = null; paintDetail(S); } }, 'close')));
    it.legs.forEach(function (l) {
      box.appendChild(T.h('div', { class: 'mono' }, 'leg ' + l.n + ': you wrote "' + (l.clause || '') + '"'));
      if (l.inferred && l.inferred.length) { box.appendChild(T.h('div', { class: 'hint' }, 'filled in by FinSim: ' + l.inferred.join('; '))); }
    });
    if (it.sent) { box.appendChild(T.h('div', { class: 'blue-box' }, 'sent: ' + (it.sent.order_id || it.sent.package_id || '') + ' ' + String(it.sent.status || '').toLowerCase() + ' (request ' + it.sent.rid + ')')); }
    if (it.result && it.result.res && P) { box.appendChild(it.kind === 'combo' ? P.packageResult(it.result.res) : P.result(it.result.res)); }
    else if (it.result && it.result.blockers && P) { box.appendChild(P.blockers(it.result.blockers)); }
    if (it.err && P) { box.appendChild(P.blockers([it.err])); }
    if (it.kind === 'leg' && it.pv && P) { var pvEl = T.h('div'); P.render(pvEl, it.pv, { request: it.req }); box.appendChild(pvEl); }
    if (it.kind === 'combo' && it.spec) { box.appendChild(specBox(it)); }
    if (it.kind === 'combo' && it.ppv && P) { box.appendChild(T.h('h2', null, 'the shares leg (primary)')); var ppEl = T.h('div'); P.render(ppEl, it.ppv, { request: primaryReq(it) }); box.appendChild(ppEl); }
    ui.detail.appendChild(box);
  }
  /* a combination's package preview: the server's legs, totals and margin, formatted (no figure of the page's own) */
  function specBox(it) {
    var sp = it.spec || {}; var tt = sp.totals || {}; var mg = sp.margin || {};
    var box = T.h('div', { class: 'section' });
    box.appendChild(T.h('div', null, T.h('b', null, sp.title || 'your own legs'), ' ', T.h('span', { class: 'muted' }, it.fill_mode === 'AON' ? AON_WORDS : 'the shares go first; the options are released as they fill')));
    var t = T.h('table');
    t.appendChild(T.h('thead', null, T.h('tr', null, T.h('th', { class: 'l' }, 'leg'), T.h('th', { class: 'l' }, 'side'), T.h('th', null, 'qty'), T.h('th', null, 'est. price'),
      T.h('th', { class: 'l' }, 'basis'), T.h('th', null, 'cash'), T.h('th', null, 'fee'))));
    var tb = T.h('tbody');
    (sp.legs || []).forEach(function (l) {
      tb.appendChild(T.h('tr', null, T.h('td', { class: 'l' }, l.what || l.symbol || l.instrument_id || ''), T.h('td', { class: 'l' }, SIDE_WORDS[l.side] || l.side || ''),
        T.h('td', { class: 'num' }, T.fmt.qty(l.qty)),
        T.h('td', { class: 'num' }, l.est_price != null && T.px ? T.px(Object.assign({}, l.quote || {}, { value: l.est_price }), { field: 'value', size: 'inline', basis: l.price_basis }) : 'n/a'),
        T.h('td', { class: 'l' }, l.price_basis || ''),
        T.h('td', { class: 'num' }, money(l.cash) || 'n/a'), T.h('td', { class: 'num' }, money(l.fee) || 'n/a')));
    });
    t.appendChild(tb); box.appendChild(t);
    box.appendChild(T.kv([['net cash', money(tt.net_cash) || 'n/a'], ['fees', money(tt.fees) || 'n/a'], ['net cash after fees', money(tt.net_cash_after_fees) || 'n/a'],
      ['option margin', money(tt.option_margin) || 'n/a']]));
    if (mg.note || mg.detail) { box.appendChild(T.h('div', { class: 'hint' }, mg.note || mg.detail)); }
    (sp.warnings || []).forEach(function (w) { box.appendChild(T.h('div', { class: 'warn-box' }, typeof w === 'string' ? w : (w && w.message) || '')); });
    if (sp.chain_quality) { box.appendChild(T.h('div', { class: 'hint' }, 'priced from the Cboe chain (' + sp.chain_quality + ')' + (sp.as_of ? ' as of ' + T.fmt.time(sp.as_of) + ' ET' : ''))); }
    return box;
  }
  function openFlag(S, r, f, combo) {
    var sev = SEV[f.severity] != null ? f.severity : 'info';
    var body = T.h('div', { class: 'smart-dialog' });
    body.appendChild(T.h('div', { class: sev === 'blocker' ? 'error-box' : (sev === 'check' ? 'warn-box' : 'blue-box') }, f.text || ''));
    if (r) { body.appendChild(T.h('div', { class: 'hint' }, 'leg ' + r.n + ': you wrote "' + (r.leg.clause || '') + '"')); }
    var h = null;
    var ch = (f.choices || []).filter(function (x) { return x && typeof x.clause === 'string'; });
    if (ch.length) {
      body.appendChild(T.h('h2', null, 'listed alternatives (each one replaces the words and reads the text again; nothing is applied by itself)'));
      ch.forEach(function (x) {
        body.appendChild(T.h('div', { class: 'row', style: 'margin:3px 0' }, T.h('button', { class: 'btn small', onclick: function () { if (h) { h.close(); } choose(S, r, Object.assign({ $combo: !!combo }, f), x); } }, x.label || x.clause),
          T.h('span', { class: 'muted mono' }, x.clause)));
      });
    }
    var acts = [{ label: 'Close' }];
    if (f.action && f.action.label) { acts.push({ label: f.action.label, onClick: function (m) { m.close(); runAction(S, r, f.action); } }); }
    h = T.ui.modal({ title: (r ? 'Leg ' + r.n + ': ' : 'The instruction: ') + SEV_TITLE[sev], body: body, actions: acts });
  }
  function openInfos(S, r) {
    var list = rowFlags(r).map(function (x) { return x.f; }).filter(function (f) { return f.severity === 'info'; });
    var body = T.h('div', { class: 'smart-dialog' });
    list.forEach(function (f) { body.appendChild(T.h('div', { class: 'blue-box' }, f.text || '')); });
    if (r.leg.inferred && r.leg.inferred.length) { body.appendChild(T.h('div', { class: 'hint' }, 'filled in by FinSim: ' + r.leg.inferred.join('; '))); }
    body.appendChild(T.h('div', { class: 'hint' }, 'leg ' + r.n + ': you wrote "' + (r.leg.clause || '') + '"'));
    T.ui.modal({ title: 'Leg ' + r.n + ': how it was read', body: body, actions: [{ label: 'Close' }] });
  }
  function onGridClick(S, r, e) {
    if (!r) { return; }
    var b = e.target.closest('[data-act]');
    if (b) {
      var act = b.dataset.act;
      if (act === 'flag') {
        if (b.dataset.i === 'info') { openInfos(S, r); return; }
        var x = rowFlags(r)[Number(b.dataset.i)]; if (x) { openFlag(S, r, x.f, x.combo); }
        return;
      }
      if (act === 'ticket') { var o = orderOf(r); var i = instOf(r.leg);
        openTicket(S, r, { account_id: r.leg.account_id, instrument_id: (r.item.req && r.item.req.instrument_id) || i.id, kind: i.kind || undefined, side: o.side, qty: o.qty,
                           order_type: o.order_type, limit_px: o.limit_px, stop_px: o.stop_px, tif: o.tif, gtd_date: o.gtd_date }, r.item);
        return; }
      return;
    }
    if (e.target.closest('input, select, textarea, a')) { return; }
    S.detail = r.item.key; paintDetail(S);
  }

  /* ---------------------------------------------------------------- the confirm ----------------------------------- */
  function typeWords(o) {
    var t = o.order_type;
    if (t === 'LIMIT') { return 'limit ' + o.limit_px; }
    if (t === 'STOP') { return 'stop ' + o.stop_px; }
    if (t === 'STOP_LIMIT') { return 'stop ' + o.stop_px + ' limit ' + o.limit_px; }
    if (t === 'TRAILING_STOP') { return 'trailing ' + (o.trail_pct != null ? o.trail_pct + ' %' : o.trail_amt); }
    return 'at market';
  }
  function tifWords(o) { return o.tif === 'GTD' ? 'good till ' + (o.gtd_date || '?') : (o.tif === 'NEXT_SESSION' ? 'next session' : (o.tif || 'DAY')); }
  function whereWords(it) {
    var g = it.legs[0] || {};
    var other = it.book && T.book.code && it.book !== T.book.code;
    return (g.book_name || bookName(it.book)) + ' . ' + (g.account_name || T.accountName(it.account_id)) + (other ? ' (not the selected book)' : '');
  }
  function legWords(r) { var o = orderOf(r); var i = instOf(r.leg); return (SIDE_WORDS[o.side] || o.side || '?') + ' ' + T.fmt.qty(o.qty) + ' ' + (i.words || i.symbol || i.id || '?'); }
  function confirmLine(S, it) {
    var li = T.h('li');
    var rows = S.rows.filter(function (r) { return r.item === it; });
    if (it.kind === 'leg') {
      var o = it.req; var pv = it.pv || {}; var ec = pv.economics || {}; var fu = pv.funding || {};
      li.appendChild(T.h('div', null, T.h('b', null, it.key + ' . ' + whereWords(it) + ': '), (rows[0] ? legWords(rows[0]) : it.key) + ' ' + typeWords(o) + ' ' + tifWords(o)));
      var figs = [execWords(pv)];
      if (money(ec.gross)) { figs.push('gross ' + money(ec.gross) + (ec.currency ? ' ' + ec.currency : '')); }
      if (money(ec.fee)) { figs.push('fee ' + money(ec.fee)); }
      if (money(fu.sweep_in) && Number(fu.sweep_in) > 0) { figs.push('sweep-in from Treasury ' + money(fu.sweep_in)); }
      li.appendChild(T.h('div', { class: 'muted' }, 'the server\'s preview: ' + figs.join(' . ')));
      if (fu.confirm_required) {
        var rules = Array.isArray(fu.confirm_rules) ? fu.confirm_rules.filter(function (x) { return x && x.text; }).map(function (x) { return x.text; }) : [];
        li.appendChild(T.h('div', { class: 'smart-warn' }, 'a large order: ' + (rules.length ? rules.join('; ') : (fu.confirm_reason || 'it needs your confirmation')) + ' - sending stops here and asks you again'));
      }
    } else {
      var tt = (it.spec && it.spec.totals) || {};
      li.appendChild(T.h('div', null, T.h('b', null, it.key + ' . ' + whereWords(it) + ': '),
        rows.map(function (r) { var o2 = orderOf(r); return legWords(r) + (o2.order_type === 'LIMIT' ? ' limit ' + o2.limit_px : ''); }).join(it.fill_mode === 'SEQUENTIAL' ? ', then ' : ' + ') + ' ' + tifWords(it.req)));
      var f2 = [];
      if (money(tt.net_cash)) { f2.push('net cash ' + money(tt.net_cash)); }
      if (money(tt.fees)) { f2.push('fees ' + money(tt.fees)); }
      if (money(tt.option_margin)) { f2.push('option margin ' + money(tt.option_margin)); }
      if (it.ppv) { f2.push('shares: ' + execWords(it.ppv)); }
      li.appendChild(T.h('div', { class: 'muted' }, 'the server\'s package preview: ' + (f2.length ? f2.join(' . ') : 'no totals')));
      li.appendChild(T.h('div', { class: it.fill_mode === 'AON' ? 'smart-warn' : 'muted' }, it.fill_mode === 'AON' ? 'one combination: ' + AON_WORDS : 'one combination: the shares first, the options released as they fill'));
    }
    return li;
  }
  function submit() {
    var S = st; if (!S || S.dead) { return; }
    var why = submitBlock(S);
    if (why) { say(S, 'not sent: ' + why, 'err'); T.ui.toast('Smart Ticket: ' + why, 'err', 3500); return; }
    var list = selectedItems(S); var n = list.length;
    var body = T.h('div', { class: 'smart-confirm smart-dialog' });
    body.appendChild(T.h('div', null, 'These ' + plural(n, 'order') + ' go out one at a time, in this order; the first refusal stops the rest. Nothing has been sent yet.'));
    var ol = T.h('ol'); list.forEach(function (it) { ol.appendChild(confirmLine(S, it)); }); body.appendChild(ol);
    body.appendChild(T.h('div', { class: 'hint' }, 'Ticket ' + S.ticket + '. Each order is checked again just before it is sent and goes with the preview listed here;' +
      ' a moved price or a large order asks you again (moved: more than 0.5 % against you since this preview, or this preview older than 2 minutes).'));
    var label = 'Send ' + plural(n, 'order');
    /* what the owner confirms is exactly what goes: no edit and no new reading while the confirm is open, and "Send" checks that every
       listed item is still staged with the same terms (else nothing is sent).  Second review: the preview each order was confirmed with
       (the figures listed above, taken now: the 15-s refresh may replace it.pv while the confirm is open) is the one its POST carries */
    var sigs = list.map(function (it) { return it.sig; });
    var pvids = list.map(confirmedPv);
    S.confirming = true; paint(S);
    var h = T.ui.modal({ title: label + '?', body: body, sticky: true, wide: true,
      onDismiss: function () { say(S, 'not sent (Back)'); },
      onClose: function () { S.confirming = false; paint(S); },
      actions: [{ label: 'Back', id: 'smart-back', onClick: function (m) { m.close(); say(S, 'not sent (Back)'); } },
                { label: label, cls: 'primary', id: 'smart-send', onClick: function (m, btn) { btn.disabled = true; m.close(); sendAll(S, list, sigs, pvids); } }] });
    var back = h.el.querySelector('#smart-back');
    if (back) { try { back.focus(); } catch (e) { /* focus is a convenience */ } }
  }

  /* ---------------------------------------------------------------- the send loop --------------------------------- */
  /* the words typed for this item (kept with the command: the audit trail of what was written): a combination's own text when the
     reader gives it, else its legs' clauses */
  function clauseOf(it) {
    if (it.kind === 'combo' && typeof it.src.clause === 'string' && it.src.clause) { return short(it.src.clause, 400); }
    var seen = []; it.legs.forEach(function (l) { if (l.clause && seen.indexOf(l.clause) < 0) { seen.push(l.clause); } });
    return short(seen.join(' | '), 400);
  }
  /* a fresh preview just before the send: {blockers, pv | spec + ppv} */
  function fresh(S, it) {
    function more(out) {
      if (out.pending) {
        if (it.pend >= PKG_TRIES) { return { blockers: [{ code: 'PENDING', message: 'the chain did not load: use Load the chain, then send again' }] }; }
        it.pend += 1; return wait(out.retry_ms).then(function () { return previewItem(it).then(more); });
      }
      it.pend = 0; it.err = null; it.pvSig = it.sig;
      if (it.kind === 'leg') { it.pv = out.pv; } else { it.spec = out.spec; it.ppv = out.ppv; }
      return { blockers: pvBlockers(it), pv: out.pv, ppv: out.ppv, spec: out.spec };
    }
    return previewItem(it).then(more, function (e) {
      it.err = blockerOf(e); it.pv = it.spec = it.ppv = null; it.pvSig = it.sig;
      return { blockers: [it.err] };
    });
  }
  /* the id of the preview the confirm lists for an item: an order's own, a sequential combination's shares leg's (an all-or-none
     combination sends none: its legs fill at their bid / ask, no net limit) */
  function confirmedPv(it) {
    if (it.kind === 'leg') { return (it.pv && it.pv.preview_id) || null; }
    return it.fill_mode === 'SEQUENTIAL' ? ((it.ppv && it.ppv.preview_id) || null) : null;
  }
  /* the POST body: the request, the smart note and the preview the owner CONFIRMED (it.cpv, second review), never one taken after the
     click - so a price that moved against the owner since the confirm comes back 409 PREVIEW_STALE and asks again (askStale); the fresh
     preview of sendOne only checks that nothing new blocks the order (its id is used only when the confirm had none) */
  function bodyOf(S, it, fr) {
    var b = clone(it.req);
    var pid = it.cpv || (it.kind === 'leg' ? (fr.pv && fr.pv.preview_id) : (it.fill_mode === 'SEQUENTIAL' && fr.ppv ? fr.ppv.preview_id : null));
    if (pid && (it.kind === 'leg' || it.fill_mode === 'SEQUENTIAL')) { b.preview_id = pid; }
    b.confirm_large = false;
    b.smart = { ticket: S.ticket, item: it.key, clause: clauseOf(it) };
    return b;
  }
  function sendAll(S, list, sigs, pvids) {
    if (S.sending || S.dead) { return; }
    var changed = list.some(function (it, i) { return S.items.indexOf(it) < 0 || it.sent || it.moved || !it.sel || it.sig !== sigs[i]; });
    if (changed) { say(S, 'the staged orders changed after the confirm opened: nothing was sent - open the confirm again', 'err'); paint(S); return; }
    list.forEach(function (it, i) { it.cpv = (pvids && pvids[i]) || null; });
    S.sending = true; S.run = { list: list, i: 0, sent: 0, skipped: 0 };
    S.items.forEach(function (it) { clearTimeout(it.timer); }); S.queue = [];
    say(S, 'sending ' + plural(list.length, 'order') + ', one at a time ...'); paint(S);
    next(S);
  }
  function next(S) {
    var run = S.run; if (!run) { return; }
    if (S.dead) { finish(S, 'the page was left: the rest were not sent'); return; }
    if (run.i >= run.list.length) { finish(S, null); return; }
    var it = run.list[run.i];
    if (S.items.indexOf(it) < 0 || it.sent) { run.i++; next(S); return; }
    say(S, 'sending ' + (run.i + 1) + ' of ' + run.list.length + ': ' + it.key + ' (fresh preview first) ...');
    it.result = { state: 'sending', text: 'sending ...' }; paint(S);
    sendOne(S, it).then(function (outcome) {
      if (outcome === 'sent') { run.sent++; run.i++; next(S); }
      else if (outcome === 'skip') { run.skipped++; run.i++; next(S); }
      else { finish(S, it.key + ': ' + ((it.result && it.result.text) || 'stopped')); }
    }, function (e) {
      console.error('smart ticket send', it.key, e);
      it.result = { state: 'error', text: 'not sent: ' + T.api.describe(e) };
      finish(S, it.key + ': ' + it.result.text);
    });
  }
  function finish(S, why) {
    var run = S.run; S.run = null; S.sending = false;
    kick();
    if (S.dead) { return; }                                  /* its sends are already recorded (persistSent); a newer page owns the draft */
    var all = run && run.list.every(function (it) { return !!it.sent; });
    if (all) { T.store.set(STORE, null); S.cleared = true; } else { saveState(S); }
    var n = run ? run.list.length : 0;
    if (!why) { say(S, (all ? 'all ' + plural(n, 'order') + ' sent' : 'sent ' + run.sent + ' of ' + n + (run.skipped ? ', skipped ' + run.skipped : '')) + '. They are in Pending Trades.', 'ok'); }
    else { say(S, 'sent ' + (run ? run.sent : 0) + ' of ' + n + '; stopped at ' + why, 'err'); }
    S.items.forEach(function (it) { if (!it.sent && !it.moved) { schedule(S, it, false); } });
    paint(S);
  }
  function record(S, it, rid, res) {
    var o = (res && res.order) || {}; var p = (res && res.package) || {};
    var first = (res && Array.isArray(res.orders) && res.orders[0]) || {};
    var rec = { key: it.key, sig: it.sig, clause: clauseOf(it), rid: rid, account_id: (it.req && it.req.account_id) || it.account_id, book: it.book, order_id: o.order_id || first.order_id || null,
                package_id: p.package_id || o.package_id || null, status: p.status || o.status || first.status || 'SENT', replayed: !!(res && res.replayed),
                recovered: !!(res && res.recovered), at: T.clock.nowInt() };
    S.sentLog.push(rec); it.sent = rec; it.sel = false;
    it.result = { state: 'sent', text: 'sent', res: res };
    persistSent(S, rec);
    return 'sent';
  }
  /* a send that answers after the page was left is still recorded: in the page of the same ticket shown now, else in the stored draft
     (merged, never overwriting a newer draft), so a later visit shows it sent and never offers it again */
  function persistSent(S, rec) {
    if (!S.dead) { saveState(S); return; }
    var live = st;
    if (live && !live.dead && live.ticket === S.ticket) {
      if (!live.sentLog.some(function (s) { return s.rid === rec.rid; })) { live.sentLog.push(rec); }
      var used = {}; live.items.forEach(function (x) { matchSent(live, x, used); });
      saveState(live); paint(live); return;
    }
    var d = T.store.get(STORE, null);
    if (d && d.ticket === S.ticket) {
      var list = Array.isArray(d.sent) ? d.sent : [];
      if (!list.some(function (s) { return s && s.rid === rec.rid; })) { d.sent = list.concat([rec]); T.store.set(STORE, d); }
    } else if (!d) { saveState(S); }
  }
  function sendOne(S, it) {
    return fresh(S, it).then(function (fr) {
      if (fr.blockers && fr.blockers.length) {
        it.result = { state: 'refused', text: 'not sent: the fresh preview refuses it: ' + (fr.blockers[0].message || 'not accepted'), blockers: fr.blockers };
        return 'stop';
      }
      return post(S, it, bodyOf(S, it, fr), ridOf(S, it));
    });
  }
  function post(S, it, body, rid) {
    var opts = { request_id: rid };
    /* third review: a lost answer of a single order is recovered only when the order under its id has this item's terms (else the
       answer is REQUEST_ID_REUSED, handled below like the server's own) */
    if (it.kind === 'leg') { opts.reconcile = body.account_id; opts.match = function (o, list) { return sameTerms(it.req || body, list, rid); }; }
    return T.api.post('/api/orders' + bookQ(it.book), body, opts).then(function (res) {
      return record(S, it, rid, res);
    }, function (e) {
      if (e && e.name === 'LostResponse') {
        it.result = { state: 'lost', text: 'lost answer (' + e.message + '): check Pending Trades before sending it again' };
        return 'stop';
      }
      if (e && e.code === 'NETWORK' && it.kind === 'combo') { return reconcileCombo(S, it, rid, body); }
      /* R1 (1.0.7.2 review): this ticket's own request id already carries an order (a reload or a second window between the send and
         its answer): it is looked up; second review: recorded as sent ("already submitted") only when that order has THIS item's terms
         - the server replays the same terms itself, so a 409 means another window sent other terms under the id - else refused */
      if (e && e.code === 'REQUEST_ID_REUSED' && String(rid).indexOf('st:' + S.ticket + ':') === 0) { return reconcileCombo(S, it, rid, body, e); }
      var d = detailsOf(e);
      if (e && e.code === 'PREVIEW_STALE') { return askStale(S, it, body, rid, e, d); }
      if (e && e.code === 'CONFIRM_REQUIRED') { return askConfirm(S, it, body, rid, e, d); }
      it.result = { state: 'refused', text: 'refused: ' + T.api.describe(e), blockers: [blockerOf(e)] };
      if (e && e.status >= 500) { console.error('smart ticket submit', it.key, e); }
      return 'stop';
    });
  }
  /* an item whose answer was lost (or whose own request id already carries an order): its request id (a single order, a sequential
     primary), rid:L1 (all-or-none legs) or rid:H1 (the primary's children) is looked up in the account's orders before anything else
     is sent.  Third review: the order recorded is the item's own - the request id itself (a single order, the shares order of a
     sequential package) or the first all-or-none leg - never a waiting leg (the list is newest first, so rid:H1 came first) */
  function reconcileCombo(S, it, rid, body, err) {
    var acct = (it.req && it.req.account_id) || it.account_id;
    return T.api.get('/api/accounts/' + encodeURIComponent(acct) + '/orders', { status: 'all', limit: 2000 }).then(function (rows) {
      var list = Array.isArray(rows) ? rows : (rows && rows.orders) || [];
      var by = {};
      list.forEach(function (o) { if (o && typeof o.request_id === 'string' && !by[o.request_id]) { by[o.request_id] = o; } });
      var hit = by[rid] || by[rid + ':L1'] || by[rid + ':H1'] || null;
      if (hit && sameTerms(it.req || body, list, rid)) {
        return record(S, it, rid, { order: hit, package: { package_id: hit.package_id, status: hit.status }, recovered: true, replayed: true });
      }
      if (hit) {
        /* another order (other terms) already went under this id: nothing of THIS item was placed; its revision moves on so that sending it
           again places it under a new id, never answered as that other order */
        var next = bumpRev(S, it);
        it.result = { state: 'refused', text: 'refused: request ' + rid + ' already carries ' + (hit.order_id || 'an order') + ' (' + orderWords(hit) + '), not these terms - ' +
                      'nothing was sent for this item; it is now revision r' + next + ': send it again to place it as a new order',
                      blockers: [{ code: (err && err.code) || 'REQUEST_ID_REUSED', message: 'request ' + rid + ' already carries ' + (hit.order_id || 'another order') }] };
        return 'stop';
      }
      it.result = { state: 'lost', text: 'lost answer and no order found under request ' + rid + ': check Pending Trades before sending it again' };
      return 'stop';
    }, function () {
      it.result = { state: 'lost', text: 'lost answer; the orders could not be re-read: do not send it again until Pending Trades answers' };
      return 'stop';
    });
  }
  /* whether the orders under request ``rid`` (a single order, a sequential primary rid with its waiting legs rid:H1.., or all-or-none
     legs rid:L1..) are the item's request ``req``: account, instrument, side, quantity, type, prices and time in force.  Third review: a
     sequential package's option leg is matched on its type and limit too, and on its size through its ratio (the leg's quantity over the
     shares' at 8 decimals, which a release never changes; a leg still waiting also keeps its whole quantity) - another call quantity
     under the same id is another order */
  function sameTerms(req, orders, rid) {
    if (!req) { return false; }
    var by = {}; (orders || []).forEach(function (o) { if (o && typeof o.request_id === 'string') { by[o.request_id] = o; } });
    function px(a, b) { return (a == null || a === '') ? (b == null || b === '') : (b != null && b !== '' && Number(a) === Number(b)); }
    function tifOf(o) { return o.gtd_date ? 'GTD' : (o.activate_ts != null ? 'NEXT_SESSION' : o.tif); }
    function one(o, r, sized) {
      if (!o || o.account_id !== req.account_id || o.instrument_id !== r.instrument_id || o.side !== r.side) { return false; }
      if (!sized) { return true; }
      return Number(o.qty) === Number(r.qty) && o.order_type === (r.order_type || 'MARKET') && px(o.limit_px, r.limit_px) && px(o.stop_px, r.stop_px) &&
        px(o.trail_amt, r.trail_amt) && px(o.trail_pct, r.trail_pct) && tifOf(o) === (req.tif || 'DAY') && (req.tif !== 'GTD' || String(o.gtd_date || '').slice(0, 10) === req.gtd_date);
    }
    var pl = req.package && Array.isArray(req.package.legs) ? req.package.legs : null;
    if (!pl) { return one(by[rid], req, true); }
    /* a sequential package's option leg (rid:H1..): its type and limit as sent, its size through its ratio */
    function waiting(o, l) {
      if (!one(o, l, false) || o.order_type !== (l.order_type || 'MARKET') || !px(o.limit_px, l.limit_px)) { return false; }
      if (o.status === 'WAITING_PARENT' && Number(o.qty) !== Number(l.qty)) { return false; }
      var want = Number(l.qty) / Number(pl[0].qty);
      return o.ratio != null && o.ratio !== '' && isFinite(want) ? Math.abs(Number(o.ratio) - want) < 1e-8 : Number(o.qty) === Number(l.qty);
    }
    /* each leg of the request matched to one order of its own under the id (rid:H1.. waiting legs, rid:L1.. all-or-none legs) */
    function each(legs, prefix, match) {
      var got = Object.keys(by).filter(function (k) { return k.indexOf(prefix) === 0; }).map(function (k) { return by[k]; });
      if (got.length !== legs.length) { return false; }
      var used = {};
      return legs.every(function (l) {
        for (var j = 0; j < got.length; j++) { if (!used[j] && match(got[j], l)) { used[j] = true; return true; } }
        return false;
      });
    }
    if (by[rid]) {                                     /* sequential: the shares leg is the primary (listed first), the options wait */
      return one(by[rid], pl[0], true) && each(pl.slice(1), rid + ':H', waiting);
    }
    return each(pl, rid + ':L', function (o, l) { return one(o, l, true); });
  }
  function orderWords(o) {
    return (SIDE_WORDS[o.side] || o.side || '?') + ' ' + T.fmt.qty(o.qty) + ' ' + (o.symbol || o.instrument_id || '?') + (o.order_type === 'LIMIT' && o.limit_px ? ' limit ' + o.limit_px : '');
  }
  /* the item's next revision, kept with the draft: a resend gets a request id never used for other terms */
  function bumpRev(S, it) {
    var r = S.revs[it.key];
    it.rev = Math.max(Number(it.rev) || 0, r ? Number(r.rev) || 0 : 0) + 1;
    S.revs[it.key] = { rev: it.rev, sig: it.sig };
    saveState(S);
    return it.rev;
  }
  /* the owner's choice in a sticky dialog: resolves once, whatever closes it (Esc / x = Stop) */
  function ask(spec) {
    return new Promise(function (resolve) {
      var settled = false;
      function settle(v) { if (!settled) { settled = true; resolve(v); } }
      var h = T.ui.modal({ title: spec.title, body: spec.body, sticky: true, wide: true, onDismiss: function () { settle('stop'); },
        actions: spec.actions.map(function (a) { return { label: a.label, cls: a.cls, id: a.id, onClick: function (m) { m.close(); settle(a.value); } }; }) });
      var stop = h.el.querySelector('#smart-stop'); if (stop) { try { stop.focus(); } catch (e) { /* focus is a convenience */ } }
    });
  }
  function askStale(S, it, body, rid, e, d) {
    var freshPv = d.preview && typeof d.preview === 'object' ? d.preview : null;
    it.result = { state: 'stale', text: 'the price moved since the preview: waiting for your choice' }; paint(S);
    var P = T.components.preview;
    var box = T.h('div', { class: 'smart-dialog' }, T.h('div', null, it.key + ' (' + whereWords(it) + '): ' + T.api.describe(e)));
    if (P && P.diff) {
      var dbox = P.diff(d.diff || {}, {});
      T.qa('button', dbox).forEach(function (b) { if (b.parentNode) { b.parentNode.removeChild(b); } });
      T.qa('.hint', dbox).forEach(function (x) { x.textContent = 'Accept & continue sends this order again under the same request id with the fresh preview; Skip leaves it unsent and goes on; Stop ends the sending here.'; });
      box.appendChild(dbox);
    }
    if (P && freshPv && freshPv.execution) { var pvEl = T.h('div'); P.render(pvEl, freshPv, { request: body }); box.appendChild(pvEl); }
    return ask({ title: 'The price moved: ' + it.key, body: box, actions: [{ label: 'Stop', id: 'smart-stop', value: 'stop' }, { label: 'Skip', id: 'smart-skip', value: 'skip' },
                                                                        { label: 'Accept & continue', cls: 'primary', id: 'smart-accept', value: 'accept' }] }).then(function (v) {
      if (v === 'skip') { it.result = { state: 'skipped', text: 'skipped: not sent (the price moved)' }; return 'skip'; }
      if (v !== 'accept') { it.result = { state: 'stopped', text: 'not sent: you stopped when the price moved' }; return 'stop'; }
      var again = Object.assign({}, body);
      if (freshPv && freshPv.preview_id) { again.preview_id = freshPv.preview_id; return post(S, it, again, rid); }
      if (it.kind === 'combo' && it.fill_mode !== 'SEQUENTIAL') { delete again.preview_id; return post(S, it, again, rid); }
      return fresh(S, it).then(function (fr) {
        if (fr.blockers && fr.blockers.length) { it.result = { state: 'refused', text: 'not sent: the fresh preview refuses it: ' + (fr.blockers[0].message || ''), blockers: fr.blockers }; return 'stop'; }
        var pid = it.kind === 'leg' ? (fr.pv && fr.pv.preview_id) : (fr.ppv && fr.ppv.preview_id);
        if (pid) { again.preview_id = pid; } else { delete again.preview_id; }
        return post(S, it, again, rid);
      });
    });
  }
  function askConfirm(S, it, body, rid, e, d) {
    var rules = Array.isArray(d.rules) ? d.rules.filter(function (x) { return x && x.text; }).map(function (x) { return x.text; }) : [];
    var reason = rules.length ? rules.join('; ') : (d.reason || T.api.describe(e));
    it.result = { state: 'confirm', text: 'a large order: waiting for your confirmation (' + reason + ')' }; paint(S);
    var sweep = d.sweep_in != null ? T.fmt.money(d.sweep_in) : (typeof d.sweep_in_c === 'number' && d.sweep_in_c > 0 ? T.fmt.cents(d.sweep_in_c) : null);
    var box = T.h('div', { class: 'smart-dialog' },
      T.h('div', null, it.key + ' (' + whereWords(it) + '): a large order - ' + reason + '.'),
      sweep ? T.h('div', { class: 'hint' }, 'It sweeps ' + sweep + ' from Treasury' + (d.threshold ? ' (the confirm threshold is ' + T.fmt.money(d.threshold) + ')' : '') + '.') : null,
      d.notional ? T.h('div', { class: 'hint' }, 'notional ' + T.fmt.money(d.notional) + (d.nav ? ' . lifetime NAV ' + T.fmt.money(d.nav) : '')) : null,
      T.h('div', { class: 'hint' }, 'Confirm and send sends this same order again (request ' + rid + ') with your confirmation; nothing was written yet. Skip leaves it unsent and goes on; Stop ends the sending here.'));
    return ask({ title: 'Confirm the large order: ' + it.key, body: box, actions: [{ label: 'Stop', id: 'smart-stop', value: 'stop' }, { label: 'Skip', id: 'smart-skip', value: 'skip' },
                                                                                  { label: 'Confirm and send', cls: 'primary', id: 'smart-confirm-large', value: 'confirm' }] }).then(function (v) {
      if (v === 'skip') { it.result = { state: 'skipped', text: 'skipped: not sent (a large order you did not confirm)' }; return 'skip'; }
      if (v !== 'confirm') { it.result = { state: 'stopped', text: 'not sent: you stopped at the large-order confirmation' }; return 'stop'; }
      var again = Object.assign({}, body, { confirm_large: true });
      if (d.preview && d.preview.preview_id) { again.preview_id = d.preview.preview_id; }
      return post(S, it, again, rid);
    });
  }

  /* ---------------------------------------------------------------- cancel what this ticket sent ------------------ */
  function cancelSent() {
    var S = st; if (!S || S.dead || S.sending) { return; }
    var prefix = 'st:' + S.ticket + ':';
    var bookOf = {};
    S.sentLog.forEach(function (s) { if (s.account_id) { bookOf[s.account_id] = s.book || (T.accountOwner(s.account_id) || {}).book_code || null; } });
    var accts = Object.keys(bookOf).sort();
    if (!accts.length) { say(S, 'this ticket has sent nothing yet'); return; }
    say(S, 'looking for the open orders of ticket ' + S.ticket + ' ...');
    Promise.all(accts.map(function (a) {
      return T.api.get('/api/accounts/' + encodeURIComponent(a) + '/orders', { status: 'open' }).then(function (r) {
        return ((r && r.orders) || []).filter(function (o) { return o && typeof o.request_id === 'string' && o.request_id.indexOf(prefix) === 0; })
          .map(function (o) { return Object.assign({}, o, { book: bookOf[a] }); });
      });
    })).then(function (lists) {
      if (S.dead) { return; }
      var open = [].concat.apply([], lists);
      if (!open.length) { say(S, 'nothing this ticket sent is still open'); return; }
      var body = T.h('div', { class: 'smart-dialog' }, T.h('div', null, 'Cancel ' + plural(open.length, 'open order') + ' that ticket ' + S.ticket + ' sent?'));
      var ul = T.h('ul', { class: 'mono' });
      open.slice(0, 12).forEach(function (o) {
        ul.appendChild(T.h('li', null, (o.order_id || '') + ': ' + String(o.side || '').replace(/_/g, ' ') + ' ' + T.fmt.qty((Number(o.qty) || 0) - (Number(o.filled_qty) || 0)) + ' ' + (o.symbol || o.instrument_id || '') +
          ' . ' + T.accountName(o.account_id) + ' . ' + bookName(o.book)));
      });
      if (open.length > 12) { ul.appendChild(T.h('li', { class: 'muted' }, '... and ' + (open.length - 12) + ' more')); }
      body.appendChild(ul);
      body.appendChild(T.h('div', { class: 'hint' }, 'One cancel per book; an order that ends meanwhile is reported, never cancelled twice.'));
      T.ui.confirm(body, { title: 'Cancel what this ticket sent', ok: 'Cancel ' + plural(open.length, 'order'), cancel: 'Keep' }).then(function (yes) {
        if (!yes || S.dead) { return; }
        cancelByBook(S, open, 'cancelled by the owner (Smart Ticket ' + S.ticket + ')');
      });
    }, function (e) { if (!S.dead) { say(S, 'could not read the open orders: ' + T.api.describe(e), 'err'); } });
  }
  function cancelByBook(S, orders, reason) {
    /* every order listed in the confirm is cancelled by its own command: a hedge leg goes BEFORE its primary (third review: the server
       reads a leg listed after its primary as "ends with it", which would keep a released leg working) */
    var placed = {}, listed = [];
    function put(o) { if (!placed[o.order_id]) { placed[o.order_id] = true; listed.push(o); } }
    orders.forEach(function (o) {
      orders.forEach(function (k) { if (k.parent_order_id && k.parent_order_id === o.order_id) { put(k); } });
      put(o);
    });
    var groups = {}, order = [];
    listed.forEach(function (o) { var b = o.book || ''; if (!groups[b]) { groups[b] = []; order.push(b); } if (groups[b].indexOf(o.order_id) < 0) { groups[b].push(o.order_id); } });
    var batches = [];
    order.forEach(function (b) { for (var i = 0; i < groups[b].length; i += BATCH_MAX) { batches.push({ book: b, ids: groups[b].slice(i, i + BATCH_MAX) }); } });
    var tot = { cancelled: 0, skipped: 0, failed: 0, kept: 0, errors: [] };
    function one(k) {
      if (k >= batches.length) { return Promise.resolve(); }
      var bt = batches[k];
      return T.api.post('/api/orders/cancel' + bookQ(bt.book), { order_ids: bt.ids, reason: reason }, { request_id: 'mc:' + hex12() }).then(function (r) {
        tot.cancelled += ((r && r.cancelled) || []).length; tot.skipped += ((r && r.skipped) || []).length; tot.failed += ((r && r.failed) || []).length;
        tot.kept += ((r && r.kept) || []).length;
      }, function (e) { tot.errors.push(bookName(bt.book) + ': ' + T.api.describe(e)); }).then(function () { return one(k + 1); });
    }
    say(S, 'cancelling ...');
    one(0).then(function () {
      kick();
      var msg = 'cancelled ' + tot.cancelled + (tot.skipped ? ' . already ended ' + tot.skipped : '') + (tot.failed ? ' . failed ' + tot.failed : '') +
        (tot.kept ? ' . kept working ' + tot.kept : '') + (tot.errors.length ? ' . refused: ' + tot.errors.join('; ') : '');
      T.ui.toast(msg, tot.failed || tot.errors.length ? 'err' : 'ok', 6000);
      if (!S.dead) { say(S, msg, tot.failed || tot.errors.length ? 'err' : 'ok'); }
    });
  }

  /* ---------------------------------------------------------------- the page -------------------------------------- */
  function newTicketNow() {
    var S = st; if (!S || S.dead || S.sending) { return; }
    function go() {
      S.items.forEach(function (it) { clearTimeout(it.timer); });
      S.pseq++; S.ticket = newTicket(); S.parsed = null; S.parsedFrom = null; S.items = []; S.rows = []; S.revs = {}; S.sentLog = []; S.moved = {}; S.queue = [];
      S.restored = false; S.detail = null; S.text = ''; ui.bar.value = ''; S.cleared = false;
      T.store.set(STORE, null);
      paint(S); say(S, 'a new ticket: ' + S.ticket); try { ui.bar.focus(); } catch (e) { /* focus is a convenience */ }
    }
    var unsent = S.items.filter(function (it) { return !it.sent && !it.moved; }).length;
    if (!unsent) { go(); return; }
    T.ui.confirm('Start a new ticket? The ' + plural(unsent, 'staged order') + ' not sent are dropped (nothing was sent for them).', { title: 'New ticket', ok: 'New ticket', cancel: 'Keep this one' })
      .then(function (yes) { if (yes) { go(); } });
  }
  function render(el, route) {
    unmount();
    void route;
    var S = st = { dead: false, ticket: null, text: '', parsed: null, parsedFrom: null, items: [], rows: [], revs: {}, sentLog: [], moved: {}, queue: [], flight: 0,
                   pseq: 0, parsing: false, sending: false, run: null, restored: false, detail: null, grid: null, savedAt: null };
    var page = T.h('div', { class: 'smart-page' }); el.appendChild(page);
    ui = {};
    ui.newBtn = T.h('button', { class: 'btn', id: 'smart-new', title: 'drop what is staged and start a new ticket (a new ticket id)', onclick: newTicketNow }, 'New ticket');
    var head = T.pageHead(page, 'Smart Ticket', 'ticket', [ui.newBtn]);
    ui.sub = head.querySelector('.sub');
    ui.bar = T.h('textarea', { id: 'smart-text', rows: '3', maxlength: String(MAX_CHARS), spellcheck: 'false', autocomplete: 'off', 'data-search': '1',
                               'aria-label': 'what you want to trade', placeholder: 'type what you want to trade, e.g. buy 200 ACME limit 225, sell 2 ACME Dec 18 2026 240 calls' });
    ui.parseBtn = T.h('button', { class: 'btn primary', id: 'smart-parse', title: 'read the instruction (Enter in the box); nothing is sent' }, 'Parse');
    ui.parseBtn.addEventListener('click', function () { parse(); });
    page.appendChild(T.h('div', { class: 'smart-bar' }, ui.bar, ui.parseBtn));
    page.appendChild(T.h('div', { class: 'hint' }, 'Enter reads it, Shift+Enter starts a new line, Esc clears the box. Several orders: separate them with a comma, "and", ";" or a new line. Nothing is sent until you confirm.'));
    ui.dirty = T.h('div', { class: 'smart-warn', hidden: true }, 'the text changed since it was read: press Enter to read it again');
    page.appendChild(ui.dirty);
    page.appendChild(T.h('div', { class: 'smart-examples' }, T.h('span', null, 'examples (fictional names):'), EXAMPLES.map(function (x) {
      return T.h('a', { href: '#/smart', title: 'put this example in the box; nothing is read until you press Enter', onclick: function (e) { e.preventDefault(); ui.bar.value = x; S.text = x; paintActions(S); try { ui.bar.focus(); } catch (er) { /* ignore */ } } }, x);
    })));
    ui.readback = T.h('div', { class: 'smart-readback', id: 'smart-readback', hidden: true }); page.appendChild(ui.readback);
    ui.iflags = T.h('div', { class: 'smart-iflags', hidden: true }); page.appendChild(ui.iflags);
    ui.aon = T.h('div', { class: 'smart-warn', hidden: true }, 'All-or-none combinations have no net limit in this version: their legs fill together at each leg\'s executable bid / ask, or not at all.');
    page.appendChild(ui.aon);
    ui.gridEl = T.h('div', { class: 'smart-grid', id: 'smart-grid' }); page.appendChild(ui.gridEl);
    ui.previewBtn = T.h('button', { class: 'btn', id: 'smart-preview-all', title: 'preview every staged order now (nothing is sent)', onclick: function () { if (!S.sending) { S.items.forEach(function (it) { it.pend = 0; schedule(S, it, true); }); say(S, 'previewing ...'); } } }, 'Preview all');
    ui.submitBtn = T.h('button', { class: 'btn primary', id: 'smart-submit', onclick: submit }, 'Submit selected');
    ui.cancelBtn = T.h('button', { class: 'btn', id: 'smart-cancel-sent', title: 'cancel the orders of this ticket that are still open (listed first, then confirmed)', onclick: cancelSent }, 'Cancel what this ticket sent');
    ui.status = T.h('div', { class: 'smart-status', id: 'smart-status', role: 'status' });
    page.appendChild(T.h('div', { class: 'smart-actions' }, ui.previewBtn, ui.submitBtn, ui.cancelBtn, T.h('span', { class: 'muted' }, '!! blocks . ! look before sending . i how it was read')));
    page.appendChild(ui.status);
    ui.detail = T.h('div', { id: 'smart-detail' }); page.appendChild(ui.detail);
    page.appendChild(T.convNote(['SMART_TICKET', 'AON']));
    ui.bar.addEventListener('keydown', function (e) {
      if (e.isComposing) { return; }
      var plain = !e.shiftKey && !e.ctrlKey && !e.altKey && !e.metaKey;
      if (e.key === 'Enter' && plain) { e.preventDefault(); parse(); return; }
      if ((e.key === 'Escape' || e.key === 'Esc') && plain && ui.bar.value) { e.preventDefault(); ui.bar.value = ''; S.text = ''; saveState(S); paintActions(S); say(S, 'the box is cleared (what is staged stays until you read a new text or start a new ticket)'); }
    });
    ui.bar.addEventListener('input', function () { S.text = ui.bar.value; paintActions(S); });
    S.grid = T.components.grid.mount(ui.gridEl, { columns: columns(S), rows: [], tools: false, empty: 'nothing staged yet: type what you want to trade above and press Enter',
      rowKey: function (r) { return 'L' + r.n; }, rowClass: function (r) { return r.item && r.item.sent ? 'smart-sent' : ''; },
      onClick: function (r, e) { onGridClick(S, r, e); }, onEnter: function (r) { if (r) { S.detail = r.item.key; paintDetail(S); } } });
    ui.gridEl.addEventListener('change', onChange);
    var restored = restore(S);
    if (!restored) { S.ticket = newTicket(); }
    ui.bar.value = S.text || '';
    if (!T.modulesKnown() || T.hasModule('smart_ticket')) { say(S, restored && S.parsed ? 'restored ticket ' + S.ticket + ' from this browser' + (S.savedAt ? ' (saved ' + T.fmt.hm(Math.floor(S.savedAt / 1000)) + ' ET)' : '') + ': press Enter to read it again before sending' : ''); }
    else { say(S, 'the Smart Ticket\'s reader is not in this build: the page can show nothing to send', 'err'); }
    paint(S);
    T.poll.page('smart', REFRESH_MS, refreshAll);
    try { ui.bar.focus({ preventScroll: true }); } catch (e) { /* focus is a convenience */ }
  }
  function unmount() {
    var S = st; if (!S) { return; }
    if (ui) { S.text = ui.bar.value; }
    saveState(S);
    S.dead = true;
    S.items.forEach(function (it) { clearTimeout(it.timer); });
    if (S.grid) { try { S.grid.destroy(); } catch (e) { /* the page is gone anyway */ } }
    st = null; ui = null;
  }
  T.pages.smart = { render: render, unmount: unmount, submit: submit, sameTerms: sameTerms };     /* sameTerms: pure, read by the browser smoke */
})();
