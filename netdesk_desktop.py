"""Packaged entry point for GUI and console executables."""
import json
import os
import sys
from pathlib import Path


def smoke_test():
    from PySide6.QtCore import QLocale
    from PySide6.QtWidgets import QApplication
    from netdesk_gui import MainWindow, STYLE, spin
    from netdesk_paths import data_root, RESOURCE_ROOT
    from netdesk_command_gui import CommandLibrary
    from netdesk_commands import suggestions
    QLocale.setDefault(QLocale('bn_BD'))
    app = QApplication([])
    app.setStyle('Fusion')
    app.setStyleSheet(STYLE)
    assert spin(1, 65535, 22).text() == '22'
    window = MainWindow()
    window.show()
    app.processEvents()
    library = CommandLibrary()
    assert library.tree.topLevelItemCount() > 0
    assert suggestions('dis cur') == ['display current-configuration']
    assert (RESOURCE_ROOT / 'assets' / 'netdesk.svg').exists()
    root = data_root()
    root.mkdir(parents=True, exist_ok=True)
    probe = root / '.netdesk-write-test'
    probe.write_text('ok', encoding='utf-8')
    probe.unlink()
    if '--screenshot' in sys.argv:
        window.grab().save(sys.argv[sys.argv.index('--screenshot') + 1])
    window.close()
    return 0


if __name__ == '__main__':
    if '--smoke-test' in sys.argv:
        raise SystemExit(smoke_test())
    if Path(sys.executable).stem == 'NetDeskCLI' or '--cli' in sys.argv:
        from netdesk import main
        raise SystemExit(main([arg for arg in sys.argv[1:] if arg != '--cli']))
    from netdesk_gui import main
    raise SystemExit(main())
