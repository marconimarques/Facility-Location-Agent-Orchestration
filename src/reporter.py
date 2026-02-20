"""Report generation: baseline markdown and what-if comparison reports."""

import re
from datetime import datetime
from pathlib import Path

from .models import BaselineResult, WhatIfResult


def generate_markdown_report(
    result: BaselineResult,
    output_path: str,
    scenario_name: str = "Baseline",
) -> None:
    """Generate a detailed markdown report for a baseline (or any single) solution.

    Args:
        result: BaselineResult with full solution details.
        output_path: File path to write the report to.
        scenario_name: Label used in the report title.
    """
    costs = result.costs

    report = f"""# Logistics Optimization Report

## Scenario: {scenario_name}

**Generated:** {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}

---

## Optimal Solution

### Facility Location
**Selected Site:** {result.facility_location}

### Production Summary"""

    ports = result.selected_ports
    if len(ports) == 1:
        report += f"\n- **Selected Port:** {ports[0]}"
    elif len(ports) > 1:
        report += f"\n- **Selected Ports:** {', '.join(ports)}"

    report += f"""
- **Total Finished Product Produced:** {result.total_finished_product_tons:,.2f} tons
- **Total Raw Material Consumed:** {result.total_raw_material_tons:,.2f} tons
- **Average Yield Factor:** {result.avg_yield_factor:.2%}

---

## Cost Breakdown

| Component | Total Cost ($) | Per Ton ($/t) | % of Total |
|-----------|----------------|---------------|------------|
| Raw Materials (avg) | ${costs['raw_material_total']:,.2f} | ${costs['raw_material_per_ton']:.2f} | {costs['raw_material_total']/costs['total_cost']*100:.1f}% |
| Inbound Freight | ${costs['inbound_freight_total']:,.2f} | ${costs['inbound_freight_per_ton']:.2f} | {costs['inbound_freight_total']/costs['total_cost']*100:.1f}% |
| Outbound Freight | ${costs['outbound_freight_total']:,.2f} | ${costs['outbound_freight_per_ton']:.2f} | {costs['outbound_freight_total']/costs['total_cost']*100:.1f}% |
| Port Operations | ${costs['port_operational_total']:,.2f} | ${costs['port_operational_per_ton']:.2f} | {costs['port_operational_total']/costs['total_cost']*100:.1f}% |
| Sea Freight | ${costs['sea_freight_total']:,.2f} | ${costs['sea_freight_per_ton']:.2f} | {costs['sea_freight_total']/costs['total_cost']*100:.1f}% |
| **TOTAL** | **${costs['total_cost']:,.2f}** | **${costs['total_cost']/result.total_finished_product_tons:.2f}** | **100.0%** |

---

## Raw Material Sourcing Breakdown

| Collection Point | Total (t) | Mat A | Mat B | Mat C | Mat D | Mat E | % Total |
|-----------------|-----------|-------|-------|-------|-------|-------|---------|
"""

    # Build source-by-material matrix from procurement_details
    facility = result.facility_location
    source_material_matrix = {}

    for (s1, s2, m), qty in result.procurement_details.items():
        if s2 == facility:  # Only materials going to the facility
            if s1 not in source_material_matrix:
                source_material_matrix[s1] = {'A': 0, 'B': 0, 'C': 0, 'D': 0, 'E': 0, 'total': 0}
            source_material_matrix[s1][m] += qty
            source_material_matrix[s1]['total'] += qty

    sorted_sources = sorted(
        source_material_matrix.items(),
        key=lambda x: x[1]['total'],
        reverse=True,
    )

    total_raw = result.total_raw_material_tons

    for source, materials in sorted_sources:
        source_total = materials['total']
        source_pct = (source_total / total_raw * 100) if total_raw > 0 else 0

        mat_a = f"{materials['A']:,.0f}" if materials['A'] > 0.5 else "-"
        mat_b = f"{materials['B']:,.0f}" if materials['B'] > 0.5 else "-"
        mat_c = f"{materials['C']:,.0f}" if materials['C'] > 0.5 else "-"
        mat_d = f"{materials['D']:,.0f}" if materials['D'] > 0.5 else "-"
        mat_e = f"{materials['E']:,.0f}" if materials['E'] > 0.5 else "-"

        report += f"| {source} | {source_total:,.0f} | {mat_a} | {mat_b} | {mat_c} | {mat_d} | {mat_e} | {source_pct:.1f}% |\n"

    totals_by_type = {
        'A': result.raw_material_by_type.get('RawMaterialA', 0),
        'B': result.raw_material_by_type.get('RawMaterialB', 0),
        'C': result.raw_material_by_type.get('RawMaterialC', 0),
        'D': result.raw_material_by_type.get('RawMaterialD', 0),
        'E': result.raw_material_by_type.get('RawMaterialE', 0),
    }

    report += (
        f"| **TOTAL BY TYPE** | **{total_raw:,.0f}** "
        f"| **{totals_by_type['A']:,.0f}** | **{totals_by_type['B']:,.0f}** "
        f"| **{totals_by_type['C']:,.0f}** | **{totals_by_type['D']:,.0f}** "
        f"| **{totals_by_type['E']:,.0f}** | **100.0%** |\n"
    )

    pct_a = (totals_by_type['A'] / total_raw * 100) if total_raw > 0 else 0
    pct_b = (totals_by_type['B'] / total_raw * 100) if total_raw > 0 else 0
    pct_c = (totals_by_type['C'] / total_raw * 100) if total_raw > 0 else 0
    pct_d = (totals_by_type['D'] / total_raw * 100) if total_raw > 0 else 0
    pct_e = (totals_by_type['E'] / total_raw * 100) if total_raw > 0 else 0

    report += (
        f"| *% of Total* | *100.0%* | *{pct_a:.1f}%* | *{pct_b:.1f}%* "
        f"| *{pct_c:.1f}%* | *{pct_d:.1f}%* | *{pct_e:.1f}%* | |\n"
    )

    report += f"""
---

## Port Shipments

| Facility | Port | Tons Shipped |
|----------|------|-------------|
"""

    for (site, port), tons in sorted(result.port_shipments.items()):
        report += f"| {site} | {port} | {tons:,.2f} |\n"

    report += f"""
---

## Optimization Details

- **Solver:** HiGHS
- **Solve Time:** {result.solve_time_seconds:.2f} seconds
- **MIP Gap:** 1.00%

---

*Report generated by Logistics Optimizer*
"""

    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    with open(output_path, 'w', encoding='utf-8') as f:
        f.write(report)


