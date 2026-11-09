"""Check current tracked and untracked source without reading ignored private files."""
from pathlib import Path
import re
import subprocess

root = Path(__file__).resolve().parents[1]
names = set(subprocess.check_output(
    ['git', 'ls-files', '--cached', '--others', '--exclude-standard', '-z'], cwd=root
).decode().split('\0'))
failures = []
if not any(names):
    raise SystemExit('No tracked/staged source; stage the candidate before scanning.')
for name in filter(None, names):
    path = Path(name)
    if any(part in {'.oci', '.local', '.aidp-local', '.venv', '.terraform', '__pycache__'} for part in path.parts):
        failures.append(name + ': private/generated path')
        continue
    if path.suffix in {'.tfstate', '.tfvars', '.pem', '.key', '.pyc'}:
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
if failures:
    raise SystemExit('\n'.join(failures))
print('Current tracked/untracked source boundary and credential-pattern checks passed.')
