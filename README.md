# AuctionScan

Finds wholesale buys at online auctions that still have enough meat to flip to
another dealer.

It reads a run list, values each car, discounts that value for history and
condition, subtracts what it will actually cost you to own and move the car,
and gives you one number: **the most you can bid and still make your gross.**

```
SCORE  VEHICLE                               MILES       BID   MAX BID     ROOM  CONF
--------------------------------------------------------------------------------------
   15  2021 Ford F-150 XLT SuperCrew 4WD    62,400    18,500    21,776    3,276  0.15
   15  2020 Chevrolet Silverado 1500 LT C   79,800    16,200    19,004    2,804  0.15
   13  2020 Volkswagen Jetta GLI            43,700     9,200    10,920    1,720  0.15
```

## The hourly agent

Every hour from 7am to 10pm ET a scheduled Claude run picks up newly listed
**Buy Now and Make Offer** cars from two feeds: your saved-search alert emails
from ACV, OPENLANE and Manheim, and the extension's picks file. It opens the
promising ones in your Chrome and reads them like a buyer: the condition
report and photos, the AutoCheck/Carfax box, AccuTrade's MMR and target
auction, the transport quote. Then it scores each car with this engine
(`python -m auctionscan score`) and pings your phone when the price you can
actually pay clears your gross. Every car it reads lands on the AuctionScan
Finds dashboard, so nothing is checked or sent twice.

- `agent/RUNBOOK.md` — exactly what each run does, and its ground rules
  (read-only, never past a sign-in, at most 12 listing opens a run).
- `agent/SETUP.md` — the four setup steps.
- `auctionscan/agent_io.py` — the JSON the agent writes and the verdict it gets
  back (buy / look / pass, with the move and a phone-ready message).

## The browser extension

`extension/` is the part that does the looking. It triages search results for
free, opens only what survives, scores it locally with the same engine, and
notifies you when a car is worth buying. See `extension/README.md` to install
and test it.

The JS engine is a port of the Python one, and `extension/test/parity.test.mjs`
asserts the two produce identical numbers on the same cars — if they ever
diverge, that test fails before you find out from a bad bid.

## Getting listings in

There are no run lists any more — the marketplaces are live platforms and
nothing exports. So the input is a **screenshot of the page you're already
looking at**. Everything the scorer needs is rendered there: VIN, miles,
grade, announcements, the damage table, the bid or Buy Now or published
reserve, and the AccuTrade sidebar with MMR, target auction, instant offer
and retail.

```bash
# Snap the vehicle page and the AccuTrade panel, then:
python -m auctionscan grab adesa1.png adesa2.png --store watchlist.csv

# Score everything you've collected
python -m auctionscan scan --csv watchlist.csv --config config.yaml --valuation csv
```

Several shots of one car merge into a single listing. The watchlist is keyed
by VIN, so re-grabbing a car you already have updates its price instead of
duplicating it. Roughly $0.05–0.15 per car in API cost depending on effort
and image size; `--effort medium` roughly halves it.

This touches nothing but a picture you chose to take — no marketplace API, no
automated access, nothing near your dealer accounts.

A VIN that fails its check digit is reported loudly rather than dropped,
because in this workflow a bad checksum almost always means one character was
misread, and silently skipping it would hide a real car from your list.

## Quick start

```bash
pip install -r requirements.txt
cp config.example.yaml config.yaml     # then put YOUR numbers in it
python -m auctionscan scan --csv samples/sample_run_list.csv --config config.yaml
```

That runs offline with no credentials and no API keys, on synthetic sample
data, so you can see the shape of the output and tune your buy box before
paying for anything.

The `CONF 0.15` column in that output is the point: with no market valuation,
no history report and no condition analysis wired up, the tool tells you it
barely trusts its own answer. Confidence climbs as you connect real sources.

## How the number is built

```
max_bid = adjusted wholesale value
        - reconditioning
        - transport
        - buy fee + sell fee
        - floorplan / holding
        - the gross you demand
```

