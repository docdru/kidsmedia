"""Runtime configuration, read from environment variables (see deploy/.env.example)."""

from functools import lru_cache
from pathlib import Path
from typing import Literal
from zoneinfo import ZoneInfo

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from kidsmedia.webdav import check_url


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="", extra="ignore")

    tz: str = "Europe/Berlin"
    data_dir: Path = Path("/data")
    public_url: str = "http://localhost:8080"
    default_locale: Literal["en", "de", "el"] = "en"
    accel_redirect: bool = True
    max_upload_mb: int = 4096
    # Directory with the built admin SPA (admin/dist). Served at "/" when it exists.
    admin_dist: Path | None = None
    # APK baked into a release image by CI (SPEC §4.3): release.json + the APK.
    # Imported into data/apk on startup. Unset or missing in dev and non-release images.
    bundled_apk_dir: Path | None = None
    # Read aloud (SPEC §14.3): the Piper voices baked into the image (/app/voices), and a
    # voice per language by name (e.g. "el_GR-joy-medium"); empty = the default voice.
    bundled_voices_dir: Path | None = None
    # More example packs (SPEC §13.5), one folder per pack like kidsmedia/packs/. A derived
    # image (the family image) sets this to /app/packs; a pack here wins over a bundled one
    # with the same slug.
    extra_packs_dir: Path | None = None
    # Story pack zips on a WebDAV folder (SPEC §13.5a), e.g. a Seafile library. Empty = off.
    packs_webdav_url: str = ""
    packs_webdav_user: str = ""
    packs_webdav_password: str = ""
    voice_de: str = ""
    voice_en: str = ""
    voice_el: str = ""
    # The background worker that renders speech files. Off in tests.
    speech_worker: bool = True

    # First-run admin account. Created only while no admin exists.
    admin_user: str = "admin"
    admin_password: str = ""
    session_days: int = 30

    @field_validator("packs_webdav_url")
    @classmethod
    def _webdav_url(cls, value: str) -> str:
        return check_url(value.strip()) if value.strip() else ""

    # Single sign-on with OpenID Connect, e.g. Authelia (SPEC §7.1). Setting OIDC_ISSUER turns
    # it on and the password login off. Everyone who may sign in is a full admin: an account
    # listed in OIDC_ALLOWED_USERS or a member of a group in OIDC_ALLOWED_GROUPS (both
    # comma-separated). Both empty = nobody.
    oidc_issuer: str = ""
    oidc_client_id: str = "kidsmedia"
    oidc_client_secret: str = ""
    oidc_allowed_users: str = ""
    oidc_allowed_groups: str = ""
    # Name on the sign-in button.
    oidc_label: str = "Authelia"

    @property
    def oidc_enabled(self) -> bool:
        return bool(self.oidc_issuer.strip())

    @property
    def oidc_users(self) -> frozenset[str]:
        return _csv(self.oidc_allowed_users)

    @property
    def oidc_groups(self) -> frozenset[str]:
        return _csv(self.oidc_allowed_groups)

    @property
    def oidc_redirect_uri(self) -> str:
        return self.public_url.rstrip("/") + "/api/admin/oidc/callback"

    def oidc_allows(self, username: str, groups: frozenset[str] | set[str]) -> bool:
        """Whether an SSO account may use the admin. There are no roles: allowed = admin."""
        return username in self.oidc_users or bool(self.oidc_groups & set(groups))

    @property
    def zone(self) -> ZoneInfo:
        return ZoneInfo(self.tz)

    @property
    def db_path(self) -> Path:
        return self.data_dir / "kidsmedia.db"

    @property
    def media_dir(self) -> Path:
        return self.data_dir / "media"

    @property
    def thumbs_dir(self) -> Path:
        return self.data_dir / "thumbs"

    @property
    def apk_dir(self) -> Path:
        return self.data_dir / "apk"

    @property
    def voices_dir(self) -> Path:
        """Voices the parent adds (SPEC §14.3); they win over the bundled ones."""
        return self.data_dir / "voices"

    @property
    def speech_dir(self) -> Path:
        """Rendered speech, served like media via /_media/_speech/ (SPEC §14.3)."""
        return self.media_dir / "_speech"

    @property
    def photos_dir(self) -> Path:
        """Photos of drawings and stories, served like media via /_media/_photos/ (SPEC §15.3)."""
        return self.media_dir / "_photos"

    @property
    def secure_cookies(self) -> bool:
        return self.public_url.startswith("https://")


def _csv(value: str) -> frozenset[str]:
    return frozenset(part.strip() for part in value.split(",") if part.strip())


@lru_cache
def get_settings() -> Settings:
    return Settings()
