"""The AWS panel's Networking sections: VPCs, subnets, load balancers, target groups, security groups.

No AWS calls: :class:`FakeAws` answers from a small made-up account in
the shapes the AWS CLI prints.
"""

from __future__ import annotations

import json

import pytest

from polyglot_ai.core import aws_cli, aws_resources
from polyglot_ai.core.aws_resources import BY_KEY, format_summary

LB_ARN = "arn:aws:elasticloadbalancing:eu-west-1:111122223333:loadbalancer/app/web/50dc6c495c0c9188"
TG = "arn:aws:elasticloadbalancing:eu-west-1:111122223333:targetgroup"
TG_API, TG_WEB, TG_IDLE = f"{TG}/api/1111", f"{TG}/web/2222", f"{TG}/idle/3333"


def _tags(name: str) -> list[dict]:
    return [{"Key": "team", "Value": "platform"}, {"Key": "Name", "Value": name}]


VPCS = [
    {"VpcId": "vpc-0def", "State": "available", "CidrBlock": "172.31.0.0/16", "IsDefault": True},
    {
        "VpcId": "vpc-0aaa",
        "State": "available",
        "CidrBlock": "10.10.0.0/16",
        "CidrBlockAssociationSet": [{"CidrBlock": "10.10.0.0/16"}, {"CidrBlock": "10.20.0.0/16"}],
        "IsDefault": False,
        "InstanceTenancy": "default",
        "DhcpOptionsId": "dopt-0abc",
        "OwnerId": "111122223333",
        "Tags": _tags("platform"),
    },
]

SUBNETS = [
    {
        "SubnetId": "subnet-0iso",
        "VpcId": "vpc-0aaa",
        "State": "available",
        "CidrBlock": "10.10.20.0/24",
        "AvailabilityZone": "eu-west-1b",
        "Tags": _tags("data-b"),
    },
    {
        "SubnetId": "subnet-0pub",
        "VpcId": "vpc-0aaa",
        "State": "available",
        "CidrBlock": "10.10.1.0/24",
        "AvailabilityZone": "eu-west-1a",
        "AvailableIpAddressCount": 250,
        "MapPublicIpOnLaunch": True,
        "Tags": _tags("public-a"),
    },
    {
        "SubnetId": "subnet-0prv",
        "VpcId": "vpc-0aaa",
        "State": "available",
        "CidrBlock": "10.10.10.0/24",
        "AvailabilityZone": "eu-west-1a",
        "Tags": _tags("app-a"),
    },
    {
        "SubnetId": "subnet-0dfl",
        "VpcId": "vpc-0def",
        "State": "available",
        "CidrBlock": "172.31.0.0/20",
        "AvailabilityZone": "eu-west-1a",
    },
]

LOCAL = {"DestinationCidrBlock": "10.10.0.0/16", "GatewayId": "local", "State": "active"}
ROUTE_TABLES = [
    {
        "RouteTableId": "rtb-0pub",
        "VpcId": "vpc-0aaa",
        "Associations": [{"Main": False, "SubnetId": "subnet-0pub"}],
        "Routes": [
            LOCAL,
            {"DestinationCidrBlock": "0.0.0.0/0", "GatewayId": "igw-0aaa", "State": "active"},
        ],
    },
    {
        "RouteTableId": "rtb-0prv",
        "VpcId": "vpc-0aaa",
        "Associations": [{"Main": False, "SubnetId": "subnet-0prv"}],
        "Routes": [
            LOCAL,
            {"DestinationCidrBlock": "0.0.0.0/0", "NatGatewayId": "nat-0aaa", "State": "active"},
        ],
    },
    {
        "RouteTableId": "rtb-0main",
        "VpcId": "vpc-0aaa",
        "Associations": [{"Main": True}],
        "Routes": [
            LOCAL,
            {
                "DestinationCidrBlock": "10.99.0.0/16",
                "VpcPeeringConnectionId": "pcx-0dead",
                "State": "blackhole",
            },
            # A gateway that has gone: the route is there, the way out isn't.
            {"DestinationCidrBlock": "0.0.0.0/0", "GatewayId": "igw-0gone", "State": "blackhole"},
        ],
    },
    {
        "RouteTableId": "rtb-0dfl",
        "VpcId": "vpc-0def",
        "Associations": [{"Main": True}],
        "Routes": [
            {"DestinationCidrBlock": "0.0.0.0/0", "GatewayId": "igw-0def", "State": "active"}
        ],
    },
]

