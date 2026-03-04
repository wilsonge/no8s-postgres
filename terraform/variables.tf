variable "aws_region" {
  description = "AWS region"
  type        = string
  default     = "eu-west-1"
}

variable "cluster_name" {
  description = "Name of the PostgresCluster resource — used as a resource name prefix"
  type        = string
}

variable "cluster_size" {
  description = "Number of PostgreSQL nodes"
  type        = number
  default     = 3
}

variable "instance_type" {
  description = "EC2 instance type for each node"
  type        = string
  default     = "t3.medium"
}

variable "postgres_version" {
  description = "PostgreSQL major version (informational — installed by Ansible)"
  type        = string
  default     = "16"
}

variable "volume_size_gb" {
  description = "EBS data volume size per node in GB"
  type        = number
  default     = 100
}

variable "etcd_volume_size_gb" {
  description = "EBS etcd volume size per node in GB"
  type        = number
  default     = 10
}

variable "volume_type" {
  description = "EBS volume type (gp3, io1, io2)"
  type        = string
  default     = "gp3"
}

variable "vpc_cidr" {
  description = "CIDR block for the VPC"
  type        = string
  default     = "10.0.0.0/16"
}

variable "allowed_cidrs" {
  description = "CIDRs allowed to connect to PostgreSQL (5432) and PgBouncer (6432)"
  type        = list(string)
  default     = []
}

variable "ssh_key_name" {
  description = "AWS key pair name for EC2 SSH access (Ansible)"
  type        = string
  default     = ""
}

variable "tags" {
  description = "Additional tags applied to all resources"
  type        = map(string)
  default     = {}
}
