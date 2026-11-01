# Setting up the hourly agent

Four things, about 20 minutes. Do them on **DeMechine (the Windows PC)**, in the
Chrome you buy from — that's the Chrome the agent works in.

## 1. Load the extension

If you already cloned the repo:

```powershell
cd AuctionScan
git pull
```

If not:

```powershell
git clone https://github.com/jamusyt-web/AuctionScan.git
cd AuctionScan
git checkout claude/wholesale-car-auction-finder-c46t91
```

Then `chrome://extensions` → **Developer mode** on → **Load unpacked** → pick the
`extension` folder (or hit the reload arrow if it's already loaded). The card
should say version **0.2.0**. Clicking the toolbar icon now opens the popup,
and **Setup** opens the settings page — both were missing before.

Also make sure AccuTrade's **VIN-dow** extension is installed and signed in in
this Chrome; the AccuTrade numbers come from its panel on each listing.

## 2. Save one search per auction, Buy Now and Make Offer only

On each site, filter to what you'd actually buy, then save the search with
alerts on:

- year 2015 and newer, 160,000 miles or less, the price range you buy in
- **Buy Now / Make Offer only** (leave timed auctions out)
- turn on **email** alerts for new matches; on Manheim pick the most frequent
  option and the longest duration it offers

| Site | Where | Alerts |
|---|---|---|
| ACV | Search → filters → Save Search | In the app, plus email if you switch it on |
| OPENLANE | Search → filters → save the search | Opt in to alerts on the saved search |
| Manheim | Search → filters → Save Search | Email; choose how often and for how long |

Send the alerts to the Gmail you connect in step 4. If an auction account
emails a different address, change its notification email or forward those
alerts to that Gmail.

## 3. Give the extension the same searches

Copy each saved search's results-page URL into the extension's **Setup →
Saved searches**, one per line, and save. Check that **Buy Now** and **Make
Offer** are the only formats ticked and that **Save picks for the agent** is on.

Then click the toolbar icon → **Sweep now**. When it finishes, the popup says
"Picks for Claude: N · saved … to Downloads/AuctionScan" and the folder
`Downloads\AuctionScan` exists. If a page couldn't be read, the "What the last
page reads saw" section says what it saw. Once a manual sweep looks right,
tick **Sweep automatically** (every 60 minutes).

## 4. Connect Gmail and switch the agent on

Connect the Gmail connector in Claude (the card in the chat) with the inbox
from step 2. Then tell Claude you're ready: it connects the
`Downloads\AuctionScan` folder, creates the hourly scheduled task and does the
first run with you watching.

## Fix the placeholder costs

`config.jamus.yaml` still guesses at your buy fee ($400), sell fee ($300),
floorplan ($9/day) and how long a fixed car takes to retail (45 days). Every
max bid uses them. Send your real numbers and they get updated in the config
and the extension.
