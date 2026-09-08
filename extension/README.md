# AuctionScan browser extension

Watches ADESA/OPENLANE, Manheim and ACV for you and says something when a car
is worth buying.

## What it does

1. **Triage, free.** On a search results page it parses every row and decides
   what's worth opening — using year/make/model/miles/price only, so it works
   on ACV where the VIN doesn't appear until a car is opened.
2. **Opens only the survivors.** One tab at a time, jittered pauses, a hard cap
   per sweep. ~60 opens instead of thousands.
3. **Scores them properly.** The full engine — buy box, powertrain reserve at
   *your* repair cost, wholesale vs retail exit, executable price vs hidden
   reserve — runs locally in the extension.
4. **Tells you.** Desktop notification when something clears your bar, with the
   action: *buy now at $X*, *offer their published reserve*, *bid up to $Y*.

It never fetches anything on its own. Every page it reads was already delivered
to your browser.

## Install

Clone the repo and load this folder — then updates are `git pull` plus the
reload button, with no reinstall. See `UPDATING.md`.

1. `chrome://extensions` → enable **Developer mode**
2. **Load unpacked** → select this `extension/` folder
3. Click the icon → **Setup**

## Setup

**Saved searches** is the important field. Filter on the site first — year,
make, mileage, price, Buy Now / Make Offer — and paste the resulting URLs, one
per line. The site's own search doing the coarse filtering is what keeps a
sweep to ~60 page opens instead of thousands.

Then set your buy box, your costs, and — most importantly — **what you can fix
in-house**. A repair you've priced becomes a cost of goods; one you haven't
keeps the market's worst case and demands more gross before the tool calls it
a buy.

## Testing it

Open any listing on one of the four sites. A badge appears bottom-left with the
verdict, max bid, and what to do. That confirms extraction is working on real
markup before you turn the sweep loose.

Then: **Sweep now** in the popup. Watch the status line. When it finds
something you get a notification; the popup lists everything found and
**Export CSV** hands it to the Python CLI in the same format
(`scan --valuation csv`).

Turn on **Sweep automatically** only once a manual sweep looks right.

## If extraction misses

The parser is built on text patterns (a 17-character VIN, a dollar amount next
to the words "Buy Now"), not CSS selectors, so a redesign changes wording at
worst rather than breaking it outright. If a field comes back empty on a real
page, the label vocabulary in `src/sites.js` is where to add the wording that
site actually uses — no markup knowledge required.

## Tests

```bash
node --test "extension/test/*.test.mjs"
```

29 tests. `parity.test.mjs` asserts the JS engine produces the **same numbers**
as the Python engine on the same cars — if the two ever diverge, the badge on
the page would disagree with the CLI, and that test fails first.
`extract.test.mjs` runs against text taken off a real ADESA vehicle page.
