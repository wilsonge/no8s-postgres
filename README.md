# no8s-postgres

## Project Overview

This is a standalone **reconciler plugin** for the [no8s-operator](https://github.com/wilsonge/no8s-operator) that
provisions and manages highly-available PostgreSQL clusters on AWS EC2 using **Terraform via GitHub Actions** (infrastructure),
**Ansible via GitHub Actions** (configuration), and **Patroni** (HA management).

## WARNING
This project is being largely "vibe-coded" with minimal human review during the build out phase as I test the limits of
Claude Code. The intention will be after a first phase to do a full human review of the code. It is the intention to make
this fully production ready! But be warned if you're looking at it during these early development phases.

## Overview


## Development

```bash
# Install dev dependencies
pip install ".[dev]"

# Format code
black .

# Lint
flake8
```

## License

GPL-3.0-or-later
