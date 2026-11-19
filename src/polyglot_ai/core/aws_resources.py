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
import re
import shlex
import time
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import datetime
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
    #: alongside ``describe``; shown after ``summarize``'s — what it is,
    #: then what is in it.
    facts: Callable[[dict, Run], list[tuple[str, str]]] | None = None
    #: Heading over the describe output when a summary sits above it.
    output_label: str = "Full output"
    #: The word before the name in the details heading, when the first
    #: word of ``title`` won't do ("LOAD — web").
    heading: str = ""


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


def running_instances(profile: str = "", region: str = "") -> tuple[list[tuple[str, str]], str]:
    """Running EC2 instances to offer as a way in for Session Manager.

    Returns ``([(label, instance_id)], problem)``. The instances that
    Session Manager can reach right now come first; one it can't is
    still listed, and says so, because that is the likelier reason a
    tunnel through it fails than a mistyped ID. ``problem`` is why the
    list couldn't be read, in words, and '' when it could.
    """
    from polyglot_ai.core import aws_cli

    if not aws_cli.aws_available():
        return [], "The AWS CLI (aws) isn't installed on this machine."
    out, code = aws_cli.run(
        [
            "ec2",
            "describe-instances",
            "--filters",
            "Name=instance-state-name,Values=running",
            "--max-items",
            "200",
        ],
        profile=profile,
        region=region,
    )
    if code != 0:
        return [], aws_cli.explain_error(out.removeprefix("Error:").strip())
    try:
        rows = _fetch_ec2(lambda _argv: json.loads(out or "{}"))
    except (ValueError, TypeError, AttributeError):
        return [], "The AWS CLI's answer couldn't be read."

    # Best effort: without ssm:DescribeInstanceInformation the list is
    # still useful, it just can't say which instances have the agent.
    online: set[str] | None = None
    out, code = aws_cli.run(
        [
            "ssm",
            "describe-instance-information",
            "--query",
            "InstanceInformationList[?PingStatus==`Online`].InstanceId",
            "--max-items",
            "200",
        ],
        profile=profile,
        region=region,
    )
    if code == 0:
        try:
            answer = json.loads(out or "[]")
            if isinstance(answer, list):
                online = {str(i) for i in answer}
        except ValueError:
            online = None

    choices = []
    for row in rows:
        if row.get("state") != "running" or not row.get("id"):
            continue
        label = f"{row['id']}  {row.get('name', '')}".strip()
        reachable = online is None or row["id"] in online
        if not reachable:
            label += "  — not connected to Session Manager"
        choices.append((not reachable, label, row["id"]))
    choices.sort(key=lambda c: (c[0], c[1]))
    return [(label, instance_id) for _unreachable, label, instance_id in choices], ""


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


# ECR ---------------------------------------------------------------

#: How many of a repository's images are read: one request's worth (the
#: API's own page limit), so a repository with years of builds in it
#: costs the same one call as a new one.
_ECR_MAX_IMAGES = 1000
#: The newest images listed in a repository's details.
_ECR_NEWEST = 10


def _ecr_images_argv(name: str) -> list[str]:
    # ``--max-results`` with ``--no-paginate`` rather than ``--max-items``:
    # the panel reads a cut-short ``--max-items`` answer as "this section
    # lists only some of what there is", which is about the repositories,
    # not about the images in one of them.
    return [
        "ecr",
        "describe-images",
        "--repository-name",
        name,
        "--max-results",
        str(_ECR_MAX_IMAGES),
        "--no-paginate",
    ]


def _instant(value) -> float:
    """A timestamp as the CLI prints it — ISO text (v2) or seconds (v1) — for sorting."""
    if isinstance(value, (int, float)):
        return float(value)
    try:
        return datetime.fromisoformat(str(value)).timestamp()
    except ValueError:
        return 0.0


def _when(value) -> str:
    if isinstance(value, (int, float)):
        return time.strftime("%Y-%m-%d %H:%M:%S", time.gmtime(value)) + " UTC"
    return _ts(value)


_SEVERITIES = ("CRITICAL", "HIGH")
_SCAN_STATUS = {
    "FAILED": "scan failed",
    "UNSUPPORTED_IMAGE": "can't be scanned",
    "IN_PROGRESS": "scan in progress",
    "PENDING": "scan pending",
}


def _scan_result(image: dict) -> str:
    """``2 critical, 5 high, 12 lower`` — or that the scan found nothing, or didn't run."""
    summary = image.get("imageScanFindingsSummary")
    if summary is None:
        return _SCAN_STATUS.get((image.get("imageScanStatus") or {}).get("status", ""), "")
    counts = summary.get("findingSeverityCounts") or {}
    lower = sum(n for severity, n in counts.items() if severity not in _SEVERITIES)
    found = [f"{counts[s]} {s.lower()}" for s in _SEVERITIES if counts.get(s)]
    if lower:
        found.append(f"{lower} lower")
    return ", ".join(found) or "scanned, nothing found"


def _image_name(image: dict) -> str:
    tags = image.get("imageTags") or []
    # An untagged image has only its digest: enough of it to tell two apart.
    return ", ".join(sorted(tags)) or "untagged " + image.get("imageDigest", "")[:19]


def _image_line(image: dict) -> str:
    size = image.get("imageSizeInBytes")
    return "  ".join(
        filter(
            None,
            [
                _image_name(image),
                _when(image.get("imagePushedAt"))[:16],
                f"{size / 1_000_000:.1f} MB" if isinstance(size, (int, float)) else "",
                _scan_result(image),
            ],
        )
    )


def _newest(images: list[dict]) -> list[dict]:
    return sorted(images, key=lambda i: _instant(i.get("imagePushedAt")), reverse=True)


def _count_images(row: dict, data: dict) -> None:
    images = data["imageDetails"]
    more = bool(data.get("nextToken"))
    row["images"] = len(images)
    if not images:
        row["status"] = "empty"
        return
    row["status"] = f"{len(images)}{'+' if more else ''} image{'s' if len(images) != 1 else ''}"
    if more:
        # The newest of the ones read isn't necessarily the newest there is.
        return
    latest = _newest(images)[0]
    scan = _scan_result(latest)
    row["info"] = "  ".join(
        filter(
            None,
            [
                "latest: " + _image_name(latest),
                _when(latest.get("imagePushedAt"))[:10],
                # Only what needs attention; a clean scan isn't news here.
                scan if "critical" in scan else "",
            ],
        )
    )