def generate_whatif_report(
    result: WhatIfResult,
    output_path: str,
) -> None:
    """Generate a markdown comparison report for a what-if scenario.

    Args:
        result: WhatIfResult with both baseline and what-if solutions.
        output_path: File path to write the report to.
    """
    scenario_name = result.scenario_name
    modifications = result.modifications
    baseline_solution = result.baseline

    report = f"""# What-If Scenario Analysis Report

## Scenario: {scenario_name}

**Generated:** {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}

---

## Scenario Description

### Applied Modifications

"""

    for mod in modifications:
        report += f"- **{mod.parameter}**: {mod.description}\n"

    report += """

---

## Comparison: Baseline vs What-If

"""

    if not result.is_feasible:
        report += f"""**Status: INFEASIBLE**

This scenario could not be solved by the optimizer.

**Reason:** {result.infeasibility_reason or 'Unknown'}

---

*Report generated by Logistics Optimizer - What-If Analysis*
"""
        output_path = Path(output_path)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        with open(output_path, 'w', encoding='utf-8') as f:
            f.write(report)
        return

    whatif_solution = result.whatif

    report += """### Key Metrics

| Metric | Baseline | What-If | Change | % Change |
|--------|----------|---------|--------|----------|
"""

    # Facility location
    baseline_fac = baseline_solution.facility_location
    whatif_fac = whatif_solution.facility_location
    report += f"| Facility Location | {baseline_fac} | {whatif_fac} | {'No Change' if baseline_fac == whatif_fac else 'Changed'} | - |\n"

    # Total cost
    baseline_cost = baseline_solution.total_cost
    whatif_cost = whatif_solution.total_cost
    cost_diff = whatif_cost - baseline_cost
    cost_pct = (cost_diff / baseline_cost * 100) if baseline_cost > 0 else 0
    report += f"| Total Cost | ${baseline_cost:,.2f} | ${whatif_cost:,.2f} | ${cost_diff:+,.2f} | {cost_pct:+.1f}% |\n"

    # Cost per ton
    baseline_cpt = baseline_cost / baseline_solution.total_finished_product_tons
    whatif_cpt = whatif_cost / whatif_solution.total_finished_product_tons
    cpt_diff = whatif_cpt - baseline_cpt
    cpt_pct = (cpt_diff / baseline_cpt * 100) if baseline_cpt > 0 else 0
    report += f"| Cost per Ton | ${baseline_cpt:.2f} | ${whatif_cpt:.2f} | ${cpt_diff:+.2f} | {cpt_pct:+.1f}% |\n"

    # Production
    baseline_prod = baseline_solution.total_finished_product_tons
    whatif_prod = whatif_solution.total_finished_product_tons
    prod_diff = whatif_prod - baseline_prod
    prod_pct = (prod_diff / baseline_prod * 100) if baseline_prod > 0 else 0
    report += f"| Production (tons) | {baseline_prod:,.2f} | {whatif_prod:,.2f} | {prod_diff:+,.2f} | {prod_pct:+.1f}% |\n"

    # Raw material
    baseline_raw = baseline_solution.total_raw_material_tons
    whatif_raw = whatif_solution.total_raw_material_tons
    raw_diff = whatif_raw - baseline_raw
    raw_pct = (raw_diff / baseline_raw * 100) if baseline_raw > 0 else 0
    report += f"| Raw Material (tons) | {baseline_raw:,.2f} | {whatif_raw:,.2f} | {raw_diff:+,.2f} | {raw_pct:+.1f}% |\n"

    # Yield
    baseline_yield = baseline_solution.avg_yield_factor
    whatif_yield = whatif_solution.avg_yield_factor
    yield_diff = whatif_yield - baseline_yield
    report += f"| Avg Yield Factor | {baseline_yield:.2%} | {whatif_yield:.2%} | {yield_diff:+.2%} | - |\n"

    report += """

---

## Cost Breakdown Comparison

| Component | Baseline | What-If | Change | % Change |
|-----------|----------|---------|--------|----------|
"""

    components = [
        ("Raw Materials", 'raw_material_total'),
        ("Inbound Freight", 'inbound_freight_total'),
        ("Outbound Freight", 'outbound_freight_total'),
        ("Port Operations", 'port_operational_total'),
        ("Sea Freight", 'sea_freight_total'),
    ]

    for name, key in components:
        base_val = baseline_solution.costs[key]
        what_val = whatif_solution.costs[key]
        diff = what_val - base_val
        pct = (diff / base_val * 100) if base_val > 0 else 0
        report += f"| {name} | ${base_val:,.2f} | ${what_val:,.2f} | ${diff:+,.2f} | {pct:+.1f}% |\n"

    report += f"""

---

## What-If Solution Details

### Facility & Ports
- **Facility Location:** {whatif_solution.facility_location}
- **Selected Ports:** {', '.join(whatif_solution.selected_ports)}

### Production Summary
- **Total Finished Product Produced:** {whatif_solution.total_finished_product_tons:,.2f} tons
- **Total Raw Material Consumed:** {whatif_solution.total_raw_material_tons:,.2f} tons
- **Average Yield Factor:** {whatif_solution.avg_yield_factor:.2%}

---

## Raw Material Consumption by Type

| Material | Baseline (tons) | What-If (tons) | Change |
|----------|----------------|----------------|--------|
"""

    for mat_type in ['RawMaterialA', 'RawMaterialB', 'RawMaterialC', 'RawMaterialD', 'RawMaterialE']:
        base_tons = baseline_solution.raw_material_by_type.get(mat_type, 0.0)
        what_tons = whatif_solution.raw_material_by_type.get(mat_type, 0.0)
        diff = what_tons - base_tons
        report += f"| {mat_type} | {base_tons:,.2f} | {what_tons:,.2f} | {diff:+,.2f} |\n"

    report += """

---

*Report generated by Logistics Optimizer - What-If Analysis*
"""

    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    with open(output_path, 'w', encoding='utf-8') as f:
        f.write(report)


def get_next_version_number(results_dir: Path) -> int:
    """Scan results directory and return next available version number.

    Args:
        results_dir: Path to the results directory.

    Returns:
        Next version number (1, 2, 3, ...).
    """
    if not results_dir.exists():
        results_dir.mkdir(parents=True, exist_ok=True)
        return 1

    pattern = re.compile(r'whatif_output_v(\d+)\.md')
    max_version = 0

    for file in results_dir.glob('whatif_output_v*.md'):
        match = pattern.match(file.name)
        if match:
            version = int(match.group(1))
            max_version = max(max_version, version)

    return max_version + 1
