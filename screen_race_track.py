"""
screen_race_track.py — Race Track Simulation 畫面

2026-09-22 新增。把原本 DA005 Protobuf Sender 那種「逐欄位對應 protobuf
訊息」的 TrackInformation 操作介面，改成比較適合客戶操作的表單：使用者
不用知道 protobuf 欄位名稱，也不用自己組 sectorStartTime 的 seconds/nanos。

2026-10-05 改成「interface demonstration」版本：
- 這一頁不再連接裝置，只用來說明 race-track session 需要哪些資料、
  每個資料從哪裡來（HCP3 計時並在 session 結束時送出 sector / lap 資訊，
  Dashcam 接收後寫進錄影檔的 metadata）。
- 移除左上 Race Track 狀態卡片（Race Track Status / Enable Race Track）、
  左側 Control 卡片（Send Race Data）、底部 Status 那一行，以及右側
  Live View Preview。
- 左上改成說明卡片，右側改成示意圖（純畫面，用 Canvas + PIL 畫出來，
  不讀任何裝置資料）。同一天第二版把示意圖從「賽道地圖 / 計時儀表板」
  改成「錄影檔 + metadata」。
- 2026-10-06 第三版：示意圖改成遊戲風格的 race overlay / HUD 假畫面
  （賽道背景、右上小地圖與 S1/S2/S3 中繼站、Sector 時間、LAP、
  FINISHED），並標示 CONCEPT MOCKUP · DEMO ONLY。
- 2026-10-06 第四版：太像電玩，改成「真實賽道車載錄影畫面＋低調的
  轉播風格資訊疊圖」。
- 2026-10-06 第五版：依使用者提供的參考圖 assets/race_care.png 重做。
  背景改用真實照片（參考圖把原本畫在上面的疊圖補平後，另存成
  assets/race_track_demo_background.jpg），疊圖由程式畫：左上計時卡
  （Lap 3 / 3、Finished、S1/S2/S3、Total Lap）、右上小地圖（S1/S2/S3、
  Start / Finish、目前位置，目前 sector 高亮）。程式畫賽道的版本移除。
- 2026-10-06：移除 OSD Position 區塊（下拉選單、X/Y 欄位及相關程式）
  ——目前沒有已確認的 API / protobuf requirement 支援 Race Track OSD
  Position，demo 也不需要讓使用者設定 OSD 位置。HCP3 說明改成「每個
  sector 完成時送出」；右側計時卡字體放大，預設視窗下也清楚可讀。
- Track Information / Timing / Status 欄位保留，用來展示 session 需要
  哪些資料；欄位內容不會送出。

build_screen() 的參數（on_send_race_data / on_enable_race_track /
on_get_race_track_status / on_back）跟 refresh_race_track_status() 都刻意
保留，DA005ControlPanel.pyw 跟 race_track_service.py 完全不用改：
callback 照樣傳進來、存起來，只是這一版的畫面不會呼叫它們。之後要恢復
成可操作版本，可以從 git 歷史（2026-09-22 版本）找回原本的按鈕與流程。

payload dict 的 key 對應到 SectorInfo/TrackInformation protobuf 欄位
（實際組 message 的邏輯在 race_track_service.send_track_information()），
這一版畫面上的欄位仍然是照這份對照表設計的：
    sequence_id                    -> SectorInfo.sequenceID
    sector_id                      -> SectorInfo.id
    sector_name                    -> SectorInfo.name
    sector_start_time_seconds      -> SectorInfo.sectorStartTime.seconds
    sector_start_time_nanos        -> SectorInfo.sectorStartTime.nanos
    sector_start_time_display_text -> 只給人看的可讀時間字串，沒有對應 wire 欄位
    sector_duration_seconds        -> SectorInfo.sectorDuration.seconds
    is_total_lap                   -> SectorInfo.isTotalLap
    is_finished                    -> SectorInfo.isFinished
    total_lap_time_seconds         -> TrackInformation.totalLapTime.seconds

顏色/字型/按鈕樣式全部沿用 screen_gallery.py 的 Dark Theme 常數
（BACKGROUND_COLOR/ROW_COLOR/TEXT_PRIMARY_COLOR/style_dark_button()），
跟 screen_settings.py 是同一套，不另外發明新的視覺風格。
"""

import math
import os
import sys
import tkinter as tk
import tkinter.font as tkfont
from datetime import datetime

from PIL import Image, ImageDraw, ImageOps, ImageTk

import resource_paths
import screen_gallery
import ui_shapes

SECTION_TITLE_FONT = ('Calibri', 12, 'bold')  # 跟 screen_settings.py 的 SETTINGS_ROW_TITLE_FONT 同一組數值
FIELD_LABEL_FONT = ('Calibri', 10)
FIELD_ENTRY_FONT = ('Calibri', 10)

DEMO_TITLE_FONT = ('Calibri', 14, 'bold')
DEMO_BODY_FONT = ('Calibri', 11)
DEMO_ROLE_FONT = ('Calibri', 11, 'bold')

BACK_CHEVRON_ICON_SIZE = 18

# 說明卡片跟示意圖共用的角色顏色：HCP3 藍、Dashcam 綠，兩邊看到同一個
# 顏色就知道是同一個角色。
HCP3_COLOR = '#6cb4ee'
DASHCAM_COLOR = '#7dcea0'

# ---- 示意圖（真實賽道錄影畫面＋賽事資訊疊圖）----
MOCK_BACKGROUND_COLOR = '#1b1b20'
MOCK_PANEL_BORDER_COLOR = '#34343c'
OVERLAY_TEXT_COLOR = '#ffffff'
OVERLAY_MUTED_TEXT_COLOR = '#c4c7cd'
OVERLAY_SHADOW_COLOR = '#1a1a1a'          # 直接疊在照片上的小字，底下墊一層深色陰影比較清楚
CARD_RGBA = (24, 26, 30, 205)             # 計時卡的半透明深色底（最後一個數字是透明度）
LABEL_CHIP_RGBA = (20, 22, 26, 200)       # 地圖上 S1/S2/S3 小標籤的底色
HIGHLIGHT_COLOR = '#4cc38a'               # 目前所在 sector 的高亮色（低調的綠，不用霓虹色）
START_FINISH_RED = (225, 45, 55)
OVERLAY_SUPERSAMPLE = 2                   # 疊圖先放大 2 倍畫再縮小，線條邊緣比較平滑

# 背景照片：由 assets/race_care.png（參考圖）把原本畫在圖上的疊圖補平後
# 另存而成，疊圖改由程式畫，才能依需求調整內容
DEMO_BACKGROUND_PHOTO_PARTS = ('assets', 'race_track_demo_background.jpg')

# 示意用的單圈資料：三個 sector 加起來就是 Total Lap（31.204 + 38.771 +
# 32.343 = 102.318 秒），左側欄位的預設值也用這組數字，整頁對得起來。
DEMO_SECTOR_TIMES = (31.204, 38.771, 32.343)
DEMO_SECTOR_DURATION = DEMO_SECTOR_TIMES[0]
DEMO_TOTAL_LAP_TIME = 102.318
DEMO_LAP_NUMBER = 3
DEMO_LAP_COUNT = 3
CURRENT_SECTOR_INDEX = 2                  # 目前所在 sector（0=S1、1=S2、2=S3），地圖跟計時卡會一起高亮
CURRENT_POSITION_RATIO = 0.95             # 目前位置在整圈的哪裡（0~1，從 Start / Finish 起算）
MOCKUP_TAG_TEXT = 'CONCEPT MOCKUP - DEMO ONLY'

