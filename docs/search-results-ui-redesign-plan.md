Prompt 1 — Refactor the search-results foundation
You are working in the existing repository:

smakamali/rental_search_agent

This is STEP 1 of a five-step search-results UI redesign.

IMPORTANT:
The detailed Analyze UI was recently refactored into dedicated modules
(`analysis_view.py`, `streamlit_analysis.py`, `display_format.py`).
Follow that architectural direction.

DO NOT redesign Grid, Table, or Map visually in this step.
This step is specifically about creating a clean shared foundation for the
search-results UI before the three views are redesigned separately.

Before editing, inspect the CURRENT repository state. Do not rely on stale
assumptions from previous commits.

Primary files to inspect:

- src/rental_search_agent/streamlit_app.py
- src/rental_search_agent/display_format.py
- src/rental_search_agent/streamlit_analysis.py
- src/rental_search_agent/analysis_view.py
- src/rental_search_agent/match_scoring.py
- src/rental_search_agent/models.py
- src/rental_search_agent/proximity.py
- src/rental_search_agent/client.py
- tests/unit/test_streamlit_helpers.py
- tests/unit/test_display_format.py

==================================================
CURRENT ARCHITECTURE YOU SHOULD PRESERVE
==================================================

The Streamlit app currently stores the rendered results in:

st.session_state["display_list"]

and related state in:

- display_source
- last_sort_by
- results_view
- map_label_mode

Do NOT create a second search-result source of truth.

Listings are dict representations of the existing Listing model.

Do NOT introduce a new Pydantic result model just for UI rendering unless there
is a compelling concrete reason. The current Listing/result dictionaries are
already the canonical data.

The agent/tool layer assigns an explicit `rank` field to listings.
That rank is authoritative for conversational references such as:

"listing 1"
"#3"
"the third listing"

DO NOT recompute or overwrite rank from current display position.

The current Streamlit UI may locally reorder results while preserving rank.
Preserve that behavior unless you discover a clear bug.

==================================================
GOAL
==================================================

Move search-result-specific presentation logic out of the already-large
`streamlit_app.py` into a dedicated module, analogous to the existing
`streamlit_analysis.py`.

Preferred new module:

src/rental_search_agent/streamlit_results.py

The exact filename may differ only if an existing project convention strongly
suggests something else.

`streamlit_app.py` should remain responsible for:

- application/session orchestration
- chat
- obtaining `display_list`
- applying existing result filtering/sort safeguards
- invoking the active results view

The new results module should own:

- result display formatting specific to Grid/Table/Map
- Grid renderer
- Table renderer
- Map renderer and map helpers
- compact Match visualization
- proximity display shaping
- results header/view selector if appropriate

==================================================
1. REUSE EXISTING SHARED FORMATTERS
==================================================

DO NOT recreate logic that already exists in:

rental_search_agent.display_format

Reuse where applicable:

- format_currency
- format_duration
- format_sqft
- format_count
- format_percentage
- score_to_pct
- get_score_color
- split_listing_address
- safe_http_url
- proximity_criterion_name

The existing result-specific helpers can remain when they add behavior not
covered by display_format, for example:

- bedroom den notation
- days on market
- freshness/open-house/price-drop tags
- compact map currency

But avoid duplicated generic currency/score/URL/address logic.

Preserve the current security behavior around scraped URLs and text.
Never weaken URL validation or HTML escaping while refactoring.

==================================================
2. MATCH SCORE SEMANTICS
==================================================

The current search result score should use:

listing["match_score"]

as the primary Match value.

This is the multi-metric overall score produced by the current match-scoring
pipeline.

For backward compatibility with older/current session data, preserve fallback to:

listing["semantic_score"]

when match_score is absent.

Do NOT relabel Semantic score as Overall Match.

Reuse:

get_score_color(...)

for all search-results score visualization.

Do not define another green/amber/orange threshold map.

==================================================
3. PROXIMITY VIEW MODEL
==================================================

Replace `_format_proximity_display()`'s single prose string with a structured
display representation.

The underlying listing.proximity structure uses keys conceptually like:

"<location>|<mode>"

and values like:

{
    "distance_km": ...,
    "duration_min": ...
}

or:

None

when the calculation could not be performed.

Create a small helper representation suitable for ALL result views.

For example, conceptually:

@dataclass
class ProximityDisplayItem:
    location: str
    mode: str
    status: Literal["available", "unavailable"]
    duration_min: float | None
    distance_km: float | None
    display_text: str
    unavailable_text: str | None

You do not have to use a dataclass if a lightweight dict/helper is cleaner.

The important requirement is that Grid and Table should NOT parse the raw
proximity dict independently.

Examples of desired derived display text:

available:
"23 min to 800 Burrard St"

available nearest transit:
"2 min walk to transit"

unavailable:
"Drive to Metrotown unavailable"

If several are unavailable, the UI must also be able to say:

"1 proximity criterion unavailable"
"2 proximity criteria unavailable"

