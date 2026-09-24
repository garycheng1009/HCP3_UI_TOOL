"""
DA005ControlPanel.pyw — DA005 DVR Control Panel 主程式

畫面切換方式：同一個主視窗裡疊放多個 Frame（Recording / Gallery / Player / Settings），
用 tkraise() 切換要看哪一個，不是用 ttk.Notebook 分頁。
上方選單固定有 Recording、Gallery、Settings 三個按鈕；Favorite 是動態顯示/隱藏的第四個
按鈕（只有存在 Favorite 項目時才出現，見 update_favorite_tab_visibility()），
跟 Gallery 共用同一個 Frame，只是 screen_gallery 內部的顯示模式不同。Player 是點擊
影片後才會切過去的內容畫面，不是選單上的按鈕。

正式流程資料來源一律是 api.py（真正的 DVR REST API / Protobuf）。
mock_data.py 只保留給離線／沒有接真機時的手動測試用，正式流程不會 import 它。

單一 instance 限制：一開始就先檢查 single_instance，如果已經有一份在跑，
直接把那份帶到前景、結束自己，不往下 import api/screen_xxx（避免第二個
process 也去連線、也開背景執行緒，做一堆等一下就要丟掉的事）。
"""

import sys
import inspect
import threading
from time import time, perf_counter

import single_instance

# 主視窗標題：這裡跟下面 window.title() 用同一個常數，避免兩邊字串打錯字
# 導致 single instance 帶到前景時 FindWindowW 找不到視窗。
MAIN_WINDOW_TITLE = 'Dashcam Control Panel v2.0'

if not single_instance.try_acquire_single_instance_lock():
    single_instance.bring_existing_instance_to_front(MAIN_WINDOW_TITLE)
    sys.exit(0)

import tkinter as tk
from PIL import ImageTk

import api
import screen_recording
import screen_gallery
import screen_player
import screen_settings
import screen_race_track
import race_track_service
import screen_iq_adjustment
import iq_adjustment_service
import video_source
import live_view_controller
import screen_fake_ui
import ui_shapes
from resource_paths import resource_path

screens = {}
nav_tab_canvases = {}      # 'recording' / 'gallery' / 'favorite' -> Canvas 版膠囊分頁，見 create_nav_tab()
                            # settings 是 tk.Button（settings_menu_button），不放進這個 dict，見 set_active_nav_tab() 的說明
current_active_nav_tab = None
is_favorite_tab_visible = False   # Favorite 分頁動態顯示/隱藏，see update_favorite_tab_visibility()

# 2026-08-19 使用者要求 Gallery Tab 比照 Favorite 的顯示邏輯：Gallery 最終
# item 數（分組/篩選完成後的數字）= 0 就隱藏，> 0 才顯示。預設先給 True，
# 不是 False——多數情況下裝置本來就有錄影資料，先預設顯示比較不會在剛
# 啟動、還沒收到第一次 Gallery 數量回報前，讓 Tab 短暫消失又出現一次。
is_gallery_tab_visible = True

# _repack_top_bar_right_side() 用來判斷「顯示狀態跟上次呼叫時一樣就整個
# 跳過」的快取，見該函式的說明。_repack_top_nav_tabs() 不需要對應的快取
# ——它只會在 update_favorite_tab_visibility()/update_gallery_tab_
# visibility() 判斷「狀態真的改變」之後才被呼叫，呼叫端已經做掉這層過濾。
_top_bar_right_side_state = None

# current_screen_name：目前實際顯示的畫面名稱，每一個 show_screen() 都會更新，
# 包含 'player' 這個不是 Nav Tab 的畫面（current_active_nav_tab 刻意不算它，
# 見 set_active_nav_tab() 的說明，所以另外用這個變數才能正確判斷「目前是不是
# 在 Player 畫面」，見 leave_player_and_then()）。2026-08-19 使用者要求 Photo
# 併入同一個 Unified Media Viewer 之後，Photo 也是走 'player' 這個畫面
# （screen_player.py 內部用 current_media_type 分辨要顯示哪些 Controls），
# 不再有獨立的 'photo_viewer' 畫面。
current_screen_name = None

# 2026-08-19 使用者最後一輪視覺調整要求：Top Bar 背景要跟 Left Rail 外圍/
# Main Content 統一成同一個深灰色，不能再看起來像三個不同層級——直接沿用
# screen_gallery.BACKGROUND_COLOR（Main Content 本來就是這個顏色），不是
# 另外調一個新色碼。Active/Hover 這兩個「比底色稍亮」的顏色也跟著往上調
# 整（原本相對 #1a1a1a 分別 +18/+10，這裡維持同樣的相對亮度差，改成相對
# 新底色 #26262b 計算），不然底色變亮之後，原本的 Active/Hover 亮度差會
# 小到看不出來、甚至 Hover 顏色會比底色還暗（方向反了）。
#
# 2026-08-20 使用者試做過「Top Bar 改淺紫灰漸層殼」一版，看過截圖後不
# 接受，要求整個退回這一版原本的深色配色，不要在那個版本上繼續微調。
NAV_BAR_COLOR = screen_gallery.BACKGROUND_COLOR
NAV_TAB_ACTIVE_COLOR = '#38383d'
NAV_TAB_HOVER_COLOR = '#303035'    # 非目前分頁滑過去時，非常輕微的亮度變化
NAV_TAB_TEXT_COLOR = '#cccccc'
NAV_TAB_ACTIVE_TEXT_COLOR = 'white'

# Player 的「返回」要回到「進入 Player 之前那個畫面」（Recording 首頁或 Gallery），
# 不是固定寫死一個目的地——2026-08-11 這次要求「Gallery 進 Player，Back 要回
# Gallery（並且重新整理），不要回到 Recording」，但 Recording 首頁縮圖點進
# Player 這條路徑（維持不變，不動 Recording 頁面邏輯）還是要跳回 Recording，
# 所以用這個變數記住「這次是從哪個畫面進來的」。
_screen_before_player = 'recording'

# 2026-08-19 使用者要求：Settings 不是跟 Recording/Gallery 同層的主要
# Tab，是從目前畫面進入的次級頁面——進入 Settings 時就要明確記錄是從
# 'recording' 還是 'gallery' 進來的（Favorite 併入 'gallery'），Back 的
# 時候才知道要回哪裡；不能只靠當下 active tab 在返回那一刻重新猜測。
# 實際 Back 的判斷順序（見 go_back_from_settings()）：Gallery 已經沒有
# 資料/Tab 已隱藏這個「實際狀態」優先於這裡記錄的來源。
settings_return_screen = 'recording'

# 工具一開啟就開始背景送 keepalive，保持跟 DVR 的連線不斷
api.start_keepalive()


def register_screen(name, frame):
    """把畫面放進同一個位置疊起來，之後可以用 show_screen() 切換要看哪一個"""
    screens[name] = frame
    frame.place(x=0, y=0, relwidth=1, relheight=1)


def show_screen(name):
    """2026-08-18 修正「切換畫面時看到前一頁殘影」問題的一部分：所有畫面
    其實都用 place() 疊在同一個位置（見 register_screen()），tkraise()
    只是改變 Z-order，實際的螢幕重繪要等 Tk 事件迴圈下一次處理閒置事件
    才會真的發生。如果 tkraise() 之後緊接著做其他事（例如
    screen_recording.refresh_recording_page() 這種還會再動到 widget 的
    呼叫），畫面有可能在真正重繪新頁面之前，先讓使用者看到舊頁面殘留的
    畫面內容。這裡在 tkraise() 之後立刻呼叫 update_idletasks()，強制
    Tk 馬上把新頁面該有的畫面處理完、真的畫出來，才繼續往下做其他事，
    不用整個重構 Screen 切換架構。"""
    global current_screen_name
    previous_screen_name = current_screen_name
    current_screen_name = name
    if name in ('gallery', 'favorite'):
        # 2026-08-19 使用者回報「Recording -> Gallery Double Render」排查用
        # 的暫時診斷 log，只在切去 Gallery/Favorite 時印，不影響其他畫面。
        caller_name = inspect.stack()[1].function
        print(f'[Gallery Render Diag] t={time():.3f} stage=show_screen(\'{name}\') caller={caller_name} '
              f'previous_screen_name={previous_screen_name}')
    screens[name].tkraise()
    window.update_idletasks()
    set_active_nav_tab(name)

    # 2026-08-14 使用者確認規則：離開 Gallery/Favorite 分頁時要停止
    # Thumbnail Lazy Loading 的週期性可視範圍檢查，避免背景無意義一直跑。
    # Gallery/Favorite 是同一個 Frame，只要目前不是這兩個名稱就算離開了。
    # show_screen() 是唯一的畫面切換入口，這裡是唯一需要加這個判斷的地方。
    was_gallery_screen = previous_screen_name in ('gallery', 'favorite')
    is_gallery_screen = name in ('gallery', 'favorite')
    if was_gallery_screen and not is_gallery_screen:
        screen_gallery.on_gallery_screen_hidden()

    # 2026-08-18 使用者要求：離開 Recording 分頁時要停止 DVR 連線狀態的
    # 週期性檢查，避免背景無意義一直跑——跟上面 Gallery 的
    # on_gallery_screen_hidden() 是同一種做法。
    if previous_screen_name == 'recording' and name != 'recording':
        screen_recording.stop_connection_monitor_loop()
        # 2026-09-21 新增：Live View 借用 Recording 首頁的縮圖區域，離開
        # 這個畫面時如果還在跑，必須自動停止，避免殘留 ffmpeg 子行程。
        stop_live_view_if_active()

    # 2026-09-23 新增：IQ Adjustment 進頁面會自動開始 Live View（獨立的
    # RTSP/未來 UVC 來源，跟上面 Recording 首頁那個 Live View 是完全分開
    # 的兩套），離開這個畫面時必須自動停止，避免殘留 ffmpeg 子行程或第二次
    # 進來變成兩條 stream。
    if previous_screen_name == 'iq_adjustment' and name != 'iq_adjustment':
        screen_iq_adjustment.stop_live_view()

    # 2026-08-21 新增：切回 Recording 分頁時做一次輕量 freshness check
    # （見 screen_recording.check_recording_preview_freshness() 的說明），
    # 修「換 SD 卡後首頁 Preview 一直停留在舊縮圖」的問題——只在使用者
    # 真的切回來看這個畫面時檢查一次，不是新增高頻背景輪詢。
    if name == 'recording' and previous_screen_name != 'recording':
        screen_recording.check_recording_preview_freshness()

    # 2026-08-17 使用者要求：Filter/X/... 搬到最上方 Navigation Bar 後，
    # 只有實際在看 Gallery/Favorite 分頁時才顯示；2026-08-19 再加上
    # Settings 分頁本身要隱藏 Gear，統一放進 _repack_top_bar_right_side()
    # 一起管理，見該函式的說明。
    _repack_top_bar_right_side(name)