IGWS = [
    {"InternetGatewayId": "igw-0aaa", "Attachments": [{"VpcId": "vpc-0aaa"}]},
    {"InternetGatewayId": "igw-0def", "Attachments": [{"VpcId": "vpc-0def"}]},
]
NATS = [
    {
        "NatGatewayId": "nat-0aaa",
        "VpcId": "vpc-0aaa",
        "State": "available",
        "NatGatewayAddresses": [{"PublicIp": "203.0.113.7"}],
    },
    {"NatGatewayId": "nat-0old", "VpcId": "vpc-0aaa", "State": "deleted"},
]

LOAD_BALANCERS = [
    {
        "LoadBalancerArn": LB_ARN,
        "LoadBalancerName": "web",
        "DNSName": "web-123.eu-west-1.elb.amazonaws.com",
        "Type": "application",
        "Scheme": "internet-facing",
        "State": {"Code": "active"},
        "VpcId": "vpc-0aaa",
        "AvailabilityZones": [
            {"ZoneName": "eu-west-1b", "SubnetId": "subnet-0iso"},
            {"ZoneName": "eu-west-1a", "SubnetId": "subnet-0pub"},
        ],
        "SecurityGroups": ["sg-0web"],
        "IpAddressType": "ipv4",
        "CreatedTime": "2026-10-01T09:30:00.123000+00:00",
    },
    {
        "LoadBalancerArn": LB_ARN.replace("app/web", "net/Internal"),
        "LoadBalancerName": "Internal",
        "Type": "network",
        "Scheme": "internal",
        "State": {"Code": "failed", "Reason": "No free addresses in subnet-0iso"},
    },
]

LISTENERS = [
    {
        "Port": 443,
        "Protocol": "HTTPS",
        "DefaultActions": [
            {"Type": "forward", "Order": 2, "TargetGroupArn": TG_API},
            {"Type": "authenticate-cognito", "Order": 1},
        ],
    },
    {
        "Port": 80,
        "Protocol": "HTTP",
        "DefaultActions": [
            {
                "Type": "redirect",
                "RedirectConfig": {"Protocol": "HTTPS", "Port": "443", "StatusCode": "HTTP_301"},
            }
        ],
    },
    {
        "Port": 8443,
        "Protocol": "HTTPS",
        "DefaultActions": [
            {
                "Type": "forward",
                "ForwardConfig": {
                    "TargetGroups": [{"TargetGroupArn": TG_API}, {"TargetGroupArn": TG_WEB}]
                },
            }
        ],
    },
    {
        "Port": 9000,
        "Protocol": "HTTP",
        "DefaultActions": [
            {"Type": "fixed-response", "FixedResponseConfig": {"StatusCode": "404"}}
        ],
    },
]

TARGET_GROUPS = [
    {
        "TargetGroupArn": TG_WEB,
        "TargetGroupName": "web",
        "Protocol": "HTTP",
        "Port": 8080,
        "TargetType": "instance",
        "VpcId": "vpc-0aaa",
        "LoadBalancerArns": [LB_ARN],
        "HealthCheckProtocol": "HTTP",
        "HealthCheckPath": "/health",
        "HealthCheckPort": "traffic-port",
        "HealthCheckIntervalSeconds": 30,
        "HealthyThresholdCount": 5,
        "UnhealthyThresholdCount": 2,
        "Matcher": {"HttpCode": "200-299"},
    },
    {
        "TargetGroupArn": TG_API,
        "TargetGroupName": "api",
        "Protocol": "HTTP",
        "Port": 80,
        "TargetType": "ip",
        "LoadBalancerArns": [LB_ARN],
    },
    {"TargetGroupArn": TG_IDLE, "TargetGroupName": "idle", "Protocol": "TCP", "Port": 5432},
]

HEALTH = {
    TG_API: [
        {"Target": {"Id": "10.10.10.5", "Port": 80}, "TargetHealth": {"State": "healthy"}},
        {"Target": {"Id": "10.10.10.6", "Port": 80}, "TargetHealth": {"State": "healthy"}},
    ],
    TG_WEB: [
        {
            "Target": {"Id": "i-0good", "Port": 8080, "AvailabilityZone": "eu-west-1a"},
            "TargetHealth": {"State": "healthy"},
        },
        {
            "Target": {"Id": "i-0bad", "Port": 8080},
            "TargetHealth": {
                "State": "unhealthy",
                "Reason": "Target.ResponseCodeMismatch",
                "Description": "Health checks failed with these codes: [502]",
            },
        },
        {
            "Target": {"Id": "i-0going", "Port": 8080},
            "TargetHealth": {"State": "draining", "Reason": "Target.DeregistrationInProgress"},
        },
    ],
    TG_IDLE: [],
}


def _from(cidr: str, description: str = "") -> dict:
    return {"CidrIp": cidr, **({"Description": description} if description else {})}


