# Smart Ticket (FinSim 1.0.7.2)

The Smart Ticket reads what you type, in your own words, into staged orders and option combinations. Open it with Ctrl+F9 or the sidebar's Smart Ticket entry (#/smart). FinSim fills in every field it can from its own instrument master, the cached Cboe chains and your positions. It flags every field it filled in, and nothing is sent until you confirm. The reading is a fixed grammar (smart-ticket/1) with no language model and no guessing: the same words with the same master, chains and positions always give the same ticket.

## What you can type (fictional names)

| You type | FinSim stages | Worth noting |
|---|---|---|
| buy 200 ACME limit 225 | BUY 200 ACME, limit 225, DAY | time in force DAY (information) |
| buy 200 ACME at 225 gtc | BUY 200 ACME, limit 225, GTC | "at 225" is always a limit, never a stop |
| short 300 WIDG limit 41.20 | SELL_SHORT 300 WIDG, limit 41.20, with a borrow | the locate happens at the preview |
| sell 2 ACME 240c 12/18/26 | sell to close when 2 or more are held, else sell to open | the side is read from the position (check) |
| buy 2 ACME Dec 18 2026 240 calls at the ask | limit = the chain's ask | the quote's time and freshness are shown (check) |
| buy 1 ACME Dec 18 2026 240/250 call spread | one all-or-none combination: BTO 240 call, STO 250 call | bought = a debit, whatever order the strikes are typed in |
| buy 1 ACME Dec 18 2026 240/250 put spread | one all-or-none combination: BTO 250 put, STO 240 put | bought = a debit: the higher put is bought |
| sell 1 ACME Dec 18 2026 220/230/250/260 iron condor | the credit condor, 4 legs, all-or-none | sell = credit, buy = debit |
| roll 2 ACME Dec 18 2026 240 calls to Jan 15 2027 250 | close the 2 held and open 2 of the new contract, all-or-none | refused when none is held |
| buy 200 ACME limit 225 and sell 2 ACME Dec 18 2026 240 calls as a package | the shares first; the calls are released as the shares fill | |
| covered call 100 ACME Dec 18 2026 240 | with 100 ACME held free: STO 1 call only; none held: BUY 100 ACME, then STO 1 call (said as a check) | the quantity is in shares, by 100; shares held but not free, or a short holding, are refused and said; "buy write" always buys the shares |
| collar 100 ACME Dec 18 2026 220/260 | shares held free: the put and the call, all-or-none; none held: the shares first | said as a check; shares held but not free, or a short holding, are refused and said |
| buy 2 ES Mar / sell 1 front ES stop 6500 | the next listed March contract / the active month | the year or the month FinSim picked (check) |
| in the Fund book: buy 100 ACME; buy 100 WIDG gtc | two orders in the Fund book | |
| buy 100 ACME in the Personal and Fund books | one order in each book at the same quantity | no allocation (check) |
| close ACME calls | one closing order per ACME call position held in the book | |
| buy 1m EURUSD | no order: "Open the FX desk", prefilled | FX, bonds, OTC and private names keep their own ticket |
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
  - Limit: limit / lmt / at / @ 225, or 225 limit.
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
  - Covered call: the calls on shares held free, else shares + calls (a check says the shares are bought). Shares held but not free (in working sells, reserved by calls, lent) offer the calls on the free round lots or a buy write; a book short the shares is refused. Buy write: shares + calls.
  - Collar: the put and the call on shares held free, else shares + put + call; the same refusals as the covered call.
  - Roll ... to ...
- **Clauses.**
  - ";", "." and a new line end an order.
  - ",", "and", "then", "plus" and "&" end one only when an order follows, so the comma of "Dec 18, 2026" never splits.
  - An order that starts with a quantity takes the side of the order before it (a check), and with it that order's book and account ("buy 100 ACME in the Fund book and 50 WIDG": both in the Fund book, a check; "buy 100 ACME at pb ms and 50 WIDG": both at pb ms), unless it names its own book or account. A strategy takes only buy or sell: a carried short, cover, btc or stc is refused there, never read as a buy.
