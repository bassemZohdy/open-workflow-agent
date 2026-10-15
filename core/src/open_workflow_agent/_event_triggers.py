"""Embedded pre-invocation event cycles, independent of workflow checkpoints."""

from __future__ import annotations

import copy
import hashlib
import json
import time
from collections.abc import Callable, Mapping
from dataclasses import asdict, dataclass, field
from typing import Any, cast

from . import __version__
from ._schedule_storage import ScheduleTransactions
from .errors import EventValidationError, WorkflowExecutionError
from .event_matching import candidate_matcher, event_projection
from .event_store import EventArrival, EventConsumerPin, EventStore
from .events import EventEnvelope
from .expressions import ExpressionEvaluator, ExpressionMode, validate_expression_data
from .schedule_profile import parse_schedule
from .scheduling import ScheduleRecord, ScheduleStore


def _json(value: Any) -> str:
    validate_expression_data(value)
    return json.dumps(
        value, sort_keys=True, ensure_ascii=False, separators=(",", ":"), allow_nan=False
    )


def _digest(value: Any) -> str:
    return hashlib.sha256(_json(value).encode()).hexdigest()


class EventTriggerBackpressure(EventValidationError):
    """Admission bound reached; existing committed invocations remain dispatchable."""


@dataclass(frozen=True, slots=True)
class TriggerIdentity:
    schedule_id: str
    fingerprint: str
    scope: str
    cycle_ordinal: int

    @property
    def key(self) -> str:
        return _digest(asdict(self))


@dataclass(frozen=True, slots=True)
class TriggerRegistration:
    schedule_id: str
    fingerprint: str
    scope: str
    profile_digest: str
    consumer_key: str
    read: str


@dataclass(frozen=True, slots=True)
class TriggerProgress:
    identity: TriggerIdentity
    cursor: int
    events: tuple[EventEnvelope, ...] = ()
    correlations: Mapping[str, Any] = field(default_factory=dict)
    matched_filters: tuple[int, ...] = ()
    progress: Mapping[str, Any] = field(default_factory=dict)
    decision_ordinal: int = 0
    closed: bool = False


@dataclass(frozen=True, slots=True)
class TriggerCycleBinding:
    identity: TriggerIdentity
    invocation_id: str
    input_data: Any
    events: tuple[EventEnvelope, ...]
    cursor: int


