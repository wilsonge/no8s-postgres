# no8s-postgres

## Project Overview

This is a standalone **reconciler plugin** for the [no8s-operator](https://github.com/wilsonge/no8s-operator) that provisions and manages highly-available PostgreSQL clusters on AWS EC2 using **Terraform via GitHub Actions** (infrastructure), **Ansible** (configuration), and **Patroni** (HA management).

Terraform plan/apply/destroy are dispatched as GitHub Actions `workflow_dispatch` runs (`.github/workflows/terraform.yml`). The reconciler triggers the workflow via the operator's built-in `github_actions` action plugin, polls for completion, and — after an apply — downloads the `terraform-outputs` artifact to obtain EC2 IPs and other outputs. Ansible then discovers nodes via the `amazon.aws.aws_ec2` inventory plugin (filtering by the `ClusterName` EC2 tag set by Terraform) rather than building a static inventory from the artifact.

The plugin implements the `ReconcilerPlugin` interface from no8s-operator (`src/plugins/reconcilers/base.py`) and is registered via Python entry points under the `no8s.reconcilers` group. It owns the full reconciliation loop for `PostgresCluster` resources.

## Architecture

```
┌─────────────────────────────────────────────────────────┐
│                   no8s-operator                          │
│              (controller startup)                        │
│                                                         │
│  discovers reconcilers via entry points                  │
│  calls reconciler.start(ctx) in dedicated asyncio task  │
└──────────────────────────┬──────────────────────────────┘
                           │
                           ▼
┌─────────────────────────────────────────────────────────┐
│          PostgresClusterReconciler                       │
│              (ReconcilerPlugin impl)                     │
│                                                         │
│  start() loop:                                          │
│    while not shutdown_event:                            │
│      resources = get_resources_needing_reconciliation() │
│      for resource in resources:                         │
│        await reconcile(resource, ctx)                   │
│      sleep(poll_interval)                               │
│                                                         │
│  reconcile() per-resource:                              │
│    1. Handle deletion (GHA destroy workflow)            │
│    2. Detect drift (GHA plan workflow + Patroni API)    │
│    3. Apply changes if needed:                          │
│       a. GHA apply workflow (EC2 + networking via TF)   │
│       b. Ansible (EC2 inventory plugin → all roles)     │
│       c. Cluster init (quorum, DB, roles)               │
│    4. Update resource status                            │
└─────────────────────────────────────────────────────────┘
```

## Reconciler Contract

This plugin implements the `ReconcilerPlugin` abstract base class from no8s-operator. The operator discovers and starts it at boot:

1. **`name`** (property) → `"postgres_cluster"`
2. **`resource_types`** (property) → `["PostgresCluster"]`
3. **`start(ctx: ReconcilerContext)`** → Run the reconciliation loop until `ctx.shutdown_event` is set
4. **`reconcile(resource, ctx: ReconcilerContext)`** → Reconcile a single `PostgresCluster` resource; returns `ReconcileResult`
5. **`stop()`** → Graceful shutdown, clean up connections

Key data types from no8s-operator (`src/plugins/reconcilers/base.py`):
- `ReconcilerContext` — db, registry, shutdown_event; methods: `get_resources_needing_reconciliation`, `update_status`, `record_reconciliation`, `remove_finalizer`, `hard_delete_resource`
- `ReconcileResult` — success, message, requeue_after

Resource status lifecycle managed by the reconciler: `pending` → `reconciling` → `ready` / `failed` / `deleting`

## Directory Structure

```
no8s-postgres/
├── CLAUDE.md                          # This file
├── README.md
├── pyproject.toml                     # Package config, entry points, package-data
├── .github/
│   └── workflows/
│       └── terraform.yml              # workflow_dispatch: plan | apply | destroy
├── src/
│   └── no8s_postgres/
│       ├── __init__.py
│       ├── reconciler.py              # PostgresClusterReconciler (ReconcilerPlugin impl)
│       ├── config.py                  # PostgresConfig dataclass (env vars + plugin_config)
│       ├── workspace.py               # ReconcileWorkspace (temp dir context manager; unused)
│       ├── github/
│       │   ├── __init__.py
│       │   └── actions.py             # download_artifact_content() — fetches GHA zip artifact
│       ├── terraform/
│       │   ├── __init__.py
│       │   ├── runner.py              # Superseded — Terraform now runs in GHA
│       │   └── templates/
│       │       ├── main.tf            # VPC, subnets, security groups, EC2 instances, EBS
│       │       ├── variables.tf       # Input variables
│       │       ├── outputs.tf         # Instance IPs, endpoints (used by reconciler + Ansible)
│       │       └── backend.tf         # S3 backend + provider config
│       ├── ansible/
│       │   ├── __init__.py
│       │   ├── inventory.py           # InventoryBuilder — writes aws_ec2.yml plugin config
│       │   ├── runner.py              # AnsibleRunner — asyncio subprocess wrapper
│       │   └── playbooks/
│       │       ├── requirements.yml   # Ansible Galaxy: amazon.aws>=8, ansible.posix>=1.5
│       │       ├── site.yml           # Main playbook (all roles; passwords via lookup)
│       │       └── roles/
│       │           ├── common/        # apt packages, sysctl PostgreSQL tuning, chrony
│       │           │   ├── tasks/main.yml
│       │           │   └── handlers/main.yml
│       │           ├── etcd/          # etcd cluster for Patroni DCS (all nodes)
│       │           │   ├── tasks/main.yml
│       │           │   ├── handlers/main.yml
│       │           │   └── templates/etcd.env.j2
│       │           ├── postgresql/    # pgdg repo, PostgreSQL install, EBS mount
│       │           │   ├── tasks/main.yml
│       │           │   └── handlers/main.yml
│       │           ├── patroni/       # Patroni service + patroni.yml config
│       │           │   ├── tasks/main.yml
│       │           │   ├── handlers/main.yml
│       │           │   └── templates/patroni.yml.j2
│       │           ├── pgbouncer/     # Connection pooling (when pgbouncer_enabled)
│       │           │   ├── tasks/main.yml
│       │           │   ├── handlers/main.yml
│       │           │   └── templates/pgbouncer.ini.j2
│       │           └── pgbackrest/    # S3-backed backups (when backup_enabled)
│       │               ├── tasks/main.yml
│       │               └── templates/pgbackrest.conf.j2
│       └── cluster/
│           ├── __init__.py
│           ├── initialiser.py         # ClusterInitialiser — stubs (NotImplementedError)
│           └── health.py              # HealthChecker — stub (returns healthy=True)
├── tests/
│   ├── conftest.py                    # operator stubs injected into sys.modules
│   ├── test_reconciler.py             # Reconciler lifecycle tests (6 tests)
│   ├── test_inventory.py              # InventoryBuilder tests (18 tests)
│   ├── test_ansible_runner.py         # AnsibleRunner tests (12 tests)
│   ├── test_terraform_runner.py       # (placeholder)
│   └── test_cluster_initialiser.py    # (placeholder)
└── .gitignore
```

## EC2 Inventory Plugin

`InventoryBuilder.build(terraform_outputs, config)` writes a temp `aws_ec2.yml` file and returns its `Path`.  `AnsibleRunner.run_playbook()` passes this as `-i <path>` and deletes the file after the playbook completes.

The generated config:

```yaml
plugin: amazon.aws.aws_ec2
regions: [eu-west-1]
filters:
  tag:ClusterName: <cluster-name>   # matches Terraform's ClusterName tag
  instance-state-name: running
hostnames: [public_ip_address]
compose:
  ansible_host: public_ip_address
  ansible_user: '"ubuntu"'
  ansible_ssh_private_key_file: '"/path/to/key"'   # omitted if SSH_PRIVATE_KEY_PATH unset
  ansible_ssh_common_args: '"-o StrictHostKeyChecking=no -o ConnectTimeout=30"'
  node_index: "tags['NodeIndex'] | int"
  cluster_name: "tags['ClusterName']"
groups:
  patroni_primary:  "tags.get('NodeIndex', '999') == '0'"
  patroni_replicas: "tags.get('NodeIndex', '999') != '0'"
  postgres_nodes:   "'ClusterName' in tags"
```

Requires the `amazon.aws` Ansible collection:
```bash
ansible-galaxy collection install -r src/no8s_postgres/ansible/playbooks/requirements.yml
```

## Resource Type Schema

This reconciler handles `PostgresCluster` resources:

```json
{
  "name": "PostgresCluster",
  "version": "v1",
  "description": "HA PostgreSQL cluster on EC2 with Patroni",
  "schema": {
    "type": "object",
    "required": ["postgres_version", "instance_type", "cluster_size"],
    "properties": {
      "postgres_version": {
        "type": "string",
        "enum": ["14", "15", "16", "17"],
        "description": "PostgreSQL major version"
      },
      "instance_type": {
        "type": "string",
        "default": "t3.medium",
        "description": "EC2 instance type for each node"
      },
      "cluster_size": {
        "type": "integer",
        "minimum": 1,
        "maximum": 7,
        "default": 3,
        "description": "Number of nodes (odd number recommended for quorum)"
      },
      "volume_size_gb": {
        "type": "integer",
        "minimum": 20,
        "maximum": 16000,
        "default": 100,
        "description": "EBS volume size per node in GB"
      },
      "volume_type": {
        "type": "string",
        "enum": ["gp3", "io1", "io2"],
        "default": "gp3"
      },
      "region": {
        "type": "string",
        "default": "eu-west-1"
      },
      "vpc_cidr": {
        "type": "string",
        "default": "10.0.0.0/16",
        "description": "CIDR for the new VPC"
      },
      "db_name": {
        "type": "string",
        "description": "Application database to create after cluster init"
      },
      "db_user": {
        "type": "string",
        "description": "Application database user to create"
      },
      "allowed_cidrs": {
        "type": "array",
        "items": { "type": "string" },
        "default": [],
        "description": "CIDRs allowed to connect to PostgreSQL (port 5432)"
      },
      "backup_enabled": {
        "type": "boolean",
        "default": true
      },
      "backup_retention_days": {
        "type": "integer",
        "default": 7,
        "minimum": 1
      },
      "pgbouncer_enabled": {
        "type": "boolean",
        "default": true
      },
      "pgbouncer_pool_size": {
        "type": "integer",
        "default": 20,
        "minimum": 1
      },
      "ssh_key_name": {
        "type": "string",
        "description": "AWS SSH key pair name for EC2 access"
      },
      "tags": {
        "type": "object",
        "additionalProperties": { "type": "string" },
        "default": {}
      }
    }
  }
}
```

## Resource Example

```json
{
  "name": "prod-postgres",
  "resource_type_name": "PostgresCluster",
  "resource_type_version": "v1",
  "spec": {
    "postgres_version": "16",
    "instance_type": "r6i.xlarge",
    "cluster_size": 3,
    "volume_size_gb": 500,
    "volume_type": "gp3",
    "region": "eu-west-1",
    "db_name": "myapp",
    "db_user": "myapp_user",
    "ssh_key_name": "my-key",
    "allowed_cidrs": ["10.0.0.0/8"],
    "backup_enabled": true,
    "pgbouncer_enabled": true,
    "tags": {
      "Environment": "production",
      "Team": "platform"
    }
  }
}
```

## Configuration (Environment Variables)

| Variable | Description | Default |
|---|---|---|
| `AWS_REGION` | AWS region | `eu-west-1` |
| `TF_STATE_BUCKET` | S3 bucket for Terraform state | required |
| `TF_STATE_DYNAMODB_TABLE` | DynamoDB table for state locking | `terraform-locks` |
| `TF_STATE_KEY_PREFIX` | Key prefix in the S3 bucket | `no8s-postgres/` |
| `SSH_PRIVATE_KEY_PATH` | Path to SSH key for Ansible | required |
| `ANSIBLE_TIMEOUT` | Ansible SSH timeout in seconds | `30` |
| `CLUSTER_INIT_TIMEOUT` | Timeout waiting for cluster quorum (seconds) | `300` |
| `RECONCILE_POLL_INTERVAL` | Seconds between reconciliation polls | `30` |
| `GITHUB_TOKEN` | GitHub personal access token (artifact download) | required |
| `GITHUB_REPO` | GitHub repository containing the Terraform workflow (`owner/repo`) | required |
| `GITHUB_REF` | Branch/ref to run the workflow on | `main` |
| `GITHUB_WORKFLOW` | Workflow filename in `.github/workflows/` | `terraform.yml` |

The GitHub Actions workflow reads repository secrets for AWS OIDC (`AWS_ROLE_ARN`, `AWS_REGION`, `TF_STATE_BUCKET`, `TF_STATE_DYNAMODB_TABLE`) — set these in the repository's Actions secrets.

## Entry Point Registration

In `pyproject.toml`:

```toml
[project.entry-points.'no8s.reconcilers']
postgres_cluster = 'no8s_postgres.reconciler:PostgresClusterReconciler'
```

## Reconcile Flow (detail)

The `reconcile()` method handles each resource in four stages:

### Stage 1: Deletion
If the resource has `status=deleting` or a deletion timestamp:
1. Trigger the GHA `terraform.yml` workflow with `action=destroy` via `_run_terraform("destroy", ...)`, which uses the operator's `github_actions` action plugin
2. Poll until the workflow completes; raise on non-success
3. Call `ctx.remove_finalizer(resource_id, "no8s-postgres")`
4. Call `ctx.hard_delete_resource(resource_id)` if no remaining finalizers
5. Return `ReconcileResult(success=True, message="Deleted")`

### Stage 2: Drift Detection
Only when `generation == observed_generation` and `status == "ready"`:
1. **Infrastructure drift** — `_run_terraform("plan", ...)` via GHA plugin; non-success conclusion sets `needs_apply = True`
2. **Cluster health drift** — `HealthChecker(config).check(patroni_endpoints)` (currently a no-op stub returning `healthy=True`)

If no drift, requeue after poll interval.

### Stage 3: Apply (if drift or first provision)
1. **Terraform (via GitHub Actions)** — `_run_terraform("apply", ...)` via GHA plugin; download the `terraform-outputs` artifact (JSON) via `download_artifact_content()`
2. **Ansible** — `InventoryBuilder().build(outputs, config)` writes an `aws_ec2.yml` EC2 inventory plugin config targeting instances by `ClusterName` tag; `AnsibleRunner(config).run_playbook("site.yml", inventory_path, spec)` runs `ansible-playbook` as an asyncio subprocess with the full spec as `--extra-vars`:
   - `common` — OS packages, sysctl tuning, chrony
   - `etcd` — Install and cluster etcd (Patroni DCS backend); idempotent via `ETCD_INITIAL_CLUSTER_STATE`
   - `postgresql` — pgdg repo, install PostgreSQL, stop default service, format + mount EBS volume
   - `patroni` — Install via pip, template `patroni.yml` (etcd hosts from inventory group), systemd unit
   - `pgbouncer` — Install and configure PgBouncer (when `pgbouncer_enabled=true`)
   - `pgbackrest` — Configure S3-backed backups (when `backup_enabled=true`)
3. **Cluster init** — `ClusterInitialiser.wait_for_quorum()`, `create_database()`, `verify_replication()` (stubs — `NotImplementedError`)

### Stage 4: Status Update
Call `ctx.update_status(resource_id, "ready", ...)` and `ctx.record_reconciliation(...)` with outputs:

```json
{
  "leader_endpoint": "10.0.1.10:5432",
  "replica_endpoints": ["10.0.1.11:5432", "10.0.1.12:5432"],
  "pgbouncer_endpoint": "10.0.1.10:6432",
  "patroni_endpoints": ["10.0.1.10:8008", "10.0.1.11:8008", "10.0.1.12:8008"],
  "vpc_id": "vpc-abc123",
  "db_name": "myapp",
  "db_user": "myapp_user",
  "cluster_name": "prod-postgres"
}
```

## Development

```bash
# Install in dev mode
pip install -e ".[dev]"

# Install Ansible collections (required for playbooks)
ansible-galaxy collection install -r src/no8s_postgres/ansible/playbooks/requirements.yml

# Format
black src/ tests/

# Lint
flake8 src/ tests/
```

## Running Tests

The test suite requires no live operator installation, AWS credentials, Terraform, or Ansible.
Run after every change:

```bash
PYTHONPATH=src pytest tests/ -v
```

`PYTHONPATH=src` is required because the operator package (`no8s-operator`) is not installed;
`tests/conftest.py` injects lightweight stubs for `plugins.reconcilers.base` into `sys.modules`
automatically, so importing the reconciler works without the operator on the path.

To run a single test file or test:

```bash
PYTHONPATH=src pytest tests/test_reconciler.py -v
PYTHONPATH=src pytest tests/test_reconciler.py::test_reconcile_handles_deletion -v
```

## Key Dependencies

- `ansible-core` (Python package — runs `ansible-playbook` as a subprocess)
- `PyYAML` (writes the `aws_ec2.yml` inventory plugin config)
- `httpx` (Patroni REST API health checks + GitHub API calls)
- `boto3` (AWS SDK — used by the `amazon.aws` Ansible collection at runtime)
- `jinja2` (Terraform template rendering, used inside the GHA workflow)
- Ansible collections: `amazon.aws` (EC2 inventory plugin), `ansible.posix` (sysctl, mount)
- Terraform CLI — runs inside GitHub Actions only, not on the reconciler host

## Reference Files (no8s-operator)

When implementing, refer to these files in the `no8s-operator` project:
- `src/plugins/reconcilers/base.py` — ReconcilerPlugin ABC, ReconcilerContext, ReconcileResult
- `src/plugins/base.py` — ActionPhase, ActionResult, DriftResult dataclasses
- `src/plugins/registry.py` — Plugin registration and discovery (entry point loading)
- `src/controller.py` — How the controller starts reconcilers and manages shutdown
- `tests/test_reconciler.py` — Minimal concrete ReconcilerPlugin example
- `docs/writing-a-reconciler.md` — Full guide with DNS example (deletion, finalizers, requeue)
