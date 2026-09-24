"""
screen_race_track.py — Race Track Simulation 畫面

2026-09-22 新增。把原本 DA005 Protobuf Sender 那種「逐欄位對應 protobuf
訊息」的 TrackInformation 操作介面，改成比較適合客戶操作的表單：使用者
不用知道 protobuf 欄位名稱，也不用自己組 sectorStartTime 的 seconds/nanos。

這支檔案仍然只負責 UI，不 import audi_pb2、不直接呼叫 api.send_get()/
send_put()、也不 hardcode Dashcam URL——protobuf 組包跟 RESTful 傳輸全部
放在 race_track_service.py，這裡只透過 build_screen() 傳入的 callback
呼叫（on_send_race_data / on_enable_race_track / on_get_race_track_status），
callback 由 DA005ControlPanel.pyw 決定要接哪一支真正的函式。

2026-09-22 第二輪新增 Race Track Enable：
- 「Send Race Data」不再是隨時可以送——一定要先確認裝置目前 race_track
  是 Enabled 才能送，Disabled/Unknown 都會被擋下來，狀態文字不同
  （Unknown 不能當成 Disabled，見 on_send_button_click()）。
- 呼叫 on_enable_race_track_callback()/on_get_race_track_status_callback()
  跟 on_send_race_data_callback() 一樣，統一包成背景 thread + 用
  widget.after(0, ...) 排回 main thread 才更新畫面——這些 callback 實際
  上會發真正的網路請求（跟一開始的 placeholder 版本不同），直接在
  Tkinter callback 裡同步呼叫會卡住整個 UI。進行中會把對應按鈕 disable，
  避免使用者重複點擊造成兩個請求互相干擾。

payload dict 的 key 對應到 SectorInfo/TrackInformation protobuf 欄位
（實際組 message 的邏輯在 race_track_service.send_track_information()）：
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

import sys
import threading
import tkinter as tk
from tkinter import ttk
from datetime import datetime

from PIL import ImageTk

import screen_gallery
import ui_shapes

SECTION_TITLE_FONT = ('Calibri', 12, 'bold')  # 跟 screen_settings.py 的 SETTINGS_ROW_TITLE_FONT 同一組數值
FIELD_LABEL_FONT = ('Calibri', 10)
FIELD_ENTRY_FONT = ('Calibri', 10)

OSD_POSITION_OPTIONS = ('Top Left', 'Top Right', 'Bottom Left', 'Bottom Right', 'Custom')

BACK_CHEVRON_ICON_SIZE = 18

# Race Track Enable 狀態文字——Unknown 刻意獨立於 Enabled/Disabled 之外，
# 使用者要求「Unknown 不能當成 Disabled」，Send Race Data 的擋下邏輯見
# on_send_button_click()。
RACE_TRACK_STATUS_UNKNOWN = 'Unknown'
RACE_TRACK_STATUS_CHECKING = 'Checking...'
RACE_TRACK_STATUS_ENABLED = 'Enabled'
RACE_TRACK_STATUS_DISABLED = 'Disabled'
RACE_TRACK_STATUS_ENABLING = 'Enabling...'

on_back_callback = None
on_send_race_data_callback = None
on_enable_race_track_callback = None
on_get_race_track_status_callback = None
back_button_image = None  # PhotoImage 沒有其他參照會被 GC 回收，用模組變數存住

# ---- 下面這些都是 build_screen() 建立的 widget，之後的函式要更新畫面內容
# 都靠這些模組變數，不用整個重建 ----
status_label = None
current_time_value_label = None
sequence_id_entry = None
sector_id_entry = None
sector_name_entry = None
sector_duration_entry = None
total_lap_time_entry = None
total_lap_var = None
is_finished_var = None
osd_position_var = None
osd_custom_x_entry = None
osd_custom_y_entry = None
osd_custom_frame = None
race_track_status_label = None
enable_race_track_button = None
send_button = None

# _race_track_enabled 是 Send Race Data 用來判斷「能不能送」的唯一依據：
# None=不知道（還沒 GET 成功過，或 GET/Enable 失敗過）、True=Enabled、
# False=Disabled。狀態文字（race_track_status_label 顯示的字串）只是
# 給人看，Enable 失敗時文字會顯示錯誤訊息，但這個布林值刻意不會被失敗
# 訊息覆蓋——維持「最後一次確定知道的狀態」，不會因為某次操作失敗就被
# 誤判成 Unknown 或 Disabled。
_race_track_enabled = None

# Use Current Time 按下後才會有值；使用者還沒按過就送出的話，這兩個欄位
# 維持 None，交給以後真的串接的人決定要怎麼處理（例如擋下來要求先按過）。
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


def _create_labeled_entry_with_suffix(card, label_text, suffix_text):
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


def _configure_osd_combobox_style():
    """ttk.Combobox 預設是系統原生淺色外觀，這裡建立一個獨立命名的 ttk
    style（'RaceTrackOSD.TCombobox'），只套用在這一顆下拉選單上，不會
    影響工具裡其他地方（目前也沒有其他地方用到 ttk.Combobox）。跟
    Checkbutton 一樣，macOS Aqua 主題對 ttk 元件的顏色客製化也有限制，
    這裡是儘量貼近、不是保證跟 Windows 完全一致。"""
    style = ttk.Style()
    style.theme_use(style.theme_use())
    style.configure('RaceTrackOSD.TCombobox',
                     fieldbackground=screen_gallery.CONTROL_COLOR,
                     background=screen_gallery.CONTROL_COLOR,
                     foreground=screen_gallery.TEXT_PRIMARY_COLOR,
                     arrowcolor=screen_gallery.TEXT_PRIMARY_COLOR)


def build_screen(parent, on_send_race_data=None, on_enable_race_track=None,
                  on_get_race_track_status=None, on_back=None):
    """建立 Race Track Simulation 畫面。

    on_send_race_data(payload)：按下「Send Race Data」時呼叫（只有在
    Race Track 目前狀態確定是 Enabled 才會被呼叫，見 on_send_button_click()）。
    on_enable_race_track(enabled)：按下「Enable Race Track」時呼叫，固定
    傳 True。
    on_get_race_track_status()：進入這個畫面時呼叫一次，用來讀取裝置目前
    race_track 的實際狀態（見 DA005ControlPanel.pyw 的
    _enter_race_track_screen()），回傳 True/False/None（None＝不知道）。
    三個都可能是 None（呼叫端還沒接上真正的函式）——這裡一律當作「這個
    操作目前不可用」處理，只更新 Status 文字，不會嘗試自己送任何網路
    請求或猜測結果。
    on_back：按下左上角 Back 時呼叫，由呼叫端決定要回哪個畫面（這個畫面
    目前固定是從 Settings 的入口按鈕進來，所以呼叫端會傳回 Settings 的
    函式，這裡不自己判斷）。"""
    global on_back_callback, on_send_race_data_callback
    global on_enable_race_track_callback, on_get_race_track_status_callback
    global back_button_image
    global status_label, current_time_value_label
    global sequence_id_entry, sector_id_entry, sector_name_entry
    global sector_duration_entry, total_lap_time_entry
    global total_lap_var, is_finished_var
    global osd_position_var, osd_custom_x_entry, osd_custom_y_entry, osd_custom_frame
    global race_track_status_label, enable_race_track_button, send_button

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

    # ---- 左右兩欄：左側控制項（約 35~40%）／右側 Live View Preview（約
    # 60~65%）。用 grid()＋columnconfigure(weight=...) 做比例分配，視窗
    # 縮放時兩欄還是維持這個比例，不用自己算絕對寬度。----
    body = tk.Frame(frame, bg=screen_gallery.BACKGROUND_COLOR)
    body.pack(fill='both', expand=True)
    body.grid_rowconfigure(0, weight=1)
    body.grid_columnconfigure(0, weight=38)
    body.grid_columnconfigure(1, weight=62)

    # ---- 左側：Race Track controls（跟 screen_settings.py 一樣是可捲動的
    # Canvas + Scrollbar，欄位不算多，但視窗開得比較小時還是能完整操作）----
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

    # ---- [Race Track]（Enable gate，2026-09-22 新增；一定要放最上面——
    # Send Race Data 需要靠這裡的狀態決定能不能送）----
    race_track_card = _create_card(left_inner)
    _create_section_title(race_track_card, 'Race Track')
    race_track_status_label = tk.Label(
        race_track_card, text=f'Race Track Status: {RACE_TRACK_STATUS_UNKNOWN}',
        font=('Calibri', 10, 'bold'), bg=screen_gallery.ROW_COLOR, fg=screen_gallery.TEXT_PRIMARY_COLOR)
    race_track_status_label.pack(anchor='w', padx=16, pady=(0, 8))
    enable_race_track_button = screen_gallery.style_dark_button(
        tk.Button(race_track_card, text='Enable Race Track', command=lambda: on_enable_button_click()))
    enable_race_track_button.pack(anchor='w', padx=16, pady=(0, 14))

    # ---- [Track Information] ----
    track_info_card = _create_card(left_inner)
    _create_section_title(track_info_card, 'Track Information')
    sequence_id_entry = _create_labeled_entry(track_info_card, 'Sequence ID', default_text='1')
    sector_id_entry = _create_labeled_entry(track_info_card, 'Sector ID')
    sector_name_entry = _create_labeled_entry(track_info_card, 'Sector Name')

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

    sector_duration_entry = _create_labeled_entry_with_suffix(timing_card, 'Sector Duration', 'sec')
    total_lap_time_entry = _create_labeled_entry_with_suffix(timing_card, 'Total Lap Time', 'sec')

    # ---- [Status]（Total Lap / Finished，使用者要求 Checkbutton）----
    status_card = _create_card(left_inner)
    _create_section_title(status_card, 'Status')
    total_lap_var = _create_dark_checkbutton(status_card, 'Total Lap', default_checked=True)
    is_finished_var = _create_dark_checkbutton(status_card, 'Finished', default_checked=True)
    tk.Frame(status_card, bg=screen_gallery.ROW_COLOR, height=4).pack()  # 底部留白，跟其他卡片間距一致

    # ---- [OSD Position]（先只做 UI，不送 command；Custom 才顯示 X/Y）----
    osd_card = _create_card(left_inner)
    _create_section_title(osd_card, 'OSD Position')
    _configure_osd_combobox_style()

    osd_position_var = tk.StringVar(value='Top Left')
    osd_row = tk.Frame(osd_card, bg=screen_gallery.ROW_COLOR)
    osd_row.pack(fill='x', padx=16, pady=(0, 6))
    osd_position_label = tk.Label(osd_row, text='Position:', font=FIELD_LABEL_FONT,
                                   bg=screen_gallery.ROW_COLOR, fg=screen_gallery.TEXT_SECONDARY_COLOR)
    osd_position_label.pack(anchor='w')
    osd_position_combobox = ttk.Combobox(osd_row, textvariable=osd_position_var, values=OSD_POSITION_OPTIONS,
                                         state='readonly', style='RaceTrackOSD.TCombobox')
    osd_position_combobox.pack(fill='x')
    osd_position_combobox.bind('<<ComboboxSelected>>', lambda event: _refresh_osd_custom_fields_visibility())

    osd_custom_frame = tk.Frame(osd_card, bg=screen_gallery.ROW_COLOR)
    # 預設不 pack()——只有選到 Custom 才顯示，見 _refresh_osd_custom_fields_visibility()
    osd_custom_x_entry = _create_labeled_entry(osd_custom_frame, 'X:')
    osd_custom_y_entry = _create_labeled_entry(osd_custom_frame, 'Y:')
    tk.Frame(osd_card, bg=screen_gallery.ROW_COLOR, height=4).pack()

    # ---- [Control] ----
    control_card = _create_card(left_inner)
    _create_section_title(control_card, 'Control')
    send_button = screen_gallery.style_dark_button(
        tk.Button(control_card, text='Send Race Data', command=lambda: on_send_button_click()))
    send_button.pack(anchor='w', padx=16, pady=(0, 14))

    # ---- 底部 Status 一行（保留未來 Debug Log 區域的可能性，這版先只用
    # 一行文字，不做大型 LOG 視窗）----
    status_label = tk.Label(frame, text='Status: Ready', font=('Calibri', 9),
                             bg=screen_gallery.BACKGROUND_COLOR, fg=screen_gallery.TEXT_SECONDARY_COLOR)
    status_label.pack(padx=20, pady=(0, 12), anchor='w')

    # ---- 右側：Live View Preview（先只做深色 placeholder，不實作 UVC）----
    preview_frame = tk.Frame(body, bg='#000000')
    preview_frame.grid(row=0, column=1, sticky='nsew', padx=(12, 20), pady=(0, 0))

    preview_title = tk.Label(preview_frame, text='Live View Preview', font=('Calibri', 12, 'bold'),
                              bg='#000000', fg=screen_gallery.TEXT_SECONDARY_COLOR)
    preview_title.pack(pady=(16, 0))

    preview_placeholder_label = tk.Label(
        preview_frame, text='UVC Live View\nWaiting for device support...',
        font=('Calibri', 13), bg='#000000', fg=screen_gallery.TEXT_MUTED_COLOR, justify='center')
    preview_placeholder_label.pack(expand=True)

    return frame


def _refresh_osd_custom_fields_visibility():
    """Custom 才顯示 X/Y 輸入欄位，其他四個固定位置選項不需要（先只做 UI，
    這裡也不會真的送出 OSD position command）。"""
    if osd_position_var.get() == 'Custom':
        osd_custom_frame.pack(fill='x', padx=0, pady=(0, 10))
    else:
        osd_custom_frame.pack_forget()


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


def _parse_optional_int(text):
    """空字串或無法轉換都回傳 None，不丟例外——這版只做 UI，欄位驗證盡量
    寬鬆，不因為使用者還沒填完就跳錯誤訊息。"""
    text = text.strip()
    if not text:
        return None
    try:
        return int(text)
    except ValueError:
        return None


def _parse_optional_float(text):
    text = text.strip()
    if not text:
        return None
    try:
        return float(text)
    except ValueError:
        return None


def _collect_race_data_payload():
    """把目前 Track Information / Timing / Status 欄位收集成一個 plain
    dict——不組 protobuf message（這版刻意不 import audi_pb2），交給
    on_send_race_data_callback 決定後續怎麼處理。"""
    return {
        'sequence_id': _parse_optional_int(sequence_id_entry.get()),
        'sector_id': sector_id_entry.get().strip(),
        'sector_name': sector_name_entry.get().strip(),
        'sector_start_time_seconds': _sector_start_time_seconds,
        'sector_start_time_nanos': _sector_start_time_nanos,
        'sector_start_time_display_text': _sector_start_time_display_text,
        'sector_duration_seconds': _parse_optional_float(sector_duration_entry.get()),
        'is_total_lap': bool(total_lap_var.get()),
        'is_finished': bool(is_finished_var.get()),
        'total_lap_time_seconds': _parse_optional_float(total_lap_time_entry.get()),
    }


def set_status_text(text):
    """更新底部這一行 Status 文字（Send Race Data 的結果）。"""
    status_label.config(text=f'Status: {text}')


def _set_race_track_status_label(text):
    """更新 Race Track 這張卡片自己的狀態文字（Enable Race Track 的結果），
    跟底部的 status_label（Send Race Data 結果）是兩件獨立的事，不要
    共用同一個 Label。"""
    race_track_status_label.config(text=f'Race Track Status: {text}')


def refresh_race_track_status():
    """進入 Race Track 頁面時呼叫（見 DA005ControlPanel.pyw 的
    _enter_race_track_screen()）：重新 GET 一次裝置目前 race_track 的
    實際狀態。on_get_race_track_status_callback 是 None（呼叫端還沒接上
    真正的 GET 函式）就直接維持 Unknown，不自己猜。

    網路呼叫放進背景 thread，結果用 race_track_status_label.after(0, ...)
    排回 main thread 才更新畫面，不會卡住 UI；進行中 Enable 按鈕先
    disable，避免使用者在 GET 還沒回來時就按 Enable，兩個請求互相干擾。"""
    if on_get_race_track_status_callback is None:
        return

    enable_race_track_button.config(state='disabled')
    _set_race_track_status_label(RACE_TRACK_STATUS_CHECKING)

    def worker():
        try:
            result = on_get_race_track_status_callback()
        except Exception as error:
            race_track_status_label.after(0, lambda captured_error=error: _on_check_status_done(None, captured_error))
            return
        race_track_status_label.after(0, lambda: _on_check_status_done(result, None))

    threading.Thread(target=worker, daemon=True).start()


def _on_check_status_done(result, error):
    global _race_track_enabled
    enable_race_track_button.config(state='normal')

    if error is not None:
        # GET 失敗：維持/回到 Unknown，不假裝知道目前是 Enabled 還是
        # Disabled。錯誤細節印到 console 給人查，狀態文字保持簡短。
        print(f'[Race Track] 讀取目前 race_track 狀態失敗: {error}')
        _race_track_enabled = None
        _set_race_track_status_label(RACE_TRACK_STATUS_UNKNOWN)
        return

    if result is None:
        # 裝置回傳的 SystemConfiguration 沒設定過 race_track（proto3
        # optional 沒有值），一樣算 Unknown，不猜是 Disabled。
        _race_track_enabled = None
        _set_race_track_status_label(RACE_TRACK_STATUS_UNKNOWN)
        return

    _race_track_enabled = bool(result)
    _set_race_track_status_label(RACE_TRACK_STATUS_ENABLED if _race_track_enabled else RACE_TRACK_STATUS_DISABLED)


def on_enable_button_click():
    """按下「Enable Race Track」：呼叫 on_enable_race_track_callback(True)。
    這一定是使用者明確按下去才會發生——不會在進入頁面時自動呼叫（見
    refresh_race_track_status() 只呼叫 GET，不會呼叫 Enable）。"""
    if on_enable_race_track_callback is None:
        _set_race_track_status_label('Enable failed: not wired up yet')
        return

    enable_race_track_button.config(state='disabled')
    _set_race_track_status_label(RACE_TRACK_STATUS_ENABLING)

    def worker():
        try:
            on_enable_race_track_callback(True)
        except Exception as error:
            race_track_status_label.after(0, lambda captured_error=error: _on_enable_done(False, captured_error))
            return
        race_track_status_label.after(0, lambda: _on_enable_done(True, None))

    threading.Thread(target=worker, daemon=True).start()


def _on_enable_done(success, error):
    global _race_track_enabled
    enable_race_track_button.config(state='normal')

    if success:
        _race_track_enabled = True
        _set_race_track_status_label(RACE_TRACK_STATUS_ENABLED)
        return

    # Enable 失敗：可能是 GET 失敗、也可能 GET 成功但 PUT 失敗，兩種都算
    # Enable failed。這裡刻意不去改動 _race_track_enabled——保留「最後一次
    # 確定知道的狀態」，不會因為這次失敗就被誤判成 Unknown 或 Disabled
    # （如果裝置本來就已經是 Enabled，這次重複 Enable 失敗不代表它變成
    # 別的狀態）。
    print(f'[Race Track] Enable Race Track 失敗: {error}')
    _set_race_track_status_label(f'Enable failed: {error}')


def on_send_button_click():
    """按下「Send Race Data」：
    - Unknown（_race_track_enabled is None）：擋下來，訊息明確要求先
      Refresh 或 Enable，不能被誤判成 Disabled。
    - Disabled（False）：擋下來，訊息要求先 Enable。
    - Enabled（True）：才真的呼叫 on_send_race_data_callback()。"""
    if _race_track_enabled is None:
        set_status_text('Race Track status is unknown. Please refresh or enable Race Track first.')
        return
    if not _race_track_enabled:
        set_status_text('Please enable Race Track first.')
        return

    payload = _collect_race_data_payload()

    if on_send_race_data_callback is None:
        # 2026-09-22：理論上這時候呼叫端應該已經接上真正的發送函式，這裡
        # 保留 placeholder 只是保險——避免呼叫端漏接時直接 AttributeError。
        set_status_text('(Placeholder) Race data collected, sending not yet wired up.')
        return

    send_button.config(state='disabled')
    set_status_text('Sending...')

    def worker():
        try:
            on_send_race_data_callback(payload)
        except Exception as error:
            status_label.after(0, lambda captured_error=error: _on_send_done(False, captured_error))
            return
        status_label.after(0, lambda: _on_send_done(True, None))

    threading.Thread(target=worker, daemon=True).start()


def _on_send_done(success, error):
    send_button.config(state='normal')
    if success:
        set_status_text('Race data sent successfully. HTTP 200')
    else:
        print(f'[Race Track] Send Race Data 失敗: {error}')
        set_status_text(f'Send failed: {error}')