Use `proximity_criterion_name()` where useful.

DO NOT describe an unavailable proximity calculation as:

- unmet
- listing information missing
- open property question

It is specifically a proximity calculation availability state.

Delete the user-facing phrase:

"(some unknown)"

from this presentation layer.

==================================================
4. COMPACT MATCH INDICATOR FOUNDATION
==================================================

The Analyze UI already has reusable SVG gauge/color semantics.

For search results, create a compact Match indicator suitable for cards/table.

Do NOT use the full large Analyze gauge.

Implement something conceptually like:

render_compact_match_score(listing_or_score, ...)

or an HTML-producing helper if easier to embed.

It should:

- use `match_score`, fallback `semantic_score`
- show the numeric percentage
- use `get_score_color`
- use a small ring/pill treatment
- expose accessible text
- gracefully show "—" when unavailable

Keep this reusable across Grid and Table.

Map Match markers can reuse the score/color helper later.

==================================================
5. ADDRESS DISPLAY FOUNDATION
==================================================

Use `split_listing_address()` from display_format so Grid/Table can consistently
show:

Street address
City / Province / Postal Code

Do not write separate address parsing logic in each view.

==================================================
6. MAP PRICE FORMATTER
==================================================

Preserve compact map formatting.

Current behavior such as:

$1.25M
$970,000

is acceptable.

If you move `_format_map_price_label`, preserve backward-compatible test behavior.

Do not change map formatting in this step unless needed for reuse.

==================================================
7. RESULT VIEW STATE MIGRATION
==================================================

The current session state defaults to:

results_view = "cards"

The final design will use:

"grid"
"table"
"map"

Prepare the state cleanly now.

Use "grid" as the new canonical name.

Handle old/stale session state safely, for example:

"cards" -> "grid"

Do not allow Streamlit's segmented control to receive an invalid persisted value.

Do NOT visually redesign the navigation yet beyond what is necessary to make
the state migration safe.

`map_label_mode` should remain separate.

==================================================
8. MAP LABEL STATE
==================================================

The eventual map modes will be:

price
match
rank

Prepare validation of map_label_mode so unknown/stale state safely falls back to
"price".

Do not implement the Match marker visual redesign yet unless a tiny helper
change is necessary.

==================================================
9. DO NOT ADD A SORT DROPDOWN
==================================================

The application does NOT currently have a direct user-facing sort widget.

Sorting is driven through the agent/filter pipeline and recorded in:

st.session_state["last_sort_by"]

Do NOT implement the mockup's "Sort by" dropdown in this step.

Do not create a second sort system.

Optionally create a helper that converts existing last_sort_by values into
human-readable passive text for later use:

semantic_score / match_score -> Match
proximity -> Proximity
price -> Price
listing_age_hours -> Newest
etc.

But this must be display-only.

==================================================
10. ANALYZE ACTION
==================================================

Preserve the existing Analyze behavior:

st.session_state["analyze_listing_id"]
st.session_state["analyze_listing"]
st.rerun()

Do not create separate Analyze logic per view.

Extract a shared helper if useful, but behavior must remain identical.

==================================================
11. TEST COMPATIBILITY
==================================================

Existing tests import helper functions from:

rental_search_agent.streamlit_app

If helpers are moved to streamlit_results.py, either:

A. update the tests cleanly

or preferably

B. import/re-export the moved helpers from streamlit_app so existing imports
continue to work during this incremental refactor.

Do not break existing regression coverage unnecessarily.

Add focused tests for:

- proximity parsing into structured display rows
- named unavailable proximity criteria
- unavailable count singular/plural
- overall match_score preferred over semantic_score
- semantic fallback
- compact score percentage
- score color reuse
- cards -> grid state migration if implemented as helper
- invalid map_label_mode fallback

==================================================
12. SCOPE CONTROL
==================================================

DO NOT:

- redesign Grid
- redesign Table
- redesign Map
- change scoring
- change match weights
- change agent ranking
- change filter behavior
- change proximity API calls
- introduce React
- introduce a new charting library
- introduce another mapping library
- rewrite unrelated Streamlit code

==================================================
VERIFICATION
==================================================

Run:

pytest -q

Also run a basic Python syntax/import check if useful.

Do not invent lint/type-check commands that are not configured in the repo.

==================================================
ACCEPTANCE CRITERIA
==================================================

This step is complete when:

- search-result UI helpers are cleanly separated from app orchestration
- existing Listing/result dicts remain the canonical data
- rank semantics remain untouched
- shared display_format helpers are reused
- Match uses match_score with semantic fallback
- score color is centralized
- proximity is represented structurally
- "(some unknown)" can no longer be produced by the new results display helper
- the results-view state is ready for grid/table/map
- existing functionality still works
- tests pass

Before editing, briefly state what you found and the files you plan to change.

Then implement the changes directly.

At the end report:

- files changed
- helpers moved/added
- any compatibility re-exports
- rank behavior preserved
- proximity representation
- tests run/results

