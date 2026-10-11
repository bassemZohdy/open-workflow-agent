import httpx

from openworkflow_adk.security.auth import OAuth2ClientCredentialsAuth, resolve_authentication


def test_all_authentication_variants_resolve(monkeypatch) -> None:
    monkeypatch.setenv("WORKFLOW_SECRET__user", "alice")
    monkeypatch.setenv("WORKFLOW_SECRET__password", "pw")
    monkeypatch.setenv("WORKFLOW_SECRET__token", "token")
    environ = {
        "WORKFLOW_SECRET__user": "alice",
        "WORKFLOW_SECRET__password": "pw",
        "WORKFLOW_SECRET__token": "token",
    }
    basic, _ = resolve_authentication(
        {"basic": {"username": {"use": "user"}, "password": {"use": "password"}}},
        environ=environ,
    )
    bearer, headers = resolve_authentication(
        {"bearer": {"token": {"use": "token"}}}, environ=environ
    )
    digest, _ = resolve_authentication(
        {"digest": {"username": {"use": "user"}, "password": {"use": "password"}}},
        environ=environ,
    )
    oauth, _ = resolve_authentication(
        {
            "oauth2": {
                "authority": "https://issuer.example/token",
                "grant": "client_credentials",
                "client": {"id": "client", "secret": {"use": "password"}},
            }
        },
        environ=environ,
    )
    oidc, _ = resolve_authentication(
        {
            "oidc": {
                "authority": "https://issuer.example/token",
                "grant": "client_credentials",
                "client": {"id": "client", "secret": {"use": "password"}},
            }
        },
        environ=environ,
    )

    assert isinstance(basic, httpx.BasicAuth)
    assert headers["authorization"] == "Bearer token"
    assert isinstance(digest, httpx.DigestAuth)
    assert isinstance(oauth, OAuth2ClientCredentialsAuth)
    assert isinstance(oidc, OAuth2ClientCredentialsAuth)
