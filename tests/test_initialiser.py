"""Tests for ClusterInitialiser."""

import asyncio
import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from no8s_postgres.cluster.initialiser import ClusterInitialiser
from no8s_postgres.config import PostgresConfig


def _config(**kwargs):
    return PostgresConfig(
        cluster_init_timeout=kwargs.get("cluster_init_timeout", 10),
        postgres_superuser="postgres",
        postgres_superuser_password="secret",
    )


def _make_http_response(data):
    resp = MagicMock()
    resp.json.return_value = data
    resp.raise_for_status = MagicMock()
    return resp


def _cluster_response(members):
    return {"members": members, "scope": "test-cluster"}


def _member(name, role="replica", state="running", lag=0):
    return {"name": name, "role": role, "state": state, "lag": lag}


# ---------------------------------------------------------------------------
# wait_for_quorum
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_wait_for_quorum_succeeds_on_leader():
    members = [
        _member("node0", role="leader", state="running"),
        _member("node1", role="replica", state="streaming"),
    ]
    initialiser = ClusterInitialiser(_config())
    with patch(
        "no8s_postgres.cluster.initialiser.httpx.AsyncClient"
    ) as MockClient:
        client = AsyncMock()
        client.get = AsyncMock(
            return_value=_make_http_response(_cluster_response(members))
        )
        MockClient.return_value.__aenter__ = AsyncMock(return_value=client)
        MockClient.return_value.__aexit__ = AsyncMock(return_value=False)

        # Should return without raising
        await initialiser.wait_for_quorum(["10.0.1.10:8008"])


@pytest.mark.asyncio
async def test_wait_for_quorum_retries_until_leader_appears():
    no_leader = _cluster_response([_member("node0", role="replica")])
    with_leader = _cluster_response(
        [
            _member("node0", role="leader", state="running"),
            _member("node1", role="replica", state="streaming"),
        ]
    )
    call_count = 0

    async def fake_get(url):
        nonlocal call_count
        call_count += 1
        data = with_leader if call_count >= 2 else no_leader
        return _make_http_response(data)

    initialiser = ClusterInitialiser(_config(cluster_init_timeout=30))
    with patch(
        "no8s_postgres.cluster.initialiser.httpx.AsyncClient"
    ) as MockClient:
        client = AsyncMock()
        client.get = fake_get
        MockClient.return_value.__aenter__ = AsyncMock(return_value=client)
        MockClient.return_value.__aexit__ = AsyncMock(return_value=False)

        with patch("no8s_postgres.cluster.initialiser.asyncio.sleep") as mock_sleep:
            mock_sleep.return_value = None
            await initialiser.wait_for_quorum(["10.0.1.10:8008"])

    assert call_count >= 2


@pytest.mark.asyncio
async def test_wait_for_quorum_times_out():
    initialiser = ClusterInitialiser(_config(cluster_init_timeout=0))
    with patch(
        "no8s_postgres.cluster.initialiser.httpx.AsyncClient"
    ) as MockClient:
        client = AsyncMock()
        client.get = AsyncMock(side_effect=Exception("refused"))
        MockClient.return_value.__aenter__ = AsyncMock(return_value=client)
        MockClient.return_value.__aexit__ = AsyncMock(return_value=False)

        with pytest.raises(TimeoutError, match="quorum not established"):
            await initialiser.wait_for_quorum(["10.0.1.10:8008"])


@pytest.mark.asyncio
async def test_wait_for_quorum_falls_through_to_second_endpoint():
    members = [_member("node0", role="leader", state="running")]
    good = _make_http_response(_cluster_response(members))
    call_count = 0

    async def fake_get(url):
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            raise Exception("timeout")
        return good

    initialiser = ClusterInitialiser(_config(cluster_init_timeout=30))
    with patch(
        "no8s_postgres.cluster.initialiser.httpx.AsyncClient"
    ) as MockClient:
        client = AsyncMock()
        client.get = fake_get
        MockClient.return_value.__aenter__ = AsyncMock(return_value=client)
        MockClient.return_value.__aexit__ = AsyncMock(return_value=False)

        await initialiser.wait_for_quorum(["bad:8008", "10.0.1.10:8008"])

    assert call_count == 2


