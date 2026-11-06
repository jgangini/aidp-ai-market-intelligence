"""Deploy Studio lifecycle orchestration with paused TEST as the only install target."""
import asyncio
import time
from .authorization import verify_administrator
from .errors import AidpProvisionConflict, AidpProvisionPending
from .operation import installation_lock


def bind_new_administrator(client):
    """Assign the selected active identity on a newly owned platform, then read back."""
    verify_administrator(client, roles_required=False)
    user = client.settings.admin_user_ocid
    users, _ = client._role_principal_ocids('AI_DATA_PLATFORM_ADMIN')
    if user not in users:
        role = client._role('AI_DATA_PLATFORM_ADMIN')
        client._request('POST', '/roles/' + role['key'] + '/actions/addMember',
            payload={'assignees': [{'type': 'USER', 'target': user}]},
            phase='permissions', retry_scope='market-admin:' + user)
    verify_administrator(client)


def install(client, operation_id, *, token=None, reuse_existing=True, test_session_id=None,
            new_platform=False, max_steps=80, sleep=time.sleep):
    if not 1 <= max_steps <= 120:
        raise ValueError('Installer progress must be bounded')
    with installation_lock(client, operation_id):
        # Initial role assignment is allowed only before this product has a receipt.
        # Resume must observe a revocation rather than silently grant the role again.
        if new_platform and client._read_installation(str(client._workspace()['key'])) is None:
            bind_new_administrator(client)
        for _ in range(max_steps):
            # Re-check revocation on each advance, including every resumed invocation.
            verify_administrator(client)
            status = asyncio.run(client.operate(client.settings.admin_user_ocid, operation_id,
                reuse_existing=reuse_existing, token=token, test_session_id=test_session_id))
            if status['status'] in ('error', 'awaiting_action'):
                raise AidpProvisionConflict(status.get('message') or 'Reconcile the saved installation before resuming.')
            if status.get('installed'):
                return status
            sleep(5)
    raise AidpProvisionPending('Installation remains pending. Resume this deployment with the same operation and administrator.', 'installation')