# 小地圖賽道形狀：照參考圖右上角的賽道線描下來的點（像素座標），
# 從 Start / Finish 開始、順時針。畫的時候會正規化並用 Catmull-Rom
# 曲線串成平滑的封閉賽道。
MINIMAP_TRACK_POINTS = (
    (1578, 68), (1610, 72), (1630, 95), (1638, 140), (1636, 185), (1625, 205),
    (1605, 207), (1585, 195), (1560, 182), (1535, 190), (1512, 193), (1500, 180),
    (1503, 163), (1520, 152), (1550, 152), (1572, 145), (1575, 128), (1558, 118),
    (1520, 117), (1470, 120), (1430, 122), (1407, 118), (1398, 95), (1400, 62),
    (1420, 45), (1450, 38), (1480, 35), (1495, 48), (1515, 60), (1545, 65),
)
MINIMAP_CURVE_STEPS = 6

on_back_callback = None
on_send_race_data_callback = None
on_enable_race_track_callback = None
on_get_race_track_status_callback = None
back_button_image = None  # PhotoImage 沒有其他參照會被 GC 回收，用模組變數存住
mock_frame_image = None   # 同上，右側示意圖（整張畫面）
_overlay_font_family = None   # 疊圖字型，第一次畫的時候才決定（見 _overlay_font()）
_background_photo = None      # assets/ 裡的賽道照片（沒有就是 None）
_background_photo_checked = False   # 是否已經找過照片（只找一次）

# ---- 下面這些都是 build_screen() 建立的 widget，之後的函式要更新畫面內容
# 都靠這些模組變數，不用整個重建 ----
current_time_value_label = None
sequence_id_entry = None
sector_id_entry = None
sector_name_entry = None
sector_duration_entry = None
total_lap_time_entry = None
total_lap_var = None
is_finished_var = None
mock_canvas = None
_mock_redraw_after_id = None
_mock_retry_count = 0

# 示意圖第一次畫的時機：Canvas 剛建立時 layout 還沒算完，winfo_width()/
# winfo_height() 會是 1x1。這時候不畫，等 MOCK_RETRY_DELAY_MS 再試，
# 最多試 MOCK_MAX_RETRY 次（避免畫面一直沒顯示時無限重試）。
MOCK_RETRY_DELAY_MS = 50
MOCK_MAX_RETRY = 20
MOCK_RESIZE_DELAY_MS = 60

# Use Current Time 按下後才會有值。
_sector_start_time_seconds = None
_sector_start_time_nanos = None
_sector_start_time_display_text = None


def _new_back_chevron_image():
    """畫法跟 screen_settings.py 的 _new_back_chevron_image() 一致（同一個
    左箭頭視覺語言），這裡獨立寫一份而不是 import screen_settings 來共用
    ——這支函式很小，為了一個 icon 函式去建立兩個畫面模組之間的相依關係
    不划算，兩邊各自維護一份完全相同的小函式即可。"""
    def draw_callback(draw, diameter, _height, _scale):
        cx, cy = diameter / 2, diameter / 2
        size = diameter * 0.32
        line_width = max(2, int(diameter * 0.14))
        points = [(cx + size * 0.6, cy - size), (cx - size * 0.6, cy), (cx + size * 0.6, cy + size)]
        draw.line(points, fill=screen_gallery.TEXT_PRIMARY_COLOR, width=line_width, joint='curve')

    image = ui_shapes.render_supersampled(BACK_CHEVRON_ICON_SIZE, BACK_CHEVRON_ICON_SIZE, draw_callback)
    return ImageTk.PhotoImage(image)


def _create_card(parent):
    """跟 screen_settings.py 的卡片外觀一致：ROW_COLOR 底色的 Frame，
    外距沿用同一套間距習慣。"""
    card = tk.Frame(parent, bg=screen_gallery.ROW_COLOR)
    card.pack(fill='x', padx=20, pady=(4, 4))
    return card


def _create_section_title(card, text):
    title = tk.Label(card, text=text, font=SECTION_TITLE_FONT,
                      bg=screen_gallery.ROW_COLOR, fg=screen_gallery.TEXT_PRIMARY_COLOR)
    title.pack(anchor='w', padx=16, pady=(14, 8))
    return title


def _create_labeled_entry(card, label_text, default_text=''):
    """Label 在上、Entry 在下的一列，回傳 Entry 本身讓呼叫端存模組變數。"""
    row = tk.Frame(card, bg=screen_gallery.ROW_COLOR)
    row.pack(fill='x', padx=16, pady=(0, 10))
    label = tk.Label(row, text=label_text, font=FIELD_LABEL_FONT,
                      bg=screen_gallery.ROW_COLOR, fg=screen_gallery.TEXT_SECONDARY_COLOR)
    label.pack(anchor='w')
    entry = tk.Entry(row, font=FIELD_ENTRY_FONT, bg=screen_gallery.CONTROL_COLOR,
                      fg=screen_gallery.TEXT_PRIMARY_COLOR, insertbackground=screen_gallery.TEXT_PRIMARY_COLOR,
                      relief='flat', highlightthickness=1, highlightbackground=screen_gallery.CONTROL_COLOR,
                      highlightcolor=screen_gallery.CONTROL_HOVER_COLOR)
    entry.pack(fill='x', ipady=4)
    if default_text:
        entry.insert(0, default_text)
    return entry


def _create_labeled_entry_with_suffix(card, label_text, suffix_text, default_text=''):
    """跟 _create_labeled_entry() 一樣，但 Entry 右邊多一個固定文字後綴
    （例如「sec」），Sector Duration / Total Lap Time 這種數字＋單位的
    欄位用這個。"""
    row = tk.Frame(card, bg=screen_gallery.ROW_COLOR)
    row.pack(fill='x', padx=16, pady=(0, 10))
    label = tk.Label(row, text=label_text, font=FIELD_LABEL_FONT,
                      bg=screen_gallery.ROW_COLOR, fg=screen_gallery.TEXT_SECONDARY_COLOR)
    label.pack(anchor='w')

    entry_row = tk.Frame(row, bg=screen_gallery.ROW_COLOR)
    entry_row.pack(fill='x')
    entry = tk.Entry(entry_row, font=FIELD_ENTRY_FONT, bg=screen_gallery.CONTROL_COLOR,
                      fg=screen_gallery.TEXT_PRIMARY_COLOR, insertbackground=screen_gallery.TEXT_PRIMARY_COLOR,
                      relief='flat', highlightthickness=1, highlightbackground=screen_gallery.CONTROL_COLOR,
                      highlightcolor=screen_gallery.CONTROL_HOVER_COLOR, width=10)
    entry.pack(side='left', ipady=4)
    if default_text:
        entry.insert(0, default_text)
    suffix_label = tk.Label(entry_row, text=suffix_text, font=FIELD_LABEL_FONT,
                             bg=screen_gallery.ROW_COLOR, fg=screen_gallery.TEXT_SECONDARY_COLOR)
    suffix_label.pack(side='left', padx=(8, 0))
    return entry


