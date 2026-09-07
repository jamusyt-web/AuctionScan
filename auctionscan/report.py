"""Output: terminal table, CSV, and a standalone HTML report."""

from __future__ import annotations

import csv
import html
from pathlib import Path

from .models import Deal


def to_console(deals: list[Deal], limit: int = 25) -> str:
    if not deals:
        return "No vehicles cleared the buy box.\n"

    lines = [
        f"{'SCORE':>5}  {'VEHICLE':<32} {'MILES':>8} {'FORMAT':<11} "
        f"{'PRICE':>9} {'MAX BID':>9} {'ROOM':>8}",
        "-" * 92,
    ]
    for deal in deals[:limit]:
        l = deal.listing
        acq = deal.acquisition
        fmt = acq.format.value if acq else "?"
        # A price we can't execute at is not a price. Show it as unknown.
        price = (
            f"{acq.price:,.0f}" if acq and acq.price is not None else "reserve?"
        )
        room = (
            f"{deal.headroom:,.0f}" if acq and acq.price is not None else "--"
        )
        lines.append(
            f"{deal.score:5.0f}  {l.description[:32]:<32} "
            f"{(l.odometer or 0):>8,} {fmt:<11} {price:>9} "
            f"{deal.max_bid:>9,.0f} {room:>8}"
        )
        if acq and acq.action:
            lines.append(f"       > {acq.action}")
        if deal.speculative:
            lines.append(
                f"       ~ SPECULATIVE: ${deal.max_bid:,.0f} worst case / "
                f"${deal.max_bid_optimistic:,.0f} best case "
                f"(${deal.risk_spread:,.0f} swing) - diagnose before bidding"
            )
        if deal.risk_flags:
            lines.append(f"       ! {'; '.join(deal.risk_flags[:3])}")
    if len(deals) > limit:
        lines.append(f"\n... and {len(deals) - limit} more")
    return "\n".join(lines) + "\n"


def to_csv(deals: list[Deal], path: str | Path) -> None:
    fields = [
        "score", "confidence", "vin", "year", "make", "model", "trim", "odometer",
        "grade", "current_bid", "max_bid", "max_bid_optimistic", "risk_spread",
        "speculative", "headroom", "projected_gross", "total_costs",
        "wholesale_source", "wholesale_value", "mmr", "instant_offer",
        "powertrain_reserve", "history_source", "accidents", "recon_estimate",
        "risk_flags", "location", "sale_date", "url",
    ]
    with Path(path).open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=fields)
        writer.writeheader()
        for d in deals:
            l = d.listing
            writer.writerow({
                "score": f"{d.score:.1f}",
                "confidence": f"{d.confidence:.2f}",
                "vin": l.vin,
                "year": l.year or "",
                "make": l.make,
                "model": l.model,
                "trim": l.trim,
                "odometer": l.odometer or "",
                "grade": l.grade if l.grade is not None else "",
                "current_bid": f"{l.current_bid:.0f}" if l.current_bid else "",
                "max_bid": f"{d.max_bid:.0f}",
                "max_bid_optimistic": f"{d.max_bid_optimistic:.0f}",
                "risk_spread": f"{d.risk_spread:.0f}",
                "speculative": "yes" if d.speculative else "",
                "mmr": (
                    f"{d.valuation.mmr:.0f}" if d.valuation and d.valuation.mmr else ""
                ),
                "instant_offer": (
                    f"{d.valuation.instant_offer:.0f}"
                    if d.valuation and d.valuation.instant_offer else ""
                ),
                "powertrain_reserve": (
                    f"{d.powertrain_reserve.high:.0f}" if d.powertrain_reserve else ""
                ),
                "headroom": f"{d.headroom:.0f}",
                "projected_gross": f"{d.projected_gross:.0f}",
                "total_costs": f"{d.total_costs:.0f}",
                "wholesale_source": d.valuation.source if d.valuation else "",
                "wholesale_value": (
                    f"{d.valuation.wholesale:.0f}"
                    if d.valuation and d.valuation.wholesale else ""
                ),
                "history_source": d.history.source if d.history else "",
                "accidents": d.history.accident_count if d.history else "",
                "recon_estimate": (
                    f"{d.condition.estimated_recon_cost:.0f}" if d.condition else ""
                ),
                "risk_flags": "; ".join(d.risk_flags),
                "location": l.location,
                "sale_date": l.sale_date.isoformat() if l.sale_date else "",
                "url": l.url,
            })


