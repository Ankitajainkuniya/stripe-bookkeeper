"""Live Stripe API fetcher.

Pulls events between two timestamps and yields the raw event payloads. The
caller is responsible for converting them to journal entries via
`rules.event_to_entries`.
"""

import os
from datetime import datetime, timezone
from typing import Iterator, Optional


def _to_unix(value: str) -> int:
    """Parse YYYY-MM-DD into a UTC unix timestamp (start of day)."""
    dt = datetime.strptime(value, "%Y-%m-%d").replace(tzinfo=timezone.utc)
    return int(dt.timestamp())


def fetch_events(
    since: str,
    until: Optional[str] = None,
    api_key: Optional[str] = None,
    types: Optional[list] = None,
) -> Iterator[dict]:
    """Yield raw Stripe Event objects (as dicts) in the given window.

    Requires `stripe` package and a valid API key.
    """
    try:
        import stripe
    except ImportError as e:
        raise RuntimeError("`stripe` package not installed. Run: pip install stripe") from e

    stripe.api_key = api_key or os.environ.get("STRIPE_API_KEY")
    if not stripe.api_key:
        raise RuntimeError(
            "No Stripe API key found. Set STRIPE_API_KEY in your environment "
            "or pass --api-key. For offline testing use `stripe-bookkeeper demo`."
        )

    created_filter = {"gte": _to_unix(since)}
    if until:
        created_filter["lte"] = _to_unix(until)

    list_kwargs = {"created": created_filter, "limit": 100}
    if types:
        list_kwargs["types"] = types

    for event in stripe.Event.list(**list_kwargs).auto_paging_iter():
        yield event.to_dict() if hasattr(event, "to_dict") else dict(event)
