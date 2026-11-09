"""One resumable, ownership-bound Market Intelligence TEST installation."""
from __future__ import annotations
import asyncio
import copy
import hashlib
import json
import re
import time
import traceback
from datetime import datetime, timezone
from urllib.parse import urlsplit
from uuid import UUID
from ai_market_intelligence.transport import AidpClient
from ai_market_intelligence.errors import AidpProvisionConflict, AidpProvisionError, AidpProvisionPending, AidpRequestError
from ai_market_intelligence.package import load_package
from ai_market_intelligence.installation.agent_package import agent_package, validate_agent_config
from ai_market_intelligence.installation.compute import COMPUTE_NAME, prepare_compute
from ai_market_intelligence.installation.credentials import ensure_apify_credential, _project_credential, existing_project_reference, created_by_operation
from ai_market_intelligence.runtime.runtime_versions import TARGET_GRAPH_PACKAGES, RUNTIME_DISTRIBUTIONS
from ai_market_intelligence.installation.playground import MarketPlaygroundClient, deployment_identity
from ai_market_intelligence.installation.stream import MarketGatewayStream
from ai_market_intelligence.installation.resources import resource
from .installation.layout import AGENT_ROOT, LEGACY_AGENT_ROOT, agent_root, metadata_root
from .runtime.package_identity import file_hashes, inventory_digest, source_identity, validate_inventory
MODULE_ID = 'ai_market_intelligence'
ROOT = '/Workspace/.artifact/' + MODULE_ID
CONTROL_ROOT = ROOT + '/.control'
LEGACY_CONTROL_ROOT = '/Workspace/.control/modules/' + MODULE_ID
PHASES = ('verify', 'credentials', 'compute', 'files', 'agent', 'permissions', 'test', 'ready')
UNSUPPORTED_LANGGRAPH_ERROR = 'TEST requires the selected managed LangGraph profile; reconcile the dependency environment before acceptance.'