`adjusted wholesale value` starts from a market value (MMR or AccuTrade) and
comes down for the things that make the next dealer pay less:

| Factor | Haircut |
|---|---|
| Moderate accident | 8% (each additional accident adds 40% of that) |
| Structural damage | 18% |
| Airbag deployed | 10% |
| Hail / lemon / TMU | 12% / 20% / 25% |
| Rebuilt / flood / salvage | 35% / 45% / 50% |
| AutoGrade | +4% at 5.0 down to −30% at 0 |

Those percentages are starting points, not gospel. They live at the top of
`auctionscan/scoring.py` — tune them against your own sold data, because your
lane and your retail channel are not the average.

## Data sources

**If you already run AccuTrade with MMR and Carfax bundled in, AccuTrade is the
only valuation integration you need.** One call returns the Manheim MMR, the
target auction number, the instant offer, Cars.com median retail, the odometer
adjustment and the known failure modes for the VIN — replacing three separate
contracts. The VIN-dow browser extension shows exactly that payload on the
auction page; the Perseus API is the sanctioned way to get it programmatically.

| Source | What it gives you | Access |
|---|---|---|
| **CSV + VIN-dow numbers** | Everything below, keyed in by hand | Free, works today. `--valuation csv` |
| **ADESA / OPENLANE** | Listings incl. Buy Now | Negotiated — no public portal |
| **Manheim** | Listings incl. Buy Now | Developer portal, OAuth2 |
| **ACV** | Listings incl. Make Offer | Dealer API via your rep |
| **AccuTrade (Perseus)** | MMR, target auction, instant offer, retail, common problems | Cars Commerce API key |
| **NHTSA vPIC** | Factory build data, VIN validation | Free, no key |
| **Carfax** | Accident/service history | Bundled with AccuTrade, or a negotiated data agreement for API access |
| **NMVTIS** | Federal title brands, theft, total loss | ~$1–3/VIN — only worth it if you *don't* have unlimited Carfax |
| **Claude** | Recon estimate from the CR and photos | Anthropic API key |

Start with `--valuation csv`: read the VIN-dow numbers off the auction page
into a spreadsheet column. No API key, no contract, and the scoring is
identical to what it will be once Perseus is wired up. Swapping in
`--valuation accutrade` later changes nothing downstream.

Note that unlimited Carfax covers *viewing* reports. Pulling them
programmatically is a separate data agreement — which is the one thing worth
asking Carfax for.

```bash
# Once you have credentials:
export MANHEIM_CLIENT_ID=... MANHEIM_CLIENT_SECRET=...
export NMVTIS_API_KEY=... NMVTIS_API_URL=...
export ANTHROPIC_API_KEY=...

python -m auctionscan scan --csv run.csv --config config.yaml \
    --valuation mmr --history nmvtis --ai --decode \
    --out-html today.html
```

The API adapters in `auctionscan/sources/api_adapters.py`,
`enrich/valuation.py` and `enrich/history.py` have the auth, paging and retry
written, but the response field names are marked `CONFIRM` — those specs sit
behind a login and change, so check them against a real payload the first time
you connect.

## Drivability and powertrain risk

Every valuation you can buy — MMR, AccuTrade target auction, ADESA CLEAR —
prices a vehicle that **runs**. None of them price an INOP car, because none of
them know why it doesn't run. This is where the tool earns its keep.

When a listing is announced INOP or not drivable, the tool stops estimating
cosmetic recon and starts bracketing the unknown:

- Checks whether the powertrain is still under warranty. EV traction packs are
  covered 8 years **or** a mileage cap, whichever comes **first** — 120,000
  miles on a Model Y Long Range/Performance. Past that cap, the pack is yours.
- Prices the worst case (a traction pack, an engine) against the best case (a
  dead 12V, a missing charge cable).
