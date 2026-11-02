"""Guardrails that keep CI read-only and local secrets out of a public repo."""

import re
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
WORKFLOWS = sorted((ROOT / ".github" / "workflows").glob("*.y*ml"))
FORBIDDEN = re.compile(
    r"terraform\s+apply|terraform\s+destroy|helm\s+(install|upgrade)|kubectl\s+apply"
    r"|gcloud\s|make\s+(up|bench|down)|secrets\.|google-github-actions/auth"
)


def test_workflows_exist():
    assert WORKFLOWS


def test_workflows_never_deploy_or_use_credentials():
    for workflow in WORKFLOWS:
        code = "\n".join(
            line for line in workflow.read_text().splitlines() if not line.lstrip().startswith("#")
        )
        assert not FORBIDDEN.search(code), workflow.name


def test_local_config_is_gitignored():
    for path in ("infra/terraform/terraform.tfvars", "terraform.tfstate", ".env"):
        result = subprocess.run(["git", "check-ignore", "-q", path], cwd=ROOT, check=False)
        assert result.returncode == 0, path


def test_tfvars_example_is_not_ignored():
    path = "infra/terraform/terraform.tfvars.example"
    result = subprocess.run(["git", "check-ignore", "-q", path], cwd=ROOT, check=False)
    assert result.returncode == 1
