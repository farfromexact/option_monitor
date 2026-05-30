from __future__ import annotations

import sys
from pathlib import Path

from PySide6.QtWidgets import QApplication

from ui.main_window import MainWindow
from utils.logger import configure_logging


def main() -> int:
    configure_logging()
    app = QApplication(sys.argv)
    app.setApplicationName("Option Monitor")
    window = MainWindow(config_path=Path("config/settings.yaml"))
    window.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
