"""Pure Rich display layer — no business logic, no solver or Anthropic imports."""

from contextlib import contextmanager
from typing import Iterator

from rich.console import Console
from rich.panel import Panel
from rich.progress import Progress, SpinnerColumn, TextColumn
from rich.prompt import Confirm, Prompt
from rich.table import Table

from .data_loader import OptimizationData
from .models import BaselineResult, WhatIfResult

console = Console()


def show_welcome() -> None:
    console.print()
    console.print(Panel.fit(
        "[bold cyan]LOGISTICS FACILITY LOCATION OPTIMIZER[/bold cyan]\n\n"
        "Powered by AI + Pyomo/HiGHS MILP solver.\n"
        "Optimizes facility location, raw material sourcing,\n"
        "and finished-product shipping through export ports.",
        border_style="cyan",
    ))
    console.print()


def show_model_info(data: OptimizationData) -> None:
    table = Table(show_header=False, box=None, padding=(0, 2))
    table.add_column(style="cyan")
    table.add_column()
    table.add_row("Model:", "claude-sonnet-4-6")
    table.add_row("Solver:", "Pyomo + HiGHS (MILP, two-phase)")
    table.add_row("Collection Points:", str(len(data.collection_points)))
    table.add_row("Export Ports:", str(len(data.ports)))
    table.add_row(
        "Production Target:",
        f"{data.production_params.target_tons:,.0f} tons"
    )
    console.print(table)
    console.print()


def prompt_user_message() -> str:
    return Prompt.ask("[bold cyan]You[/bold cyan]")


@contextmanager
def show_thinking() -> Iterator[Progress]:
    with Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        console=console,
        transient=True,
    ) as progress:
        yield progress


def show_baseline_result(result: BaselineResult) -> None:
    """Display a baseline optimization result as Rich tables."""
    costs = result.costs

    # Header panel
    console.print(Panel.fit(
        f"[bold green]BASELINE SOLUTION[/bold green]\n\n"
        f"[bold]Facility:[/bold] [cyan]{result.facility_location}[/cyan]\n"
        f"[bold]Ports:[/bold] [cyan]{', '.join(result.selected_ports)}[/cyan]\n"
        f"[bold]Solve Time:[/bold] {result.solve_time_seconds:.2f}s",
        border_style="green",
    ))

    # Production summary
    summary = Table(show_header=False, box=None, padding=(0, 2))
    summary.add_column(style="cyan")
    summary.add_column(justify="right")
    summary.add_row("Finished Product:", f"{result.total_finished_product_tons:,.2f} tons")
    summary.add_row("Raw Material Used:", f"{result.total_raw_material_tons:,.2f} tons")
    summary.add_row("Avg Yield Factor:", f"{result.avg_yield_factor:.2%}")
    summary.add_row("Total Cost:", f"${costs['total_cost']:,.2f}")
    summary.add_row("Cost per Ton:", f"${costs['total_cost'] / result.total_finished_product_tons:.2f}")
    console.print(summary)
    console.print()

    # Cost breakdown table
    console.print("[bold]Cost Breakdown:[/bold]")
    cost_table = Table(show_header=True, box=None, padding=(0, 2))
    cost_table.add_column("Component", style="cyan")
    cost_table.add_column("Total ($)", justify="right")
    cost_table.add_column("Per Ton ($/t)", justify="right")
    cost_table.add_column("% of Total", justify="right")

    total_cost = costs['total_cost']
    components = [
        ("Raw Materials", 'raw_material_total', 'raw_material_per_ton'),
        ("Inbound Freight", 'inbound_freight_total', 'inbound_freight_per_ton'),
        ("Outbound Freight", 'outbound_freight_total', 'outbound_freight_per_ton'),
        ("Port Operations", 'port_operational_total', 'port_operational_per_ton'),
        ("Sea Freight", 'sea_freight_total', 'sea_freight_per_ton'),
    ]
    for label, tot_key, pt_key in components:
        val = costs[tot_key]
        pt = costs[pt_key]
        pct = (val / total_cost * 100) if total_cost > 0 else 0
        cost_table.add_row(label, f"${val:,.2f}", f"${pt:.2f}", f"{pct:.1f}%")

    cost_table.add_section()
    cost_table.add_row(
        "[bold]TOTAL[/bold]",
        f"[bold]${total_cost:,.2f}[/bold]",
        f"[bold]${total_cost / result.total_finished_product_tons:.2f}[/bold]",
        "[bold]100.0%[/bold]",
    )
    console.print(cost_table)
    console.print()

    # Raw material sourcing summary
    console.print("[bold]Raw Material by Type:[/bold]")
    mat_table = Table(show_header=True, box=None, padding=(0, 2))
    mat_table.add_column("Material", style="cyan")
    mat_table.add_column("Tons", justify="right")
    mat_table.add_column("% of Total", justify="right")
    total_raw = result.total_raw_material_tons
    for mat_key in ['RawMaterialA', 'RawMaterialB', 'RawMaterialC', 'RawMaterialD', 'RawMaterialE']:
        qty = result.raw_material_by_type.get(mat_key, 0.0)
        pct = (qty / total_raw * 100) if total_raw > 0 else 0
        mat_table.add_row(mat_key, f"{qty:,.2f}", f"{pct:.1f}%")
    console.print(mat_table)
    console.print()


