"""Stripe event → journal entry rules engine.

Each function takes a Stripe event payload (the `data.object` field of a Stripe
event) and returns a list of balanced JournalEntry objects.

All monetary amounts in Stripe are integer minor units (cents). We convert to
Decimal dollars here. Currency conversion across non-USD entries is out of
scope for v0.1 — entries inherit the underlying currency.
"""

from datetime import date, datetime, timezone
from decimal import Decimal
from typing import Any, Dict, List, Optional

from stripe_bookkeeper.chart_of_accounts import ChartOfAccounts
from stripe_bookkeeper.models import JournalEntry, JournalLine


def _cents_to_decimal(cents: Optional[int]) -> Decimal:
    if cents is None:
        return Decimal("0")
    return (Decimal(int(cents)) / Decimal(100)).quantize(Decimal("0.01"))


def _date_from_timestamp(ts: Optional[int]) -> date:
    if not ts:
        return date.today()
    return datetime.fromtimestamp(int(ts), tz=timezone.utc).date()


def _spans_multiple_months(period_start: int, period_end: int) -> bool:
    """True if the billing period covers > ~31 days (annual / quarterly subs)."""
    if not period_start or not period_end:
        return False
    return (int(period_end) - int(period_start)) > 31 * 24 * 60 * 60


def charge_succeeded(obj: Dict[str, Any], coa: ChartOfAccounts) -> List[JournalEntry]:
    """A one-time charge succeeded.

    DR Stripe Clearing
        CR Revenue
    DR Payment Processing Fees
        CR Stripe Clearing
    """
    amount = _cents_to_decimal(obj.get("amount"))
    if amount <= 0:
        return []

    fee = _cents_to_decimal(_extract_fee(obj))
    entry_date = _date_from_timestamp(obj.get("created"))
    charge_id = obj.get("id", "unknown")
    customer = obj.get("customer") or obj.get("billing_details", {}).get("email") or "—"

    entries: List[JournalEntry] = []

    revenue_entry = JournalEntry(
        entry_date=entry_date,
        memo=f"Charge {charge_id} ({customer})",
        source_id=charge_id,
        source_type="charge.succeeded",
        lines=[
            JournalLine(account=coa.stripe_clearing, debit=amount),
            JournalLine(account=coa.revenue, credit=amount),
        ],
    )
    entries.append(revenue_entry)

    if fee > 0:
        fee_entry = JournalEntry(
            entry_date=entry_date,
            memo=f"Stripe fee on {charge_id}",
            source_id=charge_id,
            source_type="charge.fee",
            lines=[
                JournalLine(account=coa.payment_processing_fees, debit=fee),
                JournalLine(account=coa.stripe_clearing, credit=fee),
            ],
        )
        entries.append(fee_entry)

    return entries


def charge_refunded(obj: Dict[str, Any], coa: ChartOfAccounts) -> List[JournalEntry]:
    """A charge was refunded (full or partial).

    DR Refunds Contra-Revenue
        CR Stripe Clearing

    Stripe does NOT refund the original processing fee, so no fee reversal is
    booked. The fee remains as an expense (this is correct US GAAP).
    """
    refunded = _cents_to_decimal(obj.get("amount_refunded"))
    if refunded <= 0:
        return []

    entry_date = _date_from_timestamp(obj.get("created"))
    charge_id = obj.get("id", "unknown")

    return [
        JournalEntry(
            entry_date=entry_date,
            memo=f"Refund of charge {charge_id}",
            source_id=charge_id,
            source_type="charge.refunded",
            lines=[
                JournalLine(account=coa.refunds_contra, debit=refunded),
                JournalLine(account=coa.stripe_clearing, credit=refunded),
            ],
        )
    ]


