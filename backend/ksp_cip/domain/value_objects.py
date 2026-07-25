"""Value objects with real invariants.

The CrimeNo grammar is defined by the source ER document:

``[1 digit case-category][4 digit district][4 digit police-station][4 digit year][5 digit serial]``

for a total of 18 digits, e.g. ``104430006202600001``. A separate running
serial is maintained per (police station, case category, year).
"""

from __future__ import annotations

import math
import re
import unicodedata
from dataclasses import dataclass
from datetime import date

from .errors import ValidationError

CRIME_NO_RE = re.compile(r"^(?P<category>\d)(?P<district>\d{4})(?P<station>\d{4})(?P<year>\d{4})(?P<serial>\d{5})$")
CASE_NO_RE = re.compile(r"^(?P<year>\d{4})(?P<serial>\d{5})$")

# Karnataka bounding box with tolerance, used by the DQ coordinate check.
KARNATAKA_BBOX = (11.5, 18.6, 74.0, 78.7)  # (lat_min, lat_max, lon_min, lon_max)
EARTH_RADIUS_METRES = 6_371_008.8


@dataclass(frozen=True, slots=True)
class CrimeNo:
    """Parsed, validated crime number."""

    raw: str
    category_code: int
    district_id: int
    station_id: int
    year: int
    serial: int

    @classmethod
    def parse(cls, raw: str) -> "CrimeNo":
        candidate = (raw or "").strip()
        match = CRIME_NO_RE.match(candidate)
        if not match:
            raise ValidationError(
                "CrimeNo must be 18 digits: 1 category + 4 district + 4 station + 4 year + 5 serial",
                crime_no=raw,
            )
        year = int(match["year"])
        if not 1900 <= year <= 2999:
            raise ValidationError("CrimeNo year component out of range", crime_no=raw, year=year)
        return cls(
            raw=candidate,
            category_code=int(match["category"]),
            district_id=int(match["district"]),
            station_id=int(match["station"]),
            year=year,
            serial=int(match["serial"]),
        )

    @classmethod
    def try_parse(cls, raw: str) -> "CrimeNo | None":
        try:
            return cls.parse(raw)
        except ValidationError:
            return None

    @classmethod
    def build(cls, *, category_code: int, district_id: int, station_id: int, year: int, serial: int) -> "CrimeNo":
        if not 0 <= category_code <= 9:
            raise ValidationError("Case category code must be a single digit", category_code=category_code)
        if not 0 <= district_id <= 9999:
            raise ValidationError("District id must fit 4 digits", district_id=district_id)
        if not 0 <= station_id <= 9999:
            raise ValidationError("Police station id must fit 4 digits", station_id=station_id)
        if not 0 <= serial <= 99_999:
            raise ValidationError("Serial must fit 5 digits", serial=serial)
        raw = f"{category_code:d}{district_id:04d}{station_id:04d}{year:04d}{serial:05d}"
        return cls.parse(raw)

    @property
    def case_no(self) -> str:
        """Last 9 digits of the CrimeNo, per the ER document."""
        return f"{self.year:04d}{self.serial:05d}"

    def __str__(self) -> str:  # pragma: no cover - trivial
        return self.raw


def parse_case_no(raw: str) -> tuple[int, int]:
    match = CASE_NO_RE.match((raw or "").strip())
    if not match:
        raise ValidationError("CaseNo must be YYYY + 5 digit serial", case_no=raw)
    return int(match["year"]), int(match["serial"])


@dataclass(frozen=True, slots=True)
class GeoPoint:
    latitude: float
    longitude: float

    def __post_init__(self) -> None:
        if not -90.0 <= self.latitude <= 90.0:
            raise ValidationError("Latitude out of range", latitude=self.latitude)
        if not -180.0 <= self.longitude <= 180.0:
            raise ValidationError("Longitude out of range", longitude=self.longitude)

    @property
    def within_karnataka(self) -> bool:
        lat_min, lat_max, lon_min, lon_max = KARNATAKA_BBOX
        return lat_min <= self.latitude <= lat_max and lon_min <= self.longitude <= lon_max

    def distance_metres(self, other: "GeoPoint") -> float:
        """Haversine distance. PostGIS stand-in at hackathon data volume."""
        lat1, lon1, lat2, lon2 = map(math.radians, (self.latitude, self.longitude, other.latitude, other.longitude))
        dlat = lat2 - lat1
        dlon = lon2 - lon1
        h = math.sin(dlat / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin(dlon / 2) ** 2
        return 2 * EARTH_RADIUS_METRES * math.asin(math.sqrt(h))

    def grid_cell(self, cell_metres: int) -> tuple[int, int]:
        """Deterministic equal-area-ish grid cell id for hotspot binning."""
        metres_per_deg_lat = 111_320.0
        metres_per_deg_lon = 111_320.0 * math.cos(math.radians(self.latitude)) or 1.0
        row = int(math.floor(self.latitude * metres_per_deg_lat / cell_metres))
        col = int(math.floor(self.longitude * metres_per_deg_lon / cell_metres))
        return row, col


@dataclass(frozen=True, slots=True)
class DateRange:
    start: date
    end: date

    def __post_init__(self) -> None:
        if self.start > self.end:
            raise ValidationError("Date range start must not be after end", start=str(self.start), end=str(self.end))

    def contains(self, value: date) -> bool:
        return self.start <= value <= self.end

    @property
    def days(self) -> int:
        return (self.end - self.start).days + 1


HONORIFICS = {"sri", "shri", "smt", "smt.", "sri.", "kum", "kum.", "dr", "dr.", "mr", "mr.", "mrs", "mrs.", "ms", "ms."}
ALIAS_MARKERS = re.compile(r"\s*(?:@|alias|a/k/a|aka)\s*", flags=re.IGNORECASE)


def normalize_person_name(raw: str) -> str:
    """Normalization step 1 of entity resolution (architecture §8.3).

    Unicode NFC, casefold, honorific stripping, alias-marker splitting,
    punctuation removal, whitespace collapse. Kannada text is preserved
    verbatim (the transliteration key is produced separately).
    """
    if not raw:
        return ""
    text = unicodedata.normalize("NFC", raw).strip()
    text = ALIAS_MARKERS.split(text)[0]
    text = re.sub(r"[.,'\"()\[\]/\\-]+", " ", text)
    tokens = [t for t in text.casefold().split() if t and t not in HONORIFICS]
    return " ".join(tokens)


def split_name_initials(raw: str) -> tuple[str, list[str]]:
    """Separate leading initials from the substantive name.

    Karnataka police records routinely carry village and patronymic initials
    ("K. M. Ramesh Gowda"), and the *same person* is frequently written with
    them, without them, or with only one. Comparing the full strings therefore
    penalises the commonest benign variation there is, so the resolver compares
    the core name and treats the initials as a separate, weaker signal.

    Returns ``(core_name, initials)`` — both normalized and casefolded.
    """
    normalized = normalize_person_name(raw)
    if not normalized:
        return "", []
    tokens = normalized.split()
    initials = [token for token in tokens if len(token) == 1]
    core = [token for token in tokens if len(token) > 1]
    # A name that is *only* initials keeps them, otherwise nothing is left.
    if not core:
        return " ".join(initials), []
    return " ".join(core), initials
