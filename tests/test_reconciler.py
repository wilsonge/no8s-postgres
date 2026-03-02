"""Tests for PostgresClusterReconciler."""

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from no8s_postgres.reconciler import PostgresClusterReconciler


def _action_result(success=True, error_message=None, artifacts=None):
    """Build a minimal ActionResult-like object for github_actions plugin."""
    if artifacts is None and success:
        artifacts = [
            {
                "name": "terraform-outputs",
                "archive_download_url": (
                    "https://api.github.com/repos/org/repo"
                    "/actions/artifacts/1/zip"
                ),
            }
        ]
    return SimpleNamespace(
        success=success,
        error_message=error_message,
        outputs={"artifacts": artifacts or []},
    )


def _mock_plugin(apply_result=None):
    """Return an AsyncMock GHA plugin whose apply() returns apply_result."""
    if apply_result is None:
        apply_result = _action_result()
    plugin = AsyncMock()
    plugin.prepare = AsyncMock(return_value={})
    plugin.apply = AsyncMock(return_value=apply_result)
    plugin.cleanup = AsyncMock()
    return plugin


# ---------------------------------------------------------------------------
# Properties
# ---------------------------------------------------------------------------


def test_reconciler_name_and_types():
    r = PostgresClusterReconciler()
    assert r.name == "postgres_cluster"
    assert r.resource_types == ["PostgresCluster"]


# ---------------------------------------------------------------------------
# Start loop
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_start_loop_shuts_down(mock_ctx):
    """Setting shutdown event before start() causes it to exit cleanly."""
    r = PostgresClusterReconciler()
    mock_ctx.shutdown_event.set()
    await asyncio.wait_for(r.start(mock_ctx), timeout=2.0)


# ---------------------------------------------------------------------------
# No-op reconcile (generation already matches, status ready)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_reconcile_no_changes_when_generation_matches(
    mock_ctx, sample_resource
):
    """generation == observed_generation, status ready, no drift → requeue."""
    sample_resource["generation"] = 5
    sample_resource["observed_generation"] = 5
    sample_resource["status"] = "ready"

    # GHA plan returns success (no infra changes)
    mock_ctx._action_plugins["github_actions"] = _mock_plugin(
        apply_result=_action_result(success=True)
    )

    r = PostgresClusterReconciler()

    with patch("no8s_postgres.reconciler.HealthChecker") as MockHealth:
        health_instance = AsyncMock()
        health_instance.check.return_value = MagicMock(has_drift=False)
        MockHealth.return_value = health_instance

        result = await r.reconcile(sample_resource, mock_ctx)

    assert result.success is True
    assert result.requeue_after is not None
    statuses = [u["status"] for u in mock_ctx.status_updates]
    assert "failed" not in statuses


# ---------------------------------------------------------------------------
# Full provision path
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_reconcile_marks_reconciling_then_ready(
    mock_ctx, sample_resource
):
    """New resource (generation=1, observed=0) triggers GHA apply → ready."""
    r = PostgresClusterReconciler()

    plugin = _mock_plugin()
    mock_ctx._action_plugins["github_actions"] = plugin

    tf_outputs = {
        "leader_endpoint": "10.0.1.10:5432",
        "patroni_endpoints": ["10.0.1.10:8008"],
    }

    # First call: terraform apply (returns TF outputs artifact).
    # Second call: ansible workflow (returns simple success).
    ansible_result = _action_result(success=True, artifacts=[])
    plugin.apply = AsyncMock(
        side_effect=[_action_result(), ansible_result]
    )

    with (
        patch(
            "no8s_postgres.reconciler.download_artifact_content",
            new=AsyncMock(return_value=tf_outputs),
        ),
        patch("no8s_postgres.reconciler.ClusterInitialiser") as MockInit,
    ):
        init_instance = AsyncMock()
        init_instance.wait_for_quorum = AsyncMock()
        init_instance.create_database = AsyncMock()
        init_instance.verify_replication = AsyncMock()
        MockInit.return_value = init_instance

        result = await r.reconcile(sample_resource, mock_ctx)

    assert result.success is True
    assert result.message == "Applied"

    statuses = [u["status"] for u in mock_ctx.status_updates]
    assert "reconciling" in statuses
    assert "ready" in statuses

    # First plugin call: terraform apply
    assert plugin.apply.call_count == 2
    tf_ctx = plugin.apply.call_args_list[0][0][0]
    assert tf_ctx.spec["inputs"]["action"] == "apply"
    assert tf_ctx.spec["inputs"]["cluster_name"] == sample_resource["name"]

    # Second plugin call: ansible workflow
    ansible_ctx = plugin.apply.call_args_list[1][0][0]
    assert ansible_ctx.spec["workflow"] == "ansible.yml"
    assert (
        ansible_ctx.spec["inputs"]["cluster_name"] == sample_resource["name"]
    )

    init_instance.wait_for_quorum.assert_called_once()
    init_instance.create_database.assert_called_once()
    init_instance.verify_replication.assert_called_once()


