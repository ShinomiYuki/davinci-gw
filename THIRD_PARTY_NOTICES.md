# 第三方软件许可说明

DaVinci GW 项目自身使用 MIT License，见工程根目录 `LICENSE`。GUI 免安装包还包含下列第三方组件：

| 组件 | 本次构建版本 | 许可口径 | 官方来源 |
|---|---:|---|---|
| Python | 3.12 | Python Software Foundation License | https://docs.python.org/3/license.html |
| PySide6 / Shiboken6 | 6.8.3 | LGPL-3.0-only 或商业许可；本发布按 LGPL-3.0-only 使用 | https://doc.qt.io/qtforpython-6/licenses.html |
| Qt | 6.8.3 | 使用到的 Qt Core、Gui、Widgets 等模块按 LGPL-3.0-only 使用 | https://www.qt.io/licensing/open-source-lgpl-obligations |
| openpyxl | 3.1.5 | MIT | https://foss.heptapod.net/openpyxl/openpyxl |
| et_xmlfile | 2.0.0 | MIT | https://foss.heptapod.net/openpyxl/et_xmlfile |
| lxml | 6.1.2 | BSD-3-Clause；其二进制依赖另按各自许可 | https://lxml.de/credits.html |
| PyInstaller | 6.22.2 | GPL-2.0-or-later，附带打包非自由程序的特别例外 | https://pyinstaller.org/en/stable/license.html |
| OpenSSL | 3.6.3 | Apache-2.0 | https://www.openssl.org/source/license.html |

发布目录中的 `third-party-licenses` 保存本次构建环境随附的许可证原文。Qt 动态库位于免安装目录，可由接收者替换；本项目未对 Qt/PySide6 作修改。

本文件是工程技术侧的开源组件清单，不替代公司合规或法务审核。正式对外交付前仍应由组织按实际发布范围完成许可证确认。
