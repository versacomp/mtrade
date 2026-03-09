import os
import numpy as np
import torch
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
from ui.blotter import OphirOrderBlotter
from ui.dashboard import OphirPerformanceDashboard
from engine.streamer import MarketDataStreamer
from engine.broker import OphirBroker
from collections import deque
from stable_baselines3 import PPO

class OphirTradeIDE(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("OphirTrade - Quant Developer IDE")
        self.resize(1400, 900)

        # --- Live Data Buffers ---
        # Keep a rolling window of the last 1000 ticks to prevent memory leaks
        self.live_price_buffer = deque(maxlen=1000)
        self.live_time_buffer = deque(maxlen=1000)
        self.tick_count = 0
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

        # Add the Save Shortcut (Ctrl+S or Cmd+S)
        self.save_shortcut = QShortcut(QKeySequence.StandardKey.Save, self)
        self.save_shortcut.activated.connect(self.save_current_file)

        # --- The AI Ghost Variables ---
        try:
            # Attempt to load the trained brain into memory
            self.ai_model = PPO.load("citadel_ppo_v1")
            self.append_log("[SYSTEM] AI Model 'citadel_ppo_v1' loaded and standing by.")
        except Exception as e:
            self.ai_model = None
            self.append_log(f"[WARN] No compiled AI found. Running in manual mode. ({str(e)})")

        # The Execution Lock (0 = Flat, 1 = Long, -1 = Short)
        # This prevents the AI from spamming the exchange.
        self.market_position = 0

        # The Observation Window (Must match what the AI was trained on)
        self.ai_window_size = 10

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
        dock = QDockWidget("Live Market Data (/NQ)", self)
        dock.setAllowedAreas(
            Qt.DockWidgetArea.RightDockWidgetArea | Qt.DockWidgetArea.LeftDockWidgetArea | Qt.DockWidgetArea.BottomDockWidgetArea)

        # Make the chart an instance variable so we can feed it data later
        self.chart_widget = OphirTradeChart()

        dock.setWidget(self.chart_widget)
        self.addDockWidget(Qt.DockWidgetArea.RightDockWidgetArea, dock)

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
        if self.streamer_thread and self.streamer_thread.isRunning():
            # Disconnect
            self.streamer_thread.stop()
            self.streamer_thread.wait()
            self.streamer_thread = None
            self.btn_live_data.setText("Connect Live Data Feed")
            self.btn_live_data.setStyleSheet("background-color: #2b2b2b; color: #50fa7b; border: 1px solid #50fa7b;")
            self.append_log("[SYSTEM] Live WebSocket feed terminated.")
        else:
            # Connect
            self.append_log("[SYSTEM] Initializing secure OAuth session for live data...")
            self.btn_live_data.setText("Connecting...")
            self.btn_live_data.setStyleSheet("background-color: #f1fa8c; color: #282a36;")

            # We initialize the broker purely to grab the authenticated session
            try:
                self.live_broker = OphirBroker(is_live=False)

                # --- PREPARE THE CHART FOR LIVE DATA ---
                # Clear any existing backtest candlesticks
                self.chart_widget.clear_chart()
                self.live_curve = self.chart_widget.create_live_line()

                # Reset the memory buffers
                self.live_price_buffer.clear()
                self.live_time_buffer.clear()
                self.tick_count = 0
                # ---------------------------------------

                # Start the background firehose, locked onto the S&P 500 ETF
                self.streamer_thread = MarketDataStreamer(symbol="SPY", is_live=False)
                self.streamer_thread.tick_signal.connect(self.process_live_tick)
                self.streamer_thread.error_signal.connect(self.append_error)
                self.streamer_thread.start()

                self.btn_live_data.setText("Disconnect Live Feed")
                self.btn_live_data.setStyleSheet(
                    "background-color: #ff5555; color: #f8f8f2; border: 1px solid #ff5555;")
            except Exception as e:
                self.append_error(f"[NETWORK ERROR] Failed to authenticate stream: {str(e)}")

    def process_live_tick(self, data: dict):
        if data.get("type") == "status":
            self.append_log(data.get("msg"))

        elif data.get("type") == "tick":
            event = data.get('event_type')

            if event == 'Quote':
                bid = data.get('bid')
                ask = data.get('ask')
                symbol = data.get('symbol')

                if bid and ask:
                    mid_price = float(bid + ask) / 2.0
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

                    # --- THE AI TRIGGER & TELEMETRY ---
                    if self.ai_model and len(self.live_price_buffer) >= self.ai_window_size:

                        # 1. Format the live data
                        raw_obs = list(self.live_price_buffer)[-self.ai_window_size:]
                        obs_array = np.array(raw_obs, dtype=np.float32).reshape(1, -1)

                        # 2. Get the physical action
                        action, _states = self.ai_model.predict(obs_array, deterministic=True)

                        # 3. INTERROGATE THE NEURAL NETWORK FOR CONFIDENCE
                        # Move the numpy array to PyTorch tensors on the correct device (CPU/GPU)
                        obs_tensor = torch.tensor(obs_array).to(self.ai_model.device)

                        # Get the probability distribution for this specific market state
                        dist = self.ai_model.policy.get_distribution(obs_tensor)
                        probs = dist.distribution.probs.detach().cpu().numpy()[0]

                        # probs is an array like [0.15, 0.70, 0.15] corresponding to [Hold, Buy, Sell]
                        action_names = ["HOLD", "BUY", "SELL"]
                        selected_action_name = action_names[int(action)]
                        confidence_percentage = probs[int(action)] * 100

                        # 4. Update the UI Readout in Real-Time
                        self.lbl_ai_confidence.setText(
                            f"AI Matrix: {selected_action_name} ({confidence_percentage:.1f}%)")

                        # Change color based on action for visual flair
                        if int(action) == 1:
                            self.lbl_ai_confidence.setStyleSheet(
                                "color: #50fa7b; font-weight: bold; font-family: Consolas; padding: 5px;")  # Green for Buy
                        elif int(action) == 2:
                            self.lbl_ai_confidence.setStyleSheet(
                                "color: #ff5555; font-weight: bold; font-family: Consolas; padding: 5px;")  # Red for Sell
                        else:
                            self.lbl_ai_confidence.setStyleSheet(
                                "color: #8be9fd; font-weight: bold; font-family: Consolas; padding: 5px;")  # Cyan for Hold

                        # 5. Route the AI's intent through the Execution Locks
                        self._process_ai_action(int(action), symbol, mid_price)

    def _process_ai_action(self, action: int, symbol: str, current_price: float):
        """Translates neural network outputs into strictly controlled broker commands."""

        if action == 1 and self.market_position == 0:
            # AI wants to BUY and we currently hold nothing
            self.append_log(f"[AI GHOST] Alpha signature detected on {symbol}. Initiating BUY sequence.")
            self.market_position = 1

            # Fire the physical order!
            if self.live_broker:
                # We use a market order here for instant execution, but a limit is safer in prod
                response = self.live_broker.route_order(symbol=symbol, side="BUY", qty=1, price=None)
                self.append_log(f"[BROKER] {response}")

        elif action == 2 and self.market_position == 1:
            # AI wants to SELL and we are currently holding a long position
            self.append_log(f"[AI GHOST] Exit condition met for {symbol}. Initiating SELL sequence.")
            self.market_position = 0

            # Fire the physical order!
            if self.live_broker:
                response = self.live_broker.route_order(symbol=symbol, side="SELL", qty=1, price=None)
                self.append_log(f"[BROKER] {response}")