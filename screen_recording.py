"""
screen_recording.py — Recording 首頁畫面

畫面上只有三個操作按鈕（Recording / Event / Photo）+ 一個顯示「最新影片」縮圖的
大縮圖區域，不再有可切換分類的「資料瀏覽」清單（舊版本那個 Recording/Event/Photo
三個切換鈕已經拿掉，這三個按鈕現在同時就是操作按鈕）。

====================== 核心規則（很重要，改這支檔案前先看這裡） ======================

1. 判斷目前是 Standby / Scenic Recording / Event Recording，一律用 derive_dvr_state()，
   同時看 dvr_mode 跟 event_recording_setting 兩個欄位，不能只看某一個欄位就猜。

2. 每一次送出控制指令（開始/停止 Scenic、觸發 Event）之後，一律要「再 GET 一次
   DvrMode 確認」，確認結果跟預期相符才更新按鈕顏色；不能送出 PUT 就假設一定成功。

3. Event 30 秒會不會結束，不是用固定間隔去 GET DvrMode 猜，是等 KeepAlive 回應
   通知「DvrMode 有變化」（api.wait_for_dvr_mode_change_notification()）才去
   GET 確認 event_recording_setting 是不是不再是 DVREVENT_USER_TRIGGERED
   （實機驗證過：KeepAlive 通常 50~250ms 內就會回報，比固定輪詢更快更省）。
   不能用 sleep(30) 猜時間就假設結束了。

4. 「縮圖要不要換」跟「Recording/Event 按鈕顏色要不要換」是兩件分開但都由
   同一種事件驅動的事：
   - 按鈕顏色：DvrMode 輪詢隨時都可能觸發更新（apply_dvr_state_to_buttons）
   - 縮圖：由「已確認的 DVR state transition」驅動，統一收斂到
     refresh_preview_for_dvr_state()（見該函式說明），背景 DvrMode 輪詢
     本身絕對不會直接去換縮圖或縮圖能不能播放——輪詢只負責發現「狀態變了」
     這件事，實際換縮圖一定要等對應的 transition confirm 流程呼叫
     refresh_preview_for_dvr_state() 才會做。Scenic/Event Start 這次
     transition 真正對應的新檔案可能要等 DVR 登記幾秒，這裡改成
     refresh_preview_for_dvr_state() 內部分兩段獨立等待：先等新
     recording 出現（KeepAlive 通知喚醒，最多 15 秒），再等縮圖準備好
     （固定短間隔重試，最多 5 秒），見 _wait_for_new_group_of_type()
     跟 _wait_for_thumbnail_ready()（這是恢復真機驗證過正常的舊版
     v1.0 行為，不是新設計）。

5. KeepAlive（api.start_keepalive()）負責兩件事：
   - 回報「跟 DVR 的連線還活著」
   - 回報「DvrMode / Recordings 什麼時候有變化」（updated_resources 欄位），
     這支檔案拿它當「什麼時候該去查一次真實狀態」的觸發訊號
   但錄影/事件狀態、縮圖清單本身的內容，一律只信任 GET /dashcam/dvr_mode /
   GET /dashcam/recordings 的實際回應，KeepAlive 本身不會直接拿來當作狀態用。

6. Photo 是完全獨立的動作：任何狀態下都可以拍照，拍照本身不會呼叫
   stop_recording()、不會改變 Recording/Event 按鈕顏色、也不會動到縮圖。

2026-08-11 純樣式調整（不動任何功能邏輯，DVR API/KeepAlive/DvrMode/
Recording/Event/Photo/Thumbnail/Player 邏輯完全沒改）：改用跟 Gallery
相同的 Dark Theme（直接 import screen_gallery 共用顏色常數，不另外建立
一套 Recording Page 自己的 Theme）；移除左側白色 Sidebar；三個按鈕統一
Dark Button Style；Idle 用深灰、Recording/Event Active 跟 Photo 拍照中
用紅色底色（不是文字顏色）；移除「Latest Video」標題；No Video 改成
Dark Gray Placeholder + "No videos available"。
"""

import os
import sys
import threading
import hashlib
import tkinter as tk
from tkinter import messagebox
from time import sleep, time
from datetime import datetime
from io import BytesIO
from PIL import Image, ImageDraw, ImageFont, ImageTk

import api
import audi_pb2
import live_view_controller as live_view_controller_module
import screen_gallery
import ui_shapes
from resource_paths import resource_path

# 2026-08-14 改用真的 UI 素材（從 參考圖2.png 這張 HCP3 UI 資源表裁切出來，
# 存在 assets/ 底下），取代原本用 PIL 手畫的線條圖示。
#
# 2026-08-20 改用 resource_path()（見 resource_paths.py）取代原本單純用
# __file__ 的絕對路徑組出來的寫法——PyInstaller 打包成 exe 之後，
# __file__ 所在的資料夾不是資源檔案真正的存放位置，要改用
# sys._MEIPASS，resource_path() 已經處理好開發環境／打包後兩種情況。
ASSETS_DIR = resource_path('assets')


def load_asset_image(filename, max_size=None):
    """讀取 assets/ 底下裁切好的 UI 素材圖片。max_size 有給的話會用
    Image.thumbnail() 等比例縮放（不會變形拉伸，維持圖片原始比例）。"""
    image = Image.open(os.path.join(ASSETS_DIR, filename)).convert('RGBA')
    if max_size is not None:
        image = image.copy()
        image.thumbnail(max_size, Image.LANCZOS)
    return image

# 2026-08-13 UI 調整：Preview 區域放大、跟左側功能面板的比例更接近 HCP3 參考畫面
# （原本是 560x360）。buttons_frame 的高度直接用這個常數對齊，改這裡兩邊會一起變。
# 2026-08-14 第二次調整：稍微加寬（640→680），跟左側面板加寬＋間距縮小
# 一起讓整體比例更接近 HCP3 實機截圖。
#
# 2026-08-19 使用者對照原圖第三次調整：改用固定的 16:9（一般行車紀錄器
# 縮圖/影片本身的比例）直接算出 PREVIEW_SIZE，不再是「量測可用空間、
# 塞滿整個矩形」的做法——塞滿矩形會讓 Preview 的長寬比跟縮圖本身不一致，
# fit_image_contain() 只能靠上下/左右留白硬湊，留白反而變成畫面上一大片
# 沒有內容的灰色空白。改成先決定 Preview 應該多寬（PREVIEW_WIDTH），
# 高度直接用 16:9 算出來，理論上 letterbox 應該趨近於 0（實際縮圖比例
# 剛好也是 16:9 的話）。
#
# 這也連帶讓左側 Recording/Event/Photo Panel 的高度（panel_height，直接
# 等於 PREVIEW_SIZE[1]）真正對齊「實際影像顯示區域」的 top/bottom，不再
# 是對齊一個含 letterbox 的高 Preview Container。
#
# 2026-08-19 使用者要求 Window geometry 要「依內容反推」，不要「先決定
# 視窗大小再讓內容想辦法塞滿」——這裡不再像上一輪那樣在 build_screen()
# 當下用 parent.winfo_width()/height() 量測，PREVIEW_SIZE 變回建置期就
# 確定的常數，DA005ControlPanel.pyw 改成呼叫下面的
# compute_recording_content_size() 先算出 Recording 需要的尺寸，再拿去
# 決定 window.geometry()，不用再呼叫 window.update() 強制 realize
# （上一輪 Codex review 指出 window.update() 在 close protocol 註冊前
# 執行有風險，這裡直接把整個「執行期量測」機制移除，一次解決）。
PREVIEW_ASPECT_RATIO = 16 / 9
PREVIEW_WIDTH = 700
PREVIEW_SIZE = (PREVIEW_WIDTH, round(PREVIEW_WIDTH / PREVIEW_ASPECT_RATIO))

PANEL_WIDTH = 260
LEFT_NAV_PADX = (20, 6)
RIGHT_AREA_PADX = (6, 20)

# 2026-08-19 使用者第四次對照原圖要求：Window 高度再縮 40~60px，優先從
# Main Content 跟 Bottom Control Bar 之間「多餘的黑色空間」下手，不縮小
# 實際 Preview 畫面（PREVIEW_SIZE 不變）。VERTICAL_PADY 改成不對稱的
# (top, bottom)——上緣維持一點呼吸空間，下緣（正是「Main Content 跟
# Bottom Bar 之間」那段）縮小。
#
# 2026-08-19 使用者最後一輪視覺調整再次要求「把多餘的垂直空白拿掉，
# Recording/Event/Photo + Preview 整組往下貼近 Bottom Bar」，進一步收緊
# 到 (8, 4)。
#
# 2026-08-20 使用者要求主內容區更靠近 Bottom Bar，這裡下緣（bottom）從
# 2 再收到 1（純留白部分的極限，再小視覺上就完全貼到底了）。實際最終
# 間距還包含下面 status_label 那行真正的文字高度（STATUS_LABEL_RESERVED_
# HEIGHT），那個不是純留白、是會顯示真實 DVR 狀態文字的功能區塊，不能為
# 了湊數字而砍到會裁字——詳見 STATUS_LABEL_TOP_GAP 那裡的說明。
VERTICAL_PADY = (8, 1)

# 2026-08-19 使用者最後一輪要求：Preview 底下的留白要縮到「只留必要
# padding」。原本 status_label 侷限在 PANEL_WIDTH(260px) 寬，最長的既有
# 文字（例如 'Event status unknown: failed to verify DVR status. Please
# check manually.'）會換行成 2 行、需要 36px，逼得整個底部留白至少要
# 46px+ 起跳，跟「只留必要 padding」的視覺目標衝突。
#
# 實測過：同一句文字如果不侷限在 260px 寬、而是給到 >=700px 寬
# （wraplength），全部既有 status 文字都只需要 1 行（reqheight=21px）。
# 所以這裡把 status_label 從 left_nav 移到 frame（見 build_screen()），
# 寬度改成跟 Panel+Preview 合起來一樣寬，只在垂直位置上仍然貼齊 Panel
# 左緣下方——文字內容/觸發時機完全沒變，只是改寬度/位置，讓它單行顯示，
# 大幅縮小需要保留的垂直空間（46px -> 約 28px）。
# 2026-08-20 使用者要求主內容區底部間距縮到 8~10px：Panel 底部到文字之間
# 的間距（純留白）從 3 再收到 1；STATUS_LABEL_TEXT_HEIGHT 的 3px 餘裕
# 也收到 1px（實測單行文字本身需要 21px，這 21px 是真正會顯示 DVR 狀態
# 文字需要的高度，不能再往下砍，砍了會裁字）。收到這裡之後純留白
# （VERTICAL_PADY[1] + STATUS_LABEL_TOP_GAP = 1+1 = 2px）已經逼近極限，
# 但因為文字本身還要 22px，總間距（2+22=24px）還是比 8~10px 大，這是
# 目前這個結構（狀態文字跟 Panel/Preview 同一路垂直排列）下沒辦法再往下
# 收的原因——如果要真的做到 8~10px，需要把 status_label 從「Panel/
# Preview 下方」這個位置移開（例如疊到別的地方顯示），這已經不是留白
# 數字調整、是版面結構改動，這輪沒有做，先維持文字仍在原位置正常顯示。
STATUS_LABEL_TOP_GAP = 1      # Panel 底部到 status_label 文字之間的間距
STATUS_LABEL_TEXT_HEIGHT = 22  # 單行文字實測 21px，這裡留 22px 有 1px 餘裕
STATUS_LABEL_RESERVED_HEIGHT = STATUS_LABEL_TOP_GAP + STATUS_LABEL_TEXT_HEIGHT


def compute_recording_content_size():
    """回傳 Recording 畫面需要的 (width, height)，給 DA005ControlPanel.pyw
    在建立視窗「之前」計算 window.geometry() 用——由內容需要的尺寸反推
    視窗要多大，不是先給定一個視窗尺寸再讓內容想辦法塞滿/留白。"""
    content_width = (LEFT_NAV_PADX[0] + LEFT_NAV_PADX[1] + PANEL_WIDTH
                      + RIGHT_AREA_PADX[0] + RIGHT_AREA_PADX[1] + PREVIEW_SIZE[0])
    content_height = sum(VERTICAL_PADY) + PREVIEW_SIZE[1] + STATUS_LABEL_RESERVED_HEIGHT
    return content_width, content_height

# 錄影/事件/拍照的「Active」狀態統一用紅色底色標示，跟 Gallery 的中性色分開定義
# （Gallery 自己的 Theme 沒有「錄影中」這種狀態概念，所以這個顏色不放進 screen_gallery.py）。
ACTIVE_RED_COLOR = '#e5484d'

# ============================================================
# HCP3 高擬真配色 / 圓角（2026-08-14 對照真機截圖新增）
# ============================================================
# 左側功能面板跟 Standby 畫面在真機上是「淺色卡片」，跟 Top Bar／右側背景
# 的深色系是刻意的對比（不是我猜的風格，是三張真機照片比對出來的）。
# 深色系（Top Bar、外圍背景）維持沿用 screen_gallery 既有的 BACKGROUND_COLOR，
# 這裡只新增淺色卡片專用的顏色，不動 screen_gallery 既有的深色 Theme。
#
# 2026-08-14 第三次調整：Idle 底色/文字/Divider 整體加深一階，讓 Active
# 的白色區塊更能自然突出（不是繼續加深 Active 的粉紅色飽和度）。
HCP3_LIGHT_PANEL_COLOR = '#ddd9e1'
HCP3_LIGHT_PANEL_TEXT_COLOR = '#34313a'
HCP3_LIGHT_PANEL_DIVIDER_COLOR = '#b7b0bc'
HCP3_LIGHT_PANEL_DISABLED_TEXT_COLOR = '#9d97a3'
HCP3_PANEL_CORNER_RADIUS = 18       # 左側面板／Preview 圓角半徑
HCP3_PANEL_INSET = 6                # 面板內縮量，讓圓角在四個角落露出來（見 build_screen() 說明）

# ============================================================
# Recording / Event / Photo 按鈕圖示
# ============================================================
# tk.Button 沒辦法像 Gallery List 那樣直接塞一個 Canvas 進去當圖示（Gallery
# 的 create_type_icon() 回傳的是 Canvas，只能當獨立 widget 擺在 Label 旁邊，
# 不能放進 Button 的 image= 參數），所以這裡用 load_asset_image() 讀取
# assets/ 底下裁切好的 PNG，轉成 Button 的 image=/compound='left' 用。
BUTTON_ICON_SIZE = 28  # 2026-08-25 使用者要求 Recording/Event/Photo 視覺微調：24→28（+16.7%），原始素材 100x75，放大不會糊


# 2026-08-14 對照真機「錄影中」截圖（參考圖3.png）新增：Active 狀態不是
# 整格變紅色背景，是這一格底色變成近白色，圖示／文字變成粉紅紅色。跟既有的
# ACTIVE_RED_COLOR（Photo 拍照中短暫閃紅、Gallery Event 類型圖示色）是
# 不同用途，不共用，避免這次調整意外連動改到那兩個地方的顏色。
#
# 2026-08-14 第二次調整：第一版顏色（#f7eef7 / #e94e9e）是直接從照片取樣，
# 但照片本身有玻璃反光/環境光造成的偏淡效果，實際套用到 PC Control Panel
# 上辨識度不夠、看起來像 Disabled 不像 Active。改成保留「白底＋紅字＋紅
# icon」的設計原則，但提高對比度：底色改成接近純白，文字/icon 顏色改深、
# 提高飽和度，不是照片的精確 RGB 值。
#
# 2026-08-14 第三次調整：Active 本身已經滿意，不再加深粉紅色飽和度；
# 底色改成純白（#FFFFFF），改成靠加深 Idle（見上面 HCP3_LIGHT_PANEL_*）
# 來拉開對比，讓 Active 的白色區塊自然突出。
#
# 2026-08-14 第四次調整：純白（#FFFFFF）跟整體 HCP3 UI 的灰紫色調不吻合，
# 改成帶灰紫感的近白色，「亮起來」但不是突兀的純白。
HCP3_ACTIVE_ROW_COLOR = '#f3eff4'
HCP3_ACTIVE_TEXT_COLOR = '#d81b60'


def recolor_image(image, hex_color):
    """把一張 RGBA 圖片所有非透明像素改成同一個顏色（保留原本的 alpha
    透明度、形狀完全不變），拿來從同一份 icon 素材產生 Active 狀態要用的
    粉紅紅色版本，不用另外裁一份新素材、也不會破壞圖案本身。"""
    target_r = int(hex_color[1:3], 16)
    target_g = int(hex_color[3:5], 16)
    target_b = int(hex_color[5:7], 16)
    recolored = image.copy().convert('RGBA')
    pixels = recolored.load()
    for y in range(recolored.height):
        for x in range(recolored.width):
            _, _, _, alpha = pixels[x, y]
            if alpha > 0:
                pixels[x, y] = (target_r, target_g, target_b, alpha)
    return recolored


def build_camera_icon_images():
    """Recording 按鈕圖示：回傳 (Idle 深色版, Active 粉紅紅色版) 一組
    PhotoImage。兩個版本共用同一份 assets/recording_icon.png，只是重新
    上色，不是另外裁切兩份素材。"""
    idle_image = load_asset_image('recording_icon.png', (BUTTON_ICON_SIZE, BUTTON_ICON_SIZE))
    active_image = recolor_image(idle_image, HCP3_ACTIVE_TEXT_COLOR)
    return ImageTk.PhotoImage(idle_image), ImageTk.PhotoImage(active_image)


def build_event_icon_images():
    """Event 按鈕圖示：回傳 (Idle 深色版, Active 粉紅紅色版)，理由同上。"""
    idle_image = load_asset_image('event_icon.png', (BUTTON_ICON_SIZE, BUTTON_ICON_SIZE))
    active_image = recolor_image(idle_image, HCP3_ACTIVE_TEXT_COLOR)
    return ImageTk.PhotoImage(idle_image), ImageTk.PhotoImage(active_image)


def build_photo_icon_images():
    """Photo 按鈕圖示：回傳 (Idle 深色版, Active 粉紅紅色版)。2026-08-14
    第四次調整：Photo 點擊時短暫顯示的效果原本是整格變紅色底
    （ACTIVE_RED_COLOR），跟 Recording/Event 是不同一套視覺——改成跟
    Recording/Event 共用同一套 Active 樣式（HCP3_ACTIVE_ROW_COLOR/
    HCP3_ACTIVE_TEXT_COLOR），所以 Photo 也需要一個 Active 版本的圖示，
    不再只有單一版本。"""
    idle_image = load_asset_image('photo_icon.png', (BUTTON_ICON_SIZE, BUTTON_ICON_SIZE))
    active_image = recolor_image(idle_image, HCP3_ACTIVE_TEXT_COLOR)
    return ImageTk.PhotoImage(idle_image), ImageTk.PhotoImage(active_image)


# PhotoImage 一定要保留 Python 參照，不然會被 GC 回收、按鈕上的圖示會突然消失
# （Tkinter 常見陷阱），build_screen() 建立好之後存在這裡。
recording_icon_idle_image = None
recording_icon_active_image = None
event_icon_idle_image = None
event_icon_active_image = None
photo_icon_idle_image = None
photo_icon_active_image = None

# Event 有沒有結束，不用固定間隔去 GET DvrMode 猜，改成等 KeepAlive 通知
# 「DvrMode 有變化」才去 GET 確認（api.wait_for_dvr_mode_change_notification()）。
# 實機驗證過（2026-08-10）：KeepAlive 通常在 50~250ms 內就會回報變化，比固定
# 輪詢更快也更省。EVENT_NOTIFY_WAIT_SECONDS 是「萬一某次通知遺漏」的安全網，
# 不是真正的輪詢間隔——沒等到通知也會自己檢查一次，不會完全卡住。
EVENT_NOTIFY_WAIT_SECONDS = 5

# 2026-08-20 使用者真機確認規則修正：Event 每次成功 trigger 會讓 DVR 端
# 的 Event 持續時間從那次 trigger 起再延長約 30 秒——只要使用者在結束前
# 持續重新觸發，Event 合法可以持續好幾分鐘，不是固定 45 秒就一定結束。
# 原本 EVENT_POLL_TIMEOUT_SECONDS 是「從第一次 polling worker 啟動時」
# 算一次的固定預算，跟這個協議不一致：重複觸發時 DVR 端持續時間會延長，
# 但監控的 deadline 沒有跟著延長，導致監控可能在 Event 仍然真的 active
# 時就先逾時放棄。改成維護一個會被每次成功 trigger 動態延後的絕對時間
# deadline（_event_monitor_deadline，見 _start_event_worker()），不是
# 固定預算；EVENT_EXPECTED_DURATION_SECONDS/EVENT_TIMEOUT_SAFETY_MARGIN_
# SECONDS 這兩個相加正好等於原本 45 秒的概念（30 秒 Event 本身 + 15 秒
# 容忍誤差），只是現在每次成功 trigger 都會用「這次 trigger 的時間 + 這
# 兩個值」重新算一次 deadline，並取跟舊 deadline 的較大值（只會往後延，
# 不會縮短）。
EVENT_EXPECTED_DURATION_SECONDS = 30   # 真機規則：每次成功 trigger 大約延長這麼久
EVENT_TIMEOUT_SAFETY_MARGIN_SECONDS = 15   # 容忍誤差，沿用原本 45 秒的概念（30+15）

# 2026-08-20 使用者真機回報「Event 結束後 Event 按鈕偶爾仍卡在綠色」的
# 排查用暫時診斷 log：只做記錄，不改任何行為/協議。_event_worker_
# token_counter 純粹拿來標記「這是第幾次點擊 Event 產生的 worker」，方便
# 從 log 分辨是不是同時有兩個 worker 在跑，不會拿來擋任何東西。問題排查
# 穩定後可以移除或改回 False。
EVENT_DIAGNOSTIC_LOGGING = True
_event_worker_token_counter = 0

# 2026-08-20 新增：目前這一輪 Event 監控的動態 deadline（time() 絕對時間
# 戳，不是剩餘秒數）。None 代表目前沒有任何監控在追蹤的 deadline。每次
# _start_event_worker() 確認成功 trigger 都會往後延，_event_polling_
# worker() 的迴圈每次都讀取「當下最新」的這個值，不是進迴圈時就固定住，
# 這樣使用者中途再次觸發 Event 才能讓還在跑的同一支 worker 自動展延，
# 不用重新建立 worker。
_event_monitor_deadline = None

# 2026-08-21 第三次重新設計：上一輪為了修 Codex 抓到的 race 而加的
# _event_confirmed_generation 這裡移除——那個問題的根源是
# current_preview_start_generation 會在「點擊當下」（PUT/confirm 還沒
# 完成）就先遞增。這次改成 generation 只在「confirm 真正成功」之後才
# 遞增（見 current_preview_start_generation 的說明），_event_polling_
# worker() 偵測到「Event 結束」本身就是一次新的、自己的 transition
# confirm，直接在那裡自己遞增、直接讀 current_preview_start_generation
# 就是正確的，不需要再另外追蹤一個獨立變數。


def _event_diag_log(message):
    if EVENT_DIAGNOSTIC_LOGGING:
        print(f'[Event] {message}')


# 2026-08-21 使用者真機回報「按下 Event 後 Preview 圖片肉眼沒有變」的
# 排查用暫時診斷 log：只做記錄，不改任何行為/邏輯。目的是分辨三種可能：
# (A) 新 Event group 找對了，但 DVR 回傳的 thumbnail 內容跟舊圖一樣、
# (B) fetch_ready_group_and_thumbnail() 選錯 item、
# (C) thumbnail bytes 確實是新的，但 Tk Canvas 沒有真的換圖。
# 問題排查穩定後可以移除或改回 False。
PREVIEW_THUMBNAIL_DIAGNOSTIC_LOGGING = True


def _preview_diag_log(message):
    if PREVIEW_THUMBNAIL_DIAGNOSTIC_LOGGING:
        print(f'[Recording][ThumbDiag] {message}')


def _preview_diag_describe_item(item):
    """診斷用：把一筆 Recording item 的關鍵欄位印成字串，item 為 None 回傳 'None'。"""
    if item is None:
        return 'None'
    type_name = audi_pb2.TypeOfRecording.Name(item.type_of_recording)
    trigger_time = item.interval.start_time.ToDatetime()
    return (f'fileid={item.fileid} groupid={item.groupid} sequenceid={item.sequenceid} '
            f'type={type_name} srcUri={item.srcUri!r} trigger_time={trigger_time}')


def _preview_diag_describe_group(label, group):
    """診斷用：印出這個 group 的代表項目，跟正式邏輯一樣用 api.get_group_
    representative() 挑，不是自己另外猜一個，確保診斷看到的跟實際套用的
    是同一筆。"""
    if not group:
        _preview_diag_log(f'{label}: group=None/empty')
        return
    representative = api.get_group_representative(group)
    _preview_diag_log(f'{label}: representative=[{_preview_diag_describe_item(representative)}] group_size={len(group)}')


def _preview_diag_hash_and_length(thumbnail_bytes):
    """診斷用：回傳 (length, sha1_hex)，thumbnail_bytes 為 None 回傳 (None, None)。
    只是拿來比對兩張縮圖內容是不是完全一樣，不是安全用途，用 sha1 就足夠。"""
    if thumbnail_bytes is None:
        return None, None
    return len(thumbnail_bytes), hashlib.sha1(thumbnail_bytes).hexdigest()


def _preview_diag_log_thumbnail(label, thumbnail_bytes):
    length, digest = _preview_diag_hash_and_length(thumbnail_bytes)
    _preview_diag_log(f'{label}: length={length} sha1={digest}')