def _create_dark_checkbutton(card, text, default_checked):
    """使用者明確要求用 Checkbutton（不要用 True/False 下拉選單）。
    selectcolor/bg/fg 這些手動上色在 Windows 沒問題；macOS 的 Aqua 主題
    跟這個工具其他地方的 tk.Button（見 screen_gallery.py 的 _MacDarkButton
    說明）一樣，勾選方塊本身是系統原生元件，不會吃這裡設的顏色——這是
    macOS tkinter 的已知限制，不是這裡漏設定，先不處理（不影響勾選/
    讀值的實際功能，只是外觀在 mac 上不會完全一致）。"""
    var = tk.IntVar(value=1 if default_checked else 0)
    checkbutton = tk.Checkbutton(
        card, text=text, variable=var, font=FIELD_LABEL_FONT,
        bg=screen_gallery.ROW_COLOR, fg=screen_gallery.TEXT_PRIMARY_COLOR,
        activebackground=screen_gallery.ROW_COLOR, activeforeground=screen_gallery.TEXT_PRIMARY_COLOR,
        selectcolor=screen_gallery.CONTROL_COLOR, highlightthickness=0, bd=0, cursor='hand2')
    checkbutton.pack(anchor='w', padx=16, pady=(0, 8))
    return var


def _create_demo_explanation_card(parent):
    """左上的說明卡片（取代原本的 Race Track 狀態卡片）。純說明用途，
    沒有按鈕。內文分三段；中間那段把 HCP3 / Dashcam 做成有顏色的角色
    標籤，跟右側示意圖底部的資料流程用同一組顏色。"""
    card = _create_card(parent)

    title = tk.Label(card, text='Race Track — interface demonstration', font=DEMO_TITLE_FONT,
                      bg=screen_gallery.ROW_COLOR, fg=screen_gallery.TEXT_PRIMARY_COLOR,
                      justify='left', anchor='w')
    title.pack(fill='x', padx=16, pady=(16, 10))

    # 這兩個 list 是為了視窗縮放時重新計算換行寬度（見下面的 _on_card_resize）。
    # 標題也放進來：預設視窗下左欄比較窄，標題不換行會被切掉。
    paragraph_labels = [title]
    role_rows = []   # 每一筆是 (角色標籤, 說明文字 Label)

    intro_label = tk.Label(
        card, text='This page is not connected to a device. It shows the data a race-track '
                   'session needs and where each item comes from.',
        font=DEMO_BODY_FONT, bg=screen_gallery.ROW_COLOR, fg=screen_gallery.TEXT_SECONDARY_COLOR,
        justify='left', anchor='w')
    intro_label.pack(fill='x', padx=16, pady=(0, 12))
    paragraph_labels.append(intro_label)

    role_descriptions = (
        ('HCP3', HCP3_COLOR,
         'times the race and sends sector / lap information as each sector is completed.'),
        ('Dashcam', DASHCAM_COLOR,
         'receives it and writes it into the recording file as metadata.'),
    )
    for role_name, role_color, description in role_descriptions:
        row = tk.Frame(card, bg=screen_gallery.ROW_COLOR)
        row.pack(fill='x', padx=16, pady=(0, 6))
        # width=8 讓 HCP3 / Dashcam 兩個標籤一樣寬，後面的說明文字左緣對齊
        role_label = tk.Label(row, text=role_name, font=DEMO_ROLE_FONT, width=8,
                               bg=screen_gallery.CONTROL_COLOR, fg=role_color, pady=2)
        role_label.pack(side='left', anchor='n')
        description_label = tk.Label(row, text=description, font=DEMO_BODY_FONT,
                                      bg=screen_gallery.ROW_COLOR, fg=screen_gallery.TEXT_PRIMARY_COLOR,
                                      justify='left', anchor='w')
        description_label.pack(side='left', fill='x', expand=True, padx=(10, 0))
        role_rows.append((role_label, description_label))

    remaining_label = tk.Label(
        card, text='The dashcam side is implemented. What remains is the HCP3 implementation.',
        font=DEMO_BODY_FONT, bg=screen_gallery.ROW_COLOR, fg=screen_gallery.TEXT_SECONDARY_COLOR,
        justify='left', anchor='w')
    remaining_label.pack(fill='x', padx=16, pady=(8, 16))
    paragraph_labels.append(remaining_label)

    def _on_card_resize(event):
        # tk.Label 不會自動換行，要依卡片目前寬度設定 wraplength；
        # 32 = 左右各 16 的 padx。
        text_width = max(event.width - 32, 100)
        for label in paragraph_labels:
            label.config(wraplength=text_width)
        for role_label, description_label in role_rows:
            description_width = text_width - role_label.winfo_reqwidth() - 10
            description_label.config(wraplength=max(description_width, 80))

    card.bind('<Configure>', _on_card_resize)
    return card


