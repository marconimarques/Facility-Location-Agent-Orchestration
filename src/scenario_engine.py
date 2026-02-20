"""ScenarioEngine: wraps the two-phase MILP pipeline and holds baseline state."""

import copy
from typing import Dict, List, Optional

from .data_loader import OptimizationData, check_production_feasibility
from .model_builder import build_facility_location_model
from .optimizer import solve_optimization
from .models import BaselineResult, Modification, WhatIfResult


class ScenarioEngine:
    """Manages baseline data and runs two-phase MILP optimizations.

    The engine holds _baseline_result as instance state so what-if runs
    can always compare against it without re-passing data through the agent.
    """

    def __init__(self, baseline_data: OptimizationData) -> None:
        self._baseline_data = baseline_data
        self._baseline_result: Optional[BaselineResult] = None

    @property
    def has_baseline(self) -> bool:
        return self._baseline_result is not None

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def run_baseline(self, time_limit: int = 300) -> BaselineResult:
        """Run two-phase MILP with original data and cache the result.

        Args:
            time_limit: Solver time limit in seconds.

        Returns:
            BaselineResult with full solution details.

        Raises:
            ValueError: If data is infeasible before even reaching the solver.
            RuntimeError: If the solver fails or returns infeasible.
        """
        result = self._run_two_phase_pipeline(self._baseline_data, time_limit)
        self._baseline_result = result
        return result

    def run_whatif(
        self,
        modifications: List[Modification],
        scenario_name: str,
        time_limit: int = 300,
    ) -> WhatIfResult:
        """Apply modifications and re-run two-phase MILP.

        Args:
            modifications: List of Modification objects describing parameter changes.
            scenario_name: Human-readable name for this scenario.
            time_limit: Solver time limit in seconds.

        Returns:
            WhatIfResult comparing baseline vs what-if solution.
        """
        # Ensure baseline exists
        if not self.has_baseline:
            raise RuntimeError(
                "run_baseline() must be called before run_whatif()."
            )

        # Apply modifications to a deep copy of baseline data
        try:
            modified_data = self._apply_modifications(modifications)
        except ValueError as exc:
            return WhatIfResult(
                scenario_name=scenario_name,
                modifications=modifications,
                baseline=self._baseline_result,
                is_feasible=False,
                infeasibility_reason=f"Invalid modification: {exc}",
            )

        # Run the pipeline; catch infeasibility gracefully
        try:
            whatif_result = self._run_two_phase_pipeline(modified_data, time_limit)
        except (ValueError, RuntimeError) as exc:
            return WhatIfResult(
                scenario_name=scenario_name,
                modifications=modifications,
                baseline=self._baseline_result,
                is_feasible=False,
                infeasibility_reason=str(exc),
            )

        # Compute comparison metrics
        baseline_cost = self._baseline_result.total_cost
        whatif_cost = whatif_result.total_cost
        total_cost_change = whatif_cost - baseline_cost
        total_cost_change_pct = (
            (total_cost_change / baseline_cost * 100) if baseline_cost > 0 else 0.0
        )

        baseline_cpt = (
            baseline_cost / self._baseline_result.total_finished_product_tons
            if self._baseline_result.total_finished_product_tons > 0 else 0.0
        )
        whatif_cpt = (
            whatif_cost / whatif_result.total_finished_product_tons
            if whatif_result.total_finished_product_tons > 0 else 0.0
        )
        cost_per_ton_change = whatif_cpt - baseline_cpt

        return WhatIfResult(
            scenario_name=scenario_name,
            modifications=modifications,
            baseline=self._baseline_result,
            whatif=whatif_result,
            is_feasible=True,
            total_cost_change=total_cost_change,
            total_cost_change_pct=total_cost_change_pct,
            cost_per_ton_change=cost_per_ton_change,
        )

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _run_two_phase_pipeline(
        self, data: OptimizationData, time_limit: int
    ) -> BaselineResult:
        """Execute two-phase MILP and return a BaselineResult.

        Phase 1: Find optimal facility location (Materials A-D only).
        Phase 2: Full optimisation with MaterialE, facility fixed from Phase 1.

        Raises:
            ValueError: If pre-solver feasibility check fails.
            RuntimeError: If solver reports infeasibility or failure.
        """
        # Pre-solve feasibility checks
        check_production_feasibility(data, exclude_material_e=True)
        check_production_feasibility(data, exclude_material_e=False)

        # Determine facility — honour forced_facility if set
        if data.forced_facility:
            facility = data.forced_facility
        else:
            # Phase 1: find optimal facility (without MaterialE)
            model_p1 = build_facility_location_model(data, exclude_material_e=True)
            sol_p1 = solve_optimization(model_p1, time_limit=time_limit)
            facility = sol_p1['facility_location']

        # Phase 2: full optimisation with facility fixed
        model_p2 = build_facility_location_model(data, exclude_material_e=False)

        for s in model_p2.Sites:
            if s == facility:
                model_p2.y[s].fix(1)
            else:
                model_p2.y[s].fix(0)

        # Apply port forcing if specified
        if data.forced_ports:
            for s in model_p2.Sites:
                for p in model_p2.Ports:
                    if p not in data.forced_ports:
                        model_p2.ship_to_port[s, p].fix(0)

        sol_p2 = solve_optimization(model_p2, time_limit=time_limit)

        return self._dict_to_baseline_result(sol_p2)

    @staticmethod
    def _dict_to_baseline_result(solution: Dict) -> BaselineResult:
        """Convert a raw solver solution dict to a BaselineResult model."""
        return BaselineResult(
            facility_location=solution['facility_location'],
            selected_ports=solution['selected_ports'],
            total_cost=solution['costs']['total_cost'],
            total_finished_product_tons=solution['total_finished_product_tons'],
            total_raw_material_tons=solution['total_raw_material_tons'],
            raw_material_by_type=solution['raw_material_by_type'],
            raw_material_by_source=solution['raw_material_by_source'],
            costs=solution['costs'],
            avg_yield_factor=solution['avg_yield_factor'],
            solve_time_seconds=solution['solve_time_seconds'],
            procurement_details=solution['procurement_details'],
            port_shipments=solution['port_shipments'],
        )

    def _apply_modifications(self, modifications: List[Modification]) -> OptimizationData:
        """Deep-copy baseline data and apply each modification.

        Args:
            modifications: List of Modification objects.

        Returns:
            Modified OptimizationData (deep copy, baseline unchanged).

        Raises:
            ValueError: If a modification references a non-existent entity
                        or uses invalid values.
        """
        modified_data = copy.deepcopy(self._baseline_data)

        for mod in modifications:
            param = mod.parameter
            action = mod.action
            value = mod.value
            target = mod.target or {}
            material = target.get('material')
            site = target.get('site')

            try:
                if param == 'production_target':
                    if action == 'set':
                        modified_data.production_params.target_tons = float(value)
                    elif action == 'increase':
                        modified_data.production_params.target_tons += float(value)
                    elif action == 'decrease':
                        modified_data.production_params.target_tons -= float(value)
                    elif action == 'multiply':
                        modified_data.production_params.target_tons *= float(value)

                elif param == 'facility_location':
                    modified_data.forced_facility = str(value)

                elif param == 'port_selection':
                    if isinstance(value, list):
                        modified_data.forced_ports = [str(v) for v in value]
                    else:
                        modified_data.forced_ports = [str(value)]

                elif param == 'freight_cost_inbound':
                    multiplier = float(value)
                    for key in modified_data.inbound_freight:
                        modified_data.inbound_freight[key] *= multiplier
                    modified_data.material_e_freight *= multiplier

                elif param == 'freight_cost_outbound':
                    multiplier = float(value)
                    for key in modified_data.outbound_freight:
                        modified_data.outbound_freight[key] *= multiplier

                elif param == 'freight_cost_sea':
                    multiplier = float(value)
                    for port in modified_data.ports:
                        port.sea_freight_cost *= multiplier

                elif param == 'yield_factor':
                    if not material:
                        raise ValueError("yield_factor modification requires target.material")
                    if material not in modified_data.production_params.yield_factors:
                        raise ValueError(f"Material '{material}' not found in yield_factors")
                    if action == 'set':
                        modified_data.production_params.yield_factors[material] = float(value)
                    elif action == 'multiply':
                        modified_data.production_params.yield_factors[material] *= float(value)
                    elif action == 'increase':
                        modified_data.production_params.yield_factors[material] += float(value)
                    elif action == 'decrease':
                        modified_data.production_params.yield_factors[material] -= float(value)

                elif param == 'max_consumption':
                    if not material:
                        raise ValueError("max_consumption modification requires target.material")
                    if material not in modified_data.production_params.max_consumption:
                        raise ValueError(f"Material '{material}' not found in max_consumption")
                    if action == 'set':
                        modified_data.production_params.max_consumption[material] = float(value)
                    elif action == 'multiply':
                        modified_data.production_params.max_consumption[material] *= float(value)
                    elif action == 'increase':
                        modified_data.production_params.max_consumption[material] += float(value)
                    elif action == 'decrease':
                        modified_data.production_params.max_consumption[material] -= float(value)

                elif param == 'raw_material_availability':
                    if not site or not material:
                        raise ValueError(
                            "raw_material_availability requires target.site and target.material"
                        )
                    cp = next(
                        (c for c in modified_data.collection_points if c.site_id == site), None
                    )
                    if not cp:
                        raise ValueError(f"Collection point '{site}' not found")
                    if material not in cp.volumes:
                        raise ValueError(f"Material '{material}' not found at site '{site}'")
                    if action == 'set':
                        cp.volumes[material] = float(value)
                    elif action == 'multiply':
                        cp.volumes[material] *= float(value)
                    elif action == 'increase':
                        cp.volumes[material] += float(value)
                    elif action == 'decrease':
                        cp.volumes[material] -= float(value)

                elif param == 'material_price':
                    if site and material:
                        # Site-specific material price
                        cp = next(
                            (c for c in modified_data.collection_points if c.site_id == site), None
                        )
                        if not cp:
                            raise ValueError(f"Collection point '{site}' not found")
                        if material not in cp.prices:
                            raise ValueError(f"Material '{material}' not found at site '{site}'")
                        if action == 'set':
                            cp.prices[material] = float(value)
                        elif action == 'multiply':
                            cp.prices[material] *= float(value)
                        elif action == 'increase':
                            cp.prices[material] += float(value)
                        elif action == 'decrease':
                            cp.prices[material] -= float(value)
                    else:
                        # Global price adjustment (all sites, all materials)
                        multiplier = float(value)
                        for cp in modified_data.collection_points:
                            for mat in cp.prices:
                                cp.prices[mat] *= multiplier

                else:
                    raise ValueError(f"Unknown parameter: '{param}'")

            except (KeyError, TypeError, ValueError) as exc:
                raise ValueError(
                    f"Error applying modification '{mod.description}': {exc}"
                ) from exc

        return modified_data
