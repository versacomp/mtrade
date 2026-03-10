import numpy as np
import pyqtgraph as pg
from PyQt6.QtWidgets import QWidget, QVBoxLayout
from PyQt6.QtGui import QColor, QFont
from ui.candlestick import CandlestickItem


class OphirTradeChart(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)

        self.plot_widget = pg.PlotWidget()

        # 1. Global Antialiasing for smooth, modern lines
        pg.setConfigOptions(antialias=True)

        # Assuming your plot widget is 'self' (if inheriting from PlotWidget)
        # or 'self.plot_item' / 'self.graph' (if it's an attribute)
        # Update 'self' below to match your actual graph variable!

        # 2. Deep Dark Background
        self.plot_widget.setBackground('#1e1e2e')  # A deep, modern dark blue/gray

        # 3. Clean up the Grid Lines
        self.plot_widget.showGrid(x=True, y=True, alpha=0.2)

        # 4. Style the Axes and Ticks
        axis_pen = pg.mkPen(color='#6272a4', width=1)
        text_font = QFont("Consolas", 10)

        for axis_name in ['left', 'bottom']:
            axis = self.plot_widget.getAxis(axis_name)
            axis.setPen(axis_pen)  # The axis line itself
            axis.setTextPen('#8be9fd')  # The text color (Cyan)
            axis.setTickFont(text_font)  # The font style

        # 5. Remove the ugly default borders
        self.plot_widget.getPlotItem().hideAxis('right')
        self.plot_widget.getPlotItem().hideAxis('top')
        self.plot_widget.getPlotItem().getViewBox().setBorder(None)

        layout.addWidget(self.plot_widget)

        # Keep track of our candlestick graphics object so we can remove it later
        self.candlesticks = None

    def set_real_data(self, df):
        """Draws the base candlesticks."""
        # --- NEW: Completely wipe the canvas before drawing ---
        self.plot_widget.clear()

        data_list = []
        for i, row in enumerate(df.itertuples()):
            data_list.append((i, row.open, row.close, row.low, row.high))

        self.candlesticks = CandlestickItem(data_list)
        self.plot_widget.addItem(self.candlesticks)
        self.plot_widget.autoRange()

    def add_indicator(self, name, series, color):
        """
        Catches the Pandas Series from the background thread and draws it.
        """
        # 1. Convert the Pandas Series to a pure C-speed Numpy array
        y_data = series.to_numpy()

        # 2. Create the X-axis (0 to 50000 to match the candlesticks)
        x_data = np.arange(len(y_data))

        # 3. Create the pen using the provided Hex color
        pen = pg.mkPen(color=color, width=2)

        # 4. Plot it!
        # connect='finite' tells pyqtgraph to ignore NaN values (like the first 19 periods of an SMA)
        self.plot_widget.plot(x=x_data, y=y_data, name=name, pen=pen, connect='finite')

    def clear_chart(self):
        """Wipes the chart clean for the live data feed."""
        # Change 'self.graph' to whatever your internal pyqtgraph variable is named!
        self.plot_widget.clear()

    def create_live_line(self, pen, name="Live Data"):
        """Creates and returns a neon green line for the live WebSocket feed."""
        return self.plot_widget.plot(pen=pen, name=name)

    def update_data(self, data):
        """
        Updates the chart with new OHLC data.
        :param data: list of tuples (time_index, open, close, low, high)
        """
        if hasattr(self, 'candlesticks'):
            self.plot_widget.removeItem(self.candlesticks)
        
        self.candlesticks = CandlestickItem(data)
        self.plot_widget.addItem(self.candlesticks)
