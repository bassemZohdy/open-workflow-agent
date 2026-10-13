"""Typed finite A2A v1 client over the common bounded HTTP transport."""

from __future__ import annotations

import asyncio
import copy
import hashlib
import json
import math
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any
from uuid import NAMESPACE_URL, uuid4, uuid5

import httpx

from ._a2a_types import (
    A2A_METHODS,
    A2A_PROTOCOL_VERSION,
    MAX_VALUE_BYTES,
    STREAM_METHODS,
    failure,
    validate_v1_card,
    validate_v1_request,
    validate_v1_result,
    verify_sdk,
)
from .errors import OwaError, ToolError, _A2AOutcomeUncertain
from .expressions import validate_expression_data

if TYPE_CHECKING:
    from .protocols import AuthenticationProvider, HttpClient

_CARD_PROFILE = "owa/a2a-card-selection/v1"
_TERMINAL = frozenset(
    {
        "TASK_STATE_COMPLETED",
        "TASK_STATE_FAILED",
        "TASK_STATE_CANCELED",
        "TASK_STATE_REJECTED",
        "TASK_STATE_INPUT_REQUIRED",
        "TASK_STATE_AUTH_REQUIRED",
    }
)


def _canonical(value: Any) -> str:
    validate_expression_data(value)
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False
    )


@dataclass(frozen=True, slots=True)
class A2ACardSelection:
    endpoint: str
    binding: str
    protocol_version: str
    card_digest: str
    card_json: str
    credential_profile: str | None = None
    remaining_timeout: float | None = field(default=None, compare=False, repr=False)

    def as_value(self) -> dict[str, Any]:
        return {
            "profile": _CARD_PROFILE,
            "endpoint": self.endpoint,
            "binding": self.binding,
            "protocol_version": self.protocol_version,
            "card_digest": self.card_digest,
            "card_json": self.card_json,
            "credential_profile": self.credential_profile,
        }

    @classmethod
    def from_value(cls, value: Any) -> A2ACardSelection:
        if (
            not isinstance(value, dict)
            or set(value)
            != {
                "profile",
                "endpoint",
                "binding",
                "protocol_version",
                "card_digest",
                "card_json",
                "credential_profile",
            }
            or value.get("profile") != _CARD_PROFILE
        ):
            raise failure("invalid_card_selection")
        encoded = value["card_json"]
        if not isinstance(encoded, str) or len(encoded.encode("utf-8")) > MAX_VALUE_BYTES:
            raise failure("invalid_card_selection")
        try:
            card = _strict_json(encoded.encode("utf-8"))
            selected = select_interface(
                card, binding=value["binding"], credential_profile=value["credential_profile"]
            )
        except (OwaError, ValueError, UnicodeError):
            raise failure("invalid_card_selection") from None
        if selected.as_value() != value:
            raise failure("invalid_card_selection")
        return selected


def select_interface(
    card: Any,
    *,
    explicit_endpoint: str | None = None,
    binding: str = "JSONRPC",
    credential_profile: str | None = None,
) -> A2ACardSelection:
    # OWS: agentCard takes precedence; server is ignored when a card is present.
    validated = validate_v1_card(card)
    if binding != "JSONRPC":
        raise failure("unsupported_binding")
    if credential_profile is not None and (
        not isinstance(credential_profile, str)
        or not credential_profile
        or len(credential_profile) > 128
    ):
        raise failure("invalid_credential_binding")
    if any(
        extension.get("required")
        for extension in validated.get("capabilities", {}).get("extensions", [])
    ):
        raise failure("unsupported_extension")
    selected = next(
        (
            interface
            for interface in validated["supportedInterfaces"]
            if interface.get("protocolBinding") == binding
            and interface.get("protocolVersion") == A2A_PROTOCOL_VERSION
            and not interface.get("tenant")
        ),
        None,
    )
    if selected is None:
        raise failure("unsupported_interface")
    encoded = _canonical(validated)
    return A2ACardSelection(
        selected["url"],
        binding,
        A2A_PROTOCOL_VERSION,
        hashlib.sha256(encoded.encode("utf-8")).hexdigest(),
        encoded,
        credential_profile,
    )


class _NoAuthentication:
    def headers(self, endpoint: str) -> Mapping[str, str]:
        return {}