def _fetch_ecr(call: Call) -> list[dict]:
    data = call(["ecr", "describe-repositories", "--max-items", "200"])
    rows = []
    for repo in (data or {}).get("repositories", []):
        name = repo.get("repositoryName", "")
        rows.append(
            {
                "id": name,
                "name": name,
                "arn": repo.get("repositoryArn", ""),
                "uri": repo.get("repositoryUri", ""),
                "registry": repo.get("registryId", ""),
                "label": name,
                "status": "",
                "info": "",
            }
        )
    rows.sort(key=lambda r: r["name"].lower())
    _enrich(call, rows, lambda r: _ecr_images_argv(r["name"]), _count_images)
    return rows


_TAG_MUTABILITY = {
    "MUTABLE": "mutable — pushing a tag again moves it to the new image",
    "IMMUTABLE": "immutable — a tag can't be moved to another image",
}


def _summarize_ecr(d: dict) -> list[tuple[str, str]]:
    try:
        repo = d["repositories"][0]
    except (KeyError, IndexError, TypeError):
        return []
    mutability = repo.get("imageTagMutability") or ""
    encryption = repo.get("encryptionConfiguration") or {}
    kind = encryption.get("encryptionType") or ""
    key = (encryption.get("kmsKey") or "").rsplit("/", 1)[-1]
    scan = (repo.get("imageScanningConfiguration") or {}).get("scanOnPush")
    return _kv(
        [
            ("Repository", repo.get("repositoryName")),
            ("URI", repo.get("repositoryUri")),
            ("Created", _when(repo.get("createdAt"))),
            ("Tags", _TAG_MUTABILITY.get(mutability, mutability.lower().replace("_", " "))),
            (
                "Scan on push",
                None
                if scan is None
                else "on"
                if scan
                else "off (the registry's own scanning settings may still apply)",
            ),
            (
                "Encryption",
                {"AES256": "AES-256"}.get(kind, f"{kind} ({key})" if key else kind),
            ),
        ]
    )


def _ecr_images(output: str, code: int) -> list[tuple[str, str]]:
    if code != 0:
        return [("Images", _unknown(output))]
    data = json.loads(output or "{}")
    images = data.get("imageDetails") or []
    if not images:
        return [("Images", "none — nothing has been pushed yet")]
    untagged = sum(1 for image in images if not image.get("imageTags"))
    count = f"{len(images)}" + (f" ({untagged} untagged)" if untagged else "")
    if data.get("nextToken"):
        count = (
            f"more than {len(images)} — only the first {len(images)} were read, "
            "so the newest may not be among those below"
        )
    return [
        ("Images", count),
        *_listed("Newest", [_image_line(image) for image in _newest(images)[:_ECR_NEWEST]]),
    ]


_LIFECYCLE_WHICH = {"untagged": "untagged images", "any": "all images"}


def _lifecycle_line(rule: dict) -> str:
    selection = rule.get("selection") or {}
    status = selection.get("tagStatus", "")
    tagged = selection.get("tagPrefixList") or selection.get("tagPatternList") or []
    which = _LIFECYCLE_WHICH.get(
        status,
        "images tagged " + ", ".join(f"{t}*" if "*" not in t else t for t in tagged)
        if tagged
        else "tagged images",
    )
    number, unit = selection.get("countNumber"), selection.get("countUnit", "days")
    action = (rule.get("action") or {}).get("type", "expire")
    what = {
        "sinceImagePushed": f"{action} {number} {unit} after they were pushed",
        "imageCountMoreThan": f"keep the newest {number}, {action} the rest",
    }.get(selection.get("countType", ""), f"{action} by {selection.get('countType', '?')}")
    line = f"{which}: {what}"
    return f"{line} — {rule['description']}" if rule.get("description") else line


def _ecr_lifecycle(output: str, code: int) -> list[tuple[str, str]]:
    label = "Lifecycle policy"
    if code != 0:
        if "LifecyclePolicyNotFoundException" in output:
            return [(label, "none — images are kept until someone deletes them")]
        return [(label, _unknown(output))]
    policy = json.loads(json.loads(output).get("lifecyclePolicyText") or "{}")
    rules = sorted(policy.get("rules") or [], key=lambda r: r.get("rulePriority", 0))
    return _listed(label, [_lifecycle_line(rule) for rule in rules], none="set, with no rules")


_ECR_PULL = frozenset(
    {"ecr:getdownloadurlforlayer", "ecr:batchgetimage", "ecr:batchchecklayeravailability"}
)
_ECR_PUSH = frozenset(
    {"ecr:putimage", "ecr:initiatelayerupload", "ecr:uploadlayerpart", "ecr:completelayerupload"}
)


def _as_list(value) -> list:
    return value if isinstance(value, list) else [] if value is None else [value]


def _principal_names(principal) -> list[str]:
    """Who a policy statement is about, shortest readable form each."""
    if principal == "*":
        return ["ANYONE"]
    names = []
    for kind, values in (principal or {}).items():
        for value in _as_list(values):
            arn = re.fullmatch(r"arn:aws[\w-]*:iam::(\d{12}):(.+)", str(value))
            if value == "*":
                names.append("ANYONE")
            elif kind != "AWS":
                names.append(str(value))
            elif arn:
                account, what = arn.groups()
                names.append(f"account {account}" if what == "root" else f"{what} in {account}")
            else:
                names.append(f"account {value}" if str(value).isdigit() else str(value))
    return names


def _policy_line(statement: dict) -> str:
    who = ", ".join(_principal_names(statement.get("Principal"))) or "nobody named"
    actions = {str(a).lower() for a in _as_list(statement.get("Action"))}
    if actions & {"*", "ecr:*"}:
        can = "do anything"
    elif actions & _ECR_PUSH:
        can = "push and pull" if actions & _ECR_PULL else "push"
    elif actions and actions <= _ECR_PULL:
        can = "pull"
    else:
        can = f"use {len(actions)} action{'s' if len(actions) != 1 else ''}"
    allowed = statement.get("Effect") == "Allow"
    line = f"{who} can {can}" if allowed else f"{who} may NOT {can}"
    return f"{line} (with conditions)" if statement.get("Condition") else line


