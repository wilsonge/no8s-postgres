"""Tests for AnsibleRunner."""

import json
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

from no8s_postgres.ansible.runner import AnsibleRunner
from no8s_postgres.config import PostgresConfig


def _config(**overrides) -> PostgresConfig:
    defaults = dict(
        aws_region="eu-west-1",
        ansible_timeout=30,
        ssh_private_key_path="",
    )
    return PostgresConfig(**{**defaults, **overrides})


def _mock_proc(returncode: int = 0, stdout: bytes = b"") -> AsyncMock:
    proc = AsyncMock()
    proc.returncode = returncode
    proc.communicate = AsyncMock(return_value=(stdout, b""))
    return proc


@pytest.fixture()
def inventory_file(tmp_path: Path) -> Path:
    f = tmp_path / "test.aws_ec2.yml"
    f.write_text("plugin: amazon.aws.aws_ec2\n")
    return f


# ---------------------------------------------------------------------------
# Basic invocation
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_run_playbook_invokes_ansible_playbook(inventory_file: Path):
    with patch("asyncio.create_subprocess_exec", return_value=_mock_proc()) as mock_exec:
        await AnsibleRunner(_config()).run_playbook("site.yml", inventory_file, {})

    mock_exec.assert_called_once()
    cmd = mock_exec.call_args[0]
    assert cmd[0] == "ansible-playbook"


@pytest.mark.asyncio
async def test_playbook_path_contains_site_yml(inventory_file: Path):
    with patch("asyncio.create_subprocess_exec", return_value=_mock_proc()) as mock_exec:
        await AnsibleRunner(_config()).run_playbook("site.yml", inventory_file, {})

    cmd = mock_exec.call_args[0]
    assert any("site.yml" in str(arg) for arg in cmd)


@pytest.mark.asyncio
async def test_inventory_path_passed_with_i_flag(inventory_file: Path):
    with patch("asyncio.create_subprocess_exec", return_value=_mock_proc()) as mock_exec:
        await AnsibleRunner(_config()).run_playbook("site.yml", inventory_file, {})

    cmd = list(mock_exec.call_args[0])
    assert "-i" in cmd
    i_idx = cmd.index("-i")
    assert str(inventory_file) == cmd[i_idx + 1]


# ---------------------------------------------------------------------------
# Extra vars
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_extra_vars_passed_as_json(inventory_file: Path):
    extra = {"postgres_version": "16", "cluster_size": 3}
    with patch("asyncio.create_subprocess_exec", return_value=_mock_proc()) as mock_exec:
        await AnsibleRunner(_config()).run_playbook("site.yml", inventory_file, extra)

    cmd = list(mock_exec.call_args[0])
    assert "--extra-vars" in cmd
    ev_idx = cmd.index("--extra-vars")
    assert json.loads(cmd[ev_idx + 1]) == extra


# ---------------------------------------------------------------------------
# SSH key
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_private_key_included_when_configured(inventory_file: Path):
    config = _config(ssh_private_key_path="/home/user/.ssh/id_rsa")
    with patch("asyncio.create_subprocess_exec", return_value=_mock_proc()) as mock_exec:
        await AnsibleRunner(config).run_playbook("site.yml", inventory_file, {})

    cmd = list(mock_exec.call_args[0])
    assert "--private-key" in cmd
    pk_idx = cmd.index("--private-key")
    assert cmd[pk_idx + 1] == "/home/user/.ssh/id_rsa"


@pytest.mark.asyncio
async def test_private_key_omitted_when_not_configured(inventory_file: Path):
    with patch("asyncio.create_subprocess_exec", return_value=_mock_proc()) as mock_exec:
        await AnsibleRunner(_config()).run_playbook("site.yml", inventory_file, {})

    cmd = list(mock_exec.call_args[0])
    assert "--private-key" not in cmd


# ---------------------------------------------------------------------------
# Environment variables
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_aws_region_set_in_env(inventory_file: Path):
    config = _config(aws_region="ap-southeast-1")
    with patch("asyncio.create_subprocess_exec", return_value=_mock_proc()) as mock_exec:
        await AnsibleRunner(config).run_playbook("site.yml", inventory_file, {})

    env = mock_exec.call_args[1]["env"]
    assert env["AWS_DEFAULT_REGION"] == "ap-southeast-1"


@pytest.mark.asyncio
async def test_host_key_checking_disabled_in_env(inventory_file: Path):
    with patch("asyncio.create_subprocess_exec", return_value=_mock_proc()) as mock_exec:
        await AnsibleRunner(_config()).run_playbook("site.yml", inventory_file, {})

    env = mock_exec.call_args[1]["env"]
    assert env["ANSIBLE_HOST_KEY_CHECKING"] == "False"


# ---------------------------------------------------------------------------
# Error handling
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_raises_runtime_error_on_non_zero_exit(inventory_file: Path):
    proc = _mock_proc(returncode=2, stdout=b"FAILED: something went wrong")
    with patch("asyncio.create_subprocess_exec", return_value=proc):
        with pytest.raises(RuntimeError, match="ansible-playbook.*failed"):
            await AnsibleRunner(_config()).run_playbook("site.yml", inventory_file, {})


@pytest.mark.asyncio
async def test_error_message_includes_output(inventory_file: Path):
    proc = _mock_proc(returncode=1, stdout=b"fatal: [host]: FAILED => task error")
    with patch("asyncio.create_subprocess_exec", return_value=proc):
        with pytest.raises(RuntimeError, match="task error"):
            await AnsibleRunner(_config()).run_playbook("site.yml", inventory_file, {})


# ---------------------------------------------------------------------------
# Inventory file cleanup
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_inventory_file_deleted_on_success(tmp_path: Path):
    inv = tmp_path / "inv.aws_ec2.yml"
    inv.write_text("plugin: amazon.aws.aws_ec2\n")

    with patch("asyncio.create_subprocess_exec", return_value=_mock_proc()):
        await AnsibleRunner(_config()).run_playbook("site.yml", inv, {})

    assert not inv.exists()


@pytest.mark.asyncio
async def test_inventory_file_deleted_on_failure(tmp_path: Path):
    inv = tmp_path / "inv.aws_ec2.yml"
    inv.write_text("plugin: amazon.aws.aws_ec2\n")

    proc = _mock_proc(returncode=1, stdout=b"failure")
    with patch("asyncio.create_subprocess_exec", return_value=proc):
        with pytest.raises(RuntimeError):
            await AnsibleRunner(_config()).run_playbook("site.yml", inv, {})

    assert not inv.exists()
