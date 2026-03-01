# ---------------------------------------------------------------------------
# Data sources
# ---------------------------------------------------------------------------

data "aws_ami" "ubuntu" {
  most_recent = true
  owners      = ["099720109477"] # Canonical

  filter {
    name   = "name"
    values = ["ubuntu/images/hvm-ssd/ubuntu-jammy-22.04-amd64-server-*"]
  }

  filter {
    name   = "virtualization-type"
    values = ["hvm"]
  }
}

# ---------------------------------------------------------------------------
# VPC and networking
# ---------------------------------------------------------------------------

resource "aws_vpc" "main" {
  cidr_block           = var.vpc_cidr
  enable_dns_hostnames = true
  enable_dns_support   = true

  tags = merge(var.tags, {
    Name        = "${var.cluster_name}-vpc"
    ClusterName = var.cluster_name
  })
}

resource "aws_internet_gateway" "main" {
  vpc_id = aws_vpc.main.id

  tags = merge(var.tags, {
    Name        = "${var.cluster_name}-igw"
    ClusterName = var.cluster_name
  })
}

resource "aws_subnet" "main" {
  vpc_id                  = aws_vpc.main.id
  cidr_block              = cidrsubnet(var.vpc_cidr, 8, 0)
  map_public_ip_on_launch = true

  tags = merge(var.tags, {
    Name        = "${var.cluster_name}-subnet"
    ClusterName = var.cluster_name
  })
}

resource "aws_route_table" "main" {
  vpc_id = aws_vpc.main.id

  route {
    cidr_block = "0.0.0.0/0"
    gateway_id = aws_internet_gateway.main.id
  }

  tags = merge(var.tags, {
    Name        = "${var.cluster_name}-rt"
    ClusterName = var.cluster_name
  })
}

resource "aws_route_table_association" "main" {
  subnet_id      = aws_subnet.main.id
  route_table_id = aws_route_table.main.id
}

# ---------------------------------------------------------------------------
# Security groups
# ---------------------------------------------------------------------------

resource "aws_security_group" "nodes" {
  name        = "${var.cluster_name}-nodes"
  description = "PostgresCluster ${var.cluster_name} — Patroni HA nodes"
  vpc_id      = aws_vpc.main.id

  tags = merge(var.tags, {
    Name        = "${var.cluster_name}-nodes-sg"
    ClusterName = var.cluster_name
  })
}

# Full intra-cluster communication (etcd 2379/2380, Patroni 8008, PostgreSQL 5432)
resource "aws_security_group_rule" "intra_cluster" {
  type                     = "ingress"
  from_port                = 0
  to_port                  = 0
  protocol                 = "-1"
  self                     = true
  security_group_id        = aws_security_group.nodes.id
  description              = "Allow all intra-cluster traffic"
}

# SSH — required for Ansible configuration management
resource "aws_security_group_rule" "ssh" {
  type              = "ingress"
  from_port         = 22
  to_port           = 22
  protocol          = "tcp"
  cidr_blocks       = ["0.0.0.0/0"]
  security_group_id = aws_security_group.nodes.id
  description       = "SSH access for Ansible"
}

# PostgreSQL from allowed CIDRs
resource "aws_security_group_rule" "postgres" {
  count             = length(var.allowed_cidrs) > 0 ? 1 : 0
  type              = "ingress"
  from_port         = 5432
  to_port           = 5432
  protocol          = "tcp"
  cidr_blocks       = var.allowed_cidrs
  security_group_id = aws_security_group.nodes.id
  description       = "PostgreSQL from allowed CIDRs"
}

# PgBouncer from allowed CIDRs
resource "aws_security_group_rule" "pgbouncer" {
  count             = length(var.allowed_cidrs) > 0 ? 1 : 0
  type              = "ingress"
  from_port         = 6432
  to_port           = 6432
  protocol          = "tcp"
  cidr_blocks       = var.allowed_cidrs
  security_group_id = aws_security_group.nodes.id
  description       = "PgBouncer from allowed CIDRs"
}

# Patroni REST API from allowed CIDRs (health checks, switchover)
resource "aws_security_group_rule" "patroni_api" {
  count             = length(var.allowed_cidrs) > 0 ? 1 : 0
  type              = "ingress"
  from_port         = 8008
  to_port           = 8008
  protocol          = "tcp"
  cidr_blocks       = var.allowed_cidrs
  security_group_id = aws_security_group.nodes.id
  description       = "Patroni REST API from allowed CIDRs"
}

resource "aws_security_group_rule" "egress" {
  type              = "egress"
  from_port         = 0
  to_port           = 0
  protocol          = "-1"
  cidr_blocks       = ["0.0.0.0/0"]
  security_group_id = aws_security_group.nodes.id
  description       = "Allow all outbound traffic"
}

# ---------------------------------------------------------------------------
# EC2 instances
# ---------------------------------------------------------------------------

resource "aws_instance" "node" {
  count = var.cluster_size

  ami                    = data.aws_ami.ubuntu.id
  instance_type          = var.instance_type
  key_name               = var.ssh_key_name != "" ? var.ssh_key_name : null
  subnet_id              = aws_subnet.main.id
  vpc_security_group_ids = [aws_security_group.nodes.id]

  root_block_device {
    volume_type = "gp3"
    volume_size = 20
    encrypted   = true
  }

  tags = merge(var.tags, {
    Name            = "${var.cluster_name}-node-${count.index}"
    ClusterName     = var.cluster_name
    NodeIndex       = tostring(count.index)
    PostgresVersion = var.postgres_version
  })
}

# ---------------------------------------------------------------------------
# EBS data volumes (one per node, separate from root)
# ---------------------------------------------------------------------------

resource "aws_ebs_volume" "data" {
  count = var.cluster_size

  availability_zone = aws_instance.node[count.index].availability_zone
  size              = var.volume_size_gb
  type              = var.volume_type
  encrypted         = true

  tags = merge(var.tags, {
    Name        = "${var.cluster_name}-data-${count.index}"
    ClusterName = var.cluster_name
    NodeIndex   = tostring(count.index)
  })
}

resource "aws_volume_attachment" "data" {
  count = var.cluster_size

  device_name                    = "/dev/xvdf"
  volume_id                      = aws_ebs_volume.data[count.index].id
  instance_id                    = aws_instance.node[count.index].id
  stop_instance_before_detaching = true
}
