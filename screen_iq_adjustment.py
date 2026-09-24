"""
screen_iq_adjustment.py — IQ Adjustment 畫面

2026-09-22 新增。從 Settings -> Engineering Tools 進入的獨立工程工具頁面，
用來調整 7 個影像品質（IQ）參數。這一版刻意「只做 UI 與欄位操作」：

- 不 import requests、不 import 任何 protobuf 模組（audi_pb2 等）、不
  hardcode Dashcam URL、不呼叫任何 RESTful API——這支檔案完全不知道
  IQ 參數實際上要怎麼送到裝置。
- 「Apply」按鈕只把目前 7 個欄位收集成一個 plain dict，交給 on_apply_iq
  callback（由 DA005ControlPanel.pyw 傳入）。目前呼叫端傳的是 None——
  還沒有 IQ 設定的 RESTful 傳輸邏輯，先保留這個 placeholder，之後要串接
  時只要在 DA005ControlPanel.pyw 那行 build_screen() 呼叫換一個真正的
  函式進來，這支檔案完全不用改。

IQ_DEFAULTS 說明（重要）：
2026-09-23 改成使用者實測 GET /dashcam/cameraConfiguration 真機回應的實際
值（不是先前規格範例的猜測值）。Contrast/Saturation 對應
isp_dynamic_filter1/isp_dynamic_filter2，是使用者跟 RD 確認過的結果，
其餘欄位直接抄真機 GET 回應的原始值。但這裡仍然只是「進頁面時 UI 顯示
的初始值」，不是「頁面每次進入都重新 GET 一次」——如果之後裝置端的實際
設定值改變了（例如透過其他工具調整過），這裡的常數不會自動跟著變，
Reset 按鈕恢復的也是這組寫死的值，不是「當下裝置的最新設定」。之後如果
要做到「進頁面自動 GET 現況」，這裡的初始值來源要改成呼叫
iq_adjustment_service 讀目前設定，不是繼續讀這個常數。

payload dict 的 key（Apply 時收集出來，交給 on_apply_iq）：
    noise_reduction   (0~63)
    edge_enhancement  (1024~65535)
    colour_red        (0~8192)
    colour_blue       (0~8192)
    brightness        (0~255)
    contrast          (0~255)
    saturation        (0~127)

顏色/字型/按鈕樣式全部沿用 screen_gallery.py 的 Dark Theme 常數
（BACKGROUND_COLOR/ROW_COLOR/TEXT_PRIMARY_COLOR/style_dark_button()），
跟 screen_settings.py/screen_race_track.py 是同一套，不另外發明新的
視覺風格。

2026-09-23 新增 Live View：右側 Live View Preview 改成真的顯示畫面，
不再只是靜態 placeholder 文字。這支檔案完全不知道畫面來源是 RTSP 還是
UVC——只透過 build_screen() 傳入的 live_view_controller（
live_view_controller.LiveViewController 的實例）呼叫：
    live_view_controller.start_live_view()
    live_view_controller.stop_live_view()
    live_view_controller.retry_live_view()
    live_view_controller.get_status()        -> (status, error_message, source_label)
    live_view_controller.get_latest_frame()   -> (width, height, rgb24_bytes) 或 None
不 import video_source.py，不 hardcode RTSP URL，不呼叫 cv2.VideoCapture
或 ffmpeg command，不自己開背景 thread 讀串流——這些全部是
live_view_controller.py／video_source.py 的責任。畫面輪詢（.after()）
在這支檔案裡進行，是因為「多久跟 controller 要一次最新畫面/狀態」本來
就是 UI 層的節奏，跟「怎麼把 RTSP/UVC 轉成畫面」是兩件事。
"""

import sys
import threading
import tkinter as tk

from PIL import Image, ImageTk

import screen_gallery
import ui_shapes
import live_view_controller as live_view_controller_module

FIELD_LABEL_FONT = ('Calibri', 10)
FIELD_ENTRY_FONT = ('Calibri', 10)
RANGE_FONT = ('Calibri', 9)
ERROR_TEXT_COLOR = '#ff6b6b'

BACK_CHEVRON_ICON_SIZE = 18

