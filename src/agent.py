"""Agent: agentic loop with two tool definitions for logistics what-if analysis."""

import json
import os
from typing import Any

import anthropic

from .models import BaselineResult, Modification, WhatIfResult
from .scenario_engine import ScenarioEngine


MAX_AGENTIC_ITERATIONS = 10
MAX_HISTORY_EXCHANGES = 5  # real user turns retained; trims unbounded history growth

SYSTEM_PROMPT_TEMPLATE = """You are an expert logistics optimization analyst for a facility location and supply chain problem.

You help users run baseline optimizations and explore what-if scenarios using a two-phase MILP solver (Pyomo + HiGHS). The model selects the optimal production facility from {num_sites} collection points, determines raw material sourcing (Materials A–E), and optimizes finished-product shipping through export ports.

AVAILABLE COLLECTION POINT SITE IDs ({num_sites} total):
{site_ids_list}

AVAILABLE PORT NAMES ({num_ports} total):
{port_names_list}

WORKFLOW:
1. Call run_baseline first to establish the reference solution.
2. For any what-if question, call run_whatif with the appropriate modifications array.
3. You may call run_whatif multiple times in a single turn to compare scenarios autonomously.
4. After tool results are returned, give the user a concise plain-text summary of key findings — no markdown headers, no bullet lists, no emoji. Just direct, clear sentences with numbers.

TOOL USAGE GUIDANCE:

run_baseline:
- Call once at the start of the session, or when explicitly asked to "reset" or "re-run baseline".
- No required parameters.

run_whatif:
- scenario_name: short descriptive label (e.g. "+20% inbound freight").
- modifications: array of changes. Each modification needs:
    * parameter: one of the enum values
    * action: "set", "multiply", "increase", or "decrease"
    * value: numeric (absolute or multiplier), string (for facility/port names), or array of strings (for multiple ports)
    * target: optional object with "material" (A–E) and/or "site" (exact site_id) for material/site-specific changes
    * description: human-readable explanation
- For percentage increases, use action="multiply" with value=1.2 (for +20%), not value=0.2.
- For percentage decreases, use action="multiply" with value=0.8 (for -20%).
- For port forcing, value can be a single string or array of strings matching port names exactly.
- For facility forcing, value must be an exact site_id from the list above.

PARAMETER REFERENCE:
- production_target: Overall production volume in tons (action: set/increase/decrease/multiply)
- facility_location: Force a specific facility (action: set, value: exact site_id string)
- port_selection: Force specific port(s) (action: set, value: port name string or array)
- freight_cost_inbound: Inbound freight multiplier, affects ALL inbound costs including MaterialE (action: multiply)
- freight_cost_outbound: Outbound freight multiplier (action: multiply)
- freight_cost_sea: Sea freight cost multiplier (action: multiply)
- yield_factor: Material conversion efficiency — requires target.material (action: set/multiply/increase/decrease)
- raw_material_availability: Volume at a specific site/material — requires target.site and target.material
- max_consumption: Max fraction of total mix — requires target.material (action: set/multiply)
- material_price: Raw material price — global multiplier if no target, or site/material-specific

RESPONSE STYLE: After tool results, answer in 2-4 plain sentences. State the key metric changes (cost delta, facility change, port changes) and give a brief interpretation. Do not use markdown formatting."""


RUN_BASELINE_TOOL = {
    "name": "run_baseline",
    "description": (
        "Run two-phase MILP facility location optimization with baseline data. "
        "Call this first before any what-if analysis to establish the reference solution."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "time_limit_seconds": {
                "type": "integer",
                "description": "Maximum solver time in seconds (default 300).",
                "default": 300,
            }
        },
        "required": [],
    },
}

RUN_WHATIF_TOOL = {
    "name": "run_whatif",
    "description": (
        "Apply parameter modifications to baseline data and re-run two-phase MILP. "
        "Only call after run_baseline has been executed."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "scenario_name": {
                "type": "string",
                "description": "Short descriptive label for this scenario.",
            },
            "modifications": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "parameter": {
                            "type": "string",
                            "enum": [
                                "production_target",
                                "facility_location",
                                "port_selection",
                                "freight_cost_inbound",
                                "freight_cost_outbound",
                                "freight_cost_sea",
                                "yield_factor",
                                "raw_material_availability",
                                "max_consumption",
                                "material_price",
                            ],
                        },
                        "action": {
                            "type": "string",
                            "enum": ["set", "multiply", "increase", "decrease"],
                        },
                        "value": {"type": ["number", "string", "array"]},
                        "target": {
                            "type": "object",
                            "properties": {
                                "material": {"type": "string"},
                                "site": {"type": "string"},
                            },
                        },
                        "description": {"type": "string"},
                    },
                    "required": ["parameter", "action", "value"],
                },
            },
            "time_limit_seconds": {
                "type": "integer",
                "description": "Maximum solver time in seconds (default 300).",
                "default": 300,
            },
        },
        "required": ["scenario_name", "modifications"],
    },
}


