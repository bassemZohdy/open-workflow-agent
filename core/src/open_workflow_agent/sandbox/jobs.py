"""Durable sandbox job admission and observation, without launch or activation.

Job ownership is independent of its creator's invocation lease. A submit intent
cannot become ready again: an ambiguous OS/controller launch needs exact backend
inspection, never another launch. Backend receipts are supplied only by the
future trusted supervisor SPI; this store does not manufacture that evidence.
This finite durable profile permits environment secret references only. Literal
environment values remain valid synchronous sandbox input but reject here before
admission; they are never dropped or converted to references. Linked/container
lifetime overrides and external script sources need separate qualification.
"""

from __future__ import annotations

import json
import math
import re
import uuid
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import asdict, dataclass, replace
from typing import TYPE_CHECKING, Any, Literal

from .._effect_storage import insert_completed_receipt
from ..effects import EffectIdentity, EffectReceipt, ReceiptDependency, effect_request_digest
from ..errors import InvocationStateConflict, WorkflowExecutionError
from ..ownership import ExecutionLease, owned_write_transaction
from ..persistence import ExecutionHandle
from ..recovery_definitions import _parsed, admission_identity, verify_definition_admission_receipt
from ..storage import StorageConnection
from ..timing import DeadlineAnchor, clock_started_at, root_clock_identity
from .contract import SandboxExecutionRequest, SandboxExecutionResult, SandboxSecretReference

if TYPE_CHECKING:
    from ..services import RuntimeServices
    from .job_backend import SandboxJobOriginalDeadlineProof

ADMISSION_POLICY = "policy:sandbox-job-admission:v1"
_PROFILE = "owa/sandbox-job/v1"
_MAX_BYTES = 1_048_576
_MAX_OUTPUT_BYTES = 262_144
_STATES = {"ready", "submitting", "accepted", "uncertain", "terminal"}
_REASONS = {
    "submit_response_lost",
    "outcome_unavailable",
    "controller_lost",
    "cancel_response_lost",
}


def _failure() -> WorkflowExecutionError:
    return WorkflowExecutionError("sandbox job evidence is unavailable or inconsistent")


def _encoded(value: Any) -> str:
    try:
        effect_request_digest(_PROFILE, value)
        encoded = json.dumps(value, ensure_ascii=False, sort_keys=True, allow_nan=False)
        if len(encoded.encode("utf-8")) > _MAX_BYTES:
            raise ValueError
        return encoded
    except (ValueError, UnicodeError, RecursionError):
        raise _failure() from None


def _unique(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for name, value in pairs:
        if not isinstance(name, str) or name in result:
            raise _failure()
        result[name] = value
    return result


def _decode(value: Any) -> Any:
    try:
        if not isinstance(value, str) or len(value.encode("utf-8")) > _MAX_BYTES:
            raise ValueError
        decoded = json.loads(value, object_pairs_hook=_unique)
        _encoded(decoded)
        return decoded
    except (ValueError, UnicodeError, RecursionError):
        raise _failure() from None


def _reference(value: Any) -> str:
    if (
        not isinstance(value, str)
        or re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,255}", value) is None
    ):
        raise _failure()
    return value


def _digest(value: Any) -> str:
    if not isinstance(value, str) or re.fullmatch(r"[a-f0-9]{64}", value) is None:
        raise _failure()
    return value


def job_id(identity: EffectIdentity) -> str:
    if identity.operation not in {"run:script", "run:shell", "run:container"}:
        raise _failure()
    return "job_" + effect_request_digest(_PROFILE, asdict(identity))


def policy_identity(identity: EffectIdentity, policy: str = ADMISSION_POLICY) -> EffectIdentity:
    return replace(identity, operation=policy)


def _creator(handle: ExecutionHandle) -> dict[str, Any]:
    return {
        name: getattr(handle, name)
        for name in (
            "invocation_id",
            "engine",
            "workflow_name",
            "workflow_version",
            "workflow_fingerprint",
            "session_id",
            "user_id",
            "owner_principal",
            "parent_invocation_id",
            "parent_task_reference",
        )
    }


@dataclass(frozen=True, slots=True)
class SandboxJobProfile:
    backend: Literal["internal", "docker", "kubernetes"]
    deployment_id: str
    revision: str
    policy_digest: str
    timeout_seconds: float = 300

    def __post_init__(self) -> None:
        if self.backend not in {"internal", "docker", "kubernetes"}:
            raise _failure()
        _reference(self.deployment_id)
        _reference(self.revision)
        _digest(self.policy_digest)
        if (
            type(self.timeout_seconds) not in {int, float}
            or not math.isfinite(self.timeout_seconds)
            or not 0 < self.timeout_seconds <= 3600
        ):
            raise _failure()


@dataclass(frozen=True, slots=True)
class SandboxJobLease:
    job_id: str
    admission_digest: str
    owner: str
    generation: int
    expires_at: float

    def __post_init__(self) -> None:
        if (
            not isinstance(self.job_id, str)
            or re.fullmatch(r"job_[a-f0-9]{64}", self.job_id) is None
        ):
            raise _failure()
        _digest(self.admission_digest)
        if (
            not isinstance(self.owner, str)
            or re.fullmatch(r"[a-f0-9]{32}", self.owner) is None
            or type(self.generation) is not int
            or self.generation < 1
        ):
            raise _failure()
        if (
            type(self.expires_at) not in {int, float}
            or not math.isfinite(self.expires_at)
            or self.expires_at <= 0
        ):
            raise _failure()


@dataclass(frozen=True, slots=True)
class SandboxJobBackendBinding:
    job_id: str
    request_digest: str
    controller_id: str
    instance_id: str

    def __post_init__(self) -> None:
        if (
            not isinstance(self.job_id, str)
            or re.fullmatch(r"job_[a-f0-9]{64}", self.job_id) is None
        ):
            raise _failure()
        _digest(self.request_digest)
        _reference(self.controller_id)
        _reference(self.instance_id)


