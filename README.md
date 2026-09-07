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

34 tests covering the money math, the buy-box rules, announcement handling,
powertrain reserve bracketing and the VIN decode. Network calls are stubbed,
so they run offline. The powertrain suite is built on the real Model Y listing
above as a regression case.

## Layout

```
auctionscan/
  models.py       Vehicle, Listing, Valuation, HistoryReport, Deal
  config.py       Buy box + cost model (YAML)
  scoring.py      The math. Start here.
  risk.py         Drivability + powertrain reserve. The part nobody else does.
  pipeline.py     Orchestration: cheap filters before expensive lookups
  report.py       Console, CSV, HTML output
  sources/        Where listings come from (CSV, Manheim, ACV)
  enrich/         VIN decode, valuation, history
  ai/             Claude condition analysis
```

## Status

The scoring engine, buy box, CSV ingestion, VIN decode, reporting and AI
condition analysis are written and tested. The credentialed API adapters are
scaffolds pending real access — see the `CONFIRM` markers.

Sample data in `samples/` is synthetic and exists to demonstrate output shape.
Do not read anything into those specific vehicles or prices.