class LogisticsAgent:
    """Agentic loop that dispatches to run_baseline / run_whatif tools."""

    def __init__(self, scenario_engine: ScenarioEngine, system_prompt: str) -> None:
        api_key = os.environ.get("ANTHROPIC_API_KEY")
        if not api_key:
            raise EnvironmentError(
                "ANTHROPIC_API_KEY is not set. "
                "Set it with: set ANTHROPIC_API_KEY=your_key_here"
            )
        self._client = anthropic.Anthropic(api_key=api_key)
        self._engine = scenario_engine
        self._system_prompt = system_prompt
        self._history: list[dict] = []
        self.last_tool_results: list[dict] = []

    def chat(self, user_message: str) -> str:
        """Send a user message and run the agentic loop.

        Returns the agent's final text response.
        """
        self._history.append({"role": "user", "content": user_message})
        self.last_tool_results = []

        for _ in range(MAX_AGENTIC_ITERATIONS):
            response = self._client.messages.create(
                model="claude-sonnet-4-6",
                max_tokens=4096,
                system=self._system_prompt,
                tools=[RUN_BASELINE_TOOL, RUN_WHATIF_TOOL],
                messages=self._trimmed_history(),
            )

            # Append assistant turn to history
            self._history.append({"role": "assistant", "content": response.content})

            if response.stop_reason == "end_turn":
                return self._extract_text(response.content)

            if response.stop_reason == "tool_use":
                tool_results = self._execute_tool_calls(response.content)
                self._history.append({"role": "user", "content": tool_results})
            else:
                # Unexpected stop reason — return whatever text we have
                return self._extract_text(response.content)

        return "Maximum reasoning iterations reached. Please try a more specific question."

    def clear_history(self) -> None:
        """Reset conversation history (engine baseline state is preserved)."""
        self._history = []
        self.last_tool_results = []

    def _trimmed_history(self) -> list[dict]:
        """Return history capped at the last MAX_HISTORY_EXCHANGES user-initiated turns.

        Trimming only at real user message boundaries (string content) ensures
        tool-use/tool-result pairs are never split.
        """
        turn_starts = [
            i for i, msg in enumerate(self._history)
            if msg["role"] == "user" and isinstance(msg["content"], str)
        ]
        if len(turn_starts) <= MAX_HISTORY_EXCHANGES:
            return self._history
        return self._history[turn_starts[-MAX_HISTORY_EXCHANGES]:]

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _execute_tool_calls(self, content: list[Any]) -> list[dict]:
        """Execute all tool-use blocks and return a list of tool_result dicts."""
        tool_result_content = []

        for block in content:
            if block.type != "tool_use":
                continue

            tool_input = block.input

            try:
                if block.name == "run_baseline":
                    time_limit = int(tool_input.get("time_limit_seconds", 300))
                    result = self._engine.run_baseline(time_limit=time_limit)

                    self.last_tool_results.append({
                        "tool_use_id": block.id,
                        "result": result,
                    })

                    result_text = self._baseline_result_to_text(result)
                    tool_result_content.append({
                        "type": "tool_result",
                        "tool_use_id": block.id,
                        "content": result_text,
                    })

                elif block.name == "run_whatif":
                    scenario_name = tool_input.get("scenario_name", "What-If Scenario")
                    time_limit = int(tool_input.get("time_limit_seconds", 300))
                    raw_mods = tool_input.get("modifications", [])

                    # Parse modifications into Modification objects
                    modifications = []
                    for m in raw_mods:
                        modifications.append(
                            Modification(
                                parameter=m["parameter"],
                                action=m["action"],
                                value=m["value"],
                                target=m.get("target"),
                                description=m.get("description", ""),
                            )
                        )

                    result = self._engine.run_whatif(
                        modifications=modifications,
                        scenario_name=scenario_name,
                        time_limit=time_limit,
                    )

                    self.last_tool_results.append({
                        "tool_use_id": block.id,
                        "result": result,
                    })

                    result_text = self._whatif_result_to_text(result)
                    tool_result_content.append({
                        "type": "tool_result",
                        "tool_use_id": block.id,
                        "content": result_text,
                    })

                else:
                    tool_result_content.append({
                        "type": "tool_result",
                        "tool_use_id": block.id,
                        "is_error": True,
                        "content": f"Unknown tool: {block.name}",
                    })

            except Exception as exc:
                tool_result_content.append({
                    "type": "tool_result",
                    "tool_use_id": block.id,
                    "is_error": True,
                    "content": f"Tool execution error: {exc}",
                })

        return tool_result_content

    @staticmethod
    def _baseline_result_to_text(result: BaselineResult) -> str:
        """Serialise BaselineResult to a compact text summary for the tool_result content."""
        costs = result.costs
        cpt = result.total_cost / result.total_finished_product_tons
        mats = " ".join(
            f"{k.replace('RawMaterial', '')}={v:,.0f}"
            for k, v in result.raw_material_by_type.items()
        )
        return (
            f"BASELINE facility={result.facility_location} ports={','.join(result.selected_ports)}\n"
            f"cost=${result.total_cost:,.0f} cost/t=${cpt:.2f} "
            f"production={result.total_finished_product_tons:,.0f}t "
            f"raw={result.total_raw_material_tons:,.0f}t yield={result.avg_yield_factor:.2%} "
            f"solve={result.solve_time_seconds:.1f}s\n"
            f"costs: raw=${costs['raw_material_total']:,.0f} "
            f"inbound=${costs['inbound_freight_total']:,.0f} "
            f"outbound=${costs['outbound_freight_total']:,.0f} "
            f"port_ops=${costs['port_operational_total']:,.0f} "
            f"sea=${costs['sea_freight_total']:,.0f}\n"
            f"materials(t): {mats}"
        )

    @staticmethod
    def _whatif_result_to_text(result: WhatIfResult) -> str:
        """Serialise WhatIfResult to a compact text summary for the tool_result content."""
        if not result.is_feasible:
            return (
                f"WHAT-IF INFEASIBLE scenario={result.scenario_name}\n"
                f"reason={result.infeasibility_reason}"
            )

        wi = result.whatif
        bl = result.baseline

        fac = (
            f"{bl.facility_location}→{wi.facility_location}"
            if bl.facility_location != wi.facility_location
            else f"{wi.facility_location}(unchanged)"
        )
        bl_ports = ",".join(bl.selected_ports)
        wi_ports = ",".join(wi.selected_ports)
        ports = f"{bl_ports}→{wi_ports}" if bl_ports != wi_ports else f"{wi_ports}(unchanged)"

        bl_cpt = bl.total_cost / bl.total_finished_product_tons if bl.total_finished_product_tons else 0
        wi_cpt = wi.total_cost / wi.total_finished_product_tons if wi.total_finished_product_tons else 0
        prod_d = wi.total_finished_product_tons - bl.total_finished_product_tons
        raw_d = wi.total_raw_material_tons - bl.total_raw_material_tons
        yield_d = wi.avg_yield_factor - bl.avg_yield_factor

        cost_parts = []
        for key, label in [
            ("raw_material_total", "raw"),
            ("inbound_freight_total", "inbound"),
            ("outbound_freight_total", "outbound"),
            ("port_operational_total", "port_ops"),
            ("sea_freight_total", "sea"),
        ]:
            bv = bl.costs[key]
            wv = wi.costs[key]
            d = wv - bv
            pct = d / bv * 100 if bv else 0
            cost_parts.append(f"{label}=${wv:,.0f}(Δ{d:+,.0f},{pct:+.1f}%)")

        mat_parts = [
            f"{mat.replace('RawMaterial', '')}={qty:,.0f}(Δ{qty - bl.raw_material_by_type.get(mat, 0.0):+,.0f})"
            for mat, qty in wi.raw_material_by_type.items()
        ]

        return "\n".join([
            f"WHAT-IF FEASIBLE scenario={result.scenario_name}",
            f"facility={fac} ports={ports}",
            f"cost=${bl.total_cost:,.0f}→${wi.total_cost:,.0f}(Δ{result.total_cost_change:+,.0f},{result.total_cost_change_pct:+.1f}%) "
            f"cost/t=${bl_cpt:.2f}→${wi_cpt:.2f}(Δ{result.cost_per_ton_change:+.2f})",
            f"production={bl.total_finished_product_tons:,.0f}→{wi.total_finished_product_tons:,.0f}t(Δ{prod_d:+,.0f}) "
            f"raw={bl.total_raw_material_tons:,.0f}→{wi.total_raw_material_tons:,.0f}t(Δ{raw_d:+,.0f}) "
            f"yield={bl.avg_yield_factor:.2%}→{wi.avg_yield_factor:.2%}(Δ{yield_d:+.2%})",
            f"costs: {' '.join(cost_parts)}",
            f"materials(t): {' '.join(mat_parts)}",
        ])

    @staticmethod
    def _extract_text(content: list[Any]) -> str:
        """Extract all text blocks from a content list."""
        parts = [block.text for block in content if hasattr(block, "text")]
        return "\n".join(parts).strip()
