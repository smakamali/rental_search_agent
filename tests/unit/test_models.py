"""Unit tests for rental_search_agent.models."""

import pytest
from pydantic import ValidationError

from rental_search_agent.models import (
    AskUserAnswerResponse,
    AskUserSelectedResponse,
    Listing,
    ListingFilterCriteria,
    RentalSearchFilters,
    RentalSearchResponse,
    SimulateViewingRequestResponse,
    UserDetails,
)


class TestRentalSearchFilters:
    def test_valid_minimal(self):
        f = RentalSearchFilters(min_bedrooms=2, location="Vancouver")
        assert f.min_bedrooms == 2
        assert f.location == "Vancouver"
        assert f.listing_type == "for_rent"

    def test_valid_full(self):
        f = RentalSearchFilters(
            min_bedrooms=2,
            max_bedrooms=3,
            min_bathrooms=1,
            location="Toronto",
            price_min=1500,
            price_max=3000,
        )
        assert f.max_bedrooms == 3
        assert f.price_min == 1500

    def test_min_bedrooms_required(self):
        with pytest.raises(ValidationError):
            RentalSearchFilters(location="Vancouver")

    def test_location_required(self):
        with pytest.raises(ValidationError):
            RentalSearchFilters(min_bedrooms=2)

    def test_ge_constraint_bedrooms(self):
        with pytest.raises(ValidationError):
            RentalSearchFilters(min_bedrooms=-1, location="Vancouver")

    def test_listing_type_valid(self):
        for lt in ("for_rent", "for_sale"):
            f = RentalSearchFilters(min_bedrooms=1, location="X", listing_type=lt)
            assert f.listing_type == lt

    def test_listing_type_invalid(self):
        with pytest.raises(ValidationError):
            RentalSearchFilters(min_bedrooms=1, location="X", listing_type="invalid")
        with pytest.raises(ValidationError):
            RentalSearchFilters(min_bedrooms=1, location="X", listing_type="for_sale_or_rent")

    def test_inverted_price_bounds_rejected(self):
        with pytest.raises(ValidationError, match="price_min"):
            RentalSearchFilters(min_bedrooms=1, location="X", price_min=3000, price_max=1500)

    def test_inverted_bedroom_bounds_rejected(self):
        with pytest.raises(ValidationError, match="min_bedrooms"):
            RentalSearchFilters(min_bedrooms=3, max_bedrooms=1, location="X")

    def test_inverted_bathroom_bounds_rejected(self):
        with pytest.raises(ValidationError, match="min_bathrooms"):
            RentalSearchFilters(min_bedrooms=1, location="X", min_bathrooms=3, max_bathrooms=1)

    def test_inverted_sqft_bounds_rejected(self):
        with pytest.raises(ValidationError, match="min_sqft"):
            RentalSearchFilters(min_bedrooms=1, location="X", min_sqft=1000, max_sqft=500)

    def test_equal_min_max_bounds_allowed(self):
        f = RentalSearchFilters(min_bedrooms=2, max_bedrooms=2, location="X", price_min=2000, price_max=2000)
        assert f.max_bedrooms == 2
        assert f.price_max == 2000


