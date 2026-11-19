"""The AWS panel's ECR section: repositories, the images in them, and their policies.

No AWS calls: :class:`FakeEcr` answers from a small made-up registry in
the shapes the AWS CLI prints.
"""

from __future__ import annotations

import json

import pytest

from polyglot_ai.core import aws_cli, aws_resources
from polyglot_ai.core.aws_resources import BY_KEY, ecr_login_command

REGISTRY = "111122223333.dkr.ecr.eu-west-1.amazonaws.com"
DENIED = ("Error: Access denied — this profile lacks permission for that call.", 254)


def _repo(name: str, **more) -> dict:
    return {
        "repositoryName": name,
        "repositoryArn": f"arn:aws:ecr:eu-west-1:111122223333:repository/{name}",
        "repositoryUri": f"{REGISTRY}/{name}",
        "registryId": "111122223333",
        "createdAt": "2025-03-04T09:10:11.123000+00:00",
        "imageTagMutability": "MUTABLE",
        "imageScanningConfiguration": {"scanOnPush": False},
        "encryptionConfiguration": {"encryptionType": "AES256"},
        **more,
    }


REPOSITORIES = [
    _repo("web"),
    _repo(
        "team/api",
        imageTagMutability="IMMUTABLE",
        imageScanningConfiguration={"scanOnPush": True},
        encryptionConfiguration={
            "encryptionType": "KMS",
            "kmsKey": "arn:aws:kms:eu-west-1:111122223333:key/1234abcd",
        },
    ),
    _repo("Empty"),
]


def _image(digest: str, pushed, tags=(), size=52_400_000, **more) -> dict:
    image = {
        "imageDigest": "sha256:" + digest * 8,
        "imagePushedAt": pushed,
        "imageSizeInBytes": size,
        **more,
    }
    if tags:
        image["imageTags"] = list(tags)
    return image


IMAGES = {
    "web": [
        _image("aaaaaaaa", "2026-11-01T08:00:00+00:00", ["v1.0"]),
        _image(
            "bbbbbbbb",
            # The same moment as 10:30 UTC, written in another zone: newer than v1.0.
            "2026-11-10T21:30:00+11:00",
            ["v1.1", "latest"],
            size=182_400_000,
            imageScanStatus={"status": "COMPLETE"},
            imageScanFindingsSummary={
                "findingSeverityCounts": {"CRITICAL": 2, "HIGH": 5, "MEDIUM": 9, "LOW": 3}
            },
        ),
        _image("cccccccc", "2026-11-05T12:00:00+00:00"),
    ],
    "team/api": [
        _image(
            "dddddddd",
            "2026-10-02T03:04:05+00:00",
            ["2026.10.1"],
            imageScanFindingsSummary={"findingSeverityCounts": {}},
        )
    ],
    "Empty": [],
}

LIFECYCLE = {
    "rules": [
        {
            "rulePriority": 2,
            "selection": {
                "tagStatus": "tagged",
                "tagPrefixList": ["v", "release"],
                "countType": "imageCountMoreThan",
                "countNumber": 30,
            },
            "action": {"type": "expire"},
        },
        {
            "rulePriority": 1,
            "description": "clear out build leftovers",
            "selection": {
                "tagStatus": "untagged",
                "countType": "sinceImagePushed",
                "countUnit": "days",
                "countNumber": 14,
            },
            "action": {"type": "expire"},
        },
    ]
}

POLICY = {
    "Version": "2012-10-17",
    "Statement": [
        {
            "Sid": "pull",
            "Effect": "Allow",
            "Principal": {
                "AWS": ["arn:aws:iam::444455556666:root", "arn:aws:iam::777788889999:role/deploy"]
            },
            "Action": ["ecr:BatchGetImage", "ecr:GetDownloadUrlForLayer"],
        },
        {
            "Effect": "Allow",
            "Principal": {"Service": "codebuild.amazonaws.com"},
            "Action": ["ecr:PutImage", "ecr:InitiateLayerUpload", "ecr:BatchGetImage"],
        },
    ],
}