def leave_player_and_then(action):
    """統一的「離開 Player」入口：只要目前畫面是 Player，就先完整跑完
    screen_player.leave_player() 的 cleanup（停止播放、釋放 decoder、
    恢復 desired_dvr_mode、GET 驗證），跑完才執行 action()；如果目前根本
    不在 Player，直接執行 action()，不需要多跑一次無意義的 restore。

    Top Navigation 的 Recording/Gallery/Favorite/Settings 這四個入口都要
    共用這個，不能繞過去直接切畫面——2026-08-14 發現的 bug：這四個按鈕
    原本都是直接 show_screen()，完全沒有檢查目前是不是在 Player，導致
    「Player 播放中直接點 Recording Tab」時 DVR 還停在暫時的 STANDBY、
    desired_dvr_mode 沒有被恢復、Recording Page 也還是舊的按鈕顏色。
    Player 自己的 Back 按鈕（on_back_button_click()）已經是正確流程，
    不受這個函式影響。"""
    if current_screen_name == 'player':
        screen_player.leave_player(action)
    else:
        action()


NAV_TAB_WIDTH = 118
NAV_TAB_HEIGHT = 34
NAV_TAB_PILL_OUTLINE_COLOR = '#f2f0f4'   # Active Tab 的膠囊外框顏色（比對 HCP3 實機截圖）


def draw_pill_outline(canvas, x1, y1, x2, y2, outline, width):
    """畫一個「體育場形狀」(stadium/pill) 的外框——左右兩端半圓、上下直線，
    2026-08-14 對照 HCP3 實機截圖新增：Active Tab（例如 Aufnahme）是這種
    白色描邊膠囊外框，不是填滿的背景色塊。原本用兩個 create_arc(style=
    'arc') + 兩條 create_line 硬拼，Canvas 原生 arc 在這種小尺寸下邊緣
    會有鋸齒。2026-08-17 改成呼叫 ui_shapes（PIL 高解析度畫完再 LANCZOS
    縮小＋cache），用 canvas.create_image() 貼上去。"""
    width_px = round(x2 - x1)
    height_px = round(y2 - y1)
    photo_image = ui_shapes.make_pill_photo_image(
        width_px, height_px, fill=None, outline=outline, outline_width=width)
    canvas.create_image(x1, y1, image=photo_image, anchor='nw')
    canvas.pill_outline_image_ref = photo_image  # PhotoImage 沒有其他參照會被 GC 回收，用 canvas 屬性存住


def render_nav_tab(canvas, active, hover=False):
    """重畫一個 Top Navigation 文字分頁（Recording/Gallery/Favorite）：
    Active 畫白色描邊膠囊外框＋粗體字；非 Active 只畫文字，滑鼠移過去時
    背景給一點點輕微亮度變化（跟原本 tk.Button 版本的 hover 效果一致，
    只是現在改成重畫 Canvas，不是 .config(bg=...)）。"""
    canvas.delete('all')
    canvas.config(bg=NAV_TAB_HOVER_COLOR if (hover and not active) else NAV_BAR_COLOR)
    center_x = NAV_TAB_WIDTH / 2
    center_y = NAV_TAB_HEIGHT / 2
    if active:
        draw_pill_outline(canvas, 2, 2, NAV_TAB_WIDTH - 2, NAV_TAB_HEIGHT - 2,
                           outline=NAV_TAB_PILL_OUTLINE_COLOR, width=2)
        canvas.create_text(center_x, center_y, text=canvas.tab_text,
                            fill=NAV_TAB_ACTIVE_TEXT_COLOR, font=('Calibri', 11, 'bold'))
    else:
        canvas.create_text(center_x, center_y, text=canvas.tab_text,
                            fill=NAV_TAB_TEXT_COLOR, font=('Calibri', 11))


def create_nav_tab(parent, text, command):
    """建立一個 Top Navigation 文字分頁（Canvas 版本，取代原本的
    tk.Button）——2026-08-14 對照 HCP3 實機截圖：Active 分頁是圓角膠囊
    外框，一般 tk.Button 沒辦法簡單畫出這種外框，改用 Canvas 自己畫，
    點擊行為（command）維持一樣，只是綁在 <Button-1> 上。"""
    canvas = tk.Canvas(parent, width=NAV_TAB_WIDTH, height=NAV_TAB_HEIGHT,
                        bg=NAV_BAR_COLOR, highlightthickness=0, cursor='hand2')
    canvas.tab_text = text
    canvas.bind('<Button-1>', lambda event: command())
    render_nav_tab(canvas, active=False)
    return canvas


def set_active_nav_tab(name):
    """更新上方選單列 Highlight。Recording/Gallery/Favorite 是 Canvas 畫的
    膠囊分頁，Settings 是圖示按鈕（維持原本的 bg 變化，不套用膠囊樣式，
    真機截圖裡的齒輪本身也沒有膠囊外框）。Player 不是分頁按鈕（是從縮圖
    點進來的內容畫面），切到 Player 時維持原本的 Highlight 不變，不會
    清掉。"""
    global current_active_nav_tab
    if name not in ('recording', 'gallery', 'favorite', 'settings'):
        return
    current_active_nav_tab = name
    for tab_name, tab_canvas in nav_tab_canvases.items():
        render_nav_tab(tab_canvas, active=(tab_name == name))
    settings_menu_button.config(bg=NAV_TAB_ACTIVE_COLOR if name == 'settings' else NAV_BAR_COLOR)


def build_gear_icon_image(size=26):
    """Settings 分頁改成齒輪圖示、不顯示文字。2026-08-14 改用真的 UI 素材
    （assets/gear_icon.png，從 HCP3 UI 素材表裁切出來，保留原本的淺紫色——
    這個圖示放在深色 Top Bar 上，淺色線條對比才夠，跟左側面板那三個改成
    深色的圖示是相反方向，不能套用同一個顏色），取代原本用 PIL 手畫的
    齒輪剪影。"""
    return ImageTk.PhotoImage(screen_recording.load_asset_image('gear_icon.png', (size, size)))


def bind_nav_tab_hover(button, name):
    """非目前分頁的按鈕，滑鼠移過去給一點非常輕微的亮度變化；目前分頁本身
    已經有 Highlight，不需要額外處理 hover。settings 是 tk.Button（維持
    原本 .config(bg=...) 的做法），recording/gallery/favorite 是 Canvas
    （改用 render_nav_tab() 重畫，見該函式）。"""
    is_canvas_tab = name in ('recording', 'gallery', 'favorite')

    def on_enter(event):
        if current_active_nav_tab == name:
            return
        if is_canvas_tab:
            render_nav_tab(button, active=False, hover=True)
        else:
            button.config(bg=NAV_TAB_HOVER_COLOR)

    def on_leave(event):
        if current_active_nav_tab == name:
            return
        if is_canvas_tab:
            render_nav_tab(button, active=False, hover=False)
        else:
            button.config(bg=NAV_BAR_COLOR)

    button.bind('<Enter>', on_enter)
    button.bind('<Leave>', on_leave)


def go_to_recording_screen():
    """Recording Tab 點擊入口：如果目前在 Player，要先走完整的離開流程
    （見 leave_player_and_then()），不能直接切畫面。"""
    leave_player_and_then(_enter_recording_screen)


def _enter_recording_screen():
    """每次切到 Recording Tab 都要重新 GET dvr_mode 重畫整個畫面，不能沿用
    上次使用者按了什麼按鈕的舊 UI state（跟 go_to_settings_screen() 每次
    重新 GET 的理由一致）。"""
    show_screen('recording')
    screen_recording.refresh_recording_page()


def on_dvr_disconnected_globally():
    """2026-08-19 使用者要求：DVR 斷線時不論目前在哪個畫面，都要立刻讓
    該畫面正在進行的動作安全退出，回到 Recording 首頁並顯示既有的
    Disconnected Preview。連線狀態偵測本身完全沿用 screen_recording 既有
    的 KeepAlive-based 全域監測（見 screen_recording.start_global_
    connection_watcher()，只讀 api.is_dvr_connected() 這個記憶體旗標，
    不是另一套 GET polling），這裡只負責「目前在哪個畫面、該怎麼安全
    退出」，不重複發明連線偵測機制。"""
    global settings_return_screen
    if current_screen_name in ('gallery', 'favorite'):
        screen_gallery.cancel_pending_work_for_disconnect()
    elif current_screen_name == 'player':
        # Photo 已經併入 Player 共用同一套處理，這裡不用分開判斷。斷線時
        # 不能走正常的 leave_player()——那條路會呼叫
        # api.restore_desired_dvr_mode() 這個網路請求，斷線時呼叫只會
        # 卡住/失敗，改用專門給斷線用、不碰網路的清理流程。
        screen_player.cancel_pending_work_for_disconnect()

    # 2026-08-19 使用者要求：斷線時如果剛好停留在 Settings，之後不應該
    # 再讓 Back 把使用者送回 Gallery——沿用同一個全域斷線監測（不新增
    # Settings 專用的連線 polling），這裡只是額外把 Back 的來源記錄
    # 強制改回 Recording。
    settings_return_screen = 'recording'

    if current_screen_name != 'recording':
        show_screen('recording')
    screen_recording.show_dvr_disconnected_screen()


def on_dvr_reconnected_globally():
    """DVR 恢復連線：不管斷線當下在哪個畫面，都維持/確保停在 Recording
    首頁，不自動送回原本的 Gallery/Favorite/Player。

    2026-08-20 使用者要求：不要直接呼叫 refresh_recording_page() 恢復
    Recording 按鈕——斷線期間 DVR 很可能剛好 Restart/Reboot，actual mode
    可能回到 WITH_RINGBUFFER；改呼叫
    screen_recording.recover_baseline_then_refresh_recording_page()，
    先在背景檢查、需要的話先做一次 WITHOUT_RINGBUFFER + NO_EVENT baseline
    recovery 並確認成功，最後才呼叫既有的 refresh_recording_page() 重新
    同步 DVR Mode/Recording state/recordings/preview。Gallery/Favorite Tab
    要不要重新出現，繼續交給 refresh_recording_page() 內部已經在呼叫的
    screen_gallery.report_gallery_item_count_from_recordings()（既有機制，
    這裡不用另外處理，也不會多打一次 API）。"""
    if current_screen_name != 'recording':
        show_screen('recording')
    screen_recording.recover_baseline_then_refresh_recording_page()


screen_recording.on_dvr_disconnected_global_callback = on_dvr_disconnected_globally
screen_recording.on_dvr_reconnected_global_callback = on_dvr_reconnected_globally


