from PyQt6.QtWidgets import (
    QMainWindow, QDockWidget, QListWidget, QTextEdit
)
from PyQt6.QtCore import Qt
from PyQt6.QtGui import QFont
from ui.editor import OphirCodeEditor
from ui.chart import OphirTradeChart

class OphirTradeIDE(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("OphirTrade - Quant Developer IDE")
        self.resize(1400, 900)

        # 1. Apply Dark Theme Styling
        self.setStyleSheet("""
            QMainWindow { background-color: #1e1e1e; }
            QDockWidget { color: #aaaaaa; font-weight: bold; }
            QDockWidget::title { background: #2d2d30; padding: 6px; }
            QTextEdit, QListWidget { background-color: #252526; color: #cccccc; border: none; }
        """)

        # 2. Set the Central Code Editor
        self.editor = OphirCodeEditor()
        self.editor.setText(
            "# 🚢 Ophir-AI Citadel Protocol\n# Write your Alpha here...\n\ndef execute_trade():\n    pass")
        self.setCentralWidget(self.editor)

        # 3. Build the Docks
        self._build_market_explorer()
        self._build_terminal()
        self._build_chart_dock()

    def _build_market_explorer(self):
        dock = QDockWidget("Market Explorer", self)
        dock.setAllowedAreas(Qt.DockWidgetArea.LeftDockWidgetArea | Qt.DockWidgetArea.RightDockWidgetArea)

        self.file_tree = QListWidget()
        self.file_tree.addItems(["🧠 citadel_ppo_v2.zip", "📜 mean_reversion.py", "⚙️ config.json"])

        dock.setWidget(self.file_tree)
        self.addDockWidget(Qt.DockWidgetArea.LeftDockWidgetArea, dock)

    def _build_terminal(self):
        dock = QDockWidget("Execution Logs", self)
        dock.setAllowedAreas(Qt.DockWidgetArea.BottomDockWidgetArea)

        self.terminal = QTextEdit()
        self.terminal.setFont(QFont("Consolas", 10))
        self.terminal.setReadOnly(True)
        self.terminal.setStyleSheet("background-color: #0d0d0d; color: #4af626;")
        self.terminal.append("[SYSTEM] OphirTrade Engine Initialized...")

        dock.setWidget(self.terminal)
        self.addDockWidget(Qt.DockWidgetArea.BottomDockWidgetArea, dock)

    def _build_chart_dock(self):
        """Snaps the pyqtgraph engine into the right side of the IDE."""
        dock = QDockWidget("Live Market Data (/NQ)", self)
        dock.setAllowedAreas(
            Qt.DockWidgetArea.RightDockWidgetArea | Qt.DockWidgetArea.LeftDockWidgetArea | Qt.DockWidgetArea.BottomDockWidgetArea)

        # Instantiate the chart widget we just built
        chart_widget = OphirTradeChart()

        dock.setWidget(chart_widget)
        self.addDockWidget(Qt.DockWidgetArea.RightDockWidgetArea, dock)