def _pairs(items: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for name, value in items:
        if name in result:
            raise ValueError("duplicate key")
        result[name] = value
    return result


def _non_finite(value: str) -> Any:
    raise ValueError("non-finite JSON")


def _strict_json(content: bytes) -> Any:
    value = json.loads(
        content.decode("utf-8"), object_pairs_hook=_pairs, parse_constant=_non_finite
    )
    validate_expression_data(value)
    return value


async def fetch_validate_card(
    http: HttpClient,
    resource: Any,
    *,
    timeout: float,
    authentication: AuthenticationProvider | None = None,
) -> dict[str, Any]:
    verify_sdk()
    from .resources import resource_uri

    try:
        endpoint = resource_uri(resource)
    except OwaError:
        raise failure("invalid_card_resource") from None
    if not math.isfinite(timeout) or timeout <= 0:
        raise failure("invalid_timeout")
    # Discovery credentials are selected explicitly, never inherited from the
    # resource/business client. A public card requires no credential resolution.
    from .protocols import HttpClient

    # An opaque context/transport can already contain a client identity. Only
    # reconstructible trust settings may be cloned for public discovery.
    if type(http.verify_tls) not in (bool, str) or (
        http.transport is not None
        and not isinstance(http.transport, (httpx.MockTransport, httpx.ASGITransport))
    ):
        raise failure("unsupported_card_transport")
    card_http = HttpClient(
        timeout=min(http.timeout, timeout),
        max_response_bytes=min(http.max_response_bytes, MAX_VALUE_BYTES),
        verify_tls=http.verify_tls is not False,
        ca_bundle=http.verify_tls if isinstance(http.verify_tls, str) else None,
        follow_redirects=False,
        allowed_hosts=http.allowed_hosts,
        transport=http.transport,
        destination_resolver=http.destination_resolver,
    )
    try:
        response = await card_http.request(
            "GET",
            endpoint,
            headers={"Accept": "application/json"},
            follow_redirects=False,
            request_timeout=timeout,
            _authentication=authentication or _NoAuthentication(),
            _return_response=True,
        )
    except ToolError:
        raise failure("card_fetch_failed") from None
    if (
        len(response.content) > MAX_VALUE_BYTES
        or response.headers.get("content-type", "").split(";", 1)[0].strip().lower()
        != "application/json"
    ):
        raise failure("invalid_card")
    try:
        return validate_v1_card(_strict_json(response.content))
    except (OwaError, ValueError, UnicodeError, RecursionError):
        raise failure("invalid_card") from None


def _parameters(
    payload: Mapping[str, Any], method: str, operation_id: str | None
) -> dict[str, Any]:
    params = payload.get("parameters", payload.get("params"))
    if params is None:
        params = (
            {"message": payload.get("message")}
            if method in {"SendMessage", "SendStreamingMessage"}
            else {}
        )
    if not isinstance(params, dict):
        raise failure("invalid_parameters")
    result = copy.deepcopy(params)
    if method in {"SendMessage", "SendStreamingMessage"}:
        message = result.get("message")
        if not isinstance(message, dict):
            raise failure("invalid_parameters")
        if "messageId" not in message:
            message["messageId"] = (
                str(uuid5(NAMESPACE_URL, "owa/a2a-message/v1:" + operation_id))
                if operation_id
                else str(uuid4())
            )
        role = message.get("role", "user")
        message["role"] = "ROLE_USER" if role == "user" else role
        skill = result.pop("skillId", None)
        if skill is not None:
            metadata = message.get("metadata", {})
            if not isinstance(metadata, dict) or (
                "skillId" in metadata and metadata["skillId"] != skill
            ):
                raise failure("invalid_parameters")
            message["metadata"] = {**metadata, "skillId": skill}
    if method not in {"SendMessage", "SendStreamingMessage"} and "skillId" in result:
        raise failure("invalid_parameters")
    return validate_v1_request(method, result)


def _uncertain(reason: str) -> _A2AOutcomeUncertain:
    return _A2AOutcomeUncertain("A2A outcome remains unresolved", details={"reason": reason})


def _rpc(value: Any, identifier: str | int) -> Any:
    if (
        not isinstance(value, dict)
        or value.get("jsonrpc") != "2.0"
        or type(value.get("id")) is not type(identifier)
        or value.get("id") != identifier
        or ("result" in value) == ("error" in value)
        or set(value) - {"jsonrpc", "id", "result", "error"}
    ):
        raise _uncertain("invalid_response")
    if "error" in value:
        error = value["error"]
        if (
            not isinstance(error, dict)
            or set(error) - {"code", "message", "data"}
            or type(error.get("code")) is not int
            or not isinstance(error.get("message"), str)
        ):
            raise _uncertain("invalid_response")
        data = error.get("data", [])
        if not isinstance(data, list) or any(
            not isinstance(detail, dict) or not isinstance(detail.get("@type"), str)
            for detail in data
        ):
            raise _uncertain("invalid_response")
        raise ToolError(
            "A2A server returned an error",
            details={
                "reason": "protocol_error",
                "code": error["code"],
                "type": "https://serverlessworkflow.io/spec/1.0.0/errors/runtime",
            },
        )
    return value["result"]


def _validate_requested_identity(
    result: Mapping[str, Any], method: str, params: Mapping[str, Any]
) -> None:
    expected_task = (
        params.get("id")
        if method in {"GetTask", "CancelTask", "SubscribeToTask"}
        else params.get("message", {}).get("taskId")
        if method in {"SendMessage", "SendStreamingMessage"}
        else None
    )
    expected_context = (
        params.get("message", {}).get("contextId")
        if method in {"SendMessage", "SendStreamingMessage"}
        else None
    )
    if expected_task is None and expected_context is None:
        return
    if method in {"GetTask", "CancelTask"}:
        actual_task, actual_context = result.get("id"), result.get("contextId")
    else:
        key = next(iter(set(result) & {"task", "message", "statusUpdate", "artifactUpdate"}))
        item = result[key]
        actual_task = item.get("id") if key == "task" else item.get("taskId")
        actual_context = item.get("contextId")
    if (
        expected_task is not None
        and actual_task != expected_task
        or expected_context is not None
        and actual_context != expected_context
    ):
        raise _uncertain("response_identity_changed")


def _stream(
    content: bytes, identifier: str | int, method: str, params: Mapping[str, Any]
) -> list[dict[str, Any]]:
    text = content.decode("utf-8")
    frames = text.replace("\r\n", "\n").replace("\r", "\n").split("\n\n")
    if len(frames) > 65 or frames[-1].strip():
        raise _uncertain("invalid_stream")
    results: list[dict[str, Any]] = []
    identity: tuple[str, str] | None = None
    finished = False
    for frame in frames[:-1]:
        chunks = []
        event = "message"
        for line in frame.split("\n"):
            if not line or line.startswith(":"):
                continue
            key, separator, value = line.partition(":")
            value = value.removeprefix(" ")
            if not separator:
                raise _uncertain("invalid_stream")
            if key == "data":
                chunks.append(value)
            elif key == "event":
                event = value
            elif key != "id" or len(value) > 256:
                raise _uncertain("invalid_stream")
        if not chunks:
            continue
        if event != "message" or finished:
            raise _uncertain("invalid_stream")
        raw = "\n".join(chunks).encode("utf-8")
        if len(raw) > MAX_VALUE_BYTES:
            raise _uncertain("stream_limit")
        result = validate_v1_result(method, _rpc(_strict_json(raw), identifier))
        _validate_requested_identity(result, method, params)
        if "message" in result:
            if results:
                raise _uncertain("invalid_stream")
            finished = True
        else:
            key = next(iter(set(result) & {"task", "statusUpdate", "artifactUpdate"}))
            item = result[key]
            current = (item["id"] if key == "task" else item["taskId"], item["contextId"])
            if identity is not None and current != identity:
                raise _uncertain("invalid_stream_identity")
            if not results and key != "task":
                raise _uncertain("invalid_stream")
            identity = current
            if key != "artifactUpdate":
                finished = item["status"]["state"] in _TERMINAL
        results.append(result)
    if not results or not finished:
        raise _uncertain("incomplete_stream")
    return results


async def call_a2a(
    http: HttpClient,
    payload: Mapping[str, Any],
    *,
    timeout: float,
    operation_id: str | None = None,
    authentication: AuthenticationProvider | None = None,
    selection: A2ACardSelection | None = None,
    credential_endpoints: Mapping[str, tuple[str, ...]] | None = None,
    bound_endpoint: str | None = None,
) -> Any:
    verify_sdk()
    from .protocols import _endpoint_value

    if any(isinstance(key, str) and key.startswith("_a2a_") for key in payload):
        raise failure("reserved_parameter")
    method = payload.get("method", "SendMessage")
    if not isinstance(method, str) or method not in A2A_METHODS:
        raise ToolError("unsupported A2A method", details={"supported": sorted(A2A_METHODS)})
    if payload.get("protocolVersion", A2A_PROTOCOL_VERSION) != A2A_PROTOCOL_VERSION:
        raise ToolError(
            "unsupported A2A protocol version; supported version is 1.0",
            details={"supported": A2A_PROTOCOL_VERSION},
        )
    if not math.isfinite(timeout) or timeout <= 0:
        raise failure("invalid_timeout")
    endpoint: str | None
    if selection is not None:
        # Recheck immutable JSON/card selection; the transient budget is private.
        verified = A2ACardSelection.from_value(selection.as_value())
        if selection.remaining_timeout is not None:
            timeout = min(timeout, selection.remaining_timeout)
        if verified.credential_profile != getattr(authentication, "profile_name", None):
            raise failure("credential_binding_changed")
        endpoint = verified.endpoint
    elif "agentCard" in payload:
        card = payload["agentCard"]
        if not isinstance(card, dict) or "supportedInterfaces" not in card:
            raise failure("card_admission_required")
        verified = select_interface(
            card, credential_profile=getattr(authentication, "profile_name", None)
        )
        endpoint = verified.endpoint
    else:
        endpoint = _endpoint_value(payload.get("endpoint") or payload.get("server"))
    if not endpoint:
        raise ToolError("a2a call requires server endpoint")
    params = _parameters(payload, method, operation_id)
    if bound_endpoint is not None and bound_endpoint != endpoint:
        raise failure("configured_endpoint_changed")
    if selection is not None or "agentCard" in payload:
        _authorize_card_credentials(
            http, endpoint, authentication, credential_endpoints, bound_endpoint
        )
    if not math.isfinite(timeout) or timeout <= 0:
        raise failure("timeout")
    http._check_endpoint(endpoint)
    raw_headers = payload.get("headers", {})
    if not isinstance(raw_headers, dict) or any(
        not isinstance(key, str) or not isinstance(value, str) for key, value in raw_headers.items()
    ):
        raise failure("invalid_headers")
    from .protocols import _merge_authoritative_headers

    headers = _merge_authoritative_headers(
        raw_headers,
        {
            "A2A-Version": A2A_PROTOCOL_VERSION,
            "Accept": "text/event-stream" if method in STREAM_METHODS else "application/json",
            "Content-Type": "application/json",
        },
    )
    identifier = payload.get("id", operation_id or str(uuid4()))
    if type(identifier) not in (str, int):
        raise failure("invalid_request_id")
    body = {"jsonrpc": "2.0", "id": identifier, "method": method, "params": params}
    if len(_canonical(body).encode("utf-8")) > MAX_VALUE_BYTES:
        raise failure("request_limit")
    dispatched = False

    def observe_dispatch() -> None:
        nonlocal dispatched
        dispatched = True

    try:
        async with asyncio.timeout(timeout):
            response = await http.request(
                "POST",
                endpoint,
                json=body,
                headers=headers,
                operation_id=operation_id,
                follow_redirects=False,
                request_timeout=timeout,
                _authentication=authentication,
                _return_response=True,
                _on_dispatch=observe_dispatch,
            )
    except ToolError:
        if dispatched:
            raise _uncertain("transport_failure") from None
        raise
    except TimeoutError:
        if dispatched:
            raise _uncertain("timeout") from None
        raise failure("timeout") from None
    content_type = response.headers.get("content-type", "").split(";", 1)[0].strip().lower()
    try:
        if method in STREAM_METHODS:
            if content_type != "text/event-stream":
                raise _uncertain("invalid_stream_type")
            return _stream(response.content, identifier, method, params)
        if content_type != "application/json":
            raise _uncertain("invalid_response_type")
        value = _rpc(_strict_json(response.content), identifier)
        result = validate_v1_result(method, value)
        _validate_requested_identity(result, method, params)
        return result
    except _A2AOutcomeUncertain:
        raise
    except ToolError as error:
        if error.details.get("reason") == "protocol_error":
            raise
        raise _uncertain("invalid_result") from None
    except (OwaError, ValueError, UnicodeError, RecursionError):
        raise _uncertain("invalid_response") from None


def _authorize_card_credentials(
    http: HttpClient,
    endpoint: str,
    authentication: AuthenticationProvider | None,
    grants: Mapping[str, tuple[str, ...]] | None,
    bound_endpoint: str | None,
) -> None:
    from .security import OAuth2ClientCredentialsSecurityProfile, ProfileAuthentication

    if authentication is None:
        if http.client_cert is not None or type(http.verify_tls) not in (bool, str):
            raise failure("unbound_card_credentials")
        return
    if not isinstance(authentication, ProfileAuthentication) or not endpoint.startswith("https://"):
        raise failure("unbound_card_credentials")
    profile = authentication.security.profile(authentication.profile_name)
    if isinstance(profile, OAuth2ClientCredentialsSecurityProfile):
        # OAuth preparation additionally verifies its declared scope/issuer.
        if endpoint not in profile.allowed_resource_endpoints:
            raise failure("unbound_card_credentials")
        return
    if bound_endpoint == endpoint or endpoint in (grants or {}).get(
        authentication.profile_name, ()
    ):
        return
    raise failure("unbound_card_credentials")
