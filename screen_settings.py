"""
screen_settings.py — Settings 畫面

2026-08-13 DVR Mode 改成 ON/OFF Switch（取代原本 WITH_RINGBUFFER /
WITHOUT_RINGBUFFER / STANDBY 三選一下拉選單），並統一跟 Recording /
Gallery / Favorite / Player 共用 api.py 的 desired_dvr_mode 概念：

- Switch 代表 desired_dvr_mode：ON = WITHOUT_RINGBUFFER，OFF = STANDBY
  （STANDBY 現在代表「Dashcam 關閉」，不是「暫停錄影」）。
- Switch 是整個工具裡「唯一」允許永久改變 desired_dvr_mode 的地方，其他
  頁面只能暫時改變 actual、結束後要呼叫 api.restore_desired_dvr_mode()
  恢復，不會呼叫 api.set_desired_dvr_mode()。
- Toggle 流程一律「PUT -> GET 確認 -> 更新畫面」（api.apply_and_set_desired_dvr_mode()），
  PUT 不代表成功；確認失敗時 Switch 要回復原本狀態、不修改 desired_dvr_mode、
  顯示明確 Error，不能默默假設成功。
- 工具剛啟動、使用者還沒操作過 Switch 時，desired_dvr_mode 需要一個起始值：
  第一次 build_screen() 時背景 GET 一次目前真實的 dvr_mode，如果剛好是
  WITHOUT_RINGBUFFER 或 STANDBY 就直接拿來當作起始 desired（代表「DVR 現在
  本來就是這個狀態，先當作使用者原本就要的」）；如果是其他原始狀態（例如
  剛好在 WITH_RINGBUFFER），沒有更好的猜測依據，維持 api.py 裡的預設值
  （WITHOUT_RINGBUFFER）。之後每次切回 Settings Tab 只是單純依 desired_dvr_mode
  重畫 Switch（不用重新 GET），因為 desired 是這個工具 session 內穩定的值，
  只有使用者自己按 Switch 才會變。

SD Card Format：協議沿用 AutoMate 完全一樣的寫法（conftest.py 的
prepare_dashcam fixture、test_DA005_dvr_010.py 等全部案例一致）：PUT
/dashcam/memory，Memory.sd_do_format = DO_FORMAT_TYPE_AUTO（api.py 的
format_sd_card()）。Format 前一定要先確認 DVR 真的處於「字面上的 STANDBY」
才送 Format（改用 screen_recording.is_standby_mode() 而不是
derive_dvr_state()，後者對 WITHOUT_RINGBUFFER 這種新的待機基準狀態會誤判
成「standby」，導致 Format 前置檢查誤判成「已經是 Standby」而跳過真正
切換 Standby 的步驟）。Format 完成後 DVR 可能自動回到 WITH_RINGBUFFER，
不能讓工具停留在這個狀態——一定要呼叫 api.restore_desired_dvr_mode() 重新
套用 Settings 的 desired_dvr_mode，然後才強制重新整理 Gallery
（screen_gallery.force_refresh()）。Recording 頁面的舊縮圖不用在這裡另外
清除：Recording 頁面改成「每次進入都重新 GET 一次」（screen_recording.
refresh_recording_page()），下次使用者切回 Recording 頁面時會自動重新抓，
不會顯示 Format 之前的舊資料。

Colors/按鈕樣式一律共用 screen_gallery.py 的 Dark Theme
（BACKGROUND_COLOR/ROW_COLOR/style_dark_button()），不在這裡另外定義
第二套顏色（Switch 的 ON/OFF 顏色除外，那是這裡專屬的新元件）。
"""

import sys
import threading
import tkinter as tk
from tkinter import messagebox
from time import sleep

from PIL import ImageTk

import api
import audi_pb2
import recording_mode_service
import screen_gallery
import screen_recording
import ui_shapes

FORMAT_SETTLE_SECONDS = 5  # 跟 AutoMate 案例一樣，Format 送出後留一點時間讓 DVR 真的處理完

# 2026-08-20 使用者要求：Format SD Card 按鈕 enable/disable 完全共用
# api.is_sd_card_present() 這個既有狀態（見 api.py 的 SD Card Presence
# 說明），不要在這裡自己另外判斷一套 SD Presence。
_format_in_progress = False  # Format 操作本身進行中時，SD Card 狀態同步不能覆蓋「Formatting...」這個進行中狀態

# 2026-08-19 使用者要求：Settings 不是跟 Recording/Gallery 同層的主要
# Tab，是從目前畫面進入的次級頁面，原本左上角純文字的「Settings」標題
# 改成可點擊的「Back」，回到 DA005ControlPanel.pyw 記錄的來源畫面（見
# go_back_from_settings()）。箭頭造型跟 screen_player.py 的 Back 按鈕
# 共用同一個左箭頭（chevron），整個工具只保留一種「返回」視覺語言，不
# 另外設計第三種返回圖示——Player 是深色圓形半透明底疊在影片上，這裡是
# 一般淺色頁面內容，不需要那個圓形底，只取線條本身，顏色改用跟頁面
# 文字同一色。
BACK_CHEVRON_ICON_SIZE = 18

on_back_callback = None
on_open_race_track_callback = None
on_open_iq_adjustment_callback = None
back_button_image = None  # PhotoImage 沒有其他參照會被 GC 回收，用模組變數存住

# Switch 元件的外觀設定（Canvas 自畫，tkinter 沒有原生 Switch 元件）
SWITCH_WIDTH = 52
SWITCH_HEIGHT = 28
SWITCH_KNOB_RADIUS = 11
SWITCH_ON_COLOR = '#4CAF50'
SWITCH_OFF_COLOR = screen_gallery.CONTROL_COLOR
SWITCH_KNOB_COLOR = screen_gallery.TEXT_PRIMARY_COLOR

# 2026-08-26 使用者要求：Dashcam／Event review mode／Start event recording
# 這三個主項目文字的字型/大小/粗細，直接比照既有 SD Card 標題
# （sd_card_title），不要另外自訂新的字體大小——這裡定義成共用常數，
# 兩邊都讀同一個值，之後不會有「兩處各自寫一份、忘記同步改」的風險。
SETTINGS_ROW_TITLE_FONT = ('Calibri', 12, 'bold')