def _ecr_policy(output: str, code: int) -> list[tuple[str, str]]:
    label = "Access policy"
    if code != 0:
        if "RepositoryPolicyNotFoundException" in output:
            return [(label, "none — only this account's own IAM permissions apply")]
        return [(label, _unknown(output))]
    policy = json.loads(json.loads(output).get("policyText") or "{}")
    statements = _as_list(policy.get("Statement"))
    return _listed(label, [_policy_line(s) for s in statements], none="set, with no statements")


def _ecr_facts(row: dict, run: Run) -> list[tuple[str, str]]:
    """What is in a repository, what clears it out, and who else may use it."""
    name = ["--repository-name", row["name"]]
    lookups = [
        ("Images", _ecr_images_argv(row["name"]), _ecr_images),
        ("Lifecycle policy", ["ecr", "get-lifecycle-policy", *name], _ecr_lifecycle),
        ("Access policy", ["ecr", "get-repository-policy", *name], _ecr_policy),
    ]

    def one(lookup):
        label, argv, parse = lookup
        try:
            return parse(*run(argv))
        except (json.JSONDecodeError, TypeError, ValueError, AttributeError, KeyError):
            return [(label, "unknown — unexpected output")]

    with ThreadPoolExecutor(max_workers=3) as pool:
        return [pair for pairs in pool.map(one, lookups) for pair in pairs]


def ecr_login_command(row: dict, profile: str = "", region: str = "") -> str:
    """The line that signs Docker in to a repository's registry.

    Text to paste into a terminal: it has no secret in it — the password
    is made when the line is run, and goes straight to Docker.
    """
    registry = row.get("uri", "").split("/", 1)[0]
    args = ["aws", "ecr", "get-login-password"]
    if profile:
        args += ["--profile", profile]
    if region:
        args += ["--region", region]
    return (
        f"{shlex.join(args)} | docker login --username AWS --password-stdin {shlex.quote(registry)}"
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
                # Set for Aurora and Multi-AZ cluster members, whose
                # snapshots are taken of the cluster, not the instance.
                "cluster": db.get("DBClusterIdentifier") or "",
            }
        )
    return rows


# ── RDS snapshots ───────────────────────────────────────────────────

_SNAPSHOT_NAME = re.compile(r"[A-Za-z][A-Za-z0-9-]*")


def snapshot_name_limit(db: dict) -> int:
    """The longest snapshot name RDS takes for this row (shorter for clusters)."""
    return 63 if db.get("cluster") else 255


def default_snapshot_name(db: dict, when: float | None = None) -> str:
    """``orders-db-manual-20261112-2215`` — the instance, and when it was taken."""
    stamp = time.strftime("%Y%m%d-%H%M", time.localtime(when))
    suffix = f"-manual-{stamp}"
    base = (db.get("cluster") or db.get("id") or "db")[: snapshot_name_limit(db) - len(suffix)]
    return base.rstrip("-") + suffix


def snapshot_name_problem(name: str, db: dict) -> str:
    """Why RDS would refuse ``name`` as a snapshot identifier; "" when it is fine."""
    limit = snapshot_name_limit(db)
    if not name:
        return "Give the snapshot a name."
    if len(name) > limit:
        return f"A snapshot name can be at most {limit} characters."
    if not _SNAPSHOT_NAME.fullmatch(name):
        return "Use letters, digits and hyphens only, starting with a letter."
    if name.endswith("-") or "--" in name:
        return "A snapshot name can't end with a hyphen or have two in a row."
    return ""


def snapshot_command(db: dict, name: str) -> list[str]:
    """The ``aws rds`` arguments that take a manual snapshot of this row."""
    if db.get("cluster"):
        return [
            "rds",
            "create-db-cluster-snapshot",
            "--db-cluster-identifier",
            db["cluster"],
            "--db-cluster-snapshot-identifier",
            name,
        ]
    return [
        "rds",
        "create-db-snapshot",
        "--db-instance-identifier",
        db["id"],
        "--db-snapshot-identifier",
        name,
    ]


def list_snapshots_command(db: dict) -> list[str]:
    """The ``aws rds`` arguments that list this row's snapshots, manual and automated."""
    if db.get("cluster"):
        return [
            "rds",
            "describe-db-cluster-snapshots",
            "--db-cluster-identifier",
            db["cluster"],
            "--query",
            "DBClusterSnapshots[].{Snapshot:DBClusterSnapshotIdentifier,Status:Status,"
            "Type:SnapshotType,Created:SnapshotCreateTime,Progress:PercentProgress}",
        ]
    return [
        "rds",
        "describe-db-snapshots",
        "--db-instance-identifier",
        db["id"],
        "--query",
        "DBSnapshots[].{Snapshot:DBSnapshotIdentifier,Status:Status,Type:SnapshotType,"
        "Created:SnapshotCreateTime,Progress:PercentProgress}",
    ]


def _rds_master_password(db: dict) -> str:
    """Where the master user's password is kept — never the password.

    A password AWS manages rotates by itself, so a copy saved last week
    stops working: worth knowing when a login is refused.
    """
    if not db.get("MasterUsername"):
        return ""
    secret = db.get("MasterUserSecret") or {}
    if not secret.get("SecretArn"):
        return "set on the instance — AWS doesn't keep a readable copy"
    name = str(secret["SecretArn"]).rsplit(":secret:", 1)[-1]
    status = secret.get("SecretStatus") or ""
    state = f", {status} now" if status and status != "active" else ""
    return f"in Secrets Manager, rotated automatically{state} — secret {name}"


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
            ("Master user", db.get("MasterUsername")),
            ("Master password", _rds_master_password(db)),
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


# ── Networking ───────────────────────────────────────────────────────

#: The extra lines of a list in the details summary (subnets, rules,
#: listeners): past this many, the rest are counted, not shown.
_LIST_MAX = 20


def _name_tag(resource: dict) -> str:
    return next(
        (t.get("Value") or "" for t in resource.get("Tags") or [] if t.get("Key") == "Name"), ""
    )