def build_screen(parent, on_send_race_data=None, on_enable_race_track=None,
                  on_get_race_track_status=None, on_back=None):
    """建立 Race Track Simulation 畫面（interface demonstration 版本）。

    on_send_race_data / on_enable_race_track / on_get_race_track_status：
    參數跟之前的可操作版本相同，DA005ControlPanel.pyw 不用改；這一版只
    把它們存到模組變數，畫面上沒有任何會呼叫它們的按鈕（這頁不連裝置）。
    on_back：按下左上角 Back 時呼叫，由呼叫端決定要回哪個畫面（這個畫面
    目前固定是從 Settings 的入口按鈕進來，所以呼叫端會傳回 Settings 的
    函式，這裡不自己判斷）。"""
    global on_back_callback, on_send_race_data_callback
    global on_enable_race_track_callback, on_get_race_track_status_callback
    global back_button_image
    global current_time_value_label
    global sequence_id_entry, sector_id_entry, sector_name_entry
    global sector_duration_entry, total_lap_time_entry
    global total_lap_var, is_finished_var
    global mock_canvas

    on_back_callback = on_back
    on_send_race_data_callback = on_send_race_data
    on_enable_race_track_callback = on_enable_race_track
    on_get_race_track_status_callback = on_get_race_track_status

    frame = tk.Frame(parent, bg=screen_gallery.BACKGROUND_COLOR)

    back_button_image = _new_back_chevron_image()
    back_button_label = tk.Label(frame, image=back_button_image, text=' Back', compound='left',
                                  font=('Calibri', 16, 'bold'), bg=screen_gallery.BACKGROUND_COLOR,
                                  fg=screen_gallery.TEXT_PRIMARY_COLOR, cursor='hand2')
    back_button_label.pack(pady=(20, 10), padx=20, anchor='w')
    back_button_label.bind('<Button-1>', lambda event: on_back_callback())

    page_title = tk.Label(frame, text='Race Track Simulation', font=('Calibri', 16, 'bold'),
                           bg=screen_gallery.BACKGROUND_COLOR, fg=screen_gallery.TEXT_PRIMARY_COLOR)
    page_title.pack(padx=20, pady=(0, 10), anchor='w')

    # ---- 左右兩欄：左側說明＋資料欄位（約 38%）／右側示意圖（約 62%）。
    # 用 grid()＋columnconfigure(weight=...) 做比例分配，視窗縮放時兩欄
    # 還是維持這個比例，不用自己算絕對寬度。----
    body = tk.Frame(frame, bg=screen_gallery.BACKGROUND_COLOR)
    body.pack(fill='both', expand=True, pady=(0, 20))
    body.grid_rowconfigure(0, weight=1)
    body.grid_columnconfigure(0, weight=38)
    body.grid_columnconfigure(1, weight=62)

    # ---- 左側：跟 screen_settings.py 一樣是可捲動的 Canvas + Scrollbar，
    # 視窗開得比較小時還是能看到全部欄位 ----
    left_scroll_container = tk.Frame(body, bg=screen_gallery.BACKGROUND_COLOR)
    left_scroll_container.grid(row=0, column=0, sticky='nsew')

    left_canvas = tk.Canvas(left_scroll_container, bg=screen_gallery.BACKGROUND_COLOR, highlightthickness=0)
    left_scrollbar = tk.Scrollbar(
        left_scroll_container, orient='vertical', command=left_canvas.yview,
        width=8, bd=0, highlightthickness=0, relief='flat', elementborderwidth=0,
        bg=screen_gallery.CONTROL_COLOR, activebackground=screen_gallery.CONTROL_HOVER_COLOR,
        troughcolor=screen_gallery.BACKGROUND_COLOR)
    left_inner = tk.Frame(left_canvas, bg=screen_gallery.BACKGROUND_COLOR)
    left_inner_window_id = left_canvas.create_window((0, 0), window=left_inner, anchor='nw')
    left_inner.bind('<Configure>', lambda event: left_canvas.configure(scrollregion=left_canvas.bbox('all')))
    left_canvas.bind('<Configure>', lambda event: left_canvas.itemconfig(left_inner_window_id, width=event.width))
    left_canvas.configure(yscrollcommand=left_scrollbar.set)
    left_canvas.pack(side='left', fill='both', expand=True)
    left_scrollbar.pack(side='right', fill='y')

    def _on_left_mouse_wheel(event):
        # 跟 screen_settings.py 同一套 Windows/macOS 滾輪換算差異處理。
        if sys.platform == 'win32':
            scroll_units = int(-1 * (event.delta / 120))
        else:
            scroll_units = int(-1 * event.delta)
            if scroll_units == 0:
                scroll_units = -1 if event.delta > 0 else 1
        left_canvas.yview_scroll(scroll_units, 'units')
    left_canvas.bind('<Enter>', lambda event: left_canvas.bind_all('<MouseWheel>', _on_left_mouse_wheel))
    left_canvas.bind('<Leave>', lambda event: left_canvas.unbind_all('<MouseWheel>'))

    # ---- [說明卡片]（2026-10-05 取代原本的 Race Track 狀態卡片）----
    _create_demo_explanation_card(left_inner)

    # ---- [Track Information] ----
    # 預設值跟右側示意圖的 Sector 1 / Total Lap 一致，讓整頁看起來是同一筆資料
    track_info_card = _create_card(left_inner)
    _create_section_title(track_info_card, 'Track Information')
    sequence_id_entry = _create_labeled_entry(track_info_card, 'Sequence ID', default_text='1')
    sector_id_entry = _create_labeled_entry(track_info_card, 'Sector ID', default_text='1')
    sector_name_entry = _create_labeled_entry(track_info_card, 'Sector Name', default_text='Sector 1')

    # ---- [Timing] ----
    timing_card = _create_card(left_inner)
    _create_section_title(timing_card, 'Timing')

    sector_start_time_label = tk.Label(timing_card, text='Sector Start Time', font=FIELD_LABEL_FONT,
                                        bg=screen_gallery.ROW_COLOR, fg=screen_gallery.TEXT_SECONDARY_COLOR)
    sector_start_time_label.pack(anchor='w', padx=16)
    use_current_time_button = screen_gallery.style_dark_button(
        tk.Button(timing_card, text='Use Current Time', command=lambda: on_use_current_time_click()))
    use_current_time_button.pack(anchor='w', padx=16, pady=(4, 4))

    current_time_caption = tk.Label(timing_card, text='Current Time:', font=FIELD_LABEL_FONT,
                                     bg=screen_gallery.ROW_COLOR, fg=screen_gallery.TEXT_SECONDARY_COLOR)
    current_time_caption.pack(anchor='w', padx=16, pady=(6, 0))
    current_time_value_label = tk.Label(timing_card, text='(not set)', font=('Calibri', 10, 'bold'),
                                         bg=screen_gallery.ROW_COLOR, fg=screen_gallery.TEXT_PRIMARY_COLOR)
    current_time_value_label.pack(anchor='w', padx=16, pady=(0, 10))

    sector_duration_entry = _create_labeled_entry_with_suffix(
        timing_card, 'Sector Duration', 'sec', default_text=f'{DEMO_SECTOR_DURATION:.3f}')
    total_lap_time_entry = _create_labeled_entry_with_suffix(
        timing_card, 'Total Lap Time', 'sec', default_text=f'{DEMO_TOTAL_LAP_TIME:.3f}')

    # ---- [Status]（Total Lap / Finished，使用者要求 Checkbutton）----
    status_card = _create_card(left_inner)
    _create_section_title(status_card, 'Status')
    total_lap_var = _create_dark_checkbutton(status_card, 'Total Lap', default_checked=True)
    is_finished_var = _create_dark_checkbutton(status_card, 'Finished', default_checked=True)
    tk.Frame(status_card, bg=screen_gallery.ROW_COLOR, height=4).pack()  # 底部留白，跟其他卡片間距一致

    # ---- 右側：真實賽道錄影畫面＋賽事資訊疊圖的示意圖（2026-10-05 取代
    # Live View Preview）。是假的 demo 畫面，不是串流，也不讀裝置。視窗
    # 大小改變時重畫，見 _schedule_mock_redraw()。----
    mock_canvas = tk.Canvas(body, bg=MOCK_BACKGROUND_COLOR, highlightthickness=1,
                             highlightbackground=MOCK_PANEL_BORDER_COLOR)
    mock_canvas.grid(row=0, column=1, sticky='nsew', padx=(12, 20))
    mock_canvas.bind('<Configure>', lambda event: _schedule_mock_redraw())
    # 不只靠 <Configure>：等 layout 算完（after_idle）後主動畫第一次
    mock_canvas.after_idle(_render_mock_when_ready)

    return frame


def refresh_race_track_status():
    """DA005ControlPanel.pyw 每次進入這個畫面都會呼叫（見
    _enter_race_track_screen()）。interface demonstration 版本不連裝置，
    所以這裡刻意不 GET race_track 狀態。保留這個函式名稱是為了讓
    DA005ControlPanel.pyw 不用跟著改。

    這裡順便重畫一次示意圖：頁面切換進來時保證畫面是依照目前實際尺寸
    畫的，不用等使用者拖曳視窗。"""
    if mock_canvas is not None:
        _schedule_mock_redraw()