CARD_SIDE_PADDING = 24  # card 跟 content area 左右邊界的間距（card 本身寬度自適應視窗，不寫死）
SLIDER_LENGTH = 230  # 2026-09-23 第五輪拉長（原本 180，約 +28%，落在使用者要求的 25~35%），thumb 變細後改用更長軌道呈現「精準調參」的感覺
SLIDER_HANDLE_LENGTH = 8  # 2026-09-23 第五輪縮細（原本 14），跟拉長的軌道搭配起來更像細長的指針，不是寬把手
SLIDER_TRACK_THICKNESS = 10  # 2026-09-23 第五輪從 8 微調到 10，軌道更容易看清楚（thumb 變細後軌道本身要更明顯才不會太不顯眼）
# 軌道/把手顏色：軌道用比 ROW_COLOR 更暗的 BACKGROUND_COLOR（凹陷感），
# 把手用比 ROW_COLOR 更亮的 TEXT_MUTED_COLOR（隨時看得清楚，不用等 hover/
# 拖曳才變亮），拖曳中再亮到 TEXT_PRIMARY_COLOR。
SLIDER_TROUGH_COLOR = screen_gallery.BACKGROUND_COLOR
SLIDER_HANDLE_COLOR = screen_gallery.TEXT_MUTED_COLOR
SLIDER_HANDLE_ACTIVE_COLOR = screen_gallery.TEXT_PRIMARY_COLOR
ENTRY_CHAR_WIDTH = 7  # tk.Entry 的 width 是字元數不是 px；量測過 Calibri 10pt 下約等於 55px，落在 55~65px 目標範圍

# (payload_key, 顯示文字, 最小值, 最大值)
# 2026-09-22 第三輪使用者要求拿掉 Colour Red/Blue 的 warning 提示文字，
# 不需要任何 warning——原本的第五個欄位（提示文字）整個移除。
IQ_FIELD_SPECS = [
    ('noise_reduction', 'Noise Reduction', 0, 63),
    ('edge_enhancement', 'Edge Enhancement', 1024, 65535),
    ('colour_red', 'Colour Red', 0, 8192),
    ('colour_blue', 'Colour Blue', 0, 8192),
    ('brightness', 'Brightness', 0, 255),
    ('contrast', 'Contrast', 0, 255),
    ('saturation', 'Saturation', 0, 127),
]

# 見檔案開頭的說明：這組是使用者實測真機 GET /dashcam/cameraConfiguration
# 回應的實際值（2026-09-23），不是規格範例的猜測值。
IQ_DEFAULTS = {
    'noise_reduction': 32,   # isp_noise_reduction
    'edge_enhancement': 1024,  # isp_redge_enhancement
    'colour_red': 493,        # isp_color_red
    'colour_blue': 552,       # isp_color_blue
    'brightness': 128,        # isp_brightness
    'contrast': 32,           # isp_dynamic_filter1
    'saturation': 64,         # isp_dynamic_filter2
}

on_back_callback = None
on_apply_iq_callback = None
on_get_iq_settings_callback = None
back_button_image = None  # PhotoImage 沒有其他參照會被 GC 回收，用模組變數存住
status_label = None
apply_button = None  # Apply 進行中需要 disable，見 on_apply_button_click()

# ---- Live View（2026-09-23 新增）----
LIVE_VIEW_TICK_INTERVAL_MS = 67  # 約 15fps，跟 video_source.py 的 RTSPVideoSource 預設 fps 對齊
LIVE_VIEW_CONNECTED_COLOR = '#4CAF50'  # 跟 screen_settings.py 的 Dashcam Switch ON 顏色一致，同一套「已連線/已開啟」語意

_live_view_controller = None  # build_screen() 傳進來的 live_view_controller.LiveViewController 實例，可能是 None
_live_view_active = False  # 目前這個頁面是不是「應該」在跑 Live View（_live_view_tick() 靠這個決定要不要繼續排下一次）
_live_view_image_ref = None  # PhotoImage 沒有其他參照會被 GC 回收，用模組變數存住
live_view_status_label = None
live_view_canvas = None
live_view_retry_button = None

# key -> {'var', 'entry', 'slider', 'error_label', 'min', 'max', 'label_text'}
_iq_field_widgets = {}
# key -> bool，Slider/Entry 互相同步時用來擋掉對方觸發自己、避免無窮遞迴
# 或不必要的重複處理（見 _make_slider_command()/_make_entry_change_handler()）。
_iq_sync_guard = {}


def _new_back_chevron_image():
    """畫法跟 screen_settings.py/screen_race_track.py 的
    _new_back_chevron_image() 一致（同一個左箭頭視覺語言），這裡獨立寫
    一份而不是互相 import 共用——這支函式很小，三個畫面模組各自維護一份
    完全相同的小函式，不為了它建立模組之間的相依關係。"""
    def draw_callback(draw, diameter, _height, _scale):
        cx, cy = diameter / 2, diameter / 2
        size = diameter * 0.32
        line_width = max(2, int(diameter * 0.14))
        points = [(cx + size * 0.6, cy - size), (cx - size * 0.6, cy), (cx + size * 0.6, cy + size)]
        draw.line(points, fill=screen_gallery.TEXT_PRIMARY_COLOR, width=line_width, joint='curve')

    image = ui_shapes.render_supersampled(BACK_CHEVRON_ICON_SIZE, BACK_CHEVRON_ICON_SIZE, draw_callback)
    return ImageTk.PhotoImage(image)


def _show_field_error(key, message):
    field = _iq_field_widgets[key]
    field['error_label'].config(text=message)
    field['entry'].config(highlightbackground=ERROR_TEXT_COLOR, highlightcolor=ERROR_TEXT_COLOR)