def show_whatif_result(result: WhatIfResult) -> None:
    """Display a what-if scenario comparison as Rich tables."""
    if not result.is_feasible:
        console.print(Panel.fit(
            f"[bold red]WHAT-IF: INFEASIBLE[/bold red]\n\n"
            f"[bold]Scenario:[/bold] {result.scenario_name}\n\n"
            f"[yellow]{result.infeasibility_reason or 'No reason provided.'}[/yellow]",
            border_style="red",
        ))
        console.print()
        return

    bl = result.baseline
    wi = result.whatif

    # Header
    cost_color = "green" if (result.total_cost_change or 0) < 0 else "red"
    console.print(Panel.fit(
        f"[bold cyan]WHAT-IF COMPARISON: {result.scenario_name}[/bold cyan]",
        border_style="cyan",
    ))

    # Main metrics table
    metrics = Table(show_header=True, box=None, padding=(0, 2))
    metrics.add_column("Metric", style="cyan")
    metrics.add_column("Baseline", justify="right")
    metrics.add_column("What-If", justify="right")
    metrics.add_column("Change", justify="right")

    # Facility
    fac_change = "same" if bl.facility_location == wi.facility_location else f"-> {wi.facility_location}"
    metrics.add_row("Facility Location", bl.facility_location, wi.facility_location, fac_change)

    # Total cost
    cost_diff = result.total_cost_change or 0
    cost_pct = result.total_cost_change_pct or 0
    c_color = "green" if cost_diff < 0 else "red" if cost_diff > 0 else "yellow"
    metrics.add_row(
        "Total Cost",
        f"${bl.total_cost:,.2f}",
        f"${wi.total_cost:,.2f}",
        f"[{c_color}]{cost_diff:+,.2f} ({cost_pct:+.1f}%)[/{c_color}]",
    )

    # Cost per ton
    cpt_diff = result.cost_per_ton_change or 0
    bl_cpt = bl.total_cost / bl.total_finished_product_tons if bl.total_finished_product_tons > 0 else 0
    wi_cpt = wi.total_cost / wi.total_finished_product_tons if wi.total_finished_product_tons > 0 else 0
    cpt_pct = (cpt_diff / bl_cpt * 100) if bl_cpt > 0 else 0
    c_color = "green" if cpt_diff < 0 else "red" if cpt_diff > 0 else "yellow"
    metrics.add_row(
        "Cost per Ton",
        f"${bl_cpt:.2f}",
        f"${wi_cpt:.2f}",
        f"[{c_color}]{cpt_diff:+.2f} ({cpt_pct:+.1f}%)[/{c_color}]",
    )

    # Production
    prod_diff = wi.total_finished_product_tons - bl.total_finished_product_tons
    prod_pct = (prod_diff / bl.total_finished_product_tons * 100) if bl.total_finished_product_tons > 0 else 0
    metrics.add_row(
        "Production (tons)",
        f"{bl.total_finished_product_tons:,.2f}",
        f"{wi.total_finished_product_tons:,.2f}",
        f"{prod_diff:+,.2f} ({prod_pct:+.1f}%)",
    )

    # Raw material
    raw_diff = wi.total_raw_material_tons - bl.total_raw_material_tons
    raw_pct = (raw_diff / bl.total_raw_material_tons * 100) if bl.total_raw_material_tons > 0 else 0
    metrics.add_row(
        "Raw Material (tons)",
        f"{bl.total_raw_material_tons:,.2f}",
        f"{wi.total_raw_material_tons:,.2f}",
        f"{raw_diff:+,.2f} ({raw_pct:+.1f}%)",
    )

    # Avg yield
    yield_diff = wi.avg_yield_factor - bl.avg_yield_factor
    metrics.add_row(
        "Avg Yield Factor",
        f"{bl.avg_yield_factor:.2%}",
        f"{wi.avg_yield_factor:.2%}",
        f"{yield_diff:+.2%}",
    )

    console.print(metrics)
    console.print()

    # Cost component breakdown
    console.print("[bold]Cost Component Changes:[/bold]")
    cost_table = Table(show_header=True, box=None, padding=(0, 2))
    cost_table.add_column("Component", style="cyan")
    cost_table.add_column("Baseline ($)", justify="right")
    cost_table.add_column("Base ($/t)", justify="right")
    cost_table.add_column("What-If ($)", justify="right")
    cost_table.add_column("W-I ($/t)", justify="right")
    cost_table.add_column("Change", justify="right")

    bl_raw = bl.total_raw_material_tons
    wi_raw = wi.total_raw_material_tons
    bl_prod = bl.total_finished_product_tons
    wi_prod = wi.total_finished_product_tons

    components = [
        ("Raw Materials", 'raw_material_total', 'raw'),
        ("Inbound Freight", 'inbound_freight_total', 'raw'),
        ("Outbound Freight", 'outbound_freight_total', 'product'),
        ("Port Operations", 'port_operational_total', 'product'),
        ("Sea Freight", 'sea_freight_total', 'product'),
    ]

    for name, key, basis in components:
        base_val = bl.costs[key]
        what_val = wi.costs[key]
        diff = what_val - base_val
        pct = (diff / base_val * 100) if base_val > 0 else 0
        c_color = "green" if diff < 0 else "red" if diff > 0 else "yellow"

        if basis == 'raw':
            base_pt = base_val / bl_raw if bl_raw > 0 else 0
            what_pt = what_val / wi_raw if wi_raw > 0 else 0
        else:
            base_pt = base_val / bl_prod if bl_prod > 0 else 0
            what_pt = what_val / wi_prod if wi_prod > 0 else 0

        cost_table.add_row(
            name,
            f"${base_val:,.2f}",
            f"${base_pt:.2f}",
            f"${what_val:,.2f}",
            f"${what_pt:.2f}",
            f"[{c_color}]{diff:+,.2f} ({pct:+.1f}%)[/{c_color}]",
        )

    console.print(cost_table)
    console.print()

    # Port changes if any
    bl_ports = set(bl.selected_ports)
    wi_ports = set(wi.selected_ports)
    if bl_ports != wi_ports:
        removed = bl_ports - wi_ports
        added = wi_ports - bl_ports
        console.print("[bold]Port Selection Changes:[/bold]")
        console.print(f"  Baseline: {', '.join(sorted(bl_ports))}")
        console.print(f"  What-If:  {', '.join(sorted(wi_ports))}")
        if removed:
            console.print(f"  Removed: [red]{', '.join(sorted(removed))}[/red]")
        if added:
            console.print(f"  Added:   [green]{', '.join(sorted(added))}[/green]")
        console.print()