def _not_found(code: str, operation: str) -> tuple[str, int]:
    return f"Error: An error occurred ({code}) when calling the {operation} operation: none", 254


class FakeEcr:
    """``run(argv) -> (output, code)`` and ``call(argv) -> dict | None`` over the registry above."""

    def __init__(self, deny: set[str] | None = None, page: int = 1000) -> None:
        self.deny = deny or set()
        self.page = page
        self.calls: list[list[str]] = []

    @staticmethod
    def _value(argv: list[str], option: str) -> str:
        return argv[argv.index(option) + 1] if option in argv else ""

    def run(self, argv: list[str], **_kw) -> tuple[str, int]:
        self.calls.append(list(argv))
        assert argv[0] == "ecr", f"unexpected call: {argv}"
        operation, name = argv[1], self._value(argv, "--repository-name")
        if operation in self.deny:
            return DENIED
        if operation == "describe-repositories":
            wanted = self._value(argv, "--repository-names")
            found = [r for r in REPOSITORIES if not wanted or r["repositoryName"] == wanted]
            return json.dumps({"repositories": found}), 0
        if operation == "describe-images":
            assert argv[-3:] == ["--max-results", "1000", "--no-paginate"]
            images = IMAGES[name]
            answer = {"imageDetails": images[: self.page]}
            if len(images) > self.page:
                answer["nextToken"] = "more"
            return json.dumps(answer), 0
        if operation == "get-lifecycle-policy":
            if name != "web":
                return _not_found("LifecyclePolicyNotFoundException", "GetLifecyclePolicy")
            return json.dumps({"lifecyclePolicyText": json.dumps(LIFECYCLE)}), 0
        if operation == "get-repository-policy":
            if name != "web":
                return _not_found("RepositoryPolicyNotFoundException", "GetRepositoryPolicy")
            return json.dumps({"policyText": json.dumps(POLICY)}), 0
        raise AssertionError(f"unexpected call: {argv}")

    def call(self, argv: list[str], **_kw) -> dict | None:
        output, code = self.run(argv)
        return json.loads(output) if code == 0 else None


def _rows(aws: FakeEcr | None = None) -> dict[str, dict]:
    return {r["name"]: r for r in BY_KEY["ecr"].fetch((aws or FakeEcr()).call)}


def _details(name: str, aws: FakeEcr | None = None) -> dict[str, list[str]]:
    """The details summary as the panel builds it: ``{label: [lines]}``."""
    aws = aws or FakeEcr()
    rt, row = BY_KEY["ecr"], _rows()[name]
    pairs = [*rt.summarize(json.loads(aws.run(rt.describe(row))[0])), *rt.facts(row, aws.run)]
    out: dict[str, list[str]] = {}
    label = ""
    for field, value in pairs:
        label = field or label
        out.setdefault(label, []).append(value)
    return out


# ── The list ────────────────────────────────────────────────────────


def test_repositories_are_listed_with_their_images_and_the_latest_one():
    aws = FakeEcr()
    rows = BY_KEY["ecr"].fetch(aws.call)
    assert [(r["label"], r["status"], r["info"]) for r in rows] == [
        ("Empty", "empty", ""),
        ("team/api", "1 image", "latest: 2026.10.1  2026-10-02"),
        # Newest by the moment it was pushed, not by how the time is written;
        # and only findings that need attention are worth the room.
        ("web", "3 images", "latest: latest, v1.1  2026-11-10  2 critical, 5 high, 12 lower"),
    ]
    assert aws.calls[0] == ["ecr", "describe-repositories", "--max-items", "200"]
    assert BY_KEY["ecr"].group == "Compute"
    colour = BY_KEY["ecr"].status_colour
    assert colour(rows[0]) == "text_muted" and colour(rows[2]) is None