ALL_OUT = [{"IpProtocol": "-1", "IpRanges": [_from("0.0.0.0/0")]}]
SECURITY_GROUPS = [
    {
        "GroupId": "sg-0web",
        "GroupName": "web",
        "Description": "public web",
        "VpcId": "vpc-0aaa",
        "IpPermissions": [
            {"IpProtocol": "tcp", "FromPort": 443, "ToPort": 443, "IpRanges": [_from("0.0.0.0/0")]},
            {
                "IpProtocol": "tcp",
                "FromPort": 80,
                "ToPort": 80,
                "IpRanges": [_from("0.0.0.0/0")],
                "Ipv6Ranges": [{"CidrIpv6": "::/0"}],
            },
        ],
        "IpPermissionsEgress": ALL_OUT,
    },
    {
        "GroupId": "sg-0ssh",
        "GroupName": "bastion",
        "Description": "jump host",
        "VpcId": "vpc-0aaa",
        "IpPermissions": [
            {
                "IpProtocol": "tcp",
                "FromPort": 22,
                "ToPort": 22,
                "IpRanges": [_from("0.0.0.0/0", "ssh from anywhere"), _from("198.51.100.0/24")],
            },
            {"IpProtocol": "tcp", "FromPort": 443, "ToPort": 443, "IpRanges": [_from("0.0.0.0/0")]},
            {
                "IpProtocol": "tcp",
                "FromPort": 8000,
                "ToPort": 8100,
                "Ipv6Ranges": [{"CidrIpv6": "::/0"}],
            },
            {"IpProtocol": "icmp", "FromPort": -1, "ToPort": -1, "IpRanges": [_from("0.0.0.0/0")]},
        ],
        "IpPermissionsEgress": ALL_OUT,
    },
    {
        "GroupId": "sg-0db",
        "GroupName": "database",
        "Description": "sql server",
        "VpcId": "vpc-0aaa",
        "IpPermissions": [
            {
                "IpProtocol": "tcp",
                "FromPort": 1433,
                "ToPort": 1433,
                "UserIdGroupPairs": [{"GroupId": "sg-0ssh", "Description": "from the bastion"}],
                "PrefixListIds": [{"PrefixListId": "pl-0abc"}],
            }
        ],
        "IpPermissionsEgress": [],
    },
    {
        "GroupId": "sg-0any",
        "GroupName": "wide-open",
        "VpcId": "vpc-0def",
        "IpPermissions": [{"IpProtocol": "-1", "IpRanges": [_from("0.0.0.0/0")]}],
        "IpPermissionsEgress": ALL_OUT,
    },
    {
        "GroupId": "sg-0tcp",
        "GroupName": "every-port",
        "VpcId": "vpc-0def",
        "IpPermissions": [
            {
                "IpProtocol": "tcp",
                "FromPort": 0,
                "ToPort": 65535,
                "IpRanges": [_from("203.0.113.0/24")],
            }
        ],
        "IpPermissionsEgress": ALL_OUT,
    },
]

INTERFACES = {
    "sg-0db": [
        {"NetworkInterfaceId": "eni-1", "Description": "RDSNetworkInterface"},
        {"NetworkInterfaceId": "eni-2", "Description": "RDSNetworkInterface"},
    ],
    "sg-0ssh": [
        {"NetworkInterfaceId": "eni-3", "Description": "", "Attachment": {"InstanceId": "i-0jump"}},
        {"NetworkInterfaceId": "eni-4", "Description": ""},
    ],
}

DENIED = (
    "Error: Access denied — this profile lacks permission for that call.",
    254,
)


