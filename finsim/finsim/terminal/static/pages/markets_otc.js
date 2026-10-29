/* PB page -> Markets -> OTC (Build 2 P12; BUILD2_PLAN C11 plug-in T.marketPlugins.PB with tabs ['otc'], C21; PO-01, PO-14, PO-16, PO-20).
 *
 * The OTC tab of a prime-broker account is this plug-in (pages/markets.js hands it the body for the 'otc' tab; the PB equities /
 * options / futures tabs stay the standard grid):
 *
 *   - the relationship line: "ISDA Master / CSA / PBA with <dealer>, signed <date>" (PO-16), the variation-margin mode and the
 *     dealer's OTC terms (half-widths, initial margin) with a link to edit them in Settings > Prime brokers;
 *   - the eligibility up front (PO-14): when the book may not book OTC (!(pb_otc && ecp)) the reason is shown above the tickets
 *     and Preview / Book are disabled in every sub-tab;
 *   - sub-tabs Swaps (the swap ticket, components/swapticket.js, mounted with its notes off) | Options | Forwards
 *     (components/otcticket.js) above ONE contracts table - the swap ticket's, which renders every OTC kind (C21); under the
 *     Options / Forwards sub-tabs only that table of the swap ticket is shown;
 *   - no instruments grid, no add-symbol box and no order conventions here (PO-20): the notes of OTC dealing are printed once at
 *     the bottom.
 *
 * Reads GET /api/otc/options/desk?account_id=  (relationship, eligibility, the dealer's terms, pairs, tenors) - again every 60 s,
 * so a NAV that crosses the ECP floor blocks or unblocks the tickets in place.  The ASCII-only file needs core.js only.
 */