def on_use_current_time_click():
    """由程式內部產生 seconds/nanos/可讀 datetime 字串，使用者不需要（也
    看不到）手動輸入 seconds/nanos——跟 DA005ProtobufSender 舊版介面刻意
    不同的地方。"""
    global _sector_start_time_seconds, _sector_start_time_nanos, _sector_start_time_display_text
    now = datetime.now()
    _sector_start_time_seconds = int(now.timestamp())
    _sector_start_time_nanos = now.microsecond * 1000  # microsecond -> nanosecond
    _sector_start_time_display_text = now.strftime('%Y-%m-%d %H:%M:%S.') + f'{now.microsecond // 1000:03d}'
    current_time_value_label.config(text=_sector_start_time_display_text)


# ==========================================================================
# 右側示意圖（真實賽道錄影畫面＋賽事資訊疊圖）
#
# 2026-10-06 第五版：依使用者提供的參考圖（assets/race_care.png）重做。
# 背景是真實的車載賽道照片，上面疊一層專業賽事分析資訊，不是 Live View，
# 不讀任何裝置資料。
#
#   左上   計時卡：Lap 3 / 3 ＋ 格子旗 Finished、S1/S2/S3 時間、Total Lap
#   右上   小地圖：白色賽道線、S1/S2/S3 標籤、sector 分界點、
#          Start / Finish、目前位置
#   右下   CONCEPT MOCKUP - DEMO ONLY
# 目前所在的 sector（CURRENT_SECTOR_INDEX）在地圖上那一段跟計時卡那一列
# 用同一個綠色淡淡標出來，兩邊對得起來。
#
# 半透明底框、地圖線條用 PIL 畫（才能半透明、線條平滑），文字用 Canvas 的
# create_text（字比較清楚）。
# ==========================================================================

def _schedule_mock_redraw():
    """拖曳視窗大小時 <Configure> 會連續觸發很多次，這裡延遲一點再重畫，
    只畫最後一次，避免一直重畫造成卡頓。"""
    global _mock_redraw_after_id
    if _mock_redraw_after_id is not None:
        mock_canvas.after_cancel(_mock_redraw_after_id)
    _mock_redraw_after_id = mock_canvas.after(MOCK_RESIZE_DELAY_MS, _render_mock_when_ready)


def _render_mock_when_ready():
    """先確認 Canvas 已經有實際尺寸才畫。還是 1x1（layout 還沒算完）就
    不畫，等一下再試，避免畫出一片黑。"""
    global _mock_redraw_after_id, _mock_retry_count
    _mock_redraw_after_id = None

    width = mock_canvas.winfo_width()
    height = mock_canvas.winfo_height()
    if width <= 1 or height <= 1:
        if _mock_retry_count < MOCK_MAX_RETRY:
            _mock_retry_count += 1
            _mock_redraw_after_id = mock_canvas.after(MOCK_RETRY_DELAY_MS, _render_mock_when_ready)
        return

    _mock_retry_count = 0
    _redraw_mock(width, height)


def _redraw_mock(width, height):
    global mock_frame_image
    mock_canvas.delete('all')

    # 小到畫不下：至少顯示一行文字，不留一片黑
    if width < 260 or height < 180:
        mock_canvas.create_text(width / 2, height / 2, text='Race track mockup',
                                font=('Calibri', 11, 'bold'), fill=OVERLAY_MUTED_TEXT_COLOR)
        return

    layout = _compute_overlay_layout(width, height)
    minimap = _compute_minimap_geometry(layout)

    # 1. 背景照片＋半透明的計時卡、地圖線條
    frame = _render_background(width, height)
    overlay = _render_overlay(width, height, layout, minimap)
    frame = Image.alpha_composite(frame.convert('RGBA'), overlay)
    mock_frame_image = ImageTk.PhotoImage(frame)
    mock_canvas.create_image(0, 0, image=mock_frame_image, anchor='nw')

    # 2. 文字
    _draw_timing_card_text(layout)
    _draw_minimap_text(layout, minimap)
    _draw_mockup_tag_text(layout)


# ---------------- 共用小工具 ----------------

def _hex_to_rgb(hex_color):
    """'#4cc38a' -> (76, 195, 138)"""
    hex_color = hex_color.lstrip('#')
    return (int(hex_color[0:2], 16), int(hex_color[2:4], 16), int(hex_color[4:6], 16))


def _overlay_font(size, bold=False, min_size=7):
    """疊圖用的字型。Windows 用 Segoe UI（乾淨的轉播風格字體），
    沒有的話（例如 macOS）改用 Calibri。字的大小最小 min_size。"""
    global _overlay_font_family
    if _overlay_font_family is None:
        if 'Segoe UI' in tkfont.families(mock_canvas):
            _overlay_font_family = 'Segoe UI'
        else:
            _overlay_font_family = 'Calibri'
    size = max(min_size, int(round(size)))
    if bold:
        return (_overlay_font_family, size, 'bold')
    return (_overlay_font_family, size)


def _text_width(font, text):
    """文字用這個字型畫出來的實際寬度（像素）。Windows 縮放比例不同時
    寬度也不同，所以框的大小都用量的，不寫死。"""
    return tkfont.Font(font=font).measure(text)


def _line_height(font):
    """這個字型一行文字的實際高度（像素）"""
    return tkfont.Font(font=font).metrics('linespace')


# ---------------- 背景照片 ----------------

def _load_background_photo():
    """讀取 assets/ 底下的賽道照片，只讀一次。檔案不存在或讀取失敗就
    回傳 None（畫面改用深色底，疊圖照常顯示）。"""
    global _background_photo, _background_photo_checked
    if _background_photo_checked:
        return _background_photo
    _background_photo_checked = True

    photo_path = resource_paths.resource_path(*DEMO_BACKGROUND_PHOTO_PARTS)
    if not os.path.exists(photo_path):
        print(f'[Race Track] 找不到示意背景圖: {photo_path}')
        return None
    try:
        _background_photo = Image.open(photo_path).convert('RGB')
    except OSError as error:
        print(f'[Race Track] 無法讀取示意背景圖 {photo_path}: {error}')
        _background_photo = None
    return _background_photo


def _render_background(width, height):
    """背景照片等比例縮放後置中裁切（ImageOps.fit），填滿整個畫面、不變形"""
    photo = _load_background_photo()
    if photo is None:
        return Image.new('RGB', (width, height), _hex_to_rgb(MOCK_BACKGROUND_COLOR))
    return ImageOps.fit(photo, (width, height), Image.LANCZOS)


# ---------------- 版面計算 ----------------

