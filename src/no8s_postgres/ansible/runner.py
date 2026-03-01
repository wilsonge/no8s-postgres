"""Ansible playbook runner — executes ansible-playbook as an async subprocess."""

import asyncio
import json
import logging
import os
from pathlib import Path
from typing import Any, Dict, Union

from no8s_postgres.config import PostgresConfig

logger = logging.getLogger(__name__)

# Playbooks are bundled alongside this module.
_PLAYBOOKS_DIR = Path(__file__).parent / "playbooks"


class AnsibleRunner:
    """Runs Ansible playbooks against an EC2 dynamic inventory.

    Uses ``asyncio.create_subprocess_exec`` to invoke ``ansible-playbook``
    as a child process.  Streams combined stdout/stderr to the logger at
    DEBUG level and raises ``RuntimeError`` if the process exits non-zero.
    """

    def __init__(self, config: PostgresConfig) -> None:
        self.config = config

    async def run_playbook(
        self,
        playbook: str,
        inventory_path: Union[str, Path],
        extra_vars: Dict[str, Any],
    ) -> None:
        """Execute an Ansible playbook.

        Args:
            playbook: Filename of the playbook relative to the bundled
                ``playbooks/`` directory (e.g. ``"site.yml"``).
            inventory_path: Path to an Ansible inventory file — typically the
                ``aws_ec2.yml`` written by :class:`InventoryBuilder`.  The file
                is deleted after the playbook completes (success or failure).
            extra_vars: Variables forwarded to ``ansible-playbook`` via
                ``--extra-vars`` as a JSON string.  These come from the
                PostgresCluster spec so all spec fields are available in plays.

        Raises:
            RuntimeError: If ``ansible-playbook`` exits with a non-zero code.
        """
        playbook_path = _PLAYBOOKS_DIR / playbook
        inventory_path = Path(inventory_path)

        env = {
            **os.environ,
            "ANSIBLE_HOST_KEY_CHECKING": "False",
            "ANSIBLE_TIMEOUT": str(self.config.ansible_timeout),
            "AWS_DEFAULT_REGION": self.config.aws_region,
            "ANSIBLE_RETRY_FILES_ENABLED": "False",
            # Suppress cowsay output in logs.
            "ANSIBLE_NOCOWS": "1",
        }

        cmd = [
            "ansible-playbook",
            str(playbook_path),
            "-i",
            str(inventory_path),
            "--extra-vars",
            json.dumps(extra_vars),
        ]

        if self.config.ssh_private_key_path:
            cmd.extend(["--private-key", self.config.ssh_private_key_path])

        logger.info(
            "Running ansible-playbook %s with inventory %s", playbook, inventory_path
        )

        proc = await asyncio.create_subprocess_exec(
            *cmd,
            env=env,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
        )

        stdout, _ = await proc.communicate()
        output = stdout.decode(errors="replace") if stdout else ""

        for line in output.splitlines():
            logger.debug("[ansible] %s", line)

        # Clean up the inventory file regardless of success/failure.
        try:
            inventory_path.unlink(missing_ok=True)
        except OSError:
            pass

        if proc.returncode != 0:
            # Include the last 2 000 characters of output to stay within log limits.
            raise RuntimeError(
                f"ansible-playbook {playbook!r} failed (exit {proc.returncode}):\n"
                f"{output[-2000:]}"
            )

        logger.info("ansible-playbook %s completed successfully", playbook)
