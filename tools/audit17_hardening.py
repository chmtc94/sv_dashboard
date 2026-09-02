from __future__ import annotations

import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CC = ROOT / "custom_components" / "sv_dashboard"
STATIC = CC / "static"
TESTS = ROOT / "tests"
OLD_VERSION = "0.6.0-beta.4"
NEW_VERSION = "0.6.0-beta.5"


def read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def replace(path: Path, old: str, new: str, *, count: int = -1) -> None:
    text = read(path)
    if old not in text:
        raise RuntimeError(f"Expected text not found in {path}: {old[:100]!r}")
    write(path, text.replace(old, new, count))


def replace_regex(path: Path, pattern: str, replacement: str, *, count: int = 0) -> None:
    text = read(path)
    updated, matches = re.subn(pattern, replacement, text, count=count, flags=re.S)
    if matches == 0:
        raise RuntimeError(f"Expected pattern not found in {path}: {pattern}")
    write(path, updated)


# ---------------------------------------------------------------------------
# Version/cache bump and local frontend runtime dependency.
# ---------------------------------------------------------------------------
manifest_path = CC / "manifest.json"
manifest = json.loads(read(manifest_path))
if manifest.get("version") != OLD_VERSION:
    raise RuntimeError(f"Unexpected manifest version: {manifest.get('version')}")
manifest["version"] = NEW_VERSION
write(manifest_path, json.dumps(manifest, indent=2, ensure_ascii=False) + "\n")

replace(CC / "const.py", f'FRONTEND_VERSION = "{OLD_VERSION}"', f'FRONTEND_VERSION = "{NEW_VERSION}"')

for path in [*STATIC.glob("*.js"), *TESTS.glob("*.mjs")]:
    text = read(path)
    if OLD_VERSION in text:
        write(path, text.replace(OLD_VERSION, NEW_VERSION))

for path in STATIC.glob("*.js"):
    text = read(path)
    updated = re.sub(
        r'from\s+["\']https://unpkg\.com/lit\?module["\']',
        f'from "./vendor-lit.js?v={NEW_VERSION}"',
        text,
    )
    write(path, updated)

# ---------------------------------------------------------------------------
# Move the two beta.4 card-local language matrices into the shared frontend
# localisation layer. The data itself is preserved byte-for-byte.
# ---------------------------------------------------------------------------
def extract_card_text(path: Path) -> str:
    text = read(path)
    match = re.search(r"const TEXT = (\{.*?\n\});\n\nconst statusCandidates", text, re.S)
    if not match:
        raise RuntimeError(f"Could not extract TEXT catalog from {path}")
    catalog = match.group(1)
    text = text[: match.start()] + "const statusCandidates" + text[match.end() :]
    write(path, text)
    return catalog


dual_path = STATIC / "dual-energy-overview-card.js"
fuel_path = STATIC / "fuel-history-card.js"
dual_catalog = extract_card_text(dual_path)
fuel_catalog = extract_card_text(fuel_path)

hybrid_i18n_path = STATIC / "i18n-hybrid-cards.js"
write(
    hybrid_i18n_path,
    "/* Shared 18-language catalogs for Hybrid/Fuel-specific SV cards. */\n"
    "export const HYBRID_CARD_TEXT = {\n"
    f"  dualEnergyOverview: {dual_catalog},\n"
    f"  fuelHistory: {fuel_catalog},\n"
    "};\n",
)

# Cards now consume the shared catalog and HA locale resolver.
replace(
    dual_path,
    f'import {{ languageFor }} from "./i18n.js?v={NEW_VERSION}";',
    f'import {{ localeFor, textFor }} from "./i18n.js?v={NEW_VERSION}";',
)
replace(
    dual_path,
    '  _text() { const language = languageFor(this._hass || {}); return TEXT[language] || TEXT.en; }',
    '  _text() { return textFor(this._hass || {}, "dualEnergyOverview"); }',
)
replace(
    dual_path,
    '  _formatValue(entityId, digits = 0) { const value = numeric(this._hass?.states?.[entityId]); if (value === null) return "—"; return new Intl.NumberFormat(languageFor(this._hass), { maximumFractionDigits: digits, minimumFractionDigits: digits }).format(value); }',
    '  _formatValue(entityId, digits = 0) { const value = numeric(this._hass?.states?.[entityId]); if (value === null) return "—"; return new Intl.NumberFormat(localeFor(this._hass), { maximumFractionDigits: digits, minimumFractionDigits: digits }).format(value); }',
)
replace_regex(
    dual_path,
    r'const registrationLanguage = languageFor\(\{ locale: \{ language: typeof navigator !== "undefined" \? navigator\.language : "en" \} \}\);\nconst registrationText = TEXT\[registrationLanguage\] \|\| TEXT\.en;',
    'const registrationText = textFor({ locale: { language: typeof navigator !== "undefined" ? navigator.language : "en" } }, "dualEnergyOverview");',
)

replace(
    fuel_path,
    f'import {{ languageFor, localeFor }} from "./i18n.js?v={NEW_VERSION}";',
    f'import {{ localeFor, textFor }} from "./i18n.js?v={NEW_VERSION}";',
)
replace(
    fuel_path,
    '  _text() { const language = languageFor(this._hass || {}); return TEXT[language] || TEXT.en; }',
    '  _text() { return textFor(this._hass || {}, "fuelHistory"); }',
)
replace_regex(
    fuel_path,
    r'const registrationLanguage = languageFor\(\{ locale: \{ language: typeof navigator !== "undefined" \? navigator\.language : "en" \} \}\);\nconst registrationText = TEXT\[registrationLanguage\] \|\| TEXT\.en;',
    'const registrationText = textFor({ locale: { language: typeof navigator !== "undefined" ? navigator.language : "en" } }, "fuelHistory");',
)

