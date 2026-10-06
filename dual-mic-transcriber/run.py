"""PyInstaller / 開発実行用のエントリースクリプト。"""
import multiprocessing

if __name__ == "__main__":
    multiprocessing.freeze_support()
    from app.main import main

    main()
