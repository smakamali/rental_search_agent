"""Named Canadian metro regions → municipality picker rows for multi-city search.

Bare city names (Vancouver, Toronto) are not aliases: only metro/greater/region
phrasing should expand. Each city has a UI label and an Apify search_location;
City + District of North Vancouver share one search string so selecting both
still produces a single scrape after dedupe.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Sequence

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class RegionCity:
    """One municipality shown in the confirm picker."""

    label: str
    search_location: str


@dataclass(frozen=True)
class SearchRegion:
    """A known metro area the agent can expand before ask_user."""

    name: str
    aliases: tuple[str, ...]
    cities: tuple[RegionCity, ...]


def _city(label: str, search_location: str | None = None) -> RegionCity:
    return RegionCity(label=label, search_location=search_location or label)


def _normalize_region_key(text: str) -> str:
    """Lowercase, collapse whitespace, drop commas and a trailing province code."""
    s = " ".join(str(text or "").lower().replace(",", " ").replace("-", " ").split())
    for suffix in (
        " british columbia",
        " ontario",
        " alberta",
        " manitoba",
        " nova scotia",
        " quebec",
        " canada",
        " bc",
        " on",
        " ab",
        " mb",
        " ns",
        " qc",
    ):
        if s.endswith(suffix):
            s = s[: -len(suffix)].strip()
    return s


METRO_VANCOUVER = SearchRegion(
    name="Metro Vancouver",
    aliases=(
        "metro vancouver",
        "greater vancouver",
        "greater vancouver area",
        "gva",
        "gvrd",
        "metro vancouver regional district",
    ),
    cities=(
        _city("Anmore", "Anmore, BC"),
        _city("Belcarra", "Belcarra, BC"),
        _city("Bowen Island", "Bowen Island, BC"),
        _city("Burnaby", "Burnaby, BC"),
        _city("Coquitlam", "Coquitlam, BC"),
        _city("Delta", "Delta, BC"),
        _city("Langley City", "Langley City, BC"),
        _city("Township of Langley", "Langley, BC"),
        _city("Lions Bay", "Lions Bay, BC"),
        _city("Maple Ridge", "Maple Ridge, BC"),
        _city("New Westminster", "New Westminster, BC"),
        # City and District share one Realtor.ca location string.
        _city("City of North Vancouver", "North Vancouver, BC"),
        _city("District of North Vancouver", "North Vancouver, BC"),
        _city("Pitt Meadows", "Pitt Meadows, BC"),
        _city("Port Coquitlam", "Port Coquitlam, BC"),
        _city("Port Moody", "Port Moody, BC"),
        _city("Richmond", "Richmond, BC"),
        _city("Surrey", "Surrey, BC"),
        _city("Vancouver", "Vancouver, BC"),
        _city("West Vancouver", "West Vancouver, BC"),
        _city("White Rock", "White Rock, BC"),
        # Treaty First Nation member; Realtor.ca place string remains Tsawwassen.
        _city("Tsawwassen First Nation", "Tsawwassen, BC"),
    ),
)

GREATER_VICTORIA = SearchRegion(
    name="Greater Victoria",
    aliases=(
        "greater victoria",
        "victoria metro",
        "capital regional district",
        "crd",
    ),
    cities=(
        _city("Victoria", "Victoria, BC"),
        _city("Saanich", "Saanich, BC"),
        _city("Esquimalt", "Esquimalt, BC"),
        _city("Oak Bay", "Oak Bay, BC"),
        _city("View Royal", "View Royal, BC"),
        _city("Colwood", "Colwood, BC"),
        _city("Langford", "Langford, BC"),
        _city("Sooke", "Sooke, BC"),
        _city("Central Saanich", "Central Saanich, BC"),
        _city("North Saanich", "North Saanich, BC"),
        _city("Sidney", "Sidney, BC"),
        _city("Highlands", "Highlands, BC"),
        _city("Metchosin", "Metchosin, BC"),
    ),
)

FRASER_VALLEY = SearchRegion(
    name="Fraser Valley",
    aliases=("fraser valley", "fvrd", "fraser valley regional district"),
    cities=(
        _city("Abbotsford", "Abbotsford, BC"),
        _city("Chilliwack", "Chilliwack, BC"),
        _city("Mission", "Mission, BC"),
        _city("Hope", "Hope, BC"),
        _city("Kent (Agassiz)", "Agassiz, BC"),
        _city("Harrison Hot Springs", "Harrison Hot Springs, BC"),
    ),
)

GREATER_TORONTO = SearchRegion(
    name="Greater Toronto Area",
    aliases=(
        "greater toronto area",
        "greater toronto",
        "gta",
        "metro toronto",
        "toronto metro",
    ),
    cities=(
        _city("Toronto", "Toronto, ON"),
        _city("Mississauga", "Mississauga, ON"),
        _city("Brampton", "Brampton, ON"),
        _city("Markham", "Markham, ON"),
        _city("Vaughan", "Vaughan, ON"),
        _city("Richmond Hill", "Richmond Hill, ON"),
        _city("Oakville", "Oakville, ON"),
        _city("Burlington", "Burlington, ON"),
        _city("Milton", "Milton, ON"),
        _city("Pickering", "Pickering, ON"),
        _city("Ajax", "Ajax, ON"),
        _city("Whitby", "Whitby, ON"),
        _city("Oshawa", "Oshawa, ON"),
        _city("Newmarket", "Newmarket, ON"),
        _city("Aurora", "Aurora, ON"),
        _city("Caledon", "Caledon, ON"),
        _city("Halton Hills", "Halton Hills, ON"),
        _city("Clarington", "Clarington, ON"),
        _city("King", "King, ON"),
        _city("Whitchurch-Stouffville", "Whitchurch-Stouffville, ON"),
        _city("East Gwillimbury", "East Gwillimbury, ON"),
        _city("Georgina", "Georgina, ON"),
        _city("Uxbridge", "Uxbridge, ON"),
        _city("Scugog", "Scugog, ON"),
        _city("Brock", "Brock, ON"),
    ),
)

HAMILTON = SearchRegion(
    name="Hamilton Metro",
    aliases=("hamilton metro", "greater hamilton", "hamilton cma"),
    # Hamilton CMA census subdivisions only (former Hamilton neighbourhoods
    # like Ancaster/Dundas/Stoney Creek are inside the amalgamated city).
    cities=(
        _city("Hamilton", "Hamilton, ON"),
        _city("Burlington", "Burlington, ON"),
        _city("Grimsby", "Grimsby, ON"),
    ),
)

WATERLOO_REGION = SearchRegion(
    name="Waterloo Region",
    aliases=(
        "waterloo region",
        "region of waterloo",
        "kitchener waterloo",
        "kitchener-waterloo",
        "kw",
        "kitchener waterloo cambridge",
    ),
    cities=(
        _city("Kitchener", "Kitchener, ON"),
        _city("Waterloo", "Waterloo, ON"),
        _city("Cambridge", "Cambridge, ON"),
        _city("Woolwich", "Woolwich, ON"),
        _city("Wilmot", "Wilmot, ON"),
        _city("Wellesley", "Wellesley, ON"),
        _city("North Dumfries", "North Dumfries, ON"),
    ),
)

OTTAWA = SearchRegion(
    name="Ottawa-Gatineau",
    aliases=(
        "ottawa gatineau",
        "ottawa-gatineau",
        "national capital region",
        "ncr",
        "ottawa metro",
        "greater ottawa",
    ),
    # Realtor.ca location matching is unreliable for some CMA names (e.g. Pontiac →
    # Schwartz; L'Ange-Gardien matches the Québec City twin; rural townships can
    # collapse to province-wide "Ontario"). Keep unambiguous, well-known places only.
    cities=(
        _city("Ottawa", "Ottawa, ON"),
        _city("Gatineau", "Gatineau, QC"),
        _city("Clarence-Rockland", "Clarence-Rockland, ON"),
        _city("Carleton Place", "Carleton Place, ON"),
        _city("Arnprior", "Arnprior, ON"),
        _city("Mississippi Mills", "Almonte, ON"),
        _city("North Grenville (Kemptville)", "Kemptville, ON"),
        _city("Chelsea", "Chelsea, QC"),
        _city("Cantley", "Cantley, QC"),
        _city("Val-des-Monts", "Val-des-Monts, QC"),
        _city("La Pêche", "La Peche, QC"),
    ),
)

GREATER_MONTREAL = SearchRegion(
    name="Greater Montreal",
    aliases=(
        "greater montreal",
        "montreal metro",
        "métro de montréal",
        "communaute metropolitaine de montreal",
        "communauté métropolitaine de montréal",
        "cmm",
    ),
    cities=(
        _city("Montreal", "Montreal, QC"),
        _city("Laval", "Laval, QC"),
        _city("Longueuil", "Longueuil, QC"),
        _city("Terrebonne", "Terrebonne, QC"),
        _city("Brossard", "Brossard, QC"),
        _city("Repentigny", "Repentigny, QC"),
        _city("Boucherville", "Boucherville, QC"),
        _city("Dollard-Des Ormeaux", "Dollard-Des Ormeaux, QC"),
        _city("Blainville", "Blainville, QC"),
        _city("Châteauguay", "Chateauguay, QC"),
        _city("Saint-Eustache", "Saint-Eustache, QC"),
        _city("Mascouche", "Mascouche, QC"),
        _city("Mirabel", "Mirabel, QC"),
        _city("Pointe-Claire", "Pointe-Claire, QC"),
        _city("Westmount", "Westmount, QC"),
        _city("Kirkland", "Kirkland, QC"),
        _city("Beaconsfield", "Beaconsfield, QC"),
        _city("Dorval", "Dorval, QC"),
        _city("Mount Royal", "Mount Royal, QC"),
        _city("La Prairie", "La Prairie, QC"),
    ),
)

CALGARY = SearchRegion(
    name="Calgary Region",
    aliases=("calgary region", "calgary metro", "calgary cma", "greater calgary"),
    # Calgary CMA census subdivisions (Okotoks is a separate CA, not in the CMA).
    cities=(
        _city("Calgary", "Calgary, AB"),
        _city("Airdrie", "Airdrie, AB"),
        _city("Cochrane", "Cochrane, AB"),
        _city("Chestermere", "Chestermere, AB"),
        _city("Crossfield", "Crossfield, AB"),
    ),
)

EDMONTON = SearchRegion(
    name="Edmonton Metro",
    aliases=("edmonton metro", "edmonton region", "edmonton cma", "greater edmonton"),
    # Major Edmonton CMA municipalities (Sherwood Park is inside Strathcona County).
    cities=(
        _city("Edmonton", "Edmonton, AB"),
        _city("St. Albert", "St. Albert, AB"),
        _city("Strathcona County (Sherwood Park)", "Sherwood Park, AB"),
        _city("Spruce Grove", "Spruce Grove, AB"),
        _city("Leduc", "Leduc, AB"),
        _city("Fort Saskatchewan", "Fort Saskatchewan, AB"),
        _city("Stony Plain", "Stony Plain, AB"),
        _city("Beaumont", "Beaumont, AB"),
        _city("Devon", "Devon, AB"),
        _city("Morinville", "Morinville, AB"),
    ),
)

WINNIPEG = SearchRegion(
    name="Winnipeg Metro",
    aliases=("winnipeg metro", "winnipeg cma", "capital region manitoba"),
    # Winnipeg Metropolitan Region / Capital Planning Region municipalities (legislated 18).
    cities=(
        _city("Winnipeg", "Winnipeg, MB"),
        _city("Selkirk", "Selkirk, MB"),
        _city("Niverville", "Niverville, MB"),
        _city("Stonewall", "Stonewall, MB"),
        _city("Dunnottar", "Dunnottar, MB"),
        _city("Cartier", "Cartier, MB"),
        _city("East St. Paul", "East St. Paul, MB"),
        _city("Headingley", "Headingley, MB"),
        _city("Macdonald", "Macdonald, MB"),
        _city("Ritchot", "Ritchot, MB"),
        _city("Rockwood", "Rockwood, MB"),
        _city("Rosser", "Rosser, MB"),
        _city("Springfield", "Springfield, MB"),
        _city("St. Andrews", "St. Andrews, MB"),
        _city("St. Clements", "St. Clements, MB"),
        _city("St. François Xavier", "St. Francois Xavier, MB"),
        _city("Taché", "Tache, MB"),
        _city("West St. Paul", "West St. Paul, MB"),
    ),
)

HALIFAX = SearchRegion(
    name="Halifax Metro",
    aliases=(
        "halifax metro",
        "hrm",
        "halifax regional municipality",
        "greater halifax",
    ),
    # Halifax CMA: HRM is one municipality; communities like Dartmouth are inside it.
    cities=(
        _city("Halifax Regional Municipality", "Halifax, NS"),
        _city("East Hants", "East Hants, NS"),
    ),
)

SEARCH_REGIONS: tuple[SearchRegion, ...] = (
    METRO_VANCOUVER,
    GREATER_VICTORIA,
    FRASER_VALLEY,
    GREATER_TORONTO,
    HAMILTON,
    WATERLOO_REGION,
    OTTAWA,
    GREATER_MONTREAL,
    CALGARY,
    EDMONTON,
    WINNIPEG,
    HALIFAX,
)


def _alias_index() -> dict[str, SearchRegion]:
    index: dict[str, SearchRegion] = {}
    for region in SEARCH_REGIONS:
        index[_normalize_region_key(region.name)] = region
        for alias in region.aliases:
            index[_normalize_region_key(alias)] = region
    return index


_REGION_BY_KEY = _alias_index()


def known_region_names() -> list[str]:
    """Canonical region names, for error messages and docs."""
    return [region.name for region in SEARCH_REGIONS]


def lookup_region(region: str) -> SearchRegion | None:
    """Return the catalog region for a name/alias, or None if unknown."""
    key = _normalize_region_key(region)
    if not key:
        return None
    return _REGION_BY_KEY.get(key)


def unique_search_locations(cities: Sequence[RegionCity]) -> list[str]:
    """Dedup search_location strings case-insensitively, preserving first-seen order."""
    seen: set[str] = set()
    out: list[str] = []
    for city in cities:
        loc = (city.search_location or "").strip()
        if not loc:
            continue
        key = loc.lower()
        if key in seen:
            continue
        seen.add(key)
        out.append(loc)
    return out


def _city_key_to_search_location() -> dict[str, str]:
    """Normalized picker label or search_location → canonical Apify search string."""
    index: dict[str, str] = {}
    for region in SEARCH_REGIONS:
        for city in region.cities:
            search = (city.search_location or "").strip()
            if not search:
                continue
            index[_normalize_region_key(city.label)] = search
            index[_normalize_region_key(search)] = search
    return index


_CITY_KEY_TO_SEARCH = _city_key_to_search_location()


def canonicalize_search_locations(locations: Sequence[str]) -> list[str]:
    """Map catalog labels to search_location and dedupe (e.g. both North Vancouvers → one scrape)."""
    raw_in = list(locations)
    seen: set[str] = set()
    out: list[str] = []
    n_blank = 0
    n_dup = 0
    for loc in raw_in:
        s = (loc or "").strip()
        if not s:
            n_blank += 1
            continue
        mapped = _CITY_KEY_TO_SEARCH.get(_normalize_region_key(s), s)
        key = mapped.lower()
        if key in seen:
            n_dup += 1
            continue
        seen.add(key)
        out.append(mapped)
    logger.debug(
        "canonicalize_search_locations: n_in=%d n_out=%d n_blank=%d n_dup_dropped=%d in=%r out=%r",
        len(raw_in),
        len(out),
        n_blank,
        n_dup,
        raw_in,
        out,
    )
    return out


def resolve_search_location_input(text: str) -> str | list[str]:
    """Resolve a sidebar location: known metro → all search_locations, else a city string.

    Bare city names are not expanded. The typed string is stored as-is; call this only
    when building rental_search filters. Returns "" when the input is blank.
    """
    stripped = (text or "").strip()
    if not stripped:
        logger.debug("resolve_search_location_input: blank")
        return ""
    match = lookup_region(stripped)
    if match is None:
        logger.debug("resolve_search_location_input: bare_or_unknown input=%r", stripped)
        return stripped
    locations = unique_search_locations(match.cities)
    if not locations:
        logger.warning(
            "resolve_search_location_input: metro=%s has no search locations; using input=%r",
            match.name,
            stripped,
        )
        return stripped
    if len(locations) == 1:
        logger.debug(
            "resolve_search_location_input: metro=%s n_locations=1 location=%r",
            match.name,
            locations[0],
        )
        return locations[0]
    logger.debug(
        "resolve_search_location_input: metro=%s n_locations=%d",
        match.name,
        len(locations),
    )
    return locations


def expand_search_region(region: str) -> dict[str, Any]:
    """Expand a metro name into picker rows, or return { error } if unknown."""
    text = (region or "").strip()
    if not text:
        logger.warning("expand_search_region: blank region")
        return {
            "error": (
                "region is required and must be a non-empty string. "
                f"Known regions: {', '.join(known_region_names())}."
            )
        }
    match = lookup_region(text)
    if match is None:
        logger.warning("expand_search_region: unknown region=%r", text)
        return {
            "error": (
                f"Unknown region {text!r}. Known regions: {', '.join(known_region_names())}. "
                "Ask the user which cities to include."
            )
        }
    n_cities = len(match.cities)
    logger.debug("expand_search_region: region=%s n_cities=%d", match.name, n_cities)
    return {
        "region": match.name,
        "cities": [
            {"label": city.label, "search_location": city.search_location} for city in match.cities
        ],
    }