class FakeAws:
    """``run(argv) -> (output, code)`` and ``call(argv) -> dict | None`` over the account above."""

    def __init__(self, deny: set[tuple[str, str]] | None = None) -> None:
        self.deny = deny or set()
        self.calls: list[list[str]] = []

    @staticmethod
    def _filters(argv: list[str]) -> dict[str, str]:
        found = {}
        for token in argv:
            if token.startswith("Name="):
                name, _, values = token.removeprefix("Name=").partition(",Values=")
                found[name] = values
        return found

    @staticmethod
    def _value(argv: list[str], option: str) -> str:
        return argv[argv.index(option) + 1] if option in argv else ""

    def answer(self, argv: list[str]) -> dict:
        operation, filters = tuple(argv[:2]), self._filters(argv)
        vpc = filters.get("vpc-id")
        if operation == ("ec2", "describe-vpcs"):
            wanted = self._value(argv, "--vpc-ids")
            return {"Vpcs": [v for v in VPCS if not wanted or v["VpcId"] == wanted]}
        if operation == ("ec2", "describe-subnets"):
            wanted = self._value(argv, "--subnet-ids")
            return {
                "Subnets": [
                    s
                    for s in SUBNETS
                    if (not wanted or s["SubnetId"] == wanted) and (not vpc or s["VpcId"] == vpc)
                ]
            }
        if operation == ("ec2", "describe-route-tables"):
            tables = [t for t in ROUTE_TABLES if not vpc or t["VpcId"] == vpc]
            if "association.subnet-id" in filters:
                tables = [
                    t
                    for t in tables
                    if any(
                        a.get("SubnetId") == filters["association.subnet-id"]
                        for a in t["Associations"]
                    )
                ]
            if filters.get("association.main") == "true":
                tables = [t for t in tables if any(a.get("Main") for a in t["Associations"])]
            return {"RouteTables": tables}
        if operation == ("ec2", "describe-internet-gateways"):
            wanted = filters.get("attachment.vpc-id")
            return {"InternetGateways": [g for g in IGWS if g["Attachments"][0]["VpcId"] == wanted]}
        if operation == ("ec2", "describe-nat-gateways"):
            assert "--filter" in argv, "describe-nat-gateways takes --filter, not --filters"
            return {"NatGateways": [n for n in NATS if n["VpcId"] == vpc]}
        if operation == ("ec2", "describe-security-groups"):
            wanted = self._value(argv, "--group-ids")
            return {
                "SecurityGroups": [
                    g for g in SECURITY_GROUPS if not wanted or g["GroupId"] == wanted
                ]
            }
        if operation == ("ec2", "describe-network-interfaces"):
            return {"NetworkInterfaces": INTERFACES.get(filters.get("group-id", ""), [])}
        if operation == ("elbv2", "describe-load-balancers"):
            wanted = self._value(argv, "--load-balancer-arns")
            return {
                "LoadBalancers": [
                    b for b in LOAD_BALANCERS if not wanted or b["LoadBalancerArn"] == wanted
                ]
            }
        if operation == ("elbv2", "describe-listeners"):
            mine = self._value(argv, "--load-balancer-arn") == LB_ARN
            return {"Listeners": LISTENERS if mine else []}
        if operation == ("elbv2", "describe-target-groups"):
            of, wanted = (
                self._value(argv, "--load-balancer-arn"),
                self._value(argv, "--target-group-arns"),
            )
            return {
                "TargetGroups": [
                    g
                    for g in TARGET_GROUPS
                    if (not wanted or g["TargetGroupArn"] == wanted)
                    and (not of or of in g.get("LoadBalancerArns", []))
                ]
            }
        if operation == ("elbv2", "describe-target-health"):
            return {"TargetHealthDescriptions": HEALTH[self._value(argv, "--target-group-arn")]}
        raise AssertionError(f"unexpected call: {argv}")

    def run(self, argv: list[str], **_kw) -> tuple[str, int]:
        self.calls.append(list(argv))
        if tuple(argv[:2]) in self.deny:
            return DENIED
        return json.dumps(self.answer(argv)), 0

    def call(self, argv: list[str], **_kw) -> dict | None:
        output, code = self.run(argv)
        return json.loads(output) if code == 0 else None


def _details(key: str, row: dict, aws: FakeAws) -> dict[str, list[str]]:
    """The details summary as the panel builds it: ``{label: [lines]}``."""
    rt = BY_KEY[key]
    summary = rt.summarize(json.loads(aws.run(rt.describe(row))[0]))
    pairs = [*summary, *rt.facts(row, aws.run)]
    out: dict[str, list[str]] = {}
    label = ""
    for name, value in pairs:
        label = name or label
        out.setdefault(label, []).append(value)
    return out


def _row(key: str, ident: str, aws: FakeAws | None = None) -> dict:
    rows = BY_KEY[key].fetch((aws or FakeAws()).call)
    return next(r for r in rows if r["id"] == ident)


def _table(key: str, aws: FakeAws | None = None) -> list[tuple[str, str, str]]:
    rows = BY_KEY[key].fetch((aws or FakeAws()).call)
    return [(r["label"], r["status"], r["info"]) for r in rows]


# ── VPCs ────────────────────────────────────────────────────────────


def test_vpcs_are_listed_with_their_range_and_the_default_one_last():
    assert _table("vpc") == [
        ("platform  (vpc-0aaa)", "available", "10.10.0.0/16"),
        ("vpc-0def", "available", "172.31.0.0/16  default VPC"),
    ]
    colour = BY_KEY["vpc"].status_colour
    assert colour({"status": "available"}) == "accent_success_muted"
    assert colour({"status": "pending"}) == "accent_warning"


def test_the_default_vpc_is_last_whatever_it_is_called():
    def call(_argv, **_kw):
        return {
            "Vpcs": [
                {"VpcId": "vpc-0001", "IsDefault": True, "Tags": _tags("a default")},
                {"VpcId": "vpc-0002", "IsDefault": False},
            ]
        }

    assert [r["id"] for r in BY_KEY["vpc"].fetch(call)] == ["vpc-0002", "vpc-0001"]


