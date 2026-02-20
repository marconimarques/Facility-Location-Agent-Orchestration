"""
Logistics Facility Location Agent — entry point.

Run:
    python main.py

Requirements:
    pip install -r requirements.txt
    ANTHROPIC_API_KEY must be set in the environment.
"""
import traceback
from pathlib import Path

from src.cli import (
    confirm_clear_history,
    prompt_user_message,
    show_baseline_result,
    show_cancellation,
    show_claude_response,
    show_error,
    show_help,
    show_sites,
    show_model_info,
    show_thinking,
    show_warning,
    show_welcome,
    show_whatif_result,
)
from src.data_loader import load_all_data
from src.models import BaselineResult, WhatIfResult
from src.reporter import generate_markdown_report, generate_whatif_report, get_next_version_number
from src.scenario_engine import ScenarioEngine

from src.agent import SYSTEM_PROMPT_TEMPLATE


def _build_system_prompt(data) -> str:
    """Inject data-derived values into the system prompt template."""
    site_ids = [cp.site_id for cp in data.collection_points]
    port_names = [p.port_name for p in data.ports]

    site_ids_list = "\n".join(f"  - {sid}" for sid in site_ids)
    port_names_list = "\n".join(f"  - {pname}" for pname in port_names)

    return SYSTEM_PROMPT_TEMPLATE.format(
        num_sites=len(site_ids),
        num_ports=len(port_names),
        site_ids_list=site_ids_list,
        port_names_list=port_names_list,
    )


def main() -> None:
    try:  # Layer 3: outer catch-all
        show_welcome()

        # Load data from the data directory
        data_path = Path(__file__).parent / "data"
        try:
            data = load_all_data(data_path)
        except FileNotFoundError as exc:
            show_error(str(exc))
            return
        except ValueError as exc:
            show_error(f"Data validation failed: {exc}")
            return

        show_model_info(data)

        system_prompt = _build_system_prompt(data)
        engine = ScenarioEngine(baseline_data=data)

        # Import LogisticsAgent here so a missing API key surfaces immediately
        from src.agent import LogisticsAgent

        try:
            agent = LogisticsAgent(scenario_engine=engine, system_prompt=system_prompt)
        except EnvironmentError as exc:
            show_error(str(exc))
            return

        results_dir = Path(__file__).parent / "results"

        while True:
            try:  # Layer 2: per-step
                user_input = prompt_user_message().strip()

                if not user_input:
                    continue

                if user_input.lower() in {"quit", "exit", "q"}:
                    show_cancellation()
                    break

                if user_input.lower() in {"help", "h", "?"}:
                    show_help()
                    continue

                if user_input.lower() in {"list", "sites"}:
                    show_sites(data)
                    continue

                if user_input.lower() == "clear":
                    if confirm_clear_history():
                        agent.clear_history()
                        show_warning("Conversation history cleared.")
                    continue

                with show_thinking() as progress:
                    progress.add_task("Reasoning...", total=None)
                    response = agent.chat(user_input)

                # Display tool results
                for item in agent.last_tool_results:
                    result = item["result"]

                    if isinstance(result, BaselineResult):
                        show_baseline_result(result)
                        # Auto-save baseline report
                        try:
                            report_path = results_dir / "baseline_output.md"
                            generate_markdown_report(
                                result=result,
                                output_path=str(report_path),
                                scenario_name="Baseline",
                            )
                        except Exception:
                            pass  # Report saving is non-critical

                    elif isinstance(result, WhatIfResult):
                        show_whatif_result(result)
                        # Auto-save what-if report
                        try:
                            version = get_next_version_number(results_dir)
                            report_path = results_dir / f"whatif_output_v{version}.md"
                            generate_whatif_report(
                                result=result,
                                output_path=str(report_path),
                            )
                            show_warning(f"Report saved: {report_path.name}")
                        except Exception:
                            pass  # Report saving is non-critical

                show_claude_response(response)

            except KeyboardInterrupt:
                show_cancellation()
                break
            except Exception as exc:  # noqa: BLE001
                show_error(str(exc))
                continue

    except KeyboardInterrupt:
        show_cancellation()
    except Exception as exc:  # noqa: BLE001
        show_error(str(exc))
        traceback.print_exc()


if __name__ == "__main__":
    main()