class TestListing:
    def test_to_short_label_with_index(self):
        l = Listing(
            id="mls-1",
            title="Test",
            url="https://example.com",
            address="123 Main St",
            price=2800,
            bedrooms=2,
        )
        assert l.to_short_label(1) == "[1] 123 Main St — $2,800"

    def test_to_short_label_with_price_display(self):
        l = Listing(
            id="mls-1",
            title="Test",
            url="https://example.com",
            address="123 Main St",
            price=2800,
            price_display="$2,800/month",
            bedrooms=2,
        )
        assert l.to_short_label(1) == "[1] 123 Main St — $2,800/month"

    def test_semantic_score_defaults_to_none(self):
        l = Listing(id="mls-1", title="Test", url="https://example.com", address="123 Main St", price=2800, bedrooms=2)
        assert l.semantic_score is None

    def test_semantic_score_round_trips_through_model_validate(self):
        # Regression test: semantic_score must be a real Listing field (not just a dict key)
        # so it survives Listing.model_validate(...) round-trips in filter_listings and
        # enrich_listings_with_proximity — see filtering.py and proximity.py.
        d = Listing(
            id="mls-1", title="Test", url="https://example.com", address="123 Main St", price=2800, bedrooms=2,
            semantic_score=0.87,
        ).model_dump()
        assert Listing.model_validate(d).semantic_score == 0.87

    def test_semantic_score_bounds(self):
        with pytest.raises(ValidationError):
            Listing(
                id="mls-1", title="Test", url="https://example.com", address="123 Main St", price=2800, bedrooms=2,
                semantic_score=1.5,
            )
        with pytest.raises(ValidationError):
            Listing(
                id="mls-1", title="Test", url="https://example.com", address="123 Main St", price=2800, bedrooms=2,
                semantic_score=-0.1,
            )

    def test_has_den_and_bedrooms_display_default_to_none(self):
        l = Listing(id="mls-1", title="Test", url="https://example.com", address="123 Main St", price=2800, bedrooms=2)
        assert l.has_den is None
        assert l.bedrooms_display is None

    def test_has_den_and_bedrooms_display_round_trip_through_model_validate(self):
        d = Listing(
            id="mls-1", title="Test", url="https://example.com", address="123 Main St", price=2800, bedrooms=2,
            has_den=True, bedrooms_display="2 + 1",
        ).model_dump()
        validated = Listing.model_validate(d)
        assert validated.has_den is True
        assert validated.bedrooms_display == "2 + 1"

    def test_to_short_label_without_index(self):
        l = Listing(
            id="mls-1",
            title="Test",
            url="https://example.com",
            address="456 Oak Ave",
            price=1500,
            bedrooms=1,
        )
        assert l.to_short_label() == "456 Oak Ave — $1,500"

    def test_required_fields(self):
        with pytest.raises(ValidationError):
            Listing(id="x", url="u", address="a", price=1, bedrooms=0)


class TestListingFilterCriteria:
    def test_all_optional(self):
        c = ListingFilterCriteria()
        assert c.min_bedrooms is None
        assert c.price_max is None

    def test_with_values(self):
        c = ListingFilterCriteria(min_bedrooms=2, price_max=2500)
        assert c.min_bedrooms == 2
        assert c.price_max == 2500

    def test_ge_constraint(self):
        with pytest.raises(ValidationError):
            ListingFilterCriteria(min_sqft=-1)

    def test_inverted_price_bounds_rejected(self):
        with pytest.raises(ValidationError, match="price_min"):
            ListingFilterCriteria(price_min=3000, price_max=1500)


class TestUserDetails:
    def test_valid_minimal(self):
        u = UserDetails(name="Jane", email="jane@example.com")
        assert u.name == "Jane"
        assert u.phone is None

    def test_valid_full(self):
        u = UserDetails(
            name="Jane",
            email="jane@example.com",
            phone="555-1234",
            preferred_times="weekends",
        )
        assert u.phone == "555-1234"

    def test_name_required(self):
        with pytest.raises(ValidationError):
            UserDetails(email="j@x.com")

    def test_email_required(self):
        with pytest.raises(ValidationError):
            UserDetails(name="Jane")


class TestResponseModels:
    def test_rental_search_response_roundtrip(self):
        l = Listing(
            id="m1",
            title="T",
            url="u",
            address="a",
            price=1,
            bedrooms=1,
        )
        r = RentalSearchResponse(listings=[l], total_count=1)
        d = r.model_dump()
        r2 = RentalSearchResponse.model_validate(d)
        assert r2.total_count == 1
        assert r2.listings[0].id == "m1"

    def test_ask_user_answer_response(self):
        r = AskUserAnswerResponse(answer="Yes")
        assert r.model_dump()["answer"] == "Yes"

    def test_ask_user_selected_response(self):
        r = AskUserSelectedResponse(selected=["A", "B"])
        assert r.model_dump()["selected"] == ["A", "B"]

    def test_simulate_viewing_request_response(self):
        r = SimulateViewingRequestResponse(summary="Done", contact_url="mailto:x")
        assert "Done" in r.summary
        assert r.contact_url == "mailto:x"
