const $ = (id) => document.getElementById(id);
const money = (n) => "$" + Math.round(n || 0).toLocaleString("en-US");

async function render() {
  const state = await chrome.runtime.sendMessage({ type: "getState" });
  const finds = (state?.finds || []).filter((f) => f.alerted);
  const s = state?.sweepStatus;

  $("status").textContent = s
    ? (s.running
        ? `Sweeping — opened ${s.opened || 0}, ${s.found || 0} worth a look. ${s.note || ""}`
        : (s.note || "Idle"))
    : "Idle — no sweep run yet.";

  if (state?.lastError) {
    $("status").textContent += `  ⚠ ${state.lastError.message}`;
  }

  const list = $("list");
  list.innerHTML = "";
  if (!finds.length) {
    list.innerHTML = `<div class="empty">Nothing yet.<br>Add a saved search under
      Setup, then hit Sweep now.</div>`;
    return;
  }

  for (const f of finds) {
    const l = f.listing || {};
    const name = [l.year, l.make, l.model, l.trim].filter(Boolean).join(" ") || l.vin || "Vehicle";
    const price = f.acquisition?.price != null ? money(f.acquisition.price) : "reserve?";
    const div = document.createElement("div");
    div.className = "find";
    div.innerHTML = `
      <div class="top">
        <span class="veh"></span>
        <span class="score good">${Math.round(f.score)}</span>
      </div>
      <div class="act"></div>
      <div class="meta">${l.odometer ? l.odometer.toLocaleString() + " mi · " : ""}
        price ${price} · max ${money(f.maxBid)} · room ${money(f.headroom)}
        ${f.exitChannel ? "· " + f.exitChannel : ""}</div>
      ${f.riskFlags?.length ? `<div class="flags"></div>` : ""}`;
    div.querySelector(".veh").textContent = name;
    div.querySelector(".act").textContent = f.acquisition?.action || "";
    if (f.riskFlags?.length) div.querySelector(".flags").textContent = "! " + f.riskFlags.join("; ");
    div.onclick = () => f.url && chrome.tabs.create({ url: f.url });
    list.appendChild(div);
  }
}

$("sweep").onclick = async () => {
  $("sweep").disabled = true;
  await chrome.runtime.sendMessage({ type: "runSweepNow" });
  setTimeout(() => { $("sweep").disabled = false; render(); }, 1200);
};
$("options").onclick = () => chrome.runtime.openOptionsPage();
$("clear").onclick = async () => {
  await chrome.runtime.sendMessage({ type: "clearFinds" });
  render();
};

// Same column names the Python CLI reads, so an export drops straight into
// `auctionscan scan --csv ... --valuation csv`.
$("export").onclick = async () => {
  const state = await chrome.runtime.sendMessage({ type: "getState" });
  const fields = ["Lot","VIN","Year","Make","Model","Trim","Miles","Grade","Current Bid",
    "Buy Now","Reserve","Reserve Met","Make Offer","Sale Format","Announcements",
    "Mechanical","Condition Report","Common Problems","Seller Type","Location","Source",
    "MMR","Target Auction","Instant Offer","Retail","Median Retail"];
  const rows = (state?.finds || []).map((f) => {
    const l = f.listing || {};
    return {
      Lot: "", VIN: l.vin || "", Year: l.year || "", Make: l.make || "", Model: l.model || "",
      Trim: l.trim || "", Miles: l.odometer || "", Grade: l.grade ?? "",
      "Current Bid": l.currentBid || "", "Buy Now": l.buyNow || "", Reserve: l.reserve || "",
      "Reserve Met": l.reserveMet == null ? "" : (l.reserveMet ? "yes" : "no"),
      "Make Offer": l.makeOffer ? "yes" : "", "Sale Format": l.saleFormat || "",
      Announcements: (l.announcements || []).join("; "),
      Mechanical: l.drivable === false ? "Not Drivable - INOP" : "",
      "Condition Report": l.conditionReport || "", "Common Problems": "",
      "Seller Type": "", Location: "", Source: l.source || "",
      MMR: l.mmr || "", "Target Auction": l.targetAuction || "",
      "Instant Offer": l.instantOffer || "", Retail: l.retail || "",
      "Median Retail": l.medianRetail || "",
    };
  });
  const esc = (v) => `"${String(v).replace(/"/g, '""')}"`;
  const csv = [fields.join(","), ...rows.map((r) => fields.map((f) => esc(r[f])).join(","))].join("\n");
  const url = "data:text/csv;charset=utf-8," + encodeURIComponent(csv);
  chrome.downloads
    ? chrome.downloads.download({ url, filename: "auctionscan-watchlist.csv" })
    : chrome.tabs.create({ url });
};

render();
setInterval(render, 3000);
