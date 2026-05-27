"""Export journal entries to CSV / JSON / IIF / pretty table."""

import csv
import json
from decimal import Decimal
from pathlib import Path
from typing import List, TextIO

from rich.console import Console
from rich.table import Table

from stripe_bookkeeper.models import JournalEntry


def to_csv(entries: List[JournalEntry], path: Path) -> None:
    with open(path, "w", newline="", encoding="utf-8") as fh:
        writer = csv.writer(fh)
        writer.writerow(["date", "account", "debit", "credit", "memo", "source_id", "source_type"])
        for entry in entries:
            for line in entry.lines:
                writer.writerow(
                    [
                        entry.entry_date.isoformat(),
                        line.account,
                        f"{line.debit:.2f}" if line.debit else "",
                        f"{line.credit:.2f}" if line.credit else "",
                        entry.memo,
                        entry.source_id,
                        entry.source_type,
                    ]
                )


def _sanitize_iif_field(value: str) -> str:
    """IIF is tab-delimited and historically Windows-1252. Strip tabs/newlines, drop non-printable."""
    return "".join(c if 32 <= ord(c) < 127 else " " for c in str(value).replace("\t", " ").replace("\n", " "))


def to_iif(entries: List[JournalEntry], path: Path) -> None:
    """Write entries as a QuickBooks Desktop IIF file (one GENERAL JOURNAL per entry).

    IIF conventions:
      - Tab-delimited, CRLF line endings, ASCII
      - Debits are positive amounts, credits are negative — every TRNS block sums to zero
      - First line of each entry is TRNS; subsequent lines are SPL; the block ends with ENDTRNS
    """
    header_trns = "\t".join(
        ["!TRNS", "TRNSID", "TRNSTYPE", "DATE", "ACCNT", "NAME", "AMOUNT", "DOCNUM", "MEMO", "CLEAR"]
    )
    header_spl = "\t".join(
        ["!SPL", "SPLID", "TRNSTYPE", "DATE", "ACCNT", "NAME", "AMOUNT", "DOCNUM", "MEMO", "CLEAR"]
    )
    header_end = "!ENDTRNS"

    out_lines: List[str] = [header_trns, header_spl, header_end]

    for entry in entries:
        if not entry.lines:
            continue
        date_str = f"{entry.entry_date.month}/{entry.entry_date.day}/{entry.entry_date.year}"
        memo = _sanitize_iif_field(entry.memo)
        docnum = _sanitize_iif_field(entry.source_id)

        signed_pairs = [
            (line.account, line.debit if line.debit else -line.credit)
            for line in entry.lines
        ]

        first_acct, first_amt = signed_pairs[0]
        out_lines.append(
            "\t".join(
                [
                    "TRNS",
                    "",
                    "GENERAL JOURNAL",
                    date_str,
                    _sanitize_iif_field(first_acct),
                    "",
                    f"{first_amt:.2f}",
                    docnum,
                    memo,
                    "N",
                ]
            )
        )
        for acct, amt in signed_pairs[1:]:
            out_lines.append(
                "\t".join(
                    [
                        "SPL",
                        "",
                        "GENERAL JOURNAL",
                        date_str,
                        _sanitize_iif_field(acct),
                        "",
                        f"{amt:.2f}",
                        docnum,
                        memo,
                        "N",
                    ]
                )
            )
        out_lines.append("ENDTRNS")

    with open(path, "w", encoding="ascii", errors="replace", newline="") as fh:
        fh.write("\r\n".join(out_lines) + "\r\n")


def to_json(entries: List[JournalEntry], path: Path) -> None:
    payload = [
        {
            "date": e.entry_date.isoformat(),
            "memo": e.memo,
            "source_id": e.source_id,
            "source_type": e.source_type,
            "lines": [
                {
                    "account": l.account,
                    "debit": str(l.debit) if l.debit else "0",
                    "credit": str(l.credit) if l.credit else "0",
                }
                for l in e.lines
            ],
        }
        for e in entries
    ]
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, indent=2)


def print_table(entries: List[JournalEntry], console: Console) -> None:
    if not entries:
        console.print("[yellow]No journal entries produced.[/yellow]")
        return

    table = Table(title="Journal Entries", show_lines=False)
    table.add_column("Date", style="cyan", no_wrap=True)
    table.add_column("Account", style="white")
    table.add_column("Debit", justify="right", style="green")
    table.add_column("Credit", justify="right", style="red")
    table.add_column("Memo", style="dim")

    total_debit = Decimal("0")
    total_credit = Decimal("0")

    for entry in entries:
        first = True
        for line in entry.lines:
            table.add_row(
                entry.entry_date.isoformat() if first else "",
                ("  " if line.credit else "") + line.account,
                f"${line.debit:,.2f}" if line.debit else "",
                f"${line.credit:,.2f}" if line.credit else "",
                entry.memo if first else "",
            )
            total_debit += line.debit
            total_credit += line.credit
            first = False
        table.add_row("", "", "", "", "")

    console.print(table)

    balanced = total_debit == total_credit
    status = "[green]Balanced  OK[/green]" if balanced else "[red]UNBALANCED[/red]"
    console.print(
        f"{status}   Total DR [green]${total_debit:,.2f}[/green]  =  "
        f"Total CR [red]${total_credit:,.2f}[/red]"
    )
