"""PostgresClusterReconciler — ReconcilerPlugin impl for no8s-operator."""

import asyncio
import json
import logging
import os
import time
from typing import Any, Dict, Optional

from plugins.base import ActionContext
from plugins.reconcilers.base import (
    ReconcilerContext,
    ReconcilerPlugin,
    ReconcileResult,
)

from no8s_postgres.cluster.health import HealthChecker
from no8s_postgres.cluster.initialiser import ClusterInitialiser
from no8s_postgres.config import PostgresConfig
from no8s_postgres.github.actions import download_artifact_content

logger = logging.getLogger(__name__)

_FINALIZER = "no8s-postgres"
_TERRAFORM_OUTPUTS_ARTIFACT = "terraform-outputs"
_ANSIBLE_WORKFLOW_FILE = "ansible.yml"


def _trigger_reason(resource: Dict[str, Any]) -> str:
    generation = resource.get("generation", 0)
    observed = resource.get("observed_generation", 0)
    if generation != observed:
        return "generation_changed"
    status = resource.get("status", "")
    if status == "deleting" or resource.get("deleted_at"):
        return "deletion"
    return "drift_check"


def _workflow_inputs(action: str, resource: Dict[str, Any]) -> dict:
    return {
        "action": action,
        "cluster_name": resource["name"],
        "resource_id": str(resource["id"]),
        "spec_json": json.dumps(resource.get("spec", {})),
    }


def _ansible_workflow_inputs(resource: Dict[str, Any]) -> dict:
    return {
        "cluster_name": resource["name"],
        "resource_id": str(resource["id"]),
        "spec_json": json.dumps(resource.get("spec", {})),
    }


def _make_action_ctx(
    resource: Dict[str, Any],
    config: PostgresConfig,
    workflow: str,
    inputs: dict,
) -> ActionContext:
    """Build an ActionContext for the github_actions plugin."""
    owner, _, repo = config.github_repo.partition("/")
    return ActionContext(
        resource_id=resource["id"],
        resource_name=resource["name"],
        generation=resource.get("generation", 0),
        spec={
            "owner": owner,
            "repo": repo,
            "workflow": workflow,
            "ref": config.github_ref,
            "inputs": inputs,
        },
        spec_hash=resource.get("spec_hash", ""),
    )


async def _run_terraform(
    action: str,
    resource: Dict[str, Any],
    config: PostgresConfig,
    ctx: ReconcilerContext,
):
    """Trigger a Terraform workflow via the github_actions plugin."""
    plugin = await ctx.get_action_plugin("github_actions")
    action_ctx = _make_action_ctx(
        resource,
        config,
        config.github_workflow,
        _workflow_inputs(action, resource),
    )
    workspace = await plugin.prepare(action_ctx)
    result = await plugin.apply(action_ctx, workspace)
    await plugin.cleanup(workspace)
    return result


async def _run_ansible(
    resource: Dict[str, Any],
    config: PostgresConfig,
    ctx: ReconcilerContext,
):
    """Trigger the Ansible workflow via the github_actions plugin."""
    plugin = await ctx.get_action_plugin("github_actions")
    action_ctx = _make_action_ctx(
        resource,
        config,
        _ANSIBLE_WORKFLOW_FILE,
        _ansible_workflow_inputs(resource),
    )
    workspace = await plugin.prepare(action_ctx)
    result = await plugin.apply(action_ctx, workspace)
    await plugin.cleanup(workspace)
    return result


