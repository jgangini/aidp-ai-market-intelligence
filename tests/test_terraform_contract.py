"""Deployment contract invariants; native ownership tests live in terraform/tests."""
import json
from pathlib import Path
import re


ROOT = Path(__file__).resolve().parents[1]
MANIFEST = json.loads((ROOT / "terraform/deploy-studio.json").read_text(encoding="utf-8"))


def test_apify_secret_cannot_enter_terraform_or_public_outputs():
    fields = {field["name"]: field for field in MANIFEST["form"]["fields"]}
    secret = fields["apify_token"]
    assert secret["secret"] and secret["type"] == "password"
    assert secret["transform"] == "none" and "default" not in secret
    for hook in ("preflight", "post_apply"):
        assert "apify_token" in MANIFEST[hook]["secret_inputs"]
    terraform = "\n".join(path.read_text(encoding="utf-8") for path in (ROOT / "terraform").glob("*.tf"))
    assert not re.search(r'variable\s+"(?:apify_token|admin_password|private_key)"', terraform)
    assert not set(MANIFEST["outputs"]) & {name for name, field in fields.items() if field.get("secret")}
    assert MANIFEST["terraform"]["package_oci_credentials"] is False


def test_existing_destination_uses_exact_keys_and_no_portal_resources():
    fields = {field["name"]: field for field in MANIFEST["form"]["fields"]}
    for key in ("existing_ai_data_platform_ocid", "existing_workspace_key"):
        assert fields[key]["required"]
        assert fields[key]["visible_when"] == {"field": "aidp_mode", "equals": "existing"}
    assert fields["admin_user_ocid"]["options_source"] == "oci_aidp_administrators"
    terraform = "\n".join(path.read_text(encoding="utf-8") for path in (ROOT / "terraform").glob("*.tf"))
    resource_types = set(re.findall(r'resource\s+"([^"]+)"', terraform))
    assert resource_types <= {"oci_identity_policy", "time_sleep", "oci_ai_data_platform_ai_data_platform", "oci_objectstorage_bucket"}
    assert "oci_core_instance" not in resource_types
    assert MANIFEST["capabilities"]["database_profile"] == "none"


def test_administrator_selection_is_required_without_self_granted_privileges():
    fields = {field["name"]: field for field in MANIFEST["form"]["fields"]}
    assert fields["admin_user_ocid"]["required"]
    assert fields["identity_domain_id"]["required"]
    domain = fields["identity_domain_id"]
    assert domain["type"] == "select"
    assert domain["options_source"] == "oci_identity_domains"
    assert "default" not in domain and "options" not in domain
    assert re.fullmatch(domain["pattern"], "ocid1.domain.oc1..example")
    for invalid in ("Default", "https://example.com", "ocid1.domain.oc1..example injected"):
        assert re.fullmatch(domain["pattern"], invalid) is None
    assert fields["admin_user_ocid"]["options_source"] == "oci_aidp_administrators"
    assert "default" not in fields["admin_user_ocid"]
    assert "admin_password" not in fields and "admin_username" not in fields
    resources = (ROOT / "terraform/c_resources.tf").read_text(encoding="utf-8")
    policy = resources.split('resource "oci_identity_policy"', 1)[1].split('resource "time_sleep"', 1)[0]
    assert "var.admin_user_ocid" not in policy
    assert "AI_DATA_PLATFORM_ADMIN" not in policy
    assert MANIFEST["preflight"]["requires_oci_credentials"]
    assert MANIFEST["post_apply"]["requires_oci_credentials"]


def test_hook_archive_covers_only_portable_public_sources():
    includes = MANIFEST["post_apply"]["include_paths"]
    assert {"src", "aidp", "package", "pyproject.toml", "uv.lock"} <= set(includes)
    for relative in includes:
        assert not Path(relative).is_absolute()
        assert not set(Path(relative).parts) & {".oci", ".local", ".git", "apps", "node_modules"}
    steps = [step["key"] for step in MANIFEST["run_steps"]]
    assert steps[0] == "queue" and "apply" in steps and steps[-1] == "complete"
    assert len(steps) == len(set(steps))


def test_transient_preflight_id_is_not_persisted_as_deployment_ownership():
    runtime_fields = {field["name"] for field in MANIFEST["preflight"]["runtime_fields"]}
    assert not {"deployment_id", "operation_id"} & runtime_fields
    assert not {"deployment_id", "operation_id"} & set(MANIFEST["preflight"]["output_inputs"])
    variables = (ROOT / "terraform/b_variables.tf").read_text(encoding="utf-8")
    assert not re.search(r'variable\s+"(?:deployment_id|operation_id)"', variables)
    assert "deployment_binding" in MANIFEST["outputs"]
    assert "deployment_id" not in MANIFEST["outputs"]
