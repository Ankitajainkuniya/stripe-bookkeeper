"""Tests for the Stripe event → journal entry rules engine."""

import json
from decimal import Decimal
from pathlib import Path

import pytest

from stripe_bookkeeper.chart_of_accounts import ChartOfAccounts
from stripe_bookkeeper.models import JournalEntry, JournalLine
from stripe_bookkeeper.rules import (
    charge_refunded,
    charge_succeeded,
    dispute_funds_withdrawn,
    event_to_entries,
    invoice_paid,
    payout_paid,
)


COA = ChartOfAccounts()


def _all_balanced(entries):
    return entries and all(e.is_balanced() for e in entries)


def test_journal_line_rejects_negative():
    with pytest.raises(ValueError):
        JournalLine(account="X", debit=Decimal("-1"))


def test_journal_line_rejects_both_sides():
    with pytest.raises(ValueError):
        JournalLine(account="X", debit=Decimal("1"), credit=Decimal("1"))


def test_journal_line_rejects_zero():
    with pytest.raises(ValueError):
        JournalLine(account="X")


def test_charge_succeeded_books_revenue_and_fee():
    obj = {
        "id": "ch_test_1",
        "amount": 10000,
        "created": 1700000000,
        "balance_transaction": {"fee": 320},
    }
    entries = charge_succeeded(obj, COA)
    assert len(entries) == 2
    assert _all_balanced(entries)

    revenue, fee = entries
    assert revenue.total_debit() == Decimal("100.00")
    assert any(l.account == COA.revenue and l.credit == Decimal("100.00") for l in revenue.lines)
    assert fee.total_debit() == Decimal("3.20")
    assert any(l.account == COA.payment_processing_fees and l.debit == Decimal("3.20") for l in fee.lines)


def test_charge_succeeded_with_no_fee():
    obj = {"id": "ch_test_2", "amount": 5000, "created": 1700000000}
    entries = charge_succeeded(obj, COA)
    assert len(entries) == 1
    assert _all_balanced(entries)


def test_charge_succeeded_skips_zero_amount():
    obj = {"id": "ch_zero", "amount": 0, "created": 1700000000}
    assert charge_succeeded(obj, COA) == []


def test_charge_refunded_books_contra_revenue():
    obj = {"id": "ch_test_3", "amount_refunded": 4900, "created": 1700000000}
    entries = charge_refunded(obj, COA)
    assert len(entries) == 1
    assert _all_balanced(entries)
    assert entries[0].total_debit() == Decimal("49.00")
    assert any(l.account == COA.refunds_contra and l.debit == Decimal("49.00") for l in entries[0].lines)


def test_invoice_paid_monthly_books_revenue():
    obj = {
        "id": "in_monthly",
        "amount_paid": 9900,
        "created": 1700000000,
        "lines": {
            "data": [{"period": {"start": 1700000000, "end": 1702592000}, "amount": 9900}]
        },
    }
    entries = invoice_paid(obj, COA)
    assert _all_balanced(entries)
    revenue_line = next(l for e in entries for l in e.lines if l.account == COA.revenue)
    assert revenue_line.credit == Decimal("99.00")


def test_invoice_paid_annual_books_deferred_revenue():
    obj = {
        "id": "in_annual",
        "amount_paid": 118800,
        "created": 1700000000,
        "lines": {
            "data": [{"period": {"start": 1700000000, "end": 1731536000}, "amount": 118800}]
        },
    }
    entries = invoice_paid(obj, COA)
    assert _all_balanced(entries)
    deferred = next(l for e in entries for l in e.lines if l.account == COA.deferred_revenue)
    assert deferred.credit == Decimal("1188.00")
    assert not any(l.account == COA.revenue and l.credit > 0 for e in entries for l in e.lines)


def test_payout_paid_books_bank():
    obj = {"id": "po_test", "amount": 226516, "created": 1700000000, "arrival_date": 1700086400}
    entries = payout_paid(obj, COA)
    assert _all_balanced(entries)
    assert entries[0].total_debit() == Decimal("2265.16")
    assert any(l.account == COA.bank and l.debit == Decimal("2265.16") for l in entries[0].lines)


def test_dispute_withdrawn_books_disputes_pending():
    obj = {"id": "dp_test", "amount": 10000, "created": 1700000000}
    entries = dispute_funds_withdrawn(obj, COA)
    assert _all_balanced(entries)
    assert any(l.account == COA.disputes_pending and l.debit == Decimal("100.00") for l in entries[0].lines)


def test_event_to_entries_dispatches_by_type():
    event = {
        "type": "charge.succeeded",
        "data": {"object": {"id": "ch_x", "amount": 10000, "created": 1700000000}},
    }
    entries = event_to_entries(event)
    assert _all_balanced(entries)


def test_event_to_entries_ignores_unknown_types():
    assert event_to_entries({"type": "customer.created", "data": {"object": {}}}) == []


def test_demo_fixtures_all_balanced():
    fixtures = json.loads(
        (Path(__file__).resolve().parent.parent / "stripe_bookkeeper" / "fixtures" / "demo_events.json").read_text()
    )
    all_entries = []
    for event in fixtures:
        all_entries.extend(event_to_entries(event))
    assert all_entries
    assert all(e.is_balanced() for e in all_entries)
