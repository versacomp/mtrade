from PyQt6.QtGui import QColor, QFont
from PyQt6.Qsci import QsciScintilla, QsciLexerPython


class OphirCodeEditor(QsciScintilla):
    def __init__(self, parent=None):
        super().__init__(parent)

        # 1. Font Configuration
        font = QFont("Consolas", 11)
        self.setFont(font)
        self.setMarginsFont(font)

        # 2. Python Syntax Highlighter
        self.lexer = QsciLexerPython()
        self.lexer.setDefaultFont(font)
        self.lexer.setDefaultPaper(QColor("#1e1e1e"))
        self.lexer.setDefaultColor(QColor("#d4d4d4"))
        
        # Style Python specific elements for dark theme
        self.lexer.setColor(QColor("#569cd6"), QsciLexerPython.Keyword)
        self.lexer.setColor(QColor("#4ec9b0"), QsciLexerPython.ClassName)
        self.lexer.setColor(QColor("#dcdcaa"), QsciLexerPython.FunctionMethodName)
        self.lexer.setColor(QColor("#ce9178"), QsciLexerPython.DoubleQuotedString)
        self.lexer.setColor(QColor("#ce9178"), QsciLexerPython.SingleQuotedString)
        self.lexer.setColor(QColor("#6a9955"), QsciLexerPython.Comment)
        self.lexer.setColor(QColor("#b5cea8"), QsciLexerPython.Number)
        self.lexer.setColor(QColor("#c586c0"), QsciLexerPython.Operator)
        
        self.setLexer(self.lexer)

        # 3. Line Numbers & Margins
        self.setMarginType(0, QsciScintilla.MarginType.NumberMargin)
        self.setMarginLineNumbers(0, True)
        self.setMarginWidth(0, "0000")
        self.setMarginsBackgroundColor(QColor("#252526"))
        self.setMarginsForegroundColor(QColor("#858585"))

        # 4. IDE Ergonomics
        self.setAutoIndent(True)
        self.setIndentationsUseTabs(False)
        self.setTabWidth(4)
        self.setBraceMatching(QsciScintilla.BraceMatch.SloppyBraceMatch)
        self.setCaretForegroundColor(QColor("#4af626"))
        self.setCaretWidth(2)
        self.setCaretLineVisible(True)
        self.setCaretLineBackgroundColor(QColor("#2c2c2d"))

        # 5. Code Folding
        self.setFolding(QsciScintilla.FoldStyle.PlainFoldStyle)
        self.setMarginType(2, QsciScintilla.MarginType.SymbolMargin)
        self.setMarginWidth(2, 14)
        self.setFoldMarginColors(QColor("#2d2d30"), QColor("#2d2d30"))

    def set_text(self, text: str):
        """Sets the editor content."""
        self.setText(text)

    def get_text(self) -> str:
        """Returns the editor content."""
        return self.text()
