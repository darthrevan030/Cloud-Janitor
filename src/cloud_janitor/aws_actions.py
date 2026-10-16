"""Small AWS actions used by Terraform local-exec provisioners."""

from __future__ import annotations

import argparse
import os
import sys

import boto3


def _client(service: str):
    kwargs: dict[str, str] = {
        "region_name": (
            os.environ.get("AWS_DEFAULT_REGION")
            or os.environ.get("AWS_REGION")
            or "us-east-1"
        )
    }
    endpoint_url = os.environ.get("AWS_ENDPOINT_URL")
    if endpoint_url:
        kwargs["endpoint_url"] = endpoint_url
    return boto3.client(service, **kwargs)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run a Cloud Janitor AWS action.")
    subparsers = parser.add_subparsers(dest="service", required=True)

    elasticache = subparsers.add_parser("elasticache")
    elasticache_subparsers = elasticache.add_subparsers(dest="action", required=True)
    snapshot = elasticache_subparsers.add_parser("create-snapshot")
    snapshot.add_argument("--cache-cluster-id", required=True)
    snapshot.add_argument("--snapshot-name", required=True)
    delete_cluster = elasticache_subparsers.add_parser("delete-cache-cluster")
    delete_cluster.add_argument("--cache-cluster-id", required=True)
    delete_cluster.add_argument("--final-snapshot-identifier", required=True)

    ec2 = subparsers.add_parser("ec2")
    ec2_subparsers = ec2.add_subparsers(dest="action", required=True)
    delete_volume = ec2_subparsers.add_parser("delete-volume")
    delete_volume.add_argument("--volume-id", required=True)

    args = parser.parse_args(argv)
    if args.service == "elasticache":
        client = _client("elasticache")
        if args.action == "create-snapshot":
            client.create_snapshot(
                CacheClusterId=args.cache_cluster_id,
                SnapshotName=args.snapshot_name,
            )
        else:
            client.delete_cache_cluster(
                CacheClusterId=args.cache_cluster_id,
                FinalSnapshotIdentifier=args.final_snapshot_identifier,
            )
    else:
        _client("ec2").delete_volume(VolumeId=args.volume_id)
    return 0


if __name__ == "__main__":
    sys.exit(main())