# Add the shared card catalogs to the runtime composition layer.
i18n_path = STATIC / "i18n.js"
replace(
    i18n_path,
    f'import {{ ADVANCED_FRONTEND_TEXT as EASTERN_ADVANCED }} from "./i18n-advanced-east.js?v={NEW_VERSION}";\n',
    f'import {{ ADVANCED_FRONTEND_TEXT as EASTERN_ADVANCED }} from "./i18n-advanced-east.js?v={NEW_VERSION}";\nimport {{ HYBRID_CARD_TEXT }} from "./i18n-hybrid-cards.js?v={NEW_VERSION}";\n',
)
replace(
    i18n_path,
    '// Capability labels are owned by the per-language catalogs.\n',
    'for (const [namespace, catalog] of Object.entries(HYBRID_CARD_TEXT)) {\n  FRONTEND_TEXT[namespace] = catalog;\n}\n\n// Capability labels are owned by the per-language catalogs.\n',
)

# ---------------------------------------------------------------------------
# Harden fuel/refuelling history: require a sustained increase instead of a
# single Recorder spike. Litres remain direct-source-only.
# ---------------------------------------------------------------------------
replace_regex(
    fuel_path,
    r'  _detect\(levelStates, refillStates\) \{.*?\n  \}\n  async _maybeLoad\(\) \{',
    '''  _median(values) {
    const sorted = values.filter(Number.isFinite).sort((a, b) => a - b);
    if (!sorted.length) return null;
    const middle = Math.floor(sorted.length / 2);
    return sorted.length % 2 ? sorted[middle] : (sorted[middle - 1] + sorted[middle]) / 2;
  }
  _detect(levelStates, refillStates) {
    const minimum = Math.max(1, Number(this._config.minimum_increase) || 5);
    const samples = levelStates.map((raw) => this._normalize(raw)).map((state) => ({ ...state, value: numberValue(state.state) })).filter((state) => state.value !== null && Number.isFinite(Date.parse(state.last_updated || ""))).sort((a,b) => Date.parse(a.last_updated) - Date.parse(b.last_updated));
    const refill = refillStates.map((raw) => this._normalize(raw)).filter((state) => Number.isFinite(Date.parse(state.last_updated || "")));
    const events = [];
    for (let index = 1; index < samples.length; index += 1) {
      const after = samples[index];
      const confirmation = samples[index + 1];
      if (!confirmation) continue;
      const baseline = this._median(samples.slice(Math.max(0, index - 3), index).map((state) => state.value));
      if (baseline === null) continue;
      const increase = after.value - baseline;
      if (increase < minimum) continue;
      // A real refuel must survive the next vehicle report. This rejects the
      // common one-sample fuel-level bounce without deriving tank volume.
      const sustainedFloor = baseline + Math.max(1, minimum * 0.6);
      if (confirmation.value < sustainedFloor) continue;
      const timestamp = Date.parse(after.last_updated);
      const confirmedAfter = Math.max(after.value, confirmation.value);
      const last = events.at(-1);
      const amount = this._nearestAmount(refill, timestamp);
      if (last && timestamp - Date.parse(last.time) <= 60 * 60 * 1000) {
        last.before = Math.min(last.before, baseline);
        last.after = Math.max(last.after, confirmedAfter);
        last.time = after.last_updated;
        if (amount !== null) last.liters = amount;
        continue;
      }
      events.push({ time: after.last_updated, before: baseline, after: confirmedAfter, liters: amount });
    }
    return events.reverse().slice(0, Math.max(0, Number(this._config.max_events) || 50));
  }
  async _maybeLoad() {''',
    count=1,
)

# ---------------------------------------------------------------------------
# Powertrain fallback + stable vehicle identity.
# ---------------------------------------------------------------------------
const_path = CC / "const.py"
replace(
    const_path,
    'CONF_BATTERY_CAPACITY_KWH = "battery_capacity_kwh"\n',
    'CONF_BATTERY_CAPACITY_KWH = "battery_capacity_kwh"\nCONF_VEHICLE_VIN = "vehicle_vin"\nCONF_POWERTRAIN_OVERRIDE = "powertrain_override"\n',
)
replace(
    const_path,
    'METRIC_CURRENT_TRIP_ENERGY = "current_trip_energy"\n',
    'METRIC_CURRENT_TRIP_ENERGY = "current_trip_energy"\nMETRIC_CURRENT_TRIP_CONSUMPTION = "current_trip_consumption"\n',
)
replace(
    const_path,
    '    METRIC_CURRENT_TRIP_ENERGY,\n',
    '    METRIC_CURRENT_TRIP_ENERGY,\n    METRIC_CURRENT_TRIP_CONSUMPTION,\n',
)

config_path = CC / "config_flow.py"
replace(
    config_path,
    'from .capabilities import capability_map, mapping_from_registry_entries, powertrain_from_mapping\n',
    'from .capabilities import (\n    KNOWN_POWERTRAINS,\n    POWERTRAIN_UNKNOWN,\n    capability_map,\n    mapping_from_registry_entries,\n    normalize_powertrain,\n    powertrain_from_mapping,\n)\n',
)
replace(
    config_path,
    '    CONF_BATTERY_CAPACITY_KWH,\n    CONF_VEHICLE_DEVICE_ID,\n',
    '    CONF_BATTERY_CAPACITY_KWH,\n    CONF_POWERTRAIN_OVERRIDE,\n    CONF_VEHICLE_DEVICE_ID,\n    CONF_VEHICLE_VIN,\n',
)
replace(
    config_path,
    '\n\ndef _upstream_vehicle_entries(hass, device_id: str):\n',
    '''\n\ndef _vehicle_vin_for_device(hass, device_id: str) -> str | None:
    device = dr.async_get(hass).async_get(device_id)
    if device is None:
        return None
    for identifier in device.identifiers:
        if len(identifier) >= 2 and identifier[0] == UPSTREAM_DOMAIN:
            vin = str(identifier[1]).strip()
            if vin:
                return vin
    return None


def _upstream_vehicle_entries(hass, device_id: str):
''',
)
replace(config_path, '    VERSION = 1\n', '    VERSION = 2\n')
replace(
    config_path,
    '                await self.async_set_unique_id(f"{DOMAIN}_{device_id}")\n',
    '                vin = _vehicle_vin_for_device(self.hass, device_id)\n                await self.async_set_unique_id(f"{DOMAIN}_{vin or device_id}")\n',
)
replace(
    config_path,
    '                        data = {\n                            CONF_VEHICLE_DEVICE_ID: device_id,\n                            CONF_VEHICLE_SLUG: vehicle_slug,\n                        }\n',
    '                        data = {\n                            CONF_VEHICLE_DEVICE_ID: device_id,\n                            CONF_VEHICLE_SLUG: vehicle_slug,\n                        }\n                        if vin:\n                            data[CONF_VEHICLE_VIN] = vin\n',
)

