"""Synthetic socio-economic indicator generator for Karnataka districts.

Produces one row per district per census year containing plausible district-level
socio-economic indicators derived from:
- Census of India 2011 (Karnataka state tables)
- NSSO 68th round (2011-12) district estimates
- Karnataka Economic Survey data ranges
- Planning Commission BPL estimates

IMPORTANT: All values are synthetic approximations for demonstration purposes.
No individual is identifiable. Every generated row is labelled data_quality='synthetic'
and data_source references the public datasets that informed the approximation.
This is an ext_* table — explicitly a platform extension, not source FIR data.
"""

from __future__ import annotations

import hashlib
from datetime import datetime, timezone
from typing import Any

# District-level socio-economic approximations for Karnataka's 31 districts.
# Columns: (district_name, population, literacy_rate, male_literacy, female_literacy,
#           urbanization_percent, sex_ratio, population_density, unemployment_rate,
#           poverty_headcount, migration_inflow_rate, sc_st_percent, per_capita_income_index)
#
# Source references used for calibration (all synthetic approximations):
#   - Census 2011: KA-08 series for literacy/sex-ratio/urbanization/population
#   - NSSO 68th Round: district unemployment proxies
#   - Planning Commission 2012: BPL headcount ratios
#   - Karnataka Economic Survey 2022-23: per-capita income relativities
#
# Districts ordered to match KARNATAKA_DISTRICTS in masters.py (alphabetical by first char).
_DISTRICT_INDICATORS: dict[str, tuple] = {
    # district_name -> (population, literacy, male_lit, female_lit, urban%, sex_ratio,
    #                   pop_density, unemployment%, poverty%, migration%, sc_st%, pci_index)
    "Bagalkote":        (1890826,  67.2, 75.1, 58.8,  31.5, 948, 191, 4.8, 29.4, 5.2, 25.1, 0.73),
    "Ballari":          (2532383,  62.1, 72.9, 50.8,  45.2, 957, 256, 5.9, 35.7, 9.3, 31.8, 0.82),
    "Belagavi":         (4779661,  71.0, 79.2, 62.4,  30.1, 955, 291, 4.2, 26.8, 6.1, 18.4, 0.88),
    "Bengaluru City":   (9621551,  87.7, 91.2, 83.7,  98.5, 908, 4381,2.8,  8.5, 38.7,  9.6, 2.41),
    "Bengaluru Rural":  (990923,   71.4, 79.6, 62.8,  17.8, 951, 253, 3.7, 16.9, 14.2, 20.1, 1.12),
    "Bidar":            (1703300,  64.6, 72.8, 55.7,  26.2, 949, 219, 5.2, 32.1, 4.8, 24.6, 0.70),
    "Chamarajanagara":  (1020791,  59.0, 70.1, 47.6,  17.9, 991, 137, 5.8, 38.2, 3.9, 36.4, 0.65),
    "Chikkaballapura":  (1254432,  70.8, 79.7, 61.5,  18.6, 966, 285, 4.5, 23.1, 11.8, 22.7, 0.79),
    "Chikkamagaluru":   (1137961,  77.0, 83.6, 70.1,  28.9, 1005,140, 3.9, 18.7, 7.6, 17.3, 0.95),
    "Chitradurga":      (1660378,  70.5, 78.5, 62.1,  29.4, 971, 142, 4.6, 27.8, 5.3, 26.9, 0.78),
    "Dakshina Kannada": (2089649,  88.6, 91.5, 85.6,  56.3, 1018,419, 3.1, 10.2, 9.8, 12.1, 1.31),
    "Davanagere":       (1946905,  72.5, 80.3, 64.2,  37.8, 970, 247, 4.4, 24.3, 7.2, 22.8, 0.91),
    "Dharwad":          (1847023,  79.2, 85.3, 72.7,  60.4, 951, 403, 3.8, 17.6, 10.4, 14.3, 1.08),
    "Gadag":            (1065235,  73.4, 80.6, 65.8,  35.6, 959, 179, 4.7, 25.9, 4.6, 20.2, 0.76),
    "Hassan":           (1776421,  74.9, 82.3, 67.2,  24.5, 1005,199, 4.1, 20.4, 6.8, 18.5, 0.87),
    "Haveri":           (1598506,  72.1, 79.8, 64.1,  27.8, 963, 213, 4.3, 24.8, 5.1, 21.6, 0.80),
    "Kalaburagi":       (2564892,  61.3, 71.6, 50.5,  36.9, 954, 193, 5.5, 36.2, 5.7, 28.3, 0.71),
    "Kodagu":           (554762,   82.6, 87.4, 77.5,  25.1, 1008, 75, 3.6, 13.1, 8.4, 19.7, 1.05),
    "Kolar":            (1540231,  72.3, 80.4, 63.8,  24.6, 975, 367, 4.9, 22.7, 12.6, 25.9, 0.84),
    "Koppal":           (1391292,  60.8, 70.9, 50.2,  23.8, 960, 155, 5.6, 33.4, 4.4, 29.8, 0.68),
    "Mandya":           (1808680,  71.1, 79.8, 62.1,  19.4, 985, 321, 4.0, 19.6, 7.3, 22.1, 0.89),
    "Mysuru":           (3001127,  77.2, 83.9, 70.2,  44.7, 978, 349, 3.7, 16.3, 12.1, 19.3, 1.09),
    "Raichur":          (1928812,  54.9, 65.3, 43.9,  30.5, 963, 169, 5.9, 39.7, 5.0, 33.6, 0.66),
    "Ramanagara":       (1082739,  70.1, 79.2, 60.7,  20.3, 963, 330, 4.2, 20.8, 11.4, 24.3, 0.88),
    "Shivamogga":       (1752753,  78.3, 84.7, 71.6,  42.1, 989, 176, 3.8, 16.8, 8.9, 15.2, 0.97),
    "Tumakuru":         (2678980,  75.5, 82.6, 68.1,  26.7, 966, 265, 4.1, 21.3, 9.7, 21.7, 0.93),
    "Udupi":            (1177361,  86.2, 89.1, 83.4,  55.6, 1094,309, 3.4, 11.4, 10.2, 11.8, 1.19),
    "Uttara Kannada":   (1437169,  79.8, 84.9, 74.4,  28.3, 999,  68, 3.7, 16.2, 7.6, 14.9, 0.96),
    "Vijayanagara":     (1321148,  61.5, 72.1, 50.4,  28.1, 961, 148, 5.1, 31.6, 5.8, 27.9, 0.72),
    "Vijayapura":       (2175102,  66.8, 75.4, 57.6,  34.6, 949, 207, 4.9, 30.1, 5.5, 22.5, 0.74),
    "Yadgir":           (1174685,  50.1, 60.2, 39.5,  18.3, 967, 173, 6.2, 44.3, 4.1, 31.2, 0.62),
}

