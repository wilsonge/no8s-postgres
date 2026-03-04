#!/usr/bin/env python3
"""Custom Ansible fact: reports Patroni role and cluster initialisation state.

Deployed to /etc/ansible/facts.d/patroni.fact on each cluster node.
Ansible executes it during fact gathering and exposes the output as
ansible_local.patroni.* for use in subsequent tasks and plays.

Facts returned
--------------
patroni_running       bool  Patroni REST API reachable on localhost:8008
cluster_initialized   bool  Cluster has completed bootstrap (state=running,
                            timeline >= 1)
is_leader             bool  This node is the current Patroni leader
patroni_state         str   Raw Patroni state ("running", "starting", …)
                            or "unknown"
patroni_role          str   "master" / "primary" / "replica" /
                            "standby_leader" / … or "unknown"
patroni_scope         str   Patroni cluster scope name, or ""
"""

import json
import urllib.error
import urllib.request

PATRONI_API = "http://localhost:8008/"
TIMEOUT = 5  # seconds

# Roles that mean this node holds the write lock.
# "master" is used by Patroni < 3.0, "primary" by >= 3.0.
# "standby_leader" is the leader in a cascading standby cluster.
_LEADER_ROLES = {"master", "primary", "standby_leader"}


def main() -> None:
    facts: dict = {
        "patroni_running": False,
        "cluster_initialized": False,
        "is_leader": False,
        "patroni_state": "unknown",
        "patroni_role": "unknown",
        "patroni_scope": "",
    }

    try:
        with urllib.request.urlopen(PATRONI_API, timeout=TIMEOUT) as resp:
            data = json.loads(resp.read().decode())
    except (urllib.error.URLError, OSError):
        # Patroni not running or not yet listening — return safe defaults.
        print(json.dumps(facts))
        return
    except (json.JSONDecodeError, ValueError):
        print(json.dumps(facts))
        return

    facts["patroni_running"] = True
    facts["patroni_state"] = data.get("state", "unknown")
    facts["patroni_role"] = data.get("role", "unknown")
    facts["patroni_scope"] = data.get("patroni", {}).get("scope", "")

    # Cluster is initialised when Patroni reports state=running AND a valid
    # timeline (>= 1).  A missing or zero timeline means PostgreSQL has not
    # yet been bootstrapped by the first leader.
    timeline = data.get("timeline", 0)
    facts["cluster_initialized"] = (
        facts["patroni_state"] == "running"
        and isinstance(timeline, int)
        and timeline >= 1
    )

    facts["is_leader"] = facts["patroni_role"] in _LEADER_ROLES

    print(json.dumps(facts))


if __name__ == "__main__":
    main()
