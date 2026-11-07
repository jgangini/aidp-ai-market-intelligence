"""Workbench navigation from the exact signed platform metadata."""
import re
from urllib.parse import urlencode, urlsplit

import oci

from .errors import AidpProvisionConflict


def _workbench_host(platform, region):
    endpoint = str(getattr(platform, 'web_socket_endpoint', '') or '')
    parsed = urlsplit(endpoint if '://' in endpoint else 'wss://' + endpoint)
    host = parsed.hostname or ''
    if not host:
        alias = str(getattr(platform, 'alias_key', '') or '')
        region_key = next((key for key, region_name in oci.regions.REGIONS_SHORT_NAMES.items()
                           if region_name == region), '')
        if not alias or not region_key:
            return ''
        host = (alias if alias.endswith(region_key) else alias + region_key) + '.datalake.oci.oraclecloud.com'
    if (parsed.scheme not in ('wss', 'https') or (endpoint and parsed.netloc.lower() not in (host.lower(), host.lower() + ':443'))
            or not re.fullmatch(r'[a-z0-9-]+\.datalake\.oci\.oraclecloud\.com', host, re.IGNORECASE)):
        raise AidpProvisionConflict('Signed Workbench endpoint is not a supported Oracle host.')
    return host.lower()


def workbench_url(client, platform):
    host = _workbench_host(platform, client.settings.aidp_region)
    if not host:
        return ''
    identity = oci.identity.IdentityClient(client._oci_config, signer=client.signer)
    tenancy = identity.get_tenancy(client._oci_config['tenancy']).data
    domain = identity.get_domain(client.settings.identity_domain_id).data
    if (tenancy.id != client._oci_config['tenancy'] or domain.id != client.settings.identity_domain_id
            or not tenancy.name or not domain.display_name):
        raise AidpProvisionConflict('Workbench sign-in identity could not be verified.')
    return 'https://' + host.lower() + '#?' + urlencode({'tenant': tenancy.name, 'domain': domain.display_name})
