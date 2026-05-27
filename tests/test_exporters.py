"""Tests for the journal-entry exporters (CSV, JSON, IIF)."""

import csv
import json
from decimal import Decimal
from pathlib import Path

import pytest

from stripe_bookkeeper.chart_of_accounts import ChartOfAccounts
from stripe_bookkeeper.exporters import to_csv, to_iif, to_json
from stripe_bookkeeper.models import JournalEntry, JournalLine
from stripe_bookkeeper.rules import event_to_entries


COA = ChartOfAccounts()


def _demo_entries():
    fixtures = json.loads(
        (
            Path(__file__).resolve().parent.parent
            / "stripe_bookkeeper"
            / "fixtures"
            / "demo_events.json"
        ).read_text()
    )
    entries = []
    for event in fixtures:
        entries.extend(event_to_entries(event, COA))
    return entries


def test_csv_roundtrip_header(tmp_path: Path):
    entries = _demo_entries()
    out = tmp_path / "j.csv"
    to_csv(entries, out)
    rows = list(csv.reader(out.read_text().splitlines()))
    assert rows[0] == ["date", "account", "debit", "credit", "memo", "source_id", "source_type"]
    assert len(rows) > 1


def test_json_roundtrip(tmp_path: Path):
    entries = _demo_entries()
    out = tmp_path / "j.json"
    to_json(entries, out)
    payload = json.loads(out.read_text())
    assert len(payload) == len(entries)
    assert "date" in payload[0]
    assert "lines" in payload[0]


def test_iif_basic_structure(tmp_path: Path):
    entries = _demo_entries()
    out = tmp_path / "j.iif"
    to_iif(entries, out)
    raw = out.read_bytes().decode("ascii")
    assert raw.endswith("\r\n")
    lines = raw.rstrip("\r\n").split("\r\n")
    assert lines[0].startswith("!TRNS\t")
    assert lines[1].startswith("!SPL\t")
    assert lines[2] == "!ENDTRNS"
    trns_count = sum(1 for ln in lines if ln.startswith("TRNS\t"))
    end_count = sum(1 for ln in lines if ln == "ENDTRNS")
    assert trns_count == len(entries)
    assert end_count == len(entries)


def test_iif_each_block_sums_to_zero(tmp_path: Path):
    entries = _demo_entries()
    out = tmp_path / "j.iif"
    to_iif(entries, out)
    raw = out.read_text(encoding="ascii")
    blocks = raw.split("ENDTRNS")
    saw_block = False
    for block in blocks:
        amounts = []
        for line in block.splitlines():
            if not line.startswith(("TRNS\t", "SPL\t")):
                continue
            fields = line.split("\t")
            if len(fields) >= 7 and fields[6]:
                amounts.append(Decimal(fields[6]))
        if amounts:
            saw_block = True
            assert sum(amounts) == Decimal("0"), f"unbalanced block: {amounts}"
    assert saw_block, "expected at least one TRNS block"


def test_iif_us_date_format(tmp_path: Path):
    """IIF uses M/D/YYYY (US), not ISO."""
    entries = [
        JournalEntry(
            entry_date=__import__("datetime").date(2026, 1, 4),
            memo="test",
            source_id="ch_test",
            source_type="charge.succeeded",
            lines=[
                JournalLine(account="Stripe Clearing", debit=Decimal("100.00")),
                JournalLine(account="Revenue", credit=Decimal("100.00")),
            ],
        ),
    ]
    out = tmp_path / "j.iif"
    to_iif(entries, out)
    raw = out.read_text(encoding="ascii")
    assert "1/4/2026" in raw
    assert "2026-01-04" not in raw  # not ISO


def test_iif_no_tabs_inside_memo(tmp_path: Path):
    """A tab in the memo would corrupt the file. We sanitize it."""
    entries = [
        JournalEntry(
            entry_date=__import__("datetime").date(2026, 1, 4),
            memo="charge\twith\ttabs and\nnewlines",
            source_id="ch_t",
            source_type="charge.succeeded",
            lines=[
                JournalLine(account="A", debit=Decimal("1")),
                JournalLine(account="B", credit=Decimal("1")),
            ],
        ),
    ]
    out = tmp_path / "j.iif"
    to_iif(entries, out)
    raw = out.read_text(encoding="ascii")
    # the data rows (after the headers) must each have exactly the header column count
    header_cols = raw.splitlines()[0].count("\t") + 1
    for line in raw.splitlines():
        if line.startswith(("TRNS\t", "SPL\t")):
            assert line.count("\t") + 1 == header_cols


def test_iif_signs_debit_positive_credit_negative(tmp_path: Path):
    entries = [
        JournalEntry(
            entry_date=__import__("datetime").date(2026, 1, 4),
            memo="m",
            source_id="x",
            source_type="t",
            lines=[
                JournalLine(account="Cash", debit=Decimal("50.00")),
                JournalLine(account="Income", credit=Decimal("50.00")),
            ],
        ),
    ]
    out = tmp_path / "j.iif"
    to_iif(entries, out)
    lines = out.read_text(encoding="ascii").splitlines()
    trns_line = next(ln for ln in lines if ln.startswith("TRNS\t"))
    spl_line = next(ln for ln in lines if ln.startswith("SPL\t"))
    assert "50.00" in trns_line and "-50.00" not in trns_line
    assert "-50.00" in spl_line
