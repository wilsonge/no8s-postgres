"""Patroni REST API health checks for drift detection."""

import logging
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

import httpx

from no8s_postgres.config import PostgresConfig

logger = logging.getLogger(__name__)


@dataclass
class HealthResult:
    healthy: bool = True
    leader: Optional[str] = None
    members: List[Dict[str, Any]] = field(default_factory=list)
    drift_details: str = ""

    @property
    def has_drift(self) -> bool:
        return not self.healthy


_HEALTHY_STATES = {"running", "streaming"}


class HealthChecker:
    def __init__(self, config: PostgresConfig) -> None:
        self.config = config

    async def check(self, patroni_endpoints: List[str]) -> HealthResult:
        """Query Patroni /cluster on each endpoint and detect drift."""
        cluster_state = await self._fetch_cluster_state(patroni_endpoints)
        if cluster_state is None:
            return HealthResult(
                healthy=False,
                drift_details=(
                    f"could not reach any Patroni endpoint: {patroni_endpoints}"
                ),
            )
        return self._evaluate(cluster_state)

    async def _fetch_cluster_state(
        self, endpoints: List[str]
    ) -> Optional[Dict[str, Any]]:
        """Try each endpoint in turn; return the first successful /cluster response."""
        async with httpx.AsyncClient(timeout=10.0) as client:
            for ep in endpoints:
                url = f"http://{ep}/cluster"
                try:
                    resp = await client.get(url)
                    resp.raise_for_status()
                    return resp.json()
                except Exception as exc:
                    logger.debug("Patroni endpoint %s unreachable: %s", ep, exc)
        return None

    def _evaluate(self, cluster_state: Dict[str, Any]) -> HealthResult:
        members: List[Dict[str, Any]] = cluster_state.get("members", [])
        issues: List[str] = []

        leaders = [m for m in members if m.get("role") == "leader"]
        replicas = [m for m in members if m.get("role") != "leader"]

        if len(leaders) == 0:
            issues.append("no leader elected")
        elif len(leaders) > 1:
            names = [m.get("name", "?") for m in leaders]
            issues.append(f"split-brain: multiple leaders {names}")

        for m in members:
            state = m.get("state", "")
            name = m.get("name", "?")
            if state not in _HEALTHY_STATES:
                issues.append(f"member {name!r} in state {state!r}")

        for m in replicas:
            lag = m.get("lag")
            if lag == "unknown":
                name = m.get("name", "?")
                issues.append(
                    f"member {name!r} has unknown replication lag"
                    " (not connected to leader)"
                )

        leader_name: Optional[str] = leaders[0].get("name") if leaders else None

        if issues:
            return HealthResult(
                healthy=False,
                leader=leader_name,
                members=members,
                drift_details="; ".join(issues),
            )

        return HealthResult(healthy=True, leader=leader_name, members=members)
