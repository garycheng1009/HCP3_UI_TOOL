# -*- mode: python ; coding: utf-8 -*-
"""
Control_Panel_mac.spec — DA005ControlPanel 正式打包設定（macOS onedir 版本）

沿用 DA005_DVR_Control_Panel_v1.0.spec 的 Analysis/datas/hiddenimports
設定（打包內容本身沒有變化，只是版本號跟視窗行為調整），詳細理由見
v1.0 那份 spec 的說明，這裡不重複。

2026-08-26 v2.0 兩項調整：
1. 版本名稱從 v1.0 改成 v2.0（exe 檔名、COLLECT 資料夾名稱都跟著改）。
2. console=True -> False：v1.0 那輪保留 console 是為了方便在乾淨 PC 上
   出錯時看 traceback；這輪功能驗證已完成、準備正式發行，改成不顯示
   Console/Terminal 視窗（windowed）。

2026-10-15 v3.0：版本名稱從 v2.0 改成 v3.0（exe 檔名、COLLECT 資料夾名稱、
macOS .app 名稱都跟著改），跟 DA005ControlPanel.pyw 的 MAIN_WINDOW_TITLE
（視窗標題）一致。

打包方式（在這支 .spec 檔案所在資料夾執行）：
    pyinstaller Control_Panel_mac.spec

2026-09-21：改用 PyInstaller 預設輸出路徑（dist/、build/），不再另外
指定 --distpath/--workpath，理由見 README.md「重新打包」章節。
"""

import os

from PyInstaller.utils.hooks import collect_submodules, collect_data_files

block_cipher = None

PROJECT_DIR = os.path.dirname(os.path.abspath(SPEC))

# google.protobuf / google.type 是 PEP 420 namespace package，protobuf
# 套件跟 googleapis-common-protos 套件各自貢獻一部分子模組到同一個
# google 命名空間——實測過 PyInstaller 對這種「兩個不同 pip 套件共用同一個
# 頂層 namespace package」的狀況，靜態分析偶爾會漏掉其中一邊，這裡明確用
# collect_submodules() 各自收一次，不要只靠 Analysis 自動追蹤 import。
hidden_imports = (
    collect_submodules('google.protobuf')
    + collect_submodules('google.type')
    + [
        # protobuf 4.x 預設用 upb（C++ 實作）的 extension module，不是
        # 一般的 from X import Y，靜態分析追不到，一定要明確列出來。
        'google._upb._message',
        'PIL.ImageTk',
        'PIL._tkinter_finder',
        # 2026-08-20 Codex review 抓到：screen_fake_ui.py 大頭貼用
        # renderPM.drawToFile() 把 user.svg 轉成 PNG，reportlab 5.x 的
        # renderPM 沒有內建點陣圖 backend，一定要靠 rlPyCairo 這個外掛
        # （import 是寫在 reportlab 原始碼的函式內部，不是一般的頂層
        # from X import Y，實測過目前這版 PyInstaller 靜態分析抓得到、
        # dist 資料夾也確認有收到 cairo/_cairo.*.pyd，這裡明確列出來是
        # 為了不要依賴這個「抓得到」的行為以後版本升級還成立）。
        'rlPyCairo',
        'cairo',
    ]
)

datas = [
    (os.path.join(PROJECT_DIR, 'assets'), 'assets'),
]
# reportlab 內建的字型/資料檔（screen_fake_ui.py 的大頭貼用 svglib+
# reportlab 把 user.svg 轉成 PNG，需要這些資料檔才能正常畫）。
datas += collect_data_files('reportlab')

# ffmpeg 是可執行檔，放進 binaries=（不是 datas=）——PyInstaller 打包
# binaries 清單裡的項目時會確保可執行權限，datas 只是單純複製資料，
# 來源檔案權限遺失（例如從 Windows 檔案系統複製過來）也不會補回來，
# 放對清單才能保證打包出來的 mac App 裡 ffmpeg 一定可執行。
binaries = [
    (os.path.join(PROJECT_DIR, 'runtime', 'ffmpeg', 'ffmpeg'), os.path.join('runtime', 'ffmpeg')),
]

a = Analysis(
    ['DA005ControlPanel.pyw'],
    pathex=[PROJECT_DIR],
    binaries=binaries,
    datas=datas,
    hiddenimports=hidden_imports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
    cipher=block_cipher,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    # 2026-09-16 使用者要求：打包產出的名稱（exe 檔名／COLLECT 資料夾）
    # 從 DA005_DVR_Control_Panel 改成 Dashcam_Control_Panel，版本號格式
    # 不變（沿用 _v3.0 這種寫法）。這支 .spec 檔案本身的檔名維持不變，
    # 只改實際打包出來的產物名稱。
    name='Dashcam_Control_Panel_v3.0',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,          # 關掉 UPX 壓縮——求「防毒誤判機率最低、啟動最快、
                         # 缺檔最容易診斷」，不是求檔案最小，沿用 v1.0 的決定。
    console=False,       # 2026-08-26 功能驗證完成，準備正式發行：不顯示
                         # Console/Terminal 視窗。
    contents_directory='.',
    # exe 檔案本身（工作列/檔案總管看到的圖示）用自訂的行車紀錄器 outline
    # icon，跟 DA005ControlPanel.pyw 的 window.iconbitmap() 用同一份
    # assets/app_icon.ico（見 generate_app_icon.py）。
    icon=os.path.join(PROJECT_DIR, 'assets', 'app_icon.ico'),
)

coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name='Dashcam_Control_Panel_v3.0',
)

app = BUNDLE(
    coll,
    name='Dashcam_Control_Panel_mac_v3.0.app',
    icon=None,
    bundle_identifier='com.dashcam.controlpanel',
    # 2026-10-13 macOS Live View 改用 UVC（ffmpeg AVFoundation 讀 Dashcam 的
    # "USB2 NCM" 攝影機）。ffmpeg 是這個 .app 的子行程，macOS 會用 .app
    # 本身的身分詢問相機權限；Info.plist 沒有 NSCameraUsageDescription 的話
    # 系統會直接拒絕存取（甚至把 App 關掉）。只讀影像不讀聲音（-i "名稱:none"），
    # 不需要 NSMicrophoneUsageDescription。
    # 目前沒有 codesign；以後如果改成簽章 + Hardened Runtime，還要另外加
    # com.apple.security.device.camera entitlement。
    info_plist={
        'NSCameraUsageDescription': 'Dashcam Control Panel uses the camera to show the dashcam Live View over USB (UVC).',
    },
 )