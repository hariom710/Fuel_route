"""Build data/city_coordinates.csv from the GeoNames US dataset.

The fuel-prices CSV has no coordinates, so (City, State) pairs are mapped to
approximate city-level coordinates using GeoNames (https://www.geonames.org/).
Run once during development; the API only reads the generated CSV.

Usage:  python scripts/build_city_coordinates.py [path/to/fuel-prices.csv]
"""

from __future__ import annotations

import csv
import io
import sys
import tempfile
import urllib.request
import zipfile
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from route_api.services.geo_utils import (  # noqa: E402
    normalize_city,
    normalize_state,
    us_state_abbreviations,
)

GEONAMES_DUMP_URL = "https://download.geonames.org/export/dump/US.zip"
OUTPUT_PATH = PROJECT_ROOT / "data" / "city_coordinates.csv"


def fetch_geonames_rows() -> list[list[str]]:
    """Download the GeoNames US dump (cached in the temp dir) and return its rows."""
    cache_dir = Path(tempfile.gettempdir()) / "geonames"
    dump_path = cache_dir / "US.txt"
    if not dump_path.exists():
        cache_dir.mkdir(parents=True, exist_ok=True)
        with urllib.request.urlopen(GEONAMES_DUMP_URL, timeout=120) as response:
            archive = zipfile.ZipFile(io.BytesIO(response.read()))
        dump_path.write_bytes(archive.read("US.txt"))
    with dump_path.open(encoding="utf-8") as handle:
        return [line.rstrip("\n").split("\t") for line in handle]


def best_places(rows: list[list[str]]) -> dict[tuple[str, str], tuple[str, str, str, str]]:
    """(normalized city, state) -> (display name, state, latitude, longitude).

    Keeps the most populous GeoNames place per key; indexes both the original
    name and the ASCII name so 'Cañon City' and 'Canon City' both match.
    """
    best: dict[tuple[str, str], tuple[str, str, str, str]] = {}
    population: dict[tuple[str, str], int] = {}
    for row in rows:
        if row[6] != "P":  # populated places only
            continue
        state = row[10].strip().upper()
        if state not in us_state_abbreviations():
            continue
        try:
            pop = int(row[14] or 0)
        except ValueError:
            pop = 0
        for name in {row[1].strip(), row[2].strip()}:
            if not name:
                continue
            key = (normalize_city(name), state)
            if pop > population.get(key, -1):
                best[key] = (name, state, row[4], row[5])
                population[key] = pop
    return best


def fuel_csv_pairs(fuel_csv: Path) -> set[tuple[str, str]]:
    with fuel_csv.open(encoding="utf-8-sig", newline="") as handle:
        return {
            (normalize_city(row["City"]), normalize_state(row["State"]))
            for row in csv.DictReader(handle)
            if normalize_state(row["State"]) in us_state_abbreviations()
        }


def main() -> None:
    fuel_csv = Path(sys.argv[1]) if len(sys.argv) > 1 else PROJECT_ROOT / "fuel-prices-for-be-assessment.csv"
    pairs = fuel_csv_pairs(fuel_csv)
    places = best_places(fetch_geonames_rows())

    matched = sorted(key for key in pairs if key in places)
    unmatched = sorted(key for key in pairs if key not in places)

    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    with OUTPUT_PATH.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(["city", "state", "latitude", "longitude"])
        for key in matched:
            writer.writerow(places[key])

    print(f"US unique city+state pairs in CSV : {len(pairs)}")
    print(f"Matched to GeoNames               : {len(matched)}")
    print(f"Unmatched (excluded at runtime)   : {len(unmatched)}")
    for city, state in unmatched:
        print(f"  - {city}, {state}")
    print(f"Wrote {OUTPUT_PATH.relative_to(PROJECT_ROOT)} ({len(matched)} rows)")


if __name__ == "__main__":
    main()