# ---------------------------------------------------------------------------
# Deletion path
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_reconcile_handles_deletion(mock_ctx, sample_resource):
    """status=deleting should trigger destroy workflow and remove finalizer."""
    sample_resource["status"] = "deleting"
    mock_ctx._finalizers[1] = ["no8s-postgres"]

    plugin = _mock_plugin()
    mock_ctx._action_plugins["github_actions"] = plugin

    r = PostgresClusterReconciler()
    result = await r.reconcile(sample_resource, mock_ctx)

    assert result.success is True
    assert result.message == "Deleted"

    plugin.apply.assert_called_once()
    call_action_ctx = plugin.apply.call_args[0][0]
    assert call_action_ctx.spec["inputs"]["action"] == "destroy"
    assert 1 in mock_ctx._hard_deleted


# ---------------------------------------------------------------------------
# Failure handling
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_reconcile_failure_sets_failed_status(mock_ctx, sample_resource):
    """When the apply workflow fails, status should be set to failed."""
    plugin = _mock_plugin(
        apply_result=_action_result(
            success=False, error_message="workflow error", artifacts=[]
        )
    )
    mock_ctx._action_plugins["github_actions"] = plugin

    r = PostgresClusterReconciler()
    result = await r.reconcile(sample_resource, mock_ctx)

    assert result.success is False
    assert "Terraform apply workflow failed" in result.message
    statuses = [u["status"] for u in mock_ctx.status_updates]
    assert "failed" in statuses


# ---------------------------------------------------------------------------
# Conditions
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_reconcile_sets_conditions_on_success(mock_ctx, sample_resource):
    """Successful provision sets InfrastructureProvisioned, AnsibleConfigured, ClusterInitialized to True."""
    r = PostgresClusterReconciler()

    plugin = _mock_plugin()
    mock_ctx._action_plugins["github_actions"] = plugin

    tf_outputs = {
        "leader_endpoint": "10.0.1.10:5432",
        "patroni_endpoints": ["10.0.1.10:8008"],
    }
    plugin.apply = AsyncMock(
        side_effect=[_action_result(), _action_result(success=True, artifacts=[])]
    )

    with (
        patch(
            "no8s_postgres.reconciler.download_artifact_content",
            new=AsyncMock(return_value=tf_outputs),
        ),
        patch("no8s_postgres.reconciler.ClusterInitialiser") as MockInit,
    ):
        init_instance = AsyncMock()
        init_instance.wait_for_quorum = AsyncMock()
        init_instance.create_database = AsyncMock()
        init_instance.verify_replication = AsyncMock()
        MockInit.return_value = init_instance

        result = await r.reconcile(sample_resource, mock_ctx)

    assert result.success is True

    def _cond(type_):
        return next(
            (c for c in mock_ctx.conditions if c["type"] == type_), None
        )

    infra = _cond("InfrastructureProvisioned")
    assert infra is not None
    assert infra["status"] == "True"
    assert infra["reason"] == "TerraformApplied"

    ansible = _cond("AnsibleConfigured")
    assert ansible is not None
    assert ansible["status"] == "True"
    assert ansible["reason"] == "AnsibleApplied"

    init = _cond("ClusterInitialized")
    assert init is not None
    assert init["status"] == "True"
    assert init["reason"] == "InitComplete"


@pytest.mark.asyncio
async def test_reconcile_sets_infra_condition_false_on_terraform_failure(
    mock_ctx, sample_resource
):
    """Terraform failure sets InfrastructureProvisioned=False."""
    plugin = _mock_plugin(
        apply_result=_action_result(
            success=False, error_message="apply error", artifacts=[]
        )
    )
    mock_ctx._action_plugins["github_actions"] = plugin

    r = PostgresClusterReconciler()
    result = await r.reconcile(sample_resource, mock_ctx)

    assert result.success is False

    infra = next(
        (c for c in mock_ctx.conditions if c["type"] == "InfrastructureProvisioned"),
        None,
    )
    assert infra is not None
    assert infra["status"] == "False"
    assert infra["reason"] == "TerraformFailed"


