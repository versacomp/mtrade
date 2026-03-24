import sys
import traceback
import pandas as pd
import numpy as np
from datetime import datetime
from PyQt6.QtCore import QThread, pyqtSignal
from stable_baselines3 import PPO
from ai.env import CitadelEnv
from engine.broker import OphirBroker

class OutputRedirector:
    def __init__(self, signal):
        self.signal = signal

    def write(self, text):
        if text.strip():
            self.signal.emit(text.strip())

    def flush(self):
        pass


class OphirExecutionEngine(QThread):
    log_signal = pyqtSignal(str)
    error_signal = pyqtSignal(str)
    finished_signal = pyqtSignal()
    data_ready_signal = pyqtSignal(object)
    order_signal = pyqtSignal(dict)

    # The Indicator Signal ---
    # Passes: (Indicator Name, Pandas Series, Hex Color)
    indicator_signal = pyqtSignal(str, object, str)

    # --- The Statistics Signal ---
    stats_signal = pyqtSignal(dict)

    def __init__(self, code_string):
        super().__init__()
        self.code_string = code_string

    def run(self):
        original_stdout = sys.stdout
        sys.stdout = OutputRedirector(self.log_signal)

        try:
            self.log_signal.emit("[ENGINE] Initializing Ophir-AI Citadel Protocol...")
            self.log_signal.emit("[DATABASE] Fetching historical /NQ tick data...")

            # --- Initialize the OAuth Broker Bridge ---
            # Set to True ONLY when you are ready to risk real capital
            broker_bridge = OphirBroker(is_live=False)
            self.log_signal.emit("[ENGINE] Secure OAuth connection established.")

            # 1. Generate the Historical Data
            dates = pd.date_range(start='2026-01-01', periods=50000, freq='1min')
            market_data = pd.DataFrame({
                'open': np.random.uniform(18500, 18600, 50000),
                'high': np.random.uniform(18600, 18650, 50000),
                'low': np.random.uniform(18450, 18500, 50000),
                'close': np.random.uniform(18500, 18600, 50000),
                'volume': np.random.randint(100, 1000, 50000)
            }, index=dates)

            # Fire the data across the thread boundary to the UI ---
            self.data_ready_signal.emit(market_data)

            def execute_live_order(symbol: str, side: str, qty: int, price: float = None):
                """Injected function that hits the exchange via the OAuth session."""
                self.log_signal.emit(f"[BROKER] Transmitting {side} {qty}x {symbol}...")
                response = broker_bridge.route_order(symbol, side, qty, price)

                order_log = {
                    'time': datetime.now().strftime('%H:%M:%S'),
                    'symbol': symbol,
                    'side': side,
                    'qty': qty,
                    'price': price if price else 0.0,
                    'status': 'ROUTED'
                }
                self.order_signal.emit(order_log)
                self.log_signal.emit(f"[BROKER] Response: {response}")

            # --- Create the mock plotting API function ---
            def execute_plot(series: pd.Series, name: str = "Indicator", color: str = "#FFC66D"):
                """This function is injected into the user's environment to draw on the chart."""
                self.indicator_signal.emit(name, series, color)
                self.log_signal.emit(f"[CHART] Plotting overlay: {name}")

            # --- The AI Training API ---
            def execute_training(df: pd.DataFrame, timesteps: int = 10000):
                self.log_signal.emit(f"[AI] Constructing Citadel Dojo with {len(df)} ticks...")
                env = CitadelEnv(df)

                self.log_signal.emit("[AI] Waking PPO Neural Network...")
                # verbose=1 forces SB3 to print its progress to our hijacked stdout
                model = PPO("MlpPolicy", env, verbose=1)

                self.log_signal.emit(f"[AI] Commencing Training Phase ({timesteps} timesteps)...")
                model.learn(total_timesteps=timesteps)

                self.log_signal.emit("[AI] Training Complete. Saving weights to memory...")
                model.save("citadel_ppo_v1")
                self.log_signal.emit("[AI] Weights safely archived as 'citadel_ppo_v1.zip'.")

            # Inject Data AND the new order routing function
            isolated_namespace = {
                'historical_df': market_data,
                'pd': pd,
                'np': np,
                'send_order': execute_live_order,  # <--- The Magic Bridge
                'plot': execute_plot, # <--- The Magic Plotting Bridge
                'train_ai': execute_training # <--- The Magic ML Training Bridge
            }

            # 3. Execute the user's script
            exec(self.code_string, isolated_namespace)

            # 4. Look for an entry point that takes the DataFrame as an argument
            if 'execute_trade' in isolated_namespace:
                isolated_namespace['execute_trade'](market_data)
                self.stats_signal.emit(
                    {"net_profit": 0.0, "win_rate": 0.0, "total_trades": 0, "max_drawdown": 0.0, "profit_factor": 0.0,
                     "sharpe_ratio": 0.0})
            else:
                self.log_signal.emit("[WARN] Missing 'execute_trade(df)' entry point.")

        except Exception as e:
            error_msg = f"[CRASH REPORT] {str(e)}\n{traceback.format_exc()}"
            self.error_signal.emit(error_msg)

        finally:
            sys.stdout = original_stdout
            self.finished_signal.emit()