HTML_HEAD = """<title>AuctionScan Results</title>
<style>
  :root { --bg:#fbfbfa; --fg:#1a1a18; --muted:#6b6b66; --line:#e3e3de;
          --card:#fff; --good:#1a7f4b; --warn:#b45309; --bad:#b42318; }
  @media (prefers-color-scheme: dark) { :root:not([data-theme="light"]) {
    --bg:#16161a; --fg:#ececea; --muted:#9a9a94; --line:#2c2c32;
    --card:#1e1e23; --good:#4ade80; --warn:#fbbf24; --bad:#f87171; } }
  body { background:var(--bg); color:var(--fg); margin:0;
         font:15px/1.5 -apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif; }
  .wrap { max-width:1100px; margin:0 auto; padding:32px 20px 64px; }
  h1 { font-size:24px; margin:0 0 4px; letter-spacing:-0.01em; }
  .sub { color:var(--muted); margin:0 0 28px; font-size:14px; }
  .card { background:var(--card); border:1px solid var(--line); border-radius:10px;
          padding:16px 18px; margin-bottom:12px; }
  .row { display:flex; justify-content:space-between; align-items:baseline; gap:16px; }
  .veh { font-weight:600; font-size:16px; }
  .score { font-variant-numeric:tabular-nums; font-weight:700; font-size:20px; }
  .meta { color:var(--muted); font-size:13px; margin-top:2px; }
  .act { font-size:13px; font-weight:600; margin-top:3px; color:var(--good); }
  .nums { display:flex; gap:22px; margin-top:12px; flex-wrap:wrap;
          font-variant-numeric:tabular-nums; }
  .nums div { font-size:13px; } .nums b { display:block; font-size:16px; font-weight:600; }
  .good { color:var(--good); } .warn { color:var(--warn); } .bad { color:var(--bad); }
  .flags { margin-top:10px; font-size:13px; color:var(--bad); }
  .spec { margin-top:10px; padding:9px 11px; border-radius:7px; font-size:13px;
          background:color-mix(in srgb, var(--warn) 12%, transparent);
          border:1px solid color-mix(in srgb, var(--warn) 35%, transparent);
          color:var(--warn); }
  .why { margin-top:10px; font-size:12px; color:var(--muted); }
  .why div { margin-top:2px; }
</style>
"""


def to_html(deals: list[Deal], path: str | Path, title: str = "AuctionScan Results") -> None:
    def esc(x: object) -> str:
        return html.escape(str(x))

    cards = []
    for d in deals:
        l = d.listing
        cls = "good" if d.score >= 60 else "warn" if d.score >= 35 else "bad"
        flags = (
            f'<div class="flags">! {esc("; ".join(d.risk_flags))}</div>'
            if d.risk_flags else ""
        )
        spec = (
            f'<div class="spec"><b>Speculative</b> &mdash; '
            f'${d.max_bid:,.0f} if the failure is the expensive one, '
            f'${d.max_bid_optimistic:,.0f} if it is the cheap one. '
            f'A ${d.risk_spread:,.0f} swing on an undiagnosed car.</div>'
            if d.speculative else ""
        )
        why = "".join(f"<div>{esc(r)}</div>" for r in d.reasoning)
        cards.append(f"""
  <div class="card">
    <div class="row">
      <div>
        <div class="veh">{esc(l.description)}</div>
        <div class="act">{esc(d.acquisition.action) if d.acquisition else ""}</div>
        <div class="meta">{esc(l.vin)} &middot; {(l.odometer or 0):,} mi
          &middot; {esc(l.source)}{" &middot; " + esc(l.location) if l.location else ""}
          &middot; confidence {d.confidence:.0%}</div>
      </div>
      <div class="score {cls}">{d.score:.0f}</div>
    </div>
    <div class="nums">
      <div>{"Buy now" if d.acquisition and d.acquisition.format.value == "buy_now" else "Price"}<b>{
          f"${d.acquisition.price:,.0f}" if d.acquisition and d.acquisition.price is not None
          else "reserve unknown"}</b></div>
      <div>Max bid<b>${d.max_bid:,.0f}</b></div>
      <div>Headroom<b class="{cls}">${d.headroom:,.0f}</b></div>
      <div>Costs<b>${d.total_costs:,.0f}</b></div>
      <div>Gross at bid<b>${d.projected_gross:,.0f}</b></div>
    </div>
    {spec}
    {flags}
    <div class="why">{why}</div>
  </div>""")

    body = "".join(cards) or "<p>No vehicles cleared the buy box.</p>"
    doc = (
        HTML_HEAD
        + f'<div class="wrap"><h1>{esc(title)}</h1>'
        + f'<p class="sub">{len(deals)} vehicle(s) worth a look. '
        + "Max bid is your walk-away number, all-in.</p>"
        + body
        + "</div>"
    )
    Path(path).write_text(doc, encoding="utf-8")
