"""Renames must not make source validation crash or omit newly moved files."""
from pathlib import Path
import subprocess
import sys


def test_source_check_handles_deleted_tracked_files_and_scans_untracked_replacements(tmp_path):
    repo = Path(__file__).resolve().parents[1]
    subprocess.run(['git', 'init', '--quiet'], cwd=tmp_path, check=True)
    (tmp_path / 'scripts').mkdir()
    script = tmp_path / 'scripts/check_repository.py'
    script.write_bytes((repo / 'scripts/check_repository.py').read_bytes())
    old = tmp_path / 'old.py'
    old.write_text('value = 1\n', encoding='utf-8')
    subprocess.run(['git', 'add', 'old.py'], cwd=tmp_path, check=True)
    old.unlink()
    new = tmp_path / 'new.py'
    new.write_text('value = 1\n', encoding='utf-8')
    result = subprocess.run([sys.executable, str(script)], cwd=tmp_path, capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    new.write_text('from ' + 'app.private import value\n', encoding='utf-8')
    result = subprocess.run([sys.executable, str(script)], cwd=tmp_path, capture_output=True, text=True)
    assert result.returncode != 0 and 'new.py: monolith import' in result.stderr