def on_sd_card_removed_globally():
    """2026-08-20 使用者要求：SD 卡在工具執行中被拔除時，Gallery/Favorite/
    Player（含 Photo，共用同一個畫面）這幾個依賴媒體/SD 卡的畫面要立刻
    安全退出，回到 Recording 首頁顯示「Please insert SD card.」。連線
    狀態偵測沿用既有的 KeepAlive-based 全域監測（見 screen_recording.
    start_global_connection_watcher() 裡新增的 SD Card Presence 檢查，
    只在 KeepAlive 通知 Memory 有變化時才 GET 一次，不是新的週期性
    polling），這裡只負責「目前在哪個畫面、該怎麼安全退出」，沿用既有的
    active-screen/request-token/cleanup 機制，不建立第二套 lifecycle：
    - Gallery/Favorite：cancel Selection Mode、清除勾選、停止 Lazy
      Loading，不執行 pending 的 Delete/Favorite——跟 DVR 斷線要做的事
      完全一樣，直接沿用 screen_gallery.cancel_pending_work_for_
      disconnect()，不重寫一份幾乎一樣的程式碼。
    - Player：停止 decoder/播放、讓 request token 失效，不等待無意義的
      media request——同樣沿用 screen_player.cancel_pending_work_for_
      disconnect()。
    Settings 不在這個清單裡：SD 卡拔除不影響使用者停留在 Settings 設定
    別的項目，只有 Format 按鈕本身要 disable（透過共用的 SD Card 狀態，
    見 screen_settings.py），這裡不處理畫面切換。"""
    if current_screen_name in ('gallery', 'favorite'):
        screen_gallery.cancel_pending_work_for_disconnect()
    elif current_screen_name == 'player':
        screen_player.cancel_pending_work_for_disconnect()

    if current_screen_name not in ('recording', 'settings'):
        show_screen('recording')
    if current_screen_name == 'recording':
        screen_recording.show_no_sd_card_preview()

    # 2026-08-20 使用者要求：Settings 的 Format 按鈕不自己判斷一套 SD
    # Presence，共用同一個狀態；如果使用者當下正好停留在 Settings，SD
    # 卡拔除也要立刻同步 Format 按鈕（disable），不用等下次重新進入
    # Settings 才更新——呼叫本身不會另外 GET Memory，只是依剛更新好的
    # api.is_sd_card_present() 重畫按鈕狀態。
    screen_settings.refresh_format_button_state()


def on_sd_card_inserted_globally():
    """SD 卡插回：跟 DVR 斷線恢復不同，這裡不強制把使用者切回 Recording
    畫面——SD 卡拔除當下 Gallery/Favorite/Player 已經被強制導回
    Recording（見 on_sd_card_removed_globally()），使用者這時候只可能
    停留在 Recording 或 Settings，插卡不需要打斷使用者正在 Settings 做
    的事。但不管使用者目前在哪個畫面，都呼叫既有的 refresh_recording_
    page()——它會重新 GET DvrMode/recordings，把 Recording 分頁自己的
    Preview/按鈕更新好（就算現在沒被看到，下次切過去就是最新的），並且
    透過既有的 report_gallery_item_count_from_recordings() 更新 Gallery
    Tab 顯示狀態（這件事本來就不受目前畫面影響，是全域 Navigation Bar
    外觀，見該函式的說明）——沿用既有 refresh 路徑，不是只把先前快取的
    Thumbnail 顯示回來。"""
    screen_recording.refresh_recording_page()
    screen_settings.refresh_format_button_state()  # 使用者可能停留在 Settings，立刻同步 Format 按鈕（disable -> enable）


screen_recording.on_sd_card_removed_global_callback = on_sd_card_removed_globally
screen_recording.on_sd_card_inserted_global_callback = on_sd_card_inserted_globally


def go_to_gallery_screen():
    """單純切到 Gallery 畫面（切 view mode + 顯示），不含任何「要不要重新
    整理」的判斷——這支函式是給程式內部導覽用的（例如 Favorite 最後一筆
    被取消時，update_favorite_tab_visibility() 自動切回 Gallery），不能
    連帶觸發整個 Gallery 重新整理，不然會違反「單筆 Favorite/Unfavorite
    不得觸發完整 Gallery refresh」的規則。使用者真的點 Gallery Tab 要用
    on_gallery_tab_clicked()。"""
    caller_name = inspect.stack()[1].function
    print(f'[Gallery Render Diag] t={time():.3f} stage=go_to_gallery_screen caller={caller_name}')
    screen_gallery.show_gallery_view()
    show_screen('gallery')


def on_gallery_tab_clicked():
    """使用者實際點擊上方 Gallery Tab：如果目前在 Player，先走完整的離開
    流程（見 leave_player_and_then()）。"""
    leave_player_and_then(_enter_gallery_screen_from_tab)


def _switch_to_gallery_or_favorite_with_cover_protection(view_setter_fn, screen_name):
    """2026-08-19 Ghosting 正式修法、2026-08-20 擴充成共用入口：所有「真的
    是不同 Frame、tkraise() 才會換到 Gallery/Favorite」的來源都要走這裡，
    不建立第二套 Cover/repaint workaround——Recording -> Gallery（原本
    唯一支援的路徑）、Player(Video)/Player(Photo，共用同一個畫面) ->
    Gallery/Favorite（2026-08-20 使用者真機回報同一種殘影，見下面的
    coming_from_screen_needing_cover 判斷）都共用同一套：

        raise Cover -> 切到 Gallery/Favorite -> force_refresh() ->
        Gallery 真的 tkraise 完、版面 settle -> lower Cover

    coming_from_screen_needing_cover：只要「切換前」的畫面不是 Gallery/
    Favorite 本身（那是同一個 Frame 內部切換 View Mode，見
    go_to_gallery_screen()/go_to_favorite_screen() 的說明，不會有
    tkraise() 造成的殘影風險，不需要 Cover），就套用保護——涵蓋
    Recording、Player（含 Photo）這幾種確實會 tkraise() 到 Gallery 的
    情況，不是只鎖定 Recording。

    呼叫端（on_gallery_tab_clicked() 的 leave_player_and_then()、
    go_back_from_player() 的 Player 自己的 Back 按鈕）都已經確保 Player
    的 stop_video()/decoder cleanup/request token 失效/DVR mode 恢復
    在呼叫這支函式之前就已經完整跑完——這裡不重複、不跳過、也不改變那段
    lifecycle 的順序，只負責「切過去 Gallery/Favorite 這一步」本身的
    殘影保護。

    這裡維持用 window.update()（不是 update_idletasks()/after_idle()）：
    真機已經驗證過這個順序能讓殘影消失。查過 Tcl/Tk 的文件，
    update idletasks 只處理 idle callback（geometry 重新計算、排隊中的
    after idle），明確不處理視窗系統的 repaint/expose 事件；after_idle()
    排的回呼本身也只會在「idle」這個事件分類裡執行，一樣不會觸發真正的
    畫面重繪。只有 update()（處理 TCL_ALL_EVENTS）才會真的把 OS 層級的
    repaint 事件處理掉。這裡刻意只用在這一個轉場、只呼叫兩次、範圍很小
    （不是到處這樣用），reentrancy 風險可控；如果换成 update_idletasks()/
    after_idle()，等於是拿掉已經驗證有效的修法去賭一個理論上就不會處理
    repaint 事件的替代方案，這裡選擇不冒這個風險。"""
    print(f'[Gallery Render Diag] t={time():.3f} stage=_switch_to_gallery_or_favorite_with_cover_protection '
          f'target={screen_name} current_screen_name(before)={current_screen_name}')
    coming_from_screen_needing_cover = current_screen_name not in ('gallery', 'favorite')
    if coming_from_screen_needing_cover:
        content_cover_frame.tkraise()
        window.update()

    view_setter_fn()
    show_screen(screen_name)
    print(f'[Gallery Render Diag] t={time():.3f} stage=_switch_to_gallery_or_favorite_with_cover_protection '
          f'即將呼叫 screen_gallery.force_refresh()（view_setter_fn() 剛剛已經同步 render 過一次）')
    screen_gallery.force_refresh()

    if coming_from_screen_needing_cover:
        window.update()
        content_cover_frame.lower()


def _enter_gallery_screen_from_tab():
    """每次都要強制重新整理，拿 DVR 最新資料——不能只在第一次載入（那是
    ensure_loaded() 的行為，這裡改用 force_refresh()）。Gallery/Favorite
    之間單純切換 view（不是點 Gallery Tab 這個動作本身）仍然維持不重新
    整理，見 go_to_gallery_screen()／go_to_favorite_screen() 的說明。

    2026-08-20 使用者新規則：這是「Recording -> Gallery」（含 Settings
    Back 沿用同一條路徑，見 go_back_from_settings()）的入口，一律要捲動
    重設回頂端，所以這裡傳 reset_scroll_to_top=True 給 show_gallery_
    view()。Player/Photo Viewer -> Gallery 刻意不是走這裡（見
    go_back_from_player()，維持不傳、保留捲動位置），兩者共用同一支
    show_gallery_view()，差別只在呼叫端傳不傳這個參數。"""
    _switch_to_gallery_or_favorite_with_cover_protection(
        lambda: screen_gallery.show_gallery_view(reset_scroll_to_top=True), 'gallery')


def go_to_favorite_screen():
    """Favorite Tab 點擊入口：如果目前在 Player，先走完整的離開流程
    （見 leave_player_and_then()）。"""
    leave_player_and_then(_enter_favorite_screen)


def _enter_favorite_screen():
    """Favorite 分頁跟 Gallery 是同一個 Frame（screens['favorite'] 指向
    同一份物件），只是 current_view_mode 不同（只顯示 is_favorite 的
    group）——Favorite 本來就只是目前 Gallery 資料的篩選結果，不是另外
    一份資料，所以理由跟 go_to_gallery_screen() 一致，一樣用
    ensure_loaded()，不會每次切過去都重新 GET。

    2026-08-20 使用者新規則：這是「(Recording/Gallery) -> Favorite」點
    Favorite Tab 的入口，一律要捲動重設回頂端，傳 reset_scroll_to_top=
    True 給 show_favorite_view()，理由/差異同 _enter_gallery_screen_
    from_tab()。"""
    screen_gallery.show_favorite_view(reset_scroll_to_top=True)
    show_screen('favorite')
    screen_gallery.ensure_loaded()


def _repack_top_nav_tabs():
    """全部 unpack 再照正確順序重新 pack：Tkinter 的 pack() 在 forget 之後
    重新插入，會被排到目前已 pack 元件的最後面，不會自動回到原本的
    相對位置，所以 Gallery/Favorite 的顯示狀態改變時都整排重排一次，
    順序才會固定是 Recording / Gallery / Favorite。settings_menu_button
    （齒輪）不受這個排序影響——它是 side='right'，固定釘在 Top Navigation
    最右側，不管 Gallery/Favorite 有沒有顯示都不會被這個函式動到，所以
    不放進來一起 forget/repack。

    2026-08-19 使用者要求 Gallery Tab 比照 Favorite 的顯示邏輯之後，
    update_favorite_tab_visibility()/update_gallery_tab_visibility() 兩邊
    都要重排，抽出來共用一份，不要各自維護一份重複的 pack 邏輯。

    2026-09-17 macOS 實機回報：切換 Recording/Gallery/Favorite/Settings
    分頁後，Top Navigation 的按鈕偶發消失。這幾顆 Tab 是 Canvas 畫的
    （見 create_nav_tab()），macOS 的 Tk 對「Canvas 剛被 pack_forget()
    又立刻 pack() 回來」偶爾會來不及在下一次畫面重繪前完成 geometry
    計算，導致那顆 Canvas 暫時量不到自己的顯示區域、畫不出東西。這裡本身
    呼叫頻率不高（只有 Favorite/Gallery 數量真的跨過 0 這個門檻才會被
    呼叫一次，見呼叫端），不需要像 _repack_top_bar_right_side() 那樣另外
    加「狀態沒變就跳過」的快取；只在 macOS 上、repack 完之後多呼叫一次
    window.update_idletasks()，強制 Tk 在把控制權交還事件迴圈之前，先把
    這幾顆 Canvas 的 geometry/繪製算完，不改變 Windows 原本的行為/時序。"""
    for button in (recording_menu_button, gallery_menu_button, favorite_menu_button):
        button.pack_forget()
    recording_menu_button.pack(side='left', padx=(16, 6), pady=5)
    if is_gallery_tab_visible:
        gallery_menu_button.pack(side='left', padx=6, pady=5)
    if is_favorite_tab_visible:
        favorite_menu_button.pack(side='left', padx=6, pady=5)

    if sys.platform == 'darwin':
        window.update_idletasks()


