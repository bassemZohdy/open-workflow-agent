"""Bounded read-only supervisor authority checks; no runtime bootstrap or SDK.

The authenticated manager already performs current deployment/archive admission.
The supervisor independently compares its trusted profile and exact retained
proofs immediately before its fenced mutation. No descriptor alone is authority.
"""

from __future__ import annotations

from dataclasses import asdict
from typing import TYPE_CHECKING, Any, Literal

from ..effects import EffectIdentity, ReceiptDependency, effect_request_digest
from ..errors import InvocationStateConflict, WorkflowExecutionError
from ..recovery_definitions import (
    ADMISSION_OPERATION,
    MAX_ARCHIVE_BYTES,
    MAX_PROFILE_BYTES,
    PROFILE,
    _expected_receipt,
    _parsed,
)
from ..recovery_definitions import (
    _digest as archive_digest,
)
from ..storage import StorageConnection
from ..timing import DeadlineAnchor, clock_started_at, root_clock_identity
from .jobs import (
    _PROFILE,
    ADMISSION_POLICY,
    SandboxJobProfile,
    _decode,
    _raw_receipt,
    _receipt,
    _request,
    job_id,
    policy_identity,
)

if TYPE_CHECKING:
    from .job_backend import SandboxJobLaunch


def _json_row(
    connection: StorageConnection, table: str, column: str, key: str, value: str, limit: int
) -> Any:
    row = connection.execute(
        f"SELECT CASE WHEN length({column})<=? THEN {column} "
        f"ELSE NULL END FROM {table} WHERE {key}=?",
        (limit, value),
    ).fetchone()
    if row is None or row[0] is None:
        raise ValueError("missing bounded authority")
    return _parsed(row[0], limit=limit)


