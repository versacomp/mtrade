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
        self.plot_widget.setBackground('#2B2B2B')  # Match Darcula background
        self.plot_widget.showGrid(x=True, y=True, alpha=0.3)

        axis_pen = pg.mkPen(color='#555555', width=1)
        self.plot_widget.getAxis('left').setPen(axis_pen)
        self.plot_widget.getAxis('bottom').setPen(axis_pen)
        self.plot_widget.getAxis('left').setTextPen('#A9B7C6')
        self.plot_widget.getAxis('bottom').setTextPen('#A9B7C6')

        layout.addWidget(self.plot_widget)

        # Keep track of our candlestick graphics object so we can remove it later
        self.candlesticks = None

    def set_real_data(self, df):
        """
        Catches the Pandas DataFrame from the background thread and paints it.
        """
        # 1. Clear the old chart if a user runs back-to-back backtests
        if self.candlesticks is not None:
            self.plot_widget.removeItem(self.candlesticks)

        # 2. Extract the data.
        # pyqtgraph expects an x-axis integer index for smooth zooming.
        data_list = []

        # We use itertuples() because iterating over a DataFrame is usually slow,
        # but itertuples() strips the overhead and gives us pure C-speed tuples.
        for i, row in enumerate(df.itertuples()):
            # row[0] is index, row[1] is open, row[2] is high, row[3] is low, row[4] is close
            # Our CandlestickItem expects: (time, open, close, low, high)
            data_list.append((i, row.open, row.close, row.low, row.high))

        # 3. Instantiate the graphics object and slam it onto the GPU
        self.candlesticks = CandlestickItem(data_list)
        self.plot_widget.addItem(self.candlesticks)

        # 4. Auto-scale the view to fit the new data perfectly
        self.plot_widget.autoRange()

    def update_data(self, data):
        """
        Updates the chart with new OHLC data.
        :param data: list of tuples (time_index, open, close, low, high)
        """
        if hasattr(self, 'candlesticks'):
            self.plot_widget.removeItem(self.candlesticks)
        
        self.candlesticks = CandlestickItem(data)
        self.plot_widget.addItem(self.candlesticks)
