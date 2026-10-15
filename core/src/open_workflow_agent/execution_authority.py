"""Private nonsecret static-profile authority; credentials never enter this context."""

from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from typing import Any

from .security import Principal

PROFILE = "owa/execution-authority/v1"
_CURRENT_AUTHORITY: ContextVar["ExecutionAuthority | None"] = ContextVar(
    "execution_authority", default=None
)


@dataclass(frozen=True, slots=True)
class ExecutionAuthority:
    principal: Principal
    security_profile: str

    def __post_init__(self) -> None:
        if (
            not isinstance(self.principal, Principal)
            or type(self.security_profile) is not str
            or not self.security_profile
            or len(self.security_profile.encode("utf-8")) > 256
            or any(ord(character) < 33 for character in self.security_profile)
        ):
            raise ValueError("execution authority requires a bounded named profile")

    @classmethod
    def capture(cls, principal: Principal, *, security_profile: str) -> "ExecutionAuthority":
        # Only trusted authenticated ingress calls this. Store admission also
        # resolves the actual deployment profile and compares every claim.
        return cls(principal, security_profile)

    def as_value(self) -> dict[str, Any]:
        return {
            "profile": PROFILE,
            "security_profile": self.security_profile,
            "principal": {
                "identity": self.principal.identity,
                "roles": sorted(self.principal.roles),
                "scopes": sorted(self.principal.scopes),
                "audience": sorted(self.principal.audience),
            },
        }

    @classmethod
    def from_value(cls, value: Any) -> "ExecutionAuthority":
        if (
            type(value) is not dict
            or set(value) != {"profile", "security_profile", "principal"}
            or value["profile"] != PROFILE
        ):
            raise ValueError("invalid authority descriptor")
        claims = value["principal"]
        if type(claims) is not dict or set(claims) != {"identity", "roles", "scopes", "audience"}:
            raise ValueError("invalid authority claims")
        for name in ("roles", "scopes", "audience"):
            items = claims[name]
            if (
                type(items) is not list
                or len(items) > 64
                or any(type(item) is not str for item in items)
                or items != sorted(set(items))
            ):
                raise ValueError("invalid authority claim set")
        return cls(
            Principal(
                claims["identity"],
                frozenset(claims["roles"]),
                frozenset(claims["scopes"]),
                frozenset(claims["audience"]),
            ),
            value["security_profile"],
        )


def current_authority() -> ExecutionAuthority | None:
    return _CURRENT_AUTHORITY.get()


@contextmanager
def authority_scope(authority: ExecutionAuthority | None) -> Iterator[None]:
    if authority is not None and not isinstance(authority, ExecutionAuthority):
        raise ValueError("authority scope requires a trusted typed descriptor")
    token = _CURRENT_AUTHORITY.set(authority)
    try:
        yield
    finally:
        _CURRENT_AUTHORITY.reset(token)
