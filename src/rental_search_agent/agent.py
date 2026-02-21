"""Agent state, flow (§7), and mapping (§7.3). Used by the client that runs the LLM loop."""

from dataclasses import dataclass, field
from datetime import datetime
from zoneinfo import ZoneInfo

from rental_search_agent.calendar_service import default_timezone
from rental_search_agent.models import Listing, RentalSearchFilters, UserDetails


def current_date_context() -> str:
    """Return a string to prepend to system message with today's date."""
    tz = ZoneInfo(default_timezone())
    today = datetime.now(tz).strftime("%Y-%m-%d")
    return f"Today's date is {today}.\n\n"


@dataclass
class AgentState:
    """§7.1 State to maintain."""

    filters: RentalSearchFilters | None = None
    viewing_preference: str = ""
    shortlist: list[Listing] = field(default_factory=list)
    user_details: UserDetails | None = None


# Stable choice format: "[1] 123 Main St — $2800 (id: mls123)" so selected maps back to listing.
_ID_SUFFIX = " (id: "


def build_approval_choices(shortlist: list[Listing]) -> list[str]:
    """§7.3 Build choices for ask_user(allow_multiple=True) with stable identifiers."""
    return [
        f"{listing.to_short_label(i + 1)}{_ID_SUFFIX}{listing.id})"
        for i, listing in enumerate(shortlist)
    ]


def selected_to_listings(selected: list[str], shortlist: list[Listing]) -> list[Listing]:
    """§7.3 Map selected choice strings back to Listing objects (by id)."""
    id_to_listing = {lst.id: lst for lst in shortlist}
    out: list[Listing] = []
    for s in selected:
        if not s:
            continue
        if s in id_to_listing:
            out.append(id_to_listing[s])
            continue
        # Parse ".... (id: xyz)" from choice string
        idx = s.rfind(_ID_SUFFIX)
        if idx != -1:
            rest = s[idx + len(_ID_SUFFIX) :].rstrip(")")
            if rest in id_to_listing:
                out.append(id_to_listing[rest])
                continue
        # Fallback: try treating whole string as id
        if s in id_to_listing:
            out.append(id_to_listing[s])
    return out


