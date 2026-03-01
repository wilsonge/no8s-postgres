"""Patroni REST API health checks for drift detection — stub implementation."""

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from no8s_postgres.config import PostgresConfig


@dataclass
class HealthResult:
    healthy: bool = True
    leader: Optional[str] = None
    members: List[Dict[str, Any]] = field(default_factory=list)
    drift_details: str = ""

    @property
    def has_drift(self) -> bool:
        return not self.healthy


class HealthChecker:
    def __init__(self, config: PostgresConfig) -> None:
        self.config = config

    async def check(self, patroni_endpoints: List[str]) -> HealthResult:
        """Stub: returns a healthy result without querying Patroni."""
        return HealthResult(healthy=True)