- Reports **both** max bids and the spread between them.
- Cross-references AccuTrade's known failure modes for the VIN. When the list
  says "Battery Replacement" and the car in front of you won't move, that is
  not a coincidence.

A worked example is in `samples/adesa_vindow.csv` — a real 2021 Model Y
Performance at 143,991 miles, AS-IS and INOP, where the auction site's own
panel showed a projected profit of $8,096 and a max bid of $17,546:

```
~ SPECULATIVE: $-4,706 worst case / $11,894 best case ($16,600 swing)
! Known failure modes for this VIN line up with the no-start:
  Charger Missing; Battery Replacement; Electric Motor(s) Wear/Failure
```

That is the whole point. It is not a $17,546 car and it is not a $-4,706 car —
it is a coin flip with $16,600 riding on it, and no book value on the page
tells you that.

## The listed bid is not a price

On a timed auction with an undisclosed reserve, the number on screen is not
what the car costs. A unit showing $11,500 with zero bidders and a $13,500
reserve looks like the best deal on the page and is not for sale at that
price. Scoring spread against it manufactures deals that don't exist.

So the tool separates **what you'd actually pay** from what's displayed:

| Format | Price you'd pay | Certainty |
|---|---|---|
| **Buy Now** | The published price. Take it or leave it. | 1.00 |
| **Make Offer, reserve shown** | Their published reserve — offer it and it's yours | 1.00 |
| **Make Offer, no reserve shown** | Your number, offered blind | 0.80 |
| Absolute / no reserve | The bid is the price | 0.75 |
| Timed, reserve **met** | The bid is live | 0.50 |
| Timed, reserve undisclosed | **Unknown — not scored as a price** | 0.20 |

ACV's make-offer tab publishes the seller's reserve, which makes it a firm
price with free upside: offering it takes the car, and anything they accept
below it is money you keep. So it scores with full certainty and the
recommendation is always *offer the reserve, but try lower first*. Only a
blind offer with no published number falls back to a guess at acceptance.

Certainty multiplies the score, so executable prices outrank hopeful ones
automatically. Headroom is measured against the acquisition price, never the
listed bid. A hidden-reserve lot reports `reserve?` instead of a spread,
because inventing one would be lying.

Two flags for the way you actually buy:

```bash
# Only cars you can transact on right now
python -m auctionscan scan --csv run.csv --executable-only

# Just the Buy Now and Make Offer channels
python -m auctionscan scan --csv run.csv --format buy_now,make_offer
```

A known price above your max scores **zero**, not a thin deal — you know
exactly what it costs and it's too much. That's a pass. (The price is still
*transactable*; it just isn't a deal. Those are separate questions and the
code keeps them separate.)

## Your repair capability is the edge

The lane prices a broken car at what the repair costs *the average buyer*. If
you can do that repair in-house for less, the gap is your margin — but only a
tool that knows your cost can see it. So costs you can do yourself go in
`capabilities`:

```yaml
capabilities:
  ev_battery_pack: 3500       # your cost, not the market's $17,000
  ev_drive_unit: null         # can't price it? say so
  unpriced_powertrain_min_gross: 4000
```

A repair you've priced stops being a gamble and becomes a cost of goods — the
car is no longer flagged speculative, however scary it looks to everyone else.
A repair you *haven't* priced keeps the market's worst case, stays flagged, and
demands `unpriced_powertrain_min_gross` before it's worth doing. That's the
"only if there's a good amount of money on the table" rule, written down.

## Two ways out of a car

A wholesale flip and a fix-and-retail are different businesses: different exit
price, hold time, recon standard and gross target. Both are scored, and the
better one wins:

```yaml
exits:
  - name: wholesale
    days_held: 21
    sell_fee: 300
    recon_multiplier: 1.0
    target_gross: 1200
  - name: retail
    days_held: 45
    sell_fee: 0
    recon_multiplier: 1.6     # retail-ready costs more than lane-ready
    target_gross: 2500
```

