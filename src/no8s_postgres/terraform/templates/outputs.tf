output "instance_public_ips" {
  description = "Public IP addresses of all nodes (ordered by node index)"
  value       = aws_instance.node[*].public_ip
}

output "instance_private_ips" {
  description = "Private IP addresses of all nodes (ordered by node index)"
  value       = aws_instance.node[*].private_ip
}

output "leader_endpoint" {
  description = "PostgreSQL endpoint on the initial primary node (node 0)"
  value       = "${aws_instance.node[0].public_ip}:5432"
}

output "replica_endpoints" {
  description = "PostgreSQL endpoints on replica nodes (nodes 1..N)"
  value = [
    for i, inst in aws_instance.node : "${inst.public_ip}:5432"
    if i > 0
  ]
}

output "pgbouncer_endpoint" {
  description = "PgBouncer endpoint on the initial primary node"
  value       = "${aws_instance.node[0].public_ip}:6432"
}

output "patroni_endpoints" {
  description = "Patroni REST API endpoints for all nodes"
  value       = [for inst in aws_instance.node : "${inst.public_ip}:8008"]
}

output "vpc_id" {
  description = "VPC ID"
  value       = aws_vpc.main.id
}

output "security_group_id" {
  description = "Security group ID for the PostgreSQL nodes"
  value       = aws_security_group.nodes.id
}

output "subnet_id" {
  description = "Subnet ID"
  value       = aws_subnet.main.id
}

output "cluster_name" {
  description = "Cluster name (passthrough)"
  value       = var.cluster_name
}
