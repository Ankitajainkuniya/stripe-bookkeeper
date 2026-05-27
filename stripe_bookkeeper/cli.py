"""stripe-bookkeeper CLI."""

import json
from pathlib import Path
from typing import Optional

import typer
from rich.console import Console

from stripe_bookkeeper import __version__
from stripe_bookkeeper.chart_of_accounts import ChartOfAccounts
from stripe_bookkeeper.exporters import print_table, to_csv, to_iif, to_json
from stripe_bookkeeper.rules import event_to_entries

app = typer.Typer(
    help="Convert Stripe activity into GAAP-compliant double-entry journal entries.",
    add_completion=False,
    no_args_is_help=True,
)
console = Console()


def _load_coa(coa_path: Optional[Path]) -> ChartOfAccounts:
    if coa_path:
        return ChartOfAccounts.from_yaml(coa_path)
    return ChartOfAccounts()


def _process_events(events, coa: ChartOfAccounts):
    entries = []
    for event in events:
        entries.extend(event_to_entries(event, coa))
    entries.sort(key=lambda e: (e.entry_date, e.source_id))
    return entries


@app.command()
def demo(
    csv_out: Optional[Path] = typer.Option(None, "--csv", help="Write CSV journal to this path."),
    json_out: Optional[Path] = typer.Option(None, "--json", help="Write JSON journal to this path."),
    iif_out: Optional[Path] = typer.Option(None, "--iif", help="Write QuickBooks Desktop IIF file to this path."),
    coa: Optional[Path] = typer.Option(None, "--coa", help="Custom chart of accounts (YAML)."),
) -> None:
    """Run on bundled sample Stripe events — no API key needed."""
    console.print("[bold cyan]Stripe Bookkeeper[/bold cyan] — demo mode (no API key needed)\n")

    fixtures_path = Path(__file__).parent / "fixtures" / "demo_events.json"
    events = json.loads(fixtures_path.read_text())

    coa_obj = _load_coa(coa)
    entries = _process_events(events, coa_obj)

    print_table(entries, console)

    if csv_out:
        to_csv(entries, csv_out)
        console.print(f"\n[green]Wrote CSV:[/green] {csv_out}")
    if json_out:
        to_json(entries, json_out)
        console.print(f"[green]Wrote JSON:[/green] {json_out}")
    if iif_out:
        to_iif(entries, iif_out)
        console.print(f"[green]Wrote IIF:[/green] {iif_out}  (import via QuickBooks Desktop → File → Utilities → Import → IIF Files)")


@app.command()
def sync(
    since: str = typer.Option(..., "--since", help="Start date YYYY-MM-DD (UTC)."),
    until: Optional[str] = typer.Option(None, "--until", help="End date YYYY-MM-DD (UTC)."),
    api_key: Optional[str] = typer.Option(
        None, "--api-key", help="Stripe API key (or set STRIPE_API_KEY in env)."
    ),
    csv_out: Optional[Path] = typer.Option(None, "--csv", help="Write CSV journal to this path."),
    json_out: Optional[Path] = typer.Option(None, "--json", help="Write JSON journal to this path."),
    iif_out: Optional[Path] = typer.Option(None, "--iif", help="Write QuickBooks Desktop IIF file to this path."),
    coa: Optional[Path] = typer.Option(None, "--coa", help="Custom chart of accounts (YAML)."),
) -> None:
    """Pull Stripe events from your live account and convert to journal entries."""
    from stripe_bookkeeper.stripe_source import fetch_events

    console.print(
        f"[bold cyan]Stripe Bookkeeper[/bold cyan] — syncing events from "
        f"[cyan]{since}[/cyan] to [cyan]{until or 'now'}[/cyan]\n"
    )

    coa_obj = _load_coa(coa)
    events = list(
        fetch_events(
            since=since,
            until=until,
            api_key=api_key,
            types=list(__import__("stripe_bookkeeper.rules", fromlist=["EVENT_HANDLERS"]).EVENT_HANDLERS.keys()),
        )
    )
    console.print(f"Fetched [green]{len(events)}[/green] Stripe events.\n")

    entries = _process_events(events, coa_obj)
    print_table(entries, console)

    if csv_out:
        to_csv(entries, csv_out)
        console.print(f"\n[green]Wrote CSV:[/green] {csv_out}")
    if json_out:
        to_json(entries, json_out)
        console.print(f"[green]Wrote JSON:[/green] {json_out}")
    if iif_out:
        to_iif(entries, iif_out)
        console.print(f"[green]Wrote IIF:[/green] {iif_out}  (import via QuickBooks Desktop → File → Utilities → Import → IIF Files)")


@app.command()
def version() -> None:
    """Print the installed version."""
    console.print(f"stripe-bookkeeper {__version__}")


if __name__ == "__main__":
    app()