@pytest.mark.asyncio
async def test_reconcile_sets_ansible_condition_false_on_ansible_failure(
    mock_ctx, sample_resource
):
    """Ansible failure sets AnsibleConfigured=False (InfrastructureProvisioned stays True)."""
    plugin = _mock_plugin()
    mock_ctx._action_plugins["github_actions"] = plugin

    tf_outputs = {
        "leader_endpoint": "10.0.1.10:5432",
        "patroni_endpoints": ["10.0.1.10:8008"],
    }
    plugin.apply = AsyncMock(
        side_effect=[
            _action_result(),  # terraform apply succeeds
            _action_result(success=False, error_message="ansible error", artifacts=[]),
        ]
    )

    r = PostgresClusterReconciler()

    with patch(
        "no8s_postgres.reconciler.download_artifact_content",
        new=AsyncMock(return_value=tf_outputs),
    ):
        result = await r.reconcile(sample_resource, mock_ctx)

    assert result.success is False

    def _cond(type_):
        return next(
            (c for c in mock_ctx.conditions if c["type"] == type_), None
        )

    infra = _cond("InfrastructureProvisioned")
    assert infra is not None
    assert infra["status"] == "True"

    ansible = _cond("AnsibleConfigured")
    assert ansible is not None
    assert ansible["status"] == "False"
    assert ansible["reason"] == "AnsibleFailed"


@pytest.mark.asyncio
async def test_reconcile_sets_cluster_healthy_on_no_drift(
    mock_ctx, sample_resource
):
    """When status=ready and no drift, ClusterHealthy=True is set."""
    sample_resource["generation"] = 3
    sample_resource["observed_generation"] = 3
    sample_resource["status"] = "ready"

    mock_ctx._action_plugins["github_actions"] = _mock_plugin(
        apply_result=_action_result(success=True)
    )

    r = PostgresClusterReconciler()

    with patch("no8s_postgres.reconciler.HealthChecker") as MockHealth:
        health_instance = AsyncMock()
        health_instance.check.return_value = MagicMock(
            has_drift=False, drift_details=""
        )
        MockHealth.return_value = health_instance

        result = await r.reconcile(sample_resource, mock_ctx)

    assert result.success is True

    healthy = next(
        (c for c in mock_ctx.conditions if c["type"] == "ClusterHealthy"), None
    )
    assert healthy is not None
    assert healthy["status"] == "True"
    assert healthy["reason"] == "HealthCheckPassed"


@pytest.mark.asyncio
async def test_reconcile_sets_cluster_healthy_false_on_health_drift(
    mock_ctx, sample_resource
):
    """When health check detects drift, ClusterHealthy=False and apply is triggered."""
    sample_resource["generation"] = 3
    sample_resource["observed_generation"] = 3
    sample_resource["status"] = "ready"

    plugin = _mock_plugin()
    mock_ctx._action_plugins["github_actions"] = plugin

    tf_outputs = {
        "leader_endpoint": "10.0.1.10:5432",
        "patroni_endpoints": ["10.0.1.10:8008"],
    }
    # First call: terraform plan (no infra drift), second: terraform apply, third: ansible
    plan_result = _action_result(success=True)
    apply_result = _action_result()
    ansible_result = _action_result(success=True, artifacts=[])
    plugin.apply = AsyncMock(
        side_effect=[plan_result, apply_result, ansible_result]
    )

    r = PostgresClusterReconciler()

    with (
        patch("no8s_postgres.reconciler.HealthChecker") as MockHealth,
        patch(
            "no8s_postgres.reconciler.download_artifact_content",
            new=AsyncMock(return_value=tf_outputs),
        ),
        patch("no8s_postgres.reconciler.ClusterInitialiser") as MockInit,
    ):
        health_instance = AsyncMock()
        health_instance.check.return_value = MagicMock(
            has_drift=True, drift_details="replica lag too high"
        )
        MockHealth.return_value = health_instance

        init_instance = AsyncMock()
        init_instance.wait_for_quorum = AsyncMock()
        init_instance.create_database = AsyncMock()
        init_instance.verify_replication = AsyncMock()
        MockInit.return_value = init_instance

        result = await r.reconcile(sample_resource, mock_ctx)

    assert result.success is True

    healthy = next(
        (c for c in mock_ctx.conditions if c["type"] == "ClusterHealthy"), None
    )
    assert healthy is not None
    assert healthy["status"] == "False"
    assert healthy["reason"] == "HealthDriftDetected"
    assert "replica lag" in healthy["message"]
