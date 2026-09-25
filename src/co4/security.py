from __future__ import annotations
import hashlib
import json
import re
import secrets
from typing import Any
from cryptography.fernet import Fernet

# Best-effort redaction, NOT an anonymization or data-loss-prevention guarantee.
PATTERNS = [
    re.compile(r"\b(?:gh[pousr]_[A-Za-z0-9_]{16,}|github_pat_[A-Za-z0-9_]{16,}|sk-[A-Za-z0-9_-]{16,})\b"),
    re.compile(r"(?i)(?:bearer\s+)[A-Za-z0-9._~+/-]{12,}"),
    re.compile(r"(?i)(?:api[_-]?key|password|secret|token)\s*[=:]\s*['\"]?[^\s'\",;]{8,}"),
    re.compile(r"-----BEGIN [^-]*PRIVATE KEY-----.*?-----END [^-]*PRIVATE KEY-----", re.S),
]

def redact(text: str) -> str:
    for pattern in PATTERNS:
        text = pattern.sub("[REDACTED]", text)
    return text

def digest(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()

def token(prefix: str = "co4") -> str:
    return prefix + "_" + secrets.token_urlsafe(32)

def canonical(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)

class Vault:
    def __init__(self, key: str):
        self.fernet = Fernet(key.encode())
    def seal(self, value: Any) -> str:
        return self.fernet.encrypt(canonical(value).encode()).decode()
    def open(self, value: str) -> Any:
        return json.loads(self.fernet.decrypt(value.encode()))
