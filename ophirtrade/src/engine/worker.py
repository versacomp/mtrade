import sys
import traceback
import pandas as pd
import numpy as np
from datetime import datetime
from PyQt6.QtCore import QThread, pyqtSignal
from stable_baselines3 import PPO
from ai.env import CitadelEnv

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

            # --- Create the mock broker API function ---
            def execute_broker_order(symbol: str, side: str, qty: int, price: float):
                """This function is injected into the user's environment."""
                order = {
                    'time': datetime.now().strftime('%H:%M:%S.%f')[:-3],
                    'symbol': symbol,
                    'side': side,
                    'qty': qty,
                    'price': price,
                    'status': 'FILLED'  # Mocking an instant fill for the backtest
                }
                self.order_signal.emit(order)
                self.log_signal.emit(f"[BROKER] Routed {side} {qty}x {symbol} @ {price}")

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
                'send_order': execute_broker_order,  # <--- The Magic Bridge
                'plot': execute_plot, # <--- The Magic Plotting Bridge
                'train_ai': execute_training # <--- The Magic ML Training Bridge
            }

            # 3. Execute the user's script
            exec(self.code_string, isolated_namespace)

            # 4. Look for an entry point that takes the DataFrame as an argument
            if 'execute_trade' in isolated_namespace:
                isolated_namespace['execute_trade'](market_data)

                # --- Generate and emit the final performance metrics ---
                self.log_signal.emit("[ENGINE] Calculating final strategy metrics...")

                # Mocking the calculation engine's output
                final_stats = {
                    "net_profit": 4250.50,
                    "win_rate": 62.5,
                    "total_trades": 14,
                    "max_drawdown": -4.2,
                    "profit_factor": 1.85,
                    "sharpe_ratio": 1.4
                }

                self.stats_signal.emit(final_stats)
            else:
                self.log_signal.emit("[WARN] Missing 'execute_trade(df)' entry point.")

        except Exception as e:
            error_msg = f"[CRASH REPORT] {str(e)}\n{traceback.format_exc()}"
            self.error_signal.emit(error_msg)

        finally:
            sys.stdout = original_stdout
            self.finished_signal.emit()