# Compute the automatic powertrain before processing Options input.
replace(
    config_path,
    '    async def async_step_init(self, user_input=None):\n        """Configure title, capacity fallback and portable modules."""\n        if user_input is not None:\n',
    '''    async def async_step_init(self, user_input=None):
        """Configure title, capacity fallback and portable modules."""
        device_id = self.config_entry.data[CONF_VEHICLE_DEVICE_ID]
        mapping = mapping_from_registry_entries(_upstream_vehicle_entries(self.hass, device_id))
        auto_powertrain = powertrain_from_mapping(self.hass, mapping)
        configured_override = normalize_powertrain(
            self.config_entry.data.get(CONF_POWERTRAIN_OVERRIDE)
        )

        if user_input is not None:
''',
)
replace(
    config_path,
    '            capacity = normalized.pop(CONF_BATTERY_CAPACITY_KWH, None)\n            entry_data = dict(self.config_entry.data)\n',
    '''            capacity = normalized.pop(CONF_BATTERY_CAPACITY_KWH, None)
            powertrain_override = normalized.pop(CONF_POWERTRAIN_OVERRIDE, None)
            entry_data = dict(self.config_entry.data)
            if auto_powertrain == POWERTRAIN_UNKNOWN:
                normalized_override = normalize_powertrain(powertrain_override)
                if normalized_override in KNOWN_POWERTRAINS:
                    entry_data[CONF_POWERTRAIN_OVERRIDE] = normalized_override
                else:
                    entry_data.pop(CONF_POWERTRAIN_OVERRIDE, None)
            else:
                # Automatic detection always wins and stale fallbacks disappear.
                entry_data.pop(CONF_POWERTRAIN_OVERRIDE, None)
''',
)
replace(
    config_path,
    '        fields = {\n            vol.Optional(\n                OPTION_DASHBOARD_NAME,\n                default=options[OPTION_DASHBOARD_NAME],\n            ): str,\n        }\n        capabilities = _vehicle_capabilities_for_device(\n            self.hass, self.config_entry.data[CONF_VEHICLE_DEVICE_ID]\n        )\n',
    '''        fields = {
            vol.Optional(
                OPTION_DASHBOARD_NAME,
                default=options[OPTION_DASHBOARD_NAME],
            ): str,
        }
        if auto_powertrain == POWERTRAIN_UNKNOWN:
            override_key = (
                vol.Optional(CONF_POWERTRAIN_OVERRIDE, default=configured_override)
                if configured_override in KNOWN_POWERTRAINS
                else vol.Optional(CONF_POWERTRAIN_OVERRIDE)
            )
            fields[override_key] = selector.SelectSelector(
                selector.SelectSelectorConfig(
                    options=sorted(KNOWN_POWERTRAINS),
                    mode=selector.SelectSelectorMode.DROPDOWN,
                )
            )
        effective_powertrain = (
            auto_powertrain
            if auto_powertrain != POWERTRAIN_UNKNOWN
            else configured_override
        )
        capabilities = capability_map(effective_powertrain, mapping)
''',
)

entity_identity_path = CC / "entity_identity.py"
replace(
    entity_identity_path,
    'from .const import CONF_VEHICLE_DEVICE_ID, DOMAIN, UPSTREAM_DOMAIN\n',
    'from .const import (\n    CONF_VEHICLE_DEVICE_ID,\n    CONF_VEHICLE_VIN,\n    DOMAIN,\n    UPSTREAM_DOMAIN,\n)\n',
)
replace_regex(
    entity_identity_path,
    r'def vehicle_vin\(hass: HomeAssistant, entry: ConfigEntry\) -> str \| None:.*?\n\ndef _vehicle_identity_base',
    '''def _device_vin(device: Any) -> str | None:
    if device is None:
        return None
    for identifier in device.identifiers:
        if len(identifier) >= 2 and identifier[0] == UPSTREAM_DOMAIN:
            vin = str(identifier[1]).strip()
            if vin:
                return vin
    return None


def _device_for_vin(hass: HomeAssistant, vin: str | None):
    if not vin:
        return None
    registry = dr.async_get(hass)
    for device in registry.devices.values():
        if _device_vin(device) == vin:
            return device
    return None


def vehicle_vin(hass: HomeAssistant, entry: ConfigEntry) -> str | None:
    """Return the stable upstream VIN, with stored identity as recovery fallback."""
    device_id = entry.data.get(CONF_VEHICLE_DEVICE_ID)
    device = dr.async_get(hass).async_get(device_id) if device_id else None
    live_vin = _device_vin(device)
    stored_vin = str(entry.data.get(CONF_VEHICLE_VIN) or "").strip() or None
    if live_vin:
        return live_vin
    if stored_vin:
        recovered = _device_for_vin(hass, stored_vin)
        return _device_vin(recovered) or stored_vin
    return None


def async_repair_vehicle_reference(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Repair a stale HA device pointer from the stable VIN without touching upstream."""
    data = dict(entry.data)
    stored_vin = str(data.get(CONF_VEHICLE_VIN) or "").strip() or None
    device_id = data.get(CONF_VEHICLE_DEVICE_ID)
    registry = dr.async_get(hass)
    device = registry.async_get(device_id) if device_id else None
    live_vin = _device_vin(device)

    if stored_vin and live_vin != stored_vin:
        recovered = _device_for_vin(hass, stored_vin)
        if recovered is not None:
            device = recovered
            live_vin = _device_vin(recovered)

    if device is None and stored_vin:
        device = _device_for_vin(hass, stored_vin)
        live_vin = _device_vin(device)

    changed = False
    if device is not None and data.get(CONF_VEHICLE_DEVICE_ID) != device.id:
        data[CONF_VEHICLE_DEVICE_ID] = device.id
        changed = True
    if live_vin and data.get(CONF_VEHICLE_VIN) != live_vin:
        data[CONF_VEHICLE_VIN] = live_vin
        changed = True
    if changed:
        hass.config_entries.async_update_entry(entry, data=data)


def _vehicle_identity_base''',
    count=1,
)