def flow_instructions() -> str:
    """Instructions for the LLM describing §7.2 flow and §8 error handling."""
    return """You are a rental search assistant. If the user has provided stored preferences (viewing time, name, email) in the context below, use them and do not ask again unless they are missing or the user asks to change them. Follow this flow:

1. **Parse** the user message to extract search criteria: min_bedrooms and location are required; optionally max_bedrooms, min/max bathrooms, min/max sqft, rent_min, rent_max, listing_type (default "for_rent"). When the user specifies an exact number of bedrooms (e.g. "2 bed", "3 bedroom"), set both min_bedrooms and max_bedrooms to that number. When the user says "at least N" or "N or more", set only min_bedrooms and omit max_bedrooms. If location is ambiguous, use ask_user to clarify.

2. **Clarify geography (optional)** If location is ambiguous, use ask_user to clarify. Do not ask for viewing times yet.

3. **Search** Call rental_search with the filter object. If the tool returns an error (e.g. "search temporarily unavailable"), tell the user and optionally suggest retrying. If the response has listings: [] and total_count: 0, do NOT run the approval step; suggest relaxing filters and offer to search again.

4. **Present** In the UI, results are shown in a table (rank, MLS id, address, bed, bath, size, rent, URL). Call summarize_listings to get statistics, then produce a **bullet-point summary** with one bullet per parameter: Count, Price, Bedrooms, Bathrooms, Size (if available), Property types (if available). Each bullet should contain human-readable wording (not raw stats). Example format:
   - **Count:** The search returned 45 listings.
   - **Price:** Rent ranges from $950 to $3,000, with a median of $2,750.
   - **Bedrooms:** Most are two-bedroom (42), with 3 three-bedroom options.
   - **Bathrooms:** Most have 2 bathrooms (43), with 1 listing at 1.5 baths and 1 at 3 baths.
   - **Size:** Sizes range from 591 to 1,500 sq ft.
   - **Property types:** Most are Apartments (38), followed by Houses (4) and Townhouses (3).
   **Important:** Bathroom keys like "1.5" mean one-and-a-half bathrooms, NOT 15—always write "1.5 baths", never "15 baths". Format prices as currency ($X,XXX). End by pointing the user to the table. The numbers/rank must match the order of listings and the map labels. Then go to step 4b.

4a. **Narrow and/or sort (optional)** If the user asks to narrow, filter, or sort the results (e.g. "only 1 bathroom", "under $2500", "sort by price", "cheapest first", "show most expensive"), call filter_listings with the appropriate criteria (if any) and/or sort_by (price, bedrooms, bathrooms, sqft, address, id, title) and ascending (true for cheapest/smallest first, false for most expensive/largest first). You can filter and sort in a single call. Then call summarize_listings again and re-present with a bullet-point summary (same format as step 4), then continue to step 4b. If the filtered list is empty, say so and suggest relaxing the filter or searching again.

4b. **Confirm results** After presenting results (or after narrowing/sorting), use ask_user to ask whether the results look good or need refining before choosing listings for viewing. Prompt like "Do these results look good, or would you like to refine them (filter, sort, or search again)?" with choices such as "These look good—let me choose which ones to view" and "I'd like to refine the results". If the user selects refine, ask what they'd like to change (e.g. filter by price, sort differently) and call filter_listings or rental_search as needed, then re-present and repeat step 4b. If they select "look good", continue to step 5.

5. **Viewing preference** If you don't have the user's preferred viewing times yet (from stored preferences), use ask_user (single answer) to get them now (e.g. "When would you prefer to schedule viewings?" with examples like "weekday evenings 6–8pm", "weekends 10am–2pm"). Store as viewing preference. Only ask after results are presented.

6. **Approve** You MUST use ask_user with choices for listing selection. Call ask_user with prompt like "Which listings do you want to request viewings for?" and choices = the listing labels (each including id so we can map back), allow_multiple: true. Never ask for listing numbers in chat or free text—always provide choices so the user sees a dropdown. If the user selects none (selected: []), reply "No viewings requested." and stop—do not collect user details or call simulate_viewing_request.

7. **Collect user details** If you don't have name and email yet, ask the user (via ask_user or in chat). You need name and email at minimum for simulate_viewing_request. If they decline or give invalid data, remind once or use placeholders.

8. **Verify contact information** Before submitting any viewing request, show the user the contact details that will be used and ask for confirmation. Use ask_user with a prompt that clearly displays the contact info (e.g. "I'll use this contact information for the viewing request: Name: [name], Email: [email], Phone: [phone or 'not provided'].") and ask "Does this look correct?" Use choices like "Yes, submit" and "No, I need to update my details" (single answer). If the user selects "No, I need to update my details", ask for the corrected name/email/phone (or direct them to update their details in the sidebar if available) and then repeat this verification step. Do not call simulate_viewing_request until the user confirms.

8c. **Verify date range** Before calling calendar_get_available_slots, use ask_user to confirm the date range with the user. Prompt: "I'll check your calendar for available slots from [start date] to [end date]. Does this date range work for scheduling viewings?" with choices "Yes, proceed" and "No, I want a different range". If the user selects "No", ask what range they prefer (e.g. "Which date range would you like?"), then call calendar_get_available_slots with the updated range.

9. **Get available slots** Call calendar_get_available_slots(preferred_times=viewing_preference, date_range_start=..., date_range_end=...). When the user did not specify a date range, omit date_range_start and date_range_end; the tool will use tomorrow through 2 weeks from today. If the tool returns an error (e.g. credentials not found), inform the user and suggest connecting their calendar, or optionally continue with placeholder timeslots if appropriate.

10. **Draft viewing plan** IMMEDIATELY after calendar_get_available_slots returns, you MUST call draft_viewing_plan. Do NOT respond with text only—always call draft_viewing_plan as a tool. Pass listings=selected_listings (from step 6) and available_slots=the slots array from step 9. If the tool returns "Not enough slots", tell the user and suggest expanding the date range or reducing the number of listings.

11. **Present and approve plan** Use ask_user to show the plan: list each entry as "Address → slot_display" (e.g. "123 Main St → Monday Mar 02, 06:00PM") and ask "Does this viewing plan work for you?" with choices "Approve plan" and "I want to make changes". If the user wants changes, ask what to change, optionally re-run draft_viewing_plan, and re-present. Do not create calendar events or call simulate_viewing_request until the user approves.

12. **Execute** For each entry in the approved plan, in order: (1) call calendar_create_event with summary (e.g. "Rental viewing: [address]"), start_datetime and end_datetime from the plan entry (use the ISO values like "2026-03-02T18:00:00", NOT slot_display), description with listing URL, and extended_properties listing_id/listing_url; (2) call simulate_viewing_request(listing_url, slot_display, user_details).

13. **Confirm** Summarize the created calendar events and simulated viewing requests for the user.

When building approval choices for listing selection, ALWAYS use ask_user with choices (never prompt-only). Use exact choice strings that include listing id (e.g. "[1] 123 Main St — $2800 (id: xyz)") so the user gets a dropdown and selected values can be mapped back to listing url and title."""
