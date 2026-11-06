"""Read-only Apify account, actor and pinned build checks. Never starts runs."""
from datetime import datetime, timezone
import re
import httpx
from pydantic import SecretStr
from .runtime.actor_profiles import PROFILES


MESSAGES = {
    401: ('unauthenticated', 'Replace an invalid or revoked Apify token.'),
    402: ('restricted', 'Check actor rental/subscription and account billing restrictions; no run was started.'),
    403: ('access_denied', 'Check token scopes, actor permission and rental/subscription restrictions.'),
    404: ('absent_or_invisible', 'Check the exact actor/build ID and access for this account.'),
    429: ('indeterminate', 'Apify rate limited this read. Retry later without starting a run.'),
}


def _read(client, path, token, params=None):
    # Only these documented read surfaces may receive the bearer credential.
    if not re.fullmatch(r'/users/me|/actors/[A-Za-z0-9]+(?:/builds)?', path):
        raise ValueError('Unsupported preflight read surface')
    try:
        response = client.get('https://api.apify.com/v2' + path,
            headers={'Authorization': 'Bearer ' + token.get_secret_value()},
            params=params, timeout=15, follow_redirects=False)
        if response.status_code != 200:
            status, message = MESSAGES.get(response.status_code,
                ('indeterminate', 'Apify read failed. Check connectivity/service availability; no run was started.'))
            return None, {'status': status, 'http_status': response.status_code, 'message': message}
        data = response.json().get('data')
        if not isinstance(data, dict):
            raise ValueError('Malformed response')
        return data, None
    except (httpx.HTTPError, ValueError, TypeError, AttributeError):
        return None, {'status': 'indeterminate', 'message': 'Unable to verify this Apify read. No run was started.'}


def check_apify(token: SecretStr, *, client=None, profiles=None, max_build_pages=3):
    if not isinstance(token, SecretStr) or not 20 <= len(token.get_secret_value()) <= 512 or any(
            ord(c) <= 32 for c in token.get_secret_value()):
        raise ValueError('Invalid Apify credential')
    if type(max_build_pages) is not int or not 1 <= max_build_pages <= 5:
        raise ValueError('Build reads must be bounded')
    if client is None:
        with httpx.Client() as session:
            return check_apify(token, client=session, profiles=profiles, max_build_pages=max_build_pages)
    profiles = PROFILES if profiles is None else profiles
    account, error = _read(client, '/users/me', token)
    result = {'checked_at': datetime.now(timezone.utc).isoformat(), 'account': error or {'status': 'authenticated'},
              'actors': [], 'execution_permission': 'not_verified', 'apify_starts': 0}
    if error or not isinstance(account.get('id'), str) or not account['id']:
        result['account'] = error or {'status': 'indeterminate', 'message': 'Account identity missing.'}
        result['reads_verified'] = False
        return result
    # Never retain account body: it may contain email, profile or proxy secrets.
    for name, profile in profiles.items():
        row = {'profile': name, 'actor_id': profile['actor_id'], 'build': profile['build'],
               'execution_permission': 'not_verified', 'entitlement': 'not_verified'}
        result['actors'].append(row)
        if profile.get('unavailable_reason'):
            row.update(status='suspended', required=False)
            continue
        row['required'] = True
        actor, error = _read(client, '/actors/' + profile['actor_id'], token)
        if error:
            row.update(error)
            continue
        if actor.get('id') != profile['actor_id']:
            row.update(status='identity_mismatch', message='Actor identity differs from the pinned registry.')
            continue
        row['actor_visibility'] = 'verified'
        for page in range(max_build_pages):
            builds, error = _read(client, '/actors/' + profile['actor_id'] + '/builds', token,
                                  {'limit': 1000, 'offset': page * 1000, 'desc': 'true'})
            if error:
                row.update(error)
                break
            items = builds.get('items')
            if not isinstance(items, list) or not all(isinstance(item, dict) for item in items):
                row.update(status='indeterminate', message='Malformed build metadata.')
                break
            pinned = [item for item in items if item.get('buildNumber') == profile['build']]
            if len(pinned) == 1:
                row.update(status='visible' if pinned[0].get('status') == 'SUCCEEDED' else 'build_unavailable')
                break
            if len(pinned) > 1:
                row.update(status='indeterminate', message='Pinned build identity is ambiguous.')
                break
            if len(items) < 1000:
                row.update(status='build_absent_or_invisible', message='Pinned build not visible. Do not use latest.')
                break
        else:
            row.update(status='not_verified', message='Bounded build search exhausted; no execution permission established.')
    result['reads_verified'] = all(row['status'] in {'visible', 'suspended'} for row in result['actors'])
    return result