def _clear_field_error(key):
    field = _iq_field_widgets[key]
    field['error_label'].config(text='')
    field['entry'].config(highlightbackground=screen_gallery.CONTROL_COLOR,
                           highlightcolor=screen_gallery.CONTROL_HOVER_COLOR)


def _make_slider_command(key):
    """Slider 被拖動（或被 Entry 那邊透過共用的 IntVar 設定）時呼叫：
    把目前 Slider 的值同步寫回 Entry。用 _iq_sync_guard 擋掉「Entry 觸發
    Slider、Slider 又觸發 Entry」的來回遞迴。"""
    def on_slider_move(value_str):
        if _iq_sync_guard.get(key):
            return
        field = _iq_field_widgets[key]
        _iq_sync_guard[key] = True
        try:
            current_text = field['entry'].get()
            new_text = str(int(round(float(value_str))))
            if current_text != new_text:
                field['entry'].delete(0, 'end')
                field['entry'].insert(0, new_text)
            _clear_field_error(key)
        finally:
            _iq_sync_guard[key] = False
    return on_slider_move


def _make_entry_change_handler(key):
    """Entry 內容變更確認時（Enter 或失焦）呼叫：驗證後同步寫回 Slider
    （透過共用的 tk.IntVar），驗證失敗顯示錯誤、不動 Slider、不 crash。"""
    def on_entry_change(event=None):
        if _iq_sync_guard.get(key):
            return
        field = _iq_field_widgets[key]
        label_text = field['label_text']
        min_value, max_value = field['min'], field['max']
        text = field['entry'].get().strip()

        if not text:
            _show_field_error(key, f'{label_text} cannot be empty.')
            return
        try:
            value = int(text)
        except ValueError:
            _show_field_error(key, f'{label_text} must be a whole number.')
            return
        if value < min_value or value > max_value:
            _show_field_error(key, f'{label_text} must be between {min_value} and {max_value}.')
            return

        _clear_field_error(key)
        _iq_sync_guard[key] = True
        try:
            field['var'].set(value)  # tk.Scale 綁同一個 IntVar，這裡會連動移動 Slider
        finally:
            _iq_sync_guard[key] = False
    return on_entry_change