def _event_after_if_active(delay_ms, func, description):
    """跟既有的 _after_if_active() 一樣的過期判斷（是不是還在 Recording
    分頁），多包一層診斷 log。log 一定要在 after() 真正 fire 的當下才印，
    不能在排入的當下就先猜——is_recording_screen_active 在排入當下跟真正
    fire 時可能不一樣，只有 fire 時的判斷才是真正決定這次 callback 有沒有
    被套用的依據。這是 Event 專用的診斷包裝，不修改共用的
    _after_if_active() 本身（那支函式還被 Restart/Scenic/Photo 共用，不
    應該為了 Event 的排查多印一堆不相關的 log）。"""
    def guarded():
        applied = is_recording_screen_active
        _event_diag_log(f'callback {"applied" if applied else "ignored"} desc={description} '
                         f'is_recording_screen_active={is_recording_screen_active}')
        if applied:
            func()
    preview_canvas.after(delay_ms, guarded)

on_play_video_callback = None

# ---- DVR 真實狀態（一律來自 GET DvrMode，不是靠按鈕自己上次的顏色去猜） ----
current_dvr_state = 'unknown'         # 'standby' / 'scenic_recording' / 'event_recording' / 'unknown'
was_recording_before_event = False    # Event 是從 Scenic 錄影中觸發的，還是從 Standby 直接觸發的
is_event_monitoring = False           # Event 輪詢執行緒是否正在跑，避免重複開兩個

# is_dashcam_off：actual dvr_mode 字面上真的是 STANDBY（代表 Dashcam 關閉，
# 不是「暫停錄影」），這時右側顯示 Dashcam Off 畫面、Recording/Event/Photo
# 三個按鈕都要 disable。restart_button_bbox 記錄 Dashcam Off 畫面上
# Restart 按鈕的範圍 (x1, y1, x2, y2)，讓 on_preview_click() 判斷點擊位置
# 有沒有落在按鈕上，不用額外綁一個新的 canvas click handler。
is_dashcam_off = False
restart_button_bbox = None
restart_busy = False           # Restart 按鈕的恢復流程是否正在跑，避免連點跑出兩個並行的 restore_desired_dvr_mode()

# 2026-08-18 新增：DVR 斷線是跟 STANDBY／Connected+No Video 都不一樣的
# 第三種獨立狀態（見 show_dvr_disconnected_screen()）。is_dvr_disconnected
# 只是給畫面本身參考用（跟 is_dashcam_off 平行，不能共用同一個旗標，
# 語意完全不同：一個是「DVR 開著但字面上是 STANDBY」，一個是「根本連不
# 上 DVR」）。
is_dvr_disconnected = False

# ---- 縮圖 / 播放狀態（只在明確操作事件時更新，跟上面的 DvrMode 輪詢完全分開） ----
current_preview_group = None
current_preview_playable = False
preview_image_ref = None

# 2026-08-21 診斷用（見 PREVIEW_THUMBNAIL_DIAGNOSTIC_LOGGING 附近說明）：
# 記錄目前 Canvas 上實際套用的縮圖原始 bytes，純粹讓診斷 log 可以印出
# 「目前顯示的縮圖」跟「即將套用的新縮圖」的 hash 比對，不參與任何判斷
# 邏輯，也不影響 current_preview_playable/current_preview_group。
_last_applied_thumbnail_bytes = None

# 2026-08-21 第三次重新設計：Preview 更新改成由「DVR state transition」
# 驅動，收斂成單一入口 refresh_preview_for_dvr_state()（Scenic/Event
# Start confirm、Scenic Stop confirm、Event 確認結束、startup/reconnect
# 都呼叫同一支），不再讓 Start worker 自己「等新影片出現」、也不再需要
# Start worker 跟 Stop/Event-finish worker 互相協調「誰該負責 finalize」
# ——因為現在每一次確認的 state transition 都會自己完整做一次
# 「GET recordings → 找對應的最新影片 → 下載縮圖 → 套用」，不會有一半
# 資料由 Start 寫、一半由 Stop 寫的情況，前幾輪的
# _group_applied_generation/_stopped_generation/_preview_coordination_
# lock 這整套協調機制因此完全移除，不是疊加，是整個換掉。
#
# current_preview_start_generation 重新定義：代表「最新一次已確認 DVR
# state transition」的編號，只在下面 4 個真正的 transition confirm 成功
# 之後才遞增（不是在按鈕點擊當下）：Scenic Start confirm、Scenic Stop
# confirm、Event Start confirm、Event 確認結束（含逾時後最後確認發現
# 已結束的分支）。用途縮小成只剩一件事：refresh_preview_for_dvr_state()
# 完成 GET/縮圖下載後，套用前如果發現自己的 generation 已經不是目前最新
# （代表這段時間又有更新的 transition 確認成功），就整個丟棄，不套用—
# 不會影響要不要去 GET/下載縮圖（那些永遠先做，跟 generation 無關）。
#
# startup/reconnect（on_initial_dvr_state_ready() 觸發）不使用這個
# generation 機制（呼叫時傳 generation=None）——它不是由某次特定的
# transition confirm 觸發，套用前改成確認 current_dvr_state ==
# target_state 仍然成立，不成立（代表這段時間發生了一次真正的 transition，
# 那次自己的結果才該套用）就丟棄。
current_preview_start_generation = 0

# 2026-08-24 第九次修正：跟 current_preview_start_generation 綁在同一個
# 唯一遞增點（_bump_preview_generation()）配對記錄「這次 transition 是
# 什麼時候 confirm 成功的」，Stop/Event Complete 的 finalize identity
# 比對要用「這次錄影是什麼時候開始的」當時間下限（見 fetch_finalized_
# group() 的說明），不能用 Stop 開始 finalize 那一刻（可能是錄影開始後
# 數十秒甚至數分鐘），也不能用 stub 當下凍結的 trigger_time（stub 演變
# 成真實資料時 trigger_time 會變）。不是新的狀態機，只是同一個既有遞增點
# 多存一個時間戳記，沒有新的 transition 邏輯。
current_preview_start_generation_confirmed_at = None

# 2026-08-21 Codex review 抓到：current_preview_start_generation += 1
# 是跨執行緒的 read-modify-write，沒有保護。Event 允許重複觸發，理論上
# 可能同時有兩個 worker 各自的 confirm 幾乎同時成功，各自讀到同一個舊值
# 再各自 +1、寫回同一個新值，讓兩個不同的 transition 拿到同一個
# generation，guard 就失去排序能力。用一個很小的 lock 把「讀舊值+寫新值」
# 包成原子操作，只保護這一個遞增動作，不是重新引入上一輪那種雙邊協調的
# lock。
_preview_generation_lock = threading.Lock()


def _bump_preview_generation():
    """原子地把 current_preview_start_generation 遞增並回傳新值，同時記錄
    這次遞增發生的時間到 current_preview_start_generation_confirmed_at
    （見該變數的說明）。"""
    global current_preview_start_generation, current_preview_start_generation_confirmed_at
    with _preview_generation_lock:
        current_preview_start_generation += 1
        current_preview_start_generation_confirmed_at = time()
        return current_preview_start_generation

# ---- 畫面元件 ----
recording_button = None
event_button = None
photo_button = None
status_label = None
preview_canvas = None


def style_recording_page_button(button):
    """左側功能面板按鈕樣式——2026-08-14 對照 HCP3 實機截圖，面板本身是
    淺色卡片，改用 HCP3_LIGHT_PANEL_COLOR/HCP3_LIGHT_PANEL_TEXT_COLOR
    （深色文字），不是原本的深色卡片＋白字。這三個按鈕的底色會隨錄影
    狀態變化（Idle 淺色 / Active 紅色），所以不套用 style_dark_button()
    內建的 hover 換色，避免滑鼠移開時把「目前是 Active」的紅色蓋掉——
    Idle/Active 顏色由 apply_dvr_state_to_buttons()/Photo 按鈕自己的
    邏輯決定，這裡只負責統一的字體/邊框/間距/初始顏色。"""
    button.config(bg=HCP3_LIGHT_PANEL_COLOR, fg=HCP3_LIGHT_PANEL_TEXT_COLOR,
                   activebackground=HCP3_LIGHT_PANEL_COLOR,
                   activeforeground=HCP3_LIGHT_PANEL_TEXT_COLOR,
                   disabledforeground=HCP3_LIGHT_PANEL_DISABLED_TEXT_COLOR,
                   relief='flat', bd=0, highlightthickness=0, padx=26, pady=6,
                   font=('Calibri', 13), width=14, anchor='w', compound='left', cursor='hand2')
    return button


def build_screen(parent, on_play_video):
    """建立 Recording 首頁畫面，on_play_video 是點縮圖播放時要呼叫的函式（傳整個 group 過去）"""
    global on_play_video_callback
    global recording_button, event_button, photo_button, status_label
    global preview_canvas
    global recording_icon_idle_image, recording_icon_active_image
    global event_icon_idle_image, event_icon_active_image
    global photo_icon_idle_image, photo_icon_active_image

    on_play_video_callback = on_play_video

    # PhotoImage 一定要在 Tk root 建立之後才能產生（PIL 的 ImageTk 需要用到
    # 目前的 Tk 環境），所以放在 build_screen() 裡面建立，不是模組頂層。
    recording_icon_idle_image, recording_icon_active_image = build_camera_icon_images()
    event_icon_idle_image, event_icon_active_image = build_event_icon_images()
    photo_icon_idle_image, photo_icon_active_image = build_photo_icon_images()

    frame = tk.Frame(parent, bg=screen_gallery.BACKGROUND_COLOR)

    # ---- 左側：三個操作按鈕（圖示＋文字）+ 狀態訊息 ----
    # 2026-08-14 對照 HCP3 實機截圖：左側功能面板是「淺色圓角卡片」，跟
    # Top Bar／右側背景的深色系是刻意對比。Tkinter 沒有原生圓角 Frame，
    # 做法是先用 Canvas 畫一個圓角矩形當卡片背景（panel_background_canvas），
    # 再把裝著 3 個按鈕的 buttons_frame 用 place() 內縮
    # HCP3_PANEL_INSET 疊在上面，讓圓角在四個角落露出來——buttons_frame
    # 裡面 3 個按鈕的 pack/狀態切換/click handler 完全不變，只是換了一種
    # 背景畫法，不影響任何既有功能邏輯。
    # 2026-08-14 第二次調整（對照真機 Layout 比例）：面板加寬（220→260），
    # 跟右側 Preview 的水平間距縮小（原本左右各自留 10px+10px 共 20px，
    # 改成各自只留 6px 共 12px），兩塊看起來更像同一組面板，不是分開的
    # 兩個獨立元件。
    # 2026-08-19 使用者對照原圖第三次調整：panel_width/padx/pady 都改成
    # 上面模組層級的常數（PANEL_WIDTH/LEFT_NAV_PADX/RIGHT_AREA_PADX/
    # VERTICAL_PADY），跟 compute_recording_content_size() 用同一組數字，
    # 不會有兩處分別維護、之後改一邊忘記改另一邊的風險。
    left_nav = tk.Frame(frame, width=PANEL_WIDTH, bg=screen_gallery.BACKGROUND_COLOR)
    left_nav.pack(side='left', fill='y', padx=LEFT_NAV_PADX, pady=VERTICAL_PADY)
    left_nav.pack_propagate(False)

    # 面板高度固定跟右側 preview_canvas 一樣高（PREVIEW_SIZE[1]），這裡
    # 就是「left_nav 的 top/bottom 對齊實際影像 top/bottom」的實作——
    # PREVIEW_SIZE[1] 現在是用 16:9 直接算出來的「實際影像高度」，不再是
    # 含 letterbox 的整個 Preview Container 高度（2026-08-19 使用者對照
    # 原圖要求）。Standby/Normal 兩種狀態只是按鈕 enable/disable 跟顏色
    # 不同，這個高度完全不變，不會因為切換狀態造成版面跳動。status_label
    # 用 place() 放在面板下方，不佔用面板本身的高度。
    panel_width = PANEL_WIDTH
    panel_height = PREVIEW_SIZE[1]

    panel_background_canvas = tk.Canvas(left_nav, width=panel_width, height=panel_height,
                                         bg=screen_gallery.BACKGROUND_COLOR, highlightthickness=0)
    panel_background_canvas.place(x=0, y=0, width=panel_width, height=panel_height)
    screen_gallery.draw_rounded_rect(panel_background_canvas, 0, 0, panel_width, panel_height,
                                       HCP3_PANEL_CORNER_RADIUS, fill=HCP3_LIGHT_PANEL_COLOR)

    buttons_frame = tk.Frame(left_nav, bg=HCP3_LIGHT_PANEL_COLOR)
    buttons_frame.place(x=HCP3_PANEL_INSET, y=HCP3_PANEL_INSET,
                        width=panel_width - 2 * HCP3_PANEL_INSET,
                        height=panel_height - 2 * HCP3_PANEL_INSET)

    # 剛開啟時還不知道 DVR 真實狀態，先把按鈕都關掉，等 GET DvrMode 回來才開放點擊。
    # 三顆按鈕緊貼在一起（沒有各自的 padx/pady），中間用 1px divider 隔開，整個
    # buttons_frame 看起來像一塊面板，不是三個分開的方塊。
    recording_button = style_recording_page_button(
        tk.Button(buttons_frame, text=' Recording', image=recording_icon_idle_image,
                   state='disabled', command=on_recording_button_click))
    recording_button.pack(fill='both', expand=True)

    tk.Frame(buttons_frame, height=1, bg=HCP3_LIGHT_PANEL_DIVIDER_COLOR).pack(fill='x')

    event_button = style_recording_page_button(
        tk.Button(buttons_frame, text=' Event', image=event_icon_idle_image,
                   state='disabled', command=on_event_button_click))
    event_button.pack(fill='both', expand=True)

    tk.Frame(buttons_frame, height=1, bg=HCP3_LIGHT_PANEL_DIVIDER_COLOR).pack(fill='x')

    photo_button = style_recording_page_button(
        tk.Button(buttons_frame, text=' Photo', image=photo_icon_idle_image,
                   state='disabled', command=on_photo_button_click))
    photo_button.pack(fill='both', expand=True)

    # 2026-08-19 使用者最後一輪要求縮小 Preview 下方留白：status_label
    # 改成掛在 frame（不是 left_nav），寬度改成跟 Panel+Preview 合起來
    # 一樣寬——文字內容/更新時機完全沒變，純粹只是给它更寬的顯示空間，
    # 讓既有最長的訊息也能維持單行（實測 21px），不用再為了容納 2 行
    # 換行預留額外高度。水平位置仍然對齊 Panel 左緣（x=LEFT_NAV_PADX[0]，
    # 跟 left_nav 自己的左邊距一致），視覺上看起來還是「掛在 Panel 下方」。
    content_width, _ = compute_recording_content_size()
    status_label_width = content_width - LEFT_NAV_PADX[0]
    status_label = tk.Label(frame, text='Loading DVR status...', bg=screen_gallery.BACKGROUND_COLOR,
                             fg=screen_gallery.TEXT_SECONDARY_COLOR, wraplength=status_label_width, justify='left')
    status_label.place(x=LEFT_NAV_PADX[0], y=VERTICAL_PADY[0] + panel_height + STATUS_LABEL_TOP_GAP,
                        width=status_label_width)

    # ---- 右側：最新影片縮圖（不顯示標題，維持畫面簡潔） ----
    # 2026-08-14 對照真機 Layout 比例：跟左側面板的間距縮小（原本 10px，
    # 改成 6px，兩邊共 12px），視覺上更像同一組面板。
    right_area = tk.Frame(frame, bg=screen_gallery.BACKGROUND_COLOR)
    right_area.pack(side='left', fill='both', expand=True, padx=RIGHT_AREA_PADX, pady=VERTICAL_PADY)

    # right_area 本身已經有 pady=20 的外距，這裡不用再疊加一次，
    # 讓 preview_canvas 的頂端跟左側 buttons_frame 的頂端對齊。canvas 背景
    # 改用跟外圍一樣的 BACKGROUND_COLOR（不是純黑）——圓角遮罩／Standby
    # 淺色背景都要跟外圍背景無縫接軌（見 create_rounded_background_image()）。
    preview_canvas = tk.Canvas(right_area, width=PREVIEW_SIZE[0], height=PREVIEW_SIZE[1],
                                bg=screen_gallery.BACKGROUND_COLOR, highlightthickness=0, cursor='hand2')
    preview_canvas.pack(anchor='n')
    preview_canvas.bind('<Button-1>', on_preview_click)

    show_loading_state()
    refresh_recording_page()

    return frame


# ============================================================
# DVR 狀態判斷（Standby / Scenic Recording / Event Recording）
# ============================================================

def derive_dvr_state(mode):
    """把 GET DvrMode 的結果，換成畫面要用的狀態字串。
    規則一定要同時看 dvr_mode 跟 event_recording_setting，不能只看
    event_recording_setting != DVREVENT_NO_EVENT 就當作在錄影。

    2026-08-14 使用者確認規則：Settings ON 後 DVR 正常 mode 就是
    WITHOUT_RINGBUFFER，Recording/Event 不再切到 WITH_RINGBUFFER
    （見 _start_scenic_worker()/_start_event_worker()），所以錄影中的
    raw dvr_mode 現在應該是 WITHOUT_RINGBUFFER，不是 WITH_RINGBUFFER，
    這裡要同時接受兩種 dvr_mode——WITH_RINGBUFFER 保留只是防呆
    （避免 DVR 真的處於這個狀態時被誤判成 standby），正常操作流程不會
    再產生這個值。

    註：dvr_mode 是 RECORD_WITH/WITHOUT_RINGBUFFER 不代表「現在真的在
    寫入新檔案」，實測過 RECORD_WITH_RINGBUFFER + DVREVENT_NO_EVENT 這個
    組合，/dashcam/recordings 最新一筆的 state 是 RECORDING_STATE_FINISHED
    （不是 ONGOING），而且清單內容過了 5 秒也完全沒變化，代表其實沒有真的
    在錄影，所以只有明確是 DVREVENT_SCENIC / DVREVENT_USER_TRIGGERED
    才算「在錄影」，其他事件類型（包含 NO_EVENT）一律當 standby 顯示。"""
    DvrSystemState = audi_pb2.DvrMode.DvrSystemState
    DvrEvent = audi_pb2.DvrMode.DvrEvent

    recording_capable_modes = (DvrSystemState.DVR_SYSTEM_STATE_RECORD_WITH_RINGBUFFER,
                                DvrSystemState.DVR_SYSTEM_STATE_RECORD_WITHOUT_RINGBUFFER)
    if mode.dvr_mode in recording_capable_modes:
        if mode.event_recording_setting == DvrEvent.DVREVENT_USER_TRIGGERED:
            return 'event_recording'
        elif mode.event_recording_setting == DvrEvent.DVREVENT_SCENIC:
            return 'scenic_recording'
        else:
            return 'standby'
    else:
        return 'standby'


HCP3_IDLE_FONT = ('Calibri', 13)  # 2026-08-25 使用者要求視覺微調：11→13（+18%），跟 icon 放大比例接近
HCP3_ACTIVE_FONT = ('Calibri', 13, 'bold')  # 2026-08-14 第二次調整：Active 文字加粗，提高辨識度


def apply_dvr_state_to_buttons(state):
    """依真實 DVR 狀態更新 current_dvr_state 跟 Recording/Event 按鈕樣式。

    2026-08-14 對照真機「錄影中」截圖（參考圖3.png）修正：Active 不是
    整格變紅色背景（原本的做法），真機是這一格底色變成近白色
    （HCP3_ACTIVE_ROW_COLOR），圖示／文字變成粉紅紅色
    （HCP3_ACTIVE_TEXT_COLOR），Idle 那一格維持原本的淺色面板底色＋深色
    圖示／文字不變。Photo 按鈕不受這個函式影響，另外處理（Photo 不需要
    持續 Active 狀態）。文字字重也跟著切換：Active 用粗體，Idle 用一般
    字重，不會讓 Idle 狀態意外殘留粗體。"""
    global current_dvr_state
    current_dvr_state = state

    if state == 'scenic_recording':
        recording_button.config(bg=HCP3_ACTIVE_ROW_COLOR, fg=HCP3_ACTIVE_TEXT_COLOR,
                                 activebackground=HCP3_ACTIVE_ROW_COLOR, activeforeground=HCP3_ACTIVE_TEXT_COLOR,
                                 image=recording_icon_active_image, font=HCP3_ACTIVE_FONT)
        event_button.config(bg=HCP3_LIGHT_PANEL_COLOR, fg=HCP3_LIGHT_PANEL_TEXT_COLOR,
                             activebackground=HCP3_LIGHT_PANEL_COLOR, activeforeground=HCP3_LIGHT_PANEL_TEXT_COLOR,
                             image=event_icon_idle_image, font=HCP3_IDLE_FONT)
    elif state == 'event_recording':
        recording_button.config(bg=HCP3_LIGHT_PANEL_COLOR, fg=HCP3_LIGHT_PANEL_TEXT_COLOR,
                                 activebackground=HCP3_LIGHT_PANEL_COLOR, activeforeground=HCP3_LIGHT_PANEL_TEXT_COLOR,
                                 image=recording_icon_idle_image, font=HCP3_IDLE_FONT)
        event_button.config(bg=HCP3_ACTIVE_ROW_COLOR, fg=HCP3_ACTIVE_TEXT_COLOR,
                             activebackground=HCP3_ACTIVE_ROW_COLOR, activeforeground=HCP3_ACTIVE_TEXT_COLOR,
                             image=event_icon_active_image, font=HCP3_ACTIVE_FONT)
    else:  # standby / unknown
        recording_button.config(bg=HCP3_LIGHT_PANEL_COLOR, fg=HCP3_LIGHT_PANEL_TEXT_COLOR,
                                 activebackground=HCP3_LIGHT_PANEL_COLOR, activeforeground=HCP3_LIGHT_PANEL_TEXT_COLOR,
                                 image=recording_icon_idle_image, font=HCP3_IDLE_FONT)
        event_button.config(bg=HCP3_LIGHT_PANEL_COLOR, fg=HCP3_LIGHT_PANEL_TEXT_COLOR,
                             activebackground=HCP3_LIGHT_PANEL_COLOR, activeforeground=HCP3_LIGHT_PANEL_TEXT_COLOR,
                             image=event_icon_idle_image, font=HCP3_IDLE_FONT)

    # 2026-08-14 使用者修正：Play Button 能不能顯示/點擊，只要 current_dvr_state
    # 一變化就要立刻重新判斷，不能只在换缩图/確認完成那幾個時間點才更新，
    # 不然「Event 結束後 DVR 自動接回 Scenic」這種「縮圖沒換、但錄影狀態變了」
    # 的情況會漏掉。
    refresh_play_button_visibility()


CONFIRM_STATE_TIMEOUT_SECONDS = 6   # 實測過 Event 觸發有時要等到接近 3 秒才會反映新狀態，這裡留寬一點
CONFIRM_STATE_POLL_INTERVAL_SECONDS = 0.3


def confirm_dvr_state(expected_state):
    """送出控制指令後，確認 DVR 是不是真的變成 expected_state。
    實測過送出 PUT 後馬上 GET 一次，DVR 常常還沒套用完成（看到的還是舊狀態），
    需要等到最多 1~2 秒才會反映新狀態，所以這裡不是只檢查一次就放棄，
    是在 CONFIRM_STATE_TIMEOUT_SECONDS 秒內每 CONFIRM_STATE_POLL_INTERVAL_SECONDS
    秒重新 GET 一次，一看到符合 expected_state 就馬上回傳；
    等到逾時都不符合，回傳最後一次看到的狀態（可能是 None，代表 GET 本身失敗），
    呼叫端可以用這個資訊顯示正確的錯誤訊息。"""
    start_time = time()
    last_state = None
    while True:
        try:
            mode = api.get_dvr_mode()
            last_state = derive_dvr_state(mode)
            # 2026-08-14 暫時診斷 Log（只在確認 Event trigger 時印出，用完
            # 這次調查後要移除）：使用者懷疑錯誤訊息裡的 "standby" 可能不是
            # 字面上真的 DVR_SYSTEM_STATE_STANDBY，而是 derive_dvr_state()
            # 把 WITHOUT_RINGBUFFER 也歸類成 'standby' 造成的誤導，所以這裡
            # 印出 raw dvr_mode / raw event_recording_setting，不要只印
            # derive 過的字串。
            if expected_state == 'event_recording':
                raw_dvr_mode_name = audi_pb2.DvrMode.DvrSystemState.Name(mode.dvr_mode)
                raw_event_name = audi_pb2.DvrMode.DvrEvent.Name(mode.event_recording_setting)
                desired_name = audi_pb2.DvrMode.DvrSystemState.Name(api.get_desired_dvr_mode())
                print(f'[Event trigger 診斷] raw dvr_mode={raw_dvr_mode_name}, '
                      f'raw event_recording_setting={raw_event_name}, '
                      f'derived state={last_state}, desired_dvr_mode={desired_name}')
        except Exception as error:
            print(f'[Recording] 確認 DVR 狀態失敗: {error}')
            last_state = None

        if last_state == expected_state:
            return last_state
        if time() - start_time >= CONFIRM_STATE_TIMEOUT_SECONDS:
            return last_state
        sleep(CONFIRM_STATE_POLL_INTERVAL_SECONDS)


def describe_confirm_failure(confirmed_state):
    if confirmed_state is None:
        return 'Failed to retrieve DVR status for confirmation.'
    return f'Actual state is "{confirmed_state}", which does not match the expected state.'


