"""Deployment contract invariants; native ownership tests live in terraform/tests."""
import json
from collections import Counter
from pathlib import Path
import re


ROOT = Path(__file__).resolve().parents[1]
MANIFEST = json.loads((ROOT / "terraform/deploy-studio.json").read_text(encoding="utf-8"))


def test_declarative_form_places_every_field_and_file_exactly_once():
    assert MANIFEST["schema_version"] == 2
    form = MANIFEST["form"]
    groups = form["groups"]
    assert len({group["id"] for group in groups}) == len(groups)
    placed = Counter()
    for group in groups:
        assert group["title"].strip() and group["items"]
        for item in group["items"]:
            assert item["kind"] in {"field", "file", "control"}
            assert item.get("span", 2) in {1, 2}
            if item["kind"] != "control":
                placed[item["kind"], item["name"]] += 1
                collection = form["fields" if item["kind"] == "field" else "files"]
                definition = next(entry for entry in collection if entry["name"] == item["name"])
                assert definition.get("group", group["id"]) == group["id"]
    expected = Counter((kind, entry["name"]) for kind, key in (("field", "fields"), ("file", "files"))
                       for entry in form.get(key, []))
    assert placed == expected
    assert all(count == 1 for count in placed.values())


def test_declarative_credentials_and_destination_controls_match_capabilities():
    groups = MANIFEST["form"]["groups"]
    assert groups[0]["id"] == "credentials"
    assert [(item["kind"], item["name"]) for item in groups[0]["items"]] == [
        ("control", "oci_config"), ("control", "oci_key")]
    controls = [item for group in groups for item in group["items"] if item["kind"] == "control"]
    assert Counter(item["name"] for item in controls) == Counter([
        "oci_config", "oci_key", "oci_region", "compartment_mode", "new_compartment", "existing_compartment"])
    assert all(item["label"].strip() for item in controls)
    services = next(group for group in groups if group["id"] == "services")
    assert services["items"][0]["name"] == "oci_region"
    assert services["items"][0]["span"] == 2
    mode = next(item for item in controls if item["name"] == "compartment_mode")
    assert [option["value"] for option in mode["options"]] == MANIFEST["capabilities"]["compartment_modes"]
    assert all(option["label"].strip() for option in mode["options"])
    assert MANIFEST["capabilities"]["existing_database_wallet"] == "none"


def test_visible_form_matches_governance_layout_except_apify():
    groups = {g['id']: [i['name'] for i in g['items']] for g in MANIFEST['form']['groups']}
    assert groups['aidp_platform'] == ['aidp_mode', 'new_aidp_name', 'existing_ai_data_platform_ocid', 'existing_workspace_key']
    assert groups['enterprise_ai'] == ['agent_model_id', 'apify_token']
    assert groups['artifacts'] == ['artifacts_bucket_mode', 'artifacts_bucket_name', 'existing_artifacts_bucket_name']
    assert groups['database'] == ['autonomous_database_mode', 'autonomous_database_version', 'existing_autonomous_database_ocid', 'autonomous_database_admin_password']
    fields = {f['name']: f for f in MANIFEST['form']['fields']}
    assert len(fields) == 15 and not {'test_session_id', 'autonomous_database_compute_count'} & fields.keys()
    assert fields['admin_user_ocid']['label'] == 'AIDP administrator'
    assert fields['new_aidp_name']['default'] == 'aidp_market_intelligence'
    assert fields['new_aidp_name']['visible_when'] == {'field': 'aidp_mode', 'equals': 'new'}
    assert fields['artifacts_bucket_mode']['default'] == 'new'
    assert fields['artifacts_bucket_name']['default'] == 'oci_artifacts'
    assert 'default' not in fields['admin_user_ocid'] and 'default_selection' not in fields['admin_user_ocid']
    assert fields['agent_model_id']['default'] == 'xai.grok-4.7'
    assert fields['autonomous_database_version']['options'] == [
        {'value': '26ai:DW', 'label': 'Oracle AI Database 26ai · AI Lakehouse (DW)'}]


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


def test_memory_password_uses_protected_hooks_without_aidp_state_credential():
    fields = {field["name"]: field for field in MANIFEST["form"]["fields"]}
    secret = fields['autonomous_database_admin_password']
    assert secret['secret'] and secret['type'] == 'password' and 'default' not in secret
    assert secret['generate_password'] is True
    assert secret['generate_password_when'] == {'field': 'autonomous_database_mode', 'equals': 'new'}
    assert not fields['apify_token'].get('generate_password', False)
    for hook in ('preflight', 'post_apply'):
        assert 'autonomous_database_admin_password' in MANIFEST[hook]['secret_inputs']
    terraform = '\n'.join(p.read_text() for p in (ROOT/'terraform').glob('*.tf'))
    assert not re.search(r'\bvector_db_admin_cred\s*=', terraform)
    assert 'autonomous_database_admin_password' not in MANIFEST['outputs']
    assert fields['autonomous_database_version']['transform'] == 'database_profile'
    assert fields['autonomous_database_mode']['group'] == 'database'


def test_public_status_reports_saved_model_instead_of_live_selection():
    from ai_market_intelligence.client import public_status
    config = {'schema_version': 1, 'research_enabled': False, 'services': {
        'model_id': 'saved-model', 'region': 'us-chicago-1', 'compartment_id': 'ocid1.compartment.oc1..fixture'}}
    status = public_status({'phase': 'ready', 'runtime_config': config})
    assert status['configured_agent_model_id'] == 'saved-model'
    assert status['enabled'] is False
    assert public_status()['configured_agent_model_id'] == ''


def test_existing_destination_uses_exact_keys_and_no_portal_resources():
    fields = {field["name"]: field for field in MANIFEST["form"]["fields"]}
    for key in ("existing_ai_data_platform_ocid", "existing_workspace_key"):
        assert fields[key]["required"]
        assert fields[key]["visible_when"] == {"field": "aidp_mode", "equals": "existing"}
    assert fields["admin_user_ocid"]["options_source"] == "oci_aidp_administrators"
    terraform = "\n".join(path.read_text(encoding="utf-8") for path in (ROOT / "terraform").glob("*.tf"))
    resource_types = set(re.findall(r'resource\s+"([^"]+)"', terraform))
    assert resource_types <= {"oci_identity_policy", "time_sleep", "oci_ai_data_platform_ai_data_platform", "oci_objectstorage_bucket", "oci_database_autonomous_database", "terraform_data"}
    assert "oci_core_instance" not in resource_types
    assert MANIFEST["capabilities"]["database_profile"] == "new_or_existing"
    assert MANIFEST["capabilities"]["existing_database_wallet"] == "none"
    assert fields["existing_ai_data_platform_ocid"]["options_source"] == "oci_aidp_platforms"


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
    assert MANIFEST["execution_source"] == "oci_resource_manager"
    assert [step["key"] for step in MANIFEST["run_steps"]] == [
        "queue", "credentials", "compartment", "plan", "apply", "application", "complete",
    ]


def test_transient_preflight_id_is_not_persisted_as_deployment_ownership():
    runtime_fields = {field["name"] for field in MANIFEST["preflight"]["runtime_fields"]}
    assert not {"deployment_id", "operation_id"} & runtime_fields
    assert not {"deployment_id", "operation_id"} & set(MANIFEST["preflight"]["output_inputs"])
    variables = (ROOT / "terraform/b_variables.tf").read_text(encoding="utf-8")
    assert not re.search(r'variable\s+"(?:deployment_id|operation_id)"', variables)
    assert "deployment_binding" in MANIFEST["outputs"]
    assert "deployment_id" not in MANIFEST["outputs"]