def test_a_row_carries_what_the_actions_need():
    row = _rows()["team/api"]
    assert row["id"] == "team/api"
    assert row["uri"] == f"{REGISTRY}/team/api"
    assert row["arn"].endswith(":repository/team/api")
    assert row["registry"] == "111122223333"


def test_a_repository_with_more_images_than_were_read_says_so_and_names_no_latest():
    row = _rows(FakeEcr(page=2))["web"]
    assert (row["status"], row["info"]) == ("2+ images", "")


def test_without_permission_to_read_images_repositories_are_still_listed():
    rows = _rows(FakeEcr(deny={"describe-images"}))
    assert [(r["status"], r["info"]) for r in rows.values()] == [("", "")] * 3
    assert BY_KEY["ecr"].status_colour(rows["web"]) is None


def test_only_the_first_repositories_have_their_images_counted(monkeypatch):
    monkeypatch.setattr(aws_resources, "_ENRICH_MAX", 1)
    aws = FakeEcr()
    rows = BY_KEY["ecr"].fetch(aws.call)
    assert [r["status"] for r in rows] == ["empty", "", ""]
    assert sum(argv[1] == "describe-images" for argv in aws.calls) == 1


def test_reading_the_images_never_marks_the_listing_as_cut_short():
    """``--max-items`` on the per-repository call would: the panel reads a
    cut-short answer to it as "there are more repositories"."""
    aws = FakeEcr(page=2)
    BY_KEY["ecr"].fetch(aws.call)
    images = [argv for argv in aws.calls if argv[1] == "describe-images"]
    assert len(images) == 3 and not any("--max-items" in argv for argv in images)


# ── Details ─────────────────────────────────────────────────────────


def test_a_repositorys_details_say_what_is_in_it_and_who_may_use_it():
    details = _details("web")
    assert details["Repository"] == ["web"]
    assert details["URI"] == [f"{REGISTRY}/web"]
    assert details["Created"] == ["2025-03-04 09:10:11"]
    assert details["Tags"] == ["mutable — pushing a tag again moves it to the new image"]
    assert details["Scan on push"] == ["off (the registry's own scanning settings may still apply)"]
    assert details["Encryption"] == ["AES-256"]
    assert details["Images"] == ["3 (1 untagged)"]
    assert details["Newest"] == [
        "latest, v1.1  2026-11-10 21:30  182.4 MB  2 critical, 5 high, 12 lower",
        "untagged sha256:cccccccccccc  2026-11-05 12:00  52.4 MB",
        "v1.0  2026-11-01 08:00  52.4 MB",
    ]
    assert details["Lifecycle policy"] == [
        "untagged images: expire 14 days after they were pushed — clear out build leftovers",
        "images tagged v*, release*: keep the newest 30, expire the rest",
    ]
    assert details["Access policy"] == [
        "account 444455556666, role/deploy in 777788889999 can pull",
        "codebuild.amazonaws.com can push and pull",
    ]


def test_a_locked_down_repository_with_no_policies():
    details = _details("team/api")
    assert details["Tags"] == ["immutable — a tag can't be moved to another image"]
    assert details["Scan on push"] == ["on"]
    assert details["Encryption"] == ["KMS (1234abcd)"]
    assert details["Images"] == ["1"]
    assert details["Newest"] == ["2026.10.1  2026-10-02 03:04  52.4 MB  scanned, nothing found"]
    assert details["Lifecycle policy"] == ["none — images are kept until someone deletes them"]
    assert details["Access policy"] == ["none — only this account's own IAM permissions apply"]


def test_an_empty_repository_and_one_with_more_images_than_were_read():
    assert _details("Empty")["Images"] == ["none — nothing has been pushed yet"]
    assert "Newest" not in _details("Empty")

    details = _details("web", FakeEcr(page=2))
    assert details["Images"] == [
        "more than 2 — only the first 2 were read, so the newest may not be among those below"
    ]
    assert len(details["Newest"]) == 2


