"""Post-provision cluster initialisation — stub implementation."""

from typing import List

from no8s_postgres.config import PostgresConfig


class ClusterInitialiser:
    def __init__(self, config: PostgresConfig) -> None:
        self.config = config

    async def wait_for_quorum(self, patroni_endpoints: List[str]) -> None:
        raise NotImplementedError(
            "ClusterInitialiser.wait_for_quorum() is not yet implemented"
        )

    async def create_database(
        self, leader_endpoint: str, db_name: str, db_user: str
    ) -> None:
        raise NotImplementedError(
            "ClusterInitialiser.create_database() is not yet implemented"
        )

    async def verify_replication(self, endpoints: List[str]) -> None:
        raise NotImplementedError(
            "ClusterInitialiser.verify_replication() is not yet implemented"
        )
