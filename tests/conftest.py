"""Shared test fixtures for no8s-postgres tests.

The operator package (no8s-operator) may not be installed in the test
environment.  We inject lightweight stubs for its public interface into
sys.modules *before* any reconciler import so that tests run without a live
operator or AWS/Terraform.
"""

import asyncio
import sys
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from types import ModuleType
from typing import Any, Dict, List, Optional

import pytest

# ---------------------------------------------------------------------------
# Operator stubs injected into sys.modules
# ---------------------------------------------------------------------------


@dataclass
class _ActionContext:
    resource_id: int
    resource_name: str
    generation: int
    spec: Dict[str, Any]
    spec_hash: str
    plugin_config: Dict[str, Any] = field(default_factory=dict)


@dataclass
class _ReconcileResult:
    success: bool = False
    message: str = ""
    requeue_after: Optional[int] = None


class _ReconcilerContext:
    """Minimal ABC stub — concrete impl is MockReconcilerContext below."""

    shutdown_event: asyncio.Event

    async def get_resources_needing_reconciliation(
        self, resource_type_names, limit=10
    ):
        raise NotImplementedError

    async def update_status(
        self, resource_id, status, message="", observed_generation=None
    ):
        raise NotImplementedError

    async def record_reconciliation(
        self,
        resource_id,
        result,
        duration_seconds=None,
        trigger_reason=None,
        drift_detected=False,
    ):
        raise NotImplementedError

    async def remove_finalizer(self, resource_id, finalizer):
        raise NotImplementedError

    async def get_finalizers(self, resource_id):
        raise NotImplementedError

    async def get_action_plugin(self, name: str):
        raise NotImplementedError

    async def hard_delete_resource(self, resource_id):
        raise NotImplementedError

    async def set_condition(
        self,
        resource_id,
        condition_type,
        status,
        reason,
        message="",
        observed_generation=None,
    ):
        raise NotImplementedError


class _ReconcilerPlugin(ABC):
    @property
    @abstractmethod
    def name(self) -> str:
        pass

    @property
    @abstractmethod
    def resource_types(self) -> List[str]:
        pass

    @abstractmethod
    async def start(self, ctx: _ReconcilerContext) -> None:
        pass

    @abstractmethod
    async def reconcile(
        self, resource: Dict[str, Any], ctx: _ReconcilerContext
    ):
        pass

    @abstractmethod
    async def stop(self) -> None:
        pass


def _make_module(name: str, **attrs) -> ModuleType:
    m = ModuleType(name)
    for k, v in attrs.items():
        setattr(m, k, v)
    return m


# Inject stubs only if operator not already on sys.path
if "plugins" not in sys.modules:
    sys.modules["plugins"] = _make_module("plugins")
    sys.modules["plugins.base"] = _make_module(
        "plugins.base",
        ActionContext=_ActionContext,
    )
    sys.modules["plugins.reconcilers"] = _make_module("plugins.reconcilers")
    sys.modules["plugins.reconcilers.base"] = _make_module(
        "plugins.reconcilers.base",
        ReconcilerPlugin=_ReconcilerPlugin,
        ReconcilerContext=_ReconcilerContext,
        ReconcileResult=_ReconcileResult,
    )


# ---------------------------------------------------------------------------
# In-memory mock of ReconcilerContext — no DB required
# ---------------------------------------------------------------------------


class MockReconcilerContext(_ReconcilerContext):
    def __init__(self):
        self.shutdown_event = asyncio.Event()
        self.status_updates: List[Dict[str, Any]] = []
        self.reconciliation_history: List[Dict[str, Any]] = []
        self._finalizers: Dict[int, List[str]] = {}
        self._hard_deleted: List[int] = []
        self._resources: List[Dict[str, Any]] = []
        self._action_plugins: Dict[str, Any] = {}
        self.conditions: List[Dict[str, Any]] = []

    async def get_resources_needing_reconciliation(
        self, resource_type_names: List[str], limit: int = 10
    ) -> List[Dict[str, Any]]:
        return self._resources[:limit]

    async def update_status(
        self,
        resource_id: int,
        status: str,
        message: str = "",
        observed_generation: Optional[int] = None,
    ) -> None:
        self.status_updates.append(
            {
                "resource_id": resource_id,
                "status": status,
                "message": message,
                "observed_generation": observed_generation,
            }
        )

    async def record_reconciliation(
        self,
        resource_id: int,
        result,
        duration_seconds: Optional[float] = None,
        trigger_reason: Optional[str] = None,
        drift_detected: bool = False,
    ) -> None:
        self.reconciliation_history.append(
            {
                "resource_id": resource_id,
                "success": result.success,
                "message": result.message,
                "duration_seconds": duration_seconds,
                "trigger_reason": trigger_reason,
                "drift_detected": drift_detected,
            }
        )

    async def remove_finalizer(self, resource_id: int, finalizer: str) -> None:
        fins = self._finalizers.get(resource_id, [])
        self._finalizers[resource_id] = [f for f in fins if f != finalizer]

    async def get_finalizers(self, resource_id: int) -> List[str]:
        return list(self._finalizers.get(resource_id, []))

    async def get_action_plugin(self, name: str):
        return self._action_plugins[name]

    async def hard_delete_resource(self, resource_id: int) -> bool:
        self._hard_deleted.append(resource_id)
        return True

    async def set_condition(
        self,
        resource_id: int,
        condition_type: str,
        status: str,
        reason: str,
        message: str = "",
        observed_generation: Optional[int] = None,
    ) -> None:
        self.conditions.append(
            {
                "resource_id": resource_id,
                "type": condition_type,
                "status": status,
                "reason": reason,
                "message": message,
                "observed_generation": observed_generation,
            }
        )


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def mock_ctx():
    return MockReconcilerContext()


@pytest.fixture
def sample_resource():
    return {
        "id": 1,
        "name": "prod-postgres",
        "resource_type_name": "PostgresCluster",
        "resource_type_version": "v1",
        "generation": 1,
        "observed_generation": 0,
        "status": "pending",
        "deleted_at": None,
        "plugin_config": {},
        "outputs": {},
        "spec": {
            "postgres_version": "16",
            "instance_type": "t3.medium",
            "cluster_size": 3,
            "volume_size_gb": 100,
            "volume_type": "gp3",
            "region": "eu-west-1",
            "db_name": "myapp",
            "db_user": "myapp_user",
            "ssh_key_name": "test-key",
            "allowed_cidrs": [],
            "backup_enabled": True,
            "pgbouncer_enabled": True,
            "tags": {},
        },
    }
