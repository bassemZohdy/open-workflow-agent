"""Bounded local workflow registry for nested workflow execution."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from .errors import UnsupportedWorkflowFeature, WorkflowExecutionError
from .workflow import WorkflowPlan, compile_workflow


class WorkflowCatalog:
    """Explicitly registered workflows; no remote catalog resolution is performed."""

    def __init__(self) -> None:
        self._plans: dict[tuple[str, str, str], WorkflowPlan] = {}

    def register(
        self,
        workflow: WorkflowPlan | Mapping[str, Any],
        *,
        trusted_catalogs: Mapping[str, Any] | None = None,
    ) -> WorkflowPlan:
        plan = (
            workflow
            if isinstance(workflow, WorkflowPlan)
            else compile_workflow(workflow, trusted_catalogs=trusted_catalogs)
        )
        self._plans[(plan.namespace, plan.name, plan.version)] = plan
        return plan

    def resolve(self, reference: Mapping[str, Any]) -> WorkflowPlan:
        namespace_value = reference.get("namespace")
        name_value = reference.get("name")
        version_value = reference.get("version", "latest")
        if (
            not isinstance(namespace_value, str)
            or not namespace_value
            or not isinstance(name_value, str)
            or not name_value
            or not isinstance(version_value, str)
            or not version_value
        ):
            raise WorkflowExecutionError(
                "nested workflow reference requires namespace, name, and version"
            )
        namespace = namespace_value
        name = name_value
        version = version_value
        if version == "latest":
            candidates = [
                plan
                for (item_namespace, item_name, _), plan in self._plans.items()
                if item_namespace == namespace and item_name == name
            ]
            if len(candidates) == 1:
                return candidates[0]
        else:
            plan = self._plans.get((namespace, name, version))
            if plan is not None:
                return plan
        raise UnsupportedWorkflowFeature(
            "nested workflow is not registered in the local workflow catalog",
            details={"namespace": namespace, "name": name, "version": version},
        )

    def resolve_by_name(self, name: str) -> WorkflowPlan:
        """Resolve a uniquely named registered plan (deployment-declared reference).

        Ambiguous names fail closed: a deployment must register exactly one
        workflow per name used by an A2A skill declaration.
        """

        candidates = [plan for (_, item_name, _), plan in self._plans.items() if item_name == name]
        if len(candidates) == 1:
            return candidates[0]
        raise UnsupportedWorkflowFeature(
            "workflow name is not uniquely registered in the local workflow catalog",
            details={"name": name, "matches": len(candidates)},
        )
