"""Runtime configuration, read from environment variables (see deploy/.env.example)."""

from functools import lru_cache
from pathlib import Path
from typing import Literal
from zoneinfo import ZoneInfo

from pydantic_settings import BaseSettings, SettingsConfigDict


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
    voice_de: str = ""
    voice_en: str = ""
    voice_el: str = ""
    # The background worker that renders speech files. Off in tests.
    speech_worker: bool = True

    # First-run admin account. Created only while no admin exists.
    admin_user: str = "admin"
    admin_password: str = ""
    session_days: int = 30

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


@lru_cache
def get_settings() -> Settings:
    return Settings()
