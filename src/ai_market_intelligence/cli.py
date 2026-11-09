"""Explicit local package checks and authenticated native install/status operations."""
import argparse
import asyncio
import json
import time
from pathlib import Path
from uuid import UUID
from pydantic import SecretStr
from .package import load_package
from .installation.agent_package import agent_package


def package_check():
    pack = load_package('ai_market_intelligence', require_available=False)
    source, assets = agent_package({'schema_version': 1, 'research_enabled': False})
    compile(source, 'market_intelligence.py', 'exec')
    return {'package_version': pack.pack_version, 'runtime_files': len(pack.runtime_files),
            'source_bytes': len(source), 'deployment_surface': 'TEST', 'research_enabled': False}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command', choices=['package-check', 'status', 'install', 'update', 'redeploy', 'rollback', 'activate-test', 'migrate', 'rebuild-development', 'release-lock'])
    parser.add_argument('--settings', type=Path)
    parser.add_argument('--operation-id')
    parser.add_argument('--token-file', type=Path, help='Private file containing the Apify token; never passed on the command line')
    parser.add_argument('--llm-api-key-file', type=Path, help='Protected OpenAI API key file; never a literal command argument')
    parser.add_argument('--test-session-id')
    parser.add_argument('--expected-etag')
    parser.add_argument('--confirm-no-running-hook', action='store_true')
    parser.add_argument('--apply', action='store_true')
    parser.add_argument('--project', type=Path, default=Path.cwd())
    parser.add_argument('--plugin-scripts', type=Path)
    parser.add_argument('--preserve-prod', action='store_true', help='Pin the existing PROD snapshot while updating only TEST through the plugin')
    parser.add_argument('--restore-migration', action='store_true')
    parser.add_argument('--research-binding', type=Path, help='Complete nonsecret runtime binding for explicit TEST activation')
    args = parser.parse_args(argv)
    if args.restore_migration and args.command != 'migrate':
        parser.error('--restore-migration only applies to migrate')
    if args.preserve_prod and (args.command not in {'update', 'redeploy', 'rollback'} or not args.plugin_scripts):
        parser.error('--preserve-prod requires update/redeploy/rollback and the reviewed --plugin-scripts')
    if bool(args.research_binding) != (args.command == 'activate-test'):
        parser.error('--research-binding is required only for activate-test')
    if args.command == 'package-check':
        print(json.dumps(package_check()))
        return 0
    if not args.settings:
        parser.error('--settings is required')
    if args.command not in {'status', 'rebuild-development'} and not args.apply:
        parser.error('--apply is required for this mutation')
    if args.command in ('install', 'update', 'redeploy', 'rollback', 'activate-test', 'migrate', 'rebuild-development'):
        try:
            if str(UUID(args.operation_id)) != args.operation_id:
                raise ValueError('Noncanonical UUID')
            if args.test_session_id and str(UUID(args.test_session_id)) != args.test_session_id:
                raise ValueError('Noncanonical UUID')
        except (TypeError, ValueError, AttributeError):
            parser.error('Canonical operation and TEST session UUIDs are required')
    from .settings import Settings
    from .client import MarketAidpClient
    from .authorization import verify_administrator
    from .lifecycle import install
    from .operation import release_lock
    client = None
    try:
        settings = Settings.model_validate_json(args.settings.read_text(encoding='utf-8'))
        llm_options = {}
        if args.llm_api_key_file:
            if settings.llm_auth_mode != 'openai' or args.command not in {'install', 'update', 'activate-test'}:
                raise ValueError('LLM API key input requires an API-key install, update or activation')
            from .runtime.config import validate_api_key
            with args.llm_api_key_file.open('r', encoding='utf-8') as stream:
                raw = stream.read(4098)
            if len(raw) > 4097:
                raise ValueError('LLM API key file exceeds its bound')
            llm_options['llm_api_key'] = SecretStr(validate_api_key(raw.rstrip('\r\n')))
        client = MarketAidpClient(settings)
        verify_administrator(client)
        if args.command == 'status':
            result = asyncio.run(client.status())
        elif args.command == 'activate-test':
            from .maintenance import maintain
            from .research_activation import read_research_binding
            result = maintain(client, 'update', args.operation_id, test_session_id=args.test_session_id,
                              runtime_config=read_research_binding(args.research_binding), **llm_options)
        elif args.command == 'rebuild-development':
            if not args.plugin_scripts or not (args.plugin_scripts / 'aidp_agent_flow/native_layout.py').is_file():
                parser.error('--plugin-scripts must select the reviewed layout-aware AIDP plugin')
            from .rebuild import prepare, advance
            from .errors import AidpProvisionPending
            for step in range(120 if args.apply else 1):
                previous, current = prepare(args.project, client, args.operation_id, args.plugin_scripts)
                try:
                    result = advance(client, previous, current, args.operation_id, apply=args.apply)
                    if not args.apply or result.get('status') == 'completed':
                        break
                except AidpProvisionPending as pending:
                    if pending.phase not in {'rebuild', 'test'}:
                        raise
                    result = {'status': 'pending', 'phase': 'rebuild'}
                time.sleep(2)
        elif args.command == 'migrate':
            if not args.plugin_scripts or not (args.plugin_scripts / 'aidp_agent_flow/native_lifecycle.py').is_file():
                parser.error('--plugin-scripts must select the installed AIDP plugin')
            from .migration import prepare, migrate, restore, saved_forward
            if args.restore_migration:
                native = saved_forward(args.project, client, args.operation_id, args.plugin_scripts)
                previous = prepare(args.project, client, args.operation_id, args.plugin_scripts, restoring=True)
                result = restore(client, previous, native, args.operation_id, max_steps=120)
            else:
                native = prepare(args.project, client, args.operation_id, args.plugin_scripts)
                result = migrate(client, native, args.operation_id, apply=True, max_steps=120)
        elif args.command == 'release-lock':
            if not args.expected_etag or not args.confirm_no_running_hook:
                parser.error('Lock recovery requires --expected-etag and --confirm-no-running-hook')
            release_lock(client, expected_etag=args.expected_etag)
            result = {'lock_released': True}
        else:
            if not args.operation_id:
                parser.error('--operation-id is required')
            token = None
            if args.command != 'rollback':
                token = SecretStr(args.token_file.read_text(encoding='utf-8').strip()) if args.token_file else None
                if token is None:
                    parser.error('--token-file is required for the read-only Apify preflight; existing secrets are never exported')
                from .apify_preflight import check_apify
                if not check_apify(token)['reads_verified']:
                    raise ValueError('Apify read preflight failed; no installation was started')
            if args.command == 'install':
                result = install(client, args.operation_id, token=token, test_session_id=args.test_session_id, **llm_options)
            else:
                from .maintenance import maintain
                native_factory = None
                if args.plugin_scripts:
                    from .installation.plugin_test import PluginTestDeployment
                    native_factory = lambda settings: PluginTestDeployment(settings, project=args.project,
                        plugin_scripts=args.plugin_scripts, operation_id=args.operation_id)
                result = maintain(client, args.command, args.operation_id, test_session_id=args.test_session_id,
                                  preserve_production=args.preserve_prod, native_factory=native_factory, **llm_options)
        print(json.dumps(result))
        if args.command == 'rebuild-development' and args.apply and result.get('status') != 'completed':
            return 2
        if args.command in ('update', 'redeploy', 'activate-test') and result.get('status') == 'rolled_back':
            return 1
        return 0
    except Exception:
        # External failures and validation exceptions can contain secret-bearing input.
        print(json.dumps({'status': 'blocked', 'message': 'Native operation could not be verified. Preserve the operation and resources; review protected diagnostics.'}))
        return 1
    finally:
        if client:
            asyncio.run(client.close())


if __name__ == '__main__':
    raise SystemExit(main())
