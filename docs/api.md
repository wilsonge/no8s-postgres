# PostgresCluster API Reference

This document describes the API calls required to provision and manage a PostgreSQL cluster
using the no8s-postgres reconciler plugin installed alongside the no8s-operator.

All requests are made to the operator's HTTP API (default: `http://localhost:8000`).
Names must be lowercase alphanumeric with hyphens, max 63 characters (Kubernetes naming rules).

---

## Prerequisites

Before creating any `PostgresCluster` resources, the resource type must be registered with the
operator. This is a one-time setup step (equivalent to applying a CRD in Kubernetes).

### Register the resource type

```bash
curl -X POST http://localhost:8000/api/v1/resource-types \
  -H "Content-Type: application/json" \
  -d '{
    "name": "PostgresCluster",
    "version": "v1",
    "description": "HA PostgreSQL cluster on EC2 with Patroni",
    "schema": {
      "type": "object",
      "required": ["postgres_version", "instance_type", "cluster_size"],
      "properties": {
        "postgres_version": {
          "type": "string",
          "enum": ["14", "15", "16", "17"]
        },
        "instance_type": {
          "type": "string",
          "default": "t3.medium"
        },
        "cluster_size": {
          "type": "integer",
          "minimum": 1,
          "maximum": 7,
          "default": 3
        },
        "volume_size_gb": {
          "type": "integer",
          "minimum": 20,
          "maximum": 16000,
          "default": 100
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
          "default": "10.0.0.0/16"
        },
        "db_name": {"type": "string"},
        "db_user": {"type": "string"},
        "allowed_cidrs": {
          "type": "array",
          "items": {"type": "string"},
          "default": []
        },
        "backup_enabled": {"type": "boolean", "default": true},
        "backup_retention_days": {"type": "integer", "default": 7, "minimum": 1},
        "pgbouncer_enabled": {"type": "boolean", "default": true},
        "pgbouncer_pool_size": {"type": "integer", "default": 20, "minimum": 1},
        "ssh_key_name": {"type": "string"},
        "tags": {
          "type": "object",
          "additionalProperties": {"type": "string"},
          "default": {}
        }
      }
    }
  }'
```

**Response** `201 Created`:

```json
{
  "id": 1,
  "name": "PostgresCluster",
  "version": "v1",
  "description": "HA PostgreSQL cluster on EC2 with Patroni",
  "status": "active",
  "schema": { "..." : "..." },
  "metadata": {},
  "created_at": "2026-01-01T00:00:00+00:00",
  "updated_at": "2026-01-01T00:00:00+00:00"
}
```

**Error** `409 Conflict` — resource type already registered (safe to ignore on repeat runs).

---

## Create a PostgresCluster

```
POST /api/v1/resources
```

Creates a new cluster. The operator validates the spec against the registered schema, attaches
the `no8s-postgres` finalizer, and immediately queues the resource for reconciliation.

### Required fields

| Field                   | Type    | Description                                                          |
|-------------------------|---------|----------------------------------------------------------------------|
| `name`                  | string  | Unique cluster name (lowercase alphanumeric + hyphens, max 63 chars) |
| `resource_type_name`    | string  | Must be `"PostgresCluster"`                                          |
| `resource_type_version` | string  | Must be `"v1"`                                                       |
| `spec.postgres_version` | string  | PostgreSQL major version: `"14"`, `"15"`, `"16"`, or `"17"`          |
| `spec.instance_type`    | string  | EC2 instance type (e.g. `"t3.medium"`, `"r6i.xlarge"`)               |
| `spec.cluster_size`     | integer | Number of nodes (1–7; odd numbers recommended for quorum)            |

### Optional spec fields

| Field                   | Type             | Default         | Description                                       |
|-------------------------|------------------|-----------------|---------------------------------------------------|
| `volume_size_gb`        | integer          | `100`           | EBS volume size per node in GB (20–16000)         |
| `volume_type`           | string           | `"gp3"`         | EBS volume type: `"gp3"`, `"io1"`, or `"io2"`     |
| `region`                | string           | `"eu-west-1"`   | AWS region                                        |
| `vpc_cidr`              | string           | `"10.0.0.0/16"` | CIDR for the dedicated VPC                        |
| `db_name`               | string           | —               | Application database to create after cluster init |
| `db_user`               | string           | —               | Application database user to create               |
| `allowed_cidrs`         | array of strings | `[]`            | CIDRs allowed to reach PostgreSQL on port 5432    |
| `backup_enabled`        | boolean          | `true`          | Enable pgbackrest S3-backed backups               |
| `backup_retention_days` | integer          | `7`             | Backup retention period in days                   |
| `pgbouncer_enabled`     | boolean          | `true`          | Deploy PgBouncer connection pooler                |
| `pgbouncer_pool_size`   | integer          | `20`            | PgBouncer pool size per database                  |
| `ssh_key_name`          | string           | —               | AWS EC2 key pair name for SSH access              |
| `tags`                  | object           | `{}`            | Extra AWS tags applied to all EC2 resources       |

