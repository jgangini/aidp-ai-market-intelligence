"""Use the installed plugin's canonical sync/TEST journals inside owner maintenance."""
import hashlib
import json
from pathlib import Path
import re
import subprocess
import sys
import tomllib
from uuid import UUID

from .playground import MarketPlaygroundClient, deployment_identity
from ..errors import AidpProvisionConflict, AidpProvisionPending
from ..runtime.package_identity import file_hashes, inventory_digest, source_identity


class PluginTestDeployment(MarketPlaygroundClient):
    deployment_driver = 'plugin_cli'

    def __init__(self, settings, *, project, plugin_scripts, operation_id):
        super().__init__(settings)
        self.project = Path(project).resolve()
        self.scripts = Path(plugin_scripts).resolve()
        self.operation_id = operation_id
        if str(UUID(operation_id)) != operation_id:
            raise ValueError('Use the canonical maintenance operation identity')
        files = sorted((self.scripts / 'aidp_agent_flow').glob('*.py'))
        if not files or not (self.scripts / 'aidp_agent_flow/cli.py').is_file():
            raise ValueError('Select the reviewed installed AIDP plugin scripts')
        inventory = {path.name: hashlib.sha256(path.read_bytes()).hexdigest() for path in files}
        self.scripts_sha256 = hashlib.sha256(json.dumps(inventory, sort_keys=True).encode()).hexdigest()
        self.project_sha256 = self.driver_binding()['project_sha256']

    def driver_binding(self):
        config = tomllib.loads((self.project / 'aidp-agent.toml').read_text(encoding='utf-8'))
        for key in ('entry', 'dependencies'):
            config['agent'].pop(key)
        config_hash = hashlib.sha256(json.dumps(config, sort_keys=True).encode()).hexdigest()
        return {'kind': self.deployment_driver, 'project': str(self.project),
                'scripts': str(self.scripts), 'scripts_sha256': self.scripts_sha256,
                'project_sha256': config_hash}

    def cli(self, *arguments):
        files = sorted((self.scripts / 'aidp_agent_flow').glob('*.py'))
        inventory = {path.name: hashlib.sha256(path.read_bytes()).hexdigest() for path in files}
        current = hashlib.sha256(json.dumps(inventory, sort_keys=True).encode()).hexdigest()
        if current != self.scripts_sha256 or self.driver_binding()['project_sha256'] != self.project_sha256:
            raise AidpProvisionConflict('The pinned plugin or project configuration changed before its command.')
        code = ('import sys;sys.path.insert(0,sys.argv.pop(1));'
                'from aidp_agent_flow.cli import main;raise SystemExit(main())')
        command = [sys.executable, '-c', code, str(self.scripts), '--project', str(self.project),
                   '--json', 'native', *arguments]
        try:
            completed = subprocess.run(command, cwd=self.project, capture_output=True, timeout=180)
            if max(len(completed.stdout), len(completed.stderr)) > 2 * 1024 * 1024:
                raise ValueError('Oversized plugin reply')
            reply = json.loads(completed.stdout)
        except (OSError, ValueError, subprocess.TimeoutExpired):
            raise AidpProvisionPending('Plugin submission or readback is uncertain; reconcile its existing journal.', 'maintenance') from None
        if completed.returncode or reply.get('ok') is not True:
            folder = self.project / '.aidp-local/driver-errors'
            if folder.resolve() != folder:
                raise AidpProvisionConflict('The private driver error folder changed.')
            folder.mkdir(parents=True, exist_ok=True)
            path = folder / (self.operation_id.replace('-', '') + '.json')
            if not path.exists():
                path.write_bytes(completed.stdout)
            raise AidpProvisionConflict('The plugin refused the operation; preserve its canonical journal and inspect its status.')
        return reply['result']

    def prepare_candidate(self, state, files):
        config_path = self.project / 'aidp-agent.toml'
        if config_path.resolve() != config_path or state['maintenance']['operation_id'] != self.operation_id:
            raise AidpProvisionConflict('The plugin project or operation path changed.')
        text = config_path.read_text(encoding='utf-8')
        config = tomllib.loads(text)
        expected = {'workspace_key': state['workspace'], 'agent_key': state['resources']['agent_key'],
                    'compute_key': state['resources']['agent_compute_key'],
                    'remote_root': '/Workspace/.artifact/ai_market_intelligence/agents',
                    'metadata_root': '/Workspace/.artifact/ai_market_intelligence/agents'}
        if any(config.get('native', {}).get(key) != value for key, value in expected.items()):
            raise AidpProvisionConflict('The plugin project does not select this managed agent and exact roots.')
        executable = {key: value for key, value in files.items() if key != 'agent-manifest.json'}
        context = {'operation_id': self.operation_id, 'source_sha256': source_identity(executable)}
        identity = hashlib.sha256(json.dumps(context, sort_keys=True).encode()).hexdigest()[:32]
        relative = Path('.aidp-local/deploy') / identity / 'candidate'
        folder = self.project / relative
        if folder.resolve() != folder or not folder.resolve().is_relative_to(self.project):
            raise AidpProvisionConflict('The candidate folder escaped its owning project.')
        folder.mkdir(parents=True, exist_ok=True)
        marker = folder.parent / 'context.json'
        if marker.exists() and json.loads(marker.read_bytes()) != context:
            raise AidpProvisionConflict('The saved deployment folder belongs to another operation or source.')
        if not marker.exists():
            marker.write_text(json.dumps(context), encoding='utf-8')
        for name, content in files.items():
            path = folder / name
            if not path.resolve().is_relative_to(folder.resolve()) or path.is_symlink():
                raise AidpProvisionConflict('The fixed candidate escaped its private operation folder.')
            path.parent.mkdir(parents=True, exist_ok=True)
            if path.exists() and path.read_bytes() != content:
                raise AidpProvisionConflict('The saved plugin candidate changed; preserve its bytes.')
            if not path.exists():
                path.write_bytes(content)
        def replace_section(match):
            section = match.group(0)
            for key, name in (('entry', 'market_intelligence.py'), ('dependencies', 'requirements.txt')):
                section, count = re.subn(r'(?m)^' + key + r'\s*=.*$',
                    key + ' = ' + json.dumps((relative / name).as_posix()), section)
                if count != 1:
                    raise AidpProvisionConflict('The plugin project needs exactly one declared entry and dependency file.')
            return section
        updated, count = re.subn(r'(?ms)^\[agent\]\r?\n.*?(?=^\[|\Z)', replace_section, text)
        if count != 1:
            raise AidpProvisionConflict('The selected plugin project has no unique agent section.')
        if updated != text:
            backup = folder.parent / 'config-before.toml'
            if not backup.exists():
                backup.write_text(text, encoding='utf-8')
            temporary = folder.parent / 'config.pending'
            temporary.write_text(updated, encoding='utf-8')
            temporary.replace(config_path)
        return folder

    def sync_candidate(self, state, files):
        folder = self.prepare_candidate(state, files)
        journal = state['maintenance']
        package = inventory_digest(file_hashes(files))
        expected = {'version': 1, 'package_sha256': package, 'files': {
            name: sorted({side[name] for side in (journal['before_hashes'], journal['target_hashes']) if name in side})
            for name in files}}
        path = folder.parent / 'expected-existing.json'
        if path.exists() and json.loads(path.read_bytes()) != expected:
            raise AidpProvisionConflict('The saved source preconditions changed; preserve the operation.')
        if not path.exists():
            path.write_text(json.dumps(expected), encoding='utf-8')
        result = self.cli('sync', '--expected-inventory', path.relative_to(self.project).as_posix(), '--apply')
        if (result.get('readback_verified') is not True or result.get('preconditions_checked') is not True
                or result.get('package_sha256') != package):
            raise AidpProvisionConflict('The plugin did not verify candidate synchronization.')

    @staticmethod
    def refresh_keys(status):
        rows = [row for row in status['operations'] if row['kind'] == 'test-refresh']
        if any(not isinstance(row.get('operation_key'), str) or not row['operation_key'] for row in rows):
            raise AidpProvisionConflict('A plugin refresh lacks its exact operation identity; reconcile it first.')
        return {row['operation_key'] for row in rows}

    def refresh(self, workspace, agent_key, compute_key, source, *, assets, expected_workspace,
                agent_root, initialize, on_submit, metadata_descriptor, metadata_root=None):
        selected = tomllib.loads((self.project / 'aidp-agent.toml').read_text(encoding='utf-8'))['native']
        if (initialize or workspace != expected_workspace or workspace != selected['workspace_key']
                or agent_key != selected['agent_key'] or compute_key != selected['compute_key']
                or agent_root != selected['remote_root'] or metadata_root not in (None, selected['metadata_root'])):
            raise AidpProvisionConflict('This adapter updates only the existing TEST deployment.')
        files = {'market_intelligence.py': source, **assets, 'agent-manifest.json': metadata_descriptor}
        # Source sync already prepared this exact package; do not create another project or receipt.
        status = self.cli('status')
        recover = bool(status['test']) and all(row['lifecycleState'] == 'FAILED' for row in status['test'])
        recovery = ['--recover-failed'] if recover else []
        plan = self.cli('test-refresh', *recovery)
        if plan.get('up_to_date'):
            raise AidpProvisionConflict('TEST changed before its owner intent was recorded.')
        digest = hashlib.sha256(json.dumps(file_hashes({name: raw for name, raw in files.items()
            if name != 'agent-manifest.json'}), sort_keys=True, separators=(',', ':')).encode()).hexdigest()
        before = self.test_deployments(workspace, agent_key)
        if len(before) != 1:
            raise AidpProvisionConflict('The existing TEST revision is not uniquely readable.')
        intent = {'driver': self.deployment_driver, 'package_sha256': digest,
                  'plugin_package_sha256': plan['package_sha256'],
                  'previous_version': before[0].get('deploymentVersion'),
                  'plugin_operations_before': sorted(self.refresh_keys(status)), 'recover_failed': recover}
        on_submit(intent)
        result = self.cli('test-refresh', *recovery, '--apply')
        return {'driver': self.deployment_driver, 'package_sha256': digest, 'compute_key': compute_key,
                'deployment_type': 'TEST', 'plugin_submission': result}

    def reconcile_refresh(self, intent):
        status = self.cli('status')
        recovery = ['--recover-failed'] if intent['recover_failed'] else []
        fresh = self.refresh_keys(status) - set(intent['plugin_operations_before'])
        if any(row.get('state') == 'FAILED_RECONCILED' for row in status['operations']
               if row.get('operation_key') in fresh):
            return False
        if any(row.get('state') != 'RECONCILED' for row in status['operations']
               if row.get('operation_key') in fresh):
            raise AidpProvisionPending('The exact plugin refresh remains pending.', 'maintenance')
        plan = self.cli('test-refresh', *recovery)
        if plan['package_sha256'] != intent['plugin_package_sha256']:
            raise AidpProvisionConflict('The plugin candidate changed after the recorded owner intent.')
        if plan.get('up_to_date') is True:
            return True
        if fresh:
            if all(row.get('state') == 'RECONCILED' for row in status['operations']
                   if row.get('operation_key') in fresh):
                raise AidpProvisionConflict('The completed plugin operation did not verify its exact package.')
            raise AidpProvisionPending('The exact plugin refresh remains pending.', 'maintenance')
        config = tomllib.loads((self.project / 'aidp-agent.toml').read_text(encoding='utf-8'))['native']
        tests = self.test_deployments(config['workspace_key'], config['agent_key'])
        if len(tests) != 1 or deployment_identity(tests[0]) != intent['previous_identity']:
            raise AidpProvisionConflict('TEST changed outside the pinned plugin submission.')
        # No journaled POST exists: recover the gap after owner intent and before plugin admission.
        self.cli('test-refresh', *recovery, '--apply')
        raise AidpProvisionPending('The pinned plugin refresh was submitted; observe its existing operation.', 'maintenance')
