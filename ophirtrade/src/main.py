import sys
from PyQt6.QtWidgets import QApplication
from ui.main_window import OphirTradeIDE


def main():
    # 1. Initialize the Qt Application
    app = QApplication(sys.argv)

    # 2. Instantiate the Master Window
    ide = OphirTradeIDE()
    ide.show()

    # 3. Lock into the Event Loop
    sys.exit(app.exec())


if __name__ == "__main__":
    main()