Do not stop after giving a plan.
Prompt 2 — Grid redesign

Attach the Grid mockup.

You are working in:

smakamali/rental_search_agent

This is STEP 2 of the search-results redesign.

STEP 1 has already created/refined the shared search-result presentation
foundation, preferably in:

src/rental_search_agent/streamlit_results.py

Use the CURRENT code, not assumptions from the original implementation.

I am attaching the Grid mockup.

Use it as VISUAL DIRECTION, not as a literal specification.
The requirements below override details in the mockup.

Do not hard-code any listing values shown in the mockup.

Your task is to redesign ONLY the Grid results view.

Do not materially redesign Table or Map in this step.

==================================================
KEY REPOSITORY RULES
==================================================

Use existing listing dicts from `display_list`.

Preserve the listing's explicit `rank` field.

DO NOT derive rank from `enumerate()` except as the existing fallback when a
listing truly has no rank.

Match means:

listing["match_score"]

with backward-compatible fallback to semantic_score.

Use the shared:

get_score_color()

and the compact Match indicator created in Step 1.

Use split_listing_address() for address hierarchy.

Use the structured proximity display helper from Step 1.

Preserve existing listing links and Analyze behavior.

==================================================
1. RESULTS HEADER
==================================================

Replace the current redundant combination of:

Results view
[Cards | Table]

and a separate Search results expander heading

with a cleaner results header.

Target concept:

Search results
5 properties

                         [ Grid | Table | Map ]

Use:

st.segmented_control

with canonical values:

grid
table
map

Grid should be active.

Do NOT add the mockup's interactive Sort dropdown.

The repository's sorting is agent-driven through `last_sort_by`.

If `last_sort_by` is available, optionally show a small passive indicator such as:

Ordered by proximity
Ordered by match
Ordered by price
Ordered by newest

Only show text that accurately corresponds to the existing last_sort_by value.

Do not imply that Rank == Match.

==================================================
2. REMOVE THE EXTRA SEARCH RESULTS EXPANDER IF SAFE
==================================================

The current cards are inside:

st.expander("Search results", expanded=True)

The new design should preferably render the active result view directly beneath
the Search results header.

Avoid unnecessary nested chrome.

If removing the expander would create a meaningful regression, explain why and
retain it, but the preferred target is a clean direct results area.

==================================================
3. GRID STRUCTURE
==================================================

Desktop target:

3 cards per row.

Each card should follow this hierarchy:

[ photo ]

#rank

PRICE                         compact MATCH indicator

Street address
City / Province / Postal

3 bd · 2 ba · 1,852 sq ft

--------------------------------

23 min to 800 Burrard St
2 min walk to transit
1 proximity criterion unavailable

[ Analyze ]

Use a two-column layout inside the price/match row where practical.

The key priorities are:

Photo
Price + Match
Address
Property basics
Proximity
Analyze

==================================================
4. IMAGE PRESENTATION
==================================================

Improve the current clickable raw `<img width=220>` rendering.

Use a consistent aspect ratio across cards.

Preferred:

aspect-ratio: 16 / 10;
width: 100%;
object-fit: cover;

or a similar ratio that matches the mockup.

Requirements:

- no stretching
- uniform visual height
- listing link remains clickable
- border radius consistent with the app
- valid safe HTTP(S) URLs only
- graceful fallback when photo is unavailable

Use scoped CSS owned by the results module.

Do not target unstable Streamlit-generated `.st-emotion-cache-*` classes.

==================================================
5. RANK
==================================================

Keep rank visible but secondary.

Use the listing's explicit:

listing["rank"]

Possible treatment:

#1

as:
- a subtle image-corner badge if practical, OR
- a small label immediately below the image

Do not renumber based on the current Grid position.

==================================================
6. PRICE + MATCH
==================================================

Make Price and Match the strongest comparison signals after the photo.

Price should use shared full currency formatting:

$1,730,000

Match should use the compact Match renderer from Step 1.

Target concept:

$1,730,000                     ◯ 74% Match

The compact indicator should:

- be much smaller than the Analyze-page gauge
- display the numeric score
- use the shared green -> amber -> orange score colors
- not rely on color alone
- show a neutral unavailable state if no score exists

Do not create a new color mapping.

==================================================
7. ADDRESS
==================================================

Use split_listing_address().

Target:

4137 Dominion Street
Burnaby, British Columbia V5G 1C5

Street:
- primary
- clickable when a valid listing URL exists

Locality:
- secondary/muted

Do not make a three-line bright-blue full address dominate the card.

Preserve safe URL handling.

==================================================
8. PROPERTY BASICS
==================================================

Current style:

3 bed · 2 bath · 1852 sqft

Target:

3 bd · 2 ba · 1,852 sq ft

Use:
- existing bedroom helper so den notation such as "2 + 1" survives
- format_count for bathrooms where appropriate
- format_sqft for size

If a value is missing, avoid producing awkward strings such as:

