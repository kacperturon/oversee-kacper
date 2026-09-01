"""Journey-key indexing and candidate lookup."""

from __future__ import annotations

from datetime import date

from conftest import offer, pnr

from rebooking_copilot.matching import candidate_offers, index_offers, journey_key


def test_journey_key_uses_origin_destination_and_local_departure_date():
    assert journey_key("AAA", "BBB", date(2026, 12, 1)) == ("AAA", "BBB", date(2026, 12, 1))


def test_offers_are_indexed_by_journey_key():
    index = index_offers([offer(offerId="OF-1"), offer(offerId="OF-2")])

    assert list(index) == [("AAA", "BBB", date(2026, 12, 1))]
    assert len(index[("AAA", "BBB", date(2026, 12, 1))]) == 2


def test_only_offers_on_the_same_route_and_adjacent_dates_are_candidates():
    same_day = offer(offerId="OF-SAME")
    other_route = offer(offerId="OF-ROUTE", route={"destination": "CCC"})
    distant_date = offer(
        offerId="OF-DATE",
        route={"departureDate": "2026-12-03"},
        departure="2026-12-03T10:00:00",
        arrival="2026-12-03T12:00:00",
    )

    index = index_offers([same_day, other_route, distant_date])
    candidates = candidate_offers(pnr(), index)

    assert [item.offer_id for item in candidates] == ["OF-SAME"]


def test_no_offers_for_the_journey_returns_empty_candidate_list():
    assert candidate_offers(pnr(), index_offers([])) == []


def test_offers_outside_the_schedule_window_remain_candidates_for_evidence():
    """The window is a policy rejection with a reason code, not a silent drop."""
    late = offer(offerId="OF-LATE", departure="2026-12-01T23:00:00", arrival="2026-12-02T01:00:00")

    candidates = candidate_offers(pnr(), index_offers([late]))

    assert [item.offer_id for item in candidates] == ["OF-LATE"]


def test_previous_day_offer_within_two_hours_is_retrieved():
    booking = pnr(
        segment={
            "departure": "2026-12-02T00:30:00",
            "arrival": "2026-12-02T02:30:00",
        }
    )
    previous_day = offer(
        offerId="OF-PREVIOUS",
        route={"departureDate": "2026-12-01"},
        departure="2026-12-01T23:30:00",
        arrival="2026-12-02T01:30:00",
    )

    candidates = candidate_offers(booking, index_offers([previous_day]))

    assert [item.offer_id for item in candidates] == ["OF-PREVIOUS"]


def test_next_day_offer_within_two_hours_is_retrieved():
    booking = pnr(
        segment={
            "departure": "2026-12-01T23:30:00",
            "arrival": "2026-12-02T01:30:00",
        }
    )
    next_day = offer(
        offerId="OF-NEXT",
        route={"departureDate": "2026-12-02"},
        departure="2026-12-02T00:30:00",
        arrival="2026-12-02T02:30:00",
    )

    candidates = candidate_offers(booking, index_offers([next_day]))

    assert [item.offer_id for item in candidates] == ["OF-NEXT"]


def test_adjacent_day_offer_outside_two_hours_is_still_retrieved_for_policy_evidence():
    adjacent_but_late = offer(
        offerId="OF-ADJACENT-LATE",
        route={"departureDate": "2026-12-02"},
        departure="2026-12-02T10:00:00",
        arrival="2026-12-02T12:00:00",
    )

    candidates = candidate_offers(pnr(), index_offers([adjacent_but_late]))

    assert [item.offer_id for item in candidates] == ["OF-ADJACENT-LATE"]
