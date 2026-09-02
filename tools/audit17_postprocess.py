from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
STATIC = ROOT / "custom_components" / "sv_dashboard" / "static"
TESTS = ROOT / "tests"


# Fold the generated Hybrid/Fuel namespaces into the canonical frontend catalog.
# The temporary split catalog must not survive the build.
hybrid_path = STATIC / "i18n-hybrid-cards.js"
hybrid = hybrid_path.read_text(encoding="utf-8")
prefix = (
    "/* Shared 18-language catalogs for Hybrid/Fuel-specific SV cards. */\n"
    "export const HYBRID_CARD_TEXT = {\n"
)
if not hybrid.startswith(prefix) or not hybrid.endswith("};\n"):
    raise RuntimeError("Unexpected generated Hybrid/Fuel catalog shape")
namespaces = hybrid[len(prefix) : -3]

core_path = STATIC / "i18n-core.js"
core = core_path.read_text(encoding="utf-8")
marker = "  },\n};\n\nconst SUPPORTED_LANGUAGES"
if marker not in core:
    raise RuntimeError("Could not locate canonical frontend catalog boundary")
core = core.replace(
    marker,
    "  },\n" + namespaces + "};\n\nconst SUPPORTED_LANGUAGES",
    1,
)
core_path.write_text(core, encoding="utf-8")
hybrid_path.unlink()

i18n_path = STATIC / "i18n.js"
i18n = i18n_path.read_text(encoding="utf-8")
i18n = i18n.replace(
    'import { HYBRID_CARD_TEXT } from "./i18n-hybrid-cards.js?v=0.6.0-beta.5";\n',
    "",
)
i18n = i18n.replace(
    "for (const [namespace, catalog] of Object.entries(HYBRID_CARD_TEXT)) {\n"
    "  FRONTEND_TEXT[namespace] = catalog;\n"
    "}\n\n",
    "",
)
if "i18n-hybrid-cards" in i18n or "HYBRID_CARD_TEXT" in i18n:
    raise RuntimeError("Split Hybrid/Fuel catalog still referenced by i18n.js")
i18n_path.write_text(i18n, encoding="utf-8")


# Compact/shared hero: use live consumption, not accumulated trip energy, and
# show both electric and fuel consumption when a Hybrid exposes both.
overview_path = STATIC / "vehicle-overview-card.js"
overview = overview_path.read_text(encoding="utf-8")
overview = overview.replace(
    "  const fuelAutonomy = mapped.fuel_autonomy;\n",
    "  const fuelAutonomy = mapped.fuel_autonomy;\n"
    "  const fuelConsumptionEntity = mapped.fuel_consumption_instant;\n",
    1,
)
overview = overview.replace(
    "    fuelAutonomy,\n    temperature,\n",
    "    fuelAutonomy,\n    fuelConsumptionEntity,\n    temperature,\n",
    1,
)
old_driving = """            if (isDriving) {
              const energy = states[${literal(tripEnergy)}];
              if (energy && !['unknown','unavailable','none',''].includes(energy.state) && Number.isFinite(Number(energy.state))) {
                return ${literal(strings.driving)} + ' · ' + Number(energy.state).toLocaleString(undefined, { minimumFractionDigits: 1, maximumFractionDigits: 1 }) + ' kWh';
              }
              return ${literal(strings.driving)};
            }
"""
new_driving = """            if (isDriving) {
              const values = [];
              const electric = states[${literal(tripConsumption)}];
              const fuelNow = states[${literal(fuelConsumptionEntity)}];
              if (electric && !['unknown','unavailable','none',''].includes(String(electric.state).toLowerCase()) && Number.isFinite(Number(electric.state))) {
                values.push(Number(electric.state).toLocaleString(undefined, { minimumFractionDigits: 1, maximumFractionDigits: 1 }) + ' kWh/100 km');
              }
              if (fuelNow && !['unknown','unavailable','none',''].includes(String(fuelNow.state).toLowerCase()) && Number.isFinite(Number(fuelNow.state))) {
                values.push(Number(fuelNow.state).toLocaleString(undefined, { minimumFractionDigits: 1, maximumFractionDigits: 1 }) + ' l/100 km');
              }
              return ${literal(strings.driving)} + (values.length ? ' · ' + values.join(' · ') : '');
            }
"""
if old_driving not in overview:
    raise RuntimeError("Could not locate compact hero driving energy block")
overview = overview.replace(old_driving, new_driving, 1)
overview = overview.replace(
    "triggers_update: [primaryLevel, battery, batteryResidual, fuel, charging, "
    "engine, chargePower, tripEnergy].filter(Boolean),",
    "triggers_update: [primaryLevel, battery, batteryResidual, fuel, "
    "fuelConsumptionEntity, charging, engine, chargePower, tripEnergy, "
    "tripConsumption].filter(Boolean),",
    1,
)
overview_path.write_text(overview, encoding="utf-8")