# ---------------------------------------------------------------------------
# create_database
# ---------------------------------------------------------------------------


def _make_conn(user_exists=False, db_exists=False):
    """Return a mock asyncpg connection."""
    conn = AsyncMock()

    async def fetchval(query, *args):
        if "quote_ident" in query:
            return args[0]  # return unquoted for simplicity
        if "pg_roles" in query:
            return 1 if user_exists else None
        if "pg_database" in query:
            return 1 if db_exists else None
        return None

    conn.fetchval = fetchval
    conn.execute = AsyncMock()
    conn.close = AsyncMock()
    return conn


@pytest.mark.asyncio
async def test_create_database_creates_role_and_db():
    conn = _make_conn(user_exists=False, db_exists=False)

    with patch("no8s_postgres.cluster.initialiser.asyncpg.connect", AsyncMock(return_value=conn)):
        await ClusterInitialiser(_config()).create_database(
            "10.0.1.10:5432", "myapp", "myapp_user"
        )

    calls = [str(c) for c in conn.execute.call_args_list]
    assert any("CREATE ROLE" in c for c in calls)
    assert any("CREATE DATABASE" in c for c in calls)
    conn.close.assert_awaited_once()


@pytest.mark.asyncio
async def test_create_database_skips_existing_role_and_db():
    conn = _make_conn(user_exists=True, db_exists=True)

    with patch("no8s_postgres.cluster.initialiser.asyncpg.connect", AsyncMock(return_value=conn)):
        await ClusterInitialiser(_config()).create_database(
            "10.0.1.10:5432", "myapp", "myapp_user"
        )

    conn.execute.assert_not_awaited()
    conn.close.assert_awaited_once()


@pytest.mark.asyncio
async def test_create_database_skips_existing_role_creates_db():
    conn = _make_conn(user_exists=True, db_exists=False)

    with patch("no8s_postgres.cluster.initialiser.asyncpg.connect", AsyncMock(return_value=conn)):
        await ClusterInitialiser(_config()).create_database(
            "10.0.1.10:5432", "myapp", "myapp_user"
        )

    calls = [str(c) for c in conn.execute.call_args_list]
    assert not any("CREATE ROLE" in c for c in calls)
    assert any("CREATE DATABASE" in c for c in calls)


@pytest.mark.asyncio
async def test_create_database_closes_conn_on_error():
    conn = _make_conn()
    conn.execute = AsyncMock(side_effect=Exception("pg error"))

    with patch("no8s_postgres.cluster.initialiser.asyncpg.connect", AsyncMock(return_value=conn)):
        with pytest.raises(Exception, match="pg error"):
            await ClusterInitialiser(_config()).create_database(
                "10.0.1.10:5432", "myapp", "myapp_user"
            )

    conn.close.assert_awaited_once()


@pytest.mark.asyncio
async def test_create_database_parses_default_port():
    conn = _make_conn()

    with patch(
        "no8s_postgres.cluster.initialiser.asyncpg.connect", AsyncMock(return_value=conn)
    ) as mock_connect:
        await ClusterInitialiser(_config()).create_database(
            "10.0.1.10", "myapp", "myapp_user"
        )

    _, kwargs = mock_connect.call_args
    assert kwargs["port"] == 5432


# ---------------------------------------------------------------------------
# verify_replication
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_verify_replication_passes_when_all_streaming():
    members = [
        _member("node0", role="leader", state="running"),
        _member("node1", role="replica", state="streaming"),
        _member("node2", role="replica", state="streaming"),
    ]
    initialiser = ClusterInitialiser(_config())
    with patch(
        "no8s_postgres.cluster.initialiser.httpx.AsyncClient"
    ) as MockClient:
        client = AsyncMock()
        client.get = AsyncMock(
            return_value=_make_http_response(_cluster_response(members))
        )
        MockClient.return_value.__aenter__ = AsyncMock(return_value=client)
        MockClient.return_value.__aexit__ = AsyncMock(return_value=False)

        await initialiser.verify_replication(["10.0.1.10:8008"])