init_path = CC / "__init__.py"
replace(
    init_path,
    '    CONF_VEHICLE_SLUG,\n',
    '    CONF_VEHICLE_DEVICE_ID,\n    CONF_VEHICLE_SLUG,\n    CONF_VEHICLE_VIN,\n',
)
replace(
    init_path,
    'from .entity_identity import async_migrate_package_entity_ids\n',
    'from .entity_identity import (\n    async_migrate_package_entity_ids,\n    async_repair_vehicle_reference,\n    vehicle_vin,\n)\n',
)
replace(
    init_path,
    '\n\nasync def async_setup_entry(\n',
    '''\n\nasync def async_migrate_entry(hass: HomeAssistant, entry: SvDashboardConfigEntry) -> bool:
    """Migrate beta config-entry identity from HA device id to stable VIN."""
    if entry.version > 2:
        return False
    async_repair_vehicle_reference(hass, entry)
    data = dict(entry.data)
    vin = vehicle_vin(hass, entry)
    if vin:
        data[CONF_VEHICLE_VIN] = vin
    identity = vin or data.get(CONF_VEHICLE_DEVICE_ID) or entry.entry_id
    desired_unique_id = f"{DOMAIN}_{identity}"
    conflict = next(
        (
            candidate
            for candidate in hass.config_entries.async_entries(DOMAIN)
            if candidate.entry_id != entry.entry_id
            and candidate.unique_id == desired_unique_id
        ),
        None,
    )
    kwargs = {"data": data, "version": 2}
    if conflict is None:
        kwargs["unique_id"] = desired_unique_id
    else:
        _LOGGER.warning(
            "Keeping existing SV config-entry unique id because %s is already owned by %s",
            desired_unique_id,
            conflict.entry_id,
        )
    hass.config_entries.async_update_entry(entry, **kwargs)
    return True


async def async_setup_entry(
''',
    count=1,
)
replace(
    init_path,
    '    # Normalize only package-owned registry rows before platform setup. This\n',
    '    async_repair_vehicle_reference(hass, entry)\n\n    # Normalize only package-owned registry rows before platform setup. This\n',
)

coordinator_path = CC / "coordinator.py"
replace(
    coordinator_path,
    'from .capabilities import capability_map, powertrain_from_mapping\n',
    'from .capabilities import (\n    POWERTRAIN_UNKNOWN,\n    capability_map,\n    normalize_powertrain,\n    powertrain_from_mapping,\n)\n',
)
replace(
    coordinator_path,
    '    CONF_VEHICLE_DEVICE_ID,\n',
    '    CONF_POWERTRAIN_OVERRIDE,\n    CONF_VEHICLE_DEVICE_ID,\n',
)
replace(
    coordinator_path,
    '        powertrain = powertrain_from_mapping(self.hass, entity_mapping)\n        capabilities = capability_map(powertrain, entity_mapping)\n',
    '''        auto_powertrain = powertrain_from_mapping(self.hass, entity_mapping)
        fallback_powertrain = normalize_powertrain(
            self.entry.data.get(CONF_POWERTRAIN_OVERRIDE)
        )
        powertrain = (
            fallback_powertrain
            if auto_powertrain == POWERTRAIN_UNKNOWN
            and fallback_powertrain != POWERTRAIN_UNKNOWN
            else auto_powertrain
        )
        powertrain_source = (
            "fallback_override"
            if auto_powertrain == POWERTRAIN_UNKNOWN
            and powertrain != POWERTRAIN_UNKNOWN
            else "automatic"
            if auto_powertrain != POWERTRAIN_UNKNOWN
            else "unknown"
        )
        capabilities = capability_map(powertrain, entity_mapping)
''',
)
replace(
    coordinator_path,
    '            "powertrain": powertrain,\n',
    '            "powertrain": powertrain,\n            "auto_powertrain": auto_powertrain,\n            "powertrain_source": powertrain_source,\n',
)

sensor_path = CC / "sensor.py"
replace(
    sensor_path,
    '    METRIC_CURRENT_TRIP_ENERGY,\n',
    '    METRIC_CURRENT_TRIP_CONSUMPTION,\n    METRIC_CURRENT_TRIP_ENERGY,\n',
)
replace(
    sensor_path,
    '            "powertrain": self.coordinator.data.get("powertrain", "unknown"),\n',
    '            "powertrain": self.coordinator.data.get("powertrain", "unknown"),\n            "auto_powertrain": self.coordinator.data.get("auto_powertrain", "unknown"),\n            "powertrain_source": self.coordinator.data.get("powertrain_source", "unknown"),\n',
)

# Internal implementation names are project-neutral; public entity IDs/keys stay unchanged.
for path in CC.glob("*.py"):
    text = read(path)
    if "Ec3" in text:
        write(path, text.replace("Ec3", "Sv"))