def invoice_paid(obj: Dict[str, Any], coa: ChartOfAccounts) -> List[JournalEntry]:
    """A Stripe Invoice (typically a subscription) was paid.

    For invoices whose billing period covers more than ~1 month, the cash is
    booked to Deferred Revenue (to be recognized monthly by a separate
    amortization process). For monthly invoices, it goes straight to Revenue.

    DR Stripe Clearing
        CR Revenue  (or Deferred Revenue for annual/quarterly)
    DR Payment Processing Fees
        CR Stripe Clearing
    """
    amount = _cents_to_decimal(obj.get("amount_paid"))
    if amount <= 0:
        return []

    fee = _cents_to_decimal(_extract_fee(obj))
    entry_date = _date_from_timestamp(obj.get("created"))
    invoice_id = obj.get("id", "unknown")

    is_long_period = False
    lines_data = (obj.get("lines") or {}).get("data") or []
    for line in lines_data:
        period = line.get("period") or {}
        if _spans_multiple_months(period.get("start"), period.get("end")):
            is_long_period = True
            break

    credit_account = coa.deferred_revenue if is_long_period else coa.revenue
    memo_suffix = " (long-period sub — booked to deferred revenue)" if is_long_period else ""

    entries: List[JournalEntry] = []

    entries.append(
        JournalEntry(
            entry_date=entry_date,
            memo=f"Invoice {invoice_id} paid{memo_suffix}",
            source_id=invoice_id,
            source_type="invoice.paid",
            lines=[
                JournalLine(account=coa.stripe_clearing, debit=amount),
                JournalLine(account=credit_account, credit=amount),
            ],
        )
    )

    if fee > 0:
        entries.append(
            JournalEntry(
                entry_date=entry_date,
                memo=f"Stripe fee on invoice {invoice_id}",
                source_id=invoice_id,
                source_type="invoice.fee",
                lines=[
                    JournalLine(account=coa.payment_processing_fees, debit=fee),
                    JournalLine(account=coa.stripe_clearing, credit=fee),
                ],
            )
        )

    return entries


def payout_paid(obj: Dict[str, Any], coa: ChartOfAccounts) -> List[JournalEntry]:
    """A Stripe payout landed in the bank.

    DR Bank
        CR Stripe Clearing
    """
    amount = _cents_to_decimal(obj.get("amount"))
    if amount <= 0:
        return []

    arrival = obj.get("arrival_date") or obj.get("created")
    entry_date = _date_from_timestamp(arrival)
    payout_id = obj.get("id", "unknown")

    return [
        JournalEntry(
            entry_date=entry_date,
            memo=f"Payout {payout_id} to bank",
            source_id=payout_id,
            source_type="payout.paid",
            lines=[
                JournalLine(account=coa.bank, debit=amount),
                JournalLine(account=coa.stripe_clearing, credit=amount),
            ],
        )
    ]


def dispute_funds_withdrawn(obj: Dict[str, Any], coa: ChartOfAccounts) -> List[JournalEntry]:
    """Stripe withdrew funds for an in-flight dispute.

    DR Disputes Pending
        CR Stripe Clearing
    """
    amount = _cents_to_decimal(obj.get("amount"))
    if amount <= 0:
        return []

    entry_date = _date_from_timestamp(obj.get("created"))
    dispute_id = obj.get("id", "unknown")

    return [
        JournalEntry(
            entry_date=entry_date,
            memo=f"Dispute {dispute_id} funds withdrawn",
            source_id=dispute_id,
            source_type="charge.dispute.funds_withdrawn",
            lines=[
                JournalLine(account=coa.disputes_pending, debit=amount),
                JournalLine(account=coa.stripe_clearing, credit=amount),
            ],
        )
    ]


def _extract_fee(obj: Dict[str, Any]) -> int:
    """Pull the Stripe fee (in cents) from a charge or invoice payload."""
    bt = obj.get("balance_transaction")
    if isinstance(bt, dict):
        fee = bt.get("fee")
        if fee is not None:
            return int(fee)
    fee_field = obj.get("application_fee_amount") or obj.get("fee")
    if isinstance(fee_field, int):
        return fee_field
    return 0


EVENT_HANDLERS = {
    "charge.succeeded": charge_succeeded,
    "charge.refunded": charge_refunded,
    "invoice.paid": invoice_paid,
    "invoice.payment_succeeded": invoice_paid,
    "payout.paid": payout_paid,
    "charge.dispute.funds_withdrawn": dispute_funds_withdrawn,
}


def event_to_entries(event: Dict[str, Any], coa: Optional[ChartOfAccounts] = None) -> List[JournalEntry]:
    """Dispatch a full Stripe event (with .type and .data.object) to entries."""
    coa = coa or ChartOfAccounts()
    event_type = event.get("type")
    handler = EVENT_HANDLERS.get(event_type)
    if not handler:
        return []
    obj = ((event.get("data") or {}).get("object")) or {}
    return handler(obj, coa)
