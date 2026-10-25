"""Private native fork reentry authorized by a different recorded child wake.

Acknowledging a native interruption only permits reevaluating the owning fork.
It neither wakes the interrupted child nor creates a common delivery decision.
"""

from __future__ import annotations

import copy
from dataclasses import replace
from typing import TYPE_CHECKING, Any

from .child_suspension import ChildSuspension
from .effects import EffectJournal, ReceiptDependency
from .errors import InvocationStateError
from .expressions import validate_expression_data

if TYPE_CHECKING:
    from .child_waits import ChildWaitStore

_PROFILE = "owa/child-rescan/v1"
_BRANCH = "/fork/branches/"


def _fork_bindings(
    suspension: ChildSuspension,
) -> list[tuple[str, str, tuple[tuple[str, int], ...]]]:
    reference = suspension.identity.task_reference
    occurrence = suspension.identity.occurrence
    result = []
    offset = reference.find(_BRANCH)
    while offset >= 0:
        fork = reference[:offset]
        branch_tail = reference[offset + len(_BRANCH) :]
        branch, separator, _ = branch_tail.partition("/")
        if not separator or not branch.isdecimal() or str(int(branch)) != branch:
            raise InvocationStateError("native child rescan branch reference is inconsistent")
        boundary = next(
            (
                index
                for index, (item, _) in enumerate(occurrence)
                if item.startswith(fork + _BRANCH)
            ),
            None,
        )
        if boundary is None or not any(item == fork for item, _ in occurrence[:boundary]):
            raise InvocationStateError("native child rescan fork occurrence is absent")
        # Include the owning task's retry attempt and enclosing loop/scope visits.
        # A previous occurrence's completed child cannot authorize current reentry.
        result.append((fork, branch, occurrence[:boundary]))
        offset = reference.find(_BRANCH, offset + len(_BRANCH))
    return list(reversed(result))


def _shared_fork(
    pending: ChildSuspension,
    other: ChildSuspension,
    fork_reference: str | None,
) -> tuple[str, str, tuple[tuple[str, int], ...]] | None:
    candidates = {value[0]: value for value in _fork_bindings(other)}
    # Native ADK owns a whole-plan node: use the nearest common owning fork.
    # A native LG task passes its explicit fork reference, preventing a direct
    # branch child from acknowledging its own unfinished wait on sibling proof.
    for owner in _fork_bindings(pending):
        sibling = candidates.get(owner[0])
        if (
            sibling is not None
            and (fork_reference is None or owner[0] == fork_reference)
            and sibling[1] != owner[1]
            and sibling[2] == owner[2]
        ):
            return owner
    return None


def _admission(
    pending: ChildSuspension,
    *,
    child_waits: ChildWaitStore,
    journal: EffectJournal,
    dependencies: dict[str, ReceiptDependency],
    invocation_id: str,
    workflow_fingerprint: str,
) -> None:
    identity = pending.identity
    registration = child_waits.registration(identity)
    admission = replace(identity, operation="policy:child-await:v1")
    if (
        identity.invocation_id != invocation_id
        or identity.workflow_fingerprint != workflow_fingerprint
        or registration is None
        or registration.identity != identity
        or registration.suspension.as_value() != pending.as_value()
        or journal.get(admission) != registration.receipt
        or dependencies.get(admission.key)
        != ReceiptDependency.capture(admission, registration.receipt)
    ):
        raise InvocationStateError("native child rescan admission is inconsistent")
    dependencies[admission.key].verify(journal)


def verify_child_rescan_wake(
    value: Any,
    pending: ChildSuspension,
    *,
    child_waits: ChildWaitStore,
    journal: EffectJournal,
    dependencies: dict[str, ReceiptDependency],
    invocation_id: str,
    workflow_fingerprint: str,
    fork_reference: str | None = None,
) -> dict[str, Any]:
    """Validate saved or current private rescan against immutable common proof."""
    try:
        validate_expression_data(value)
        if (
            not isinstance(value, dict)
            or set(value) != {"profile", "pending", "ready_wakes"}
            or value["profile"] != _PROFILE
            or value["pending"] != pending.as_value()
            or not isinstance(value["ready_wakes"], dict)
            or not value["ready_wakes"]
        ):
            raise ValueError("invalid private child rescan transport")
        _admission(
            pending,
            child_waits=child_waits,
            journal=journal,
            dependencies=dependencies,
            invocation_id=invocation_id,
            workflow_fingerprint=workflow_fingerprint,
        )
        owners = _fork_bindings(pending)
        if not owners or (
            fork_reference is not None and not any(owner[0] == fork_reference for owner in owners)
        ):
            raise ValueError("child rescan belongs to another native task")
        known = child_waits.known_wakes(invocation_id, workflow_fingerprint)
        for key, wake in value["ready_wakes"].items():
            registration = child_waits.registration_for_key(key)
            if (
                registration is None
                or key not in known
                or known[key] != wake
                or not isinstance(wake, dict)
                or type(wake.get("deadline_at")) not in {int, float, type(None)}
            ):
                raise ValueError("child rescan wake is not recorded")
            other = registration.suspension
            if key == pending.identity.key or _shared_fork(pending, other, fork_reference) is None:
                raise ValueError("child rescan wake does not belong to a sibling occurrence")
            _admission(
                other,
                child_waits=child_waits,
                journal=journal,
                dependencies=dependencies,
                invocation_id=invocation_id,
                workflow_fingerprint=workflow_fingerprint,
            )
        return copy.deepcopy(value)
    except Exception:
        raise InvocationStateError("native child fork rescan evidence is inconsistent") from None


def child_rescan_wake(
    pending: ChildSuspension,
    *,
    child_waits: ChildWaitStore,
    journal: EffectJournal,
    dependencies: dict[str, ReceiptDependency],
    ready_wakes: dict[str, Any] | None,
    invocation_id: str,
    workflow_fingerprint: str,
    fork_reference: str | None = None,
) -> dict[str, Any] | None:
    """Build reentry only for an unconsumed sibling in the saved owning fork."""
    owners = _fork_bindings(pending)
    if not owners or (
        fork_reference is not None and not any(owner[0] == fork_reference for owner in owners)
    ):
        return None
    ready: dict[str, Any] = {}
    available_wakes = ready_wakes or {}
    for registration in child_waits.for_invocation(invocation_id, workflow_fingerprint):
        other = registration.suspension
        if (
            other.identity.key in available_wakes
            and _shared_fork(pending, other, fork_reference) is not None
        ):
            ready[other.identity.key] = copy.deepcopy(available_wakes[other.identity.key])
    if not ready:
        return None
    return verify_child_rescan_wake(
        {"profile": _PROFILE, "pending": pending.as_value(), "ready_wakes": ready},
        pending,
        child_waits=child_waits,
        journal=journal,
        dependencies=dependencies,
        invocation_id=invocation_id,
        workflow_fingerprint=workflow_fingerprint,
        fork_reference=fork_reference,
    )