- **Grouping.** "( ... )" groups exactly what is inside. "as a package", "as one order", "together", "all or none" and "aon" group the run of orders on the same name that ends at the word.
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
  - An order outside the selected book is allowed, and said on the leg and in the confirm.
- **Accounts.**
  - The account is the one account of the book's open lifetime that trades the instrument. With several, US Exchange comes first and the others are offered (one that holds the contract says what it holds). An explicit opening side (bto, sto, short) follows this rule too: it opens where the rule puts it, never silently in the account that holds the contract.
  - A named account ("at pb ms", "at us exchange", or an account id) must belong to the book.
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
  - A sale beyond the free quantity (net of working sells, covered-call reservations and lent shares) offers "sell the free quantity" or "sell it and short the rest".
  - A quantity that is only partly covered by the position offers the split.
  - Explicit sides (short, cover, bto, sto, btc, stc) are never changed.
  - When the whole position is already in working closing orders, "buy" / "sell" is refused (the explicit opening side is offered): it is never turned silently into an order that opens a new position.
  - The same words already sent from a Smart Ticket on that contract in that book (still working, or filled today) read as that same order again (a check), never as a new one once the position it closed has moved; an order whose "buy" / "sell" is carried from the order before is matched by its own words and that side.
- **Held quantities.** "all" is the quantity held and "half" half of it, rounded down to the lot (a check says the quantity taken). They close a position: a side that would add to it ("buy all WIDG" while WIDG is held, "sell all" of a short) is refused, with "close" offered.

## Combinations

- Options on one name fill all-or-none: every leg from one Cboe observation at its executable bid / ask, or nothing.
- There is no net limit in this version, so an option leg with its own limit inside such a group is refused. Two fixes are offered: drop the limits, or send the legs as separate orders. A price typed on a strategy, a roll or a collar is refused the same way, never dropped, also inside a group ("as a package", parentheses).
- Shares with their options on the same name fill in sequence: the shares first, then the options are released in proportion as the shares fill (whole numbers: all of them once the shares have filled). A price typed on a collar that buys its shares is refused (the shares, or a net price?).
- One price typed for a close of several contracts ("close ACME calls limit 2") is refused: name each contract with its own price, or close them at market.
- Anything else (two names, a futures leg, two share legs, one contract named twice) is refused: a package holds one name, its shares and its options, each contract once.
- A package has 2 to 8 legs, one account, and DAY or GTC.

## Flags

Each leg and combination carries flags.

- A **blocker** means it cannot be sent as written.
- A **check** means FinSim filled something in: look before sending.
- **Information** says how the text was read.

| Severity | Codes |
|---|---|
| blocker | UNPARSED_TEXT, MISSING_SIDE, MISSING_QTY, MISSING_INSTRUMENT, MISSING_PRICE, DUPLICATE_FIELD, TOKEN_AMBIGUOUS, QTY_NOT_WHOLE, QTY_LOT, UNIT_KIND_MISMATCH, SYMBOL_UNKNOWN, SYMBOL_AMBIGUOUS, KIND_OWN_TICKET, NOT_TRADEABLE_HERE, EXPIRY_NOT_LISTED, EXPIRY_MONTH_ONLY, STRIKE_NOT_LISTED, ROOT_CHOICE, STRIKE_ORDER, SIDE_SPLIT, WOULD_OVERSELL, NOTHING_HELD, NOTHING_TO_ROLL, LIMIT_IN_AON, COMBO_INVALID, ACCOUNT_NOT_IN_BOOK, ALREADY_CLOSING, ADDS_TO_POSITION, PRICE_AMBIGUOUS, ACCOUNT_CHOICE (holdings split, or held in another account than the one named) |
| check | SIDE_INFERRED, SIDE_CARRIED, YEAR_INFERRED, EXPIRY_FROM_MONTH, TYPE_DEFAULT, PRICE_FROM_QUOTE, QTY_ROUNDED, QTY_FROM_HELD, RIGHT_DEFAULT, BOOK_FROM_SCOPE (end of the text, or carried with the side), BOOK_CONFLICT, OTHER_BOOK, MULTI_BOOK, ACCOUNT_CHOICE, OPTION_UNVERIFIED, CHAIN_STALE, COVER_HINT, DUPLICATE_LEG, ALREADY_SENT |
| information | TIF_DEFAULT, BOOK_SELECTED, BOOK_FROM_SCOPE (opening phrase), TYPE_FROM_AT, STRATEGY_EXPANDED, STRATEGY_SIDE_CONVENTION, COMBO_FILL_MODE |

