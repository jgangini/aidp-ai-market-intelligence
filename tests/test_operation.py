import json
import io
from types import SimpleNamespace as NS

import pytest

from ai_market_intelligence.errors import AidpProvisionConflict
from ai_market_intelligence.operation import installation_lock, release_lock


OP = '11111111-1111-4111-8111-111111111111'


class StorageError(Exception):
    def __init__(self, status):
        self.status = status


class ConditionalStorage:
    def __init__(self, *, missing_etag=False):
        self.objects = {}
        self.deleted = []
        self.missing_etag = missing_etag

    def put_object(self, namespace, bucket, name, body, **conditions):
        if conditions.get('if_none_match') == '*' and name in self.objects:
            raise StorageError(412)
        if 'if_match' in conditions and (name not in self.objects or self.objects[name][1] != conditions['if_match']):
            raise StorageError(412)
        etag = 'current-etag' if name not in self.objects else self.objects[name][1] + '-next'
        self.objects[name] = (body, etag)
        return NS(headers={} if self.missing_etag else {'etag': etag})

    def get_object(self, namespace, bucket, name):
        raw, etag = self.objects[name]
        return NS(data=NS(raw=io.BytesIO(raw)), headers={'etag': etag})

    def delete_object(self, namespace, bucket, name, **conditions):
        if self.objects[name][1] != conditions['if_match']:
            raise StorageError(412)
        self.deleted.append((name, conditions['if_match']))
        del self.objects[name]


def lock_client(storage):
    return NS(settings=NS(aidp_platform_id='ocid1.aidataplatform.oc1..fixture',
                          objectstorage_namespace='namespace', artifacts_bucket_name='artifacts'),
              _workspace=lambda: {'key': 'workspace'}, object_storage=storage,
              _oci=NS(exceptions=NS(ServiceError=StorageError)))


def test_concurrent_installer_cannot_enter_or_remove_first_holders_lock():
    storage = ConditionalStorage()
    client = lock_client(storage)
    with installation_lock(client, OP):
        saved = next(iter(storage.objects.values()))
        assert json.loads(saved[0])['operation_id'] == OP
        with pytest.raises(AidpProvisionConflict, match='lock exists'):
            with installation_lock(client, OP):
                pytest.fail('Concurrent execution entered the protected installer')
        assert storage.objects and not storage.deleted
    assert not storage.objects and storage.deleted[0][1] == 'current-etag'


def test_lease_can_retain_lock_until_external_operation_is_reconciled():
    storage = ConditionalStorage()
    client = lock_client(storage)
    with installation_lock(client, OP) as lease:
        assert lease.operation_id == OP and not lease.retain_on_exit
        lease.retain_on_exit = True
    assert storage.objects and not storage.deleted
    with pytest.raises(AidpProvisionConflict, match='lock exists'):
        with installation_lock(client, OP):
            pytest.fail('An unresolved external operation must keep writers excluded')
    release_lock(client, expected_etag='current-etag')
    assert not storage.objects


def test_retained_lease_does_not_suppress_external_operation_failure():
    storage = ConditionalStorage()
    client = lock_client(storage)
    with pytest.raises(RuntimeError, match='unknown submission'):
        with installation_lock(client, OP) as lease:
            lease.retain_on_exit = True
            raise RuntimeError('unknown submission')
    assert storage.objects and not storage.deleted


def test_default_lease_still_releases_after_error_before_submission():
    storage = ConditionalStorage()
    client = lock_client(storage)
    with pytest.raises(ValueError, match='validation'):
        with installation_lock(client, OP):
            raise ValueError('validation')
    assert not storage.objects and storage.deleted


def test_uncertain_lock_write_preserves_object_and_blocks_retry():
    storage = ConditionalStorage(missing_etag=True)
    client = lock_client(storage)
    with pytest.raises(AidpProvisionConflict, match='uncertain'):
        with installation_lock(client, OP):
            pytest.fail('An unconfirmed lock must not authorize installation')
    assert storage.objects and not storage.deleted
    with pytest.raises(AidpProvisionConflict, match='lock exists'):
        with installation_lock(client, OP):
            pytest.fail('Blind retry entered an uncertain lock')


def test_timeout_after_lock_write_preserves_uncertain_lease():
    storage = ConditionalStorage()
    original_put = storage.put_object
    def timeout_after_commit(*args, **kwargs):
        original_put(*args, **kwargs)
        raise TimeoutError('response lost after conditional write')
    storage.put_object = timeout_after_commit
    with pytest.raises(TimeoutError):
        with installation_lock(lock_client(storage), OP):
            pytest.fail('Lost write response cannot authorize the installer')
    assert storage.objects and not storage.deleted
    storage.put_object = original_put
    with pytest.raises(AidpProvisionConflict, match='lock exists'):
        with installation_lock(lock_client(storage), OP):
            pytest.fail('A timeout cannot justify another holder')


def test_release_uses_exact_etag_and_stale_holder_cannot_delete_replacement():
    storage = ConditionalStorage()
    client = lock_client(storage)
    with installation_lock(client, OP):
        with pytest.raises(StorageError) as failure:
            release_lock(client, expected_etag='stale-etag')
        assert failure.value.status == 412 and storage.objects
    assert len(storage.deleted) == 1


def test_normal_failure_releases_only_confirmed_lease():
    storage = ConditionalStorage()
    with pytest.raises(RuntimeError, match='installer failed'):
        with installation_lock(lock_client(storage), OP):
            raise RuntimeError('installer failed')
    assert not storage.objects and storage.deleted[0][1] == 'current-etag'


@pytest.mark.parametrize('etag', ['', 'x\n', 'x\x00'])
def test_explicit_recovery_rejects_empty_or_unsafe_etag(etag):
    storage = ConditionalStorage()
    with pytest.raises(ValueError, match='ETag'):
        release_lock(lock_client(storage), expected_etag=etag)
    assert not storage.deleted


def test_migration_guard_blocks_legacy_hooks_and_allows_new_conditional_leases():
    storage = ConditionalStorage()
    client = lock_client(storage)
    with installation_lock(client, OP):
        client.retain_installation_guard = True
    name, (body, etag) = next(iter(storage.objects.items()))
    assert json.loads(body)['state'] == 'idle' and not storage.deleted
    with pytest.raises(StorageError):
        storage.put_object('namespace', 'artifacts', name, b'old hook', if_none_match='*')
    with installation_lock(lock_client(storage), OP):
        assert json.loads(storage.objects[name][0])['state'] == 'held'
        with pytest.raises(AidpProvisionConflict, match='lock exists'):
            with installation_lock(lock_client(storage), OP):
                pytest.fail('Concurrent hook acquired the persistent guard')
    assert json.loads(storage.objects[name][0])['state'] == 'idle' and not storage.deleted


def test_explicit_guard_recovery_preserves_old_hook_exclusion():
    storage = ConditionalStorage()
    client = lock_client(storage)
    client.retain_installation_guard = True
    with installation_lock(client, OP):
        pass
    name, (_, etag) = next(iter(storage.objects.items()))
    release_lock(client, expected_etag=etag)
    assert name in storage.objects and not storage.deleted