def is_standby_mode(mode):
    """直接檢查 dvr_mode 是不是字面上的 DVR_SYSTEM_STATE_STANDBY，不透過
    derive_dvr_state()。derive_dvr_state() 是為了 Recording 頁面的
    Scenic/Event 按鈕顯示設計的，只分 Standby/Scenic Recording/
    Event Recording 三種，其他原始狀態（例如
    DVR_SYSTEM_STATE_RECORD_WITHOUT_RINGBUFFER、DVR_SYSTEM_STATE_PLAY、
    DVR_SYSTEM_STATE_PARK、DVR_SYSTEM_STATE_SHUTDOWN...）都會被歸類成
    'standby' 顯示——這樣分類拿來做 Recording 頁面顯示沒問題，但不能拿來
    判斷「DVR 現在是不是真的處於 Standby，可以安全播放影片」。Gallery
    播放前的檢查要的是字面上真正的 STANDBY，所以這裡直接看 dvr_mode
    這個欄位本身，不透過那三分類。"""
    DvrSystemState = audi_pb2.DvrMode.DvrSystemState
    return mode.dvr_mode == DvrSystemState.DVR_SYSTEM_STATE_STANDBY


def confirm_standby_mode():
    """送出 stop_recording() 之後，確認 DVR 是不是真的切到字面上的 STANDBY。
    跟 confirm_dvr_state() 用同一種「逾時內重複 GET」策略，但檢查條件用
    is_standby_mode()，不是 derive_dvr_state() 那三種分類（理由見
    is_standby_mode() 的說明）。回傳 True 表示確認成功；False 表示逾時
    都沒等到（可能是 GET 本身持續失敗，也可能是 DVR 真的沒切過去）。"""
    start_time = time()
    while True:
        try:
            mode = api.get_dvr_mode()
            if is_standby_mode(mode):
                return True
        except Exception as error:
            print(f'[Recording] 確認 DVR 是否為 Standby 失敗: {error}')

        if time() - start_time >= CONFIRM_STATE_TIMEOUT_SECONDS:
            return False
        sleep(CONFIRM_STATE_POLL_INTERVAL_SECONDS)


# ============================================================
# DVR System Restart 後的 WITHOUT_RINGBUFFER Idle Baseline 恢復
# ============================================================
# 2026-08-20 使用者真機確認規則：DVR 只要發生 System Restart/Reboot，即使
# PC Tool 沒有重開，actual mode 也可能自行回到 WITH_RINGBUFFER + NO_EVENT
# ——如果這時候直接送 WITHOUT_RINGBUFFER + SCENIC/USER_TRIGGERED，DVR 只會
# 先完成 WITH -> WITHOUT 這個轉換（event_recording_setting 被重置回
# NO_EVENT），要使用者再按第二次才會真的變成 SCENIC/USER_TRIGGERED。
#
# ensure_without_ringbuffer_idle_baseline() 是唯一的共用實作，Startup
# Sync、DVR Reconnect、KeepAlive 通知 DvrMode 有變化、Recording/Event
# Click 前的安全檢查都重用這一個，不要各自寫一套同步邏輯。內部完全沿用
# 既有的 api.apply_and_set_desired_dvr_mode()（PUT -> GET 確認 -> 成功才
# 更新 desired）跟它本來就會呼叫的 api.ensure_safe_ringbuffer_transition()
# （WITH/WITHOUT 不能直接互切，會先過渡到 STANDBY），不重寫第二套
# protocol。
dvr_baseline_sync_in_progress = False   # 對外可觀察的旗標，跟下面的 lock 狀態一致，方便其他地方或測試檢查「現在是不是正在 recovery」
_dvr_baseline_recovery_lock = threading.Lock()   # 用 try-lock（acquire(blocking=False)）避免兩個呼叫端同時偵測到需要 recovery、各自送出一次 PUT 互相打架

# 2026-08-20 使用者發現：舊版用 (needed_recovery, success) 兩個 boolean，
# 拿不到 lock（已經有另一個 thread 在做 recovery）時回傳 (False, True)，
# 跟「本來就不需要 recovery」完全無法區分。Recording/Event Click 安全檢查
# 因此會把「別人正在做 recovery」誤判成「baseline 已經沒事」，直接送出
# SCENIC/USER_TRIGGERED，跟正在進行中的 recovery PUT 互相競爭。改成明確
# 的字串狀態，讓每個呼叫端都能分辨這五種狀況、各自決定要怎麼處理。
BASELINE_RECOVERY_OK = 'baseline_ok'                    # 不需要 recovery，actual 本來就已經是 WITHOUT_RINGBUFFER（任何 event_recording_setting）
BASELINE_RECOVERY_RECOVERED = 'recovered'               # 這次呼叫自己偵測到需要 recovery，而且自己完成並確認成功
BASELINE_RECOVERY_IN_PROGRESS = 'recovery_in_progress'  # 拿不到 lock，這個當下有另一個 thread 正在做 recovery，呼叫端要送真正的操作前必須自己等它結束、重新確認
BASELINE_RECOVERY_FAILED = 'recovery_failed'            # 這次呼叫自己偵測到需要 recovery，但 PUT/確認失敗
BASELINE_RECOVERY_STANDBY = 'standby'                   # actual 字面上是 STANDBY，維持既有規則不處理


def ensure_without_ringbuffer_idle_baseline(known_mode=None):
    """確保「desired 是 WITHOUT_RINGBUFFER」時，actual 沒有意外停留在
    WITH_RINGBUFFER 這個 DVR 自己 Restart/Reboot 後可能回到的預設值。
    只負責把 actual 恢復到 WITHOUT_RINGBUFFER + NO_EVENT 這個 idle
    baseline，不負責呼叫端接下來真正要做的 SCENIC/USER_TRIGGERED，那是
    呼叫端自己接著做的下一步。

    known_mode（可選）：呼叫端如果剛好已經 GET 過一次 DvrMode（例如
    Recording/Event Click 前的安全檢查、Startup Sync 本來就會 GET 一次），
    直接把結果傳進來，這裡就不會再多打一次 GET；不傳的話這裡自己 GET
    一次。

    刻意不處理、不會誤觸發的情況（使用者明確要求，不能誤傷正常狀態）：
    - desired 不是 WITHOUT_RINGBUFFER（Settings OFF，desired=STANDBY）
      ——這個 baseline 概念只在 Settings ON 時才有意義。
    - actual 是字面上的 STANDBY——維持既有規則，不能因為 baseline
      recovery 又自動打開 Dashcam。
    - actual 已經是 WITHOUT_RINGBUFFER（不論 event_recording_setting 是
      NO_EVENT、SCENIC 還是 USER_TRIGGERED）——這些都是正常狀態，SCENIC/
      USER_TRIGGERED 尤其不能被這裡誤重置成 NO_EVENT。
    只有「desired=WITHOUT_RINGBUFFER 且 actual 真的是 WITH_RINGBUFFER」
    這個明確的不預期組合才會觸發 recovery。

    回傳上面五個 BASELINE_RECOVERY_* 狀態字串其中一個，不再用兩個
    boolean（2026-08-20 使用者發現舊版 (False, True) 同時代表「不需要」
    跟「已經有別人在做」，Action safety check 沒辦法分辨，見上面的說明）。
    這裡的 try-lock 只負責讓「兩個呼叫端同時偵測到需要 recovery」時只有
    一個真的送 PUT，不負責讓拿不到 lock 的那個呼叫端等待——要不要等、
    等多久，交給各自呼叫端依照自己的語意決定（Startup/Reconnect/Runtime
    通知選擇不等、直接讓路；Action safety check 選擇要等，見
    _run_baseline_safety_check_before_action()）。"""
    global dvr_baseline_sync_in_progress
    DvrSystemState = audi_pb2.DvrMode.DvrSystemState

    if not _dvr_baseline_recovery_lock.acquire(blocking=False):
        print('[Recording] ensure_without_ringbuffer_idle_baseline()：已經有一個 recovery 在進行中，這次呼叫直接讓路')
        return BASELINE_RECOVERY_IN_PROGRESS

    try:
        if api.get_desired_dvr_mode() != DvrSystemState.DVR_SYSTEM_STATE_RECORD_WITHOUT_RINGBUFFER:
            return BASELINE_RECOVERY_OK  # Settings OFF，不歸這支函式管

        if known_mode is not None:
            mode = known_mode
        else:
            try:
                mode = api.get_dvr_mode()
            except Exception as error:
                print(f'[Recording] ensure_without_ringbuffer_idle_baseline() GET DvrMode 失敗: {error}，'
                      f'沒有依據判斷要不要 recovery，交給呼叫端接下來自己的動作/例外處理')
                return BASELINE_RECOVERY_OK

        if is_standby_mode(mode):
            return BASELINE_RECOVERY_STANDBY  # 字面上真的是 STANDBY，維持既有規則不處理

        if mode.dvr_mode != DvrSystemState.DVR_SYSTEM_STATE_RECORD_WITH_RINGBUFFER:
            return BASELINE_RECOVERY_OK  # 已經是 WITHOUT_RINGBUFFER（不論 event_recording_setting 是什麼），正常狀態，不能誤觸發

        print('[Recording] 偵測到 desired=WITHOUT_RINGBUFFER 但 actual=WITH_RINGBUFFER'
              '（DVR 可能剛 Restart/Reboot），開始 baseline recovery -> WITHOUT_RINGBUFFER + NO_EVENT')
        dvr_baseline_sync_in_progress = True
        _set_recording_buttons_busy_for_baseline_recovery(True)
        try:
            success = api.apply_and_set_desired_dvr_mode(DvrSystemState.DVR_SYSTEM_STATE_RECORD_WITHOUT_RINGBUFFER)
        finally:
            dvr_baseline_sync_in_progress = False
            _set_recording_buttons_busy_for_baseline_recovery(False)

        if success:
            print('[Recording] baseline recovery 成功，actual 已確認回到 WITHOUT_RINGBUFFER + NO_EVENT')
            return BASELINE_RECOVERY_RECOVERED
        else:
            print('[Recording] baseline recovery 失敗（PUT 或 GET 確認沒有成功）')
            return BASELINE_RECOVERY_FAILED
    finally:
        _dvr_baseline_recovery_lock.release()


def _set_recording_buttons_busy_for_baseline_recovery(is_busy):
    """Recovery 進行中鎖住 Recording/Event/Photo，避免使用者的操作跟
    Recovery 本身的 PUT 搶狀態（真機會出現「Recovery 還在進行、使用者
    同時點 Event，兩個 PUT 搶狀態」的風險）。這裡可能從背景執行緒呼叫，
    用 preview_canvas.after(0, ...) 排到主執行緒才動 widget。

    Recovery 結束後的最終正確按鈕狀態一律交給呼叫端接下來要做的事決定
    （Startup/Reconnect 接著呼叫 refresh_recording_page() 用最新真實狀態
    重新判斷；Recording/Event Click 安全檢查接著送真正的目標指令，該
    worker 自己會再管一次按鈕狀態）——這裡的 is_busy=False 只是先把
    Recovery 造成的暫時 disabled 解除，不代表最終狀態一定是 normal，
    很快就會被呼叫端接下來的動作覆蓋掉，不會讓使用者看到不一致的空檔。"""
    def apply():
        state = 'disabled' if is_busy else 'normal'
        recording_button.config(state=state)
        event_button.config(state=state)
        photo_button.config(state=state)
        if is_busy:
            status_label.config(text='Recovering DVR baseline...')
    preview_canvas.after(0, apply)


# ============================================================
# 每次進入 Recording 頁面：GET 一次 DvrMode + 一次 recordings，重新整理畫面
# ============================================================
# 不能只在 build_screen() 第一次執行時做這件事——DA005ControlPanel.pyw 的
# go_to_recording_screen() 每次切到 Recording Tab 都會呼叫
# refresh_recording_page()，確保畫面永遠反映「現在真實的 DVR 狀態」，不會
# 沿用上次使用者按了什麼按鈕的舊 UI state（例如在別的畫面把 Settings 關掉、
# 或用 Player/Format 暫時切了 Standby，回到 Recording 頁面時要正確反映）。

# refresh_recording_page() 可能被重複呼叫（使用者快速切換分頁、Restart
# 成功後又觸發一次、Stop Scenic 也可能觸發），每次呼叫都 +1 記一個 token，
# 背景執行緒完成時只有 token 還是目前最新的那一次才真的套用到畫面上，避免
# 舊的背景結果晚到、蓋掉新的畫面狀態（Codex review 2026-08-13 發現的
# race condition）。
current_recording_refresh_token = 0


# ============================================================
# App 首次啟動的 idle baseline 同步（2026-08-19 新增）
# ============================================================
# 使用者回報：第一次啟動工具後，DVR actual state 可能還停在 WITH_RINGBUFFER
# + NO_EVENT（例如上一次關閉工具前是這個狀態，或韌體本身開機預設值），這時
# 使用者第一次按 Recording，會同時發生「WITH_RINGBUFFER -> WITHOUT_RINGBUFFER」
# 這個 mode transition，firmware 可能順便把 Event/Scenic 欄位重置，造成第一次
# 操作的行為跟後續不一致。這裡在 App 啟動、Recording 畫面剛建立好時呼叫一次，
# 先確保 actual 是 WITHOUT_RINGBUFFER + NO_EVENT 這個 idle baseline，再讓
# 使用者操作 Recording/Event/Photo。
def start_dvr_startup_sync():
    """App 啟動時呼叫一次（由 DA005ControlPanel.pyw 在 build_screen() 之後
    呼叫，整個工具生命週期只會呼叫這一次）。同步完成前 Recording/Event/
    Photo 一律先 disable，避免使用者在同步跑完之前就點到、跟同步本身的
    PUT 打架；同步完成後（不論結果如何）統一交給既有的
    refresh_recording_page() 重新整理整個畫面，它自己會依真實狀態決定要不要
    re-enable 這幾個按鈕，這裡不用另外處理。"""
    recording_button.config(state='disabled')
    event_button.config(state='disabled')
    photo_button.config(state='disabled')
    status_label.config(text='Synchronizing dashcam state...')
    thread = threading.Thread(target=_startup_sync_worker, daemon=True)
    thread.start()


def _startup_sync_worker():
    """背景執行緒：讀一次目前的 actual DvrMode，判斷要不要同步。

    2026-08-20 使用者要求改用共用的 ensure_without_ringbuffer_idle_
    baseline()，不要自己另外判斷一套——原本這裡的 if/elif/else 對「已經
    是 WITHOUT_RINGBUFFER 但 event_recording_setting 不是 NO_EVENT」
    （例如 Startup 當下 DVR 剛好已經在 SCENIC/USER_TRIGGERED 中，工具
    可能是中途重開的）跟「真的是 WITH_RINGBUFFER」一視同仁，都會呼叫
    apply_and_set_desired_dvr_mode(WITHOUT_RINGBUFFER)，這會誤送一次
    WITHOUT_RINGBUFFER+NO_EVENT，錯誤打斷已經在進行中的 SCENIC/
    USER_TRIGGERED。共用函式只有真的偵測到 actual=WITH_RINGBUFFER（DVR
    可能剛 Restart/Reboot）才會動作，已經是 WITHOUT_RINGBUFFER（不論
    event_recording_setting 是什麼）都當作正常，不會誤觸發；字面上真的
    是 STANDBY 一樣維持既有 Settings OFF 規則，不擅自打開。

    不論需不需要 recovery，最後都呼叫既有的 refresh_recording_page()，
    讓畫面走一次「正常流程」重新整理、正確 re-enable 按鈕。

    2026-08-20 使用者要求：App 啟動成功連線後要主動 GET Memory 一次，
    判斷 SD Card 是否存在——跟這裡的 DVR Mode 同步協調（同一個背景執行緒、
    同一個時間點），但不重寫下面 DVR Mode 這段既有的初始化邏輯，只是
    在最前面單獨加一段獨立的檢查。失敗的話維持 api.py 裡樂觀預設的快取
    值，交給之後 KeepAlive 通知時再更新，不會擋掉後面 DVR Mode 的同步
    流程。"""
    try:
        api.refresh_sd_card_presence()
    except Exception as error:
        print(f'[Recording] Startup sync 讀取 SD Card 狀態失敗，SD Card presence 維持原本快取值: {error}')
    else:
        # 同步一次 _last_known_sd_card_present 這個「上一次已知狀態」
        # 基準值——不然如果 Startup 當下 SD 卡就已經是缺卡狀態，這個模組
        # 層級變數仍然停在預設值 True，等到第一次 KeepAlive 通知 Memory
        # 有變化時，_apply_sd_card_presence_change() 會誤判成「這是一次
        # 新的拔卡事件」而重複觸發 on_sd_card_removed_global_callback()
        # （重複觸發本身無害/冪等，但語意上不精確，這裡順手修正）。在主
        # 執行緒（Tk after 排入）賦值，維持這個變數只有主執行緒會寫入的
        # 既有慣例，不從這支背景執行緒直接寫。
        preview_canvas.after(0, _sync_last_known_sd_card_present_after_startup)

    try:
        mode = api.get_dvr_mode()
    except Exception as error:
        print(f'[Recording] Startup sync 讀取 DVR 狀態失敗，略過同步，直接進行一般 refresh: {error}')
        preview_canvas.after(0, refresh_recording_page)
        return

    # 2026-08-19 使用者要求：這裡確認 GET DvrMode 成功，代表 App 啟動後第一次
    # 真的連上 DVR（這個新的 Connected Session），自動送一次 PC Current Time。
    # 失敗只印 log，不擋下面既有的 baseline recovery 流程。
    api.sync_current_time_once('startup connection')

    result = ensure_without_ringbuffer_idle_baseline(known_mode=mode)
    if result == BASELINE_RECOVERY_RECOVERED:
        print('[Recording] Startup sync：偵測到 actual=WITH_RINGBUFFER，已成功恢復 WITHOUT_RINGBUFFER + NO_EVENT baseline')
    elif result == BASELINE_RECOVERY_FAILED:
        print('[Recording] Startup sync：baseline recovery 未能成功，仍會繼續進行一般 refresh（維持既有錯誤處理，不阻擋使用者）')
    elif result == BASELINE_RECOVERY_IN_PROGRESS:
        # 2026-08-20 使用者確認：Startup 本身不用因為撞上別的 recovery 而
        # 排隊等待——理論上開機階段幾乎不會真的跟其他 recovery 撞上，就算
        # 撞上，讓路後照常 refresh，refresh_recording_page() 自己會用最新
        # 真實狀態重新整理，不會因此顯示錯誤資訊。
        print('[Recording] Startup sync：偵測到已經有另一個 recovery 在進行中，這次直接讓路，仍會繼續進行一般 refresh')
    else:
        print(f'[Recording] Startup sync：不需要 baseline recovery '
              f'(actual dvr_mode={audi_pb2.DvrMode.DvrSystemState.Name(mode.dvr_mode)}, '
              f'event_recording_setting={audi_pb2.DvrMode.DvrEvent.Name(mode.event_recording_setting)})')

    preview_canvas.after(0, refresh_recording_page)


def refresh_recording_page():
    global current_recording_refresh_token
    current_recording_refresh_token += 1
    refresh_token = current_recording_refresh_token

    recording_button.config(state='disabled')
    event_button.config(state='disabled')
    photo_button.config(state='disabled')
    thread = threading.Thread(target=_refresh_recording_page_worker, args=(refresh_token,), daemon=True)
    thread.start()
    start_connection_monitor_loop()  # 標記 Recording 分頁使用中，給 _after_if_active() 判斷用（連線監測本身是全域的，見 start_global_connection_watcher()）


def check_recording_preview_freshness():
    """使用者切回 Recording 分頁時呼叫（DA005ControlPanel.pyw 的
    show_screen() 唯一呼叫點）。2026-08-21 新增，修「換 SD 卡（或 DVR 沒有
    明確回報 SD 卡狀態變化）之後，首頁 Preview 一直停留在舊縮圖」的問題：
    根因不是 thumbnail 有快取，是「沒有東西告訴系統該重新整理」——SD 卡
    狀態偵測完全依賴 KeepAlive 的 RESOURCE_MEMORY 通知，快速換卡時 DVR
    韌體很可能完全不會回報這個轉變，導致 refresh_recording_page() 永遠
    沒機會被呼叫。

    這裡不是新增高頻背景輪詢，只在「使用者確實切回來看這個畫面」這個
    明確事件發生時，做一次輕量比對：目前 Preview 顯示的影片是不是還是
    /dashcam/recordings 目前真正的最新影片，不一致才呼叫既有
    refresh_recording_page() 做完整刷新——跟現有『縮圖只在明確操作事件
    才更新』的設計原則一致（『使用者切回來看』就是一個明確事件），不會
    讓背景輪詢自己換縮圖。"""
    thread = threading.Thread(target=_check_recording_preview_freshness_worker, daemon=True)
    thread.start()


def _check_recording_preview_freshness_worker():
    # 2026-08-21 Codex review 抓到：這支函式會先讀一次 current_preview_group
    # 判斷要不要提早 return，中間夾了一次會花時間的網路呼叫
    # （api.get_recordings()），如果不先把值存成區域變數，後面再讀一次
    # current_preview_group 時，主執行緒可能已經把它換成 None（例如這段
    # 空檔內 SD 卡被判定移除、切去 No SD Card 畫面）——這裡固定用同一份
    # 快照，不要重讀全域變數兩次。
    preview_group_snapshot = current_preview_group
    if preview_group_snapshot is None:
        # 目前沒有顯示任何影片縮圖（例如 Standby/No SD Card/No Video/
        # Disconnected 畫面本身），沒有「跟目前最新影片比對」的基準，這些
        # 畫面本來就有自己既有的狀態偵測，這裡不用多做事。
        return
    try:
        recordings = api.get_recordings()
    except Exception as error:
        print(f'[Recording Freshness Check] GET recordings 失敗，略過這次檢查: {error}')
        return

    latest_item = api.get_latest_video_item_by_trigger_time(recordings)
    if latest_item is None:
        return

    current_representative_item = api.get_group_representative(preview_group_snapshot)
    current_trigger_time = current_representative_item.interval.start_time.ToDatetime()
    latest_trigger_time = latest_item.interval.start_time.ToDatetime()

    if latest_trigger_time > current_trigger_time:
        print(f'[Recording Freshness Check] 偵測到 Preview 已過期 '
              f'(顯示中 trigger_time={current_trigger_time}, 目前最新 trigger_time={latest_trigger_time})，'
              f'重新整理 Recording 頁面')
        preview_canvas.after(0, refresh_recording_page)


def _apply_if_current_refresh(refresh_token, func, *args):
    """只有 refresh_token 還是目前最新的那一次，才真的呼叫 func 套用到
    畫面上；過期的結果直接丟棄，不做任何事（包括不彈錯誤訊息、不改按鈕
    狀態）。"""
    if refresh_token == current_recording_refresh_token:
        func(*args)


def recover_baseline_then_refresh_recording_page():
    """DVR Disconnect -> Reconnect 時呼叫（取代直接呼叫
    refresh_recording_page()，由 DA005ControlPanel.pyw 的
    on_dvr_reconnected_globally() 呼叫）。

    2026-08-20 使用者要求：Reconnect 當下不能直接恢復 Recording 按鈕——
    因為 reboot 通常會伴隨 KeepAlive 暫時中斷再恢復，斷線期間 DVR 很可能
    剛好 Restart/Reboot 回到 WITH_RINGBUFFER。這裡先在背景執行緒 GET 一次
    DvrMode，透過共用的 ensure_without_ringbuffer_idle_baseline() 判斷
    要不要做 baseline recovery；不論結果是「本來就不需要」、「需要且成功」
    還是「需要但失敗」，都統一交給既有的 refresh_recording_page() 用最新
    真實狀態重新整理畫面、re-enable 按鈕（跟 Startup Sync 收尾方式一致，
    不另外自己管按鈕狀態）。"""
    thread = threading.Thread(target=_recover_baseline_then_refresh_worker, daemon=True)
    thread.start()


def _recover_baseline_then_refresh_worker():
    try:
        mode = api.get_dvr_mode()
    except Exception as error:
        print(f'[Recording] Reconnect baseline check 讀取 DVR 狀態失敗，略過檢查，直接進行一般 refresh: {error}')
        preview_canvas.after(0, refresh_recording_page)
        return

    # 2026-08-19 使用者要求：這裡確認 GET DvrMode 成功，代表這次真的是
    # Reconnect（斷線後重新連上，這個新的 Connected Session），自動送一次
    # PC Current Time。_connection_check_tick() 是 edge-triggered（只在
    # disconnected -> connected 這個轉換瞬間才呼叫到這支 worker，見該函式
    # 註解），所以這裡天生就是「每個新 Session 只呼叫一次」，不用另外
    # 加旗標防重複。失敗只印 log，不擋下面既有的 baseline recovery 流程。
    api.sync_current_time_once('reconnect')

    result = ensure_without_ringbuffer_idle_baseline(known_mode=mode)
    if result == BASELINE_RECOVERY_RECOVERED:
        print('[Recording] Reconnect：偵測到 actual=WITH_RINGBUFFER，已成功恢復 WITHOUT_RINGBUFFER + NO_EVENT baseline')
    elif result == BASELINE_RECOVERY_FAILED:
        print('[Recording] Reconnect：baseline recovery 未能成功，仍會繼續進行一般 refresh（維持既有錯誤處理，不阻擋使用者）')
    elif result == BASELINE_RECOVERY_IN_PROGRESS:
        # Reconnect 本身不用排隊等待別的 recovery——讓路後照常 refresh，
        # refresh_recording_page() 會用最新真實狀態重新整理，不會顯示
        # 過期資訊；真正持有 lock 的那個呼叫端結束時自己會再 refresh 一次。
        print('[Recording] Reconnect：偵測到已經有另一個 recovery 在進行中，這次直接讓路，仍會繼續進行一般 refresh')

    preview_canvas.after(0, refresh_recording_page)


