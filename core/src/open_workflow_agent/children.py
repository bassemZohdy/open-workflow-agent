"""Atomic child identity reservations and root-tree budgets, not checkpoints."""

from __future__ import annotations

import hashlib
import json
import threading
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from ._effect_storage import insert_completed_receipt
from .effects import EffectIdentity, EffectJournal, EffectReceipt, effect_request_digest
from .errors import InvocationStateConflict, WorkflowExecutionError
from .ownership import owned_write_transaction
from .persistence import ExecutionHandle, InvocationStore

if TYPE_CHECKING:
    from .config import ChildWorkflowConfig
    from .workflow import WorkflowPlan


@dataclass(frozen=True, slots=True)
class ChildBinding:
    key: str
    root_id: str
    parent_id: str
    child_id: str
    depth: int
    request_digest: str
    workflow_source_json: str
    parent_task_reference: str


class ChildWorkflowStore:
    """Reserve once under parent authority; saved quotas never reset on reopen."""

    def __init__(
        self, journal: EffectJournal, invocations: InvocationStore, policy: ChildWorkflowConfig
    ) -> None:
        self.connection = journal.connection
        self.journal = journal
        self.invocations = invocations
        self.policy = policy
        self._lock = threading.RLock()
        self.connection.execute(
            "CREATE TABLE IF NOT EXISTS child_workflow_budgets ("
            "root_id TEXT PRIMARY KEY, fingerprint TEXT NOT NULL, "
            "max_depth BIGINT NOT NULL, max_children BIGINT NOT NULL, used BIGINT NOT NULL, "
            "policy_digest TEXT NOT NULL)"
        )
        columns = self.connection.execute(
            "SELECT * FROM child_workflow_budgets LIMIT 0"
        ).description
        if "policy_digest" not in {column[0] for column in columns}:
            # Unpublished prototype rows have no trustworthy policy evidence.
            # Preserve them without blessing the mutable limits on reopen.
            self.connection.execute(
                "ALTER TABLE child_workflow_budgets ADD COLUMN policy_digest TEXT"
            )
        self.connection.execute(
            "CREATE TABLE IF NOT EXISTS child_workflow_bindings ("
            "binding_key TEXT PRIMARY KEY, root_id TEXT NOT NULL, parent_id TEXT NOT NULL, "
            "child_id TEXT UNIQUE NOT NULL, depth BIGINT NOT NULL, "
            "request_digest TEXT NOT NULL, source_json TEXT NOT NULL, digest TEXT NOT NULL, "
            "parent_task_reference TEXT NOT NULL)"
        )
        self.connection.execute(
            "CREATE INDEX IF NOT EXISTS child_workflow_parent ON child_workflow_bindings(parent_id)"
        )
        self.connection.execute(
            "CREATE TABLE IF NOT EXISTS detached_child_jobs ("
            "job_key TEXT PRIMARY KEY,binding_key TEXT UNIQUE NOT NULL,"
            "child_id TEXT UNIQUE NOT NULL,engine TEXT NOT NULL,payload TEXT NOT NULL,"
            "digest TEXT NOT NULL)"
        )
        self.connection.commit()

    @staticmethod
    def _digest(values: tuple[Any, ...]) -> str:
        encoded = json.dumps(values, allow_nan=False, ensure_ascii=False, separators=(",", ":"))
        return hashlib.sha256(encoded.encode()).hexdigest()

    def _binding(self, row: Any) -> ChildBinding:
        if row is None:
            raise WorkflowExecutionError("child workflow binding is absent")
        if len(row) != 9 or self._digest((*row[:7], row[8])) != row[7]:
            raise WorkflowExecutionError("child workflow binding is inconsistent")
        binding = ChildBinding(
            key=row[0],
            root_id=row[1],
            parent_id=row[2],
            child_id=row[3],
            depth=row[4],
            request_digest=row[5],
            workflow_source_json=row[6],
            parent_task_reference=row[8],
        )
        if (
            not all(isinstance(value, str) and value for value in row[:4])
            or isinstance(binding.depth, bool)
            or not isinstance(binding.depth, int)
            or binding.depth < 1
            or not isinstance(binding.parent_task_reference, str)
            or not binding.parent_task_reference
        ):
            raise WorkflowExecutionError("child workflow binding identity is invalid")
        return binding

    def get(self, identity: EffectIdentity) -> ChildBinding | None:
        row = self.connection.execute(
            "SELECT binding_key,root_id,parent_id,child_id,depth,request_digest,source_json,digest "
            ",parent_task_reference FROM child_workflow_bindings WHERE binding_key = ?",
            (identity.key,),
        ).fetchone()
        if row is None:
            return None
        binding = self._binding(row)
        if binding.parent_id != identity.invocation_id:
            raise WorkflowExecutionError("child workflow binding belongs to another parent")
        return binding

    def reserve(
        self,
        identity: EffectIdentity,
        parent: ExecutionHandle,
        workflow: WorkflowPlan,
        input_data: Any,
        *,
        task_reference: str | None = None,
    ) -> ExecutionHandle:
        """Commit binding, identity, original input and quota in one transaction."""
        if (
            identity.operation != "run:workflow"
            or identity.invocation_id != parent.invocation_id
            or identity.workflow_fingerprint != parent.workflow_fingerprint
            or parent._execution_lease is None
        ):
            raise InvocationStateConflict("child reservation requires current parent authority")
        request = effect_request_digest(
            "run:workflow", {"workflow_fingerprint": workflow.fingerprint, "input": input_data}
        )
        source = workflow.source_json
        canonical_reference = task_reference or identity.task_reference
        try:
            with (
                self._lock,
                owned_write_transaction(
                    self.connection,
                    parent._execution_lease,
                    invocation_id=parent.invocation_id,
                    workflow_fingerprint=parent.workflow_fingerprint,
                ),
            ):
                existing = self.get(identity)
                if existing is not None:
                    if (
                        existing.request_digest != request
                        or existing.workflow_source_json != source
                    ):
                        raise WorkflowExecutionError("saved child workflow request changed")
                    if existing.parent_task_reference != canonical_reference:
                        raise WorkflowExecutionError("saved child task reference changed")
                    child = self.verify(existing, parent, workflow, input_data)
                    return child
                if parent.parent_invocation_id is None:
                    root_id, depth = parent.invocation_id, 1
                    self.connection.execute(
                        "INSERT INTO child_workflow_budgets "
                        "(root_id,fingerprint,max_depth,max_children,used,policy_digest) "
                        "VALUES (?,?,?,?,0,?) "
                        "ON CONFLICT(root_id) DO NOTHING",
                        (
                            root_id,
                            parent.workflow_fingerprint,
                            self.policy.max_depth,
                            self.policy.max_children,
                            self._digest(
                                (
                                    root_id,
                                    parent.workflow_fingerprint,
                                    self.policy.max_depth,
                                    self.policy.max_children,
                                )
                            ),
                        ),
                    )
                else:
                    row = self.connection.execute(
                        "SELECT binding_key,root_id,parent_id,child_id,depth,request_digest,"
                        "source_json,digest,parent_task_reference FROM child_workflow_bindings "
                        "WHERE child_id = ?",
                        (parent.invocation_id,),
                    ).fetchone()
                    ancestor = self._binding(row)
                    if ancestor.parent_id != parent.parent_invocation_id:
                        raise WorkflowExecutionError("child workflow ancestry is inconsistent")
                    root_id, depth = ancestor.root_id, ancestor.depth + 1
                query = (
                    "SELECT fingerprint,max_depth,max_children,used,policy_digest "
                    "FROM child_workflow_budgets "
                    "WHERE root_id = ?"
                )
                if self.connection.is_postgresql:
                    query += " FOR UPDATE"
                budget = self.connection.execute(query, (root_id,)).fetchone()
                root = self.invocations.get(root_id)
                if (
                    budget is None
                    or root is None
                    or root.workflow_fingerprint != budget[0]
                    or any(
                        isinstance(value, bool) or not isinstance(value, int)
                        for value in budget[1:4]
                    )
                    or not 1 <= budget[1] <= 64
                    or not 1 <= budget[2] <= 100_000
                    or not 0 <= budget[3] <= budget[2]
                    or budget[4] != self._digest((root_id, *budget[:3]))
                ):
                    raise WorkflowExecutionError("child workflow budget is inconsistent")
                count = self.connection.execute(
                    "SELECT COUNT(*) FROM child_workflow_bindings WHERE root_id = ?", (root_id,)
                ).fetchone()[0]
                if count != budget[3]:
                    raise WorkflowExecutionError("child workflow budget history is inconsistent")
                if depth > budget[1] or budget[3] >= budget[2]:
                    raise WorkflowExecutionError(
                        "child workflow execution budget exhausted",
                        details={"max_depth": budget[1], "max_children": budget[2]},
                    )
                child = self.invocations.create_in_transaction(
                    self.connection,
                    engine=parent.engine,
                    session_id=None,
                    user_id=parent.user_id,
                    owner_principal=parent.owner_principal,
                    workflow_name=workflow.name,
                    workflow_version=workflow.version,
                    workflow_fingerprint=workflow.fingerprint,
                    parent_invocation_id=parent.invocation_id,
                    parent_task_reference=canonical_reference,
                    input_data=input_data,
                )
                values = (
                    identity.key,
                    root_id,
                    parent.invocation_id,
                    child.invocation_id,
                    depth,
                    request,
                    source,
                )
                self.connection.execute(
                    "INSERT INTO child_workflow_bindings (binding_key,root_id,parent_id,"
                    "child_id,depth,request_digest,source_json,digest,parent_task_reference) "
                    "VALUES (?,?,?,?,?,?,?,?,?)",
                    (*values, self._digest((*values, canonical_reference)), canonical_reference),
                )
                self.connection.execute(
                    "UPDATE child_workflow_budgets SET used = used + 1 WHERE root_id = ?",
                    (root_id,),
                )
            return child
        except BaseException:
            if not self.connection.is_postgresql:
                self.connection.raw.rollback()
            raise

    def verify(
        self,
        binding: ChildBinding,
        parent: ExecutionHandle,
        workflow: WorkflowPlan,
        input_data: Any,
    ) -> ExecutionHandle:
        budget = self.connection.execute(
            "SELECT fingerprint,max_depth,max_children,used,policy_digest,"
            "(SELECT COUNT(*) FROM child_workflow_bindings WHERE root_id = ?) "
            "FROM child_workflow_budgets "
            "WHERE root_id = ?",
            (binding.root_id, binding.root_id),
        ).fetchone()
        root = self.invocations.get(binding.root_id)
        if (
            budget is None
            or root is None
            or root.workflow_fingerprint != budget[0]
            or any(
                isinstance(value, bool) or not isinstance(value, int)
                for value in (*budget[1:4], budget[5])
            )
            or not 1 <= budget[1] <= 64
            or not 1 <= budget[2] <= 100_000
            or not 1 <= budget[3] <= budget[2]
            or budget[4] != self._digest((binding.root_id, *budget[:3]))
            or budget[5] != budget[3]
            or not 1 <= binding.depth <= budget[1]
            or binding.request_digest
            != effect_request_digest(
                "run:workflow", {"workflow_fingerprint": workflow.fingerprint, "input": input_data}
            )
            or binding.workflow_source_json != workflow.source_json
        ):
            raise WorkflowExecutionError("child workflow budget or request is inconsistent")
        child = self.invocations.get(binding.child_id)
        if (
            child is None
            or child.parent_invocation_id != parent.invocation_id
            or child.parent_task_reference != binding.parent_task_reference
            or child.engine != parent.engine
            or child.owner_principal != parent.owner_principal
            or child.user_id != parent.user_id
            or child.workflow_fingerprint != workflow.fingerprint
            or child.workflow_name != workflow.name
            or child.workflow_version != workflow.version
            or self.invocations.read_input(child) != input_data
        ):
            raise WorkflowExecutionError("child workflow invocation binding is inconsistent")
        return child

    def enqueue(
        self,
        identity: EffectIdentity,
        parent: ExecutionHandle,
        workflow: WorkflowPlan,
        input_data: Any,
        returned_input: Any,
    ) -> EffectReceipt:
        """Publish durable detached work and its parent return decision together."""
        binding = self.get(identity)
        if binding is None:
            raise WorkflowExecutionError("detached child requires an admitted binding")
        child = self.verify(binding, parent, workflow, input_data)
        decision = EffectIdentity(
            identity.invocation_id,
            identity.workflow_fingerprint,
            identity.task_reference,
            identity.occurrence,
            "policy:detached-child:v1",
        )
        value = {
            "profile": "owa/detached-child/v1",
            "binding_key": binding.key,
            "child_id": child.invocation_id,
            "root_id": binding.root_id,
            "request_digest": binding.request_digest,
            "returned_input": returned_input,
            "lifetime": "independent",
        }
        digest = effect_request_digest(decision.operation, value)
        receipt = EffectReceipt(digest, "child-queue", status="completed", output=value)
        with (
            self._lock,
            owned_write_transaction(
                self.connection,
                parent._execution_lease,
                invocation_id=parent.invocation_id,
                workflow_fingerprint=parent.workflow_fingerprint,
            ),
        ):
            self.connection.execute(
                "INSERT INTO detached_child_jobs "
                "(job_key,binding_key,child_id,engine,payload,digest) VALUES (?,?,?,?,?,?) "
                "ON CONFLICT(job_key) DO NOTHING",
                (
                    decision.key,
                    binding.key,
                    child.invocation_id,
                    child.engine,
                    json.dumps(value, ensure_ascii=False, allow_nan=False),
                    digest,
                ),
            )
            insert_completed_receipt(self.connection, decision, receipt)
            verify_detached_child_receipt(self.connection, self.invocations, decision, receipt)
        return receipt

    def pending_jobs(
        self,
        engine: str,
        *,
        limit: int = 128,
        job_key: str | None = None,
        after_key: str | None = None,
    ) -> list[EffectIdentity]:
        """Read a strictly validated page for direct contract callers."""
        return [
            self.job_identity(key)
            for key in self.pending_job_keys(
                engine, limit=limit, job_key=job_key, after_key=after_key
            )
        ]

    def pending_job_keys(
        self,
        engine: str,
        *,
        limit: int = 128,
        job_key: str | None = None,
        after_key: str | None = None,
    ) -> list[str]:
        """Read bounded candidates without letting one bad index poison a page."""
        if not 1 <= limit <= 1000:
            raise ValueError("detached child scan limit is invalid")
        scoped = " AND jobs.job_key=?" if job_key is not None else ""
        cursor = " AND jobs.job_key>?" if after_key is not None else ""
        parameters: tuple[Any, ...] = (engine,)
        if job_key is not None:
            parameters += (job_key,)
        if after_key is not None:
            parameters += (after_key,)
        parameters += (limit,)
        rows = self.connection.execute(
            "SELECT jobs.job_key FROM detached_child_jobs AS jobs "
            "JOIN invocations AS inv ON inv.invocation_id=jobs.child_id "
            "WHERE jobs.engine=? AND inv.status IN ('running','waiting') "
            + scoped
            + cursor
            + " ORDER BY jobs.job_key LIMIT ?",
            parameters,
        ).fetchall()
        return [row[0] for row in rows]

    def job_identity(self, job_key: str) -> EffectIdentity:
        """Validate one candidate without mutating its recovery evidence."""
        row = self.connection.execute(
            "SELECT idx.payload FROM detached_child_jobs AS jobs "
            "LEFT JOIN workflow_effect_invocations AS idx ON idx.identity=jobs.job_key "
            "WHERE jobs.job_key=?",
            (job_key,),
        ).fetchone()
        try:
            if row is None:
                raise ValueError("detached child job is absent")
            identity = EffectIdentity.from_value(json.loads(row[0]))
            if identity.key != job_key or identity.operation != "policy:detached-child:v1":
                raise ValueError("detached child identity index differs")
        except (TypeError, ValueError) as error:
            raise WorkflowExecutionError("detached child identity index is inconsistent") from error
        return identity