### Minimal example

```bash
curl -X POST http://localhost:8000/api/v1/resources \
  -H "Content-Type: application/json" \
  -d '{
    "name": "prod-postgres",
    "resource_type_name": "PostgresCluster",
    "resource_type_version": "v1",
    "spec": {
      "postgres_version": "16",
      "instance_type": "r6i.xlarge",
      "cluster_size": 3
    }
  }'
```

### Full production example

```bash
curl -X POST http://localhost:8000/api/v1/resources \
  -H "Content-Type: application/json" \
  -d '{
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
      "vpc_cidr": "10.10.0.0/16",
      "db_name": "myapp",
      "db_user": "myapp_user",
      "ssh_key_name": "my-key",
      "allowed_cidrs": ["10.0.0.0/8"],
      "backup_enabled": true,
      "backup_retention_days": 14,
      "pgbouncer_enabled": true,
      "pgbouncer_pool_size": 50,
      "tags": {
        "Environment": "production",
        "Team": "platform"
      }
    }
  }'
```

### Response `201 Created`

```json
{
  "id": 42,
  "name": "prod-postgres",
  "resource_type_name": "PostgresCluster",
  "resource_type_version": "v1",
  "status": "pending",
  "status_message": null,
  "generation": 1,
  "observed_generation": 0,
  "finalizers": ["postgres_cluster"],
  "conditions": [],
  "created_at": "2026-01-01T12:00:00+00:00",
  "updated_at": "2026-01-01T12:00:00+00:00",
  "last_reconcile_time": null
}
```

### Error responses

| Status   | Cause                                                                                                       |
|----------|-------------------------------------------------------------------------------------------------------------|
| `400`    | Missing required spec fields, spec validation failure, or `PostgresCluster/v1` resource type not registered |
| `409`    | A resource named `prod-postgres` already exists                                                             |
| `500`    | Internal operator error                                                                                     |

---

## Get a cluster

### By name

```bash
curl http://localhost:8000/api/v1/resources/by-name/PostgresCluster/v1/prod-postgres
```

### Response

```json
{
  "id": 42,
  "name": "prod-postgres",
  "resource_type_name": "PostgresCluster",
  "resource_type_version": "v1",
  "status": "ready",
  "status_message": "Cluster reconciled successfully",
  "generation": 1,
  "observed_generation": 1,
  "finalizers": ["postgres_cluster"],
  "conditions": [
    {
      "type": "Ready",
      "status": "True",
      "reason": "ReconcileSuccess",
      "message": "Resource reconciled successfully",
      "lastTransitionTime": "2026-01-01T12:30:00+00:00",
      "observedGeneration": 1
    },
    {
      "type": "Reconciling",
      "status": "False",
      "reason": "ReconcileComplete",
      "message": "Reconciliation completed",
      "lastTransitionTime": "2026-01-01T12:30:00+00:00",
      "observedGeneration": 1
    },
    {
      "type": "Degraded",
      "status": "False",
      "reason": "NoErrors",
      "message": "",
      "lastTransitionTime": "2026-01-01T12:30:00+00:00",
      "observedGeneration": 1
    },
    {
      "type": "InfrastructureProvisioned",
      "status": "True",
      "reason": "TerraformApplied",
      "message": "Terraform apply completed",
      "lastTransitionTime": "2026-01-01T12:15:00+00:00",
      "observedGeneration": 1
    },
    {
      "type": "AnsibleConfigured",
      "status": "True",
      "reason": "AnsibleApplied",
      "message": "Ansible playbook completed",
      "lastTransitionTime": "2026-01-01T12:25:00+00:00",
      "observedGeneration": 1
    },
    {
      "type": "ClusterInitialized",
      "status": "True",
      "reason": "InitComplete",
      "message": "Cluster quorum established and database created",
      "lastTransitionTime": "2026-01-01T12:30:00+00:00",
      "observedGeneration": 1
    },
    {
      "type": "ClusterHealthy",
      "status": "True",
      "reason": "HealthCheckPassed",
      "message": "All Patroni nodes healthy",
      "lastTransitionTime": "2026-01-01T13:00:00+00:00",
      "observedGeneration": 1
    }
  ],
  "created_at": "2026-01-01T12:00:00+00:00",
  "updated_at": "2026-01-01T12:30:00+00:00",
  "last_reconcile_time": "2026-01-01T12:30:00+00:00"
}
```

### Status values

