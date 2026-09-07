// Default buy box, mirroring config.jamus.yaml so the extension and the CLI
// agree. Edited in the options page and stored in chrome.storage.sync.

export const DEFAULT_CONFIG = {
  buyBox: {
    minYear: 2015, maxYear: null, maxOdometer: 160000,
    minWholesaleValue: 5000, maxWholesaleValue: 60000,
    targetGross: 1200, targetGrossPct: 0.06,
    allowedMakes: [], excludedMakes: [],
    minGrade: 0, maxRecon: 6000,
    allowInoperable: true, allowAsIsInop: true,
    maxPowertrainReserve: 12000, maxRiskSpread: 5000,
    allowBrandedTitle: false, allowStructuralDamage: false,
    allowAirbagDeployed: false, maxAccidents: 1,
  },
  costs: {
    buyFee: 400, transportPerMile: 1.15, transportMinimum: 150,
    defaultTransportMiles: 250, floorplanDaily: 9.0,
    baselineRecon: 450, reconSafetyMultiplier: 1.25,
  },
  capabilities: {
    evBatteryPack: 3500,      // confirmed in-house cost
    evDriveUnit: null,        // TODO: set this -- it is the binding number
    engine: null, transmission: null,
    unpricedPowertrainMinGross: 4000,
  },
  exits: [
    { name: "wholesale", enabled: true, daysHeld: 21, sellFee: 300,
      reconMultiplier: 1.0, targetGross: 1200, targetGrossPct: 0.06 },
    { name: "retail", enabled: true, daysHeld: 45, sellFee: 0,
      reconMultiplier: 1.6, targetGross: 2500, targetGrossPct: 0.10 },
  ],

  // Sweep behavior
  triageMinGap: 1500,         // rough value must beat asking by this to open
  alertScore: 25,             // notify at or above this score
  executableOnly: true,       // only alert on prices you can actually take
  sweepIntervalMinutes: 30,
  maxOpensPerSweep: 60,
  openDelayMs: [4000, 11000], // random pause between opens
  sweepEnabled: false,        // off until you turn it on
};

export async function loadConfig() {
  const stored = await chrome.storage.sync.get("config");
  return deepMerge(structuredClone(DEFAULT_CONFIG), stored.config || {});
}

export async function saveConfig(config) {
  await chrome.storage.sync.set({ config });
}

function deepMerge(base, over) {
  for (const [k, v] of Object.entries(over || {})) {
    if (v && typeof v === "object" && !Array.isArray(v) && base[k]) deepMerge(base[k], v);
    else base[k] = v;
  }
  return base;
}