(function () {
  'use strict';
  var T = window.T = window.T || {};
  T.marketPlugins = T.marketPlugins || {};
  T.components = T.components || {};

  var SUBTABS = [{ key: 'swaps', label: 'Swaps' }, { key: 'options', label: 'Options' }, { key: 'forwards', label: 'Forwards' }, { key: 'credit', label: 'Credit (CDX)' }];
  var NOTES = ['SWAP_MARK', 'SWAP_VM', 'SWAP_RESET', 'OTC_OPTION', 'OTC_FORWARD', 'OTC_VM', 'CDX_PROXY', 'CDX_DEAL', 'CDX_COUPON', 'ELIGIBILITY'];
  var DESK_MS = 60000;
  var sub = {};                                     /* account -> the sub-tab last used */
  var desks = [];                                   /* the mounted desks (a swap prefill brings its account's desk to Swaps) */

  /* the hedge panel's TRS overlay fills the swap ticket (swapticket.prefill, C21): its account's desk shows the Swaps sub-tab first,
     whether the desk is on screen or about to open (the swap forms are hidden under Options / Forwards) */
  function wrapPrefill() {
    var sw = T.components.swapticket;
    if (!sw || typeof sw.prefill !== 'function' || sw.prefill.otcDesk) { return; }
    var orig = sw.prefill;
    var wrapped = function (kind, terms, opts) {
      var acct = opts && opts.account_id;
      if (acct) { sub[acct] = 'swaps'; }
      desks.forEach(function (d) { if (!acct || d.account_id === acct) { d.showSwaps(); } });
      return orig.apply(this, arguments);
    };
    wrapped.otcDesk = true;
    sw.prefill = wrapped;
  }

  var cssDone = false;
  function ensureCss() {
    if (cssDone || document.getElementById('markets-otc-css')) { cssDone = true; return; }
    cssDone = true;
    var s = document.createElement('style');
    s.id = 'markets-otc-css';
    /* under Options / Forwards the swap ticket shows only its contracts table (the one table of every OTC kind) */
    s.textContent = '.otc-desk.sub-options .otc-swaps .swapticket > :not(.contracts), .otc-desk.sub-forwards .otc-swaps .swapticket > :not(.contracts), .otc-desk.sub-credit .otc-swaps .swapticket > :not(.contracts) { display: none; }' +
      '.otc-desk .otc-head { display: flex; flex-wrap: wrap; gap: 4px 16px; align-items: baseline; margin: 4px 0 8px; }' +
      '.otc-desk .otc-head .rel { font-weight: 600; }' +
      '.otc-desk .otc-blocked { border-left: 3px solid var(--down, #c55); margin: 6px 0; }' +
      '.otc-desk .otc-subtabs { margin: 6px 0; }' +
      /* the desk states the eligibility reason once, above every ticket (PO-14): the swap ticket's own copy of it is not repeated */
      '.otc-desk .otc-swaps .swapticket .st-blocked { display: none; }';
    document.head.appendChild(s);
  }

  function pctText(x) { var n = Number(x); return isFinite(n) && x !== '' && x != null ? (n * 100).toFixed(2).replace(/\.00$/, '') + ' %' : 'n/a'; }
  function volText(x) { var n = Number(x); return isFinite(n) && x !== '' && x != null ? (n * 100).toFixed(2) + ' vol pts' : 'n/a'; }

  function render(el, ctx) {
    ensureCss();
    var acct = ctx.account_id;
    var state = { sub: sub[acct] || 'swaps', desk: null, blocked: null, dead: false, timer: null };
    var root = T.h('div', { class: 'otc-desk sub-' + state.sub });
    el.appendChild(root);
    var head = T.h('div', { class: 'otc-head' });
    var blockedBox = T.h('div', { class: 'conv otc-blocked', hidden: true });
    var tabsBox = T.h('div', { class: 'otc-subtabs' });
    var ticketBox = T.h('div', { class: 'otc-ticket', hidden: true });
    var swapBox = T.h('div', { class: 'otc-swaps' });
    var cdsBox = T.h('div', { class: 'otc-cds', hidden: true });
    var notes = T.h('div');
    root.appendChild(head); root.appendChild(blockedBox); root.appendChild(tabsBox); root.appendChild(ticketBox); root.appendChild(cdsBox); root.appendChild(swapBox); root.appendChild(notes);

    /* the eligibility the shell already knows, before the desk answers (PO-14: the reason up front) */
    var e0 = ((T.book && T.book.current && T.book.current()) || {}).eligibility || {};
    if (e0 && (e0.pb_otc === false || e0.ecp === false)) { state.blocked = e0.pb_otc_reason || 'this book is not eligible for OTC bookings'; }

    wrapPrefill();
    var swapH = T.mountOptional('swapticket', swapBox, { account_id: acct, notes: false, blocked: state.blocked }, 'the swap ticket and the OTC contracts table');
    var otcH = null;
    var cdsH = null;
    var me = { account_id: acct, showSwaps: function () { if (state.sub !== 'swaps') { state.sub = 'swaps'; sub[acct] = 'swaps'; paintSub(); } } };
    desks.push(me);

    function paintHead() {
      T.clear(head);
      var d = state.desk;
      if (!d) { head.appendChild(T.h('span', { class: 'muted' }, 'loading the relationship ...')); return; }
      var rel = d.relationship || {};
      var t = d.terms || {};
      head.appendChild(T.h('span', { class: 'rel', title: (rel.agreements || []).map(function (a) { return a.label + ': ' + a.meaning + ' (signed ' + a.signed_date + ')'; }).join('\n') },
        rel.agreements_line || ('prime broker ' + (d.name || ''))));
      head.appendChild(T.h('span', { class: 'muted', title: 'the Credit Support Annex: how variation margin moves' }, 'VM ' + (t.swap_margin_mode === 'STM' ? 'settled to market' : 'collateralised to market') + ' (CSA)'));
      head.appendChild(T.h('span', { class: 'muted', title: 'the dealer\'s OTC terms (assumed, editable in Settings > Prime brokers)' },
        'half-widths: equity options ' + volText(t.eq_option_hw_vol) + ', FX options ' + volText(t.fx_option_hw_vol) + ', forwards ' + (t.forward_hw_bp || 'n/a') + ' bp' +
        (t.otc_spread_mult && Number(t.otc_spread_mult) !== 1 ? ' (x ' + t.otc_spread_mult + ')' : '') + ' . IM: sold options ' + pctText(t.option_im_pct) + ', forwards ' + pctText(t.forward_im_pct) +
        ', IRS ' + (t.irs_im_bp || 'n/a') + ' bp of DV01, TRS ' + pctText(t.trs_im_pct) + ', XCCY ' + pctText(t.xccy_im_pct)));
      if (t.xccy_basis_eur_bp != null) {             /* FX_CCS_PLAN D5: the cross-currency terms, each an assumed, editable schedule term */
        head.appendChild(T.h('span', { class: 'muted', title: 'cross-currency terms (ASSUMED, editable in Settings > Prime brokers): the basis levels sit on the EUR / GBP discount curves of the FX forwards and the cross-currency swaps; the EUR OIS spread on the ECB AAA curve' },
          'cross-currency: EUR/USD basis ' + t.xccy_basis_eur_bp + ' bp, GBP/USD ' + t.xccy_basis_gbp_bp + ' bp, EUR OIS spread ' + t.eur_ois_spread_bp + ' bp, swap half-width ' +
          t.xccy_basis_hw_bp + ' bp (ASSUMED)'));
      }
      head.appendChild(T.h('a', { href: '#/settings/pb', title: 'edit this dealer\'s schedule' }, 'terms'));
    }
    function paintBlocked() {
      blockedBox.textContent = state.blocked ? 'OTC bookings are refused for this book: ' + state.blocked + '. The relationship stays open; swaps, options and forwards already booked keep running.' : '';
      blockedBox.hidden = !state.blocked;
      if (swapH && swapH.setBlocked) { swapH.setBlocked(state.blocked); }
      if (otcH && otcH.setBlocked) { otcH.setBlocked(state.blocked); }
      if (cdsH && cdsH.setBlocked) { cdsH.setBlocked(state.blocked); }
    }
    function paintTabs() {
      T.clear(tabsBox).appendChild(T.tabs(SUBTABS, state.sub, function (k) {
        if (k === state.sub) { return; }
        state.sub = k; sub[acct] = k;
        paintSub();
      }));
    }
    function paintSub() {
      root.className = 'otc-desk sub-' + state.sub;
      paintTabs();
      cdsBox.hidden = state.sub !== 'credit';
      if (state.sub === 'credit') {
        ticketBox.hidden = true;
        if (!cdsH) {
          cdsH = T.mountOptional('cdsticket', cdsBox, { account_id: acct, blocked: state.blocked, note: false,
            onBooked: function () { if (swapH && swapH.refresh) { swapH.refresh(); } } }, 'the CDX (CDS index) ticket');
        }
        return;
      }
      if (state.sub === 'swaps') { ticketBox.hidden = true; return; }
      ticketBox.hidden = false;
      if (!otcH) {
        otcH = T.mountOptional('otcticket', ticketBox, { account_id: acct, mode: state.sub, blocked: state.blocked, desk: state.desk, note: false,
          onBooked: function () { if (swapH && swapH.refresh) { swapH.refresh(); } } }, 'the OTC options and forwards ticket');
      } else if (otcH.setMode) { otcH.setMode(state.sub); }
    }
    function loadDesk() {
      return T.api.get('/api/otc/options/desk', { account_id: acct }).then(function (d) {
        if (state.dead) { return; }
        state.desk = d;
        state.blocked = d.blocked || null;
        paintHead(); paintBlocked();
        if (otcH && otcH.setDesk) { otcH.setDesk(d); }
      }, function (e) {
        if (state.dead) { return; }
        T.clear(head).appendChild(T.h('span', { class: 'error' }, 'the relationship could not be loaded: ' + T.api.describe(e) + ' - retrying'));
      });
    }

    paintHead(); paintBlocked(); paintSub();
    notes.appendChild(T.convNote(NOTES));
    loadDesk();
    state.timer = setInterval(function () { if (!state.dead && document.body.contains(root)) { loadDesk(); } }, DESK_MS);
    return {
      unmount: function () {
        state.dead = true;
        var i = desks.indexOf(me); if (i >= 0) { desks.splice(i, 1); }
        if (state.timer) { clearInterval(state.timer); state.timer = null; }
        if (swapH && swapH.unmount) { try { swapH.unmount(); } catch (e) { console.error('swapticket unmount', e); } }
        if (otcH && otcH.unmount) { try { otcH.unmount(); } catch (e) { console.error('otcticket unmount', e); } }
        if (cdsH && cdsH.unmount) { try { cdsH.unmount(); } catch (e) { console.error('cdsticket unmount', e); } }
      }
    };
  }

  T.marketPlugins.PB = { tabs: [{ key: 'otc', label: 'OTC' }], render: render };
})();
