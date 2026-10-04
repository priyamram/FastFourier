import sys
import time
import serial
import requests

from PySide6.QtWidgets import (
    QApplication,
    QWidget,
    QLabel,
    QVBoxLayout,
    QHBoxLayout,
    QFrame,
    QPushButton,
)
from PySide6.QtCore import Qt, QThread, Signal, QTimer, QUrl
from PySide6.QtGui import QFont, QPainter, QPen, QBrush
from PySide6.QtWebEngineWidgets import QWebEngineView


# ============================================================
# SETTINGS
# ============================================================

ARDUINO_PORT = "COM10"
BAUD_RATE = 9600

# Your friend's Fast Fourier server
API_BASE = "http://172.20.10.7:8080"

# How often the dashboard checks for changes
REFRESH_MS = 5000

# Your known MAROON card
CARD_UID = "3D:C9:F7:03"


# ============================================================
# API
# ============================================================

def api_tap(uid):
    """Tell the friend's Fast Fourier server that this card was tapped."""
    r = requests.post(
        f"{API_BASE}/api/dev/tap",
        json={"uid": uid},
        timeout=5,
    )
    r.raise_for_status()
    return r.json()


def api_state():
    r = requests.get(f"{API_BASE}/api/state", timeout=5)
    r.raise_for_status()
    return r.json()


def api_dashboard():
    r = requests.get(f"{API_BASE}/api/dashboard", timeout=10)
    r.raise_for_status()
    return r.json()


# ============================================================
# NFC THREAD
# ============================================================

class NFCWorker(QThread):
    card_detected = Signal(str)
    status_update = Signal(str)
    error = Signal(str)

    def run(self):
        try:
            arduino = serial.Serial(
                ARDUINO_PORT,
                BAUD_RATE,
                timeout=1,
            )

            time.sleep(2)

            self.status_update.emit("TAP YOUR MAROON CARD")

            last_uid = None
            last_tap = 0

            while True:
                line = arduino.readline().decode(
                    errors="ignore"
                ).strip()

                if line.startswith("CARD_UID:"):
                    uid = line.replace("CARD_UID:", "").strip().upper()

                    # Prevent one card from firing continuously
                    now = time.time()
                    if uid == last_uid and now - last_tap < 3:
                        continue

                    last_uid = uid
                    last_tap = now
                    self.card_detected.emit(uid)

        except Exception as e:
            self.error.emit(str(e))


# ============================================================
# ANIMATED MARKET BACKGROUND
# ============================================================

class MarketBackground(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.phase = 0
        self.setAttribute(Qt.WA_TransparentForMouseEvents)

        self.timer = QTimer(self)
        self.timer.timeout.connect(self.animate)
        self.timer.start(35)

    def animate(self):
        self.phase += 1
        self.update()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)

        w = self.width()
        h = self.height()

        # Dark background
        painter.fillRect(0, 0, w, h, QBrush("#070b09"))

        # Subtle grid
        grid_pen = QPen("#102018")
        grid_pen.setWidth(1)
        painter.setPen(grid_pen)

        spacing = 50

        for x in range(0, w, spacing):
            painter.drawLine(x, 0, x, h)

        for y in range(0, h, spacing):
            painter.drawLine(0, y, w, y)

        # Animated stock line
        points = []

        import math

        for x in range(0, w + 10, 8):
            t = x / max(w, 1)

            base = h * 0.68
            trend = -h * 0.25 * t

            wave1 = math.sin(t * 15 + self.phase * 0.035) * h * 0.035
            wave2 = math.sin(t * 39 + self.phase * 0.018) * h * 0.018

            # deterministic "market" movement
            y = base + trend + wave1 + wave2

            points.append((x, y))

        # Glow layers
        for width, alpha in [(9, 25), (5, 45), (2, 220)]:
            pen = QPen("#20ff7a")
            pen.setWidth(width)
            pen.setColor(pen.color().lighter(100))
            color = pen.color()
            color.setAlpha(alpha)
            pen.setColor(color)
            painter.setPen(pen)

            for i in range(1, len(points)):
                painter.drawLine(
                    int(points[i - 1][0]),
                    int(points[i - 1][1]),
                    int(points[i][0]),
                    int(points[i][1]),
                )

        # Moving point
        if points:
            idx = (self.phase * 3) % len(points)
            x, y = points[idx]

            dot_pen = QPen("#55ff99")
            dot_pen.setWidth(2)
            painter.setPen(dot_pen)
            painter.setBrush(QBrush("#55ff99"))
            painter.drawEllipse(int(x) - 5, int(y) - 5, 10, 10)


# ============================================================
# DASHBOARD
# ============================================================

