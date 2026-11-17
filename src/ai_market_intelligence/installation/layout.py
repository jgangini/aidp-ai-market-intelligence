"""Versioned roots: an old receipt must never be interpreted as a new installation."""
from pathlib import PurePosixPath

from ..errors import AidpProvisionConflict

ROOT = "/Workspace/.artifact/ai_market_intelligence"
AGENT_ROOT = ROOT + "/agents"
LEGACY_AGENT_ROOT = AGENT_ROOT + "/ai_market_intelligence"


def agent_root(state):
    version = state.get("layout_version", 1)
    if type(version) is not int or version not in (1, 2):
        raise ValueError("Unsupported agent layout; preserve the installation")
    return AGENT_ROOT if version == 2 else LEGACY_AGENT_ROOT


def metadata_root(state):
    root = state.get('agent_metadata_root', agent_root(state))
    if root not in (AGENT_ROOT, LEGACY_AGENT_ROOT):
        raise ValueError('Unsupported agent metadata root; preserve the installation')
    return root


def require_source_tree(client, workspace, root, names):
    """Reject obsolete code and folders even when every required file exists."""
    expected = set(names) | {'agent-manifest.json'}
    allowed_folders = {str(parent) for name in expected for parent in PurePosixPath(name).parents if str(parent) != '.'}
    files, folders, stack = set(), set(), [root]
    while stack:
        folder = stack.pop()
        if len(files) + len(folders) > 1000 or len(PurePosixPath(folder).parts) > 30:
            raise AidpProvisionConflict('The native source tree exceeds its bounded inventory.')
        for row in client._list(f'/workspaces/{workspace}/objects', params={'path': folder}):
            path, kind = row.get('path'), row.get('type')
            if (not isinstance(path, str) or str(PurePosixPath(path).parent) != folder
                    or '\\' in path or any(part in {'.', '..'} for part in path.split('/'))
                    or kind not in {'FILE', 'FOLDER'}):
                raise AidpProvisionConflict('The native source listing escaped its expected folder.')
            name = path.removeprefix(root + '/')
            if kind == 'FOLDER':
                if name not in allowed_folders or name in folders:
                    raise AidpProvisionConflict('The native source tree contains an unexpected or duplicate folder.')
                folders.add(name)
                stack.append(path)
            else:
                if name not in expected or name in files:
                    raise AidpProvisionConflict('The native source tree contains an unexpected or duplicate file.')
                files.add(name)
    if files != expected:
        raise AidpProvisionConflict('The native source tree is missing required files or its ownership descriptor.')