def _listed(label: str, lines: list[str], none: str = "none") -> list[tuple[str, str]]:
    """``lines`` under one label: the first beside it, the rest below."""
    if not lines:
        return [(label, none)]
    shown = lines[:_LIST_MAX]
    if len(lines) > _LIST_MAX:
        shown.append(f"… and {len(lines) - _LIST_MAX} more")
    return [(label, shown[0]), *(("", line) for line in shown[1:])]


def _read(run: Run, argv: list[str]) -> tuple[dict | None, str]:
    """One more call for a summary: ``(answer, '')`` or ``(None, why not)``."""
    output, code = run(argv)
    if code != 0:
        return None, _unknown(output)
    try:
        data = json.loads(output or "{}")
    except ValueError:
        return None, "unknown — unexpected output"
    return (data, "") if isinstance(data, dict) else (None, "unknown — unexpected output")


def _read_all(run: Run, lookups: dict[str, list[str]]) -> dict[str, tuple[dict | None, str]]:
    with ThreadPoolExecutor(max_workers=4) as pool:
        answers = pool.map(lambda argv: _read(run, argv), lookups.values())
        return dict(zip(lookups, answers))


def _fetch_vpcs(call: Call) -> list[dict]:
    data = call(["ec2", "describe-vpcs", "--max-items", "200"])
    rows = []
    for vpc in (data or {}).get("Vpcs", []):
        vid, name = vpc.get("VpcId", ""), _name_tag(vpc)
        default = bool(vpc.get("IsDefault"))
        rows.append(
            {
                "id": vid,
                "name": name,
                "label": f"{name}  ({vid})" if name else vid,
                "status": vpc.get("State", ""),
                "info": f"{vpc.get('CidrBlock', '')}  {'default VPC' if default else ''}".strip(),
                "default": default,
            }
        )
    # The ones somebody made, before the one every region comes with.
    rows.sort(key=lambda r: (r["default"], r["label"].lower()))
    return rows


def _summarize_vpc(d: dict) -> list[tuple[str, str]]:
    try:
        vpc = d["Vpcs"][0]
    except (KeyError, IndexError, TypeError):
        return []
    v4 = [a.get("CidrBlock", "") for a in vpc.get("CidrBlockAssociationSet") or []]
    v6 = [a.get("Ipv6CidrBlock", "") for a in vpc.get("Ipv6CidrBlockAssociationSet") or []]
    return _kv(
        [
            ("Name", _name_tag(vpc)),
            ("VPC ID", vpc.get("VpcId")),
            ("State", vpc.get("State")),
            ("IPv4 range", ", ".join(filter(None, v4)) or vpc.get("CidrBlock")),
            ("IPv6 range", ", ".join(filter(None, v6))),
            ("Default VPC", bool(vpc.get("IsDefault"))),
            ("Tenancy", vpc.get("InstanceTenancy")),
            ("DHCP options", vpc.get("DhcpOptionsId")),
            ("Owner", vpc.get("OwnerId")),
        ]
    )


def _to_internet(table: dict) -> bool:
    return any(
        str(route.get("GatewayId") or "").startswith("igw-")
        and route.get("State", "active") == "active"
        for route in table.get("Routes") or []
    )


def _public_subnets(route_tables: list[dict]) -> Callable[[dict], bool]:
    """``is_public(subnet)``: its route table sends traffic to an internet gateway.

    A subnet uses the table it is associated with, or its VPC's main
    table when it has none of its own. (AWS's "auto-assign public IP"
    switch is not this: a subnet with it on and no such route still
    can't be reached.)
    """
    own: dict[str, bool] = {}
    main: dict[str, bool] = {}
    for table in route_tables:
        for association in table.get("Associations") or []:
            if association.get("SubnetId"):
                own[association["SubnetId"]] = _to_internet(table)
            elif association.get("Main"):
                main[table.get("VpcId", "")] = _to_internet(table)
    return lambda subnet: own.get(
        subnet.get("SubnetId", ""), main.get(subnet.get("VpcId", ""), False)
    )


def _vpc_facts(row: dict, run: Run) -> list[tuple[str, str]]:
    """What is in a VPC: its subnets (public or private) and its ways out."""
    in_vpc = f"Name=vpc-id,Values={row['id']}"
    found = _read_all(
        run,
        {
            "subnets": ["ec2", "describe-subnets", "--filters", in_vpc],
            "routes": ["ec2", "describe-route-tables", "--filters", in_vpc],
            "igw": [
                "ec2",
                "describe-internet-gateways",
                "--filters",
                f"Name=attachment.vpc-id,Values={row['id']}",
            ],
            # This one command spells it --filter.
            "nat": ["ec2", "describe-nat-gateways", "--filter", in_vpc],
        },
    )
    pairs: list[tuple[str, str]] = []

    data, problem = found["igw"]
    gateways = [g.get("InternetGatewayId", "") for g in (data or {}).get("InternetGateways", [])]
    pairs.append(("Internet gateway", problem or ", ".join(filter(None, gateways)) or "none"))

    data, problem = found["nat"]
    nats = []
    for nat in (data or {}).get("NatGateways", []):
        if nat.get("State") in ("deleted", "deleting", "failed"):
            continue
        ip = next((a.get("PublicIp") for a in nat.get("NatGatewayAddresses") or []), "")
        nats.append(
            f"{nat.get('NatGatewayId', '')} ({', '.join(filter(None, [nat.get('State'), ip]))})"
        )
    pairs.append(("NAT gateways", problem or ", ".join(nats) or "none"))

    data, problem = found["subnets"]
    if data is None:
        return [*pairs, ("Subnets", problem)]
    tables, _why = found["routes"]
    is_public = _public_subnets(tables.get("RouteTables", [])) if tables is not None else None
    subnets = sorted(
        data.get("Subnets", []),
        key=lambda s: (s.get("AvailabilityZone", ""), s.get("CidrBlock", "")),
    )
    lines, public = [], 0
    for subnet in subnets:
        sid = subnet.get("SubnetId", "")
        reach = ""
        if is_public is not None:
            reach = "public" if is_public(subnet) else "private"
            public += reach == "public"
        lines.append(
            "  ".join(
                filter(
                    None,
                    [
                        sid,
                        subnet.get("CidrBlock"),
                        subnet.get("AvailabilityZone"),
                        reach,
                        _name_tag(subnet),
                    ],
                )
            )
        )
    count = str(len(subnets))
    if subnets and is_public is not None:
        count += f" — {public} public, {len(subnets) - public} private"
    return [*pairs, ("Subnets", count), *(pair for pair in _listed("", lines) if lines)]