def update_favorite_tab_visibility(favorite_count):
    """screen_gallery 每次 refresh 完都會呼叫這個，回報目前有幾筆
    Favorite。Favorite Tab 是動態顯示/隱藏的：>0 才出現，=0 就消失；
    如果使用者正好停在 Favorite 分頁時數量歸零，要自動切回 Gallery
    （不能讓使用者卡在一個已經消失的分頁上）。

    這個「停在 Favorite 但分頁其實已經不可見」的檢查特地放在最前面、
    不受下面「顯示狀態沒變就直接 return」影響——目前這支工具本身的操作
    路徑不會走到這個狀態（Favorite 按鈕本身沒顯示就點不到），但這是很
    低成本的防禦性檢查，之後萬一改動別的地方不小心製造出這個狀態，
    這裡也能自己修正回來，不會讓使用者卡住。

    2026-08-19：如果 Gallery Tab 這次也一併被 update_gallery_tab_visibility()
    隱藏、使用者已經被導去 Recording，這裡 current_active_nav_tab 已經不是
    'favorite' 了，不會再重複導去一個同樣被隱藏的 Gallery（見
    on_recordings_loaded() 裡兩個 notify 函式的呼叫順序說明）。"""
    global is_favorite_tab_visible
    should_show = favorite_count > 0

    if not should_show and current_active_nav_tab == 'favorite':
        go_to_gallery_screen()

    if should_show == is_favorite_tab_visible:
        return
    is_favorite_tab_visible = should_show
    _repack_top_nav_tabs()


def update_gallery_tab_visibility(gallery_item_count):
    """screen_gallery 每次 refresh 完、或 screen_recording.py 完成拍照/
    停止 Scenic/Event 結束/重新連線後都會呼叫這個，回報目前 Gallery 最終
    item 數（分組/篩選完成後的數字，不是 raw get_recordings() 筆數）。
    機制完全比照 update_favorite_tab_visibility()：>0 才顯示，=0 就隱藏；
    如果使用者正停留在 Gallery 或 Favorite（Favorite 是 Gallery 的子集，
    Gallery 都空了 Favorite 一定也空），自動切回 Recording，不能讓使用者
    卡在一個已經沒有 Nav Tab 對應的畫面上。

    2026-08-19 使用者要求：Gallery item count 歸零的當下（不論是 Format
    SD Card 還是其他任何原因造成，不新增 Format 專用的判斷），如果使用者
    正停留在 Settings，之後按 Back 不能被導去一個已經沒有內容、Tab 也
    已經隱藏的 Gallery——沿用這同一個既有的 Gallery item count 回報機制，
    把 Back 的來源記錄強制改回 Recording，見 go_back_from_settings()。"""
    global is_gallery_tab_visible, settings_return_screen
    should_show = gallery_item_count > 0

    if not should_show:
        settings_return_screen = 'recording'

    if not should_show and current_screen_name in ('gallery', 'favorite'):
        go_to_recording_screen()  # 跟 Recording Tab 點擊走同一個入口，不是直接 show_screen()

    if should_show == is_gallery_tab_visible:
        return
    is_gallery_tab_visible = should_show
    _repack_top_nav_tabs()


def _determine_settings_return_screen():
    """Settings 只認兩種來源：'recording' 或 'gallery'（Favorite 併入
    'gallery'——Back 回去一樣走 go_back_from_settings() 裡 Gallery 那條
    路徑，不需要另外記一個 'favorite' 狀態）。Top Bar 的 Gear 全程都在
    （不會因為切到 Player 就隱藏），理論上使用者可能在 Player 播放中點
    Gear，這裡用 _screen_before_player（Player 自己 Back 要回去的畫面）
    當依據，不會誤判成 'recording'。"""
    source_screen = _screen_before_player if current_screen_name == 'player' else current_screen_name
    return 'gallery' if source_screen in ('gallery', 'favorite') else 'recording'


def go_to_settings_screen():
    """Settings 齒輪點擊入口：先記錄這次是從哪個畫面進來的（Back 用，見
    go_back_from_settings()），再走既有的「如果目前在 Player，先完整
    離開」流程。必須在 leave_player_and_then()/screen_player.leave_player()
    執行之前就記錄——那兩支都只是做清理（停止播放、恢復
    desired_dvr_mode），不會改動 current_screen_name/_screen_before_player，
    所以在使用者按下 Gear 的當下記錄是最新、最正確的依據。"""
    global settings_return_screen
    settings_return_screen = _determine_settings_return_screen()
    leave_player_and_then(_enter_settings_screen)


def _enter_settings_screen():
    """每次切到 Settings 都要重新畫 Switch（refresh_dvr_mode_display()
    只是依目前的 desired_dvr_mode 重畫，不用重新 GET，見該函式的說明），
    不能顯示舊的畫面。同時同步一次 Format SD Card 按鈕的 enable/disable
    （refresh_format_button_state()，共用 api.is_sd_card_present() 這個
    既有狀態，不是重新 GET Memory——確保就算 App Startup 那次背景 GET
    Memory 完成得比使用者第一次點進 Settings 慢，實際看到 Settings 時
    按鈕狀態一定是正確的）。"""
    show_screen('settings')
    screen_settings.refresh_dvr_mode_display()
    screen_settings.refresh_format_button_state()


def go_back_from_settings():
    """Settings 左上角 Back：使用者明確要求的判斷順序是「實際狀態優先於
    進入 Settings 前記錄的來源」：
    1. Gallery 目前已經沒有任何項目（Tab 已經隱藏）-> 一律回 Recording，
       不管 settings_return_screen 記的是什麼（例如 Format SD Card 把
       Gallery 清空了，不能讓使用者回到一個已經不存在、Tab 也已經隱藏
       的 Gallery）。
    2. 否則依 settings_return_screen 判斷：'gallery' -> 回 Gallery，沿用
       _enter_gallery_screen_from_tab() 既有的 cache-first force_refresh
       行為（跟使用者直接點 Gallery Tab 回來是同一條路徑，不寫第二套，
       不會額外造成 Loading/Double Render/重新下載縮圖）；其他 -> 回
       Recording。"""
    if not is_gallery_tab_visible:
        go_to_recording_screen()
    elif settings_return_screen == 'gallery':
        _enter_gallery_screen_from_tab()
    else:
        go_to_recording_screen()


def go_to_race_track_screen():
    """Settings 畫面裡「Race Track Simulation」入口按鈕：跟 go_to_settings_
    screen() 一樣先走既有的「如果目前在 Player，先完整離開」流程——雖然
    這個入口目前只會從 Settings 按下去，理論上不會在 Player 時被呼叫，這裡
    還是沿用同一套保護，不假設呼叫來源一定安全。"""
    leave_player_and_then(_enter_race_track_screen)


def _enter_race_track_screen():
    show_screen('race_track')
    # 每次進入都重新 GET 一次裝置目前的 race_track 狀態（不是只有第一次
    # build_screen() 才問一次）——這個狀態是裝置端的實際設定，跟 Settings
    # 的 desired_dvr_mode（工具自己記住「應該是什麼」）不同，值得每次進來
    # 都重新確認一次。
    screen_race_track.refresh_race_track_status()


def go_back_from_race_track():
    """Race Track Simulation 目前固定只從 Settings 的入口按鈕進來，Back
    固定回 Settings，不需要像 go_back_from_settings() 那樣判斷多種來源。"""
    go_to_settings_screen()


def go_to_iq_adjustment_screen():
    """Settings 畫面 Engineering Tools 裡「IQ Adjustment」入口按鈕：跟
    go_to_race_track_screen() 一樣先走既有的「如果目前在 Player，先完整
    離開」流程。"""
    leave_player_and_then(_enter_iq_adjustment_screen)


def _enter_iq_adjustment_screen():
    show_screen('iq_adjustment')
    # 2026-09-23 新增：進頁面自動開始 Live View。離開頁面時的 stop 統一在
    # show_screen() 裡處理（見該函式的說明），不是這裡的職責。
    screen_iq_adjustment.start_live_view()
    # 2026-09-23 第三輪新增：進頁面重新 GET 一次裝置目前真實的 IQ 設定，
    # 取代畫面上顯示的 IQ_DEFAULTS——每次進入都重新讀一次（不是只有第一次
    # build_screen() 才讀），跟 Race Track 的 refresh_race_track_status()
    # 同一種理由：這是裝置端的實際狀態，值得每次進來都重新確認。
    screen_iq_adjustment.load_iq_settings_from_device()


def go_back_from_iq_adjustment():
    """IQ Adjustment 目前固定只從 Settings 的入口按鈕進來，Back 固定回
    Settings。"""
    go_to_settings_screen()


def go_to_player_screen_from_recording(recording_group):
    """Recording 首頁縮圖點進 Player：維持原本行為，Back 要回 Recording"""
    global _screen_before_player
    _screen_before_player = 'recording'
    screen_player.set_current_recording_group(recording_group)
    show_screen('player')


def go_to_player_screen_from_gallery(recording_group):
    """Gallery 或 Favorite 點列表播放進 Player：Back 要回到原本那個分頁
    （並重新整理）。Gallery/Favorite 共用同一個 on_select_video callback
    （同一個 Frame，只是 view mode 不同），用 current_active_nav_tab
    判斷使用者當下實際在看哪一個分頁，才知道 Back 要回哪裡。"""
    global _screen_before_player
    _screen_before_player = 'favorite' if current_active_nav_tab == 'favorite' else 'gallery'
    screen_player.set_current_recording_group(recording_group)
    show_screen('player')


def go_to_photo_from_gallery(recording_item):
    """Gallery 或 Favorite 點到 Photo 類型的項目：2026-08-19 使用者要求
    Photo 併入跟 Video 同一個 Unified Media Viewer（screen_player.py），
    不再走獨立的 Photo Viewer 畫面——Photo 仍然完全不會碰 FFmpeg/OpenCV
    Video Decoder（screen_player.set_current_photo_item() 內部只是下載
    原始 JPG 用 PIL 顯示，見該函式的說明），只是共用同一顆 Canvas/Back/
    Title/Previous/Next。Back 要回到原本那個分頁，規則跟
    go_to_player_screen_from_gallery() 一致：用 current_active_nav_tab
    判斷使用者當下實際在看哪一個分頁，共用同一個 _screen_before_player
    （Photo 只會從 Gallery/Favorite 進來，不會從 Recording 進來，跟 Video
    共用這個變數不會有衝突）。"""
    global _screen_before_player
    _screen_before_player = 'favorite' if current_active_nav_tab == 'favorite' else 'gallery'
    screen_player.set_current_photo_item(recording_item)
    show_screen('player')


