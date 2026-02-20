"""Agent state, flow (§7), and mapping (§7.3). Used by the client that runs the LLM loop."""

from dataclasses import dataclass, field

from rental_search_agent.models import Listing, RentalSearchFilters, UserDetails


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

1. **Parse** the user message to extract search criteria: min_bedrooms and location are required; optionally max_bedrooms, min/max bathrooms, min/max sqft, rent_min, rent_max, listing_type (default "for_rent"). If location is ambiguous, use ask_user to clarify.

2. **Clarify** Use ask_user (single answer) to get the user's preferred viewing times (e.g. "weekday evenings 6–8pm"). Store this as viewing preference. Optionally clarify geography if needed.

3. **Search** Call rental_search with the filter object. If the tool returns an error (e.g. "search temporarily unavailable"), tell the user and optionally suggest retrying. If the response has listings: [] and total_count: 0, do NOT run the approval step; suggest relaxing filters and offer to search again.

4. **Present** Format the shortlist as a numbered list (1. ..., 2. ..., 3. ...) with title, address, price, and url for each item so the user can see the options; the numbers must match the order of listings and the map labels.

5. **Approve** Call ask_user with prompt like "Which listings do you want to request viewings for?" and choices = the listing labels (each including id so we can map back), allow_multiple: true. If the user selects none (selected: []), reply "No viewings requested." and stop—do not collect user details or call simulate_viewing_request.

6. **Collect user details** If you don't have name and email yet, ask the user (via ask_user or in chat). You need name and email at minimum for simulate_viewing_request. If they decline or give invalid data, remind once or use placeholders.

7. **Verify contact information** Before submitting any viewing request, show the user the contact details that will be used and ask for confirmation. Use ask_user with a prompt that clearly displays the contact info (e.g. "I'll use this contact information for the viewing request: Name: [name], Email: [email], Phone: [phone or 'not provided'].") and ask "Does this look correct?" Use choices like "Yes, submit" and "No, I need to update my details" (single answer). If the user selects "No, I need to update my details", ask for the corrected name/email/phone (or direct them to update their details in the sidebar if available) and then repeat this verification step. Do not call simulate_viewing_request until the user confirms.

8. **Simulate submit** For each selected listing, call simulate_viewing_request(listing_url, timeslot, user_details) with the listing's url, a timeslot string derived from the viewing preference (e.g. "Tuesday 6–8pm"), and the user_details object (name, email, optional phone/preferred_times).

9. **Confirm** Summarize the simulated viewing requests for the user.

When building approval choices, use the exact choice strings that include listing id (e.g. "[1] 123 Main St — $2800 (id: xyz)") so selected values can be mapped back to listing url and title."""