DATA_SOURCE = "Census 2011 + NSSO 68th Round (synthetic approximation)"
CENSUS_YEAR = 2011


def generate_socioeconomic_indicators(
    districts: list[dict[str, Any]],
    *,
    census_year: int = CENSUS_YEAR,
) -> list[dict[str, Any]]:
    """Generate one ext_socioeconomic_indicator row per district.

    ``districts`` is the list of dicts produced by ``generate_masters()`` with
    keys ``DistrictID`` and ``DistrictName``.  Districts not found in
    ``_DISTRICT_INDICATORS`` get state-average fallback values so the table is
    always complete.
    """
    now = datetime.now(timezone.utc).isoformat()
    # Karnataka state averages — fallback for any district not in the table
    state_avg = (
        3610000, 75.6, 82.5, 68.1, 38.6, 968, 319, 4.3, 20.8, 8.9, 20.7, 1.0
    )

    rows: list[dict[str, Any]] = []
    for d in districts:
        district_id = int(d["DistrictID"])
        district_name = str(d["DistrictName"])

        vals = _DISTRICT_INDICATORS.get(district_name, state_avg)
        (population, lit, male_lit, female_lit, urban_pct, sex_ratio,
         pop_density, unemployment, poverty, migration, sc_st, pci_index) = vals

        # Stable unique ID: hash of district_id + year (no random component so
        # re-seeding is idempotent on a live database).
        indicator_id = hashlib.sha256(
            f"socio:{district_id}:{census_year}".encode()
        ).hexdigest()[:24]

        rows.append({
            "indicator_id": indicator_id,
            "district_id": district_id,
            "district_name": district_name,
            "census_year": census_year,
            "population": population,
            "literacy_rate": round(lit, 2),
            "male_literacy": round(male_lit, 2),
            "female_literacy": round(female_lit, 2),
            "urbanization_percent": round(urban_pct, 2),
            "sex_ratio": sex_ratio,
            "population_density": pop_density,
            "unemployment_rate": round(unemployment, 2),
            "poverty_headcount": round(poverty, 2),
            "migration_inflow_rate": round(migration, 2),
            "sc_st_percent": round(sc_st, 2),
            "per_capita_income_index": round(pci_index, 3),
            "data_source": DATA_SOURCE,
            "data_quality": "synthetic",
            "is_extension": 1,
            "created_at": now,
        })
    return rows
