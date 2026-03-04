"""Configuration dataclass for no8s-postgres.

Loaded from env vars and plugin_config.
"""

import os
from dataclasses import dataclass
from typing import Optional


@dataclass
class PostgresConfig:
    aws_region: str = "eu-west-1"
    tf_state_bucket: str = ""
    tf_state_key_prefix: str = "no8s-postgres/"
    tf_state_dynamodb_table: str = "terraform-locks"
    ansible_timeout: int = 30
    cluster_init_timeout: int = 300
    reconcile_poll_interval: int = 30
    github_repo: str = ""
    github_ref: str = "main"
    github_workflow: str = "terraform.yml"
    postgres_superuser: str = "postgres"
    postgres_superuser_password: str = ""

    @classmethod
    def from_env_and_plugin_config(
        cls, plugin_config: Optional[dict] = None
    ) -> "PostgresConfig":
        """Build config from env vars, overridden by plugin_config."""
        cfg = plugin_config or {}
        return cls(
            aws_region=cfg.get(
                "aws_region", os.environ.get("AWS_REGION", "eu-west-1")
            ),
            tf_state_bucket=cfg.get(
                "tf_state_bucket", os.environ.get("TF_STATE_BUCKET", "")
            ),
            tf_state_key_prefix=cfg.get(
                "tf_state_key_prefix",
                os.environ.get("TF_STATE_KEY_PREFIX", "no8s-postgres/"),
            ),
            tf_state_dynamodb_table=cfg.get(
                "tf_state_dynamodb_table",
                os.environ.get("TF_STATE_DYNAMODB_TABLE", "terraform-locks"),
            ),
            ansible_timeout=int(
                cfg.get(
                    "ansible_timeout",
                    os.environ.get("ANSIBLE_TIMEOUT", 30),
                )
            ),
            cluster_init_timeout=int(
                cfg.get(
                    "cluster_init_timeout",
                    os.environ.get("CLUSTER_INIT_TIMEOUT", 300),
                )
            ),
            reconcile_poll_interval=int(
                cfg.get(
                    "reconcile_poll_interval",
                    os.environ.get("RECONCILE_POLL_INTERVAL", 30),
                )
            ),
            github_repo=cfg.get(
                "github_repo", os.environ.get("GITHUB_REPO", "")
            ),
            github_ref=cfg.get(
                "github_ref", os.environ.get("GITHUB_REF", "main")
            ),
            github_workflow=cfg.get(
                "github_workflow",
                os.environ.get("GITHUB_WORKFLOW", "terraform.yml"),
            ),
            postgres_superuser=cfg.get(
                "postgres_superuser",
                os.environ.get("POSTGRES_SUPERUSER", "postgres"),
            ),
            postgres_superuser_password=cfg.get(
                "postgres_superuser_password",
                os.environ.get("POSTGRES_SUPERUSER_PASSWORD", ""),
            ),
        )