def go_back_from_player():
    """Player 按 Back：回到進入 Player 之前那個畫面。回 Gallery/Favorite
    的話，因為播放前可能已經在那邊順手停掉 Scenic/Event Recording，
    recordings list 有可能已經更新，本地快取可能跟 DVR 真實狀態不一致，
    所以要強制重新整理一次（force_refresh()）——不能用 ensure_loaded()，
    那個只有「第一次進入」才會真的打 API，這裡是「狀態可能真的變了」，
    兩種情況不一樣。

    2026-08-20 使用者真機回報：Player(Video)/Photo Viewer -> Gallery
    這條路徑（點 Player 自己的 Back 按鈕）跟 on_gallery_tab_clicked()
    點 Gallery Tab 那條路徑一樣，都是從「真的不同 Frame」tkraise() 過去，
    會有跟 2026-08-19 修過的 Recording -> Gallery 完全同一種殘影——原因
    是這裡原本各自直接呼叫 show_gallery_view()/show_favorite_view() +
    show_screen() + force_refresh()，沒有經過那次修好的 Cover Frame
    保護。改成呼叫這裡呼叫端已經呼叫過 screen_player.leave_player()
    （stop_video()/decoder cleanup/request token 失效/DVR mode 恢復都
    已經跑完，見 on_back_button_click()）之後，共用
    _switch_to_gallery_or_favorite_with_cover_protection()——不建立
    第二套 Cover/repaint workaround，Recording/Player(Video)/
    Player(Photo) -> Gallery/Favorite 現在都走同一套安全轉場。"""
    if _screen_before_player == 'gallery':
        _switch_to_gallery_or_favorite_with_cover_protection(screen_gallery.show_gallery_view, 'gallery')
    elif _screen_before_player == 'favorite':
        _switch_to_gallery_or_favorite_with_cover_protection(screen_gallery.show_favorite_view, 'favorite')
    else:
        show_screen('recording')


# ============ 建立主視窗 ============

window = tk.Tk()
window.title(MAIN_WINDOW_TITLE)
# 2026-08-25 使用者要求把 Title Bar 圖示從 Tkinter 預設圖示換成自訂的
# 行車紀錄器 outline icon（assets/app_icon.ico，見 generate_app_icon.py）。
# iconbitmap() 只影響「執行中視窗」左上角/工作列看到的圖示，跟打包後
# .exe 檔案本身的圖示（.spec 的 icon= 參數）是兩件分開設定的事，兩邊
# 都要設才會一致。
# 2026-08-26 真機截圖證實：只傳路徑（不帶 default=）在 Windows 上只會
# 確實設定「這個視窗自己」的小圖示（title bar 左上角），不會可靠更新
# 工作列讀取的大圖示（ICON_BIG／視窗類別圖示）——title bar 已經正確顯示
# 行車紀錄器圖示，但工作列仍顯示 Tk 內建的羽毛。改成 default=，在視窗
# 類別層級設定預設圖示，工作列讀的就是這一層。
# .ico / iconbitmap 是 Windows 專用設定；macOS 直接略過，避免 TclError。
if sys.platform == 'win32':
    window.iconbitmap(default=resource_path('assets', 'app_icon.ico'))

# 2026-08-19 使用者要求外觀比對 HCP3，新增 Left Rail / Bottom Bar 這兩塊
# 裝飾殼（Right Rail 對照原圖後確認原圖沒有對應結構，已移除，不自行仿造
# 補齊）。
#
# 2026-08-19 第三次對照原圖調整：Window geometry 改成「由內容反推」，
# 不是「先決定一個視窗尺寸，再讓內容想辦法塞滿/留白」——
# screen_recording.compute_recording_content_size() 用 16:9 直接算出
# Recording 畫面真正需要的寬高（不含 letterbox），這裡只要再加上
# Top Bar／Bottom Bar 高度、Left Rail 寬度，就是整個視窗需要的尺寸，
# 不用等視窗建立、realize 之後才回頭量測（上一輪為了量測 content_
# container 實際尺寸而呼叫的 window.update()，被 Codex review 指出
# 「在 WM_DELETE_WINDOW close protocol 註冊之前執行，可能提早處理到
# 不該處理的事件」，這裡把整個「執行期量測」機制拿掉，一次解決）。
TOP_BAR_HEIGHT = 50
_recording_content_width, _recording_content_height = screen_recording.compute_recording_content_size()
WINDOW_WIDTH = screen_fake_ui.LEFT_RAIL_WIDTH + _recording_content_width
WINDOW_HEIGHT = TOP_BAR_HEIGHT + _recording_content_height + screen_fake_ui.BOTTOM_BAR_HEIGHT
window.geometry(f'{WINDOW_WIDTH}x{WINDOW_HEIGHT}')
# 2026-08-19 使用者要求（Codex review 連續兩輪提出、這輪確認要修）：
# window 本身沒有設 resizable(False, False)，使用者手動縮小視窗時
# content_container 可能小於 Recording/Bottom Bar 需要的最小尺寸，重新
# 造成 clipping/重疊。用剛算出來的 WINDOW_WIDTH/HEIGHT 當 minsize，
# 兩者同一個來源，不會有兩邊算的數字對不上的問題。
window.minsize(WINDOW_WIDTH, WINDOW_HEIGHT)

# 2026-08-19 使用者第四次對照原圖要求的對齊微調：
# - Inner Navigation Rail 的 top/bottom 對齊 Recording/Event/Photo Panel
#   的 top/bottom：Panel 高度就是 screen_recording.PREVIEW_SIZE[1]，
#   Panel 頂端距離 content_container 頂端的距離就是
#   screen_recording.VERTICAL_PADY[0]。
# - Bottom Bar 左側「－」「＋」對齊 Panel 水平中心、右側對齊 Preview
#   Area 水平中心——這裡直接沿用 screen_recording 已公開的 PANEL_WIDTH/
#   LEFT_NAV_PADX/RIGHT_AREA_PADX/PREVIEW_SIZE 常數 + LEFT_RAIL_WIDTH
#   算出絕對像素座標，不猜測、不重複定義一份數字。
_nav_rail_top_offset = screen_recording.VERTICAL_PADY[0]
_panel_left_edge = screen_fake_ui.LEFT_RAIL_WIDTH + screen_recording.LEFT_NAV_PADX[0]
_panel_center_x = _panel_left_edge + screen_recording.PANEL_WIDTH / 2
_preview_left_edge = (_panel_left_edge + screen_recording.PANEL_WIDTH + screen_recording.LEFT_NAV_PADX[1]
                      + screen_recording.RIGHT_AREA_PADX[0])
_preview_center_x = _preview_left_edge + screen_recording.PREVIEW_SIZE[0] / 2

# ---- 上方選單列：只有 Recording / Gallery / Settings ----
# 2026-08-19 使用者對照原圖要求：Recording/Gallery/Settings 這些主要
# 控制項要跟下面的主內容區（content_container）左右邊界對齊，不是貼齊
# 整個視窗邊界——左邊補一塊跟 Left Rail 一樣寬的 spacer（顏色也用同一套
# 深色殼色，視覺上像是 Left Rail 往上延伸，不是憑空出現一塊空白），讓
# 右邊的 top_bar 主體剛好對齊 content_container 的水平範圍。右側因為
# 已經移除 Right Rail，content_container 右邊界本來就等於視窗右邊界，
# 不需要再補右邊的 spacer，Gear 自然就對齊 Preview 右側。
top_bar_row = tk.Frame(window, height=TOP_BAR_HEIGHT)
top_bar_row.pack(fill='x')
top_bar_row.pack_propagate(False)

top_bar_left_spacer = tk.Frame(top_bar_row, width=screen_fake_ui.LEFT_RAIL_WIDTH, bg=screen_fake_ui.FAKE_UI_BG_COLOR)
top_bar_left_spacer.pack(side='left', fill='y')
top_bar_left_spacer.pack_propagate(False)  # 時間區只佔用部分寬度，避免這個 spacer 被壓縮成比 LEFT_RAIL_WIDTH 窄

# 2026-08-20 使用者比對 HCP3 實機圖要求：時間要在 Top Bar 高度範圍內
# （跟 Recording/Gallery Tab 同一個垂直高度區域），不能再放在 Left Rail/
# middle_row 裡面——見 screen_fake_ui.build_top_bar_time_label() 的說明。
screen_fake_ui.build_top_bar_time_label(top_bar_left_spacer)

top_bar = tk.Frame(top_bar_row, bg=NAV_BAR_COLOR)
top_bar.pack(side='left', fill='both', expand=True)

# Recording/Gallery/Favorite 改成 Canvas 版膠囊分頁（create_nav_tab()），
# 取代原本的 tk.Button——2026-08-14 對照 HCP3 實機截圖，Active Tab 是白色
# 描邊膠囊外框，一般 tk.Button 畫不出這種外框。
recording_menu_button = create_nav_tab(top_bar, 'Recording', go_to_recording_screen)
recording_menu_button.pack(side='left', padx=(16, 6), pady=5)
nav_tab_canvases['recording'] = recording_menu_button
bind_nav_tab_hover(recording_menu_button, 'recording')

gallery_menu_button = create_nav_tab(top_bar, 'Gallery', on_gallery_tab_clicked)
gallery_menu_button.pack(side='left', padx=6, pady=5)
nav_tab_canvases['gallery'] = gallery_menu_button
bind_nav_tab_hover(gallery_menu_button, 'gallery')

favorite_menu_button = create_nav_tab(top_bar, 'Favorite', go_to_favorite_screen)
nav_tab_canvases['favorite'] = favorite_menu_button
bind_nav_tab_hover(favorite_menu_button, 'favorite')
# 不在這裡 pack()——預設不顯示，只有 Favorite 數量 > 0 時才會由
# update_favorite_tab_visibility() 動態 pack 出來（見該函式的說明）

