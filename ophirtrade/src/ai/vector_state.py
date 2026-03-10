import numpy as np
import pandas as pd
from ai.risk_engine import AccountState


class StateVectorizer:
    """
    Translates raw market data and account balances into a normalized float32 matrix.
    This is the only language the Stable Baselines3 PPO model understands.
    """

    def __init__(self, lookback_window=10):
        self.lookback_window = lookback_window

        # Calculate the exact size of the array we will hand to the AI
        # (5 price features * 10 candles) + 4 account features = 54 data points
        self.shape = ((5 * self.lookback_window) + 4,)

    def process_step(self, df_window: pd.DataFrame, account: AccountState) -> np.ndarray:
        """
        Takes a slice of the market and the current account state,
        returning a perfectly scaled 1D numpy array.
        """
        # --- 1. Vectorize the Market Data ---

        # We don't want raw prices. We want percentage returns so the data is stationary.
        # .pct_change() turns $18,500 and $18,510 into a normalized decimal like 0.00054
        returns_df = df_window[['open', 'high', 'low', 'close', 'volume']].pct_change().fillna(0)

        # Convert the DataFrame slice directly to a C-speed Numpy array and flatten it
        # A 10x5 DataFrame becomes a 50-element 1D array
        market_vector = returns_df.to_numpy(dtype=np.float32).flatten()

        # --- 2. Vectorize the Account State ---

        # The AI needs to know its drawdown relative to its peak, clamped between -1 and 1
        drawdown_pct = (account.current_balance - account.high_water_mark) / account.high_water_mark

        # Normalize margin usage (assuming a 50k account maxes out around 10k margin)
        margin_utilization = account.open_margin / 10000.0

        # Normalize unrealized PnL (assuming a standard trade risks ~$500)
        pnl_ratio = account.unrealized_pnl / 500.0

        # Position state is already small discrete integers (-2 to 2), so we just scale it slightly
        position_scaled = account.current_position / 2.0

        account_vector = np.array([
            drawdown_pct,
            margin_utilization,
            pnl_ratio,
            position_scaled
        ], dtype=np.float32)

        # --- 3. Fuse and Return ---

        # Concatenate the market history and the account state into one continuous matrix
        final_observation = np.concatenate([market_vector, account_vector])

        # Safety Check: Replace any infinite values or NaNs that might have slipped through
        final_observation = np.nan_to_num(final_observation, nan=0.0, posinf=1.0, neginf=-1.0)

        # AI models strictly require float32 to run efficiently on GPUs
        return final_observation.astype(np.float32)