def _get_overlay_fonts(scale):
    """疊圖各處用的字型，大小跟著 scale 縮放。
    計時卡的字另外設了最小字級（min_size）：預設視窗下右側只有大約
    467x276，scale 會被壓到最小，沒有下限的話計時卡的字會小到看不清楚。"""
    return {
        'lap_label': _overlay_font(14 * scale, bold=True, min_size=10),
        'lap_number': _overlay_font(16 * scale, bold=True, min_size=11),
        'lap_count': _overlay_font(12 * scale, min_size=9),
        'finished': _overlay_font(10.5 * scale, bold=True, min_size=8),
        'row_label': _overlay_font(11.5 * scale, bold=True, min_size=9),
        'row_time': _overlay_font(11.5 * scale, min_size=9),
        'map_label': _overlay_font(7.5 * scale, bold=True),
        'tag': _overlay_font(7.5 * scale),
    }


def _compute_overlay_layout(width, height):
    """依 Canvas 大小算出計時卡、小地圖、標示的位置（Canvas 座標）。
    scale 以 820x560 為 1.0，限制在 0.55~1.2 之間；計時卡的大小是量
    文字實際大小再加留白算出來的，字不會超出框。"""
    scale = min(width / 820, height / 560)
    scale = max(0.55, min(scale, 1.2))
    margin = int(10 + 8 * scale)
    padding = int(10 * scale) + 2
    fonts = _get_overlay_fonts(scale)
    layout = {'width': width, 'height': height, 'scale': scale, 'margin': margin,
              'padding': padding, 'fonts': fonts}

    # ---- 左上：計時卡 ----
    # 表頭：Lap │ 3 / 3      格子旗 Finished
    item_gap = int(8 * scale)
    flag_cell = max(2, int(_line_height(fonts['finished']) * 0.18))
    header_width = (_text_width(fonts['lap_label'], 'Lap') + item_gap * 2
                    + _text_width(fonts['lap_number'], str(DEMO_LAP_NUMBER))
                    + _text_width(fonts['lap_count'], f' / {DEMO_LAP_COUNT}')
                    + int(22 * scale) + flag_cell * 4 + int(5 * scale)
                    + _text_width(fonts['finished'], 'Finished'))
    # 每一列：● S1 ........ 31.204 s
    dot_space = int(12 * scale)
    row_width = (dot_space + _text_width(fonts['row_label'], 'Total Lap') + int(30 * scale)
                 + _text_width(fonts['row_time'], f'{DEMO_TOTAL_LAP_TIME:.3f} s'))
    card_width = padding * 2 + max(header_width, row_width)

    header_height = _line_height(fonts['lap_number'])
    row_height = _line_height(fonts['row_label']) + int(6 * scale)
    separator_space = int(8 * scale)
    card_top = margin
    header_center_y = card_top + padding + header_height / 2
    first_separator_y = card_top + padding + header_height + separator_space / 2
    rows_top = first_separator_y + separator_space / 2
    second_separator_y = rows_top + row_height * 3 + separator_space / 2
    total_center_y = second_separator_y + separator_space / 2 + row_height / 2
    card_bottom = int(total_center_y + row_height / 2 + padding * 0.6)

    layout['card'] = (margin, card_top, margin + card_width, card_bottom)
    layout['card_item_gap'] = item_gap
    layout['card_flag_cell'] = flag_cell
    layout['card_dot_space'] = dot_space
    layout['card_header_center_y'] = header_center_y
    layout['card_first_separator_y'] = first_separator_y
    layout['card_rows_top'] = rows_top
    layout['card_row_height'] = row_height
    layout['card_second_separator_y'] = second_separator_y
    layout['card_total_center_y'] = total_center_y

    # ---- 右上：小地圖（不加底框，跟參考圖一樣直接畫在畫面上）----
    # 外圍留一點空間給 S1/S2/S3、Start / Finish 標籤
    label_space = int(_line_height(fonts['map_label']) * 1.4)
    map_width = int(min(width * 0.27, height * 0.42))
    layout['minimap'] = (width - margin - label_space - map_width, margin + label_space,
                         width - margin - label_space, margin + label_space + map_width)   # 高度之後依形狀比例再算

    # ---- 右下：示意畫面標示 ----
    layout['tag_position'] = (width - margin, height - margin)
    return layout


def _catmull_rom_closed_curve(control_points, steps):
    """用 Catmull-Rom 曲線把控制點串成平滑的封閉曲線，回傳很多個點。
    公式是標準的 Catmull-Rom：每一段由前後共 4 個控制點決定，曲線會
    經過每一個控制點，所以賽道形狀可以直接用控制點調整。"""
    curve_points = []
    point_count = len(control_points)
    for index in range(point_count):
        p0 = control_points[(index - 1) % point_count]
        p1 = control_points[index]
        p2 = control_points[(index + 1) % point_count]
        p3 = control_points[(index + 2) % point_count]
        for step in range(steps):
            t = step / steps
            t2 = t * t
            t3 = t2 * t
            x = 0.5 * (2 * p1[0] + (-p0[0] + p2[0]) * t
                       + (2 * p0[0] - 5 * p1[0] + 4 * p2[0] - p3[0]) * t2
                       + (-p0[0] + 3 * p1[0] - 3 * p2[0] + p3[0]) * t3)
            y = 0.5 * (2 * p1[1] + (-p0[1] + p2[1]) * t
                       + (2 * p0[1] - 5 * p1[1] + 4 * p2[1] - p3[1]) * t2
                       + (-p0[1] + 3 * p1[1] - 3 * p2[1] + p3[1]) * t3)
            curve_points.append((x, y))
    return curve_points


def _index_at_length_ratio(cumulative_lengths, ratio):
    """找出走到整圈長度 ratio（0~1）時，是曲線上的第幾個點"""
    target = cumulative_lengths[-1] * ratio
    for index, length in enumerate(cumulative_lengths):
        if length >= target:
            return index
    return len(cumulative_lengths) - 1


def _offset_from_center(x, y, center_x, center_y, distance):
    """從 (center_x, center_y) 往 (x, y) 的方向，把點再往外推 distance。
    distance 是負數就是往中心推。"""
    direction_x = x - center_x
    direction_y = y - center_y
    length = math.hypot(direction_x, direction_y)
    if length == 0:
        return x, y
    return x + direction_x / length * distance, y + direction_y / length * distance


