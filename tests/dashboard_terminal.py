"""Minimal terminal screen for checking dashboard ANSI output after PTY processing."""
import re
import unicodedata

class Screen:
    def __init__(self, cols=140, rows=50):
        self.cols, self.rows = cols, rows
        self.cells = [[' '] * cols for _ in range(rows)]
        self.x = self.y = 0

    def feed(self, value):
        tokens = re.findall(r'\x1b\[[0-?]*[ -/]*[@-~]|[^\x1b]', value)
        for token in tokens:
            if token.startswith('\x1b['):
                params, command = token[2:-1], token[-1]
                if command in ('H', 'f'):
                    args = [int(p or '1') for p in params.split(';')]
                    self.y, self.x = args[0] - 1, (args[1] if len(args) > 1 else 1) - 1
                elif command == 'J' and params == '2':
                    self.cells = [[' '] * self.cols for _ in range(self.rows)]
                continue
            if token == '\n':
                self.y = min(self.rows - 1, self.y + 1)
                continue
            if token == '\r':
                self.x = 0
                continue
            if token == '\t':
                self.x = min(self.cols - 1, (self.x // 8 + 1) * 8)
                continue
            if token == '\b':
                self.x = max(0, self.x - 1)
                continue
            if unicodedata.category(token).startswith('C') or unicodedata.combining(token):
                continue
            width = 2 if unicodedata.east_asian_width(token) in ('W', 'F') else 1
            if self.x + width > self.cols:
                self.x, self.y = 0, min(self.rows - 1, self.y + 1)
            if 0 <= self.y < self.rows:
                self.cells[self.y][self.x] = token
                if width == 2:
                    self.cells[self.y][self.x + 1] = ''
            self.x += width

    def outside(self, ox=0, oy=0, width=90, height=46):
        return [(x, y, c) for y, row in enumerate(self.cells) for x, c in enumerate(row)
                if c.strip() and not (ox <= x < ox + width and oy <= y < oy + height)]

    def text(self):
        return '\n'.join(''.join(row) for row in self.cells)