def test_only_the_newest_images_are_listed(monkeypatch):
    monkeypatch.setattr(aws_resources, "_ECR_NEWEST", 1)
    assert [line.split()[0] for line in _details("web")["Newest"]] == ["latest,"]


def test_details_without_some_permissions_show_what_they_can():
    denied = {"describe-images", "get-lifecycle-policy", "get-repository-policy"}
    details = _details("web", FakeEcr(deny=denied))
    assert details["URI"] == [f"{REGISTRY}/web"]
    for label in ("Images", "Lifecycle policy", "Access policy"):
        assert details[label] == [
            "unknown — Access denied — this profile lacks permission for that call."
        ]


@pytest.mark.parametrize(
    ("image", "said"),
    [
        ({}, ""),
        ({"imageScanStatus": {"status": "FAILED"}}, "scan failed"),
        ({"imageScanStatus": {"status": "UNSUPPORTED_IMAGE"}}, "can't be scanned"),
        ({"imageScanStatus": {"status": "IN_PROGRESS"}}, "scan in progress"),
        ({"imageScanFindingsSummary": {"findingSeverityCounts": {"LOW": 4}}}, "4 lower"),
        ({"imageScanFindingsSummary": {"findingSeverityCounts": {"HIGH": 1}}}, "1 high"),
    ],
)
def test_what_a_scan_found_or_why_there_is_nothing_to_say(image, said):
    assert aws_resources._scan_result(image) == said


def test_newest_goes_by_the_moment_not_by_how_the_time_is_written():
    """The CLI writes times in the machine's zone, which changes with the
    clocks: late evening in New York is after 1am the next day in UTC."""
    evening = _image("eeeeeeee", "2026-11-10T23:30:00-05:00", ["evening"])
    next_day = _image("ffffffff", "2026-11-11T01:00:00+00:00", ["next-day"])
    newest = aws_resources._newest([next_day, evening])
    assert [i["imageTags"][0] for i in newest] == ["evening", "next-day"]


def test_times_from_the_older_cli_are_numbers():
    older = _image("eeeeeeee", 1_700_000_000.5, ["old"], size=None)
    newer = _image("ffffffff", 1_700_000_900, ["new"])
    assert aws_resources._image_line(older) == "old  2023-11-14 22:13"
    assert [i["imageTags"][0] for i in aws_resources._newest([older, newer])] == ["new", "old"]
    assert aws_resources._instant("not a time") == 0.0
    assert aws_resources._when(1_700_000_000) == "2023-11-14 22:13:20 UTC"


@pytest.mark.parametrize(
    ("selection", "action", "said"),
    [
        (
            {"tagStatus": "any", "countType": "imageCountMoreThan", "countNumber": 5},
            "expire",
            "all images: keep the newest 5, expire the rest",
        ),
        (
            {
                "tagStatus": "tagged",
                "tagPatternList": ["prod-*"],
                "countType": "sinceImagePushed",
                "countUnit": "days",
                "countNumber": 90,
            },
            "expire",
            "images tagged prod-*: expire 90 days after they were pushed",
        ),
        (
            {"tagStatus": "tagged", "countType": "sinceImagePulled", "countNumber": 30},
            "transition",
            "tagged images: transition by sinceImagePulled",
        ),
    ],
)
def test_lifecycle_rules_in_words(selection, action, said):
    rule = {"selection": selection, "action": {"type": action}}
    assert aws_resources._lifecycle_line(rule) == said


