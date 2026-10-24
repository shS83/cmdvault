"""Local secret detection, shared by the picker and future sync preparation."""
import re
from dataclasses import dataclass
from typing import Any, Mapping, Protocol, Tuple


@dataclass(frozen=True)
class ScanResult:
    allowed: bool
    findings: Tuple[str, ...] = ()


class SecretScanner(Protocol):
    def scan(self, payload: Mapping[str, Any]) -> ScanResult:
        """Inspect locally; never log payloads or validate secrets remotely."""
        ...


PASSWORD_HASH_PATTERNS = (
    ('yescrypt', re.compile(r'\$y\$[./A-Za-z0-9]+\$[./A-Za-z0-9]+\$[./A-Za-z0-9]{8,}')),
    ('gost-yescrypt', re.compile(r'\$gy\$[./A-Za-z0-9]+\$[./A-Za-z0-9]+\$[./A-Za-z0-9]{8,}')),
    ('scrypt', re.compile(r'\$7\$[./A-Za-z0-9]+\$[./A-Za-z0-9]{8,}')),
    ('SHA-512-crypt', re.compile(r'\$6\$(?:rounds=\d+\$)?[./A-Za-z0-9]+\$[./A-Za-z0-9]{8,}')),
    ('SHA-256-crypt', re.compile(r'\$5\$(?:rounds=\d+\$)?[./A-Za-z0-9]+\$[./A-Za-z0-9]{8,}')),
    ('MD5-crypt', re.compile(r'\$1\$[./A-Za-z0-9]+\$[./A-Za-z0-9]{8,}')),
    ('Apache MD5-crypt', re.compile(r'\$apr1\$[./A-Za-z0-9]+\$[./A-Za-z0-9]{8,}')),
    ('bcrypt', re.compile(r'\$2[abxy]?\$\d{2}\$[./A-Za-z0-9]{8,}')),
    ('Argon2', re.compile(r'\$argon2(?:id|i|d)\$(?:v=\d+\$)?m=\d+,t=\d+,p=\d+\$[A-Za-z0-9+/=]+\$[A-Za-z0-9+/=]{8,}')),
)

PRIVATE_KEY = re.compile(r'-----BEGIN (?:OPENSSH |RSA |EC |DSA |ENCRYPTED )?PRIVATE KEY-----')
BEARER = re.compile(r'\bBearer\s+([A-Za-z0-9._~+/=-]{8,})', re.IGNORECASE)
SECRET_VALUE = re.compile(
    r'''(?ix)(?<![\w${])(?:[a-z][a-z0-9_]*_)?
    (?:api[_-]?key|access[_-]?key|access[_-]?token|auth[_-]?token|secret[_-]?key|client[_-]?secret|token|password|passwd)
    ["']?\s*(?:=|:|\s)\s*["']?([^\s"'`;|&<>]+)''')
TOKEN_SHAPES = re.compile(
    r'(?<![\w])(?:gh[pousr]_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{20,}'
    r'|sk-(?:proj-|ant-)?[A-Za-z0-9_-]{20,}|xox[baprs]-[A-Za-z0-9-]{20,})(?![\w])')


def secret_reasons(command):
    """Local heuristics only: never validate credentials or send them anywhere."""
    reasons = []
    # Shell-escaped dollar signs still represent the same literal hash.
    hash_text = command.replace('\\$', '$')
    for name, pattern in PASSWORD_HASH_PATTERNS:
        if pattern.search(hash_text):
            reasons.append(name + ' password hash')
    if PRIVATE_KEY.search(command):
        reasons.append('private key')
    if BEARER.search(command):
        reasons.append('Bearer token')
    if TOKEN_SHAPES.search(command):
        reasons.append('API token pattern')
    for match in SECRET_VALUE.finditer(command):
        value = match.group(1)
        # References to shell variables are not literal credentials.
        if len(value) >= 8 and not value.startswith(('$', '`')) and value.lower() not in (
                'your_api_key', 'your_token', 'changeme', 'redacted', '[redacted]'):
            reasons.append('literal credential')
            break
    return tuple(reasons)



class CommandSecretScanner:
    """Conservative local gate; does not approve unknown record shapes."""

    def scan(self, payload: Mapping[str, Any]) -> ScanResult:
        if not isinstance(payload, dict) or not payload:
            return ScanResult(False, ("unknown_payload",))
        texts = []
        for key, value in payload.items():
            if key not in {"command", "name", "description", "tags", "favorite",
                           "command_uuid", "tag_uuid"}:
                return ScanResult(False, ("unknown_payload",))
            if isinstance(value, str):
                texts.append(value)
            elif isinstance(value, list) and all(isinstance(item, str) for item in value):
                texts.extend(value)
            elif type(value) is bool and key == "favorite":
                pass
            else:
                return ScanResult(False, ("unknown_payload",))
        findings = tuple(dict.fromkeys(reason for text in texts for reason in secret_reasons(text)))
        return ScanResult(not findings, findings)
