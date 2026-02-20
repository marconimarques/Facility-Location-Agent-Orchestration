"""Solver execution and solution extraction module."""

from pyomo.environ import *
from pyomo.opt import TerminationCondition
from typing import Dict
import time


def solve_optimization(
    model: ConcreteModel,
    solver_name: str = 'highs',
    time_limit: int = 300,
    mip_gap: float = 0.01
) -> Dict:
    """Solve optimization model and return solution.

    Args:
        model: Pyomo ConcreteModel
        solver_name: Solver to use ('highs', 'gurobi', 'cplex')
        time_limit: Maximum solve time in seconds
        mip_gap: MIP optimality gap tolerance

    Returns:
        Dictionary with solution details

    Raises:
        RuntimeError: If model is infeasible or solver fails
    """
    solver = SolverFactory(solver_name)

    if not solver.available():
        raise RuntimeError(
            f"{solver_name} solver not available. "
            f"Install via: pip install highspy"
        )

    # Set solver options
    if solver_name == 'highs':
        solver.options['time_limit'] = time_limit
        solver.options['mip_rel_gap'] = mip_gap
    elif solver_name in ['gurobi', 'cplex']:
        solver.options['TimeLimit'] = time_limit
        solver.options['MIPGap'] = mip_gap

    # Solve (tee=False suppresses solver output)
    start_time = time.time()
    results = solver.solve(model, tee=False)
    solve_time = time.time() - start_time

    # Check termination condition
    if results.solver.termination_condition == TerminationCondition.infeasible:
        raise RuntimeError(
            "Model is INFEASIBLE. Possible causes:\n"
            "  - Insufficient raw material volumes to meet production target\n"
            "  - Constraint conflicts (e.g., yield factors + max consumption)\n"
            "  - Data quality issues"
        )

    if results.solver.termination_condition != TerminationCondition.optimal:
        raise RuntimeError(
            f"Solver terminated with condition: {results.solver.termination_condition}\n"
            f"Status: {results.solver.status}"
        )

    # Extract solution
    solution = extract_solution(model, solve_time)

    return solution


def extract_solution(model: ConcreteModel, solve_time: float) -> Dict:
    """Extract solution values from solved model.

    Args:
        model: Solved Pyomo model
        solve_time: Time taken to solve (seconds)

    Returns:
        Dictionary with complete solution details
    """
    # Find selected facility
    facility_location = None
    for s in model.Sites:
        if value(model.y[s]) > 0.5:
            facility_location = s
            break

    # Extract procurement volumes (filter negligible amounts)
    procurement_by_source_and_material = {}
    total_raw_material = 0.0

    for s1 in model.Sites:
        for s2 in model.Sites:
            for m in model.Materials:
                qty = value(model.procure[s1, s2, m])
                if qty > 0.01:  # Filter < 0.01 tons
                    key = (s1, s2, m)
                    procurement_by_source_and_material[key] = qty
                    total_raw_material += qty

    # Raw material by type
    raw_material_by_type = {}
    for m in model.Materials:
        total = sum(
            value(model.procure[s1, s2, m])
            for s1 in model.Sites
            for s2 in model.Sites
        )
        raw_material_by_type[f'RawMaterial{m}'] = total

    # Raw material by source
    raw_material_by_source = {}
    for s1 in model.Sites:
        total = sum(
            value(model.procure[s1, s2, m])
            for s2 in model.Sites
            for m in model.Materials
        )
        if total > 0.01:
            raw_material_by_source[s1] = total

    # Calculate costs
    raw_material_cost = sum(
        value(model.price[s1, m] * model.procure[s1, s2, m])
        for s1 in model.Sites
        for s2 in model.Sites
        for m in model.Materials
    )

    # Inbound freight cost
    # MaterialE: Always $15/ton (50 miles) regardless of source
    # Materials A-D: Zero if same site, distance-based otherwise
    inbound_freight_cost = sum(
        value((model.material_e_freight if m == 'E' else (0 if s1 == s2 else model.inbound_freight[s1, s2]))
              * model.procure[s1, s2, m])
        for s1 in model.Sites
        for s2 in model.Sites
        for m in model.Materials
    )

    outbound_freight_cost = sum(
        value(model.outbound_freight[s, p] * model.ship_to_port[s, p])
        for s in model.Sites
        for p in model.Ports
    )

    port_operational_cost = sum(
        value(model.port_op_cost[p] * model.ship_to_port[s, p])
        for s in model.Sites
        for p in model.Ports
    )

    sea_freight_cost = sum(
        value(model.sea_freight[p] * model.ship_to_port[s, p])
        for s in model.Sites
        for p in model.Ports
    )

    total_cost = value(model.objective)
    total_finished_product = value(model.produce)

    # Port shipments
    port_shipments = {}
    selected_ports = []
    for s in model.Sites:
        for p in model.Ports:
            qty = value(model.ship_to_port[s, p])
            if qty > 0.01:
                port_shipments[(s, p)] = qty
                if p not in selected_ports:
                    selected_ports.append(p)

    # Calculate average yield factor
    weighted_yield_sum = 0.0
    for m in model.Materials:
        material_qty = raw_material_by_type[f'RawMaterial{m}']
        weighted_yield_sum += value(model.yield_factor[m]) * material_qty

    avg_yield_factor = (
        weighted_yield_sum / total_raw_material
        if total_raw_material > 0 else 0
    )

    # Per-unit costs
    raw_material_cost_per_ton = (
        raw_material_cost / total_raw_material
        if total_raw_material > 0 else 0
    )
    inbound_freight_cost_per_ton = (
        inbound_freight_cost / total_raw_material
        if total_raw_material > 0 else 0
    )
    outbound_freight_cost_per_ton = (
        outbound_freight_cost / total_finished_product
        if total_finished_product > 0 else 0
    )
    port_operational_cost_per_ton = (
        port_operational_cost / total_finished_product
        if total_finished_product > 0 else 0
    )
    sea_freight_cost_per_ton = (
        sea_freight_cost / total_finished_product
        if total_finished_product > 0 else 0
    )

    solution = {
        'facility_location': facility_location,
        'selected_ports': selected_ports,
        'total_finished_product_tons': total_finished_product,
        'total_raw_material_tons': total_raw_material,
        'raw_material_by_type': raw_material_by_type,
        'raw_material_by_source': raw_material_by_source,
        'procurement_details': procurement_by_source_and_material,
        'port_shipments': port_shipments,
        'costs': {
            'raw_material_total': raw_material_cost,
            'inbound_freight_total': inbound_freight_cost,
            'outbound_freight_total': outbound_freight_cost,
            'port_operational_total': port_operational_cost,
            'sea_freight_total': sea_freight_cost,
            'total_cost': total_cost,
            'raw_material_per_ton': raw_material_cost_per_ton,
            'inbound_freight_per_ton': inbound_freight_cost_per_ton,
            'outbound_freight_per_ton': outbound_freight_cost_per_ton,
            'port_operational_per_ton': port_operational_cost_per_ton,
            'sea_freight_per_ton': sea_freight_cost_per_ton
        },
        'avg_yield_factor': avg_yield_factor,
        'solve_time_seconds': solve_time,
        'mip_gap': 0.01
    }

    return solution
