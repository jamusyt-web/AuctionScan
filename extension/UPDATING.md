# Installing from git (so you stop reinstalling)

Clone once, then every update is `git pull` + one click. No more zips.

## One-time setup

```bash
git clone https://github.com/jamusyt-web/AuctionScan.git
cd AuctionScan
git checkout claude/wholesale-car-auction-finder-c46t91
```

Then in Chrome:

1. `chrome://extensions` → Developer mode on
2. **Load unpacked** → select the **`extension`** folder inside the clone
   (the one with `manifest.json`)

Because it now points at a real git working copy, the files on disk change
when you pull.

## Every update after that

```bash
cd AuctionScan
git pull
```

Then `chrome://extensions` → the **reload arrow** on the AuctionScan card.
That's it. Your settings live in Chrome storage and survive reloads.

The one exception: if a release **adds a permission** (a new site, say),
Chrome needs you to Remove and Load unpacked again so it can ask you to
approve it. Release notes will say when that applies.

## Windows

Same thing with GitHub Desktop, or:

```powershell
git clone https://github.com/jamusyt-web/AuctionScan.git
cd AuctionScan
git checkout claude/wholesale-car-auction-finder-c46t91
```

Load unpacked → `AuctionScan\extension`.

## 0.2.0

Adds the popup and Setup pages, which were never saved to the repo before (a
`.gitignore` rule meant for reports caught them), the Buy Now / Make Offer
filter, and the picks hand-off to the hourly agent. No new permissions: pull
and hit reload.

## Checking which version is loaded

The version shows on the extension's card in `chrome://extensions`. If it
doesn't match `manifest.json` after a pull, you didn't hit reload.
