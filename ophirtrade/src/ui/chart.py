import numpy as np
import pyqtgraph as pg
from PyQt6.QtWidgets import QWidget, QVBoxLayout
from ui.candlestick import CandlestickItem


class OphirTradeChart(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)

        pg.setConfigOptions(antialias=True)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)

        self.plot_widget = pg.PlotWidget()
        self.plot_widget.setBackground('#1e1e1e')
        self.plot_widget.showGrid(x=True, y=True, alpha=0.3)
        self.plot_widget.setLabel('left', 'Price', color='#aaaaaa', size='12pt')
        self.plot_widget.setLabel('bottom', 'Time', color='#aaaaaa', size='12pt')

        axis_pen = pg.mkPen(color='#555555', width=1)
        self.plot_widget.getAxis('left').setPen(axis_pen)
        self.plot_widget.getAxis('bottom').setPen(axis_pen)
        self.plot_widget.getAxis('left').setTextPen('#aaaaaa')
        self.plot_widget.getAxis('bottom').setTextPen('#aaaaaa')

        layout.addWidget(self.plot_widget)
        self._load_simulated_ohlc_data()

    def _load_simulated_ohlc_data(self):
        """Generates realistic-looking mock candlesticks to test the rendering engine."""
        data = []
        current_price = 18500.0

        # Generate 200 candles
        for i in range(200):
            # Create some random volatility
            volatility = np.random.uniform(5.0, 25.0)
            open_price = current_price
            close_price = current_price + np.random.uniform(-volatility, volatility)

            # Ensure the wicks extend past the body
            high_price = max(open_price, close_price) + np.random.uniform(2.0, 10.0)
            low_price = min(open_price, close_price) - np.random.uniform(2.0, 10.0)

            data.append((i, open_price, close_price, low_price, high_price))

            # The next candle opens where this one closed
            current_price = close_price

        # Instantiate our custom graphics object
        self.candlesticks = CandlestickItem(data)

        # Add it to the pyqtgraph canvas
        self.plot_widget.addItem(self.candlesticks)

    def update_data(self, data):
        """
        Updates the chart with new OHLC data.
        :param data: list of tuples (time_index, open, close, low, high)
        """
        if hasattr(self, 'candlesticks'):
            self.plot_widget.removeItem(self.candlesticks)
        
        self.candlesticks = CandlestickItem(data)
        self.plot_widget.addItem(self.candlesticks)
