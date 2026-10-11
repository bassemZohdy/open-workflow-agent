"""Minimal role-based authorization primitives for management integrations."""

from __future__ import annotations

from dataclasses import dataclass


class AuthorizationError(PermissionError):
    """Raised when a principal lacks a required permission."""


@dataclass(frozen=True)
class Principal:
    subject: str
    roles: frozenset[str] = frozenset()


class AccessPolicy:
    """Map roles to permissions and enforce management actions."""

    def __init__(self, roles: dict[str, set[str] | frozenset[str]] | None = None) -> None:
        self.roles = {name: frozenset(values) for name, values in (roles or {}).items()}

    def allows(self, principal: Principal, permission: str) -> bool:
        return any(permission in self.roles.get(role, frozenset()) for role in principal.roles)

    def require(self, principal: Principal, permission: str) -> None:
        if not self.allows(principal, permission):
            raise AuthorizationError(f"principal {principal.subject!r} lacks {permission!r}")
