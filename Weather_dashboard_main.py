from __future__ import annotations

import json
from pathlib import Path
from datetime import datetime, date

from PySide6.QtCore import (
    Qt, QUrl, QAbstractTableModel, QModelIndex, QTimer, QSettings, QPointF
)
from PySide6.QtGui import QPainter, QPen, QColor, QBrush
from PySide6.QtWidgets import (
    QApplication, QWidget, QLineEdit, QPushButton, QLabel, QVBoxLayout, QHBoxLayout,
    QTableView, QComboBox, QFrame, QMessageBox, QFileDialog, QHeaderView, QAbstractItemView
)
from PySide6.QtNetwork import QNetworkAccessManager, QNetworkRequest

# -------------------------- API endpoints --------------------------
OPEN_METEO_GEOCODE = (
    "https://geocoding-api.open-meteo.com/v1/search?name={name}&count=1"
)
OPEN_METEO_FORECAST = (
    "https://api.open-meteo.com/v1/forecast?"
    "latitude={lat}&longitude={lon}"
    "&hourly=temperature_2m,precipitation_probability,windspeed_10m,weathercode"
    "&daily=weathercode,temperature_2m_max,temperature_2m_min,sunrise,sunset,uv_index_max"
    "&current_weather=true&timezone=auto&temperature_unit={tunit}&windspeed_unit={wunit}"
)

# -------------------------- Hourly model --------------------------
class HourlyModel(QAbstractTableModel):
    HEADERS = ["Time", "Temp", "Precip%", "Wind"]

    def __init__(self, parent=None):
        super().__init__(parent)
        self._rows: list[dict] = []

    def rowCount(self, *_):
        return len(self._rows)

    def columnCount(self, *_):
        return 4

    def headerData(self, s, o, role=Qt.DisplayRole):
        if role == Qt.DisplayRole and o == Qt.Horizontal:
            return self.HEADERS[s]
        return None

    def data(self, index: QModelIndex, role=Qt.DisplayRole):
        if not index.isValid():
            return None
        r = self._rows[index.row()]
        c = index.column()

        if role == Qt.DisplayRole:
            if c == 0: return r["time"]
            if c == 1: return f"{r['temp']:.0f}°"
            if c == 2: return f"{r['precip']:.0f}"
            if c == 3: return f"{r['wind']:.0f}"

        if role == Qt.TextAlignmentRole:
            return Qt.AlignCenter

        if role == Qt.BackgroundRole:
            # subtle accent for current hour
            try:
                hr = datetime.strptime(r["time"], "%I:%M %p").hour
                if hr == datetime.now().hour:
                    return QBrush(QColor(255, 255, 255, 18))
            except Exception:
                pass

        if role == Qt.ToolTipRole:
            return f"{r['time']}\nTemp {r['temp']:.0f}°, Wind {r['wind']:.0f}, Precip {r['precip']:.0f}%"

        return None

    def load(self, rows: list[dict]):
        self.beginResetModel()
        self._rows = rows
        self.endResetModel()

# -------------------------- Sparkline --------------------------
class Sparkline(QFrame):
    def __init__(self, parent=None):
        super().__init__(parent)
        self._values: list[float] = []
        self._pts: list[QPointF] = []
        self._accent = QColor("#5aa0ff")
        self.setMinimumHeight(60)

    def set_points(self, y_values: list[float]):
        self._values = list(y_values) if y_values else []
        self._recompute()

    def set_theme(self, condition: str):
        self._accent = QColor("#5aa0ff") if condition in ("clear", "clouds") else QColor("#6ec3ff")
        self.update()

    def resizeEvent(self, e):
        self._recompute()
        super().resizeEvent(e)

    def _recompute(self):
        self._pts.clear()
        if not self._values:
            self.update(); return
        w = max(1, self.width() - 24)
        h = max(1, self.height() - 24)
        y_min, y_max = min(self._values), max(self._values)
        dy = (y_max - y_min) or 1.0
        step = w / max(1, len(self._values) - 1)
        for i, y in enumerate(self._values):
            x = 12 + i * step
            yy = 12 + h - ((y - y_min) / dy) * h
            self._pts.append(QPointF(x, yy))
        self.update()

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing, True)
        if len(self._pts) >= 2:
            p.setPen(QPen(self._accent, 2.0))
            for i in range(1, len(self._pts)):
                p.drawLine(self._pts[i - 1], self._pts[i])
            p.setBrush(self._accent)
            p.drawEllipse(self._pts[-1], 3.5, 3.5)
        p.end()

