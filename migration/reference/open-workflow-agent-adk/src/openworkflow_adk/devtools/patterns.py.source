"""Reusable orchestration-pattern fragments."""

from __future__ import annotations

from typing import Any


def map_reduce_pattern(
    name: str,
    collection: str,
    map_tasks: list[dict[str, Any]],
    reduce_task: dict[str, Any],
) -> list[dict[str, Any]]:
    """Return a composable ``for`` map stage followed by a reduce stage."""
    return [
        {
            name: {
                "for": {"each": "item", "in": collection, "at": "index"},
                "do": map_tasks,
            }
        },
        {f"{name}_reduce": reduce_task},
    ]


def debate_pattern(
    name: str,
    model: str,
    members: list[dict[str, Any]],
    instruction: str = "Compare member responses and produce the best answer.",
) -> dict[str, Any]:
    """Return a coordinator agent with named debate members."""
    return {
        name: {
            "wait": {"seconds": 0},
            "metadata": {
                "adk": {
                    "agent": {
                        "model": model,
                        "instruction": instruction,
                        "sub_agents": [
                            {
                                "name": member["name"],
                                "model": model,
                                "instruction": member["instruction"],
                            }
                            for member in members
                        ],
                    }
                }
            },
        }
    }


def hierarchical_pattern(
    name: str,
    model: str,
    instruction: str,
    workers: list[dict[str, str]],
) -> dict[str, Any]:
    """Return a manager agent with a worker hierarchy."""
    return debate_pattern(name, model, workers, instruction)
