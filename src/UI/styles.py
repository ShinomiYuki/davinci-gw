"""集中维护少量视觉常量和浅色样式。"""

APP_STYLE = """
QMainWindow, QWidget { background: #f5f7fa; color: #1f2937; }
QGroupBox { background: #ffffff; border: 1px solid #d9e0e8; border-radius: 8px;
            margin-top: 10px; padding-top: 10px; font-weight: 600; }
QGroupBox::title { subcontrol-origin: margin; left: 12px; padding: 0 4px; }
QLineEdit, QComboBox, QTableView { background: #ffffff; border: 1px solid #c9d2dc;
                                  border-radius: 5px; padding: 5px; }
QLineEdit:focus, QComboBox:focus { border-color: #2563eb; }
QPushButton { background: #ffffff; border: 1px solid #b8c3cf; border-radius: 5px;
              padding: 6px 14px; }
QPushButton:hover { background: #eef4ff; }
QPushButton#primaryButton { background: #2563eb; color: white; border-color: #2563eb; }
QPushButton#primaryButton:disabled { background: #9eb7df; border-color: #9eb7df; }
QLabel#statusBanner { background: #eaf2ff; border-radius: 6px; padding: 8px; }
QFrame#featureCard { background: #ffffff; border: 1px solid #d9e0e8; border-radius: 7px; }
QLabel#outputError { color: #b42318; }
"""