@dataclass(frozen=True, slots=True)
class SandboxJob:
    job_id: str
    identity: EffectIdentity
    admission_json: str
    admission_digest: str
    status: str
    submit_json: str | None
    backend_json: str | None
    terminal_json: str | None
    uncertainty: str | None
    control_json: str | None
    control_ack_json: str | None
    receipt: EffectReceipt

    @property
    def transformed_input(self) -> Any:
        return _decode(self.admission_json)["transformed_input"]


def _request(request: SandboxExecutionRequest, identity: EffectIdentity) -> dict[str, Any]:
    if (
        request.execution_id != job_id(identity)
        or request.kind != identity.operation.removeprefix("run:")
        or request.invocation_id != identity.invocation_id
        or request.task_reference != identity.task_reference
    ):
        raise _failure()
    for value in (
        request.command,
        request.stdin,
        request.script_language,
        request.script_code,
        request.image,
    ):
        if value is not None and (not isinstance(value, str) or "\x00" in value):
            raise _failure()
    if request.kind == "script" and (
        not request.script_language
        or not request.script_code
        or request.command is not None
        or request.image is not None
    ):
        raise _failure()
    if request.kind == "shell" and (
        not request.command
        or request.script_language is not None
        or request.script_code is not None
        or request.image is not None
    ):
        raise _failure()
    if request.kind == "container" and (
        not request.image or request.script_language is not None or request.script_code is not None
    ):
        raise _failure()
    environment: dict[str, Any] = {}
    for name, environment_value in request.environment:
        if (
            not isinstance(name, str)
            or re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]{0,127}", name) is None
            or name in environment
            or not isinstance(environment_value, SandboxSecretReference)
        ):
            # Narrow durable profile: no resolved or literal environment values.
            raise WorkflowExecutionError(
                "durable sandbox jobs require environment secret references"
            )
        environment[name] = {"secret_reference": _reference(environment_value.name)}
    if (
        len(environment) > 64
        or len(request.arguments) > 128
        or any(not isinstance(value, str) or "\x00" in value for value in request.arguments)
    ):
        raise _failure()
    prepared = {
        "execution_id": request.execution_id,
        "kind": request.kind,
        "command": request.command,
        "arguments": list(request.arguments),
        "stdin": request.stdin,
        "environment": environment,
        "script_language": request.script_language,
        "script_code": request.script_code,
        "image": request.image,
        "invocation_id": request.invocation_id,
        "task_reference": request.task_reference,
    }
    _encoded(prepared)
    return prepared


def _receipt(value: dict[str, Any], policy: str) -> EffectReceipt:
    return EffectReceipt(
        request_digest=effect_request_digest(policy, value),
        owner="sandbox-jobs-v1",
        status="completed",
        output=value,
    )


def _raw_receipt(connection: StorageConnection, identity: EffectIdentity) -> EffectReceipt | None:
    row = connection.execute(
        "SELECT owner,status,CASE WHEN length(payload)<=? THEN payload ELSE NULL END "
        "FROM workflow_effects WHERE identity=?",
        (_MAX_BYTES, identity.key),
    ).fetchone()
    index = connection.execute(
        "SELECT invocation_id,fingerprint,CASE WHEN length(payload)<=? THEN payload ELSE NULL END "
        "FROM workflow_effect_invocations WHERE identity=?",
        (_MAX_BYTES, identity.key),
    ).fetchone()
    if row is None and index is None:
        return None
    if row is None or index is None or row[2] is None or index[2] is None:
        raise _failure()
    try:
        value = EffectReceipt(**_decode(row[2]))
        if (
            tuple(row[:2]) != (value.owner, value.status)
            or tuple(index[:2]) != (identity.invocation_id, identity.workflow_fingerprint)
            or EffectIdentity.from_value(_decode(index[2])) != identity
        ):
            raise ValueError
        return value
    except (ValueError, TypeError):
        raise _failure() from None


