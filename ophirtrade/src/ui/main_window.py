import os
import time
import datetime
import re
import pyqtgraph as pg
from PyQt6.QtWidgets import (
    QMainWindow, QDockWidget, QListWidget, QTextEdit,
    QToolBar, QPushButton, QWidget, QHBoxLayout,
    QVBoxLayout, QLabel, QLineEdit, QComboBox, QMessageBox
)
from PyQt6.QtCore import Qt, QSize
from PyQt6.QtGui import QFont, QKeySequence, QShortcut
from ui.editor import OphirCodeEditor
from ui.chart import OphirTradeChart
from ui.explorer import OphirFileExplorer
from engine.worker import OphirExecutionEngine
from ui.blotter import OphirOrderBlotter
from ui.dashboard import OphirPerformanceDashboard
from engine.streamer import MarketDataStreamer
from engine.broker import OphirBroker
from collections import deque
from ai.vector_state import StateVectorizer
from ai.risk_engine import AccountState
from engine.alpha_engine import AlphaEngine
from engine.database import OphirDatabase

class OphirTradeIDE(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("OphirTrade - Quant Developer IDE")
        self.resize(1400, 900)

        # Execution Lock: 1 = Long, -1 = Short, 0 = Flat
        self.market_position = 0

        # --- THE RISK MANAGER ---
        self.active_trade = None  # Holds the dictionary of the live position

        # --- EXECUTION STATE ---
        self.is_live_mode = False  # True = Connect to Production API
        self.paper_trade = False  # True = Bypass Broker Routing

        self.active_symbol = "SPY"

        # --- Live Data Buffers ---
        # Keep a rolling window of the last 1000 ticks to prevent memory leaks
        self.live_price_buffer = deque(maxlen=1000)
        self.live_time_buffer = deque(maxlen=1000)
        self.tick_count = 0
        self.tick_counter = 0
        self.live_curve = None  # This will hold our specific pyqtgraph line

        # Track the currently open file so we can save it
        self.current_file_path = None

        # Apply the unified, high-contrast dark theme across the entire application
        self.setStyleSheet("""
                    QMainWindow { background-color: #16161e; } /* Match deep editor background */
                    QDockWidget { color: #aaaaaa; font-weight: bold; }
                    QDockWidget::title { background: #1a1a22; padding: 6px; border-bottom: 1px solid #2d2d30;}

                    /* Update the terminal and explorer docks to be slightly cohesive with new tones */
                    QTextEdit, QListWidget, QTreeView { background-color: #101014; color: #cccccc; border: none; }

                    QToolBar { background-color: #1a1a22; border: none; spacing: 10px; padding: 5px; }
                """)

        # Central Code Editor
        self.editor = OphirCodeEditor()
        self.editor.setText("# 🚢 Ophir-AI Citadel Protocol\n# Write your Alpha here...\n\ndef execute_trade(df):\n    pass")
        self.setCentralWidget(self.editor)

        # 1. Initialize the Terminal first (so the toolbar can print to it)
        self._build_terminal()

        # 2. Build the rest of the UI
        self._build_market_explorer()
        self._build_chart_dock()

        # 3. Ignite the Command Center
        self._build_top_toolbar()

        # Build the Portfolio Matrix UI
        self._build_position_manager()

        # Add the Save Shortcut (Ctrl+S or Cmd+S)
        self.save_shortcut = QShortcut(QKeySequence.StandardKey.Save, self)
        self.save_shortcut.activated.connect(self.save_current_file)

        # --- THE QUANTITATIVE ALPHA ENGINE ---
        self.alpha_engine = AlphaEngine()
        self.append_log("[SYSTEM] Institutional Liquidity Engine loaded and standing by.")

        # The Real-Time Candle Builder (Expanded to 250 for the SMA-200 filter)
        self.live_candles = deque(maxlen=250)

        # --- TIME-BASED AGGREGATOR ---
        self.timeframe_minutes = 1  # We can wire this to a UI dropdown later (1, 5, 15)
        self.current_candle_time = None
        self.current_candle = {'open': None, 'high': None, 'low': None, 'close': None, 'volume': 0}

        # Execution Lock: 1 = Long, -1 = Short, 0 = Flat
        self.market_position = 0

    def _build_market_explorer(self):
        dock = QDockWidget("Market Explorer", self)
        dock.setAllowedAreas(Qt.DockWidgetArea.LeftDockWidgetArea | Qt.DockWidgetArea.RightDockWidgetArea)

        # Instantiate our new native file explorer
        self.file_explorer = OphirFileExplorer(workspace_dir="./strategies")

        # When the user double clicks a file, catch the signal and load it
        self.file_explorer.file_loaded.connect(self.load_file_to_editor)

        dock.setWidget(self.file_explorer)
        self.addDockWidget(Qt.DockWidgetArea.LeftDockWidgetArea, dock)

        # --- Build the Dashboard Dock ---
        dashboard_dock = QDockWidget("Strategy Performance", self)
        dashboard_dock.setAllowedAreas(Qt.DockWidgetArea.LeftDockWidgetArea | Qt.DockWidgetArea.RightDockWidgetArea)

        self.dashboard = OphirPerformanceDashboard()
        dashboard_dock.setWidget(self.dashboard)

        # Snap it directly beneath the file explorer
        self.addDockWidget(Qt.DockWidgetArea.LeftDockWidgetArea, dashboard_dock)

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
        self.dock_chart = QDockWidget(f"Live {self.active_symbol}", self)
        self.dock_chart.setAllowedAreas(
            Qt.DockWidgetArea.RightDockWidgetArea | Qt.DockWidgetArea.LeftDockWidgetArea | Qt.DockWidgetArea.BottomDockWidgetArea)

        # Make the chart an instance variable so we can feed it data later
        self.chart_widget = OphirTradeChart()

        self.dock_chart.setWidget(self.chart_widget)
        self.addDockWidget(Qt.DockWidgetArea.RightDockWidgetArea, self.dock_chart)

    def _build_terminal(self):
        # We modify this slightly so we can tab the terminal and blotter together
        self.terminal_dock = QDockWidget("Execution Logs", self)
        self.terminal_dock.setAllowedAreas(Qt.DockWidgetArea.BottomDockWidgetArea)

        self.terminal = QTextEdit()
        # ... (Keep your terminal setup) ...

        self.terminal_dock.setWidget(self.terminal)
        self.addDockWidget(Qt.DockWidgetArea.BottomDockWidgetArea, self.terminal_dock)

        # --- NEW: Build the Blotter Dock ---
        self.blotter_dock = QDockWidget("Order Blotter", self)
        self.blotter = OphirOrderBlotter()
        self.blotter_dock.setWidget(self.blotter)
        self.addDockWidget(Qt.DockWidgetArea.BottomDockWidgetArea, self.blotter_dock)

        # Stack them on top of each other into tabs (Very PyCharm/VS Code!)
        self.tabifyDockWidget(self.terminal_dock, self.blotter_dock)
        self.blotter_dock.raise_()  # Bring Blotter to the front initially

    def _build_position_manager(self):
        """Constructs the sidebar dock for live account metrics."""
        self.dock_positions = QDockWidget("PORTFOLIO MATRIX", self)
        self.dock_positions.setAllowedAreas(
            Qt.DockWidgetArea.LeftDockWidgetArea | Qt.DockWidgetArea.RightDockWidgetArea)

        container = QWidget()
        layout = QVBoxLayout(container)

        # -- Styling --
        label_style = "color: #f8f8f2; font-family: Consolas; font-size: 14px; padding: 5px;"
        header_style = "color: #bd93f9; font-family: Consolas; font-size: 16px; font-weight: bold; margin-top: 10px;"

        # -- Metrics Labels --
        self.lbl_net_liq = QLabel("Net Liq:        $ --")
        self.lbl_net_liq.setStyleSheet(header_style)

        self.lbl_bp = QLabel("Buying Power:   $ --")
        self.lbl_bp.setStyleSheet(label_style)

        self.lbl_positions = QLabel("Open Inventory:\nFLAT (0 Positions)")
        self.lbl_positions.setStyleSheet(label_style)

        # -- Refresh Button --
        self.btn_refresh_portfolio = QPushButton("⟳ Sync with Exchange")
        self.btn_refresh_portfolio.setStyleSheet(
            "background-color: #44475a; color: #f8f8f2; border: 1px solid #6272a4; padding: 5px;")
        self.btn_refresh_portfolio.clicked.connect(self.refresh_portfolio)

        # Add to layout
        layout.addWidget(self.lbl_net_liq)
        layout.addWidget(self.lbl_bp)
        layout.addWidget(self.lbl_positions)
        layout.addSpacing(20)
        layout.addWidget(self.btn_refresh_portfolio)
        layout.addStretch()  # Pushes everything to the top

        self.dock_positions.setWidget(container)

        # Snap the dock to the right side of the IDE
        self.addDockWidget(Qt.DockWidgetArea.RightDockWidgetArea, self.dock_positions)

    def refresh_portfolio(self):
        """Pulls the latest ledger data and updates the UI."""
        if not self.live_broker:
            self.append_log("[SYSTEM] Broker offline. Click 'Connect Live Data Feed' to authenticate first.")
            return

        self.append_log("[SYSTEM] Syncing portfolio ledgers with Wall Street...")
        balances, positions = self.live_broker.get_portfolio_status()

        if isinstance(balances, str):
            self.append_log(f"[PORTFOLIO ERROR] {balances}")
            return

        # 1. Update Account Balances
        if balances:
            # Safely extract the exact Decimal values Tastytrade provides
            net_liq = getattr(balances, 'net_liquidating_value', 0)
            bp = getattr(balances, 'equity_buying_power', 0)

            self.lbl_net_liq.setText(f"Net Liq:        ${float(net_liq):,.2f}")
            self.lbl_bp.setText(f"Buying Power:   ${float(bp):,.2f}")

        # 2. Update Open Positions
        if positions is not None:
            if len(positions) == 0:
                self.lbl_positions.setText("Open Inventory:\n> FLAT (0 Positions)")
            else:
                pos_text = "Open Inventory:\n"
                for p in positions:
                    sym = getattr(p, 'symbol', 'UNKNOWN')
                    qty = getattr(p, 'quantity', 0)
                    pos_text += f"> {sym} : {qty} shares\n"

                self.lbl_positions.setText(pos_text)

        self.append_log("[SYSTEM] Portfolio Sync Complete.")

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

        # --- DYNAMIC SYMBOL SELECTOR ---
        lbl_symbol = QLabel("TICKER:")
        lbl_symbol.setStyleSheet("color: #8be9fd; font-weight: bold; font-family: Consolas;")
        toolbar.addWidget(lbl_symbol)

        # 1. CREATE THE TEXT BOX FIRST
        self.txt_symbol = QLineEdit("SPY")  # "SPY" is the default text
        self.txt_symbol.setFixedWidth(80)
        self.txt_symbol.setStyleSheet(
            "background-color: #44475a; "
            "color: #f8f8f2; "
            "border: 1px solid #6272a4; "
            "padding: 5px; "
            "font-weight: bold; "
            "font-family: Consolas;"
        )
        self.txt_symbol.textChanged.connect(lambda text: self.txt_symbol.setText(text.upper()))
        toolbar.addWidget(self.txt_symbol)

        # --- TIMEFRAME DROPDOWN ---
        self.combo_timeframe = QComboBox()
        self.combo_timeframe.addItems(["1 Minute", "5 Minutes", "15 Minutes", "1 Hour"])
        self.combo_timeframe.setStyleSheet(
            "background-color: #2b2b2b; color: #f8f8f2; border: 1px solid #6272a4; padding: 5px;")

        # Add it to your toolbar layout (Example:)
        toolbar.addWidget(self.combo_timeframe)

        # 2. NOW SET THE VARIABLE
        # Because self.txt_symbol exists now, we can safely read it.
        self.active_symbol = self.txt_symbol.text().strip()

        # --- Live Data Toggle ---
        self.btn_live_data = QPushButton("Connect Live Data Feed")
        self.btn_live_data.setStyleSheet("background-color: #2b2b2b; color: #50fa7b; border: 1px solid #50fa7b;")
        self.btn_live_data.clicked.connect(self.toggle_live_stream)
        toolbar.addWidget(self.btn_live_data)

        # Keep track of the streamer state
        self.streamer_thread = None
        self.live_broker = None

        # --- AI Telemetry Readout ---
        self.lbl_ai_confidence = QLabel("AI State: Awaiting Data...")
        self.lbl_ai_confidence.setStyleSheet("color: #8be9fd; font-weight: bold; font-family: Consolas; padding: 5px;")
        toolbar.addWidget(self.lbl_ai_confidence)

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

        # --- NEW: EMERGENCY HALT BUTTON ---
        self.btn_halt = QPushButton("🛑 HALT ALL")
        self.btn_halt.setStyleSheet(
            "background-color: #ff5555; "
            "color: #ffffff; "
            "font-weight: bold; "
            "border: 2px solid #ff0000; "
            "padding: 5px 15px;"
        )
        self.btn_halt.clicked.connect(self.halt_all_trading)
        toolbar.addWidget(self.btn_halt)

        # --- 3-WAY ENVIRONMENT TOGGLE ---
        self.combo_env = QComboBox()
        self.combo_env.addItems([
            "SANDBOX (Cert Data & Exec)",
            "PAPER (Live Data & Local Exec)",
            "LIVE (Live Data & Real Money)"
        ])
        self.combo_env.setStyleSheet("""
                    QComboBox { background-color: #44475a; color: #f1fa8c; font-weight: bold; border: 1px solid #f1fa8c; padding: 5px; }
                """)
        self.combo_env.currentTextChanged.connect(self._on_env_changed)
        toolbar.addWidget(self.combo_env)

        # --- THE QUANTITATIVE ALPHA ENGINE ---
        self.alpha_engine = AlphaEngine()
        self.db = OphirDatabase()  # <--- NEW: Initialize the Local SQLite Storage
        self.append_log("[SYSTEM] Institutional Liquidity Engine and SQLite Storage online.")

    def halt_all_trading(self):
        """The Master Kill Switch: Severs data, stops the AI, and flattens all positions."""
        self.append_log("\n[EMERGENCY] =========================================")
        self.append_log("[EMERGENCY] HALT ALL PROTOCOL INITIATED.")

        # 1. SEVER THE DATA FIREHOSE
        if self.streamer_thread and self.streamer_thread.isRunning():
            self.streamer_thread.stop()
            self.streamer_thread.wait()
            self.streamer_thread = None
            self.txt_symbol.setEnabled(True)  # Unlock the text box

            # Reset the Live Data button UI
            self.btn_live_data.setText("Connect Live Data Feed")
            self.btn_live_data.setStyleSheet("background-color: #2b2b2b; color: #50fa7b; border: 1px solid #50fa7b;")
            self.append_log("[EMERGENCY] WebSocket data stream severed. AI is blind.")

            # Update the AI label
            self.lbl_ai_confidence.setText("AI State: SYSTEM HALTED")
            self.lbl_ai_confidence.setStyleSheet(
                "color: #ff5555; font-weight: bold; font-family: Consolas; padding: 5px;")

        # 2. FLATTEN THE MARKET POSITION
        target_symbol = self.active_symbol

        if self.market_position == 1:
            self.append_log(f"[EMERGENCY] Liquidating LONG position on {target_symbol}...")
            if self.live_broker:
                try:
                    # Fire a Market SELL order to close out the 1 share
                    response = self.live_broker.route_order(symbol=target_symbol, side="SELL", qty=1, price=None)
                    self.append_log(f"[BROKER] {response}")
                except Exception as e:
                    self.append_log(f"[BROKER FATAL] Failed to route liquidation order: {str(e)}")

            self.market_position = 0

        elif self.market_position == 0:
            self.append_log("[EMERGENCY] Market position is currently FLAT. No liquidation required.")

        self.append_log("[EMERGENCY] =========================================\n")

    # --- The Action Slots (Where the magic will happen) ---

    def action_run_backtest(self):
        if hasattr(self, 'engine_thread') and self.engine_thread.isRunning():
            self.terminal.append("[WARN] Engine is already running a backtest!")
            return

        self.terminal.append("\n" + "=" * 40)
        self.terminal.append("[SYSTEM] Initiating Background Execution...")

        raw_code = self.editor.text()
        self.engine_thread = OphirExecutionEngine(raw_code)

        self.engine_thread.log_signal.connect(self.append_log)
        self.engine_thread.error_signal.connect(self.append_error)
        self.engine_thread.finished_signal.connect(self.on_execution_finished)
        self.engine_thread.data_ready_signal.connect(self.chart_widget.set_real_data)

        # --- Connect the Blotter Signal ---
        self.engine_thread.order_signal.connect(self.blotter.add_order)

        # --- Connect the Indicator Signal ---
        self.engine_thread.indicator_signal.connect(self.chart_widget.add_indicator)

        # --- Connect the Stats Signal ---
        self.engine_thread.stats_signal.connect(self.dashboard.update_stats)

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

    def toggle_live_stream(self):
        # Check if the object exists, completely ignoring its .isRunning() state
        if getattr(self, 'streamer_thread', None) is not None:
            # Disconnect
            self.append_log("[SYSTEM] Initiating streamer shutdown sequence...")
            self.streamer_thread.stop()

            # Give the thread 1 second to exit gracefully.
            # If it refuses because it is blocked by network I/O, execute it.
            if not self.streamer_thread.wait(1000):
                self.append_log("[SYSTEM] Network thread unresponsive. Forcing termination.")
                self.streamer_thread.terminate()  # Brutally sever the C++ wrapper
                self.streamer_thread.wait()  # Confirm the kill

            self.streamer_thread = None
            self.btn_live_data.setText("Connect Live Data Feed")
            self.btn_live_data.setStyleSheet("background-color: #2b2b2b; color: #50fa7b; border: 1px solid #50fa7b;")
            self.txt_symbol.setEnabled(True)  # Unlock the text box
            self.combo_timeframe.setEnabled(True) # Unlock the timeframe combobox
            self.append_log("[SYSTEM] Live WebSocket feed severed successfully.")
        else:
            # Connect
            self.append_log("[SYSTEM] Initializing secure OAuth session for live data...")
            self.btn_live_data.setText("Connecting...")
            self.btn_live_data.setStyleSheet("background-color: #f1fa8c; color: #282a36;")

            # We initialize the broker purely to grab the authenticated session
            try:
                # --- PREPARE THE CHART FOR LIVE DATA ---
                # Clear any existing backtest candlesticks
                # 1. Grab the active symbol from the UI
                raw_symbol = self.txt_symbol.text().strip().upper()
                tf_text = self.combo_timeframe.currentText()

                if not raw_symbol:
                    self.append_error("[SYSTEM] Ticker symbol cannot be empty.")
                    return

                # --- NEW: AUTO-FORMAT FUTURES SYMBOLS ---
                # If it looks like a future (e.g. MESH6) and doesn't have a slash, add it
                if re.match(r'^[A-Z]+[FGHJKMNQUVXZ]\d{1,2}$', raw_symbol) and not raw_symbol.startswith('/'):
                    self.active_symbol = f"/{raw_symbol}"
                    self.txt_symbol.setText(self.active_symbol)  # Update the UI visually
                    self.append_log(f"[SYSTEM] Auto-formatted futures contract to {self.active_symbol}")
                else:
                    self.active_symbol = raw_symbol
                # ----------------------------------------

                if not self.active_symbol:
                    self.append_error("[SYSTEM] Ticker symbol cannot be empty.")
                    return

                # Map UI Text to Engine Variables
                if tf_text == "1 Minute":
                    self.timeframe_minutes = 1
                    yf_interval = '1m'
                elif tf_text == "5 Minutes":
                    self.timeframe_minutes = 5
                    yf_interval = '5m'
                elif tf_text == "15 Minutes":
                    self.timeframe_minutes = 15
                    yf_interval = '15m'
                elif tf_text == "1 Hour":
                    self.timeframe_minutes = 60
                    yf_interval = '1h'

                # Lock the UI inputs
                self.txt_symbol.setEnabled(False)
                self.combo_timeframe.setEnabled(False)

                # Inject the dynamic UI state into the networking engines
                self.live_broker = OphirBroker(is_live=self.is_live_mode)

                # Dynamic Seeder
                self.append_log(
                    f"[SYSTEM] Requesting {yf_interval} historical tape for {self.active_symbol} to seed the Alpha Engine...")

                history = self.live_broker.get_historical_candles(self.active_symbol, interval=yf_interval)

                if isinstance(history, str):
                    self.append_log(f"[WARN] Seeder failed: {history}. Engine will cold start.")
                    self.live_candles.clear()
                else:
                    self.live_candles.clear()
                    # Cap the history injection so we don't overflow
                    for c in history[-250:]:
                        self.live_candles.append(c)
                    self.append_log(
                        f"[SYSTEM] Seeder injected {len(self.live_candles)} historical {yf_interval} candles. Engine ARMED.")

                # 2. Lock the input box so the user can't change it mid-stream
                self.txt_symbol.setEnabled(False)

                # --- UPDATE THE UI TITLES ---
                # Update the Dock Widget Title
                # (Note: Change 'self.dock_chart' to whatever you actually named your chart dock variable!)
                if hasattr(self, 'dock_chart'):
                    self.dock_chart.setWindowTitle(f"MARKET MATRIX: {self.active_symbol}")

                # Update the pyqtgraph internal title (if you want the text directly on the grid)
                if hasattr(self.chart_widget, 'graph'):
                    self.chart_widget.plot_widget.setTitle(
                        f"<span style='color: #8be9fd; font-size: 14pt;'>{self.active_symbol} Live Tape</span>")
                # ---------------------------------

                # Use your custom wrapper methods (Optional: update your create_live_line to take a name!)
                self.chart_widget.clear_chart()
                self.live_curve = self.chart_widget.create_live_line(name=f"Live {self.active_symbol}")

                # --- NEW: VISUAL OVERLAYS ---
                # 1. The SMA 200 Line (Golden)
                self.sma_curve = self.chart_widget.plot_widget.plot(
                    pen=pg.mkPen('#f1fa8c', width=2, style=Qt.PenStyle.DashLine))

                # 2. Execution Markers (Scatter Plots)
                # symbol 't1' is an Up-Arrow, 't' is a Down-Arrow
                self.buy_scatter = pg.ScatterPlotItem(symbol='t1', size=14, brush='#50fa7b', pen='w')
                self.sell_scatter = pg.ScatterPlotItem(symbol='t', size=14, brush='#ff5555', pen='w')

                self.chart_widget.plot_widget.addItem(self.buy_scatter)
                self.chart_widget.plot_widget.addItem(self.sell_scatter)

                # Memory arrays for the markers
                self.buy_x, self.buy_y = [], []
                self.sell_x, self.sell_y = [], []
                self.sma_data = []
                # ---------------------------------------

                # Reset the memory buffers
                self.live_price_buffer.clear()
                self.live_time_buffer.clear()
                self.tick_count = 0
                # ---------------------------------------

                # Start the background firehose, locked onto the S&P 500 ETF
                # Pass the authenticated live session directly to the data firehose
                self.streamer_thread = MarketDataStreamer(
                    symbol=self.active_symbol,
                    is_live=self.is_live_mode
                )
                self.streamer_thread.tick_signal.connect(self.process_live_tick)
                self.streamer_thread.error_signal.connect(self.append_error)
                self.streamer_thread.start()

                self.btn_live_data.setText("Disconnect Live Feed")
                self.btn_live_data.setStyleSheet(
                    "background-color: #ff5555; color: #f8f8f2; border: 1px solid #ff5555;")
            except Exception as e:
                self.append_error(f"[NETWORK ERROR] Failed to authenticate stream: {str(e)}")

    def process_live_tick(self, data):
        # 1. Handle Status Messages
        if data.get("type") == "status":
            self.append_log(data["msg"])
            return

        # 2. Handle Historical Seed Payload
        if data.get("type") == "history":
            self.live_candles.clear()
            for c in data["data"]:
                self.live_candles.append(c)
            self.append_log(
                f"[SYSTEM] DXLink Seeder perfectly aligned {len(self.live_candles)} native candles. Engine ARMED.")
            return

        # 3. Handle Live Ticks (Your existing code starts here...)
        if data.get("type") == "tick":
            event = data.get('event_type')

            if event == 'Quote':
                bid = data.get('bid')
                ask = data.get('ask')
                symbol = data.get('symbol')

                if bid and ask:
                    mid_price = float(bid + ask) / 2.0

                    # --- 1. LIVE RISK MANAGER EVALUATION ---
                    if self.active_trade is not None:
                        t = self.active_trade
                        tp_dist = abs(t['tp'] - t['entry_price'])

                        if t['direction'] == 'LONG':
                            # Update Peak & Ratcheting Stop
                            if mid_price > t['peak']: t['peak'] = mid_price
                            progress = t['peak'] - t['entry_price']

                            # Trail Stop Trigger
                            if progress >= 0.75 * tp_dist:
                                trail_sl = t['peak'] - (t['risk'] * 0.50)
                                if trail_sl > t['sl']:
                                    t['sl'] = trail_sl
                                    t['stage'] = 2
                            # Breakeven Trigger
                            elif progress >= 0.50 * tp_dist and t['stage'] < 1:
                                t['sl'] = t['entry_price']
                                t['stage'] = 1

                            # Exit Conditions
                            if mid_price <= t['sl'] or mid_price >= t['tp']:
                                if mid_price >= t['tp']:
                                    status = "WIN"
                                elif t['sl'] == t['entry_price']:
                                    status = "SCRATCH"
                                else:
                                    status = "LOSS"

                                pnl = t['tp'] - t['entry_price'] if status == "WIN" else t['sl'] - t['entry_price']
                                exit_price = t['tp'] if status == "WIN" else t['sl']
                                self._close_active_trade(exit_price, pnl, status)

                        elif t['direction'] == 'SHORT':
                            # Update Peak & Ratcheting Stop
                            if mid_price < t['peak']: t['peak'] = mid_price
                            progress = t['entry_price'] - t['peak']

                            # Trail Stop Trigger
                            if progress >= 0.75 * tp_dist:
                                trail_sl = t['peak'] + (t['risk'] * 0.50)
                                if trail_sl < t['sl']:
                                    t['sl'] = trail_sl
                                    t['stage'] = 2
                            # Breakeven Trigger
                            elif progress >= 0.50 * tp_dist and t['stage'] < 1:
                                t['sl'] = t['entry_price']
                                t['stage'] = 1

                            # Exit Conditions
                            if mid_price >= t['sl'] or mid_price <= t['tp']:
                                if mid_price <= t['tp']:
                                    status = "WIN"
                                elif t['sl'] == t['entry_price']:
                                    status = "SCRATCH"
                                else:
                                    status = "LOSS"

                                pnl = t['entry_price'] - t['tp'] if status == "WIN" else t['entry_price'] - t['sl']
                                exit_price = t['tp'] if status == "WIN" else t['sl']
                                self._close_active_trade(exit_price, pnl, status)
                    # ---------------------------------------

                    self.tick_count += 1
                    self.live_time_buffer.append(self.tick_count)
                    self.live_price_buffer.append(mid_price)

                    if self.live_curve:
                        self.live_curve.setData(
                            x=list(self.live_time_buffer),
                            y=list(self.live_price_buffer)
                        )

                    if self.tick_count % 25 == 0:
                        self.append_log(f"[LIVE MARKET] {symbol} | MID: {mid_price:.2f}")

                    # --- 1. BUILD THE OHLCV CANDLE ---
                    if self.current_candle['open'] is None:
                        self.current_candle['open'] = mid_price
                        self.current_candle['high'] = mid_price
                        self.current_candle['low'] = mid_price

                    self.current_candle['high'] = max(self.current_candle['high'], mid_price)
                    self.current_candle['low'] = min(self.current_candle['low'], mid_price)
                    self.current_candle['close'] = mid_price
                    self.current_candle['volume'] += 1

                    self.tick_counter += 1

                    # --- 2. TIME-BASED CANDLE AGGREGATOR ---
                    now = datetime.datetime.now()

                    # Floor the time to the nearest timeframe interval
                    minute_floored = (now.minute // self.timeframe_minutes) * self.timeframe_minutes
                    interval_time = now.replace(minute=minute_floored, second=0, microsecond=0)

                    if self.current_candle_time is None:
                        # First tick initializes the clock
                        self.current_candle_time = interval_time
                        self.current_candle['open'] = mid_price
                        self.current_candle['high'] = mid_price
                        self.current_candle['low'] = mid_price
                        self.current_candle['close'] = mid_price

                    if interval_time > self.current_candle_time:
                        # THE MINUTE HAS ROLLED OVER. SEAL THE CANDLE.
                        sealed_candle = self.current_candle.copy()
                        self.live_candles.append(sealed_candle)

                        # Log to the SQLite database
                        self.db.insert_candle(symbol, sealed_candle, self.current_candle_time.timestamp())

                        # Trigger the Alpha Engine (if warmed up)
                        if len(self.live_candles) >= 200:
                            intent = self.alpha_engine.evaluate(list(self.live_candles))
                            action_val = intent["action"]

                            # UI Updates
                            if action_val == 0:
                                self.lbl_ai_confidence.setText(f"Alpha Engine ({self.timeframe_minutes}m): SCANNING...")
                                self.lbl_ai_confidence.setStyleSheet("color: #8be9fd; font-weight: bold; padding: 5px;")
                            elif action_val == 1:
                                self.lbl_ai_confidence.setText(f"Alpha Engine: PRIME BULL GRAB @ {intent['level']:.2f}")
                                self.lbl_ai_confidence.setStyleSheet("color: #50fa7b; font-weight: bold; padding: 5px;")
                            elif action_val == 2:
                                self.lbl_ai_confidence.setText(f"Alpha Engine: PRIME BEAR GRAB @ {intent['level']:.2f}")
                                self.lbl_ai_confidence.setStyleSheet("color: #ff5555; font-weight: bold; padding: 5px;")

                            self._process_quant_action(action_val, symbol, mid_price)
                        else:
                            candles_needed = 200 - len(self.live_candles)
                            self.lbl_ai_confidence.setText(
                                f"Alpha Engine: WARMING UP ({candles_needed} candles remaining)")
                            self.lbl_ai_confidence.setStyleSheet("color: #f1fa8c; font-weight: bold; padding: 5px;")

                        # RESET FOR THE NEW INTERVAL
                        self.current_candle = {
                            'open': mid_price, 'high': mid_price, 'low': mid_price, 'close': mid_price, 'volume': 0
                        }
                        self.current_candle_time = interval_time
                    else:
                        # WE ARE STILL IN THE CURRENT MINUTE. UPDATE THE ACTIVE CANDLE.
                        if self.current_candle['open'] is None:
                            self.current_candle['open'] = mid_price

                        self.current_candle['close'] = mid_price
                        if mid_price > (self.current_candle['high'] or mid_price):
                            self.current_candle['high'] = mid_price
                        if mid_price < (self.current_candle['low'] or mid_price):
                            self.current_candle['low'] = mid_price
                    # ---------------------------------------

    def _process_quant_action(self, action: int, symbol: str, current_price: float):
        """Translates Alpha signals into strict risk-managed orders."""

        # We need the current timestamp for the X-axis of our chart
        current_time = time.time()

        # If we are already in a trade, we ignore new signals until it closes (or you can add flip logic)
        if self.active_trade is not None:
            return

        # Grab the signal candle (the last sealed candle in the buffer)
        signal_candle = self.live_candles[-1]

        # STATE CHANGE: Prime BULL Signal -> LONG
        if action == 1 and self.market_position == 0:
            self.append_log(f"[QUANT GHOST] Prime BULL liquidity grab detected on {symbol}. Initiating LONG sequence.")

            # Paint the Buy Arrow (Green)
            self.buy_x.append(current_time)
            self.buy_y.append(current_price)
            self.buy_scatter.setData(self.buy_x, self.buy_y)

            # Risk Math
            sl = signal_candle['low']  # Stop Loss at wick tip
            risk = current_price - sl
            if risk <= 0: return  # Degenerate candle guard
            tp = current_price + (risk * 2.0)  # 1:2 R:R Target

            # Arm the Risk Manager
            self.active_trade = {
                'symbol': symbol, 'direction': 'LONG', 'entry_price': current_price,
                'sl': sl, 'tp': tp, 'risk': risk, 'stage': 0, 'peak': current_price,
                'entry_time': time.time()
            }

            if self.live_broker:
                if self.paper_trade:
                    self.append_log(f"[PAPER] Simulated BUY order locked locally. Wall Street bypassed.")
                else:
                    response = self.live_broker.route_order(symbol, "BUY", 1)
                    self.market_position = 1
                    self.append_log(f"[RISK MGR] LONG {symbol} | Entry: {current_price:.2f} | SL: {sl:.2f} | TP: {tp:.2f}")
                    self.append_log(f"[BROKER] {response}")

        # STATE CHANGE: Prime BEAR Signal -> SHORT
        elif action == 2 and self.market_position == 0:
            self.append_log(f"[QUANT GHOST] Prime BEAR liquidity grab detected on {symbol}. Initiating SHORT sequence.")

            # Risk Math
            sl = signal_candle['high']  # Stop Loss at wick tip
            risk = sl - current_price
            if risk <= 0: return  # Degenerate candle guard
            tp = current_price - (risk * 2.0)  # 1:2 R:R Target

            # Arm the Risk Manager
            self.active_trade = {
                'symbol': symbol, 'direction': 'SHORT', 'entry_price': current_price,
                'sl': sl, 'tp': tp, 'risk': risk, 'stage': 0, 'peak': current_price,
                'entry_time': time.time()
            }

            if self.live_broker:
                if self.paper_trade:
                    self.append_log(f"[PAPER] Simulated SELL_SHORT order locked locally. Wall Street bypassed.")
                else:
                    response = self.live_broker.route_order(symbol, "SELL_SHORT", 1)
                    self.market_position = -1
                    self.append_log(f"[RISK MGR] SHORT {symbol} | Entry: {current_price:.2f} | SL: {sl:.2f} | TP: {tp:.2f}")
                    self.append_log(f"[BROKER] {response}")

    def _on_env_changed(self, text):
        """Safety interlock for Data and Execution environments."""
        if "LIVE (Live Data" in text:
            # STATE 3: FULL PRODUCTION (DANGER)
            reply = QMessageBox.warning(
                self,
                "DANGER: LIVE TRADING ARMED",
                "You are switching the execution engine to FULL LIVE mode.\n\n"
                "The Alpha Engine will now trade REAL CAPITAL on the production Tastytrade servers.\n\n"
                "Are you absolutely sure you want to proceed?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No
            )

            if reply == QMessageBox.StandardButton.No:
                self.combo_env.blockSignals(True)
                self.combo_env.setCurrentIndex(1)  # Default back to safe Paper mode
                self.combo_env.blockSignals(False)
                return

            self.is_live_mode = True
            self.paper_trade = False
            self.combo_env.setStyleSheet(
                "QComboBox { background-color: #ff5555; color: #ffffff; font-weight: bold; border: 2px solid #ff5555; padding: 5px; }")
            self.append_log("[EMERGENCY] WARNING: LIVE MONEY TRADING ARMED. PRODUCTION CLEARINGHOUSE ACTIVE.")

        elif "PAPER" in text:
            # STATE 2: PRODUCTION DATA + LOCAL PAPER EXECUTION (SAFE)
            self.is_live_mode = True
            self.paper_trade = True
            # Style it a cool blue to indicate active data but safe execution
            self.combo_env.setStyleSheet(
                "QComboBox { background-color: #8be9fd; color: #282a36; font-weight: bold; border: 2px solid #8be9fd; padding: 5px; }")
            self.append_log("[SYSTEM] Data switched to Production. Execution bypassed (Paper Mode Active).")

        else:
            # STATE 1: CERT DATA + CERT EXECUTION (SAFE)
            self.is_live_mode = False
            self.paper_trade = False
            self.combo_env.setStyleSheet(
                "QComboBox { background-color: #44475a; color: #f1fa8c; font-weight: bold; border: 1px solid #f1fa8c; padding: 5px; }")
            self.append_log("[SYSTEM] Matrix reverted to secure Sandbox Simulation.")

    def _close_active_trade(self, exit_price: float, pnl: float, status: str):
        """Flattens the position and records the financial outcome to SQLite."""
        t = self.active_trade
        t['exit_price'] = exit_price
        t['pnl'] = pnl
        t['status'] = status
        t['exit_time'] = time.time()

        # Fire closing order to the clearinghouse
        if self.live_broker:
            # To close a Long, we SELL. To close a Short, we BUY_TO_COVER.
            action = "SELL" if t['direction'] == 'LONG' else "BUY_TO_COVER"

            if self.paper_trade:
                self.append_log(f"[PAPER FLATTEN] Simulated {action} order filled locally. Wall Street bypassed.")
            else:
                response = self.live_broker.route_order(t['symbol'], action, 1)
                self.append_log(f"[BROKER FLATTEN] {response}")

        # Log to Database
        self.db.log_closed_trade(t)

        self.append_log(f"[RISK MGR] Trade Closed: {status} | P&L: {pnl:.2f} pts | Exit: {exit_price:.2f}")

        # Reset state
        self.active_trade = None
        self.market_position = 0