def _compute_minimap_geometry(layout):
    """小地圖的賽道曲線、sector 分段、標籤位置、目前位置（Canvas 座標）。
    PIL 畫線跟 Canvas 畫文字都用這一份，位置才會對得起來。"""
    scale = layout['scale']
    left, top, right, _bottom = layout['minimap']
    map_width = right - left

    # 參考圖的點正規化：x、y 用同一個比例縮放，形狀不會被拉扁
    min_x = min(point[0] for point in MINIMAP_TRACK_POINTS)
    max_x = max(point[0] for point in MINIMAP_TRACK_POINTS)
    min_y = min(point[1] for point in MINIMAP_TRACK_POINTS)
    max_y = max(point[1] for point in MINIMAP_TRACK_POINTS)
    unit = max(max_x - min_x, max_y - min_y)
    control_points = []
    for x, y in MINIMAP_TRACK_POINTS:
        control_points.append((left + (x - min_x) / unit * map_width, top + (y - min_y) / unit * map_width))
    map_height = (max_y - min_y) / unit * map_width
    layout['minimap'] = (left, top, right, top + map_height)

    curve_points = _catmull_rom_closed_curve(control_points, MINIMAP_CURVE_STEPS)

    # 累計長度，用來把整圈平均分成 3 個 sector（依實際距離，不是依點的數量）
    cumulative_lengths = [0.0]
    for index in range(1, len(curve_points)):
        previous_x, previous_y = curve_points[index - 1]
        x, y = curve_points[index]
        cumulative_lengths.append(cumulative_lengths[-1] + math.hypot(x - previous_x, y - previous_y))

    first_boundary = _index_at_length_ratio(cumulative_lengths, 1 / 3)
    second_boundary = _index_at_length_ratio(cumulative_lengths, 2 / 3)
    # 每個 sector 的點範圍：(開始, 結束)，S3 結束回到起點（Start / Finish）
    sector_ranges = ((0, first_boundary), (first_boundary, second_boundary), (second_boundary, len(curve_points)))

    center_x = (left + right) / 2
    center_y = top + map_height / 2

    # S1/S2/S3 標籤放在每段中間，往地圖外側推一點，不會壓到賽道線
    label_distance = 11 * scale
    sector_label_points = []
    for start, end in sector_ranges:
        middle_index = _index_at_length_ratio(cumulative_lengths,
                                              (cumulative_lengths[start]
                                               + (cumulative_lengths[end - 1] - cumulative_lengths[start]) / 2)
                                              / cumulative_lengths[-1])
        x, y = curve_points[middle_index]
        sector_label_points.append(_offset_from_center(x, y, center_x, center_y, label_distance))

    # Start / Finish 標籤放在起點正上方（起點在最上面那條直線，上方是空的，
    # 版面也已經在地圖上面預留空間）；放其他方向會壓到賽道線跟目前位置的圓點
    start_x, start_y = curve_points[0]
    label_half_height = (_line_height(layout['fonts']['map_label']) + 2 * scale) / 2
    start_finish_label_point = (start_x, start_y - label_half_height - 6 * scale)

    position_index = _index_at_length_ratio(cumulative_lengths, CURRENT_POSITION_RATIO)
    return {
        'curve_points': curve_points,
        'sector_ranges': sector_ranges,
        'boundary_indexes': (first_boundary, second_boundary),
        'sector_label_points': sector_label_points,
        'start_finish_label_point': start_finish_label_point,
        'position_index': position_index,
    }


# ---------------- PIL：半透明計時卡、地圖線條 ----------------

def _render_overlay(width, height, layout, minimap):
    """畫出所有需要半透明 / 平滑線條的東西。先用 OVERLAY_SUPERSAMPLE 倍
    大小畫，最後縮小，線條跟圓點邊緣才不會有鋸齒。"""
    k = OVERLAY_SUPERSAMPLE
    overlay = Image.new('RGBA', (width * k, height * k), (0, 0, 0, 0))
    draw = ImageDraw.Draw(overlay)
    _paint_timing_card(draw, layout, k)
    _paint_minimap(draw, layout, minimap, k)
    _paint_label_chips(draw, layout, minimap, k)
    return overlay.resize((width, height), Image.LANCZOS)


def _paint_timing_card(draw, layout, k):
    """左上計時卡：圓角深色底、表頭分隔線、格子旗、目前 sector 那一列的高亮"""
    scale = layout['scale']
    padding = layout['padding']
    left, top, right, bottom = layout['card']
    draw.rounded_rectangle((left * k, top * k, right * k, bottom * k), radius=6 * scale * k, fill=CARD_RGBA)

    # 兩條分隔線
    for line_y in (layout['card_first_separator_y'], layout['card_second_separator_y']):
        draw.line([((left + padding) * k, line_y * k), ((right - padding) * k, line_y * k)],
                  fill=(255, 255, 255, 70), width=max(1, k))

    # 表頭「Lap」跟「3 / 3」之間的直線
    fonts = layout['fonts']
    divider_x = left + padding + _text_width(fonts['lap_label'], 'Lap') + layout['card_item_gap']
    header_center_y = layout['card_header_center_y']
    half_height = _line_height(fonts['lap_number']) * 0.32
    draw.line([(divider_x * k, (header_center_y - half_height) * k), (divider_x * k, (header_center_y + half_height) * k)],
              fill=(255, 255, 255, 110), width=max(1, k))

    # Finished 前面的小格子旗（4x3）
    cell = layout['card_flag_cell']
    finished_width = _text_width(fonts['finished'], 'Finished')
    flag_left = right - padding - finished_width - int(5 * scale) - cell * 4
    flag_top = header_center_y - cell * 1.5
    for row in range(3):
        for column in range(4):
            if (row + column) % 2 == 0:
                cell_color = (245, 245, 245, 255)
            else:
                cell_color = (24, 24, 26, 255)
            cell_left = flag_left + column * cell
            cell_top = flag_top + row * cell
            draw.rectangle((cell_left * k, cell_top * k, (cell_left + cell) * k - 1, (cell_top + cell) * k - 1),
                           fill=cell_color)
    layout['card_finished_x'] = flag_left + cell * 4 + int(5 * scale)

    # 每一列前面的小圓點；目前所在 sector 那一列加淡淡的綠色底跟綠色圓點。
    # 注意：在同一個透明圖層上畫，新的顏色會「取代」底下的像素，不會跟
    # 計時卡的深色底混色，所以高亮底色要自己先混好（深色底 75% ＋ 綠 25%）
    row_height = layout['card_row_height']
    dot_radius = max(2, 3 * scale)
    highlight = _hex_to_rgb(HIGHLIGHT_COLOR)
    highlight_row_color = (int(CARD_RGBA[0] * 0.75 + highlight[0] * 0.25),
                           int(CARD_RGBA[1] * 0.75 + highlight[1] * 0.25),
                           int(CARD_RGBA[2] * 0.75 + highlight[2] * 0.25),
                           CARD_RGBA[3])
    for sector_index in range(3):
        row_top = layout['card_rows_top'] + row_height * sector_index
        center_y = row_top + row_height / 2
        if sector_index == CURRENT_SECTOR_INDEX:
            draw.rectangle(((left + padding * 0.4) * k, (row_top + 1) * k, (right - padding * 0.4) * k,
                            (row_top + row_height - 1) * k), fill=highlight_row_color)
            dot_color = highlight + (255,)
        else:
            dot_color = (255, 255, 255, 220)
        dot_x = left + padding + dot_radius
        draw.ellipse(((dot_x - dot_radius) * k, (center_y - dot_radius) * k,
                      (dot_x + dot_radius) * k, (center_y + dot_radius) * k), fill=dot_color)