def verify_backend_launch(
    connection: StorageConnection,
    launch: SandboxJobLaunch,
    *,
    expected_profile: SandboxJobProfile,
    purpose: Literal["submit", "inspect", "control"] = "submit",
    control_generation: int | None = None,
    reason: Literal["cancel", "deadline"] | None = None,
) -> None:
    """Only reads already-installed tables; never acquires or extends ownership."""
    try:
        clock = (
            "EXTRACT(EPOCH FROM clock_timestamp())"
            if connection.is_postgresql
            else "((julianday('now')-2440587.5)*86400.0)"
        )
        lease = launch.lease
        if (
            connection.execute(
                "SELECT 1 FROM sandbox_job_ownership WHERE job_id=? AND "
                f"admission_digest=? AND owner=? AND generation=? AND expires_at>{clock} "
                f"AND expires_at<={clock}+3600",
                (lease.job_id, lease.admission_digest, lease.owner, lease.generation),
            ).fetchone()
            is None
        ):
            raise InvocationStateConflict("sandbox job backend ownership is stale")
        value = _json_row(connection, "sandbox_jobs", "payload", "job_id", lease.job_id, 1_048_576)
        identity = EffectIdentity.from_value(value["identity"])
        if (
            job_id(identity) != lease.job_id
            or effect_request_digest(ADMISSION_POLICY, value) != lease.admission_digest
            or value["profile"] != _PROFILE
            or value["lifetime"] != "independent"
            or value["backend_profile"] != asdict(expected_profile)
            or launch.profile != expected_profile
            or value["request"] != _request(launch.request, identity)
            or value["request_digest"] != launch.request_digest
            or launch.request_digest != effect_request_digest(_PROFILE, value["request"])
            or value["deadline_at"] != launch.deadline_at
        ):
            raise ValueError("changed launch")
        row = connection.execute(
            "SELECT invocation_id,fingerprint,digest FROM sandbox_jobs WHERE job_id=?",
            (lease.job_id,),
        ).fetchone()
        if tuple(row) != (
            identity.invocation_id,
            identity.workflow_fingerprint,
            lease.admission_digest,
        ) or _raw_receipt(connection, policy_identity(identity)) != _receipt(
            value, ADMISSION_POLICY
        ):
            raise ValueError("changed admission proof")
        if value["transformed_input_digest"] != effect_request_digest(
            _PROFILE, value["transformed_input"]
        ):
            raise ValueError("changed transformed input")
        creator = _json_row(
            connection,
            "invocations",
            "payload",
            "invocation_id",
            identity.invocation_id,
            MAX_ARCHIVE_BYTES,
        )
        if any(creator.get(name) != item for name, item in value["creator"].items()):
            raise ValueError("changed creator")
        raw_input = _json_row(
            connection,
            "invocation_inputs",
            "payload",
            "invocation_id",
            identity.invocation_id,
            16_777_216,
        )
        if effect_request_digest(_PROFILE, raw_input) != value["original_input_digest"]:
            raise ValueError("changed input")
        input_fp = connection.execute(
            "SELECT fingerprint FROM invocation_inputs WHERE invocation_id=?",
            (identity.invocation_id,),
        ).fetchone()
        if input_fp is None or input_fp[0] != identity.workflow_fingerprint:
            raise ValueError("changed input identity")
        binding = _json_row(
            connection,
            "recovery_invocation_definitions",
            "payload",
            "invocation_id",
            identity.invocation_id,
            MAX_PROFILE_BYTES,
        )
        row = connection.execute(
            "SELECT fingerprint,digest FROM recovery_invocation_definitions WHERE invocation_id=?",
            (identity.invocation_id,),
        ).fetchone()
        if (
            tuple(row) != (identity.workflow_fingerprint, archive_digest(binding))
            or binding["profile"] != PROFILE
            or any(creator.get(name) != item for name, item in binding["identity"].items())
            or binding["input_digest"] != archive_digest(raw_input)
            or binding["runtime_profile_digest"] != archive_digest(binding["runtime_profile"])
        ):
            raise ValueError("changed original definition binding")
        definition = _json_row(
            connection,
            "recovery_definitions",
            "payload",
            "fingerprint",
            identity.workflow_fingerprint,
            MAX_ARCHIVE_BYTES,
        )
        row = connection.execute(
            "SELECT digest FROM recovery_definitions WHERE fingerprint=?",
            (identity.workflow_fingerprint,),
        ).fetchone()
        if (
            row[0] != archive_digest(definition)
            or row[0] != binding["definition_digest"]
            or definition["fingerprint"] != identity.workflow_fingerprint
            or definition["name"] != creator["workflow_name"]
            or definition["version"] != creator["workflow_version"]
        ):
            raise ValueError("changed original source")
        dependency = ReceiptDependency.from_value(value["definition_receipt"])
        expected_identity = EffectIdentity(
            identity.invocation_id,
            identity.workflow_fingerprint,
            "/do",
            (("/do", 1),),
            ADMISSION_OPERATION,
        )
        definition_receipt = _raw_receipt(connection, expected_identity)
        if (
            dependency.identity != expected_identity
            or definition_receipt is None
            or definition_receipt != _expected_receipt(binding)
            or ReceiptDependency.capture(expected_identity, definition_receipt) != dependency
        ):
            raise ValueError("changed definition receipt")
        for key, clock_identity in (
            (
                "clock_receipt",
                root_clock_identity(identity.invocation_id, identity.workflow_fingerprint),
            ),
            ("task_deadline_receipt", policy_identity(identity, "policy:task-deadline:v1")),
        ):
            if value[key] is not None:
                dependency = ReceiptDependency.from_value(value[key])
                receipt = _raw_receipt(connection, clock_identity)
                if (
                    dependency.identity != clock_identity
                    or receipt is None
                    or receipt.status != "completed"
                    or ReceiptDependency.capture(clock_identity, receipt) != dependency
                ):
                    raise ValueError("changed original clock")
                if key == "clock_receipt":
                    clock_started_at(receipt.output)
                else:
                    anchor = DeadlineAnchor.from_value(receipt.output, receipt.output["duration"])
                    if launch.deadline_at > anchor.started_at + anchor.duration:
                        raise ValueError("changed original deadline")
        state = connection.execute(
            "SELECT status FROM sandbox_job_delivery WHERE job_id=?", (lease.job_id,)
        ).fetchone()
        if state is None or state[0] not in {"submitting", "accepted", "uncertain", "terminal"}:
            raise ValueError("missing submit state")
        submit = _json_row(
            connection, "sandbox_job_delivery", "submit_json", "job_id", lease.job_id, 1_048_576
        )
        if (
            submit["job_id"] != lease.job_id
            or submit["admission_digest"] != lease.admission_digest
            or submit["submit_id"] != launch.submit_id
            or submit["request_digest"] != launch.request_digest
            or _raw_receipt(connection, policy_identity(identity, "policy:sandbox-job-submit:v1"))
            != _receipt(submit, "policy:sandbox-job-submit:v1")
        ):
            raise ValueError("changed submit intent")
        control_row = connection.execute(
            "SELECT CASE WHEN length(control_json)<=1048576 THEN control_json ELSE 'invalid' END "
            "FROM sandbox_job_delivery WHERE job_id=?",
            (lease.job_id,),
        ).fetchone()
        if purpose == "submit":
            now = float(connection.execute(f"SELECT {clock}").fetchone()[0])
            if state[0] != "submitting" or control_row[0] is not None or now >= launch.deadline_at:
                raise ValueError("launch prohibited")
        elif purpose == "control":
            control = _decode(control_row[0])
            if (
                state[0] == "terminal"
                or type(control_generation) is not int
                or control_generation != control["generation"]
                or reason != control["reason"]
                or control["job_id"] != lease.job_id
                or control["admission_digest"] != lease.admission_digest
                or _raw_receipt(
                    connection, policy_identity(identity, "policy:sandbox-job-control:v1")
                )
                != _receipt(control, "policy:sandbox-job-control:v1")
            ):
                raise ValueError("changed control intent")
            if (
                reason == "deadline"
                and float(connection.execute(f"SELECT {clock}").fetchone()[0]) < launch.deadline_at
            ):
                raise ValueError("deadline not due")
            if reason == "cancel" and (
                control["principal"] is None
                or control["principal"] != value["creator"]["owner_principal"]
            ):
                raise ValueError("foreign control")
        elif purpose != "inspect":
            raise ValueError("unsupported purpose")
        # Parsing can outlive a short lease. Recheck at the return boundary;
        # the supervisor must still fence its actual ledger/process mutation.
        if (
            connection.execute(
                "SELECT 1 FROM sandbox_job_ownership WHERE job_id=? AND "
                f"admission_digest=? AND owner=? AND generation=? AND expires_at>{clock} "
                f"AND expires_at<={clock}+3600",
                (lease.job_id, lease.admission_digest, lease.owner, lease.generation),
            ).fetchone()
            is None
        ):
            raise InvocationStateConflict("sandbox job backend ownership is stale")
        if (
            purpose == "submit"
            and connection.execute(
                f"SELECT 1 FROM sandbox_job_delivery WHERE job_id=? AND status='submitting' "
                f"AND control_json IS NULL AND {clock}<?",
                (lease.job_id, launch.deadline_at),
            ).fetchone()
            is None
        ):
            raise ValueError("launch became prohibited")
    except InvocationStateConflict:
        raise
    except Exception:
        raise WorkflowExecutionError(
            "sandbox job backend authority is unavailable or inconsistent"
        ) from None