3 bd · — ba · — sq ft

Prefer omitting missing segments when doing so is cleaner.

Do not add a new icon dependency.

Simple existing/Unicode icons are optional.

==================================================
9. TAGS
==================================================

Existing result tags include signals such as:

New
Open house
Reduced

Keep these when present.

Render them as subtle secondary badges/labels.

Do not let tags push the primary Match/price information down unnecessarily.

==================================================
10. PROXIMITY
==================================================

Do NOT render one prose sentence.

Do NOT emit:

"(some unknown)"

Render one structured line per proximity criterion.

Examples:

23 min to 800 Burrard St
2 min walk to transit

For an unavailable calculation:

ⓘ 1 proximity criterion unavailable

If the structured helper knows the actual unavailable criterion, make the name
discoverable using a concise line or tooltip/help affordance, for example:

ⓘ Drive to Metrotown unavailable

Use the actual location/mode from the proximity dict.

Nearest-transit rules should read naturally:

"2 min walk to transit"

rather than:

"nearest transit station: 2 min"

Do not mark unavailable proximity as unmet.

==================================================
11. ANALYZE
==================================================

Keep Analyze as the primary card action.

Use a primary Streamlit button if that fits the visual design.

Prefer:

Analyze

or:

Analyze property

Do not add Save/Favorite functionality merely because the mockup contains a
heart icon.

Preserve the existing state behavior exactly.

==================================================
12. CARD CONSISTENCY
==================================================

Aim for visually consistent card heights.

Important alignment targets:

- image height
- price/match row
- address area
- property basics
- proximity area
- Analyze button

Because this is Streamlit, do not implement fragile DOM hacks solely to achieve
pixel-perfect equal heights.

A reasonable scoped min-height/flex treatment is acceptable if robust.

==================================================
13. RESPONSIVENESS
==================================================

Use the current Streamlit architecture pragmatically.

Desktop:
3 columns

Medium:
2 columns if this can be implemented robustly

Small:
1 column

If Streamlit's native column behavior makes dynamic 3/2/1 switching brittle,
prioritize readable cards and no horizontal overflow over exact breakpoint
matching.

==================================================
14. DO NOT TOUCH
==================================================

Do not materially change:

- Table renderer
- Map renderer
- scoring logic
- match_scoring.py
- proximity backend
- agent ranking
- analysis UI

Shared result helper fixes are allowed only when necessary.

==================================================
TESTS
==================================================

Update/add focused helper tests where practical.

At minimum run:

pytest -q

==================================================
ACCEPTANCE CRITERIA
==================================================

Grid is complete when:

- navigation says Grid, not Cards
- Grid/Table/Map selector exists
- no redundant Results view label remains
- count is visible
- cards use consistent image treatment
- Price is prominent
- Overall Match is prominent
- Match uses existing shared color semantics
- rank remains canonical
- address is split into street/locality
- property facts use compact consistent formatting
- proximity uses structured lines
- "(some unknown)" is gone
- unavailable proximity is explicitly represented
- Analyze works exactly as before
- no mockup values are hard-coded
- Table/Map remain functional
- tests pass

Before editing, state the relevant functions you are modifying.

Then implement, test, and report the files changed.
Prompt 3 — Table redesign

Attach the Table mockup.

You are working in:

smakamali/rental_search_agent

This is STEP 3 of the search-results redesign.

The shared results foundation and redesigned Grid view already exist.

I am attaching the Table mockup.

Use it as visual direction, but follow the repository-specific requirements
below.

Do not hard-code mockup values.

Redesign ONLY the Table view.

Do not materially change Grid or Map.

==================================================
DESIGN INTENT
==================================================

Grid = visual browsing.

Table = dense comparison.

The Table should NOT duplicate the card visual design.

It should maximize:

- scanability
- numeric comparison
- row alignment
- information density

Use the same listing data and shared helper semantics as Grid.

==================================================
1. SHARED HEADER / VIEW CONTROL
==================================================

Use the Grid/Table/Map navigation already implemented.

Table should be active.

Do not create another results heading or another view selector.

Do not add an interactive Sort dropdown.

If the shared header exposes existing `last_sort_by` as passive text, preserve
that.

Switching Grid -> Table must not:

- rerun the search
- re-score
- re-enrich proximity
- rewrite rank

==================================================
2. TABLE COLUMNS
==================================================

The current table has:

Rank
Photo
Address
Type
Bed
Bath
Size
Price
Days on Market
Match score
Tags
Proximity
Analyze

Use this as the starting point.

Preferred redesigned columns:

Rank
Photo
Address
Type
Bed
Bath
Size
Price
DOM
Match
Proximity
Analyze

TAGS:
The current screenshot shows the Tags column empty.

Do NOT blindly keep an all-"—" column.

Preferred behavior:

- if NO visible result has a tag, omit the Tags column entirely
- if at least one result has a meaningful tag, include the column

