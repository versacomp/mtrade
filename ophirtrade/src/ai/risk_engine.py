from dataclasses import dataclass


@dataclass
class AccountState:
    """
    The mathematical reality of your brokerage account.
    The AI reads this to know if it's nearing a margin call or sitting on profits.
    """
    current_balance: float = 50000.0
    high_water_mark: float = 50000.0
    open_margin: float = 0.0
    unrealized_pnl: float = 0.0

    # 0 = Flat, 1 = Long Micro (/MNQ), 2 = Long Mini (/NQ), -1 = Short Micro, -2 = Short Mini
    current_position: int = 0