def runtime_config_hash(config):
    return hashlib.sha256(json.dumps(config, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()).hexdigest()


def validated_runtime_config(config):
    validate_agent_config(config)
    return copy.deepcopy(config)


def paused_runtime_config(config):
    """Initial installation binds a model without authorizing research."""
    config = validated_runtime_config(config)
    if config['research_enabled'] or (config.get('services') is not None and
            set(config['services']) != {'model_id', 'region', 'compartment_id'}):
        raise AidpProvisionConflict('The installer requires an exact paused model binding.')
    return copy.deepcopy(config)


def installation_phases(state):
    return (PHASES + (('migration',) if isinstance(state.get('migration'), dict) else ())
            + (('rebuild',) if isinstance(state.get('rebuild'), dict) else ()))

def public_status(manifest=None):
    state = manifest or {}
    config = validated_runtime_config(state.get('runtime_config', {'schema_version': 1, 'research_enabled': False}))
    model_binding = config.get('services', {})
    pack = load_package(MODULE_ID, require_available=False)
    phases = installation_phases(state)
    suffix = state.get('resources', {}).get('apify_token_suffix', '')
    result = {'module_id': MODULE_ID, 'display_name': 'AI Market Intelligence', 'deployment_surface': 'TEST', 'status': state.get('status', 'not_installed'), 'installed': state.get('phase') == 'ready', 'phase': state.get('phase', 'not_installed'), 'enabled': False, 'operation_id': state.get('operation_id'), 'operation_type': 'install', 'message': state.get('message', ''), 'bundled_version': pack.pack_version, 'target_pack_sha256': state.get('pack_sha256'), 'bundled_pack_sha256': pack.pack_sha256, 'reuse_existing': state.get('reuse_existing', True), 'completed_phases': list(phases[:phases.index(state['phase'])]) if state.get('phase') in phases else [], 'apify_credential_status': 'configured' if state.get('resources', {}).get('apify_credential_key') else 'missing', 'apify_credential_owned_by_operation': bool(state.get('resources', {}).get('apify_credential_key')), 'apify_token_suffix': suffix if state.get('resources', {}).get('apify_credential_key') and isinstance(suffix, str) and re.fullmatch('[A-Za-z0-9]{4}', suffix) else '', 'test_sessions': state.get('test_sessions', [])}
    result['configured_agent_model_id'] = model_binding.get('model_id', '')
    result.update(research_enabled=config['research_enabled'],
                  enabled=config['research_enabled'] and state.get('phase') == 'ready')
    maintenance = state.get('maintenance')
    if isinstance(maintenance, dict):
        result['maintenance'] = {key: maintenance[key] for key in ('operation_id', 'action', 'phase', 'status') if key in maintenance}
        if maintenance.get('status') == 'running':
            result.update(status='updating', phase=maintenance.get('phase', 'maintenance'), enabled=False)
    return result

class MarketProbeStream(MarketGatewayStream):

    def __init__(self):
        super().__init__(require_response=False)
        self.result = None
        self.completed_probe = False

    def event(self):
        packets = super().event()
        if self.lines and b'\n'.join(self.lines).strip() != b'[DONE]':
            event = json.loads(b'\n'.join(self.lines))
            response = event.get('response') or {}
            completed = event.get('type') == 'response.completed' or str(response.get('status', '')).lower() == 'completed'
            self.completed_probe = self.completed_probe or bool(self.result and completed)
        return packets

    def finish(self):
        super().finish()
        if not self.completed_probe:
            raise ValueError('Native probe has no completed response')

    def public_output(self, output):
        if not isinstance(output, list) or len(output) > 256:
            return super().public_output(output)
        for message in output:
            results = super().public_output([message])
            if results and str(message.get('status', '')).lower() == 'completed':
                self.completed_probe = True
        return []

    def public_text(self, text):
        if not isinstance(text, str):
            raise ValueError('Invalid native probe text')
        try:
            result = json.loads(text)
        except ValueError:
            return []
        if isinstance(result, dict) and result.get('mode') == 'market_runtime_probe':
            if self.result is not None and result != self.result:
                raise ValueError('Native probe changed within one response')
            self.result = result
            return [result]
        return []

class MarketAidpClient(AidpClient):
    installation_root = ROOT
    control_root = CONTROL_ROOT
    compute_name = COMPUTE_NAME
    _agent_name = MODULE_ID

    def _read_installation(self, workspace):
        receipts = [(root, self._workspace_json(workspace, root + '/installer.json', 'Invalid Market Intelligence installation receipt'))
                    for root in (CONTROL_ROOT, LEGACY_CONTROL_ROOT)]
        present = [(root, state) for root, state in receipts if state is not None]
        if len(present) > 1:
            raise AidpProvisionConflict('Installation receipts exist at both control roots; preserve them for reconciliation.')
        root, state = present[0] if present else (CONTROL_ROOT, None)
        if state is not None:
            try:
                agent_root(state)
                metadata_root(state)
            except ValueError as exc:
                raise AidpProvisionConflict(str(exc)) from None
        if state is not None and (state.get('module_id') != MODULE_ID or state.get('installer_version') != 2 or any(key in state for key in ('runtime_version', 'mode', 'previous_operation_id')) or (state.get('publication_root') != self.installation_root) or (state.get('workspace') != workspace) or (state.get('phase') not in installation_phases(state))):
            raise AidpProvisionConflict('An incompatible installation receipt exists; preserve it for reconciliation.')
        self.control_root = root
        return state

    def _save(self, state):
        state['updated_at'] = datetime.now(timezone.utc).isoformat()
        self._upload_file(state['workspace'], self.control_root + '/installer.json', json.dumps(state, sort_keys=True, separators=(',', ':')).encode())

    async def status(self):

        def observe():
            state = self._read_installation(str(self._workspace()['key']))
            result = public_status(state or {})
            result['administrator_ocid'] = (state or {}).get('owner')
            if state and state['phase'] == 'test' and state['resources'].get('agent_key'):
                test = MarketPlaygroundClient(self.settings)
                try:
                    sessions = test._list(f"/workspaces/{state['workspace']}/agentFlowSessions", params={'agentFlowKey': [state['resources']['agent_key']], 'deploymentType': 'TEST'})
                    result['test_sessions'] = [{'value': row['key'], 'label': row.get('displayName') or row.get('name') or row['key']} for row in sessions if row.get('key')]
                finally:
                    test.session.close()
            expected_key = (state or {}).get('resources', {}).get('apify_credential_key', '')
            result['apify_credential_owned_by_operation'] = False
            try:
                existing = _project_credential(self)
                if existing:
                    existing_project_reference(existing, expected_key)
                    result['apify_credential_status'] = 'configured'
                    result['apify_credential_owned_by_operation'] = bool(expected_key or (state and created_by_operation(existing, state)))
                else:
                    result['apify_credential_status'] = 'conflict' if expected_key else 'missing'
            except RuntimeError:
                result['apify_credential_status'] = 'conflict'
            except (AidpProvisionError, AidpProvisionPending):
                result['apify_credential_status'] = 'unknown'
            result['apify_credential_available'] = result['apify_credential_status'] == 'configured'
            if not result['apify_credential_available']:
                result.pop('apify_token_suffix', None)
            return result
        return await asyncio.to_thread(observe)

    async def operate(self, user_ocid, operation_id, *, reuse_existing, token=None, test_session_id=None):
        if str(UUID(operation_id)) != operation_id:
            raise AidpProvisionConflict('A stable installation operation is required.')
        async with self._locks.setdefault(MODULE_ID, asyncio.Lock()):
            return await asyncio.to_thread(self._advance, user_ocid, operation_id, reuse_existing, token, test_session_id)

    def _advance(self, user, operation, reuse, token, session):
        workspace = str(self._workspace()['key'])
        state = self._read_installation(workspace)
        if state and state.get('phase') in {'migration', 'rebuild'}:
            raise AidpProvisionConflict('Resume the recorded migration or replacement before installing or updating this agent.')
        if state is None:
            config = self._selected_runtime_config()
            self._ensure_workspace_layout(workspace, ('/Workspace/.artifact', self.installation_root, CONTROL_ROOT, self.installation_root + '/agents', AGENT_ROOT))
            state = {'installer_version': 2, 'layout_version': 2, 'module_id': MODULE_ID, 'workspace': workspace, 'publication_root': self.installation_root, 'operation_id': operation, 'owner': user, 'reuse_existing': reuse, 'phase': 'verify', 'status': 'installing', 'resources': {}, 'runtime_config': config, 'pack_sha256': load_package(MODULE_ID, require_available=False).pack_sha256}
            self._save(state)
        if (state.get('maintenance') or {}).get('status') == 'running':
            raise AidpProvisionConflict('Resume the recorded maintenance operation; installation cannot overwrite its candidate.')
        if state['operation_id'] != operation or state['owner'] != user or state['reuse_existing'] is not reuse:
            raise AidpProvisionConflict('Resume the saved installation with the same administrator and settings.')
        self._assert_candidate(state)
        if state['phase'] == 'ready':
            return public_status(state)
        pending_status = 'installing'
        state['status'] = pending_status
        state['message'] = ''
        state.pop('native_error', None)
        self._save(state)
        try:
            next_phase = self._step(state, token, session)
            if next_phase:
                state['phase'] = next_phase
            else:
                phases = installation_phases(state)
                state['phase'] = phases[phases.index(state['phase']) + 1]
            state['status'] = 'active' if state['phase'] == 'ready' else pending_status
            self._save(state)
        except AidpProvisionPending as exc:
            state['message'] = str(exc)
            self._save(state)
        except AidpProvisionConflict as exc:
            state.update(status='awaiting_action', message=str(exc))
            self._save(state)
        except AidpRequestError as exc:
            state.update(status='error', native_error=exc.diagnostic, message=f"A native AIDP request failed (HTTP {exc.diagnostic['status_code']}). Resources were preserved; review the installation receipt before resuming.")
            self._save(state)
        except Exception as exc:
            state.update(status='error', native_error={'exception_type': type(exc).__name__, 'frames': [{'function': frame.name, 'line': frame.lineno} for frame in traceback.extract_tb(exc.__traceback__)[-5:]]}, message='This step failed. Its resources were preserved; resume the same operation after reviewing the native service.')
            self._save(state)
        return public_status(state)

    def _compute_step(self, state):
        resources, operation = (state['resources'], state['operation_id'])
        resources.setdefault('compute_creation_operation', operation)
        self._save(state)
        result = prepare_compute(self, state['workspace'], resources['compute_creation_operation'], expected_key=resources.get('agent_compute_key', ''), name=self.compute_name)
        resources['agent_compute_key'] = result['compute_key']
        self._save(state)
        if result['state'] == 'STOPPED':
            self._request('POST', f"/workspaces/{state['workspace']}/clusters/{result['compute_key']}/actions/start", payload={}, retry_scope='market-start:' + state['operation_id'])
        if result['state'] != 'ACTIVE':
            raise AidpProvisionPending('Waiting for the dedicated AI Compute.', 'compute')

    def _assert_package(self, state):
        if state['pack_sha256'] != load_package(MODULE_ID, require_available=False).pack_sha256:
            raise AidpProvisionConflict('Resume the installation with its exact saved package; the bundled candidate changed.')

    def _credentials(self, state, token):
        resources = state['resources']
        shared = self._shared_oci_credential()
        if shared is None:
            if resources.get('shared_oci_create_requested'):
                raise AidpProvisionConflict('Shared AidpRuntime creation was submitted but is not visible. Reconcile it before resuming; another shared credential will not be created.')
            resources['shared_oci_create_requested'] = True
            self._save(state)
            shared_key, _ = self._ensure_governance_credential()
        else:
            shared_key = shared.get('key') or shared.get('id')
        if not shared_key or resources.get('shared_oci_credential_key', shared_key) != shared_key:
            raise AidpProvisionConflict('The shared OCI credential identity changed; it was preserved.')
        resources['shared_oci_credential_key'] = shared_key
        existing = _project_credential(self)
        if existing and (state['reuse_existing'] or created_by_operation(existing, state)):
            try:
                reference = existing_project_reference(existing, resources.get('apify_credential_key', ''))
            except RuntimeError as exc:
                raise AidpProvisionConflict(str(exc)) from None
            if not created_by_operation(existing, state) and resources.get('apify_credential_key') != reference['credential_key']:
                resources.pop('apify_token_suffix', None)
        elif token:
            if not existing:
                if resources.get('apify_create_requested'):
                    raise AidpProvisionConflict('The credential creation was submitted but is not visible. Reconcile it before resuming; another secret will not be created automatically.')
                resources['apify_create_requested'] = True
                value = token.get_secret_value()
                if value.startswith('apify_api_') and re.fullmatch('[A-Za-z0-9]{4}', value[-4:]):
                    resources['apify_token_suffix'] = value[-4:]
                self._save(state)
            reference = ensure_apify_credential(self, token, state['operation_id'], expected_key=resources.get('apify_credential_key', ''))
        else:
            raise AidpProvisionConflict('Enter an Apify token or explicitly reuse the existing project credential.')
        resources['apify_credential_key'] = reference['credential_key']

    def _selected_runtime_config(self):
        return paused_runtime_config({'schema_version': 1, 'research_enabled': False, 'services': {
            'model_id': self.settings.agent_model_id, 'region': self.settings.aidp_region,
            'compartment_id': self.settings.compartment_id}})

    def _runtime_config(self, state):
        return validated_runtime_config(state.get('runtime_config', {'schema_version': 1, 'research_enabled': False}))

    def _descriptor(self, state):
        descriptor = {'module_id': 'ai_market_intelligence', 'schema_version': 1, 'operation_id': state['operation_id'], 'workspace': state['workspace'], 'owner': state['owner'], 'pack_sha256': state['pack_sha256']}
        if 'layout_version' in state:
            descriptor['layout_version'] = state['layout_version']
        if 'runtime_config' in state:
            descriptor['runtime_config_sha256'] = runtime_config_hash(self._runtime_config(state))
        return descriptor

    def _assert_candidate(self, state):
        try:
            self._assert_package(state)
        except AidpProvisionConflict:
            if state['phase'] not in {'test', 'ready'} or not state['resources'].get('test_intent'):
                raise
            self._saved_test_package(state)

    def _saved_test_package(self, state):
        root = agent_root(state)
        descriptor = self._workspace_json(state['workspace'], root + '/agent-manifest.json', 'Invalid current package ownership')
        if descriptor != self._descriptor(state):
            raise AidpProvisionConflict('The submitted current package ownership changed.')
        expected_hashes = state['resources'].get('package_hashes')
        if state.get('layout_version') == 2:
            try:
                validate_inventory(expected_hashes)
                if 'package-manifest.json' not in expected_hashes:
                    raise ValueError('The modular package manifest is missing from its receipt')
            except ValueError as exc:
                raise AidpProvisionConflict(str(exc)) from None
        files = {}
        for name in (expected_hashes if state.get('layout_version') == 2 else ('market_intelligence.py', 'requirements.txt')):
            body, _ = self._workspace_object(state['workspace'], root + '/' + name, phase='content')
            if isinstance(body, str):
                body = body.encode('utf-8')
            if not isinstance(body, bytes):
                raise AidpProvisionConflict('The submitted current package cannot be read back exactly.')
            files[name] = body
        hashes = file_hashes(files)
        digest = inventory_digest(hashes)
        resources = state['resources']
        if digest != resources['test_intent'].get('package_sha256') or source_identity(files) != resources.get('source_sha256') or (expected_hashes is not None and hashes != expected_hashes):
            raise AidpProvisionConflict('The submitted current package bytes changed; its candidate was not replaced.')

    def _step(self, state, token, session):
        phase = state['phase']
        if phase == 'verify':
            if not all((self.settings.objectstorage_namespace, self.settings.compartment_id)):
                raise AidpProvisionConflict('Configure the OCI model, compartment and artifacts storage before installation.')
            self._ensure_governance_bucket()
        elif phase == 'files':
            self._files(state)
        elif phase == 'agent':
            self._agent(state)
        elif phase == 'test':
            self._test_step(state, session)
        elif phase == 'credentials':
            self._credentials(state, token)
        elif phase == 'compute':
            self._compute_step(state)
        elif phase == 'permissions':
            for path, assignment in ((f"/workspaces/{state['workspace']}/agents/{state['resources']['agent_key']}", 'assignAgentPermissionDetails'), (f"/workspaces/{state['workspace']}/clusters/{state['resources']['agent_compute_key']}", 'assignClusterPermissionDetails')):
                self._ensure_role_permission(path, assignment, 'AI_DATA_PLATFORM_ADMIN', 'ADMIN')
        else:
            raise AidpProvisionConflict('This phase does not belong to the current TEST installation.')

    def _files(self, state):
        workspace, resources = (state['workspace'], state['resources'])
        root = agent_root(state)
        if root != AGENT_ROOT:
            raise AidpProvisionConflict('Legacy layout requires its saved installer; prepare an explicit layout migration before update.')
        descriptor = self._descriptor(state)
        previous = self._workspace_json(workspace, root + '/agent-manifest.json', 'Invalid current package ownership')
        if previous is not None and previous != descriptor:
            raise AidpProvisionConflict('Existing current files belong to another installation; they were preserved.')
        agents = self._agents(workspace, self._agent_name)
        if len(agents) > 1 or (agents and (previous != descriptor or resources.get('agent_key') != agents[0]['key'])):
            raise AidpProvisionConflict('The existing current agent requires its own installation receipt; it was not adopted.')
        if agents and self._agent_deployments(f"/workspaces/{workspace}/agents/{agents[0]['key']}/deployments"):
            raise AidpProvisionConflict('An existing production deployment requires a separately reviewed update.')
        source, assets = agent_package(self._runtime_config(state))
        published = {'market_intelligence.py': source, **assets}
        if previous is None:
            for name in published:
                body, headers = self._workspace_object(workspace, root + '/' + name, phase='content')
                if self._workspace_object_exists(body, headers):
                    raise AidpProvisionConflict('Unowned files already exist in the current folder; no source was overwritten.')
        self._upload_file(workspace, root + '/agent-manifest.json', json.dumps(descriptor, sort_keys=True, separators=(',', ':')).encode(), repair_drift=False)
        self._ensure_folder(workspace, root + '/core')
        self._ensure_folder(workspace, root + '/core/decisions')
        for name, content in published.items():
            self._upload_file(workspace, root + '/' + name, content, repair_drift=False)
        resources['source_sha256'] = source_identity(published)
        resources['package_hashes'] = file_hashes(published)

    def _agent(self, state):
        workspace, resources = (state['workspace'], state['resources'])
        root = agent_root(state)
        descriptor = self._workspace_json(workspace, root + '/agent-manifest.json', 'Invalid current package ownership')
        if descriptor != self._descriptor(state):
            raise AidpProvisionConflict('The current package ownership changed before creating its agent.')
        declared = resource('agents')
        if declared['name'] != self._agent_name or declared['pathInfo'] != AGENT_ROOT or declared['deployment_surface'] != 'TEST':
            raise AidpProvisionConflict('Current agent descriptor identity changed.')
        expected = {key: declared[key] for key in ('type', 'pathInfo', 'entryFilePath', 'dependenciesFilePath')}
        expected.update(displayName=declared['name'], computeKey=resources['agent_compute_key'], description='AI Market Intelligence paused TEST; operation=' + state['operation_id'])
        rows = self._agents(workspace, self._agent_name)
        if not rows:
            if resources.get('agent_create_requested'):
                raise AidpProvisionConflict('Agent creation was submitted but is not visible; reconcile it before another start.')
            resources['agent_create_requested'] = True
            self._save(state)
            self._request('POST', f'/workspaces/{workspace}/agents', payload={**expected, 'sessionConfig': {'variables': {}}}, retry_scope='market-agent:' + state['operation_id'])
            rows = self._agents(workspace, self._agent_name)
        if len(rows) != 1:
            raise AidpProvisionPending('Waiting for the recorded current agent identity.', 'agent')
        key = rows[0]['key']
        if resources.get('agent_key', key) != key or not resources.get('agent_create_requested'):
            raise AidpProvisionConflict('The current agent identity differs from this installation.')
        detail = self._request('GET', f'/workspaces/{workspace}/agents/{key}')
        if detail.get('key') != key or any((detail.get(field) != value for field, value in expected.items())):
            raise AidpProvisionConflict('The current source, compute or identity binding changed; no agent was updated.')
        resources['agent_key'] = key

    def _test_step(self, state, session_key):
        resources, workspace = (state['resources'], state['workspace'])
        client = MarketPlaygroundClient(self.settings)
        try:
            if not resources.get('test_intent'):
                source, assets = agent_package(self._runtime_config(state))

                def submitted(intent):
                    resources['test_intent'] = intent
                    self._save(state)
                resources['test_receipt'] = client.refresh(workspace, resources['agent_key'], resources['agent_compute_key'], source, assets=assets, expected_workspace=workspace, agent_root=agent_root(state), initialize=True, on_submit=submitted)
                self._save(state)
                raise AidpProvisionPending('Preparing the paused current candidate in TEST.', 'test')
            rows = client.test_deployments(workspace, resources['agent_key'])
            if not rows:
                raise AidpProvisionConflict('TEST was submitted but is not visible. Reconcile the recorded operation; no duplicate was submitted.')
            row = rows[0]
            if row.get('agentFlowComputeKey') != resources['agent_compute_key']:
                raise AidpProvisionConflict('The TEST compute differs from this installation.')
            previous = resources['test_intent'].get('previous_version')
            if row.get('lifecycleState') != 'ACTIVE' or (previous is not None and row.get('deploymentVersion') == previous):
                raise AidpProvisionPending('Waiting for the exact current TEST revision.', 'test')
            identity = deployment_identity(row)
            sessions = client._list(f'/workspaces/{workspace}/agentFlowSessions', params={'agentFlowKey': [resources['agent_key']], 'deploymentType': 'TEST'})
            candidates = scoped_test_sessions(sessions, resources['agent_key'], resources['agent_compute_key'])
            state['test_sessions'] = [{'value': item['key'], 'label': item.get('displayName') or item['key']} for item in candidates]
            session_key = self._test_session(client, state, sessions, session_key)
            detail = client._request('GET', f'/workspaces/{workspace}/agentFlowSessions/{session_key}')
            if not bound_test_sessions([detail], resources['agent_key'], resources['agent_compute_key'], row['endpointUrl']):
                raise AidpProvisionConflict('The selected session does not match the exact TEST endpoint, agent and compute.')
            config = self._runtime_config(state)
            resources['test_verified'] = self._probe_market(row['endpointUrl'], session_key, resources['source_sha256'],
                **({'research_enabled': True, 'runtime_profile': config['services']['runtime_profile']}
                   if config['research_enabled'] else {}))
            resources['test_verified']['deployment_identity'] = identity
            if client.test_deployments(workspace, resources['agent_key']) != rows:
                raise AidpProvisionConflict('TEST changed during the probe; this revision was not accepted.')
        finally:
            client.session.close()

    def _test_session(self, client, state, sessions, selected):
        resources, workspace = (state['resources'], state['workspace'])
        allowed = {item['value'] for item in state['test_sessions']}
        if selected:
            if selected not in allowed:
                raise AidpProvisionConflict('Select a TEST session bound to this current agent and compute.')
            return selected
        recorded = resources.get('test_session_key')
        if recorded:
            if recorded not in allowed:
                raise AidpProvisionConflict('The recorded TEST session is not visible with its exact binding; no new session was created.')
            return recorded
        label = self._agent_name + ' ' + state['operation_id']
        matches = [item for item in sessions if item.get('key') in allowed and (item.get('displayName') or item.get('name')) == label]
        if resources.get('test_session_requested'):
            if len(matches) != 1:
                raise AidpProvisionConflict('Reconcile the submitted TEST session; no duplicate session was created.')
            resources['test_session_key'] = matches[0]['key']
            self._save(state)
            return matches[0]['key']
        if matches:
            raise AidpProvisionConflict('An unowned TEST session already uses this operation identity.')
        self._create_test_session(client, state, label)

    def _create_test_session(self, client, state, label):
        resources, workspace = (state['resources'], state['workspace'])
        resources['test_session_requested'] = True
        self._save(state)
        result = client._request('POST', f'/workspaces/{workspace}/agentFlowSessions', payload={'agentFlowKey': resources['agent_key'], 'agentFlowComputeKey': resources['agent_compute_key'], 'displayName': label, 'deploymentType': 'TEST'}, retry_scope='market-session:' + state['operation_id'])
        if isinstance(result, dict) and result.get('key'):
            resources['test_session_key'] = result['key']
            self._save(state)
        raise AidpProvisionPending('Verifying the recorded TEST session binding.', 'test')

    def _probe_market(self, endpoint, session, source_sha256, *, research_enabled=False, runtime_profile=None):
        validate_test_endpoint(endpoint, self.settings.aidp_region)
        response = self.session.post(endpoint, auth=self.signer, stream=True, allow_redirects=False, timeout=(10, 120), json={'isStreamEnabled': True, 'input': [{'role': 'user', 'content': [{'type': 'INPUT_TEXT', 'text': 'inspect_runtime'}]}], 'sessionId': session, 'session_id': session, 'metadata': {}}, headers={'Accept': 'text/event-stream', 'x-session-id': session, 'x-deployment-type': 'TEST'})
        decoder = MarketProbeStream()
        deadline = time.monotonic() + 120
        try:
            response.raise_for_status()
            if 'text/event-stream' not in response.headers.get('Content-Type', ''):
                raise AidpProvisionError('TEST did not return native SSE.')
            for chunk in response.iter_content(chunk_size=8192):
                if time.monotonic() > deadline:
                    raise AidpProvisionError('TEST exceeded the verification window.')
                decoder.feed(chunk)
            decoder.finish()
            return market_probe_receipt(decoder.result or {}, source_sha256,
                                        research_enabled=research_enabled, runtime_profile=runtime_profile)
        finally:
            response.close()

    async def agent_endpoint(self):

        def observe():
            workspace = str(self._workspace()['key'])
            state = self._read_installation(workspace)
            if state and (state.get('maintenance') or {}).get('status') == 'running':
                raise AidpProvisionConflict('The maintenance candidate is not yet verified. Resume its recorded operation.')
            if not state or state['phase'] != 'ready':
                raise AidpProvisionConflict('Complete the current TEST installation before copying its endpoint.')
            client = MarketPlaygroundClient(self.settings)
            try:
                rows = client.test_deployments(workspace, state['resources']['agent_key'])
                verified = state['resources'].get('test_verified', {})
                if len(rows) != 1 or rows[0].get('lifecycleState') != 'ACTIVE' or rows[0].get('agentFlowComputeKey') != state['resources']['agent_compute_key'] or (deployment_identity(rows[0]) != verified.get('deployment_identity')):
                    raise AidpProvisionConflict('The installed TEST revision changed; revalidate it before use.')
                validate_test_endpoint(rows[0]['endpointUrl'], self.settings.aidp_region)
                return rows[0]['endpointUrl']
            finally:
                client.session.close()
        return await asyncio.to_thread(observe)


def validate_test_endpoint(endpoint, region):
    url = urlsplit(endpoint)
    if url.scheme != 'https' or url.hostname != 'gateway.aidp.' + region + '.oci.oraclecloud.com' or url.port or url.username or url.password or url.query or url.fragment:
        raise AidpProvisionError('Unexpected TEST endpoint returned by AIDP.')

def bound_test_sessions(sessions, agent_key, compute_key, endpoint):
    return [item for item in scoped_test_sessions(sessions, agent_key, compute_key) if item.get('endpointUrl') == endpoint]

def scoped_test_sessions(sessions, agent_key, compute_key):
    expected = {'agentFlowKey': agent_key, 'agentFlowComputeKey': compute_key}
    return [item for item in sessions if item.get('key') and item.get('deploymentType') in (None, 'TEST') and (item.get('lifecycleState') in (None, 'ACTIVE')) and all((item.get(key) == value for key, value in expected.items()))]

def market_probe_receipt(result, source_sha256, *, research_enabled=False, runtime_profile=None):
    if type(research_enabled) is not bool:
        raise ValueError('Expected research mode must be explicit')
    if research_enabled and not isinstance(runtime_profile, dict):
        raise ValueError('Active verification requires its configured exact runtime profile')
    expected = {'source_sha256': source_sha256, 'schema_version': 1, 'research_enabled': research_enabled, 'business_tables_required': False, 'authenticated_session_bound': True, 'model_calls': 0, 'apify_starts': 0}
    if any((type(result.get(key)) is not type(value) or result[key] != value for key, value in expected.items())):
        raise AidpProvisionError('TEST did not verify this active current source and authenticated session.' if research_enabled
                                 else 'TEST did not verify this paused current source and authenticated session.')
    versions = result.get('versions')
    if not isinstance(versions, dict) or not versions.get('python') or (not isinstance(versions.get('packages'), dict)):
        raise AidpProvisionError('TEST returned incomplete runtime observations.')
    if any((not versions['packages'].get(name) for name in RUNTIME_DISTRIBUTIONS)):
        raise AidpProvisionError('TEST is missing required runtime packages.')
    if any(versions['packages'].get(name) != version for name, version in TARGET_GRAPH_PACKAGES.items()):
        raise AidpProvisionError(UNSUPPORTED_LANGGRAPH_ERROR)
    if research_enabled and versions != runtime_profile:
        raise AidpProvisionError('TEST active runtime differs from its configured exact dependency profile.')
    return {'surface': 'TEST', 'source_sha256': source_sha256, 'research_enabled': research_enabled, 'versions': versions, 'model_calls': 0, 'apify_starts': 0, 'semantic_accepted': False, 'mcp_verified': False}