If dynamic column widths become too complicated, it is acceptable to remove
Tags from Table and leave tags primarily in Grid, provided no important
functionality is lost.

==================================================
3. DO NOT USE ONE STATIC COLUMN-WIDTH ARRAY BLINDLY
==================================================

The existing `_TABLE_COL_WIDTHS` assumes the old 13-column shape.

Update the table layout cleanly.

If Tags becomes conditional, construct:

headers
column widths
cell renderers

from the same chosen column schema rather than maintaining mismatched parallel
arrays.

Avoid index-heavy code like:

row_cols[11]
row_cols[12]

when a small explicit schema/helper would make the code safer.

Keep the implementation understandable.

==================================================
4. RANK
==================================================

Use listing["rank"].

Rank is the canonical listing identity used by the agent.

Do NOT replace it with the current row index.

If the current displayed order and rank differ due to an intentional display
sort, preserve the canonical rank.

==================================================
5. PHOTO
==================================================

Use a compact uniform clickable thumbnail.

Target roughly:

56–72 px wide

with:
- consistent aspect ratio
- object-fit: cover
- safe URL validation
- graceful fallback

Table photos should not control row height excessively.

==================================================
6. ADDRESS
==================================================

Use split_listing_address().

Render:

4137 Dominion Street
Burnaby, BC V5G 1C5

with:
- street as primary text
- locality as small secondary text
- safe listing link where currently supported

Avoid a long unsplit address block.

==================================================
7. PROPERTY TYPE
==================================================

Continue to use:

listing["house_category"]

This is the specific building format used elsewhere in the app.

Do not replace it with broader `property_category`.

Examples can include:

House
Apartment
Row / Townhouse

Do not infer type from image/address.

==================================================
8. BED / BATH / SIZE
==================================================

Preserve bedroom den notation via the existing bedroom formatter.

Use compact values:

Bed: 3
Bath: 2
Size: 1,852 sq ft

If keeping the header `Size`, either:

1,852 sq ft

or:

1,852
sq ft

is acceptable.

Use shared format_count / format_sqft semantics.

==================================================
9. PRICE
==================================================

Use full currency formatting:

$1,730,000

not compact map formatting.

Do not use price_display when a valid numeric price exists.

Preserve the existing security rationale of preferring numeric price over
scraped Markdown-capable text.

==================================================
10. DAYS ON MARKET
==================================================

The current DOM is derived from:

listing_age_hours

and is approximate.

Keep the compact form:

5d

Header may be:

DOM

with help text such as:

"Approximate days since listing publication"

if practical.

Do not imply this is an authoritative MLS DOM field when the source only
provides listing freshness.

==================================================
11. MATCH
==================================================

Replace plain:

88%

with the same compact Match treatment used by Grid.

Example:

◯ 88%

The percentage must remain explicit.

Use:

match_score

with semantic_score fallback.

Use shared get_score_color().

The Match column should be visually easy to scan vertically.

==================================================
12. PROXIMITY
==================================================

This is a major change.

Current:

800 Burrard st: 23 min; nearest transit station: 2 min (some unknown)

Target multi-line cell:

23 min to 800 Burrard St
2 min walk to transit
ⓘ 1 proximity criterion unavailable

Use the shared structured proximity helper.

Do not parse listing.proximity separately in Table.

Use actual location names and modes.

If an unavailable criterion can be named compactly, use the name.

Never show:

"(some unknown)"

==================================================
13. ANALYZE
==================================================

Keep a compact Analyze button.

Use the same underlying action helper/state behavior as Grid.

Do not duplicate Analyze state mutation logic if Step 1 already extracted it.

==================================================
14. DENSITY
==================================================

The Table should show materially more results per viewport than Grid.

Reduce unnecessary:

- vertical padding
- large blank spaces
- repeated captions

Use subtle row dividers.

Maintain readability.

Do not turn each row into a card.

==================================================
15. RESPONSIVENESS
==================================================

This is a wide comparison table.

Do not destroy readability trying to fit every column onto a narrow phone.

If narrow screens require horizontal overflow or reduced columns, prefer a
controlled usable result over microscopic text.

Do not use unstable Streamlit DOM selectors.

==================================================
16. DO NOT TOUCH
==================================================

Do not materially change:

- Grid
- Map
- ranking/scoring
- proximity backend
- Analyze detail view

==================================================
TESTS
==================================================

Update existing:

tests/unit/test_streamlit_helpers.py

and/or the new result-view test module.

Add tests for:

- conditional Tags behavior if implemented
- formatted size
- Match preference over semantic
- canonical rank preservation
- structured proximity rows

Run:

pytest -q

==================================================
ACCEPTANCE CRITERIA
==================================================

Table is complete when:

- same results as Grid
- same canonical ranks
- same Match values/colors
- compact uniform thumbnails
- address hierarchy is improved
- size is formatted with commas/unit
- Price is full currency
- DOM is compact
- empty Tags column is not wasting space
- Proximity is structured
- "(some unknown)" is gone
- Analyze works
- Table is denser than Grid
- Grid and Map remain intact
- tests pass

