"""Run with a Python environment containing requirements.txt and PyInstaller."""
import platform
from pathlib import Path
import shutil
import tarfile
import subprocess
import sys

root = Path(__file__).resolve().parents[1]
subprocess.run([sys.executable, '-m', 'PyInstaller', '--noconfirm', '--clean',
                str(root / 'packaging' / 'netdesk.spec')], cwd=root, check=True)
name = f'NetDesk-{platform.system()}-{platform.machine()}'
if sys.platform == 'win32':
    archive = Path(shutil.make_archive(str(root / 'dist' / (name + '.partial')), 'zip', root / 'dist', 'NetDesk'))
    output = root / 'dist' / (name + '.zip')
else:
    archive = root / 'dist' / (name + '.partial.tar.gz')
    output = root / 'dist' / (name + '.tar.gz')
    with tarfile.open(archive, 'w:gz', compresslevel=3) as bundle:
        bundle.add(root / 'dist' / 'NetDesk', arcname='NetDesk')
archive.replace(output)
print(f'Portable package: {output}')
