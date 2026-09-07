import { DEFAULT_CONFIG, loadConfig, saveConfig } from "./config.js";

const $ = (id) => document.getElementById(id);
const num = (v) => (v === "" || v == null ? null : Number(v));

// id -> path in the config object.
const FIELDS = {
  sweepEnabled: "sweepEnabled", sweepIntervalMinutes: "sweepIntervalMinutes",
  maxOpensPerSweep: "maxOpensPerSweep", alertScore: "alertScore",
  triageMinGap: "triageMinGap", executableOnly: "executableOnly",
  bb_minYear: "buyBox.minYear", bb_maxOdometer: "buyBox.maxOdometer",
  bb_minWholesaleValue: "buyBox.minWholesaleValue",
  bb_maxWholesaleValue: "buyBox.maxWholesaleValue",
  bb_targetGross: "buyBox.targetGross", bb_targetGrossPct: "buyBox.targetGrossPct",
  bb_minGrade: "buyBox.minGrade", bb_maxRecon: "buyBox.maxRecon",
  bb_maxPowertrainReserve: "buyBox.maxPowertrainReserve",
  bb_maxRiskSpread: "buyBox.maxRiskSpread",
  bb_allowInoperable: "buyBox.allowInoperable", bb_allowAsIsInop: "buyBox.allowAsIsInop",
  cap_evBatteryPack: "capabilities.evBatteryPack",
  cap_evDriveUnit: "capabilities.evDriveUnit",
  cap_engine: "capabilities.engine", cap_transmission: "capabilities.transmission",
  cap_unpricedPowertrainMinGross: "capabilities.unpricedPowertrainMinGross",
  c_buyFee: "costs.buyFee", c_floorplanDaily: "costs.floorplanDaily",
  c_transportPerMile: "costs.transportPerMile",
  c_defaultTransportMiles: "costs.defaultTransportMiles",
  c_baselineRecon: "costs.baselineRecon",
  c_reconSafetyMultiplier: "costs.reconSafetyMultiplier",
};

const get = (obj, path) => path.split(".").reduce((o, k) => o?.[k], obj);
function set(obj, path, value) {
  const keys = path.split(".");
  const last = keys.pop();
  keys.reduce((o, k) => (o[k] ??= {}), obj)[last] = value;
}

async function load() {
  const cfg = await loadConfig();
  for (const [id, path] of Object.entries(FIELDS)) {
    const el = $(id);
    if (!el) continue;
    const v = get(cfg, path);
    if (el.type === "checkbox") el.checked = Boolean(v);
    else el.value = v ?? "";
  }
  $("sweepUrls").value = (cfg.sweepUrls || []).join("\n");
  $("bb_excludedMakes").value = (cfg.buyBox.excludedMakes || []).join(", ");
}

$("save").onclick = async () => {
  const cfg = await loadConfig();
  for (const [id, path] of Object.entries(FIELDS)) {
    const el = $(id);
    if (!el) continue;
    set(cfg, path, el.type === "checkbox" ? el.checked : num(el.value));
  }
  cfg.sweepUrls = $("sweepUrls").value.split("\n").map((s) => s.trim()).filter(Boolean);
  cfg.buyBox.excludedMakes = $("bb_excludedMakes").value
    .split(",").map((s) => s.trim()).filter(Boolean);
  // Keep the exits from defaults; they aren't editable here yet.
  cfg.exits ||= DEFAULT_CONFIG.exits;

  await saveConfig(cfg);
  // The alarm period lives in the service worker; nudge it to re-read.
  chrome.runtime.sendMessage({ type: "configChanged" }).catch(() => {});
  $("saved").textContent = "Saved";
  setTimeout(() => ($("saved").textContent = ""), 1800);
};

load();