This matters more than it sounds. On that Model Y, even at a $3,500 in-house
pack, the **wholesale flip never clears** — it's the retail exit that makes
broken EVs work. A tool that only modeled the flip would have told you to pass
on your own best niche.

One caution the code enforces: a "median retail" book number is not
odometer-adjusted, and using it as an exit price overstates a high-mile car by
thousands. Only a mileage-adjusted retail value is scored as an exit.

## The AI part

`auctionscan/ai/condition.py` sends the condition report text and up to 8
photos to Claude and gets back a structured recon estimate: damage items with
costs, tire status, mechanical concerns, and red flags.

It is scoped deliberately narrowly. It estimates **what recon will cost** — it
does not appraise the car. Valuation comes from market data; the model only
feeds the cost side. It is also told to refuse to price what it cannot see: a
warning light or a transmission complaint goes in `mechanical_concerns` with no
dollar figure attached, because you cannot see a bad transmission in a JPEG.
Photo-based estimates run light, so `recon_safety_multiplier` (default 1.25)
pads whatever comes back.

For a whole run list, `analyze_batch()` uses the Batch API at half price. These
calls are not latency-sensitive — you are looking at tomorrow's sale.

## Terms of service — read this before you automate a login

The adapters here use official APIs and your own credentials. That is
deliberate.

Manheim's terms prohibit automated queries, screen scraping, spiders, robots
and crawlers against their services without express written permission, and
every other major marketplace has equivalent language. Courts have held that
scraping *public* data is not a computer-crime violation, but that reasoning
does not extend to data behind a dealer login, and it has never protected
anyone from a breach-of-contract claim or from simply having their account
turned off.

Your Manheim, ACV and OPENLANE accounts are business-critical assets. A tool
that saves you two hours a week is not worth losing lane access over. Ask your
rep for API access — Cox, ACV and Cars Commerce all have dealer integration
programs, and "I want to plug your data into my buying process" is a
conversation they want to have.

## What already exists

Before you build much further, know that vAuto's **Stockwave** aggregates
~300,000 vehicles a day across 300+ marketplaces with Cox's own valuation data
behind it, and **Autoniq** and **Carbly** do lighter-weight versions. If a
generic buy box is all you need, buying one of those is cheaper than
maintaining this.

Where a tool like this one wins is the parts they don't do: your specific cost
model, your recon rates, your retail channel, AI reading of condition report
photos, and scoring rules you can actually inspect and change.

## Tests

```bash
python -m pytest tests/ -q
```

90 tests covering the money math, the agent's scoring path, the buy-box rules, announcement handling,
powertrain reserve bracketing, capability arbitrage, exit selection, sale-format
and acquisition-price handling, and the VIN decode. Network calls are stubbed,
so they run offline. The powertrain suite is built on the real Model Y listing
above as a regression case.

## Layout

```
auctionscan/
  models.py       Vehicle, Listing, Valuation, HistoryReport, Deal
  config.py       Buy box + cost model (YAML)
  scoring.py      The math. Start here.
  risk.py         Drivability + powertrain reserve. The part nobody else does.
  acquisition.py  Sale format, executable price, what to actually do
  config.py       Buy box, cost model, capabilities, exit channels
  pipeline.py     Orchestration: cheap filters before expensive lookups
  report.py       Console, CSV, HTML output
  sources/        Where listings come from (CSV, Manheim, ACV)
  enrich/         VIN decode, valuation, history
  ai/             Screenshot extraction + Claude condition analysis
  agent_io.py     JSON in/out for the hourly agent's `score` command
agent/            Hourly agent runbook and setup steps
extension/        Chrome extension: sweep, triage, score, notify, hand off picks
```

## Status

The scoring engine, buy box, CSV ingestion, VIN decode, reporting and AI
condition analysis are written and tested. The credentialed API adapters are
scaffolds pending real access — see the `CONFIRM` markers.

Sample data in `samples/` is synthetic and exists to demonstrate output shape.
Do not read anything into those specific vehicles or prices.
