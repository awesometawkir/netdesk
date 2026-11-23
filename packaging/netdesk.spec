# Build on the target OS. Only public resources are bundled; never the repo tree.
from pathlib import Path
from PyInstaller.utils.hooks import collect_data_files, copy_metadata

root = Path(SPECPATH).parent
resources = [(str(root / 'assets'), 'assets'), (str(root / 'README.md'), '.'),
             (str(root / 'docs' / 'PRESETS.md'), 'docs'),
             (str(root / 'docs' / 'PORTABLE.md'), 'docs'),
             (str(root / 'tools' / 'serial'), 'tools/serial')]
a = Analysis([str(root / 'netdesk_desktop.py')], pathex=[str(root)],
             datas=resources + collect_data_files('ntc_templates') + copy_metadata('netmiko'),
             hiddenimports=['netdesk_serial_gui', 'netdesk_serial', 'netdesk_batch'],
             excludes=['tkinter', 'matplotlib', 'IPython', 'pytest'],
             noarchive=False)
pyz = PYZ(a.pure)
gui = EXE(pyz, a.scripts, [], exclude_binaries=True, name='NetDesk',
          console=False, debug=False, strip=False, upx=False)
cli = EXE(pyz, a.scripts, [], exclude_binaries=True, name='NetDeskCLI',
          console=True, debug=False, strip=False, upx=False)
coll = COLLECT(gui, cli, a.binaries, a.datas, strip=False, upx=False, name='NetDesk')