def test_a_vpcs_details_say_what_is_in_it():
    aws = FakeAws()
    details = _details("vpc", _row("vpc", "vpc-0aaa"), aws)
    assert details["Name"] == ["platform"] and details["VPC ID"] == ["vpc-0aaa"]
    assert details["IPv4 range"] == ["10.10.0.0/16, 10.20.0.0/16"]
    assert details["Default VPC"] == ["no"]
    assert details["Internet gateway"] == ["igw-0aaa"]
    assert details["NAT gateways"] == ["nat-0aaa (available, 203.0.113.7)"]  # not the deleted one
    assert details["Subnets"] == [
        "3 — 1 public, 2 private",
        "subnet-0pub  10.10.1.0/24  eu-west-1a  public  public-a",
        "subnet-0prv  10.10.10.0/24  eu-west-1a  private  app-a",
        # No table of its own: the main one, whose internet route is dead.
        "subnet-0iso  10.10.20.0/24  eu-west-1b  private  data-b",
    ]
    assert list(details)[-1] == "Subnets"


def test_a_default_vpc_and_one_with_nothing_in_it():
    details = _details("vpc", _row("vpc", "vpc-0def"), FakeAws())
    assert details["Default VPC"] == ["yes"] and details["IPv4 range"] == ["172.31.0.0/16"]
    assert details["NAT gateways"] == ["none"]
    assert details["Subnets"] == [
        "1 — 1 public, 0 private",
        "subnet-0dfl  172.31.0.0/20  eu-west-1a  public",
    ]
    empty = BY_KEY["vpc"].facts({"id": "vpc-0new"}, FakeAws().run)
    assert empty == [("Internet gateway", "none"), ("NAT gateways", "none"), ("Subnets", "0")]


def test_vpc_details_without_some_permissions_show_what_they_can():
    no_routes = FakeAws({("ec2", "describe-route-tables"), ("ec2", "describe-nat-gateways")})
    details = _details("vpc", _row("vpc", "vpc-0aaa"), no_routes)
    assert details["NAT gateways"] == [
        "unknown — Access denied — this profile lacks permission for that call."
    ]
    # Not guessed: without the route tables a subnet is neither public nor private.
    assert details["Subnets"][:2] == ["3", "subnet-0pub  10.10.1.0/24  eu-west-1a  public-a"]

    no_subnets = FakeAws({("ec2", "describe-subnets")})
    details = _details("vpc", _row("vpc", "vpc-0aaa"), no_subnets)
    assert details["Internet gateway"] == ["igw-0aaa"]
    assert details["Subnets"] == [
        "unknown — Access denied — this profile lacks permission for that call."
    ]


# ── Subnets ─────────────────────────────────────────────────────────


def test_subnets_are_listed_as_public_or_private_by_their_route_table():
    assert _table("subnets") == [
        ("app-a  (subnet-0prv)", "private", "10.10.10.0/24  eu-west-1a  vpc-0aaa"),
        ("public-a  (subnet-0pub)", "public", "10.10.1.0/24  eu-west-1a  vpc-0aaa"),
        ("data-b  (subnet-0iso)", "private", "10.10.20.0/24  eu-west-1b  vpc-0aaa"),
        # Follows its own VPC's main table, not the other VPC's.
        ("subnet-0dfl", "public", "172.31.0.0/20  eu-west-1a  vpc-0def"),
    ]


def test_subnets_without_the_route_tables_show_their_state():
    aws = FakeAws({("ec2", "describe-route-tables")})
    assert {status for _label, status, _info in _table("subnets", aws)} == {"available"}


def test_a_subnet_still_being_made_shows_that(monkeypatch):
    monkeypatch.setitem(SUBNETS[0], "State", "pending")
    assert _row("subnets", "subnet-0iso")["status"] == "pending"
    assert _row("subnets", "subnet-0pub")["status"] == "public"


def test_no_subnets_means_no_second_call():
    calls = []

    def call(argv, **_kw):
        calls.append(argv[:2])
        return {"Subnets": []}

    assert BY_KEY["subnets"].fetch(call) == []
    assert calls == [["ec2", "describe-subnets"]]


@pytest.mark.parametrize(
    ("subnet", "reach", "table", "routes"),
    [
        (
            "subnet-0pub",
            "public — it routes to an internet gateway",
            "rtb-0pub",
            ["10.10.0.0/16 → local", "0.0.0.0/0 → igw-0aaa"],
        ),
        (
            "subnet-0prv",
            "private — it reaches the internet out through a NAT gateway only",
            "rtb-0prv",
            ["10.10.0.0/16 → local", "0.0.0.0/0 → nat-0aaa"],
        ),
        (
            "subnet-0iso",
            "private — no route to the internet",
            "rtb-0main",
            [
                "10.10.0.0/16 → local",
                "10.99.0.0/16 → pcx-0dead  (blackhole: the target is gone)",
                "0.0.0.0/0 → igw-0gone  (blackhole: the target is gone)",
            ],
        ),
    ],
)
def test_a_subnets_details_show_where_its_traffic_can_go(subnet, reach, table, routes):
    aws = FakeAws()
    details = _details("subnets", _row("subnets", subnet), aws)
    assert details["Subnet ID"] == [subnet] and details["VPC"] == ["vpc-0aaa"]
    assert details["Reach"] == [reach]
    assert details["Route table"] == [table]
    assert details["Routes"] == routes