def _fetch_subnets(call: Call) -> list[dict]:
    data = call(["ec2", "describe-subnets", "--max-items", "200"])
    subnets = (data or {}).get("Subnets", [])
    # Public or private is in the route tables, not on the subnet. Best
    # effort: without them the column shows the subnet's state instead.
    tables = getattr(call, "quiet", call)(["ec2", "describe-route-tables"]) if subnets else None
    is_public = _public_subnets(tables.get("RouteTables", [])) if tables is not None else None
    rows = []
    for subnet in subnets:
        sid, name = subnet.get("SubnetId", ""), _name_tag(subnet)
        state = subnet.get("State", "")
        reach = ""
        if is_public is not None and state == "available":
            reach = "public" if is_public(subnet) else "private"
        rows.append(
            {
                "id": sid,
                "name": name,
                "label": f"{name}  ({sid})" if name else sid,
                "status": reach or state,
                "info": "  ".join(
                    filter(
                        None,
                        [
                            subnet.get("CidrBlock"),
                            subnet.get("AvailabilityZone"),
                            subnet.get("VpcId"),
                        ],
                    )
                ),
                "vpc": subnet.get("VpcId", ""),
                "zone": subnet.get("AvailabilityZone", ""),
            }
        )
    rows.sort(key=lambda r: (r["vpc"], r["zone"], r["label"].lower()))
    return rows


def _summarize_subnet(d: dict) -> list[tuple[str, str]]:
    try:
        subnet = d["Subnets"][0]
    except (KeyError, IndexError, TypeError):
        return []
    return _kv(
        [
            ("Name", _name_tag(subnet)),
            ("Subnet ID", subnet.get("SubnetId")),
            ("State", subnet.get("State")),
            ("VPC", subnet.get("VpcId")),
            ("Availability zone", subnet.get("AvailabilityZone")),
            ("IPv4 range", subnet.get("CidrBlock")),
            ("Free addresses", subnet.get("AvailableIpAddressCount")),
            ("Auto-assign public IP", bool(subnet.get("MapPublicIpOnLaunch"))),
            ("Default for its zone", bool(subnet.get("DefaultForAz"))),
        ]
    )


#: Where a route can send traffic, in the order worth showing.
_ROUTE_TARGETS = (
    "GatewayId",
    "NatGatewayId",
    "TransitGatewayId",
    "VpcPeeringConnectionId",
    "EgressOnlyInternetGatewayId",
    "NetworkInterfaceId",
    "InstanceId",
    "LocalGatewayId",
    "CarrierGatewayId",
    "CoreNetworkArn",
)


def _route_line(route: dict) -> str:
    """``0.0.0.0/0 → igw-0abc`` — one line of a route table."""
    to = (
        route.get("DestinationCidrBlock")
        or route.get("DestinationIpv6CidrBlock")
        or route.get("DestinationPrefixListId")
        or "?"
    )
    via = next((route[key] for key in _ROUTE_TARGETS if route.get(key)), "?")
    dead = "  (blackhole: the target is gone)" if route.get("State") == "blackhole" else ""
    return f"{to} → {via}{dead}"


def _subnet_facts(row: dict, run: Run) -> list[tuple[str, str]]:
    """Where a subnet's traffic can go: its route table, and what that makes it."""
    data, problem = _read(
        run,
        [
            "ec2",
            "describe-route-tables",
            "--filters",
            f"Name=association.subnet-id,Values={row['id']}",
        ],
    )
    if data is not None and not data.get("RouteTables") and row.get("vpc"):
        # No table of its own: it follows the VPC's main one.
        data, problem = _read(
            run,
            [
                "ec2",
                "describe-route-tables",
                "--filters",
                f"Name=vpc-id,Values={row['vpc']}",
                "Name=association.main,Values=true",
            ],
        )
    if data is None:
        return [("Routes", problem)]
    tables = data.get("RouteTables") or []
    if not tables:
        return [("Routes", "no route table found")]
    table = tables[0]
    routes = [r for r in table.get("Routes") or [] if isinstance(r, dict)]
    live = [r for r in routes if r.get("State", "active") == "active"]
    if _to_internet(table):
        reach = "public — it routes to an internet gateway"
    elif any(r.get("NatGatewayId") for r in live):
        reach = "private — it reaches the internet out through a NAT gateway only"
    else:
        reach = "private — no route to the internet"
    return [
        ("Reach", reach),
        ("Route table", table.get("RouteTableId", "")),
        *_listed("Routes", [_route_line(r) for r in routes]),
    ]


_LB_STATE = {
    "active": "accent_success_muted",
    "provisioning": "accent_warning",
    "active_impaired": "accent_warning",
    "failed": "accent_error",
}

#: Target groups whose health is looked up for one load balancer's details.
_LB_MAX_GROUPS = 8


def _fetch_load_balancers(call: Call) -> list[dict]:
    """Application, network and gateway load balancers (not Classic ones)."""
    data = call(["elbv2", "describe-load-balancers", "--max-items", "200"])
    rows = []
    for lb in (data or {}).get("LoadBalancers", []):
        name = lb.get("LoadBalancerName", "")
        rows.append(
            {
                "id": name,
                "name": name,
                "arn": lb.get("LoadBalancerArn", ""),
                "label": name,
                "status": (lb.get("State") or {}).get("Code", ""),
                "info": f"{lb.get('Type', '')}  {lb.get('Scheme', '')}".strip(),
                "dns": lb.get("DNSName", ""),
            }
        )
    rows.sort(key=lambda r: r["name"].lower())
    return rows


