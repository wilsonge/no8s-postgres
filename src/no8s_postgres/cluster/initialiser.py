"""Post-provision cluster initialisation."""

import asyncio
import logging
from typing import List

import asyncpg
import httpx

from no8s_postgres.config import PostgresConfig

logger = logging.getLogger(__name__)

_POLL_INTERVAL = 5  # seconds between quorum polls
_HEALTHY_REPLICA_STATES = {"streaming", "running"}


class ClusterInitialiser:
    def __init__(self, config: PostgresConfig) -> None:
        self.config = config

    async def wait_for_quorum(self, patroni_endpoints: List[str]) -> None:
        """Poll Patroni /cluster until a leader is elected or timeout expires."""
        loop = asyncio.get_running_loop()
        deadline = loop.time() + self.config.cluster_init_timeout

        async with httpx.AsyncClient(timeout=10.0) as client:
            while True:
                remaining = deadline - loop.time()
                if remaining <= 0:
                    raise TimeoutError(
                        f"Cluster quorum not established within "
                        f"{self.config.cluster_init_timeout}s"
                    )

                for ep in patroni_endpoints:
                    try:
                        resp = await client.get(f"http://{ep}/cluster")
                        resp.raise_for_status()
                        members = resp.json().get("members", [])
                        leaders = [m for m in members if m.get("role") == "leader"]
                        if leaders:
                            logger.info(
                                "Cluster quorum established, leader: %s",
                                leaders[0].get("name"),
                            )
                            return
                    except Exception as exc:
                        logger.debug("Patroni endpoint %s unreachable: %s", ep, exc)

                await asyncio.sleep(min(_POLL_INTERVAL, remaining))

    async def create_database(
        self, leader_endpoint: str, db_name: str, db_user: str
    ) -> None:
        """Create the application database and role on the leader PostgreSQL."""
        host, _, port_str = leader_endpoint.partition(":")
        port = int(port_str) if port_str else 5432

        conn = await asyncpg.connect(
            host=host,
            port=port,
            user=self.config.postgres_superuser,
            password=self.config.postgres_superuser_password,
            database="postgres",
        )
        try:
            # Use PostgreSQL's quote_ident to safely escape identifiers.
            quoted_user = await conn.fetchval("SELECT quote_ident($1)", db_user)
            quoted_db = await conn.fetchval("SELECT quote_ident($1)", db_name)

            user_exists = await conn.fetchval(
                "SELECT 1 FROM pg_roles WHERE rolname = $1", db_user
            )
            if not user_exists:
                await conn.execute(f"CREATE ROLE {quoted_user} WITH LOGIN")
                logger.info("Created role %r", db_user)

            db_exists = await conn.fetchval(
                "SELECT 1 FROM pg_database WHERE datname = $1", db_name
            )
            if not db_exists:
                await conn.execute(f"CREATE DATABASE {quoted_db} OWNER {quoted_user}")
                logger.info("Created database %r with owner %r", db_name, db_user)
        finally:
            await conn.close()

    async def verify_replication(self, endpoints: List[str]) -> None:
        """Verify that all Patroni replicas are streaming from the leader."""
        async with httpx.AsyncClient(timeout=10.0) as client:
            for ep in endpoints:
                try:
                    resp = await client.get(f"http://{ep}/cluster")
                    resp.raise_for_status()
                    members = resp.json().get("members", [])
                    replicas = [m for m in members if m.get("role") != "leader"]
                    not_streaming = [
                        m
                        for m in replicas
                        if m.get("state") not in _HEALTHY_REPLICA_STATES
                        or m.get("lag") == "unknown"
                    ]
                    if not_streaming:
                        names = [m.get("name", "?") for m in not_streaming]
                        raise RuntimeError(
                            f"Replicas not streaming after cluster init: {names}"
                        )
                    logger.info(
                        "Replication verified: %d replica(s) streaming",
                        len(replicas),
                    )
                    return
                except RuntimeError:
                    raise
                except Exception as exc:
                    logger.debug("Patroni endpoint %s unreachable: %s", ep, exc)

        raise RuntimeError(
            "Could not verify replication: no Patroni endpoint reachable: "
            f"{endpoints}"
        )
