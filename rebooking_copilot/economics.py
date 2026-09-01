"""Decimal currency conversion, exchange economics, and threshold arithmetic.

Pure functions. No policy interpretation beyond the arithmetic the policy asks
for, and no exception handling: an unsupported currency raises rather than
producing an invented number.
"""

from __future__ import annotations

from decimal import ROUND_HALF_UP, Decimal

from pydantic import BaseModel

from .models import (
    RATIO_PRECISION,
    CandidateEconomics,
    Conversion,
    Money,
    Offer,
    Pnr,
    Policy,
    quantize_money,
)


class FxRateUnavailable(Exception):
    """Raised when no configured rate covers a currency pair."""


class FxTable(BaseModel):
    """Fixed proof-of-concept rates, keyed `"FROM/TO"`.

    The inverse of a configured pair is derived; nothing else is guessed.
    """

    rates: dict[str, Decimal]
    source: str

    def convert(self, money: Money, to_currency: str) -> Conversion:
        if money.currency == to_currency:
            return Conversion(
                original=money,
                converted=money,
                quote_pair=f"{money.currency}/{to_currency}",
                quoted_rate=Decimal("1"),
                applied_pair=f"{money.currency}/{to_currency}",
                applied_rate=Decimal("1"),
                source=self.source,
            )

        applied_pair = f"{money.currency}/{to_currency}"
        direct = self.rates.get(applied_pair)
        if direct is not None:
            converted = money.amount * direct
            quote_pair = applied_pair
            quoted_rate = direct
            applied_rate = direct
        else:
            quote_pair = f"{to_currency}/{money.currency}"
            inverse = self.rates.get(quote_pair)
            if inverse is None or inverse == 0:
                raise FxRateUnavailable(f"no configured rate for {applied_pair}")
            quoted_rate = inverse
            applied_rate = Decimal("1") / inverse
            converted = money.amount * applied_rate

        return Conversion(
            original=money,
            converted=Money(amount=quantize_money(converted), currency=to_currency),
            quote_pair=quote_pair,
            quoted_rate=quoted_rate,
            applied_pair=applied_pair,
            applied_rate=applied_rate,
            source=self.source,
        )


def exchange_economics(pnr: Pnr, offer: Offer, fx: FxTable) -> CandidateEconomics:
    """Cost of exchanging this ticket for this offer, in the booking's currency.

    Per-passenger amounts are converted before multiplication so the reported
    figures match the per-passenger fares the fixtures actually quote.
    """
    settlement = pnr.ticket.total_paid.currency
    passengers = pnr.passengers

    fare_conversion = fx.convert(offer.price, settlement)
    current_fee_conversion = fx.convert(pnr.ticket.change_fee_per_passenger, settlement)
    try:
        future_fee_conversion = fx.convert(offer.change_fee_per_passenger, settlement)
    except FxRateUnavailable:
        future_fee_conversion = None

    new_fare_total = quantize_money(fare_conversion.converted.amount * passengers)
    current_exchange_fee_total = quantize_money(
        current_fee_conversion.converted.amount * passengers
    )
    total_paid = pnr.ticket.total_paid.amount
    estimated_net_saving = quantize_money(total_paid - new_fare_total - current_exchange_fee_total)

    percent = (
        (estimated_net_saving / total_paid).quantize(RATIO_PRECISION, rounding=ROUND_HALF_UP)
        if total_paid
        else Decimal("0")
    )

    return CandidateEconomics(
        currency=settlement,
        new_fare_total=new_fare_total,
        current_exchange_fee_total=current_exchange_fee_total,
        future_change_fee_per_passenger=offer.change_fee_per_passenger,
        future_change_fee_in_booking_currency=(
            future_fee_conversion.converted.amount if future_fee_conversion else None
        ),
        estimated_net_saving=estimated_net_saving,
        net_saving_percent_of_total_paid=percent,
        fare_conversion=fare_conversion if offer.price.currency != settlement else None,
        future_change_fee_conversion=(
            future_fee_conversion
            if future_fee_conversion and offer.change_fee_per_passenger.currency != settlement
            else None
        ),
    )


def minimum_saving(total_paid: Decimal, policy: Policy) -> Decimal:
    """The greater of the absolute floor and the percentage of the amount paid."""
    percentage = quantize_money(total_paid * policy.min_saving_percent_of_total_paid)
    return max(quantize_money(policy.min_saving_absolute), percentage)


def trigger_price_per_passenger(
    total_paid: Decimal,
    exchange_fee_total: Decimal,
    minimum_saving: Decimal,
    passengers: int,
) -> Decimal:
    """Per-passenger fare at which this booking would exactly meet the threshold."""
    return quantize_money((total_paid - exchange_fee_total - minimum_saving) / passengers)
