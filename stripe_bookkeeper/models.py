"""Journal entry data model — minimal, stdlib-only."""

from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from typing import List, Optional


@dataclass
class JournalLine:
    account: str
    debit: Decimal = Decimal("0")
    credit: Decimal = Decimal("0")

    def __post_init__(self) -> None:
        if self.debit < 0 or self.credit < 0:
            raise ValueError("debit and credit must be non-negative")
        if self.debit > 0 and self.credit > 0:
            raise ValueError("a single line cannot have both debit and credit")
        if self.debit == 0 and self.credit == 0:
            raise ValueError("a line must have either a debit or a credit")


@dataclass
class JournalEntry:
    """A balanced double-entry journal entry."""

    entry_date: date
    memo: str
    source_id: str
    source_type: str
    lines: List[JournalLine] = field(default_factory=list)

    def is_balanced(self) -> bool:
        total_debit = sum((l.debit for l in self.lines), Decimal("0"))
        total_credit = sum((l.credit for l in self.lines), Decimal("0"))
        return total_debit == total_credit and total_debit > 0

    def total_debit(self) -> Decimal:
        return sum((l.debit for l in self.lines), Decimal("0"))

    def total_credit(self) -> Decimal:
        return sum((l.credit for l in self.lines), Decimal("0"))