Inspect the existing implementation first, then make the changes directly.
Prompt 4 — Map redesign

Attach the Map mockup.

You are working in:

smakamali/rental_search_agent

This is STEP 4 of the search-results redesign.

The shared foundation, Grid view, and Table view have already been implemented.

I am attaching the Map mockup.

Use it as visual direction.

Redesign the Map view using the EXISTING map architecture.

==================================================
IMPORTANT CURRENT IMPLEMENTATION
==================================================

The repository already uses:

Folium as the primary map implementation

with:

PyDeck as fallback

Do NOT introduce another map library.

Current relevant helpers include concepts equivalent to:

- _build_map_data
- _folium_marker_icon
- _add_folium_markers
- _get_map_html_cached
- _render_results_map
- _listings_cache_key
- _format_map_price_label

Use/refactor these rather than replacing the map stack.

==================================================
1. MAP BECOMES THE THIRD RESULTS VIEW
==================================================

The shared results selector should now be:

Grid | Table | Map

Map should render when:

st.session_state["results_view"] == "map"

The old behavior rendered Map permanently below Grid/Table in a separate
"Search results map" expander.

Remove that duplicate always-visible map.

There should be ONE active primary results representation at a time.

Changing views must only change presentation.

It must not:

- rerun rental_search
- rerun semantic embeddings
- rerun proximity API calls
- change display_list
- change rank

==================================================
2. MAP HEADER
==================================================

Use the shared Search results header.

Do not add another large redundant "Search results map" expander unless needed.

Within Map, show a compact control:

Map labels
[ Price | Match | Rank ]

Use the existing:

map_label_mode

session state.

Supported canonical values:

price
match
rank

Default/fallback:

price

==================================================
3. PRICE LABEL MODE
==================================================

Preserve current compact price behavior.

Examples:

$1.73M
$1.38M
$973,000

Use the existing shared compact map price formatter.

Do not use the full Grid/Table currency string if it makes map markers too wide.

==================================================
4. MATCH LABEL MODE
==================================================

Add:

match

to `_build_map_data` / equivalent.

Marker label examples:

74%
88%
75%

Use:

listing["match_score"]

with semantic_score fallback.

Use:

get_score_color()

for Match marker color.

Do not create separate map score thresholds.

Numeric percentage must remain visible.

Missing score:

show a neutral marker/label rather than crashing or pretending it is 0%.

==================================================
5. RANK LABEL MODE
==================================================

Use the authoritative:

listing["rank"]

Examples:

#1
#2
#3

or plain:

1
2
3

Choose the version that renders best.

DO NOT derive rank from marker order or current map_points index.

Map #2 must correspond to the same listing as:

Grid #2
Table rank 2

==================================================
6. MAP POINT DATA
==================================================

Enhance the shared map-point representation enough to support useful popups and
marker styling.

Include where available:

- listing id
- rank
- lat
- lon
- label
- safe listing URL
- price
- match score
- street address
- locality
- bedrooms
- bathrooms
- sqft
- photo URL if useful

Do not duplicate or normalize backend data beyond what presentation requires.

==================================================
7. FIT MAP TO RESULTS
==================================================

Current Folium implementation uses:

average center
zoom_start=11

Improve this.

For 2+ valid map points:

use Folium bounds / fit_bounds so all result markers are naturally visible.

For 1 point:

use a sensible local zoom.

Do not hard-code Burnaby/Vancouver coordinates or viewport bounds.

==================================================
8. UNMAPPABLE RESULTS
==================================================

Current `_build_map_data` silently skips invalid/missing coordinates.

Keep skipping invalid coordinates for marker creation, but expose this to the
user.

Examples:

5 results on map

or:

4 of 5 results on map
1 listing has no mappable location

Do not silently imply all results are plotted.

==================================================
9. FOLIUM MARKERS
==================================================

Evolve the existing Folium DivIcon implementation.

Price:
compact rectangular label

Match:
compact score label/pill/ring-like marker using shared score color

Rank:
compact clear rank marker

Keep labels legible against the map.

Do not add excessive decoration.

Preserve safe links.

==================================================
10. POPUPS
==================================================

If straightforward with the existing Folium implementation, add a compact popup
for each marker containing:

street address
price
Match
bed / bath / size

and a safe link to the REALTOR.CA listing.

Do NOT force a Streamlit Analyze button inside the Folium iframe using custom
JavaScript.

Analyze can remain outside the iframe.

Do not introduce brittle bidirectional JS just for this.

==================================================
11. OPTIONAL RESULT SUMMARY BELOW MAP
==================================================

The mockup shows compact property summaries below the map.

Implement this ONLY if it can be done cleanly using Streamlit and shared result
helpers.

Preferred simple implementation:

a compact Streamlit row/grid below the map showing:

#rank
small photo
price
Match
street address
bed · bath · size

Do NOT build a complex custom carousel.