# 2026-10-14 新增：Recording Mode 二選一 segmented control（4K / 1080P）。
# 第二版接上 RD 的 API（CameraConfiguration.resolution_setting，protobuf/
# RESTful 細節全部在 recording_mode_service.py）：
# - 每次進 Settings 背景 GET 一次實際值，依結果決定高亮哪一個（不再寫死 4K）
# - 點選後背景 PUT，成功才算數；失敗恢復原本選項並顯示錯誤
# - HTTP 一律在背景 thread，不會卡住 UI
RECORDING_MODE_4K = recording_mode_service.RECORDING_MODE_4K
RECORDING_MODE_1080P = recording_mode_service.RECORDING_MODE_1080P
RECORDING_MODE_OPTIONS = (RECORDING_MODE_4K, RECORDING_MODE_1080P)
SEGMENT_WIDTH = 150         # 每個選項的寬度
SEGMENT_HEIGHT = 34         # 整條 segmented control 的高度
SEGMENT_INSET = 3           # 選中項目（亮色塊）跟外框之間留的空隙
SEGMENT_ACTIVE_COLOR = SWITCH_ON_COLOR   # 跟 Switch ON 同一個綠色，代表「目前選中」

current_recording_mode = None   # 裝置上目前「確認過」的值；None = 還沒讀到／讀取失敗／不是 4K 也不是 1080P
recording_mode_busy = False     # 背景 GET 或 PUT 進行中：這段期間點擊一律忽略，避免兩個請求互相蓋掉
recording_mode_canvas = None
recording_mode_status_label = None

DvrSystemState = audi_pb2.DvrMode.DvrSystemState

dvr_switch_canvas = None
dvr_mode_status_label = None
format_button = None
format_status_label = None

# Switch toggle 進行中（PUT -> GET 確認）時避免使用者連點造成兩個流程互相干擾
switch_busy = False

# 使用者是否已經自己按過 Switch。開機時 initialize_desired_dvr_mode_from_actual()
# 的背景 GET 可能比使用者操作慢，如果使用者已經按過 Switch，那次開機推斷的
# 結果就不能再覆蓋使用者剛選的值（Codex review 2026-08-13 發現的 race
# condition：背景 GET 晚到會蓋掉使用者剛按的選擇）。
user_touched_switch = False


def _new_back_chevron_image():
    """畫法完全比照 screen_player.py 的 _draw_chevron('left')：一樣是純
    線條的左箭頭，只是這裡沒有 Player 那個深色圓形半透明底、顏色改用
    跟頁面文字同一色。一樣用 ui_shapes.render_supersampled() 統一做
    anti-alias，不用 Canvas 原生線條。"""
    def draw_callback(draw, diameter, _height, _scale):
        cx, cy = diameter / 2, diameter / 2
        size = diameter * 0.32
        line_width = max(2, int(diameter * 0.14))
        points = [(cx + size * 0.6, cy - size), (cx - size * 0.6, cy), (cx + size * 0.6, cy + size)]
        draw.line(points, fill=screen_gallery.TEXT_PRIMARY_COLOR, width=line_width, joint='curve')

    image = ui_shapes.render_supersampled(BACK_CHEVRON_ICON_SIZE, BACK_CHEVRON_ICON_SIZE, draw_callback)
    return ImageTk.PhotoImage(image)


# ============================================================
# 2026-08-26 使用者要求新增：Event review mode／Start event recording
# 子項目，這幾個開關全部只是「本地 UI 模擬」——不呼叫任何 API、不碰
# protobuf、不影響 Recording/Event/Gallery 任何真實功能，也不持久化
# （工具重開後一律回到這裡寫死的 default，沒有存檔機制，天然符合
# 「不需要永久儲存」）。跟 DVR Mode 那顆 Switch 共用同一套視覺畫法
# （ui_shapes 的 make_pill_photo_image()/make_circle_photo_image()、
# 同樣的 SWITCH_WIDTH/HEIGHT/ON_COLOR/OFF_COLOR 常數），但這裡完全獨立
# 一份 canvas/click handler/本地布林狀態，不會共用、也不會修改
# draw_switch()/dvr_switch_canvas 這些既有 DVR Mode 專屬的東西。
# ============================================================

def _draw_mock_switch(canvas, is_on):
    """跟 draw_switch() 畫法一致（同一套 ui_shapes 呼叫），但吃一個明確
    傳入的 canvas，不是寫死畫在 dvr_switch_canvas 上——讓下面
    _create_mock_toggle_row() 可以重複呼叫產生多個互相獨立的開關。"""
    canvas.delete('all')
    track_color = SWITCH_ON_COLOR if is_on else SWITCH_OFF_COLOR
    radius = SWITCH_HEIGHT / 2
    track_image = ui_shapes.make_pill_photo_image(SWITCH_WIDTH, SWITCH_HEIGHT, fill=track_color)
    canvas.create_image(0, 0, image=track_image, anchor='nw')
    knob_diameter = SWITCH_KNOB_RADIUS * 2
    knob_image = ui_shapes.make_circle_photo_image(knob_diameter, fill=SWITCH_KNOB_COLOR)
    knob_x = SWITCH_WIDTH - radius if is_on else radius
    canvas.create_image(knob_x, radius, image=knob_image, anchor='center')
    canvas.mock_switch_image_refs = (track_image, knob_image)  # 避免 PhotoImage 被 GC 回收


def _create_mock_toggle_row(parent, label_text, default_on, indent=False, pady=4, font=None):
    """左側文字＋右側 Switch 的一整列，純本地模擬用（見上方區塊說明）。
    indent=True 用比一般列更大的左邊距，給 Start event recording 底下
    的子項目縮排用。font 沒給的話用原本的小字（子項目維持目前較小的
    尺寸），主項目（例如 Event review mode）呼叫端會明確傳
    SETTINGS_ROW_TITLE_FONT 比照 SD Card 標題。"""
    row = tk.Frame(parent, bg=screen_gallery.ROW_COLOR)
    row_padx = (40, 16) if indent else 16
    row.pack(fill='x', padx=row_padx, pady=pady)

    label = tk.Label(row, text=label_text, font=(font or ('Calibri', 10)),
                      bg=screen_gallery.ROW_COLOR, fg=screen_gallery.TEXT_PRIMARY_COLOR)
    label.pack(side='left')

    canvas = tk.Canvas(row, width=SWITCH_WIDTH, height=SWITCH_HEIGHT,
                        bg=screen_gallery.ROW_COLOR, highlightthickness=0, cursor='hand2')
    canvas.pack(side='right')

    mock_state = {'is_on': default_on}

    def on_click(event):
        mock_state['is_on'] = not mock_state['is_on']
        _draw_mock_switch(canvas, mock_state['is_on'])

    canvas.bind('<Button-1>', on_click)
    _draw_mock_switch(canvas, default_on)
    return row


