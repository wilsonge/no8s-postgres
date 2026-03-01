"""EC2 inventory plugin configuration builder for no8s-postgres."""

import tempfile
from pathlib import Path
from typing import Any, Dict

import yaml

from no8s_postgres.config import PostgresConfig


def _extract(outputs: Dict[str, Any], key: str, default=None):
    """Extract a value from Terraform outputs — handles both flat and wrapped formats.

    ``terraform output -json`` wraps each value as ``{"value": ..., "type": ...}``.
    The reconciler tests and any pre-flattened artifact use the bare value directly.
    """
    val = outputs.get(key, default)
    if isinstance(val, dict) and "value" in val:
        return val["value"]
    return val


class InventoryBuilder:
    """Builds an Ansible aws_ec2 dynamic inventory plugin config from Terraform outputs.

    Instead of constructing a static inventory dict, this class writes an
    ``aws_ec2.yml`` config file that tells Ansible to discover EC2 instances
    from AWS using the ``amazon.aws.aws_ec2`` inventory plugin.

    Instances are selected by the ``ClusterName`` EC2 tag (set by Terraform) and
    grouped into ``patroni_primary`` (NodeIndex=0) and ``patroni_replicas``
    (NodeIndex>0).  ``ansible_host`` is set to each instance's public IP.
    """

    def build(self, terraform_outputs: Dict[str, Any], config: PostgresConfig) -> Path:
        """Write an ``aws_ec2.yml`` inventory plugin config and return its path.

        The caller (AnsibleRunner) is responsible for deleting the file after use.

        Args:
            terraform_outputs: Outputs from the Terraform apply step — either flat
                ``{"cluster_name": "prod", ...}`` or Terraform-JSON wrapped
                ``{"cluster_name": {"value": "prod", ...}, ...}``.
            config: Reconciler config supplying AWS region and SSH key path.

        Returns:
            Path to the temporary ``aws_ec2.yml`` file.
        """
        cluster_name: str = _extract(terraform_outputs, "cluster_name", "") or ""
        region: str = _extract(terraform_outputs, "aws_region", None) or config.aws_region

        compose: Dict[str, str] = {
            "ansible_host": "public_ip_address",
            "ansible_user": '"ubuntu"',
            "ansible_ssh_common_args": (
                f'"-o StrictHostKeyChecking=no -o ConnectTimeout={config.ansible_timeout}"'
            ),
            "node_index": "tags['NodeIndex'] | int",
            "cluster_name": "tags['ClusterName']",
        }

        if config.ssh_private_key_path:
            compose["ansible_ssh_private_key_file"] = f'"{config.ssh_private_key_path}"'

        inventory: Dict[str, Any] = {
            "plugin": "amazon.aws.aws_ec2",
            "regions": [region],
            "filters": {
                "tag:ClusterName": cluster_name,
                "instance-state-name": "running",
            },
            "hostnames": ["public_ip_address"],
            "compose": compose,
            "groups": {
                # NodeIndex tag is a string; compare as string to avoid type coercion.
                "patroni_primary": "tags.get('NodeIndex', '999') == '0'",
                "patroni_replicas": "tags.get('NodeIndex', '999') != '0'",
                "postgres_nodes": "'ClusterName' in tags",
            },
        }

        tmp = tempfile.NamedTemporaryFile(
            mode="w",
            suffix=".aws_ec2.yml",
            prefix=f"no8s-postgres-{cluster_name}-",
            delete=False,
        )
        try:
            yaml.dump(inventory, tmp, default_flow_style=False, allow_unicode=True)
        finally:
            tmp.close()

        return Path(tmp.name)