- **Choices.** A choice is the replacement text of the words its flag names: the order, the combination, or both orders for a covered-call hint. Picking one puts that text in the instruction and reads it again; nothing is applied by itself.
- **A choice changes only the order it is on.** Read again with the whole instruction, every other order keeps its side, book, account and grouping, and the order the choice is on keeps its book and account (unless the choice is that book or account). Where that needs it, the choice also writes out what those orders had inherited, in plain words, and covers them:
  - a side carried from the order before ("short 100 WIDG in the Fund book and 1 ACME Dec 18 2026 240/250 call spread": "buy 1 ACME ... call spread in the Fund book");
  - the orders after it that carry its side keep their own ("... and short 1 ACME ... put spread in the Institutional book");
  - a book or account phrase that would land in the closing phrase of the last order, and so apply to every order naming none, is matched by the others' own ("buy 3 WIDG ... calls in the Personal book and buy 1 ACME ... at I1:PB_MS in the Institutional book").
  - The opening part of a split ("stc 2 + sto 1") names the account the order was read in, so it stays where the calls are held.
  - A choice that cannot keep the other orders as they were is not offered: a side for the first of two orders without one would carry into the second.
- **Actions.** An action runs an existing step and then reads the text again: "Look it up", "Load the chain", or the product's own ticket.

## The route

POST /api/smart-ticket/parse[?book=P]

- **Body.** {"text", "book"?, "account_id"?}. The selected book is the body's book, else ?book=, else the first open book. No request id is needed; one sent is ignored.
- **200 answer.** {grammar, as_of, selected_book, text, ok, counts, legs, combos, unparsed, flags}.
  - A leg's request is exactly the order body: account_id, instrument_id, side, qty, order_type, tif, the prices and gtd_date that apply, and borrow: true for a short. It is null while the leg has a blocker.
  - A combination's request is {account_id, tif, package: {strategy_key: "CUSTOM", legs: [...]}}.
- **Errors.** 400 BAD_REQUEST, TEXT_TOO_LONG (over 2,000 characters) and TOO_MANY_LEGS (over 20 legs); 404 UNKNOWN_BOOK; 500 PARSE_FAILED (a fault of the parser, logged). Never 202: it never fetches.

## Purity

- **No side effects.** The parse writes nothing, fetches nothing, subscribes no quote, stores no preview and takes no mark.
- **What it reads.** It reads one database connection (books, lifetimes, accounts, positions, working orders), the in-memory instrument master, the cached chains (peek only, so the cache order never moves) and quote copies.
- **What it never calls.** option listing (ensure_option, _materialise_option), instruments.upsert / ensure_option, worker.resolve / request_bars / request_chain, cache.subscribe / pin, _ask_for_quote, chains.get / request / keep_on_screen, orders.preview, PreviewStore.put, marks.snapshot, bus.execute, db.writer.
- **How it is proven.** The tests prove this over a 304-case corpus: the state version, the database's data_version, the instrument and command counts, the chain cache's order and queue, the quote pins and priorities and the preview store are unchanged, and spies on every function above record no call. After deploy, gate1072_parse.py checks it on the live terminal.

## Limits in this version

- No net limit on an all-or-none combination; each leg fills at its executable bid / ask.
- FX, bonds, OTC and private names open their own ticket.
- Several books mean the same quantity in each, with no allocation.
- A month without a day ("Dec 2026") needs the cached chain, and resolves only when one expiry is listed that month.
- An option leg of a strategy that has two roots must be typed as OCC symbols in parentheses; no choice is offered there.
