import gymnasium as gym
from gymnasium import spaces
import numpy as np
import pandas as pd

from ai.risk_engine import AccountState
from ai.vector_state import StateVectorizer


class CitadelEnv(gym.Env):
    """
    The training arena for the Citadel Protocol.
    Conforms to the strict Farama Foundation Gymnasium interface.
    """

    def __init__(self, df: pd.DataFrame):
        super(CitadelEnv, self).__init__()

        # Ensure our index is perfectly sequential for stepping
        self.df = df.reset_index(drop=True)
        self.max_steps = len(self.df) - 1

        # Initialize the eyes
        self.vectorizer = StateVectorizer(lookback_window=10)

        # --- 1. Define the Action Space (The Controls) ---
        # 0 = Flat (Close all), 1 = Buy/Hold Micro, 2 = Buy/Hold Mini
        self.action_space = spaces.Discrete(3)

        # --- 2. Define the Observation Space (The Screen) ---
        # We tell the AI exactly what shape of matrix to expect from the Vectorizer
        self.observation_space = spaces.Box(
            low=-np.inf, high=np.inf,
            shape=self.vectorizer.shape,
            dtype=np.float32
        )

        self.current_step = 0
        self.account = AccountState()

    def reset(self, seed=None, options=None):
        """Called by the AI at the start of every new training episode."""
        super().reset(seed=seed)

        # Start far enough into the data so the vectorizer has history to look back on
        self.current_step = self.vectorizer.lookback_window

        # Reset the fortress metrics back to default
        self.account = AccountState()

        obs = self._get_observation()
        info = {}
        return obs, info

    def _get_observation(self):
        """Extracts the rolling window and passes it to the Vectorizer."""
        start_idx = self.current_step - self.vectorizer.lookback_window
        window_df = self.df.iloc[start_idx: self.current_step]
        return self.vectorizer.process_step(window_df, self.account)

    def step(self, action):
        """The core physics engine of the simulation."""
        self.current_step += 1

        # --- 1. Execute the Action ---
        # Update our internal state tracker based on what the AI chose
        self.account.current_position = int(action)

        # --- 2. Calculate PnL (The Math) ---
        current_close = self.df.iloc[self.current_step]['close']
        previous_close = self.df.iloc[self.current_step - 1]['close']
        price_delta = current_close - previous_close

        # Apply leverage scale based on position (e.g., Micro = $2/pt, Mini = $20/pt)
        multiplier = 0.0
        if self.account.current_position == 1:
            multiplier = 2.0
        elif self.account.current_position == 2:
            multiplier = 20.0

        step_pnl = price_delta * multiplier

        # Update the bankroll
        self.account.current_balance += step_pnl
        if self.account.current_balance > self.account.high_water_mark:
            self.account.high_water_mark = self.account.current_balance

        # --- 3. The Reward Function (The Feedback) ---
        # Neural networks mathematically optimize for maximum cumulative reward:
        # $R = \sum_{t=0}^{T} \gamma^t r_t$
        # Therefore, we directly tie the reward to the actual dollars gained or lost.
        reward = step_pnl

        # --- 4. Termination Conditions ---
        terminated = False
        truncated = False

        # Did the AI blow the account? (Dropped below our risk tolerance)
        if self.account.current_balance < 45000.0:
            reward -= 5000.0  # Massive mathematical penalty for failure
            terminated = True

        # Did we successfully reach the end of the historical data?
        if self.current_step >= self.max_steps:
            truncated = True

        obs = self._get_observation()
        info = {'balance': self.account.current_balance}

        return obs, reward, terminated, truncated, info