def _create_expandable_section(parent, header_text, default_expanded=True):
    """▼/▶ 箭頭＋標題的可點擊列，回傳一個 content_frame 讓呼叫端把子
    項目 pack 進去。點擊箭頭、標題文字、整列空白處都會觸發展開/收合
    （三個 widget 各自綁 <Button-1>，不是靠事件從子元件冒泡到父層——
    Tkinter 預設不會自動冒泡）。純視覺，不影響任何功能。"""
    # 2026-08-26 使用者回報這一列文字看起來沒有跟 Dashcam/Event review
    # mode 置中對齊：根本原因是這裡的 pady 一直是 (14, 4)（不對稱），而
    # Dashcam 的 switch_row／Event review mode 的 row 都是 (14, 14)（對稱）
    # ——不對稱的外距差異才是視覺上「文字中心高度」不一致的實際原因，不是
    # anchor 或 widget 對齊問題。改成依展開/收合狀態動態切換 pady：收合時
    # （目前預設狀態）用跟其他列一致的對稱 (14, 14)，展開時維持原本的
    # (14, 4)（跟子項目間距不變，這次使用者明確要求先不要動）。
    HEADER_ROW_PADY_COLLAPSED = (14, 14)
    HEADER_ROW_PADY_EXPANDED = (14, 4)

    header_row = tk.Frame(parent, bg=screen_gallery.ROW_COLOR, cursor='hand2')
    header_row.pack(fill='x', padx=16,
                     pady=(HEADER_ROW_PADY_EXPANDED if default_expanded else HEADER_ROW_PADY_COLLAPSED))

    # 2026-08-26 使用者要求：標題文字比照 SD Card 標題字體
    # （SETTINGS_ROW_TITLE_FONT），箭頭跟著用同一個字體，維持視覺對齊。
    arrow_label = tk.Label(header_row, text=('▼' if default_expanded else '▶'),
                            font=SETTINGS_ROW_TITLE_FONT, bg=screen_gallery.ROW_COLOR,
                            fg=screen_gallery.TEXT_PRIMARY_COLOR, width=2, cursor='hand2')
    arrow_label.pack(side='left')

    title_label = tk.Label(header_row, text=header_text, font=SETTINGS_ROW_TITLE_FONT,
                            bg=screen_gallery.ROW_COLOR, fg=screen_gallery.TEXT_PRIMARY_COLOR,
                            cursor='hand2')
    title_label.pack(side='left', padx=(4, 0))

    content_frame = tk.Frame(parent, bg=screen_gallery.ROW_COLOR)
    expand_state = {'expanded': default_expanded}
    if default_expanded:
        content_frame.pack(fill='x', pady=(0, 10))

    def toggle(event=None):
        expand_state['expanded'] = not expand_state['expanded']
        if expand_state['expanded']:
            header_row.pack(fill='x', padx=16, pady=HEADER_ROW_PADY_EXPANDED)
            content_frame.pack(fill='x', pady=(0, 10))
            arrow_label.config(text='▼')
        else:
            content_frame.pack_forget()
            header_row.pack(fill='x', padx=16, pady=HEADER_ROW_PADY_COLLAPSED)
            arrow_label.config(text='▶')

    header_row.bind('<Button-1>', toggle)
    arrow_label.bind('<Button-1>', toggle)
    title_label.bind('<Button-1>', toggle)

    return content_frame


def _draw_recording_mode_selector(highlighted_mode):
    """重畫 Recording Mode segmented control：整條是 CONTROL_COLOR 的
    膠囊外框，highlighted_mode 那一格疊一塊綠色膠囊＋白色粗體字，其他
    格維持次要文字顏色（secondary style）。highlighted_mode 是 None 時
    兩格都不高亮（還沒讀到裝置的值，或讀到的不是 4K / 1080P）。"""
    canvas = recording_mode_canvas
    if canvas is None:
        return
    canvas.delete('all')

    total_width = SEGMENT_WIDTH * len(RECORDING_MODE_OPTIONS)
    track_image = ui_shapes.make_pill_photo_image(total_width, SEGMENT_HEIGHT,
                                                  fill=screen_gallery.CONTROL_COLOR)
    canvas.create_image(0, 0, image=track_image, anchor='nw')

    active_width = SEGMENT_WIDTH - SEGMENT_INSET * 2
    active_height = SEGMENT_HEIGHT - SEGMENT_INSET * 2
    active_image = ui_shapes.make_pill_photo_image(active_width, active_height, fill=SEGMENT_ACTIVE_COLOR)

    for index, mode in enumerate(RECORDING_MODE_OPTIONS):
        segment_left = index * SEGMENT_WIDTH
        text_center_x = segment_left + SEGMENT_WIDTH / 2
        text_center_y = SEGMENT_HEIGHT / 2

        if mode == highlighted_mode:
            canvas.create_image(segment_left + SEGMENT_INSET, SEGMENT_INSET, image=active_image, anchor='nw')
            canvas.create_text(text_center_x, text_center_y, text=mode, font=('Calibri', 10, 'bold'),
                               fill=screen_gallery.TEXT_PRIMARY_COLOR)
        else:
            canvas.create_text(text_center_x, text_center_y, text=mode, font=('Calibri', 10),
                               fill=screen_gallery.TEXT_SECONDARY_COLOR)

    canvas.recording_mode_image_refs = (track_image, active_image)  # 避免 PhotoImage 被 GC 回收


def _set_recording_mode_status(text):
    if recording_mode_status_label is not None:
        recording_mode_status_label.config(text=text)


