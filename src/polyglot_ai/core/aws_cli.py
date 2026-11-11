"""Shared AWS CLI plumbing for the AWS panel and the ``aws_cli`` AI tool.

Everything goes through the user's own ``aws`` binary, so credentials,
SSO sessions, ``aws-vault`` and MFA all work exactly as they do in a
terminal — Polyglot never reads ``~/.aws/credentials`` itself.
"""

from __future__ import annotations

import configparser
import os
import re
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
# ``query``, ``scan`` and ``select`` are deliberately absent: they return
# table rows / object contents, and customer data must not go to the AI
# provider without the user seeing the command first.
_READ_VERB_PREFIXES = (
    "describe",
    "list",
    "get",
    "head",
    "search",
    "lookup",
    "filter",
    "batch-get",
    "check",
    "test",
    "estimate",
    "preview",
    "simulate",
    "validate",
)

# ``aws s3`` (the high-level commands) uses verbs, not API names.
# ``presign`` (and generate-presigned-url above) is deliberately not
# read-only: the URL it returns grants access to the object.
_S3_READ_VERBS = {"ls"}
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
            if path == creds:
                name = section  # every section of the credentials file is a profile
            elif section == "default":
                name = section
            elif section.startswith("profile "):
                name = section[len("profile ") :].strip()
            else:
                # ``[sso-session x]``, ``[services x]``, ``[plugins]``… are
                # settings, not profiles; listing them offered choices
                # that can only fail.
                continue
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


