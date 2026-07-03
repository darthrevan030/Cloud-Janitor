"""AWS provider implementation for the Cloud Janitor MCP server.

Queries live AWS infrastructure via boto3. All three methods return dicts
whose schema exactly matches the fixture files so agents and tests need no
special-casing between backends.

LocalStack support: set AWS_ENDPOINT_URL=http://localhost:4566 and
TF_CMD=tflocal before running. The boto3 clients pick up the env var
automatically — no code changes needed.

Required IAM permissions are documented per-method below.
"""

from __future__ import annotations

import os
from typing import Optional

from mcp_server.backends import CloudProvider


def _make_client(service: str, region: Optional[str]):
    """Return a boto3 client, wiring in AWS_ENDPOINT_URL when present."""
    import boto3

    kwargs: dict = {"region_name": region}
    endpoint = os.environ.get("AWS_ENDPOINT_URL")
    if endpoint:
        kwargs["endpoint_url"] = endpoint
    return boto3.client(service, **kwargs)


class AWSProvider(CloudProvider):
    """CloudProvider backed by live AWS APIs (or LocalStack when AWS_ENDPOINT_URL is set).

    boto3 is imported lazily at instantiation time so the MCP server can
    start without the AWS SDK when a different backend is selected.
    """

    def __init__(self, region: Optional[str] = None):
        """Initialise the AWS provider.

        Args:
            region: AWS region. Defaults to boto3 resolution order
                (AWS_DEFAULT_REGION env var, then ~/.aws/config).

        Raises:
            ImportError: If boto3 is not installed.
        """
        try:
            import boto3  # noqa: F401
        except ImportError:
            raise ImportError(
                "boto3 is required for the AWS backend but is not installed. "
                "Install it with: pip install boto3>=1.34.0"
            )
        self._region = region

    # ------------------------------------------------------------------
    # get_cost_data
    # Required IAM: ec2:DescribeVolumes, ec2:DescribeInstances,
    #               elasticache:DescribeCacheClusters,
    #               cloudwatch:GetMetricStatistics
    # ------------------------------------------------------------------

    def get_cost_data(
        self,
        resource_type: Optional[str] = None,
        min_idle_days: int = 7,
    ) -> dict:
        """Return idle/orphaned resource data from live AWS.

        Queries EBS volumes, EC2 instances, and ElastiCache clusters.
        For each resource, CloudWatch metrics are used to determine
        idle_days (days since last meaningful activity).

        Args:
            resource_type: Filter by "ebs", "ec2", or "elasticache".
                None returns all types.
            min_idle_days: Only include resources idle for at least
                this many days.

        Returns:
            {"resources": [...], "total_monthly_waste": float}
        """
        import boto3
        from datetime import datetime, timedelta, timezone

        import botocore.exceptions

        resources: list[dict] = []

        def _cw_idle_days(namespace: str, metric: str, dimensions: list[dict]) -> int:
            """Return days since the metric last had non-zero datapoints."""
            cw = _make_client("cloudwatch", self._region)
            end = datetime.now(timezone.utc)
            start = end - timedelta(days=90)
            try:
                resp = cw.get_metric_statistics(
                    Namespace=namespace,
                    MetricName=metric,
                    Dimensions=dimensions,
                    StartTime=start,
                    EndTime=end,
                    Period=86400,
                    Statistics=["Maximum"],
                )
                points = sorted(
                    [p for p in resp["Datapoints"] if p["Maximum"] > 0],
                    key=lambda p: p["Timestamp"],
                    reverse=True,
                )
                if not points:
                    return 90  # no activity in 90-day window
                delta = end - points[0]["Timestamp"]
                return delta.days
            except botocore.exceptions.ClientError:
                return 0

        # ---- EBS volumes ------------------------------------------------
        if resource_type in (None, "ebs"):
            try:
                ec2 = _make_client("ec2", self._region)
                paginator = ec2.get_paginator("describe_volumes")
                for page in paginator.paginate(
                    Filters=[{"Name": "status", "Values": ["available"]}]
                ):
                    for vol in page["Volumes"]:
                        vid = vol["VolumeId"]
                        created = vol["CreateTime"]
                        age_days = (datetime.now(timezone.utc) - created).days
                        idle = age_days  # unattached → idle since detach

                        if idle < min_idle_days:
                            continue

                        # Rough cost: gp3 $0.08/GB-month, gp2 $0.10/GB-month
                        gb = vol.get("Size", 0)
                        vtype = vol.get("VolumeType", "gp3")
                        price_per_gb = 0.10 if vtype == "gp2" else 0.08
                        monthly_cost = round(gb * price_per_gb, 2)

                        name = next(
                            (t["Value"] for t in vol.get("Tags", []) if t["Key"] == "Name"),
                            vid,
                        )
                        resources.append(
                            {
                                "id": vid,
                                "type": "ebs",
                                "name": name,
                                "idle_days": idle,
                                "monthly_cost": monthly_cost,
                                "status": vol["State"],
                                "attached": vol["State"] == "in-use",
                                "volume_type": vtype,
                                "size_gb": gb,
                                "availability_zone": vol.get("AvailabilityZone", ""),
                                "encrypted": vol.get("Encrypted", False),
                                "created_at": created.isoformat(),
                                "description": (
                                    f"Unattached {vtype} volume, {gb} GB, "
                                    f"idle {idle} days"
                                ),
                            }
                        )
            except botocore.exceptions.ClientError as exc:
                resources.append(
                    {
                        "id": "ebs-error",
                        "type": "ebs",
                        "name": "error",
                        "idle_days": 0,
                        "monthly_cost": 0.0,
                        "status": "error",
                        "error": str(exc),
                    }
                )

        # ---- ElastiCache clusters ----------------------------------------
        if resource_type in (None, "elasticache"):
            try:
                ec = _make_client("elasticache", self._region)
                paginator = ec.get_paginator("describe_cache_clusters")
                for page in paginator.paginate(ShowCacheNodeInfo=True):
                    for cluster in page["CacheClusters"]:
                        cid = cluster["CacheClusterId"]
                        created = cluster.get("CacheClusterCreateTime")

                        idle = _cw_idle_days(
                            "AWS/ElastiCache",
                            "CurrConnections",
                            [{"Name": "CacheClusterId", "Value": cid}],
                        )
                        if idle < min_idle_days:
                            continue

                        node_type = cluster.get("CacheNodeType", "")
                        # Rough cost map (on-demand, us-east-1)
                        cost_map = {
                            "cache.t3.micro": 12.0,
                            "cache.t3.small": 24.0,
                            "cache.t3.medium": 46.0,
                            "cache.r6g.large": 122.0,
                        }
                        monthly_cost = cost_map.get(node_type, 30.0)

                        engine = cluster.get("Engine", "redis")
                        resources.append(
                            {
                                "id": cid,
                                "type": "elasticache",
                                "name": cid,
                                "idle_days": idle,
                                "monthly_cost": monthly_cost,
                                "status": cluster.get("CacheClusterStatus", ""),
                                "connections": 0,
                                "instance_type": node_type,
                                "engine": engine,
                                "engine_version": cluster.get("EngineVersion", ""),
                                "availability_zone": cluster.get(
                                    "PreferredAvailabilityZone", ""
                                ),
                                "cluster_mode": "disabled",
                                "num_cache_nodes": cluster.get("NumCacheNodes", 1),
                                "created_at": (
                                    created.isoformat() if created else ""
                                ),
                                "description": (
                                    f"{engine} cluster idle {idle} days"
                                ),
                            }
                        )
            except botocore.exceptions.ClientError as exc:
                resources.append(
                    {
                        "id": "elasticache-error",
                        "type": "elasticache",
                        "name": "error",
                        "idle_days": 0,
                        "monthly_cost": 0.0,
                        "status": "error",
                        "error": str(exc),
                    }
                )

        # ---- EC2 instances (stopped/idle) --------------------------------
        if resource_type in (None, "ec2"):
            try:
                ec2 = _make_client("ec2", self._region)
                paginator = ec2.get_paginator("describe_instances")
                for page in paginator.paginate(
                    Filters=[{"Name": "instance-state-name", "Values": ["stopped"]}]
                ):
                    for reservation in page["Reservations"]:
                        for inst in reservation["Instances"]:
                            iid = inst["InstanceId"]
                            launch = inst.get("LaunchTime")
                            idle = (
                                (datetime.now(timezone.utc) - launch).days
                                if launch
                                else 0
                            )
                            if idle < min_idle_days:
                                continue

                            itype = inst.get("InstanceType", "")
                            # Rough stopped-instance cost (EBS only, no compute)
                            monthly_cost = 5.0

                            name = next(
                                (
                                    t["Value"]
                                    for t in inst.get("Tags", [])
                                    if t["Key"] == "Name"
                                ),
                                iid,
                            )
                            resources.append(
                                {
                                    "id": iid,
                                    "type": "ec2",
                                    "name": name,
                                    "idle_days": idle,
                                    "monthly_cost": monthly_cost,
                                    "status": "stopped",
                                    "instance_type": itype,
                                    "availability_zone": inst.get(
                                        "Placement", {}
                                    ).get("AvailabilityZone", ""),
                                    "created_at": (
                                        launch.isoformat() if launch else ""
                                    ),
                                    "description": (
                                        f"Stopped {itype} instance, idle {idle} days"
                                    ),
                                }
                            )
            except botocore.exceptions.ClientError as exc:
                resources.append(
                    {
                        "id": "ec2-error",
                        "type": "ec2",
                        "name": "error",
                        "idle_days": 0,
                        "monthly_cost": 0.0,
                        "status": "error",
                        "error": str(exc),
                    }
                )

        total_waste = round(
            sum(r["monthly_cost"] for r in resources if "error" not in r), 2
        )
        return {"resources": resources, "total_monthly_waste": total_waste}

    # ------------------------------------------------------------------
    # get_security_data
    # Required IAM: ec2:DescribeSecurityGroups,
    #               elasticache:DescribeReplicationGroups,
    #               ec2:DescribeVolumes (encryption check)
    # ------------------------------------------------------------------

    def get_security_data(self, check_type: Optional[str] = None) -> dict:
        """Return security findings from live AWS.

        Checks:
        - security_group: inbound rules open to 0.0.0.0/0 on sensitive ports
        - encryption: unencrypted EBS volumes and ElastiCache clusters

        Args:
            check_type: "security_group", "encryption", or None for all.

        Returns:
            {"findings": [...], "critical_count": int}
        """
        import botocore.exceptions

        findings: list[dict] = []

        # Ports that must never be open to the internet
        SENSITIVE_PORTS = {
            22: ("SSH", "CRITICAL"),
            3306: ("MySQL", "CRITICAL"),
            5432: ("PostgreSQL", "CRITICAL"),
            6379: ("Redis", "CRITICAL"),
            27017: ("MongoDB", "CRITICAL"),
            3389: ("RDP", "CRITICAL"),
            8080: ("HTTP-alt", "HIGH"),
            8443: ("HTTPS-alt", "HIGH"),
        }

        # ---- Security groups ---------------------------------------------
        if check_type in (None, "security_group"):
            try:
                ec2 = _make_client("ec2", self._region)
                paginator = ec2.get_paginator("describe_security_groups")
                for page in paginator.paginate():
                    for sg in page["SecurityGroups"]:
                        sgid = sg["GroupId"]
                        sgname = sg.get("GroupName", sgid)
                        for perm in sg.get("IpPermissions", []):
                            from_port = perm.get("FromPort")
                            to_port = perm.get("ToPort")
                            cidrs = [
                                r["CidrIp"]
                                for r in perm.get("IpRanges", [])
                            ]
                            cidrs6 = [
                                r["CidrIpv6"]
                                for r in perm.get("Ipv6Ranges", [])
                            ]

                            open_to_world = "0.0.0.0/0" in cidrs or "::/0" in cidrs6
                            if not open_to_world:
                                continue

                            # Check each sensitive port in the rule's range
                            for port, (svc, severity) in SENSITIVE_PORTS.items():
                                in_range = (
                                    from_port is None  # -1 = all traffic
                                    or (
                                        from_port <= port <= (to_port or port)
                                    )
                                )
                                if not in_range:
                                    continue

                                cidr = "0.0.0.0/0" if "0.0.0.0/0" in cidrs else "::/0"
                                findings.append(
                                    {
                                        "id": f"finding-sg-{sgid}-{port}",
                                        "resource_id": sgid,
                                        "resource_type": "aws_security_group",
                                        "check_type": "security_group",
                                        "severity": severity,
                                        "port": port,
                                        "cidr": cidr,
                                        "current_state": "open_to_world",
                                        "required_state": "vpc_only",
                                        "title": f"{svc} port open to internet",
                                        "description": (
                                            f"Security group {sgname} ({sgid}) allows "
                                            f"inbound {cidr} on port {port} ({svc}). "
                                            "Must be restricted to VPC CIDR only."
                                        ),
                                    }
                                )
            except botocore.exceptions.ClientError as exc:
                findings.append(
                    {
                        "id": "sg-error",
                        "resource_id": "unknown",
                        "resource_type": "aws_security_group",
                        "check_type": "security_group",
                        "severity": "ERROR",
                        "title": "Security group scan failed",
                        "description": str(exc),
                    }
                )

        # ---- EBS encryption ---------------------------------------------
        if check_type in (None, "encryption"):
            try:
                ec2 = _make_client("ec2", self._region)
                paginator = ec2.get_paginator("describe_volumes")
                for page in paginator.paginate():
                    for vol in page["Volumes"]:
                        if vol.get("Encrypted", False):
                            continue
                        vid = vol["VolumeId"]
                        name = next(
                            (t["Value"] for t in vol.get("Tags", []) if t["Key"] == "Name"),
                            vid,
                        )
                        findings.append(
                            {
                                "id": f"finding-enc-ebs-{vid}",
                                "resource_id": vid,
                                "resource_type": "aws_ebs_volume",
                                "check_type": "encryption",
                                "severity": "MEDIUM",
                                "encryption_at_rest": False,
                                "current_state": "unencrypted",
                                "required_state": "encrypted",
                                "title": "EBS volume without encryption at rest",
                                "description": (
                                    f"EBS volume {name} ({vid}) does not have "
                                    "encryption at rest enabled. All block storage "
                                    "must be encrypted."
                                ),
                            }
                        )
            except botocore.exceptions.ClientError as exc:
                findings.append(
                    {
                        "id": "ebs-enc-error",
                        "resource_id": "unknown",
                        "resource_type": "aws_ebs_volume",
                        "check_type": "encryption",
                        "severity": "ERROR",
                        "title": "EBS encryption scan failed",
                        "description": str(exc),
                    }
                )

        # ---- ElastiCache encryption --------------------------------------
        if check_type in (None, "encryption"):
            try:
                ec = _make_client("elasticache", self._region)
                paginator = ec.get_paginator("describe_cache_clusters")
                for page in paginator.paginate():
                    for cluster in page["CacheClusters"]:
                        cid = cluster["CacheClusterId"]
                        at_rest = cluster.get("AtRestEncryptionEnabled", False)
                        if at_rest:
                            continue
                        findings.append(
                            {
                                "id": f"finding-enc-cache-{cid}",
                                "resource_id": cid,
                                "resource_type": "aws_elasticache_cluster",
                                "check_type": "encryption",
                                "severity": "HIGH",
                                "encryption_at_rest": False,
                                "current_state": "unencrypted",
                                "required_state": "encrypted",
                                "title": "ElastiCache cluster without encryption at rest",
                                "description": (
                                    f"ElastiCache cluster {cid} does not have "
                                    "encryption at rest enabled. All cache clusters "
                                    "must be encrypted."
                                ),
                            }
                        )
            except botocore.exceptions.ClientError as exc:
                findings.append(
                    {
                        "id": "cache-enc-error",
                        "resource_id": "unknown",
                        "resource_type": "aws_elasticache_cluster",
                        "check_type": "encryption",
                        "severity": "ERROR",
                        "title": "ElastiCache encryption scan failed",
                        "description": str(exc),
                    }
                )

        critical_count = sum(1 for f in findings if f.get("severity") == "CRITICAL")
        return {"findings": findings, "critical_count": critical_count}

    # ------------------------------------------------------------------
    # check_dependencies
    # Required IAM: ec2:DescribeNetworkInterfaces,
    #               elasticache:DescribeCacheClusters,
    #               ec2:DescribeVolumes
    # ------------------------------------------------------------------

    def check_dependencies(self, resource_id: str) -> dict:
        """Check what depends on a given resource using live AWS APIs.

        Inspects network interfaces for security groups, attached volumes
        for EBS, and replication group membership for ElastiCache clusters.

        Args:
            resource_id: AWS resource ID (vol-*, cache-*, sg-*, i-*, etc.).

        Returns:
            {"has_dependencies": bool, "dependents": [...]}
        """
        import botocore.exceptions

        dependents: list[str] = []

        # ---- Security group dependency check ----------------------------
        if resource_id.startswith("sg-"):
            try:
                ec2 = _make_client("ec2", self._region)
                paginator = ec2.get_paginator("describe_network_interfaces")
                for page in paginator.paginate(
                    Filters=[{"Name": "group-id", "Values": [resource_id]}]
                ):
                    for eni in page["NetworkInterfaces"]:
                        attachment = eni.get("Attachment", {})
                        instance_id = attachment.get("InstanceId")
                        if instance_id:
                            dependents.append(instance_id)
                        else:
                            dependents.append(eni["NetworkInterfaceId"])
            except botocore.exceptions.ClientError:
                pass

        # ---- EBS volume dependency check --------------------------------
        elif resource_id.startswith("vol-"):
            try:
                ec2 = _make_client("ec2", self._region)
                resp = ec2.describe_volumes(VolumeIds=[resource_id])
                for vol in resp.get("Volumes", []):
                    for attachment in vol.get("Attachments", []):
                        iid = attachment.get("InstanceId")
                        if iid:
                            dependents.append(iid)
            except botocore.exceptions.ClientError:
                pass

        # ---- ElastiCache cluster dependency check -----------------------
        elif resource_id.startswith("cache-") or resource_id.startswith("cluster-"):
            try:
                ec = _make_client("elasticache", self._region)
                resp = ec.describe_replication_groups()
                for rg in resp.get("ReplicationGroups", []):
                    if resource_id in rg.get("MemberClusters", []):
                        dependents.append(rg["ReplicationGroupId"])
            except botocore.exceptions.ClientError:
                pass

        return {
            "has_dependencies": len(dependents) > 0,
            "dependents": dependents,
        }