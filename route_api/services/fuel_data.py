"""Fuel-station data: load the provided CSV once and enrich it with local
city coordinates (data/city_coordinates.csv, generated from GeoNames).

The CSV remains the source of truth for prices. Stations are US-only; stations
whose city cannot be matched to GeoNames coordinates are excluded (coordinates
are never invented). Loading happens once per process and is reused.
"""

from __future__ import annotations

import logging
import threading
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pandas as pd

from django.conf import settings

from route_api.exceptions import FuelDataError
from route_api.services.geo_utils import normalize_city, normalize_state, us_state_abbreviations

logger = logging.getLogger("route_api")

REQUIRED_FUEL_COLUMNS = [
    "OPIS Truckstop ID",
    "Truckstop Name",
    "Address",
    "City",
    "State",
    "Rack ID",
    "Retail Price",
]
REQUIRED_COORD_COLUMNS = ["city", "state", "latitude", "longitude"]


@dataclass(frozen=True)
class FuelStation:
    station_id: str
    name: str
    address: str
    city: str
    state: str
    price: float
    latitude: float
    longitude: float


@dataclass(frozen=True)
class FuelDataset:
    stations: tuple[FuelStation, ...]
    stats: dict

    @property
    def lats(self) -> np.ndarray:
        return np.array([s.latitude for s in self.stations])

    @property
    def lons(self) -> np.ndarray:
        return np.array([s.longitude for s in self.stations])


def load_fuel_data(
    fuel_csv_path: Path | str | None = None,
    city_coords_path: Path | str | None = None,
) -> FuelDataset:
    """Read and clean the fuel CSV, join GeoNames coordinates, report stats."""
    fuel_path = Path(fuel_csv_path or settings.FUEL_CSV_PATH)
    coords_path = Path(city_coords_path or settings.CITY_COORDS_PATH)

    fuel_df = _read_csv(fuel_path, "fuel-price")
    _require_columns(fuel_df, REQUIRED_FUEL_COLUMNS, fuel_path)
    total_rows = len(fuel_df)

    # Clean: whitespace, missing values, numeric prices.
    for column in ["Truckstop Name", "Address", "City", "State"]:
        fuel_df[column] = fuel_df[column].astype("string").str.strip()
    fuel_df["Retail Price"] = pd.to_numeric(fuel_df["Retail Price"], errors="coerce")
    fuel_df = fuel_df.dropna(subset=["Truckstop Name", "City", "State", "Retail Price"])
    fuel_df = fuel_df[fuel_df["Retail Price"] > 0]
    fuel_df["State"] = fuel_df["State"].str.upper()
    invalid_rows = total_rows - len(fuel_df)

    # US-only filter: the CSV also contains Canadian and other non-US records.
    us_df = fuel_df[fuel_df["State"].isin(us_state_abbreviations())].copy()
    non_us_rows = len(fuel_df) - len(us_df)

    us_df["city_key"] = us_df["City"].map(normalize_city)
    us_df["state_key"] = us_df["State"].map(normalize_state)
    pairs = set(zip(us_df["city_key"], us_df["state_key"]))

    coordinates = _load_coordinates(coords_path)
    matched_pairs = {pair for pair in pairs if pair in coordinates}
    unmatched_pairs = pairs - matched_pairs

    pair_index = pd.MultiIndex.from_arrays([us_df["city_key"], us_df["state_key"]])
    matched_mask = pair_index.isin(list(matched_pairs))
    excluded_rows = int((~matched_mask).sum())
    ready = us_df[matched_mask]

    stations: list[FuelStation] = []
    for row in ready.to_dict(orient="records"):
        lat, lon = coordinates[(row["city_key"], row["state_key"])]
        stations.append(
            FuelStation(
                station_id=str(row["OPIS Truckstop ID"]),
                name=row["Truckstop Name"],
                address=row["Address"] or "",
                city=row["City"],
                state=row["State"],
                price=float(row["Retail Price"]),
                latitude=lat,
                longitude=lon,
            )
        )
    stations = _deduplicate(stations)

    stats = {
        "total_rows": total_rows,
        "invalid_rows": int(invalid_rows),
        "us_rows": int(len(us_df)),
        "non_us_rows": int(non_us_rows),
        "unique_city_state_pairs": len(pairs),
        "matched_pairs": len(matched_pairs),
        "unmatched_pairs": len(unmatched_pairs),
        "excluded_unmatched_rows": excluded_rows,
        "station_count": len(stations),
    }
    logger.info("Fuel data loaded: %s", stats)
    if unmatched_pairs:
        logger.info("Unmatched city/state pairs excluded: %s", sorted(unmatched_pairs)[:20])
    if not stations:
        raise FuelDataError("No fuel stations could be loaded from the fuel-price CSV.")
    return FuelDataset(stations=tuple(stations), stats=stats)


def get_fuel_data() -> FuelDataset:
    """Process-wide singleton: the CSV is read once, then reused."""
    global _dataset
    if _dataset is None:
        with _lock:
            if _dataset is None:
                _dataset = load_fuel_data()
    return _dataset


def reset_cache() -> None:
    """Used by tests."""
    global _dataset
    with _lock:
        _dataset = None


_dataset: FuelDataset | None = None
_lock = threading.Lock()


def _deduplicate(stations: list[FuelStation]) -> list[FuelStation]:
    """The CSV lists some stations more than once; keep the cheapest listing."""
    best: dict[tuple[str, str, str], FuelStation] = {}
    for station in stations:
        key = (normalize_city(station.name), normalize_city(station.city), normalize_state(station.state))
        current = best.get(key)
        if current is None or station.price < current.price:
            best[key] = station
    return list(best.values())


def _read_csv(path: Path, label: str) -> pd.DataFrame:
    try:
        return pd.read_csv(path, encoding="utf-8-sig", dtype=str, keep_default_na=True)
    except FileNotFoundError as exc:
        raise FuelDataError(f"The {label} CSV file was not found at '{path}'.") from exc
    except (pd.errors.EmptyDataError, pd.errors.ParserError, UnicodeDecodeError) as exc:
        raise FuelDataError(f"The {label} CSV file could not be parsed.") from exc


def _require_columns(frame: pd.DataFrame, required: list[str], path: Path) -> None:
    missing = [column for column in required if column not in frame.columns]
    if missing:
        raise FuelDataError(f"The {_label_for(path)} CSV is missing required columns: {', '.join(missing)}.")


def _load_coordinates(path: Path) -> dict[tuple[str, str], tuple[float, float]]:
    frame = _read_csv(path, "city-coordinates")
    _require_columns(frame, REQUIRED_COORD_COLUMNS, path)
    frame["latitude"] = pd.to_numeric(frame["latitude"], errors="coerce")
    frame["longitude"] = pd.to_numeric(frame["longitude"], errors="coerce")
    frame = frame.dropna(subset=["latitude", "longitude"])
    frame["city_key"] = frame["city"].astype("string").str.strip().map(normalize_city)
    frame["state_key"] = frame["state"].astype("string").str.strip().map(normalize_state)
    return {
        (row.city_key, row.state_key): (float(row.latitude), float(row.longitude))
        for row in frame.itertuples(index=False)
    }


def _label_for(path: Path) -> str:
    return "fuel-price" if "fuel" in path.name.lower() else "city-coordinates"