def test_subnet_summary_and_what_it_says_without_permission():
    aws = FakeAws()
    details = _details("subnets", _row("subnets", "subnet-0pub"), aws)
    assert details["Name"] == ["public-a"] and details["Free addresses"] == ["250"]
    assert details["Auto-assign public IP"] == ["yes"]
    denied = BY_KEY["subnets"].facts(
        {"id": "subnet-0pub", "vpc": "vpc-0aaa"}, FakeAws({("ec2", "describe-route-tables")}).run
    )
    assert denied == [
        ("Routes", "unknown — Access denied — this profile lacks permission for that call.")
    ]
    assert BY_KEY["subnets"].facts({"id": "subnet-0none", "vpc": "vpc-0new"}, aws.run) == [
        ("Routes", "no route table found")
    ]


# ── Load balancers ──────────────────────────────────────────────────


def test_load_balancers_are_listed_with_state_type_and_scheme():
    assert _table("elb") == [
        ("Internal", "failed", "network  internal"),
        ("web", "active", "application  internet-facing"),
    ]
    colour = BY_KEY["elb"].status_colour
    assert colour({"status": "active"}) == "accent_success_muted"
    assert colour({"status": "failed"}) == "accent_error"
    assert colour({"status": "provisioning"}) == "accent_warning"
    assert _row("elb", "web")["dns"] == "web-123.eu-west-1.elb.amazonaws.com"


def test_a_load_balancers_details_show_listeners_and_target_health():
    aws = FakeAws()
    details = _details("elb", _row("elb", "web"), aws)
    assert details["Type"] == ["application"] and details["Scheme"] == ["internet-facing"]
    assert details["DNS name"] == ["web-123.eu-west-1.elb.amazonaws.com"]
    assert details["Availability zones"] == ["eu-west-1a (subnet-0pub), eu-west-1b (subnet-0iso)"]
    assert details["Security groups"] == ["sg-0web"]
    assert details["Created"] == ["2026-10-01 09:30:00"]
    assert details["Listeners"] == [
        "HTTP:80 → redirect to HTTPS:443",
        "HTTPS:443 → api",  # after signing in, which comes first
        "HTTPS:8443 → api, web",
        "HTTP:9000 → fixed response 404",
    ]
    assert details["Targets"] == [
        "api  HTTP:80  2 healthy",
        "web  HTTP:8080  1 healthy, 1 unhealthy, 1 draining — "
        "i-0bad: Health checks failed with these codes: [502]",
    ]


def test_a_load_balancer_with_nothing_behind_it_and_one_that_failed():
    failed = _row("elb", "Internal")
    details = _details("elb", failed, FakeAws())
    assert details["State"] == ["failed — No free addresses in subnet-0iso"]
    assert details["Listeners"] == ["none"] and details["Targets"] == ["no target groups"]

    denied = FakeAws({("elbv2", "describe-listeners"), ("elbv2", "describe-target-health")})
    details = _details("elb", _row("elb", "web"), denied)
    assert details["Listeners"][0].startswith("unknown — Access denied")
    assert details["Targets"][0].startswith("api  HTTP:80  health unknown — Access denied")


def test_a_target_group_with_nothing_registered_says_so():
    line = aws_resources._health_line(TARGET_GROUPS[2], {"TargetHealthDescriptions": []}, "")
    assert line == "idle  TCP:5432  no targets registered"


def test_only_the_first_target_groups_have_their_health_looked_up(monkeypatch):
    monkeypatch.setattr(aws_resources, "_LB_MAX_GROUPS", 1)
    aws = FakeAws()
    details = _details("elb", _row("elb", "web"), aws)
    assert details["Targets"] == ["api  HTTP:80  2 healthy", "… and 1 more target groups"]
    assert sum(argv[1] == "describe-target-health" for argv in aws.calls) == 1


# ── Target groups ───────────────────────────────────────────────────