Do NOT duplicate the full Grid card.

If there are many results, wrap summaries to additional rows.

If this becomes brittle, prioritize:
- good markers
- useful popups
- correct identity

over matching the mockup's horizontal strip exactly.

==================================================
12. MAP CACHE
==================================================

Preserve caching.

The map HTML cache key must vary correctly with:

- listings
- label mode

If Match mode uses score-based marker colors, the score is already part of the
serialized listing data; ensure cache behavior remains correct.

Changing:

Price -> Match -> Rank

must update the markers.

Do not invalidate/recompute expensive search/enrichment data.

==================================================
13. PYDECK FALLBACK
==================================================

Preserve PyDeck fallback behavior.

Extend it enough that Match label mode does not crash.

It does not need pixel-identical styling to Folium.

Folium remains the primary rendering target.

==================================================
14. NO PROXIMITY OVERLOAD
==================================================

Do not put all proximity criteria directly onto the map.

The map's job is geographic property comparison.

Detailed proximity belongs in Grid/Table/Analyze.

==================================================
15. DO NOT TOUCH
==================================================

Do not materially redesign:

- Grid
- Table
- Analyze
- scoring
- ranking
- proximity computation

==================================================
TESTS
==================================================

Extend map helper tests.

Test at least:

_build_map_data(..., label_mode="price")
_build_map_data(..., label_mode="match")
_build_map_data(..., label_mode="rank")

Verify:

- canonical rank
- match_score preferred
- semantic fallback
- missing score safe
- invalid coordinates skipped
- mappable count can be determined
- compact price preserved

Run:

pytest -q

==================================================
ACCEPTANCE CRITERIA
==================================================

Map is complete when:

- it is a true third Grid/Table/Map view
- the old duplicate map below Grid/Table is gone
- Price mode works
- Match mode works
- Rank mode works
- canonical rank is preserved
- Match uses shared color logic
- map fits the result coordinates
- unmappable result count is reported
- cached Folium behavior remains intact
- PyDeck fallback still works
- no new map dependency is introduced
- Grid/Table remain unchanged
- tests pass

Inspect first, then implement directly.
Prompt 5 — Integration and regression pass
You are working in:

smakamali/rental_search_agent

This is STEP 5, the final integration/regression pass for the search-results
redesign.

Grid, Table, and Map are already implemented.

Do NOT redesign them again from scratch.

Review the CURRENT diff and make the three views behave as one coherent
Streamlit feature.

==================================================
1. VERIFY THE REPOSITORY ARCHITECTURE
==================================================

The desired architecture is:

streamlit_app.py
    application/session/chat orchestration

streamlit_results.py
    Grid/Table/Map presentation

display_format.py
    generic shared formatting and score-color semantics

streamlit_analysis.py
    detailed Analyze presentation

Do not force this exact decomposition if the implementation arrived at a cleaner
equivalent, but avoid moving the search-result renderers back into a monolithic
streamlit_app.py.

Remove obvious duplicated helper logic.

==================================================
2. CROSS-VIEW IDENTITY
==================================================

For every listing:

Grid #N
Table rank N
Map #N

must all refer to the same listing.

`rank` is the agent/tool-layer canonical reference.

Do NOT replace it with visible array position.

Test intentionally out-of-rank-order result arrays.

==================================================
3. MATCH CONSISTENCY
==================================================

All search-result views should use:

match_score first
semantic_score fallback

The detailed Analyze UI may display all four component gauges, but the compact
search-results Match should represent the overall match_score.

Grid:
compact Match

Table:
compact Match

Map Match mode:
Match marker

All must use:

display_format.get_score_color()

No duplicate score thresholds.

==================================================
4. RANK VS MATCH CLARITY
==================================================

A property with rank #1 is not necessarily required to have the highest visible
Match score, depending on the current agent-driven ordering/sort state.

Do NOT silently change rank to force that visual relationship.

Inspect:

st.session_state["last_sort_by"]

If appropriate, show a passive ordering hint in the shared results header.

Examples ONLY when accurate:

Ordered by proximity
Ordered by match
Ordered by price
Ordered by newest

Do not add a user-facing sort dropdown in this pass.

Do not claim "Relevance" unless that is literally the active ordering semantics.

==================================================
5. VIEW STATE
==================================================

Verify:

Grid -> Table -> Map -> Grid

without changing:

- display_list
- master_list
- rank
- last_sort_by
- scoring
- proximity
- chat state
- selected Analyze listing

Canonical values:

grid
table
map

Old persisted:

cards

must migrate/fallback to:

grid

Invalid states must safely recover.

==================================================
6. MAP LABEL STATE
==================================================

Verify:

Price -> Match -> Rank -> Price

without triggering search/scoring/enrichment.

Canonical:

price
match
rank

Invalid/stale values:

fallback to price

==================================================
7. PROXIMITY CONSISTENCY
==================================================

Search the codebase for:

"(some unknown)"

It should no longer be rendered in Grid/Table.