# Add the live trip-consumption entity next to current trip energy.
sensor_text = read(sensor_path)
sensor_text = sensor_text.replace(
    '            SvCurrentTripEnergySensor(coordinator, entry),\n',
    '            SvCurrentTripEnergySensor(coordinator, entry),\n            SvCurrentTripConsumptionSensor(coordinator, entry),\n',
)
marker = '''class SvCurrentChargePowerSensor(SvMetricSensor):
'''
if marker not in sensor_text:
    raise RuntimeError("Could not locate current charge power sensor")
consumption_class = '''class SvCurrentTripConsumptionSensor(SvMetricSensor):
    _attr_name = "Current trip consumption"
    _attr_translation_key = "current_trip_consumption"
    _attr_icon = "mdi:chart-line"
    _attr_native_unit_of_measurement = (
        f"{UnitOfEnergy.KILO_WATT_HOUR}/100 {UnitOfLength.KILOMETERS}"
    )
    _attr_state_class = SensorStateClass.MEASUREMENT

    def __init__(self, coordinator, entry: ConfigEntry) -> None:
        super().__init__(coordinator, entry, METRIC_CURRENT_TRIP_CONSUMPTION)

    @property
    def available(self) -> bool:
        return self.metrics.data.get("active_trip") is not None

    @property
    def native_value(self) -> float | None:
        return self.metrics.current_trip_consumption()


'''
sensor_text = sensor_text.replace(marker, consumption_class + marker, 1)
write(sensor_path, sensor_text)

# ---------------------------------------------------------------------------
# Hybrid-capable local/degraded trip capture + live kWh/100 km.
# ---------------------------------------------------------------------------
metrics_path = CC / "metrics.py"
replace(
    metrics_path,
    '    METRIC_CURRENT_TRIP_ENERGY,\n',
    '    METRIC_CURRENT_TRIP_CONSUMPTION,\n    METRIC_CURRENT_TRIP_ENERGY,\n',
)
replace(
    metrics_path,
    '            self.mapping.get("last_trip"),\n',
    '            self.mapping.get("last_trip"),\n            self.mapping.get("fuel"),\n            self.mapping.get("fuel_autonomy"),\n            self.mapping.get("fuel_consumption_total"),\n',
)
replace(
    metrics_path,
    '            "start_soc": self._number("battery"),\n            "capacity_kwh": capacity,\n',
    '            "start_soc": self._number("battery"),\n            "start_fuel": self._number("fuel"),\n            "start_fuel_range": self._number("fuel_autonomy"),\n            "start_fuel_total": self._number("fuel_consumption_total"),\n            "capacity_kwh": capacity,\n',
)
replace(
    metrics_path,
    '        active["end_soc"] = self._number("battery")\n',
    '        active["end_soc"] = self._number("battery")\n        active["end_fuel"] = self._number("fuel")\n        active["end_fuel_range"] = self._number("fuel_autonomy")\n        active["end_fuel_total"] = self._number("fuel_consumption_total")\n',
)
replace(
    metrics_path,
    '            consumption = (\n                round(energy_kwh / distance_km * 100, 2)\n                if energy_kwh is not None and distance_km > 0\n                else None\n            )\n            completed.append({\n',
    '''            consumption = (
                round(energy_kwh / distance_km * 100, 2)
                if energy_kwh is not None and distance_km > 0
                else None
            )
            fuel_level_start = self._as_float(candidate.get("start_fuel"))
            fuel_level_end = self._as_float(candidate.get("end_fuel"))
            fuel_range_start = self._as_float(candidate.get("start_fuel_range"))
            fuel_range_end = self._as_float(candidate.get("end_fuel_range"))
            fuel_total_start = self._as_float(candidate.get("start_fuel_total"))
            fuel_total_end = self._as_float(candidate.get("end_fuel_total"))
            fuel_consumption_l = (
                round(fuel_total_end - fuel_total_start, 3)
                if fuel_total_start is not None
                and fuel_total_end is not None
                and fuel_total_end >= fuel_total_start
                else None
            )
            fuel_consumption_l_100km = (
                round(fuel_consumption_l / distance_km * 100, 2)
                if fuel_consumption_l is not None and distance_km > 0
                else None
            )
            electric_used = bool(
                (energy_kwh is not None and energy_kwh > 0)
                or (
                    start_soc is not None
                    and end_soc is not None
                    and end_soc < start_soc
                )
            )
            fuel_used = bool(
                (fuel_consumption_l is not None and fuel_consumption_l > 0)
                or (
                    fuel_level_start is not None
                    and fuel_level_end is not None
                    and fuel_level_end < fuel_level_start
                )
            )
            trip_type = (
                "hybrid" if electric_used and fuel_used
                else "ice" if fuel_used
                else "ev" if electric_used
                else "unknown"
            )
            completed.append({
''',
)
replace(
    metrics_path,
    '                "soc_end": end_soc,\n                "capacity_kwh": round(capacity, 2) if capacity is not None else None,\n',
    '                "soc_end": end_soc,\n                "fuel_level_start": fuel_level_start,\n                "fuel_level_end": fuel_level_end,\n                "fuel_range_start_km": fuel_range_start,\n                "fuel_range_end_km": fuel_range_end,\n                "fuel_consumption_l": fuel_consumption_l,\n                "fuel_consumption_l_100km": fuel_consumption_l_100km,\n                "trip_type": trip_type,\n                "capacity_kwh": round(capacity, 2) if capacity is not None else None,\n',
)
replace(
    metrics_path,
    '    def current_charge_power(self) -> float | None:\n',
    '''    def current_trip_consumption(self) -> float | None:
        """Return live battery-side trip consumption only with usable distance."""
        energy = self.current_trip_energy()
        active = self.data.get("active_trip")
        if energy is None or not isinstance(active, dict):
            return None
        start_mileage = self._as_float(active.get("start_mileage"))
        mileage = self._number("mileage")
        if start_mileage is None or mileage is None:
            return None
        distance = mileage - start_mileage
        if distance <= 0.1:
            return None
        return round(energy / distance * 100, 2)

    def current_charge_power(self) -> float | None:
''',
)
replace(
    metrics_path,
    '    METRIC_CURRENT_TRIP_ENERGY: "current_trip_energy",\n',
    '    METRIC_CURRENT_TRIP_ENERGY: "current_trip_energy",\n    METRIC_CURRENT_TRIP_CONSUMPTION: "current_trip_consumption",\n',
)