def _summarize_load_balancer(d: dict) -> list[tuple[str, str]]:
    try:
        lb = d["LoadBalancers"][0]
    except (KeyError, IndexError, TypeError):
        return []
    state = lb.get("State") or {}
    zones = [
        f"{z.get('ZoneName', '')} ({z.get('SubnetId', '')})"
        for z in lb.get("AvailabilityZones") or []
    ]
    return _kv(
        [
            ("Name", lb.get("LoadBalancerName")),
            ("Type", lb.get("Type")),
            ("Scheme", lb.get("Scheme")),
            ("State", " — ".join(filter(None, [state.get("Code"), state.get("Reason")]))),
            ("DNS name", lb.get("DNSName")),
            ("VPC", lb.get("VpcId")),
            ("Availability zones", ", ".join(sorted(zones))),
            ("Security groups", ", ".join(lb.get("SecurityGroups") or [])),
            ("IP address type", lb.get("IpAddressType")),
            ("Created", _ts(lb.get("CreatedTime"))),
        ]
    )


def _group_name(arn: str) -> str:
    """``…:targetgroup/web/73e2d6bc24d8a067`` → ``web``."""
    parts = str(arn or "").split("/")
    return parts[-2] if len(parts) >= 3 else str(arn or "")


def _listener_line(listener: dict) -> str:
    """``HTTPS:443 → web`` — where a listener sends what it receives."""
    front = f"{listener.get('Protocol', '')}:{listener.get('Port', '')}"
    actions = sorted(listener.get("DefaultActions") or [], key=lambda a: int(a.get("Order") or 0))
    if not actions:
        return front
    # Any authentication steps come first; the last is where it ends up.
    action = actions[-1]
    kind = action.get("Type", "")
    if kind == "forward":
        groups = [
            g.get("TargetGroupArn", "")
            for g in (action.get("ForwardConfig") or {}).get("TargetGroups") or []
        ] or [action.get("TargetGroupArn", "")]
        does = ", ".join(_group_name(arn) for arn in groups if arn) or "forward"
    elif kind == "redirect":
        to = action.get("RedirectConfig") or {}
        does = f"redirect to {to.get('Protocol', '')}:{to.get('Port', '')}"
    elif kind == "fixed-response":
        code = (action.get("FixedResponseConfig") or {}).get("StatusCode", "")
        does = f"fixed response {code}".strip()
    else:
        does = kind
    return f"{front} → {does}"


def _health_line(group: dict, health: dict | None, problem: str) -> str:
    """``web  HTTP:8080  2 healthy, 1 unhealthy — i-0abc: Health checks failed``."""
    where = f"{group.get('Protocol', '')}:{group.get('Port', '')}".strip(":")
    line = "  ".join(filter(None, [group.get("TargetGroupName", ""), where]))
    if health is None:
        return f"{line}  health {problem}"
    targets = health.get("TargetHealthDescriptions") or []
    if not targets:
        return f"{line}  no targets registered"
    states: dict[str, int] = {}
    unwell = []
    for target in targets:
        said = target.get("TargetHealth") or {}
        state = said.get("State", "unknown")
        states[state] = states.get(state, 0) + 1
        if state == "unhealthy":
            who = (target.get("Target") or {}).get("Id", "")
            why = said.get("Description") or said.get("Reason") or ""
            unwell.append(f"{who}: {why}" if why else who)
    # Healthy first, unhealthy next, then whatever else (draining, initial…).
    order = sorted(states, key=lambda s: ({"healthy": 0, "unhealthy": 1}.get(s, 2), s))
    line += "  " + ", ".join(f"{states[s]} {s}" for s in order)
    if unwell:
        more = f" (+{len(unwell) - 3} more)" if len(unwell) > 3 else ""
        line += " — " + "; ".join(unwell[:3]) + more
    return line


def _load_balancer_facts(row: dict, run: Run) -> list[tuple[str, str]]:
    """What a load balancer listens on, and whether what's behind it is healthy."""
    of_this = ["--load-balancer-arn", row["arn"]]
    found = _read_all(
        run,
        {
            "listeners": ["elbv2", "describe-listeners", *of_this],
            "groups": ["elbv2", "describe-target-groups", *of_this],
        },
    )
    data, problem = found["listeners"]
    if data is None:
        pairs = [("Listeners", problem)]
    else:
        listeners = sorted(data.get("Listeners", []), key=lambda li: int(li.get("Port") or 0))
        pairs = _listed("Listeners", [_listener_line(li) for li in listeners])

    data, problem = found["groups"]
    if data is None:
        return [*pairs, ("Targets", problem)]
    groups = sorted(data.get("TargetGroups", []), key=lambda g: g.get("TargetGroupName", ""))
    looked_at = groups[:_LB_MAX_GROUPS]
    health = _read_all(
        run,
        {
            g.get("TargetGroupArn", ""): [
                "elbv2",
                "describe-target-health",
                "--target-group-arn",
                g.get("TargetGroupArn", ""),
            ]
            for g in looked_at
        },
    )
    lines = [_health_line(g, *health[g.get("TargetGroupArn", "")]) for g in looked_at]
    if len(groups) > len(looked_at):
        lines.append(f"… and {len(groups) - len(looked_at)} more target groups")
    return [*pairs, *_listed("Targets", lines, none="no target groups")]


def _balancer_name(arn: str) -> str:
    """``…:loadbalancer/app/web/50dc6c495c0c9188`` → ``web``."""
    parts = str(arn or "").split("/")
    return parts[-2] if len(parts) >= 3 else str(arn or "")


def _count_health(row: dict, data: dict) -> None:
    targets = data.get("TargetHealthDescriptions") or []
    healthy = sum(1 for t in targets if (t.get("TargetHealth") or {}).get("State") == "healthy")
    row["targets"], row["healthy"] = len(targets), healthy
    row["status"] = f"{healthy}/{len(targets)} healthy" if targets else "no targets"


def _fetch_target_groups(call: Call) -> list[dict]:
    data = call(["elbv2", "describe-target-groups", "--max-items", "200"])
    rows = []
    for group in (data or {}).get("TargetGroups", []):
        name = group.get("TargetGroupName", "")
        where = f"{group.get('Protocol', '')}:{group.get('Port', '')}".strip(":")
        balancers = ", ".join(_balancer_name(a) for a in group.get("LoadBalancerArns") or [])
        rows.append(
            {
                "id": name,
                "name": name,
                "arn": group.get("TargetGroupArn", ""),
                "label": name,
                "status": "",
                "info": "  ".join(
                    filter(
                        None,
                        [where, group.get("TargetType"), f"← {balancers}" if balancers else ""],
                    )
                ),
            }
        )
    rows.sort(key=lambda r: r["name"].lower())
    _enrich(
        call,
        rows,
        lambda r: ["elbv2", "describe-target-health", "--target-group-arn", r["arn"]],
        _count_health,
    )
    return rows