def _paint_minimap(draw, layout, minimap, k):
    """右上小地圖：深色陰影＋白色賽道線，目前 sector 那段用綠色，
    sector 分界的小白點，Start / Finish 紅白短線，目前位置"""
    scale = layout['scale']
    points = []
    for x, y in minimap['curve_points']:
        points.append((x * k, y * k))
    closed_points = points + [points[0]]

    # 陰影讓白線在亮的天空上也看得清楚
    draw.line(closed_points, fill=(0, 0, 0, 80), width=int(6 * scale * k), joint='curve')
    draw.line(closed_points, fill=(255, 255, 255, 240), width=int(2.6 * scale * k), joint='curve')

    # 目前所在 sector 那一段改成綠色
    start, end = minimap['sector_ranges'][CURRENT_SECTOR_INDEX]
    current_segment = points[start:end]
    if end == len(points):
        current_segment = current_segment + [points[0]]
    draw.line(current_segment, fill=_hex_to_rgb(HIGHLIGHT_COLOR) + (255,), width=int(3 * scale * k), joint='curve')

    # sector 分界點：小白點，深色外框
    boundary_radius = 2.8 * scale * k
    for point_index in minimap['boundary_indexes']:
        x, y = points[point_index]
        draw.ellipse((x - boundary_radius, y - boundary_radius, x + boundary_radius, y + boundary_radius),
                     fill=(255, 255, 255, 255), outline=(30, 30, 32, 255), width=max(1, int(scale * k)))

    # Start / Finish：跟賽道垂直的一小段紅白線
    start_x, start_y = points[0]
    next_x, next_y = points[1]
    length = math.hypot(next_x - start_x, next_y - start_y) or 1
    normal_x = -(next_y - start_y) / length
    normal_y = (next_x - start_x) / length
    half_length = 6 * scale * k
    line_width = max(2, int(3 * scale * k))
    draw.line([(start_x - normal_x * half_length, start_y - normal_y * half_length), (start_x, start_y)],
              fill=(255, 255, 255, 255), width=line_width)
    draw.line([(start_x, start_y), (start_x + normal_x * half_length, start_y + normal_y * half_length)],
              fill=START_FINISH_RED + (255,), width=line_width)

    # 目前位置：綠色圓點＋白色外框
    x, y = points[minimap['position_index']]
    position_radius = 4.5 * scale * k
    draw.ellipse((x - position_radius, y - position_radius, x + position_radius, y + position_radius),
                 fill=_hex_to_rgb(HIGHLIGHT_COLOR) + (255,), outline=(255, 255, 255, 255),
                 width=max(1, int(1.5 * scale * k)))


def _get_map_labels(minimap):
    """地圖上的所有標籤：(文字, 中心點, 是否為目前 sector)"""
    labels = []
    for sector_index, point in enumerate(minimap['sector_label_points']):
        labels.append((f'S{sector_index + 1}', point, sector_index == CURRENT_SECTOR_INDEX))
    labels.append(('Start / Finish', minimap['start_finish_label_point'], False))
    return labels


def _paint_label_chips(draw, layout, minimap, k):
    """地圖標籤的小底框（深色圓角），目前 sector 的標籤用綠色底"""
    scale = layout['scale']
    font = layout['fonts']['map_label']
    chip_height = _line_height(font) + 2 * scale
    for text, (center_x, center_y), is_current in _get_map_labels(minimap):
        chip_width = _text_width(font, text) + 8 * scale
        if is_current:
            fill = _hex_to_rgb(HIGHLIGHT_COLOR) + (230,)
        else:
            fill = LABEL_CHIP_RGBA
        draw.rounded_rectangle(((center_x - chip_width / 2) * k, (center_y - chip_height / 2) * k,
                                (center_x + chip_width / 2) * k, (center_y + chip_height / 2) * k),
                               radius=3 * scale * k, fill=fill)


# ---------------- Canvas：文字 ----------------

def _draw_timing_card_text(layout):
    """左上計時卡的文字：表頭 Lap 3 / 3、Finished，S1/S2/S3 時間，Total Lap"""
    fonts = layout['fonts']
    padding = layout['padding']
    left, top, right, bottom = layout['card']
    inner_left = left + padding
    inner_right = right - padding
    header_center_y = layout['card_header_center_y']

    # 表頭：Lap │ 3 / 3
    mock_canvas.create_text(inner_left, header_center_y, text='Lap', anchor='w',
                            font=fonts['lap_label'], fill=OVERLAY_TEXT_COLOR)
    number_x = inner_left + _text_width(fonts['lap_label'], 'Lap') + layout['card_item_gap'] * 2
    number_id = mock_canvas.create_text(number_x, header_center_y, text=str(DEMO_LAP_NUMBER), anchor='w',
                                        font=fonts['lap_number'], fill=OVERLAY_TEXT_COLOR)
    mock_canvas.create_text(mock_canvas.bbox(number_id)[2], header_center_y, text=f' / {DEMO_LAP_COUNT}',
                            anchor='w', font=fonts['lap_count'], fill=OVERLAY_MUTED_TEXT_COLOR)
    # 表頭右邊：格子旗（PIL 畫好了）＋ Finished
    mock_canvas.create_text(layout['card_finished_x'], header_center_y, text='Finished', anchor='w',
                            font=fonts['finished'], fill=OVERLAY_TEXT_COLOR)

    # S1 / S2 / S3：標籤名稱跟地圖上一樣，時間靠右
    label_x = inner_left + layout['card_dot_space']
    row_height = layout['card_row_height']
    for sector_index, sector_seconds in enumerate(DEMO_SECTOR_TIMES):
        center_y = layout['card_rows_top'] + row_height * sector_index + row_height / 2
        mock_canvas.create_text(label_x, center_y, text=f'S{sector_index + 1}', anchor='w',
                                font=fonts['row_label'], fill=OVERLAY_TEXT_COLOR)
        mock_canvas.create_text(inner_right, center_y, text=f'{sector_seconds:.3f} s', anchor='e',
                                font=fonts['row_time'], fill=OVERLAY_TEXT_COLOR)

    # Total Lap
    total_center_y = layout['card_total_center_y']
    mock_canvas.create_text(inner_left, total_center_y, text='Total Lap', anchor='w',
                            font=fonts['row_label'], fill=OVERLAY_TEXT_COLOR)
    mock_canvas.create_text(inner_right, total_center_y, text=f'{DEMO_TOTAL_LAP_TIME:.3f} s', anchor='e',
                            font=fonts['row_label'], fill=OVERLAY_TEXT_COLOR)


def _draw_minimap_text(layout, minimap):
    """地圖標籤的文字（底框 PIL 已經畫好）"""
    font = layout['fonts']['map_label']
    for text, (center_x, center_y), is_current in _get_map_labels(minimap):
        if is_current:
            color = '#0f2a1d'     # 綠色底配深色字
        else:
            color = OVERLAY_TEXT_COLOR
        mock_canvas.create_text(center_x, center_y, text=text, font=font, fill=color)


def _draw_mockup_tag_text(layout):
    """右下角小字：明確告訴使用者這是示意畫面，不是正式輸出。
    直接疊在照片上，先畫一層深色陰影（往右下偏 1px）再畫字，比較清楚。"""
    x, y = layout['tag_position']
    font = layout['fonts']['tag']
    mock_canvas.create_text(x + 1, y + 1, text=MOCKUP_TAG_TEXT, anchor='se', font=font, fill=OVERLAY_SHADOW_COLOR)
    mock_canvas.create_text(x, y, text=MOCKUP_TAG_TEXT, anchor='se', font=font, fill=OVERLAY_MUTED_TEXT_COLOR)
