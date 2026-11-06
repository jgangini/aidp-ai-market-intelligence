"""Include canonical AIDP source assets in wheels without a second editable copy."""
import base64
import csv
import hashlib
import io
from pathlib import Path
import zipfile
from setuptools import setup
from setuptools.command.bdist_wheel import bdist_wheel


class WheelWithAssets(bdist_wheel):
    def run(self):
        super().run()
        root = Path(__file__).parent
        assets = {}
        for name in ('aidp', 'package'):
            for source in (root / name).rglob('*'):
                if source.is_file() and '__pycache__' not in source.parts and source.suffix != '.pyc':
                    assets['ai_market_intelligence/_assets/' + source.relative_to(root).as_posix()] = source.read_bytes()
        # Write deep Workspace paths directly into the standard wheel ZIP. Staging them
        # through bdist.win-amd64 exceeds Windows MAX_PATH on this repository path.
        filename = Path(self.dist_dir) / (self.wheel_dist_name + '-' + '-'.join(self.get_tag()) + '.whl')
        with zipfile.ZipFile(filename) as archive:
            payload = {entry.filename: archive.read(entry) for entry in archive.infolist()}
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


setup(cmdclass={'bdist_wheel': WheelWithAssets})
