"""Tests for HealthChecker."""

import pytest
from unittest.mock import AsyncMock, patch, MagicMock

from no8s_postgres.cluster.health import HealthChecker, HealthResult
from no8s_postgres.config import PostgresConfig


def _config():
    return PostgresConfig()


def _cluster_response(members):
    return {"members": members, "scope": "test-cluster"}


def _member(name, role="replica", state="running", lag=0):
    return {"name": name, "role": role, "state": state, "lag": lag}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_http_response(data):
    resp = MagicMock()
    resp.json.return_value = data
    resp.raise_for_status = MagicMock()
    return resp


# ---------------------------------------------------------------------------
# No endpoints reachable
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_check_returns_unhealthy_when_no_endpoints_reachable():
    checker = HealthChecker(_config())
    with patch("no8s_postgres.cluster.health.httpx.AsyncClient") as MockClient:
        client = AsyncMock()
        client.get = AsyncMock(side_effect=Exception("connection refused"))
        MockClient.return_value.__aenter__ = AsyncMock(return_value=client)
        MockClient.return_value.__aexit__ = AsyncMock(return_value=False)

        result = await checker.check(["10.0.1.10:8008", "10.0.1.11:8008"])

    assert result.healthy is False
    assert result.has_drift is True
    assert "could not reach" in result.drift_details


# ---------------------------------------------------------------------------
# Healthy cluster
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_check_healthy_cluster():
    members = [
        _member("node0", role="leader", state="running", lag=0),
        _member("node1", role="replica", state="running", lag=0),
        _member("node2", role="replica", state="running", lag=0),
    ]
    checker = HealthChecker(_config())
    with patch("no8s_postgres.cluster.health.httpx.AsyncClient") as MockClient:
        client = AsyncMock()
        client.get = AsyncMock(
            return_value=_make_http_response(_cluster_response(members))
        )
        MockClient.return_value.__aenter__ = AsyncMock(return_value=client)
        MockClient.return_value.__aexit__ = AsyncMock(return_value=False)

        result = await checker.check(["10.0.1.10:8008"])

    assert result.healthy is True
    assert result.has_drift is False
    assert result.leader == "node0"
    assert len(result.members) == 3
    assert result.drift_details == ""


# ---------------------------------------------------------------------------
# No leader
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_check_detects_no_leader():
    members = [
        _member("node0", role="replica", state="running"),
        _member("node1", role="replica", state="running"),
    ]
    checker = HealthChecker(_config())
    with patch("no8s_postgres.cluster.health.httpx.AsyncClient") as MockClient:
        client = AsyncMock()
        client.get = AsyncMock(
            return_value=_make_http_response(_cluster_response(members))
        )
        MockClient.return_value.__aenter__ = AsyncMock(return_value=client)
        MockClient.return_value.__aexit__ = AsyncMock(return_value=False)

        result = await checker.check(["10.0.1.10:8008"])

    assert result.healthy is False
    assert "no leader elected" in result.drift_details


# ---------------------------------------------------------------------------
# Split-brain
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_check_detects_split_brain():
    members = [
        _member("node0", role="leader", state="running"),
        _member("node1", role="leader", state="running"),
        _member("node2", role="replica", state="running"),
    ]
    checker = HealthChecker(_config())
    with patch("no8s_postgres.cluster.health.httpx.AsyncClient") as MockClient:
        client = AsyncMock()
        client.get = AsyncMock(
            return_value=_make_http_response(_cluster_response(members))
        )
        MockClient.return_value.__aenter__ = AsyncMock(return_value=client)
        MockClient.return_value.__aexit__ = AsyncMock(return_value=False)

        result = await checker.check(["10.0.1.10:8008"])

    assert result.healthy is False
    assert "split-brain" in result.drift_details


# ---------------------------------------------------------------------------
# Member in bad state
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_check_detects_member_in_bad_state():
    members = [
        _member("node0", role="leader", state="running"),
        _member("node1", role="replica", state="start failed"),
        _member("node2", role="replica", state="running"),
    ]
    checker = HealthChecker(_config())
    with patch("no8s_postgres.cluster.health.httpx.AsyncClient") as MockClient:
        client = AsyncMock()
        client.get = AsyncMock(
            return_value=_make_http_response(_cluster_response(members))
        )
        MockClient.return_value.__aenter__ = AsyncMock(return_value=client)
        MockClient.return_value.__aexit__ = AsyncMock(return_value=False)

        result = await checker.check(["10.0.1.10:8008"])

    assert result.healthy is False
    assert "node1" in result.drift_details
    assert "start failed" in result.drift_details


# ---------------------------------------------------------------------------
# Unknown replication lag
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_check_detects_unknown_lag():
    members = [
        _member("node0", role="leader", state="running", lag=0),
        {"name": "node1", "role": "replica", "state": "running", "lag": "unknown"},
    ]
    checker = HealthChecker(_config())
    with patch("no8s_postgres.cluster.health.httpx.AsyncClient") as MockClient:
        client = AsyncMock()
        client.get = AsyncMock(
            return_value=_make_http_response(_cluster_response(members))
        )
        MockClient.return_value.__aenter__ = AsyncMock(return_value=client)
        MockClient.return_value.__aexit__ = AsyncMock(return_value=False)

        result = await checker.check(["10.0.1.10:8008"])

    assert result.healthy is False
    assert "node1" in result.drift_details
    assert "unknown replication lag" in result.drift_details


# ---------------------------------------------------------------------------
# Falls through to second endpoint
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_check_falls_through_to_second_endpoint():
    members = [_member("node0", role="leader", state="running")]
    checker = HealthChecker(_config())

    good_response = _make_http_response(_cluster_response(members))

    call_count = 0

    async def fake_get(url):
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            raise Exception("timeout")
        return good_response

    with patch("no8s_postgres.cluster.health.httpx.AsyncClient") as MockClient:
        client = AsyncMock()
        client.get = fake_get
        MockClient.return_value.__aenter__ = AsyncMock(return_value=client)
        MockClient.return_value.__aexit__ = AsyncMock(return_value=False)

        result = await checker.check(["10.0.1.10:8008", "10.0.1.11:8008"])

    assert result.healthy is True
    assert call_count == 2


# ---------------------------------------------------------------------------
# Streaming state is healthy
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_check_streaming_state_is_healthy():
    members = [
        _member("node0", role="leader", state="running"),
        _member("node1", role="replica", state="streaming"),
    ]
    checker = HealthChecker(_config())
    with patch("no8s_postgres.cluster.health.httpx.AsyncClient") as MockClient:
        client = AsyncMock()
        client.get = AsyncMock(
            return_value=_make_http_response(_cluster_response(members))
        )
        MockClient.return_value.__aenter__ = AsyncMock(return_value=client)
        MockClient.return_value.__aexit__ = AsyncMock(return_value=False)

        result = await checker.check(["10.0.1.10:8008"])

    assert result.healthy is True