#: Global options that take a value. Their value must not be mistaken for
#: the service or operation: ``ec2 --query describe terminate-instances``
#: is a TerminateInstances call (``describe`` is the --query value), which
#: the old word filter classed as a harmless "describe".
_VALUE_OPTIONS = frozenset(
    {
        "--query",
        "--output",
        "--region",
        "--profile",
        "--color",
        "--ca-bundle",
        "--cli-read-timeout",
        "--cli-connect-timeout",
        "--page-size",
        "--max-items",
        "--starting-token",
        "--cli-binary-format",
    }
)
_FLAG_OPTIONS = frozenset(
    {"--debug", "--no-paginate", "--no-cli-pager", "--no-cli-auto-prompt", "--cli-auto-prompt"}
)
#: Never "read", wherever they appear: they send the request somewhere
#: else, drop TLS checks or signing, or hide the real arguments in a file.
_NEVER_READ_OPTIONS = frozenset(
    {
        "--endpoint-url",
        "--no-sign-request",
        "--no-verify-ssl",
        "--cli-input-json",
        "--cli-input-yaml",
    }
)
#: Read-shaped calls that return credentials or secrets. They need approval
#: like any mutation.
_SECRET_OR_FILE_READS = frozenset(
    {
        ("secretsmanager", "get-secret-value"),
        ("secretsmanager", "batch-get-secret-value"),
        ("ssm", "get-parameter"),
        ("ssm", "get-parameters"),
        ("ssm", "get-parameters-by-path"),
        ("ssm", "get-parameter-history"),
        ("sts", "get-session-token"),
        ("sts", "get-federation-token"),
        ("ecr", "get-login-password"),
        ("ecr", "get-authorization-token"),
        ("codeartifact", "get-authorization-token"),
        ("eks", "get-token"),
        ("rds", "generate-db-auth-token"),
        ("cognito-identity", "get-credentials-for-identity"),
        ("lambda", "get-function"),
        ("redshift", "get-cluster-credentials"),
        ("redshift", "get-cluster-credentials-with-iam"),
        ("redshift-serverless", "get-credentials"),
        ("lightsail", "get-instance-access-details"),
        ("lightsail", "get-relational-database-master-user-password"),
        ("gamelift", "get-instance-access"),
        ("gamelift", "get-compute-access"),
        ("gamelift", "get-compute-auth-token"),
        ("cognito-identity", "get-open-id-token"),
        ("cognito-identity", "get-open-id-token-for-developer-identity"),
        ("cognito-idp", "describe-user-pool-client"),
        ("sso", "get-role-credentials"),
        ("connect", "get-federation-token"),
        ("emr-containers", "get-managed-endpoint-session-credentials"),
        ("apigateway", "get-api-key"),
        ("apigateway", "get-api-keys"),
        ("appsync", "list-api-keys"),
        ("ec2", "get-password-data"),
    }
)
#: Reads that return stored data (table items, stream records, query
#: results, repository files) rather than configuration. Output is
#: secret-redacted, but redaction can't recognise customer data.
_DATA_READS = frozenset(
    {
        ("dynamodb", "get-item"),
        ("dynamodb", "batch-get-item"),
        ("dynamodbstreams", "get-records"),
        ("kinesis", "get-records"),
        ("athena", "get-query-results"),
        ("redshift-data", "get-statement-result"),
        ("codecommit", "get-file"),
        ("codecommit", "get-blob"),
    }
)
#: Read-shaped operations that take an ``outfile`` argument and write the
#: response body to a path of the caller's choosing. Generated from the
#: service definitions bundled with AWS CLI 2.35.11: every operation whose
#: output payload is a blob (that is what makes the CLI add ``outfile``)
#: and whose name starts with a read prefix. Regenerate when bumping it.
_OUTFILE_READS = frozenset(
    {
        ("agenttoolkit", "get-skill-file"),
        ("apigateway", "get-export"),
        ("apigateway", "get-sdk"),
        ("appconfig", "get-configuration"),
        ("appconfig", "get-hosted-configuration-version"),
        ("appconfigdata", "get-latest-configuration"),
        ("appsync", "get-introspection-schema"),
        ("cloudfront", "get-connection-function"),
        ("cloudfront", "get-function"),
        ("codeartifact", "get-package-version-asset"),
        ("codeguruprofiler", "get-profile"),
        ("datazone", "get-lineage-event"),
        ("ebs", "get-snapshot-block"),
        ("geo-maps", "get-glyphs"),
        ("geo-maps", "get-sprites"),
        ("geo-maps", "get-static-map"),
        ("geo-maps", "get-style-descriptor"),
        ("geo-maps", "get-tile"),
        ("glacier", "get-job-output"),
        ("iot-data", "get-thing-shadow"),
        ("iotwireless", "get-position-estimate"),
        ("iotwireless", "get-resource-position"),
        ("kinesis-video-archived-media", "get-clip"),
        ("kinesis-video-archived-media", "get-media-for-fragment-list"),
        ("kinesis-video-media", "get-media"),
        ("lakeformation", "get-work-unit-results"),
        ("location", "get-map-glyphs"),
        ("location", "get-map-sprites"),
        ("location", "get-map-style-descriptor"),
        ("location", "get-map-tile"),
        ("medialive", "describe-input-device-thumbnail"),
        ("mediastore-data", "get-object"),
        ("medical-imaging", "get-image-frame"),
        ("medical-imaging", "get-image-set-metadata"),
        ("omics", "get-read-set"),
        ("omics", "get-reference"),
        ("s3api", "get-object"),
        ("s3api", "get-object-annotation"),
        ("s3api", "get-object-torrent"),
        ("sagemaker-geospatial", "get-tile"),
        ("schemas", "get-code-binding-source"),
        ("tnb", "get-sol-function-package-content"),
        ("tnb", "get-sol-function-package-descriptor"),
        ("tnb", "get-sol-network-package-content"),
        ("tnb", "get-sol-network-package-descriptor"),
        ("workmailmessageflow", "get-raw-message-content"),
    }
)
_APPROVAL_READS = _SECRET_OR_FILE_READS | _DATA_READS | _OUTFILE_READS


def _service_and_operation(argv: list[str]) -> tuple[str, str, list[str]] | None:
    """``(service, operation, the operation's own arguments)``.

    The first two positional words, skipping global options and their
    values. None when an option we don't know appears before the
    operation — we can't tell whether it takes a value, so the caller
    must assume the worst.
    """
    positionals: list[str] = []
    i = 0
    while i < len(argv) and len(positionals) < 2:
        tok = argv[i]
        if tok.startswith("-"):
            name = tok.split("=", 1)[0]
            if name in _VALUE_OPTIONS:
                i += 1 if "=" in tok else 2
                continue
            if name in _FLAG_OPTIONS:
                i += 1
                continue
            return None
        positionals.append(tok.lower())
        i += 1
    if len(positionals) != 2:
        return None
    return positionals[0], positionals[1], argv[i:]