# ============================================================
# DVR 連線狀態監測（2026-08-18 新增，2026-08-19 改成全域監測）
# ============================================================
# 目前 Tool 唯一「知道 DVR 連線狀態」的來源是 api.py 的 KeepAlive 背景
# 執行緒（本來就每 ~100ms 送一次），這裡只是週期性讀取
# api.is_dvr_connected() 這個記憶體旗標，不會另外發出任何 HTTP 請求，
# 不是新的高頻率 GET polling。
#
# 2026-08-19 使用者要求：斷線要不論目前在哪個畫面都能偵測到並自動回
# Recording 首頁，不能只有「Recording 分頁使用中才跑」——原本這個檢查
# 迴圈跟 is_recording_screen_active 綁在一起，離開 Recording 就停止。
# 現在拆成兩件事：
#   1. is_recording_screen_active 繼續只給 _after_if_active() 這類
#      「Recording 分頁自己的背景 worker 回呼要不要生效」判斷用。
#   2. 連線檢查迴圈本身改成 start_global_connection_watcher()，App 啟動時
#      呼叫一次、全程運作（跟 api.start_keepalive() 一樣是整個工具生命週期
#      都在跑的東西），不會因為離開 Recording 分頁就停止；偵測到狀態變化
#      時透過 on_dvr_disconnected_global_callback/on_dvr_reconnected_global_
#      callback 這兩個掛勾（由 DA005ControlPanel.pyw 設定）通知，讓它決定
#      「目前在哪個畫面、該怎麼安全退出」，Recording 自己不用知道別的畫面
#      要做什麼——這樣才不會變成 Recording/Gallery/Player 各自 poll 一份。
is_recording_screen_active = False
_connection_check_after_id = None
CONNECTION_CHECK_INTERVAL_MS = 1000
_last_known_dvr_connected = True  # 樂觀預設，跟 api.is_dvr_connected() 的預設值一致

on_dvr_disconnected_global_callback = None   # DA005ControlPanel.pyw 設定：偵測到斷線瞬間呼叫一次
on_dvr_reconnected_global_callback = None    # DA005ControlPanel.pyw 設定：偵測到恢復連線瞬間呼叫一次

# 2026-08-20 使用者要求：SD Card Presence 監測沿用同一個全域 watcher
# 迴圈（_connection_check_tick()），不另外開一個迴圈——跟上面 DVR 連線
# 監測同一種「_last_known_xxx 記住上一次已知狀態、變化時才動作、透過
# callback 交給 DA005ControlPanel.pyw 決定畫面怎麼反應」的設計。跟 DVR
# 連線不同的地方：SD Card 是否存在無法只靠既有旗標算出來，需要真的
# GET 一次 Memory 才知道，所以這裡多一層「KeepAlive 有沒有通知過 Memory
# 變化」的檢查（api.consume_memory_change_notification()，非阻塞），有
# 通知才觸發背景執行緒去 GET，不是每一輪 tick 都呼叫，也不是固定週期
# GET Memory。
_last_known_sd_card_present = True  # 樂觀預設，跟 api.is_sd_card_present() 的預設值一致

on_sd_card_removed_global_callback = None    # DA005ControlPanel.pyw 設定：偵測到 SD 卡拔除瞬間呼叫一次
on_sd_card_inserted_global_callback = None   # DA005ControlPanel.pyw 設定：偵測到 SD 卡插回瞬間呼叫一次


def start_connection_monitor_loop():
    """Recording 分頁變成使用中時呼叫（refresh_recording_page() 本身就會
    呼叫，不用另外在 DA005ControlPanel.pyw 手動加一次）。現在只負責標記
    is_recording_screen_active，給 _after_if_active() 判斷用；實際的連線
    監測迴圈是全域的，見 start_global_connection_watcher()。"""
    global is_recording_screen_active
    is_recording_screen_active = True


def stop_connection_monitor_loop():
    """離開 Recording 分頁時呼叫。2026-08-19 Ghosting 全面 Audit 發現：這裡
    原本只停連線檢查迴圈本身，沒有讓 current_recording_refresh_token 過期，
    導致還在背景跑的 _refresh_recording_page_worker 晚到的結果仍然會通過
    _apply_if_current_refresh() 的檢查（因為它只比對「是不是最新一次
    refresh」，不知道使用者已經離開整個 Recording 分頁），繼續去動
    preview_canvas。這裡補上 bump token，沿用既有機制，不用另外發明。
    現在不再管理連線檢查迴圈本身（那是全域的，不會因為離開 Recording
    分頁就停止，見 start_global_connection_watcher()）。"""
    global is_recording_screen_active, current_recording_refresh_token
    is_recording_screen_active = False
    current_recording_refresh_token += 1


def start_global_connection_watcher():
    """App 啟動時呼叫一次（在 build_screen() 之後，preview_canvas 才存在），
    全程運作，不會因為使用者切去 Gallery/Player/Settings 就停止——這樣
    斷線才能在任何畫面都被偵測到。重複呼叫不會疊加多個迴圈（用
    _connection_check_after_id 判斷是不是已經在跑）。"""
    if _connection_check_after_id is not None:
        return
    _schedule_next_connection_check()


def _after_if_active(delay_ms, func):
    """Restart/Scenic/Event/Photo 這幾支背景 worker 的完成回呼，原本都是
    直接 preview_canvas.after(0, ...)／photo_button.after(0, ...)，完全沒有
    檢查使用者是不是已經離開 Recording 分頁（2026-08-19 Ghosting 全面
    Audit 發現的問題——它們沒有各自的 refresh_token，跟
    _refresh_recording_page_worker 是不同的兩套，不能直接套用
    _apply_if_current_refresh()）。這裡統一用既有的 is_recording_screen_active
    旗標（本來就是「Recording 分頁是不是使用中」的權威判斷，Connection
    Monitor 也是用它）當作共用的過期判斷，不用再另外發明新的旗標；用哪個
    widget 呼叫 .after() 只是排進同一個 Tk event loop，跟畫面可不可見無關，
    所以統一固定用 preview_canvas.after()。"""
    def guarded():
        if is_recording_screen_active:
            func()
    preview_canvas.after(delay_ms, guarded)


def _report_gallery_item_count_after_completion():
    """Scenic/Event 錄影真正結束（定稿）、或拍照成功之後呼叫（在背景執行緒
    上呼叫，不是主執行緒）：這幾個時間點代表 Gallery 資料可能第一次從 0
    變成 >0，Gallery Tab 要能自動出現，不能等使用者自己點進 Gallery 才
    更新——Tab 都被隱藏了，使用者根本點不到。這裡另外打一次
    api.get_recordings()（是這幾個完成事件各自觸發一次，不是週期性
    polling），結果透過 screen_gallery.report_gallery_item_count_from_
    recordings() 只更新 Gallery Tab 顯示狀態，不會動 Gallery 自己的
    current_groups/畫面內容。刻意不用 _after_if_active()，理由跟
    _refresh_recording_page_worker 裡的說明一樣：Gallery Tab 顯示狀態是
    全域 Navigation Bar 外觀，不是「離開 Recording 畫面就該失效」的
    Recording 專屬 UI。"""
    try:
        recordings = api.get_recordings()
    except Exception as error:
        print(f'[Recording] 更新 Gallery Tab 顯示狀態時取得清單失敗: {error}')
        return
    preview_canvas.after(0, lambda: screen_gallery.report_gallery_item_count_from_recordings(recordings))


def _schedule_next_connection_check():
    global _connection_check_after_id
    _connection_check_after_id = preview_canvas.after(CONNECTION_CHECK_INTERVAL_MS, _connection_check_tick)


def _connection_check_tick():
    """只在連線狀態真的「變化」時才動作，不會每一輪都重畫畫面。全域監測
    （不論目前在哪個畫面都會跑，見 start_global_connection_watcher()），
    這裡只負責偵測狀態變化跟 Recording 自己的 token 失效，不管其他畫面
    要怎麼反應——那是 on_dvr_disconnected_global_callback/
    on_dvr_reconnected_global_callback 的事（DA005ControlPanel.pyw 設定，
    知道目前在哪個畫面、該怎麼安全退出）。
    - 連線中 -> 斷線：讓目前正在背景跑的 refresh worker（如果有的話）失效
      （沿用既有的 current_recording_refresh_token 機制，不用另外發明一套）。
    - 斷線 -> 恢復連線：不能只是把舊 Thumbnail 顯示回來，呼叫端會透過
      callback 呼叫既有的 refresh_recording_page()，重新 GET 真實的
      DvrMode/recordings，依真正狀態顯示 Standby/No Video/Thumbnail。"""
    global _last_known_dvr_connected, current_recording_refresh_token

    currently_connected = api.is_dvr_connected()
    if currently_connected != _last_known_dvr_connected:
        _last_known_dvr_connected = currently_connected
        if currently_connected:
            if on_dvr_reconnected_global_callback:
                on_dvr_reconnected_global_callback()
        else:
            current_recording_refresh_token += 1  # 讓任何還在背景跑的舊 refresh worker 的回呼直接失效，不會晚到蓋掉 Disconnected 畫面
            if on_dvr_disconnected_global_callback:
                on_dvr_disconnected_global_callback()

    # 2026-08-20 新增：SD Card Presence。api.consume_memory_change_
    # notification() 是非阻塞的檢查，只有 KeepAlive 真的收到過
    # RESOURCE_MEMORY 通知才會回傳 True——不是每一輪 tick 都觸發，也不是
    # 固定週期 GET Memory。GET 本身是網路請求，不能直接在這個 Tk after()
    # tick（主執行緒）裡同步呼叫（會卡住整個畫面），丟到背景執行緒處理，
    # 完成後才用 preview_canvas.after(0, ...) 排回主執行緒更新狀態/觸發
    # callback，跟這支檔案其他背景 worker 的既有寫法一致。
    if api.consume_memory_change_notification():
        threading.Thread(target=_handle_memory_changed_notification, daemon=True).start()

    # 2026-08-20 新增：Runtime DVR Baseline Recovery。跟上面 SD Card
    # Presence 同一種寫法——api.consume_dvr_mode_baseline_check_
    # notification() 是非阻塞檢查，只有 KeepAlive 真的收到過
    # RESOURCE_DVR_MODE 通知才會回傳 True，不是每一輪 tick 都觸發，也不是
    # 固定週期 GET DvrMode。用獨立的 _dvr_mode_baseline_check_event（見
    # api.py 說明），不會跟 Event Monitoring 既有的阻塞式
    # wait_for_dvr_mode_change_notification() 互相偷走對方的通知。
    if api.consume_dvr_mode_baseline_check_notification():
        threading.Thread(target=_handle_runtime_dvr_mode_change_notification, daemon=True).start()

    _schedule_next_connection_check()  # 全域監測，全程持續跑，不再受 is_recording_screen_active 限制


def _handle_runtime_dvr_mode_change_notification():
    """背景執行緒：KeepAlive 通知過 DvrMode 有變化（不論是 Tool 自己送出
    的合法操作，還是 DVR 自己 Restart/Reboot 造成的），這裡才真的 GET 一次
    DvrMode，交給共用的 ensure_without_ringbuffer_idle_baseline() 判斷是不是
    「desired=WITHOUT_RINGBUFFER 但 actual 意外變成 WITH_RINGBUFFER」這個
    不預期組合。

    Tool 自己所有合法的 transition（Startup/Reconnect/Recording/Event/
    Photo/Settings/Player/Format）最終都會停在 WITHOUT_RINGBUFFER 的某種
    event_recording_setting、或字面上的 STANDBY，兩者都不會被共用函式判定
    成需要 recovery（見該函式內的說明），所以這裡不需要另外分辨「這次通知
    是不是 Tool 自己合法操作造成的」——共用函式的判斷條件本身就已經排除
    了所有合法狀態，不會誤觸發。

    只有真的判定需要 recovery 時才呼叫 refresh_recording_page() 重新整理
    畫面（recovery 本身進行中的按鈕鎖定，已經在 ensure_without_ringbuffer_
    idle_baseline() 內部處理）；不需要 recovery 就什麼都不做，避免每一次
    DvrMode 變化通知（多數時候是 Tool 自己合法操作造成的）都觸發一次多餘
    的畫面重整。"""
    try:
        mode = api.get_dvr_mode()
    except Exception as error:
        print(f'[Recording] Runtime DvrMode 變化通知，GET DvrMode 失敗，略過這次檢查: {error}')
        return

    result = ensure_without_ringbuffer_idle_baseline(known_mode=mode)
    if result in (BASELINE_RECOVERY_OK, BASELINE_RECOVERY_STANDBY):
        return

    if result == BASELINE_RECOVERY_IN_PROGRESS:
        # 已經有另一個呼叫端（例如同時發生的 Reconnect）在做這次 recovery，
        # 這裡不用排隊等待也不用重複 refresh——那個呼叫端結束時自己會
        # refresh_recording_page()。
        print('[Recording] Runtime：偵測到已經有另一個 recovery 在進行中，這次直接讓路')
        return

    if result == BASELINE_RECOVERY_RECOVERED:
        print('[Recording] Runtime 偵測到 actual=WITH_RINGBUFFER（DVR 可能剛 Restart/Reboot），已成功恢復 WITHOUT_RINGBUFFER + NO_EVENT baseline')
    else:
        print('[Recording] Runtime baseline recovery 未能成功')
    preview_canvas.after(0, refresh_recording_page)


def _handle_memory_changed_notification():
    """背景執行緒：KeepAlive 通知過 Memory 有變化，這裡才真的 GET 一次
    Memory，確認目前 sd_inserted。GET 失敗維持原本快取值，不猜、不改變
    ——下一次 KeepAlive 通知（或使用者重開工具）還有機會重新確認。"""
    try:
        is_present = api.refresh_sd_card_presence()
    except Exception as error:
        print(f'[Recording] KeepAlive 通知 Memory 有變化，但 GET Memory 失敗，SD Card presence 維持原本快取值: {error}')
        return
    preview_canvas.after(0, lambda: _apply_sd_card_presence_change(is_present))


def _sync_last_known_sd_card_present_after_startup():
    """App Startup 呼叫過 api.refresh_sd_card_presence() 之後呼叫，把
    _last_known_sd_card_present 這個「上一次已知狀態」基準值同步成
    Startup 剛讀到的真實值，不留在預設值 True——見 _startup_sync_
    worker() 呼叫端的說明。"""
    global _last_known_sd_card_present
    _last_known_sd_card_present = api.is_sd_card_present()


def _apply_sd_card_presence_change(is_present):
    """只在 SD Card Presence 真的「變化」時才動作（跟 _connection_check_
    tick() 判斷 DVR 連線變化是同一種寫法）：
    - 存在 -> 移除：讓目前正在背景跑的 refresh worker（如果有的話）失效
      （沿用既有的 current_recording_refresh_token 機制），交給
      on_sd_card_removed_global_callback（DA005ControlPanel.pyw 設定）
      決定目前在哪個畫面、該怎麼安全退出。
    - 移除 -> 插回：交給 on_sd_card_inserted_global_callback 呼叫既有的
      refresh_recording_page()，重新 GET 真實的 DvrMode/recordings，不能
      只把先前快取的 Thumbnail 顯示回來。"""
    global _last_known_sd_card_present, current_recording_refresh_token

    if is_present == _last_known_sd_card_present:
        return  # 沒有真的變化，不重複觸發
    _last_known_sd_card_present = is_present

    if is_present:
        if on_sd_card_inserted_global_callback:
            on_sd_card_inserted_global_callback()
    else:
        current_recording_refresh_token += 1  # 讓任何還在背景跑的舊 refresh worker 的回呼直接失效，不會晚到蓋掉 No SD Card 畫面
        if on_sd_card_removed_global_callback:
            on_sd_card_removed_global_callback()


def _refresh_recording_page_worker(refresh_token):
    try:
        mode = api.get_dvr_mode()
    except Exception as error:
        # 2026-08-18 使用者要求：DVR 斷線要有自己獨立的畫面（Rearview
        # Mirror + unavailable 文字 + 三個按鈕 disable），不能只是跳一個
        # 錯誤訊息、讓 Preview 卡在舊的縮圖上。用既有 KeepAlive 累積出來的
        # api.is_dvr_connected() 判斷這次失敗是不是因為斷線，是的話走
        # Disconnected 畫面；不是的話（DVR 連得上，只是這次 GET 剛好出了
        # 其他錯誤）維持原本的通用錯誤訊息，不動這個既有分支。
        if not api.is_dvr_connected():
            preview_canvas.after(0, lambda: _apply_if_current_refresh(refresh_token, show_dvr_disconnected_screen))
        else:
            preview_canvas.after(0, lambda captured_error=error: _apply_if_current_refresh(
                refresh_token, on_initial_state_error, 'Failed to retrieve DVR status.', captured_error))
        return

    # 字面上真的是 STANDBY 代表 Dashcam 關閉，畫面要換成 Dashcam Off，
    # Recording/Event/Photo 三個功能都不能操作，不需要再抓 recordings/縮圖。
    # 2026-08-20 使用者要求的 Recording Preview 狀態優先權：Disconnected
    # （上面已經處理）> Standby > No SD Card > No Video > Normal Preview。
    # Standby 檢查一定要放在 SD Card 檢查前面、維持這裡完全不動——使用者
    # 明確要求「不要破壞目前已驗證的 Standby 行為」，字面上是 STANDBY
    # 代表使用者自己把 Dashcam 關閉，這件事跟有沒有插 SD 卡無關，Standby
    # 畫面優先顯示。
    if is_standby_mode(mode):
        preview_canvas.after(0, lambda: _apply_if_current_refresh(refresh_token, show_dashcam_off_screen))
        return

    # 2026-08-20 新增：DVR 開著、不是 Standby，但目前沒有插 SD 卡——這裡
    # 只讀取 api.is_sd_card_present() 這個既有的記憶體快取值，不會觸發
    # 任何新的網路請求（真正的 GET Memory 只在 App Startup／KeepAlive
    # 通知 Memory 有變化時發生，見 _handle_memory_changed_notification()）。
    if not api.is_sd_card_present():
        preview_canvas.after(0, lambda: _apply_if_current_refresh(refresh_token, show_no_sd_card_preview))
        return

    state = derive_dvr_state(mode)
    # 2026-08-14 暫時診斷 Log（使用者回報 Recording 按鈕持續顯示反白/Active
    # 樣式，懷疑是真機在 WITHOUT_RINGBUFFER 待機時 event_recording_setting
    # 沒有回報 NO_EVENT，導致每次進入 Recording 頁面都被誤判成 scenic_recording。
    # 用完這次調查後要移除，不要留在正式版本。）
    raw_dvr_mode_name = audi_pb2.DvrMode.DvrSystemState.Name(mode.dvr_mode)
    raw_event_name = audi_pb2.DvrMode.DvrEvent.Name(mode.event_recording_setting)
    print(f'[Recording 頁面刷新診斷] raw dvr_mode={raw_dvr_mode_name}, '
          f'raw event_recording_setting={raw_event_name}, derived state={state}')
    preview_canvas.after(0, lambda: _apply_if_current_refresh(refresh_token, on_initial_dvr_state_ready, state))

    # 2026-08-21 第三次重新設計：找最新影片/下載縮圖/套用 Preview 這幾步
    # 收斂進 refresh_preview_for_dvr_state()，這裡只需要單獨為 Gallery Tab
    # 項目數再 GET 一次（跟 refresh_preview_for_dvr_state() 內部各自獨立
    # GET，不共用同一份資料——這裡的 GET 主要是為了 Gallery 項目數，換
    # 一次網路請求換取兩邊職責清楚分離，這個路徑本身不是高頻操作）。
    try:
        _log_get_recordings_call('_refresh_recording_page_worker', time(), 0)
        recordings = api.get_recordings()
        preview_canvas.after(0, lambda: screen_gallery.report_gallery_item_count_from_recordings(recordings))
    except Exception as error:
        if not api.is_dvr_connected():
            preview_canvas.after(0, lambda: _apply_if_current_refresh(refresh_token, show_dvr_disconnected_screen))
        else:
            preview_canvas.after(0, lambda captured_error=error: _apply_if_current_refresh(
                refresh_token, on_initial_state_error, 'Failed to retrieve recordings list.', captured_error))
        return

    # refresh_preview_for_dvr_state() 找不到任何影片時什麼都不會套用
    # （維持畫面現狀），startup/reconnect 這裡需要明確判斷「真的完全沒有
    # 任何影片」並顯示 No Video 畫面，不能讓 Preview 停在空白/上次殘留的
    # 內容——沿用剛剛已經抓到的 recordings，不用再多打一次 API。
    #
    # 2026-08-21 Codex review 抓到：這裡原本只靠既有的 refresh_token 保護
    # （跟其他分頁切換/斷線情境共用），沒有跟 current_dvr_state 比對——
    # 如果這次判斷「沒有任何影片」之後，在排入的 .after() 真正執行之前，
    # 剛好發生一次真正的 transition（例如使用者立刻按了 Start），那次
    # transition 自己的 refresh_preview_for_dvr_state() 可能已經先把畫面
    # 換成正確的新內容，這裡晚到的 No Video 畫面會把它蓋掉。改成跟
    # refresh_preview_for_dvr_state() 的 generation=None 保護用同一套
    # 規則：套用前再確認 current_dvr_state 仍然等於這次判斷時的 state。
    if api.get_latest_video_group_by_trigger_time(recordings) is None:
        def guarded_show_no_video():
            if current_dvr_state == state:
                show_no_video_preview()
        preview_canvas.after(0, lambda: _apply_if_current_refresh(refresh_token, guarded_show_no_video))
        return

    refresh_preview_for_dvr_state(state, None)


def on_initial_dvr_state_ready(state):
    global is_dashcam_off, is_dvr_disconnected
    is_dashcam_off = False
    is_dvr_disconnected = False  # 這裡代表 GET DvrMode 真的成功了，一定是連得上的，不會還停在斷線狀態
    apply_dvr_state_to_buttons(state)
    recording_button.config(state='normal')
    event_button.config(state='normal')
    photo_button.config(state='normal')
    status_label.config(text='')

    # 如果一開啟工具就發現 DVR 已經在 Event 錄影中，順便開始輪詢，
    # 這樣事件結束時按鈕顏色才會自己更新，不用等使用者手動點什麼才發現
    if state == 'event_recording':
        start_event_completion_polling()


def on_initial_state_error(title, error):
    status_label.config(text=title)
    recording_button.config(state='normal')
    event_button.config(state='normal')
    photo_button.config(state='normal')
    messagebox.showerror(title, f'{error}')


# ============================================================
# Dashcam Off 畫面：actual dvr_mode 字面上真的是 STANDBY
# ============================================================

def show_dashcam_off_screen():
    """2026-08-14 使用者回報修正：進入這個畫面前如果 Recording/Event 正是
    Active 樣式（白底+粉紅 icon/text/粗體），單純 .config(state='disabled')
    不會重設 bg/fg/image/font，畫面會卡住舊的 Active 樣式看起來像還在錄影。
    這裡改成先呼叫 apply_dvr_state_to_buttons('standby')，把 current_dvr_state
    跟 Recording/Event 按鈕樣式一起同步回 Idle，再統一 disable 三個按鈕
    （Photo 不受 apply_dvr_state_to_buttons() 管，這裡另外重設回 Idle 樣式，
    跟 _reset_photo_button_after_success()/on_photo_failed() 用同一套值）。"""
    global is_dashcam_off, is_dvr_disconnected
    is_dashcam_off = True
    is_dvr_disconnected = False  # 真的收到 GET DvrMode 回應才會走到這裡，確定是連得上的
    apply_dvr_state_to_buttons('standby')
    recording_button.config(state='disabled')
    event_button.config(state='disabled')
    photo_button.config(state='disabled', bg=HCP3_LIGHT_PANEL_COLOR, fg=HCP3_LIGHT_PANEL_TEXT_COLOR,
                         image=photo_icon_idle_image, font=HCP3_IDLE_FONT)
    status_label.config(text='')
    draw_dashcam_off_overlay()


# ============================================================
# DVR 斷線畫面：跟 STANDBY／Connected+No Video 都不一樣的第三種獨立狀態
# ============================================================

def show_dvr_disconnected_screen():
    """2026-08-18 新增：DVR 斷線（拔網路線、DVR 沒回應、API 確認連不上）
    時顯示的畫面，不能跟 STANDBY 或「有連線但沒有影片」混在一起：
    - 一樣顯示 Rearview Mirror，但不能有 Power/Restart 熱區（連不上，
      按了也沒有意義）——沿用沒有電源圖示的素材版本，理由跟
      show_no_video_preview() 一樣。
    - 文字用「Dashcam is currently unavailable.」（對照 HCP3 實機德文
      原文「Dashcam ist zurzeit nicht verfügbar.」的語意，比字面翻譯的
      「DVR is not connected.」更貼近實機用詞）。
    - Recording/Event/Photo 三個按鈕全部 disable——斷線狀態下任何操作都
      送不出去。
    is_dashcam_off 刻意不設成 True：那個旗標的語意是「DVR 開著、字面上
    真的是 STANDBY」，跟「根本連不上 DVR」是兩件不同的事，其他地方
    （例如 on_restart_button_click()）判斷要不要顯示/處理 Restart 動作
    時，不應該把斷線誤判成可以按 Restart 恢復的 STANDBY。"""
    global is_dashcam_off, is_dvr_disconnected, restart_button_bbox, preview_image_ref

    is_dashcam_off = False
    is_dvr_disconnected = True
    restart_button_bbox = None  # 斷線畫面沒有可點擊的 Power/Restart 熱區

    apply_dvr_state_to_buttons('standby')  # Recording/Event 按鈕樣式回到中性 Idle，沿用既有函式
    recording_button.config(state='disabled')
    event_button.config(state='disabled')
    photo_button.config(state='disabled', bg=HCP3_LIGHT_PANEL_COLOR, fg=HCP3_LIGHT_PANEL_TEXT_COLOR,
                         image=photo_icon_idle_image, font=HCP3_IDLE_FONT)
    status_label.config(text='')

    preview_canvas.delete('all')
    background_image, _ = build_mirror_background_image(
        STANDBY_MIRROR_TOP_RATIO, mirror_filename='rearview_mirror_no_power.png')
    preview_image_ref = ImageTk.PhotoImage(background_image)
    preview_canvas.create_image(0, 0, anchor='nw', image=preview_image_ref)

    center_x = PREVIEW_SIZE[0] // 2
    preview_canvas.create_text(center_x, int(PREVIEW_SIZE[1] * 0.78),
                                text='Dashcam is currently unavailable.',
                                fill=HCP3_LIGHT_PANEL_TEXT_COLOR, font=('Calibri', 15, 'bold'))