# The canonical i18n parity test can now read the new namespaces directly from
# i18n-core.js; there is no special second-source branch.
frontend_test = TESTS / "frontend-i18n.test.mjs"
text = frontend_test.read_text(encoding="utf-8")
text = text.replace(
    'import { HYBRID_CARD_TEXT } from "../custom_components/sv_dashboard/static/'
    'i18n-hybrid-cards.js";\n',
    "",
)
text = text.replace(
    "      const provided = HYBRID_CARD_TEXT[namespace]?.[language] || {\n"
    "        ...(baseCatalogs[language]?.[namespace] || {}),\n"
    "        ...(advancedCatalogs[language]?.[namespace] || {}),\n"
    "      };\n",
    "      const provided = {\n"
    "        ...(baseCatalogs[language]?.[namespace] || {}),\n"
    "        ...(advancedCatalogs[language]?.[namespace] || {}),\n"
    "      };\n",
)
frontend_test.write_text(text, encoding="utf-8")

hybrid_test = TESTS / "hybrid-fuel-ui.test.mjs"
text = hybrid_test.read_text(encoding="utf-8")
text = text.replace(
    'import { HYBRID_CARD_TEXT } from "../custom_components/sv_dashboard/static/'
    'i18n-hybrid-cards.js";\n',
    'import { FRONTEND_TEXT as CORE_FRONTEND_TEXT } from '
    '"../custom_components/sv_dashboard/static/i18n-core.js";\n',
)
text = text.replace(
    "HYBRID_CARD_TEXT.dualEnergyOverview",
    "CORE_FRONTEND_TEXT.dualEnergyOverview",
)
text = text.replace("HYBRID_CARD_TEXT.fuelHistory", "CORE_FRONTEND_TEXT.fuelHistory")
hybrid_test.write_text(text, encoding="utf-8")


# Synchronize regex literals that escaped the normal beta.4 -> beta.5 source
# replacement, plus the one stale implementation-class expectation.
for path in TESTS.glob("*.mjs"):
    text = path.read_text(encoding="utf-8")
    text = text.replace(r"0\.6\.0-beta\.4", r"0\.6\.0-beta\.5")
    text = text.replace("Ec3ServerTripHistorySensor", "SvServerTripHistorySensor")
    path.write_text(text, encoding="utf-8")


# Validation/docs describe and check the final canonical layout.
validate_path = ROOT / ".github" / "workflows" / "validate.yaml"
validate = validate_path.read_text(encoding="utf-8")
validate = validate.replace(
    "          node --check custom_components/sv_dashboard/static/"
    "i18n-hybrid-cards.js\n",
    "",
)
validate_path.write_text(validate, encoding="utf-8")

loc_path = ROOT / "docs" / "LOCALISATION.en.md"
loc = loc_path.read_text(encoding="utf-8")
loc = loc.replace(
    "Hybrid/fuel-specific custom-card strings are part of the shared frontend "
    "catalog in `static/i18n-hybrid-cards.js` and are composed through "
    "`static/i18n.js`.",
    "Hybrid/fuel-specific custom-card strings are namespaces of the canonical "
    "frontend catalog in `static/i18n-core.js` and are consumed through "
    "`static/i18n.js`.",
)
loc_path.write_text(loc, encoding="utf-8")


audit_path = TESTS / "audit17-hardening.test.mjs"
audit = audit_path.read_text(encoding="utf-8")
audit += (
    '\n\ntest("Hybrid/Fuel strings have one canonical frontend source", () => {\n'
    "  const files = fs.readdirSync(staticDir);\n"
    '  assert.ok(!files.includes("i18n-hybrid-cards.js"));\n'
    '  const core = read("static/i18n-core.js");\n'
    "  assert.match(core, /dualEnergyOverview:/);\n"
    "  assert.match(core, /fuelHistory:/);\n"
    '  assert.doesNotMatch(read("static/dual-energy-overview-card.js"), '
    "/const TEXT =/);\n"
    '  assert.doesNotMatch(read("static/fuel-history-card.js"), '
    "/const TEXT =/);\n"
    "});\n"
)
audit_path.write_text(audit, encoding="utf-8")


# Final source contract: the removed catalog cannot be referenced anywhere
# relevant to the package or its validation suite.
for path in [*STATIC.glob("*.js"), *TESTS.glob("*.mjs"), validate_path, loc_path]:
    if "i18n-hybrid-cards.js" in path.read_text(encoding="utf-8"):
        raise RuntimeError(f"Split catalog reference remains in {path}")

print("Audit #17 postprocessing complete")