def _target_group_colour(row: dict) -> str | None:
    targets, healthy = row.get("targets"), row.get("healthy")
    if targets is None:
        return None
    if not targets:
        return "text_muted"
    if healthy == targets:
        return "accent_success_muted"
    return "accent_warning" if healthy else "accent_error"


def _summarize_target_group(d: dict) -> list[tuple[str, str]]:
    try:
        group = d["TargetGroups"][0]
    except (KeyError, IndexError, TypeError):
        return []
    port = group.get("HealthCheckPort")
    matcher = group.get("Matcher") or {}
    expects = matcher.get("HttpCode") or matcher.get("GrpcCode")
    healthy, unhealthy = group.get("HealthyThresholdCount"), group.get("UnhealthyThresholdCount")
    check = ", ".join(
        filter(
            None,
            [
                " ".join(
                    filter(
                        None,
                        [
                            str(group.get("HealthCheckProtocol") or ""),
                            str(group.get("HealthCheckPath") or ""),
                            # "traffic-port": the port the targets are sent traffic on.
                            ("on the traffic port" if port == "traffic-port" else f"on port {port}")
                            if port
                            else "",
                        ],
                    )
                ),
                f"every {group['HealthCheckIntervalSeconds']}s"
                if group.get("HealthCheckIntervalSeconds")
                else "",
                f"expects {expects}" if expects else "",
            ],
        )
    )
    if group.get("HealthCheckEnabled") is False:
        check = "off"
    return _kv(
        [
            ("Name", group.get("TargetGroupName")),
            ("Sends to", f"{group.get('Protocol', '')}:{group.get('Port', '')}".strip(":")),
            ("Target type", group.get("TargetType")),
            ("VPC", group.get("VpcId")),
            (
                "Load balancer",
                ", ".join(_balancer_name(a) for a in group.get("LoadBalancerArns") or [])
                or "none — nothing sends traffic to this group",
            ),
            ("Health check", check),
            (
                "Thresholds",
                f"healthy after {healthy} checks, unhealthy after {unhealthy}"
                if healthy and unhealthy
                else "",
            ),
        ]
    )


def _target_group_facts(row: dict, run: Run) -> list[tuple[str, str]]:
    """Each target and its health, unhealthy ones first with the reason."""
    data, problem = _read(
        run, ["elbv2", "describe-target-health", "--target-group-arn", row["arn"]]
    )
    if data is None:
        return [("Targets", problem)]
    lines = []
    for target in data.get("TargetHealthDescriptions") or []:
        who = target.get("Target") or {}
        said = target.get("TargetHealth") or {}
        state = said.get("State", "unknown")
        name = f"{who.get('Id', '')}:{who['Port']}" if who.get("Port") else who.get("Id", "")
        why = said.get("Description") or said.get("Reason") or ""
        line = "  ".join(filter(None, [name, who.get("AvailabilityZone"), state]))
        lines.append((state == "healthy", f"{line} — {why}" if why else line))
    lines.sort()
    return _listed("Targets", [line for _well, line in lines], none="none registered")


_ANYWHERE = ("0.0.0.0/0", "::/0")
#: Ports a web server is expected to have open to everyone.
_WEB_PORTS = frozenset({80, 443})
_ALL_PORTS = range(0, 65536)


def _ports(permission: dict) -> str:
    """``tcp 443``, ``tcp 8000-8100``, ``tcp all ports``, ``all traffic``, ``icmp``."""
    protocol = str(permission.get("IpProtocol", ""))
    if protocol == "-1":
        return "all traffic"
    low, high = permission.get("FromPort"), permission.get("ToPort")
    if protocol not in ("tcp", "udp") or low is None or high is None:
        return protocol
    if low == high:
        return f"{protocol} {low}"
    if (low, high) == (_ALL_PORTS.start, _ALL_PORTS.stop - 1):
        return f"{protocol} all ports"
    return f"{protocol} {low}-{high}"


def _rule_lines(permissions: list[dict], word: str) -> list[str]:
    """One line per port range and peer: ``tcp 443  from 0.0.0.0/0  — https``."""
    lines = []
    for permission in permissions or []:
        ports = _ports(permission)
        peers = [
            *(
                (r.get("CidrIp", ""), r.get("Description"))
                for r in permission.get("IpRanges") or []
            ),
            *(
                (r.get("CidrIpv6", ""), r.get("Description"))
                for r in permission.get("Ipv6Ranges") or []
            ),
            *(
                (r.get("GroupId", ""), r.get("Description"))
                for r in permission.get("UserIdGroupPairs") or []
            ),
            *(
                (r.get("PrefixListId", ""), r.get("Description"))
                for r in permission.get("PrefixListIds") or []
            ),
        ]
        for peer, description in peers:
            line = f"{ports}  {word} {peer}"
            lines.append(f"{line}  — {description}" if description else line)
    return lines


def _open_to_anyone(group: dict) -> tuple[str, bool]:
    """What a group lets in from the whole internet: ``("open: 22, 443", risky)``.

    ``risky`` is anything beyond a web server's 80 and 443 — an SSH or
    database port the world can knock on is the thing worth a colour.
    """
    ports: set[str] = set()
    risky = everything = False
    for permission in group.get("IpPermissions") or []:
        from_anyone = any(
            r.get("CidrIp") in _ANYWHERE for r in permission.get("IpRanges") or []
        ) or any(r.get("CidrIpv6") in _ANYWHERE for r in permission.get("Ipv6Ranges") or [])
        if not from_anyone:
            continue
        what = _ports(permission)
        low, high = permission.get("FromPort"), permission.get("ToPort")
        if what == "all traffic" or what.endswith("all ports"):
            everything = True
        elif permission.get("IpProtocol") in ("tcp", "udp") and low is not None:
            ports.add(str(low) if low == high else f"{low}-{high}")
            risky = risky or not (low == high and low in _WEB_PORTS)
        else:
            ports.add(what)  # icmp and the like: reachable, not a way in
    if everything:
        return "open: everything", True
    if not ports:
        return "", False
    ordered = sorted(
        ports, key=lambda p: (not p[0].isdigit(), int(p.split("-")[0]) if p[0].isdigit() else 0, p)
    )
    return "open: " + ", ".join(ordered), risky


