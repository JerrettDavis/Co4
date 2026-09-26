from __future__ import annotations
import os
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urlparse
from cryptography.fernet import Fernet

LOOPBACK_HOSTS = frozenset({"localhost", "127.0.0.1", "::1"})

def is_loopback_url(url: str) -> bool:
    try:
        return urlparse(url).hostname in LOOPBACK_HOSTS
    except ValueError:
        return False

@dataclass
class Settings:
    database_url: str = field(default_factory=lambda: os.getenv("CO4_DATABASE_URL", "sqlite:///./co4.db"))
    public_url: str = field(default_factory=lambda: os.getenv("CO4_PUBLIC_URL", "http://localhost:8080").rstrip("/"))
    demo: bool = field(default_factory=lambda: os.getenv("CO4_DEMO", "false").lower() == "true")
    data_key: str = field(default_factory=lambda: os.getenv("CO4_DATA_KEY", ""))
    app_id: str = field(default_factory=lambda: os.getenv("GITHUB_APP_ID", ""))
    app_slug: str = field(default_factory=lambda: os.getenv("GITHUB_APP_SLUG", ""))
    private_key_path: str = field(default_factory=lambda: os.getenv("GITHUB_APP_PRIVATE_KEY_PATH", ""))
    webhook_secret: str = field(default_factory=lambda: os.getenv("GITHUB_WEBHOOK_SECRET", ""))
    client_id: str = field(default_factory=lambda: os.getenv("GITHUB_CLIENT_ID", ""))
    client_secret: str = field(default_factory=lambda: os.getenv("GITHUB_CLIENT_SECRET", ""))
    # Test seams: point the GitHub gateway at a local stand-in (e.g. the browser e2e mock). Non-HTTPS
    # overrides are only accepted for loopback hosts; see GitHub.__init__.
    github_url: str = field(default_factory=lambda: os.getenv("CO4_GITHUB_URL", "https://github.com").rstrip("/"))
    github_api_url: str = field(default_factory=lambda: os.getenv("CO4_GITHUB_API_URL", "https://api.github.com").rstrip("/"))
    background: bool = True
    secure_cookies: bool = True
    stale_seconds: int = 12 * 3600
    recovery_seconds: int = 12 * 3600
    # A much shorter, same-user-only grace period. When a person re-enrolls a device for a harness
    # (their old worker process died and a new device row is polling), their active lease can be
    # pinned to the dead device row for up to `stale_seconds` (12h) before the cross-user dib flow
    # even becomes eligible -- and dib explicitly refuses a same-user takeover. That deadlocks every
    # device belonging to the person until a human manually denies the stuck lease. Once a sibling
    # device of the same person is confirmed live (it is polling right now), an active lease that has
    # not heartbeated in this many seconds is presumed abandoned and is self-serviced back to the
    # queue. Lease fencing (generation tokens) makes this safe even if the "dead" device turns out to
    # still be alive and tries to check in later; it will simply be fenced out.
    orphan_seconds: int = 300
    event_retention_days: int = 30

    def validate(self) -> None:
        if self.demo:
            self.secure_cookies = self.public_url.startswith("https://")
            if not self.data_key:
                # Stable demo-only key. No real integrations or secrets permitted in demo.
                self.data_key = "AAECAwQFBgcICQoLDA0ODxAREhMUFRYXGBkaGxwdHh8="
            return
        missing = [k for k, v in {
            "CO4_DATA_KEY": self.data_key, "GITHUB_APP_ID": self.app_id,
            "GITHUB_APP_SLUG": self.app_slug, "GITHUB_APP_PRIVATE_KEY_PATH": self.private_key_path,
            "GITHUB_WEBHOOK_SECRET": self.webhook_secret, "GITHUB_CLIENT_ID": self.client_id,
            "GITHUB_CLIENT_SECRET": self.client_secret}.items() if not v]
        if missing:
            raise ValueError("Missing production settings: " + ", ".join(missing))
        if not self.public_url.startswith("https://"):
            if not is_loopback_url(self.public_url):
                raise ValueError("Production requires an HTTPS CO4_PUBLIC_URL")
            # Browsers drop Secure cookies over plain-HTTP loopback, which breaks every login path.
            self.secure_cookies = False
        if len(self.webhook_secret) < 32:
            raise ValueError("GITHUB_WEBHOOK_SECRET must contain at least 32 characters")
        Fernet(self.data_key.encode())
        if not Path(self.private_key_path).is_file():
            raise ValueError("GitHub App private key file not found")
