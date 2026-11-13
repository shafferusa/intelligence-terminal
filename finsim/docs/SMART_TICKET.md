# Smart Ticket (FinSim 1.0.7.2)

The Smart Ticket fills standard order tickets from what you type, in your own words: one card per order, several orders and options in one instruction. Open it with Ctrl+F9 or the sidebar's Smart Ticket entry (#/smart). FinSim fills in every field it can from its own instrument master, the cached Cboe chains and your positions, and flags every field it filled in. You check or change each card, open it in the order ticket of its account and send it from there with that ticket's own preview, exactly as if you had typed it there. The Smart Ticket sends nothing itself: no combined send, no package of its own, no send-all (simplified on the owner's decision of 2026-11-11). The reading is a fixed grammar (smart-ticket/1) with no language model and no guessing: the same words with the same master, chains and positions always give the same tickets.

## What you can type (fictional names)

| You type | FinSim fills | Worth noting |
|---|---|---|
| buy 200 ACME limit 225 | BUY 200 ACME, limit 225, DAY | time in force DAY (information) |
| buy 200 ACME at 225 gtc | BUY 200 ACME, limit 225, GTC | "at 225" is always a limit, never a stop |
| short 300 WIDG limit 41.20 | SELL_SHORT 300 WIDG, limit 41.20, with a borrow | the locate happens at the preview |
| sell 2 ACME 240c 12/18/26 | sell to close when 2 or more are held, else sell to open | the side is read from the position (check) |
| buy 2 ACME Dec 18 2026 240 calls at the ask | limit = the chain's ask | the quote's time and freshness are shown (check) |
| buy 1 ACME Dec 18 2026 240/250 call spread | two tickets in one group, to send in this order: #1 BTO 240 call, #2 STO 250 call | bought = a debit, whatever order the strikes are typed in; the group offers the Strategies tab for one all-or-none order |
| buy 1 ACME Dec 18 2026 240/250 put spread | two tickets: BTO 250 put, then STO 240 put | bought = a debit: the higher put is bought |
| sell 1 ACME Dec 18 2026 220/230/250/260 iron condor | four tickets, the bought legs first | sell = credit, buy = debit |
| roll 2 ACME Dec 18 2026 240 calls to Jan 15 2027 250 | two tickets: close the 2 held, then open 2 of the new contract | refused when none is held |
| buy 200 ACME limit 225 and sell 2 ACME Dec 18 2026 240 calls as a package | nothing until you change it: "as a package" asks for one order (a red flag, "as separate tickets" offered) | the Smart Ticket never splits words asking for one order silently |
| covered call 100 ACME Dec 18 2026 240 | with 100 ACME held free: STO 1 call only, marked "sold as covered"; none held: BUY 100 ACME, then STO 1 call marked "sold as covered" (two tickets; send the call after the shares have filled) | the ticket places a call sold as covered only while its shares are free, and FinSim keeps them for the call until it is closed; the quantity is in shares, by 100 ("100 contracts" or "1 lot" is refused, with "100 contracts = 10,000 shares" and "100 shares" offered); shares held but not free, or a short holding, are refused and said; "buy write" always buys the shares |
| collar 100 ACME Dec 18 2026 220/260 | shares held free: two tickets, the put and the call (marked "sold as covered"); none held: the shares first | said as a check; shares held but not free, or a short holding, are refused and said |
| buy 2 ES Mar / sell 1 front ES stop 6500 | the next listed March contract / the active month | the year or the month FinSim picked (check) |
| in the Fund book: buy 100 ACME; buy 100 WIDG gtc | two orders in the Fund book | |
| buy 100 ACME in the Personal and Fund books | one order in each book at the same quantity | no allocation (check) |
| close ACME calls | one closing order per ACME call position held in the book | |
| buy 1m EURUSD | no order: "Open the FX desk", prefilled | FX, bonds, OTC and private names keep their own ticket; a price, a time in force or another account typed for them is refused (OWN_TICKET_TERMS), never dropped |
| buy 100 q | nothing: a one- or two-letter ticker needs capitals or $ | offered: buy 100 $Q |

## How the words are read

- **Normalising.** Curly quotes, dashes, non-breaking spaces and the multiplication sign become plain text. Any other unusual character is underlined as not read. At most 2,000 characters and 20 legs per instruction.
- **Orders.**
  - An order is a side, a quantity and an instrument, then any of: a price, a time in force, a book, an account and a group word. Each of these may appear once.
  - Sides: buy, sell, short / sell short, cover / buy to cover, bto, sto, btc, stc and their long forms.
  - Quantities: whole numbers, 1.5k, 2m, "all", "half", with an optional unit (shares, contracts, lots, x). A dollar amount is not a quantity.
  - A comma inside a number that is not a thousands group ("1,5k", "2,5", "1,5000") is refused as one number, never read as two orders, whatever the number of digits after the comma. Right after a month a day and a year stay a day and a year ("Dec 18,2026"). "1.000" as a quantity asks whether you meant 1 or 1,000.
- **Prices.**
  - Market: market, mkt, at market.
  - Limit: limit / lmt / at / @ 225, or 225 limit. "225 limit" takes the number only when it is none of the contract's words and no price follows the word: a year or a day typed after a month ("Dec 18 2026 limit"), a one- or two-digit number right after the month and day ("Dec 18 26 limit", "Jan 15 27 lmt": a year written short, refused as a second strike, never the price) and a roll's target strike ("to Jan 15 2027 260 limit") stay the contract's, and a "limit" with no price after it asks for one (MISSING_PRICE); "2026 limit 2.50" is a limit of 2.50.
  - Stops: stop 110.5, and stop 110 limit 109.50.
  - Trailing stops: trail 1.50, trail 2%.
  - From the quote: at the bid / ask / mid.
  - Prices are copied as typed (a leading $ and commas removed), never rounded: the preview judges the tick.
- **Time in force.** day, gtc / good till cancelled, gtd 2026-12-18, good till / until Dec 18, next session.
- **Options.**
  - Options are written as ticker, expiry, strike and call / put: "ACME Dec 18 2026 240 call", "ACME 240c 12/18/26", "ACME 2026-12-18 240 C", and the other orders listed in the grammar.
  - A US date always has its year: 12/18 is a strike pair, never a date. A 4-digit year right after a month and day is the year; a bare 2-digit number after a day is a strike. "Dec 18" alone is the first Dec 18 on or after today (a check).
  - An OCC symbol typed whole is taken as typed.
- **Strategy words (sides for buy; sell reverses every leg).**
  - Call / put spread or vertical: K1/K2. Buy = a debit (the lower call or the higher put bought), sell = a credit, whatever order the strikes are typed in.
  - Straddle: K.
  - Strangle: K1/K2, low to high.
  - Iron condor: K1/K2/K3/K4.
  - Call condor / put condor: K1/K2/K3/K4 of one right; buy = the wings bought, the body sold. "condor" alone asks which one you meant (iron, call or put): it is never read silently.
  - Iron butterfly: K1/K2/K3.
  - Butterfly / fly: K1/K2/K3, 2 x the body; calls unless "put butterfly" (a check says so when no call / put word is typed).
  - Calendar: E1/E2 K call or put; the near expiry is sold. Near and far are told apart as dates once both are read ("Dec 18/Jan 15 2027" sells Dec 18; a month alone, "Dec 2026", is the one date the cached chain lists that month), never by how they are typed. When either date cannot be read, the calendar is refused.
  - Covered call: the calls on shares held free, else shares + calls (a check says the shares are bought). Shares held but not free (in working sells or working short sales, reserved by calls sold or still being sold - an earlier covered call resting at its limit, a released hedge leg - or lent) offer the calls on the free round lots or a buy write (named in the account the covered call was read in); a book short the shares is refused. Buy write: shares + calls. The quantity is in shares: in contracts or lots it is refused, never read as shares.
  - Each order is read on the account as it is: the orders of one instruction do not count against each other. The tickets you send first may take shares a later card was read with; that ticket's own preview then says so.
  - A covered call or collar goes to the ticket marked "sold as covered" (one ticked line; untick it for a plain call). The ticket places it only while those shares are still free (held, net of working sells and short sales, of calls sold or being sold, and of lent shares), and FinSim then keeps them for the calls until the calls are closed - from any ticket; if they were sold or taken meanwhile, nothing is placed (NOT_COVERED in the ticket's preview).
  - Collar: the put and the call on shares held free, else shares + put + call; the same refusals as the covered call, the quantity in shares too.
  - Roll ... to ...
- **Clauses.**
  - ";", "." and a new line end an order.
  - ",", "and", "then", "plus" and "&" end one only when an order follows, so the comma of "Dec 18, 2026" never splits. Such a word alone between two groups ("(...) then (...)") is only a separator.
  - An order that starts with a quantity takes the side of the order before it (a check), and with it that order's book and account ("buy 100 ACME in the Fund book and 50 WIDG": both in the Fund book, a check; "buy 100 ACME at pb ms and 50 WIDG": both at pb ms), unless it names its own book or account. A strategy takes only buy or sell: a carried short, cover, btc or stc is refused there, never read as a buy.
- **Grouping.** "( ... )" groups exactly what is inside: the orders show together, and each one is still its own ticket. Words asking for one order ("as a package", "as one order", "as one", "as a combo", "together", "all or none", "aon") are refused (ONE_ORDER_ASKED), never split silently: they hold every order of the run they end - the orders written before them back to the previous group or group word, whatever their names and whether they open or close ("buy 200 WIDG and buy 100 ACME as a package" and "close WIDG and buy 100 ACME as a package" hold both cards) - and "as separate tickets" removes them; for one all-or-none order of options, use the order ticket's Strategies tab.
- **Words that are also tickers.**
  - The slot decides: a word in the instrument slot is a ticker; in the quantity or price slot it is a keyword.
  - A grammar word typed in lower case is never a ticker. The common English collisions (now, key, so, it, on) need capitals or $.
  - A futures root alone ("buy 2 NQ") must say $NQ for the shares or "front NQ" / "NQ Dec" for the future.
  - When two readings are both complete, FinSim asks which one you meant.

## Where each order goes

- **Books.**
  - A book phrase in an order applies to that order.
  - An opening phrase ("in the Fund book: ...") applies to every order that names none. An order that names its own book keeps it (a check says the opening phrase does not apply to it).
  - A book phrase at the very end of the text applies to every order that names none, flagged as a check on each. It still counts as the end when only the last order's own price, time in force, account or group word follow it ("... in the Fund book at market"). An account phrase inside it or right beside it ("at pb ms in the Institutional book", "in the Institutional book, at pb ms") goes with it.
  - With no book phrase anywhere, the order goes to the selected book.
  - Several books ("in the Personal and Fund books") repeat the order in each book at the same quantity.
  - A book word that names no book ("in the Fnd book") leaves the order's book unread (no book, no account) and offers every book. So does every order that takes that phrase's book - the order whose side is carried from it, every order under it as the opening or the closing phrase - never the selected book: each is a blocker that names the words and offers every book, and the book picked reaches them all, as if the word had been typed right.
  - An order outside the selected book is allowed, and said on its card.
- **Accounts.**
  - The account is the one account of the book's open lifetime that trades the instrument. With several, US Exchange comes first and the others are offered (one that holds the contract says what it holds). An explicit opening side (bto, sto, short) follows this rule too: it opens where the rule puts it, never silently in the account that holds the contract.
  - A named account ("at pb ms", "at us exchange", or an account id) must belong to the book. The choice that moves the order to the account's book replaces the order's own book phrase when it names one ("in the Personal book at I1:PB_MS" becomes "in the Institutional book at I1:PB_MS"), and an account choice replaces the account phrase whole, its "at" with it.
  - An account phrase is never dropped: in an opening or closing phrase with a book it goes with that book; alone ("at pb ms: ...", "...; at pb ms") it applies to every order that names no account, in that order's own book (refused when that book has no such account).
  - When the side is read from the position (buy / sell), closes it, or the quantity is "all" / "half", the order goes to the account of the book that holds the contract, whatever the quantity's wording. Holdings in several accounts, or a named account that holds nothing while another one holds it, ask you to name the account.

## How the instrument and the side are found

- **Equities.** Equities are found by exact symbol only: several matches are offered, none gives "Look it up".
- **Options.** Options come from the cached chain first, then from the master's option rows. Otherwise the contract is built from its terms and flagged unverified, with "Load the chain": the preview refuses it until the chain lists it, so nothing is invented.
- **Never snapped.** An expiry or strike that is not listed is never snapped to a near one: the nearest listed values are offered instead.
- **Several roots.** When Cboe lists two roots for one expiry, strike and right (an index weekly root, an adjusted series), you pick; the standard root is offered first.
- **Sides from the position.**
  - "buy" an option you are short buys to close; "sell" one you hold sells to close.
  - "sell" an option you do not hold sells to open (a check: its margin is in the preview).
  - "buy" shares you are short covers them.
  - A sale beyond the free quantity (net of working sells, shares covering calls sold or still being sold, and lent shares) offers "sell the free quantity"; a short sale is never offered for the rest: shares held long leave only through a sale. An explicit short of shares the account holds long is refused (SHORT_WHILE_LONG: sell them first).
  - A quantity that is only partly covered by the position offers the split for options ("btc 2 + bto 1", "stc 2 + sto 1"). For shares, a buy beyond a short offers the cover alone ("cover 200 only"): the rest is bought once the cover has filled - shares have no side that opens a long while the short is held, so "buy" would read as a second cover.
  - Explicit sides (short, cover, bto, sto, btc, stc) are never changed.
  - When the whole position is already in working closing orders, "buy" / "sell" is refused (the explicit opening side is offered): it is never turned silently into an order that opens a new position.
  - "sto" on shares is a short sale: the alternative offered is "short" (sell short), read where the "sto" was read. "bto" on shares offers "buy", named in the account it was read in, and says when it would cover a short held there.
- **Held quantities.** "all" is the quantity held and "half" half of it, rounded down to the lot (a check says the quantity taken). They close a position: a side that would add to it ("buy all WIDG" while WIDG is held, "sell all" or "sell half" of a short) is refused (ADDS_TO_POSITION), with "close" offered - never a short sale.

## Several orders and strategies

- Orders written together - a strategy, a roll, a collar, a covered call that buys its shares, parentheses - are a group: separate tickets shown together, with the order to send them in (purchases first, shares then options, then sales to close, sales to open, share sales and short sales), worked out from the sides the cards hold now: a side changed in place moves its card in that order. Each ticket goes and fills on its own: no all-or-none, no sequence.
- For one all-or-none order of options, an option strategy the order ticket's Strategies tab has a template for offers "Strategies tab": it opens the order ticket of the group's account on that name with its Strategies tab, where you build it.
- Words asking for one order ("as a package", "all or none" ...) are refused (ONE_ORDER_ASKED), with "as separate tickets" offered; no card of that group opens until then.
- A price typed on a strategy, a roll, a collar or a covered call that buys its shares is a net price the tickets cannot carry: refused (NET_PRICE, "without the price" offered), never dropped. A leg's own limit is that ticket's.
- One price typed for a close of several contracts ("close ACME calls limit 2") is refused: name each contract with its own price, or close them at market.

## Flags

Each leg and combination carries flags.

- A **blocker** means it cannot be sent as written.
- A **check** means FinSim filled something in: look before sending.
- **Information** says how the text was read.

| Severity | Codes |
|---|---|
| blocker | UNPARSED_TEXT, MISSING_SIDE, MISSING_QTY, MISSING_INSTRUMENT, MISSING_PRICE, DUPLICATE_FIELD, TOKEN_AMBIGUOUS, QTY_NOT_WHOLE, QTY_LOT, UNIT_KIND_MISMATCH, SYMBOL_UNKNOWN, SYMBOL_AMBIGUOUS, KIND_OWN_TICKET, NOT_TRADEABLE_HERE, EXPIRY_NOT_LISTED, EXPIRY_MONTH_ONLY, STRIKE_NOT_LISTED, ROOT_CHOICE, STRIKE_ORDER, SIDE_SPLIT, WOULD_OVERSELL, NOTHING_HELD, NOTHING_TO_ROLL, ACCOUNT_NOT_IN_BOOK, TOO_MANY_LEGS, ALREADY_CLOSING, ADDS_TO_POSITION, PRICE_AMBIGUOUS, NET_PRICE, ONE_ORDER_ASKED, SHORT_WHILE_LONG, OWN_TICKET_TERMS, ACCOUNT_CHOICE (holdings split, or held in another account than the one named) |
| check | SIDE_INFERRED, SIDE_CARRIED, YEAR_INFERRED, EXPIRY_FROM_MONTH, TYPE_DEFAULT, PRICE_FROM_QUOTE, QTY_ROUNDED, QTY_FROM_HELD, RIGHT_DEFAULT, BOOK_FROM_SCOPE (end of the text, or carried with the side), BOOK_CONFLICT, OTHER_BOOK, MULTI_BOOK, ACCOUNT_CHOICE, OPTION_UNVERIFIED, CHAIN_STALE, COVER_HINT, DUPLICATE_LEG, SEPARATE_TICKETS |
| information | TIF_DEFAULT, BOOK_SELECTED, BOOK_FROM_SCOPE (opening phrase), TYPE_FROM_AT, STRATEGY_EXPANDED, STRATEGY_SIDE_CONVENTION |

Gone with the simplification (never deployed): LIMIT_IN_AON, COMBO_INVALID, COVER_TAKEN, ALREADY_SENT, COMBO_FILL_MODE.

- **Choices.** A choice is the replacement text of the words its flag names: the order, the group, or both orders for a covered-call hint. Picking one puts that text in the instruction and reads it again; nothing is applied by itself. A choice applies only while the box holds exactly the text last read: after any change you have not read (Enter), its buttons are disabled and nothing is put in - press Enter to read the text again, then pick.
- **A choice changes only the order it is on.** Read again with the whole instruction, every other order keeps its side, book, account and grouping, and the order the choice is on keeps its book and account (unless the choice is that book or account). Where that needs it, the choice also writes out what those orders had inherited, in plain words, and covers them:
  - a side carried from the order before ("short 100 WIDG in the Fund book and 1 ACME Dec 18 2026 240/250 call spread": "buy 1 ACME ... call spread in the Fund book");
  - the orders after it that carry its side keep their own ("... and short 1 ACME ... put spread in the Institutional book");
  - a book or account phrase that would land in the closing phrase of the last order, and so apply to every order naming none, is matched by the others' own ("buy 3 WIDG ... calls in the Personal book and buy 1 ACME ... at I1:PB_MS in the Institutional book").
  - The opening part of a split ("stc 2 + sto 1") names the account the order was read in, so it stays where the calls are held; so do a buy write and a side alternative that would read the account another way.
  - A choice that cannot keep the other orders as they were is not offered: a side for the first of two orders without one would carry into the second.
- **Every choice is checked resolved.** Before it is offered, every choice (also on an instruction of one order) is read again the way the page would read it, with the positions and accounts: every other order - matched by its place, the orders the choice writes out too - must keep its book, account, side, quantity, contract, prices and time in force, stay in its group and never become complete when it was not (a leg whose only blocker was its group with the order the choice fixes excepted: fixing an unlisted strike completes the strategy typed); the order the choice is on must stay in its book and account (unless the choice is that book or account) and join no group with another order; and the choice's words must read (no new word "not understood"). A choice that fails this is not offered.
- **Orders repeated per book.** A choice on one book's order of "... in the Personal and Fund books" is computed from that book's holding, so it is written for that book alone and leaves the other books' orders as typed ("sell 300 WIDG in the Personal book; sell 100 WIDG in the Fund book"), unless the same choice is offered on them too. Each book's order says its alternatives in its own words, from its own holding: "stc 100 WIDG in the Personal and Fund books" offers "sell (sells the 300 held in P1:US_EXCHANGE)" on the Personal order and a plain "sell" on the Fund order, which holds none.
- **Groups and copies.** A choice offered on one group reads every other order outside that group as before, another book's copy of the same clause included.
- **Written once.** A choice that writes an order out again ("as separate tickets", "read as: ...") writes the account of a book phrase once ("in the Institutional book at pb ms"), and is not offered when its words would read a field twice. "as separate tickets" also drops a group word typed inside the parentheses ("(..., sell 1 ACME Dec 18 2026 250 call as a package)"); the parentheses stay and only group the cards.
- **A calendar's expiry** is offered as words ("Dec 18 2026"), never as the other leg's own expiry.
- **Actions.** An action runs an existing step and then reads the text again: "Look it up", "Load the chain", or the product's own ticket; "Strategies tab" opens the order ticket's Strategies tab for one all-or-none order of options. The product's own ticket ("Open the FX desk", "Open the bond ticket" ...) opens only when Open in ticket would, less the order ticket's own gates: the reading not in flight, the text box holding the text last read, no red flag and no unread word in the instruction, no red flag on the card but the one naming its own ticket (and "how many?" when no amount was typed: that ticket opens with its amount box empty and asks for it), none on its group; held, its button is disabled with the reason beside it.

## The cards and Open in ticket

- **One card per order**, in the order written: its book and account (another book than the selected one is said), the instrument in words, side, quantity, type, limit, stop or trail, time in force, GTD date, the "sold as covered" line of a covered call, and its flags with their alternatives and actions. The cards of a group sit under it, with its words, its flags and the order to send them in.
- **In-place edits** change that card only, with no new reading: the side among its kind's sides, quantity, type, prices, trail (an amount, or a percent ending in %), time in force, GTD date, and the covered tick (unticked: a plain call). A new reading matches each card with the card you last saw: first one that reads exactly as before (every field read the same - also when the words around it changed, for example an order before it is gone or a choice on another card wrote its words out): it keeps your edits and its "opened" mark; else one with the same words, book named and instrument, read otherwise: each edit on it is dropped and said on that card. An edit on a card the new reading has no match for is said above the cards: an edit is never dropped unsaid. A field that differs from the matched card as you last saw it shows "was X" (for example a side read again after the position moved; an account as "book . account"). These notes stay until you act on them: a card's notes (an edit dropped on it, its "was" marks) stay in the draft, across a navigation and through the next readings that read that card as before, until you edit that field (its notes go) or open the card (all of them go); the note above the cards stays through the next readings of the same text (the reading after a restore, Enter, a look-up, "Read for the <book>") and goes when another text is read.
- **The book of a reading.** The text is read for the book of the last reading until New ticket - an order that names no book stays in that book - also after another book is selected (opening an order of another book in the ticket selects that book). The page then says so, and "Read for the <selected book>" reads the text there (the cards say what changed).
- **Open in ticket** (one card, one click; a click right after typing in a card counts: the cards are painted again only once the button is released) opens the order ticket docked on the card's account page with that order filled in exactly - the trail and the "sold as covered" line included - as a NEW order: nothing of an order you had typed there before (a limit, a hedge pick, a mode) rides along, and the ticket's symbol box names the order filled in (a symbol you had typed there and not looked up is dropped with its look-up). A call sold as covered opens with its "sold as covered" line on the first open also on an adjusted contract (a root with a digit, such as ACME1): the card hands the ticket the contract's facts (call or put, underlying, expiry, strike), and the ticket reads FinSim's OCC symbols; a claim the ticket cannot keep (not a call sold to open) is said in a note, never dropped unsaid. You preview and send it there, exactly as an order typed there: its own preview, Submit, moved-price and large-order questions and lost-answer handling. An answer to a Submit that comes after the ticket was filled with another order is never shown on that order and never asks its question over it: a note names the order it is for and says what happened (placed; refused; NOT placed - a large order to confirm, a moved price: open it again). An answer that comes while the order ticket is out of sight (you went back to the Smart Ticket, or to a page with no account) is shown on the ticket and also said in a note naming its order, so opening the next card never clears it unseen. Open in ticket works only when the reading is not in flight, the text box holds the text last read, a restored draft was read again, the instruction has no red flag and no unread word, the card and its group have no red flag, its order is complete and the order ticket takes its kind (the others offer their own ticket). The card then says "opened in the ticket at HH:MM"; the page never reads orders, so whether the order was sent is on Pending Trades.
- **The draft** (the text, its reading and its book, your edits, the notes of dropped edits, the "was" marks and the "opened" marks) is kept in this browser only, one per window: each window keeps its own, so another window never replaces your edits or "opened" marks, and a new window starts from a copy of the draft saved last (it says so). Restored, it says when it was read, and no card opens until you press Enter to read the text again (orders may have been sent since). New ticket clears this window's.
- **Never on this page:** a send, submit or send-all button, a confirm of its own, a request id, a request to an order, preview, cancel, replace or strategies route. Its only POST is the parse below.

## The route

POST /api/smart-ticket/parse[?book=P]

- **Body.** {"text", "book"?, "account_id"?}. The selected book is the body's book, else ?book=, else the first open book. No request id is needed; one sent is ignored.
- **200 answer.** {grammar, as_of, selected_book, text, ok, counts, legs, combos, unparsed, flags}.
  - A leg's request is exactly the order body: account_id, instrument_id, side, qty, order_type, tif, the prices and gtd_date that apply, borrow: true for a short, and covered: true for calls sold as covered (the order route places them only while their shares are free and keeps those shares for them until they are closed). It is null while the leg has a blocker, its group's included.
  - A group (combos: id, kind "SEPARATE", the legs, order - the order to send them in -, its words and flags) has no request: each of its orders is its own ticket. The answer's shape is unchanged for the clients that relay it.
- **Errors.** 400 BAD_REQUEST, TEXT_TOO_LONG (over 2,000 characters) and TOO_MANY_LEGS (over 20 legs); 404 UNKNOWN_BOOK; 500 PARSE_FAILED (a fault of the parser, logged). Never 202: it never fetches.

## Purity

- **No side effects.** The parse writes nothing, fetches nothing, subscribes no quote, stores no preview and takes no mark.
- **What it reads.** It reads one database connection (books, lifetimes, accounts, positions, working orders), the in-memory instrument master, the cached chains (peek only, so the cache order never moves) and quote copies.
- **What it never calls.** option listing (ensure_option, _materialise_option), instruments.upsert / ensure_option, worker.resolve / request_bars / request_chain, cache.subscribe / pin, _ask_for_quote, chains.get / request / keep_on_screen, orders.preview, PreviewStore.put, marks.snapshot, bus.execute, db.writer.
- **How it is proven.** The tests prove this over a 323-case corpus: the state version, the database's data_version, the instrument and command counts, the chain cache's order and queue, the quote pins and priorities and the preview store are unchanged, and spies on every function above record no call. After deploy, gate1072_parse.py checks it on the live terminal.

## Limits in this version

- The Smart Ticket sends nothing itself: each order goes from the order ticket, one ticket at a time.
- No all-or-none order from the Smart Ticket: words asking for one order are refused; build one all-or-none order of options in the order ticket's Strategies tab (its templates take strikes and expiries as rules, not the contracts you typed).
- While calls sold as covered are open on a name, a further call sold to open on it - alone or as a spread's short leg - needs free shares: FinSim's margin would otherwise give it their cover (the ticket's preview says so).
- Each order is read on the account as it is: the orders of one instruction do not count against each other, and a ticket you send first may take shares a later card was read with (its own preview then refuses it).
- FX, bonds, OTC and private names open their own ticket. Opened from a card, the FX desk, the bond ticket and the private-markets ticket start a new deal: nothing typed or picked there before (an amount, a forward or its tenor, a face, a price) rides along, and the FX desk opens a spot deal with the amount in the pair's base currency (empty when the card names none). The card hands over the side (buy or sell), the amount and the account only: the desk deals at its own rate, the bond ticket at the mark and its half-spread, and the private-markets ticket takes the negotiated price typed there. So a term typed for them that the hand-off cannot carry - a price other than market, a time in force, an account other than the one it opens in, another side word (short, cover, bto ...), "all" or "half", a unit, a close, a covered call or a collar - is the red flag OWN_TICKET_TERMS naming those words, never dropped unsaid and never "read as a limit". A price or a time in force alone offers "without" those words; an account named is to be removed by you; the others ask for the order written as buy or sell with a number.
- Several books mean the same quantity in each, with no allocation.
- A month without a day ("Dec 2026") needs the cached chain, and resolves only when one expiry is listed that month.
- An option leg of a strategy that has two roots must be typed as OCC symbols in parentheses; no choice is offered there.