def _create_iq_row(parent, key, label_text, min_value, max_value):
    """建立一列 IQ 參數控制項，固定兩行：
    第一行 Label＋Range（緊接在 Label 後面）
    第二行 Slider＋Entry（緊接在 Slider 右側）
    （驗證錯誤訊息只在真的有錯誤時才佔用額外空間，見下面 error_label 的
    說明——不是恆定的第三行）。

    2026-09-23 第四輪：Slider 縮短＋軌道/把手顏色重新設計（跟背景更高
    對比）、Entry 維持一般樣式（沒有、也不要變成 Spinbox 的上下箭頭）、
    Entry 緊接 Slider 右側、整體間距再收緊一點，更像工程參數面板。
    Slider/Entry 雙向同步邏輯完全沒改，這裡純粹是排版/顏色。"""
    row = tk.Frame(parent, bg=screen_gallery.ROW_COLOR)
    row.pack(fill='x', padx=16, pady=(0, 3), anchor='w')  # 原本 5，再收緊

    label_row = tk.Frame(row, bg=screen_gallery.ROW_COLOR)
    label_row.pack(anchor='w')
    label = tk.Label(label_row, text=label_text, font=FIELD_LABEL_FONT,
                      bg=screen_gallery.ROW_COLOR, fg=screen_gallery.TEXT_SECONDARY_COLOR)
    label.pack(side='left')
    range_label = tk.Label(label_row, text=f'{min_value} ~ {max_value}', font=RANGE_FONT,
                            bg=screen_gallery.ROW_COLOR, fg=screen_gallery.TEXT_MUTED_COLOR)
    range_label.pack(side='left', padx=(6, 0))  # 緊接在 Label 後面

    default_value = IQ_DEFAULTS[key]
    var = tk.IntVar(value=default_value)

    control_row = tk.Frame(row, bg=screen_gallery.ROW_COLOR)
    control_row.pack(anchor='w', pady=(1, 0))  # 原本 2，再收緊一點

    # tk.Scale 的 range 可能很大（Edge Enhancement 1024~65535），但這裡只
    # 要求「拖得動、Entry 能精確輸入」，不要求 Slider 本身有精細刻度顯示
    # ——showvalue=False，實際數值一律看右邊的 Entry，避免 Slider 上方
    # 又疊一層跟 Entry 重複、還可能不同步的數字。固定 length（不用
    # fill='x'/expand=True），7 個欄位起點/終點/寬度都相同，不會因為
    # card 變寬跟著被拉長。sliderlength 縮小成細長把手，視覺上是一條
    # 連續軌道加一個小把手，不是被截成兩段。
    #
    # 顏色：bg/activebackground 決定的是「把手」顏色（tk.Scale 沒有獨立
    # 的把手顏色選項，bd=0/highlightthickness=0 之後，widget 本身的
    # bg 只會顯出來在把手上）；troughcolor 是軌道本身。原本 bg 用
    # ROW_COLOR（等於背景色），把手在沒拖曳時幾乎看不到——這裡改成
    # SLIDER_HANDLE_COLOR（比背景亮）常駐可見，軌道改用比背景更暗的
    # SLIDER_TROUGH_COLOR（凹陷感），兩端都跟 ROW_COLOR 有明顯落差。
    slider = tk.Scale(control_row, from_=min_value, to=max_value, orient='horizontal',
                       variable=var, showvalue=False, length=SLIDER_LENGTH,
                       sliderlength=SLIDER_HANDLE_LENGTH, width=SLIDER_TRACK_THICKNESS,
                       bg=SLIDER_HANDLE_COLOR, fg=screen_gallery.TEXT_PRIMARY_COLOR,
                       troughcolor=SLIDER_TROUGH_COLOR, highlightthickness=0, bd=0,
                       activebackground=SLIDER_HANDLE_ACTIVE_COLOR, sliderrelief='flat')
    slider.pack(side='left', padx=(0, 4))  # 緊接 Entry，原本 6 再收緊

    # 一般 Entry 樣式（不是 Spinbox，沒有上下箭頭）：flat relief、bd=0，
    # highlightthickness 的邊框顏色平常跟 CONTROL_COLOR 背景同色（幾乎
    # 看不到邊框），只有驗證錯誤時才會被 _show_field_error() 換成紅色，
    # 平常看起來就是一個乾淨的輸入框。
    entry = tk.Entry(control_row, width=ENTRY_CHAR_WIDTH, font=FIELD_ENTRY_FONT, bg=screen_gallery.CONTROL_COLOR,
                      fg=screen_gallery.TEXT_PRIMARY_COLOR, insertbackground=screen_gallery.TEXT_PRIMARY_COLOR,
                      relief='flat', bd=0, highlightthickness=1, highlightbackground=screen_gallery.CONTROL_COLOR,
                      highlightcolor=screen_gallery.CONTROL_HOVER_COLOR, justify='right')
    entry.pack(side='left', ipady=3)
    entry.insert(0, str(default_value))

    # 錯誤文字預設是空字串、高度極小（不是恆定佔一整行的空白），有錯誤
    # 才會顯示出明顯的一行——維持「每個參數兩行」的訴求，錯誤只在真的
    # 發生時才多佔一點垂直空間。
    error_label = tk.Label(row, text='', font=('Calibri', 9), bg=screen_gallery.ROW_COLOR, fg=ERROR_TEXT_COLOR)
    error_label.pack(anchor='w')

    _iq_field_widgets[key] = {
        'var': var, 'entry': entry, 'slider': slider, 'error_label': error_label,
        'min': min_value, 'max': max_value, 'label_text': label_text,
    }

    slider.config(command=_make_slider_command(key))
    entry_change_handler = _make_entry_change_handler(key)
    entry.bind('<Return>', entry_change_handler)
    entry.bind('<FocusOut>', entry_change_handler)