# -------------------------- App --------------------------
class WeatherApp(QWidget):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Weather Dashboard")
        self.manager = QNetworkAccessManager(self)
        self.manager.finished.connect(self._on_reply)

        # persistence
        self.prefs_dir = Path.home() / ".config" / "WeatherDash"
        self.prefs_dir.mkdir(parents=True, exist_ok=True)
        self.prefs_path = self.prefs_dir / "prefs.json"
        self.prefs = {"favorites": [], "last_city": "", "unit": "F"}
        self._load_prefs()

        # --- top bar ---
        top = QHBoxLayout()
        self.city_edit = QLineEdit(); self.city_edit.setPlaceholderText("City (e.g., Fort Myers)")
        self.fetch_btn = QPushButton("Fetch"); self.fetch_btn.clicked.connect(self.fetch)

        self.unit_box = QComboBox(); self.unit_box.addItems(["°F", "°C"])
        self.unit_box.setCurrentIndex(0 if self.prefs.get("unit") == "F" else 1)
        self.unit_box.currentIndexChanged.connect(lambda _:
            self.fetch() if self.city_edit.text().strip() else None)

        self.fav_box = QComboBox(); self.fav_box.setPlaceholderText("Favorites")
        self.fav_box.currentTextChanged.connect(self._fav_selected)
        self.fav_add = QPushButton("+"); self.fav_add.setFixedWidth(28); self.fav_add.clicked.connect(self._fav_add)
        self.fav_del = QPushButton("–"); self.fav_del.setFixedWidth(28); self.fav_del.clicked.connect(self._fav_remove)

        self.png_btn = QPushButton("PNG")
        self.png_btn.setToolTip("Save a screenshot of the dashboard")
        self.png_btn.clicked.connect(self._export_png)

        top.addWidget(self.city_edit, 1)
        top.addWidget(self.fetch_btn)
        top.addWidget(self.unit_box)
        top.addWidget(self.fav_box)
        top.addWidget(self.fav_add)
        top.addWidget(self.fav_del)
        top.addWidget(self.png_btn)

        # --- hero ---
        self.bg = QFrame(); self.bg.setObjectName("bg")
        hero = QVBoxLayout(self.bg); hero.setContentsMargins(16, 16, 16, 16)
        self.header = QLabel("—"); self.header.setAlignment(Qt.AlignCenter)
        self.subheader = QLabel(""); self.subheader.setAlignment(Qt.AlignCenter)
        self.icon_lbl = QLabel("🌤️"); self.icon_lbl.setAlignment(Qt.AlignCenter); self.icon_lbl.setStyleSheet("font-size: 28pt;")
        self.location_label = QLabel(""); self.location_label.setAlignment(Qt.AlignCenter)
        self.spark = Sparkline()

        hero.addWidget(self.header)
        hero.addWidget(self.subheader)
        hero.addWidget(self.icon_lbl)
        hero.addWidget(self.location_label)
        hero.addWidget(self.spark)

        # --- 5-day strip ---
        self.daily_strip = QFrame(self); self.daily_strip.setObjectName("dailyStrip")
        self.daily_layout = QHBoxLayout(self.daily_strip)
        self.daily_layout.setContentsMargins(8, 0, 8, 0)
        self.daily_layout.setSpacing(8)

        # --- hourly table ---
        self.table = QTableView()
        self.model = HourlyModel(self.table); self.table.setModel(self.model)
        self.table.verticalHeader().hide()
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        self.table.setSelectionMode(QAbstractItemView.NoSelection)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)

        # --- root ---
        root = QVBoxLayout(self)
        root.addLayout(top)
        root.addWidget(self.bg)
        root.addWidget(self.daily_strip)
        root.addWidget(self.table)

        self._apply_theme("default")
        self._refresh_favorites()
        if self.prefs.get("last_city"): self.city_edit.setText(self.prefs["last_city"])

        # auto refresh timer (10 min)
        self.auto_timer = QTimer(self)
        self.auto_timer.setInterval(10 * 60 * 1000)
        self.auto_timer.timeout.connect(lambda: self.fetch() if self.city_edit.text().strip() else None)

        # geometry
        try:
            qs = QSettings("COP3003", "WeatherDash")
            g = qs.value("geometry")
            if g: self.restoreGeometry(g)
        except Exception:
            pass

    # ---------------- actions ----------------
    def fetch(self):
        name = self.city_edit.text().strip()
        if not name:
            QMessageBox.information(self, "Missing", "Please enter a city.")
            return
        self.fetch_btn.setEnabled(False)
        self.prefs["unit"] = "F" if self.unit_box.currentText().endswith("F") else "C"
        self._save_prefs()

        url = QUrl(OPEN_METEO_GEOCODE.format(name=name))
        reply = self.manager.get(QNetworkRequest(url))
        reply.setProperty("kind", "geocode")

    def _on_reply(self, reply):
        kind = reply.property("kind")
        try:
            payload = json.loads(bytes(reply.readAll()).decode("utf-8"))
        except Exception:
            payload = {}
        if kind == "geocode":
            self._handle_geocode(payload)
        elif kind == "forecast":
            self._handle_forecast(payload)
        reply.deleteLater()

    def _handle_geocode(self, payload: dict):
        results = payload.get("results") or []
        if not results:
            QMessageBox.warning(self, "Not found", "City not found.")
            self.fetch_btn.setEnabled(True); return

        r0 = results[0]
        lat, lon = r0["latitude"], r0["longitude"]
        self._resolved_place = ", ".join([p for p in (r0.get("name"), r0.get("admin1"), r0.get("country")) if p])

        tunit = "fahrenheit" if self.prefs.get("unit") == "F" else "celsius"
        wunit = "mph" if tunit == "fahrenheit" else "kmh"

        url = QUrl(OPEN_METEO_FORECAST.format(lat=lat, lon=lon, tunit=tunit, wunit=wunit))
        reply = self.manager.get(QNetworkRequest(url))
        reply.setProperty("kind", "forecast")

    def _handle_forecast(self, payload: dict):
        current = payload.get("current_weather") or {}
        hourly  = payload.get("hourly") or {}
        daily   = payload.get("daily") or {}

        temp = current.get("temperature", 0.0)
        wind = current.get("windspeed", 0.0)
        code = int(current.get("weathercode", 0))
        cond = self._condition_from_code(code)
        unit_letter = self.prefs.get("unit", "F")
        self.header.setText(f"{temp:.0f}°{unit_letter} — {cond.title()}")
        self.subheader.setText(f"Wind: {wind:.0f} {'mph' if unit_letter=='F' else 'km/h'}  |  {date.today().strftime('%A')}")
        self.icon_lbl.setText(self._emoji_from_code(code))
        self.location_label.setText(self._resolved_place)

        # hourly rows (today only)
        rows = []
        times = hourly.get("time", [])
        temps = hourly.get("temperature_2m", [])
        precs = hourly.get("precipitation_probability", [])
        winds = hourly.get("windspeed_10m", [])
        today = date.today().isoformat()

        spark_vals = []
        for i, t in enumerate(times):
            try:
                dt = datetime.fromisoformat(t)
            except Exception:
                continue
            if dt.date().isoformat() != today:
                continue
            rows.append({
                "time": dt.strftime("%I:%M %p").lstrip("0"),
                "temp": float(temps[i]) if i < len(temps) else 0.0,
                "precip": float(precs[i] or 0.0) if i < len(precs) else 0.0,
                "wind": float(winds[i]) if i < len(winds) else 0.0,
            })
            if i < len(temps): spark_vals.append(float(temps[i]))

        self.model.load(rows)
        self.spark.set_points([r["temp"] for r in rows] if rows else spark_vals)

        # daily (5)
        rows_daily = []
        d_dates = daily.get("time", [])
        d_max   = daily.get("temperature_2m_max", [])
        d_min   = daily.get("temperature_2m_min", [])
        d_code  = daily.get("weathercode", [])
        for i in range(min(5, len(d_dates))):
            rows_daily.append({
                "date": datetime.fromisoformat(d_dates[i]).strftime("%a"),
                "max": float(d_max[i]) if i < len(d_max) else 0.0,
                "min": float(d_min[i]) if i < len(d_min) else 0.0,
                "code": int(d_code[i]) if i < len(d_code) else 0,
            })
        self._fill_daily_strip(rows_daily)

        self._apply_theme(cond)
        self.fetch_btn.setEnabled(True)

        # start auto refresh after first good fetch
        try:
            if not self.auto_timer.isActive():
                self.auto_timer.start()
        except Exception:
            pass

        # remember last city
        self.prefs["last_city"] = self.city_edit.text().strip()
        self._save_prefs()

    # ---------------- helpers ----------------
    def _condition_from_code(self, code: int) -> str:
        if code == 0: return "clear"
        if code in (1, 2, 3): return "clouds"
        if code in (45, 48): return "fog"
        if 51 <= code <= 67: return "drizzle" if code < 61 else "rain"
        if 71 <= code <= 77: return "snow"
        if 80 <= code <= 82: return "showers"
        if 95 <= code <= 99: return "storm"
        return "default"

    def _emoji_from_code(self, code: int) -> str:
        if code == 0: return "☀️"
        if code in (1,2,3): return "⛅️"
        if code in (45,48): return "🌫️"
        if 51 <= code <= 67: return "🌦️" if code < 61 else "🌧️"
        if 71 <= code <= 77: return "🌨️"
        if 80 <= code <= 82: return "🌧️"
        if 95 <= code <= 99: return "⛈️"
        return "🌤️"

    def _fill_daily_strip(self, rows_daily: list[dict]):
        # clear previous
        while self.daily_layout.count():
            w = self.daily_layout.takeAt(0).widget()
            if w: w.deleteLater()
        for d in rows_daily:
            card = QFrame(); card.setObjectName("dailyCard")
            v = QVBoxLayout(card); v.setContentsMargins(10, 8, 10, 8)
            L1 = QLabel(d.get("date","")); L1.setAlignment(Qt.AlignCenter)
            L2 = QLabel(self._emoji_from_code(d.get("code",0))); L2.setAlignment(Qt.AlignCenter); L2.setStyleSheet("font-size: 18pt;")
            L3 = QLabel(f"{d.get('max',0):.0f}° / {d.get('min',0):.0f}°"); L3.setAlignment(Qt.AlignCenter)
            for L in (L1, L2, L3): v.addWidget(L)
            self.daily_layout.addWidget(card)
        self.daily_layout.addStretch(1)

    def _apply_theme(self, condition: str):
        palettes = {
            "clear":   "qlineargradient(x1:0,y1:0,x2:1,y2:1, stop:0 #87CEFA, stop:1 #FFE69A)",
            "clouds":  "qlineargradient(x1:0,y1:0,x2:1,y2:1, stop:0 #c9d6df, stop:1 #f0f3f5)",
            "rain":    "qlineargradient(x1:0,y1:0,x2:1,y2:1, stop:0 #5a8bbb, stop:1 #2f4858)",
            "drizzle": "qlineargradient(x1:0,y1:0,x2:1,y2:1, stop:0 #9bbad1, stop:1 #6d8299)",
            "snow":    "qlineargradient(x1:0,y1:0,x2:1,y2:1, stop:0 #e6f2ff, stop:1 #cfe0f5)",
            "fog":     "qlineargradient(x1:0,y1:0,x2:1,y2:1, stop:0 #d7d7d7, stop:1 #eeeeee)",
            "showers": "qlineargradient(x1:0,y1:0,x2:1,y2:1, stop:0 #7393B3, stop:1 #4b6584)",
            "storm":   "qlineargradient(x1:0,y1:0,x2:1,y2:1, stop:0 #2b2d42, stop:1 #4b4e6d)",
            "default": "qlineargradient(x1:0,y1:0,x2:1,y2:1, stop:0 #dde6f1, stop:1 #eef2f7)"
        }
        self.bg.setStyleSheet(f"""
            QFrame#bg {{ border-radius: 12px; padding: 18px; background: {palettes.get(condition, palettes['default'])}; }}
            QLabel {{ font-size: 22px; }}
            QFrame#dailyCard {{ background: rgba(255,255,255,0.07); border-radius: 10px; }}
        """)
        self.spark.set_theme(condition)

    # favorites / prefs
    def _refresh_favorites(self):
        self.fav_box.blockSignals(True)
        self.fav_box.clear()
        for c in self.prefs.get("favorites", []):
            self.fav_box.addItem(c)
        self.fav_box.blockSignals(False)

    def _fav_add(self):
        c = self.city_edit.text().strip()
        if not c: return
        favs = self.prefs.setdefault("favorites", [])
        if c not in favs:
            favs.append(c)
            self._save_prefs()
            self._refresh_favorites()

    def _fav_remove(self):
        c = self.fav_box.currentText().strip()
        if not c: return
        favs = self.prefs.setdefault("favorites", [])
        if c in favs:
            favs.remove(c)
            self._save_prefs()
            self._refresh_favorites()

    def _fav_selected(self, name: str):
        if not name: return
        self.city_edit.setText(name)
        self.fetch()

    def _load_prefs(self):
        try:
            if self.prefs_path.exists():
                self.prefs.update(json.loads(self.prefs_path.read_text()))
        except Exception:
            pass

    def _save_prefs(self):
        try:
            self.prefs_path.write_text(json.dumps(self.prefs, indent=2))
        except Exception:
            pass

    # export PNG
    def _export_png(self):
        path, _ = QFileDialog.getSaveFileName(self, "Save PNG", "dashboard.png", "PNG Files (*.png)")
        if not path: return
        self.grab().save(path, "PNG")
        QMessageBox.information(self, "Saved", f"Saved to {path}")

    # save geometry
    def closeEvent(self, e):
        try:
            QSettings("COP3003", "WeatherDash").setValue("geometry", self.saveGeometry())
        finally:
            return super().closeEvent(e)

# -------------------------- main --------------------------
if __name__ == "__main__":
    app = QApplication([])
    w = WeatherApp()
    w.resize(860, 680)
    w.show()
    app.exec()
