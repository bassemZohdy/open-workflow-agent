import respx
from httpx import Response

from openworkflow_adk import OidcClient, SamlMetadata


@respx.mock
async def test_oidc_discovery_and_code_exchange(monkeypatch) -> None:
    # respx intercepts the transport; allow the unresolvable test host through
    # the egress guard.
    monkeypatch.setenv("WORKFLOW_EGRESS_SKIP_DNS", "1")
    respx.get("https://id.example/.well-known/openid-configuration").mock(
        return_value=Response(
            200,
            json={
                "issuer": "https://id.example",
                "authorization_endpoint": "https://id.example/auth",
                "token_endpoint": "https://id.example/token",
                "jwks_uri": "https://id.example/keys",
            },
        )
    )
    token = respx.post("https://id.example/token").mock(
        return_value=Response(200, json={"access_token": "token"})
    )
    client = OidcClient("https://id.example", "client", "secret")
    await client.discover()
    assert "client_id=client" in client.authorization_url("https://app/callback", "state", "nonce")
    result = await client.exchange_code("code", "https://app/callback")
    assert result["access_token"] == "token"
    assert token.called


def test_saml_metadata_builds_redirect_login_url() -> None:
    metadata = SamlMetadata.from_xml(
        '<EntityDescriptor xmlns="urn:oasis:names:tc:SAML:2.0:metadata" entityID="idp">'
        "<IDPSSODescriptor><SingleSignOnService "
        'Binding="urn:oasis:names:tc:SAML:2.0:bindings:HTTP-Redirect" '
        'Location="https://idp.example/sso" /></IDPSSODescriptor></EntityDescriptor>'
    )

    assert metadata.entity_id == "idp"
    assert "SAMLRequest=" in metadata.login_url("request", "state")