# Refresh non-polling live trip metrics on relevant upstream changes without
# writing every sample to Store.
replace(
    metrics_path,
    '        elif entity_id == self.mapping.get("last_trip"):\n            self.hass.async_create_task(self.async_reconcile_pending_trip(new_state))\n',
    '''        elif entity_id == self.mapping.get("last_trip"):
            self.hass.async_create_task(self.async_reconcile_pending_trip(new_state))
        elif self.data.get("active_trip") and entity_id in {
            self.mapping.get("battery"),
            self.mapping.get("mileage"),
            self.mapping.get("fuel"),
            self.mapping.get("fuel_autonomy"),
            self.mapping.get("fuel_consumption_total"),
        }:
            for entity in self._entities:
                entity.async_write_ha_state()
''',
)

# ---------------------------------------------------------------------------
# Hybrid notification payload: localized base message + language-neutral fuel
# telemetry suffix. Never invent litres when only tank percent is available.
# ---------------------------------------------------------------------------
notifications_path = CC / "notifications.py"
replace(
    notifications_path,
    '        await self._async_notify(\n            title,\n            message,\n            "trip_completed",\n',
    '''        fuel_parts: list[str] = []
        fuel_l = self._as_float(trip.get("fuel_consumption_l"))
        fuel_average = self._as_float(trip.get("fuel_consumption_l_100km"))
        fuel_start = self._as_float(trip.get("fuel_level_start"))
        fuel_end = self._as_float(trip.get("fuel_level_end"))
        if fuel_l is not None:
            fuel_parts.append(f"⛽ {self._number(fuel_l, 2)} l")
        if fuel_average is not None:
            fuel_parts.append(f"{self._number(fuel_average, 2)} l/100 km")
        elif fuel_start is not None and fuel_end is not None and fuel_start != fuel_end:
            fuel_parts.append(
                f"⛽ {self._number(fuel_start, 0)} → {self._number(fuel_end, 0)} %"
            )
        if fuel_parts:
            message = f"{message} · {' · '.join(fuel_parts)}"
        await self._async_notify(
            title,
            message,
            "trip_completed",
''',
    count=1,
)

# ---------------------------------------------------------------------------
# Dual-energy hero: use real live consumption and show both energy paths when
# both are available.
# ---------------------------------------------------------------------------
replace(
    dual_path,
    '    const tripEnergy = metricEntity(this._hass, attributes, "current_trip_energy");\n',
    '    const tripConsumption = metricEntity(this._hass, attributes, "current_trip_consumption");\n',
)
replace(
    dual_path,
    '''    if (engine) {
      const fuelConsumption = numeric(state(mapped.fuel_consumption_instant));
      if (fuelConsumption !== null) return { icon: "mdi:car", label: text.driving, value: `${this._formatValue(mapped.fuel_consumption_instant, 1)} l/100 km` };
      const energy = numeric(state(tripEnergy));
      return { icon: "mdi:car", label: text.driving, value: energy === null ? "" : `${this._formatValue(tripEnergy, 1)} kWh` };
    }
''',
    '''    if (engine) {
      const values = [];
      const electricConsumption = numeric(state(tripConsumption));
      const fuelConsumption = numeric(state(mapped.fuel_consumption_instant));
      if (electricConsumption !== null) values.push(`${this._formatValue(tripConsumption, 1)} kWh/100 km`);
      if (fuelConsumption !== null) values.push(`${this._formatValue(mapped.fuel_consumption_instant, 1)} l/100 km`);
      return { icon: "mdi:car", label: text.driving, value: values.join(" · ") };
    }
''',
)

# The compact legacy/shared hero should also track the new live metric.
vehicle_overview_path = STATIC / "vehicle-overview-card.js"
replace(
    vehicle_overview_path,
    '  const tripEnergy = metricEntity(hass, attributes, "current_trip_energy");\n',
    '  const tripEnergy = metricEntity(hass, attributes, "current_trip_energy");\n  const tripConsumption = metricEntity(hass, attributes, "current_trip_consumption");\n',
)
replace(
    vehicle_overview_path,
    '    tripEnergy,\n',
    '    tripEnergy,\n    tripConsumption,\n',
)

# Locale-aware charging sub-state formatting instead of forcing a decimal comma.
strategy_path = STATIC / "sv_dashboard.js"
replace(
    strategy_path,
    f'import {{ languageFor, textFor }} from "./i18n.js?v={NEW_VERSION}";',
    f'import {{ languageFor, localeFor, textFor }} from "./i18n.js?v={NEW_VERSION}";',
)
old_power = "const text = !charging ? '-' : invalid(value) || !Number.isFinite(numericValue) ? '0 kW' : numericValue.toFixed(1).replace('.', ',') + ' ' + (stateEntity.attributes?.unit_of_measurement || 'kW');"
new_power = "const formatter = new Intl.NumberFormat(" + "${literalText(localeFor(hass))}" + ", { minimumFractionDigits: 1, maximumFractionDigits: 1 });\\n        const text = !charging ? '-' : invalid(value) || !Number.isFinite(numericValue) ? '0 kW' : formatter.format(numericValue) + ' ' + (stateEntity.attributes?.unit_of_measurement || 'kW');"
replace(strategy_path, old_power, new_power)

