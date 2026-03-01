"""Tests for InventoryBuilder (EC2 inventory plugin config generator)."""

from pathlib import Path

import pytest
import yaml

from no8s_postgres.ansible.inventory import InventoryBuilder, _extract
from no8s_postgres.config import PostgresConfig


def _config(**overrides) -> PostgresConfig:
    defaults = dict(
        aws_region="eu-west-1",
        ssh_private_key_path="/home/user/.ssh/id_rsa",
        ansible_timeout=30,
    )
    return PostgresConfig(**{**defaults, **overrides})


def _flat_outputs(cluster_name: str = "my-cluster", **extra) -> dict:
    return {"cluster_name": cluster_name, **extra}


def _wrapped_outputs(cluster_name: str = "my-cluster") -> dict:
    """Simulates ``terraform output -json`` wrapped format."""
    return {
        "cluster_name": {"value": cluster_name, "type": "string", "sensitive": False}
    }


# ---------------------------------------------------------------------------
# _extract helper
# ---------------------------------------------------------------------------


def test_extract_flat_value():
    assert _extract({"k": "v"}, "k") == "v"


def test_extract_wrapped_value():
    assert _extract({"k": {"value": "v", "type": "string"}}, "k") == "v"


def test_extract_missing_returns_default():
    assert _extract({}, "missing", "default") == "default"


# ---------------------------------------------------------------------------
# InventoryBuilder.build()
# ---------------------------------------------------------------------------


class TestInventoryBuilder:
    def _load(self, path: Path) -> dict:
        with open(path) as f:
            return yaml.safe_load(f)

    def test_returns_path_to_existing_file(self):
        path = InventoryBuilder().build(_flat_outputs(), _config())
        try:
            assert isinstance(path, Path)
            assert path.exists()
        finally:
            path.unlink(missing_ok=True)

    def test_writes_valid_yaml(self):
        path = InventoryBuilder().build(_flat_outputs("prod"), _config())
        try:
            content = self._load(path)
            assert isinstance(content, dict)
        finally:
            path.unlink(missing_ok=True)

    def test_uses_ec2_plugin(self):
        path = InventoryBuilder().build(_flat_outputs(), _config())
        try:
            content = self._load(path)
        finally:
            path.unlink(missing_ok=True)
        assert content["plugin"] == "amazon.aws.aws_ec2"

    def test_filters_by_cluster_name(self):
        path = InventoryBuilder().build(_flat_outputs("prod-postgres"), _config())
        try:
            content = self._load(path)
        finally:
            path.unlink(missing_ok=True)
        assert content["filters"]["tag:ClusterName"] == "prod-postgres"
        assert content["filters"]["instance-state-name"] == "running"

    def test_uses_region_from_config(self):
        path = InventoryBuilder().build(_flat_outputs(), _config(aws_region="us-east-1"))
        try:
            content = self._load(path)
        finally:
            path.unlink(missing_ok=True)
        assert "us-east-1" in content["regions"]

    def test_defines_required_groups(self):
        path = InventoryBuilder().build(_flat_outputs(), _config())
        try:
            content = self._load(path)
        finally:
            path.unlink(missing_ok=True)
        groups = content["groups"]
        assert "patroni_primary" in groups
        assert "patroni_replicas" in groups
        assert "postgres_nodes" in groups

    def test_patroni_primary_targets_node_index_zero(self):
        path = InventoryBuilder().build(_flat_outputs(), _config())
        try:
            content = self._load(path)
        finally:
            path.unlink(missing_ok=True)
        # The expression must select NodeIndex='0'
        assert "'0'" in content["groups"]["patroni_primary"]

    def test_sets_ansible_host_to_public_ip(self):
        path = InventoryBuilder().build(_flat_outputs(), _config())
        try:
            content = self._load(path)
        finally:
            path.unlink(missing_ok=True)
        assert "public_ip_address" in content["hostnames"]
        assert content["compose"]["ansible_host"] == "public_ip_address"

    def test_sets_ansible_user_to_ubuntu(self):
        path = InventoryBuilder().build(_flat_outputs(), _config())
        try:
            content = self._load(path)
        finally:
            path.unlink(missing_ok=True)
        assert "ubuntu" in content["compose"]["ansible_user"]

    def test_includes_ssh_key_when_configured(self):
        path = InventoryBuilder().build(_flat_outputs(), _config(ssh_private_key_path="/my/key"))
        try:
            content = self._load(path)
        finally:
            path.unlink(missing_ok=True)
        assert "/my/key" in content["compose"]["ansible_ssh_private_key_file"]

    def test_no_ssh_key_entry_when_not_configured(self):
        path = InventoryBuilder().build(_flat_outputs(), _config(ssh_private_key_path=""))
        try:
            content = self._load(path)
        finally:
            path.unlink(missing_ok=True)
        assert "ansible_ssh_private_key_file" not in content["compose"]

    def test_ssh_common_args_include_timeout(self):
        path = InventoryBuilder().build(_flat_outputs(), _config(ansible_timeout=45))
        try:
            content = self._load(path)
        finally:
            path.unlink(missing_ok=True)
        assert "45" in content["compose"]["ansible_ssh_common_args"]

    def test_handles_terraform_wrapped_output_format(self):
        """terraform output -json wraps values as {"value": ..., "type": ...}."""
        path = InventoryBuilder().build(_wrapped_outputs("wrapped-cluster"), _config())
        try:
            content = self._load(path)
        finally:
            path.unlink(missing_ok=True)
        assert content["filters"]["tag:ClusterName"] == "wrapped-cluster"

    def test_falls_back_to_config_region_when_not_in_outputs(self):
        path = InventoryBuilder().build({}, _config(aws_region="ap-southeast-1"))
        try:
            content = self._load(path)
        finally:
            path.unlink(missing_ok=True)
        assert "ap-southeast-1" in content["regions"]

    def test_filename_contains_cluster_name(self):
        path = InventoryBuilder().build(_flat_outputs("mycluster"), _config())
        try:
            assert "mycluster" in path.name
        finally:
            path.unlink(missing_ok=True)
