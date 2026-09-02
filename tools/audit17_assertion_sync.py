from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TESTS = ROOT / "tests"


# Frontend i18n: beta.5 is the runtime cache key. Hybrid/Fuel namespaces now
# live completely in the canonical core catalog, so the regional overlay source
# coverage test must only police namespaces owned by those overlays.
path = TESTS / "frontend-i18n.test.mjs"
text = path.read_text(encoding="utf-8")
text = text.replace("beta\\.4", "beta\\.5")
text = text.replace("beta\\\\.4", "beta\\\\.5")
text = text.replace(
    'test("15 extra languages explicitly provide every ordinary EN key before runtime fallback", () => {\n'
    '  for (const language of EXTRA_LANGUAGES) {\n'
    '    for (const namespace of NAMESPACES) {\n',
    'test("15 extra languages explicitly provide every overlay-owned EN key before runtime fallback", () => {\n'
    '  const coreOwnedNamespaces = new Set(["dualEnergyOverview", "fuelHistory"]);\n'
    '  for (const language of EXTRA_LANGUAGES) {\n'
    '    for (const namespace of NAMESPACES) {\n'
    '      if (coreOwnedNamespaces.has(namespace)) continue;\n',
)
path.write_text(text, encoding="utf-8")


# All Python implementation classes are now package-neutral Sv* classes.
path = TESTS / "vehicle-capabilities.test.mjs"
text = path.read_text(encoding="utf-8")
for old, new in {
    "Ec3ServerTripHistorySensor": "SvServerTripHistorySensor",
    "Ec3ServerGpsHistorySensor": "SvServerGpsHistorySensor",
    "Ec3VehicleInfoSensor": "SvVehicleInfoSensor",
    "Ec3LastTripResultSensor": "SvLastTripResultSensor",
    "Ec3TrailingConsumptionSensor": "SvTrailingConsumptionSensor",
    "Ec3CurrentTripEnergySensor": "SvCurrentTripEnergySensor",
    "Ec3CurrentTripConsumptionSensor": "SvCurrentTripConsumptionSensor",
    "Ec3ServerChargeHistorySensor": "SvServerChargeHistorySensor",
    "Ec3DistanceSinceChargeSensor": "SvDistanceSinceChargeSensor",
    "Ec3LastChargeResultSensor": "SvLastChargeResultSensor",
    "Ec3CurrentChargePowerSensor": "SvCurrentChargePowerSensor",
}.items():
    text = text.replace(old, new)
# Protect the newly added live consumption metric as part of the electric set.
anchor = "  assert.match(sensorPlatform, /SvCurrentTripEnergySensor/);\n"
if anchor in text and "SvCurrentTripConsumptionSensor" not in text:
    text = text.replace(
        anchor,
        anchor + "  assert.match(sensorPlatform, /SvCurrentTripConsumptionSensor/);\n",
        1,
    )
path.write_text(text, encoding="utf-8")


# Compact/shared hero: driving is now a consumption surface. For Hybrid cars it
# deliberately renders every available live energy path and must update when
# either source changes.
path = TESTS / "vehicle-overview-card.test.mjs"
text = path.read_text(encoding="utf-8")
anchor = '  assert.match(source, /metricEntity\\(hass, attributes, "current_trip_energy"\\)/);\n'
if anchor in text and 'current_trip_consumption' not in text[text.find(anchor):text.find(anchor)+300]:
    text = text.replace(
        anchor,
        anchor
        + '  assert.match(source, /metricEntity\\(hass, attributes, "current_trip_consumption"\\)/);\n',
        1,
    )
old_trigger = (
    "  assert.match(source, /triggers_update: \\[primaryLevel, battery, batteryResidual, fuel, "
    "charging, engine, chargePower, tripEnergy\\]/);\n"
)
new_contract = (
    "  assert.match(source, /const fuelConsumptionEntity = mapped\\.fuel_consumption_instant/);\n"
    "  assert.match(source, /const electric = states\\[\\$\\{literal\\(tripConsumption\\)\\}\\]/);\n"
    "  assert.match(source, /const fuelNow = states\\[\\$\\{literal\\(fuelConsumptionEntity\\)\\}\\]/);\n"
    "  assert.match(source, /kWh\\/100 km/);\n"
    "  assert.match(source, /l\\/100 km/);\n"
    "  assert.match(source, /values\\.join\\(' · '\\)/);\n"
    "  assert.match(source, /triggers_update: \\[primaryLevel, battery, batteryResidual, fuel, fuelConsumptionEntity, charging, engine, chargePower, tripEnergy, tripConsumption\\]/);\n"
)
if old_trigger not in text:
    raise RuntimeError("Could not locate stale vehicle overview trigger assertion")
text = text.replace(old_trigger, new_contract, 1)
path.write_text(text, encoding="utf-8")

print("Audit #17 assertion synchronization complete")