def _fetch_security_groups(call: Call) -> list[dict]:
    data = call(["ec2", "describe-security-groups", "--max-items", "200"])
    rows = []
    for group in (data or {}).get("SecurityGroups", []):
        gid, name = group.get("GroupId", ""), group.get("GroupName", "")
        status, risky = _open_to_anyone(group)
        rules_in = len(_rule_lines(group.get("IpPermissions"), "from"))
        rules_out = len(_rule_lines(group.get("IpPermissionsEgress"), "to"))
        rows.append(
            {
                "id": gid,
                "name": name,
                "label": f"{name}  ({gid})" if name else gid,
                "status": status,
                "info": f"{rules_in} in, {rules_out} out  {group.get('VpcId', '')}".strip(),
                "risky": risky,
            }
        )
    rows.sort(key=lambda r: r["label"].lower())
    return rows


def _summarize_security_group(d: dict) -> list[tuple[str, str]]:
    try:
        group = d["SecurityGroups"][0]
    except (KeyError, IndexError, TypeError):
        return []
    status, _risky = _open_to_anyone(group)
    return [
        *_kv(
            [
                ("Name", group.get("GroupName")),
                ("Group ID", group.get("GroupId")),
                ("Description", group.get("Description")),
                ("VPC", group.get("VpcId")),
                ("From anywhere", status.removeprefix("open: ") or "nothing"),
            ]
        ),
        *_listed("Inbound", _rule_lines(group.get("IpPermissions"), "from"), none="nothing"),
        *_listed("Outbound", _rule_lines(group.get("IpPermissionsEgress"), "to"), none="nothing"),
    ]


def _security_group_facts(row: dict, run: Run) -> list[tuple[str, str]]:
    """What a group is attached to, through the network interfaces that use it."""
    data, problem = _read(
        run,
        [
            "ec2",
            "describe-network-interfaces",
            "--filters",
            f"Name=group-id,Values={row['id']}",
        ],
    )
    if data is None:
        return [("Used by", problem)]
    users: list[str] = []
    for interface in data.get("NetworkInterfaces", []):
        # An instance by its ID; anything else (a load balancer, a
        # database, an endpoint) by what AWS wrote on the interface.
        user = (interface.get("Attachment") or {}).get("InstanceId") or (
            interface.get("Description") or interface.get("NetworkInterfaceId", "")
        )
        if user and user not in users:
            users.append(user)
    shown = ", ".join(users[:8]) + (f" (+{len(users) - 8} more)" if len(users) > 8 else "")
    return [("Used by", shown or "nothing — no network interface uses it")]


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
        key="ecr",
        title="ECR repositories",
        group="Compute",
        fetch=_fetch_ecr,
        describe=lambda r: ["ecr", "describe-repositories", "--repository-names", r["name"]],
        summarize=_summarize_ecr,
        facts=_ecr_facts,
        # A name may have slashes in it (team/app); the console keeps them.
        console_url=lambda r, reg: _console(
            reg,
            f"ecr/repositories/private/{r['registry']}/{quote(r['name'])}?region={reg}"
            if r.get("registry")
            else f"ecr/repositories?region={reg}",
        ),
        status_colour=lambda r: "text_muted" if r.get("status") == "empty" else None,
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
        key="vpc",
        title="VPCs",
        group="Networking",
        heading="VPC",
        fetch=_fetch_vpcs,
        describe=lambda r: ["ec2", "describe-vpcs", "--vpc-ids", r["id"]],
        summarize=_summarize_vpc,
        facts=_vpc_facts,
        console_url=lambda r, reg: _console(
            reg, f"vpcconsole/home?region={reg}#VpcDetails:VpcId={r['id']}"
        ),
        status_colour=lambda r: {
            "available": "accent_success_muted",
            "pending": "accent_warning",
        }.get(r.get("status", "")),
    ),
    ResourceType(
        key="subnets",
        title="Subnets",
        group="Networking",
        heading="SUBNET",
        fetch=_fetch_subnets,
        describe=lambda r: ["ec2", "describe-subnets", "--subnet-ids", r["id"]],
        summarize=_summarize_subnet,
        facts=_subnet_facts,
        console_url=lambda r, reg: _console(
            reg, f"vpcconsole/home?region={reg}#SubnetDetails:subnetId={r['id']}"
        ),
    ),
    ResourceType(
        key="elb",
        title="Load balancers",
        group="Networking",
        heading="LOAD BALANCER",
        fetch=_fetch_load_balancers,
        describe=lambda r: [
            "elbv2",
            "describe-load-balancers",
            "--load-balancer-arns",
            r["arn"],
        ],
        summarize=_summarize_load_balancer,
        facts=_load_balancer_facts,
        console_url=lambda r, reg: _console(
            reg, f"ec2/home?region={reg}#LoadBalancer:loadBalancerArn={r['arn']}"
        ),
        status_colour=lambda r: _LB_STATE.get(r.get("status", "")),
    ),
    ResourceType(
        key="target_groups",
        title="Target groups",
        group="Networking",
        heading="TARGET GROUP",
        fetch=_fetch_target_groups,
        describe=lambda r: [
            "elbv2",
            "describe-target-groups",
            "--target-group-arns",
            r["arn"],
        ],
        summarize=_summarize_target_group,
        facts=_target_group_facts,
        console_url=lambda r, reg: _console(
            reg, f"ec2/home?region={reg}#TargetGroup:targetGroupArn={r['arn']}"
        ),
        status_colour=_target_group_colour,
    ),
    ResourceType(
        key="security_groups",
        title="Security groups",
        group="Networking",
        heading="SECURITY GROUP",
        fetch=_fetch_security_groups,
        describe=lambda r: ["ec2", "describe-security-groups", "--group-ids", r["id"]],
        summarize=_summarize_security_group,
        facts=_security_group_facts,
        console_url=lambda r, reg: _console(
            reg, f"ec2/home?region={reg}#SecurityGroup:groupId={r['id']}"
        ),
        # Only what goes beyond a web server's ports is worth the colour.
        status_colour=lambda r: "accent_warning" if r.get("risky") else None,
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
