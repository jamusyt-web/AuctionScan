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
  exportPicks: "exportPicks", pickSlack: "pickSlack",
};

// Sale formats you buy. Each checkbox id is "fmt_" + the format name.
const FORMATS = ["buy_now", "make_offer", "absolute", "timed", "live"];

// Exit channels are a list in the config, so they're addressed by name.
// id -> [exit name, field]
const EXIT_FIELDS = {
  ex_wholesale_sellFee: ["wholesale", "sellFee"],
  ex_wholesale_daysHeld: ["wholesale", "daysHeld"],
  ex_wholesale_targetGross: ["wholesale", "targetGross"],
  ex_retail_enabled: ["retail", "enabled"],
  ex_retail_daysHeld: ["retail", "daysHeld"],
  ex_retail_targetGross: ["retail", "targetGross"],
};

function exitNamed(cfg, name) {
  cfg.exits ||= structuredClone(DEFAULT_CONFIG.exits);
  let ex = cfg.exits.find((e) => e.name === name);
  if (!ex) {
    ex = structuredClone(DEFAULT_CONFIG.exits.find((e) => e.name === name) || { name });
    cfg.exits.push(ex);
  }
  return ex;
}

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
  for (const f of FORMATS) {
    const el = $(`fmt_${f}`);
    if (el) el.checked = (cfg.formats || []).includes(f);
  }
  for (const [id, [name, key]] of Object.entries(EXIT_FIELDS)) {
    const el = $(id);
    if (!el) continue;
    const v = exitNamed(cfg, name)[key];
    if (el.type === "checkbox") el.checked = v !== false;
    else el.value = v ?? "";
  }
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
  cfg.formats = FORMATS.filter((f) => $(`fmt_${f}`)?.checked);
  for (const [id, [name, key]] of Object.entries(EXIT_FIELDS)) {
    const el = $(id);
    if (!el) continue;
    exitNamed(cfg, name)[key] = el.type === "checkbox" ? el.checked : num(el.value);
  }

  await saveConfig(cfg);
  // The alarm period lives in the service worker; nudge it to re-read.
  chrome.runtime.sendMessage({ type: "configChanged" }).catch(() => {});
  $("saved").textContent = "Saved";
  setTimeout(() => ($("saved").textContent = ""), 1800);
};

load();