# ============================================================
# No SD Card 畫面：DVR 連線正常、字面上不是 STANDBY，但沒有插 SD 卡
# ============================================================

def show_no_sd_card_preview():
    """2026-08-20 新增：api.is_sd_card_present() 為 False 時顯示的畫面
    ——這是獨立於 Disconnected／Standby／No Video 的第四種狀態，不能混在
    一起：
    - 跟 No Video（「有 SD 卡但目前沒有任何錄影/照片」）不一樣，沒有 SD
      卡的話 Recording/Event/Photo 這幾個操作根本沒有地方可以寫入，維持
      disable（跟 Standby/Disconnected 一致的處理方式）。
    - 視覺語言完全比照 Standby(「Dashcam is turned off.」)/Disconnected
      (「Dashcam is currently unavailable.」)/No Video(「No videos
      available.」) 這三個既有狀態：同一個 Mirror 位置
      （STANDBY_MIRROR_TOP_RATIO，不額外偏移）、同一個文字樣式（Calibri
      15 bold、HCP3_LIGHT_PANEL_TEXT_COLOR、baseline 在
      PREVIEW_SIZE[1]*0.78）。跟 No Video/Disconnected 一樣用沒有 Power
      圖示的 Mirror 素材（DVR 這時是開著的，不能出現看起來可以點的電源
      圖示），不會設定 is_dashcam_off/restart_button_bbox，沒有可點擊的
      熱區，也不會顯示 Play icon（current_preview_group 設成 None，跟
      show_no_video_preview() 一樣不能點擊播放）。

    這個函式只依賴 api.is_sd_card_present() 這個記憶體快取值，本身完全
    不會觸發任何網路請求——真正的 GET Memory 只在 App Startup／KeepAlive
    通知 Memory 有變化這兩個時機發生，見 api.py 的說明。"""
    global is_dashcam_off, is_dvr_disconnected, restart_button_bbox
    global current_preview_group, current_preview_playable, preview_image_ref

    is_dashcam_off = False
    is_dvr_disconnected = False  # 跟 Disconnected 是不同狀態，這裡 DVR 本身是連得上的
    restart_button_bbox = None  # 沒有 SD 卡的畫面沒有可點擊的 Power/Restart 熱區
    current_preview_group = None
    current_preview_playable = False

    apply_dvr_state_to_buttons('standby')  # Recording/Event 按鈕樣式回到中性 Idle，沿用既有函式
    recording_button.config(state='disabled')
    event_button.config(state='disabled')
    photo_button.config(state='disabled', bg=HCP3_LIGHT_PANEL_COLOR, fg=HCP3_LIGHT_PANEL_TEXT_COLOR,
                         image=photo_icon_idle_image, font=HCP3_IDLE_FONT)
    status_label.config(text='')

    preview_canvas.delete('all')
    background_image, _ = build_mirror_background_image(
        STANDBY_MIRROR_TOP_RATIO, mirror_filename='rearview_mirror_no_power.png')
    preview_image_ref = ImageTk.PhotoImage(background_image)
    preview_canvas.create_image(0, 0, anchor='nw', image=preview_image_ref)

    center_x = PREVIEW_SIZE[0] // 2
    preview_canvas.create_text(center_x, int(PREVIEW_SIZE[1] * 0.78),
                                text='Please insert SD card.',
                                fill=HCP3_LIGHT_PANEL_TEXT_COLOR, font=('Calibri', 15, 'bold'))


# Rearview Mirror 圖片寬度佔 Preview 寬度的比例，STANDBY 畫面跟「DVR 已
# 開啟但沒有 Thumbnail」的 Fallback 畫面共用同一個比例，看起來才一致。
MIRROR_WIDTH_RATIO = 0.5

# Rearview Mirror 圖片頂端在 Preview 區域裡的垂直位置比例——Standby 畫面
# 原本就用 0.06（見 draw_dashcam_off_overlay()）。Standby/Disconnected/
# No Video/縮圖下載失敗這幾個狀態都直接沿用同一個比例、不加任何額外
# 偏移，切換時不會上下跳動，Mirror 尺寸/位置/下方文字 baseline 三種狀態
# 完全一致，只有中央 icon/文字內容不同。
STANDBY_MIRROR_TOP_RATIO = 0.06

# 電源圖示在 assets/rearview_mirror.png 原始圖片（717x440）裡的相對位置，
# 量測自實際圖片（圓心座標、半徑都除以原圖寬高換算成比例），縮放後用
# 同樣比例換算成實際畫面座標，不管 mirror 最後顯示多大都能正確算出
# Power/Refresh 的可點擊範圍，不用另外畫一個獨立的 Restart 按鈕。
MIRROR_POWER_ICON_CENTER_RATIO = (335 / 717, 317 / 440)
MIRROR_POWER_ICON_RADIUS_RATIO = 44 / 717


def build_mirror_background_image(mirror_top_ratio=None, mirror_filename='rearview_mirror.png'):
    """組出「淺色圓角背景 + Rearview Mirror 圖片」的 PIL 圖片。Standby/
    Disconnected/No Video 這三個狀態、跟「DVR 已開啟但沒有 Thumbnail」的
    Fallback 畫面都共用這個函式，差別只在 mirror 貼的垂直位置：
    mirror_top_ratio 有給的話，mirror 頂端對齊 PREVIEW_SIZE[1] 的這個比例
    位置（Standby/Disconnected/No Video 都用同一個 STANDBY_MIRROR_TOP_RATIO，
    下面留同樣的空間放文字，三種狀態的 Mirror 尺寸/位置/文字 baseline
    因此完全一致，只有中央 icon/文字內容不同）；沒給（None）就整張圖垂直
    置中（縮圖下載失敗的 Fallback 畫面用，沒有文字）。

    mirror_filename 預設是 'rearview_mirror.png'（STANDBY 用，裡面內建
    電源圖示）。2026-08-14 新增：DVR 已開啟但沒有 Thumbnail 時不能用這張
    圖——素材本身內建的電源圖示會被誤會成「可以點擊恢復」，但這個狀態
    下 DVR 已經是開著的，不應該再出現電源圖示。改用另外產生的
    'rearview_mirror_no_power.png'（用 OpenCV inpainting 把電源圖示的
    區域用周圍鏡面的漸層顏色補平，其餘部分完全沒動），呼叫端指定要用
    哪一個。

    回傳 (合成後的圖片, mirror 實際的 (x, y, width, height))，呼叫端可以
    用後者算 Power Icon 的可點擊範圍（如果這次用的是 no_power 版本，
    呼叫端就不應該再用這個範圍設定任何可點擊熱區）。"""
    background_image = create_rounded_background_image(PREVIEW_SIZE, HCP3_PANEL_CORNER_RADIUS,
                                                         HCP3_LIGHT_PANEL_COLOR)
    mirror_max_size = (int(PREVIEW_SIZE[0] * MIRROR_WIDTH_RATIO), int(PREVIEW_SIZE[0] * MIRROR_WIDTH_RATIO))
    mirror_image = load_asset_image(mirror_filename, mirror_max_size)
    mirror_x = (PREVIEW_SIZE[0] - mirror_image.width) // 2
    if mirror_top_ratio is None:
        mirror_y = (PREVIEW_SIZE[1] - mirror_image.height) // 2
    else:
        mirror_y = int(PREVIEW_SIZE[1] * mirror_top_ratio)
    background_image.paste(mirror_image, (mirror_x, mirror_y), mirror_image)
    return background_image, (mirror_x, mirror_y, mirror_image.width, mirror_image.height)


def draw_dashcam_off_overlay():
    """畫面右側維持沿用同一塊 preview_canvas 區域（不重建 Widget、大小完全
    不變），只是改畫淺色圓角背景＋後照鏡商品圖＋文字，跟正常縮圖畫面切換
    時不會有版面跳動。2026-08-14 第二次調整：拿掉獨立的 Restart 按鈕
    （原本是另外畫一個矩形+文字），改成直接讓 Rearview Mirror 圖片裡
    內建的 Power/Refresh 圖示可以點擊——restart_button_bbox 現在對應的是
    mirror 圖片裡電源圖示的實際位置，不是獨立按鈕的位置；點擊後觸發的
    still 是同一個 on_restart_button_click()/_restart_worker()，恢復
    邏輯完全沒變，只是觸發位置換了。"""
    global restart_button_bbox, preview_image_ref

    preview_canvas.delete('all')

    background_image, (mirror_x, mirror_y, mirror_width, mirror_height) = build_mirror_background_image(STANDBY_MIRROR_TOP_RATIO)
    preview_image_ref = ImageTk.PhotoImage(background_image)
    preview_canvas.create_image(0, 0, anchor='nw', image=preview_image_ref)

    center_x = PREVIEW_SIZE[0] // 2
    preview_canvas.create_text(center_x, int(PREVIEW_SIZE[1] * 0.78),
                                text='Dashcam is turned off.',
                                fill=HCP3_LIGHT_PANEL_TEXT_COLOR, font=('Calibri', 15, 'bold'))

    power_center_x = mirror_x + mirror_width * MIRROR_POWER_ICON_CENTER_RATIO[0]
    power_center_y = mirror_y + mirror_height * MIRROR_POWER_ICON_CENTER_RATIO[1]
    # 點擊熱區比圖示本身稍微放大一點（1.5 倍），比較好點中，不用剛好點在
    # 圖示的每一個像素上。
    power_hit_radius = mirror_width * MIRROR_POWER_ICON_RADIUS_RATIO * 1.5
    restart_button_bbox = (power_center_x - power_hit_radius, power_center_y - power_hit_radius,
                            power_center_x + power_hit_radius, power_center_y + power_hit_radius)


def is_point_in_bbox(x, y, bbox):
    x1, y1, x2, y2 = bbox
    return x1 <= x <= x2 and y1 <= y <= y2


def on_restart_button_click():
    """Restart 是「把 DVR 開起來」的動作：只要畫面顯示 Dashcam Off（actual
    字面上真的是 STANDBY），按下去就一定要嘗試切成 WITHOUT_RINGBUFFER，
    不能因為 desired_dvr_mode 剛好也是 STANDBY 就悶不吭聲什麼都不做
    （2026-08-14 使用者明確要求：不管 desired 目前是什麼，只要 DVR 開啟
    著、點 Restart 就要變成 WITHOUT_RINGBUFFER）。所以這裡改成呼叫跟
    Settings Switch 同一個函式 api.apply_and_set_desired_dvr_mode()——
    PUT → GET 確認 → 成功才把這個值存成新的 desired_dvr_mode，這樣
    Restart 成功後 Settings 顯示的狀態也會一起同步變成 ON，不會出現
    「actual 已經 WITHOUT_RINGBUFFER 但 Settings 還顯示 OFF」的不同步
    情況。"""
    global restart_busy
    if restart_busy:
        return  # 已經有一次 Restart 在跑，忽略連點，避免同時跑出兩個並行 request

    restart_busy = True
    status_label.config(text='Restarting...')
    thread = threading.Thread(target=_restart_worker, daemon=True)
    thread.start()


def _restart_worker():
    """api.apply_and_set_desired_dvr_mode() 目前設計上內部會自己接住例外、
    回傳 True/False，理論上不會 raise，但這裡不能只依賴這個隱性假設——如果
    api.py 以後改動不小心漏包 try/except、真的讓例外跑出來，這個 daemon
    thread 沒接住就會直接默默死掉，restart_busy 卡在 True 永遠不會被清除，
    之後每次點 Restart 都會被擋掉、畫面上也不會有任何錯誤訊息
    （Codex review 2026-08-13 第二輪發現的問題）。用 try/except/finally
    包起來：finally 一定清 restart_busy，非預期例外一樣走既有的
    on_action_error() 顯示錯誤。"""
    global restart_busy
    try:
        success = api.apply_and_set_desired_dvr_mode(
            audi_pb2.DvrMode.DvrSystemState.DVR_SYSTEM_STATE_RECORD_WITHOUT_RINGBUFFER)
    except Exception as error:
        _after_if_active(0, lambda captured_error=error: on_action_error(
            'Failed to restart dashcam.', captured_error))
        return
    finally:
        restart_busy = False

    if success:
        _after_if_active(0, refresh_recording_page)
    else:
        _after_if_active(0, lambda: on_action_error(
            'Failed to restart dashcam.', Exception('DVR did not switch to the expected mode.')))


# ============================================================
# 縮圖繪製：只有明確操作事件才會呼叫這幾個函式
# ============================================================

def get_preview_font(size):
    """Preview 區域文字用的字型：Windows 優先用 Calibri（跟其他 Tkinter
    元件一致），行為不變。macOS 沒有 calibri.ttf 這個檔案，改抓系統內建
    的 Helvetica／Arial，維持文字有正常大小跟清晰度；兩邊都找不到才退回
    PIL 內建的陽春點陣字型（忽略 size，但不會直接噴例外讓畫面整個開不
    起來）。"""
    if sys.platform == 'darwin':
        for macos_font_path in (
            '/System/Library/Fonts/Helvetica.ttc',
            '/System/Library/Fonts/Supplemental/Arial.ttf',
        ):
            try:
                return ImageFont.truetype(macos_font_path, size)
            except Exception:
                continue
        return ImageFont.load_default()

    try:
        return ImageFont.truetype('calibri.ttf', size)
    except Exception:
        return ImageFont.load_default()


def create_rounded_background_image(size, radius, fill_color):
    """建立一張純色、四個角落是圓角的 RGBA 圖片（角落以外是透明的，貼到
    preview_canvas 上時會透出外圍的 BACKGROUND_COLOR）。2026-08-14 對照
    HCP3 實機截圖新增：右側 Preview 區域四個角落都是圓角。2026-08-17
    改成呼叫 ui_shapes.make_rounded_rect_image()（高解析度畫完再 LANCZOS
    縮小，邊緣才平滑，不是直接在最終尺寸上畫）——回傳的是獨立副本，
    呼叫端（show_loading_state() 會在上面畫文字、build_mirror_background_
    image() 會貼 mirror 圖片）可以直接繼續 mutate，不會弄髒共用 cache。"""
    return ui_shapes.make_rounded_rect_image(size[0], size[1], radius, fill=fill_color)


def apply_rounded_mask(image, radius):
    """把一張圖片（例如縮圖）套上圓角遮罩，四個角落以外變透明。2026-08-17
    改成用 ui_shapes.make_rounded_mask()（高解析度畫完再 LANCZOS 縮小＋
    cache，同尺寸/半徑不用每次重算），邊緣才會平滑。"""
    mask = ui_shapes.make_rounded_mask(image.size[0], image.size[1], radius)
    result = image.convert('RGBA')
    result.putalpha(mask)
    return result


def fit_image_contain(image, target_size, background_color):
    """把圖片等比例縮放到剛好完整放進 target_size 裡面（不裁切、不變形
    拉伸），多出來的空間用 background_color 補滿、置中對齊。

    2026-08-19 使用者要求：Preview Canvas 放大之後，縮圖不能再用
    image.resize(target_size) 直接硬拉伸成新的寬高比，會讓畫面變形，改用
    這支函式算出「維持原始比例，長邊剛好貼齊 target_size」的縮放結果。"""
    target_width, target_height = target_size
    source_width, source_height = image.size
    scale = min(target_width / source_width, target_height / source_height)
    new_width = max(1, round(source_width * scale))
    new_height = max(1, round(source_height * scale))
    resized_image = image.resize((new_width, new_height), Image.LANCZOS)

    canvas_image = Image.new('RGB', target_size, background_color)
    paste_x = (target_width - new_width) // 2
    paste_y = (target_height - new_height) // 2
    canvas_image.paste(resized_image, (paste_x, paste_y))
    return canvas_image