def _reads_local_file(argv: list[str]) -> bool:
    """A ``file://`` / ``fileb://`` value: the CLI loads that local file
    and sends its contents to AWS as the parameter — a way to read files
    outside the project sandbox."""
    return any("file://" in tok or "fileb://" in tok for tok in argv)


def _has_stray_path(rest: list[str]) -> bool:
    """A path-looking argument that isn't the value of an option.

    ``_OUTFILE_READS`` lists the operations known to write a file, but a
    newer CLI can add more; a bare path after the operation is the sign
    of one. The first word after ``--option`` is that option's value
    (``--log-group-name /aws/lambda/fn`` is fine); anything after that
    is only questioned when it looks like a path.
    """
    expecting_value = False
    for tok in rest:
        if tok.startswith("-"):
            name = tok.split("=", 1)[0]
            expecting_value = "=" not in tok and name not in _FLAG_OPTIONS
            continue
        if expecting_value:
            expecting_value = False
            continue
        if tok.startswith(("/", "~", ".")) or "/" in tok or "\\" in tok:
            return True
    return False


def classify(command: str | list[str]) -> str:
    """Return ``"read"``, ``"mutate"`` or ``"destructive"`` for an AWS command.

    Conservative: anything not recognisably read-only is a mutation, so
    a typo, an unknown service or an unfamiliar option can never pass as
    read-only. "read" is used to colour the approval card and to decide
    what may run without asking, so it must also exclude reads that
    return secrets or write files.
    """
    argv = split_command(command) if isinstance(command, str) else list(command)
    parsed = _service_and_operation(argv)
    if parsed is None:
        return "mutate"
    service, verb, rest = parsed
    # Destructive wins over everything below, so the warning colour shows.
    if service == "s3" and verb in _S3_DESTRUCTIVE_VERBS:
        return "destructive"
    if service != "s3" and verb.startswith(_DESTRUCTIVE_VERB_PREFIXES):
        return "destructive"
    if any(tok.split("=", 1)[0] in _NEVER_READ_OPTIONS for tok in argv):
        return "mutate"
    if (service, verb) in _APPROVAL_READS:
        return "mutate"
    if _reads_local_file(argv):
        return "mutate"
    if service == "s3":
        return "read" if verb in _S3_READ_VERBS else "mutate"
    if service == "sts":
        return "read" if verb == "get-caller-identity" else "mutate"
    if service == "configure":
        # ``configure get aws_secret_access_key`` prints the secret.
        return "read" if verb in ("list", "list-profiles") else "mutate"
    if service == "logs" and verb == "tail":
        # Its one positional is the log group, which looks like a path.
        return "mutate" if _has_stray_path(rest[1:]) else "read"
    if _has_stray_path(rest):
        return "mutate"
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
        # Name what was refused: it is the one thing needed to fix the policy.
        action = re.search(r"not authorized to perform: ([A-Za-z0-9-]+:[A-Za-z0-9*]+)", text)
        if action:
            return f"Access denied — this profile lacks permission for {action.group(1)}."
        operation = re.search(r"when calling the (\w+) operation", text)
        if operation:
            return f"Access denied — this profile isn't allowed to call {operation.group(1)}."
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
    cwd: str | None = None,
) -> tuple[str, int]:
    """Run ``aws <args>`` synchronously; returns (stdout-or-stderr, returncode).

    Runs on whatever thread calls it — the panel uses the thread
    bridge, the AI tool uses ``asyncio.to_thread``. ``cwd`` sets the
    working directory relative paths resolve against.
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
        result = subprocess.run(
            cmd, capture_output=True, text=True, timeout=timeout, env=env, cwd=cwd
        )
    except subprocess.TimeoutExpired:
        return f"Error: `aws {' '.join(args[:2])}` timed out after {timeout}s", 124
    except OSError as exc:
        return f"Error: couldn't run aws: {exc}", 126
    if result.returncode != 0:
        return f"Error: {explain_error(result.stderr or result.stdout)}", result.returncode
    return result.stdout.strip(), 0
