"""FinSim 1.0.7.2 simplify review round 10 (on 2f108c1): the guard - the regression tests of the class it closes, kept in the terminal suite.

Rounds 8 and 9 found one class four times: ONE real double click on a Submit / Confirm sent or booked two orders (the order ticket's
Submit, the FX desk's Confirm, the bond ticket's Submit, Submit package).  Each was fixed where it was found; round 10 closes the class at
both ends.

The page.  Every control that can place, replace, amend, cancel or book anything - the order ticket (Submit, its submit keys, Accept &
submit, Submit package), the desks (FX, bonds, private trades and credit, repos, securities lending, loans, the PB financing, collateral,
the treasury), the OTC quote requests and their Accept / Decline / Request again, Pending's and the blotters' Cancel / Resume and the mass
cancel, the positions' exercise and return, every dialog's action (core.js T.ui.modal: one place for every dialog), the Smart Ticket's
hand-off - acts through ONE guard, ``T.guard`` in core.js: one request in flight per control; a second activation - the second click of a
double click, a key held down, a press while the control's request is in flight or inside the double-click time (500 ms) after its last
press or its answer - never sends and says why (on the control's own line, or in a toast when its first click acted: its answer is
there).
  InventoryTests  reads every write request the client can make (T.api.post / patch / del / mutate in static/): each is a SEND - reached
                  only through T.guard (directly, through a function that runs its argument through it - creditdesk's ``clicker``,
                  swapticket's ``guardRequest``, pending's ``massGuard`` ... - or as a dialog's action), a test per control - or NOT A
                  SEND, listed with its reason (a preview, a parse, a setting, a watchlist ...).  A new write request fails the test until
                  it is one or the other.
  PageTests       the real page (an offline server on port 0 in a temp home, the round 9 world: the Personal and the Fund books, the
                  fictional ACME / WIDG): the guard's rules; a real double click on a dialog's action acts once; Pending's Cancel
                  activated twice at once (a double click's two clicks) asks once and, confirmed once, sends one cancel.

The server.  Where a route already takes a preview id or a confirm token, that id is single-use for its order kind: a second submission
with it under another request id (a double click that got through, a resubmission after a lost answer) is answered as the submission that
used it - its stored answer, replayed (X-Replayed: 1) - and nothing is placed, amended, replaced or booked twice.  Nothing else changes:
the answer a single submission gets is the same, a refusal (CONFIRM_REQUIRED, PREVIEW_STALE ...) leaves the id free, an expired preview
is still PREVIEW_STALE.  commands.first_use / commands.AnsweredAs; orders._preview_used; products/swaps.h_rfq_accept.
  SingleUseTests     a test per order kind: a new order (a fill), a sequential package, an all-or-none package, an amend, a replace;
  RfqSingleUseTests  an OTC quote's acceptance (its rfq_id): booked once.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import tempfile
import time
import unittest
from urllib.parse import urlsplit

from tests import test_terminal_rfq as RFQ
from tests import test_terminal_simplify_r9 as R9
from tests.terminal_cdp import NO_BROWSER, Browser, find_browser
from tests.terminal_helpers import execute
from tests.test_terminal_smart_submit import ACCT, SmartSubmitBase, acme, leg
from finsim.terminal import config
from finsim.terminal.clock import FakeClock
from finsim.terminal.quotes.providers import FakeProvider

TERMINAL = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "finsim", "terminal")
STATIC = os.path.join(TERMINAL, "static")


def _read(path: str) -> str:
    with open(path, encoding="utf-8") as fh:
        return fh.read()


# ==== the static client, as the inventory reads it ==================================================================================
_ID = r"[A-Za-z_$][\w$]*"
_CALL = re.compile(r"T\.api\.(?:post|patch|del|mutate)\(")
_GUARD = re.compile(r"T\.guard(?:\.on)?\(")
_MODAL = re.compile(r"T\.ui\.modal\(")
_FUNC = re.compile(r"(?<![\w$.])function(?:\s+(" + _ID + r"))?\s*\(")
#: what an event handler's function follows: addEventListener('click', ... / onclick = ... / onClick: ... / onKey: ...
_HANDLER = re.compile(r"(?:addEventListener\(\s*'\w+'\s*,|(?<![\w$])on[A-Za-z]\w*\s*(?::|=(?!=)))\s*$")
_ASSIGNED = re.compile(r"(?<![\w$])(" + _ID + r")\s*(?::|=(?!=))\s*$")
_REGEX_AFTER = set("(,=:[!&|?{};+-*<>~^%") | {""}


def js_mask(src: str) -> str:
    """``src`` with every comment, string body and regular expression blanked (the same length, line breaks kept): only the code's
    brackets and names are left to read."""
    out = list(src)
    i, n = 0, len(src)
    last_sig, last_word = "", ""
    while i < n:
        c = src[i]
        if c in " \t\r\n":
            i += 1
            continue
        if src.startswith("//", i):
            j = src.find("\n", i)
            j = n if j < 0 else j
            out[i:j] = " " * (j - i)
            i = j
            continue
        if src.startswith("/*", i):
            j = src.find("*/", i + 2)
            j = n if j < 0 else j + 2
            out[i:j] = [ch if ch == "\n" else " " for ch in src[i:j]]
            i = j
            continue
        if c in "'\"`":
            k = i + 1
            while k < n and src[k] != c:
                k += 2 if src[k] == "\\" else 1
            k = min(k, n)
            out[i + 1:k] = [ch if ch == "\n" else "x" for ch in src[i + 1:k]]
            i, last_sig = k + 1, "s"
            continue
        if c == "/" and (last_sig in _REGEX_AFTER or last_word in ("return", "typeof", "case", "in", "of")):
            k, in_class = i + 1, False
            while k < n and src[k] != "\n":
                ch = src[k]
                if ch == "\\":
                    k += 2
                    continue
                if ch == "[":
                    in_class = True
                elif ch == "]":
                    in_class = False
                elif ch == "/" and not in_class:
                    break
                k += 1
            out[i + 1:k] = "x" * (k - i - 1)
            i = k + 1
            while i < n and src[i].isalpha():
                i += 1
            last_sig = "s"
            continue
        if c.isalnum() or c in "_$":
            k = i
            while k < n and (src[k].isalnum() or src[k] in "_$"):
                k += 1
            last_word, last_sig, i = src[i:k], "w", k
            continue
        last_sig, last_word = c, ""
        i += 1
    return "".join(out)


def _pairs(m: str) -> dict:
    """Each opening bracket's offset -> its closing bracket's offset in the masked text."""
    out, stack = {}, []
    for k, ch in enumerate(m):
        if ch in "([{":
            stack.append(k)
        elif ch in ")]}":
            out[stack.pop()] = k
    return out


class Client:
    """The static client read for its write requests: each file masked (js_mask), its functions, the functions that run an argument
    through T.guard (wrappers) and where the guard's runs and the dialogs' actions are."""

    def __init__(self):
        self.src, self.m, self.pairs, self.fns = {}, {}, {}, {}
        for root, _d, names in os.walk(STATIC):
            for nm in names:
                if nm.endswith(".js"):
                    rel = os.path.relpath(os.path.join(root, nm), STATIC).replace(os.sep, "/")
                    self.src[rel] = _read(os.path.join(root, nm))
                    self.m[rel] = js_mask(self.src[rel])
                    self.pairs[rel] = _pairs(self.m[rel])
        for rel in self.src:
            self.fns[rel] = self._functions(rel)
        # a wrapper: a function that runs its parameter through T.guard - the parameter is T.guard's run (its third argument) or is
        # called inside the guard's arguments (creditdesk's clicker(fn), swapticket's guardRequest(key, ev, run) ...); its calls in its
        # own file, and as .name( where the file exports it (name: name), are guard runs too
        self.wrappers = {rel: set() for rel in self.src}
        self.exported = set()
        for rel in self.src:
            m = self.m[rel]
            for x in re.finditer(r"(?<![\w$.])(" + _ID + r")\s*:\s*(" + _ID + r")\s*[,}]", m):
                if x.group(1) == x.group(2):
                    self.exported.add(x.group(1))
            for a, b in self._spans(rel, _GUARD):
                args = self._args(rel, a, b)
                for f in self.fns[rel]:
                    if f["name"] and f["body"][0] < a < f["body"][1] and any(
                            (len(args) > 2 and args[2].strip() == p) or re.search(r"(?<![\w$.])" + re.escape(p) + r"\s*\(", m[a:b]) for p in f["params"]):
                        self.wrappers[rel].add(f["name"])
        # a guard's run: the argument list of T.guard( / T.guard.on( and of a wrapper's call (a wrapper's own name is not in it)
        self.guards = {rel: self._spans(rel, _GUARD) + self._wrapper_calls(rel) for rel in self.src}
        self.modals = {rel: self._spans(rel, _MODAL) for rel in self.src}
        self._memo: dict = {}

    def line(self, rel: str, k: int) -> int:
        return self.src[rel].count("\n", 0, k) + 1

    def _spans(self, rel, rx) -> list:
        return [(g.end() - 1, self.pairs[rel][g.end() - 1]) for g in rx.finditer(self.m[rel])]

    def _args(self, rel, a, b) -> list:
        """The top-level arguments of the call whose brackets are at ``a`` .. ``b`` (masked text)."""
        m, out, depth, start = self.m[rel], [], 0, a + 1
        for k in range(a + 1, b):
            ch = m[k]
            if ch in "([{":
                depth += 1
            elif ch in ")]}":
                depth -= 1
            elif ch == "," and depth == 0:
                out.append(m[start:k])
                start = k + 1
        out.append(m[start:b])
        return out

    def _wrapper_calls(self, rel) -> list:
        out, m = [], self.m[rel]
        names = [(w, r"(?<![\w$.])") for w in self.wrappers[rel]]
        names += [(w, r"\.\s*") for r2, ws in self.wrappers.items() if r2 != rel for w in ws if w in self.exported]
        for w, before in names:
            for g in re.finditer(before + re.escape(w) + r"\(", m):
                if not m[max(0, g.start() - 9):g.start()].endswith("function "):
                    out.append((g.end() - 1, self.pairs[rel][g.end() - 1]))
        return out

    def _functions(self, rel) -> list:
        m, p, out = self.m[rel], self.pairs[rel], []
        for f in _FUNC.finditer(m):
            po = f.end() - 1
            pc = p[po]
            bo = m.index("{", pc)
            head = m[max(0, f.start() - 80):f.start()]
            name, prop = f.group(1), False
            if name is None:
                g = _ASSIGNED.search(head)
                if g:
                    name, prop = g.group(1), True
            out.append({"name": name, "prop": prop, "start": f.start(), "head": head, "body": (bo, p[bo]),
                        "params": [x.strip() for x in m[po + 1:pc].split(",") if x.strip()]})
        return out

    def sites(self) -> list:
        """(file, line, offset, the request's text from ``T.api.`` to the end of its line) of every write request of the client but the
        three of the request layer itself (core.js T.api.post / patch / del)."""
        out = []
        for rel in sorted(self.src):
            for c in _CALL.finditer(self.m[rel]):
                text = self.src[rel][c.start():].split("\n", 1)[0]
                if rel == "core.js" and re.match(r"T\.api\.mutate\('(POST|PATCH|DELETE)', path, body, opts\); \},$", text):
                    continue
                out.append((rel, self.line(rel, c.start()), c.start(), text))
        return out

    # ---- reached only through the guard? ----------------------------------------------------------------------------------------
    def guarded(self, rel: str, k: int, depth: int = 0) -> tuple:
        """(True, how) when the code at offset ``k`` of ``rel`` runs only inside a guard's run or a dialog's action, else (False, why)."""
        if any(a <= k <= b for a, b in self.guards[rel]):
            return True, "inside T.guard"
        if depth > 14:
            return False, "too deep to follow"
        for f in sorted((f for f in self.fns[rel] if f["body"][0] < k < f["body"][1]), key=lambda f: -f["body"][0]):
            if re.search(r"(?<![\w$])onClick\s*:\s*$", f["head"]) and any(a < f["start"] < b for a, b in self.modals[rel]):
                return True, "a dialog's action (T.ui.modal guards every action but Cancel)"
            if _HANDLER.search(f["head"]):
                return False, f"an event handler at {rel}:{self.line(rel, f['start'])} reaches it without T.guard"
            if f["name"]:
                return self._fn_guarded(rel, f, depth + 1)
        return False, f"{rel}:{self.line(rel, k)} at the top level"

    def _fn_guarded(self, rel: str, f: dict, depth: int) -> tuple:
        key = (rel, f["start"])
        if key not in self._memo:
            self._memo[key] = (True, "a recursive call: decided by its other callers")
            self._memo[key] = self._callers_guarded(rel, f, depth)
        return self._memo[key]

    def _callers_guarded(self, rel: str, f: dict, depth: int) -> tuple:
        name, m = f["name"], self.m[rel]
        calls, methods = [], {name} if f["prop"] else set()
        for g in re.finditer(r"(?<![\w$.])" + re.escape(name) + r"(?![\w$])", m):
            k = g.start()
            before, after = m[max(0, k - 80):k], m[g.end():g.end() + 24]
            if before.endswith("function ") or re.match(r"\s*(?::|=(?!=))\s*function\b", after):
                continue                                                  # its own definition
            if re.match(r"\s*:(?!:)", after):
                continue                                                  # a property of the same name
            if re.search(r"(?<![\w$])onClick\s*:\s*$", before) and any(a < k < b for a, b in self.modals[rel]):
                continue                                                  # a dialog's action
            if _HANDLER.search(before):
                return False, f"{name} is an event handler at {rel}:{self.line(rel, k)} without T.guard"
            ex = None if re.match(r"\s*\(", after) else re.search(r"(" + _ID + r")\s*(?::|=(?!=))\s*$", before)
            if ex:                                                        # exported (key: name / x.key = name): called as .key(
                methods.add(ex.group(1))
                continue
            calls.append((rel, k))
        for mn in methods:
            for other in self.src:
                calls += [(other, g.start()) for g in re.finditer(r"\.\s*" + re.escape(mn) + r"\(", self.m[other])]
        if not calls:
            return False, f"{name} ({rel}:{self.line(rel, f['start'])}) is called nowhere the inventory can follow"
        for r2, k in calls:
            ok, why = self.guarded(r2, k, depth)
            if not ok:
                return False, f"{name} called at {r2}:{self.line(r2, k)}: {why}"
        return True, f"every call of {name} is guarded"


# ==== the inventory ===================================================================================================================
#: every write request that places, replaces, amends, cancels or books something: (file, its text from "T.api.") -> (the controls that
#: send it, what holds them: a guard as written in the file, or "T.ui.modal(" - a dialog's action, guarded in core.js for every dialog)
SENDS = {
    ("components/ticket.js", "T.api.post('/api/orders', body"): ("the order ticket: a new order or a package (Submit, its submit keys, Accept & submit, "
                                                                 "Submit package, the Smart Ticket's ticket)", ("T.guard('ticket.submit'", "T.guard('ticket.package'")),
    ("components/ticket.js", "T.api.patch('/api/orders/' + encodeURIComponent(oidm)"): ("the order ticket: an amend (Submit)", ("T.guard('ticket.submit'",)),
    ("components/ticket.js", "T.api.post('/api/orders/' + encodeURIComponent(oidm) + '/replace'"): ("the order ticket: a replace (Submit)", ("T.guard('ticket.submit'",)),
    ("components/fxdesk.js", "T.api.post('/api/fx/deal'"): ("the FX desk: Confirm and its submit keys", ("T.guard('fx.confirm'",)),
    ("components/bondticket.js", "T.api.post('/api/bonds/trade'"): ("the bond ticket: Submit and its submit keys", ("T.guard('bond.submit'",)),
    ("components/privateticket.js", "T.api.post('/api/private/trades'"): ("the private trade ticket: Submit", ("var tradeKey = 'private.submit:'", "T.guard(tradeKey")),
    ("components/privatecredit.js", "T.api.post(PATH, b)"): ("private credit: Commit", ("T.guard('pcredit.commit'",)),
    ("components/privatecredit.js", "T.api.post(PATH + '/' + encodeURIComponent(loan.loan_id) + '/fee'"): ("private credit: a fee (its dialog)", ("T.ui.modal(",)),
    ("components/privatecredit.js", "T.api.post(PATH + '/' + encodeURIComponent(loan.loan_id) + '/repay'"): ("private credit: a repayment (its dialog)", ("T.ui.modal(",)),
    ("components/creditdesk.js", "T.api.post('/api/credit/loans', {"): ("the credit desk: Borrow (a currency loan)", ("T.guard.on('credit.loan:'",)),
    ("components/creditdesk.js", "T.api.post('/api/credit/loans/' + encodeURIComponent(row.loan_id) + '/repay'"): ("the credit desk: Repay (its dialog)", ("T.ui.modal(",)),
    ("components/creditdesk.js", "T.api.post('/api/credit/repos', last)"): ("the credit desk: Book the repo", ("T.guard.on('credit.repo.book'",)),
    ("components/creditdesk.js", "T.api.post('/api/credit/repos/' + encodeURIComponent(row.repo_id) + '/' + act"): ("the credit desk: a repo's Roll / Close",
                                                                                                                  ("function clicker(fn) {", "T.guard('credit.' + act + ':'")),
    ("components/creditdesk.js", "T.api.post('/api/credit/lending', body())"): ("the credit desk: Lend shares", ("T.guard.on('credit.lend:'",)),
    ("components/creditdesk.js", "T.api.post('/api/credit/lending/' + encodeURIComponent(row.loan_id) + '/recall'"): ("the credit desk: Recall", ("onClick: clicker(recallAct)",)),
    ("components/creditdesk.js", "T.api.post('/api/credit/borrows', loc)"): ("the credit desk: Borrow shares", ("T.guard.on('credit.borrow'",)),
    ("components/creditdesk.js", "T.api.post('/api/credit/borrows/' + encodeURIComponent(row.borrow_id) + '/return'"): ("the credit desk: Return", ("onClick: clicker(returnAct)",)),
    ("components/swapticket.js", "T.api.post('/api/otc/rfqs', {"): ("the OTC desks: Request quote (swap, OTC, CDX, CDS) and Request again",
                                                                    ("function guardRequest(key, ev, run) { return T.guard(key,", "guardRequest('rfq.request:swap:'",
                                                                     "T.guard.on('rfq.again:'")),
    ("components/swapticket.js", "T.api.post('/api/otc/rfqs/' + encodeURIComponent(d.rfq_id) + '/accept'"): ("the OTC desks: Accept a quote (its dialog)",
                                                                                                          ("T.guard.on('rfq.accept:'", "T.ui.modal(")),
    ("components/swapticket.js", "T.api.post('/api/otc/rfqs/' + encodeURIComponent(r.rfq_id) + '/decline'"): ("the OTC desks: Decline a quote", ("T.guard.on('rfq.decline:'",)),
    ("components/swapticket.js", "T.api.post('/api/otc/contracts/' + encodeURIComponent(c.contract_id) + '/terminate'"): ("an OTC contract: Terminate (its dialog)", ("T.ui.modal(",)),
    ("components/cdsnames.js", "T.api.post(R_CONTRACTS + encodeURIComponent(c.contract_id) + '/credit-event'"): ("a CDS contract: a credit event (its dialog)", ("T.ui.modal(",)),
    ("components/lifetimes.js", "T.api.post('/api/books/' + encodeURIComponent(code) + '/reset'"): ("a book: Reset (its dialog)", ("T.ui.modal(",)),
    ("components/sidebar.js", "T.api.post('/api/books/' + encodeURIComponent(book.book_code) + '/onboard'"): ("a book: Onboard (its dialog)", ("T.ui.modal(",)),
    ("components/pbschedule.js", "T.api.post(relPath, {})"): ("a PB relationship: Close (its dialog)", ("T.ui.modal(",)),
    ("pages/pending.js", "T.api.del('/api/orders/' + encodeURIComponent(r.order_id)"): ("Pending / the blotters / an instrument's orders: Cancel (and its key)",
                                                                                      ("T.guard('order.cancel:' + r.order_id",)),
    ("pages/pending.js", "T.api.post('/api/orders/' + encodeURIComponent(r.order_id) + '/resume'"): ("Pending: Resume", ("T.guard('order.resume:' + r.order_id",)),
    ("pages/pending.js", "T.api.post('/api/orders/cancel?book='"): ("Pending: Cancel selected / Cancel all open", ("T.guard('orders.mass-cancel'",)),
    ("pages/orders.js", "T.api.post('/api/orders/' + encodeURIComponent(o.order_id) + '/resume'"): ("the order blotter: Resume", ("T.guard('order.resume:' + o.order_id",)),
    ("pages/instrument.js", "T.api.del('/api/orders/' + encodeURIComponent(o.order_id)"): ("an instrument's orders: Cancel (without Pending)", ("T.guard('order.cancel:' + o.order_id",)),
    ("pages/positions.js", "T.api.post('/api/options/exercise'"): ("Positions: Exercise", ("T.guard('position.exercise:'",)),
    ("pages/positions.js", "T.api.post('/api/credit/borrows/' + encodeURIComponent(r.borrow.borrow_id) + '/return'"): ("Positions: Return a borrow", ("T.guard('position.return:'",)),
    ("pages/positions.js", "T.api.post('/api/credit/private/' + encodeURIComponent(r.instrument_id) + '/repay'"): ("Positions: Repay private credit (its dialog)", ("T.ui.modal(",)),
    ("pages/cash.js", "T.api.post('/api/credit/loans', body)"): ("Cash: Borrow", ("T.guard.on('cash.loan:' + acct",)),
    ("pages/cash.js", "T.api.post('/api/credit/pb/' + encodeURIComponent(acct) + '/' + kind"): ("Cash: the PB financing's draw / repay", ("T.guard.on('cash.pb.' + kind",)),
    ("pages/cash.js", "T.api.post('/api/credit/loans/' + encodeURIComponent(loan.loan_id) + '/repay'"): ("Cash: Repay (its dialog)", ("T.ui.modal(",)),
    ("pages/cash_collateral.js", "T.api.post('/api/collateral/pledges', {"): ("Collateral: Pledge", ("T.guard.on('collateral.pledge'",)),
    ("pages/cash_collateral.js", "T.api.post('/api/collateral/pledges/' + encodeURIComponent(r.pledge_id) + '/release'"): ("Collateral: Release (its dialog)", ("T.ui.modal(",)),
    ("pages/completed.js", "T.api.post('/api/settlements/' + encodeURIComponent(s.settle_id) + '/fail'"): ("Completed: a settlement's Fail (its dialog)", ("T.ui.modal(",)),
    ("pages/completed.js", "T.api.post('/api/settlements/' + encodeURIComponent(s.settle_id) + '/resolve'"): ("Completed: a settlement's Resolve (its dialog)", ("T.ui.modal(",)),
    ("pages/derivatives.js", "T.api.post('/api/otc/contracts/' + encodeURIComponent(c.contract_id) + '/credit-event'"): ("Derivatives: a credit event (its dialog)", ("T.ui.modal(",)),
    ("pages/private_markets.js", "T.api.post('/api/private/companies/' + encodeURIComponent(sel.value) + '/rounds'"): ("Private markets: a round (its dialog)", ("T.ui.modal(",)),
    ("pages/private_markets.js", "T.api.post('/api/private/companies/' + encodeURIComponent(sel.value) + '/status'"): ("Private markets: a status (its dialog)", ("T.ui.modal(",)),
    ("pages/private_markets.js", "T.api.post('/api/private/companies', body)"): ("Private markets: a company (its dialog)", ("T.ui.modal(",)),
    ("pages/treasury.js", "T.api.post('/api/treasury/' + kind"): ("Treasury: a deposit / withdrawal", ("T.guard('treasury.' + kind",)),
    ("pages/treasury.js", "T.api.post('/api/transfers'"): ("Treasury: a transfer", ("T.guard('treasury.transfer'",)),
}
#: the client's other write requests: nothing placed, replaced, amended, cancelled or booked (a dialog's actions are guarded anyway)
NOT_SENDS = {
    ("components/ticket.js", "T.api.post('/api/orders/preview'"): "the order ticket's preview (its preview id is what an order then carries)",
    ("components/fxdesk.js", "T.api.mutate('POST', '/api/fx/preview'"): "the FX desk's preview",
    ("components/bondticket.js", "T.api.mutate('POST', '/api/bonds/preview'"): "the bond ticket's preview",
    ("components/privateticket.js", "T.api.post('/api/private/preview'"): "the private trade ticket's preview",
    ("components/creditdesk.js", "T.api.post('/api/credit/repos/preview'"): "the repo's preview",
    ("components/creditdesk.js", "T.api.post('/api/credit/lending/preview'"): "the share loan's preview",
    ("components/creditdesk.js", "T.api.patch('/api/credit/counterparties/' + encodeURIComponent(row.counterparty_id)"): "a counterparty's terms (its dialog)",
    ("components/otcticket.js", "T.api.post('/api/otc/preview'"): "an OTC ticket's preview",
    ("components/swapticket.js", "T.api.post('/api/otc/preview'"): "the swap ticket's preview",
    ("components/cdsticket.js", "T.api.post('/api/otc/cdx/terms'"): "the CDX terms worked out",
    ("components/cdsticket.js", "T.api.post('/api/otc/cdx/preview'"): "the CDX preview",
    ("components/cdsnames.js", "T.api.post(R_TERMS"): "the CDS terms worked out",
    ("components/cdsnames.js", "T.api.post(R_PREVIEW"): "the CDS preview",
    ("components/cdsnames.js", "T.api.post(R_BASIS"): "a CDS name's basis (a model input; its dialog)",
    ("components/hedge.js", "T.api.post(path, body, { retries: 1 })"): "the Hedges / Strategies tab's preview (postReady: /api/strategies/preview)",
    ("components/imports.js", "T.api.post('/api/import/report'"): "an import's check report",
    ("components/attention.js", "T.api.post('/api/attention/' + encodeURIComponent(r.id) + '/resolve'"): "an attention notice marked resolved",
    ("components/pbschedule.js", "T.api.patch(path, c.patch)"): "the PB schedule's terms (its dialog)",
    ("pages/smartticket.js", "T.api.post(PARSE + bookQ(book)"): "the Smart Ticket's reading (the pure parse: it sends nothing; a ticket the owner opens does)",
    ("pages/settings.js", "T.api.patch('/api/settings'"): "the settings and the key binds",
    ("pages/settings.js", "T.api.patch('/api/accounts/' + encodeURIComponent(a.account_id)"): "an account's settings",
    ("pages/cash.js", "T.api.patch('/api/accounts/' + encodeURIComponent(acct)"): "an account's PB financing / sweep setting",
    ("pages/cash_collateral.js", "T.api.post('/api/margin/terms'"): "the margin terms",
    ("pages/cash_collateral.js", "T.api.post('/api/collateral/csa'"): "the CSA's reuse setting",
    ("pages/treasury.js", "T.api.patch('/api/settings'"): "the treasury reserve setting",
    ("pages/positions.js", "T.api.post('/api/instruments/' + encodeURIComponent(iid) + '/sector'"): "an instrument's sector label (its dialog)",
    ("pages/markets.js", "T.api.post('/api/watchlists'"): "a watchlist",
    ("pages/markets_fx.js", "T.api.post('/api/watchlists'"): "a watchlist",
    ("pages/watchlists.js", "('/api/watchlists'"): "a watchlist",
}
#: controls that send nothing themselves but hand the owner to a ticket: one hand-off per click
HANDOFFS = {("pages/smartticket.js", "return T.guard('smart.handoff:' + (b.dataset.act || '') + ':' + (b.dataset.w || '') + ':' + (b.dataset.f || ''), e, run,"):
            "the Smart Ticket's hand-off (each card's Open in ticket, a group's Strategies tab, a flag's action: each button its own control)"}

_CLIENT = None


def client() -> Client:
    global _CLIENT
    if _CLIENT is None:
        _CLIENT = Client()
    return _CLIENT


class InventoryTests(unittest.TestCase):
    """Every write request of the client is a send reached only through the guard, or listed as not a send."""

    def entries(self, rel: str, text: str) -> list:
        return [k for k in list(SENDS) + list(NOT_SENDS) if k[0] == rel and k[1] in text]

    def test_a_every_write_request_is_listed_once(self):
        c = client()
        sites = c.sites()
        self.assertGreater(len(sites), 70)
        for rel, line, _k, text in sites:
            with self.subTest(at=f"{rel}:{line}"):
                self.assertEqual(1, len(self.entries(rel, text)),
                                 f"{rel}:{line} {text[:110]!r}: a write request must be a send behind T.guard (SENDS) or not a send with its reason "
                                 f"(NOT_SENDS) - listed once; matches {self.entries(rel, text)}")
        for key in list(SENDS) + list(NOT_SENDS):
            with self.subTest(entry=key):
                self.assertTrue(any(rel == key[0] and key[1] in text for rel, _l, _k, text in sites), f"no write request matches {key}: a stale entry")

    def test_b_each_control_sends_only_through_the_guard(self):
        """A test per control: each of its requests is reached only through T.guard (or a dialog's action), held by the guard named."""
        c = client()
        sites = c.sites()
        for (rel, needle), (control, holders) in SENDS.items():
            with self.subTest(control=control):
                at = [(line, k) for r, line, k, text in sites if r == rel and needle in text]
                self.assertTrue(at, f"{control}: its request {needle!r} is gone from {rel}")
                for line, k in at:
                    ok, why = c.guarded(rel, k)
                    self.assertTrue(ok, f"{control}: {rel}:{line} sends without the guard - {why}")
                for h in holders:
                    self.assertIn(h, c.src[rel], f"{control}: {h!r} holds it")
        for (rel, needle), control in HANDOFFS.items():
            with self.subTest(control=control):
                self.assertEqual(1, c.src[rel].count(needle), control)
        for (rel, needle), why in NOT_SENDS.items():
            with self.subTest(not_a_send=why):
                self.assertNotRegex(needle, r"/api/orders'|/api/orders/'|/deal'|/trade'|/trades'|/rfqs|/exercise|/transfers|/treasury/", why)

    def test_c_the_guards_own_rules_and_the_dialogs(self):
        """core.js: one guard for every control (T.guard) and every dialog's action but its Cancel through it."""
        core = _read(os.path.join(STATIC, "core.js"))
        for s in ("var DOUBLE_MS = 500, OWN_MS = 5000, busy = {}, seen = {}, own = {};",
                  "if (ev && ev.repeat) { msg = not + ': ' + label + ' acts once per press (that was the key held down) - press it again to ' + act; }",
                  "else if (ev && ev.detail > 1) { msg = not + ': ' + label + ' takes a click of its own (that was the second click of a double click) - click ' + label + ' once to ' + act; }",
                  "else if (busy[key]) { msg = not + ': ' + label + ' is waiting for the answer to its request - one request at a time'; }",
                  "else if (now - last < DOUBLE_MS) {",
                  "if (r && typeof r.then === 'function') { r.then(free, free); } else { free(); }",
                  "if (/(^|\\s)cancel(\\s|$)/.test(a.cls || '')) { a.onClick(handle, b, ev); return; }",
                  "T.guard(dk + i, ev, function () { return a.onClick(handle, b, ev); }, { label: a.label, not: 'not done', act: 'go ahead' });"):
            self.assertEqual(1, core.count(s), s)
        # the wrappers the inventory follows: each runs its argument through T.guard (and no other function is taken for one)
        self.assertEqual({"components/creditdesk.js": {"clicker"}, "components/swapticket.js": {"guardRequest"}, "pages/pending.js": {"massGuard"},
                          "pages/smartticket.js": {"handOff"}}, {rel: ws for rel, ws in client().wrappers.items() if ws})

    def test_d_the_inventory_reads_an_unguarded_send(self):
        """The inventory's own control: a send from a click handler, from a function a handler calls, and from a dialog body's key handler
        is found; a send inside T.guard's run, a wrapper's argument or a dialog's action is not."""
        src = """(function () {
  function send(x) { return T.api.post('/api/orders', x); }
  function wrap(ev, run) { return T.guard('k', ev, run, {}); }
  btn.addEventListener('click', function (e) { T.api.post('/api/fx/deal', {}); });
  other.onclick = function (e) { send({}); };
  ok.addEventListener('click', function (e) { T.guard('k2', e, function () { return T.api.post('/api/bonds/trade', {}); }, {}); });
  ok2.addEventListener('click', function (e) { wrap(e, function () { return T.api.post('/api/transfers', {}); }); });
  T.ui.modal({ body: T.h('input', { onkeydown: function (e) { T.api.post('/api/treasury/x', {}); } }),
               actions: [{ label: 'Book', onClick: function (m) { return T.api.post('/api/private/trades', {}); } }] });
})();
"""
        c = Client.__new__(Client)
        c.src, c.m = {"x.js": src}, {"x.js": js_mask(src)}
        c.pairs = {"x.js": _pairs(c.m["x.js"])}
        c.fns = {"x.js": Client._functions(c, "x.js")}
        c.wrappers, c.exported = {"x.js": {"wrap"}}, set()
        c.guards = {"x.js": c._spans("x.js", _GUARD) + c._wrapper_calls("x.js")}
        c.modals = {"x.js": c._spans("x.js", _MODAL)}
        c._memo = {}
        got = {text.split("'")[1]: c.guarded("x.js", k)[0] for _r, _l, k, text in c.sites()}
        self.assertEqual({"/api/orders": False, "/api/fx/deal": False, "/api/bonds/trade": True, "/api/transfers": True, "/api/treasury/x": False,
                          "/api/private/trades": True}, got)


# ==== the server: single-use ids ======================================================================================================
class SingleUseTests(SmartSubmitBase):
    """A preview id is single-use for its order kind: the same reviewed order sent again under another request id is the first's answer."""

    def preview(self, body: dict) -> str:
        st, pv, _h = self.call("POST", "/api/orders/preview?book=T", dict(body))
        self.assertEqual(200, st, pv)
        self.assertTrue(pv.get("preview_id"), pv)
        return pv["preview_id"]

    def twice(self, method: str, path: str, body: dict, what: str) -> tuple:
        """``body`` sent as the page sends it under two request ids (a double click that got through, a resubmission after a lost
        answer): the two answers, the second's headers."""
        st1, a1, h1 = self.call(method, path, dict(body), f"ui:r10-{what}-0001")
        self.assertEqual(200, st1, a1)
        self.assertNotIn("X-Replayed", h1 or {})                        # the first: the answer a single submission always got
        st2, a2, h2 = self.call(method, path, dict(body), f"ui:r10-{what}-0002")
        self.assertEqual(200, st2, a2)
        self.assertEqual("1", (h2 or {}).get("X-Replayed"), a2)         # the second: the first's answer, replayed
        self.assertEqual(a1, a2)
        return a1, a2

    def count(self, sql: str, args=()) -> int:
        return len(self.q(sql, args))

    def test_a_a_new_order_fills_once(self):
        """BUY 10 ACME MARKET (last 100.00): the double click's second submission with the same preview id is the first's fill - 10 held,
        one order, one fill; a new preview places the next; once the preview has expired (120 s) the same id is PREVIEW_STALE as ever."""
        body = {"account_id": ACCT, "instrument_id": "ACME", "side": "BUY", "qty": 10, "order_type": "MARKET", "tif": "DAY"}
        pid = self.preview(body)
        a1, _a2 = self.twice("POST", "/api/orders?book=T", dict(body, preview_id=pid), "new")
        self.assertEqual("FILLED", a1["order"]["status"])
        self.assertEqual((1, 1), (self.count("SELECT 1 FROM orders WHERE instrument_id='ACME'"), self.count("SELECT 1 FROM fills WHERE instrument_id='ACME'")))
        self.assertEqual([10], [int(r["qty"]) for r in self.q("SELECT qty FROM v_positions WHERE account_id=? AND instrument_id='ACME'", (ACCT,))])
        st, out, h = self.call("POST", "/api/orders?book=T", dict(body, preview_id=self.preview(body)), "ui:r10-new-0003")
        self.assertEqual((200, "FILLED"), (st, out["order"]["status"]), out)
        self.assertNotIn("X-Replayed", h or {})
        self.rt.clock.advance(config.PREVIEW_TTL_S + 1)
        st, out, h = self.call("POST", "/api/orders?book=T", dict(body, preview_id=pid), "ui:r10-new-0004")
        self.assertEqual((409, "PREVIEW_STALE"), (st, (out.get("error") or {}).get("code")), out)
        self.assertEqual(2, self.count("SELECT 1 FROM fills WHERE instrument_id='ACME'"))

    def test_b_a_sequential_package_is_placed_once(self):
        """BUY 100 ACME LIMIT 95.00 with its hedge's own leg (STO 1 Dec 18 2026 105 call): one package, one shares order, one leg."""
        body = {"account_id": ACCT, "instrument_id": "ACME", "side": "BUY", "qty": 100, "order_type": "LIMIT", "limit_px": "95.00", "tif": "DAY",
                "hedge": {"legs": [leg(acme("C", 105), "STO", 1)]}}
        a1, _a2 = self.twice("POST", "/api/orders?book=T", dict(body, preview_id=self.preview(body)), "seq")
        self.assertTrue(a1.get("hedge_package"), a1)
        self.assertEqual(1, self.count("SELECT 1 FROM hedge_packages"))
        self.assertEqual(2, self.count("SELECT 1 FROM orders WHERE request_id LIKE 'ui:r10-seq-%'"))
        self.assertEqual(0, self.count("SELECT 1 FROM orders WHERE request_id LIKE 'ui:r10-seq-0002%'"))

    def test_c_an_all_or_none_package_is_placed_once(self):
        """The Strategies tab's bull call spread on ACME (BTO 1 Dec 100 call, STO 1 Dec 105 call) with the ticket's preview id: one AON
        package of two legs."""
        body = {"account_id": ACCT, "tif": "DAY", "package": {"strategy_key": "BULL_CALL_SPREAD", "underlying": "ACME", "units": 100, "tenor_months": 1,
                                                              "strikes": {"0": 100, "1": 105}}}
        st, pv, _h = self.call("POST", "/api/orders/preview?book=T", dict(body))
        self.assertEqual(200, st, pv)
        self.assertTrue(pv.get("preview_id"), pv)                       # the package's preview is stored like an order's
        a1, _a2 = self.twice("POST", "/api/orders?book=T", dict(body, preview_id=pv["preview_id"]), "aon")
        self.assertEqual("AON", a1["package"]["fill_mode"], a1)
        self.assertEqual(1, self.count("SELECT 1 FROM hedge_packages WHERE fill_mode='AON'"))
        self.assertEqual(2, self.count("SELECT 1 FROM orders WHERE request_id LIKE 'ui:r10-aon-%'"))

    def resting(self) -> str:
        body = {"account_id": ACCT, "instrument_id": "ACME", "side": "BUY", "qty": 10, "order_type": "LIMIT", "limit_px": "90.00", "tif": "DAY"}
        st, out = self.ticket(body, "ui:r10-rest-0001")
        self.assertEqual((200, "WORKING"), (st, out["order"]["status"]), out)
        return out["order"]["order_id"]

    def test_d_an_amend_is_applied_once(self):
        """BUY 10 ACME LIMIT 90.00 resting, amended to 91.00 with the ticket's preview of it: the second amend with that preview id is the
        first's answer (one amend: amend_count 1); a new preview amends again."""
        oid = self.resting()
        new = {"account_id": ACCT, "instrument_id": "ACME", "side": "BUY", "qty": 10, "order_type": "LIMIT", "limit_px": "91.00", "tif": "DAY"}
        self.twice("PATCH", f"/api/orders/{oid}?book=T", {"limit_px": "91.00", "preview_id": self.preview(new)}, "amend")
        row = self.q("SELECT amend_count, limit_px FROM orders WHERE order_id=?", (oid,))[0]
        self.assertEqual((1, "91"), (int(row["amend_count"]), str(row["limit_px"])))
        st, out, _h = self.call("PATCH", f"/api/orders/{oid}?book=T", {"limit_px": "92.00", "preview_id": self.preview(dict(new, limit_px="92.00"))}, "ui:r10-amend-0003")
        self.assertEqual(200, st, out)
        self.assertEqual(2, int(self.q("SELECT amend_count FROM orders WHERE order_id=?", (oid,))[0]["amend_count"]))

    def test_e_a_replace_is_made_once(self):
        """BUY 10 ACME LIMIT 90.00 resting, replaced at 92.00 with the ticket's preview of it: one replacement order."""
        oid = self.resting()
        new = {"account_id": ACCT, "instrument_id": "ACME", "side": "BUY", "qty": 10, "order_type": "LIMIT", "limit_px": "92.00", "tif": "DAY"}
        a1, _a2 = self.twice("POST", f"/api/orders/{oid}/replace?book=T", {"limit_px": "92.00", "preview_id": self.preview(new)}, "replace")
        self.assertEqual("CANCELLED", self.q("SELECT status FROM orders WHERE order_id=?", (oid,))[0]["status"])   # the first ended it: not NOT_AMENDABLE
        self.assertEqual([a1["order"]["order_id"]], [r["order_id"] for r in self.q("SELECT order_id FROM orders WHERE order_id != ? AND instrument_id='ACME'", (oid,))])
        self.assertEqual(2, self.count("SELECT 1 FROM orders WHERE instrument_id='ACME'"))


class RfqSingleUseTests(unittest.TestCase):
    """An OTC quote's id is single-use for its acceptance: a second acceptance under another request id is the booking's answer."""
    setUp = RFQ.RfqTests.setUp
    tearDown = RFQ.RfqTests.tearDown
    request = RFQ.RfqTests.request
    tick = RFQ.RfqTests.tick

    def test_a_an_accepted_quote_is_booked_once(self):
        rt = self.rt
        self.request()
        self.tick("14:00:30")
        rt.clock.set(RFQ.at("14:01:00"))
        a = execute(rt, "otc.rfq.accept", {"rfq_id": "RFQ-000001"}, "ui:r10-rfq-accept-0001")
        self.assertTrue(a.ok and not a.replayed, a.body)
        b = execute(rt, "otc.rfq.accept", {"rfq_id": "RFQ-000001"}, "ui:r10-rfq-accept-0002")
        self.assertEqual((200, True, a.body), (b.http, b.replayed, b.body))
        self.assertEqual(1, len(RFQ.q(rt, "SELECT 1 FROM otc_contracts")))
        self.assertEqual(1, len(RFQ.q(rt, "SELECT 1 FROM commands WHERE kind='otc.rfq.accept'")))
        # a declined quote keeps its answer: never accepted, never replayed
        self.request("ui:r10-rfq-0002")
        self.tick("14:01:30")
        self.assertTrue(execute(rt, "otc.rfq.decline", {"rfq_id": "RFQ-000002"}, "ui:r10-rfq-decline-0001").ok)
        c = execute(rt, "otc.rfq.accept", {"rfq_id": "RFQ-000002"}, "ui:r10-rfq-accept-0003")
        self.assertEqual((409, "RFQ_NOT_QUOTED", False), (c.http, c.code, c.replayed))
        self.assertEqual(1, len(RFQ.q(rt, "SELECT 1 FROM otc_contracts")))


# ==== the page ========================================================================================================================
GUARD_RULES = """(async function () {
  var out = {}, said = [], toasts = [], ran = 0, toast = T.ui.toast;
  function wait(ms) { return new Promise(function (r) { setTimeout(r, ms); }); }
  T.ui.toast = function (m) { toasts.push(m); };
  try {
    var k = 'r10.rules:' + Date.now(), done, p = new Promise(function (r) { done = r; });
    var opts = { label: 'Book', not: 'not booked', act: 'book it', say: function (m) { said.push(m); } };
    out.first = T.guard(k, { detail: 1 }, function () { ran++; return p; }, opts);
    out.busy = T.guard.busy(k);
    out.second = T.guard(k, { detail: 2 }, function () { ran++; }, opts);
    await wait(600);
    out.inflight = T.guard(k, { detail: 1 }, function () { ran++; }, opts);
    out.held = T.guard(k, { repeat: true }, function () { ran++; }, opts);
    done(); await p; await wait(0);
    out.freed = !T.guard.busy(k);
    out.soon = T.guard(k, { detail: 1 }, function () { ran++; }, opts);
    await wait(650);
    out.again = T.guard(k, { detail: 1 }, function () { ran++; }, opts);
    out.other = T.guard(k + ':other', { detail: 1 }, function () { ran++; }, opts);
    out.alone = T.guard('r10.alone:' + Date.now(), { detail: 2 }, function () { ran++; }, opts);
    var kt = 'r10.throws:' + Date.now();
    try { T.guard(kt, null, function () { throw new Error('r10'); }, opts); out.thrown = false; } catch (e) { out.thrown = !T.guard.busy(kt); }
  } finally { T.ui.toast = toast; }
  out.ran = ran; out.said = said; out.toasts = toasts;
  return out;
})()"""
DIALOG = """(function () {
  window.__r10 = { acted: 0 };
  T.ui.modal({ title: 'r10 dialog', sticky: true, actions: [{ label: 'Keep', cls: 'cancel' }, { label: 'Book it', cls: 'primary', id: 'r10-book',
    onClick: function (m) { window.__r10.acted++; return new Promise(function (r) { setTimeout(function () { m.close(); r(); }, 1500); }); } }] });
  return !!document.getElementById('r10-book');
})()"""


@unittest.skipUnless(find_browser(), NO_BROWSER)
class PageTests(unittest.TestCase):
    """One offline server (port 0, a temp home, the round 9 world) and one headless browser for the class, closed in tearDownClass."""
    last_click: dict = {}

    @classmethod
    def setUpClass(cls):
        cls.home = tempfile.mkdtemp(prefix="fst-simplify-r10-page-")
        cls.env = {k: os.environ.get(k) for k in (config.HOME_ENV, config.DB_ENV)}
        os.environ[config.HOME_ENV] = cls.home
        os.environ[config.DB_ENV] = os.path.join(cls.home, "terminal.db")
        cls.server = cls.browser = None
        try:
            clock = FakeClock(R9.SMK.AT)
            cls.fp = FakeProvider(clock)
            R9._seed9(cls.home, clock, cls.fp)
            cls.server = R9._Server9(cls.home, FakeClock(R9.SMK.AT), cls.fp).start()
            cls.browser = Browser(find_browser(), 1440, 900)
            cls.sid = cls.browser.page("about:blank")
            cls.browser.navigate(cls.sid, cls.server.url)
            cls.browser.wait(cls.sid, R9.LOADED, timeout=45)
        except Exception:
            cls.tearDownClass()
            raise

    @classmethod
    def tearDownClass(cls):
        try:
            if cls.browser is not None:
                cls.browser.close()
        finally:
            try:
                if cls.server is not None:
                    cls.server.stop()
            finally:
                for k, v in cls.env.items():
                    if v is None:
                        os.environ.pop(k, None)
                    else:
                        os.environ[k] = v
                shutil.rmtree(cls.home, ignore_errors=True)

    js = R9.PageTests.js
    wait = R9.PageTests.wait
    mouse = R9.PageTests.mouse
    click_xy = R9.PageTests.click_xy
    point = R9.PageTests.point
    double_click = R9.PageTests.double_click
    click_once = R9.PageTests.click_once

    def requests(self, since: int, method: str, path: str) -> list:
        out = []
        for e in self.browser._events(self.sid, since):
            if e.get("method") == "Network.requestWillBeSent":
                r = (e.get("params") or {}).get("request") or {}
                if r.get("method") == method and urlsplit(r.get("url") or "").path == path:
                    out.append(r.get("postData"))
        return out

    def test_a_the_guards_rules(self):
        """T.guard on the page: the first activation acts and holds the control while its request is in flight; the second click of a
        double click, a press while in flight, a key held down and a press inside 500 ms of the answer never act and say why (in a toast
        when the control's own first click acted 5 s back, else on the control's line); 650 ms after, a press acts; another control is
        its own; a run that throws frees its control."""
        out = self.js(GUARD_RULES, timeout=30)
        self.assertEqual((True, True, False, False, False, True, False, True, True, False, True, 3),
                         (out["first"], out["busy"], out["second"], out["inflight"], out["held"], out["freed"], out["soon"], out["again"], out["other"],
                          out["alone"], out["thrown"], out["ran"]), out)
        mine = " (its first click acted: see its answer)"
        self.assertEqual(["not booked: Book takes a click of its own (that was the second click of a double click) - click Book once to book it" + mine,
                          "not booked: Book is waiting for the answer to its request - one request at a time" + mine,
                          "not booked: Book acts once per press (that was the key held down) - press it again to book it (its first press acted: see its answer)",
                          "not booked: Book was pressed again inside the double-click time (500 ms) - press it once to book it" + mine], out["toasts"])
        self.assertEqual(["not booked: Book takes a click of its own (that was the second click of a double click) - click Book once to book it"], out["said"])

    def test_b_a_dialogs_action_acts_once_on_a_real_double_click(self):
        """A dialog's action (core.js T.ui.modal: every dialog's, but its Cancel): a REAL double click (CDP, the second press 0.15 s after the
        first) acts once; the second is refused, said."""
        self.assertTrue(self.js(DIALOG))
        n = len(self.js("T.guard.refused.slice()") or [])
        self.double_click("document.getElementById('r10-book')", "dialog")
        self.browser.pump(2.2)
        self.assertEqual(1, self.js("window.__r10.acted"))
        self.assertEqual(0, self.js("T.ui.dialogs()"))
        refused = (self.js("T.guard.refused.slice()") or [])[n:]
        self.assertEqual(1, len(refused), refused)
        self.assertTrue(refused[0]["key"].startswith("dialog:"), refused)
        self.assertEqual("not done: Book it takes a click of its own (that was the second click of a double click) - click Book it once to go ahead", refused[0]["msg"])

    def test_c_pendings_cancel_asks_once_and_cancels_once(self):
        """BUY 10 ACME LIMIT 50.00 resting in the Fund book; on Pending its Cancel activated twice at once (a double click's two clicks, the
        second with click count 2, before its question is up): ONE question; "Cancel order" clicked once: ONE cancel request, the order
        CANCELLED."""
        rt = self.server.rt
        body = {"account_id": "F1:US_EXCHANGE", "instrument_id": "ACME", "side": "BUY", "qty": 10, "order_type": "LIMIT", "limit_px": "50.00", "tif": "DAY",
                "request_id": "ui:r10-pend-0001"}
        st, out, _h = rt.router.handle("POST", "/api/orders?book=F", None, body, "127.0.0.1", None)
        self.assertEqual((200, "WORKING"), (st, out["order"]["status"]), out)
        oid = out["order"]["order_id"]
        self.js("(function () { T.book.set('F'); location.hash = '#/pending?status=open&r=' + Date.now(); return true; })()")
        btn = ("Array.prototype.map.call(document.querySelectorAll('#page tr[data-i]'), function (tr) { return tr.innerText.indexOf(%s) >= 0 ? "
               "tr.querySelector('button[data-act=\"x\"]') : null; }).filter(Boolean)[0]" % json.dumps(oid))
        self.wait("!!(%s)" % btn, timeout=45)
        since = self.browser.mark()
        opened = self.js("""(function (b) {
          var n = 0, modal = T.ui.modal;
          T.ui.modal = function (spec) { n++; return modal.apply(this, arguments); };
          b.dispatchEvent(new MouseEvent('click', { bubbles: true, cancelable: true, detail: 1 }));
          b.dispatchEvent(new MouseEvent('click', { bubbles: true, cancelable: true, detail: 2 }));
          return new Promise(function (r) { setTimeout(function () { T.ui.modal = modal; r(n); }, 1500); });
        })(%s)""" % btn, timeout=30)
        self.assertEqual(1, opened)
        self.assertEqual(1, self.js("T.ui.dialogs()"))
        refused = [r for r in (self.js("T.guard.refused.slice()") or []) if r["key"] == "order.cancel:" + oid]
        self.assertEqual(["not cancelled: Cancel %s takes a click of its own (that was the second click of a double click) - click Cancel %s once to cancel it" % (oid, oid)],
                         [r["msg"] for r in refused])
        self.click_once("Array.prototype.filter.call(document.querySelectorAll('.modal .modal-actions .btn.primary'), function (b) {"
                        " return b.textContent === 'Cancel order'; })[0]", "pending-ok")
        deadline = time.time() + 15
        while time.time() < deadline:
            with rt.db.reader() as conn:
                status = conn.execute("SELECT status FROM orders WHERE order_id=?", (oid,)).fetchone()[0]
            if status == "CANCELLED":
                break
            self.browser.pump(0.2)
        self.browser.pump(1.0)
        self.assertEqual("CANCELLED", status)
        self.assertEqual(1, len(self.requests(since, "DELETE", "/api/orders/" + oid)))


if __name__ == "__main__":
    unittest.main()
