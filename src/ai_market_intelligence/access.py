"""Agent Hub navigation from the exact signed platform metadata."""
import re
from urllib.parse import urlencode, urlsplit

import oci

from .errors import AidpProvisionConflict


_ORACLE_HOST = re.compile(r'(?P<prefix>[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?)\.datalake\.oci\.oraclecloud\.com', re.IGNORECASE)


def _alias_prefix(platform, region):
    alias = str(getattr(platform, 'alias_key', '') or '').lower()
    region_key = next((key for key, region_name in oci.regions.REGIONS_SHORT_NAMES.items()
                       if region_name == region), '')
    if not alias or not region_key:
        return ''
    if not re.fullmatch(r'[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?', alias) or alias.endswith('agenthubagenthub'):
        raise AidpProvisionConflict('Signed platform alias is not a supported Oracle host prefix.')
    if alias.endswith(region_key + 'agenthub') or alias.endswith(region_key):
        return alias
    return alias + region_key


def _workbench_prefix(platform, region):
    endpoint = str(getattr(platform, 'web_socket_endpoint', '') or '')
    if not endpoint:
        return _alias_prefix(platform, region)
    parsed = urlsplit(endpoint if '://' in endpoint else 'wss://' + endpoint)
    host = parsed.hostname or ''
    if (parsed.scheme not in ('wss', 'https') or parsed.netloc.lower() not in (host.lower(), host.lower() + ':443')
            or not _ORACLE_HOST.fullmatch(host)):
        raise AidpProvisionConflict('Signed platform endpoint is not a supported Oracle host.')
    return _ORACLE_HOST.fullmatch(host).group('prefix').lower()


def _agent_hub_host(platform, region):
    prefix = _workbench_prefix(platform, region)
    if not prefix:
        return ''
    if prefix.endswith('agenthubagenthub'):
        raise AidpProvisionConflict('Signed Agent Hub endpoint has a duplicated host suffix.')
    # Agent Hub uses the verified Workbench prefix with its dedicated DNS suffix.
    hub = (prefix if prefix.endswith('agenthub') else prefix + 'agenthub') + '.datalake.oci.oraclecloud.com'
    if not _ORACLE_HOST.fullmatch(hub):
        raise AidpProvisionConflict('Agent Hub host exceeds the supported Oracle DNS format.')
    return hub


def agent_hub_url(client, platform):
    host = _agent_hub_host(platform, client.settings.aidp_region)
    if not host:
        return ''
    identity = oci.identity.IdentityClient(client._oci_config, signer=client.signer)
    tenancy = identity.get_tenancy(client._oci_config['tenancy']).data
    domain = identity.get_domain(client.settings.identity_domain_id).data
    if (tenancy.id != client._oci_config['tenancy'] or domain.id != client.settings.identity_domain_id
            or not tenancy.name or not domain.display_name):
        raise AidpProvisionConflict('Agent Hub sign-in identity could not be verified.')
    return 'https://' + host + '/static/ah/#?' + urlencode({'tenant': tenancy.name, 'domain': domain.display_name})
