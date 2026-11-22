"""OpenID Connect sign-in for the admin, e.g. with Authelia (SPEC §7.1).

Authorization code flow with PKCE and a confidential client. The identity comes from the
userinfo endpoint, called with the access token over TLS, so no ID token has to be verified
here. Only the account name (`preferred_username`) and its `groups` are used.
"""

import base64
import hashlib
import logging
import secrets
import time
from dataclasses import dataclass
from typing import Any
from urllib.parse import quote, urlencode

import httpx

from kidsmedia.config import Settings

log = logging.getLogger("kidsmedia")

DISCOVERY_TTL = 3600.0
TIMEOUT = 10.0
SCOPES = "openid profile groups"


class OidcError(Exception):
    """Sign-in failed; the message is for the log, never for the browser."""


@dataclass(frozen=True)
class Provider:
    authorization_endpoint: str
    token_endpoint: str
    userinfo_endpoint: str


@dataclass(frozen=True)
class Identity:
    username: str
    groups: frozenset[str]


def new_pkce() -> tuple[str, str]:
    """(verifier, S256 challenge)."""
    verifier = secrets.token_urlsafe(48)
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    return verifier, base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")


class OidcClient:
    def __init__(self, settings: Settings, transport: httpx.BaseTransport | None = None) -> None:
        self._settings = settings
        self._transport = transport  # tests pass an httpx.MockTransport
        self._provider: Provider | None = None
        self._fetched_at = 0.0

    @property
    def issuer(self) -> str:
        return self._settings.oidc_issuer.strip().rstrip("/")

    def _http(self) -> httpx.Client:
        return httpx.Client(transport=self._transport, timeout=TIMEOUT, follow_redirects=False)

    def provider(self) -> Provider:
        now = time.monotonic()
        if self._provider is not None and now - self._fetched_at < DISCOVERY_TTL:
            return self._provider
        url = self.issuer + "/.well-known/openid-configuration"
        try:
            with self._http() as http:
                r = http.get(url)
            r.raise_for_status()
            doc: dict[str, Any] = r.json()
        except (httpx.HTTPError, ValueError) as e:
            raise OidcError(f"discovery at {url} failed: {e}") from e
        if str(doc.get("issuer", "")).rstrip("/") != self.issuer:
            raise OidcError(f"discovery issuer {doc.get('issuer')!r} is not OIDC_ISSUER")
        try:
            provider = Provider(
                authorization_endpoint=_https(doc["authorization_endpoint"]),
                token_endpoint=_https(doc["token_endpoint"]),
                userinfo_endpoint=_https(doc["userinfo_endpoint"]),
            )
        except (KeyError, TypeError) as e:
            raise OidcError(f"discovery document lacks {e}") from e
        self._provider, self._fetched_at = provider, now
        return provider

    def authorize_url(self, state: str, challenge: str) -> str:
        query = urlencode(
            {
                "response_type": "code",
                "client_id": self._settings.oidc_client_id,
                "redirect_uri": self._settings.oidc_redirect_uri,
                "scope": SCOPES,
                "state": state,
                "code_challenge": challenge,
                "code_challenge_method": "S256",
            }
        )
        return f"{self.provider().authorization_endpoint}?{query}"

    def identity(self, code: str, verifier: str) -> Identity:
        provider = self.provider()
        s = self._settings
        # client_secret_basic: id and secret are form-encoded before base64 (RFC 6749 §2.3.1).
        basic = base64.b64encode(
            f"{quote(s.oidc_client_id, safe='')}:{quote(s.oidc_client_secret, safe='')}".encode()
        ).decode("ascii")
        try:
            with self._http() as http:
                r = http.post(
                    provider.token_endpoint,
                    data={
                        "grant_type": "authorization_code",
                        "code": code,
                        "redirect_uri": s.oidc_redirect_uri,
                        "code_verifier": verifier,
                    },
                    headers={"Authorization": f"Basic {basic}", "Accept": "application/json"},
                )
                if r.status_code != 200:
                    raise OidcError(f"token endpoint answered {r.status_code}: {r.text[:300]}")
                access_token = r.json().get("access_token")
                if not isinstance(access_token, str) or not access_token:
                    raise OidcError("token response has no access_token")
                u = http.get(
                    provider.userinfo_endpoint,
                    headers={
                        "Authorization": f"Bearer {access_token}",
                        "Accept": "application/json",
                    },
                )
            if u.status_code != 200:
                raise OidcError(f"userinfo endpoint answered {u.status_code}")
            claims: dict[str, Any] = u.json()
        except (httpx.HTTPError, ValueError) as e:
            raise OidcError(f"token or userinfo request failed: {e}") from e

        username = claims.get("preferred_username")
        if not isinstance(username, str) or not username.strip():
            raise OidcError("userinfo has no preferred_username (is the 'profile' scope allowed?)")
        raw_groups = claims.get("groups") or []
        groups = (
            frozenset(g for g in raw_groups if isinstance(g, str))
            if isinstance(raw_groups, list)
            else frozenset()
        )
        return Identity(username.strip(), groups)


def _https(url: object) -> str:
    if not isinstance(url, str) or not url.startswith("https://"):
        raise OidcError(f"endpoint {url!r} is not https")
    return url