def verify_detached_child_receipt(
    connection: Any,
    invocations: InvocationStore | None,
    identity: EffectIdentity,
    receipt: EffectReceipt,
) -> None:
    """A queued-child decision depends on immutable identity/input, not its outcome."""
    row = connection.execute(
        "SELECT binding_key,child_id,engine,payload,digest FROM detached_child_jobs "
        "WHERE job_key=?",
        (identity.key,),
    ).fetchone()
    value = receipt.output
    if (
        row is None
        or invocations is None
        or receipt.status != "completed"
        or not isinstance(value, dict)
        or set(value)
        != {
            "profile",
            "binding_key",
            "child_id",
            "root_id",
            "request_digest",
            "returned_input",
            "lifetime",
        }
        or value["profile"] != "owa/detached-child/v1"
        or value["lifetime"] != "independent"
        or json.loads(row[3]) != value
        or row[0] != value["binding_key"]
        or row[1] != value["child_id"]
        or row[4] != receipt.request_digest
        or receipt.request_digest != effect_request_digest(identity.operation, value)
    ):
        raise WorkflowExecutionError("detached child return decision is inconsistent")
    binding = connection.execute(
        "SELECT binding_key,root_id,parent_id,child_id,depth,request_digest,source_json,digest,"
        "parent_task_reference "
        "FROM child_workflow_bindings WHERE binding_key=?",
        (value["binding_key"],),
    ).fetchone()
    if (
        binding is None
        or ChildWorkflowStore._digest((*binding[:7], binding[8])) != binding[7]
        or binding[1] != value["root_id"]
        or binding[2] != identity.invocation_id
        or binding[3] != value["child_id"]
        or binding[5] != value["request_digest"]
    ):
        raise WorkflowExecutionError("detached child admission binding is inconsistent")
    parent = invocations.get(identity.invocation_id)
    child = invocations.get(value["child_id"])
    if (
        parent is None
        or child is None
        or parent.workflow_fingerprint != identity.workflow_fingerprint
        or child.engine != row[2]
        or child.engine != parent.engine
        or child.parent_invocation_id != parent.invocation_id
        or child.parent_task_reference != binding[8]
        or child.user_id != parent.user_id
        or child.owner_principal != parent.owner_principal
        or effect_request_digest(
            "run:workflow",
            {
                "workflow_fingerprint": child.workflow_fingerprint,
                "input": invocations.read_input(child),
            },
        )
        != binding[5]
    ):
        raise WorkflowExecutionError("detached child identity or input is inconsistent")
