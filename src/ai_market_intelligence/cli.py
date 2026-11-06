"""Explicit local package checks and authenticated native install/status operations."""
import argparse
import asyncio
import json
from pathlib import Path
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
    parser.add_argument('command', choices=['package-check', 'status', 'install', 'release-lock'])
    parser.add_argument('--settings', type=Path)
    parser.add_argument('--operation-id')
    parser.add_argument('--token-file', type=Path, help='Private file containing the Apify token; never passed on the command line')
    parser.add_argument('--test-session-id')
    parser.add_argument('--expected-etag')
    parser.add_argument('--confirm-no-running-hook', action='store_true')
    parser.add_argument('--apply', action='store_true')
    args = parser.parse_args(argv)
    if args.command == 'package-check':
        print(json.dumps(package_check()))
        return 0
    if not args.settings:
        parser.error('--settings is required')
    if args.command != 'status' and not args.apply:
        parser.error('--apply is required for this mutation')
    from .settings import Settings
    from .client import MarketAidpClient
    from .authorization import verify_administrator
    from .lifecycle import install
    from .operation import release_lock
    client = None
    try:
        settings = Settings.model_validate_json(args.settings.read_text(encoding='utf-8'))
        client = MarketAidpClient(settings)
        verify_administrator(client)
        if args.command == 'status':
            result = asyncio.run(client.status())
        elif args.command == 'release-lock':
            if not args.expected_etag or not args.confirm_no_running_hook:
                parser.error('Lock recovery requires --expected-etag and --confirm-no-running-hook')
            release_lock(client, expected_etag=args.expected_etag)
            result = {'lock_released': True}
        else:
            if not args.operation_id:
                parser.error('--operation-id is required')
            token = SecretStr(args.token_file.read_text(encoding='utf-8').strip()) if args.token_file else None
            if token is None:
                parser.error('--token-file is required for the read-only Apify preflight; existing secrets are never exported')
            from .apify_preflight import check_apify
            if token and not check_apify(token)['reads_verified']:
                raise ValueError('Apify read preflight failed; no installation was started')
            result = install(client, args.operation_id, token=token, test_session_id=args.test_session_id)
        print(json.dumps(result))
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