# ---------------------------------------------------------------------------
# Translation catalog additions for the new package metric and fallback option.
# ---------------------------------------------------------------------------
powertrain_labels = {
    "de": "Antriebsart (Fallback)", "en": "Powertrain (fallback)", "fr": "Motorisation (secours)",
    "it": "Motorizzazione (fallback)", "es": "Propulsión (respaldo)", "pt": "Motorização (fallback)",
    "nl": "Aandrijving (fallback)", "da": "Drivlinje (reserve)", "nb": "Drivlinje (reserve)",
    "sv": "Drivlina (reserv)", "fi": "Voimalinja (varmistus)", "pl": "Układ napędowy (awaryjnie)",
    "cs": "Pohon (záloha)", "sk": "Pohon (záloha)", "hu": "Hajtás (tartalék)",
    "ro": "Propulsie (rezervă)", "sl": "Pogon (rezerva)", "hr": "Pogon (rezerva)",
}
consumption_labels = {
    "de": "Aktueller Fahrverbrauch", "en": "Current trip consumption", "fr": "Consommation du trajet en cours",
    "it": "Consumo del viaggio in corso", "es": "Consumo del viaje actual", "pt": "Consumo da viagem atual",
    "nl": "Verbruik huidige rit", "da": "Forbrug på aktuel tur", "nb": "Forbruk på gjeldende tur",
    "sv": "Förbrukning aktuell resa", "fi": "Nykyisen matkan kulutus", "pl": "Zużycie bieżącej trasy",
    "cs": "Spotřeba aktuální jízdy", "sk": "Spotreba aktuálnej jazdy", "hu": "Aktuális út fogyasztása",
    "ro": "Consum cursă curentă", "sl": "Poraba trenutne vožnje", "hr": "Potrošnja trenutačne vožnje",
}
for language, label in powertrain_labels.items():
    path = CC / "translations" / f"{language}.json"
    data = json.loads(read(path))
    data["options"]["step"]["init"]["data"]["powertrain_override"] = label
    data["entity"]["sensor"]["current_trip_consumption"] = {
        "name": consumption_labels[language]
    }
    write(path, json.dumps(data, indent=2, ensure_ascii=False) + "\n")

# ---------------------------------------------------------------------------
# Frontend i18n parity tests now include the centralized Hybrid/Fuel catalogs.
# ---------------------------------------------------------------------------
frontend_i18n_test = TESTS / "frontend-i18n.test.mjs"
replace(
    frontend_i18n_test,
    f'import {{ ADVANCED_FRONTEND_TEXT as EASTERN_ADVANCED }} from "../custom_components/sv_dashboard/static/i18n-advanced-east.js";\n',
    f'import {{ ADVANCED_FRONTEND_TEXT as EASTERN_ADVANCED }} from "../custom_components/sv_dashboard/static/i18n-advanced-east.js";\nimport {{ HYBRID_CARD_TEXT }} from "../custom_components/sv_dashboard/static/i18n-hybrid-cards.js";\n',
)
replace(
    frontend_i18n_test,
    'const NAMESPACES = ["tripHistory", "chargeHistory", "vehicleOverview", "dashboard"];\n',
    'const NAMESPACES = ["tripHistory", "chargeHistory", "vehicleOverview", "dashboard", "dualEnergyOverview", "fuelHistory"];\n',
)
replace(
    frontend_i18n_test,
    '''      const provided = {
        ...(baseCatalogs[language]?.[namespace] || {}),
        ...(advancedCatalogs[language]?.[namespace] || {}),
      };
''',
    '''      const provided = HYBRID_CARD_TEXT[namespace]?.[language] || {
        ...(baseCatalogs[language]?.[namespace] || {}),
        ...(advancedCatalogs[language]?.[namespace] || {}),
      };
''',
)

hybrid_test = TESTS / "hybrid-fuel-ui.test.mjs"
hybrid_test_text = read(hybrid_test)
if 'i18n-hybrid-cards.js' not in hybrid_test_text:
    hybrid_test_text = hybrid_test_text.replace(
        'import test from "node:test";\n',
        'import test from "node:test";\nimport { HYBRID_CARD_TEXT } from "../custom_components/sv_dashboard/static/i18n-hybrid-cards.js";\n',
    )
hybrid_test_text = re.sub(
    r'test\("new card strings cover 18 languages",\(\)=>\{.*?\}\);',
    'test("new card strings cover 18 languages",()=>{for(const language of languages){assert.ok(HYBRID_CARD_TEXT.dualEnergyOverview[language]);assert.ok(HYBRID_CARD_TEXT.fuelHistory[language]);}});',
    hybrid_test_text,
    flags=re.S,
)
write(hybrid_test, hybrid_test_text)

