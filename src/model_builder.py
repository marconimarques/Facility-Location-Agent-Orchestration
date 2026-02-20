"""Pyomo optimization model builder for facility location problem."""

from pyomo.environ import *
from typing import Dict
from .data_loader import OptimizationData


def build_facility_location_model(
    data: OptimizationData,
    exclude_material_e: bool = False
) -> ConcreteModel:
    """Build Pyomo MILP model for facility location and finished product production.

    Args:
        data: Validated input data
        exclude_material_e: If True, set MaterialE volumes to 0 (Phase 1)

    Returns:
        Pyomo ConcreteModel ready to solve
    """
    model = ConcreteModel(name="Facility_Location")

    # ===================================================================
    # SETS
    # ===================================================================
    site_ids = [cp.site_id for cp in data.collection_points]
    materials = ['A', 'B', 'C', 'D', 'E']
    port_names = [port.port_name for port in data.ports]

    model.Sites = Set(initialize=site_ids)
    model.Materials = Set(initialize=materials)
    model.Ports = Set(initialize=port_names)

    # ===================================================================
    # PARAMETERS
    # ===================================================================

    # Volume availability (exclude MaterialE in Phase 1)
    volume_dict = {}
    for cp in data.collection_points:
        for mat, vol in cp.volumes.items():
            if exclude_material_e and mat == 'E':
                volume_dict[cp.site_id, mat] = 0.0
            else:
                volume_dict[cp.site_id, mat] = vol

    model.volume = Param(
        model.Sites, model.Materials,
        initialize=volume_dict,
        domain=NonNegativeReals
    )

    # Material prices
    price_dict = {
        (cp.site_id, mat): price
        for cp in data.collection_points
        for mat, price in cp.prices.items()
    }
    model.price = Param(
        model.Sites, model.Materials,
        initialize=price_dict,
        domain=NonNegativeReals
    )

    # Inbound freight costs
    model.inbound_freight = Param(
        model.Sites, model.Sites,
        initialize=data.inbound_freight,
        domain=NonNegativeReals,
        default=0.0
    )

    # MaterialE special freight (fixed at 50 miles)
    model.material_e_freight = Param(
        initialize=data.material_e_freight,
        domain=NonNegativeReals
    )

    # Outbound freight costs
    model.outbound_freight = Param(
        model.Sites, model.Ports,
        initialize=data.outbound_freight,
        domain=NonNegativeReals,
        default=0.0
    )

    # Port costs
    port_op_dict = {p.port_name: p.operational_cost for p in data.ports}
    model.port_op_cost = Param(model.Ports, initialize=port_op_dict)

    sea_freight_dict = {p.port_name: p.sea_freight_cost for p in data.ports}
    model.sea_freight = Param(model.Ports, initialize=sea_freight_dict)

    # Production parameters
    model.production_target = Param(
        initialize=data.production_params.target_tons
    )
    model.yield_factor = Param(
        model.Materials,
        initialize=data.production_params.yield_factors
    )
    model.max_consumption = Param(
        model.Materials,
        initialize=data.production_params.max_consumption
    )

    # ===================================================================
    # DECISION VARIABLES
    # ===================================================================

    # Binary: facility location selection
    model.y = Var(model.Sites, domain=Binary)

    # Continuous: material procurement from s1 to s2
    def procurement_bounds(model, s1, s2, m):
        return (0, model.volume[s1, m])

    model.procure = Var(
        model.Sites, model.Sites, model.Materials,
        domain=NonNegativeReals,
        bounds=procurement_bounds
    )

    # Continuous: finished product production (fixed at target)
    model.produce = Var(
        domain=NonNegativeReals,
        bounds=(
            data.production_params.target_tons,
            data.production_params.target_tons
        )
    )

    # Continuous: finished product shipment to ports
    def outbound_bounds(model, s, p):
        return (0, model.production_target)

    model.ship_to_port = Var(
        model.Sites, model.Ports,
        domain=NonNegativeReals,
        bounds=outbound_bounds
    )

    # ===================================================================
    # OBJECTIVE FUNCTION
    # ===================================================================

    def objective_rule(model):
        """Minimize total cost = raw materials + logistics + port ops."""

        # Raw material cost
        raw_cost = sum(
            model.price[s1, m] * model.procure[s1, s2, m]
            for s1 in model.Sites
            for s2 in model.Sites
            for m in model.Materials
        )

        # Inbound freight cost
        # MaterialE: Always $15/ton (50 miles) regardless of source
        # Materials A-D: Zero if same site, distance-based otherwise
        inbound_cost = sum(
            (model.material_e_freight if m == 'E' else (0 if s1 == s2 else model.inbound_freight[s1, s2]))
            * model.procure[s1, s2, m]
            for s1 in model.Sites
            for s2 in model.Sites
            for m in model.Materials
        )

        # Outbound freight cost
        outbound_cost = sum(
            model.outbound_freight[s, p] * model.ship_to_port[s, p]
            for s in model.Sites
            for p in model.Ports
        )

        # Port operational cost
        port_op = sum(
            model.port_op_cost[p] * model.ship_to_port[s, p]
            for s in model.Sites
            for p in model.Ports
        )

        # Sea freight cost
        sea_cost = sum(
            model.sea_freight[p] * model.ship_to_port[s, p]
            for s in model.Sites
            for p in model.Ports
        )

        return raw_cost + inbound_cost + outbound_cost + port_op + sea_cost

    model.objective = Objective(rule=objective_rule, sense=minimize)

    # ===================================================================
    # CONSTRAINTS
    # ===================================================================

    # Constraint 1: Single facility location
    def single_facility_rule(model):
        return sum(model.y[s] for s in model.Sites) == 1

    model.single_facility = Constraint(rule=single_facility_rule)

    # Constraint 1b: Only sites with raw material can be selected as facility
    def facility_must_have_material_rule(model, s):
        """Prevent selecting sites with zero total raw material."""
        total_volume = sum(model.volume[s, m] for m in model.Materials)
        if total_volume < 0.01:  # Effectively zero
            return model.y[s] == 0
        else:
            return Constraint.Skip

    model.facility_must_have_material = Constraint(
        model.Sites,
        rule=facility_must_have_material_rule
    )

    # Constraint 2: Production equals target
    def production_target_rule(model):
        return model.produce == model.production_target

    model.production_constraint = Constraint(rule=production_target_rule)

    # Constraint 3: Yield factor (raw materials -> finished product)
    def yield_constraint_rule(model):
        total_finished_product_equivalent = sum(
            model.yield_factor[m] * model.procure[s1, s2, m]
            for s1 in model.Sites
            for s2 in model.Sites
            for m in model.Materials
        )
        return total_finished_product_equivalent == model.produce

    model.yield_constraint = Constraint(rule=yield_constraint_rule)

    # Constraint 4: Maximum consumption limits per material
    def max_consumption_rule(model, m):
        material_total = sum(
            model.procure[s1, s2, m]
            for s1 in model.Sites
            for s2 in model.Sites
        )

        all_materials_total = sum(
            model.procure[s1, s2, m2]
            for s1 in model.Sites
            for s2 in model.Sites
            for m2 in model.Materials
        )

        return material_total <= model.max_consumption[m] * all_materials_total

    model.max_consumption_constraint = Constraint(
        model.Materials,
        rule=max_consumption_rule
    )

    # Constraint 5: Volume availability at each source
    def volume_availability_rule(model, s1, m):
        return (
            sum(model.procure[s1, s2, m] for s2 in model.Sites)
            <= model.volume[s1, m]
        )

    model.volume_availability = Constraint(
        model.Sites, model.Materials,
        rule=volume_availability_rule
    )

    # Constraint 6: Procurement only to selected facility
    def procurement_to_facility_rule(model, s1, s2, m):
        return model.procure[s1, s2, m] <= model.volume[s1, m] * model.y[s2]

    model.procurement_to_facility = Constraint(
        model.Sites, model.Sites, model.Materials,
        rule=procurement_to_facility_rule
    )

    # Constraint 7: Outbound shipping only from selected facility
    def outbound_from_facility_rule(model, s, p):
        return model.ship_to_port[s, p] <= model.production_target * model.y[s]

    model.outbound_from_facility = Constraint(
        model.Sites, model.Ports,
        rule=outbound_from_facility_rule
    )

    # Constraint 8: All finished product must be shipped to ports
    def total_shipment_rule(model):
        return (
            sum(model.ship_to_port[s, p]
                for s in model.Sites
                for p in model.Ports)
            == model.produce
        )

    model.total_shipment = Constraint(rule=total_shipment_rule)

    return model