| `status`      | Meaning                                                              |
|---------------|----------------------------------------------------------------------|
| `pending`     | Newly created; queued for first reconciliation                       |
| `reconciling` | Reconciliation in progress (Terraform/Ansible/init running)          |
| `ready`       | Cluster provisioned and healthy                                      |
| `failed`      | Last reconciliation failed; will be retried with exponential backoff |
| `deleting`    | Deletion in progress (Terraform destroy running)                     |

### Domain-specific conditions

Four conditions are set by the postgres reconciler in addition to the three standard operator
conditions (`Ready`, `Reconciling`, `Degraded`):

| Condition                   | `True` reason       | `False` reason           | Set during                          |
|-----------------------------|---------------------|--------------------------|-------------------------------------|
| `InfrastructureProvisioned` | `TerraformApplied`  | `TerraformFailed`        | Terraform apply                     |
| `AnsibleConfigured`         | `AnsibleApplied`    | `AnsibleFailed`          | Ansible playbook                    |
| `ClusterInitialized`        | `InitComplete`      | *(exception propagates)* | Cluster init                        |
| `ClusterHealthy`            | `HealthCheckPassed` | `HealthDriftDetected`    | Drift detection (steady-state only) |

`ClusterHealthy` is only evaluated when the cluster is `ready` and `generation == observed_generation`.

---

## Get cluster outputs

After a successful reconciliation, connection endpoints and resource identifiers are available:

```bash
curl http://localhost:8000/api/v1/resources/42/outputs
```

**Response**:

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

Returns `404` if the cluster has not yet completed its first successful reconciliation.

---

## List clusters

```bash
curl http://localhost:8000/api/v1/resources?status=ready
```

Optional query parameters:

| Parameter   | Description                                                                |
|-------------|----------------------------------------------------------------------------|
| `status`    | Filter by status (`pending`, `reconciling`, `ready`, `failed`, `deleting`) |
| `limit`     | Maximum results to return (default: `100`)                                 |

---

## Update a cluster

Updating the spec increments `generation`, which triggers a new reconciliation. Any changed
infrastructure (instance type, cluster size, volume, etc.) will be applied via Terraform and
Ansible.

```bash
curl -X PUT http://localhost:8000/api/v1/resources/42 \
  -H "Content-Type: application/json" \
  -d '{
    "spec": {
      "postgres_version": "16",
      "instance_type": "r6i.2xlarge",
      "cluster_size": 3,
      "volume_size_gb": 1000,
      "volume_type": "gp3",
      "region": "eu-west-1",
      "db_name": "myapp",
      "db_user": "myapp_user",
      "ssh_key_name": "my-key",
      "allowed_cidrs": ["10.0.0.0/8"],
      "backup_enabled": true,
      "pgbouncer_enabled": true
    }
  }'
```

The full spec must be supplied on each update — there is no merge/patch.

**Response** `200 OK` — updated resource object with `generation` incremented by 1.

---

## Delete a cluster

```bash
curl -X DELETE http://localhost:8000/api/v1/resources/42
```

**Response** `202 Accepted`

The cluster is marked `deleting`. The reconciler triggers a Terraform destroy via GitHub
Actions, waits for it to complete, removes the `postgres_cluster` finalizer, and then
permanently deletes the resource record. The cluster name becomes available for reuse only
after deletion completes.

---

## Get reconciliation history

```bash
curl http://localhost:8000/api/v1/resources/42/history?limit=10
```

Returns the last `limit` reconciliation attempts (most recent first):

```json
[
  {
    "id": 101,
    "resource_id": 42,
    "generation": 1,
    "success": true,
    "phase": "ready",
    "error_message": null,
    "resources_created": 3,
    "resources_updated": 0,
    "resources_deleted": 0,
    "reconcile_time": "2026-01-01T12:30:00+00:00"
  }
]
```

---

## Reconciliation lifecycle

Once created, a cluster moves through the following stages automatically:

```
pending
  └─► reconciling
        ├─ Stage 1: Terraform apply (GitHub Actions: terraform.yml action=apply)
        │    └─ InfrastructureProvisioned condition set
        ├─ Stage 2: Ansible playbook (GitHub Actions: ansible.yml)
        │    └─ AnsibleConfigured condition set
        ├─ Stage 3: Cluster init (quorum, database, replication verification)
        │    └─ ClusterInitialized condition set
        └─► ready
              └─ Periodic drift detection every RECONCILE_POLL_INTERVAL seconds
                   ├─ Terraform plan: re-queues apply if drift detected
                   └─ Patroni health check: sets ClusterHealthy condition
```

On failure the resource moves to `failed` and is retried with exponential backoff. Fix the
underlying cause (see condition `message` for details) and then trigger a new reconcile by
making any spec change to increment `generation`.