def test_target_groups_are_listed_with_how_many_targets_are_healthy():
    assert _table("target_groups") == [
        ("api", "2/2 healthy", "HTTP:80  ip  ← web"),
        ("idle", "no targets", "TCP:5432"),
        ("web", "1/3 healthy", "HTTP:8080  instance  ← web"),
    ]
    colour = BY_KEY["target_groups"].status_colour
    rows = {r["name"]: r for r in BY_KEY["target_groups"].fetch(FakeAws().call)}
    assert colour(rows["api"]) == "accent_success_muted"
    assert colour(rows["web"]) == "accent_warning"
    assert colour(rows["idle"]) == "text_muted"
    assert colour({"targets": 2, "healthy": 0}) == "accent_error"


def test_target_groups_without_the_health_permission_are_still_listed():
    aws = FakeAws({("elbv2", "describe-target-health")})
    assert [status for _label, status, _info in _table("target_groups", aws)] == ["", "", ""]
    assert BY_KEY["target_groups"].status_colour({"status": ""}) is None


def test_a_target_groups_details_list_each_target_unhealthy_first():
    aws = FakeAws()
    details = _details("target_groups", _row("target_groups", "web"), aws)
    assert details["Sends to"] == ["HTTP:8080"] and details["Target type"] == ["instance"]
    assert details["Load balancer"] == ["web"]
    assert details["Health check"] == [
        "HTTP /health on the traffic port, every 30s, expects 200-299"
    ]
    assert details["Thresholds"] == ["healthy after 5 checks, unhealthy after 2"]
    assert details["Targets"] == [
        "i-0bad:8080  unhealthy — Health checks failed with these codes: [502]",
        "i-0going:8080  draining — Target.DeregistrationInProgress",
        "i-0good:8080  eu-west-1a  healthy",
    ]


def test_health_checks_on_a_port_of_their_own_or_switched_off():
    summarize = BY_KEY["target_groups"].summarize
    own_port = {"TargetGroupName": "g", "HealthCheckProtocol": "TCP", "HealthCheckPort": "9000"}
    assert dict(summarize({"TargetGroups": [own_port]}))["Health check"] == "TCP on port 9000"
    grpc = {"HealthCheckProtocol": "HTTP", "HealthCheckPath": "/", "Matcher": {"GrpcCode": "12"}}
    assert dict(summarize({"TargetGroups": [grpc]}))["Health check"] == "HTTP /, expects 12"
    off = dict(own_port, HealthCheckEnabled=False)
    assert dict(summarize({"TargetGroups": [off]}))["Health check"] == "off"
    # No label is wider than the summary's column, or the values stop lining up.
    for key in NETWORKING:
        row = BY_KEY[key].fetch(FakeAws().call)[0]
        assert max(len(label) for label in _details(key, row, FakeAws())) <= 24, key


def test_a_target_group_nothing_uses():
    details = _details("target_groups", _row("target_groups", "idle"), FakeAws())
    assert details["Load balancer"] == ["none — nothing sends traffic to this group"]
    assert details["Targets"] == ["none registered"]
    assert "Health check" not in details
    denied = BY_KEY["target_groups"].facts(
        {"arn": TG_WEB}, FakeAws({("elbv2", "describe-target-health")}).run
    )
    assert denied[0][0] == "Targets" and denied[0][1].startswith("unknown — Access denied")


# ── Security groups ─────────────────────────────────────────────────


def test_security_groups_show_what_the_whole_internet_can_reach():
    rows = {r["id"]: r for r in BY_KEY["security_groups"].fetch(FakeAws().call)}
    assert [(r["label"], r["status"], r["info"]) for r in rows.values()] == [
        ("bastion  (sg-0ssh)", "open: 22, 443, 8000-8100, icmp", "5 in, 1 out  vpc-0aaa"),
        ("database  (sg-0db)", "", "2 in, 0 out  vpc-0aaa"),
        ("every-port  (sg-0tcp)", "", "1 in, 1 out  vpc-0def"),
        ("web  (sg-0web)", "open: 80, 443", "3 in, 1 out  vpc-0aaa"),
        ("wide-open  (sg-0any)", "open: everything", "1 in, 1 out  vpc-0def"),
    ]
    colour = BY_KEY["security_groups"].status_colour
    # A web server's ports are meant to be open; SSH to the world is worth a look.
    assert colour(rows["sg-0web"]) is None and colour(rows["sg-0db"]) is None
    assert colour(rows["sg-0ssh"]) == "accent_warning"
    assert colour(rows["sg-0any"]) == "accent_warning"


def test_a_security_groups_details_list_every_rule_and_what_uses_it():
    aws = FakeAws()
    details = _details("security_groups", _row("security_groups", "sg-0ssh"), aws)
    assert details["Name"] == ["bastion"] and details["Description"] == ["jump host"]
    assert details["From anywhere"] == ["22, 443, 8000-8100, icmp"]
    assert details["Inbound"] == [
        "tcp 22  from 0.0.0.0/0  — ssh from anywhere",
        "tcp 22  from 198.51.100.0/24",
        "tcp 443  from 0.0.0.0/0",
        "tcp 8000-8100  from ::/0",
        "icmp  from 0.0.0.0/0",
    ]
    assert details["Outbound"] == ["all traffic  to 0.0.0.0/0"]
    assert details["Used by"] == ["i-0jump, eni-4"]


