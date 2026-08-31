"""PyInstaller GUI 启动入口。"""

from multiprocessing import freeze_support

from UI.__main__ import main


if __name__ == "__main__":
    freeze_support()
    raise SystemExit(main())
