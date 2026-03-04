# no8s-postgres

## Project Overview

This is a standalone **reconciler plugin** for the [no8s-operator](https://github.com/wilsonge/no8s-operator) that
provisions and manages highly-available PostgreSQL clusters on AWS EC2 using **Terraform via GitHub Actions** (infrastructure),
**Ansible via GitHub Actions** (configuration), and **Patroni** (HA management).

Terraform plan/apply/destroy are dispatched as GitHub Actions `workflow_dispatch` runs (`.github/workflows/terraform.yml`).
Ansible configuration is dispatched as a separate GitHub Actions `workflow_dispatch` run (`.github/workflows/ansible.yml`).
Both are triggered via the operator's built-in `github_actions` action plugin, which polls for completion. After a Terraform
apply, the reconciler downloads the `terraform-outputs` artifact to obtain EC2 IPs and other outputs. The Ansible workflow
generates an EC2 dynamic inventory inline (using the `amazon.aws.aws_ec2` plugin, filtering by the `ClusterName` EC2 tag
set by Terraform) and runs the playbook entirely within GitHub Actions — no Ansible subprocess on the reconciler host.

The plugin implements the `ReconcilerPlugin` interface from no8s-operator (`src/plugins/reconcilers/base.py`) and is 
registered via Python entry points under the `no8s.reconcilers` group. It owns the full reconciliation loop for 
`PostgresCluster` resources.

## Architecture

```
┌─────────────────────────────────────────────────────────┐
│                   no8s-operator                         │
│              (controller startup)                       │
│                                                         │
│  discovers reconcilers via entry points                 │
│  calls reconciler.start(ctx) in dedicated asyncio task  │
└──────────────────────────┬──────────────────────────────┘
                           │
                           ▼
┌─────────────────────────────────────────────────────────┐
│          PostgresClusterReconciler                      │
│              (ReconcilerPlugin impl)                    │
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
│       a. GHA terraform workflow (apply — EC2 + network) │
│       b. GHA ansible workflow (inventory + all roles)   │
│       c. Cluster init (quorum, DB, roles)               │
│    4. Update resource status                            │
└─────────────────────────────────────────────────────────┘
```

## Reconciler Contract

This plugin implements the `ReconcilerPlugin` abstract base class from no8s-operator. The operator discovers and starts
it at boot:

1. **`name`** (property) → `"postgres_cluster"`
2. **`resource_types`** (property) → `["PostgresCluster"]`
3. **`start(ctx: ReconcilerContext)`** → Run the reconciliation loop until `ctx.shutdown_event` is set
4. **`reconcile(resource, ctx: ReconcilerContext)`** → Reconcile a single `PostgresCluster` resource; returns `ReconcileResult`
5. **`stop()`** → Graceful shutdown, clean up connections

Key data types from no8s-operator (`src/plugins/reconcilers/base.py`):
- `ReconcilerContext` — db, registry, shutdown_event; methods: `get_resources_needing_reconciliation`, `update_status`, `record_reconciliation`, `remove_finalizer`, `hard_delete_resource`
- `ReconcileResult` — success, message, requeue_after

Resource status lifecycle managed by the reconciler: `pending` → `reconciling` → `ready` / `failed` / `deleting`

## EC2 Inventory Plugin

The `ansible.yml` GHA workflow generates an `aws_ec2.yml` inventory file inline (Python script in a `run:` step) using the cluster name and spec passed as workflow inputs. The `SSH_PRIVATE_KEY` repository secret is written to `~/.ssh/no8s_postgres` on the runner.

The generated config:

```yaml
plugin: amazon.aws.aws_ec2
regions: [<region from spec>]
filters:
  tag:ClusterName: <cluster-name>   # matches Terraform's ClusterName tag
  instance-state-name: running
hostnames: [public_ip_address]
compose:
  ansible_host: public_ip_address
  ansible_user: '"ubuntu"'
  ansible_ssh_private_key_file: '"~/.ssh/no8s_postgres"'
  ansible_ssh_common_args: '"-o StrictHostKeyChecking=no -o ConnectTimeout=<ansible_timeout>"'
  node_index: "tags['NodeIndex'] | int"
  cluster_name: "tags['ClusterName']"
groups:
  patroni_primary:  "tags.get('NodeIndex', '999') == '0'"
  patroni_replicas: "tags.get('NodeIndex', '999') != '0'"
  postgres_nodes:   "'ClusterName' in tags"
```

Requires the `amazon.aws` Ansible collection (installed by the GHA workflow):
```bash
ansible-galaxy collection install -r ansible/playbooks/requirements.yml
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

| Variable                  | Description                                                   | Default           |
|---------------------------|---------------------------------------------------------------|-------------------|
| `AWS_REGION`              | AWS region                                                    | `eu-west-1`       |
| `TF_STATE_BUCKET`         | S3 bucket for Terraform state                                 | required          |
| `TF_STATE_DYNAMODB_TABLE` | DynamoDB table for state locking                              | `terraform-locks` |
| `TF_STATE_KEY_PREFIX`     | Key prefix in the S3 bucket                                   | `no8s-postgres/`  |
| `CLUSTER_INIT_TIMEOUT`    | Timeout waiting for cluster quorum (seconds)                  | `300`             |
| `RECONCILE_POLL_INTERVAL` | Seconds between reconciliation polls                          | `30`              |
| `GITHUB_TOKEN`            | GitHub personal access token (artifact download)              | required          |
| `GITHUB_REPO`             | GitHub repository containing the GHA workflows (`owner/repo`) | required          |
| `GITHUB_REF`              | Branch/ref to run the workflow on                             | `main`            |
| `GITHUB_WORKFLOW`         | Terraform workflow filename in `.github/workflows/`           | `terraform.yml`   |

The GitHub Actions workflows read repository secrets for AWS OIDC (`AWS_ROLE_ARN`, `AWS_REGION`, `TF_STATE_BUCKET`, `TF_STATE_DYNAMODB_TABLE`) and for Ansible SSH access (`SSH_PRIVATE_KEY`) — set these in the repository's Actions secrets.

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
   - No drift → sets `ClusterHealthy=True` / `HealthCheckPassed`
   - Drift detected → sets `ClusterHealthy=False` / `HealthDriftDetected`, triggers apply

If no drift, requeue after poll interval.

### Stage 3: Apply (if drift or first provision)
1. **Terraform (via GitHub Actions)** — `_run_terraform("apply", ...)` via GHA plugin; download the `terraform-outputs` artifact (JSON) via `download_artifact_content()`
   - Success → sets `InfrastructureProvisioned=True` / `TerraformApplied`
   - Failure → sets `InfrastructureProvisioned=False` / `TerraformFailed`, raises
2. **Ansible (via GitHub Actions)** — `_run_ansible(...)` dispatches the `ansible.yml` workflow via the GHA plugin, passing `cluster_name`, `resource_id`, and `spec_json` as inputs. The workflow runner:
   - Generates `aws_ec2.yml` (EC2 dynamic inventory targeting instances by `ClusterName` tag) inline in Python
   - Runs `ansible-playbook ansible/playbooks/site.yml -i aws_ec2.yml --extra-vars <spec>`:
     - `common` — OS packages, sysctl tuning, chrony
     - `etcd` — Install and cluster etcd (Patroni DCS backend); idempotent via `ETCD_INITIAL_CLUSTER_STATE`
     - `postgresql` — pgdg repo, install PostgreSQL, stop default service, format + mount EBS volume
     - `patroni` — Install via pip, template `patroni.yml` (etcd hosts from inventory group), systemd unit
     - `pgbouncer` — Install and configure PgBouncer (when `pgbouncer_enabled=true`)
     - `pgbackrest` — Configure S3-backed backups (when `backup_enabled=true`)
   - Success → sets `AnsibleConfigured=True` / `AnsibleApplied`
   - Failure → sets `AnsibleConfigured=False` / `AnsibleFailed`, raises
3. **Cluster init** — `ClusterInitialiser.wait_for_quorum()`, `create_database()`, `verify_replication()` (stubs — `NotImplementedError`)
   - Success → sets `ClusterInitialized=True` / `InitComplete`

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

## Status Conditions

The reconciler sets Kubernetes-style conditions on each `PostgresCluster` resource via `ctx.set_condition()`. Three standard conditions (`Ready`, `Reconciling`, `Degraded`) are managed automatically by the operator controller. The reconciler adds four domain-specific conditions for fine-grained observability.

### Condition reference

| Condition                   | Set in stage               | `True` reason       | `False` reason                          |
|-----------------------------|----------------------------|---------------------|-----------------------------------------|
| `InfrastructureProvisioned` | Stage 3 — Terraform apply  | `TerraformApplied`  | `TerraformFailed`                       |
| `AnsibleConfigured`         | Stage 3 — Ansible workflow | `AnsibleApplied`    | `AnsibleFailed`                         |
| `ClusterInitialized`        | Stage 3 — cluster init     | `InitComplete`      | *(exception propagates; not set False)* |
| `ClusterHealthy`            | Stage 2 — drift detection  | `HealthCheckPassed` | `HealthDriftDetected`                   |

`ClusterHealthy` is only set when the resource is `ready` and `generation == observed_generation` (i.e., during a steady-state drift check, not during initial provisioning).

### Condition values

Each condition follows the Kubernetes convention:

| Field                | Values                                                   |
|----------------------|----------------------------------------------------------|
| `status`             | `"True"`, `"False"`, `"Unknown"`                         |
| `reason`             | Short CamelCase string (see table above)                 |
| `message`            | Human-readable detail; contains the raw error on failure |
| `lastTransitionTime` | ISO-8601 timestamp; only updates when `status` changes   |
| `observedGeneration` | Resource generation when the condition was last set      |

### Investigating failure conditions

**`InfrastructureProvisioned=False` (`TerraformFailed`)**

The Terraform apply GitHub Actions workflow failed. The `message` field contains the workflow error.

1. Open the GitHub Actions run for `terraform.yml` in the repository — the run ID is logged by the operator's `github_actions` plugin.
2. Check the `terraform apply` step output for the specific AWS API error (e.g. quota exceeded, VPC limit, IAM permission denied).
3. Common causes: IAM role missing `ec2:RunInstances` or `ec2:CreateVpc`; S3/DynamoDB state backend unreachable; Terraform state locked by a previous failed run.
   - Unlock state: `terraform force-unlock <lock-id>` (lock ID is in the workflow logs).
4. After fixing the root cause, bump the resource `generation` (edit any spec field) to trigger a fresh reconcile.

**`AnsibleConfigured=False` (`AnsibleFailed`)**

The Ansible `ansible.yml` GitHub Actions workflow completed but the playbook returned a non-zero exit code. The `message` field contains the error.

1. Open the GitHub Actions run for `ansible.yml`. Find the failing task in the `ansible-playbook` step output — look for `FAILED` or `fatal:` lines.
2. The playbook runs these roles in order: `common` → `etcd` → `postgresql` → `patroni` → `pgbouncer` (if enabled) → `pgbackrest` (if enabled). The failing role narrows the scope.
3. Common causes by role:
   - **`common`** — OS package installation failed (apt lock, missing mirror). Re-run is safe; role is idempotent.
   - **`etcd`** — etcd cluster failed to form quorum; check `ETCD_INITIAL_CLUSTER` env vars in the `etcd.env.j2` template output and EC2 security group rules (etcd ports 2379/2380).
   - **`postgresql`** — pgdg repo GPG error or EBS volume not attached yet (Terraform outputs race). Retry usually resolves.
   - **`patroni`** — `patroni.yml` config rendered incorrectly; check the template `ansible/playbooks/roles/patroni/templates/patroni.yml.j2` against the spec JSON passed as `extra_vars`.
   - **`pgbouncer`** / **`pgbackrest`** — misconfigured spec (`pgbouncer_pool_size`, `backup_retention_days`); fix spec and re-reconcile.
4. SSH into the affected node (IP from Terraform outputs or EC2 console, key from `SSH_PRIVATE_KEY` secret) and check `journalctl -u patroni` / `journalctl -u etcd`.

**`ClusterHealthy=False` (`HealthDriftDetected`)**

The Patroni health check detected drift during a steady-state poll. The `message` field contains `drift_details`.

> **Note:** `HealthChecker` is currently a stub that always returns `healthy=True`. This condition will only fire once a real implementation is in place.

When implemented, the expected investigation steps are:
1. Check `patroni_endpoints` in the resource outputs and query each node directly:
   ```bash
   curl http://<node-ip>:8008/health      # leader: 200, replica: 503
   curl http://<node-ip>:8008/cluster     # full cluster state
   ```
2. A replica showing `running` but lagging: check `pg_stat_replication` on the leader.
3. A node showing `start failed` or `stopped`: SSH in and check `journalctl -u patroni -n 100`.
4. Split-brain (two nodes claiming leader): check etcd cluster health — `etcdctl endpoint health --cluster`. If etcd has lost quorum, restore it before touching Patroni.

**`ClusterInitialized` not `True` (cluster init exception)**

The cluster init stage (`wait_for_quorum` / `create_database` / `verify_replication`) raised an exception. The resource `status` will be `failed` and the exception message appears in the operator logs and in `ctx.update_status`.

> **Note:** `ClusterInitialiser` methods are currently stubs raising `NotImplementedError`. This will only occur in production once they are implemented.

1. Check operator logs for `Reconciliation failed for resource <id>` and the full traceback.
2. If quorum timed out: Ansible completed but Patroni did not elect a leader within `CLUSTER_INIT_TIMEOUT` seconds. Check `journalctl -u patroni` and `journalctl -u etcd` on each node.
3. If `create_database` failed: connect to the leader endpoint and check `pg_hba.conf` and the `patroni.yml` superuser credentials.
4. The resource will be requeued automatically; fix the underlying issue and the next reconcile will retry.

## Development

```bash
# Install in dev mode
pip install -e ".[dev]"

# Format
black src/ tests/

# Lint
flake8 src/ tests/
```

Ansible and Terraform run entirely within GitHub Actions. To test the Ansible playbook locally:

```bash
pip install ansible-core boto3
ansible-galaxy collection install -r ansible/playbooks/requirements.yml
# generate aws_ec2.yml manually, then:
ansible-playbook ansible/playbooks/site.yml -i aws_ec2.yml --extra-vars '{"cluster_name":"test",...}'
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

## Reference Files (no8s-operator)

When implementing, refer to these files in the `no8s-operator` project:
- `src/plugins/reconcilers/base.py` — ReconcilerPlugin ABC, ReconcilerContext, ReconcileResult
- `src/plugins/base.py` — ActionPhase, ActionResult, DriftResult dataclasses
- `src/plugins/registry.py` — Plugin registration and discovery (entry point loading)
- `src/controller.py` — How the controller starts reconcilers and manages shutdown
- `tests/test_reconciler.py` — Minimal concrete ReconcilerPlugin example
- `docs/writing-a-reconciler.md` — Full guide with DNS example (deletion, finalizers, requeue)

## Short term items outstanding

- [ ] Place a custom fact on the boxes to determine which is leader dynamically, node 0 should only be used if the cluster hasn't been initialised
- [ ] Cleanup how the ansible inventory is generated - it is duplicated in inventory.py and the ansible github workflow
- [ ] Identify any other redundant code from previous refactorings.

## Long term items outstanding

- [ ] Add end-to-end tests
- [ ] Handle updates of items in order