def test_rules_between_groups_and_a_group_with_no_way_out():
    details = _details("security_groups", _row("security_groups", "sg-0db"), FakeAws())
    assert details["From anywhere"] == ["nothing"]
    assert details["Inbound"] == [
        "tcp 1433  from sg-0ssh  — from the bastion",
        "tcp 1433  from pl-0abc",
    ]
    assert details["Outbound"] == ["nothing"]
    assert details["Used by"] == ["RDSNetworkInterface"]  # said once, not per interface

    wide = _details("security_groups", _row("security_groups", "sg-0tcp"), FakeAws())
    assert wide["Inbound"] == ["tcp all ports  from 203.0.113.0/24"]
    assert wide["Used by"] == ["nothing — no network interface uses it"]

    denied = BY_KEY["security_groups"].facts(
        {"id": "sg-0db"}, FakeAws({("ec2", "describe-network-interfaces")}).run
    )
    assert denied[0][1].startswith("unknown — Access denied")


def test_many_users_and_many_rules_are_counted_not_all_shown(monkeypatch):
    monkeypatch.setitem(
        INTERFACES,
        "sg-0web",
        [{"NetworkInterfaceId": f"eni-{i}", "Description": f"ELB app/web/{i}"} for i in range(11)],
    )
    used = BY_KEY["security_groups"].facts({"id": "sg-0web"}, FakeAws().run)
    assert used[0][1].endswith("ELB app/web/7 (+3 more)")

    lines = [f"tcp {port}  from 10.0.0.0/8" for port in range(25)]
    listed = aws_resources._listed("Inbound", lines)
    assert len(listed) == 21 and listed[0] == ("Inbound", lines[0])
    assert listed[19] == ("", lines[19]) and listed[20] == ("", "… and 5 more")


# ── All five ────────────────────────────────────────────────────────

NETWORKING = ("vpc", "subnets", "elb", "target_groups", "security_groups")


def test_the_networking_sections_only_read():
    """Listing and opening every row runs nothing that changes anything."""
    aws = FakeAws()
    for key in NETWORKING:
        rt = BY_KEY[key]
        assert rt.group == "Networking"
        for row in rt.fetch(aws.call):
            aws.run(rt.describe(row))
            rt.facts(row, aws.run)
    assert len(aws.calls) > 40
    assert {aws_cli.classify(argv) for argv in aws.calls} == {"read"}
    assert {argv[0] for argv in aws.calls} == {"ec2", "elbv2"}


def test_listings_are_capped_and_details_open_the_right_console_page():
    aws = FakeAws()
    for key in NETWORKING:
        BY_KEY[key].fetch(aws.call)
    listings = [argv for argv in aws.calls if "--max-items" in argv]
    assert len(listings) == 5 and all(argv[-2:] == ["--max-items", "200"] for argv in listings)

    region = "eu-west-1"
    urls = {
        key: BY_KEY[key].console_url(BY_KEY[key].fetch(FakeAws().call)[0], region)
        for key in NETWORKING
    }
    assert urls["vpc"].endswith("vpcconsole/home?region=eu-west-1#VpcDetails:VpcId=vpc-0aaa")
    assert urls["subnets"].endswith("#SubnetDetails:subnetId=subnet-0prv")
    assert urls["security_groups"].endswith(
        "ec2/home?region=eu-west-1#SecurityGroup:groupId=sg-0ssh"
    )
    assert urls["elb"].endswith(
        "#LoadBalancer:loadBalancerArn=" + LB_ARN.replace("app/web", "net/Internal")
    )
    assert urls["target_groups"].endswith("#TargetGroup:targetGroupArn=" + TG_API)


def test_an_answer_that_is_not_what_was_expected_is_not_a_crash():
    def odd(_argv):
        return "[1, 2]", 0

    def garbage(_argv):
        return "<html>", 0

    for run in (odd, garbage):
        assert BY_KEY["security_groups"].facts({"id": "sg-0"}, run) == [
            ("Used by", "unknown — unexpected output")
        ]
    for key in NETWORKING:
        assert BY_KEY[key].summarize({}) == []
        assert BY_KEY[key].fetch(lambda argv, **kw: None) == []


def test_summary_lists_line_up_under_their_label():
    text = format_summary([("Name", "web"), ("Inbound", "tcp 80"), ("", "tcp 443")])
    assert text.splitlines() == ["Name     web", "Inbound  tcp 80", "         tcp 443"]
