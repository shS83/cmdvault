"""Sync eligibility scanning interface; existing CLI heuristics are unchanged."""
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
