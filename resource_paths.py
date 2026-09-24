"""
resource_paths.py — 開發環境／PyInstaller 打包後共用的資源路徑工具

開發環境執行 python DA005ControlPanel.pyw 時，assets/ 底下的圖片就在原始碼
旁邊，用 __file__ 的資料夾就能找到。但打包成 exe 之後（PyInstaller
--onedir），資源檔案不是跟 .py 檔案放在同一個地方——PyInstaller 會把
datas 複製到 sys._MEIPASS 這個資料夾，所以要先判斷 sys.frozen 是不是
True，是的話改用 sys._MEIPASS 當作資源根目錄，不能只靠 __file__。

只負責「工具內建、跟著程式一起打包的資源」（assets/ 底下的圖片）路徑，
不是 runtime/ffmpeg/ffmpeg.exe 那種「放在 exe 旁邊、以後可能要換版本」的
外部工具——那個由 video_decoder.py 的 find_ffmpeg_executable() 自己用
sys.executable 的資料夾算，兩者用途不同，不要混用。
"""
import os
import sys


def resource_path(*relative_parts):
    """回傳打包資源（assets/ 底下的檔案）的絕對路徑，開發環境／
    PyInstaller 打包後都能正確找到。"""
    if getattr(sys, 'frozen', False):
        base_dir = sys._MEIPASS
    else:
        base_dir = os.path.dirname(os.path.abspath(__file__))
    return os.path.join(base_dir, *relative_parts)
