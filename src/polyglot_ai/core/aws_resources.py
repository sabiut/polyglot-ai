"""What the AWS panel shows: one entry per resource type.

Each :class:`ResourceType` says how to list that resource (read-only CLI
calls), how to describe one item, which facts to pull out of the describe
output for a readable summary, where it lives in the AWS Console, and how
to colour its status. The panel just renders this table, so adding a
service is one entry here — no new UI code.

No Qt here: everything runs in a worker thread and is unit-testable with
a stubbed ``call``.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from urllib.parse import quote

#: ``call(argv, global_call=False)`` → parsed JSON (dict) or None on error.
#: The caller records the error against the section. The panel's ``call``
#: also carries ``call.quiet`` (same, but a failure is not reported) and
#: ``call.note(text)`` (a line shown under the section).
Call = Callable[..., "dict | None"]

#: ``run(argv)`` → ``(output, return code)`` with the panel's profile, for
#: the extra calls a details summary needs.
Run = Callable[[list[str]], "tuple[str, int]"]


@dataclass(frozen=True)
class ResourceType:
    key: str
    title: str
    group: str
    #: List the resource: ``fetch(call) -> rows``. Each row is a dict with
    #: at least ``label`` (shown) and whatever ``describe``/actions need.
    fetch: Callable[[Call], list[dict]]
    #: CLI argv that describes one row (None: not describable).
    describe: Callable[[dict], list[str]] | None = None
    #: Pull readable facts out of the describe JSON: [(field, value), ...].
    summarize: Callable[[dict], list[tuple[str, str]]] | None = None
    #: AWS Console URL for one row in a region.
    console_url: Callable[[dict, str], str] | None = None
    #: Theme token for the status column, or None for the default colour.
    status_colour: Callable[[dict], str | None] = field(default=lambda row: None)
    #: describe output is plain text (``s3 ls``), not JSON.
    describe_is_text: bool = False
    #: More facts for the summary that need calls of their own:
    #: ``facts(row, run) -> [(field, value), ...]``. Runs off-thread
    #: alongside ``describe``.
    facts: Callable[[dict, Run], list[tuple[str, str]]] | None = None
    #: Heading over the describe output when a summary sits above it.
    output_label: str = "Full output"


def _console(region: str, service_path: str) -> str:
    return f"https://{region}.console.aws.amazon.com/{service_path}"


def _kv(pairs: list[tuple[str, object]]) -> list[tuple[str, str]]:
    """Drop empty values and stringify the rest."""
    out = []
    for label, value in pairs:
        if value in (None, "", [], {}):
            continue
        if isinstance(value, bool):
            value = "yes" if value else "no"
        out.append((label, str(value)))
    return out


def _ts(value) -> str:
    return str(value or "")[:19].replace("T", " ")


#: Per-row lookups are one CLI process each, so only the first few rows of
#: a section get them; the rest keep empty Status/Info columns.
_ENRICH_MAX = 12


def _enrich(call: Call, rows: list[dict], argv_for, apply) -> None:
    """Fill Status/Info for services whose list call returns names only.

    Failures are silent (``call.quiet``): the listing worked, and a
    missing describe permission shouldn't put an error under it.
    """
    quiet = getattr(call, "quiet", call)

    def one(row: dict) -> None:
        data = quiet(argv_for(row))
        if not data:
            return
        try:
            apply(row, data)
        except (KeyError, TypeError, ValueError):
            pass

    with ThreadPoolExecutor(max_workers=4) as pool:
        list(pool.map(one, rows[:_ENRICH_MAX]))


# ── Compute ──────────────────────────────────────────────────────────

_EC2_STATE = {
    "running": "accent_success_muted",
    "pending": "accent_warning",
    "stopping": "accent_warning",
    "stopped": "text_muted",
    "shutting-down": "accent_error",
    "terminated": "accent_error",
}


def _fetch_ec2(call: Call) -> list[dict]:
    data = call(["ec2", "describe-instances", "--max-items", "200"])
    rows = []
    for reservation in (data or {}).get("Reservations", []):
        for inst in reservation.get("Instances", []):
            tags = {t.get("Key"): t.get("Value") for t in inst.get("Tags", [])}
            name = tags.get("Name", "")
            iid = inst.get("InstanceId", "")
            rows.append(
                {
                    "id": iid,
                    "name": name,
                    "label": f"{name}  ({iid})" if name else iid,
                    "state": (inst.get("State") or {}).get("Name", "unknown"),
                    "instance_type": inst.get("InstanceType", ""),
                    "ip": inst.get("PublicIpAddress") or inst.get("PrivateIpAddress") or "",
                }
            )
    for row in rows:
        row["status"] = row["state"]
        row["info"] = f"{row['instance_type']}  {row['ip']}".strip()
    return rows


def _summarize_ec2(d: dict) -> list[tuple[str, str]]:
    try:
        inst = d["Reservations"][0]["Instances"][0]
    except (KeyError, IndexError, TypeError):
        return []
    tags = {t.get("Key"): t.get("Value") for t in inst.get("Tags", [])}
    return _kv(
        [
            ("Name", tags.get("Name")),
            ("Instance ID", inst.get("InstanceId")),
            ("State", (inst.get("State") or {}).get("Name")),
            ("Type", inst.get("InstanceType")),
            ("Launched", _ts(inst.get("LaunchTime"))),
            ("Public IP", inst.get("PublicIpAddress")),
            ("Private IP", inst.get("PrivateIpAddress")),
            ("Availability zone", (inst.get("Placement") or {}).get("AvailabilityZone")),
            ("VPC / subnet", " / ".join(filter(None, [inst.get("VpcId"), inst.get("SubnetId")]))),
            ("AMI", inst.get("ImageId")),
            ("Key pair", inst.get("KeyName")),
            (
                "Security groups",
                ", ".join(g.get("GroupName", "") for g in inst.get("SecurityGroups", [])),
            ),
            (
                "IAM role",
                ((inst.get("IamInstanceProfile") or {}).get("Arn") or "").rsplit("/", 1)[-1],
            ),
        ]
    )


def _fetch_lambda(call: Call) -> list[dict]:
    data = call(["lambda", "list-functions", "--max-items", "200"])
    rows = []
    for fn in (data or {}).get("Functions", []):
        name = fn.get("FunctionName", "")
        rows.append(
            {
                "id": name,
                "name": name,
                "label": name,
                "runtime": fn.get("Runtime", fn.get("PackageType", "")),
                "modified": _ts(fn.get("LastModified"))[:16],
                "memory": fn.get("MemorySize"),
                # A function can log somewhere other than /aws/lambda/<name>.
                "log_group": (fn.get("LoggingConfig") or {}).get("LogGroup")
                or f"/aws/lambda/{name}",
            }
        )
    for row in rows:
        row["status"] = str(row["runtime"] or "")
        row["info"] = row["modified"]
    return rows


def _summarize_lambda(d: dict) -> list[tuple[str, str]]:
    env = ((d.get("Environment") or {}).get("Variables") or {}).keys()
    return _kv(
        [
            ("Function", d.get("FunctionName")),
            ("State", d.get("State")),
            ("Runtime", d.get("Runtime") or d.get("PackageType")),
            ("Handler", d.get("Handler")),
            ("Memory", f"{d['MemorySize']} MB" if d.get("MemorySize") else None),
            ("Timeout", f"{d['Timeout']} s" if d.get("Timeout") else None),
            ("Architecture", ", ".join(d.get("Architectures") or [])),
            ("Code size", f"{d['CodeSize'] // 1024} KB" if d.get("CodeSize") else None),
            ("Last modified", _ts(d.get("LastModified"))),
            ("Role", (d.get("Role") or "").rsplit("/", 1)[-1]),
            # Names only: values are often secrets.
            ("Environment variables", ", ".join(sorted(env))),
            ("ARN", d.get("FunctionArn")),
        ]
    )


#: Caps so a huge account can't stall the panel; the CLI pages for us.
_ECS_MAX_CLUSTERS = 20
_ECS_MAX_SERVICES = 100


def _fetch_ecs(call: Call) -> list[dict]:
    clusters = call(["ecs", "list-clusters"])
    # The panel's ``call`` can attach a note to the section; a plain stub can't.
    note = getattr(call, "note", lambda text: None)
    cluster_arns = (clusters or {}).get("clusterArns", [])
    if len(cluster_arns) > _ECS_MAX_CLUSTERS:
        note(f"Only the first {_ECS_MAX_CLUSTERS} of {len(cluster_arns)} clusters are shown.")

    def one_cluster(cluster_arn: str) -> list[dict]:
        rows = []
        cluster = cluster_arn.rsplit("/", 1)[-1]
        services = call(["ecs", "list-services", "--cluster", cluster])
        all_arns = (services or {}).get("serviceArns", [])
        if len(all_arns) > _ECS_MAX_SERVICES:
            note(
                f"Only the first {_ECS_MAX_SERVICES} of {len(all_arns)} services "
                f"in cluster {cluster} are shown."
            )
        arns = all_arns[:_ECS_MAX_SERVICES]
        # describe-services takes at most 10 per call; this used to stop at
        # the first 10 services in a cluster without saying so.
        for start in range(0, len(arns), 10):
            described = call(
                [
                    "ecs",
                    "describe-services",
                    "--cluster",
                    cluster,
                    "--services",
                    *arns[start : start + 10],
                ]
            )
            for svc in (described or {}).get("services", []):
                row = {
                    "cluster": cluster,
                    "name": svc.get("serviceName", ""),
                    "running": svc.get("runningCount", 0),
                    "desired": svc.get("desiredCount", 0),
                }
                row["id"] = f"{cluster}/{row['name']}"
                row["label"] = f"{cluster} / {row['name']}"
                row["status"] = f"{row['running']}/{row['desired']}"
                row["info"] = svc.get("status", "")
                rows.append(row)
        return rows

    # Every cluster is two or more CLI calls of about a second each; one
    # after another, a dozen clusters held the whole refresh up.
    with ThreadPoolExecutor(max_workers=4) as pool:
        per_cluster = list(pool.map(one_cluster, cluster_arns[:_ECS_MAX_CLUSTERS]))
    return [row for rows in per_cluster for row in rows]


def _ecs_colour(row: dict) -> str | None:
    desired = row.get("desired", 0)
    if desired == 0:
        return "text_muted"  # scaled to zero on purpose, not unhealthy
    return "accent_success_muted" if row.get("running", 0) >= desired else "accent_warning"


def _summarize_ecs(d: dict) -> list[tuple[str, str]]:
    try:
        svc = d["services"][0]
    except (KeyError, IndexError, TypeError):
        return []
    rollout = ", ".join(
        f"{dep.get('status', '')} {dep.get('rolloutState', '')}".strip()
        for dep in svc.get("deployments", [])
    )
    events = [e.get("message", "") for e in svc.get("events", [])[:3]]
    return _kv(
        [
            ("Service", svc.get("serviceName")),
            ("Status", svc.get("status")),
            (
                "Tasks",
                f"{svc.get('runningCount', 0)} running / {svc.get('desiredCount', 0)} desired"
                f" / {svc.get('pendingCount', 0)} pending",
            ),
            ("Launch type", svc.get("launchType") or "capacity provider"),
            ("Task definition", (svc.get("taskDefinition") or "").rsplit("/", 1)[-1]),
            ("Deployments", rollout),
            ("Created", _ts(svc.get("createdAt"))),
            *[(f"Event {i + 1}", msg) for i, msg in enumerate(events)],
        ]
    )


def _fetch_eks(call: Call) -> list[dict]:
    data = call(["eks", "list-clusters"])
    rows = [
        {"id": name, "name": name, "label": name, "status": "", "info": ""}
        for name in (data or {}).get("clusters", [])
    ]

    def apply(row: dict, d: dict) -> None:
        cluster = d["cluster"]
        row["status"] = cluster.get("status", "")
        row["info"] = f"Kubernetes {cluster['version']}" if cluster.get("version") else ""

    _enrich(call, rows, lambda r: ["eks", "describe-cluster", "--name", r["name"]], apply)
    return rows


#: Shared by EKS clusters and DynamoDB tables: ACTIVE is healthy, anything
#: else (CREATING, UPDATING, DELETING, FAILED) is worth a look.
def _active_colour(row: dict) -> str | None:
    status = row.get("status", "")
    if not status:
        return None
    if status == "ACTIVE":
        return "accent_success_muted"
    return "accent_error" if status == "FAILED" else "accent_warning"


def _summarize_eks(d: dict) -> list[tuple[str, str]]:
    c = d.get("cluster") or {}
    return _kv(
        [
            ("Cluster", c.get("name")),
            ("Status", c.get("status")),
            ("Kubernetes version", c.get("version")),
            ("Platform version", c.get("platformVersion")),
            ("Endpoint", c.get("endpoint")),
            ("VPC", (c.get("resourcesVpcConfig") or {}).get("vpcId")),
            ("Created", _ts(c.get("createdAt"))),
            ("Role", (c.get("roleArn") or "").rsplit("/", 1)[-1]),
        ]
    )


# ── Data ─────────────────────────────────────────────────────────────


def _fetch_s3(call: Call) -> list[dict]:
    # Buckets are account-wide, so this lists every region's. S3 only says
    # which region each is in when the request carries a parameter, and
    # ``--page-size`` is the one that changes nothing else.
    data = call(["s3api", "list-buckets", "--page-size", "1000"], global_call=True)
    rows = []
    for b in (data or {}).get("Buckets", []):
        name = b.get("Name", "")
        region = b.get("BucketRegion") or ""
        rows.append(
            {
                "id": name,
                "name": name,
                "label": name,
                "created": (b.get("CreationDate") or "")[:10],
                "region": region,
                # The Status column shows where the bucket lives: the list
                # is the same whichever region is selected above.
                "status": region,
            }
        )
        rows[-1]["info"] = rows[-1]["created"]
    return rows


def _s3_in_region(row: dict) -> list[str]:
    return ["--region", row["region"]] if row.get("region") else []


def _unknown(output: str) -> str:
    return "unknown — " + output.removeprefix("Error: ")[:90]


def _s3_public_access(output: str, code: int) -> str:
    if code != 0:
        if "NoSuchPublicAccessBlockConfiguration" in output:
            return "no bucket-level block (the account-level setting may still apply)"
        return _unknown(output)
    cfg = (json.loads(output) or {}).get("PublicAccessBlockConfiguration") or {}
    settings = ("BlockPublicAcls", "IgnorePublicAcls", "BlockPublicPolicy", "RestrictPublicBuckets")
    off = [name for name in settings if not cfg.get(name)]
    return (
        "blocked (all four settings on)"
        if not off
        else "NOT fully blocked — off: " + ", ".join(off)
    )


def _s3_encryption(output: str, code: int) -> str:
    if code != 0:
        if "ServerSideEncryptionConfigurationNotFound" in output:
            return "none configured"
        return _unknown(output)
    rules = ((json.loads(output) or {}).get("ServerSideEncryptionConfiguration") or {}).get("Rules")
    default = (rules or [{}])[0].get("ApplyServerSideEncryptionByDefault") or {}
    algorithm = default.get("SSEAlgorithm", "")
    if algorithm == "AES256":
        return "SSE-S3 (AES256)"
    if algorithm.startswith("aws:kms"):
        key = (default.get("KMSMasterKeyID") or "").rsplit("/", 1)[-1]
        kind = "DSSE-KMS" if algorithm.endswith("dsse") else "SSE-KMS"
        return f"{kind} ({key})" if key else f"{kind} (AWS managed key)"
    return algorithm or "none configured"


def _s3_versioning(output: str, code: int) -> str:
    if code != 0:
        return _unknown(output)
    data = json.loads(output) if output else {}
    status = (data or {}).get("Status")
    if not status:
        return "off"
    return f"{status}, MFA delete on" if (data or {}).get("MFADelete") == "Enabled" else status


def _s3_region(output: str, code: int) -> str:
    if code != 0:
        return _unknown(output)
    constraint = (json.loads(output) or {}).get("LocationConstraint")
    # Two historical spellings: null is us-east-1, "EU" is eu-west-1.
    return {None: "us-east-1", "": "us-east-1", "EU": "eu-west-1"}.get(constraint, constraint)


def _s3_facts(row: dict, run: Run) -> list[tuple[str, str]]:
    """Where a bucket is and how exposed it is — one small call per fact."""
    bucket = ["--bucket", row["name"], *_s3_in_region(row)]
    lookups = {
        "Public access": (["s3api", "get-public-access-block", *bucket], _s3_public_access),
        "Encryption": (["s3api", "get-bucket-encryption", *bucket], _s3_encryption),
        "Versioning": (["s3api", "get-bucket-versioning", *bucket], _s3_versioning),
    }
    if not row.get("region"):
        lookups = {
            "Region": (["s3api", "get-bucket-location", *bucket], _s3_region),
            **lookups,
        }

    def one(item):
        label, (argv, parse) = item
        try:
            return label, parse(*run(argv))
        except (json.JSONDecodeError, TypeError, ValueError, AttributeError):
            return label, "unknown — unexpected output"

    with ThreadPoolExecutor(max_workers=4) as pool:
        found = list(pool.map(one, lookups.items()))
    return _kv(
        [
            ("Bucket", row.get("name")),
            ("Region", row.get("region")),
            ("Created", row.get("created")),
            *found,
        ]
    )


_RDS_STATUS = {
    "available": "accent_success_muted",
    "stopped": "text_muted",
    "failed": "accent_error",
    "storage-full": "accent_error",
    "incompatible-parameters": "accent_error",
}


def _fetch_rds(call: Call) -> list[dict]:
    data = call(["rds", "describe-db-instances"])
    rows = []
    for db in (data or {}).get("DBInstances", []):
        ident = db.get("DBInstanceIdentifier", "")
        endpoint = db.get("Endpoint") or {}
        rows.append(
            {
                "id": ident,
                "name": ident,
                "label": ident,
                "status": db.get("DBInstanceStatus", ""),
                "info": f"{db.get('Engine', '')} {db.get('DBInstanceClass', '')}".strip(),
                # What "Connect in Database panel" needs. The endpoint is
                # missing while an instance is still being created.
                "engine": db.get("Engine", ""),
                "endpoint": endpoint.get("Address", ""),
                "port": endpoint.get("Port") or 0,
                "master_user": db.get("MasterUsername", ""),
                "db_name": db.get("DBName") or "",
                "public": bool(db.get("PubliclyAccessible")),
                "iam_auth": bool(db.get("IAMDatabaseAuthenticationEnabled")),
            }
        )
    return rows


def _summarize_rds(d: dict) -> list[tuple[str, str]]:
    try:
        db = d["DBInstances"][0]
    except (KeyError, IndexError, TypeError):
        return []
    ep = db.get("Endpoint") or {}
    return _kv(
        [
            ("Instance", db.get("DBInstanceIdentifier")),
            ("Status", db.get("DBInstanceStatus")),
            ("Engine", f"{db.get('Engine', '')} {db.get('EngineVersion', '')}".strip()),
            ("Class", db.get("DBInstanceClass")),
            ("Endpoint", f"{ep['Address']}:{ep.get('Port', '')}" if ep.get("Address") else None),
            ("Multi-AZ", db.get("MultiAZ")),
            (
                "Storage",
                f"{db.get('AllocatedStorage', '?')} GB {db.get('StorageType', '')}".strip(),
            ),
            ("Publicly accessible", db.get("PubliclyAccessible")),
            (
                "Backups kept",
                f"{db['BackupRetentionPeriod']} days" if db.get("BackupRetentionPeriod") else None,
            ),
            ("Availability zone", db.get("AvailabilityZone")),
        ]
    )


def _fetch_dynamodb(call: Call) -> list[dict]:
    data = call(["dynamodb", "list-tables"])
    rows = [
        {"id": name, "name": name, "label": name, "status": "", "info": ""}
        for name in (data or {}).get("TableNames", [])
    ]

    def apply(row: dict, d: dict) -> None:
        table = d["Table"]
        row["status"] = table.get("TableStatus", "")
        if isinstance(table.get("ItemCount"), int):
            row["info"] = f"{table['ItemCount']:,} items"

    _enrich(call, rows, lambda r: ["dynamodb", "describe-table", "--table-name", r["name"]], apply)
    return rows


def _summarize_dynamodb(d: dict) -> list[tuple[str, str]]:
    t = d.get("Table") or {}
    keys = ", ".join(
        f"{k.get('AttributeName')} ({k.get('KeyType', '').lower()})" for k in t.get("KeySchema", [])
    )
    size = t.get("TableSizeBytes")
    return _kv(
        [
            ("Table", t.get("TableName")),
            ("Status", t.get("TableStatus")),
            ("Keys", keys),
            ("Items", t.get("ItemCount")),
            ("Size", f"{size / 1_048_576:.1f} MB" if isinstance(size, int) else None),
            ("Billing", (t.get("BillingModeSummary") or {}).get("BillingMode") or "PROVISIONED"),
            ("Created", _ts(t.get("CreationDateTime"))),
            ("ARN", t.get("TableArn")),
        ]
    )


# ── Messaging ────────────────────────────────────────────────────────


def _fetch_sqs(call: Call) -> list[dict]:
    data = call(["sqs", "list-queues"])
    rows = []
    for url in (data or {}).get("QueueUrls", []):
        name = url.rstrip("/").rsplit("/", 1)[-1]
        rows.append({"id": url, "name": name, "url": url, "label": name, "status": "", "info": ""})

    def apply(row: dict, d: dict) -> None:
        attrs = d["Attributes"]
        row["status"] = f"{int(attrs.get('ApproximateNumberOfMessages', 0)):,} waiting"
        in_flight = int(attrs.get("ApproximateNumberOfMessagesNotVisible", 0))
        parts = [f"{in_flight:,} in flight" if in_flight else "", "FIFO" if _is_fifo(row) else ""]
        row["info"] = "  ".join(filter(None, parts))

    _enrich(
        call,
        rows,
        lambda r: [
            "sqs",
            "get-queue-attributes",
            "--queue-url",
            r["url"],
            "--attribute-names",
            "ApproximateNumberOfMessages",
            "ApproximateNumberOfMessagesNotVisible",
        ],
        apply,
    )
    return rows


def _is_fifo(row: dict) -> bool:
    return row.get("name", "").endswith(".fifo")


def _summarize_sqs(d: dict) -> list[tuple[str, str]]:
    a = d.get("Attributes") or {}
    return _kv(
        [
            ("Messages available", a.get("ApproximateNumberOfMessages")),
            ("In flight", a.get("ApproximateNumberOfMessagesNotVisible")),
            ("Delayed", a.get("ApproximateNumberOfMessagesDelayed")),
            (
                "Visibility timeout",
                f"{a['VisibilityTimeout']} s" if a.get("VisibilityTimeout") else None,
            ),
            (
                "Retention",
                f"{int(a['MessageRetentionPeriod']) // 3600} h"
                if a.get("MessageRetentionPeriod")
                else None,
            ),
            ("FIFO", a.get("FifoQueue")),
            ("Dead-letter queue", a.get("RedrivePolicy")),
            ("ARN", a.get("QueueArn")),
        ]
    )


def _fetch_sns(call: Call) -> list[dict]:
    data = call(["sns", "list-topics"])
    rows = []
    for t in (data or {}).get("Topics", []):
        arn = t.get("TopicArn", "")
        name = arn.rsplit(":", 1)[-1]
        rows.append({"id": arn, "name": name, "arn": arn, "label": name, "status": "", "info": ""})

    def apply(row: dict, d: dict) -> None:
        confirmed = int(d["Attributes"].get("SubscriptionsConfirmed", 0))
        row["status"] = f"{confirmed:,} subscriber{'' if confirmed == 1 else 's'}"
        row["info"] = "FIFO" if _is_fifo(row) else ""

    _enrich(call, rows, lambda r: ["sns", "get-topic-attributes", "--topic-arn", r["arn"]], apply)
    return rows


def _summarize_sns(d: dict) -> list[tuple[str, str]]:
    a = d.get("Attributes") or {}
    return _kv(
        [
            ("Display name", a.get("DisplayName")),
            ("Confirmed subscriptions", a.get("SubscriptionsConfirmed")),
            ("Pending subscriptions", a.get("SubscriptionsPending")),
            ("ARN", a.get("TopicArn")),
        ]
    )


# ── Operations ───────────────────────────────────────────────────────


def _fetch_cloudformation(call: Call) -> list[dict]:
    data = call(["cloudformation", "describe-stacks"])
    rows = []
    for s in (data or {}).get("Stacks", []):
        name = s.get("StackName", "")
        rows.append(
            {
                "id": s.get("StackId", name),
                "name": name,
                "stack_id": s.get("StackId", ""),
                "label": name,
                "status": s.get("StackStatus", ""),
                "info": _ts(s.get("LastUpdatedTime") or s.get("CreationTime"))[:16],
            }
        )
    return rows


def _cfn_colour(row: dict) -> str | None:
    status = row.get("status", "")
    if status.endswith("_FAILED") or "ROLLBACK" in status:
        return "accent_error"
    if status.endswith("_IN_PROGRESS"):
        return "accent_warning"
    if status.endswith("_COMPLETE"):
        return "accent_success_muted"
    return None


def _summarize_cloudformation(d: dict) -> list[tuple[str, str]]:
    try:
        s = d["Stacks"][0]
    except (KeyError, IndexError, TypeError):
        return []
    # Outputs only: Parameters can carry secrets.
    outputs = [(f"Output {o.get('OutputKey')}", o.get("OutputValue")) for o in s.get("Outputs", [])]
    return _kv(
        [
            ("Stack", s.get("StackName")),
            ("Status", s.get("StackStatus")),
            ("Reason", s.get("StackStatusReason")),
            ("Created", _ts(s.get("CreationTime"))),
            ("Last updated", _ts(s.get("LastUpdatedTime"))),
            ("Termination protection", s.get("EnableTerminationProtection")),
            *outputs,
        ]
    )


_ALARM_COLOUR = {
    "ALARM": "accent_error",
    "OK": "accent_success_muted",
    "INSUFFICIENT_DATA": "accent_warning",
}


def _fetch_alarms(call: Call) -> list[dict]:
    data = call(["cloudwatch", "describe-alarms", "--max-items", "200"])
    rows = []
    for a in (data or {}).get("MetricAlarms", []):
        name = a.get("AlarmName", "")
        rows.append(
            {
                "id": name,
                "name": name,
                "label": name,
                "status": a.get("StateValue", ""),
                "info": f"{a.get('Namespace', '')} {a.get('MetricName', '')}".strip(),
            }
        )
    # Firing alarms first: that's what someone opening this section wants.
    rows.sort(key=lambda r: (r["status"] != "ALARM", r["name"]))
    return rows


def _summarize_alarm(d: dict) -> list[tuple[str, str]]:
    try:
        a = d["MetricAlarms"][0]
    except (KeyError, IndexError, TypeError):
        return []
    return _kv(
        [
            ("Alarm", a.get("AlarmName")),
            ("State", a.get("StateValue")),
            ("Why", a.get("StateReason")),
            ("Since", _ts(a.get("StateUpdatedTimestamp"))),
            ("Metric", f"{a.get('Namespace', '')} / {a.get('MetricName', '')}"),
            (
                "Condition",
                f"{a.get('Statistic', '')} {a.get('ComparisonOperator', '')} {a.get('Threshold', '')}".strip(),
            ),
            ("Description", a.get("AlarmDescription")),
        ]
    )


RESOURCE_TYPES: list[ResourceType] = [
    ResourceType(
        key="ec2",
        title="EC2",
        group="Compute",
        fetch=_fetch_ec2,
        describe=lambda r: ["ec2", "describe-instances", "--instance-ids", r["id"]],
        summarize=_summarize_ec2,
        console_url=lambda r, reg: _console(
            reg, f"ec2/home?region={reg}#InstanceDetails:instanceId={r['id']}"
        ),
        status_colour=lambda r: _EC2_STATE.get(r.get("state", "")),
    ),
    ResourceType(
        key="lambda",
        title="Lambda",
        group="Compute",
        fetch=_fetch_lambda,
        describe=lambda r: ["lambda", "get-function-configuration", "--function-name", r["name"]],
        summarize=_summarize_lambda,
        console_url=lambda r, reg: _console(
            reg, f"lambda/home?region={reg}#/functions/{quote(r['name'])}"
        ),
    ),
    ResourceType(
        key="ecs",
        title="ECS services",
        group="Compute",
        fetch=_fetch_ecs,
        describe=lambda r: [
            "ecs",
            "describe-services",
            "--cluster",
            r["cluster"],
            "--services",
            r["name"],
        ],
        summarize=_summarize_ecs,
        console_url=lambda r, reg: _console(
            reg,
            f"ecs/v2/clusters/{quote(r['cluster'])}/services/{quote(r['name'])}/health?region={reg}",
        ),
        status_colour=_ecs_colour,
    ),
    ResourceType(
        key="eks",
        title="EKS clusters",
        group="Compute",
        fetch=_fetch_eks,
        describe=lambda r: ["eks", "describe-cluster", "--name", r["name"]],
        summarize=_summarize_eks,
        console_url=lambda r, reg: _console(
            reg, f"eks/home?region={reg}#/clusters/{quote(r['name'])}"
        ),
        status_colour=_active_colour,
    ),
    ResourceType(
        key="s3",
        title="S3 buckets",
        group="Data",
        fetch=_fetch_s3,
        describe=lambda r: [
            "s3",
            "ls",
            f"s3://{r['name']}",
            "--human-readable",
            "--summarize",
            *_s3_in_region(r),
        ],
        describe_is_text=True,
        facts=_s3_facts,
        output_label="Top-level objects",
        # The bucket's own region when known, not the one selected above.
        console_url=lambda r, reg: (
            f"https://s3.console.aws.amazon.com/s3/buckets/{quote(r['name'])}"
            f"?region={r.get('region') or reg}"
        ),
    ),
    ResourceType(
        key="rds",
        title="RDS databases",
        group="Data",
        fetch=_fetch_rds,
        describe=lambda r: ["rds", "describe-db-instances", "--db-instance-identifier", r["id"]],
        summarize=_summarize_rds,
        console_url=lambda r, reg: _console(
            reg, f"rds/home?region={reg}#database:id={quote(r['id'])}"
        ),
        status_colour=lambda r: _RDS_STATUS.get(
            r.get("status", ""), "accent_warning" if r.get("status") else None
        ),
    ),
    ResourceType(
        key="dynamodb",
        title="DynamoDB tables",
        group="Data",
        fetch=_fetch_dynamodb,
        describe=lambda r: ["dynamodb", "describe-table", "--table-name", r["name"]],
        summarize=_summarize_dynamodb,
        console_url=lambda r, reg: _console(
            reg, f"dynamodbv2/home?region={reg}#table?name={quote(r['name'])}"
        ),
        status_colour=_active_colour,
    ),
    ResourceType(
        key="sqs",
        title="SQS queues",
        group="Messaging",
        fetch=_fetch_sqs,
        describe=lambda r: [
            "sqs",
            "get-queue-attributes",
            "--queue-url",
            r["url"],
            "--attribute-names",
            "All",
        ],
        summarize=_summarize_sqs,
        console_url=lambda r, reg: _console(
            reg, f"sqs/v3/home?region={reg}#/queues/{quote(r['url'], safe='')}"
        ),
    ),
    ResourceType(
        key="sns",
        title="SNS topics",
        group="Messaging",
        fetch=_fetch_sns,
        describe=lambda r: ["sns", "get-topic-attributes", "--topic-arn", r["arn"]],
        summarize=_summarize_sns,
        console_url=lambda r, reg: _console(reg, f"sns/v3/home?region={reg}#/topic/{r['arn']}"),
    ),
    ResourceType(
        key="cloudformation",
        title="CloudFormation stacks",
        group="Operations",
        fetch=_fetch_cloudformation,
        describe=lambda r: ["cloudformation", "describe-stacks", "--stack-name", r["name"]],
        summarize=_summarize_cloudformation,
        console_url=lambda r, reg: _console(
            reg,
            f"cloudformation/home?region={reg}#/stacks/stackinfo?stackId={quote(r['stack_id'], safe='')}",
        ),
        status_colour=_cfn_colour,
    ),
    ResourceType(
        key="alarms",
        title="CloudWatch alarms",
        group="Operations",
        fetch=_fetch_alarms,
        describe=lambda r: ["cloudwatch", "describe-alarms", "--alarm-names", r["name"]],
        summarize=_summarize_alarm,
        console_url=lambda r, reg: _console(
            reg, f"cloudwatch/home?region={reg}#alarmsV2:alarm/{quote(r['name'], safe='')}"
        ),
        status_colour=lambda r: _ALARM_COLOUR.get(r.get("status", "")),
    ),
]

BY_KEY: dict[str, ResourceType] = {rt.key: rt for rt in RESOURCE_TYPES}


def format_summary(pairs: list[tuple[str, str]]) -> str:
    """Aligned ``Field   value`` lines for the details pane."""
    if not pairs:
        return ""
    width = min(max(len(k) for k, _ in pairs), 24)
    return "\n".join(f"{k.ljust(width)}  {v}" for k, v in pairs)