class EventTriggerStore:
    """One completed cycle atomically creates/binds one real invocation."""

    def __init__(
        self,
        events: EventStore,
        schedules: ScheduleStore,
        *,
        engine: str,
        clock: Callable[[], float] = time.time,
        filter_guard: Callable[[Mapping[str, Any], EventEnvelope], bool] | None = None,
    ) -> None:
        self.events, self.schedules, self.engine = events, schedules, engine
        self.connection = events.connection
        self.clock, self.filter_guard = clock, filter_guard
        self._failpoint: Callable[[str], None] = lambda _name: None
        with events.transaction():
            for statement in (
                "CREATE TABLE IF NOT EXISTS event_trigger_profiles "
                "(schedule_id TEXT PRIMARY KEY,payload TEXT NOT NULL,digest TEXT NOT NULL)",
                "CREATE TABLE IF NOT EXISTS event_trigger_heads "
                "(schedule_id TEXT PRIMARY KEY,payload TEXT NOT NULL)",
                "CREATE TABLE IF NOT EXISTS event_trigger_decisions "
                "(schedule_id TEXT NOT NULL,cycle_ordinal INTEGER NOT NULL,"
                "ordinal INTEGER NOT NULL,payload TEXT NOT NULL,digest TEXT NOT NULL,"
                "PRIMARY KEY(schedule_id,cycle_ordinal,ordinal))",
                "CREATE TABLE IF NOT EXISTS event_trigger_cycles "
                "(schedule_id TEXT NOT NULL,ordinal INTEGER NOT NULL,payload TEXT NOT NULL,"
                "digest TEXT NOT NULL,PRIMARY KEY(schedule_id,ordinal))",
            ):
                self.connection.execute(statement)

    def _record(self, schedule_id: str) -> ScheduleRecord:
        # Lock order is event admission clock -> schedule admission clock.
        ScheduleTransactions.serialize(self.connection)
        return self.schedules._require(self.connection, schedule_id)

    def _variables(self, record: ScheduleRecord, runtime: Mapping[str, Any]) -> dict[str, Any]:
        return {
            "workflow": {
                "definition": json.loads(record.workflow_source_json),
                "input": copy.deepcopy(record.input_data),
            },
            "runtime": copy.deepcopy(dict(runtime)),
        }

    def _prepare(
        self,
        strategy: Mapping[str, Any],
        record: ScheduleRecord,
        expressions: ExpressionEvaluator,
        variables: Mapping[str, Any],
    ) -> dict[str, Any]:
        prepared = copy.deepcopy(dict(strategy))
        for mode in ("one", "all", "any"):
            if mode not in prepared:
                continue
            filters = [prepared[mode]] if mode == "one" else prepared[mode]
            for selected in filters:
                for rule in selected.get("correlate", {}).values():
                    if "expect" in rule:
                        rule["expect"] = expressions.evaluate(
                            rule["expect"], record.input_data, variables=variables
                        )
        if isinstance(prepared.get("until"), Mapping):
            prepared["until"] = self._prepare(prepared["until"], record, expressions, variables)
        validate_expression_data(prepared)
        return prepared

    def _registration(self, record: ScheduleRecord) -> tuple[TriggerRegistration, dict[str, Any]]:
        profile = parse_schedule(json.loads(record.workflow_source_json).get("schedule"))
        if profile.kind != "on" or profile.strategy is None:
            raise EventValidationError("schedule is not an event trigger")
        base = {
            "profile": "owa/schedule-trigger/v1",
            "schedule_id": record.schedule_id,
            "fingerprint": record.workflow_fingerprint,
            "scope": record.event_scope,
            "owner_principal": record.owner_principal,
            "engine": self.engine,
            "definition_digest": _digest(json.loads(record.workflow_source_json)),
            "registration_input_digest": _digest(record.input_data),
            "read": profile.read,
        }
        consumer_key = "schedule-trigger:" + _digest(base)
        row = self.connection.execute(
            "SELECT payload,digest FROM event_trigger_profiles WHERE schedule_id=?",
            (record.schedule_id,),
        ).fetchone()
        if row is None:
            existing_pin = self.events.consumer_pin_in_transaction(consumer_key)
            old_cycles = self.connection.execute(
                "SELECT 1 FROM event_trigger_cycles WHERE schedule_id=?", (record.schedule_id,)
            ).fetchone()
            old_slots = self.connection.execute(
                "SELECT 1 FROM schedule_slots WHERE schedule_id=?", (record.schedule_id,)
            ).fetchone()
            if existing_pin is not None or old_cycles is not None or old_slots is not None:
                raise WorkflowExecutionError("event trigger registration evidence is missing")
            runtime = {
                "name": "open-workflow-agent",
                "version": __version__,
                "metadata": {"engine": self.engine},
            }
            mode = json.loads(record.workflow_source_json).get("evaluate", {}).get("mode", "strict")
            expressions = ExpressionEvaluator(mode=cast(ExpressionMode, mode))
            payload = {
                "base": base,
                "runtime": runtime,
                "strategy": self._prepare(
                    profile.strategy, record, expressions, self._variables(record, runtime)
                ),
            }
            if len(_json(payload).encode()) > self.events.limits.max_event_bytes:
                raise EventValidationError("event trigger registration exceeds configured bounds")
            digest = _digest(payload)
            self.connection.execute(
                "INSERT INTO event_trigger_profiles(schedule_id,payload,digest) VALUES (?,?,?)",
                (record.schedule_id, _json(payload), digest),
            )
            self.events.pin_consumer_in_transaction(
                EventConsumerPin(consumer_key, record.event_scope, 0)
            )
            head = {"ordinal": 1, "cursor": 0, "closed": False, "cycle": self._initial(1, 0)}
            self.connection.execute(
                "INSERT INTO event_trigger_heads(schedule_id,payload) VALUES (?,?)",
                (record.schedule_id, _json(head)),
            )
        else:
            payload = json.loads(row[0])
            digest = row[1]
            if _digest(payload) != digest or payload["base"] != base:
                raise WorkflowExecutionError("event trigger profile is missing or changed")
        return TriggerRegistration(
            record.schedule_id,
            record.workflow_fingerprint,
            record.event_scope,
            digest,
            consumer_key,
            profile.read,
        ), payload

    @staticmethod
    def _initial(ordinal: int, cursor: int) -> dict[str, Any]:
        return {
            "ordinal": ordinal,
            "start_cursor": cursor,
            "cursor": cursor,
            "correlations": {},
            "matched_filters": [],
            "progress": {},
            "selected": [],
            "decision_ordinal": 0,
        }

    def ensure_registered(self, record: ScheduleRecord) -> TriggerRegistration:
        with self.events.transaction():
            current = self._record(record.schedule_id)
            if current.status != "active":
                raise EventValidationError("event trigger schedule is not active")
            registration, _ = self._registration(current)
            self._read(current, registration)
            return registration

    def _selected(
        self, registration: TriggerRegistration, cycle: Mapping[str, Any]
    ) -> tuple[EventArrival, ...]:
        selected = []
        for value in cycle["selected"]:
            if (
                set(value) != {"sequence", "digest", "envelope"}
                or type(value["sequence"]) is not int
                or value["sequence"] <= 0
            ):
                raise WorkflowExecutionError("event trigger selection fields changed")
            arrival = self.events.verify_consumer_selection_in_transaction(
                registration.consumer_key,
                scope=registration.scope,
                sequence=value["sequence"],
                digest=value["digest"],
            )
            if _json(arrival.envelope.as_dict()) != _json(value["envelope"]):
                raise WorkflowExecutionError("event trigger canonical selection changed")
            selected.append(arrival)
        if [entry.sequence for entry in selected] != sorted({entry.sequence for entry in selected}):
            raise WorkflowExecutionError("event trigger selection order changed")
        return tuple(selected)

    def _binding(
        self,
        record: ScheduleRecord,
        registration: TriggerRegistration,
        payload: Mapping[str, Any],
    ) -> TriggerCycleBinding:
        identity = TriggerIdentity(**payload["identity"])
        cycle = payload["cycle"]
        if (
            identity.schedule_id != record.schedule_id
            or identity.fingerprint != registration.fingerprint
            or identity.scope != registration.scope
            or identity.cycle_ordinal != cycle["ordinal"]
            or payload["profile_digest"] != registration.profile_digest
        ):
            raise WorkflowExecutionError("event trigger cycle identity changed")
        selected = self._selected(registration, cycle)
        envelopes = tuple(entry.envelope for entry in selected)
        projected = [event_projection(event, registration.read) for event in envelopes]
        if _json(projected) != _json(payload["input_data"]):
            raise WorkflowExecutionError("event trigger projection changed")
        self.schedules.verify_trigger_binding_in_transaction(
            self.connection,
            record.schedule_id,
            identity.key,
            engine=self.engine,
            invocation_id=payload["invocation_id"],
            input_data=projected,
            event_scope=record.event_scope,
        )
        return TriggerCycleBinding(
            identity, payload["invocation_id"], projected, envelopes, cycle["cursor"]
        )

    def _verify_decisions(self, record: ScheduleRecord, cycle: Mapping[str, Any]) -> None:
        rows = self.connection.execute(
            "SELECT ordinal,payload,digest FROM event_trigger_decisions WHERE schedule_id=? "
            "AND cycle_ordinal=? ORDER BY ordinal",
            (record.schedule_id, cycle["ordinal"]),
        ).fetchall()
        if [item[0] for item in rows] != list(range(1, cycle["decision_ordinal"] + 1)):
            raise WorkflowExecutionError("event trigger decision evidence is missing")
        for _, encoded, digest in rows:
            if _digest(json.loads(encoded)) != digest:
                raise WorkflowExecutionError("event trigger decision digest changed")
        if rows and _json(json.loads(rows[-1][1])) != _json(cycle):
            raise WorkflowExecutionError("event trigger immutable decision disagrees with progress")

    def _read(
        self,
        record: ScheduleRecord,
        registration: TriggerRegistration,
    ) -> tuple[dict[str, Any], TriggerProgress]:
        row = self.connection.execute(
            "SELECT payload FROM event_trigger_heads WHERE schedule_id=?", (record.schedule_id,)
        ).fetchone()
        pin = self.events.consumer_pin_in_transaction(registration.consumer_key)
        if row is None or pin is None:
            raise WorkflowExecutionError("event trigger progress evidence is missing")
        try:
            head = json.loads(row[0])
            cycle = head["cycle"]
            if (
                set(head) != {"ordinal", "cursor", "closed", "cycle"}
                or set(cycle) != set(self._initial(1, 0))
                or type(head["cursor"]) is not int
                or head["cursor"] < 0
                or type(cycle["cursor"]) is not int
                or type(cycle["start_cursor"]) is not int
                or type(cycle["decision_ordinal"]) is not int
                or cycle["decision_ordinal"] < 0
                or not isinstance(cycle["correlations"], dict)
                or not isinstance(cycle["progress"], dict)
                or not isinstance(cycle["matched_filters"], list)
                or any(type(index) is not int or index < 0 for index in cycle["matched_filters"])
                or not isinstance(cycle["selected"], list)
                or type(head["ordinal"]) is not int
                or head["ordinal"] < 1
                or type(head["closed"]) is not bool
                or pin.active == head["closed"]
                or pin.scope != registration.scope
                or pin.cursor != head["cursor"]
                or cycle["ordinal"] != head["ordinal"]
                or cycle["cursor"] != head["cursor"]
            ):
                raise ValueError("trigger cursor disagrees")
            completed = self.connection.execute(
                "SELECT ordinal,payload,digest FROM event_trigger_cycles "
                "WHERE schedule_id=? ORDER BY ordinal",
                (record.schedule_id,),
            ).fetchall()
            if [item[0] for item in completed] != list(range(1, head["ordinal"])):
                raise ValueError("trigger cycle evidence is missing")
            prior_cursor = 0
            for ordinal, encoded, digest in completed:
                value = json.loads(encoded)
                if _digest(value) != digest or value["cycle"]["ordinal"] != ordinal:
                    raise ValueError("trigger cycle digest changed")
                self._verify_decisions(record, value["cycle"])
                binding = self._binding(record, registration, value)
                if binding.cursor < prior_cursor:
                    raise ValueError("trigger cycle cursor reversed")
                prior_cursor = binding.cursor
            self._verify_decisions(record, cycle)
            if cycle["start_cursor"] != prior_cursor:
                raise ValueError("trigger cycle starting cursor changed")
            if not cycle["decision_ordinal"] and cycle != self._initial(
                head["ordinal"], prior_cursor
            ):
                raise ValueError("trigger initial state changed")
            selected = self._selected(registration, cycle)
            identity = TriggerIdentity(
                record.schedule_id, record.workflow_fingerprint, record.event_scope, head["ordinal"]
            )
            return head, TriggerProgress(
                identity,
                head["cursor"],
                tuple(a.envelope for a in selected),
                copy.deepcopy(cycle["correlations"]),
                tuple(cycle["matched_filters"]),
                copy.deepcopy(cycle["progress"]),
                cycle["decision_ordinal"],
                head["closed"],
            )
        except WorkflowExecutionError:
            raise
        except Exception:
            raise WorkflowExecutionError(
                "event trigger evidence is unavailable or inconsistent"
            ) from None

    def state(self, schedule_id: str) -> TriggerProgress:
        with self.events.transaction():
            record = self._record(schedule_id)
            registration, _ = self._registration(record)
            return self._read(record, registration)[1]

    def verify_invocation(self, invocation_id: str) -> None:
        """Read-only scheduled-dispatch admission; never reconstruct lost evidence."""
        with self.events.transaction():
            row = self.connection.execute(
                "SELECT schedule_id FROM schedule_slots WHERE invocation_id=?",
                (invocation_id,),
            ).fetchone()
            if row is None:
                raise WorkflowExecutionError("scheduled invocation binding evidence is missing")
            record = self._record(row[0])
            if record.kind != "on":
                return
            profile = self.connection.execute(
                "SELECT 1 FROM event_trigger_profiles WHERE schedule_id=?",
                (record.schedule_id,),
            ).fetchone()
            if profile is None:
                raise WorkflowExecutionError("event trigger profile evidence is missing")
            registration, _ = self._registration(record)
            self._read(record, registration)
            bindings = self.connection.execute(
                "SELECT payload FROM event_trigger_cycles WHERE schedule_id=?",
                (record.schedule_id,),
            ).fetchall()
            if (
                sum(json.loads(value)["invocation_id"] == invocation_id for (value,) in bindings)
                != 1
            ):
                raise WorkflowExecutionError("event trigger cycle invocation evidence is missing")

    def _save_decision(self, record: ScheduleRecord, head: dict[str, Any]) -> None:
        count = self.connection.execute("SELECT COUNT(*) FROM event_trigger_decisions").fetchone()[
            0
        ]
        if count >= self.events.limits.max_retained_events:
            raise EventTriggerBackpressure("event trigger decision capacity is exhausted")
        cycle = head["cycle"]
        cycle["decision_ordinal"] += 1
        if len(_json(cycle).encode()) > self.events.limits.max_event_bytes:
            raise EventValidationError("event trigger partial state exceeds configured bounds")
        self.connection.execute(
            "INSERT INTO event_trigger_decisions(schedule_id,cycle_ordinal,ordinal,payload,digest) "
            "VALUES (?,?,?,?,?)",
            (
                record.schedule_id,
                head["ordinal"],
                cycle["decision_ordinal"],
                _json(cycle),
                _digest(cycle),
            ),
        )

    def _save_head(self, record: ScheduleRecord, head: Mapping[str, Any]) -> None:
        self.connection.execute(
            "UPDATE event_trigger_heads SET payload=? WHERE schedule_id=?",
            (_json(head), record.schedule_id),
        )

    def poll(self, schedule_id: str) -> TriggerCycleBinding | None:
        # Deliver admitted publications before entering the trigger transaction.
        self.events.deliver_outbox(limit=self.events.limits.scan_batch)
        with self.events.transaction():
            record = self._record(schedule_id)
            registration, profile = self._registration(record)
            head, progress = self._read(record, registration)
            if head["closed"]:
                return None
            if record.status != "active":
                self._close(record, registration, head)
                return None
            source = json.loads(record.workflow_source_json)
            expressions = ExpressionEvaluator(
                mode=cast(ExpressionMode, source.get("evaluate", {}).get("mode", "strict"))
            )
            strategy = profile["strategy"]
            matcher = candidate_matcher(
                strategy,
                expressions=expressions,
                variables=self._variables(record, profile["runtime"]),
                read=registration.read,
                filter_guard=self.filter_guard,
            )
            from .workflow import WorkflowExecutor

            terminal = WorkflowExecutor._listener_complete_without_events(strategy)
            selected_new: list[EventArrival] = []
            changed = False
            cycle = head["cycle"]
            if not terminal:
                for arrival in self.events.arrivals_in_transaction(
                    scope=record.event_scope, after=head["cursor"]
                ):
                    decision = matcher(arrival.envelope, progress)
                    head["cursor"] = cycle["cursor"] = arrival.sequence
                    changed = True
                    if decision is None:
                        continue
                    correlations = dict(cycle["correlations"])
                    for key, value in decision.correlations.items():
                        if key in correlations and _json(correlations[key]) != _json(value):
                            raise WorkflowExecutionError(
                                "event trigger correlation changed its binding"
                            )
                        correlations[key] = value
                    cycle["correlations"] = correlations
                    cycle["matched_filters"] = sorted(
                        set(cycle["matched_filters"]) | set(decision.filter_indices)
                    )
                    cycle["progress"] = dict(decision.progress)
                    if not decision.terminator:
                        cycle["selected"].append(
                            {
                                "sequence": arrival.sequence,
                                "digest": arrival.digest,
                                "envelope": arrival.envelope.as_dict(),
                            }
                        )
                        selected_new.append(arrival)
                    terminal = decision.terminal
                    # Commit each semantic match before another candidate can use it.
                    break
            if not changed and not terminal:
                return None
            self.events.pin_consumer_in_transaction(
                EventConsumerPin(registration.consumer_key, record.event_scope, head["cursor"]),
                selected=tuple(selected_new),
            )
            self._save_decision(record, head)
            self._failpoint("trigger-selection-before-commit")
            if not terminal:
                self._save_head(record, head)
                return None
            count = self.connection.execute("SELECT COUNT(*) FROM event_trigger_cycles").fetchone()[
                0
            ]
            if count >= self.events.limits.max_retained_events:
                raise EventTriggerBackpressure("event trigger cycle capacity is exhausted")
            selected = self._selected(registration, cycle)
            envelopes = tuple(item.envelope for item in selected)
            projected = [event_projection(event, registration.read) for event in envelopes]
            identity = progress.identity
            handle = self.schedules.bind_trigger_in_transaction(
                self.connection,
                record.schedule_id,
                identity.key,
                projected,
                engine=self.engine,
                workflow_fingerprint=record.workflow_fingerprint,
                event_scope=record.event_scope,
            )
            payload = {
                "identity": asdict(identity),
                "profile_digest": registration.profile_digest,
                "invocation_id": handle.invocation_id,
                "input_data": projected,
                "cycle": copy.deepcopy(cycle),
            }
            self.connection.execute(
                "INSERT INTO event_trigger_cycles(schedule_id,ordinal,payload,digest) "
                "VALUES (?,?,?,?)",
                (record.schedule_id, head["ordinal"], _json(payload), _digest(payload)),
            )
            self._failpoint("trigger-binding-before-commit")
            head["ordinal"] += 1
            head["cycle"] = self._initial(head["ordinal"], head["cursor"])
            self._save_head(record, head)
            return TriggerCycleBinding(
                identity, handle.invocation_id, projected, envelopes, head["cursor"]
            )

    def _close(
        self,
        record: ScheduleRecord,
        registration: TriggerRegistration,
        head: dict[str, Any],
    ) -> None:
        head["closed"] = True
        self.events.pin_consumer_in_transaction(
            EventConsumerPin(
                registration.consumer_key, registration.scope, head["cursor"], active=False
            )
        )
        self._save_head(record, head)

    def close(self, schedule_id: str) -> None:
        with self.events.transaction():
            record = self._record(schedule_id)
            registration, _ = self._registration(record)
            head, _ = self._read(record, registration)
            self._close(record, registration, head)