class FastFourierDashboard(QWidget):

    def __init__(self):
        super().__init__()

        self.current_uid = None
        self.connected = False

        self.setWindowTitle("Fast Fourier — MAROON")
        self.setMinimumSize(1200, 760)

        self.setup_ui()
        self.start_nfc()

        # Poll the friend's API every 5 seconds
        self.refresh_timer = QTimer(self)
        self.refresh_timer.timeout.connect(self.refresh_from_api)
        self.refresh_timer.start(REFRESH_MS)

    # ========================================================
    # UI
    # ========================================================

    def setup_ui(self):

        self.setStyleSheet("""
            QWidget {
                color: #f4f7f5;
                font-family: Arial;
            }

            QFrame#panel {
                background-color: rgba(10, 14, 12, 235);
                border: 1px solid #20352a;
                border-radius: 18px;
            }

            QLabel {
                background: transparent;
                border: none;
            }

            QPushButton {
                background-color: #10251a;
                border: 1px solid #2c7a4d;
                border-radius: 10px;
                padding: 10px 18px;
                color: #9affbd;
                font-weight: bold;
            }

            QPushButton:hover {
                background-color: #173923;
            }
        """)

        root = QVBoxLayout(self)
        root.setContentsMargins(35, 25, 35, 25)
        root.setSpacing(15)

        # Background
        self.background = MarketBackground(self)
        self.background.lower()

        # ----------------------------------------------------
        # HEADER
        # ----------------------------------------------------

        header = QHBoxLayout()

        title = QLabel("FAST FOURIER")
        title.setFont(QFont("Arial", 26, QFont.Bold))

        subtitle = QLabel("DECENTRALIZED PORTFOLIO IDENTITY")
        subtitle.setFont(QFont("Arial", 10))
        subtitle.setStyleSheet("color: #71907e;")

        title_box = QVBoxLayout()
        title_box.setSpacing(1)
        title_box.addWidget(title)
        title_box.addWidget(subtitle)

        header.addLayout(title_box)
        header.addStretch()

        self.connection_label = QLabel("● API OFFLINE")
        self.connection_label.setStyleSheet(
            "color: #ff6b6b; font-weight: bold;"
        )

        self.refresh_button = QPushButton("REFRESH")
        self.refresh_button.clicked.connect(self.refresh_from_api)

        header.addWidget(self.connection_label)
        header.addWidget(self.refresh_button)

        root.addLayout(header)

        # ----------------------------------------------------
        # STATUS
        # ----------------------------------------------------

        self.status_label = QLabel("TAP YOUR MAROON CARD")
        self.status_label.setFont(QFont("Arial", 14, QFont.Bold))
        self.status_label.setAlignment(Qt.AlignCenter)
        self.status_label.setStyleSheet(
            "color: #8dffa9; padding: 8px;"
        )

        root.addWidget(self.status_label)

        # ----------------------------------------------------
        # SCORE PANEL
        # ----------------------------------------------------

        score_panel = QFrame()
        score_panel.setObjectName("panel")

        score_layout = QVBoxLayout(score_panel)
        score_layout.setContentsMargins(20, 15, 20, 15)
        score_layout.setAlignment(Qt.AlignCenter)

        score_title = QLabel("PORTFOLIO SCORE")
        score_title.setFont(QFont("Arial", 13))
        score_title.setAlignment(Qt.AlignCenter)

        self.score_label = QLabel("---")
        self.score_label.setFont(QFont("Arial", 72, QFont.Bold))
        self.score_label.setAlignment(Qt.AlignCenter)
        self.score_label.setStyleSheet("color: #8dffa9;")

        self.rating_label = QLabel("WAITING")
        self.rating_label.setFont(QFont("Arial", 19, QFont.Bold))
        self.rating_label.setAlignment(Qt.AlignCenter)

        score_layout.addWidget(score_title)
        score_layout.addWidget(self.score_label)
        score_layout.addWidget(QLabel("/ 850"))
        score_layout.addWidget(self.rating_label)

        root.addWidget(score_panel)

        # ----------------------------------------------------
        # METRICS
        # ----------------------------------------------------

        metrics = QHBoxLayout()
        metrics.setSpacing(12)

        self.diversification = self.create_metric(
            metrics, "DIVERSIFICATION"
        )
        self.risk = self.create_metric(
            metrics, "RISK-ADJUSTED RETURN"
        )
        self.drawdown = self.create_metric(
            metrics, "DRAWDOWN RESILIENCE"
        )
        self.liquidity = self.create_metric(
            metrics, "LIQUIDITY"
        )

        root.addLayout(metrics)

        # ----------------------------------------------------
        # VALUE
        # ----------------------------------------------------

        value_panel = QFrame()
        value_panel.setObjectName("panel")

        value_layout = QVBoxLayout(value_panel)

        value_title = QLabel("PORTFOLIO VALUE")
        value_title.setFont(QFont("Arial", 11))

        self.value_label = QLabel("$---")
        self.value_label.setFont(QFont("Arial", 29, QFont.Bold))
        self.value_label.setStyleSheet("color: #ffffff;")

        self.market_label = QLabel("MARKET STATUS: ---")
        self.market_label.setStyleSheet("color: #71907e;")

        value_layout.addWidget(value_title)
        value_layout.addWidget(self.value_label)
        value_layout.addWidget(self.market_label)

        root.addWidget(value_panel)

        # ----------------------------------------------------
        # HOLDINGS
        # ----------------------------------------------------

        holdings_panel = QFrame()
        holdings_panel.setObjectName("panel")

        holdings_layout = QVBoxLayout(holdings_panel)

        holdings_title = QLabel("LIVE HOLDINGS")
        holdings_title.setFont(QFont("Arial", 14, QFont.Bold))

        holdings_layout.addWidget(holdings_title)

        self.holdings_container = QVBoxLayout()
        self.holdings_container.setSpacing(4)

        holdings_layout.addLayout(self.holdings_container)

        root.addWidget(holdings_panel)

        # ----------------------------------------------------
        # FOOTER
        # ----------------------------------------------------

        footer = QHBoxLayout()

        self.portfolio_label = QLabel("PORTFOLIO: ---")
        self.last_update_label = QLabel("LAST SYNC: ---")

        footer.addWidget(self.portfolio_label)
        footer.addStretch()
        footer.addWidget(self.last_update_label)

        root.addLayout(footer)

    # ========================================================
    # METRIC CARD
    # ========================================================

    def create_metric(self, layout, title):

        card = QFrame()
        card.setObjectName("panel")

        card_layout = QVBoxLayout(card)
        card_layout.setContentsMargins(12, 12, 12, 12)

        title_label = QLabel(title)
        title_label.setFont(QFont("Arial", 9))
        title_label.setAlignment(Qt.AlignCenter)

        value_label = QLabel("---")
        value_label.setFont(QFont("Arial", 21, QFont.Bold))
        value_label.setAlignment(Qt.AlignCenter)
        value_label.setStyleSheet("color: #8dffa9;")

        card_layout.addWidget(title_label)
        card_layout.addWidget(value_label)

        layout.addWidget(card)

        return value_label

    # ========================================================
    # NFC
    # ========================================================

    def start_nfc(self):

        self.worker = NFCWorker()

        self.worker.card_detected.connect(
            self.card_detected
        )

        self.worker.status_update.connect(
            self.update_status
        )

        self.worker.error.connect(
            self.show_error
        )

        self.worker.start()

    # ========================================================
    # CARD DETECTED
    # ========================================================

    def card_detected(self, uid):

        self.current_uid = uid

        self.status_label.setText(
            f"CARD DETECTED — {uid}"
        )

        self.portfolio_label.setText(
            "AUTHENTICATING..."
        )

        QApplication.processEvents()

        try:
            # Tell friend's server to unlock this portfolio.
            result = api_tap(uid)

            if not result.get("ok"):
                raise Exception("Server rejected this card.")

            self.status_label.setText(
                "✓ CARD AUTHENTICATED"
            )

            self.status_label.setStyleSheet(
                "color: #55ff99; font-weight: bold; padding: 8px;"
            )

            self.refresh_from_api()

        except Exception as e:

            self.status_label.setText(
                "API CONNECTION ERROR"
            )

            self.connection_label.setText(
                "● API ERROR"
            )

            self.connection_label.setStyleSheet(
                "color: #ff6b6b; font-weight: bold;"
            )

            print("API ERROR:", e)

    # ========================================================
    # API REFRESH
    # ========================================================

    def refresh_from_api(self):

        try:

            state = api_state()

            self.connected = True

            self.connection_label.setText(
                "● API CONNECTED"
            )

            self.connection_label.setStyleSheet(
                "color: #55ff99; font-weight: bold;"
            )

            if not state.get("unlocked"):

                if self.current_uid is None:
                    self.status_label.setText(
                        "TAP YOUR MAROON CARD"
                    )

                return

            data = api_dashboard()

            self.update_dashboard(data)

        except requests.RequestException as e:

            self.connected = False

            self.connection_label.setText(
                "● API OFFLINE"
            )

            self.connection_label.setStyleSheet(
                "color: #ff6b6b; font-weight: bold;"
            )

            print("API:", e)

        except Exception as e:

            print("Dashboard error:", e)

    # ========================================================
    # UPDATE DASHBOARD
    # ========================================================

    def update_dashboard(self, data):

        account = data.get("account") or {}
        score = data.get("score") or {}
        positions = data.get("positions") or []

        name = data.get("name") or "---"

        self.portfolio_label.setText(
            f"PORTFOLIO: {name}"
        )

        self.status_label.setText(
            "● LIVE — SYNCHRONIZED WITH PORTFOLIO SERVER"
        )

        self.status_label.setStyleSheet(
            "color: #55ff99; font-weight: bold; padding: 8px;"
        )

        # Score
        if "error" not in score:

            self.score_label.setText(
                str(score.get("score", "---"))
            )

            self.rating_label.setText(
                str(score.get("rating", "WAITING")).upper()
            )

            factors = {
                f.get("key"): f
                for f in score.get("factors", [])
            }

            self.diversification.setText(
                f"{factors.get('diversification', {}).get('value', '---')} / 100"
            )

            self.risk.setText(
                f"{factors.get('risk_adjusted_return', {}).get('value', '---')} / 100"
            )

            self.drawdown.setText(
                f"{factors.get('drawdown', {}).get('value', '---')} / 100"
            )

            self.liquidity.setText(
                f"{factors.get('liquidity', {}).get('value', '---')} / 100"
            )

        # Account
        try:
            equity = float(account.get("equity") or 0)
            self.value_label.setText(
                f"${equity:,.2f}"
            )
        except (ValueError, TypeError):
            self.value_label.setText("$---")

        # Market
        if data.get("market_open"):
            self.market_label.setText(
                "MARKET STATUS: OPEN"
            )
            self.market_label.setStyleSheet(
                "color: #55ff99;"
            )
        else:
            self.market_label.setText(
                "MARKET STATUS: CLOSED"
            )
            self.market_label.setStyleSheet(
                "color: #a9b5ad;"
            )

        # Holdings
        while self.holdings_container.count():

            item = self.holdings_container.takeAt(0)

            if item.widget():
                item.widget().deleteLater()

            elif item.layout():
                while item.layout().count():
                    child = item.layout().takeAt(0)
                    if child.widget():
                        child.widget().deleteLater()

        if not positions:

            empty = QLabel("NO OPEN POSITIONS")
            empty.setStyleSheet("color: #718078;")
            self.holdings_container.addWidget(empty)

        else:

            total_equity = equity if equity > 0 else 1

            for p in positions:

                symbol = p.get("symbol", "---")
                market_value = float(p.get("market_value") or 0)
                qty = p.get("qty", "---")
                pl = float(p.get("unrealized_pl") or 0)

                percentage = (
                    market_value / total_equity * 100
                )

                row = QHBoxLayout()
                row.setContentsMargins(8, 3, 8, 3)

                ticker = QLabel(symbol)
                ticker.setFont(
                    QFont("Arial", 13, QFont.Bold)
                )

                qty_label = QLabel(
                    f"{qty} shares"
                )
                qty_label.setStyleSheet(
                    "color: #8a9a91;"
                )

                amount = QLabel(
                    f"${market_value:,.2f}"
                )

                pct = QLabel(
                    f"{percentage:.1f}%"
                )

                pl_label = QLabel(
                    f"{'+' if pl >= 0 else ''}${pl:,.2f}"
                )

                if pl >= 0:
                    pl_label.setStyleSheet(
                        "color: #55ff99;"
                    )
                else:
                    pl_label.setStyleSheet(
                        "color: #ff7777;"
                    )

                row.addWidget(ticker)
                row.addWidget(qty_label)
                row.addStretch()
                row.addWidget(amount)
                row.addWidget(pct)
                row.addWidget(pl_label)

                self.holdings_container.addLayout(row)

        self.last_update_label.setText(
            "LAST SYNC: " + time.strftime("%H:%M:%S")
        )

    # ========================================================
    # STATUS
    # ========================================================

    def update_status(self, message):
        self.status_label.setText(message)

    def show_error(self, message):

        self.status_label.setText(
            "ARDUINO CONNECTION ERROR"
        )

        self.connection_label.setText(
            "● ARDUINO ERROR"
        )

        print("Arduino error:", message)

    # ========================================================
    # RESIZE
    # ========================================================

    def resizeEvent(self, event):

        self.background.setGeometry(
            self.rect()
        )

        super().resizeEvent(event)


# ============================================================
# START
# ============================================================

if __name__ == "__main__":

    app = QApplication(sys.argv)

    window = FastFourierDashboard()
    window.show()

    sys.exit(app.exec())
