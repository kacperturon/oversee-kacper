"""Money, FX, threshold, and trigger-price calculations."""

from __future__ import annotations

from decimal import Decimal

import pytest
from conftest import offer, pnr

from rebooking_copilot.economics import (
    FxRateUnavailable,
    FxTable,
    exchange_economics,
    minimum_saving,
    trigger_price_per_passenger,
)
from rebooking_copilot.models import Money


def test_single_passenger_exchange_subtracts_fare_and_existing_change_fee(fx, policy):
    result = exchange_economics(pnr(), offer(), fx)

    assert result.new_fare_total == Decimal("300.00")
    assert result.current_exchange_fee_total == Decimal("50.00")
    assert result.estimated_net_saving == Decimal("150.00")


def test_two_passenger_exchange_applies_fare_and_fee_twice(fx):
    booking = pnr(
        passengers=2,
        ticket={"totalPaid": {"amount": Decimal("1000.00"), "currency": "USD"}},
    )

    result = exchange_economics(booking, offer(), fx)

    assert result.new_fare_total == Decimal("600.00")
    assert result.current_exchange_fee_total == Decimal("100.00")
    assert result.estimated_net_saving == Decimal("300.00")


def test_candidate_offer_change_fee_does_not_affect_current_exchange(fx):
    cheap_future_rule = offer(changeFeePerPassenger={"amount": Decimal("0.00"), "currency": "USD"})
    expensive_future_rule = offer(
        changeFeePerPassenger={"amount": Decimal("400.00"), "currency": "USD"}
    )

    cheap = exchange_economics(pnr(), cheap_future_rule, fx)
    expensive = exchange_economics(pnr(), expensive_future_rule, fx)

    assert cheap.estimated_net_saving == expensive.estimated_net_saving == Decimal("150.00")
    assert cheap.future_change_fee_in_booking_currency == Decimal("0.00")
    assert expensive.future_change_fee_in_booking_currency == Decimal("400.00")


def test_future_reshop_uses_previously_selected_offer_change_fee_as_ticket_fee(fx):
    """After booking an offer, that offer's rule becomes the next run's ticket fee."""
    previously_selected = offer(
        price={"amount": Decimal("300.00"), "currency": "USD"},
        changeFeePerPassenger={"amount": Decimal("120.00"), "currency": "USD"},
    )
    reshopped_ticket = pnr(
        ticket={
            "pricePerPassenger": {"amount": Decimal("300.00"), "currency": "USD"},
            "totalPaid": {"amount": Decimal("300.00"), "currency": "USD"},
            "changeFeePerPassenger": previously_selected.change_fee_per_passenger.model_dump(),
        }
    )

    result = exchange_economics(
        reshopped_ticket, offer(price={"amount": Decimal("100.00"), "currency": "USD"}), fx
    )

    assert result.current_exchange_fee_total == Decimal("120.00")
    assert result.estimated_net_saving == Decimal("80.00")


def test_cross_currency_conversion_records_rate_and_provenance(fx):
    booking = pnr(
        ticket={
            "pricePerPassenger": {"amount": Decimal("2200.00"), "currency": "EUR"},
            "totalPaid": {"amount": Decimal("2200.00"), "currency": "EUR"},
            "changeFeePerPassenger": {"amount": Decimal("0.00"), "currency": "EUR"},
        }
    )
    usd_offer = offer(price={"amount": Decimal("2100.00"), "currency": "USD"})

    result = exchange_economics(booking, usd_offer, fx)

    assert result.currency == "EUR"
    assert result.new_fare_total == Decimal("1944.44")
    assert result.estimated_net_saving == Decimal("255.56")
    assert result.fare_conversion is not None
    assert result.fare_conversion.quote_pair == "EUR/USD"
    assert result.fare_conversion.quoted_rate == Decimal("1.08")
    assert result.fare_conversion.applied_pair == "USD/EUR"
    assert result.fare_conversion.applied_rate == Decimal("1") / Decimal("1.08")
    assert result.fare_conversion.source == "fixed-mvp-configuration"


def test_unsupported_currency_raises_rather_than_inventing_a_rate(fx):
    unsupported = offer(price={"amount": Decimal("100.00"), "currency": "JPY"})

    with pytest.raises(FxRateUnavailable):
        exchange_economics(pnr(), unsupported, fx)


def test_same_currency_conversion_is_identity(fx):
    converted = fx.convert(Money(amount=Decimal("10.00"), currency="USD"), "USD")

    assert converted.converted.amount == Decimal("10.00")
    assert converted.quote_pair == "USD/USD"
    assert converted.quoted_rate == Decimal("1")
    assert converted.applied_pair == "USD/USD"
    assert converted.applied_rate == Decimal("1")


def test_minimum_saving_takes_the_greater_of_absolute_floor_and_percentage(policy):
    assert minimum_saving(Decimal("260.00"), policy) == Decimal("25.00")
    assert minimum_saving(Decimal("2200.00"), policy) == Decimal("110.00")


def test_trigger_price_is_the_fare_that_exactly_meets_the_threshold():
    trigger = trigger_price_per_passenger(
        total_paid=Decimal("260.00"),
        exchange_fee_total=Decimal("150.00"),
        minimum_saving=Decimal("25.00"),
        passengers=1,
    )

    assert trigger == Decimal("85.00")


def test_money_never_uses_binary_floating_point(fx):
    booking = pnr(
        ticket={
            "pricePerPassenger": {"amount": Decimal("100.10"), "currency": "USD"},
            "totalPaid": {"amount": Decimal("100.10"), "currency": "USD"},
            "changeFeePerPassenger": {"amount": Decimal("0.20"), "currency": "USD"},
        }
    )

    result = exchange_economics(
        booking, offer(price={"amount": Decimal("50.30"), "currency": "USD"}), fx
    )

    assert result.estimated_net_saving == Decimal("49.60")
    assert isinstance(result.estimated_net_saving, Decimal)


def test_fx_table_reports_unsupported_pair(fx):
    with pytest.raises(FxRateUnavailable):
        fx.convert(Money(amount=Decimal("1.00"), currency="JPY"), "USD")


def test_fx_inverse_rate_is_derived_from_the_configured_pair():
    table = FxTable(rates={"EUR/USD": Decimal("1.08")}, source="test")

    to_usd = table.convert(Money(amount=Decimal("100.00"), currency="EUR"), "USD")
    to_eur = table.convert(Money(amount=Decimal("108.00"), currency="USD"), "EUR")

    assert to_usd.converted.amount == Decimal("108.00")
    assert to_eur.converted.amount == Decimal("100.00")
    assert to_usd.quote_pair == "EUR/USD"
    assert to_usd.applied_pair == "EUR/USD"
    assert to_usd.applied_rate == Decimal("1.08")
    assert to_eur.quote_pair == "EUR/USD"
    assert to_eur.applied_pair == "USD/EUR"
    assert to_eur.applied_rate == Decimal("1") / Decimal("1.08")