def on_recording_mode_clicked(event):
    """依點擊的 x 座標判斷點到哪一格（左邊 4K、右邊 1080P）。"""
    if recording_mode_busy:
        print('[Settings] Recording Mode 讀取／切換中，這次點擊忽略')
        return
    clicked_index = int(event.x // SEGMENT_WIDTH)
    if clicked_index < 0 or clicked_index >= len(RECORDING_MODE_OPTIONS):
        return
    clicked_mode = RECORDING_MODE_OPTIONS[clicked_index]
    if clicked_mode == current_recording_mode:
        return  # 點到已選中的項目，不需要做任何事
    on_recording_mode_changed(clicked_mode)


# ---------- 進 Settings：背景 GET 實際值 ----------

def load_recording_mode_from_device():
    """每次進 Settings 時呼叫（DA005ControlPanel.pyw 的 _enter_settings_screen()）。
    背景 GET，不卡 UI；GET 完成前維持上一次確認過的高亮。
    正在 PUT 的話就不讀，PUT 完成時本來就會更新畫面。"""
    global recording_mode_busy
    if recording_mode_busy:
        return
    recording_mode_busy = True
    _set_recording_mode_status('Loading recording mode...')
    thread = threading.Thread(target=_load_recording_mode_worker, daemon=True)
    thread.start()


def _load_recording_mode_worker():
    try:
        mode = recording_mode_service.get_recording_mode()
    except Exception as error:
        print(f'[Settings] 讀取 Recording Mode 失敗: {error}')
        recording_mode_canvas.after(0, lambda: _on_recording_mode_loaded(None, False))
        return
    recording_mode_canvas.after(0, lambda: _on_recording_mode_loaded(mode, True))


def _on_recording_mode_loaded(mode, succeeded):
    """main thread：GET 完成。失敗或讀到 4K/1080P 以外的值都不高亮，
    只在狀態文字說明，不跳 messagebox（進頁面就跳錯誤視窗太打擾）。"""
    global recording_mode_busy, current_recording_mode
    recording_mode_busy = False
    current_recording_mode = mode
    _draw_recording_mode_selector(current_recording_mode)

    if not succeeded:
        _set_recording_mode_status('Unable to read recording mode.')
    elif mode is None:
        _set_recording_mode_status('Current recording mode is not 4K or 1080P.')
    else:
        _set_recording_mode_status('')


# ---------- 使用者點選：背景 PUT ----------

def on_recording_mode_changed(mode):
    """使用者選了另一個 Recording Mode：先把高亮移到新選項（讓使用者知道
    有反應），背景 PUT；成功才更新 current_recording_mode，失敗恢復原本的
    高亮並顯示錯誤。"""
    global recording_mode_busy
    recording_mode_busy = True
    previous_mode = current_recording_mode
    print(f'[Settings] Recording Mode 切換：{previous_mode} -> {mode}')
    _draw_recording_mode_selector(mode)
    _set_recording_mode_status(f'Switching to {mode}...')
    thread = threading.Thread(target=_apply_recording_mode_worker, args=(mode, previous_mode), daemon=True)
    thread.start()


def _apply_recording_mode_worker(mode, previous_mode):
    try:
        recording_mode_service.apply_recording_mode(mode)
    except Exception as error:
        print(f'[Settings] 切換 Recording Mode 失敗: {error}')
        recording_mode_canvas.after(0, lambda captured_error=error: _on_recording_mode_apply_failed(previous_mode, captured_error))
        return
    recording_mode_canvas.after(0, lambda: _on_recording_mode_apply_succeeded(mode))


def _on_recording_mode_apply_succeeded(mode):
    global recording_mode_busy, current_recording_mode
    recording_mode_busy = False
    current_recording_mode = mode
    print(f'[Settings] Recording Mode 已切換為 {mode}')
    _draw_recording_mode_selector(current_recording_mode)
    _set_recording_mode_status('')


def _on_recording_mode_apply_failed(previous_mode, error):
    global recording_mode_busy
    recording_mode_busy = False
    _draw_recording_mode_selector(previous_mode)  # 恢復原本的選項
    _set_recording_mode_status('')
    messagebox.showerror('Error', f'Failed to change recording mode. Please try again.\n{error}')


def _create_recording_mode_card(parent):
    """Recording Mode 卡片：標題 + 二選一 segmented control。外觀跟
    SD Card 卡片一致（ROW_COLOR 底色、SETTINGS_ROW_TITLE_FONT 標題、
    padx=16/pady=(14,8) 標題間距）。"""
    global recording_mode_canvas, recording_mode_status_label

    card = tk.Frame(parent, bg=screen_gallery.ROW_COLOR)
    card.pack(fill='x', padx=20, pady=(4, 4))

    title_label = tk.Label(card, text='Recording Mode', font=SETTINGS_ROW_TITLE_FONT,
                            bg=screen_gallery.ROW_COLOR, fg=screen_gallery.TEXT_PRIMARY_COLOR)
    title_label.pack(anchor='w', padx=16, pady=(14, 8))

    total_width = SEGMENT_WIDTH * len(RECORDING_MODE_OPTIONS)
    recording_mode_canvas = tk.Canvas(card, width=total_width, height=SEGMENT_HEIGHT,
                                       bg=screen_gallery.ROW_COLOR, highlightthickness=0, cursor='hand2')
    recording_mode_canvas.pack(anchor='w', padx=16, pady=(0, 4))
    recording_mode_canvas.bind('<Button-1>', on_recording_mode_clicked)

    # 讀取中／切換中／讀取失敗的提示文字，平常是空的
    recording_mode_status_label = tk.Label(card, text='', font=('Calibri', 10),
                                           bg=screen_gallery.ROW_COLOR, fg=screen_gallery.TEXT_SECONDARY_COLOR)
    recording_mode_status_label.pack(anchor='w', padx=16, pady=(0, 10))

    # 實際值要等進 Settings 時背景 GET（load_recording_mode_from_device()），先不高亮
    _draw_recording_mode_selector(current_recording_mode)


def _create_engineering_tool_card(parent, title_text, button_text, on_click, pady):
    """Engineering Tools 底下的一張獨立卡片：標題 + 開啟按鈕，沒有說明
    文字。跟 Dashcam/SD Card 那些卡片同一套外觀（ROW_COLOR 底色、
    SETTINGS_ROW_TITLE_FONT 標題字體、padx=16/pady=(14,8) 的標題間距、
    style_dark_button() 按鈕樣式），呼叫端傳入的 pady 控制這張卡片本身
    跟上一張/下一張卡片之間的垂直間距，讓兩張卡片間距能保持一致。"""
    card = tk.Frame(parent, bg=screen_gallery.ROW_COLOR)
    card.pack(fill='x', padx=20, pady=pady)

    title_label = tk.Label(card, text=title_text, font=SETTINGS_ROW_TITLE_FONT,
                            bg=screen_gallery.ROW_COLOR, fg=screen_gallery.TEXT_PRIMARY_COLOR)
    title_label.pack(anchor='w', padx=16, pady=(14, 8))

    open_button = screen_gallery.style_dark_button(tk.Button(card, text=button_text, command=on_click))
    open_button.pack(anchor='w', padx=16, pady=(0, 14))


def build_screen(parent, on_back, on_open_race_track=None, on_open_iq_adjustment=None):
    """on_back：按下左上角 Back 時要呼叫的函式，由 DA005ControlPanel.pyw
    傳入（go_back_from_settings()），這裡不自己判斷要回哪個畫面——Settings
    只負責顯示 Back 這個入口，「回哪裡」的規則（實際 Gallery 狀態優先於
    進入 Settings 前記錄的來源）是 DA005ControlPanel.pyw 的職責，不要在
    這裡重複一份判斷邏輯。

    on_open_race_track：2026-09-22 新增，按下 Engineering Tools 裡「Race
    Track Simulation」入口按鈕時呼叫。
    on_open_iq_adjustment：2026-09-22 第二輪新增，按下 Engineering Tools
    裡「IQ Adjustment」入口按鈕時呼叫。
    兩個都一樣由 DA005ControlPanel.pyw 決定要切去哪個畫面，這裡只負責
    顯示這兩顆入口。"""
    global dvr_switch_canvas, dvr_mode_status_label
    global format_button, format_status_label
    global back_button_image, on_back_callback
    global on_open_race_track_callback, on_open_iq_adjustment_callback

    on_back_callback = on_back
    on_open_race_track_callback = on_open_race_track
    on_open_iq_adjustment_callback = on_open_iq_adjustment
    frame = tk.Frame(parent, bg=screen_gallery.BACKGROUND_COLOR)

    back_button_image = _new_back_chevron_image()
    back_button_label = tk.Label(frame, image=back_button_image, text=' Back', compound='left',
                                  font=('Calibri', 16, 'bold'), bg=screen_gallery.BACKGROUND_COLOR,
                                  fg=screen_gallery.TEXT_PRIMARY_COLOR, cursor='hand2')
    back_button_label.pack(pady=(20, 10), padx=20, anchor='w')
    back_button_label.bind('<Button-1>', lambda event: on_back_callback())

    # ---- 2026-08-26 使用者要求新增垂直 Scrollbar：內容超過可視高度時
    # 可以上下捲動。做法跟 screen_gallery.create_scrollable_list_area()
    # 同一套既有模式（Canvas + Scrollbar + 內層 Frame，用 create_window()
    # 貼上去、<Configure> 更新 scrollregion/寬度），只是這裡是第一次在
    # Settings 畫面用，獨立寫一份，不去改動 Gallery 那份共用函式。下面
    # 所有卡片改成 pack 進 content_inner（可捲動的內層 Frame），不再直接
    # pack 進 frame——只是換了容器，卡片本身的內容/邏輯/子元件不變。
    # Scrollbar 外觀：trough 用跟頁面同色（讓軌道「隱形」）、thumb 用
    # CONTROL_COLOR/CONTROL_HOVER_COLOR（跟其他可互動元件同一階亮度），
    # width 縮小到 8px 儘量接近 HCP3 的細滾動條。
    scroll_container = tk.Frame(frame, bg=screen_gallery.BACKGROUND_COLOR)
    scroll_container.pack(fill='both', expand=True)

    content_canvas = tk.Canvas(scroll_container, bg=screen_gallery.BACKGROUND_COLOR, highlightthickness=0)
    # 2026-08-26 使用者要求檢查能否再貼近深色 UI：elementborderwidth=0
    # 拿掉上下箭頭按鈕原本的 3D 立體邊框，跟其餘 bd=0/relief='flat' 一致，
    # 屬於安全的小調整。真正拿掉「上下箭頭按鈕」做成 HCP3 那種純細長
    # thumb 樣式，classic tk.Scrollbar 沒有對應選項，需要換成 ttk.Style
    # 自訂 layout 或整個換成 Canvas 手畫的拖曳元件——這是較大改動，這輪
    # 不做，維持現狀。
    content_scrollbar = tk.Scrollbar(
        scroll_container, orient='vertical', command=content_canvas.yview,
        width=8, bd=0, highlightthickness=0, relief='flat', elementborderwidth=0,
        bg=screen_gallery.CONTROL_COLOR, activebackground=screen_gallery.CONTROL_HOVER_COLOR,
        troughcolor=screen_gallery.BACKGROUND_COLOR)
    content_inner = tk.Frame(content_canvas, bg=screen_gallery.BACKGROUND_COLOR)
    content_inner_window_id = content_canvas.create_window((0, 0), window=content_inner, anchor='nw')
    content_inner.bind('<Configure>',
                        lambda event: content_canvas.configure(scrollregion=content_canvas.bbox('all')))
    content_canvas.bind('<Configure>',
                         lambda event: content_canvas.itemconfig(content_inner_window_id, width=event.width))
    content_canvas.configure(yscrollcommand=content_scrollbar.set)
    content_canvas.pack(side='left', fill='both', expand=True)
    content_scrollbar.pack(side='right', fill='y')

    def _on_settings_mouse_wheel(event):
        # Windows 滾輪固定回報 delta 的 ±120 倍數，要除以 120 換算成「格數」；
        # macOS 的 delta 本身就已經是很小的整數（通常 ±1~±3），不能再除以
        # 120（除下去 int() 幾乎都會變成 0，滾輪等於完全沒反應）。用
        # sys.platform 分開處理，Windows 行為不變。
        if sys.platform == 'win32':
            scroll_units = int(-1 * (event.delta / 120))
        else:
            scroll_units = int(-1 * event.delta)
            if scroll_units == 0:
                scroll_units = -1 if event.delta > 0 else 1
        content_canvas.yview_scroll(scroll_units, 'units')
    content_canvas.bind('<Enter>', lambda event: content_canvas.bind_all('<MouseWheel>', _on_settings_mouse_wheel))
    content_canvas.bind('<Leave>', lambda event: content_canvas.unbind_all('<MouseWheel>'))

    # ---- Dashcam（原 DVR Mode，2026-08-26 使用者要求：拿掉「DVR Mode」
    # 標題文字、Switch 移到最右側對齊、主項目文字比照 SD Card 標題字體，
    # 既有邏輯/globals 完全不動）----
    dvr_mode_card = tk.Frame(content_inner, bg=screen_gallery.ROW_COLOR)
    # 2026-08-26 使用者要求縮小四個主區塊彼此之間的垂直間距（約 60%：
    # 20px -> 8px），只調整區塊「之間」的 gap，不動最外側跟 Back 按鈕的
    # 間距——所以這裡上緣維持原本的 10，只把下緣（跟下一個區塊相鄰的
    # 那一側）從 10 降到 4，下面三個區塊同樣道理，只調整互相相鄰的那幾側。
    dvr_mode_card.pack(fill='x', padx=20, pady=(10, 4))

    switch_row = tk.Frame(dvr_mode_card, bg=screen_gallery.ROW_COLOR)
    switch_row.pack(fill='x', padx=16, pady=(14, 14))

    dashcam_label = tk.Label(switch_row, text='Dashcam', font=SETTINGS_ROW_TITLE_FONT,
                              bg=screen_gallery.ROW_COLOR, fg=screen_gallery.TEXT_PRIMARY_COLOR)
    dashcam_label.pack(side='left')

    dvr_switch_canvas = tk.Canvas(switch_row, width=SWITCH_WIDTH, height=SWITCH_HEIGHT,
                                   bg=screen_gallery.ROW_COLOR, highlightthickness=0, cursor='hand2')
    dvr_switch_canvas.pack(side='right')
    dvr_switch_canvas.bind('<Button-1>', on_dvr_switch_clicked)

    # 2026-08-26 使用者要求移除「Dashcam is currently ON/OFF.」這行狀態
    # 文字的 UI 顯示——這個 Label 本身仍然建立（refresh_dvr_mode_display()/
    # on_dvr_switch_clicked()/on_switch_apply_done() 等既有函式都會呼叫
    # 它的 .config(text=...)，一行都不能刪），只是刻意不呼叫 .pack()，
    # 不會出現在畫面上。Switch 的實際功能/狀態判斷/API 邏輯完全不受影響。
    dvr_mode_status_label = tk.Label(dvr_mode_card, text='Loading current DVR mode...', font=('Calibri', 9),
                                      bg=screen_gallery.ROW_COLOR, fg=screen_gallery.TEXT_SECONDARY_COLOR)

    # ---- Event review mode（2026-08-26 新增，純 UI 模擬，獨立卡片，
    # 見 _create_mock_toggle_row() 說明）----
    event_review_card = tk.Frame(content_inner, bg=screen_gallery.ROW_COLOR)
    event_review_card.pack(fill='x', padx=20, pady=(4, 4))
    _create_mock_toggle_row(event_review_card, 'Event review mode', default_on=True, pady=(14, 14),
                             font=SETTINGS_ROW_TITLE_FONT)

    # ---- Start event recording（2026-08-26 新增，可展開/收合，預設收合，
    # 純 UI 模擬，獨立卡片，見 _create_expandable_section()/
    # _create_mock_toggle_row() 說明）----
    start_event_recording_card = tk.Frame(content_inner, bg=screen_gallery.ROW_COLOR)
    start_event_recording_card.pack(fill='x', padx=20, pady=(4, 4))
    start_event_content = _create_expandable_section(
        start_event_recording_card, 'Start event recording', default_expanded=False)
    for sub_label in ('Headlight flasher', 'Hazard warning lights',
                       'When active front assist is triggered', 'Heavy braking'):
        _create_mock_toggle_row(start_event_content, sub_label, default_on=True, indent=True)

    # ---- Recording Mode（2026-10-14 新增，純 UI，見 _create_recording_mode_card() 說明）----
    _create_recording_mode_card(content_inner)

    # ---- SD Card ----
    sd_card_card = tk.Frame(content_inner, bg=screen_gallery.ROW_COLOR)
    sd_card_card.pack(fill='x', padx=20, pady=(4, 10))

    sd_card_title = tk.Label(sd_card_card, text='SD Card', font=SETTINGS_ROW_TITLE_FONT,
                              bg=screen_gallery.ROW_COLOR, fg=screen_gallery.TEXT_PRIMARY_COLOR)
    sd_card_title.pack(anchor='w', padx=16, pady=(14, 8))

    format_button = screen_gallery.style_dark_button(
        tk.Button(sd_card_card, text='Format SD Card', command=on_format_button_click))
    format_button.pack(anchor='w', padx=16)

    format_status_label = tk.Label(sd_card_card, text='', font=('Calibri', 9),
                                    bg=screen_gallery.ROW_COLOR, fg=screen_gallery.TEXT_SECONDARY_COLOR)
    format_status_label.pack(anchor='w', padx=16, pady=(8, 14))

    # ---- Engineering Tools（2026-09-22 新增，2026-09-22 第二輪把 Race
    # Track Simulation 併進來，2026-09-22 第三輪改成兩張獨立卡片）：每個
    # 工具都是獨立畫面（screen_race_track.py/screen_iq_adjustment.py），
    # 這裡只負責顯示入口＋「按下去切過去」，畫面本身的 UI/欄位邏輯完全
    # 不在這支檔案裡。"Engineering Tools" 是這一區的 section title，本身
    # 不包成卡片——直接畫在 content_inner（跟卡片同一層），底下兩張卡片
    # 各自獨立、寬度一致（都是 padx=20，跟其他卡片一樣）、標題/按鈕位置
    # 一致（都呼叫同一支 _create_engineering_tool_card()）。----
    engineering_tools_title = tk.Label(content_inner, text='Engineering Tools', font=SETTINGS_ROW_TITLE_FONT,
                                        bg=screen_gallery.BACKGROUND_COLOR, fg=screen_gallery.TEXT_PRIMARY_COLOR)
    engineering_tools_title.pack(anchor='w', padx=20, pady=(10, 6))

    _create_engineering_tool_card(
        content_inner, 'IQ Adjustment', 'Open IQ Adjustment',
        lambda: on_open_iq_adjustment_callback() if on_open_iq_adjustment_callback else None,
        pady=(0, 4))
    _create_engineering_tool_card(
        content_inner, 'Race Track Simulation', 'Open Race Track Simulation',
        lambda: on_open_race_track_callback() if on_open_race_track_callback else None,
        pady=(0, 10))

    initialize_desired_dvr_mode_from_actual()
    refresh_format_button_state()

    return frame


def refresh_format_button_state():
    """Format SD Card 按鈕的 enable/disable 完全依賴共用的 api.is_sd_card_
    present() 狀態，不自己另外判斷一套 SD Presence。呼叫時機：
    build_screen() 初始化一次；每次切回 Settings 分頁（DA005ControlPanel.
    pyw 的 _enter_settings_screen()）；App Startup／KeepAlive 通知 Memory
    有變化時（DA005ControlPanel.pyw 的 SD Card 全域監測，見
    screen_recording.py），這裡本身不會另外 poll Memory。

    Format 操作本身進行中（_format_in_progress）時不動這顆按鈕，避免跟
    「Formatting...」這個進行中狀態互相覆蓋——SD Card 狀態理論上不會在
    Format 進行中改變，但如果剛好收到一次不相關的同步呼叫，也不應該
    讓按鈕在操作進行中被意外改回 normal。"""
    if _format_in_progress:
        return
    format_button.config(state=('normal' if api.is_sd_card_present() else 'disabled'))


# ============================================================
# DVR Mode Switch：畫 Switch / 開機時從 actual 推斷起始 desired / Toggle
# ============================================================

def draw_switch(is_on):
    """畫出目前 Switch 的外觀：track 原本是兩個 oval + 一個 rectangle 湊出
    圓角效果，knob 是一個 oval，全部都是 Canvas 原生 primitive 在最終尺寸
    下直接畫，邊緣會有鋸齒。2026-08-17 改成呼叫 ui_shapes（PIL 高解析度
    畫完再 LANCZOS 縮小＋cache），track 用 make_pill_photo_image()、knob
    用 make_circle_photo_image()，都用 canvas.create_image() 貼上去，
    ON/OFF 依 is_on 畫在右邊/左邊的邏輯不變。"""
    dvr_switch_canvas.delete('all')
    track_color = SWITCH_ON_COLOR if is_on else SWITCH_OFF_COLOR
    radius = SWITCH_HEIGHT / 2

    track_image = ui_shapes.make_pill_photo_image(SWITCH_WIDTH, SWITCH_HEIGHT, fill=track_color)
    dvr_switch_canvas.create_image(0, 0, image=track_image, anchor='nw')

    knob_diameter = SWITCH_KNOB_RADIUS * 2
    knob_image = ui_shapes.make_circle_photo_image(knob_diameter, fill=SWITCH_KNOB_COLOR)
    knob_x = SWITCH_WIDTH - radius if is_on else radius
    dvr_switch_canvas.create_image(knob_x, radius, image=knob_image, anchor='center')

    # PhotoImage 沒有其他參照會被 GC 回收，用 canvas 屬性存住（每次 draw_switch()
    # 都會覆蓋掉上一次的參照，但 dvr_switch_canvas.delete('all') 已經把舊的
    # Canvas item 一起清掉了，不會有「item 沒了但參照還留著」的情況）。
    dvr_switch_canvas.switch_image_refs = (track_image, knob_image)


def is_desired_switch_on():
    return api.get_desired_dvr_mode() == DvrSystemState.DVR_SYSTEM_STATE_RECORD_WITHOUT_RINGBUFFER


def refresh_dvr_mode_display():
    """每次切回 Settings Tab 都呼叫這個。desired_dvr_mode 是這個工具 session
    內穩定的值（只有使用者按 Switch 才會變），所以這裡只是單純依目前的
    desired_dvr_mode 重畫 Switch，不需要重新打 API。"""
    is_on = is_desired_switch_on()
    draw_switch(is_on)
    dvr_mode_status_label.config(text=f'Dashcam is currently {"ON" if is_on else "OFF"}.')


def initialize_desired_dvr_mode_from_actual():
    """只有 build_screen() 第一次執行時會呼叫這個（Settings 畫面只會被
    build 一次），背景 GET 一次目前真實的 dvr_mode，幫 desired_dvr_mode
    決定一個起始值，然後畫出對應的 Switch 狀態。"""
    thread = threading.Thread(target=_initialize_desired_worker, daemon=True)
    thread.start()


def _initialize_desired_worker():
    inferred_dvr_system_state = None
    try:
        mode = api.get_dvr_mode()
        if mode.dvr_mode in (DvrSystemState.DVR_SYSTEM_STATE_RECORD_WITHOUT_RINGBUFFER,
                              DvrSystemState.DVR_SYSTEM_STATE_STANDBY):
            inferred_dvr_system_state = mode.dvr_mode
    except Exception as error:
        print(f'[Settings] 開機時讀取目前 DVR 狀態失敗，desired_dvr_mode 維持預設值: {error}')
    dvr_mode_status_label.after(0, lambda: _apply_initial_desired(inferred_dvr_system_state))


def _apply_initial_desired(inferred_dvr_system_state):
    """套用開機推斷出來的 desired_dvr_mode。如果使用者在這個背景 GET 完成
    之前就已經自己按過 Switch（user_touched_switch），使用者的選擇優先，
    這裡就不能再覆蓋過去——只重畫目前的 Switch 狀態，不改 desired。"""
    if not user_touched_switch and inferred_dvr_system_state is not None:
        api.set_desired_dvr_mode(inferred_dvr_system_state)
    refresh_dvr_mode_display()


def on_dvr_switch_clicked(event):
    global switch_busy, user_touched_switch
    if switch_busy:
        return

    user_touched_switch = True
    target_is_on = not is_desired_switch_on()
    target_dvr_system_state = (DvrSystemState.DVR_SYSTEM_STATE_RECORD_WITHOUT_RINGBUFFER if target_is_on
                                else DvrSystemState.DVR_SYSTEM_STATE_STANDBY)

    switch_busy = True
    draw_switch(target_is_on)
    dvr_mode_status_label.config(text=f'Turning dashcam {"ON" if target_is_on else "OFF"}...')
    thread = threading.Thread(target=_apply_switch_worker, args=(target_dvr_system_state, target_is_on), daemon=True)
    thread.start()


def _apply_switch_worker(target_dvr_system_state, target_is_on):
    success = api.apply_and_set_desired_dvr_mode(target_dvr_system_state)
    dvr_mode_status_label.after(0, lambda: on_switch_apply_done(success, target_is_on))


def on_switch_apply_done(success, target_is_on):
    global switch_busy
    switch_busy = False
    if success:
        draw_switch(target_is_on)
        dvr_mode_status_label.config(text=f'Dashcam is currently {"ON" if target_is_on else "OFF"}.')
    else:
        # 失敗：Switch UI 回復原本狀態。這裡直接依 desired_dvr_mode 重畫即可，
        # 因為 apply_and_set_desired_dvr_mode() 失敗時完全沒有呼叫
        # set_desired_dvr_mode()，desired 還是原來（沒被更動過）的值。
        refresh_dvr_mode_display()
        messagebox.showerror('Error', 'Failed to change dashcam mode. Please try again.')


# ============================================================
# SD Card Format：確認對話框 -> 確認 Standby -> Format -> 恢復 desired -> 重新整理 Gallery
# ============================================================

def on_format_button_click():
    global _format_in_progress
    if not show_format_confirmation_dialog():
        return  # Cancel：不送任何 Format command

    _format_in_progress = True
    format_button.config(state='disabled')
    format_status_label.config(text='Formatting...')
    thread = threading.Thread(target=_format_sd_card_worker, daemon=True)
    thread.start()


def show_format_confirmation_dialog():
    """跟原生 messagebox.askyesno 不同，這裡要自己畫一個 Dark Theme 的
    確認視窗，按鈕文字要是 Cancel / Format（不是原生 Yes/No）。
    回傳 True 表示使用者按了 Format，False 表示 Cancel 或直接關掉視窗。"""
    result = {'confirmed': False}

    dialog = tk.Toplevel(format_button)
    dialog.title('Confirm Format')
    dialog.configure(bg=screen_gallery.ROW_COLOR)
    dialog.resizable(False, False)
    dialog.transient(format_button.winfo_toplevel())
    dialog.grab_set()

    heading_label = tk.Label(dialog, text='Format SD Card?', font=('Calibri', 13, 'bold'),
                              bg=screen_gallery.ROW_COLOR, fg=screen_gallery.TEXT_PRIMARY_COLOR)
    heading_label.pack(padx=24, pady=(20, 10))

    message_label = tk.Label(
        dialog,
        text='All recordings and photos on the SD card will be deleted.\nThis action cannot be undone.',
        bg=screen_gallery.ROW_COLOR, fg=screen_gallery.TEXT_SECONDARY_COLOR,
        font=('Calibri', 10), justify='left')
    message_label.pack(padx=24, pady=(0, 18))

    button_row = tk.Frame(dialog, bg=screen_gallery.ROW_COLOR)
    button_row.pack(pady=(0, 18))

    def on_cancel():
        result['confirmed'] = False
        dialog.destroy()

    def on_format_confirmed():
        result['confirmed'] = True
        dialog.destroy()

    cancel_button = screen_gallery.style_dark_button(tk.Button(button_row, text='Cancel', command=on_cancel))
    cancel_button.pack(side='left', padx=(0, 10))

    format_confirm_button = screen_gallery.style_dark_button(
        tk.Button(button_row, text='Format', command=on_format_confirmed))
    format_confirm_button.pack(side='left')

    dialog.protocol('WM_DELETE_WINDOW', on_cancel)
    dialog.wait_window()
    return result['confirmed']


def _format_sd_card_worker():
    """Format 前先確認 DVR 真的處於字面上的 STANDBY——沿用 AutoMate 所有
    案例都遵守的前提（先 STANDBY 才 Format）。這裡改用
    screen_recording.is_standby_mode()（直接比對 dvr_mode 欄位本身），
    不能用 derive_dvr_state()：那個函式對 WITHOUT_RINGBUFFER（新規則下
    Settings ON 時的正常待機狀態）也會回報 'standby'，會讓這裡誤判成
    「已經是 Standby」而跳過真正切到 Standby 的步驟，實際上 WITHOUT_RINGBUFFER
    並不是字面上的 STANDBY，直接 Format 不符合 AutoMate 驗證過的前提。"""
    try:
        mode = api.get_dvr_mode()
    except Exception as error:
        format_status_label.after(0, lambda captured_error=error: on_format_error(captured_error))
        return

    if not screen_recording.is_standby_mode(mode):
        format_status_label.after(0, lambda: format_status_label.config(text='Stopping recording before format...'))
        try:
            api.stop_recording()
        except Exception as error:
            format_status_label.after(0, lambda captured_error=error: on_format_error(captured_error))
            return

        if not screen_recording.confirm_standby_mode():
            format_status_label.after(0, lambda: on_format_error(
                Exception('Failed to switch to Standby before formatting.')))
            return

        format_status_label.after(0, lambda: format_status_label.config(text='Formatting...'))

    try:
        api.format_sd_card()
    except Exception as error:
        format_status_label.after(0, lambda captured_error=error: on_format_error(captured_error))
        return

    sleep(FORMAT_SETTLE_SECONDS)  # 跟 AutoMate 案例一樣，留一點時間讓 DVR 真的處理完

    # Format 完成後 DVR 可能自動回到 WITH_RINGBUFFER，不能讓工具停留在這個
    # 狀態——一定要重新套用 Settings 的 desired_dvr_mode（可能是
    # WITHOUT_RINGBUFFER 或 STANDBY，視 Switch ON/OFF 而定）。
    if not api.restore_desired_dvr_mode():
        format_status_label.after(0, lambda: on_format_error(
            Exception('SD card formatted, but failed to restore DVR mode afterwards.')))
        return

    format_status_label.after(0, on_format_success)


def on_format_success():
    global _format_in_progress
    _format_in_progress = False
    refresh_format_button_state()  # Format 剛完成，SD 卡顯然還在，正常會回到 enabled
    format_status_label.config(text='')
    # Format 後原有 Video/Photo 應該都不見了，本地端的 Gallery cache 已經
    # 跟 DVR 真實狀態不一致，一定要強制重新整理（force_refresh()），不能用
    # ensure_loaded()——那個只有「這個 process 第一次進入 Gallery」才會真的
    # 打 API，Format 通常不是第一次，用錯函式會變成 Format 完畫面卻沒更新。
    screen_gallery.force_refresh()


def on_format_error(error):
    global _format_in_progress
    _format_in_progress = False
    refresh_format_button_state()
    format_status_label.config(text='Failed to format SD card.')
    messagebox.showerror('Error', f'Failed to format SD card.\n{error}')