class SandboxJobStore:
    def __init__(
        self,
        services: RuntimeServices,
        *,
        profile_provider: Callable[[], SandboxJobProfile],
        max_pending: int = 1000,
    ) -> None:
        if type(max_pending) is not int or not 1 <= max_pending <= 10_000:
            raise ValueError("sandbox job capacity must be bounded")
        self.services = services
        self.connection = services.effect_journal.connection
        self.profile_provider = profile_provider
        self.max_pending = max_pending
        self.connection.execute("""CREATE TABLE IF NOT EXISTS sandbox_jobs (
            job_id TEXT PRIMARY KEY, invocation_id TEXT NOT NULL, fingerprint TEXT NOT NULL,
            payload TEXT NOT NULL, digest TEXT NOT NULL)""")
        self.connection.execute("""CREATE TABLE IF NOT EXISTS sandbox_job_delivery (
            job_id TEXT PRIMARY KEY, status TEXT NOT NULL, submit_json TEXT, backend_json TEXT,
            terminal_json TEXT, uncertainty TEXT, control_json TEXT, control_ack_json TEXT)""")
        self.connection.execute("""CREATE TABLE IF NOT EXISTS sandbox_job_ownership (
            job_id TEXT PRIMARY KEY, admission_digest TEXT NOT NULL, owner TEXT NOT NULL,
            generation BIGINT NOT NULL, expires_at DOUBLE PRECISION NOT NULL)""")
        self.connection.execute(
            "CREATE TABLE IF NOT EXISTS sandbox_job_guard "
            "(id TEXT PRIMARY KEY, marker BIGINT NOT NULL)"
        )
        self.connection.execute(
            "INSERT INTO sandbox_job_guard VALUES ('global',0) ON CONFLICT(id) DO NOTHING"
        )
        self.connection.commit()
        self.services.effect_journal._sandbox_job_verifier = self.verify_receipt

    @property
    def _clock(self) -> str:
        return (
            "EXTRACT(EPOCH FROM clock_timestamp())"
            if self.connection.is_postgresql
            else "((julianday('now') - 2440587.5) * 86400.0)"
        )

    def _now(self) -> float:
        return float(self.connection.execute(f"SELECT {self._clock}").fetchone()[0])

    def _lock(self) -> None:
        if (
            self.connection.execute(
                "UPDATE sandbox_job_guard SET marker=marker WHERE id='global'"
            ).rowcount
            != 1
        ):
            raise _failure()

    @contextmanager
    def _transaction(self, lease: SandboxJobLease | None = None) -> Iterator[None]:
        def enter() -> None:
            self._lock()
            if lease is not None:
                row = self.connection.execute(
                    "SELECT 1 FROM sandbox_job_ownership WHERE job_id=? AND admission_digest=? "
                    f"AND owner=? AND generation=? AND expires_at>{self._clock} "
                    f"AND expires_at<={self._clock}+3600",
                    (lease.job_id, lease.admission_digest, lease.owner, lease.generation),
                ).fetchone()
                if row is None:
                    raise InvocationStateConflict("sandbox job ownership has expired or changed")

        if self.connection.is_postgresql:
            with self.connection.raw.transaction():
                enter()
                yield
        else:
            try:
                enter()
                yield
                self.connection.commit()
            except BaseException:
                self.connection.raw.rollback()
                raise

    def _definition(self, value: dict[str, Any]) -> ExecutionHandle:
        handle = self.services.invocations.get(value["identity"]["invocation_id"])
        if (
            handle is None
            or _creator(handle) != value["creator"]
            or value["original_input_digest"]
            != effect_request_digest(_PROFILE, self._original_input(handle))
        ):
            raise _failure()
        dependency = ReceiptDependency.from_value(value["definition_receipt"])
        if dependency.identity != admission_identity(handle):
            raise _failure()
        receipt = _raw_receipt(self.connection, dependency.identity)
        if receipt is None or ReceiptDependency.capture(dependency.identity, receipt) != dependency:
            raise _failure()
        verify_definition_admission_receipt(
            self.connection, self.services.invocations, dependency.identity, receipt
        )
        return handle

    def _original_input(self, handle: ExecutionHandle) -> Any:
        row = self.connection.execute(
            "SELECT fingerprint,CASE WHEN length(payload)<=16777216 THEN payload ELSE NULL END "
            "FROM invocation_inputs WHERE invocation_id=?",
            (handle.invocation_id,),
        ).fetchone()
        if row is None or row[0] != handle.workflow_fingerprint or row[1] is None:
            raise _failure()
        return _parsed(row[1], limit=16_777_216)

    def _authority(self, job: SandboxJob) -> None:
        value = _decode(job.admission_json)
        handle = self._definition(value)
        original = self.services.recovery_definitions.resolve(handle)
        task = next(
            (
                task
                for scope in original.scopes
                for task in scope.tasks
                if (task.execution_reference or task.reference) == job.identity.task_reference
            ),
            None,
        )
        if task is None:
            raise _failure()
        if task.definition.get("run", {}).get("await", True) is False:
            self._verify_raw_action(job)
        profile = self.profile_provider()
        if (
            not isinstance(profile, SandboxJobProfile)
            or asdict(profile) != value["backend_profile"]
        ):
            raise InvocationStateConflict("sandbox job deployment authority has changed")

    def _event(self, identity: EffectIdentity, policy: str, value: dict[str, Any] | None) -> None:
        receipt = _raw_receipt(self.connection, policy_identity(identity, policy))
        if value is None:
            if receipt is not None:
                raise _failure()
        elif receipt != _receipt(value, policy):
            raise _failure()

    def get(self, identifier: str) -> SandboxJob | None:
        if not isinstance(identifier, str) or re.fullmatch(r"job_[a-f0-9]{64}", identifier) is None:
            raise _failure()
        row = self.connection.execute(
            "SELECT invocation_id,fingerprint,CASE WHEN length(payload)<=? THEN payload "
            "ELSE NULL END,digest FROM sandbox_jobs WHERE job_id=?",
            (_MAX_BYTES, identifier),
        ).fetchone()
        bounded_columns = [
            f"CASE WHEN {name} IS NULL OR length({name})<={_MAX_BYTES} "
            f"THEN {name} ELSE 'OWA_BOUND_EXCEEDED' END"
            for name in (
                "submit_json",
                "backend_json",
                "terminal_json",
                "control_json",
                "control_ack_json",
            )
        ]
        state = self.connection.execute(
            "SELECT CASE WHEN length(status)<=16 THEN status ELSE 'invalid' END,"
            + ",".join(bounded_columns[:3])
            + ",CASE WHEN uncertainty IS NULL OR "
            "length(uncertainty)<=128 THEN uncertainty ELSE 'invalid' END,"
            + ",".join(bounded_columns[3:])
            + " FROM sandbox_job_delivery WHERE job_id=?",
            (identifier,),
        ).fetchone()
        if row is None and state is None:
            return None
        try:
            if row is None or state is None or state[0] not in _STATES or row[2] is None:
                raise ValueError
            value = _decode(row[2])
            if (
                not isinstance(value, dict)
                or set(value)
                != {
                    "profile",
                    "identity",
                    "creator",
                    "original_input_digest",
                    "definition_receipt",
                    "clock_receipt",
                    "task_deadline_receipt",
                    "request",
                    "request_digest",
                    "transformed_input",
                    "transformed_input_digest",
                    "backend_profile",
                    "deadline_at",
                    "lifetime",
                }
                or value["profile"] != _PROFILE
                or value["lifetime"] != "independent"
            ):
                raise ValueError
            identity = EffectIdentity.from_value(value["identity"])
            digest = effect_request_digest(ADMISSION_POLICY, value)
            profile = SandboxJobProfile(**value["backend_profile"])
            if (
                job_id(identity) != identifier
                or tuple(row[:2]) != (identity.invocation_id, identity.workflow_fingerprint)
                or row[3] != digest
                or value["request_digest"] != effect_request_digest(_PROFILE, value["request"])
                or value["transformed_input_digest"]
                != effect_request_digest(_PROFILE, value["transformed_input"])
            ):
                raise ValueError
            raw_request = dict(value["request"])
            environment = raw_request.pop("environment")
            if not isinstance(environment, dict) or any(
                not isinstance(item, dict) or set(item) != {"secret_reference"}
                for item in environment.values()
            ):
                raise ValueError
            raw_request["environment"] = tuple(
                (name, SandboxSecretReference(item["secret_reference"]))
                for name, item in environment.items()
            )
            raw_request["arguments"] = tuple(raw_request["arguments"])
            if _request(SandboxExecutionRequest(**raw_request), identity) != value["request"]:
                raise ValueError
            deadline = value["deadline_at"]
            if type(deadline) not in {int, float} or not math.isfinite(deadline) or deadline <= 0:
                raise ValueError
            self._definition(value)
            for key, expected_identity in (
                (
                    "clock_receipt",
                    root_clock_identity(identity.invocation_id, identity.workflow_fingerprint),
                ),
                ("task_deadline_receipt", policy_identity(identity, "policy:task-deadline:v1")),
            ):
                if value[key] is not None:
                    dependency = ReceiptDependency.from_value(value[key])
                    clock = _raw_receipt(self.connection, dependency.identity)
                    if (
                        dependency.identity != expected_identity
                        or clock is None
                        or clock.status != "completed"
                        or ReceiptDependency.capture(dependency.identity, clock) != dependency
                    ):
                        raise ValueError
                    if key == "clock_receipt":
                        clock_started_at(clock.output)
                    else:
                        anchor = DeadlineAnchor.from_value(clock.output, clock.output["duration"])
                        if deadline > anchor.started_at + anchor.duration:
                            raise ValueError
            receipt = _raw_receipt(self.connection, policy_identity(identity))
            if receipt is None or receipt != _receipt(value, ADMISSION_POLICY):
                raise ValueError
            events = [
                _decode(item) if item is not None else None
                for item in (state[1], state[2], state[3], state[5], state[6])
            ]
            submit, backend, terminal, control, ack = events
            for policy, event in zip(
                (
                    "policy:sandbox-job-submit:v1",
                    "policy:sandbox-job-accepted:v1",
                    "policy:sandbox-job-terminal:v1",
                    "policy:sandbox-job-control:v1",
                    "policy:sandbox-job-control-ack:v1",
                ),
                events,
                strict=True,
            ):
                self._event(identity, policy, event)
                if event is not None and (
                    event.get("job_id") != identifier or event.get("admission_digest") != digest
                ):
                    raise ValueError
            if submit is not None and (
                set(submit)
                != {
                    "job_id",
                    "admission_digest",
                    "request_digest",
                    "submit_id",
                    "owner",
                    "generation",
                }
                or submit["request_digest"] != value["request_digest"]
                or not isinstance(submit["submit_id"], str)
                or re.fullmatch(r"[a-f0-9]{32}", submit["submit_id"]) is None
                or not isinstance(submit["owner"], str)
                or re.fullmatch(r"[a-f0-9]{32}", submit["owner"]) is None
                or type(submit["generation"]) is not int
                or submit["generation"] < 1
            ):
                raise ValueError
            if backend is not None and set(backend) != {"job_id", "admission_digest", "binding"}:
                raise ValueError
            if control is not None and (
                set(control) != {"job_id", "admission_digest", "reason", "principal", "generation"}
                or control["reason"] not in {"cancel", "deadline"}
                or type(control["generation"]) is not int
                or control["generation"] != 1
                or control["reason"] == "cancel"
                and (
                    control["principal"] is None
                    or control["principal"] != value["creator"]["owner_principal"]
                )
            ):
                raise ValueError
            if ack is not None and (
                set(ack)
                != {"job_id", "admission_digest", "binding", "generation", "control_digest"}
                or control is None
                or backend is None
                or ack["generation"] != control["generation"]
                or ack["binding"] != backend["binding"]
                or ack["control_digest"] != effect_request_digest(_PROFILE, control)
            ):
                raise ValueError
            deadline_policy = policy_identity(identity, "policy:sandbox-job-original-deadline:v1")
            deadline_receipt = _raw_receipt(self.connection, deadline_policy)
            if deadline_receipt is not None:
                deadline_value = deadline_receipt.output
                if not isinstance(deadline_value, dict) or submit is None:
                    raise ValueError
                self._deadline_evidence(value, deadline_value["binding"], deadline_value["outcome"])
                self._binding(value, SandboxJobBackendBinding(**deadline_value["binding"]))
                self._outcome(identifier, SandboxExecutionResult(**deadline_value["outcome"]))
                if backend is not None and backend["binding"] != deadline_value["binding"]:
                    raise ValueError
            elif (
                self.connection.execute(
                    "SELECT 1 FROM workflow_effect_invocations WHERE identity=?",
                    (deadline_policy.key,),
                ).fetchone()
                is not None
            ):
                raise ValueError
            unstarted_terminal = terminal is not None and terminal.get("reason") == "not_launched"
            if (
                state[0] == "ready"
                and any(item is not None for item in (submit, backend, terminal, state[4]))
                or state[0] != "ready"
                and submit is None
                or (state[0] == "terminal") != (terminal is not None)
                or (state[0] == "uncertain") != (state[4] is not None)
            ):
                if not (
                    state[0] == "terminal"
                    and unstarted_terminal
                    and submit is None
                    and backend is None
                    and state[4] is None
                ):
                    raise ValueError
            if state[4] is not None and state[4] not in _REASONS:
                raise ValueError
            if state[0] == "accepted" and backend is None or ack is not None and control is None:
                raise ValueError
            if backend is not None:
                self._binding(value, SandboxJobBackendBinding(**backend["binding"]))
            if terminal is not None:
                if set(terminal) != {"job_id", "admission_digest", "binding", "outcome", "reason"}:
                    raise ValueError
                if unstarted_terminal:
                    if (
                        terminal["binding"] is not None
                        or terminal["outcome"] is not None
                        or control is None
                        or ack is not None
                        or submit is not None
                    ):
                        raise ValueError
                else:
                    if (
                        terminal["reason"] not in {"exited", "cancel", "deadline"}
                        or terminal["reason"] != "exited"
                        and not (
                            terminal["reason"] == "deadline"
                            and self._deadline_evidence(
                                value, terminal["binding"], terminal["outcome"]
                            )
                        )
                        and (
                            ack is None
                            or control is None
                            or control["reason"] != terminal["reason"]
                        )
                    ):
                        raise ValueError
                    self._binding(value, SandboxJobBackendBinding(**terminal["binding"]))
                    self._outcome(identifier, SandboxExecutionResult(**terminal["outcome"]))
                    if backend is not None and backend["binding"] != terminal["binding"]:
                        raise ValueError
            if profile.backend not in {"internal", "docker", "kubernetes"}:
                raise ValueError
            return SandboxJob(
                identifier,
                identity,
                row[2],
                digest,
                state[0],
                state[1],
                state[2],
                state[3],
                state[4],
                state[5],
                state[6],
                receipt,
            )
        except InvocationStateConflict:
            raise
        except Exception:
            raise _failure() from None

    def admit(
        self,
        identity: EffectIdentity,
        handle: ExecutionHandle,
        request: SandboxExecutionRequest,
        transformed_input: Any,
        *,
        deadline_at: float,
        execution_lease: ExecutionLease,
        lifetime: str = "independent",
        _action: bool = False,
    ) -> SandboxJob:
        identifier = job_id(identity)
        if (
            not isinstance(execution_lease, ExecutionLease)
            or identity.invocation_id != handle.invocation_id
            or identity.workflow_fingerprint != handle.workflow_fingerprint
            or execution_lease.engine != handle.engine
            or execution_lease != handle._execution_lease
            or lifetime != "independent"
        ):
            raise InvocationStateConflict("sandbox job admission requires the original live owner")
        profile = self.profile_provider()
        if not isinstance(profile, SandboxJobProfile):
            raise _failure()
        original = self.services.recovery_definitions.resolve(handle)
        task = next(
            (
                task
                for scope in original.scopes
                for task in scope.tasks
                if (task.execution_reference or task.reference) == identity.task_reference
            ),
            None,
        )
        if (
            task is None
            or not isinstance(task.definition.get("run"), dict)
            or request.kind not in task.definition["run"]
        ):
            raise _failure()
        configuration = task.definition["run"][request.kind]
        if (
            not isinstance(configuration, dict)
            or request.kind == "script"
            and "source" in configuration
            or request.kind == "container"
            and "lifetime" in configuration
        ):
            raise WorkflowExecutionError(
                "sandbox job source requires an unsupported lifetime or source profile"
            )
        prior = self.get(identifier)
        now = self._now()
        if (
            type(deadline_at) not in {int, float}
            or not math.isfinite(deadline_at)
            or deadline_at <= 0
            or prior is None
            and not now < deadline_at <= now + profile.timeout_seconds
        ):
            raise WorkflowExecutionError("sandbox job original deadline is unavailable or expired")
        task_clock = self.services.effect_journal.get(
            policy_identity(identity, "policy:task-deadline:v1")
        )
        timeout = original.execution_source.get("timeout")
        root_clock = self.services.effect_journal.get(
            root_clock_identity(handle.invocation_id, handle.workflow_fingerprint)
        )
        if timeout is not None:
            from ..workflow import _duration_seconds

            duration = _duration_seconds(timeout)
            if (
                root_clock is None
                or duration is None
                or deadline_at > clock_started_at(root_clock.output) + duration
            ):
                raise _failure()
        if task_clock is not None:
            anchor = DeadlineAnchor.from_value(task_clock.output, task_clock.output["duration"])
            if deadline_at > anchor.started_at + anchor.duration:
                raise _failure()
        elif task.definition.get("timeout") is not None:
            raise _failure()
        definition_identity = admission_identity(handle)
        definition = self.services.effect_journal.get(definition_identity)
        if definition is None:
            raise _failure()
        _encoded(transformed_input)
        value = {
            "profile": _PROFILE,
            "identity": asdict(identity),
            "creator": _creator(handle),
            "original_input_digest": effect_request_digest(_PROFILE, self._original_input(handle)),
            "definition_receipt": asdict(
                ReceiptDependency.capture(definition_identity, definition)
            ),
            "clock_receipt": asdict(
                ReceiptDependency.capture(
                    root_clock_identity(handle.invocation_id, handle.workflow_fingerprint),
                    root_clock,
                )
            )
            if root_clock is not None
            else None,
            "task_deadline_receipt": asdict(
                ReceiptDependency.capture(
                    policy_identity(identity, "policy:task-deadline:v1"), task_clock
                )
            )
            if task_clock is not None
            else None,
            "request": _request(request, identity),
            "request_digest": None,
            "transformed_input": transformed_input,
            "transformed_input_digest": effect_request_digest(_PROFILE, transformed_input),
            "backend_profile": asdict(profile),
            "deadline_at": float(deadline_at),
            "lifetime": lifetime,
        }
        value["request_digest"] = effect_request_digest(_PROFILE, value["request"])
        encoded = _encoded(value)
        with owned_write_transaction(
            self.connection,
            execution_lease,
            invocation_id=handle.invocation_id,
            workflow_fingerprint=handle.workflow_fingerprint,
        ):
            self._lock()
            current = self.services.invocations.get(handle.invocation_id)
            if (
                current is None
                or _creator(current) != _creator(handle)
                or current.status != "running"
            ):
                raise InvocationStateConflict("sandbox job creator has changed")
            self.services.recovery_definitions.resolve(current)
            if self.profile_provider() != profile:
                raise InvocationStateConflict("sandbox job authority changed during admission")
            prior = self.get(identifier)
            if prior is not None:
                if _decode(prior.admission_json) != value:
                    raise InvocationStateConflict("sandbox job cannot be rebound")
                if _action:
                    self.verify_action(identity, self.services.effect_journal.get(identity))
                return prior
            if (
                _raw_receipt(self.connection, policy_identity(identity)) is not None
                or self.services.effect_journal.get(identity) is not None
                or self.connection.execute(
                    "SELECT 1 FROM workflow_effect_invocations WHERE identity IN (?,?)",
                    (identity.key, policy_identity(identity).key),
                ).fetchone()
                is not None
            ):
                raise _failure()
            if (
                self.connection.execute(
                    "SELECT COUNT(*) FROM sandbox_job_delivery WHERE status!='terminal'"
                ).fetchone()[0]
                >= self.max_pending
            ):
                raise WorkflowExecutionError("sandbox job admission capacity is exhausted")
            digest = effect_request_digest(ADMISSION_POLICY, value)
            self.connection.execute(
                "INSERT INTO sandbox_jobs VALUES (?,?,?,?,?)",
                (identifier, handle.invocation_id, handle.workflow_fingerprint, encoded, digest),
            )
            self.connection.execute(
                "INSERT INTO sandbox_job_delivery(job_id,status) VALUES (?,'ready')", (identifier,)
            )
            insert_completed_receipt(
                self.connection, policy_identity(identity), _receipt(value, ADMISSION_POLICY)
            )
            if _action:
                insert_completed_receipt(self.connection, identity, self._action_receipt(value))
            result = self.get(identifier)
            if result is None:
                raise _failure()
            return result

    @staticmethod
    def _action_receipt(value: dict[str, Any]) -> EffectReceipt:
        identity = EffectIdentity.from_value(value["identity"])
        return EffectReceipt(
            effect_request_digest(
                identity.operation,
                {"request": value["request"], "input": value["transformed_input"]},
            ),
            "sandbox-job:" + job_id(identity),
            status="completed",
            output=value["transformed_input"],
        )

    def admit_action(
        self,
        identity: EffectIdentity,
        handle: ExecutionHandle,
        request: SandboxExecutionRequest,
        transformed_input: Any,
        *,
        deadline_at: float,
        execution_lease: ExecutionLease,
    ) -> tuple[SandboxJob, EffectReceipt]:
        original = self.services.recovery_definitions.resolve(handle)
        task = next(
            (
                task
                for scope in original.scopes
                for task in scope.tasks
                if (task.execution_reference or task.reference) == identity.task_reference
            ),
            None,
        )
        if task is None or task.definition.get("run", {}).get("await", True) is not False:
            raise _failure()
        job = self.admit(
            identity,
            handle,
            request,
            transformed_input,
            deadline_at=deadline_at,
            execution_lease=execution_lease,
            _action=True,
        )
        receipt = self.services.effect_journal.get(identity)
        self.verify_action(identity, receipt)
        if receipt is None:
            raise _failure()
        return job, receipt

    def _verify_raw_action(self, job: SandboxJob) -> EffectReceipt:
        receipt = _raw_receipt(self.connection, job.identity)
        if receipt != self._action_receipt(_decode(job.admission_json)):
            raise _failure()
        if receipt is None:
            raise _failure()
        return receipt

    def verify_action(self, identity: EffectIdentity, receipt: EffectReceipt | None) -> None:
        job = self.get(job_id(identity))
        if job is None or receipt != self._verify_raw_action(job):
            raise _failure()
        self._authority(job)

    def verify_receipt(self, identity: EffectIdentity, receipt: EffectReceipt) -> None:
        if identity.operation in {"run:script", "run:shell", "run:container"}:
            # Ordinary synchronous receipts remain independent. A retained job
            # or job acceptance owner requires both durable proofs.
            if receipt.owner.startswith("sandbox-job:") or self.get(job_id(identity)) is not None:
                self.verify_action(identity, receipt)
            return
        if identity.operation == ADMISSION_POLICY:
            verify_job_admission_receipt(self, identity, receipt)
            business = replace(identity, operation="run:" + receipt.output["request"]["kind"])
            job = self.get(job_id(business))
            if job is None:
                raise _failure()
            self._authority(job)
            return
        # Observation policies are verified by the complete store record.
        identifier = receipt.output.get("job_id") if isinstance(receipt.output, dict) else None
        job = self.get(identifier) if isinstance(identifier, str) else None
        if job is None:
            raise _failure()
        self._authority(job)

    def pending(self, *, limit: int = 100, after: str | None = None) -> tuple[str, ...]:
        """Bounded cursor scan; a damaged job cannot hide later queue entries."""
        if type(limit) is not int or not 1 <= limit <= 1000:
            raise ValueError("sandbox job scan must be bounded")
        if after is not None:
            if not isinstance(after, str) or len(after.encode("utf-8")) > 256:
                raise ValueError("sandbox job scan cursor must be bounded")
        rows = self.connection.execute(
            "SELECT job_id FROM sandbox_job_delivery WHERE status!='terminal' "
            "AND job_id>? ORDER BY job_id LIMIT ?",
            (after or "", limit),
        ).fetchall()
        return tuple(row[0] for row in rows)

    def claim(self, identifier: str, *, seconds: float = 30) -> SandboxJobLease:
        if (
            type(seconds) not in {int, float}
            or not math.isfinite(seconds)
            or not 0 < seconds <= 3600
        ):
            raise ValueError("sandbox job ownership duration must be bounded")
        job = self.get(identifier)
        if job is None:
            raise _failure()
        self._authority(job)
        with self._transaction():
            current = self.get(identifier)
            if current is None or current.admission_digest != job.admission_digest:
                raise _failure()
            self._authority(current)
            row = self.connection.execute(
                f"""INSERT INTO sandbox_job_ownership VALUES (?,?,?,1,{self._clock}+?)
                ON CONFLICT(job_id) DO UPDATE SET owner=excluded.owner,
                generation=sandbox_job_ownership.generation+1,expires_at=excluded.expires_at
                WHERE sandbox_job_ownership.admission_digest=excluded.admission_digest
                AND sandbox_job_ownership.generation>0
                AND sandbox_job_ownership.expires_at<={self._clock}
                RETURNING job_id,admission_digest,owner,generation,expires_at""",
                (identifier, job.admission_digest, uuid.uuid4().hex, seconds),
            ).fetchone()
            if row is None:
                raise InvocationStateConflict("sandbox job ownership is unavailable")
            return SandboxJobLease(*row)

    def renew(self, lease: SandboxJobLease, *, seconds: float = 30) -> SandboxJobLease:
        if (
            type(seconds) not in {int, float}
            or not math.isfinite(seconds)
            or not 0 < seconds <= 3600
        ):
            raise ValueError("sandbox job ownership duration must be bounded")
        with self._transaction(lease):
            job = self.get(lease.job_id)
            if job is None:
                raise _failure()
            self._authority(job)
            row = self.connection.execute(
                f"UPDATE sandbox_job_ownership SET expires_at={self._clock}+? WHERE job_id=? "
                "RETURNING job_id,admission_digest,owner,generation,expires_at",
                (seconds, lease.job_id),
            ).fetchone()
            return SandboxJobLease(*row)

    def verify(self, lease: SandboxJobLease) -> SandboxJob:
        """Read-only pre-IPC check; a backend must still fence its actual mutation."""
        current = self.connection.execute(
            "SELECT 1 FROM sandbox_job_ownership WHERE job_id=? AND admission_digest=? "
            f"AND owner=? AND generation=? AND expires_at>{self._clock} "
            f"AND expires_at<={self._clock}+3600",
            (lease.job_id, lease.admission_digest, lease.owner, lease.generation),
        ).fetchone()
        if current is None:
            raise InvocationStateConflict("sandbox job ownership has expired or changed")
        return self._owned(lease)

    def release(self, lease: SandboxJobLease) -> None:
        with self._transaction(lease):
            self.connection.execute(
                "UPDATE sandbox_job_ownership SET expires_at=0 WHERE job_id=?", (lease.job_id,)
            )

    def _owned(self, lease: SandboxJobLease) -> SandboxJob:
        job = self.get(lease.job_id)
        if job is None or job.admission_digest != lease.admission_digest:
            raise _failure()
        self._authority(job)
        return job

    def _write_event(
        self, job: SandboxJob, policy: str, column: str, value: dict[str, Any]
    ) -> None:
        insert_completed_receipt(
            self.connection, policy_identity(job.identity, policy), _receipt(value, policy)
        )
        self.connection.execute(
            f"UPDATE sandbox_job_delivery SET {column}=? WHERE job_id=?",
            (_encoded(value), job.job_id),
        )

    @staticmethod
    def _value(job: SandboxJob, **values: Any) -> dict[str, Any]:
        return {"job_id": job.job_id, "admission_digest": job.admission_digest, **values}

    def begin_submit(self, lease: SandboxJobLease) -> dict[str, Any]:
        with self._transaction(lease):
            job = self._owned(lease)
            if job.status != "ready" or job.control_json is not None:
                raise WorkflowExecutionError(
                    "sandbox job requires inspection; a new launch is forbidden"
                )
            if self._now() >= _decode(job.admission_json)["deadline_at"]:
                raise WorkflowExecutionError("sandbox job original deadline has expired")
            value = self._value(
                job,
                request_digest=_decode(job.admission_json)["request_digest"],
                submit_id=uuid.uuid4().hex,
                owner=lease.owner,
                generation=lease.generation,
            )
            self._write_event(job, "policy:sandbox-job-submit:v1", "submit_json", value)
            self.connection.execute(
                "UPDATE sandbox_job_delivery SET status='submitting' WHERE job_id=?", (job.job_id,)
            )
            return value

    @staticmethod
    def _binding(value: dict[str, Any], binding: SandboxJobBackendBinding) -> None:
        if (
            binding.job_id != job_id(EffectIdentity.from_value(value["identity"]))
            or binding.request_digest != value["request_digest"]
            or binding.controller_id != value["backend_profile"]["deployment_id"]
        ):
            raise _failure()

    def record_accepted(self, lease: SandboxJobLease, binding: SandboxJobBackendBinding) -> None:
        with self._transaction(lease):
            job = self._owned(lease)
            self._binding(_decode(job.admission_json), binding)
            if job.status == "ready" or job.status == "terminal":
                raise InvocationStateConflict("sandbox job cannot accept a new backend launch")
            value = self._value(job, binding=asdict(binding))
            self._write_event(job, "policy:sandbox-job-accepted:v1", "backend_json", value)
            self.connection.execute(
                "UPDATE sandbox_job_delivery SET status='accepted',uncertainty=NULL WHERE job_id=?",
                (job.job_id,),
            )
            self.get(job.job_id)

    def mark_uncertain(self, lease: SandboxJobLease, *, reason: str) -> None:
        if reason not in _REASONS:
            raise _failure()
        with self._transaction(lease):
            job = self._owned(lease)
            if job.status in {"ready", "terminal"}:
                raise InvocationStateConflict("sandbox job uncertainty requires a prior submit")
            self.connection.execute(
                "UPDATE sandbox_job_delivery SET status='uncertain',uncertainty=? WHERE job_id=?",
                (reason, job.job_id),
            )

    def request_control(
        self,
        lease: SandboxJobLease,
        *,
        reason: Literal["cancel", "deadline"],
        principal: str | None = None,
    ) -> dict[str, Any]:
        with self._transaction(lease):
            job = self._owned(lease)
            value = _decode(job.admission_json)
            if reason == "cancel":
                if principal is None or principal != value["creator"]["owner_principal"]:
                    raise InvocationStateConflict("sandbox job control is not authorized")
            elif reason != "deadline" or self._now() < value["deadline_at"]:
                raise InvocationStateConflict("sandbox job deadline control is not due")
            control = self._value(job, reason=reason, principal=principal, generation=1)
            if job.control_json is not None:
                if _decode(job.control_json) != control:
                    raise InvocationStateConflict("sandbox job first control intent is immutable")
                return control
            if job.status == "terminal":
                raise InvocationStateConflict("sandbox job already has a terminal outcome")
            self._write_event(job, "policy:sandbox-job-control:v1", "control_json", control)
            return control

    def acknowledge_control(
        self, lease: SandboxJobLease, binding: SandboxJobBackendBinding, *, generation: int
    ) -> None:
        with self._transaction(lease):
            job = self._owned(lease)
            if (
                job.control_json is None
                or _decode(job.control_json)["generation"] != generation
                or job.backend_json is None
                or _decode(job.backend_json)["binding"] != asdict(binding)
            ):
                raise _failure()
            value = self._value(
                job,
                binding=asdict(binding),
                generation=generation,
                control_digest=effect_request_digest(_PROFILE, _decode(job.control_json)),
            )
            self._write_event(job, "policy:sandbox-job-control-ack:v1", "control_ack_json", value)

    def settle_unstarted_control(self, lease: SandboxJobLease) -> SandboxJob:
        """Close a fenced never-submitted job without inventing a process exit."""
        with self._transaction(lease):
            job = self._owned(lease)
            if job.status != "ready" or job.submit_json is not None or job.control_json is None:
                raise InvocationStateConflict("sandbox job may already have launched")
            value = self._value(job, binding=None, outcome=None, reason="not_launched")
            self._write_event(job, "policy:sandbox-job-terminal:v1", "terminal_json", value)
            self.connection.execute(
                "UPDATE sandbox_job_delivery SET status='terminal' WHERE job_id=?", (job.job_id,)
            )
            result = self.get(job.job_id)
            if result is None:
                raise _failure()
            return result

    @staticmethod
    def _outcome(identifier: str, outcome: SandboxExecutionResult) -> None:
        if (
            outcome.execution_id != identifier
            or outcome.status != "completed"
            or type(outcome.exit_code) is not int
            or not -(2**31) <= outcome.exit_code < 2**31
            or type(outcome.duration) not in {int, float}
            or not math.isfinite(outcome.duration)
            or outcome.duration < 0
        ):
            raise _failure()
        try:
            if (
                not isinstance(outcome.stdout, str)
                or not isinstance(outcome.stderr, str)
                or len(outcome.stdout.encode()) + len(outcome.stderr.encode()) > _MAX_OUTPUT_BYTES
            ):
                raise ValueError
        except (ValueError, UnicodeError):
            raise _failure() from None

    def _deadline_evidence(
        self, admission: dict[str, Any], binding: dict[str, Any], outcome: dict[str, Any]
    ) -> bool:
        identity = EffectIdentity.from_value(admission["identity"])
        policy = "policy:sandbox-job-original-deadline:v1"
        selected = policy_identity(identity, policy)
        receipt = _raw_receipt(self.connection, selected)
        if receipt is None:
            if (
                self.connection.execute(
                    "SELECT 1 FROM workflow_effect_invocations WHERE identity=?", (selected.key,)
                ).fetchone()
                is not None
            ):
                raise _failure()
            return False
        value = receipt.output
        if (
            not isinstance(value, dict)
            or set(value) != {"job_id", "admission_digest", "binding", "outcome", "proof"}
            or receipt != _receipt(value, policy)
            or value["job_id"] != job_id(identity)
            or value["admission_digest"] != effect_request_digest(ADMISSION_POLICY, admission)
            or value["binding"] != binding
            or value["outcome"] != outcome
        ):
            raise _failure()
        proof = value["proof"]
        if (
            not isinstance(proof, dict)
            or set(proof) != {"admission_digest", "deadline_at", "observation_digest"}
            or proof["admission_digest"] != value["admission_digest"]
            or proof["deadline_at"] != admission["deadline_at"]
            or not isinstance(proof["observation_digest"], str)
            or re.fullmatch(r"[a-f0-9]{64}", proof["observation_digest"]) is None
            or self._now() < admission["deadline_at"]
        ):
            raise _failure()
        return True

    def record_original_deadline(
        self,
        lease: SandboxJobLease,
        binding: SandboxJobBackendBinding,
        outcome: SandboxExecutionResult,
        proof: SandboxJobOriginalDeadlineProof,
    ) -> None:
        from .job_backend import SandboxJobOriginalDeadlineProof

        if not isinstance(proof, SandboxJobOriginalDeadlineProof):
            raise _failure()
        self._outcome(lease.job_id, outcome)
        with self._transaction(lease):
            job = self._owned(lease)
            self._authority(job)
            admission = _decode(job.admission_json)
            self._binding(admission, binding)
            if (
                job.submit_json is None
                or proof.admission_digest != job.admission_digest
                or proof.deadline_at != admission["deadline_at"]
                or self._now() < admission["deadline_at"]
                or job.backend_json is not None
                and _decode(job.backend_json)["binding"] != asdict(binding)
            ):
                raise _failure()
            policy = "policy:sandbox-job-original-deadline:v1"
            value = self._value(
                job, binding=asdict(binding), outcome=asdict(outcome), proof=asdict(proof)
            )
            insert_completed_receipt(
                self.connection, policy_identity(job.identity, policy), _receipt(value, policy)
            )
            self._deadline_evidence(admission, asdict(binding), asdict(outcome))

    def record_terminal(
        self,
        lease: SandboxJobLease,
        binding: SandboxJobBackendBinding,
        outcome: SandboxExecutionResult,
        *,
        reason: Literal["exited", "cancel", "deadline"] = "exited",
    ) -> SandboxJob:
        self._outcome(lease.job_id, outcome)
        with self._transaction(lease):
            job = self._owned(lease)
            self._binding(_decode(job.admission_json), binding)
            if (
                job.status == "ready"
                or job.backend_json is not None
                and _decode(job.backend_json)["binding"] != asdict(binding)
            ):
                raise _failure()
            if (
                reason not in {"exited", "cancel", "deadline"}
                or reason != "exited"
                and not (
                    reason == "deadline"
                    and self._deadline_evidence(
                        _decode(job.admission_json), asdict(binding), asdict(outcome)
                    )
                )
                and (job.control_ack_json is None or _decode(job.control_json)["reason"] != reason)
            ):
                raise _failure()
            value = self._value(
                job, binding=asdict(binding), outcome=asdict(outcome), reason=reason
            )
            if job.terminal_json is not None:
                if _decode(job.terminal_json) != value:
                    raise InvocationStateConflict("sandbox job first terminal outcome is immutable")
                return job
            self._write_event(job, "policy:sandbox-job-terminal:v1", "terminal_json", value)
            self.connection.execute(
                "UPDATE sandbox_job_delivery SET status='terminal',uncertainty=NULL WHERE job_id=?",
                (job.job_id,),
            )
            result = self.get(job.job_id)
            if result is None:
                raise _failure()
            return result


def verify_job_admission_receipt(
    store: SandboxJobStore, identity: EffectIdentity, receipt: EffectReceipt
) -> None:
    """Pure validation hook for later common/native dependency integration."""
    try:
        if identity.operation != ADMISSION_POLICY:
            raise ValueError
        business = replace(identity, operation="run:" + receipt.output["request"]["kind"])
        job = store.get(job_id(business))
        if job is None or job.receipt != receipt:
            raise ValueError
    except (ValueError, KeyError, TypeError):
        raise _failure() from None
