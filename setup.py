"""Include canonical AIDP source assets in wheels without a second editable copy."""
import base64
import csv
import hashlib
import io
from contextlib import contextmanager
from pathlib import Path
import sys
import zipfile
from setuptools import setup
from setuptools.command.bdist_wheel import bdist_wheel
from setuptools.command.sdist import sdist


@contextmanager
def source_package():
    root = Path(__file__).resolve().parent
    sys.path.insert(0, str(root / 'src'))
    try:
        from ai_market_intelligence import package
        if Path(package.__file__).resolve() != root / 'src/ai_market_intelligence/package.py':
            raise RuntimeError('The distribution validator belongs to another source checkout')
        if package.package_root('ai_market_intelligence').resolve() != root:
            raise RuntimeError('The distribution assets belong to another source root')
        yield root, package
    finally:
        sys.path.pop(0)


class SourceWithAssets(sdist):
    def make_release_tree(self, base_dir, files):
        destination = Path(base_dir)
        if destination.exists():
            raise FileExistsError('The source release directory must be fresh to exclude stale files')
        with source_package() as (root, package):
            assets = package.distribution_assets()
            other = []
            for name in files:
                relative = Path(name)
                if relative.parts[0] in {'aidp', 'package'} or package.private_source_path(relative):
                    continue
                package.source_file(root, relative.as_posix())
                other.append(relative.as_posix())
        super().make_release_tree(base_dir, other)
        for name, raw in assets.items():
            target = destination / name
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(raw)
        manifest = '\n'.join(sorted({*other, *assets})) + '\n'
        for name in other:
            if name.endswith('.egg-info/SOURCES.txt'):
                target = destination / name
                target.unlink()  # Break any hard link before replacing generated metadata.
                target.write_text(manifest, encoding='utf-8')


class WheelWithAssets(bdist_wheel):
    def run(self):
        super().run()
        prefix = 'ai_market_intelligence/_assets/'
        with source_package() as (_, package):
            assets = {prefix + name: raw for name, raw in package.distribution_assets().items()}
        # Write deep Workspace paths directly into the standard wheel ZIP. Staging them
        # through bdist.win-amd64 exceeds Windows MAX_PATH on this repository path.
        filename = Path(self.dist_dir) / (self.wheel_dist_name + '-' + '-'.join(self.get_tag()) + '.whl')
        with zipfile.ZipFile(filename) as archive:
            payload = {entry.filename: archive.read(entry) for entry in archive.infolist()
                       if not entry.filename.startswith(prefix)}
        record = next(name for name in payload if name.endswith('.dist-info/RECORD'))
        payload.pop(record)
        payload.update(assets)
        rows = []
        for name, content in payload.items():
            digest = base64.urlsafe_b64encode(hashlib.sha256(content).digest()).rstrip(b'=').decode()
            rows.append([name, 'sha256=' + digest, str(len(content))])
        rows.append([record, '', ''])
        output = io.StringIO(newline='')
        csv.writer(output).writerows(rows)
        payload[record] = output.getvalue().encode()
        with zipfile.ZipFile(filename, 'w', compression=zipfile.ZIP_DEFLATED) as archive:
            for name, content in payload.items():
                archive.writestr(name, content)


setup(cmdclass={'bdist_wheel': WheelWithAssets, 'sdist': SourceWithAssets})
