"""Shared AWS CLI plumbing for the AWS panel and the ``aws_cli`` AI tool.

Everything goes through the user's own ``aws`` binary, so credentials,
SSO sessions, ``aws-vault`` and MFA all work exactly as they do in a
terminal — Polyglot never reads ``~/.aws/credentials`` itself.
"""

from __future__ import annotations

import configparser
import os
import shlex
import shutil
import subprocess
from pathlib import Path

# Commonly used regions, in rough order of popularity. The region combo
# is editable so anything else can be typed.
REGIONS = [
    "us-east-1",
    "us-east-2",
    "us-west-1",
    "us-west-2",
    "ca-central-1",
    "eu-west-1",
    "eu-west-2",
    "eu-west-3",
    "eu-central-1",
    "eu-north-1",
    "ap-southeast-1",
    "ap-southeast-2",
    "ap-northeast-1",
    "ap-northeast-2",
    "ap-south-1",
    "sa-east-1",
]

# Sub-command verbs that only read state. Anything else is a mutation.
_READ_VERB_PREFIXES = (
    "describe",
    "list",
    "get",
    "head",
    "search",
    "lookup",
    "query",
    "scan",
    "filter",
    "batch-get",
    "select",
    "check",
    "test",
    "estimate",
    "preview",
    "simulate",
    "validate",
    "generate-presigned-url",
)

# ``aws s3`` (the high-level commands) uses verbs, not API names.
_S3_READ_VERBS = {"ls", "presign"}
_S3_DESTRUCTIVE_VERBS = {"rm", "rb"}

# Verbs that destroy or expose things — worth an amber warning on top
# of the normal approval.
_DESTRUCTIVE_VERB_PREFIXES = (
    "delete",
    "terminate",
    "remove",
    "purge",
    "destroy",
    "deregister",
    "disable",
    "revoke",
    "detach",
    "reboot",
    "stop",
    "put-bucket-policy",
    "put-bucket-acl",
    "put-public-access-block",
    "modify-db-instance",
    "update-stack",
    "cancel",
)


def aws_available() -> bool:
    return shutil.which("aws") is not None


def list_profiles() -> list[str]:
    """Profiles from ~/.aws/config and ~/.aws/credentials (no subprocess).

    Reading the INI files directly is instant and works even when the
    CLI is missing; ``aws configure list-profiles`` takes ~300 ms of
    Python startup every call.
    """
    profiles: list[str] = []
    home = Path(os.environ.get("AWS_CONFIG_FILE") or "~/.aws/config").expanduser()
    creds = Path(os.environ.get("AWS_SHARED_CREDENTIALS_FILE") or "~/.aws/credentials").expanduser()
    for path in (home, creds):
        if not path.is_file():
            continue
        parser = configparser.ConfigParser(interpolation=None)
        try:
            parser.read(path, encoding="utf-8")
        except (configparser.Error, OSError):
            continue
        for section in parser.sections():
            name = section[len("profile ") :] if section.startswith("profile ") else section
            if name and name not in profiles:
                profiles.append(name)
    if "default" in profiles:
        profiles.remove("default")
        profiles.insert(0, "default")
    return profiles


def profile_region(profile: str) -> str:
    """The region configured for ``profile`` (or ``AWS_DEFAULT_REGION``), else ''."""
    env = os.environ.get("AWS_REGION") or os.environ.get("AWS_DEFAULT_REGION")
    if env:
        return env
    home = Path(os.environ.get("AWS_CONFIG_FILE") or "~/.aws/config").expanduser()
    if not home.is_file():
        return ""
    parser = configparser.ConfigParser(interpolation=None)
    try:
        parser.read(home, encoding="utf-8")
    except (configparser.Error, OSError):
        return ""
    section = "default" if profile in ("", "default") else f"profile {profile}"
    return parser.get(section, "region", fallback="").strip()


def split_command(command: str) -> list[str]:
    """``"ec2 describe-instances --max-items 5"`` → argv (without the leading ``aws``)."""
    try:
        argv = shlex.split(command)
    except ValueError:
        argv = command.split()
    if argv and argv[0] == "aws":
        argv = argv[1:]
    return argv


def classify(command: str | list[str]) -> str:
    """Return ``"read"``, ``"mutate"`` or ``"destructive"`` for an AWS command.

    Conservative: anything not recognisably read-only is a mutation, so
    a typo or an unknown service can never slip past approval.
    """
    argv = split_command(command) if isinstance(command, str) else list(command)
    words = [w for w in argv if not w.startswith("-")]
    if len(words) < 2:
        return "mutate"
    service, verb = words[0].lower(), words[1].lower()
    if service in ("s3",):
        if verb in _S3_READ_VERBS:
            return "read"
        return "destructive" if verb in _S3_DESTRUCTIVE_VERBS else "mutate"
    if service == "sts" or service == "configure" and verb in ("list", "get", "list-profiles"):
        return "read"
    if service == "logs" and verb in ("tail", "filter-log-events", "get-log-events"):
        return "read"
    if verb.startswith(_DESTRUCTIVE_VERB_PREFIXES):
        return "destructive"
    if verb.startswith(_READ_VERB_PREFIXES):
        return "read"
    return "mutate"


def explain_error(stderr: str) -> str:
    """Turn the CLI's stderr into a one-line hint the user can act on."""
    text = (stderr or "").strip()
    low = text.lower()
    if "unable to locate credentials" in low or "no credentials" in low:
        return "No AWS credentials found — run `aws configure` or `aws sso login`."
    if "expiredtoken" in low or "token has expired" in low or "sso session" in low:
        return "Your AWS session has expired — run `aws sso login` (or refresh your credentials)."
    if "could not connect to the endpoint" in low or "endpoint url" in low:
        return "Couldn't reach AWS — check the region and your network connection."
    if "accessdenied" in low or "not authorized" in low or "unauthorizedoperation" in low:
        return "Access denied — this profile lacks permission for that call."
    if "invalid choice" in low or "argument command: invalid" in low:
        return "Unknown AWS command — check the service and sub-command names."
    if "you must specify a region" in low:
        return "No region set — pick one above or run `aws configure`."
    first = text.splitlines()[0] if text else "Unknown error"
    return first[:200]


def run(
    args: list[str],
    *,
    profile: str = "",
    region: str = "",
    timeout: int = 30,
    json_output: bool = True,
) -> tuple[str, int]:
    """Run ``aws <args>`` synchronously; returns (stdout-or-stderr, returncode).

    Runs on whatever thread calls it — the panel uses the thread
    bridge, the AI tool uses ``asyncio.to_thread``.
    """
    if not aws_available():
        return "Error: the AWS CLI (`aws`) is not installed on this machine.", 127
    cmd = ["aws", *args]
    if profile and "--profile" not in args:
        cmd.extend(["--profile", profile])
    if region and "--region" not in args:
        cmd.extend(["--region", region])
    if json_output and "--output" not in args:
        cmd.extend(["--output", "json"])
    env = dict(os.environ, AWS_PAGER="")  # never block on a pager
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, env=env)
    except subprocess.TimeoutExpired:
        return f"Error: `aws {' '.join(args[:2])}` timed out after {timeout}s", 124
    except OSError as exc:
        return f"Error: couldn't run aws: {exc}", 126
    if result.returncode != 0:
        return f"Error: {explain_error(result.stderr or result.stdout)}", result.returncode
    return result.stdout.strip(), 0
