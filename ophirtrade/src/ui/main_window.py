import os
from PyQt6.QtWidgets import (
    QMainWindow, QDockWidget, QListWidget, QTextEdit,
    QToolBar, QPushButton, QWidget, QHBoxLayout, QLabel
)
from PyQt6.QtCore import Qt, QSize
from PyQt6.QtGui import QFont, QKeySequence, QShortcut
from ui.editor import OphirCodeEditor
from ui.chart import OphirTradeChart
from ui.explorer import OphirFileExplorer
from engine.worker import OphirExecutionEngine

class OphirTradeIDE(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("OphirTrade - Quant Developer IDE")
        self.resize(1400, 900)

        # Track the currently open file so we can save it
        self.current_file_path = None

        self.setStyleSheet("""
            QMainWindow { background-color: #1e1e1e; }
            QDockWidget { color: #aaaaaa; font-weight: bold; }
            QDockWidget::title { background: #2d2d30; padding: 6px; }
            QTextEdit, QListWidget { background-color: #252526; color: #cccccc; border: none; }
            QToolBar { background-color: #2d2d30; border: none; spacing: 10px; padding: 5px; }
        """)

        # Central Code Editor
        self.editor = OphirCodeEditor()
        self.editor.setText(
            "# 🚢 Ophir-AI Citadel Protocol\n# Write your Alpha here...\n\ndef execute_trade():\n    pass")
        self.setCentralWidget(self.editor)

        # 1. Initialize the Terminal first (so the toolbar can print to it)
        self._build_terminal()

        # 2. Build the rest of the UI
        self._build_market_explorer()
        self._build_chart_dock()

        # 3. Ignite the Command Center
        self._build_top_toolbar()

        # Add the Save Shortcut (Ctrl+S or Cmd+S)
        self.save_shortcut = QShortcut(QKeySequence.StandardKey.Save, self)
        self.save_shortcut.activated.connect(self.save_current_file)

    def _build_market_explorer(self):
        dock = QDockWidget("Market Explorer", self)
        dock.setAllowedAreas(Qt.DockWidgetArea.LeftDockWidgetArea | Qt.DockWidgetArea.RightDockWidgetArea)

        # Instantiate our new native file explorer
        self.file_explorer = OphirFileExplorer(workspace_dir="./strategies")

        # When the user double clicks a file, catch the signal and load it
        self.file_explorer.file_loaded.connect(self.load_file_to_editor)

        dock.setWidget(self.file_explorer)
        self.addDockWidget(Qt.DockWidgetArea.LeftDockWidgetArea, dock)

    def load_file_to_editor(self, file_path, content):
        """Triggered by the file explorer double-click."""
        self.current_file_path = file_path
        self.editor.setText(content)
        self.terminal.append(f"[SYSTEM] Loaded {os.path.basename(file_path)}")

    def save_current_file(self):
        """Triggered by Ctrl+S."""
        if self.current_file_path:
            with open(self.current_file_path, 'w', encoding='utf-8') as f:
                f.write(self.editor.text())
            self.terminal.append(f"[SYSTEM] Saved {os.path.basename(self.current_file_path)}")
        else:
            self.terminal.append("[WARN] No file currently selected. Create a file in the explorer first.")

    def _build_chart_dock(self):
        """Snaps the pyqtgraph engine into the right side of the IDE."""
        self.chart_dock = QDockWidget("Live Market Data (/NQ)", self)
        self.chart_dock.setAllowedAreas(
            Qt.DockWidgetArea.RightDockWidgetArea | Qt.DockWidgetArea.LeftDockWidgetArea | Qt.DockWidgetArea.BottomDockWidgetArea)

        # Instantiate the chart widget and store reference for data updates
        self.chart_widget = OphirTradeChart()

        self.chart_dock.setWidget(self.chart_widget)
        self.addDockWidget(Qt.DockWidgetArea.RightDockWidgetArea, self.chart_dock)

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

    def _build_top_toolbar(self):
        """Constructs the main execution toolbar at the top of the IDE."""
        toolbar = QToolBar("Main Execution Toolbar")
        toolbar.setMovable(False)  # Lock it to the top so it doesn't float away
        toolbar.setIconSize(QSize(24, 24))
        self.addToolBar(Qt.ToolBarArea.TopToolBarArea, toolbar)

        # --- Button 1: Run Backtest ---
        btn_backtest = QPushButton("▶️ Run Backtest")
        btn_backtest.setStyleSheet("""
            QPushButton { background-color: #2b5c3a; color: white; font-weight: bold; padding: 6px 12px; border-radius: 4px; }
            QPushButton:hover { background-color: #3e8e53; }
        """)
        # Connect the click event to our Python function
        btn_backtest.clicked.connect(self.action_run_backtest)
        toolbar.addWidget(btn_backtest)

        # --- Button 2: Deploy Live ---
        btn_live = QPushButton("⚡ Deploy Live")
        btn_live.setStyleSheet("""
            QPushButton { background-color: #007acc; color: white; font-weight: bold; padding: 6px 12px; border-radius: 4px; }
            QPushButton:hover { background-color: #0098ff; }
        """)
        btn_live.clicked.connect(self.action_deploy_live)
        toolbar.addWidget(btn_live)

        # Add a visual spacer
        spacer = QWidget()
        spacer.setFixedSize(20, 20)
        toolbar.addWidget(spacer)

        # --- Button 3: The Kill Switch ---
        btn_halt = QPushButton("🛑 HALT ALL")
        btn_halt.setStyleSheet("""
            QPushButton { background-color: #9e2a2b; color: white; font-weight: bold; padding: 6px 12px; border-radius: 4px; }
            QPushButton:hover { background-color: #c9383a; }
        """)
        btn_halt.clicked.connect(self.action_halt_execution)
        toolbar.addWidget(btn_halt)

    # --- The Action Slots (Where the magic will happen) ---

    def action_run_backtest(self):
        # 1. Prevent the user from spamming the button and launching 50 threads
        if hasattr(self, 'engine_thread') and self.engine_thread.isRunning():
            self.terminal.append("[WARN] Engine is already running a backtest!")
            return

        self.terminal.append("\n" + "=" * 40)
        self.terminal.append("[SYSTEM] Initiating Background Execution...")

        # 2. Grab the raw text from the QScintilla code editor
        raw_code = self.editor.text()

        # 3. Instantiate the background thread
        self.engine_thread = OphirExecutionEngine(raw_code)

        # 4. Connect the thread's signals to our UI slots
        self.engine_thread.log_signal.connect(self.append_log)
        self.engine_thread.error_signal.connect(self.append_error)
        self.engine_thread.finished_signal.connect(self.on_execution_finished)

        # 5. Ignite the thread
        self.engine_thread.start()

    def action_deploy_live(self):
        self.terminal.append("\n[WARNING] Waking Live Citadel Agent!")
        self.terminal.append("[NETWORK] Connecting to tastytrade WebSocket...")
        self.terminal.append("[SYSTEM] Awaiting market ticks...")

    def action_halt_execution(self):
        self.terminal.append("\n[KILL SWITCH] 🛑 EXECUTION HALTED.")
        self.terminal.append("[SYSTEM] Flattening all open positions.")
        self.terminal.append("[SYSTEM] Disconnected from brokerage.")

    def append_log(self, text):
        self.terminal.append(f"> {text}")

    def append_error(self, text):
        # Print errors in red
        self.terminal.append(f"<span style='color: #f23645;'>{text}</span>")

    def on_execution_finished(self):
        self.terminal.append("[SYSTEM] Background execution terminated gracefully.")
        self.terminal.append("=" * 40 + "\n")