@pytest.mark.parametrize(
    ("statement", "said"),
    [
        (
            {"Effect": "Allow", "Principal": "*", "Action": "ecr:BatchGetImage"},
            "ANYONE can pull",
        ),
        (
            {
                "Effect": "Allow",
                "Principal": {"AWS": "*"},
                "Action": "ecr:*",
                "Condition": {"StringEquals": {"aws:PrincipalOrgID": "o-abc"}},
            },
            "ANYONE can do anything (with conditions)",
        ),
        (
            {"Effect": "Allow", "Principal": {"AWS": "444455556666"}, "Action": ["ecr:PutImage"]},
            "account 444455556666 can push",
        ),
        (
            {
                "Effect": "Deny",
                "Principal": {"AWS": "arn:aws:iam::444455556666:user/eve"},
                "Action": ["ecr:DeleteRepository"],
            },
            "user/eve in 444455556666 may NOT use 1 action",
        ),
        (
            {"Effect": "Allow", "Action": ["ecr:DescribeImages", "ecr:ListImages"]},
            "nobody named can use 2 actions",
        ),
    ],
)
def test_policy_statements_in_words(statement, said):
    assert aws_resources._policy_line(statement) == said


def test_policies_with_nothing_in_them_and_a_single_statement_not_in_a_list():
    def run_with(text_key: str, text: dict):
        return lambda argv: (json.dumps({text_key: json.dumps(text)}), 0)

    assert aws_resources._ecr_lifecycle(*run_with("lifecyclePolicyText", {})([])) == [
        ("Lifecycle policy", "set, with no rules")
    ]
    assert aws_resources._ecr_policy(*run_with("policyText", {})([])) == [
        ("Access policy", "set, with no statements")
    ]
    single = {"Statement": {"Effect": "Allow", "Principal": "*", "Action": "ecr:BatchGetImage"}}
    assert aws_resources._ecr_policy(*run_with("policyText", single)([])) == [
        ("Access policy", "ANYONE can pull")
    ]


# ── Around it ───────────────────────────────────────────────────────


def test_the_ecr_section_only_reads():
    """Listing and opening every repository runs nothing that changes
    anything — and nothing that returns a registry password."""
    aws = FakeEcr()
    rt = BY_KEY["ecr"]
    for row in rt.fetch(aws.call):
        aws.run(rt.describe(row))
        rt.facts(row, aws.run)
    assert {aws_cli.classify(argv) for argv in aws.calls} == {"read"}
    assert {argv[1] for argv in aws.calls} == {
        "describe-repositories",
        "describe-images",
        "get-lifecycle-policy",
        "get-repository-policy",
    }


def test_the_console_page_keeps_the_slashes_in_a_name():
    url = BY_KEY["ecr"].console_url
    assert url(_rows()["team/api"], "eu-west-1") == (
        "https://eu-west-1.console.aws.amazon.com/ecr/repositories/private/"
        "111122223333/team/api?region=eu-west-1"
    )
    assert url({"name": "web"}, "eu-west-1").endswith("/ecr/repositories?region=eu-west-1")


def test_the_login_command_is_for_the_registry_and_holds_no_password():
    row = _rows()["team/api"]
    assert ecr_login_command(row, profile="work", region="eu-west-1") == (
        "aws ecr get-login-password --profile work --region eu-west-1"
        f" | docker login --username AWS --password-stdin {REGISTRY}"
    )
    assert ecr_login_command(row) == (
        f"aws ecr get-login-password | docker login --username AWS --password-stdin {REGISTRY}"
    )
    # Running it does need approval when the AI asks: it prints a password.
    assert aws_cli.classify(["ecr", "get-login-password"]) == "mutate"


def test_an_answer_that_is_not_what_was_expected_is_not_a_crash():
    rt = BY_KEY["ecr"]
    for output in ("[1, 2]", "<html>", '{"lifecyclePolicyText": "nope", "policyText": 3}'):
        facts = dict(rt.facts({"name": "web"}, lambda argv, out=output: (out, 0)))
        assert set(facts) <= {"Images", "Newest", "Lifecycle policy", "Access policy"}
        assert facts["Lifecycle policy"] == "unknown — unexpected output"
        assert facts["Access policy"] == "unknown — unexpected output"
    assert rt.summarize({}) == [] and rt.summarize({"repositories": []}) == []
    assert rt.fetch(lambda argv, **kw: None) == []