# Dedicated hardening regression guard.
write(
    TESTS / "audit17-hardening.test.mjs",
    f'''import assert from "node:assert/strict";
import fs from "node:fs";
import test from "node:test";

const root = new URL("../custom_components/sv_dashboard/", import.meta.url);
const read = (path) => fs.readFileSync(new URL(path, root), "utf8");
const staticDir = new URL("../custom_components/sv_dashboard/static/", import.meta.url);

test("frontend runtime is self-contained and pinned", () => {{
  const files = fs.readdirSync(staticDir).filter((name) => name.endsWith(".js"));
  assert.ok(files.includes("vendor-lit.js"));
  for (const name of files) {{
    const source = fs.readFileSync(new URL(name, staticDir), "utf8");
    assert.doesNotMatch(source, /from\\s+["']https?:\\/\\//, `external runtime import in ${{name}}`);
    assert.doesNotMatch(source, /unpkg\\.com/);
  }}
  for (const name of ["trip-history-card.js", "charge-history-card.js", "dual-energy-overview-card.js", "fuel-history-card.js"]) {{
    assert.match(fs.readFileSync(new URL(name, staticDir), "utf8"), /vendor-lit\\.js\\?v={NEW_VERSION.replace('.', '\\.')}|vendor-lit\\.js\\?v=0\\.6\\.0-beta\\.5/);
  }}
}});

test("hybrid hardening contracts are present", () => {{
  const config = read("config_flow.py");
  const identity = read("entity_identity.py");
  const coordinator = read("coordinator.py");
  const metrics = read("metrics.py");
  const notifications = read("notifications.py");
  const dual = read("static/dual-energy-overview-card.js");
  assert.match(config, /CONF_POWERTRAIN_OVERRIDE/);
  assert.match(config, /auto_powertrain == POWERTRAIN_UNKNOWN/);
  assert.match(config, /CONF_VEHICLE_VIN/);
  assert.match(identity, /async_repair_vehicle_reference/);
  assert.match(coordinator, /fallback_override/);
  for (const key of ["start_fuel", "end_fuel", "fuel_consumption_l", "fuel_consumption_l_100km", "trip_type"]) assert.match(metrics, new RegExp(key));
  assert.match(metrics, /def current_trip_consumption/);
  assert.match(notifications, /fuel_consumption_l_100km/);
  assert.match(dual, /current_trip_consumption/);
  assert.match(dual, /kWh\\/100 km/);
}});

test("legacy Ec3 implementation class prefix is gone", () => {{
  for (const name of fs.readdirSync(new URL("../custom_components/sv_dashboard/", import.meta.url)).filter((name) => name.endsWith(".py"))) {{
    assert.doesNotMatch(read(name), /\\bEc3[A-Z]/, name);
  }}
}});
''',
)

# Validate workflow checks every new runtime module too.
validate_path = ROOT / ".github" / "workflows" / "validate.yaml"
replace(
    validate_path,
    '          node --check custom_components/sv_dashboard/static/i18n.js\n',
    '          node --check custom_components/sv_dashboard/static/i18n.js\n          node --check custom_components/sv_dashboard/static/i18n-hybrid-cards.js\n          node --check custom_components/sv_dashboard/static/vendor-lit.js\n',
)
replace(
    validate_path,
    '          node --check custom_components/sv_dashboard/static/charge-history-core.js\n',
    '          node --check custom_components/sv_dashboard/static/charge-history-core.js\n          node --check custom_components/sv_dashboard/static/dual-energy-overview-card.js\n          node --check custom_components/sv_dashboard/static/fuel-history-card.js\n',
)

# Maintainer rule: browser runtime must not depend on a CDN.
agents_path = ROOT / "AGENTS.md"
replace(
    agents_path,
    '- Prefer one package-owned frontend entry resource; internal ES module order/readiness is owned by that entry module.\n',
    '- Prefer one package-owned frontend entry resource; internal ES module order/readiness is owned by that entry module.\n- Browser runtime dependencies must be pinned and shipped locally with SV Dashboard; do not add CDN/runtime imports such as unpkg to package JavaScript. Preserve required third-party license notices.\n',
)

# Localisation docs record the new shared catalog boundary.
loc_path = ROOT / "docs" / "LOCALISATION.en.md"
loc_text = read(loc_path)
if "i18n-hybrid-cards.js" not in loc_text:
    loc_text += "\n## Hybrid and fuel cards\n\nHybrid/fuel-specific custom-card strings are part of the shared frontend catalog in `static/i18n-hybrid-cards.js` and are composed through `static/i18n.js`. They follow the same 18-language key and placeholder parity gates as the core frontend namespaces; card files must not carry private `TEXT` matrices.\n"
write(loc_path, loc_text)

# Changelog candidate note.
changelog_path = ROOT / "CHANGELOG.md"
changelog = read(changelog_path)
anchor = "## Unreleased — 0.6.0-beta.1 migration line\n"
if anchor not in changelog:
    raise RuntimeError("Changelog anchor not found")
entry = '''## 0.6.0-beta.5 candidate hardening\n\n- Removed the browser-time `unpkg.com` dependency; Lit is pinned and bundled locally with its license notice.\n- Added stable VIN-backed ConfigEntry identity/recovery and a powertrain fallback override that is available only when automatic detection remains unknown.\n- Added live `kWh/100 km` trip consumption, Hybrid/Fuel-aware local fallback history and fuel telemetry in trip notifications.\n- Centralized the new Hybrid/Fuel card strings into the shared 18-language frontend catalogs.\n- Hardened refuelling detection against single-sample fuel-level spikes and made charge-power formatting locale-aware.\n- Renamed remaining internal `Ec3...` implementation classes to neutral `Sv...` names without changing public entity identity.\n\n'''
changelog = changelog.replace(anchor, entry + anchor, 1)
write(changelog_path, changelog)

# Third-party notice generated from the pinned package installed by the workflow.
lit_license = Path("/tmp/sv-lit/node_modules/lit/LICENSE")
if not lit_license.exists():
    raise RuntimeError("Pinned Lit license not found; workflow dependency step did not run")
write(
    ROOT / "THIRD_PARTY_NOTICES.md",
    "# Third-party notices\n\n"
    "## Lit 3.3.1\n\n"
    "SV Dashboard bundles a browser-ready build of Lit 3.3.1 so its custom cards do not require a runtime CDN connection.\n\n"
    "Upstream package: `lit` (npm), project: https://lit.dev/\n\n"
    "License text:\n\n```text\n"
    + lit_license.read_text(encoding="utf-8").rstrip()
    + "\n```\n",
)

# Final source hygiene assertions performed before the normal regression suite.
for path in STATIC.glob("*.js"):
    source = read(path)
    if re.search(r'from\s+["\']https?://', source):
        raise RuntimeError(f"External runtime import remains in {path}")
    if "unpkg.com" in source:
        raise RuntimeError(f"unpkg remains in {path}")

for path in CC.glob("*.py"):
    if re.search(r"\bEc3[A-Z]", read(path)):
        raise RuntimeError(f"Legacy Ec3 class remains in {path}")

print(f"Audit #17 source migration prepared for {NEW_VERSION}")
