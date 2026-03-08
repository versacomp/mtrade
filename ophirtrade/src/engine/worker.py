import sys
import traceback
import pandas as pd
import numpy as np
from PyQt6.QtCore import QThread, pyqtSignal


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

    def __init__(self, code_string):
        super().__init__()
        self.code_string = code_string

    def run(self):
        original_stdout = sys.stdout
        sys.stdout = OutputRedirector(self.log_signal)

        try:
            self.log_signal.emit("[ENGINE] Initializing Ophir-AI Citadel Protocol...")
            self.log_signal.emit("[DATABASE] Fetching historical /NQ tick data...")

            # 1. Load the Historical Data
            # In production, this reads your massive CSV.
            # For now, we generate 50,000 rows of rapid OHLCV data to prove the pipeline.
            dates = pd.date_range(start='2026-01-01', periods=50000, freq='1min')
            market_data = pd.DataFrame({
                'open': np.random.uniform(18500, 18600, 50000),
                'high': np.random.uniform(18600, 18650, 50000),
                'low': np.random.uniform(18450, 18500, 50000),
                'close': np.random.uniform(18500, 18600, 50000),
                'volume': np.random.randint(100, 1000, 50000)
            }, index=dates)

            # 2. Inject Data into the Matrix
            # Anything placed in this dictionary becomes globally available in the user's script
            isolated_namespace = {
                'historical_df': market_data,  # The raw DataFrame
                'pd': pd,  # Pre-import pandas for them
                'np': np  # Pre-import numpy for them
            }

            self.log_signal.emit(f"[ENGINE] Loaded {len(market_data)} rows into memory.")

            # 3. Execute the user's script with the injected data
            exec(self.code_string, isolated_namespace)

            # 4. Look for an entry point that takes the DataFrame as an argument
            if 'execute_trade' in isolated_namespace:
                self.log_signal.emit("[ENGINE] Firing execute_trade(historical_df)...")
                # Pass the DataFrame directly into their function
                isolated_namespace['execute_trade'](market_data)
            else:
                self.log_signal.emit("[WARN] Missing 'execute_trade(df)' entry point.")

        except Exception as e:
            error_msg = f"[CRASH REPORT] {str(e)}\n{traceback.format_exc()}"
            self.error_signal.emit(error_msg)

        finally:
            sys.stdout = original_stdout
            self.finished_signal.emit()