@pytest.mark.asyncio
async def test_verify_replication_passes_with_running_replica():
    members = [
        _member("node0", role="leader", state="running"),
        _member("node1", role="replica", state="running"),
    ]
    initialiser = ClusterInitialiser(_config())
    with patch(
        "no8s_postgres.cluster.initialiser.httpx.AsyncClient"
    ) as MockClient:
        client = AsyncMock()
        client.get = AsyncMock(
            return_value=_make_http_response(_cluster_response(members))
        )
        MockClient.return_value.__aenter__ = AsyncMock(return_value=client)
        MockClient.return_value.__aexit__ = AsyncMock(return_value=False)

        await initialiser.verify_replication(["10.0.1.10:8008"])


@pytest.mark.asyncio
async def test_verify_replication_raises_when_replica_not_streaming():
    members = [
        _member("node0", role="leader", state="running"),
        _member("node1", role="replica", state="start failed"),
    ]
    initialiser = ClusterInitialiser(_config())
    with patch(
        "no8s_postgres.cluster.initialiser.httpx.AsyncClient"
    ) as MockClient:
        client = AsyncMock()
        client.get = AsyncMock(
            return_value=_make_http_response(_cluster_response(members))
        )
        MockClient.return_value.__aenter__ = AsyncMock(return_value=client)
        MockClient.return_value.__aexit__ = AsyncMock(return_value=False)

        with pytest.raises(RuntimeError, match="not streaming"):
            await initialiser.verify_replication(["10.0.1.10:8008"])


@pytest.mark.asyncio
async def test_verify_replication_raises_when_replica_lag_unknown():
    members = [
        _member("node0", role="leader", state="running"),
        {"name": "node1", "role": "replica", "state": "streaming", "lag": "unknown"},
    ]
    initialiser = ClusterInitialiser(_config())
    with patch(
        "no8s_postgres.cluster.initialiser.httpx.AsyncClient"
    ) as MockClient:
        client = AsyncMock()
        client.get = AsyncMock(
            return_value=_make_http_response(_cluster_response(members))
        )
        MockClient.return_value.__aenter__ = AsyncMock(return_value=client)
        MockClient.return_value.__aexit__ = AsyncMock(return_value=False)

        with pytest.raises(RuntimeError, match="not streaming"):
            await initialiser.verify_replication(["10.0.1.10:8008"])


@pytest.mark.asyncio
async def test_verify_replication_raises_when_no_endpoint_reachable():
    initialiser = ClusterInitialiser(_config())
    with patch(
        "no8s_postgres.cluster.initialiser.httpx.AsyncClient"
    ) as MockClient:
        client = AsyncMock()
        client.get = AsyncMock(side_effect=Exception("refused"))
        MockClient.return_value.__aenter__ = AsyncMock(return_value=client)
        MockClient.return_value.__aexit__ = AsyncMock(return_value=False)

        with pytest.raises(RuntimeError, match="no Patroni endpoint reachable"):
            await initialiser.verify_replication(["10.0.1.10:8008"])


@pytest.mark.asyncio
async def test_verify_replication_falls_through_to_second_endpoint():
    members = [
        _member("node0", role="leader", state="running"),
        _member("node1", role="replica", state="streaming"),
    ]
    good = _make_http_response(_cluster_response(members))
    call_count = 0

    async def fake_get(url):
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            raise Exception("timeout")
        return good

    initialiser = ClusterInitialiser(_config())
    with patch(
        "no8s_postgres.cluster.initialiser.httpx.AsyncClient"
    ) as MockClient:
        client = AsyncMock()
        client.get = fake_get
        MockClient.return_value.__aenter__ = AsyncMock(return_value=client)
        MockClient.return_value.__aexit__ = AsyncMock(return_value=False)

        await initialiser.verify_replication(["bad:8008", "10.0.1.10:8008"])

    assert call_count == 2