from PyQt6.QtWidgets import QTableWidget, QTableWidgetItem, QAbstractItemView, QHeaderView
from PyQt6.QtGui import QColor, QFont
from PyQt6.QtCore import Qt


class OphirOrderBlotter(QTableWidget):
    """
    The live execution ledger.
    Catches buy/sell signals from the engine and logs them visually.
    """

    def __init__(self, parent=None):
        super().__init__(parent)

        # 1. Define the Columns
        self.setColumnCount(6)
        self.setHorizontalHeaderLabels(["Time", "Symbol", "Side", "Qty", "Price", "Status"])

        # 2. Terminal Ergonomics
        # Make the table read-only and select entire rows at a time
        self.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.verticalHeader().setVisible(False)  # Hide the default row numbers

        # Stretch columns to fit the dock perfectly
        header = self.horizontalHeader()
        header.setSectionResizeMode(QHeaderView.ResizeMode.Stretch)

        # 3. Darcula Styling
        self.setStyleSheet("""
            QTableWidget {
                background-color: #2B2B2B;
                color: #A9B7C6;
                gridline-color: #323232;
                border: none;
                font-family: Consolas;
                font-size: 10pt;
            }
            QHeaderView::section {
                background-color: #3C3F41;
                color: #A9B7C6;
                padding: 4px;
                border: 1px solid #323232;
                font-weight: bold;
            }
            QTableWidget::item:selected {
                background-color: #0D293E; /* Subtle blue selection highlight */
            }
        """)

    def add_order(self, order_data: dict):
        """
        Receives a dictionary from the engine thread and appends it to the top of the table.
        """
        self.insertRow(0)  # Always push the newest trade to the top

        # Parse the dictionary
        time_item = QTableWidgetItem(order_data.get('time', '--'))
        symbol_item = QTableWidgetItem(order_data.get('symbol', '--'))
        side_item = QTableWidgetItem(order_data.get('side', '--').upper())
        qty_item = QTableWidgetItem(str(order_data.get('qty', 0)))

        # Format the price nicely
        price = order_data.get('price', 0.0)
        price_item = QTableWidgetItem(f"${price:,.2f}")

        status_item = QTableWidgetItem(order_data.get('status', 'PENDING'))

        # Color code the BUY and SELL sides like a professional terminal
        if side_item.text() == "BUY":
            side_item.setForeground(QColor("#089981"))  # Bullish Green
        elif side_item.text() == "SELL":
            side_item.setForeground(QColor("#f23645"))  # Bearish Red

        # Center align specific columns
        for item in [time_item, symbol_item, side_item, qty_item, status_item]:
            item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
        price_item.setTextAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)

        # Slam the data into the row
        self.setItem(0, 0, time_item)
        self.setItem(0, 1, symbol_item)
        self.setItem(0, 2, side_item)
        self.setItem(0, 3, qty_item)
        self.setItem(0, 4, price_item)
        self.setItem(0, 5, status_item)