class PostgresClusterReconciler(ReconcilerPlugin):
    """Reconciler plugin that provisions and manages HA PostgreSQL clusters.

    Infrastructure (EC2, networking, EBS) is provisioned via a GitHub Actions
    Terraform workflow dispatched via the operator's built-in github_actions
    action plugin. Ansible and Patroni are managed directly.
    """

    _name = "postgres_cluster"
    _resource_types = ["PostgresCluster"]

    def __init__(self) -> None:
        self._running: bool = False
        self._poll_interval: int = 30

    @property
    def name(self) -> str:
        return self._name

    @property
    def resource_types(self):
        return self._resource_types

    async def start(self, ctx: ReconcilerContext) -> None:
        """Run the reconciliation poll loop until shutdown event is set."""
        self._running = True
        logger.info("PostgresClusterReconciler starting")

        while not ctx.shutdown_event.is_set():
            resources = await ctx.get_resources_needing_reconciliation(
                self.resource_types, limit=10
            )
            for resource in resources:
                if ctx.shutdown_event.is_set():
                    break
                start_time = time.monotonic()
                result = await self.reconcile(resource, ctx)
                await ctx.record_reconciliation(
                    resource_id=resource["id"],
                    result=result,
                    duration_seconds=time.monotonic() - start_time,
                    trigger_reason=_trigger_reason(resource),
                )

            try:
                await asyncio.wait_for(
                    ctx.shutdown_event.wait(), timeout=self._poll_interval
                )
                break
            except asyncio.TimeoutError:
                continue

        logger.info("PostgresClusterReconciler stopped")

    async def reconcile(
        self, resource: Dict[str, Any], ctx: ReconcilerContext
    ) -> ReconcileResult:
        """Reconcile a single PostgresCluster resource."""
        resource_id: int = resource["id"]
        spec: dict = resource.get("spec", {})
        plugin_config: dict = resource.get("plugin_config") or {}
        generation: int = resource.get("generation", 0)
        observed_generation: int = resource.get("observed_generation", 0)
        status: str = resource.get("status", "pending")

        config = PostgresConfig.from_env_and_plugin_config(plugin_config)
        self._poll_interval = config.reconcile_poll_interval

        # Stage 1: Deletion
        if status == "deleting" or resource.get("deleted_at"):
            return await self._handle_delete(resource, config, ctx)

        # Mark as reconciling
        await ctx.update_status(
            resource_id, "reconciling", message="Reconciliation started"
        )

        # Stage 2: Drift detection
        needs_apply = generation != observed_generation

        if not needs_apply and status == "ready":
            # Infrastructure drift — trigger a Terraform plan via GHA
            plan_result = await _run_terraform("plan", resource, config, ctx)
            if not plan_result.success:
                needs_apply = True
                logger.info(
                    "Infrastructure drift detected for resource %d",
                    resource_id,
                )

            # Operational drift — Patroni health check
            if not needs_apply:
                patroni_endpoints = resource.get("outputs", {}).get(
                    "patroni_endpoints", []
                )
                health_result = await HealthChecker(config).check(
                    patroni_endpoints
                )
                if health_result.has_drift:
                    needs_apply = True
                    await ctx.set_condition(
                        resource_id,
                        "ClusterHealthy",
                        "False",
                        "HealthDriftDetected",
                        health_result.drift_details or "Cluster health drift detected",
                        observed_generation=generation,
                    )
                    logger.info(
                        "Cluster health drift detected for resource %d: %s",
                        resource_id,
                        health_result.drift_details,
                    )
                else:
                    await ctx.set_condition(
                        resource_id,
                        "ClusterHealthy",
                        "True",
                        "HealthCheckPassed",
                        "All Patroni nodes healthy",
                        observed_generation=generation,
                    )

        if not needs_apply:
            return ReconcileResult(
                success=True,
                message="No changes required",
                requeue_after=config.reconcile_poll_interval,
            )

        # Stage 3: Apply
        try:
            apply_result = await _run_terraform("apply", resource, config, ctx)
            if not apply_result.success:
                await ctx.set_condition(
                    resource_id,
                    "InfrastructureProvisioned",
                    "False",
                    "TerraformFailed",
                    apply_result.error_message or "",
                    observed_generation=generation,
                )
                raise RuntimeError(
                    "Terraform apply workflow failed: "
                    f"{apply_result.error_message}"
                )

            await ctx.set_condition(
                resource_id,
                "InfrastructureProvisioned",
                "True",
                "TerraformApplied",
                "Terraform apply succeeded",
                observed_generation=generation,
            )

            # Download terraform outputs from the artifact
            token = os.environ.get("GITHUB_TOKEN", "")
            artifacts = apply_result.outputs.get("artifacts", [])
            tf_artifact = next(
                (
                    a
                    for a in artifacts
                    if a["name"] == _TERRAFORM_OUTPUTS_ARTIFACT
                ),
                None,
            )
            if not tf_artifact:
                raise RuntimeError(
                    f"Artifact '{_TERRAFORM_OUTPUTS_ARTIFACT}' not found"
                    " in workflow outputs"
                )
            outputs = await download_artifact_content(
                tf_artifact["archive_download_url"], token
            )

            ansible_result = await _run_ansible(resource, config, ctx)
            if not ansible_result.success:
                await ctx.set_condition(
                    resource_id,
                    "AnsibleConfigured",
                    "False",
                    "AnsibleFailed",
                    ansible_result.error_message or "",
                    observed_generation=generation,
                )
                raise RuntimeError(
                    f"Ansible workflow failed: {ansible_result.error_message}"
                )

            await ctx.set_condition(
                resource_id,
                "AnsibleConfigured",
                "True",
                "AnsibleApplied",
                "Ansible configuration succeeded",
                observed_generation=generation,
            )

            patroni_endpoints = outputs.get("patroni_endpoints", [])
            initialiser = ClusterInitialiser(config)
            await initialiser.wait_for_quorum(patroni_endpoints)

            db_name: Optional[str] = spec.get("db_name")
            db_user: Optional[str] = spec.get("db_user")
            leader_endpoint: str = outputs.get("leader_endpoint", "")
            if db_name and db_user and leader_endpoint:
                await initialiser.create_database(
                    leader_endpoint, db_name, db_user
                )

            await initialiser.verify_replication(patroni_endpoints)

            await ctx.set_condition(
                resource_id,
                "ClusterInitialized",
                "True",
                "InitComplete",
                "Cluster initialized successfully",
                observed_generation=generation,
            )

        except Exception as exc:
            logger.exception(
                "Reconciliation failed for resource %d", resource_id
            )
            await ctx.update_status(resource_id, "failed", message=str(exc))
            return ReconcileResult(success=False, message=str(exc))

        # Stage 4: Status update
        await ctx.update_status(
            resource_id,
            "ready",
            message="Cluster ready",
            observed_generation=generation,
        )
        return ReconcileResult(
            success=True,
            message="Applied",
            requeue_after=config.reconcile_poll_interval,
        )

    async def _handle_delete(
        self,
        resource: Dict[str, Any],
        config: PostgresConfig,
        ctx: ReconcilerContext,
    ) -> ReconcileResult:
        """Tear down infrastructure via GHA and remove finalizers."""
        resource_id: int = resource["id"]

        try:
            destroy_result = await _run_terraform(
                "destroy", resource, config, ctx
            )
            if not destroy_result.success:
                raise RuntimeError(
                    "Terraform destroy workflow failed: "
                    f"{destroy_result.error_message}"
                )
        except Exception as exc:
            logger.exception(
                "Terraform destroy failed for resource %d", resource_id
            )
            await ctx.update_status(resource_id, "failed", message=str(exc))
            return ReconcileResult(success=False, message=str(exc))

        await ctx.remove_finalizer(resource_id, _FINALIZER)

        remaining = await ctx.get_finalizers(resource_id)
        if not remaining:
            await ctx.hard_delete_resource(resource_id)

        return ReconcileResult(success=True, message="Deleted")

    async def stop(self) -> None:
        """Signal the reconciler to stop."""
        self._running = False
        logger.info("PostgresClusterReconciler stop requested")
