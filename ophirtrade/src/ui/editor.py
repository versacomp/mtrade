from PyQt6.QtGui import QColor, QFont
from PyQt6.Qsci import QsciScintilla, QsciLexerPython, QsciAPIs


class OphirCodeEditor(QsciScintilla):
    """
    The High-Performance Python Editor Engine for OphirTrade.
    """

    def __init__(self, parent=None):
        super().__init__(parent)

        font = QFont("Consolas", 11)
        self.setFont(font)
        self.setMarginsFont(font)

        # --- THE DARCULA COLOR PALETTE ---
        bg_color = QColor("#2B2B2B")  # Main Editor Background
        margin_bg = QColor("#313335")  # Line Number Area
        margin_fg = QColor("#606366")  # Line Numbers
        default_text = QColor("#A9B7C6")  # Standard Code
        keyword_color = QColor("#CC7832")  # def, class, if (Orange)
        string_color = QColor("#6A8759")  # "strings" (Green)
        number_color = QColor("#6897BB")  # 12345 (Blue)
        class_func_color = QColor("#FFC66D")  # Function Names (Yellow)
        comment_color = QColor("#808080")  # # Comments (Grey)
        caret_color = QColor("#BBBBBB")  # Cursor

        # 1. Base Lexer Setup
        self.lexer = QsciLexerPython()
        self.lexer.setDefaultFont(font)
        self.lexer.setDefaultPaper(bg_color)
        self.lexer.setDefaultColor(default_text)

        # Force the background color across all syntax types
        for i in range(128):
            self.lexer.setPaper(bg_color, i)

        # 2. Syntax Token Mapping
        self.lexer.setColor(keyword_color, QsciLexerPython.Keyword)
        self.lexer.setColor(class_func_color, QsciLexerPython.ClassName)
        self.lexer.setColor(class_func_color, QsciLexerPython.FunctionMethodName)
        self.lexer.setColor(string_color, QsciLexerPython.DoubleQuotedString)
        self.lexer.setColor(string_color, QsciLexerPython.SingleQuotedString)
        self.lexer.setColor(number_color, QsciLexerPython.Number)
        self.lexer.setColor(comment_color, QsciLexerPython.Comment)
        self.lexer.setColor(comment_color, QsciLexerPython.CommentBlock)

        self.setLexer(self.lexer)

        # 3. Margins & Cursor
        self.setMarginType(0, QsciScintilla.MarginType.NumberMargin)
        self.setMarginLineNumbers(0, True)
        self.setMarginWidth(0, "0000")
        self.setMarginsBackgroundColor(margin_bg)
        self.setMarginsForegroundColor(margin_fg)

        self.setCaretForegroundColor(caret_color)
        self.setCaretWidth(2)

        # 4. Code Folding
        self.setFolding(QsciScintilla.FoldStyle.PlainFoldStyle)
        self.setMarginType(2, QsciScintilla.MarginType.SymbolMargin)
        self.setMarginWidth(2, 14)
        self.setFoldMarginColors(margin_bg, margin_bg)

        # 5. IDE Ergonomics & Auto-Complete
        self.setAutoIndent(True)
        self.setIndentationsUseTabs(False)
        self.setTabWidth(4)
        self.setBraceMatching(QsciScintilla.BraceMatch.SloppyBraceMatch)
        self._setup_autocomplete()

    def _setup_autocomplete(self):
        """Builds the vocabulary for the auto-completion engine."""

        # Create the API object and attach it to our Python Lexer
        self.api = QsciAPIs(self.lexer)

        # --- Define the Vocabulary ---
        # 1. Standard Python Keywords
        python_keywords = [
            "def", "class", "import", "from", "return", "if", "elif", "else",
            "try", "except", "finally", "with", "as", "for", "while", "in",
            "True", "False", "None", "print", "len", "range"
        ]

        # 2. OphirTrade Custom Injected Variables
        # This is where the IDE feels like magic to the user.
        ophir_injections = [
            "execute_trade(df)", "historical_df", "pd", "np", "latest_tick", "time",
            "send_order(symbol, side, qty, price)", "plot(series, name, color)"
        ]

        # 3. Common Data Science Methods
        pandas_numpy_methods = [
            "rolling", "mean", "std", "iloc", "loc", "shift", "dropna", "fillna"
        ]

        # Load the vocabulary into the API
        for word in python_keywords + ophir_injections + pandas_numpy_methods:
            self.api.add(word)

        # Compile the API dictionary (Crucial step, or it won't work)
        self.api.prepare()

        # --- Configure the Editor's Behavior ---

        # Tell the editor to use both our custom API list AND the words already typed in the document
        self.setAutoCompletionSource(QsciScintilla.AutoCompletionSource.AcsAll)

        # Trigger the popup menu automatically after typing 2 characters
        self.setAutoCompletionThreshold(2)

        # Make the popup menu match our cyberpunk dark theme
        self.setAutoCompletionUseSingle(QsciScintilla.AutoCompletionUseSingle.AcusNever)
        # Note: Extensive CSS styling of the Scintilla dropdown requires overriding paint events,
        # but the native OS dark mode usually handles this acceptably for now.