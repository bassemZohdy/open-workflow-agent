"""Protocol adapters for enterprise OIDC and SAML sign-in flows.

.. warning::
   These adapters are intentionally ``unverified``: ``OidcClient`` performs no
   JWKS/signature verification of exchanged tokens, and ``SamlMetadata`` is a
   metadata parser only (no SAML response assertion validation). They are kept
   behind this internal namespace for integrations that accept the risk; do not
   rely on them for production-grade authentication without adding verification.
"""

from __future__ import annotations

import base64
import urllib.parse
from dataclasses import dataclass
from typing import Any

from defusedxml import ElementTree as ET

from openworkflow_adk.security.security import guarded_async_client, validate_egress


@dataclass(frozen=True)
class OidcMetadata:
    issuer: str
    authorization_endpoint: str
    token_endpoint: str
    jwks_uri: str | None = None


class OidcClient:
    """OIDC discovery and authorization-code exchange adapter."""

    def __init__(self, issuer: str, client_id: str, client_secret: str | None = None) -> None:
        validate_egress(issuer)
        self.issuer = issuer.rstrip("/")
        self.client_id = client_id
        self.client_secret = client_secret
        self.metadata: OidcMetadata | None = None

    async def discover(self) -> OidcMetadata:
        async with guarded_async_client() as client:
            response = await client.get(f"{self.issuer}/.well-known/openid-configuration")
            response.raise_for_status()
        data = response.json()
        self.metadata = OidcMetadata(
            issuer=str(data["issuer"]),
            authorization_endpoint=str(data["authorization_endpoint"]),
            token_endpoint=str(data["token_endpoint"]),
            jwks_uri=data.get("jwks_uri"),
        )
        return self.metadata

    def authorization_url(
        self, redirect_uri: str, state: str, nonce: str, scopes: list[str] | None = None
    ) -> str:
        if self.metadata is None:
            raise RuntimeError("discover OIDC metadata before creating an authorization URL")
        query = urllib.parse.urlencode(
            {
                "response_type": "code",
                "client_id": self.client_id,
                "redirect_uri": redirect_uri,
                "scope": " ".join(scopes or ["openid", "profile"]),
                "state": state,
                "nonce": nonce,
            }
        )
        return f"{self.metadata.authorization_endpoint}?{query}"

    async def exchange_code(self, code: str, redirect_uri: str) -> dict[str, Any]:
        if self.metadata is None:
            raise RuntimeError("discover OIDC metadata before exchanging a code")
        form = {
            "grant_type": "authorization_code",
            "code": code,
            "client_id": self.client_id,
            "redirect_uri": redirect_uri,
        }
        auth = (self.client_id, self.client_secret) if self.client_secret else None
        validate_egress(self.metadata.token_endpoint)
        async with guarded_async_client() as client:
            response = await client.post(self.metadata.token_endpoint, data=form, auth=auth)
            response.raise_for_status()
            return response.json()


@dataclass(frozen=True)
class SamlMetadata:
    entity_id: str
    single_sign_on_url: str

    @classmethod
    def from_xml(cls, xml: str) -> SamlMetadata:
        root = ET.fromstring(xml)
        entity_id = root.attrib.get("entityID")
        services = root.findall(".//{urn:oasis:names:tc:SAML:2.0:metadata}SingleSignOnService")
        redirect = next(
            (
                service.attrib.get("Location")
                for service in services
                if "HTTP-Redirect" in service.attrib.get("Binding", "")
            ),
            None,
        )
        if not entity_id or not redirect:
            raise ValueError("SAML metadata requires entityID and HTTP-Redirect SSO service")
        return cls(entity_id=entity_id, single_sign_on_url=redirect)

    def login_url(self, request: str, relay_state: str | None = None) -> str:
        query = {"SAMLRequest": base64.b64encode(request.encode()).decode()}
        if relay_state is not None:
            query["RelayState"] = relay_state
        return f"{self.single_sign_on_url}?{urllib.parse.urlencode(query)}"