# Settings 改成齒輪圖示、不顯示文字，固定釘在 Top Navigation 最右側
# （pack side='right'，跟 Recording/Gallery/Favorite 的 side='left' 分開算，
# 不管左邊有幾個分頁顯示，齒輪永遠在最右邊，也不會被
# update_favorite_tab_visibility() 的重排邏輯動到）。齒輪本身維持
# tk.Button（真機截圖裡齒輪圖示沒有膠囊外框，不需要跟著改成 Canvas）。
# PhotoImage 要留一個模組層級的參照（gear_icon_image），不然圖示會被 GC
# 回收後消失。
# 2026-08-14 對照真機截圖微調：圖示放大一點（22→26）、按鈕加一點垂直
# padding（pady=4），讓 Gear 按鈕的整體高度（26+4+4=34）跟旁邊 Recording/
# Gallery 膠囊分頁的高度（NAV_TAB_HEIGHT=34）一致，垂直置中看起來才一致。
gear_icon_image = build_gear_icon_image()
# 2026-09-17 macOS 實機回報：Gear 按鈕周圍會出現白色/淺色反白背景。跟
# style_dark_button() 那三顆按鈕（見 screen_gallery.py 的 _MacDarkButton）
# 同一個根本原因——macOS 的 Aqua 主題不保證讓 tk.Button 的 bg/
# activebackground 生效，改用系統原生按鈕外觀，跟這裡想要的深色 Top Bar
# 融為一體（bg=NAV_BAR_COLOR）對不上。這顆按鈕只是顯示一張圖示、沒有
# state/disabled 的需求（不像 Format SD Card 那三顆要跟隱藏的真 Button
# 借 state 機制），改用 tk.Label 顯示 gear_icon_image 就足夠：Label 的
# bg 在所有平台都會真的套用，用 <Button-1> bind 取代 command，其餘
# set_active_nav_tab()/bind_nav_tab_hover() 都是靠 .config(bg=...) 換色，
# Label 一樣支援，不用另外改那兩個函式。Windows 維持原本的 tk.Button，
# 完全不變。
if sys.platform == 'darwin':
    settings_menu_button = tk.Label(top_bar, image=gear_icon_image,
                                     bg=NAV_BAR_COLOR, bd=0, relief='flat',
                                     highlightthickness=0, padx=12, pady=4, cursor='hand2')
    settings_menu_button.bind('<Button-1>', lambda event: go_to_settings_screen())
else:
    settings_menu_button = tk.Button(top_bar, image=gear_icon_image, command=go_to_settings_screen,
                                      bg=NAV_BAR_COLOR, activebackground=NAV_BAR_COLOR, bd=0, relief='flat',
                                      highlightthickness=0, padx=12, pady=4, cursor='hand2')
settings_menu_button.pack(side='right', pady=5, padx=(0, 16))
bind_nav_tab_hover(settings_menu_button, 'settings')

# ---- Live View 按鈕：放在 Gear 左邊，跟 Gear 同進退（Settings 畫面時一起
# 隱藏，見 _repack_top_bar_right_side() 的 should_show_gear） ----
# 2026-09-21 新增，2026-09-24 使用者要求改版：原本用整顆按鈕的底色/文字
# 三種狀態（未啟用灰底／Connecting... 過渡色／● Live View 亮底）表示連線
# 狀態，改成「固定深灰背景＋固定白色文字＋一顆會變色的狀態圓點」——背景
# 永遠是 NAV_BAR_COLOR、文字永遠是白色的 LIVE_VIEW_BUTTON_TEXT，不再隨
# 連線狀態改變；只有圓點顏色在變。使用者只定義了兩種顏色（灰＝未連線、
# 綠＝已連線），Connecting 這個過渡態沒有獨立的第三種顏色，這裡讓它沿用
# 「未連線」的灰點（跟 inactive 視覺上一致，只是還沒收到第一張畫面），
# 不是遺漏、是配合使用者這次只要兩種狀態顏色的明確要求。
# 圓點用 ui_shapes.make_circle_photo_image()（跟 screen_settings.py 的
# Toggle Switch 圓形握把同一套繪圖工具）畫兩張固定顏色的小圓點圖片。
#
# 2026-09-24 第二輪使用者要求：圓點放大到約 8px、圓點跟文字的水平間距
# 縮小到約 7px。原本用單一 Label/Button 的 compound='left' 做法沒有辦法
# 精確控制「圖片跟文字之間的間距」——那個間距是各平台 Tk 內建 layout
# 決定的固定值，不能用 padx/pady 調整（padx/pady 只會加在整個內容
# 外面，不是加在圖片文字中間）。改成一個 Frame 裡面各自放一顆圓點
# Label 跟一顆文字 Label、side='left' 排列，中間那個 7px 的間距直接用
# 文字 Label 的 padx=(7, 0) 精確控制；pack() 預設就會把每個 widget 在
# 它分配到的列高度中垂直置中（anchor 預設是 'center'），圓點（8px）跟
# 文字（字型高度較高）兩者高度不同時，一樣會各自垂直置中在同一列，看
# 起來就是「同一組、垂直置中」。
# 這個 Frame 現在取代原本的 Label/Button 成為 live_view_button，其他地方
# （_repack_top_bar_right_side() 的 pack()/pack_forget()）當它是同一顆
# 「小部件」在操作，不需要跟著改。
LIVE_VIEW_BUTTON_TEXT = 'Live View'
LIVE_VIEW_DOT_DIAMETER = 8  # 使用者要求「放大至約 8px」
LIVE_VIEW_DOT_TEXT_GAP_PX = 7  # 使用者要求圓點跟文字的水平間距「縮小至約 7px」
LIVE_VIEW_DOT_COLOR_INACTIVE = screen_gallery.TEXT_MUTED_COLOR  # 灰色：未連線／Connecting 中
LIVE_VIEW_DOT_COLOR_ACTIVE = '#4CAF50'  # 綠色：已連線，跟 screen_settings.py 的 Dashcam Switch ON 同一個「已連線/已開啟」語意

# 2026-09-24 第四輪使用者要求：拿掉第三輪加的「selected」樣式（常駐底色＋
# 底線）——回報底線很醜、按下後背景反灰不會恢復、選取狀態一直留著，而且
# 這顆按鈕本來就不需要做成分頁 Tab 那種「目前選取」效果。改成只有一種
# feedback：滑鼠案下短暫變色、放開立刻恢復，不再有任何常駐狀態色。
# LIVE_VIEW_BUTTON_PRESSED_COLOR 借用既有的 NAV_TAB_HOVER_COLOR（跟其他
# Tab 案下/hover 同一組「輕微變亮」語意，不新發明顏色）。平常（沒有案著）
# 背景固定是 NAV_BAR_COLOR，圓點顏色（灰/綠）繼續只代表連線狀態，兩者
# 完全沒有交集。
LIVE_VIEW_BUTTON_PRESSED_COLOR = NAV_TAB_HOVER_COLOR

live_view_is_active = False  # 真的已經在顯示畫面（不含 Connecting 中間態）——只影響圓點顏色

# PhotoImage 沒有其他參照會被 GC 回收，用模組變數存住（跟這個檔案其他
# icon 圖片——gear_icon_image 等——同一個既有慣例）。
live_view_dot_image_inactive = ui_shapes.make_circle_photo_image(
    LIVE_VIEW_DOT_DIAMETER, fill=LIVE_VIEW_DOT_COLOR_INACTIVE)
live_view_dot_image_active = ui_shapes.make_circle_photo_image(
    LIVE_VIEW_DOT_DIAMETER, fill=LIVE_VIEW_DOT_COLOR_ACTIVE)

live_view_button = tk.Frame(top_bar, bg=NAV_BAR_COLOR, cursor='hand2', padx=12, pady=4)
live_view_dot_label = tk.Label(live_view_button, image=live_view_dot_image_inactive,
                                bg=NAV_BAR_COLOR, bd=0, highlightthickness=0, cursor='hand2')
live_view_dot_label.pack(side='left')
live_view_text_label = tk.Label(live_view_button, text=LIVE_VIEW_BUTTON_TEXT, bg=NAV_BAR_COLOR, fg='white',
                                 font=('Calibri', 11), bd=0, highlightthickness=0, cursor='hand2')
live_view_text_label.pack(side='left', padx=(LIVE_VIEW_DOT_TEXT_GAP_PX, 0))


def _live_view_button_widgets():
    return (live_view_button, live_view_dot_label, live_view_text_label)


def _set_live_view_button_background(bg_color):
    """Frame／圓點 Label／文字 Label 三個 widget 的背景要一起換色，不然
    會看到中間留一塊沒跟著變色的縫。"""
    for widget in _live_view_button_widgets():
        widget.config(bg=bg_color)


def _on_live_view_button_pressed(event=None):
    """滑鼠案下：背景變成 LIVE_VIEW_BUTTON_PRESSED_COLOR，並照原本邏輯
    觸發啟動/停止。恢復完全交給 _on_live_view_button_released()，這裡不
    排任何計時器——沒有計時器就不會有「使用者很快案放、計時器卻還沒到就
    把後來的狀態蓋掉」這種競態，也不會有「忘記恢復」的常駐色問題。"""
    _set_live_view_button_background(LIVE_VIEW_BUTTON_PRESSED_COLOR)
    on_live_view_button_clicked()


def _on_live_view_button_released(event=None):
    """滑鼠放開：立刻恢復成固定的 NAV_BAR_COLOR。Tk 在案下的那個 widget
    上有隱含的 mouse grab，就算放開時游標已經移出按鈕範圍，這個 release
    事件還是會確實觸發到同一顆 widget 上，不會漏掉、也不會卡在按下的
    顏色。"""
    _set_live_view_button_background(NAV_BAR_COLOR)


# Frame 本身跟兩顆子 Label 都要能點擊觸發 on_live_view_button_clicked()
# ——沒有 command= 可以像 tk.Button 那樣自動處理，這裡三個都各自綁
# <Button-1>/<ButtonRelease-1>，跟 mac 原本（tk.Label 沒有 command）就是
# 這種綁法一致，Windows 這邊改成一樣的 Label+bind 寫法後也不再需要
# sys.platform 分支（原本要分 mac/Windows 是因為 macOS Aqua 主題下
# tk.Button 的 bg/fg 不會生效，這裡完全沒用到 tk.Button，兩個平台可以
# 共用同一份寫法）。
for _live_view_clickable_widget in _live_view_button_widgets():
    _live_view_clickable_widget.bind('<Button-1>', _on_live_view_button_pressed)
    _live_view_clickable_widget.bind('<ButtonRelease-1>', _on_live_view_button_released)


def _set_live_view_button_inactive():
    global live_view_is_active
    live_view_is_active = False
    live_view_dot_label.config(image=live_view_dot_image_inactive)


def _set_live_view_button_connecting():
    live_view_dot_label.config(image=live_view_dot_image_inactive)


def _set_live_view_button_active():
    global live_view_is_active
    live_view_is_active = True
    live_view_dot_label.config(image=live_view_dot_image_active)


def _handle_live_view_error(message):
    """api.start_liveview() 的 on_error：可能從背景 thread 呼叫（RTSP 連線
    中斷是背景 thread 偵測到的），這裡統一用 window.after(0, ...) 排回
    main thread 才能安全動 Tkinter widget。"""
    def restore():
        print(f'[Live View] {message}')
        api.stop_liveview()
        screen_recording.exit_live_view_mode()
        _set_live_view_button_inactive()
    window.after(0, restore)


def stop_live_view_if_active():
    """統一的 Live View 停止入口：按鈕本身點擊停止、切換分頁、關閉視窗都
    走這裡。沒有在跑（包含還在 Connecting 的過渡狀態）就直接 return，
    可以安全重複呼叫。"""
    if not live_view_is_active and not api.is_liveview_running():
        return
    api.stop_liveview()
    screen_recording.exit_live_view_mode()
    _set_live_view_button_inactive()


