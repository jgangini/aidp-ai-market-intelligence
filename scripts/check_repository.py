"""Check current tracked and untracked source without reading ignored private files."""
from pathlib import Path
import argparse
import ast
import json
import re
import subprocess
import tomllib
from ai_market_intelligence.package import private_source_path


def check_sentrux_rules(root, *, required=False):
    path = root / '.sentrux/rules.toml'
    if not path.exists():
        if required:
            raise SystemExit('Sentrux rules are required for architecture validation.')
        return
    schema_path = root / '.sentrux/rules.schema.json'
    if any(item.is_symlink() or not item.resolve().is_relative_to(root) for item in (path, schema_path)):
        raise SystemExit('Sentrux policy must use repository-local regular files.')
    from jsonschema import Draft202012Validator, validators
    from jsonschema.exceptions import SchemaError
    try:
        schema = json.loads(schema_path.read_bytes())
        Draft202012Validator.check_schema(schema)
        raw = path.read_bytes()
        if len(raw) > 65536:
            raise ValueError('Policy size exceeds its bound')
        config = tomllib.loads(raw.decode('utf-8'))
        json.dumps(config, allow_nan=False)
    except (OSError, TypeError, ValueError, SchemaError):
        raise SystemExit('Invalid Sentrux rules or schema; inspect .sentrux/rules.toml and rules.schema.json.') from None
    integer_types = Draft202012Validator.TYPE_CHECKER.redefine('integer', lambda checker, value: type(value) is int)
    policy_validator = validators.extend(Draft202012Validator, type_checker=integer_types)
    errors = list(policy_validator(schema).iter_errors(config))
    if errors:
        raise SystemExit('Invalid Sentrux rules: unsupported fields or values; see .sentrux/rules.schema.json.')
    constraints = config.get('constraints', {})
    active = any(key != 'no_god_files' or value for key, value in constraints.items())
    if not active and not config.get('boundaries'):
        raise SystemExit('Sentrux policy must enable at least one rule.')

root = Path(__file__).resolve().parents[1]
parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument('--rules-only', action='store_true', help='Validate the supported Sentrux policy before scanning')
args = parser.parse_args()
if args.rules_only:
    check_sentrux_rules(root, required=True)
    print('Sentrux policy schema and active rules verified.')
    raise SystemExit(0)
names = set(subprocess.check_output(
    ['git', 'ls-files', '--cached', '--others', '--exclude-standard', '-z'], cwd=root
).decode().split('\0'))
failures = []
if not any(names):
    raise SystemExit('No tracked/staged source; stage the candidate before scanning.')
for name in filter(None, names):
    path = Path(name)
    if any(part.casefold() in {'.oci', '.local', '.aidp-local', '.venv', '.terraform', '__pycache__'} for part in path.parts):
        failures.append(name + ': private/generated path')
        continue
    public_guidance = name in {'.codex/AGENTS.md', '.codex/improvement-log.md'}
    if private_source_path(path) and not public_guidance:
        failures.append(name + ': private/generated extension')
        continue
    current = root / path
    if current.is_symlink():
        failures.append(name + ': source symlink')
        continue
    if not current.exists():
        continue  # Tracked deletions have no working-tree bytes to inspect.
    data = current.read_bytes()
    if re.search(rb'-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----\r?\n', data):
        failures.append(name + ': private key material')
    if re.search(rb'apify_api_[A-Za-z0-9]{20,}', data):
        failures.append(name + ': Apify token material')
    if path.suffix == '.py' and re.search(rb'(?:from|import) app(?:\.|\s)', data):
        failures.append(name + ': monolith import')
    agent_source = name.startswith('aidp/workspaces/default/files/agents/')
    if path.suffix == '.py' and (agent_source or name.startswith('src/')):
        forbidden = ('scripts', 'src', 'ai_market_intelligence') if agent_source else ('scripts',)
        for node in ast.walk(ast.parse(data, filename=name)):
            modules = ([alias.name for alias in node.names] if isinstance(node, ast.Import)
                       else [node.module or ''] if isinstance(node, ast.ImportFrom) and node.level == 0 else [])
            for module in modules:
                if module.split('.')[0] in forbidden:
                    failures.append(name + ': forbidden production import: ' + module)
if failures:
    raise SystemExit('\n'.join(failures))
check_sentrux_rules(root, required=True)
print('Current tracked/untracked source boundary and credential-pattern checks passed.')
