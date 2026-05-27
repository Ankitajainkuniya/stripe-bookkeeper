"""Chart of accounts — default GAAP-style SaaS chart, override via YAML."""

from dataclasses import dataclass
from pathlib import Path
from typing import Optional

import yaml


DEFAULT_COA = {
    "revenue": "Revenue",
    "deferred_revenue": "Deferred Revenue",
    "stripe_clearing": "Stripe Clearing",
    "payment_processing_fees": "Payment Processing Fees",
    "bank": "Bank",
    "refunds_contra": "Refunds Contra-Revenue",
    "disputes_pending": "Disputes Pending",
}


@dataclass
class ChartOfAccounts:
    revenue: str = DEFAULT_COA["revenue"]
    deferred_revenue: str = DEFAULT_COA["deferred_revenue"]
    stripe_clearing: str = DEFAULT_COA["stripe_clearing"]
    payment_processing_fees: str = DEFAULT_COA["payment_processing_fees"]
    bank: str = DEFAULT_COA["bank"]
    refunds_contra: str = DEFAULT_COA["refunds_contra"]
    disputes_pending: str = DEFAULT_COA["disputes_pending"]

    @classmethod
    def from_yaml(cls, path: Path) -> "ChartOfAccounts":
        data = yaml.safe_load(Path(path).read_text())
        if not isinstance(data, dict):
            raise ValueError(f"{path} must be a YAML mapping")
        return cls(**{k: data.get(k, v) for k, v in DEFAULT_COA.items()})