def on_live_view_button_clicked():
    """未啟用（含 Connecting 中）時點擊 = 啟動；已經在顯示畫面時點擊 = 停止。
    如果目前不在 Recording 分頁，先切過去（Live View 借用的是 Recording
    首頁右側的縮圖區域，只有在那個畫面才有地方顯示）。"""
    if live_view_is_active or api.is_liveview_running():
        stop_live_view_if_active()
        return

    if current_screen_name != 'recording':
        go_to_recording_screen()

    _set_live_view_button_connecting()
    api.start_liveview(on_error=_handle_live_view_error)
    if not api.is_liveview_running():
        # start_liveview() 同步失敗（例如找不到 ffmpeg）：_handle_live_view_error
        # 已經排好 restore()，這裡不用再進 Live View 模式。
        return
    screen_recording.enter_live_view_mode(on_first_frame=_set_live_view_button_active)


def _repack_top_bar_right_side(screen_name):
    """統一管理 Top Bar 右側三個依畫面顯示/隱藏的元件：
    - Gear：只有目前不是 Settings／Race Track 才顯示。2026-08-19 使用者
      要求：Settings 不算跟 Recording/Gallery 同層的主要 Tab，是次級頁面，
      進去之後 Gear 要整個消失（不是 disable），改由 Settings 畫面自己
      左上角的 Back 取代，不能再留一個沒有作用的 Gear。2026-09-22
      Race Track Simulation、IQ Adjustment 都是從 Settings 的 Engineering
      Tools 進入的次級頁面，同一個道理，一起併入這個規則。
    - Live View 按鈕：2026-09-21 新增，跟 Gear 同一個顯示規則（Settings
      畫面時一起消失）——Live View 是獨立功能入口，不算跟 Recording/
      Gallery 同層的主要 Tab，但也不該在使用者已經進入 Settings 次級頁面
      時還懸空留一個入口。
    - Gallery Filter/Selection Controls：只有目前分頁是 Gallery/Favorite
      才顯示，切到 Recording/Settings/Player（含 Photo，共用同一個畫面）
      就整個隱藏（不是 disable，這個情境下顯示出來也沒有作用對象），這
      部分是既有邏輯（原本的 update_gallery_nav_controls_visibility()），
      沒有改變行為，只是併進來一起管理。

    三個元件每次都整組 unpack 再依固定順序重新 pack，不能只單獨對其中
    一個 pack_forget()/pack()——跟 _repack_top_nav_tabs() 同樣的理由，
    Tk 的 pack() 沒有「插回原本位置」的概念，只會排到目前已經 pack 的
    元件最後面，三個元件顯示狀態交錯變化時，右側的相對順序會亂掉。

    2026-09-17 macOS 實機回報：切換分頁後 Top Navigation 按鈕偶發消失。
    跟 _repack_top_nav_tabs() 不同，這個函式是 show_screen() 每次切畫面
    都會呼叫（見上面呼叫點），不是只有狀態真的改變時才呼叫——大部分情況
    下 Gear/Filter 這兩個元件的顯示狀態其實沒變（例如 Gallery -> Favorite
    來回切換，兩邊都要顯示 Filter），卻還是整組 pack_forget() 再 pack()
    一次，在 macOS 的 Tk 上這個「非必要的 unpack/pack」被觀察到偶爾會讓
    元件暫時量不到自己的 geometry、畫面上看起來像消失。用
    _top_bar_right_side_state 快取上一次算出的顯示狀態，狀態沒變就整個
    跳過 unpack/pack，不只是治標（macOS 不必要的重排變少），Windows 這邊
    也是單純的重複工作被省掉，結果（最終 pack 出來的畫面）完全一樣。
    真的需要重排時，一樣只在 macOS 上補一次 window.update_idletasks()
    確保 geometry/繪製算完，不改變 Windows 原本的行為/時序。"""
    global _top_bar_right_side_state

    should_show_gear = screen_name not in ('settings', 'race_track', 'iq_adjustment')
    should_show_gallery_controls = screen_name in ('gallery', 'favorite')
    new_state = (should_show_gear, should_show_gallery_controls)
    if new_state == _top_bar_right_side_state:
        return
    _top_bar_right_side_state = new_state

    settings_menu_button.pack_forget()
    live_view_button.pack_forget()
    gallery_nav_controls_canvas.pack_forget()
    if should_show_gear:
        settings_menu_button.pack(side='right', pady=5, padx=(0, 16))
        live_view_button.pack(side='right', pady=5, padx=(0, 4))
    if should_show_gallery_controls:
        gallery_nav_controls_canvas.pack(side='right', pady=5, padx=(0, 12))

    if sys.platform == 'darwin':
        window.update_idletasks()


# ---- HCP3 外觀比對用的裝飾殼：Left Rail / Bottom Bar ----
# 2026-08-19 使用者確認的方案：純視覺、所有畫面統一顯示（不分
# Recording/Gallery/Player/Settings），不綁任何 command/api，不影響下面
# content_container 的既有邏輯——見 screen_fake_ui.py 的完整說明。原本
# 對稱補的 Right Rail 已移除（原圖沒有對應結構）。
# pack 順序刻意固定：top_bar_row（上）已經在上面 pack 過，這裡先 pack
# bottom_bar（下），最後才 pack middle_row（fill='both', expand=True），
# 這樣 Tk 才會把 middle_row 分配到「扣掉上下兩條 bar 之後剩下的中間空間」，
# 不會被 expand 的 middle_row 反過來擠壓掉。
bottom_bar = screen_fake_ui.build_bottom_bar(window, left_group_center_x=_panel_center_x,
                                              right_group_center_x=_preview_center_x)
bottom_bar.pack(side='bottom', fill='x')

middle_row = tk.Frame(window, bg=screen_fake_ui.FAKE_UI_BG_COLOR)
middle_row.pack(fill='both', expand=True)

left_rail = screen_fake_ui.build_left_rail(middle_row, nav_rail_height=screen_recording.PREVIEW_SIZE[1],
                                            nav_rail_top_offset=_nav_rail_top_offset)
left_rail.pack(side='left', fill='y')

# ---- 畫面內容容器：Recording / Gallery / Player / Settings 疊在這裡 ----
# 2026-08-19 使用者要求：這裡只改 parent（從 window 改成 middle_row），
# pack 參數（fill='both', expand=True）完全不變，讓 content_container
# 的可用尺寸維持原本邏輯（Tk 自動扣掉 left_rail 的固定寬度），下面
# register_screen()/content_cover_frame 的程式碼完全不用改。
#
# 2026-08-19 第三次調整：不再需要 window.update() 強制 realize 才能量測
# 尺寸——Recording 畫面現在用 screen_recording.compute_recording_
# content_size() 的固定計算結果決定 window.geometry()（見上面），
# content_container 的實際尺寸自然就等於這個計算結果，build_screen()
# 內部直接用模組常數，不需要在這裡量測。
# 2026-09-18 使用者實機回報：macOS 上 Gallery repaint 空檔偶爾會露出底層
# 背景，排查後發現這裡本來沒有明確設定 bg（用 Tk 系統預設色，macOS Aqua
# 下通常是淺色）。這是 Recording/Gallery/Player/Settings 共用的最外層
# 容器，明確設成跟這幾個畫面一致的深色，不管哪個畫面、repaint 哪個空檔
# 露出來的都是深色，不會看到系統預設的淺色。用 screen_gallery.
# BACKGROUND_COLOR 是因為這支模組本來就已經 import screen_gallery，
# 且下面 content_cover_frame 已經是同樣用 screen_gallery.BACKGROUND_COLOR
# 的既有寫法，跟著沿用同一個顏色來源，不新增依賴。
content_container = tk.Frame(middle_row, bg=screen_gallery.BACKGROUND_COLOR)
content_container.pack(side='left', fill='both', expand=True)

register_screen('recording', screen_recording.build_screen(content_container, on_play_video=go_to_player_screen_from_recording))
# 2026-08-19 使用者要求：DVR 斷線偵測要全域運作，不能只有 Recording 分頁
# 使用中才跑——這裡在 preview_canvas 建立完成後（build_screen() 剛跑完）
# 啟動一次，全程運作，跟 api.start_keepalive() 一樣是整個工具生命週期
# 都在跑的東西，不是每次進 Recording 分頁才啟動。
screen_recording.start_global_connection_watcher()
gallery_frame = screen_gallery.build_screen(content_container, on_select_video=go_to_player_screen_from_gallery,
                                             on_favorite_state_changed=update_favorite_tab_visibility,
                                             on_select_photo=go_to_photo_from_gallery,
                                             on_gallery_item_count_changed=update_gallery_tab_visibility)
register_screen('gallery', gallery_frame)
# Favorite 分頁跟 Gallery 是同一個 Frame，只是 screen_gallery 內部的
# current_view_mode 不同（見 show_gallery_view()/show_favorite_view()），
# 不是另外建立一份重複的畫面。
screens['favorite'] = gallery_frame
# 2026-08-19 使用者要求 Photo 併入同一個 Unified Media Viewer，不再另外
# register_screen('photo_viewer', ...)——舊的 screen_photo_viewer.py 已經
# retire，不再被任何地方呼叫。
register_screen('player', screen_player.build_screen(content_container, on_back=go_back_from_player))
register_screen('settings', screen_settings.build_screen(
    content_container, on_back=go_back_from_settings,
    on_open_race_track=go_to_race_track_screen,
    on_open_iq_adjustment=go_to_iq_adjustment_screen))
# 2026-09-22 新增：Race Track Simulation，從 Settings 的 Engineering Tools
# 入口按鈕進來。protobuf 組包／RESTful 傳輸邏輯都在 race_track_service.py
# （screen_race_track.py 本身不 import audi_pb2、不直接呼叫
# api.send_get()/send_put()），這裡只是把三個 callback 接起來，不在這裡
# 塞任何 protobuf/網路邏輯。
register_screen('race_track', screen_race_track.build_screen(
    content_container,
    on_send_race_data=race_track_service.send_track_information,
    on_enable_race_track=race_track_service.set_race_track_enabled,
    on_get_race_track_status=race_track_service.get_race_track_enabled,
    on_back=go_back_from_race_track))
# 2026-09-22 第二輪新增：IQ Adjustment，一樣從 Settings 的 Engineering
# Tools 入口進來。這一版還沒有 IQ 設定的 RESTful 傳輸邏輯，on_apply_iq
# 先傳 None——screen_iq_adjustment.py 自己會顯示 placeholder 狀態，不在
# 這裡塞一個半成品的傳送邏輯。之後要串接時，只要把這裡的 None 換成真正的
# 函式即可，不用改 screen_iq_adjustment.py。
#
# 2026-09-23 新增 Live View：這裡是唯一知道「IQ Adjustment 目前用 RTSP」
# 的地方——實際建構 RTSPVideoSource（video_source.py）、包進
# LiveViewController（live_view_controller.py），screen_iq_adjustment.py
# 只拿到 live_view_controller 這個物件，完全不知道底下是 RTSP 還是 UVC。
# 之後 RD 提供 UVC，只要把下面這行換成
# video_source.UVCVideoSource(device_index)，其他都不用改。
iq_adjustment_live_view_controller = live_view_controller.LiveViewController(
    video_source.RTSPVideoSource(), source_label='RTSP')