Prefer removing the old formatter path entirely if unused.

The shared structured proximity helper must be the sole source for result-view
proximity text.

Verify:

available:
23 min to 800 Burrard St

available transit:
2 min walk to transit

unavailable:
1 proximity criterion unavailable

and, where known:

Drive to <location> unavailable

Unavailable proximity calculations are NOT:

- unmet criteria
- missing MLS facts
- Analyze "Open questions"

==================================================
8. FORMAT CONSISTENCY
==================================================

Grid/Table:

$1,730,000

Map:

$1.73M

Size:

1,852 sq ft

Duration:

23 min

Match:

74%

Beds/baths:

3 bd · 2 ba

Use shared display_format helpers wherever possible.

Preserve bedroom den notation such as:

2 + 1

where the existing model provides it.

==================================================
9. TAGS
==================================================

Verify result tags remain available:

New
Open house
Reduced

Grid can show them.

Table should not waste a large empty Tags column if all results have no tags.

Do not remove underlying tag logic.

==================================================
10. URL / HTML SECURITY
==================================================

Review all custom HTML added during the redesign.

Ensure:

- scraped URLs are validated as http/https
- URLs/text are HTML escaped
- no javascript: links
- scraped price/address values cannot inject arbitrary markup
- existing security-regression behavior is preserved

Reuse safe_http_url and escaping helpers.

==================================================
11. STREAMLIT CSS
==================================================

Review custom CSS.

Prefer scoped classes such as:

.rsa-results-...

Do not depend heavily on generated:

.st-emotion-cache-*

Ensure result CSS does not unexpectedly alter:

- chat panel
- sidebar
- Analyze panel
- unrelated buttons

Use theme-aware/inherited colors where practical.

==================================================
12. RESPONSIVENESS
==================================================

Review:

Desktop
Medium width
Narrow/mobile width

Grid:
readable cards, no horizontal overflow

Table:
controlled wide behavior, no absurdly compressed text

Map:
useful width/height

View selector:
always usable

Do not chase pixel-perfect mockup fidelity with fragile CSS.

==================================================
13. MISSING DATA
==================================================

Verify graceful handling of:

- no photo
- no listing URL
- no price
- no match score
- no type
- no baths
- no sqft
- no tags
- no proximity
- one/more proximity values None
- no coordinates
- invalid coordinates

Never expose:

None
NaN
nan
null

in the result UI.

==================================================
14. PERFORMANCE
==================================================

View switching is PRESENTATION ONLY.

Verify it does not cause:

- rental_search
- filter_listings
- proximity API calls
- embedding API calls
- match rescoring

The Streamlit script will rerun normally, but expensive data work should not be
re-executed merely because results_view changed.

Preserve the existing Folium HTML cache.

Do not add aggressive caching that risks stale search results.

==================================================
15. ANALYZE REGRESSION
==================================================

The recently redesigned Analyze UI must remain intact.

Click Analyze from:

Grid
Table

and verify the same:

analyze_listing_id
analyze_listing

flow still renders:

streamlit_analysis.render_listing_analysis(...)

Do not regress the recently introduced multi-metric Analyze experience.

==================================================
16. TESTS
==================================================

Run the complete suite:

pytest -q

Pay special attention to:

tests/unit/test_streamlit_helpers.py
tests/unit/test_display_format.py
tests/unit/test_analysis_view.py
tests/unit/test_match_scoring.py

and any new result-view tests.

If helper functions were relocated, preserve/update regression tests rather than
deleting them simply to make the suite green.

==================================================
17. FINAL CODE CLEANUP
==================================================

Remove:

- dead old Cards code
- obsolete "(some unknown)" formatter logic
- obsolete 2-view-only state handling
- obsolete always-visible-map path
- unused constants/imports
- duplicate generic formatters

Do NOT perform unrelated refactors.

==================================================
FINAL ACCEPTANCE CHECK
==================================================

Before finishing verify all of these:

[ ] Grid/Table/Map use the same display_list
[ ] canonical rank is preserved everywhere
[ ] match_score is primary Match
[ ] semantic_score fallback works
[ ] shared get_score_color is used
[ ] Grid is optimized for browsing
[ ] Table is optimized for comparison
[ ] Map is optimized for geography
[ ] map supports Price/Match/Rank
[ ] "(some unknown)" is absent
[ ] unavailable proximity is explicit
[ ] result view switching is presentation-only
[ ] Map switching is presentation-only
[ ] no interactive sort dropdown was accidentally added
[ ] last_sort_by semantics remain intact
[ ] safe URL behavior is preserved
[ ] Analyze still works
[ ] Analyze detail UI is not regressed
[ ] tests pass

Make fixes as you find them.

At the end provide:

- files changed
- architecture after refactor
- Grid changes
- Table changes
- Map changes
- view/session state behavior
- rank semantics
- Match semantics
- proximity unavailable handling
- tests run and result
- any remaining limitations

Do not stop at a review. Implement the fixes.