def show_sites(data: OptimizationData) -> None:
    """Display all available collection point site IDs and export ports."""
    sites_table = Table(show_header=True, box=None, padding=(0, 2))
    sites_table.add_column("#", style="dim", justify="right")
    sites_table.add_column("Site ID", style="cyan")
    sites_table.add_column("Company")
    sites_table.add_column("Plant")

    for i, cp in enumerate(data.collection_points, 1):
        sites_table.add_row(str(i), cp.site_id, cp.company, cp.plant)

    console.print(Panel.fit(sites_table, title="[bold green]Available Sites[/bold green]", border_style="green"))
    console.print()

    ports_table = Table(show_header=True, box=None, padding=(0, 2))
    ports_table.add_column("#", style="dim", justify="right")
    ports_table.add_column("Port Name", style="cyan")
    ports_table.add_column("Op. Cost ($/t)", justify="right")
    ports_table.add_column("Sea Freight ($/t)", justify="right")

    for i, port in enumerate(data.ports, 1):
        ports_table.add_row(
            str(i),
            port.port_name,
            f"${port.operational_cost:,.2f}",
            f"${port.sea_freight_cost:,.2f}",
        )

    console.print(Panel.fit(ports_table, title="[bold green]Available Ports[/bold green]", border_style="green"))
    console.print()


def show_claude_response(text: str) -> None:
    if not text:
        return
    console.print(f"[bold cyan]Assistant:[/bold cyan] {text}")
    console.print()


def show_error(msg: str) -> None:
    console.print(f"\n[bold red]Error:[/bold red] {msg}\n")


def show_warning(msg: str) -> None:
    console.print(f"[yellow]{msg}[/yellow]")


def show_cancellation() -> None:
    console.print("\n[yellow]Interrupted. Goodbye![/yellow]\n")


def show_help() -> None:
    content = (
        "[bold]Available commands:[/bold]\n"
        "  [cyan]help[/cyan]   — Show this message\n"
        "  [cyan]list[/cyan]   — Show all available site IDs\n"
        "  [cyan]clear[/cyan]  — Reset conversation history\n"
        "  [cyan]quit[/cyan]   — Exit\n\n"
        "[bold]Example queries:[/bold]\n\n"
        "[dim]Baseline:[/dim]\n"
        '  "Run baseline optimization"\n'
        '  "What is the optimal facility location?"\n\n'
        "[dim]What-if scenarios:[/dim]\n"
        '  "What if inbound freight costs increase by 20%?"\n'
        '  "What if production target is 220,000 tons?"\n'
        '  "What if we force the facility to SiteX?"\n'
        '  "What if MaterialE yield improves to 22%?"\n\n'
        "[dim]Comparative analysis:[/dim]\n"
        '  "Compare +20% inbound freight vs +20% sea freight"\n'
        '  "What scenario gives lower costs: reduce target 10% or improve yield 5%?"\n\n'
        "[dim]Infeasibility exploration:[/dim]\n"
        '  "What if production target is 500,000 tons?"\n'
        '  "What happens if we restrict to Port_A only?"'
    )
    console.print(Panel.fit(content, title="[bold green]Help[/bold green]", border_style="green"))
    console.print()


def confirm_clear_history() -> bool:
    return Confirm.ask("[yellow]Clear conversation history?[/yellow]", default=False)