# 2026-09-23 第二輪串接真正的 IQ 設定傳送邏輯：GET/PUT
# /dashcam/cameraConfiguration 的 protobuf 組包都在 iq_adjustment_service.py
# （screen_iq_adjustment.py 本身不 import audi_pb2、不直接呼叫
# api.send_get()/send_put()），這裡只是把 callback 接起來。
register_screen('iq_adjustment', screen_iq_adjustment.build_screen(
    content_container, on_apply_iq=iq_adjustment_service.apply_iq_settings,
    on_back=go_back_from_iq_adjustment,
    live_view_controller=iq_adjustment_live_view_controller,
    on_get_iq_settings=iq_adjustment_service.get_current_iq_settings))

# ============================================================
# Recording -> Gallery 切換用的 Cover Frame（2026-08-19 診斷階段驗證有效
# 後，正式收斂成固定機制，只鎖定這一條路徑，見 _enter_gallery_screen_
# from_tab()）
# ============================================================
# token/callback guard 只能防止「背景 worker 事後還去動已經離開的畫面」，
# 這裡處理的是另一種殘影成因：tkraise() 改變 Z-order 之後，
# update_idletasks() 只處理 Tk 的 idle task（geometry 重新計算/排隊中的
# after idle callback），不會處理 OS 層級的 repaint/expose 事件——真機驗證
# 過，只呼叫 update_idletasks() 不足以避免 Recording 最後一次繪製的像素
# 短暫殘留。這裡用一個跟 Gallery 同色、完全不透明、蓋滿整個 content area、
# 沒有任何子元件（不顯示文字/Spinner，不會讓使用者覺得切去另一個 Loading
# 畫面）的 Cover Frame 蓋住這個空檔：
#   Recording -> raise Cover -> raise Gallery -> Gallery 版面 settle -> hide Cover
# 使用者視覺上只會看到一次乾淨的切換，不會看到 Cover 本身（純色跟 Gallery
# 背景一樣）。
content_cover_frame = tk.Frame(content_container, bg=screen_gallery.BACKGROUND_COLOR)
content_cover_frame.place(x=0, y=0, relwidth=1, relheight=1)
content_cover_frame.lower()  # 預設蓋在最底層，不影響任何既有畫面切換

# 2026-08-17 使用者要求：Gallery 的 Filter 漏斗 icon（Normal Mode）／
# X + ...（Selection Mode）搬到最上方 Navigation Bar，放在 Settings Gear
# 左邊——實際的 Canvas 建立/畫圖/點擊邏輯完全沿用 screen_gallery.py 既有
# 的 create_nav_filter_controls()/update_top_bar_mode()/show_filter_menu()
# /show_selection_action_menu()/exit_selection_mode()，這裡只負責「放在
# 哪裡」跟「哪些分頁才看得到」。必須放在 screen_gallery.build_screen()
# 之後才呼叫——create_nav_filter_controls() 內部會呼叫
# update_top_bar_mode() -> update_selection_status_text()，需要用到
# build_screen() 才會建立的 status_label，順序顛倒會直接 AttributeError。
# pack() 放在 settings_menu_button 之後才呼叫，Tk 的 pack(side='right')
# 會照呼叫順序從右往左排，這裡才能保證永遠緊接在 Gear 左邊。
gallery_nav_controls_canvas = screen_gallery.create_nav_filter_controls(top_bar, bg_color=NAV_BAR_COLOR)

show_screen('recording')
# 2026-08-19 使用者要求：App 第一次啟動、成功連上 DVR 後，先主動同步一次
# idle baseline（WITHOUT_RINGBUFFER + NO_EVENT），避免使用者第一次按
# Recording/Event 時同時發生 mode transition。整個工具生命週期只會呼叫
# 這一次（不像 refresh_recording_page() 每次切回 Recording Tab 都會呼叫）。
screen_recording.start_dvr_startup_sync()


def on_main_window_close():
    """點右上角 X 關閉主視窗前，先把 Live View／Player 可能還開著的 ffmpeg
    子行程停掉，不然視窗關掉後這個 process 就消失了，沒有人會再去呼叫
    release()／terminate()，ffmpeg 子行程會變成沒人管的殘留 process。

    關閉前也要嘗試把 DVR 恢復成 desired_dvr_mode——如果工具關閉前 actual
    剛好偏離 desired（例如正在 Player 裡看影片、還沒走 Back 就直接關視窗），
    這是最後一個機會恢復，不然 DVR 會停留在錯誤的狀態直到下次重新開工具。
    restore_desired_dvr_mode() 內部如果本來就已經是 desired，只是一次快速
    的 GET，不會拖慢關閉速度；只有真的需要恢復時才會花比較久時間 PUT+確認。

    2026-08-20 使用者回報真機關閉變慢（之前幾乎立即關閉），加上
    shutdown timing 後真機確認：主要耗時在 window.destroy()——Gallery
    只要 session 中瀏覽過涵蓋全部資料的 Filter（例如 All），
    Filter Hide/Show Reuse 就會讓這些 row widget 一直活著到關閉那一刻
    （不會因為後來切到小 Filter/切去 Recording 就縮小），window.destroy()
    要一次遞迴銷毀整棵累積下來的 widget tree。使用者明確要求不要為了
    這個去動 Gallery hide/show reuse 架構或 widget lifecycle，改成低風險
    的「先 window.withdraw() 讓視窗立即消失，再執行既有必要 cleanup，
    最後才 window.destroy()」：

    - withdraw() 只是把視窗從畫面/工作列隱藏，不會 destroy 任何 widget，
      widget 都還活著、完全可以正常操作——不會因為視窗被隱藏就讓後面
      任何程式碼對 widget 的操作失敗。
    - stop_liveview()/stop_video()/restore_desired_dvr_mode() 這幾行完全
      不變、不受影響，一樣會完整執行完才會走到 window.destroy()。
    - Tk 是單執行緒事件迴圈，這整段 on_main_window_close() 執行期間
      mainloop 本來就不會去處理其他事件/callback，withdraw() 不會讓
      任何背景執行緒排進來的 .after() callback 提早或延後觸發、也不會
      让它們在這個時間點觸發到什麼會出錯的操作——這個時序特性跟有沒有
      呼叫 withdraw() 無關，withdraw()前後都一樣。
    - window.destroy() 最終還是會被呼叫，mainloop() 一樣會在它执行完後
      正常返回，process 正常從程式最後一行結束，不會遺留任何非
      daemon thread（背景 thread 全部都是 daemon=True，見下方診斷）。"""
    print('[Shutdown Perf] on_main_window_close() 開始')
    t_close_start = perf_counter()

    t_withdraw_start = perf_counter()
    window.withdraw()
    withdraw_duration = perf_counter() - t_withdraw_start
    print(f'[Shutdown Perf] window.withdraw()={withdraw_duration:.4f}s（視窗現在應該已經從畫面/工作列消失）')

    t_stop_liveview_start = perf_counter()
    api.stop_liveview()
    # 2026-09-21 新增：把 screen_recording._live_view_tick() 的遞迴 after()
    # 迴圈也一起停掉，不然視窗 destroy() 之後這個排定中的 callback 還會
    # 再跑一次、對著已經銷毀的 preview_canvas 操作。
    screen_recording.exit_live_view_mode()
    # 2026-09-23 新增：IQ Adjustment 的 Live View 是完全獨立的一套
    # RTSPVideoSource/ffmpeg 子行程，同樣需要在關閉主程式時停止，不然
    # process 會變成沒人管的殘留。
    screen_iq_adjustment.stop_live_view()
    stop_liveview_duration = perf_counter() - t_stop_liveview_start
    print(f'[Shutdown Perf] api.stop_liveview()={stop_liveview_duration:.4f}s')

    t_stop_video_start = perf_counter()
    screen_player.stop_video()
    stop_video_duration = perf_counter() - t_stop_video_start
    print(f'[Shutdown Perf] screen_player.stop_video()={stop_video_duration:.4f}s')

    t_restore_dvr_mode_start = perf_counter()
    try:
        api.restore_desired_dvr_mode()
    except Exception as error:
        print(f'[Main] 關閉前恢復 desired_dvr_mode 失敗: {error}')
    restore_dvr_mode_duration = perf_counter() - t_restore_dvr_mode_start
    print(f'[Shutdown Perf] api.restore_desired_dvr_mode()={restore_dvr_mode_duration:.4f}s')

    # 2026-08-20 使用者要求：確認目前實際上「有沒有」明確的 cancel .after()
    # jobs / stop background threads / Gallery cleanup / thumbnail worker
    # shutdown / image cache cleanup 這幾個步驟——這裡不新增任何這些步驟
    # 本身（這輪只加 log、不改邏輯），只是如實記錄目前這幾件事在
    # window.destroy() 之前完全沒有被明確呼叫過，duration 直接是 0，
    # 讓「這幾步到底存不存在」這件事本身變成看得到的診斷結果，而不是
    # 猜測。
    print('[Shutdown Perf] cancel scheduled .after() jobs=0.0000s（目前沒有這個步驟，見下方分析）')
    print('[Shutdown Perf] stop/join background threads=0.0000s（目前沒有這個步驟，全部背景 thread 都是 daemon=True，見下方分析）')
    print('[Shutdown Perf] Gallery cleanup=0.0000s（目前沒有這個步驟）')
    print('[Shutdown Perf] thumbnail worker shutdown=0.0000s（目前沒有這個步驟）')
    print('[Shutdown Perf] image/cache cleanup=0.0000s（目前沒有這個步驟）')

    # 關閉前的診斷快照：確認 Filter Hide/Show Reuse 之後，是不是有大量
    # hidden（可見清單以外，但 widget 還活著）的 Gallery row 累積到現在。
    row_widget_count = len(screen_gallery._row_widgets_by_item_key)
    visible_count = len(screen_gallery._current_row_order)
    hidden_count = row_widget_count - visible_count
    image_refs_count = len(screen_gallery.image_refs)
    pending_thumbnail_count = len(screen_gallery.pending_thumbnail_labels)
    active_thread_count = threading.active_count()
    thread_names = [t.name for t in threading.enumerate()]
    print(f'[Shutdown Perf] 關閉前快照: _row_widgets_by_item_key count={row_widget_count}, '
          f'目前可見 count={visible_count}, hidden row count={hidden_count}, '
          f'image_refs count={image_refs_count}, pending thumbnail count={pending_thumbnail_count}, '
          f'active thread count={active_thread_count} ({thread_names})')

    t_destroy_start = perf_counter()
    window.destroy()
    destroy_duration = perf_counter() - t_destroy_start
    print(f'[Shutdown Perf] window.destroy()={destroy_duration:.4f}s')

    total_duration = perf_counter() - t_close_start
    print(f'[Shutdown Perf] on_main_window_close() total={total_duration:.4f}s')


window.protocol('WM_DELETE_WINDOW', on_main_window_close)

window.mainloop()