def build_screen(parent, on_apply_iq=None, on_back=None, live_view_controller=None,
                  on_get_iq_settings=None):
    """建立 IQ Adjustment 畫面。

    on_apply_iq(payload)：按下「Apply」時呼叫，payload 是 7 個 IQ 欄位的
    plain dict（見檔案開頭的欄位對照表）。
    on_back：按下左上角「Back to Settings」時呼叫，由呼叫端決定要回哪個
    畫面（這個畫面固定從 Settings 的 Engineering Tools 進來，呼叫端會傳
    回 Settings 的函式，這裡不自己判斷）。
    live_view_controller：live_view_controller.LiveViewController 的實例
    （由 DA005ControlPanel.pyw 建構、決定裡面包的是 RTSPVideoSource 還是
    以後的 UVCVideoSource），這支檔案只透過它的 start_live_view()/
    stop_live_view()/retry_live_view()/get_status()/get_latest_frame()
    操作，完全不知道實際的影像來源是什麼。傳 None 的話 Live View 區域
    只會顯示，不會嘗試啟動任何東西（呼叫 start_live_view() 之類的公開
    函式時會直接安全地什麼都不做）。
    on_get_iq_settings()：進入頁面時呼叫一次（見 DA005ControlPanel.pyw 的
    _enter_iq_adjustment_screen() -> load_iq_settings_from_device()），
    回傳目前裝置真實的 7 個 IQ 欄位 plain dict，用來取代 IQ_DEFAULTS
    當作畫面初始顯示值（不影響 Restore Factory Defaults 按鈕——那顆
    按鈕固定恢復成 IQ_DEFAULTS，不是這裡讀到的即時值）。傳 None 的話
    load_iq_settings_from_device() 直接安全地什麼都不做，畫面會停在
    IQ_DEFAULTS。"""
    global on_back_callback, on_apply_iq_callback, on_get_iq_settings_callback
    global back_button_image, status_label, apply_button
    global _live_view_controller, live_view_status_label, live_view_canvas, live_view_retry_button

    on_back_callback = on_back
    on_apply_iq_callback = on_apply_iq
    on_get_iq_settings_callback = on_get_iq_settings
    _live_view_controller = live_view_controller
    _iq_field_widgets.clear()
    _iq_sync_guard.clear()

    frame = tk.Frame(parent, bg=screen_gallery.BACKGROUND_COLOR)

    back_button_image = _new_back_chevron_image()
    back_button_label = tk.Label(frame, image=back_button_image, text=' Back to Settings', compound='left',
                                  font=('Calibri', 16, 'bold'), bg=screen_gallery.BACKGROUND_COLOR,
                                  fg=screen_gallery.TEXT_PRIMARY_COLOR, cursor='hand2')
    back_button_label.pack(pady=(20, 4), padx=20, anchor='w')
    back_button_label.bind('<Button-1>', lambda event: on_back_callback())

    # 頁面層級的明顯標題，緊接在 Back 下面（跟 screen_race_track.py 的
    # page_title 同一個位置/字型），不是只靠卡片內部那個較小的標題。
    page_title = tk.Label(frame, text='IQ Adjustment', font=('Calibri', 16, 'bold'),
                           bg=screen_gallery.BACKGROUND_COLOR, fg=screen_gallery.TEXT_PRIMARY_COLOR)
    page_title.pack(padx=20, pady=(0, 8), anchor='w')

    # ---- 左右兩欄：左側 IQ 參數控制（40%）／右側 Live View Preview
    # （60%）。實測過 grid()+columnconfigure(weight=...) 這個 screen_
    # race_track.py 也在用的做法（那邊設定是 38/62），量出來的實際比例
    # 卻接近 50/50——grid 的 weight 只分配「超出各欄位自身內容最小需求」
    # 的空間，兩欄各自的內容最小寬度剛好都落在視窗一半左右，導致 weight
    # 幾乎不起作用，這是 grid 的既有限制，不是這裡新出現的 bug（Race
    # Track 那邊也有一樣的落差，但這次不動 screen_race_track.py）。改用
    # place(relwidth=...) 直接按比例切兩塊區域——這個機制是「不管內容
    # 多寬，一律照 relwidth 比例分配」，不會被子元件的內容最小寬度影響，
    # 才能保證量出來就是要求的 40/60，不是「大約看起來還可以」。----
    LEFT_COLUMN_RATIO = 0.40
    COLUMN_GAP_PX = 16  # 兩欄之間的視覺間距，跟 preview_frame 原本 padx=(12,20) 的量級接近

    body = tk.Frame(frame, bg=screen_gallery.BACKGROUND_COLOR)
    body.pack(fill='both', expand=True)

    # ---- 左側：可捲動區域，跟 screen_settings.py 完全一樣的
    # Canvas+Scrollbar 模式（anchor='nw' + itemconfig(width=...) 讓內層
    # Frame 跟著 Canvas 寬度伸縮），不是先前那個「anchor='n' 自己算置中
    # 座標」的寫法——那個寫法在特定視窗尺寸下會讓內容整塊被推到右側、
    # 左側留白，這裡改用已經在 Settings/Race Track 驗證過的寫法。----
    scroll_container = tk.Frame(body, bg=screen_gallery.BACKGROUND_COLOR)
    scroll_container.place(relx=0, rely=0, relwidth=LEFT_COLUMN_RATIO, relheight=1,
                            width=-(COLUMN_GAP_PX // 2))

    content_canvas = tk.Canvas(scroll_container, bg=screen_gallery.BACKGROUND_COLOR, highlightthickness=0)
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

    def _on_mouse_wheel(event):
        # 跟 screen_settings.py 同一套 Windows/macOS 滾輪換算差異處理。
        if sys.platform == 'win32':
            scroll_units = int(-1 * (event.delta / 120))
        else:
            scroll_units = int(-1 * event.delta)
            if scroll_units == 0:
                scroll_units = -1 if event.delta > 0 else 1
        content_canvas.yview_scroll(scroll_units, 'units')
    content_canvas.bind('<Enter>', lambda event: content_canvas.bind_all('<MouseWheel>', _on_mouse_wheel))
    content_canvas.bind('<Leave>', lambda event: content_canvas.unbind_all('<MouseWheel>'))

    # ---- 單一 card，寬度自適應 content area（fill='x'，左右各留
    # CARD_SIDE_PADDING），不是寫死的固定寬度。card 靠左排列（anchor='w'），
    # 不會被置中推到畫面右半部。----
    card = tk.Frame(content_inner, bg=screen_gallery.ROW_COLOR)
    card.pack(fill='x', padx=CARD_SIDE_PADDING, pady=(6, 20), anchor='w')

    for key, label_text, min_value, max_value in IQ_FIELD_SPECS:
        _create_iq_row(card, key, label_text, min_value, max_value)

    # ---- [Control]：Reset / Apply ----
    control_row = tk.Frame(card, bg=screen_gallery.ROW_COLOR)
    control_row.pack(fill='x', padx=16, pady=(4, 8))
    reset_button = screen_gallery.style_dark_button(
        tk.Button(control_row, text='Restore Factory Defaults', command=lambda: on_reset_button_click()))
    reset_button.pack(side='left')
    apply_button = screen_gallery.style_dark_button(
        tk.Button(control_row, text='Apply', command=lambda: on_apply_button_click()))
    apply_button.pack(side='right')

    status_label = tk.Label(card, text='Status: Ready', font=('Calibri', 9),
                             bg=screen_gallery.ROW_COLOR, fg=screen_gallery.TEXT_SECONDARY_COLOR)
    status_label.pack(anchor='w', padx=16, pady=(0, 14))

    # 進頁面時強制把捲動位置歸零，確保第一眼看到的是最上面的 Noise
    # Reduction，不是進頁面當下 scrollregion 還沒算完、殘留在某個中間
    # 位置。用 after_idle()，等這一輪所有 <Configure>/geometry 計算跑完
    # 才捲回頂端，不然這裡直接呼叫可能會被隨後才觸發的 <Configure>
    # 又捲動一次蓋掉。
    content_canvas.after_idle(lambda: content_canvas.yview_moveto(0))

    # ---- 右側：Live View Preview。2026-09-23：從靜態 placeholder 改成
    # 真的顯示畫面，但這支檔案完全不知道畫面來源是 RTSP 還是 UVC——只透過
    # _live_view_controller（build_screen() 傳進來的
    # live_view_controller.LiveViewController 實例）操作，見檔案開頭說明。
    preview_frame = tk.Frame(body, bg='#000000')
    preview_frame.place(relx=LEFT_COLUMN_RATIO, x=COLUMN_GAP_PX // 2, rely=0,
                         relwidth=1 - LEFT_COLUMN_RATIO, relheight=1,
                         width=-(COLUMN_GAP_PX // 2 + 20))

    preview_title = tk.Label(preview_frame, text='Live View Preview', font=('Calibri', 12, 'bold'),
                              bg='#000000', fg=screen_gallery.TEXT_SECONDARY_COLOR)
    preview_title.pack(pady=(16, 4))

    live_view_status_label = tk.Label(preview_frame, text='Connecting...', font=('Calibri', 11),
                                       bg='#000000', fg=screen_gallery.TEXT_MUTED_COLOR, justify='center')
    live_view_status_label.pack(pady=(0, 6))

    # 實際畫面畫在這個 Canvas 上（create_image），跟 screen_recording.py/
    # screen_race_track.py 的 Live View 顯示邏輯是同一種做法（黑底 Canvas
    # + 定期用最新一張畫面覆蓋），差別只是這裡的畫面來源是透過
    # LiveViewController 拿，不是直接呼叫 api.py。
    live_view_canvas = tk.Canvas(preview_frame, bg='#000000', highlightthickness=0)
    live_view_canvas.pack(fill='both', expand=True, padx=16, pady=(0, 8))

    # Retry 按鈕只有連線失敗（error 狀態）才會顯示，預設不 pack()。
    live_view_retry_button = screen_gallery.style_dark_button(
        tk.Button(preview_frame, text='Retry Live View', command=lambda: on_retry_live_view_click()))

    return frame


def set_status_text(text):
    status_label.config(text=f'Status: {text}')


def _apply_values_to_fields(values):
    """把一組 7 個 IQ 值（plain dict）寫回畫面上的 Entry/Slider，並清掉
    每個欄位的錯誤文字。load_iq_settings_from_device()（GET 到裝置真實值）
    跟 on_reset_button_click()（Restore Factory Defaults，固定用
    IQ_DEFAULTS）共用這個函式，差別只在於「值從哪裡來」。"""
    for key, field in _iq_field_widgets.items():
        value = values.get(key, IQ_DEFAULTS.get(key))
        _clear_field_error(key)
        _iq_sync_guard[key] = True
        try:
            field['entry'].delete(0, 'end')
            field['entry'].insert(0, str(value))
            field['var'].set(value)
        finally:
            _iq_sync_guard[key] = False


def on_reset_button_click():
    """Restore Factory Defaults：把畫面上 7 個 IQ 欄位恢復成 IQ_DEFAULTS
    （見檔案開頭說明），不是恢復成進頁面 GET 到的裝置目前值——只改畫面
    顯示，不呼叫任何 API、不會立即送到 Dashcam，使用者還是要另外按
    Apply 才會真的套用到裝置上。"""
    _apply_values_to_fields(IQ_DEFAULTS)
    set_status_text('Ready')


def load_iq_settings_from_device():
    """進入 IQ Adjustment 頁面時呼叫（見 DA005ControlPanel.pyw 的
    _enter_iq_adjustment_screen()）：重新 GET 一次裝置目前真實的 IQ 設定，
    取代畫面上顯示的初始值——不會把 IQ_DEFAULTS 這組寫死的數字當成真的
    現況顯示給使用者看。

    網路呼叫放進背景 thread，用 status_label.after(0, ...) 排回 main
    thread 才更新畫面，不會卡住 Tkinter。on_get_iq_settings_callback 是
    None（呼叫端還沒接上真正的 GET 函式）就直接安全地什麼都不做，畫面
    停在 IQ_DEFAULTS。"""
    if on_get_iq_settings_callback is None:
        return

    def worker():
        try:
            values = on_get_iq_settings_callback()
        except Exception as error:
            status_label.after(0, lambda captured_error=error: _on_load_done(None, captured_error))
            return
        status_label.after(0, lambda: _on_load_done(values, None))

    threading.Thread(target=worker, daemon=True).start()


def _on_load_done(values, error):
    if error is not None:
        print(f'[IQ Adjustment] 讀取目前 IQ 設定失敗: {error}')
        set_status_text(f'Failed to load current settings: {error}')
        return

    _apply_values_to_fields(values)
    set_status_text('Loaded current settings from device.')


def _collect_iq_payload():
    """重新驗證目前 7 個 Entry 的內容（不只依賴 Slider/IntVar 上次同步過
    的值）——使用者可能直接點 Apply 而不是先按 Enter/切換焦點，這裡要保證
    「畫面上顯示的數字」才是真正拿去組 payload 的數字。任何一個欄位驗證
    失敗就整個回傳 None，不會送出一部分正確、一部分是舊值的殘缺 payload。"""
    payload = {}
    all_valid = True
    for key, field in _iq_field_widgets.items():
        label_text = field['label_text']
        min_value, max_value = field['min'], field['max']
        text = field['entry'].get().strip()

        if not text:
            _show_field_error(key, f'{label_text} cannot be empty.')
            all_valid = False
            continue
        try:
            value = int(text)
        except ValueError:
            _show_field_error(key, f'{label_text} must be a whole number.')
            all_valid = False
            continue
        if value < min_value or value > max_value:
            _show_field_error(key, f'{label_text} must be between {min_value} and {max_value}.')
            all_valid = False
            continue

        _clear_field_error(key)
        payload[key] = value

    if not all_valid:
        return None
    return payload


def on_apply_button_click():
    """收集＋驗證 7 個欄位 -> 呼叫 on_apply_iq_callback(payload)。

    2026-09-23 改成真的呼叫 on_apply_iq_callback（由 DA005ControlPanel.pyw
    接到 iq_adjustment_service.apply_iq_settings），這是真正的網路請求
    （GET+PUT /dashcam/cameraConfiguration），所以放進背景 thread、用
    status_label.after(0, ...) 排回 main thread 才更新 UI，不會卡住
    Tkinter——跟 screen_race_track.py 的 Send Race Data 同一套做法。

    刻意完全不碰 Live View（不呼叫 stop_live_view()/start_live_view()）：
    Apply 只送 IQ 設定，RTSP 串流本來就應該繼續播放，讓使用者直接看到
    調整效果，不需要因為送了一次設定就重新連線。"""
    payload = _collect_iq_payload()
    if payload is None:
        set_status_text('Please fix invalid values before applying.')
        return

    if on_apply_iq_callback is None:
        # 理論上呼叫端應該已經接上真正的函式，這裡保留 placeholder 只是
        # 保險，避免呼叫端漏接時直接 AttributeError。
        set_status_text('(Placeholder) IQ settings ready to apply.')
        return

    apply_button.config(state='disabled')
    set_status_text('Applying...')

    def worker():
        try:
            on_apply_iq_callback(payload)
        except Exception as error:
            status_label.after(0, lambda captured_error=error: _on_apply_done(False, captured_error))
            return
        status_label.after(0, lambda: _on_apply_done(True, None))

    threading.Thread(target=worker, daemon=True).start()


def _on_apply_done(success, error):
    apply_button.config(state='normal')
    if success:
        set_status_text('IQ settings applied successfully.')
    else:
        print(f'[IQ Adjustment] Apply 失敗: {error}')
        set_status_text(f'Apply failed: {error}')


# ============================================================
# Live View（2026-09-23 新增）
# ============================================================
# 這一段完全透過 _live_view_controller（live_view_controller.LiveViewController
# 的實例）操作，不 import video_source.py、不知道實際來源是 RTSP 還是
# UVC。畫面輪詢用 live_view_canvas.after()（跟這個工具其他 Live View
# 顯示邏輯——screen_recording.py／screen_race_track.py——同一種「main
# thread 定期主動來問一次最新狀態/畫面」的做法，不會有背景 thread 直接
# 操作 Tk widget 的風險，因為背景 thread 全部都在 VideoSource 實作內部，
# 這裡完全沒有背景 thread）。

def start_live_view():
    """進入 IQ Adjustment 頁面時呼叫（見 DA005ControlPanel.pyw 的
    _enter_iq_adjustment_screen()）。_live_view_controller 是 None（呼叫端
    還沒接上真正的 controller）就直接安全地什麼都不做，Live View 區域
    會停在「Connecting...」，不會報錯也不會 crash。"""
    global _live_view_active
    if _live_view_controller is None:
        return
    if _live_view_active:
        return  # 已經在跑（tick 迴圈還在排），不要重複啟動
    _live_view_active = True
    live_view_retry_button.pack_forget()
    live_view_status_label.config(text='Connecting...', fg=screen_gallery.TEXT_MUTED_COLOR)
    _live_view_controller.start_live_view()
    _live_view_tick()


def stop_live_view():
    """離開 IQ Adjustment 頁面時呼叫（Back to Settings、切到其他畫面、
    或關閉主程式）。可以安全重複呼叫——_live_view_active 設 False 之後，
    就算 tick 迴圈已經排定的下一次 after() 觸發，也只會看到旗標是 False
    直接 return，不會再繼續排下一次，迴圈自然停止。"""
    global _live_view_active
    _live_view_active = False
    if _live_view_controller is not None:
        _live_view_controller.stop_live_view()
    if live_view_canvas is not None:
        live_view_canvas.delete('all')
    if live_view_retry_button is not None:
        live_view_retry_button.pack_forget()


def on_retry_live_view_click():
    """使用者按「Retry Live View」（只有 error 狀態才看得到這顆按鈕）：
    真的重新連線一次，不是只清掉錯誤文字假裝好了。"""
    if _live_view_controller is None:
        return
    live_view_retry_button.pack_forget()
    live_view_status_label.config(text='Connecting...', fg=screen_gallery.TEXT_MUTED_COLOR)
    _live_view_controller.retry_live_view()
    if not _live_view_active:
        # 正常情況下使用者會看到 Retry 按鈕，代表頁面還開著、_live_view_active
        # 應該已經是 True；這裡是防呆，避免萬一狀態不一致時 tick 迴圈沒有
        # 在跑，導致按了 Retry 卻永遠看不到後續狀態更新。
        start_live_view()


def _live_view_tick():
    """main thread 定期執行：跟 live_view_controller 要一次最新狀態/畫面，
    只顯示「目前拿到的這個狀態」，不會因為跟不上就累積或卡住。"""
    if not _live_view_active or _live_view_controller is None:
        return

    status, error_message, source_label = _live_view_controller.get_status()

    if status == live_view_controller_module.STATUS_CONNECTED:
        live_view_retry_button.pack_forget()
        live_view_status_label.config(text=f'Connected - {source_label}', fg=LIVE_VIEW_CONNECTED_COLOR)
        frame = _live_view_controller.get_latest_frame()
        if frame is not None:
            _render_live_view_frame(*frame)
    elif status == live_view_controller_module.STATUS_ERROR:
        live_view_canvas.delete('all')
        live_view_status_label.config(text=f'Live View unavailable\nError: {error_message}', fg=ERROR_TEXT_COLOR)
        live_view_retry_button.pack(pady=(0, 16))
    else:  # connecting / idle
        live_view_retry_button.pack_forget()
        live_view_status_label.config(text='Connecting...', fg=screen_gallery.TEXT_MUTED_COLOR)

    live_view_canvas.after(LIVE_VIEW_TICK_INTERVAL_MS, _live_view_tick)


def _render_live_view_frame(width, height, frame_bytes):
    """把 VideoSource 給的 raw RGB24 bytes 畫到 live_view_canvas 上：保持
    原始比例（等比例縮放＋置中，不是直接拉伸塞滿 Canvas），視窗縮放時
    Canvas 尺寸變了，下一次畫面自然就會用新的尺寸重算，不用另外綁
    <Configure>。如果 Canvas 還沒有真正的尺寸（頁面剛建立、還沒 realize，
    寬高可能是 1），直接跳過這一次，不要除以 0 或算出負值。"""
    global _live_view_image_ref
    canvas_width = live_view_canvas.winfo_width()
    canvas_height = live_view_canvas.winfo_height()
    if canvas_width <= 1 or canvas_height <= 1:
        return

    scale = min(canvas_width / width, canvas_height / height)
    display_width = max(1, int(width * scale))
    display_height = max(1, int(height * scale))

    frame_image = Image.frombytes('RGB', (width, height), frame_bytes).resize((display_width, display_height))
    _live_view_image_ref = ImageTk.PhotoImage(frame_image)
    live_view_canvas.delete('live_view_frame')
    live_view_canvas.create_image(canvas_width // 2, canvas_height // 2, anchor='center',
                                   image=_live_view_image_ref, tags='live_view_frame')
