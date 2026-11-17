"""Bind selected administrator to the actual Identity Domain and AIDP roles."""
import json
from urllib.parse import urlsplit
import oci
from .errors import AidpProvisionConflict


def resolve_domain(client):
    iam = oci.identity.IdentityClient(client._oci_config, signer=client.signer)
    domain = iam.get_domain(client.settings.identity_domain_id).data
    try:
        parsed = urlsplit(str(domain.url or ''))
        port = parsed.port
    except ValueError as exc:
        raise AidpProvisionConflict('The selected Identity Domain endpoint is invalid.') from exc
    if domain.id != client.settings.identity_domain_id or domain.lifecycle_state != 'ACTIVE' or (
            parsed.scheme != 'https' or parsed.username or parsed.password or parsed.query or parsed.fragment or
            port not in (None, 443) or parsed.path not in ('', '/') or
            not parsed.hostname or not parsed.hostname.endswith('.oraclecloud.com')):
        raise AidpProvisionConflict('The selected Identity Domain is not active or its endpoint is invalid.')
    return 'https://' + parsed.hostname


def verify_administrator(client, *, roles_required=True, domain_client=None):
    settings = client.settings
    if domain_client is None:
        endpoint = resolve_domain(client)
        domain_client = oci.identity_domains.IdentityDomainsClient(
            client._oci_config, service_endpoint=endpoint, signer=client.signer)
    response = domain_client.list_users(filter='ocid eq ' + json.dumps(settings.admin_user_ocid),
                                        count=2, attributes='id,ocid,active,groups')
    rows = response.data.resources or []
    if len(rows) != 1 or rows[0].ocid != settings.admin_user_ocid or rows[0].active is not True:
        raise AidpProvisionConflict('Select an active user in this Identity Domain and revalidate the administrator.')
    if roles_required:
        users, groups = client._role_principal_ocids('AI_DATA_PLATFORM_ADMIN')
        if settings.admin_user_ocid not in users:
            effective = False
            # Validate group OCIDs through this domain; SCIM values are not OCI OCIDs.
            for group in sorted(groups):
                found = domain_client.list_groups(filter='ocid eq ' + json.dumps(group), count=2, attributes='id,ocid').data.resources or []
                if len(found) == 1 and found[0].ocid == group and any(
                        item.value == found[0].id for item in (rows[0].groups or [])):
                    effective = True
                    break
            if not effective:
                raise AidpProvisionConflict('Selected user lacks effective AI_DATA_PLATFORM_ADMIN. Revalidate after bootstrap or revocation.')
    return {'administrator_verified': True, 'roles_verified': roles_required}
