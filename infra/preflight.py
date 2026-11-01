#!/usr/bin/env python3
"""Read-only checks to run before creating anything billable.

Only reads data: GET requests plus testIamPermissions, which reports access
without changing it. Exits non-zero if any check fails. Quota and catalog
checks cannot prove GPU capacity is in stock; only a real create can.
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
import sys
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
VERSIONS_FILE = ROOT / "deploy" / "versions.env"
TFVARS_FILE = ROOT / "infra" / "terraform" / "terraform.tfvars"

REQUIRED_TOOLS = ("gcloud", "terraform", "helm", "kubectl")
REQUIRED_APIS = (
    "compute.googleapis.com",
    "container.googleapis.com",
    "cloudbilling.googleapis.com",
    "billingbudgets.googleapis.com",
)
PROJECT_PERMISSIONS = (
    "compute.networks.create",
    "compute.subnetworks.create",
    "container.clusters.create",
    "container.clusters.delete",
    "iam.serviceAccounts.actAs",
    "iam.serviceAccounts.create",
)
BILLING_PERMISSIONS = ("billing.budgets.create",)
COMPUTE_ENGINE_SERVICE_ID = "6F81-5844-456A"

# Not available per-SKU here, so these are stated assumptions, not live prices.
# docs/decisions.md D-009 records where they come from.
ASSUMED_HOURLY_USD = {
    "GKE cluster management fee (assumed)": 0.10,
    "Disks and public IPs (assumed)": 0.03,
}

PASS, WARN, FAIL = "PASS", "WARN", "FAIL"


@dataclass
class Check:
    name: str
    status: str
    detail: str = ""


def parse_env(text: str) -> dict[str, str]:
    """Parse KEY=VALUE lines, ignoring blanks and comments."""
    values = {}
    for line in text.splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            key, value = line.split("=", 1)
            values[key.strip()] = value.strip()
    return values


def parse_tfvars(text: str) -> dict[str, str]:
    """Read simple `key = "value"` or `key = 30` assignments."""
    pattern = re.compile(r'^\s*(\w+)\s*=\s*"?([^"\n#]*?)"?\s*(?:#.*)?$', re.MULTILINE)
    return dict(pattern.findall(text))


def http_json(url: str, token: str | None = None, body: dict | None = None) -> tuple[int, dict]:
    """Return (status, parsed JSON). Status 0 means the request never completed."""
    headers = {"Content-Type": "application/json", "User-Agent": "inferscope-preflight"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    data = json.dumps(body).encode() if body is not None else None
    request = urllib.request.Request(url, data=data, headers=headers)
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            return response.status, json.load(response)
    except urllib.error.HTTPError as error:
        return error.code, {}
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError):
        return 0, {}


def gcloud_token() -> str | None:
    if not shutil.which("gcloud"):
        return None
    result = subprocess.run(
        ["gcloud", "auth", "print-access-token"], capture_output=True, text=True, check=False
    )
    return result.stdout.strip() if result.returncode == 0 else None


def adc_available() -> bool:
    if not shutil.which("gcloud"):
        return False
    result = subprocess.run(
        ["gcloud", "auth", "application-default", "print-access-token"],
        capture_output=True,
        text=True,
        check=False,
    )
    return result.returncode == 0


def quota_available(quotas: list[dict], metric: str) -> float | None:
    """Return free quota for a metric, or None if the metric isn't listed."""
    for quota in quotas:
        if quota.get("metric") == metric:
            return quota.get("limit", 0) - quota.get("usage", 0)
    return None


def unit_price(sku: dict) -> float:
    """Price per usage unit from a catalog SKU, using the highest usage tier."""
    expression = sku["pricingInfo"][0]["pricingExpression"]
    rate = expression["tieredRates"][-1]["unitPrice"]
    return int(rate.get("units", 0) or 0) + rate.get("nanos", 0) / 1e9


def find_sku_price(skus: list[dict], description_prefix: str, region: str) -> float | None:
    """Find the on-demand price of the SKU matching a description in a region."""
    for sku in skus:
        if (
            sku.get("description", "").startswith(description_prefix)
            and region in sku.get("serviceRegions", [])
            and sku.get("category", {}).get("usageType") == "OnDemand"
        ):
            return unit_price(sku)
    return None


