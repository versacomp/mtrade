# The historical_df DataFrame is magically injected by the C++ / Python boundary

def execute_trade(df):
    print("Executing Mean Reversion Alpha...")
    
    # Let's use the injected Pandas library to calculate a fast moving average
    df['SMA_20'] = df['close'].rolling(window=20).mean()
    
    # Grab the very last row of the 50,000 tick dataset
    latest_tick = df.iloc[-1]
    
    print(f"Latest /NQ Close: {latest_tick['close']:.2f}")
    print(f"SMA 20 Level: {latest_tick['SMA_20']:.2f}")
    
    if latest_tick['close'] < latest_tick['SMA_20']:
        print("SIGNAL: Price is below SMA. Executing BUY order.")
    else:
        print("SIGNAL: Price is above SMA. Executing SELL order.")
        
    print("Backtest complete.")