def show_loading_state():
    global preview_image_ref
    preview_canvas.delete('all')
    background_image = create_rounded_background_image(PREVIEW_SIZE, HCP3_PANEL_CORNER_RADIUS, screen_gallery.ROW_COLOR)
    draw = ImageDraw.Draw(background_image)
    draw.text((PREVIEW_SIZE[0] // 2, PREVIEW_SIZE[1] // 2), 'Loading...',
               fill=screen_gallery.TEXT_PRIMARY_COLOR, font=get_preview_font(15), anchor='mm')
    preview_image_ref = ImageTk.PhotoImage(background_image)
    preview_canvas.create_image(0, 0, anchor='nw', image=preview_image_ref)


def show_no_video_preview():
    """DVR 已經開啟、連線正常（不是 STANDBY、也不是斷線）但目前沒有任何
    可顯示的縮圖時的畫面。

    2026-08-19 使用者回報：HCP3 的 Information icon 本身在實機上有可點擊/
    跳轉功能，但這個工具沒有對應功能，不該留一個看起來能點、實際上完全
    沒作用的 icon（HCP3 有的功能，PC Tool 沒有實作，不能照抄外觀）。改成
    跟 Standby/Disconnected 一致的純文字提示「No videos available.」，
    字型/顏色/baseline 直接沿用同一份設定（Calibri 15 bold、
    HCP3_LIGHT_PANEL_TEXT_COLOR、PREVIEW_SIZE[1] * 0.78），不用 Information
    icon。因為現在下面有文字了，Mirror 位置也改回跟 Standby/Disconnected
    完全一致的 STANDBY_MIRROR_TOP_RATIO（不用額外的 extra_y_offset）——
    三種狀態的 Mirror 尺寸/位置/文字 baseline 一致，只有中央 icon/文字
    內容不同：Standby 是 Mirror+Power icon+「Dashcam is turned off.」，
    Disconnected 是 Mirror+「Dashcam is currently unavailable.」，這裡是
    Mirror+「No videos available.」。

    這個狀態下 DVR 已經開著，畫面上不能出現 Power/Refresh 圖示本身（不是
    只 disable 點擊，是連圖示都不能看到），所以沿用既有的
    rearview_mirror_no_power.png（用 OpenCV inpainting 把電源圖示區域
    補平的版本），不能用 STANDBY 用的 rearview_mirror.png。這裡也不會
    設定 is_dashcam_off/restart_button_bbox。"""
    global current_preview_group, current_preview_playable, preview_image_ref
    current_preview_group = None
    current_preview_playable = False
    preview_canvas.delete('all')
    background_image, _ = build_mirror_background_image(
        STANDBY_MIRROR_TOP_RATIO, mirror_filename='rearview_mirror_no_power.png')
    preview_image_ref = ImageTk.PhotoImage(background_image)
    preview_canvas.create_image(0, 0, anchor='nw', image=preview_image_ref)

    center_x = PREVIEW_SIZE[0] // 2
    preview_canvas.create_text(center_x, int(PREVIEW_SIZE[1] * 0.78),
                                text='No videos available.',
                                fill=HCP3_LIGHT_PANEL_TEXT_COLOR, font=('Calibri', 15, 'bold'))


def update_preview_image(recording_group, thumbnail_bytes, update_group_reference=True):
    """只更新縮圖本身（Canvas 上顯示的圖片），預設也會換掉背後的
    current_preview_group 參照；不動 current_preview_playable，也不呼叫
    refresh_play_button_visibility()（那兩件事由呼叫端決定）。

    update_group_reference=False 只在 refresh_preview_for_dvr_state()
    判斷 startup/reconnect 這次結果已經過期（current_dvr_state 跟
    target_state 不一致）時用來完全略過套用（見該函式說明），一般情況下
    （state transition 觸發的正常刷新）永遠傳 True。

    回傳 True 代表真的套用了縮圖內容，False 代表縮圖抓取失敗、套用了
    Fallback 畫面（呼叫端據此決定 playable 要不要強制設 False）。"""
    global preview_image_ref, current_preview_group, _last_applied_thumbnail_bytes

    # === 診斷（PREVIEW_THUMBNAIL_DIAGNOSTIC_LOGGING，見上方說明） ===
    _preview_diag_log(f'update_preview_image() 呼叫前: update_group_reference={update_group_reference}, '
                       f'photoimage_id_before={id(preview_image_ref)}')
    _preview_diag_describe_group('update_preview_image() 呼叫前的 current_preview_group', current_preview_group)
    _preview_diag_describe_group('update_preview_image() 這次要套用的 recording_group', recording_group)
    _preview_diag_log_thumbnail('update_preview_image() 呼叫前，目前 Canvas 上的舊縮圖', _last_applied_thumbnail_bytes)
    _preview_diag_log_thumbnail('update_preview_image() 這次要套用的新縮圖', thumbnail_bytes)

    if update_group_reference:
        current_preview_group = recording_group
    preview_canvas.delete('all')

    if recording_group is None or thumbnail_bytes is None:
        # 2026-08-14 使用者要求：縮圖下載失敗時也不要顯示「Thumbnail
        # Unavailable」文字，一樣用 Rearview Mirror 圖片當 Fallback，
        # 跟 show_no_video_preview() 共用同一個畫法。DVR 這時是開著的，
        # 所以一樣要用沒有 Power 圖示的版本，不能用 STANDBY 用的那張。
        background_image, _ = build_mirror_background_image(
            STANDBY_MIRROR_TOP_RATIO, mirror_filename='rearview_mirror_no_power.png')
        preview_image_ref = ImageTk.PhotoImage(background_image)
        preview_canvas.create_image(0, 0, anchor='nw', image=preview_image_ref)
        _last_applied_thumbnail_bytes = None
        _preview_diag_log(f'update_preview_image() 呼叫後: image_ready=False（recording_group 或 thumbnail_bytes 是 None），'
                           f'套用 Fallback 畫面, photoimage_id_after={id(preview_image_ref)}')
        return False

    image = Image.open(BytesIO(thumbnail_bytes)).convert('RGB')
    image = fit_image_contain(image, PREVIEW_SIZE, screen_gallery.ROW_COLOR)
    image = apply_rounded_mask(image, HCP3_PANEL_CORNER_RADIUS)
    preview_image_ref = ImageTk.PhotoImage(image)
    canvas_item_id = preview_canvas.create_image(0, 0, anchor='nw', image=preview_image_ref)
    _last_applied_thumbnail_bytes = thumbnail_bytes
    _preview_diag_log(f'update_preview_image() 呼叫後: image_ready=True, '
                       f'photoimage_id_after={id(preview_image_ref)}（一定跟呼叫前不同，這裡是 delete(\'all\') + '
                       f'create_image() 建新的，不是 itemconfig() 換現有 item 的 image 參照）, '
                       f'canvas_item_id={canvas_item_id}, canvas_item_count={len(preview_canvas.find_all())}')
    _preview_diag_describe_group('update_preview_image() 呼叫後的 current_preview_group', current_preview_group)
    return True


def show_preview(recording_group, thumbnail_bytes, playable):
    """顯示一張新的縮圖，同時設定可不可播放（開始 Scenic / 開始 Event /
    工具啟動時才會呼叫）。背景的 DvrMode 輪詢絕對不會呼叫這個函式，避免
    縮圖被輪詢意外換掉。行為跟拆開 update_preview_image() 之前完全一樣，
    只是內部委派給它。"""
    global current_preview_playable
    image_ready = update_preview_image(recording_group, thumbnail_bytes)
    if not image_ready:
        current_preview_playable = False
        return
    current_preview_playable = playable
    refresh_play_button_visibility()


def _apply_preview_update(group_to_apply, thumbnail_bytes, playable_to_apply):
    """在主執行緒套用 refresh_preview_for_dvr_state() 算好的結果。
    generation/current_dvr_state 的過期判斷都已經在 refresh_preview_for_
    dvr_state() 裡處理完才會排到這裡，這裡單純負責：縮圖+group 套用，
    playable 依縮圖是否真的套用成功決定最終值（縮圖抓取失敗時一律不可
    播放）。"""
    global current_preview_playable
    image_ready = update_preview_image(group_to_apply, thumbnail_bytes, update_group_reference=True)
    current_preview_playable = playable_to_apply if image_ready else False
    refresh_play_button_visibility()


def is_preview_currently_playable():
    """2026-08-14 使用者修正：Play Button 能不能顯示/點擊，不能只看某次轉場
    當下寫入的固定值，要即時運算三個條件：(1) 縮圖本身有可播放的內容
    （current_preview_playable，由 show_preview()/mark_current_preview_playable()
    設定，代表這個 group 的檔案已經定稿），(2) DVR 現在不是在錄影
    （current_dvr_state 不是 scenic_recording/event_recording），(3) 不是
    Dashcam Off。只要 DVR 現在仍在錄影，即使縮圖內容本身早就定稿完成，也
    不能顯示/點擊 Play Button——縮圖要不要換是另一件事，這裡只管
    Play Button。"""
    return (current_preview_playable
            and current_dvr_state not in ('scenic_recording', 'event_recording')
            and not is_dashcam_off)


def _is_play_button_area_visible():
    """2026-08-21 第七次修正：Play Button「要不要顯示」跟「顯示成可點擊還是
    disabled」拆成兩件事——只要有 group 可以看、DVR 不是在錄影、也不是
    Dashcam Off，就應該顯示 Play Button（可能是 disabled 樣式）。真正能不能
    點擊播放，還是只看 is_preview_currently_playable()（完全不改，維持原本
    的安全判斷）。這樣 Stop/Event Complete confirm 完成後，不用等
    fetch_finalized_group() 的重試跑完，Play Button 就能立刻出現（先顯示
    disabled 樣式），不會讓使用者以為完全沒有反應。"""
    return (current_preview_group is not None
            and current_dvr_state not in ('scenic_recording', 'event_recording')
            and not is_dashcam_off)


def refresh_play_button_visibility():
    """_is_play_button_area_visible() 決定 Play Button 疊圖要不要顯示；
    顯示的話再用 is_preview_currently_playable() 決定顯示成可點擊還是
    disabled 樣式。current_dvr_state/is_dashcam_off/current_preview_playable
    任何時候改變（不只是換縮圖的當下）都要呼叫這個函式重新整理，例如
    Event 結束後 DVR 自動接回 Scenic，縮圖沒換但錄影狀態變了，也要立刻把
    Play Button 藏起來。"""
    preview_canvas.delete('play_button_overlay')
    if _is_play_button_area_visible():
        draw_play_button_overlay(enabled=is_preview_currently_playable())


def mark_current_preview_playable(playable):
    """縮圖本身不變，只切換「縮圖內容本身可不可播放」（停止 Scenic 確認完成 /
    Event 確認完成時用這個，絕對不要呼叫 show_preview() 重新抓縮圖——例如
    Event 完成後，即使 DVR 已經接回 Scenic 繼續錄影，畫面上還是要維持顯示
    Event 的縮圖，不能自動換回 Scenic 縮圖）。Play Button 實際顯不顯示還要
    另外看 is_preview_currently_playable()，不是這個值說了算。"""
    global current_preview_playable
    current_preview_playable = playable
    refresh_play_button_visibility()


play_icon_image_ref = None  # PhotoImage 一定要保留參照，不然會被 GC 回收


def draw_play_button_overlay(enabled=True):
    """2026-08-14 改用 assets/play_icon.png（白底黑三角），取代原本用
    Canvas 畫的圓形+三角形。

    2026-08-21 第七次修正：新增 enabled 參數——enabled=False 時（Stop/
    Event Complete confirm 完成、還在等 fetch_finalized_group() 確認可不可
    播放的空檔）把圖示透明度降低，畫出視覺上明顯是「disabled」的樣式，
    跟可以點擊的正常樣式區分。不新增素材檔案，直接調整既有 play_icon.png
    的 alpha channel。"""
    global play_icon_image_ref
    center_x = PREVIEW_SIZE[0] // 2
    center_y = PREVIEW_SIZE[1] // 2
    icon_image = load_asset_image('play_icon.png', (80, 80))
    if not enabled:
        alpha_channel = icon_image.split()[3]
        dimmed_alpha_channel = alpha_channel.point(lambda alpha: int(alpha * 0.4))
        icon_image.putalpha(dimmed_alpha_channel)
    play_icon_image_ref = ImageTk.PhotoImage(icon_image)
    preview_canvas.create_image(center_x, center_y, image=play_icon_image_ref, tags='play_button_overlay')


def on_preview_click(event):
    # Live View 顯示中：畫面已經不是縮圖，不允許點擊觸發播放。
    if _live_view_active:
        return
    if is_dashcam_off:
        if restart_button_bbox is not None and is_point_in_bbox(event.x, event.y, restart_button_bbox):
            on_restart_button_click()
        return
    # 2026-08-14 使用者修正：點擊能不能觸發播放，改用即時運算的
    # is_preview_currently_playable()，不是只看 current_preview_playable 這個
    # 縮圖內容層級的旗標——DVR 現在如果仍在錄影，即使內容本身已經定稿完成，
    # 也不允許點擊播放。
    if current_preview_group is None or not is_preview_currently_playable():
        return
    if on_play_video_callback:
        on_play_video_callback(current_preview_group)


# ============================================================
# Live View：把 LiveViewController 提供的最新畫面畫在 preview_canvas 上
# ============================================================
# 2026-10-13 改成透過 DA005ControlPanel.pyw 建立的「共用 LiveViewController」
# 取得畫面（跟 IQ Adjustment 同一個，預設 UVC，env=rtsp 時是 RTSP），不再
# 直接呼叫 api.get_latest_liveview_frame()。這支檔案不 import video_source.py，
# 不知道實際來源是 UVC 還是 RTSP。
# 2026-09-21 新增。刻意不去動 update_preview_image()/show_preview() 這些既有
# 縮圖函式——Live View 畫面直接疊在縮圖上面（用獨立的 'live_view_frame'
# tag），開始/結束只是「多蓋一層」跟「把那一層拿掉」，底下原本的縮圖
# image item 從頭到尾沒被刪除，結束時不需要重新下載/重畫縮圖，就會自動
# 恢復可見。

_live_view_active = False
_live_view_first_frame_seen = False
_live_view_on_first_frame_callback = None
_live_view_on_error_callback = None
_live_view_controller = None  # enter_live_view_mode() 傳進來的共用 LiveViewController
_live_view_owner = None       # 呼叫 controller 時用的 owner 名稱（首頁是 'home'）
live_view_image_ref = None  # PhotoImage 一定要保留參照，不然會被 GC 回收

LIVE_VIEW_TICK_INTERVAL_MS = 67  # 約 15fps，跟 video_source.py 的輸出 fps 對齊


def enter_live_view_mode(controller, owner, on_first_frame=None, on_error=None):
    """切換 preview_canvas 開始顯示 Live View 畫面：蓋掉（不刪除）Play Button
    疊圖，開始定期跟 controller 要最新一張畫面。已經在 Live View 中就直接
    return，不會重複啟動 tick 迴圈。

    controller / owner：共用的 LiveViewController 跟這個頁面的 owner 名稱，
    啟動/停止 stream 是呼叫端（DA005ControlPanel.pyw）的責任，這裡只負責顯示。

    on_first_frame：收到第一張完整畫面時呼叫一次（沒有參數），用來讓呼叫端
    （DA005ControlPanel.pyw）把按鈕從「Connecting...」切成「● Live View」。
    on_error(message)：controller 回報錯誤時呼叫一次（連線失敗、UVC 中斷）。
    兩個 callback 都是在 preview_canvas.after() 排出來的、main thread 上執行，
    可以放心直接更新 Tkinter widget。"""
    global _live_view_active, _live_view_first_frame_seen, _live_view_on_first_frame_callback
    global _live_view_on_error_callback, _live_view_controller, _live_view_owner
    if _live_view_active:
        return
    _live_view_active = True
    _live_view_first_frame_seen = False
    _live_view_on_first_frame_callback = on_first_frame
    _live_view_on_error_callback = on_error
    _live_view_controller = controller
    _live_view_owner = owner
    preview_canvas.delete('play_button_overlay')
    _live_view_tick()


def _live_view_tick():
    """main thread 定期執行：跟 controller 要一次最新狀態/畫面，有拿到就畫上去，
    永遠只顯示「目前拿到的這一張」，跟不上就跟不上，不會排隊累積。"""
    global _live_view_first_frame_seen, live_view_image_ref
    if not _live_view_active:
        return

    status, error_message, _source_label = _live_view_controller.get_status(_live_view_owner)
    if status == live_view_controller_module.STATUS_ERROR:
        # 交給呼叫端處理（停止 stream、恢復縮圖、按鈕回到未啟用），這裡不再排下一次 tick
        if _live_view_on_error_callback:
            _live_view_on_error_callback(error_message)
        return

    frame = _live_view_controller.get_latest_frame(_live_view_owner)
    if frame is not None:
        width, height, frame_bytes = frame
        # UVC 1280x720 / RTSP 640x360 跟 PREVIEW_SIZE（700x394）都是 16:9，resize() 不會變形。
        frame_image = Image.frombytes('RGB', (width, height), frame_bytes).resize(PREVIEW_SIZE)
        live_view_image_ref = ImageTk.PhotoImage(frame_image)
        preview_canvas.delete('live_view_frame')
        preview_canvas.create_image(0, 0, anchor='nw', image=live_view_image_ref, tags='live_view_frame')

        if not _live_view_first_frame_seen:
            _live_view_first_frame_seen = True
            if _live_view_on_first_frame_callback:
                _live_view_on_first_frame_callback()

    preview_canvas.after(LIVE_VIEW_TICK_INTERVAL_MS, _live_view_tick)


def exit_live_view_mode():
    """停止顯示 Live View 畫面，恢復縮圖跟 Play Button。可以安全重複呼叫。"""
    global _live_view_active
    if not _live_view_active:
        return
    _live_view_active = False
    preview_canvas.delete('live_view_frame')
    # 底下的縮圖 image item 從頭到尾沒被刪除，這裡不用重畫縮圖；Play Button
    # 要不要顯示、顯示成 enabled 還是 disabled，用「現在」的 DVR 狀態重新
    # 判斷一次（可能在 Live View 顯示期間就已經改變），比單純還原成進入
    # 前的樣式更正確。
    refresh_play_button_visibility()


# ============================================================
# Recording 按鈕：Standby <-> Scenic Recording 切換
# ============================================================

def on_recording_button_click():
    # 2026-08-14 暫時診斷 Log（使用者回報第二次點 Recording 無法停止錄影）：
    # 印出點擊當下的 current_dvr_state、即時 GET 到的 raw dvr_mode/
    # event_recording_setting、desired_dvr_mode，以及依 current_dvr_state
    # 判斷出的 action。如果這個 log 完全沒印出來，代表按鈕當下是
    # state='disabled'，Tkinter 直接沒有呼叫這個 function，點擊等於被吃掉。
    # 調查完後要移除，不要留在正式版本。
    try:
        raw_mode = api.get_dvr_mode()
        raw_dvr_mode_name = audi_pb2.DvrMode.DvrSystemState.Name(raw_mode.dvr_mode)
        raw_event_name = audi_pb2.DvrMode.DvrEvent.Name(raw_mode.event_recording_setting)
    except Exception as error:
        raw_dvr_mode_name = f'GET failed: {error}'
        raw_event_name = 'N/A'
    desired_name = audi_pb2.DvrMode.DvrSystemState.Name(api.get_desired_dvr_mode())
    action_name = 'STOP' if current_dvr_state == 'scenic_recording' else 'START'
    print(f'[Recording click] current_dvr_state={current_dvr_state}, '
          f'actual raw dvr_mode={raw_dvr_mode_name}, event_recording_setting={raw_event_name}, '
          f'desired_dvr_mode={desired_name}, action={action_name}, '
          f'recording_button state={recording_button["state"]}')

    if current_dvr_state == 'event_recording':
        status_label.config(text='Event recording is in progress. Cannot switch Recording.')
        return

    recording_button.config(state='disabled')
    # 2026-08-21 第三次重新設計：點擊當下不再需要建立/傳遞 generation——
    # current_preview_start_generation 只在 confirm 真正成功後才遞增（見
    # _start_scenic_worker()/_stop_scenic_worker() 內部），這裡兩個分支都
    # 不用帶參數。
    if current_dvr_state == 'scenic_recording':
        thread = threading.Thread(target=_stop_scenic_worker, daemon=True)
    else:  # standby（或初始狀態未知，當作可以開始錄影）
        thread = threading.Thread(target=_start_scenic_worker, daemon=True)
    thread.start()


# 2026-08-20 使用者發現的 race：如果 Action safety check 拿到
# BASELINE_RECOVERY_IN_PROGRESS（另一個 thread 正在做 recovery），不能
# 直接放行——必須等那個 recovery 結束、重新 GET 確認過才能送出這次真正的
# 目標。等待要有上限，避免對方真的卡住時這裡跟著永久等待。recovery 內部
# 最多會呼叫兩次 api.confirm_dvr_system_state()（先過渡到 STANDBY 一次、
# 再確認 WITHOUT_RINGBUFFER 一次，見 api.ensure_safe_ringbuffer_
# transition()/_put_dvr_system_state()），各自預設最多等
# api.DVR_MODE_CONFIRM_TIMEOUT_SECONDS 秒，這裡抓兩倍當作合理上限（沿用
# 既有的 mode transition timeout 概念，不是憑空另訂一個數字）。
ACTION_SAFETY_CHECK_RECOVERY_WAIT_TIMEOUT_SECONDS = api.DVR_MODE_CONFIRM_TIMEOUT_SECONDS * 2


def _wait_for_baseline_recovery_lock_to_free(timeout):
    """Action safety check 拿到 BASELINE_RECOVERY_IN_PROGRESS 時呼叫：
    blocking 等待 _dvr_baseline_recovery_lock 被釋放（代表持有它的那個
    recovery 已經結束，不論成功或失敗），最多等 timeout 秒。這裡只是借用
    lock 的「有沒有被持有」狀態當作等待訊號，拿到之後不做任何事、立刻
    釋放——真正做 recovery 的是原本持有 lock 的那個 thread，不是這裡；
    呼叫端接下來要自己重新 GET 一次，才知道 recovery 最後有沒有成功。

    回傳 True 表示等到了（lock 已釋放），False 表示等到 timeout 都還是
    被占用中。"""
    acquired = _dvr_baseline_recovery_lock.acquire(timeout=timeout)
    if acquired:
        _dvr_baseline_recovery_lock.release()
    return acquired


def _run_baseline_safety_check_before_action(action_label):
    """Recording/Event Click 送出真正的目標指令（SCENIC/USER_TRIGGERED）
    前，用這裡自己另外做的一次 GET 結果做一次便宜的 baseline safety
    check——這兩個 flow 原本 PUT 前都沒有先 GET 過，所以這裡沒有「沿用既有
    GET」可用，只能自己多打這一次（跟使用者原本的要求「如果已經有 GET
    就沿用」不衝突，是「本來沒有才新增」的情況）。

    2026-08-20 使用者回報真機行為：DVR System Restart 後 actual 可能停在
    WITH_RINGBUFFER，這時如果直接送 WITHOUT_RINGBUFFER+SCENIC/
    USER_TRIGGERED，第一次 command 只會完成 WITH->WITHOUT 的切換、
    event_recording_setting 被重置成 NO_EVENT，要使用者再按第二次才會真的
    變成 SCENIC/USER_TRIGGERED。這裡透過共用的
    ensure_without_ringbuffer_idle_baseline() 判斷、需要的話先完成
    recovery 並確認成功，讓呼叫端接著送出的目標指令一次就成功——使用者
    只需要點一次，不會知道中間發生過 recovery。

    2026-08-20 使用者再發現的 race：如果這個當下「另一個 thread 正在做
    recovery」（拿到 BASELINE_RECOVERY_IN_PROGRESS），不可以直接當作
    安全放行，否則這次的目標 PUT 會跟那個 recovery 的 PUT 同時競爭，
    重現「DVR 只完成 WITH->WITHOUT、event 被重置」的問題。這裡改成：
    等那個 recovery 結束（_wait_for_baseline_recovery_lock_to_free()）
    -> 重新 GET 一次 DvrMode -> 只有確認 actual 真的是
    WITHOUT_RINGBUFFER + NO_EVENT 才放行；等待逾時、重新 GET 失敗、或
    重新確認後仍不是這個 baseline，一律 abort（raise），不送出這次的
    目標指令。

    GET DvrMode 失敗時沒有依據判斷，直接放行（交給呼叫端接下來真正的 PUT
    自己的例外處理，不在這裡擋下使用者的操作）。只有下面這幾種情況才會
    raise，讓呼叫端統一走既有的失敗處理流程（顯示錯誤、re-enable 按鈕）
    ——跟原本 api.start_recording_without_ringbuffer() 失敗時的處理方式
    一致，不用另外分開處理：
    - BASELINE_RECOVERY_FAILED：這次自己偵測到需要 recovery，但沒有成功。
    - BASELINE_RECOVERY_IN_PROGRESS 且等待逾時。
    - BASELINE_RECOVERY_IN_PROGRESS 且等到之後重新 GET 失敗。
    - BASELINE_RECOVERY_IN_PROGRESS 且重新確認後 actual 仍不是
      WITHOUT_RINGBUFFER + NO_EVENT（代表那個 recovery 本身失敗了，或
      actual 又被其他原因改變）。"""
    try:
        current_mode = api.get_dvr_mode()
    except Exception as error:
        print(f'[Recording] {action_label} baseline safety check GET DvrMode 失敗，略過檢查，直接送出目標: {error}')
        return

    result = ensure_without_ringbuffer_idle_baseline(known_mode=current_mode)

    if result == BASELINE_RECOVERY_FAILED:
        raise Exception('Failed to recover WITHOUT_RINGBUFFER + NO_EVENT baseline before sending the requested command.')

    if result != BASELINE_RECOVERY_IN_PROGRESS:
        return  # BASELINE_RECOVERY_OK / RECOVERED / STANDBY 都可以直接繼續

    print(f'[Recording] {action_label} baseline safety check：偵測到另一個 thread 正在做 recovery，'
          f'等待它完成（最多 {ACTION_SAFETY_CHECK_RECOVERY_WAIT_TIMEOUT_SECONDS}s）再重新確認狀態，'
          f'這段期間不會送出這次的目標指令')
    recovery_finished = _wait_for_baseline_recovery_lock_to_free(ACTION_SAFETY_CHECK_RECOVERY_WAIT_TIMEOUT_SECONDS)
    if not recovery_finished:
        raise Exception('Timed out waiting for another DVR baseline recovery to finish.')

    try:
        rechecked_mode = api.get_dvr_mode()
    except Exception as error:
        raise Exception(f'Failed to re-check DVR state after baseline recovery: {error}')

    DvrSystemState = audi_pb2.DvrMode.DvrSystemState
    DvrEvent = audi_pb2.DvrMode.DvrEvent
    if (rechecked_mode.dvr_mode == DvrSystemState.DVR_SYSTEM_STATE_RECORD_WITHOUT_RINGBUFFER
            and rechecked_mode.event_recording_setting == DvrEvent.DVREVENT_NO_EVENT):
        print(f'[Recording] {action_label} baseline safety check：另一個 recovery 已完成，'
              f'重新確認 actual 是 WITHOUT_RINGBUFFER + NO_EVENT，繼續送出這次的目標指令')
        return

    raise Exception(
        f'DVR baseline recovery by another thread did not reach WITHOUT_RINGBUFFER + NO_EVENT '
        f'(dvr_mode={DvrSystemState.Name(rechecked_mode.dvr_mode)}, '
        f'event_recording_setting={DvrEvent.Name(rechecked_mode.event_recording_setting)}).')


def _start_scenic_worker():
    known_groupids_before = get_known_video_groupids()

    try:
        _run_baseline_safety_check_before_action('Recording Click')

        # 2026-08-14 使用者確認規則：Settings ON 後 DVR 正常 mode 就是
        # WITHOUT_RINGBUFFER，Recording 不應該再切去 WITH_RINGBUFFER。
        # Recording 按鈕啟用時 DVR 本來就已經在 WITHOUT_RINGBUFFER（不是
        # Dashcam Off），這裡只是把 event_recording_setting 從 NO_EVENT
        # 換成 SCENIC，dvr_mode 本身不需要切換，所以不用再呼叫
        # ensure_safe_ringbuffer_transition() 這種「先過渡到 STANDBY 再切
        # WITH_RINGBUFFER」的流程。上面的 baseline safety check 已經處理過
        # DVR 意外停在 WITH_RINGBUFFER 的情況，這裡不用重複判斷。
        api.start_recording_without_ringbuffer(audi_pb2.DvrMode.DvrEvent.DVREVENT_SCENIC)
    except Exception as error:
        _after_if_active(0, lambda captured_error=error: on_action_error('Failed to start recording.', captured_error))
        _after_if_active(0, lambda: recording_button.config(state='normal'))
        return

    confirmed_state = confirm_dvr_state('scenic_recording')
    if confirmed_state != 'scenic_recording':
        _after_if_active(0, lambda: on_action_error(
            'Failed to confirm recording start.', Exception(describe_confirm_failure(confirmed_state))))
        _after_if_active(0, lambda: recording_button.config(state='normal'))
        return

    # 2026-08-21 第三次重新設計：confirm 真正成功才是這次 transition 算數的
    # 時間點，這裡才遞增 generation（不是點擊當下）。
    my_generation = _bump_preview_generation()

    # 2026-08-14 使用者要求移除「Scenic Recording in progress」這種
    # Debug/Engineering 風格的狀態文字——Active Button Style（白底+粉紅
    # icon/text，見 apply_dvr_state_to_buttons()）已經足夠表示錄影中，
    # 這裡只清掉可能殘留的舊訊息（例如上一次的錯誤訊息），不再另外顯示
    # 這行文字。
    _after_if_active(0, lambda: apply_dvr_state_to_buttons(confirmed_state))
    _after_if_active(0, lambda: status_label.config(text=''))
    # 2026-08-14 使用者要求：PUT+GET 確認完成就要立刻讓 Recording Button
    # 恢復可點擊，不能等下面 refresh_preview_for_dvr_state() 才恢復——不然
    # 使用者立刻想再點一次停止，會被還沒 re-enable 的 disabled 按鈕吃掉
    # 點擊（Tkinter 的 Button 在 state='disabled' 時完全不會觸發 command）。
    # Preview 更新繼續在這個背景執行緒做，不影響按鈕已經可以再操作。
    _after_if_active(0, lambda: recording_button.config(state='normal'))

    # 2026-08-21 A/B 診斷：只有 Scenic Start 這裡傳 diagnostic_bypass_guard，
    # Stop（_stop_scenic_worker() 下面）不傳，不受影響。
    refresh_preview_for_dvr_state(confirmed_state, my_generation, known_groupids_before,
                                   diagnostic_bypass_guard=DIAGNOSTIC_BYPASS_START_GUARD)


def _stop_scenic_worker():
    """2026-08-14 使用者確認規則：Recording 是真正的 Toggle，dvr_mode 全程
    維持 WITHOUT_RINGBUFFER，停止錄影只是把 event_recording_setting 從
    SCENIC 改回 NO_EVENT——不切 STANDBY、不呼叫 restore_desired_dvr_mode()、
    不經過 WITH_RINGBUFFER。STANDBY 專門代表「Dashcam 關閉」，跟「停止
    錄影」是兩件不同的事，所以這裡不會、也不需要再判斷 is_standby_mode()。"""
    try:
        api.start_recording_without_ringbuffer(audi_pb2.DvrMode.DvrEvent.DVREVENT_NO_EVENT)
    except Exception as error:
        _after_if_active(0, lambda captured_error=error: on_action_error('Failed to stop recording.', captured_error))
        _after_if_active(0, lambda: recording_button.config(state='normal'))
        return

    confirmed_state = confirm_dvr_state('standby')
    if confirmed_state != 'standby':
        _after_if_active(0, lambda: on_action_error(
            'Failed to confirm recording stop.', Exception(describe_confirm_failure(confirmed_state))))
        _after_if_active(0, lambda: recording_button.config(state='normal'))
        return

    # 2026-08-24 第九次修正：要在自己的 _bump_preview_generation() 蓋掉
    # current_preview_start_generation_confirmed_at 之前，先讀出 Scenic
    # Start 那次留下的值，當作這次要 finalize 的錄影是什麼時候開始的
    # （見 fetch_finalized_group() 的說明）。
    recording_started_at = current_preview_start_generation_confirmed_at
    my_generation = _bump_preview_generation()

    _after_if_active(0, lambda: apply_dvr_state_to_buttons(confirmed_state))
    _after_if_active(0, lambda: status_label.config(text='Recording stopped'))
    _after_if_active(0, lambda: recording_button.config(state='normal'))

    refresh_preview_for_dvr_state(confirmed_state, my_generation, recording_started_at=recording_started_at)
    _report_gallery_item_count_after_completion()


# ============================================================
# Event 按鈕：觸發使用者事件 + 背景輪詢等待自動結束
# ============================================================

def on_event_button_click():
    """2026-08-14 使用者確認規則：Event 不做 Toggle OFF——即使目前已經是
    event_recording，再次點擊也要直接再送一次 USER_TRIGGERED 給 DVR，不能
    像 Recording 那樣擋下來。只有「正在送這一次 PUT / 等這一次 GET
    confirmation」的極短時間，靠 event_button 的 state='disabled' 暫時擋
    double-click，不是靠檢查 current_dvr_state 擋。

    2026-08-20 使用者真機回報「Event 結束後按鈕偶爾仍卡在綠色」，這裡
    明確回報：目前確實沒有任何機制禁止 Event active 期間再次點擊——
    上面這段規則本身就是刻意設計成「允許重複觸發」，如果使用者在第一次
    點擊的 worker 還沒跑完（甚至 Event 還在錄影中）時又點一次，會建立
    第二個 _start_event_worker，見下面的 token 診斷 log。"""
    global was_recording_before_event, _event_worker_token_counter

    was_recording_before_event = (current_dvr_state == 'scenic_recording')

    _event_worker_token_counter += 1
    token = _event_worker_token_counter
    button_state_before = str(event_button['bg'])
    _event_diag_log(f'click token={token} current_ui_state={current_dvr_state} '
                     f'was_recording_before_event={was_recording_before_event} '
                     f'button_bg_before={button_state_before} is_event_monitoring={is_event_monitoring}')

    event_button.config(state='disabled')
    known_groupids_before = get_known_video_groupids()
    # 2026-08-21 第三次重新設計：generation 改成在 _start_event_worker()
    # 自己的 confirm 成功之後才遞增（不是點擊當下），這裡不用再建立/傳遞。
    thread = threading.Thread(target=_start_event_worker, args=(token, known_groupids_before), daemon=True)
    thread.start()


def _start_event_worker(token, known_groupids_before):
    _preview_diag_log(f'worker={token} Event 觸發前，known_groupids_before 數量={len(known_groupids_before)}')
    _preview_diag_describe_group(f'worker={token} Event 觸發前的 current_preview_group', current_preview_group)
    _preview_diag_log_thumbnail(f'worker={token} Event 觸發前，目前 Canvas 上的縮圖', _last_applied_thumbnail_bytes)
    _event_diag_log(f'worker={token} baseline safety check（DVR System Restart 後 actual 可能停在 WITH_RINGBUFFER，先確認/恢復再送目標）')
    try:
        _run_baseline_safety_check_before_action(f'Event Click worker={token}')

        _event_diag_log(f'worker={token} PUT start_recording_without_ringbuffer(DVREVENT_USER_TRIGGERED)')
        # 2026-08-14 使用者確認規則：Event 不應該切去 WITH_RINGBUFFER，
        # 跟 Recording 一樣維持在 WITHOUT_RINGBUFFER，只換
        # event_recording_setting 成 USER_TRIGGERED。上面的 baseline
        # safety check 已經處理過 DVR 意外停在 WITH_RINGBUFFER 的情況，
        # 這裡不用重複判斷。
        api.start_recording_without_ringbuffer(audi_pb2.DvrMode.DvrEvent.DVREVENT_USER_TRIGGERED)
    except Exception as error:
        _event_diag_log(f'worker={token} PUT result=failed error={error}')
        _event_after_if_active(0, lambda captured_error=error: on_action_error(
            'Failed to trigger event.', captured_error), f'worker={token} on_action_error(PUT failed)')
        _event_after_if_active(0, lambda: event_button.config(state='normal'), f'worker={token} re-enable after PUT failure')
        return
    _event_diag_log(f'worker={token} PUT result=success')

    confirmed_state = confirm_dvr_state('event_recording')
    _event_diag_log(f'worker={token} confirm_dvr_state result={confirmed_state}')
    if confirmed_state != 'event_recording':
        _event_after_if_active(0, lambda: on_action_error(
            'Failed to confirm event trigger.', Exception(describe_confirm_failure(confirmed_state))),
            f'worker={token} on_action_error(confirm failed)')
        _event_after_if_active(0, lambda: event_button.config(state='normal'), f'worker={token} re-enable after confirm failure')
        return

    # 2026-08-21 第三次重新設計：confirm 真正成功才是這次 transition 算數
    # 的時間點，這裡才遞增 generation（不是點擊當下）。
    my_generation = _bump_preview_generation()

    # 2026-08-20 使用者要求：每一次成功 trigger（PUT 成功 + confirm 到
    # actual 真的是 event_recording）都要用「這次成功的時間點」重新往後
    # 延長 event_monitor_deadline，不是固定預算——這是這次修正的核心，
    # 讓使用者持續重新觸發時，還在跑的同一支 _event_polling_worker() 不會
    # 在 Event 仍然真的 active 時就先逾時放棄。用 max() 只會往後延、不會
    # 縮短（理論上每次呼叫的時間點本來就遞增，這裡是防禦性寫法）。
    global _event_monitor_deadline
    new_deadline = time() + EVENT_EXPECTED_DURATION_SECONDS + EVENT_TIMEOUT_SAFETY_MARGIN_SECONDS
    _event_monitor_deadline = max(_event_monitor_deadline or 0, new_deadline)
    _event_diag_log(f'worker={token} confirm 成功，event_monitor_deadline 延長至 {_event_monitor_deadline:.1f} '
                     f'(now={time():.1f}, expected_duration={EVENT_EXPECTED_DURATION_SECONDS}s, '
                     f'safety_margin={EVENT_TIMEOUT_SAFETY_MARGIN_SECONDS}s)')

    # 2026-08-14 使用者要求移除「Event recording in progress」這種
    # Debug/Engineering 風格的狀態文字，理由跟 Scenic 那邊一致，見
    # _start_scenic_worker() 的說明。
    button_state_before_apply = str(event_button['bg'])
    _event_after_if_active(0, lambda: apply_dvr_state_to_buttons(confirmed_state), f'worker={token} apply_dvr_state_to_buttons({confirmed_state})')
    _event_after_if_active(0, lambda: status_label.config(text=''), f'worker={token} clear status_label')
    _event_diag_log(f'worker={token} button_state_before={button_state_before_apply} '
                     f'button_state_after=(套用 apply_dvr_state_to_buttons({confirmed_state}) 排入 after()，實際顏色見上面 callback log)')
    # 2026-08-14 使用者要求：Event command 確認完成就要立刻讓 Event Button
    # 恢復可點擊、監控背景 worker 也要立刻啟動（如果還沒有的話），不能等
    # 下面找新 Group/縮圖（最多可能等 15 秒）——Event 允許在還沒結束時
    # 重複點擊、再送一次 trigger。start_event_completion_polling() 內部
    # 的 is_event_monitoring 旗標會擋掉重複開第二個監控 worker，所以這裡
    # 重複呼叫也不會疊加 thread。
    _event_after_if_active(0, lambda: event_button.config(state='normal'), f'worker={token} re-enable after confirm success')
    _event_after_if_active(0, lambda: start_event_completion_polling(token), f'worker={token} start_event_completion_polling')

    # 2026-08-21 A/B 診斷：只有 Event Start 這裡傳 diagnostic_bypass_guard，
    # Event Complete（_event_polling_worker() 裡的兩個呼叫）不傳，不受影響。
    refresh_preview_for_dvr_state(confirmed_state, my_generation, known_groupids_before,
                                   diagnostic_bypass_guard=DIAGNOSTIC_BYPASS_START_GUARD)


def start_event_completion_polling(token=None):
    global is_event_monitoring, _event_monitor_deadline
    _event_diag_log(f'polling start requested by worker={token} is_event_monitoring(before)={is_event_monitoring}')
    if is_event_monitoring:
        _event_diag_log(f'polling start SKIPPED（已經有一個監控在跑，不開第二個），requested by worker={token}')
        return  # 已經有一個監控在跑，不要重複開第二個
    is_event_monitoring = True
    if _event_monitor_deadline is None or time() >= _event_monitor_deadline:
        # 啟動監控時還沒有已知/仍有效的 deadline——通常是這個 session 沒
        # 經過 _start_event_worker() 自己觸發，而是切回 Recording 分頁時
        # 發現 DVR 已經在 Event 中（on_initial_dvr_state_ready()）。這種
        # 情況不知道實際是什麼時候觸發的，用「現在」當基準給一個安全的
        # 起始 deadline，之後如果真的再收到成功的 trigger，會再依那次
        # 真正的時間點往後延。
        _event_monitor_deadline = time() + EVENT_EXPECTED_DURATION_SECONDS + EVENT_TIMEOUT_SAFETY_MARGIN_SECONDS
        _event_diag_log(f'polling start：沒有已知/仍有效的 deadline，用目前時間初始化 deadline={_event_monitor_deadline:.1f}')
    api.clear_dvr_mode_change_notification()  # 清掉舊旗標，避免誤判成這次要等的變化
    _event_diag_log(f'polling start ACCEPTED，由 worker={token} 啟動，目前 event_monitor_deadline={_event_monitor_deadline:.1f}')
    thread = threading.Thread(target=_event_polling_worker, args=(token,), daemon=True)
    thread.start()


def _event_polling_worker(token=None):
    """監控 Event 有沒有結束，不是用固定間隔去 GET DvrMode 猜，而是等 KeepAlive
    通知「DvrMode 有變化」（api.wait_for_dvr_mode_change_notification()）才去
    GET 確認 event_recording_setting 是不是不再是 DVREVENT_USER_TRIGGERED
    （代表事件真的結束了：可能變成 SCENIC 接回原本錄影，也可能是從 Standby
    觸發、結束後變回 NO_EVENT/Standby）。EVENT_NOTIFY_WAIT_SECONDS 只是「沒收到
    通知也會自己檢查一次」的安全網，不是真正的輪詢間隔。
    這裡只負責 Recording/Event 按鈕顏色，完全不會去動縮圖。

    2026-08-20 使用者真機確認規則修正：迴圈的結束條件改成讀取「當下最新」
    的全域 _event_monitor_deadline（不是進迴圈時就固定住的一次性預算）
    ——使用者中途再次觸發 Event 時，_start_event_worker() 會把這個 deadline
    往後延，這裡不需要重新建立 worker，繼續用同一支迴圈、下一輪就會讀到
    展延後的新 deadline，正確地不提前逾時。"""
    global is_event_monitoring, _event_monitor_deadline
    iteration = 0

    while time() < _event_monitor_deadline:
        iteration += 1
        current_deadline = _event_monitor_deadline
        notified = api.wait_for_dvr_mode_change_notification(timeout=EVENT_NOTIFY_WAIT_SECONDS)
        _event_diag_log(f'polling worker={token} iteration={iteration} KeepAlive_notified={notified} '
                         f'now={time():.1f} event_monitor_deadline={current_deadline:.1f}')

        try:
            mode = api.get_dvr_mode()
        except Exception as error:
            _event_diag_log(f'polling worker={token} iteration={iteration} GET DvrMode 失敗: {error}，繼續等待下一次通知')
            print(f'[Event 監控] GET DvrMode 失敗，繼續等待下一次通知: {error}')
            continue

        raw_event_setting_name = audi_pb2.DvrMode.DvrEvent.Name(mode.event_recording_setting)
        still_user_triggered = mode.event_recording_setting == audi_pb2.DvrMode.DvrEvent.DVREVENT_USER_TRIGGERED
        _event_diag_log(f'polling worker={token} iteration={iteration} GET DvrMode result: '
                         f'dvr_mode={audi_pb2.DvrMode.DvrSystemState.Name(mode.dvr_mode)} '
                         f'event_recording_setting={raw_event_setting_name} still_user_triggered={still_user_triggered}')

        if not still_user_triggered:
            final_state = derive_dvr_state(mode)
            _event_diag_log(f'polling worker={token} actual_event_type=ended derived_final_state={final_state}，'
                             f'polling stop reason=completed')
            is_event_monitoring = False
            _event_monitor_deadline = None  # 這一輪監控結束，deadline 歸零，下一次觸發重新算
            # 2026-08-21 第三次重新設計：Event 確認結束本身就是一次新的
            # transition confirm，這裡自己遞增 generation（不再需要另外
            # 追蹤 _event_confirmed_generation——generation 現在只在
            # confirm 真正成功後才遞增，這裡讀到的一定是正確的）。
            # 2026-08-24 第九次修正：一樣要在自己 bump 之前，先讀出 Event
            # Start 那次留下的時間戳記。
            recording_started_at = current_preview_start_generation_confirmed_at
            my_generation = _bump_preview_generation()
            _event_after_if_active(0, lambda: on_event_completed(final_state),
                                    f'worker={token} on_event_completed({final_state})')
            refresh_preview_for_dvr_state(final_state, my_generation, recording_started_at=recording_started_at)
            _report_gallery_item_count_after_completion()
            return

    # 2026-08-20 使用者要求：deadline 到期不代表「一定」是真正的 timeout
    # ——deadline 只是我們自己抓的安全網，DVR 實際狀態才是準的。這裡在
    # 判定真正逾時之前，一定要再做最後一次 GET DvrMode 確認，讓 actual
    # DvrMode 當最終 UI state 的 authoritative source，不能只是「維持
    # 最後一次已知的 local UI state」就結束（那正是使用者這次回報的
    # 症狀成因）。
    _event_diag_log(f'polling worker={token} deadline={_event_monitor_deadline:.1f} 已到期，'
                     f'逾時判定前最後確認一次 GET DvrMode')
    is_event_monitoring = False
    try:
        final_mode = api.get_dvr_mode()
    except Exception as error:
        _event_monitor_deadline = None
        _event_diag_log(f'polling worker={token} 逾時最後確認 GET DvrMode 失敗: {error}，沒有可信資料可以套用')
        _event_after_if_active(0, lambda captured_error=error: on_event_polling_timeout(get_dvr_mode_error=captured_error),
                                f'worker={token} on_event_polling_timeout(GET failed)')
        return

    still_user_triggered = final_mode.event_recording_setting == audi_pb2.DvrMode.DvrEvent.DVREVENT_USER_TRIGGERED
    final_state = derive_dvr_state(final_mode)
    _event_monitor_deadline = None  # 不論最後確認結果是哪一種，這一輪監控都結束了

    if not still_user_triggered:
        # deadline 到期當下 Event 其實已經結束了，只是比 deadline 稍微晚
        # 一點才確認到——這不是「真正 timeout」，是正常結束，走跟迴圈裡
        # 偵測到結束完全一樣的處理，不顯示逾時錯誤訊息。
        _event_diag_log(f'polling worker={token} 逾時後最後確認：Event 其實已經結束（actual_event_type=ended '
                         f'derived_final_state={final_state}），視為正常結束，不顯示 timeout')
        recording_started_at = current_preview_start_generation_confirmed_at
        my_generation = _bump_preview_generation()
        _event_after_if_active(0, lambda: on_event_completed(final_state),
                                f'worker={token} on_event_completed({final_state}) after deadline re-check')
        refresh_preview_for_dvr_state(final_state, my_generation, recording_started_at=recording_started_at)
        _report_gallery_item_count_after_completion()
        return

    # 真正 timeout：deadline 已過，且最後一次 GET DvrMode 確認 Event 仍然
    # active。用這次 GET 到的 actual 狀態重新套用一次（用真實資料覆蓋，
    # 不是什麼都不做），並顯示逾時提示，讓使用者知道背景監控已經停止。
    _event_diag_log(f'polling worker={token} 真正 timeout：deadline 已過，且 GET DvrMode 確認 Event 仍然 active '
                     f'(actual_event_type=still_active derived_state={final_state})')
    _event_after_if_active(0, lambda: on_event_polling_timeout(actual_state=final_state),
                            f'worker={token} on_event_polling_timeout(still active)')


def on_event_completed(final_state):
    """2026-08-21 第三次重新設計：只負責按鈕顏色/文字，跟 Preview
    group/thumbnail/playable 完全無關——那些由 _event_polling_worker()
    另外呼叫的 refresh_preview_for_dvr_state() 統一處理。"""
    button_state_before = str(event_button['bg'])
    apply_dvr_state_to_buttons(final_state)
    _event_diag_log(f'on_event_completed button_state_before={button_state_before} '
                     f'button_state_after={str(event_button["bg"])} final_state={final_state}')
    status_label.config(text='Event recording completed')


def on_event_polling_timeout(get_dvr_mode_error=None, actual_state=None):
    """2026-08-20 使用者要求：只有「deadline 已過 AND 最後一次 GET DvrMode
    確認 Event 仍然 active」才算真正 timeout（見 _event_polling_worker()），
    這裡分兩種情況處理，都不會永久留下「什麼都不做、維持舊 local UI
    state」的錯誤畫面：
    - actual_state 有值：GET 成功、確認 Event 真的還在 active，用這次
      GET 到的真實狀態重新套用一次按鈕（用 actual DvrMode 當
      authoritative source，不是放著舊的不管），並提示使用者背景監控
      已經停止。
    - get_dvr_mode_error 有值：GET 本身失敗，沒有任何可信資料可以套用，
      不假裝畫面是對的，也不用「維持舊顏色」暗示狀態仍然可信，改用明確
      的錯誤文字告知使用者需要自己確認（unknown/error UX）。"""
    if get_dvr_mode_error is not None:
        _event_diag_log(f'on_event_polling_timeout：GET DvrMode failed error={get_dvr_mode_error}，'
                         f'button_state_unchanged={str(event_button["bg"])}（沒有可信資料，不假裝套用任何狀態）')
        status_label.config(text='Event status unknown: failed to verify DVR status. Please check manually.')
        return

    button_state_before = str(event_button['bg'])
    apply_dvr_state_to_buttons(actual_state)
    _event_diag_log(f'on_event_polling_timeout：真正 timeout，套用 actual_state={actual_state} '
                     f'button_state_before={button_state_before} button_state_after={str(event_button["bg"])}')
    status_label.config(text='Event monitoring timed out, but DVR is still recording an event.')


# ============================================================
# Photo 按鈕：完全獨立的動作，任何狀態下都可以拍照
# ============================================================

def on_photo_button_click():
    """2026-08-14 第四次調整：Photo 點擊時短暫顯示的效果原本是整格變紅色
    底（ACTIVE_RED_COLOR），跟 Recording/Event 是不同一套視覺，使用者
    明確要求改成跟 Recording/Event 共用同一套 Active 樣式
    （HCP3_ACTIVE_ROW_COLOR 底＋HCP3_ACTIVE_TEXT_COLOR 粉紅 icon/文字＋
    粗體），不可以再出現整格紅色。Photo 是瞬間操作，短暫顯示 Active
    樣式後就恢復 Idle（見 _reset_photo_button_after_success()）。"""
    photo_button.config(state='disabled', bg=HCP3_ACTIVE_ROW_COLOR, fg=HCP3_ACTIVE_TEXT_COLOR,
                        image=photo_icon_active_image, font=HCP3_ACTIVE_FONT)
    status_label.config(text='Taking photo...')
    thread = threading.Thread(target=_take_photo_worker, daemon=True)
    thread.start()


def _take_photo_worker():
    try:
        api.take_photo()
    except Exception as error:
        _after_if_active(0, lambda captured_error=error: on_photo_failed(captured_error))
        return
    # 成功：Active 樣式維持約 1 秒再變回 Idle，完全不動 Recording/Event
    # 按鈕跟縮圖。
    _after_if_active(0, lambda: status_label.config(text='Photo taken'))
    _after_if_active(1000, _reset_photo_button_after_success)
    _report_gallery_item_count_after_completion()


def _reset_photo_button_after_success():
    """拍照成功後延遲 1 秒的按鈕還原成 Idle 樣式（底色/文字顏色/圖示/
    字重都要一起換回來，不是只換底色）。這 1 秒空檔內畫面可能已經變成
    Dashcam Off（is_dashcam_off True，例如剛好切回這個頁面重新整理發現
    DVR 變成 STANDBY），這種情況按鈕要維持 disabled，不能被這個延遲回呼
    重新打開。"""
    photo_button.config(bg=HCP3_LIGHT_PANEL_COLOR, fg=HCP3_LIGHT_PANEL_TEXT_COLOR,
                        image=photo_icon_idle_image, font=HCP3_IDLE_FONT)
    if not is_dashcam_off:
        photo_button.config(state='normal')


def on_photo_failed(error):
    photo_button.config(bg=HCP3_LIGHT_PANEL_COLOR, fg=HCP3_LIGHT_PANEL_TEXT_COLOR,
                        image=photo_icon_idle_image, font=HCP3_IDLE_FONT, state='normal')
    status_label.config(text='Photo Failed')
    messagebox.showerror('Photo Failed', f'{error}')


# ============================================================
# 共用小工具
# ============================================================

def _log_get_recordings_call(caller_name, retry_loop_start_time, retry_count):
    """2026-08-14 暫時診斷 Log：使用者回報 Standby 後 GetRecordings() 疑似
    持續不停，這裡在每一個真的會呼叫 api.get_recordings() 的地方，呼叫前
    先印一行，記錄是哪個函式呼叫的、跑在哪個 thread、目前時間、這個函式
    自己的重試迴圈已經跑了多久、重試第幾次——重現一次「Scenic → Event →
    Event 自動結束 → Stop Recording → STANDBY」之後，就能直接看出 Standby
    後還在跑的 GetRecordings() 是哪個 caller。調查完後要移除，不要留在
    正式版本。"""
    current_thread = threading.current_thread()
    elapsed = time() - retry_loop_start_time
    print(f'[GetRecordings 診斷] caller={caller_name}, thread={current_thread.name}(id={current_thread.ident}), '
          f'timestamp={time():.3f}, elapsed={elapsed:.3f}s, retry_count={retry_count}')


def get_known_video_groupids():
    """記錄目前（這次錄影開始前）已經存在的影片 groupid 集合（排除照片），
    用來判斷 /dashcam/recordings 有沒有出現「新的」影片。取得失敗回傳空
    集合，不影響錄影/事件動作本身，只是之後可能等不到新縮圖而已。

    2026-08-21 第五次修正：真機 A/B 直接證實用 trigger_time 判斷「新不新」
    在 Start 這條路徑上會出問題——DVR 剛開始錄影時，第一筆出現在
    /dashcam/recordings 的是還沒填好的 stub 佔位資料（fileid=0、
    trigger_time=2023-01-01 00:00:00 這種假時間、srcUri=''），trigger_time
    遠比 baseline 舊，會被「trigger_time > baseline」這個條件直接排除；
    真正填好資料（trigger_time 正確、srcUri 有值）的那一筆什麼時候才會
    出現在清單裡完全不可控（真機觀測過 1 秒到 90+ 秒都有），trigger_time
    判斷因此常態性等到逾時，縮圖永遠沒機會更新。改回 v1.0 的做法：groupid
    是 DVR 端一開始就配好的（stub 出現時 groupid 已經是正確的新號碼），
    不受這個佔位延遲影響，只要 groupid 不在這裡記錄的已知集合裡，就代表
    是這次動作產生的新內容。"""
    try:
        _log_get_recordings_call('get_known_video_groupids', time(), 0)
        recordings = api.get_recordings()
        return {item.groupid for item in recordings if not api.is_photo_recording(item)}
    except Exception as error:
        print(f'[Recording] 取得目前影片清單失敗: {error}')
        return set()


# 2026-08-21 第四次修正：跟舊版 v1.0（真機已驗證正常的 golden reference）
# 逐函式 bytecode 比對後發現，上一輪把「等新 recording 登記進
# /dashcam/recordings」跟「等縮圖準備好」合併成同一段 5 秒重試，是這次
# 真機重現「縮圖不更新」的 regression 來源之一——v1.0 這兩件事原本是
# 兩段獨立、疊加的等待（wait_for_new_video_group() 最多 15 秒 + 獨立的
# fetch_ready_group_and_thumbnail() 縮圖重試最多 5 秒），這裡拆回兩支
# 函式恢復 v1.0 的等待預算與（KeepAlive 通知喚醒）機制。
NEW_VIDEO_WAIT_TIMEOUT_SECONDS = 15   # 恢復 v1.0 的預算：等新 recording 出現在 /dashcam/recordings
NEW_VIDEO_NOTIFY_WAIT_SECONDS = 3     # 沒等到 KeepAlive 通知也會自己檢查一次的安全網，不是真正的輪詢間隔

THUMBNAIL_RETRY_TIMEOUT_SECONDS = 5   # 恢復 v1.0 的預算：獨立等縮圖準備好，跟上面「等新 recording」分開計時、不合併
THUMBNAIL_RETRY_INTERVAL_SECONDS = 0.5


def _wait_for_new_group_of_type(expected_type, known_groupids_before, generation):
    """恢復 v1.0 wait_for_new_video_group() 的等待預算（15 秒）跟喚醒機制
    （api.wait_for_recordings_change_notification() KeepAlive 通知，不是
    固定間隔硬 polling）。只負責「group 找到了沒」，不管縮圖準不準備好
    ——縮圖的重試由 _wait_for_thumbnail_ready() 另外、獨立處理，不合併
    成同一段重試。

    2026-08-21 第五次修正（真機 A/B 直接證實，見 get_known_video_
    groupids() 的說明）：新不新的判斷改回比對 groupid 是否在 Start 前記錄
    的已知集合（known_groupids_before）裡，不再要求 trigger_time 比
    baseline 更新——DVR 剛建立的 stub 佔位資料 trigger_time 是假的
    2023-01-01，用 trigger_time 判斷會被這個假時間卡住，等到真正填好
    資料的那一筆出現（延遲不可控）才會成立，常態性逾時。groupid 在 stub
    出現時就已經是正確的新號碼，不受這個延遲影響。

    type 篩選繼續保留：避免 Scenic 錄影中觸發 Event 時，被同時產生的
    Scenic 新段落誤選（真機重現過的案例，這次驗證也證實 stub 本身的
    type 欄位已經是正確的，不受影響）。

    known_groupids_before=None 代表「不用比對新舊，找到符合 type 的最新
    一筆就算數」——Event 確認結束後自動接回 Scenic 這種情況會這樣呼叫
    （這條路徑這輪不動，維持原行為：Scenic 本來就持續在錄，不是這次
    transition 才產生新內容，不需要等待，通常第一次 GET 就會成功）。

    途中發現 generation 已經過期就提早放棄，不用等到逾時（這是沿用既有
    的保護，不是新增）。"""
    start_time = time()
    while time() - start_time < NEW_VIDEO_WAIT_TIMEOUT_SECONDS:
        if generation is not None and generation != current_preview_start_generation:
            return None

        api.wait_for_recordings_change_notification(timeout=NEW_VIDEO_NOTIFY_WAIT_SECONDS)

        try:
            recordings = api.get_recordings()
        except Exception as error:
            print(f'[Recording] 等待新錄影項目時取得清單失敗: {error}')
            continue

        type_filtered_recordings = [item for item in recordings if item.type_of_recording == expected_type]
        groups = api.group_recordings_by_groupid(type_filtered_recordings)  # 已經是最新排最前面
        for group in groups:
            representative_item = api.get_group_representative(group)
            if known_groupids_before is None or representative_item.groupid not in known_groupids_before:
                return group

    return None


def _wait_for_thumbnail_ready(group, generation):
    """恢復 v1.0 fetch_ready_group_and_thumbnail() 的獨立縮圖等待預算
    （5 秒），跟 _wait_for_new_group_of_type() 分開計時、分開重試——group
    本身已經確定是這次真正的新內容，這裡只反覆重新 GET+比對+下載縮圖，
    直到縮圖真的下載成功或逾時（實測過剛出現的項目 thumbnailUri 可能還是
    空字串，一定要重新 GET 拿最新版本才有用，不能對舊資料重試下載）。

    2026-08-21 第五次修正（真機 A/B 直接證實）：改回只比對 groupid，不再
    要求 trigger_time 也要跟第一次看到的那一刻完全相同——真機證實同一個
    groupid 的 trigger_time 會從 stub 階段的假時間（2023-01-01）演變成
    真正填好的時間，如果比對條件要求 trigger_time 完全相同，會在資料
    真正填好、trigger_time 改變的那一刻起，永遠比對不到「這是同一筆」，
    反而在這裡逾時。groupid collision（真機證實過的、不相關影片共用
    groupid 的情況）由 api.group_recordings_by_groupid() 既有的
    groupid→trigger_time 排序→sequenceid 嚴格遞增二次分組機制負責，這裡
    不重複做第二層保護。

    回傳 (fresh_group, thumbnail_bytes)；都失敗回傳 (None, None)，呼叫端
    要 fallback 回原本已經找到的 group（跟 v1.0 行為一致：縮圖抓不到不是
    整個放棄套用，是退回用還沒重新確認過的那個 group，縮圖交給
    update_preview_image() 既有的 Fallback 畫面處理）。"""
    representative_item = api.get_group_representative(group)
    target_groupid = representative_item.groupid

    start_time = time()
    while True:
        if generation is not None and generation != current_preview_start_generation:
            return None, None
        try:
            recordings = api.get_recordings()
            groups = api.group_recordings_by_groupid(recordings)
            matching_group = next(
                (g for g in groups if api.get_group_representative(g).groupid == target_groupid),
                None)
            if matching_group is not None:
                thumbnail_bytes = safe_download_thumbnail(api.get_group_representative(matching_group))
                if thumbnail_bytes is not None:
                    return matching_group, thumbnail_bytes
        except Exception as error:
            print(f'[Recording] 重新取得新錄影項目失敗: {error}')

        if time() - start_time >= THUMBNAIL_RETRY_TIMEOUT_SECONDS:
            return None, None
        sleep(THUMBNAIL_RETRY_INTERVAL_SECONDS)


DIAGNOSTIC_BYPASS_START_GUARD = False   # 手動切 True = B 版本；False = A 版本（目前正式行為）


def _epoch_seconds_from_utc_datetime(value):
    """把 protobuf Timestamp.ToDatetime()（視為 UTC 的 naive datetime）
    轉成 POSIX epoch 秒數，跟 time.time() 的值域一致，方便直接傳給
    fetch_finalized_group() 的 recording_started_at 參數。"""
    return (value - datetime(1970, 1, 1)).total_seconds()


def _select_latest_video_and_download_thumbnail():
    """選目前 /dashcam/recordings 裡 trigger_time 最新的影片（不限
    type），做 finalized refresh 後下載縮圖——startup/reconnect 用，也給
    Stop/Event 確認結束時「目前還沒有任何 Preview 顯示過」這個邊界情況
    共用（見 _prepare_finalize_in_place() 呼叫端的說明：這種邊界情況沒有
    current_preview_group 可以 finalize，只能退回這個做法，確保 Play
    Button 至少會出現一次，不留下永遠沒有縮圖的邊界情況）。

    2026-08-24 第八次修正：playable 不能無條件回傳 True——這裡內部其實
    有呼叫 fetch_finalized_group() 確認 srcUri 填好與否，但呼叫端原本完全
    沒有使用這個確認結果，一律當作可播放。真機證實這會在切到 Gallery
    分頁再切回來時，把明明還沒填好 srcUri 的影片誤判成可播放（點下去只會
    播放失敗），跟 _prepare_finalize_in_place() 已經修過的問題是同一種
    bug，這裡補齊同一個原則：不是真的 finalize 成功就不能算 playable。

    回傳 (group, thumbnail_bytes, playable)；GET 失敗或找不到任何影片
    回傳 (None, None, False)。"""
    try:
        recordings = api.get_recordings()
    except Exception as error:
        print(f'[Recording] refresh_preview_for_dvr_state(standby) 失敗: {error}')
        return None, None, False
    group = api.get_latest_video_group_by_trigger_time(recordings)
    if group is None:
        return None, None, False
    representative_item = api.get_group_representative(group)
    # 2026-08-24 第九次修正：這裡的 trigger_time 是剛 GET 到的、真正的即時
    # 資料（不是凍結的 stub 快照，get_latest_video_group_by_trigger_time()
    # 本身就會排除 stub），直接轉成 epoch 秒數當 recording_started_at，
    # 沿用跟 Stop/Event Complete 一致的 identity 規則（見 fetch_finalized_
    # group() 的說明），一樣能排除同 groupid/type 下更早的歷史 collision。
    finalized_group = fetch_finalized_group(
        representative_item.groupid,
        representative_item.type_of_recording,
        _epoch_seconds_from_utc_datetime(representative_item.interval.start_time.ToDatetime()))
    playable = finalized_group is not None
    group = finalized_group if finalized_group is not None else group
    thumbnail_bytes = safe_download_thumbnail(api.get_group_representative(group))
    return group, thumbnail_bytes, playable


def _prepare_finalize_in_place(recording_started_at):
    """2026-08-21 第六次修正：Stop / Event 確認結束共用。真機證實這兩個
    入口原本會重新選 latest video（不限 type）並無條件重新下載縮圖，就算
    選到的其實是同一個 group 也一樣——使用者要求的正確行為是：錄影中已經
    顯示正確 Preview 後，Stop/Event Complete 不應該重新選、也不應該重新
    下載縮圖，只需要針對「目前 Canvas 上顯示的這個 group」做 finalized
    refresh（確認 srcUri 填好），維持縮圖畫面不變。

    2026-08-24 第九次修正：真機直接證實，current_preview_group 這時候
    擷取到的 trigger_time 可能還是 DVR stub 階段的假時間（2023-01-01），
    這支演變成正式資料時 trigger_time 會變成真實時間——用凍結的那個值
    當永久比對條件，不管重試多久都對不到。改成用 groupid + type_of_
    recording + recording_started_at（這次 Start/Event 確認成功的時間，
    由呼叫端傳入，見 current_preview_start_generation_confirmed_at 的
    說明）交給 fetch_finalized_group()，詳見該函式的比對規則。

    2026-08-21 第七次修正：playable 不能無條件回傳 True——
    fetch_finalized_group() 逾時代表確認過至少有一段 srcUri 還是空的，
    退回的 current_preview_group 事實上還不能播放，這裡要老實回傳 False，
    讓 Play Button 維持 disabled，不能假裝可用（點下去只會播放失敗）。

    呼叫前呼叫端要先確認 current_preview_group 不是 None（見呼叫端的
    邊界情況處理）。回傳 (group, None, playable)：thumbnail_bytes 固定是
    None，呼叫端要用這個訊號決定「不要呼叫 update_preview_image()/動
    Canvas」，只更新 group 參照跟 playable（見
    _finalize_preview_group_in_place()）。"""
    representative_item = api.get_group_representative(current_preview_group)
    finalized_group = fetch_finalized_group(
        representative_item.groupid, representative_item.type_of_recording, recording_started_at)
    group = finalized_group if finalized_group is not None else current_preview_group
    playable = finalized_group is not None
    return group, None, playable


def _finalize_preview_group_in_place(group, playable):
    """Stop / Event 確認結束用：只更新 current_preview_group 參照跟
    current_preview_playable，完全不呼叫 update_preview_image()、不碰
    preview_image_ref/Canvas——維持使用者已經在看的那張縮圖不變。"""
    global current_preview_group, current_preview_playable
    current_preview_group = group
    current_preview_playable = playable
    refresh_play_button_visibility()


BACKGROUND_FINALIZE_RETRY_TIMEOUT_SECONDS = 120  # 涵蓋真機觀察過的最差情況（finalize 曾經超過 90 秒才完成）


def _retry_finalize_until_playable(group, generation, recording_started_at):
    """2026-08-24 第八次修正：真機證實 Stop/Event Complete 第一次
    _prepare_finalize_in_place() 逾時（10 秒）後，Play Button 會永遠卡在
    disabled——沒有任何背景機制會再回頭確認 srcUri 是不是後來填好了，
    唯一能讓畫面更新的只剩「使用者手動切頁」這個完全不相關、而且原本也
    誤判 playable 的路徑（已在 _select_latest_video_and_download_thumbnail()
    修正）。

    這支函式只在 _prepare_finalize_in_place() 第一次回傳 playable=False
    時才會被呼叫（呼叫端已經先立即套用過一次 disabled 狀態，不受這裡影響），
    針對同一個 group（不重新選 latest、不重新下載縮圖）持續呼叫既有的
    fetch_finalized_group()，直到：
    1. 抓到已經 finalize 的版本 -> 套用 playable=True，Play Button 自動 enable
    2. generation 已經過期（使用者又做了別的操作，例如再 Start 一次）-> 放棄
    3. 累計超過 BACKGROUND_FINALIZE_RETRY_TIMEOUT_SECONDS 都沒成功 -> 放棄，
       維持目前的 disabled 狀態不變

    fetch_finalized_group() 本身每次呼叫已經有自己的 10 秒重試預算，這裡
    只是在它逾時之後決定要不要再給下一輪機會，不是新增一層無限迴圈——
    有整體時間上限，也有 generation 擋停損。

    2026-08-24 第九次修正：改用 groupid + type_of_recording +
    recording_started_at 當識別，不再靠這裡凍結的 trigger_time（見
    fetch_finalized_group() 的說明），recording_started_at 由呼叫端
    （refresh_preview_for_dvr_state()）原封不動往下傳，這裡不需要知道
    它是怎麼來的。"""
    representative_item = api.get_group_representative(group)
    start_time = time()
    while time() - start_time < BACKGROUND_FINALIZE_RETRY_TIMEOUT_SECONDS:
        if generation != current_preview_start_generation:
            return
        finalized_group = fetch_finalized_group(
            representative_item.groupid, representative_item.type_of_recording, recording_started_at)
        if finalized_group is not None:
            def apply_playable_now():
                if generation == current_preview_start_generation:
                    _finalize_preview_group_in_place(finalized_group, True)
            _after_if_active(0, apply_playable_now)
            return


def refresh_preview_for_dvr_state(target_state, generation=None, known_groupids_before=None,
                                   diagnostic_bypass_guard=False, recording_started_at=None):
    """2026-08-21 第三次重新設計：Preview 更新統一收斂到這一支，由已確認
    的 DVR state transition 呼叫（一定要在背景執行緒呼叫，GET/縮圖下載/
    重試都不能卡住 Tk 主執行緒）。

    2026-08-21 第四次修正：跟真機已驗證正常的舊版 v1.0 逐函式比對後發現，
    Scenic/Event 分支原本把「等新 recording 出現」跟「等縮圖準備好」合併
    成同一段 5 秒重試，是真機重現「縮圖不更新」的 regression 來源之一。
    這裡改回兩段獨立、疊加的等待（_wait_for_new_group_of_type() 15 秒 +
    _wait_for_thumbnail_ready() 5 秒），恢復 v1.0 的等待預算，詳見這兩支
    函式的說明。standby 分支這輪不動。

    2026-08-21 第五次修正：真機 A/B 直接證實，Scenic/Event Start 判斷
    「新不新」不能用 trigger_time（DVR 剛建立的 stub 佔位資料 trigger_time
    是假的 2023-01-01，會被 trigger_time 比較條件卡住），改回比對
    known_groupids_before（Start 前記錄的已知 groupid 集合，見
    get_known_video_groupids()），詳見 _wait_for_new_group_of_type() 的
    說明。

    2026-08-21 第六次修正：真機證實 Stop/Event 確認結束（呼叫時
    generation 一定是實際數字）原本會重新選 latest video + 無條件重新
    下載縮圖，即使選到的是同一個 group 也一樣。改成不重新選、不重新
    下載，只針對目前顯示的 current_preview_group 做 finalized refresh
    （見 _prepare_finalize_in_place()/_finalize_preview_group_in_place()
    的說明）。這個判斷不需要新旗標：Stop/Event Complete 呼叫時
    generation 一定不是 None，且對 scenic_recording 分支而言
    known_groupids_before 一定是 None（Scenic Start 這輪已經 Freeze，
    永遠會傳實際集合）——這兩個既有參數的組合天然就能跟「真正的
    Start」「startup/reconnect」（generation 是 None）分開，不會互相
    誤判。Scenic/Event Start、startup/reconnect 這輪完全不動。

    generation（Scenic Start/Stop confirm、Event Start confirm、Event
    確認結束 4 個真正的 transition 呼叫端用）：
    - 呼叫前先檢查一次是否已經過期（避免已知過期的 worker 還浪費一次
      DVR 查詢）。
    - 找到 group/縮圖之後、排入 .after() 前再檢查一次——期間可能已經有
      更新的 transition 確認成功，舊結果不能覆蓋。
    - 2026-08-21 Codex review 抓到：上面兩次檢查都在背景執行緒做，跟
      「排入 .after()」到「Tk 主執行緒真正執行 callback」這段時間不是
      原子的——如果剛好在這個空檔又有更新的 transition confirm 成功，
      這裡排進去的 callback 還是會照樣執行，蓋掉更新的結果。改成
      _apply_preview_update() 前面再包一層 guard，在 callback 真正
      執行的那一刻（Tk 主執行緒上）用同一套規則再確認一次，跟前兩次
      檢查形成三層保護，關掉這個空檔。
    這幾次檢查只保護「要不要套用」，完全不影響「要不要去 GET/抓縮圖」——
    這輪沒有新增任何檢查層或新的全域旗標，跟上一輪維持一樣。

    generation=None（startup/reconnect 專用）：不做 generation gating，
    改成套用前確認 current_dvr_state == target_state 仍然成立——這段
    期間如果 DVR state 已經變了，代表發生了一次真正的 transition，那次
    自己的結果才該套用，這份 startup/reconnect 結果直接丟棄。

    2026-08-21 A/B 診斷（暫時，不是正式修法，確認結果後要把
    diagnostic_bypass_guard 參數、DIAGNOSTIC_BYPASS_START_GUARD 常數跟下面
    3 處的 log/bypass 判斷、以及 _start_scenic_worker()/_start_event_worker()
    呼叫端多傳的 diagnostic_bypass_guard=DIAGNOSTIC_BYPASS_START_GUARD 一起
    刪掉）：只有 Scenic Start / Event Start 兩個呼叫端會傳
    diagnostic_bypass_guard，Stop/Event Complete/standby/startup/reconnect
    都不會傳（保持預設 False），完全不受影響。3 個 guard 不論
    diagnostic_bypass_guard 是不是 True 都會 print log 顯示「這裡本來會不會
    擋下」，diagnostic_bypass_guard=True 時才會跳過真正的 return，讓找到
    group+thumbnail 後直接排入套用（v1.0 行為）。"""
    if generation is not None and generation != current_preview_start_generation:
        print(f'[Recording][A/B診斷] guard1(進入前) target_state={target_state} '
              f'generation={generation} != current={current_preview_start_generation}'
              + ('（diagnostic_bypass_guard=True，繼續往下執行）' if diagnostic_bypass_guard
                 else '（return，不繼續）'))
        if not diagnostic_bypass_guard:
            return

    if target_state == 'scenic_recording' and generation is not None and known_groupids_before is None:
        # 第六次修正：Event 確認結束、自動接回 Scenic（generation 是實際
        # transition，但沒有 known_groupids_before——只有這個組合會落到
        # 這裡，真正的 Scenic Start 一定有 known_groupids_before）。不要
        # 重新選 latest Scenic segment、不要重新下載縮圖，只針對目前顯示
        # 的 group 做 finalized refresh。
        if current_preview_group is not None:
            group, thumbnail_bytes, playable_to_apply = _prepare_finalize_in_place(recording_started_at)
            finalize_in_place = True
        else:
            # 邊界情況：目前還沒有任何 Preview 顯示過（理論上不該發生在
            # 這個呼叫端，但防禦性處理，避免 Play Button 永遠卡住不出現）。
            group, thumbnail_bytes, playable_to_apply = _select_latest_video_and_download_thumbnail()
            if group is None:
                return
            finalize_in_place = False
    elif target_state == 'scenic_recording':
        expected_type = audi_pb2.TypeOfRecording.TYPE_OF_RECORDING_STREETVIDEO_USER_SCENIC_TRIGGERED
        new_group = _wait_for_new_group_of_type(expected_type, known_groupids_before, generation)
        if new_group is None:
            print('[Recording] 等不到新的 Scenic 錄影項目出現在 /dashcam/recordings')
            return
        fresh_group, thumbnail_bytes = _wait_for_thumbnail_ready(new_group, generation)
        group = fresh_group if fresh_group is not None else new_group
        playable_to_apply = False
        finalize_in_place = False
    elif target_state == 'event_recording':
        expected_type = audi_pb2.TypeOfRecording.TYPE_OF_RECORDING_STREETVIDEO_USER_EVENT_TRIGGERED
        new_group = _wait_for_new_group_of_type(expected_type, known_groupids_before, generation)
        if new_group is None:
            print('[Recording] 等不到新的 Event 錄影項目出現在 /dashcam/recordings')
            return
        fresh_group, thumbnail_bytes = _wait_for_thumbnail_ready(new_group, generation)
        group = fresh_group if fresh_group is not None else new_group
        playable_to_apply = False
        finalize_in_place = False
    elif generation is not None and current_preview_group is not None:
        # standby，第六次修正：Stop / Event 確認結束（generation 是實際
        # transition）。理由同上，不重新選 latest video、不重新下載縮圖。
        group, thumbnail_bytes, playable_to_apply = _prepare_finalize_in_place(recording_started_at)
        finalize_in_place = True
    else:  # standby，startup/reconnect，或 Stop/Event Complete 時目前還沒
          # 有任何 Preview 顯示過的邊界情況（generation is not None 但
          # current_preview_group is None）——這輪不動，維持原本「選
          # latest + 下載縮圖」邏輯。
        group, thumbnail_bytes, playable_to_apply = _select_latest_video_and_download_thumbnail()
        if group is None:
            return
        finalize_in_place = False

    if generation is not None:
        if generation != current_preview_start_generation:
            print(f'[Recording][A/B診斷] guard2(排程前) target_state={target_state} '
                  f'generation={generation} != current={current_preview_start_generation}'
                  + ('（diagnostic_bypass_guard=True，繼續排程）' if diagnostic_bypass_guard
                     else '（return，不排程）'))
            if not diagnostic_bypass_guard:
                return
    elif current_dvr_state != target_state:
        print(f'[Recording][A/B診斷] guard2(排程前) target_state={target_state} '
              f'current_dvr_state={current_dvr_state}'
              + ('（diagnostic_bypass_guard=True，繼續排程）' if diagnostic_bypass_guard
                 else '（return，不排程）'))
        if not diagnostic_bypass_guard:
            return

    def guarded_apply():
        # 跟上面兩次檢查同一套規則，但這裡是 callback 真正執行的那一刻
        # （Tk 主執行緒上）才判斷，關掉「排入 .after() 到真正執行」這段
        # 空檔的過期風險。
        if generation is not None:
            if generation != current_preview_start_generation:
                print(f'[Recording][A/B診斷] guard3(Tk callback 執行時) target_state={target_state} '
                      f'generation={generation} != current={current_preview_start_generation}'
                      + ('（diagnostic_bypass_guard=True，仍然套用）' if diagnostic_bypass_guard
                         else '（return，不套用）'))
                if not diagnostic_bypass_guard:
                    return
        elif current_dvr_state != target_state:
            print(f'[Recording][A/B診斷] guard3(Tk callback 執行時) target_state={target_state} '
                  f'current_dvr_state={current_dvr_state}'
                  + ('（diagnostic_bypass_guard=True，仍然套用）' if diagnostic_bypass_guard
                     else '（return，不套用）'))
            if not diagnostic_bypass_guard:
                return
        if finalize_in_place:
            _finalize_preview_group_in_place(group, playable_to_apply)
        else:
            _apply_preview_update(group, thumbnail_bytes, playable_to_apply)

    _after_if_active(0, guarded_apply)

    # 2026-08-24 第八次修正：Stop/Event Complete 的 finalize 第一次逾時
    # （playable_to_apply=False）時，上面 guarded_apply() 已經立即套用過
    # disabled 狀態——這裡另外開一條背景 thread 繼續確認同一個 group，
    # 不影響已經顯示的畫面，也不會卡住這裡的呼叫端（_stop_scenic_worker()/
    # _event_polling_worker() 還有自己後續要做的事，例如回報 Gallery
    # 項目數），成功時才會再排一次 .after() 把 Play Button 自動 enable。
    # 只有 finalize_in_place 家族（Stop/Event Complete）會落到這裡，
    # Scenic/Event Start 兩個分支 finalize_in_place 永遠是 False，不受影響。
    if finalize_in_place and not playable_to_apply:
        threading.Thread(
            target=_retry_finalize_until_playable, args=(group, generation, recording_started_at), daemon=True
        ).start()


def safe_download_thumbnail(representative_item):
    """Recording Page 是畫面上唯一一張、佔比很大的大縮圖，要用高解析度
    （THUMBNAIL_RESOLUTION_RECORDING），不是 Gallery List 用的小解析度。"""
    try:
        return api.download_preview_image(representative_item, resolution=api.THUMBNAIL_RESOLUTION_RECORDING)
    except Exception as error:
        print(f'[Recording] 縮圖下載失敗 (fileid={representative_item.fileid}): {error}')
        return None


FINALIZED_GROUP_RETRY_TIMEOUT_SECONDS = 10   # 多段的 group 每一段都要等填好，留寬一點時間
FINALIZED_GROUP_RETRY_INTERVAL_SECONDS = 0.5


FINALIZE_IDENTITY_GRACE_SECONDS = 5   # recording_started_at 到 DVR 真正記錄下 trigger_time 之間的正常延遲容許值


def fetch_finalized_group(groupid, expected_type_of_recording, recording_started_at):
    """停止 Scenic / Event 完成時用這個：重新 GET 一次這個 group 目前的樣子，
    確認「這一組裡的每一段」srcUri 都已經填好（代表全部段落都真的定稿了，
    可以拿來播放），才回傳。

    注意：一定要檢查「每一段」，不能只看代表段（第一段）——實測過
    （2026-08-10）多段的 group（Event 錄影中途 ring buffer 換檔），第一段
    srcUri 先填好了，但後面的段落（例如錄到一半才切出來的那一段）srcUri
    還是空的，如果只檢查第一段就會提早回傳，Player 播到那一段時一樣會失敗。

    實測過 DvrMode 確認回到 standby/scenic 之後，/dashcam/recordings 有時還
    會慢一點才反映定稿後的 srcUri，所以這裡一樣要重試，不是查一次沒有就放棄。
    在 FINALIZED_GROUP_RETRY_TIMEOUT_SECONDS 秒內都等不到就回傳 None。

    2026-08-24 第九次修正（真機直接證實，取代 2026-08-21 那版）：不能再用
    「呼叫端擷取當下的 trigger_time」跟現在重新 GET 到的資料做完全相等
    比對——DVR 會先建立 trigger_time 是假的 2023-01-01 的 stub 佔位資料，
    這個 stub 演變成正式資料時 trigger_time 會變成真實時間，凍結的那個
    值只要跟真實值不一樣，不管重試多久都永遠比對不到，跟等待時間長短
    無關。

    改成用 (groupid, type_of_recording, recording_started_at) 當識別：
    - groupid + type_of_recording：跟原本一樣，先用既有的
      group_recordings_by_groupid() collision 分組機制（sequenceid 嚴格
      遞增二次分組）篩出候選子群組，這裡完全不動。
    - 每段 srcUri 都填好：跟原本一樣，這是「真的定稿了」的判斷依據，這裡
      也不動。
    - recording_started_at（呼叫端在這次 Start/Event 確認成功時記錄下的
      時間，見 current_preview_start_generation_confirmed_at 的說明）：
      要求候選的 trigger_time 不早於 recording_started_at -
      FINALIZE_IDENTITY_GRACE_SECONDS，這條件對**所有**候選一律套用，
      不是只在偵測到多個候選時才套用——如果只套用在「多個候選」的情況，
      當這次錄影本身還停在 stub、剛好又有一支同 groupid/type 的歷史舊
      影片已經定稿時，篩選後只會剩下那支舊影片一個候選，沒有這條件會被
      誤選成「已經定稿」。這條件本身也自然排除 stub（stub 的 trigger_time
      2023-01-01 一定早於 recording_started_at），不需要再另外寫一個
      「排除 stub sentinel」的特判。
    - 篩選後如果還有多個候選（同 groupid/type，且 trigger_time 都不早於
      recording_started_at，理論上極少見），取 trigger_time 最新的那個。

    recording_started_at 允許是 None（防禦性處理，理論上不該發生在
    Stop/Event Complete 呼叫端）：只靠 groupid + type_of_recording +
    srcUri 完整這三個條件篩選，不做時間下限篩選。"""
    start_time = time()
    retry_count = 0
    recording_started_at_threshold = (
        datetime.utcfromtimestamp(recording_started_at - FINALIZE_IDENTITY_GRACE_SECONDS)
        if recording_started_at is not None else None)
    while True:
        try:
            retry_count += 1
            _log_get_recordings_call('fetch_finalized_group', start_time, retry_count)
            recordings = api.get_recordings()
            groups = api.group_recordings_by_groupid(recordings)
            candidates = [
                group for group in groups
                if api.get_group_representative(group).groupid == groupid
                and api.get_group_representative(group).type_of_recording == expected_type_of_recording
                and all(item.srcUri.strip() for item in group)]
            if recording_started_at_threshold is not None:
                candidates = [
                    group for group in candidates
                    if api.get_group_representative(group).interval.start_time.ToDatetime()
                    >= recording_started_at_threshold]
            if candidates:
                newest_candidate = max(
                    candidates,
                    key=lambda group: api.get_group_representative(group).interval.start_time.ToDatetime())
                return newest_candidate
        except Exception as error:
            print(f'[Recording] 取得定稿後的錄影資料失敗: {error}')

        if time() - start_time >= FINALIZED_GROUP_RETRY_TIMEOUT_SECONDS:
            return None
        sleep(FINALIZED_GROUP_RETRY_INTERVAL_SECONDS)


def on_action_error(title, error):
    status_label.config(text=title)
    messagebox.showerror(title, f'{error}')