def node_hourly_price(
    skus: list[dict],
    family: str,
    region: str,
    vcpus: int,
    ram_gib: int,
    gpu_prefix: str | None = None,
    gpu_count: int = 0,
) -> float | None:
    """Sum vCPU, RAM and GPU prices. None means a SKU couldn't be matched."""
    core = find_sku_price(skus, f"{family} Instance Core", region)
    ram = find_sku_price(skus, f"{family} Instance Ram", region)
    if core is None or ram is None:
        return None
    total = core * vcpus + ram * ram_gib
    if gpu_prefix:
        gpu = find_sku_price(skus, gpu_prefix, region)
        if gpu is None:
            return None
        total += gpu * gpu_count
    return total


def fetch_compute_skus(token: str) -> list[dict]:
    skus: list[dict] = []
    page_token = ""
    while True:
        query = urllib.parse.urlencode(
            {"currencyCode": "USD", "pageSize": 5000, "pageToken": page_token}
        )
        url = f"https://cloudbilling.googleapis.com/v1/services/{COMPUTE_ENGINE_SERVICE_ID}/skus?{query}"
        status, payload = http_json(url, token)
        if status != 200:
            return []
        skus.extend(payload.get("skus", []))
        page_token = payload.get("nextPageToken", "")
        if not page_token:
            return skus


def check_tools() -> list[Check]:
    return [
        Check(f"tool: {tool}", PASS)
        if shutil.which(tool)
        else Check(f"tool: {tool}", FAIL, "not installed")
        for tool in REQUIRED_TOOLS
    ]


def check_model(versions: dict[str, str]) -> Check:
    model, revision = versions["MODEL_ID"], versions["MODEL_REVISION"]
    status, info = http_json(f"https://huggingface.co/api/models/{model}/revision/{revision}")
    if status != 200:
        return Check("model revision", FAIL, f"{model}@{revision[:8]} returned HTTP {status}")
    if info.get("gated"):
        return Check("model revision", FAIL, f"{model} is gated and needs a token")
    return Check("model revision", PASS, f"{model}@{revision[:8]}, not gated")


def check_image(versions: dict[str, str]) -> Check:
    repo, tag = versions["VLLM_IMAGE"], f"v{versions['VLLM_VERSION']}"
    url = f"https://hub.docker.com/v2/repositories/{repo}/tags/{tag}"
    status, info = http_json(url)
    if status != 200:
        return Check("vLLM image", FAIL, f"{repo}:{tag} returned HTTP {status}")
    if info.get("digest") != versions["VLLM_IMAGE_DIGEST"]:
        return Check("vLLM image", FAIL, f"{repo}:{tag} digest differs from the pin")
    return Check("vLLM image", PASS, f"{repo}:{tag} matches the pinned digest")


def check_project(token: str, config: dict[str, str]) -> list[Check]:
    project = config["project_id"]
    checks = []

    status, info = http_json(
        f"https://cloudbilling.googleapis.com/v1/projects/{project}/billingInfo", token
    )
    enabled = status == 200 and info.get("billingEnabled")
    checks.append(Check("billing enabled", PASS if enabled else FAIL, f"HTTP {status}"))

    for api in REQUIRED_APIS:
        status, info = http_json(
            f"https://serviceusage.googleapis.com/v1/projects/{project}/services/{api}", token
        )
        ok = info.get("state") == "ENABLED"
        detail = "" if ok else f"run: gcloud services enable {api} --project <project>"
        checks.append(Check(f"api: {api}", PASS if ok else FAIL, detail))

    status, info = http_json(
        f"https://cloudresourcemanager.googleapis.com/v1/projects/{project}:testIamPermissions",
        token,
        {"permissions": list(PROJECT_PERMISSIONS)},
    )
    missing = set(PROJECT_PERMISSIONS) - set(info.get("permissions", []))
    checks.append(
        Check(
            "project permissions",
            FAIL if missing or status != 200 else PASS,
            f"missing: {', '.join(sorted(missing))}" if missing else "",
        )
    )

    account = config["billing_account"]
    status, info = http_json(
        f"https://cloudbilling.googleapis.com/v1/billingAccounts/{account}:testIamPermissions",
        token,
        {"permissions": list(BILLING_PERMISSIONS)},
    )
    missing = set(BILLING_PERMISSIONS) - set(info.get("permissions", []))
    checks.append(
        Check(
            "budget permission",
            FAIL if missing or status != 200 else PASS,
            f"missing: {', '.join(sorted(missing))}" if missing else "",
        )
    )
    return checks


