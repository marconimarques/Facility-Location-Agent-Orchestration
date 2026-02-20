"""Pydantic v2 data contracts for the logistics what-if agent."""

from typing import Any, Dict, List, Literal, Optional, Union

from pydantic import BaseModel, ConfigDict, Field


class Modification(BaseModel):
    """A single parameter modification for a what-if scenario."""
    parameter: Literal[
        "production_target", "facility_location", "port_selection",
        "freight_cost_inbound", "freight_cost_outbound", "freight_cost_sea",
        "yield_factor", "raw_material_availability", "max_consumption", "material_price"
    ]
    action: Literal["set", "multiply", "increase", "decrease"]
    value: Union[float, int, str, List[str]]
    target: Optional[Dict[str, str]] = None  # {"material": "A"} or {"site": "X", "material": "A"}
    description: str = ""


class BaselineResult(BaseModel):
    """Full solution from a two-phase MILP run."""
    model_config = ConfigDict(arbitrary_types_allowed=True)

    facility_location: str
    selected_ports: List[str]
    total_cost: float
    total_finished_product_tons: float
    total_raw_material_tons: float
    raw_material_by_type: Dict[str, float]
    raw_material_by_source: Dict[str, float]
    costs: Dict[str, float]
    avg_yield_factor: float
    solve_time_seconds: float
    # Tuple-keyed dicts for reporter use (stored as Any to avoid serialisation issues)
    procurement_details: Any = Field(default_factory=dict)
    port_shipments: Any = Field(default_factory=dict)


class WhatIfResult(BaseModel):
    """Result from a what-if scenario analysis."""
    scenario_name: str
    modifications: List[Modification]
    baseline: BaselineResult
    whatif: Optional[BaselineResult] = None
    is_feasible: bool
    infeasibility_reason: Optional[str] = None
    total_cost_change: Optional[float] = None
    total_cost_change_pct: Optional[float] = None
    cost_per_ton_change: Optional[float] = None