def check_quota(token: str, config: dict[str, str]) -> list[Check]:
    project, region = config["project_id"], config["region"]
    base = f"https://compute.googleapis.com/compute/v1/projects/{project}"
    _, project_info = http_json(base, token)
    _, region_info = http_json(f"{base}/regions/{region}", token)
    specs = (
        ("quota: GPUS_ALL_REGIONS", project_info, "GPUS_ALL_REGIONS", FAIL),
        ("quota: L4 on-demand", region_info, "NVIDIA_L4_GPUS", FAIL),
        ("quota: L4 Spot", region_info, "PREEMPTIBLE_NVIDIA_L4_GPUS", WARN),
    )
    checks = []
    for name, info, metric, severity in specs:
        free = quota_available(info.get("quotas", []), metric)
        if free is None:
            checks.append(Check(name, severity, f"{metric} not listed in {region}"))
        elif free < 1:
            checks.append(Check(name, severity, f"{free:g} free; request an increase"))
        else:
            checks.append(Check(name, PASS, f"{free:g} free"))
    return checks


def check_zone(token: str, config: dict[str, str], versions: dict[str, str]) -> list[Check]:
    project, zone = config["project_id"], config["zone"]
    base = f"https://compute.googleapis.com/compute/v1/projects/{project}/zones/{zone}"
    specs = (
        ("machine type", f"{base}/machineTypes/{versions['GPU_MACHINE_TYPE']}"),
        ("accelerator type", f"{base}/acceleratorTypes/{versions['GPU_ACCELERATOR_TYPE']}"),
    )
    checks = []
    for name, url in specs:
        status, _ = http_json(url, token)
        detail = f"offered in {zone}" if status == 200 else f"HTTP {status} in {zone}"
        checks.append(Check(name, PASS if status == 200 else FAIL, detail))
    return checks


def check_cost(token: str, config: dict[str, str], versions: dict[str, str]) -> Check:
    """Estimate hourly lab cost. Prices come live from the Cloud Billing Catalog."""
    skus = fetch_compute_skus(token)
    region = config["region"]
    gpu_node = node_hourly_price(
        skus,
        "G2",
        region,
        int(versions["GPU_MACHINE_VCPUS"]),
        int(versions["GPU_MACHINE_RAM_GIB"]),
        "Nvidia L4",
        int(versions["GPU_COUNT"]),
    )
    cpu_node = node_hourly_price(
        skus,
        "E2",
        region,
        int(versions["CPU_MACHINE_VCPUS"]),
        int(versions["CPU_MACHINE_RAM_GIB"]),
    )
    if gpu_node is None or cpu_node is None:
        return Check("hourly cost", FAIL, "could not match catalog SKUs; no estimate produced")
    items = {
        f"{versions['GPU_MACHINE_TYPE']} (catalog)": gpu_node,
        f"{versions['CPU_MACHINE_TYPE']} (catalog)": cpu_node,
        **ASSUMED_HOURLY_USD,
    }
    lines = [f"\n      ${price:.3f}/hr  {name}" for name, price in items.items()]
    return Check(
        "hourly cost", PASS, f"${sum(items.values()):.2f}/hr estimated total" + "".join(lines)
    )


def run() -> list[Check]:
    versions = parse_env(VERSIONS_FILE.read_text())
    checks = check_tools()
    checks.append(check_model(versions))
    checks.append(check_image(versions))

    if not TFVARS_FILE.exists():
        checks.append(
            Check("configuration", FAIL, "copy terraform.tfvars.example to terraform.tfvars")
        )
        return checks
    config = parse_tfvars(TFVARS_FILE.read_text())
    required = ("project_id", "billing_account", "region", "zone")
    missing = [key for key in required if not config.get(key)]
    if missing:
        checks.append(Check("configuration", FAIL, f"missing: {', '.join(missing)}"))
        return checks
    checks.append(Check("configuration", PASS, f"{config['region']} / {config['zone']}"))

    token = gcloud_token()
    if not token:
        checks.append(Check("gcloud auth", FAIL, "run: gcloud auth login"))
        return checks
    checks.append(Check("gcloud auth", PASS))
    adc = adc_available()
    adc_detail = "" if adc else "run: gcloud auth application-default login"
    checks.append(Check("application default credentials", PASS if adc else FAIL, adc_detail))

    checks.extend(check_project(token, config))
    checks.extend(check_quota(token, config))
    checks.extend(check_zone(token, config, versions))
    checks.append(check_cost(token, config, versions))
    return checks


def main() -> int:
    checks = run()
    for check in checks:
        suffix = f"  {check.detail}" if check.detail else ""
        print(f"[{check.status}] {check.name}{suffix}")
    failures = sum(check.status == FAIL for check in checks)
    print(f"\nPreflight {'FAILED' if failures else 'passed'}: {failures} failing check(s).")
    print("Note: quota and catalog checks do not prove GPU capacity is in stock.")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
