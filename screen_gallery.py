"""
screen_gallery.py — Gallery 畫面（DVR List UI + Batch Action + Favorite Tab + Filter）

2026-08-11 大改版，重點：
1. 切到 Gallery Tab 需要重新 GET /dashcam/recordings，不能只顯示舊的
   cache（這條規則在 2026-08-13 第三次調整之後改成「只有第一次進入才
   需要」，細節見下面 ensure_loaded() 的說明）。用 is_refreshing 旗標
   擋掉「還在 refresh 時又被觸發一次」的情況，避免同時有兩個
   recordings request 在跑。
2. 每一列前面多一個 Checkbox，支援多選，只改變選取狀態，不會觸發播放、
   不會切頁——點 Checkbox 跟點 Thumbnail/文字是綁在不同 widget 上的兩件事。
3. 上方新增 Batch Action（Select Action 下拉 + Confirm 按鈕），可以對勾選的
   項目做 Favorite / Unfavorite / Delete。
4. 協議是照 AutoMate 既有案例（test_DA005_edit_00x.py）確認過的，
   細節放在 api.py 的註解裡：先逐一標記每個要處理的 group/fileid，
   全部標記完才補送一次 groupid=0 當 commit，DVR 才會真的套用。
5. 點縮圖播放前，一律先確認 DVR 是不是字面上的 STANDBY：不管目前是一般
   Recording、Event Recording，還是其他任何非 STANDBY 狀態，都先切過去
   並確認成功才播放；已經是 STANDBY 就直接播放。用
   screen_recording.py 的 is_standby_mode()/confirm_standby_mode()
   直接檢查 dvr_mode，不用 derive_dvr_state() 那套只分三種分類的邏輯
   （那套是給 Recording 頁面按鈕顯示用的，細節見那兩個函式的說明）。
   這裡也不會去動 Recording 頁面自己的畫面狀態（那是它自己的事，這次
   沒有要動它）。

資料來源一律是 api.py（真正的 DVR REST API / Protobuf），跟之前一樣。
UI 文字全部改成英文（程式內註解維持中文）。

2026-08-11 追加一次純排版/樣式調整（不動 API、Favorite/Delete/Checkbox/
Player 邏輯）：單一 Toolbar（Gallery 標題＋筆數＋Refresh＋Select Action＋
Apply）、單欄直向 List、卡片縮窄上下留白、Checkbox 垂直置中、次要資訊
（Date/Duration/Group）改小字，Refresh/Dropdown/Apply 統一 Dark theme
樣式（不用 Windows 原生白底按鈕）。

2026-08-11 再追加一次純樣式調整（同樣不動任何功能邏輯）：整體提亮，
Main Background/Toolbar/Card 三層次深淺灰色（都不逼近純黑）；Card 改成
固定寬度（Content Area 的 65%，夾在 480~820px 之間）、靠左對齊，不再
撐滿整個視窗；Duration 跟 Group 合併成一行，卡片更矮；按鈕/下拉選單
hover 時用 <Enter>/<Leave> 手動換成更亮的顏色。

2026-08-11 第三次微調（同樣不動任何功能邏輯）：Apply 改回跟 Refresh 一樣
的 Dark Button Style，黃色 Accent 只留給 Favorite ★ 跟已勾選的 Checkbox
（兩者各自獨立：★ 只看 is_favorite、Checkbox 只看 checked_item_keys）；
Checkbox 跟縮圖的間距縮小；卡片再壓矮一點；「N items」提示文字改更小、
更淡（TEXT_MUTED_COLOR）。

2026-08-13 UI/UX 改版，重點：
1. Checkbox 預設不顯示，只有選了 Select Action 下拉選單裡的動作（不是
   placeholder）才會顯示每一列的 Checkbox，進入「批次選擇模式」；
   選回 Select Action 時，Checkbox 隱藏並清掉目前的勾選狀態。Apply 在
   還沒選動作前是 disabled。這幾個行為都由 on_action_selected() 統一
   處理，不是散在各處各自判斷。
2. 每一列的類型文字旁邊加一個 Icon（Scenic/Event/Photo），用 Canvas
   自己畫簡單的線條圖形，不直接用 Emoji——Emoji 在不同 Windows 字型/
   版本下粗細、對齊、有沒有彩色都不一致，畫面風格會跑掉，自己畫線條圖形
   才能保證跟其他 Dark theme 元件一樣的視覺風格。見 create_type_icon()。
3. 每一列最右側固定顯示 Favorite ☆/★，點擊直接對這一個 group 做
   Favorite/Unfavorite，不需要先進入批次選擇模式；跟原本就有的批次
   Favorite/Unfavorite（Action + Checkbox + Apply）是两种互不影響的操作
   方式，protocol（標記+commit）完全一致，但 2026-08-13 第二次調整之後
   底層不再共用同一個函式，見下面的說明。
4. 新增 Filter 下拉選單（All/Scenic/Event/Photo），純畫面端篩選，不會
   呼叫任何 DVR API，只改變目前顯示哪些 group（get_visible_groups()）。
5. 新增 Favorite Tab：DA005ControlPanel.pyw 那邊會依 Favorite 數量動態
   顯示/隱藏一個 Favorite 分頁。這支檔案負責：
   - show_gallery_view()/show_favorite_view()：切換 current_view_mode，
     決定 get_visible_groups() 要不要只留 is_favorite 的 group
   - 每次 refresh 完，透過 on_favorite_state_changed_callback 回報目前
     Favorite 數量，讓 DA005ControlPanel.pyw 決定要不要顯示/隱藏分頁、
     要不要把使用者從已經消失的 Favorite 分頁帶回 Gallery
   Gallery/Favorite 兩個分頁是同一個畫面（同一個 Frame），只是
   current_view_mode 不同，不是另外開一份重複的清單邏輯。

2026-08-13 第二次調整：單筆 Favorite/Unfavorite（點右側 ☆/★）改成不再
共用 run_batch_action()，也不再觸發任何完整 Gallery 重新整理（不重新
GET recordings、不重新下載縮圖）——原本的做法點一次星星就整個清單重畫，
體感太重也太慢。現在改成 on_star_clicked() 只對這一個 group 送 API，
採用「先樂觀更新畫面，失敗才復原」：點下去當下就直接改本地資料
（representative_item.is_favorite）跟星星圖示，失敗才復原並跳錯誤訊息。
完整 Refresh 只保留在：進入 Gallery/Favorite 分頁、按 Refresh、Delete
完成、或其他需要重新跟 DVR 同步狀態的情況；批次 Favorite/Unfavorite/
Delete（Action + Checkbox + Apply）沒有變，還是走 run_batch_action()、
結束後一樣整個重新整理。

2026-08-13 第三次調整：Favorite 分頁改成純粹是「目前 current_groups 的
篩選結果」，不是另外一份資料，所以 Gallery/Favorite 互相切換
（show_gallery_view()/show_favorite_view()）不會再呼叫 refresh_on_tab_
switch()（已移除，改成下面兩個語意更明確的函式），只會用既有的
current_groups/current_thumbnails_by_item_key 重新篩選、重畫，不會重新
GET recordings、也不會重新下載縮圖，切換分頁才會是即時的。
- ensure_loaded()：只有這個 process 生命週期內「第一次」進入 Gallery/
  Favorite 才會真的去打 API；has_loaded_recordings_once 這個旗標記著
  是不是已經載入過。
- force_refresh()：不管有沒有載入過，一定要重新 GET recordings，給
  「DVR 狀態可能真的變了」的情況用（例如 DA005ControlPanel.pyw 的
  go_back_from_player()：播放前可能已經在 Gallery/Favorite 這邊順手
  停過錄影，回來時清單內容可能已經跟本地快取不一樣了，不能套用
  ensure_loaded() 那種「載入過一次就不再打 API」的判斷）。
Delete 完成後（on_batch_action_done()）維持原本行為不變，一律強制重新整理。
2026-08-17 移除了「使用者手動按 Refresh」這個 UI（HCP3 沒有這顆按鈕），
但 force_refresh() 本身沒有拿掉，DA005ControlPanel.pyw 需要的地方（例如
go_back_from_player()）一樣照樣呼叫。

2026-08-13 第四次調整：在 Favorite 分頁點 ★ 取消收藏，這一列現在會立即
從 Favorite 清單消失，不是只改星星圖示——Favorite 分頁本來就是
current_groups 篩選 is_favorite 的結果，一筆資料的 is_favorite 變成
False，就不再符合這個分頁該顯示的條件，只改星星不夠，見
apply_star_visual_update()：在 Favorite 分頁會呼叫 render_visible_list()
整個依目前 current_groups 重新篩選、重畫（一樣是本地資料/縮圖快取，
沒有呼叫任何 DVR API）；在 Gallery 分頁則維持只改這一個 Star widget，
不用整個重畫。如果 API 後來失敗，復原時一樣用這個函式決定畫面反應
（Favorite 分頁的話，剛剛消失的那一列會重新出現）。取消到最後一筆
Favorite 時自動切回 Gallery、隱藏 Favorite Tab 的行為，是
notify_favorite_state_changed() -> DA005ControlPanel.pyw 的
update_favorite_tab_visibility() 本來就有的邏輯，這次沒有再另外處理。
"""

import sys
import threading
import queue
import inspect
import math
import traceback
import tkinter as tk
from tkinter import messagebox
from io import BytesIO
from time import time, sleep, perf_counter
from PIL import Image, ImageTk

import api
import audi_pb2
import screen_recording
import ui_shapes

THUMBNAIL_SIZE = (144, 81)  # 16:9，卡片高度主要由這個決定，不要太大
ICON_SIZE = 18              # 類型 Icon 的畫布大小（正方形）

# Top Bar 右上角控制區（Normal Mode 的 Filter icon／Selection Mode 的
# X + ...）：固定用同一個 Canvas、同一組尺寸/邊界，兩種模式切換時
# Canvas 本身完全不移動，只切換裡面哪些 item 顯示，避免位置跳動。
RIGHT_CONTROLS_CANVAS_WIDTH = 96
RIGHT_CONTROLS_CANVAS_HEIGHT = 36
RIGHT_CONTROLS_RIGHT_MARGIN = 22   # 跟視窗右緣（含下方 Scrollbar）保持的間距

# Dark theme 三層次：Main Background（最暗）< Toolbar < Card（最亮），
# 但都刻意不逼近純黑，避免看起來像 engineering/debug tool。
BACKGROUND_COLOR = '#26262b'
TOOLBAR_COLOR = '#2f2f36'
ROW_COLOR = '#3d3d45'          # Card：明顯比 Main Background 亮
CONTROL_COLOR = '#4b4b55'      # 按鈕/下拉選單比 Card 更亮一階，突顯可互動
CONTROL_HOVER_COLOR = '#5c5c68'
TEXT_PRIMARY_COLOR = '#f2f2f4'
TEXT_SECONDARY_COLOR = '#c4c4cc'   # 次要文字提高亮度，避免對比不足
TEXT_MUTED_COLOR = '#8f8f97'       # 比 Secondary 更淡，給「13 items」這種輕量提示文字用
# 2026-08-19 使用者要求：Row 移除 File/Group 這種 internal debug
# identifier 之後，Trigger Time／Duration 可以比 TEXT_SECONDARY_COLOR
# 稍微再亮一點、提高可讀性，但仍要明顯比 Scenic/Event/Photo 標題
# （TEXT_PRIMARY_COLOR）暗，維持次要資訊層級，不搶標題的視覺焦點。
GALLERY_ROW_META_TEXT_COLOR = '#d6d6dc'
ACCENT_COLOR = '#FFD700'           # 黃色 Accent，只用在 Favorite 星號跟已勾選的 Checkbox（兩者互不相關）
FAVORITE_STAR_COLOR = ACCENT_COLOR

# 類型 Icon 顏色：Scenic/Photo 用中性色（跟文字同一階），Event 借用
# screen_recording.py 的 ACTIVE_RED_COLOR（跟 Recording 頁面 Event 按鈕
# 亮起來時同一個警示色，語意一致），不重新定義一個新顏色常數。
# 註：不能在模組載入當下（top level）就讀 screen_recording.ACTIVE_RED_COLOR——
# screen_recording.py 跟這支檔案互相 import（screen_recording 需要
# screen_gallery.CONTROL_COLOR 畫按鈕），是循環 import，這支檔案被載入
# 的當下 screen_recording 可能還沒執行到定義 ACTIVE_RED_COLOR 那一行，
# 會直接 AttributeError 讓整個工具開不起來（實測撞過一次）。所以顏色
# 要留到真正畫圖示的時候（draw_event_icon()，那時候兩邊模組都已經載入
# 完成）才去讀，不要在這裡先讀出來存成模組常數。
SCENIC_ICON_COLOR = TEXT_PRIMARY_COLOR
PHOTO_ICON_COLOR = TEXT_PRIMARY_COLOR

# 2026-08-17 第四次調整：使用者要求不要再用比例夾住寬度，Row 直接延伸到
# 接近右側 Scrollbar，左右各留一個固定的小 margin 就好（不是按視窗寬度算
# 比例）。這個 margin 也故意跟 status_label 的左邊距（見 build_screen()
# 的 status_label.pack(padx=(14, ...))）用同一個數字，「N items」文字
# 才會跟 Row 左側對齊。CARD_MIN_WIDTH 維持給小視窗一個下限，避免窗口
# 開太窄時 Row 被壓成負值/過窄。
GALLERY_ROW_HORIZONTAL_MARGIN = 14
CARD_MIN_WIDTH = 480

# ============================================================
# Swipe-to-Delete（2026-08-19 新增）：Gallery Normal Mode 下 Row 向左拖曳
# 單筆刪除，不取代既有的 Long Press Selection Mode / Batch Delete。
# ============================================================
DELETE_AREA_COLOR = '#c0392b'                # 紅色 Delete Area 底色
SWIPE_MOVEMENT_THRESHOLD_PX = 8              # 小幅位移門檻，避免點擊手抖被誤判成 Swipe
SWIPE_HORIZONTAL_DOMINANCE_RATIO = 1.5       # 水平位移要明顯大於垂直位移（避免跟捲動手勢衝突）才算 Swipe
# 2026-08-19 使用者更新規則：Full Swipe Delete 門檻從 50% 提高到 Row 實際
# 寬度的 85~90%（視覺上等同拉到底），取中間值。這個門檻現在只有兩種結果：
# 沒達到 -> Release 一律回彈、不刪除；達到 -> Release 直接刪除、不再跳出
# 確認視窗（見 execute_full_swipe_delete()）。不是固定 px，跟 Row 實際寬度
# 成比例。
FULL_SWIPE_DELETE_THRESHOLD_RATIO = 0.875
DELETE_AREA_MIN_WIDTH_FOR_TRASH_ICON = 40    # 紅色區域太窄時 Trash icon 先不顯示
TRASH_ICON_SIZE = 22
TRASH_ICON_READY_SIZE = round(TRASH_ICON_SIZE * 1.125)   # 達到 Full Swipe 門檻後，Trash icon 放大約 12.5%（使用者要求 10~15%，取中間值）
SNAP_BACK_STEP_COUNT_RATIO = 2 / 3           # 回彈動畫：每一步縮小到目前位移的這個比例，幾步內收斂到 0
SNAP_BACK_STEP_INTERVAL_MS = 12              # 回彈動畫每一步的間隔（純 after()，不用 thread/sleep）
# 2026-08-19 Bug 修正：真機回報「拖到底、Row 完全移出、放開卻卡住不動」。
# row_content 是用 place(x=dx, relwidth=1) 疊在 row_container 上面，抓住
# 滑鼠的 widget（thumbnail_label 等）是 row_content 的子層——如果 dx 剛好
# 讓 row_content 完全脫離 row_container 可視範圍（dx == -card_width，兩者
# 完全不重疊），該 widget 有可能被判定為完全不可見而收不到
# <ButtonRelease-1>（收不到就永遠不會呼叫 on_row_release()，畫面自然卡在
# 拖到一半的紅色全滿狀態）。保留這麼多 px 的重疊，dx 永遠不會讓
# row_content 完全脫離 row_container，Release 事件才能穩定送達。
SWIPE_FULL_DRAG_SAFETY_MARGIN_PX = 4
# 2026-08-19 Bug 修正：加上這次診斷用的 log，先確認真機上實際卡住的
# 原因，問題排查穩定後可以拿掉或改回 False。
GALLERY_SWIPE_DIAGNOSTIC_LOGGING = True


def _swipe_diag_log(message):
    if GALLERY_SWIPE_DIAGNOSTIC_LOGGING:
        print(f'[Swipe] {message}')

# 2026-08-17 HCP3 操作流程改版：移除 Refresh 按鈕／Select Action + Apply
# 這種下拉選單流程，改成長按 Row 進入 Selection Mode，直接勾選＋點 ...
# 選單執行 Favorite/Unfavorite/Delete，不需要 Apply 這個額外步驟。Filter
# 也從 OptionMenu 改成右上角一個漏斗 icon，點下去才彈出既有的篩選選項。
# 這些都只是「操作入口」的改變，底層 filtering/batch action/thumbnail
# lazy loading 完全沿用既有函式，見下面各函式各自的說明。
#SELECTION_ACTIONS = ['Favorite', 'Unfavorite', 'Delete']
SELECTION_ACTIONS = ['Favorite', 'Delete']
# Filter 純畫面端篩選，不會呼叫任何 DVR API。'Scenic' 對應
# api.get_category() 的 'recording' 分類——目前韌體實測過的類型裡，
# 'recording' 分類底下只會出現 Scenic（Parking/Race Track 這些是協議上
# 存在、但目前沒有實機驗證過的類型），所以直接對應成 Scenic，不需要
# 額外再分一類。
FILTER_ALL = 'All'
FILTER_OPTIONS = [FILTER_ALL, 'Scenic', 'Event', 'Photo']
FILTER_TO_CATEGORY = {'Scenic': 'recording', 'Event': 'event', 'Photo': 'photo'}

VIEW_MODE_GALLERY = 'gallery'
VIEW_MODE_FAVORITE = 'favorite'

# 長按 Row 進入 Selection Mode 的時間門檻（使用者建議 500~700ms 之間）。
LONG_PRESS_THRESHOLD_MS = 600

image_refs = []
list_frame = None
list_canvas = None              # 拿來算目前 Content Area 寬度，決定 Card 寬度
status_label = None             # 內容區左上角「N items」/「N selected」小字（2026-08-17 移除了「Gallery」標題本身）
# 2026-08-17 第二次調整：Filter icon 原本用 tk.Button 承載，Button 本身
# 一定會有自己的矩形背景色（bg=CONTROL_COLOR），就算圖示本身透明，還是
# 會看到明顯的灰色方塊；改成跟 screen_player.py 的 Overlay 按鈕、
# DA005ControlPanel.pyw 的 Tab pill 同一種做法——直接畫在一個小 Canvas
# 上（Canvas 本身背景設成跟 Top Bar 一樣的顏色，視覺上完全融合），
# 用 create_image()/create_text() + tag_bind() 處理圖示跟點擊，真正做到
# 沒有任何矩形底色。Normal Mode（Filter）跟 Selection Mode（X/...）
# 共用同一個 Canvas、同一次 pack()，只是用 item 的 state（'normal'/
# 'hidden'/'disabled'）切換要顯示/隱藏/禁用哪一組，Canvas 本身的位置
# 永遠不變，兩種模式切換才不會跳動。
right_controls_canvas = None
filter_icon_image = None        # Filter 漏斗圖示的 PhotoImage 參照
filter_active_dot_image = None  # 漏斗右上角 Active 提示點的 PhotoImage 參照
filter_radio_selected_icon_image = None    # Filter Menu 選項前面的單選圓點（選中）
filter_radio_unselected_icon_image = None  # Filter Menu 選項前面的單選圓點（沒選中）
on_select_video_callback = None
on_select_photo_callback = None
on_favorite_state_changed_callback = None
on_gallery_item_count_changed_callback = None   # 2026-08-19 新增：回報目前 Gallery 最終 item 數，DA005ControlPanel.pyw 用這個決定 Gallery Tab 要不要顯示

# 長按判斷用的計時器狀態：同一時間只會有一根手指/一顆滑鼠按鍵在按，用
# 模組層級的變數記就夠了，不用每個 row 各自維護一份。
_long_press_after_id = None
_long_press_triggered = False

# Swipe-to-Delete 手勢狀態（2026-08-19 新增）：同理，同一時間只會有一根
# 手指/一顆滑鼠按鍵在拖，模組層級變數就夠了，不用每個 row 各自維護一份
# closure 狀態。_swipe_active_item_key 是目前這次按下對應的 item_key，
# <B1-Motion>/<ButtonRelease-1> 都靠它確認事件是不是同一列（理論上一定
# 是，因為 Tk 在按下時會自動把後續的 motion/release 事件都送到同一個
# widget，這裡多一層比對只是防禦性檢查）。
_swipe_press_x = 0
_swipe_press_y = 0
_swipe_active_item_key = None
# 2026-08-19 Bug 修正：原本用 _swipe_confirmed 這個布林值，只能表示「是不是
# 已經確認是 Swipe」，沒辦法表示「Release 已經判定 Full Swipe、正在執行
# Delete」這個狀態，導致理論上（重複/殘留事件）可能讓 Release 被重複處理。
# 改成明確的三態字串，Full Swipe 一經確認就立刻鎖定成 'deleting'，
# on_row_release()/on_row_motion() 看到 'deleting' 一律直接略過，不會再落
# 到 click/long press/回彈任何一個分支：
#   'idle'     —— 沒有在拖，或已經放開處理完了
#   'dragging' —— 已經確認是水平向左 Swipe，跟著手指移動中
#   'deleting' —— Release 判定 Full Swipe，Delete 進行中（鎖定，不可逆）
_swipe_state = 'idle'
_swipe_row_content = None      # 目前正在拖曳的 row_content widget（Thumbnail/文字整體）
_swipe_trash_label = None      # 目前正在拖曳那一列的 Trash icon Label
_swipe_card_width = 0
_swipe_group_items = None
_swipe_dx = 0                  # 目前累積的水平位移（<=0）

current_groups = []                   # 目前從 DVR 拿到的所有 group（未篩選）

# 2026-08-14 使用者確認規則後修正：下面這幾個「每一列的識別 key」原本
# 全部改用 representative_item.fileid，不用 groupid——理由是影片的 groupid
# 保證每組不一樣，但照片可能共用/重複 groupid（甚至是 0，見
# api.group_recordings_for_gallery() 的說明），如果拿 groupid 當這幾個
# 本地 UI 追蹤用的 dict/set 的 key，好幾張不同的照片會共用同一個 key，
# 勾選其中一張的 Checkbox 會讓其他共用 key 的照片也一起被判定成「已勾選」
# （批次操作甚至會誤刪/誤收藏到不是使用者勾選的那幾張）。
#
# 2026-08-20 使用者回報真機 /dashcam/recordings 原始資料本身就有重複
# fileid（三層診斷確認，見 api.compute_item_key() 附近的說明），「fileid
# 對每一筆 Recording 保證唯一」這個假設已經被推翻——直接用 fileid 當這幾個
# dict/set 的 key 一樣會發生 collision，只是換成不同 group 的 row/縮圖/
# Favorite/Checkbox 互相覆蓋，而不是原本擔心的照片共用 groupid 問題。
# 統一改用 api.compute_item_key()（Video 用 groupid、Photo 用 srcUri，
# 各自解決各自的重複問題，見該函式的說明），下面這幾個容器儲存的 key
# 因此都是 compute_item_key() 算出來的 tuple，不再是單純的 fileid。
current_thumbnails_by_item_key = {}   # item_key -> 縮圖 bytes（或 None），重新畫清單（切 Filter/批次模式）不用重新下載
checked_item_keys = set()             # 目前被勾選的 item_key（批次選擇模式用）
checkbox_widgets_by_item_key = {}     # item_key -> Checkbutton widget，方便 batch action 進行中整批 disable

# 2026-08-20 使用者要求 Gallery 增量更新：item_key -> row_container
# widget 對照表，只有增量 diff 更新（_try_incremental_diff_update()，含
# Filter 切換這種多筆變動）跟完整 rebuild（render_visible_list()/
# clear_list()）兩處會維護，用來在不整頁 destroy/rebuild 的情況下，直接
# 定位到哪些既有 widget 只需要重新 pack() 調整順序。跟既有的
# checkbox_widgets_by_item_key/pending_thumbnail_labels 同樣的
# 「item_key -> widget」慣例，不是新發明的 key 規則。
#
# 2026-08-20 使用者要求 Filter Hide/Show Reuse 之後：這份字典現在會保留
# 「item_key 還在 current_groups 裡，只是目前 Filter/View Mode 不顯示」
# 的 hidden row（只 pack_forget()，不 destroy），不再是「只有目前可見的
# 才留著」。真正判斷某個 item_key 目前是不是可見，要看它在不在
# _current_row_order 裡，不能只看它在不在這個字典裡——這個字典現在代表
# 「現在還有 widget 存在（可見或隱藏皆可）」，不是「目前可見」。item_key
# 真的從 current_groups 消失（真的被刪除）時，才會從這裡真正 pop 並
# destroy，見 prune_stale_row_widgets()。
_row_widgets_by_item_key = {}

# 2026-08-20 使用者回報真機 duplicate fileid 排查用的診斷字典：
# item_key -> 這次 render 建立這個 row 時對應的原始 group_items（不是
# widget）。只在完整 rebuild 的迴圈裡使用，跟 clear_list() 同步清空，
# 純粹讓 Layer 3 duplicate 診斷能印出「先前那筆」的完整識別資訊，不影響
# 任何既有行為。
_row_widgets_by_item_key_source = {}

# 2026-08-20 使用者要求 Filter Hide/Show Reuse：hidden row 之後被重新
# 顯示時，需要知道它「上次顯示/建立當下」的內容 signature，才能判斷這段
# 隱藏期間內容是不是悄悄變了（例如 is_favorite）——變了就该那一筆單獨
# destroy+重建，不能直接假設隱藏期間內容不會變就照樣沿用畫面。跟
# _rendered_signature（只涵蓋「目前可見清單」）不同，這裡是「每一個目前
# 還存在 widget 的 item_key（可見或隱藏皆可）」各自的 signature，兩者
# 用途不同、不能合併成一份。
_row_signature_by_item_key = {}

# 2026-08-20 使用者要求追查 Favorite Page Unfavorite 效能問題：
# _try_incremental_diff_update()/clear_list() 已經算好 reused/hidden/
# shown/created/destroyed/各階段 duration，只是印完 log 就丟掉，呼叫端
# 沒辦法再組成自己的 Summary。這裡加兩個「最近一次呼叫結果」的模組全域
# 變數，只是把已經算好的數字存起來給呼叫端讀，不是新增計算、不影響任何
# 既有行為——render_visible_list() 每次呼叫最前面都會重設成 None，呼叫端
# 讀完之後這兩個值分別對應「這次走了 diff 還是 clear_list()/完整
# rebuild」，兩者最多只有一個非 None。
_last_incremental_diff_metrics = None
_last_clear_list_metrics = None

# 2026-08-20 使用者要求把 Empty State（Gallery/Favorite/其他 Filter 結果
# 0 筆時顯示的提示文字）獨立管理，不要再依賴 clear_list()/完整 rebuild
# 才能顯示/切換——這是一個沒有 item_key、跟 row 完全不同結構的 widget
# （純 tk.Label，不算進 _row_widgets_by_item_key），需要自己的持久參照跟
# 顯示狀態，Empty <-> Non-empty 這個轉場才能走 Incremental Diff，不會因為
# 「可見清單變空/從空變有」就整批 destroy 所有隱藏中的 row（真機實測：
# 146 筆 Gallery row，Favorite 1->0 這種轉場之前會整批 destroy，
# destroy_duration 高達 7 秒）。_empty_state_label_widget 是 None 代表
# 還沒建立過（或剛被 clear_list() 完整 rebuild 清掉，需要重新建立）；
# _empty_state_currently_shown 是目前是不是已經 pack() 顯示中，避免重複
# pack() 或重複 pack_forget()。
_empty_state_label_widget = None
_empty_state_currently_shown = False

# 2026-08-24 新增：show_loading_state() 建立的 Loading 佔位標籤原本完全
# 沒有存進任何全域變數，只能靠 clear_list() 對 list_frame.winfo_children()
# 的無差別 destroy 迴圈「間接」清掉——如果資料成功載入後這次 render 走的是
# _try_incremental_diff_update() 增量路徑（完全不呼叫 clear_list()），這個
# Loading Label 會變成孤兒 widget，永久留在畫面上，跟正常顯示的 row 並存。
# 這裡把它納入正式 lifecycle 管理：show_loading_state() 建立時存進這個
# 變數，_hide_loading_state() 是唯一負責清除的地方，不再依賴 clear_list()
# 當唯一清理方式。None 代表目前沒有顯示中。
_loading_label_widget = None

# ============ Thumbnail Lazy Loading（2026-08-14 使用者確認規則）============
# current_thumbnails_by_item_key 現在是「有沒有下載過」的依據，不再是每次
# Refresh 都一次性填滿：Key 存在（值是 bytes 或 None）代表已經下載過/確認
# 過沒有縮圖，不會再重複下載；Key 不存在代表還沒下載過。這份 cache 一路
# 保留到 Refresh 之間都不清空（Favorite/Unfavorite、切 Filter 也都不清），
# 只有在「DVR 最新清單裡已經不存在這個 item」時才會被 prune_stale_thumbnail_cache()
# 清掉（Delete 成功後之所以也會清乾淨，就是因為下次 Refresh 的 GET 結果裡
# 已經沒有這個 item 了，同一個機制一起處理，不用另外寫 Delete 專用的清除邏輯）。
THUMBNAIL_WORKER_COUNT = 2   # 固定只開這幾個背景 worker，不會隨 row 數量增加，避免一次對 DVR 開大量 request
_thumbnail_download_queue = queue.Queue()
_thumbnail_workers_started = False

requested_item_keys = set()          # 已經排進下載佇列、還沒有結果的 item_key，擋重複下載用
pending_thumbnail_labels = {}        # item_key -> (thumbnail_label widget, representative_item)，還沒下載完成的 row 用這個原地更新，不整個重畫

# 2026-08-19 使用者回報 Regression：部分已經在可視範圍內的 Row 一直停在
# No Thumbnail。追下來原本的設計是「下載失敗（含逾時/例外）就直接把
# None 寫進 current_thumbnails_by_item_key，永久當作『這筆沒有縮圖』，
# 不會再重試」——Photo 縮圖沒有韌體預先產生好的小圖，下載的是原始全尺寸
# 照片（download_photo_preview_image()，可能好幾 MB）再本地縮小，遠比
# Video 縮圖（直接抓小圖）容易因為網路壅塞/逾時偶發失敗一次，一旦失敗
# 就永久卡住，使用者要重開 App 才會消失（下次清單抓到才會走
# prune_stale_thumbnail_cache()，但那只清「DVR 清單裡已經不存在」的
# item，不會清「還在清單裡但曾經下載失敗」的）。
# 這裡加一個有限次數的重試：失敗次數還沒到上限之前，不寫進
# current_thumbnails_by_item_key（維持 pending 狀態），只從
# requested_item_keys 移除，讓它有機會在下一次可視範圍檢查
# （GALLERY_VISIBILITY_CHECK_INTERVAL_MS 週期）被重新排進佇列；
# 真的連續失敗達到上限才判定成正式的 No Thumbnail。
THUMBNAIL_MAX_RETRY_COUNT = 3
thumbnail_retry_counts = {}          # item_key -> 目前累積失敗次數（還沒判定為永久失敗前才會有這個 key）

# 暫時診斷開關（2026-08-19 Regression 排查用）：印出每個 item_key 在
# Lazy Loading 流程裡的狀態轉換（建立 Row / 排入佇列 / 下載結果 / 重試 /
# 最終判定），問題排查完應該移除或改回 False，不是正式功能。
GALLERY_THUMBNAIL_DIAGNOSTIC_LOGGING = True


def _thumbnail_diag_log(item_key, message):
    if GALLERY_THUMBNAIL_DIAGNOSTIC_LOGGING:
        print(f'[Gallery Thumbnail Diag] item_key={item_key}: {message}')


# 2026-08-19 使用者回報「Recording -> Gallery Double Render」排查用的暫時
# 診斷 log：從 go_to_gallery_screen()（DA005ControlPanel.pyw）開始，一路
# 印到 render_visible_list()/clear_list()/queue_visible_thumbnail_downloads()，
# 目的是確認「一次點 Gallery 到底呼叫幾次 render_visible_list()，各自是誰
# 觸發的」。只做記錄，這一輪不修改任何行為。問題排查完應該移除或改回
# False，不是正式功能。
GALLERY_RENDER_DIAGNOSTIC_LOGGING = True
_gallery_render_call_count = 0   # render_visible_list() 從程式啟動以來總共被呼叫幾次，不分畫面/場次，只是方便在 log 裡對照

# 2026-08-19 使用者回報「Video Full Swipe Delete 成功、DVR 確認已刪除，但
# Gallery 畫面卡住，必須切 Tab 才會消失（非最後一筆也會發生）」的暫時
# 診斷 log：只做記錄，不改任何行為。記住「這次 Swipe Delete 到底刪了哪個
# group（所有段落的 fileid + groupid）」，refresh 回來後比對這些 fileid
# 是不是真的已經不在新資料裡，藉此分辨到底是「DVR GET 回來時資料其實還沒
# 真的變」、還是「data_changed 判斷錯」、還是「render 沒真的執行」、還是
# 「舊 Row widget 沒被清掉」。問題排查完應該移除或改回 False。
GALLERY_DELETE_REFRESH_DIAGNOSTIC_LOGGING = True
_diag_last_deleted_fileids = None      # 這次 Swipe Delete 刪除的 group 裡所有段落的 fileid（Photo 只有自己一筆）
_diag_last_deleted_groupid = None
_diag_last_swiped_row_container = None  # 這次被滑開/刪除那一列的 row_container，clear_list() 之後檢查還在不在


def _gallery_render_diag_log(stage_name, extra_info=''):
    """stage_name 是目前所在的函式名稱，caller_name 用 inspect 自動抓「呼叫
    這個函式的人」，不用在每個呼叫點手動傳一個字串進來（容易漏改/寫錯）。
    inspect.stack()[2] 是因為：[0]=這個 helper 自己，[1]=呼叫這個 helper 的
    那個函式（也就是 stage_name 對應的函式本身），[2]=真正呼叫 stage_name
    這個函式的人。"""
    if not GALLERY_RENDER_DIAGNOSTIC_LOGGING:
        return
    caller_name = inspect.stack()[2].function
    print(f'[Gallery Render Diag] t={time():.3f} stage={stage_name} caller={caller_name} '
          f'refresh_token={current_refresh_token} current_groups_count={len(current_groups)} {extra_info}')

# 目前這一「批」可視縮圖下載的計時（給效能量測用，量完可以整段移除）：
# 每次 queue_visible_thumbnail_downloads() 排進新的 item 就開始/累加計數，
# 全部完成歸零時印出這一批總共花了多久——可以看出「可見縮圖全部載入完成
# 時間」，也能看出使用者 Scroll 到下一批時才會出現新的一批計時。
_visible_batch_start_time = None
_visible_batch_pending_count = 0

# 2026-08-20 使用者要求針對 Gallery 載入速度做完整 Performance Profiling
# （這次只加 Log 量測，不做任何架構調整/優化）：
# - _gallery_load_perf_context：一次完整 Gallery Load（load_recordings() ->
#   on_recordings_loaded() -> render_visible_list()，perf_stage_durations
#   有值那種）算完 GET/分組/排序/完成度過濾/Favorite-Filter/建立 UI rows
#   這幾個階段的耗時後，暫存在這裡，等這次連帶觸發的第一批 Thumbnail
#   下載完成（前 20 張，或這批總數不到 20 張就整批下載完）才消費一次、
#   印出完整的「Gallery Load Summary」，然後清成 None——避免同一份
#   context 被之後不相關的縮圖批次（例如使用者切 Filter）重複印出。只有
#   完整 Load 才會設定這個值，切 Filter/批次模式那種局部重畫不會設定，
#   對應的縮圖批次完成時就只印下面這組獨立的 Thumbnail 統計，不印整份
#   Summary。
_gallery_load_perf_context = None

# 每張 Thumbnail 的 download/decode/apply 耗時：只保留前
# THUMBNAIL_PERF_SAMPLE_LIMIT 張的個別數字（避免 log 洗版），但 count/
# total/max 是這一批全部張數的累加，跟 _visible_batch_pending_count 綁在
# 同一個「批次」概念上，同一個地方重置（queue_visible_thumbnail_downloads()
# 偵測到「這是新的一批」的那個時間點）。
THUMBNAIL_PERF_SAMPLE_LIMIT = 20
_thumbnail_perf_samples = []
_thumbnail_perf_count = 0
_thumbnail_perf_total_time = 0.0
_thumbnail_perf_max_time = 0.0
_thumbnail_perf_batch_start = None

# 2026-08-20 使用者回報：第二次進 Gallery（cache_hit=11）比第一次
# （cache_hit=0）多花約 6.7 秒，平均每個 cache-hit row 約 0.61s——遠高於
# on_thumbnail_downloaded() 那條路徑量到的 decode 耗時（2~4ms），需要拆開
# add_list_row() 的 cache-hit branch 細看每個步驟：cache lookup ->
# Image.open/decode -> resize -> ImageTk.PhotoImage -> Label create/config
# -> pack。這裡先只加量測，不改變任何行為。_row_cache_hit_perf_samples
# 在 render_visible_list() 每次重畫開始前清空，收集這次重畫裡每一個
# cache-hit row 的完整分解耗時，重畫結束後印出彙總（total/avg/max，
# 各步驟分開列，才能一眼看出哪一步佔比最高）。
_row_cache_hit_perf_samples = []

is_gallery_screen_active = False     # Gallery/Favorite 分頁目前是不是使用中，決定週期性可視範圍檢查要不要繼續跑
_visibility_check_after_id = None    # list_canvas.after() 排程的 job id，離開畫面時要取消，避免背景無意義一直跑
GALLERY_VISIBILITY_CHECK_INTERVAL_MS = 200

# 2026-08-20 使用者要求優化：queue_visible_thumbnail_downloads() 原本每
# 200ms 就不管畫面有沒有變化，逐一對 pending_thumbnail_labels 裡的每一列
# 呼叫 winfo_rooty() 判斷可視範圍——118 筆時還好，4000 筆時 pending 數量
# 可能長期停留在數千筆，會變成每 200ms 做一次昂貴的全掃描。改成：
# - _visibility_check_last_yview：快取上一次檢查時的 canvas.yview()
#   （(first_fraction, last_fraction) tuple）。這次呼叫如果跟上次完全
#   一樣，代表畫面根本沒有捲動/改變，直接 return，完全不碰任何 widget、
#   不呼叫任何 winfo_rooty()。設成 None 代表「目前這份快取無效，一定要
#   重新掃一次」，任何造成 List 重建的操作（render_visible_list() 每次
#   呼叫都會重置，見該函式）都會讓下一次檢查強制重新掃描，不會因為
#   yview 數值剛好沒變就誤判成不用掃。
# - _current_row_order：目前畫面上由上到下的 item_key 順序（跟
#   render_visible_list() 建立 row 的順序一致，每次重畫都會重建），拿來
#   配合 yview() 的 fraction 概算「大概第幾列到第幾列在畫面上」，不用
#   呼叫 list_frame.winfo_children()（一樣是要問 Tk、一樣是 O(N) 的
#   方式取得順序）。
# - GALLERY_VISIBILITY_SCAN_BUFFER_ROWS：算出來的可視範圍前後各多抓幾列
#   當緩衝，避免快速捲動時縮圖來不及排入下載佇列。
_visibility_check_last_yview = None
_current_row_order = []
GALLERY_VISIBILITY_SCAN_BUFFER_ROWS = 3

current_view_mode = VIEW_MODE_GALLERY  # 'gallery' 顯示全部；'favorite' 只顯示 is_favorite 的 group
current_filter = FILTER_ALL            # 'All'/'Scenic'/'Event'/'Photo'，純畫面篩選

# 2026-08-20 使用者回報真機 Root Cause：Gallery -> Recording -> Gallery
# 再次進入時，show_gallery_view()/show_favorite_view() 原本無條件呼叫
# render_visible_list()，即使 current_groups 完全沒變也會整個
# clear_list() destroy 掉全部既有 row（含子 widget）再重建一次——真機
# 121 筆量到 destroy_duration≈6.93s、UI row creation≈7.09s。
# 這三個變數記住「目前畫面上實際顯示的內容，是依照哪個 view_mode/filter、
# 算出來的 signature 是什麼」，只在 render_visible_list() 真的執行完一次
# 完整重畫時更新（見該函式結尾），不是另外發明一套判斷——沿用既有的
# _compute_gallery_display_signature()，跟 on_recordings_loaded() 判斷
# data_changed 用的是同一個函式，只是比對對象換成「這次應該要顯示的
# get_visible_groups()」而不是整個 current_groups（Gallery/Favorite 顯示
# 的是篩選後的子集合，view_mode/filter 不同時子集合可能不同，即使
# current_groups 本身沒變也要能分辨）。初始值 None：代表「還沒有真的
# render 過一次」，第一次進入一定要走完整 render，不會被誤判成可以跳過。
#
# _rendered_is_batch_mode 一併記錄：is_batch_mode 決定每一列有沒有畫
# Checkbox，理論上只會透過 enter_selection_mode()/exit_selection_mode()
# 改變，這兩支函式本身就會立刻呼叫 render_visible_list() 重建，所以正常
# 情況下不會跟目前畫面不同步。但 reset_batch_action_controls()（DVR 斷線
# 的 cancel_pending_work_for_disconnect() 會呼叫到）會直接把 is_batch_mode
# 設回 False、清空 checked_item_keys，卻不會呼叫 render_visible_list()
# ——如果使用者「在 Gallery 進入 Selection Mode -> 切去 Recording -> DVR
# 這段期間斷線又重新連線 -> 使用者再切回 Gallery」，這個時候 is_batch_mode
# 已經變成 False，但畫面上（因為使用者當時不在 Gallery，沒有任何 render
# 發生）還殘留著舊的、is_batch_mode=True 時建立的 Checkbox widget。純比對
# signature/view_mode/filter 抓不到這種情況（資料內容本身沒變），一定要
# 額外比對 is_batch_mode，不然會讓 Top Bar（已經正確變回 Filter icon）
# 跟 Row 上殘留的 Checkbox 兩者狀態不一致。
_rendered_view_mode = None
_rendered_filter = None
_rendered_signature = None
_rendered_is_batch_mode = None

# 2026-08-24 新增：report_gallery_item_count_from_recordings()（Recording
# 頁面刷新/SD 卡插入時的側漏更新，見該函式說明）會直接覆寫 current_groups，
# 但刻意不呼叫 render_visible_list()（呼叫當下 Gallery 通常不可見）。這造成
# 一個落差：current_groups（資料）已經換成最新資料，但畫面上實際顯示的
# row widget 還停留在上一次真的 render 時的內容——換 SD 卡（拔卡時 Gallery
# 已被強制導出，插新卡後這支側漏函式在使用者回到 Gallery 之前就先跑過）
# 是最容易踩到這個落差的情況。
#
# 真機證實：如果不管，之後不管是 show_gallery_view()/show_favorite_view()
# 的 _can_skip_rebuild_for_current_view() cache 路徑，還是 on_recordings_
# loaded() 比對 data_changed 的 fresh GET 路徑，都可能因為「這次要顯示的
# 內容」跟「current_groups/上次 render 內容」剛好一致（因為 current_groups
# 已經被側漏更新過），誤判成「沒有變化」而跳過 render，畫面因此停留在插卡
# 前的舊內容，除非資料之後又真的變一次才會被動修正。
#
# 這個旗標記錄「current_groups 已經變了、但畫面還沒有真的重畫過一次」，
# 由 report_gallery_item_count_from_recordings() 設成 True；_can_skip_
# rebuild_for_current_view() 跟 on_recordings_loaded() 都要把它當成
# 「不能跳過 render」的條件之一；任何一個真的完成 render 的地方（完整
# rebuild 的 render_visible_list()、增量更新的 _try_incremental_diff_
# update()）都要負責在成功套用後把它清掉，下次才能再依正常規則判斷要不要
# 跳過。
_current_groups_changed_without_render = False

is_batch_mode = False                  # Select Action 選了非 placeholder 的動作才是 True，控制 Checkbox 顯示

# 2026-08-19 使用者要求：Selection Mode 要能撐過「切去別的分頁再切回來」
# （Gallery -> Recording -> Gallery 不能自動清掉已勾選項目），只有點 X、
# Batch Action 成功/失敗完成、DVR 斷線、或最後一筆資料被刪除這幾種情況
# 才離開 Selection Mode。on_recordings_loaded() 是 Batch Action 完成
# （on_batch_action_done() -> start_refresh()）跟一般切頁重新整理
# （force_refresh()）共用的同一個完成點，兩者都會在這裡收斂，沒辦法只看
# 「有沒有 refresh 完」判斷該不該退出 Selection Mode。這個旗標由
# on_batch_action_done() 在呼叫 start_refresh() 之前設成 True，
# on_recordings_loaded() 消費一次就清掉，只有這種情況才真的呼叫
# reset_batch_action_controls()，一般的切頁重新整理不會誤觸發。
_exit_selection_mode_after_next_refresh = False

is_refreshing = False          # refresh 進行中，擋掉重複觸發
is_gallery_busy = False        # refresh / batch action / 播放前確認錄影狀態 期間都會是 True，擋掉點列表播放
current_refresh_token = 0      # 每次 refresh 都 +1，過期的背景結果不要套用


def build_screen(parent, on_select_video, on_favorite_state_changed, on_select_photo,
                  on_gallery_item_count_changed=None):
    """建立 Gallery 畫面（Gallery/Favorite 共用同一個 Frame）。
    on_select_video 是確認可以播放後要呼叫的函式；on_select_photo 是點到
    Photo 類型項目時要呼叫的函式（Photo 完全不走 on_select_video/Player
    那條路，見 on_row_click() 的說明）；on_favorite_state_changed 是每次
    refresh 完，回報目前 Favorite 數量的函式（DA005ControlPanel.pyw 用
    這個決定 Favorite Tab 要不要顯示）；on_gallery_item_count_changed
    （2026-08-19 新增，預設 None 保留給既有測試腳本相容）是每次 refresh
    完，回報目前 Gallery 最終 item 數的函式（DA005ControlPanel.pyw 用這個
    決定 Gallery Tab 要不要顯示，機制跟 Favorite 完全一樣）。

    2026-08-17 第三次調整：使用者要求拿掉「Gallery」這個標題（上方
    Navigation 已經有 Gallery Tab，重複了），Filter icon 也從這裡搬到
    最上方 Navigation Bar、跟 Settings Gear 同一排（見
    create_nav_filter_controls()，由 DA005ControlPanel.pyw 呼叫建立，
    這裡不再自己畫）。這個畫面現在只剩一條很窄的「N items/N selected」
    小字說明，不再是一整條 Header。"""
    global list_frame, status_label, on_select_video_callback
    global on_favorite_state_changed_callback, on_select_photo_callback
    global on_gallery_item_count_changed_callback

    on_select_video_callback = on_select_video
    on_select_photo_callback = on_select_photo
    on_favorite_state_changed_callback = on_favorite_state_changed
    on_gallery_item_count_changed_callback = on_gallery_item_count_changed

    frame = tk.Frame(parent, bg=BACKGROUND_COLOR)

    # ---- 內容區左上角一行小字：Normal Mode「N items」/Selection Mode
    # 「N selected」，字體小、顏色低調，不再是佔一整條的 Header（原本的
    # 「Gallery」標題／Filter icon 都已經移除/搬走）。
    status_bar = tk.Frame(frame, bg=BACKGROUND_COLOR)
    status_bar.pack(fill='x')

    status_label = tk.Label(status_bar, text='Not loaded yet', font=('Calibri', 9),
                             bg=BACKGROUND_COLOR, fg=TEXT_MUTED_COLOR)
    status_label.pack(side='left', padx=(14, 0), pady=(8, 4))

    list_frame = create_scrollable_list_area(frame)

    show_no_data_placeholder()

    ensure_thumbnail_workers_started()  # 固定開幾個縮圖下載背景 worker，只在這裡啟動一次
    ensure_trash_icon_images_built()    # Swipe Delete 用的垃圾桶圖示，只畫一次

    return frame


def create_nav_filter_controls(parent, bg_color):
    """2026-08-17 第三次調整：使用者要求 Filter icon 從 Gallery 內容區搬到
    最上方 Navigation Bar、放在 Settings Gear 左邊，所以這個 Canvas 現在
    由 DA005ControlPanel.pyw 呼叫這支函式建立（parent 是主視窗的 Top
    Navigation Bar，bg_color 要跟那裡的底色一致，才能真正做到透明背景、
    不出現方塊——這裡不能自己認定顏色，因為現在的父層背景色跟這支檔案
    自己的 TOOLBAR_COLOR 不是同一個）。

    畫面內容/互動邏輯完全不變，只是換了 parent/位置：Normal Mode 顯示
    Filter 漏斗圖示、Selection Mode 顯示 X + ...，兩者共用同一個 Canvas、
    同一次 pack()，只用 itemconfigure() 切換要顯示哪一組（見
    update_top_bar_mode()/_apply_right_controls_item_states()，這兩支
    函式本身完全沒有改，一樣操作模組層級的 right_controls_canvas）。
    回傳建立好的 Canvas，實際要怎麼 pack()（跟 Gear 的間距、要不要依
    Gallery/Favorite Tab 顯示/隱藏）由呼叫端（DA005ControlPanel.pyw）
    自己決定，這裡不管。"""
    global right_controls_canvas, filter_icon_image, filter_active_dot_image
    global filter_radio_selected_icon_image, filter_radio_unselected_icon_image

    right_controls_canvas = tk.Canvas(parent, width=RIGHT_CONTROLS_CANVAS_WIDTH,
                                       height=RIGHT_CONTROLS_CANVAS_HEIGHT,
                                       bg=bg_color, highlightthickness=0, cursor='hand2')

    filter_icon_image = _new_filter_icon_image()
    center_x, center_y = RIGHT_CONTROLS_CANVAS_WIDTH / 2, RIGHT_CONTROLS_CANVAS_HEIGHT / 2
    right_controls_canvas.create_image(center_x, center_y, image=filter_icon_image, tags='filter_icon')
    right_controls_canvas.tag_bind('filter_icon', '<Button-1>', lambda event: show_filter_menu())

    # 2026-08-19 使用者要求：不打開 Menu 也要能知道目前是否套用了 Filter，
    # 在漏斗右上方疊一個小圓點，Active 才顯示（見 _apply_right_controls_
    # item_states()）。位置貼著漏斗右上角，不佔用額外空間，不會影響
    # Settings icon 的位置。
    filter_active_dot_image = _new_filter_active_dot_image()
    dot_x = center_x + FILTER_ICON_SIZE * 0.32
    dot_y = center_y - FILTER_ICON_SIZE * 0.32
    right_controls_canvas.create_image(dot_x, dot_y, image=filter_active_dot_image, tags='filter_active_dot')

    # Filter Menu 選項前面的單選圓點：只需要「選中」跟「沒選中」兩張圖，
    # 四個選項共用，不用每個選項各自畫一張。
    filter_radio_selected_icon_image = _new_radio_icon_image(selected=True)
    filter_radio_unselected_icon_image = _new_radio_icon_image(selected=False)

    close_x = RIGHT_CONTROLS_CANVAS_WIDTH * 0.28
    menu_x = RIGHT_CONTROLS_CANVAS_WIDTH * 0.68
    right_controls_canvas.create_text(close_x, center_y, text='✕', font=('Calibri', 14),
                                       fill=TEXT_PRIMARY_COLOR, tags='selection_close')
    right_controls_canvas.create_text(menu_x, center_y, text='•••', font=('Calibri', 14, 'bold'),
                                       fill=TEXT_PRIMARY_COLOR, tags='selection_menu')
    right_controls_canvas.tag_bind('selection_close', '<Button-1>', lambda event: exit_selection_mode())
    right_controls_canvas.tag_bind('selection_menu', '<Button-1>', lambda event: show_selection_action_menu())

    update_top_bar_mode()  # Normal Mode 起始狀態：只顯示 Filter，X/... 先 hidden

    return right_controls_canvas


def _can_skip_rebuild_for_current_view():
    """2026-08-20 使用者要求：show_gallery_view()/show_favorite_view()
    每次「切回來」都無條件呼叫 render_visible_list() 太貴（真機 121 筆
    destroy+rebuild 約 6~7 秒，見 clear_list()/render_visible_list() 內的
    說明）。這裡在呼叫端決定要不要真的重畫之前，先判斷「這次應該顯示的
    內容」是不是跟「目前畫面上實際顯示的內容」完全一樣——一樣的話回傳
    True，呼叫端可以直接沿用既有 widget tree，完全不用呼叫
    clear_list()/render_visible_list()；只要有任何一點不一樣就回傳
    False，呼叫端照舊呼叫 render_visible_list() 走既有的完整重畫流程
    （沒有另外發明第二套判斷，重畫邏輯本身完全不變）。

    呼叫時機要求：current_view_mode 必須已經是這次要切換過去的目標值
    （show_gallery_view()/show_favorite_view() 都是先設定 current_view_
    mode 再呼叫這裡），這樣 get_visible_groups() 算出來的才是「這次真的
    要顯示的內容」，不是切換前的舊 view_mode。

    四個條件都要同時成立才能跳過：
    - _rendered_signature 不是 None（代表至少已經完整 render 過一次，
      不是 App 剛啟動、第一次進入這種情況——那種情況一定要走完整 render）。
    - _rendered_view_mode == 目前的 current_view_mode，且
      _rendered_filter == 目前的 current_filter——不能只比對 signature：
      「Gallery 是空的」跟「Favorite 是空的」兩種情況的 signature 都是
      []，但畫面上該顯示的 Empty State 文字（get_empty_state_text()）
      依 view_mode/filter 而不同（例如「No data available.」vs.
      「No favorites yet.」），純比對 signature 會讓文字對不上目前的
      view_mode/filter，所以一定要三個條件一起比對，不能只看內容。
    - _rendered_is_batch_mode == 目前的 is_batch_mode——正常情況下
      is_batch_mode 只會透過 enter_selection_mode()/exit_selection_mode()
      改變，這兩支函式本身就會立刻呼叫 render_visible_list()，不會跟畫面
      不同步；但 DVR 斷線會經由 cancel_pending_work_for_disconnect() ->
      reset_batch_action_controls() 把 is_batch_mode 直接設回 False、
      不呼叫 render_visible_list()，如果這件事剛好發生在使用者離開
      Gallery 期間（例如在 Selection Mode 中切去 Recording，這段期間
      DVR 斷線又重新連線），純比對 signature/view_mode/filter 看不出
      內容有變化，會誤把「畫面上還殘留 Selection Mode 的舊 Checkbox」
      當作可以跳過，讓 Top Bar（已經正確變回 Filter icon）跟 Row 上的
      Checkbox 顯示狀態不一致，所以要多這一個條件擋住。
    - 用目前的 view_mode/filter 算出來的 get_visible_groups() signature
      要跟 _rendered_signature 完全一樣（沿用既有的
      _compute_gallery_display_signature()，比對對象是「這次應該要顯示
      的子集合」，不是整個 current_groups——Gallery 顯示全部、Favorite
      只顯示收藏，view_mode 不同時可見子集合可能不同，即使 current_
      groups 本身沒變也要能分辨；這也是為什麼 Gallery<->Favorite 切換
      在內容真的不同時，這裡一定會正確判斷成「不能跳過」，不會誤沿用
      錯的 View）。

    2026-08-24 新增第五個條件：_current_groups_changed_without_render 是
    True 代表 current_groups 剛被 report_gallery_item_count_from_
    recordings() 側漏更新過、畫面還沒真的重畫過一次（換 SD 卡最容易踩到，
    見該旗標宣告處的說明），這時候即使 signature/view_mode/filter/
    is_batch_mode 剛好都比對得上，也不能跳過，一定要強制走一次真的
    render，才能把「畫面上實際顯示的東西」跟已經被側漏更新的 current_
    groups 追上。"""
    if _current_groups_changed_without_render:
        return False
    if _rendered_signature is None:
        return False
    if (_rendered_view_mode != current_view_mode or _rendered_filter != current_filter
            or _rendered_is_batch_mode != is_batch_mode):
        return False
    candidate_signature = _compute_gallery_display_signature(get_visible_groups())
    return candidate_signature == _rendered_signature


def show_gallery_view(reset_scroll_to_top=False):
    """切到 Gallery 模式：顯示全部 group（還是會套用目前的 Filter）。
    由 DA005ControlPanel.pyw 點 Gallery Tab 時呼叫。Favorite 分頁本來就
    只是目前 current_groups 的篩選結果，不是另外一份資料，所以這裡只是
    切換 current_view_mode 後直接用既有快取重畫，不會重新 GET recordings、
    不會重新下載縮圖——真的要不要重新跟 DVR 同步是 ensure_loaded()（給
    第一次進入用）或 force_refresh()（給狀態可能真的變了的情況用）的事，
    不是切分頁本身的責任。

    2026-08-20 使用者回報 Root Cause：Gallery -> Recording -> Gallery
    再次進入時，這裡原本無條件呼叫 render_visible_list()，即使 current_
    groups 完全沒變也會整個 destroy 掉全部既有 row 再重建一次。現在先
    用 _can_skip_rebuild_for_current_view() 判斷這次要顯示的內容是不是
    跟目前畫面上已經有的完全一樣——一樣的話直接沿用既有 widget tree
    （Thumbnail 已下載/還在 pending 的都原封不動），只有真的偵測到不一樣
    才照舊呼叫 render_visible_list() 重建；不管走哪一條，Row 的建立/
    destroy 都完全跟捲動位置的處理分開（見下面 reset_scroll_to_top）。

    2026-08-20 使用者推翻上一版「Gallery -> Recording -> Gallery 保留
    scroll position」的規則：新規則是 Recording -> Gallery 一律要重設
    捲動到最上方，但 Player/Photo Viewer -> Gallery 仍然要維持原本保留
    位置的行為——兩者呼叫的是同一支函式，差別只在呼叫端傳不傳
    reset_scroll_to_top=True（DA005ControlPanel.pyw 的 _enter_gallery_
    screen_from_tab() 點 Gallery Tab 時傳 True；go_back_from_player()
    從 Player 返回時維持不傳，預設 False），不需要另外寫第二套「返回
    Gallery」的函式。reset_scroll_to_top=True 時一定會呼叫
    reset_gallery_scroll_to_top()，且不管上面是跳過重畫、走增量 diff、
    還是完整 rebuild 都一樣會呼叫（「Scroll reset 不代表 rebuild」：这裡
    重設捲動位置跟要不要重畫是兩件事，即使完全沒有呼叫 render_visible_
    list()，畫面還是要捲回頂端）。"""
    global current_view_mode
    current_view_mode = VIEW_MODE_GALLERY
    _gallery_render_diag_log('show_gallery_view', '切換 view mode，即將用既有 current_groups/cache render 一次')
    if _can_skip_rebuild_for_current_view():
        _gallery_render_diag_log('show_gallery_view', '目前顯示內容跟上次 render 完全相同，跳過 clear_list()/render_visible_list()，直接沿用既有 widget tree')
    else:
        # 2026-08-20 使用者回報：真機最常見的操作路徑（開 Tool -> 點 Gallery）
        # 走的其實是這裡，不是 load_recordings() 那條「完整 GET」路徑——
        # current_groups 通常已經被 Recording 頁面的 report_gallery_item_
        # count_from_recordings() 側漏填好，這裡是使用者第一次真的「看到」
        # Gallery List 的那一刻，卻完全沒有 Stage 4/6/7 計時、也沒有 Gallery
        # Load Summary。這裡補上 cache_render_start，讓 render_visible_list()
        # 也能量到這條路徑，用 mode=cache_render 的 Summary 跟 load_recordings()
        # 那條 mode=fresh_refresh 的 Summary 明確分開，不要混在一起算。
        render_visible_list(cache_render_start=perf_counter())
    if reset_scroll_to_top:
        reset_gallery_scroll_to_top()
    # 2026-08-19 使用者要求：Gallery 畫面模式（is_batch_mode）是 Top
    # Navigation 右上角要顯示 Filter/Gear 還是 X/... 的唯一依據，不能讓
    # Top Navigation 自己保存一份不同步的狀態。這裡明確重新套用一次，
    # 不管是不是本來就正確——即使目前沒有其他程式路徑會弄錯，這樣寫才是
    # 「每次顯示 Gallery 都保證跟 is_batch_mode 一致」，不是靠「剛好沒人
    # 動過」這種隱性假設。不管上面是跳過還是真的重畫都要做，跟這次有沒有
    # 重畫無關。
    update_top_bar_mode()
    start_visibility_check_loop()  # Thumbnail Lazy Loading 的週期性可視範圍檢查


def show_favorite_view(reset_scroll_to_top=False):
    """切到 Favorite 模式：只顯示 is_favorite 的 group（還是會套用目前的
    Filter）。由 DA005ControlPanel.pyw 點 Favorite Tab 時呼叫，一樣只是
    重新篩選、重畫既有的 current_groups，不會重新 GET recordings、不會
    重新下載縮圖，理由同 show_gallery_view()。2026-08-17 移除了「Gallery」/
    「Favorite」這個標題文字（上方 Navigation 的 Tab 本身已經能表示目前
    在哪個分頁），這裡不用再切換任何標題文字。

    2026-08-20 使用者要求：理由同 show_gallery_view()，一樣先判斷這次要
    顯示的內容是不是跟目前畫面完全相同，相同就跳過 clear_list()/
    render_visible_list()；reset_scroll_to_top 參數的語意/呼叫時機也
    完全比照 show_gallery_view()（Recording -> Favorite 由呼叫端傳
    True，Player/Photo Viewer 相關路徑維持不傳）。"""
    global current_view_mode
    current_view_mode = VIEW_MODE_FAVORITE
    if _can_skip_rebuild_for_current_view():
        _gallery_render_diag_log('show_favorite_view', '目前顯示內容跟上次 render 完全相同，跳過 clear_list()/render_visible_list()，直接沿用既有 widget tree')
    else:
        # 2026-08-20 使用者要求：理由同 show_gallery_view()，Favorite Tab 點擊
        # 一樣是直接用既有 current_groups cache render，同樣要能被 Performance
        # Profiling 量到（mode=cache_render）。
        render_visible_list(cache_render_start=perf_counter())
    if reset_scroll_to_top:
        reset_gallery_scroll_to_top()
    update_top_bar_mode()  # 理由同 show_gallery_view()：is_batch_mode 是唯一來源，每次顯示都明確重新套用
    start_visibility_check_loop()  # Thumbnail Lazy Loading 的週期性可視範圍檢查


class _MacDarkButton(tk.Label):
    """macOS 專用的按鈕外觀替身——只在 style_dark_button() 判斷是 macOS 時
    才會用到，Windows 完全不會建立這個 class。

    macOS 的 Aqua 主題下，tk.Button 的背景色（bg/activebackground）不會
    被套用，只會顯示系統原生的淺色按鈕外觀，Dark theme 預設的淺色文字疊
    上去就是使用者回報的「白底白字」看不到字。tk.Label 的 bg/fg 在所有
    平台都是真的會套用，所以外觀改用 Label 畫；但 Label 本身沒有
    「command 只在沒 disabled 時才觸發」這種邏輯，所以背後保留原本那顆
    真正的 tk.Button（沒有 pack/grid 進畫面，使用者看不到它，只是借用它
    既有的 command/state 機制）——點擊時呼叫 hidden_button.invoke()，
    Tkinter 自己就會在 state='disabled' 時什麼都不做，跟原生 Button 行為
    完全一致；外部呼叫端對這個 class 的實例呼叫 .config(state=...) 時，
    這裡攔下來同時轉給隱藏的 Button，畫面上的 Label 才會跟著變灰。"""

    def __init__(self, hidden_button, **label_kwargs):
        super().__init__(hidden_button.master, **label_kwargs)
        self._hidden_button = hidden_button
        self.bind('<Button-1>', lambda event: self._hidden_button.invoke())

    def config(self, **kwargs):
        if 'state' in kwargs:
            self._hidden_button.config(state=kwargs['state'])
        super().config(**kwargs)

    configure = config


def style_dark_button(button, bg_color=CONTROL_COLOR, hover_color=CONTROL_HOVER_COLOR,
                       fg_color=TEXT_PRIMARY_COLOR):
    """把 tk.Button 從 Windows 原生白底改成統一的 Dark theme 樣式。
    Hover 時要真的提高亮度，這裡直接綁 <Enter>/<Leave> 手動換色，
    不只靠 activebackground（有些平台只有滑鼠按住時才會用 active 顏色）。

    macOS 上 tk.Button 沒辦法真的改背景色（見 _MacDarkButton 的說明），
    改用 Label 替身；呼叫端拿到的回傳值不管是 Windows 的 tk.Button 還是
    macOS 的 _MacDarkButton，都支援 .pack()/.grid()/.config(state=...)，
    呼叫端既有的程式碼不用另外判斷平台。"""
    if sys.platform == 'darwin':
        label = _MacDarkButton(button, text=button.cget('text'), bg=bg_color, fg=fg_color,
                                font=('Calibri', 10), cursor='hand2', padx=14, pady=6)
        label.bind('<Enter>', lambda event: label.config(bg=hover_color))
        label.bind('<Leave>', lambda event: label.config(bg=bg_color))
        return label

    button.config(bg=bg_color, fg=fg_color, activebackground=hover_color, activeforeground=fg_color,
                   relief='flat', bd=0, highlightthickness=0, padx=14, pady=6,
                   font=('Calibri', 10), cursor='hand2')
    button.bind('<Enter>', lambda event: button.config(bg=hover_color))
    button.bind('<Leave>', lambda event: button.config(bg=bg_color))
    return button


def build_dark_popup_menu(parent):
    """建立一個 Dark theme 的 tk.Menu，給 Filter/Selection Action 兩個彈出
    選單共用同一套樣式，不用各自重複設定顏色。"""
    return tk.Menu(parent, tearoff=0, bg=CONTROL_COLOR, fg=TEXT_PRIMARY_COLOR,
                   activebackground=CONTROL_HOVER_COLOR, activeforeground=TEXT_PRIMARY_COLOR,
                   font=('Calibri', 10), bd=0, relief='flat')


def popup_menu_below_canvas_item(menu, canvas, tag):
    """把選單彈出在 Canvas 上某個 item（用 tag 找）的正下方——Filter/...
    現在都是畫在同一個 Canvas 上的 item，不是各自獨立的 widget，用
    canvas.bbox(tag) 換算成螢幕座標，不能再用 widget 本身的
    winfo_rootx()/winfo_rooty()。"""
    bbox = canvas.bbox(tag)
    if bbox is None:
        x, y = canvas.winfo_rootx(), canvas.winfo_rooty() + canvas.winfo_height()
    else:
        x = canvas.winfo_rootx() + bbox[0]
        y = canvas.winfo_rooty() + bbox[3]
    try:
        menu.tk_popup(x, y)
    finally:
        menu.grab_release()


def create_scrollable_list_area(parent):
    """建立一個可以上下捲動的區域，回傳可以放清單列的 inner frame。
    inner_frame 的寬度要跟著 canvas 一起變寬，這樣才能拿 canvas 目前的寬度
    去算 Card 應該要多寬（Card 本身不會撐滿，只佔一個比例，見 add_list_row）。"""
    global list_canvas

    container = tk.Frame(parent, bg=BACKGROUND_COLOR)
    container.pack(fill='both', expand=True)

    canvas = tk.Canvas(container, bg=BACKGROUND_COLOR, highlightthickness=0)
    list_canvas = canvas
    scrollbar = tk.Scrollbar(container, orient='vertical', command=canvas.yview)
    inner_frame = tk.Frame(canvas, bg=BACKGROUND_COLOR)

    inner_frame_window_id = canvas.create_window((0, 0), window=inner_frame, anchor='nw')
    inner_frame.bind('<Configure>', lambda event: canvas.configure(scrollregion=canvas.bbox('all')))
    canvas.bind('<Configure>', lambda event: canvas.itemconfig(inner_frame_window_id, width=event.width))
    canvas.configure(yscrollcommand=scrollbar.set)

    canvas.pack(side='left', fill='both', expand=True)
    scrollbar.pack(side='right', fill='y')

    def on_mouse_wheel(event):
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
        canvas.yview_scroll(scroll_units, 'units')
    canvas.bind('<Enter>', lambda event: canvas.bind_all('<MouseWheel>', on_mouse_wheel))
    canvas.bind('<Leave>', lambda event: canvas.unbind_all('<MouseWheel>'))

    return inner_frame


def show_no_data_placeholder():
    global _empty_state_label_widget, _empty_state_currently_shown
    for widget in list_frame.winfo_children():
        widget.destroy()
    # 理由同 show_loading_state()：這裡也是跟 clear_list() 分開的另一個
    # destroy 全部 list_frame children 的地方，一併重設，避免懸空參照。
    _empty_state_label_widget = None
    _empty_state_currently_shown = False
    tk.Label(list_frame, text='No data loaded yet.', bg=BACKGROUND_COLOR, fg=TEXT_SECONDARY_COLOR).pack(pady=20)


# ============================================================
# Refresh：什麼時候才需要真的重新 GET recordings / 重新下載縮圖
#
# 完整重新同步 DVR 資料只在以下情況執行：
# 1. 第一次進入 Gallery（ensure_loaded()）
# 2. Delete 完成後（on_batch_action_done() 一律呼叫 start_refresh()）
# 3. 播放前可能已經順手停過錄影，DVR 狀態可能真的變了，回到
#    Gallery/Favorite 時要強制重新同步（DA005ControlPanel.pyw 的
#    go_back_from_player() 呼叫 force_refresh()）
#
# （2026-08-17 移除了「使用者手動按 Refresh」這個 UI，HCP3 沒有這顆按鈕；
# force_refresh() 這個函式本身還在，只是沒有 UI 可以直接觸發它，給上面
# 第 3 點跟其他需要強制重新整理的情況用。）
#
# Gallery 分頁本身跟 Favorite 分頁單純切換（不屬於上面任何一種情況）
# 不會觸發這裡的任何一個函式，只會呼叫 show_gallery_view()/
# show_favorite_view() 用既有的 current_groups 重新篩選、重畫，
# 不會呼叫 DVR API、也不會重新下載縮圖。
# ============================================================

has_loaded_recordings_once = False   # 這個 process 生命週期內有沒有成功載入過一次


def ensure_loaded():
    """給第一次進入 Gallery/Favorite 分頁用：如果這個 process 還沒成功
    載入過任何一次 recordings，才會真的去打 API；已經載入過的話什麼都
    不做——Gallery/Favorite 互相切換不會呼叫這個以外的任何東西，切換
    才會是即時的。"""
    if has_loaded_recordings_once or is_refreshing:
        return
    start_refresh()


def force_refresh():
    """不管有沒有載入過，一定要重新 GET recordings——用在『DVR 狀態可能
    真的變了，本地資料可能跟 DVR 不一致』的情況，例如播放前在 Gallery/
    Favorite 這邊順手停過錄影，回來時清單內容可能已經不一樣了，這時候
    不能套用 ensure_loaded() 那種『載入過一次就不再打 API』的判斷。"""
    _gallery_render_diag_log('force_refresh', f'is_refreshing={is_refreshing}（True 代表這次呼叫會被擋掉，不會真的 start_refresh）')
    if is_refreshing:
        return
    start_refresh()


def start_refresh():
    """2026-08-19 使用者要求「cache-first + background refresh」：目前
    current_groups 已經有資料的話，畫面上已經是 render_visible_list()
    用 cache 畫出來的完整清單，不能再用 show_loading_state() 把它整個
    清空、換成一個「Loading...」佔位——這是造成「List -> Loading -> List」
    Double Render 跳動的直接原因之一。只有真的完全沒有資料可以顯示
    （current_groups 是空的，例如第一次進 Gallery）才需要這個過渡畫面；
    有 cache 的話維持顯示現有內容，背景 GET 完成後由 on_recordings_loaded()
    自己判斷資料有沒有真的變化，決定要不要真的重畫（見該函式的說明）。"""
    global is_refreshing, current_refresh_token

    is_refreshing = True
    current_refresh_token += 1
    refresh_token = current_refresh_token
    _gallery_render_diag_log('start_refresh', f'new_refresh_token={refresh_token}, has_cache={bool(current_groups)}')

    set_controls_busy(True)
    if not current_groups:
        status_label.config(text='Loading...')
        show_loading_state()

    thread = threading.Thread(target=load_recordings, args=(refresh_token,), daemon=True)
    thread.start()


def show_loading_state():
    global _empty_state_label_widget, _empty_state_currently_shown, _loading_label_widget
    _gallery_render_diag_log('show_loading_state', f'destroy_children_count={len(list_frame.winfo_children())}')
    # 2026-08-24 新增：正式建立新的 Loading 之前，先透過 _hide_loading_state()
    # 清掉上一個還沒被消費掉的（理論上不該發生——start_refresh() 同一時間
    # 只會有一個 refresh 在跑，但防禦性處理，不假設呼叫端一定乾淨）。這是
    # 明確的 lifecycle 呼叫，不是靠下面這個迴圈間接 destroy。
    _hide_loading_state()
    for widget in list_frame.winfo_children():
        widget.destroy()
    # 2026-08-20 使用者要求把 Empty State 獨立管理之後：這裡是跟
    # clear_list() 完全分開的另一個 destroy 全部 list_frame children 的
    # 地方（Refresh 開始時顯示「Loading...」用），如果剛好前一刻正在顯示
    # Empty State，上面的 destroy 迴圈會把它一起摧毀，這裡沒有明確重設
    # 的話，_empty_state_label_widget 會變成指向一個已經被 destroy 的
    # widget 的懸空參照，下次呼叫 _show_empty_state_label() 對它
    # .config() 會直接丟 TclError（實測抓到的真實 bug，不是理論假設）。
    _empty_state_label_widget = None
    _empty_state_currently_shown = False
    # 2026-08-24 新增：這個 Label 過去完全沒有存進任何全域變數，只能靠
    # clear_list() 的無差別 destroy 迴圈「間接」清掉——如果資料成功載入後
    # render 走的是 _try_incremental_diff_update() 增量路徑（完全不呼叫
    # clear_list()），這個 Label 會變成孤兒 widget，永久留在畫面上，跟
    # 正常顯示的 row 並存（真機證實過的真實 bug）。這裡存進
    # _loading_label_widget，交給 _hide_loading_state() 正式管理。
    _loading_label_widget = tk.Label(list_frame, text='Loading...', bg=BACKGROUND_COLOR, fg=TEXT_SECONDARY_COLOR)
    _loading_label_widget.pack(pady=20)


def _hide_loading_state():
    """清除 show_loading_state() 建立的 Loading 佔位標籤，是唯一負責清除
    它的地方——不再依賴 clear_list() 的無差別 destroy 迴圈當唯一清理方式
    （那條路徑只覆蓋「這次 render 走完整 rebuild」這一種情況，見
    _loading_label_widget 宣告處的說明）。呼叫端（on_recordings_loaded()/
    on_refresh_error()）都已經先確認過 refresh_token 是目前最新的那一次
    才會呼叫這裡，這裡不需要重複判斷 token，只需要對「目前有沒有顯示中」
    這件事本身是 idempotent 的：本來就沒有顯示（None）也安全，呼叫多次
    也安全。"""
    global _loading_label_widget
    if _loading_label_widget is not None:
        _loading_label_widget.destroy()
        _loading_label_widget = None


# ============================================================
# 2026-08-20 使用者回報真機同一次 171 rows render 中，fileid=76/75 各出現
# 兩次 'Row created'，要求追查 duplicate fileid 究竟第一次出現在哪一層
# （Raw recordings -> Grouped Gallery items -> Rendered rows）。這三支
# 診斷函式純粹印 log，不影響任何既有行為/回傳值，這輪不修改 item_key
# 設計本身。
# ============================================================

def _format_recording_item_identity(item):
    """給下面兩個 duplicate 診斷函式共用：印出一筆 Recording 完整識別資訊，
    對照使用者要求的欄位（fileid/groupid/type_of_recording/media_format/
    srcUri/trigger_time）。"""
    type_name = audi_pb2.TypeOfRecording.Name(item.type_of_recording)
    media_format_name = audi_pb2.Recording.MediaFormat.Name(item.media_format)
    trigger_time = item.interval.start_time.ToDatetime()
    return (f'fileid={item.fileid} groupid={item.groupid} sequenceid={item.sequenceid} '
            f'type_of_recording={type_name} media_format={media_format_name} '
            f'srcUri={item.srcUri!r} trigger_time={trigger_time}')


def _log_duplicate_fileids_in_raw_recordings(recordings, source_label):
    """Layer 1 診斷：檢查原始 /dashcam/recordings 清單本身（單一次 GET
    的原始結果，沒有經過任何分組/排序）有沒有重複的 fileid。找到的話印出
    每一組 duplicate 全部欄位，方便對照是不是 DVR/韌體那一端本來就回傳
    了重複的 fileid。"""
    fileid_to_items = {}
    for item in recordings:
        fileid_to_items.setdefault(item.fileid, []).append(item)
    duplicates = {fileid: items for fileid, items in fileid_to_items.items() if len(items) > 1}
    if duplicates:
        print(f'[Gallery Duplicate Diag] Layer 1 (Raw recordings, source={source_label}): '
              f'發現 {len(duplicates)} 組 duplicate fileid（共 {len(recordings)} 筆原始資料）！')
        for fileid, items in duplicates.items():
            print(f'[Gallery Duplicate Diag] Layer 1 duplicate fileid={fileid}，共 {len(items)} 筆:')
            for item in items:
                print(f'[Gallery Duplicate Diag]   {_format_recording_item_identity(item)}')
    else:
        print(f'[Gallery Duplicate Diag] Layer 1 (Raw recordings, source={source_label}): '
              f'沒有 duplicate fileid（共 {len(recordings)} 筆原始資料）')


def _log_duplicate_representative_fileids_in_groups(groups, source_label):
    """Layer 2 診斷：檢查 api.group_recordings_for_gallery() 分組完成後，
    每個 group 的代表項目（api.get_group_representative()）的 fileid
    有沒有重複——如果 Layer 1 沒有 duplicate，但這裡有，代表是分組邏輯本身
    的問題；如果 Layer 1 已經有 duplicate，這裡的 duplicate 通常就是同一組
    fileid 的自然延續（分組不會消除，也不應該消除，重複的 raw 資料）。"""
    fileid_to_groups = {}
    for group_items in groups:
        representative_item = api.get_group_representative(group_items)
        fileid_to_groups.setdefault(representative_item.fileid, []).append(group_items)
    duplicates = {fileid: group_list for fileid, group_list in fileid_to_groups.items() if len(group_list) > 1}
    if duplicates:
        print(f'[Gallery Duplicate Diag] Layer 2 (Grouped Gallery items, source={source_label}): '
              f'發現 {len(duplicates)} 組 duplicate representative fileid（共 {len(groups)} 個 group）！')
        for fileid, group_list in duplicates.items():
            print(f'[Gallery Duplicate Diag] Layer 2 duplicate representative fileid={fileid}，'
                  f'共 {len(group_list)} 個 group（每個 group 印代表項目 + 該 group 內全部段落數）:')
            for group_items in group_list:
                representative_item = api.get_group_representative(group_items)
                print(f'[Gallery Duplicate Diag]   代表項目: {_format_recording_item_identity(representative_item)}, '
                      f'該 group 內共 {len(group_items)} 段')
    else:
        print(f'[Gallery Duplicate Diag] Layer 2 (Grouped Gallery items, source={source_label}): '
              f'沒有 duplicate representative fileid（共 {len(groups)} 個 group）')


def load_recordings(refresh_token):
    """背景執行緒：GET /dashcam/recordings，分組，不下載縮圖

    2026-08-14 使用者確認規則：真機量測過 Thumbnail download 佔了 Gallery
    Refresh 94.9% 的時間，改成 Lazy Loading——這裡不再一次下載全部縮圖，
    分組/排序/完成度過濾完就直接交給 on_recordings_loaded() 立刻建立
    Gallery rows，縮圖下載交給可視範圍檢查（queue_visible_thumbnail_downloads()）
    + 固定 THUMBNAIL_WORKER_COUNT 個背景 worker（_thumbnail_download_worker()）
    處理，只下載使用者當下看得到的那幾筆。暫時保留 [Gallery Perf] 分段
    耗時 Log，量測完後要移除。

    2026-08-20 使用者要求針對 Gallery 載入速度做完整 Performance
    Profiling（實測 84 筆約需 6 秒，未來 SD Card 可能到數百~4000 筆，
    這次只加 Log 量測，不做任何架構調整/優化）：
    - 計時全部改用 time.perf_counter()（單調時鐘，量測耗時用這個比
      time() 準，不受系統時間被調整影響）；_gallery_render_diag_log()/
      現有以 time() 為主的 log 是拿來對照真實時鐘/其他裝置 log 用的，
      跟這次的耗時量測是兩件事，不動它。
    - 每個階段除了耗時，也印出「相對於這次 refresh 開始的偏移秒數」
      （start=+X.XXXs -> end=+X.XXXs），方便直接看出時間軸，不用自己
      加總前面幾行。
    - 把這次量到的 recordings_count/各階段耗時包成 perf_stage_durations
      往下傳給 on_recordings_loaded() -> render_visible_list()，最後
      在 Thumbnail 前 20 張下載完成時，統一印出一份完整的
      「Gallery Load Summary」（見 _print_gallery_load_summary()）。"""
    _gallery_render_diag_log('load_recordings', f'開始（背景執行緒），這次的 refresh_token={refresh_token}')
    try:
        t_total_start = perf_counter()

        t0 = perf_counter()
        recordings = api.get_recordings()
        t1 = perf_counter()
        recordings_count = len(recordings)
        get_duration = t1 - t0
        print(f'[Gallery Perf] 1. GET /dashcam/recordings: start=+{t0 - t_total_start:.3f}s, '
              f'end=+{t1 - t_total_start:.3f}s, duration={get_duration:.3f}s, '
              f'raw_recordings_count={recordings_count}')
        _log_duplicate_fileids_in_raw_recordings(recordings, source_label='load_recordings (fresh GET)')

        # 2026-08-14 使用者確認規則：Gallery 排序改成依 Trigger Time（每筆
        # 資料最初被觸發/開始產生的時間）由新到舊，不能用 groupid 大小代替
        # ——group_recordings_by_groupid() 只依 groupid 排序，不是時間，
        # 而且照片可能共用/重複 groupid，直接用它分組會誤合併照片。改用
        # api.group_recordings_for_gallery()：影片還是靠既有的 groupid
        # 分組規則（多 sequence 合併成一個 Video Item），照片每一張都是
        # 獨立項目，最後全部依 Trigger Time 排序。（階段 2/3 的耗時 Log
        # 印在 api.group_recordings_for_gallery() 內部，該函式內部改用
        # perf_counter() 之後，順便把這兩段耗時記在
        # api._last_gallery_grouping_perf 這個模組層級 dict，這裡讀出來
        # 組進 Summary，不用改 group_recordings_for_gallery() 的回傳值
        # ——它還有其他呼叫端（screen_player.py 等），改回傳值要跟著改全部
        # 呼叫端，超出這次「只量測」的範圍。）
        groups = api.group_recordings_for_gallery(recordings)
        t2 = perf_counter()
        group_duration = api._last_gallery_grouping_perf['group_duration']
        sort_duration = api._last_gallery_grouping_perf['sort_duration']
        _log_duplicate_representative_fileids_in_groups(groups, source_label='load_recordings (fresh GET)')

        # 還在錄製中、還沒真的存檔完的 group 不顯示在 Gallery：要整組
        # （所有段落）都 FINISHED 才算完成，不是只看代表項目——多段影片
        # 常見「前面幾段完成、最後一段還在錄」的情況，只看代表項目會誤判
        # 整組已經完成，之後點進去播放會少最後一段。判斷邏輯集中在
        # api.is_group_finished()，這裡不自己解析 protobuf enum。
        finished_groups = [group_items for group_items in groups if api.is_group_finished(group_items)]
        t3 = perf_counter()
        completion_filter_duration = t3 - t2
        print(f'[Gallery Perf] (完成度過濾，未列在 7 個階段內) is_group_finished 過濾: {completion_filter_duration:.3f}s, '
              f'finished_group_count={len(finished_groups)}')

        # 2026-08-14 使用者確認規則：這裡不再下載任何縮圖（原本的階段 5），
        # 縮圖改成 Lazy Loading，實際下載發生在 render_visible_list() 建立
        # rows 之後、由 queue_visible_thumbnail_downloads() 依可視範圍觸發，
        # 耗時另外印在那邊的 [Gallery Perf] Log。
        if GALLERY_RENDER_DIAGNOSTIC_LOGGING:
            print(f'[Gallery Render Diag] t={time():.3f} stage=load_recordings caller=(background thread) '
                  f'排入 .after(0, on_recordings_loaded)，這次的 refresh_token={refresh_token}, '
                  f'finished_group_count={len(finished_groups)}')

        # 2026-08-20 新增：這次 GET/分組/排序/完成度過濾的耗時資訊，
        # 純粹給最後組 Gallery Load Summary 用，跟原本的 finished_groups/
        # refresh_token 這條「決定要不要重畫」的既有邏輯完全無關，
        # on_recordings_loaded() 只是原封不動再往下傳給
        # render_visible_list()。
        perf_stage_durations = {
            'recordings_count': recordings_count,
            'get_duration': get_duration,
            'group_duration': group_duration,
            'sort_duration': sort_duration,
            'completion_filter_duration': completion_filter_duration,
        }
        list_frame.after(0, lambda: on_recordings_loaded(finished_groups, refresh_token, t_total_start, perf_stage_durations))
    except Exception as error:
        list_frame.after(0, lambda captured_error=error: on_refresh_error(captured_error, refresh_token))


def download_thumbnail_safely(recording_item, item_key):
    """單筆縮圖下載失敗不要讓整個清單掛掉，失敗回傳 None，呼叫端
    on_thumbnail_downloaded() 會決定要重試還是判定成正式的 No Thumbnail。
    Gallery List 一次要顯示一整串 Item，用小解析度（THUMBNAIL_RESOLUTION_GALLERY），
    不要跟 Recording Page 一樣下載高解析度，避免不必要的下載量跟 Loading 時間。
    Photo 沒有韌體預先產生好的小圖，下載的是原始全尺寸照片再本地縮小
    （見 api.download_photo_preview_image()），比 Video 縮圖慢很多，這裡
    量測耗時方便排查是不是逾時造成的失敗。

    item_key 由呼叫端傳入（跟 _thumbnail_download_queue 裡的 item_key 同一份，
    即 api.compute_item_key() 算出來的值），只是給診斷 log 用，不要再自己用
    recording_item.fileid 另外算一份——fileid 已知不保證唯一，自己另算會讓
    log 顯示的識別碼跟其他地方對不上。"""
    start_time = time()
    try:
        thumbnail_bytes = api.download_preview_image(recording_item, resolution=api.THUMBNAIL_RESOLUTION_GALLERY)
        elapsed = time() - start_time
        if thumbnail_bytes is None:
            _thumbnail_diag_log(item_key, f'download_preview_image() 回傳 None（{elapsed:.2f}s）')
        else:
            _thumbnail_diag_log(item_key, f'download_preview_image() 成功，{len(thumbnail_bytes)} bytes（{elapsed:.2f}s）')
        return thumbnail_bytes
    except Exception as error:
        elapsed = time() - start_time
        print(f'[Gallery] Thumbnail download failed (item_key={item_key}): {error}')
        _thumbnail_diag_log(item_key, f'download_preview_image() 例外：{error}（{elapsed:.2f}s）')
        return None


def _compute_single_item_signature(group_items):
    """單一 group 的 signature tuple，抽出來給
    _compute_gallery_display_signature()（整批可見清單）跟 Filter
    Hide/Show Reuse 需要的「單一 item 隱藏期間內容有沒有悄悄變」判斷
    共用同一份定義，不要各自维护一份重複邏輯。

    2026-08-20 Codex review 發現：這裡原本沒有包含 api.compute_item_key()
    的結果——如果兩筆資料剛好 fileid/trigger_time/category/is_favorite/
    段落數都相同，但 item_key（Photo 用的 srcUri）不同，signature 會被
    誤判成「沒有變化」，data_changed=False 就會跳過 render_visible_list()，
    但 prune_stale_thumbnail_cache()/prune_stale_row_widgets() 是各自
    獨立、不受 data_changed 影響照樣執行，會把舊 item_key 的 widget/cache
    當成「已經不存在」清掉，變成「widget 被銷毀了，但畫面沒有觸發重畫」的
    不同步狀態。這裡把 item_key 加進 signature 最後一個欄位（不是插進
    中間——apply_star_visual_update() 有用固定索引 [3] 讀 is_favorite，
    加在最後才不會讓既有的索引位置跟著偏移），item_key 改變就一定會讓
    signature 跟著改變，data_changed 就能正確判定成 True。"""
    representative_item = api.get_group_representative(group_items)
    return (
        representative_item.fileid,
        api.get_group_trigger_time(group_items),
        api.get_category(representative_item),
        representative_item.is_favorite,
        len(group_items),
        api.compute_item_key(group_items),
    )


def _compute_gallery_display_signature(groups):
    """給「fresh data 是不是真的跟目前畫面不一樣」判斷用的最小 signature，
    不是逐欄位 deep compare——只挑會影響 Gallery List 排序/顯示的欄位：
    代表項目 fileid（新增/刪除一定會反映在這裡）、Trigger Time（排序依據，
    見 api.group_recordings_for_gallery()）、類型（icon 會不會換）、
    is_favorite（★/☆ 會不會換）、這個 group 的段落數（Duration 文字
    會不會變）。回傳一個 list，元素順序就是目前顯示順序本身，這樣「內容
    沒變但順序換了」也會被判定為『有變化』，不用另外比對順序。"""
    return [_compute_single_item_signature(group_items) for group_items in groups]


def on_recordings_loaded(finished_groups, refresh_token, perf_total_start=None, perf_stage_durations=None):
    """2026-08-14 使用者確認規則：改成 Lazy Loading 之後，這裡不再帶著
    縮圖資料，只帶分組完成的 finished_groups——current_thumbnails_by_item_key
    不再於這裡整份重建，而是保留跨 Refresh 的 cache（同一個 item_key 已經
    下載過就不重新下載），只把 DVR 最新清單裡已經不存在的 item 對應的
    cache 清掉（prune_stale_thumbnail_cache()，Delete 成功後也是靠這個
    機制一起清乾淨，不用另外寫 Delete 專用的清除邏輯）。

    2026-08-19 使用者要求「cache-first + background refresh」：background
    GET 回來的 fresh data 如果跟目前畫面顯示的內容（用
    _compute_gallery_display_signature() 比對）完全一樣，就不要
    clear_list()/render_visible_list()——這是造成 Double Render（使用者
    看到「List -> Loading/清空 -> 同一份 List」跳動）的直接原因。只有真的
    偵測到新增/刪除/Favorite 或排序相關欄位變化才重畫；沒變化的話目前的
    捲動位置、Row widget、thumbnail cache 全部原封不動保留，只更新
    count/文字這類不需要重畫 List 就能同步的狀態。"""
    global is_refreshing, current_groups, has_loaded_recordings_once, _exit_selection_mode_after_next_refresh
    global _diag_last_deleted_fileids, _diag_last_deleted_groupid, _diag_last_swiped_row_container
    global _current_groups_changed_without_render
    is_stale = refresh_token != current_refresh_token
    print(f'[Gallery Render Diag] t={time():.3f} stage=on_recordings_loaded caller=(list_frame.after callback) '
          f'incoming_refresh_token={refresh_token} current_refresh_token={current_refresh_token} '
          f'is_stale={is_stale} finished_group_count={len(finished_groups)}')
    if is_stale:
        # 過期的 refresh 結果（使用者可能已經又觸發了新的一次），不要套用。
        # 2026-08-24 使用者明確要求：這裡不能呼叫 _hide_loading_state()——
        # 如果目前畫面上顯示的 Loading 其實是「較新那次 refresh」自己
        # start_refresh() 建立的，這裡把它清掉會變成「新 refresh 還沒有
        # 結果，Loading 卻被過期的舊結果誤清掉」，畫面反而變成錯誤地顯示
        # 空清單/舊內容。Loading 的去留只交給真正贏過這次 token 比對的
        # 那次呼叫（也就是這個函式後面沒有 return 的那條路徑）決定。
        return

    # 2026-08-24 新增：這次 refresh_token 確認是目前最新的（上面已經通過
    # is_stale 檢查），不管接下來要走 full rebuild、incremental diff、還是
    # data_changed=False 都算是「這次 refresh 有了確定的結果」，Loading
    # 佔位標籤（如果有顯示的話）都要在這裡清掉——不再依賴 render_visible_
    # list() 的 clear_list() 順便 destroy 當唯一清理方式（那條路徑不會覆蓋
    # _try_incremental_diff_update() 增量成功、或 data_changed=False 完全
    # 不重畫這兩種情況，見 _loading_label_widget 宣告處的說明）。
    _hide_loading_state()

    previous_signature = _compute_gallery_display_signature(current_groups)
    new_signature = _compute_gallery_display_signature(finished_groups)
    data_changed = previous_signature != new_signature
    _gallery_render_diag_log('on_recordings_loaded', f'data_changed={data_changed}')

    diag_tracking_a_delete = GALLERY_DELETE_REFRESH_DIAGNOSTIC_LOGGING and _diag_last_deleted_fileids is not None
    if diag_tracking_a_delete:
        fresh_fileids_flat = {item.fileid for group in finished_groups for item in group}
        deleted_group_still_exists = any(fileid in fresh_fileids_flat for fileid in _diag_last_deleted_fileids)
        print('[Gallery Refresh After Delete]')
        print(f'  old_group_count={len(current_groups)}')
        print(f'  fresh_group_count={len(finished_groups)}')
        print(f'  old_signature={previous_signature}')
        print(f'  fresh_signature={new_signature}')
        print(f'  data_changed={data_changed}')
        print(f'  deleted_group_still_exists={deleted_group_still_exists} '
              f'(groupid={_diag_last_deleted_groupid}, fileids={_diag_last_deleted_fileids})')

    current_groups = finished_groups
    prune_stale_thumbnail_cache(current_groups)
    prune_stale_row_widgets(current_groups)
    has_loaded_recordings_once = True  # ensure_loaded() 之後看到這個 True 就不會再重新打 API

    # 2026-08-19 修正：reset_batch_action_controls()（離開 Selection Mode、
    # 清空勾選）不能無條件每次 refresh 完都執行——一般切頁重新整理
    # （Gallery -> Recording -> Gallery 觸發的 force_refresh()）也會走到
    # 這裡，之前無條件呼叫會把使用者還在進行中的 Selection Mode 意外清掉。
    # 只有真的是 Batch Action 完成觸發的這次 refresh（on_batch_action_done()
    # 呼叫 start_refresh() 前會設這個旗標）才要退出 Selection Mode，一般
    # 重新整理不會誤觸發。
    is_batch_mode_before_reset = is_batch_mode
    if _exit_selection_mode_after_next_refresh:
        _exit_selection_mode_after_next_refresh = False
        reset_batch_action_controls()
    is_batch_mode_changed_by_reset = is_batch_mode_before_reset != is_batch_mode

    # 2026-08-20 使用者回報 Issue 2 測到的真實情況：Batch Delete 全部失敗
    # 時（例如 DVR 斷線/逾時），recordings 內容完全沒變（data_changed=
    # False），但上面剛把 is_batch_mode 從 True 重設回 False——如果這裡只
    # 看 data_changed 決定要不要呼叫 render_visible_list()，会导致
    # is_batch_mode 內部狀態已經是 False，畫面上的 Checkbox 卻完全沒被拿掉
    # （render_visible_list() 根本沒被呼叫），使用者看到「已經離開
    # Selection Mode（右上角變回 Filter icon），但每一列還留著 Checkbox」
    # 這種不一致畫面。現在 is_batch_mode 改變也走 Incremental Diff（不會
    # 因為這樣就整頁 destroy/rebuild，見 _try_incremental_diff_update()），
    # 這裡把「is_batch_mode 是不是被這次 reset 動過」也納入要不要呼叫
    # render_visible_list() 的條件，成本很低（is_batch_mode-only 的情況
    # 只會走 checkbox 同步這一小段，不是完整 rebuild）。
    #
    # 2026-08-24 新增：_current_groups_changed_without_render 是 True 代表
    # 上面剛被指派的 current_groups = finished_groups 之前，其實已經被
    # report_gallery_item_count_from_recordings() 側漏更新過一次、但沒有
    # render 過（換 SD 卡最常見）。這種情況下這次 fresh GET 的 finished_
    # groups 很可能跟（已經被側漏更新的）current_groups 完全一樣，
    # data_changed 會是 False，但畫面上實際顯示的還是側漏更新之前的舊
    # 內容，這裡一定要當成「不能跳過」的條件之一，強制走一次真的 render，
    # 才能把畫面追上。
    if data_changed or is_batch_mode_changed_by_reset or _current_groups_changed_without_render:
        # perf_total_start/perf_stage_durations 只有 load_recordings() 完整
        # refresh 流程才會給值，其他呼叫 render_visible_list() 的地方（切
        # Filter、批次模式等）不會給，不會印出這次量測用的 [Gallery Perf] Log。
        render_visible_list(perf_total_start=perf_total_start, perf_stage_durations=perf_stage_durations)
    else:
        # 資料沒有實質變化：不重畫 List，但 Filter/View Mode 算出來的
        # 「N items」文字還是要跟目前 current_groups 保持同步（可能筆數
        # 剛好一樣但這裡还是重算一次，成本很低，不需要重畫整個 List
        # 才能更新這一行文字）。
        update_selection_status_text()

    if diag_tracking_a_delete:
        print(f'  render_called={data_changed}')
        # 這次追蹤已經印完，清掉，避免之後不相關的 refresh 又印一次舊資訊
        _diag_last_deleted_fileids = None
        _diag_last_deleted_groupid = None
        _diag_last_swiped_row_container = None

    is_refreshing = False
    set_controls_busy(False)

    # 放在狀態都復原之後才回報，如果回呼裡觸發自動切換畫面
    # （Favorite 數量歸零時自動回 Gallery、Gallery 數量歸零時自動回
    # Recording），force_refresh() 才不會被還沒重置的 is_refreshing 卡住。
    # 2026-08-19 使用者要求：Gallery 數量歸零時要回 Recording，這個判斷要
    # 先做——如果 Gallery 本身都要隱藏了，先讓 Favorite 那邊把使用者導去
    # 「隱藏後的 Gallery」畫面沒有意義，順序反過來才會一次到位回 Recording，
    # 不會先跳 Gallery 畫面再跳 Recording 畫面。
    notify_gallery_item_count_changed()
    notify_favorite_state_changed()


def notify_gallery_item_count_changed():
    """每次 refresh 完都呼叫，回報目前 Gallery 最終 item 數（分組/篩選
    completed 之後的數字，不是 raw get_recordings() 筆數），讓
    DA005ControlPanel.pyw 決定 Gallery Tab 要不要顯示。"""
    if on_gallery_item_count_changed_callback:
        on_gallery_item_count_changed_callback(len(current_groups))


def compute_finished_gallery_item_count(recordings):
    """純計算，不會動到 current_groups、也不會觸發任何 Row 重畫——
    screen_recording.py 完成拍照/停止 Scenic/Event 結束、或重新連線後，
    只是要知道『現在 Gallery 最終算出來的 item 數』來決定 Gallery Tab
    要不要顯示，不需要真的觸發 Gallery 自己整個 Refresh/Render 流程（那些
    要等使用者真的切過去 Gallery 才需要做）。跟 load_recordings() 用同一套
    group_recordings_for_gallery() + is_group_finished() 篩選規則，不是
    另外發明一套算法。"""
    groups = api.group_recordings_for_gallery(recordings)
    finished_groups = [group_items for group_items in groups if api.is_group_finished(group_items)]
    return len(finished_groups)


def report_gallery_item_count_from_recordings(recordings):
    """給 screen_recording.py 呼叫：Photo 拍照成功／Scenic 錄影結束／
    Event 錄影結束／例行的 Recording 分頁刷新，手上已經有一份剛 GET 到的
    完整 recordings 清單時，直接拿來『同時』更新 Gallery Tab 顯示狀態
    『跟』Gallery 自己的 cache（current_groups），不需要再多打一次 API。

    2026-08-20 使用者回報：Recording 產生新媒體（Photo/Scenic/Event 完成）
    後再進 Gallery，會先看到上一次的舊 List、才跳成含新媒體的最新
    List——Root Cause 是 Gallery 的 cache-first 架構本來要等「使用者真的
    切過去 Gallery」那一刻的背景 GET 完成，才第一次發現資料其實已經變
    了。Photo/Scenic/Event 完成當下其實已經有一份剛 GET 到的 fresh
    recordings（見 screen_recording.py 的
    _report_gallery_item_count_after_completion()，這支函式本來就是這樣
    呼叫過來的），改成在這裡直接拿來更新 current_groups，讓 Gallery
    cache 在「資料來源事件發生的當下」就已經是最新的，不用等使用者進
    Gallery 才第一次同步——下次真的打開 Gallery 時，cache 本身就是最新
    的，只會 render 一次，不會出現「舊 List -> 跳一次 -> 新 List」。

    只更新 data/cache（current_groups + thumbnail cache 修剪），刻意不
    呼叫 render_visible_list()、也不會重建/顯示任何 Gallery widget——
    呼叫這個函式的當下，Gallery 通常根本不是使用者目前看得到的畫面，
    不能在背景偷偷重畫使用者看不到的東西。Gallery 下次真的被打開時
    （show_gallery_view() + force_refresh()/ensure_loaded()）才會用這份
    已經是最新的 current_groups 正常 render 一次。

    分組/完成度過濾規則跟 compute_finished_gallery_item_count()/
    load_recordings() 完全一致（都是 api.group_recordings_for_gallery()
    + api.is_group_finished()），不是另外發明一套算法——這裡需要的是
    分組後的 groups 本身（拿來更新 current_groups），不只是數量，所以
    不透過 compute_finished_gallery_item_count()（那支函式只回傳數量，
    繼續保留給只需要數量、不需要更新 cache 的呼叫端用）。"""
    global current_groups, has_loaded_recordings_once, _current_groups_changed_without_render
    _log_duplicate_fileids_in_raw_recordings(recordings, source_label='report_gallery_item_count_from_recordings (side-channel)')
    groups = api.group_recordings_for_gallery(recordings)
    _log_duplicate_representative_fileids_in_groups(
        groups, source_label='report_gallery_item_count_from_recordings (side-channel)')
    finished_groups = [group_items for group_items in groups if api.is_group_finished(group_items)]

    current_groups = finished_groups
    prune_stale_thumbnail_cache(current_groups)
    prune_stale_row_widgets(current_groups)
    has_loaded_recordings_once = True
    # 2026-08-24 新增：這裡改了 current_groups 但刻意不 render（理由見上面
    # docstring），標記旗標，強制下一次進 Gallery/Favorite（不管走
    # _can_skip_rebuild_for_current_view() 的 cache 路徑，還是 fresh GET
    # 的 on_recordings_loaded() 路徑）都至少真的 render 一次，不會被誤判
    # 成「沒有變化」而跳過——見該旗標宣告處的完整說明。
    _current_groups_changed_without_render = True

    # 2026-10-06 修正：原本這裡只回報 Gallery 數量、沒有回報 Favorite 數量，
    # 導致 App 啟動時（Recording 頁面刷新走的就是這條路徑）Favorite Tab 不會
    # 出現，要等使用者點一次 Gallery、Gallery 自己完整 refresh 完
    # （on_recordings_loaded() 最後的 notify_favorite_state_changed()）才會
    # 顯示。current_groups 上面已經用剛 GET 到的資料更新好了，這裡直接用
    # 同一份資料回報 Favorite 數量。順序跟 on_recordings_loaded() 一致：
    # 先回報 Gallery、再回報 Favorite（理由見該處說明）。
    if on_gallery_item_count_changed_callback:
        on_gallery_item_count_changed_callback(len(current_groups))
    notify_favorite_state_changed()


def prune_stale_thumbnail_cache(groups):
    """2026-08-14 使用者確認規則：Thumbnail Cache 要跨 Refresh 保留，同一個
    item_key 已經下載過就不重新下載；但 Refresh 完後如果 DVR 最新清單裡
    已經不存在某個 item_key（例如 Delete 成功後、或這次 GET 剛好沒回傳到），
    要順便把它的 cache／排隊狀態／pending row 參照都清掉，避免 cache 無限
    成長，也避免殘留無意義的參照。還存在的 item_key 完全不動，不會被
    重新下載。"""
    current_item_keys = {api.compute_item_key(group) for group in groups}
    stale_item_keys = [key for key in current_thumbnails_by_item_key if key not in current_item_keys]
    for key in stale_item_keys:
        current_thumbnails_by_item_key.pop(key, None)
        requested_item_keys.discard(key)
        pending_thumbnail_labels.pop(key, None)

    # 2026-08-19 新增：還在重試中的失敗次數也要一起清掉——這個 item_key
    # 這時候還不在 current_thumbnails_by_item_key 裡（重試期間刻意不寫入，
    # 見 on_thumbnail_downloaded()），上面那段清不到它，這裡用同一份
    # current_item_keys 單獨再清一次，避免這個 dict 對已經不存在的項目
    # 無限累積。
    stale_retry_keys = [key for key in thumbnail_retry_counts if key not in current_item_keys]
    for key in stale_retry_keys:
        thumbnail_retry_counts.pop(key, None)


def prune_stale_row_widgets(groups):
    """2026-08-20 使用者要求 Filter Hide/Show Reuse + Batch Delete 不
    full rebuild 之後：_row_widgets_by_item_key 現在會保留「item_key 還在
    current_groups 裡、只是目前 Filter/View Mode 不顯示」的 hidden row
    （只 pack_forget()，不 destroy，見該字典宣告處的說明），跟
    prune_stale_thumbnail_cache() 處理的縮圖快取是分開的另一份狀態，需要
    分開清理，不能假設「thumbnail cache 清過了，row widget 就自動沒事」。

    這裡在 current_groups 真的變動（GET 到新資料、或 Recording 側漏更新）
    當下呼叫，找出 _row_widgets_by_item_key 裡「item_key 已經不在最新
    current_groups 裡」的那些——不管它們當下是可見還是隱藏，都代表對應
    的 recording 真的被刪除/消失了（不是 Filter 造成的暫時不可見），把
    row widget 徹底 destroy 掉，並清掉所有關聯的本地狀態，避免變成
    stale/orphan widget。SD Card Format/Remove 之後尤其重要——大量
    recording 一次消失，如果使用者當時剛好在某個 Filter，很多消失的項目
    當下是「隱藏」狀態，不會被 render_visible_list() 的可見清單 diff
    自然清到，一定要靠這裡主動找出來清掉。

    這支函式必須在 render_visible_list()/_try_incremental_diff_update()
    之前呼叫（呼叫端是 on_recordings_loaded()/
    report_gallery_item_count_from_recordings()，緊接在
    prune_stale_thumbnail_cache() 後面）。

    這裡刻意不去動 _current_row_order/_rendered_signature（那是「上一次
    render 完成當下」的快照，只應該在 render_visible_list()/
    _try_incremental_diff_update() 真的執行完一次 render 時才更新——
    _can_skip_rebuild_for_current_view() 靠這兩者判斷要不要跳過整個
    render，如果這裡搶先幫它們「善意地」濾掉被刪除的 key，可能造成
    _rendered_signature 剛好變得跟目前 get_visible_groups() 的新 signature
    一樣，讓 show_gallery_view() 誤判成「內容沒變、可以跳過」，導致該顯示
    的 Empty State 文字/該重畫的內容完全沒有 render 出來——這是實測抓到
    的真實 bug，不是理論假設。改成完全不動這兩者，讓
    _try_incremental_diff_update() 的 hide 步驟對「這個 key 的 widget
    是不是已經被這裡清掉」自己做防禦性檢查（見該函式），_current_row_order
    本身只會在下一次真的 render 完成時才被覆蓋成最新內容，不會因為這裡
    清過 widget 就提早跟著變。

    真正可見中的項目被刪除（例如 Batch Delete 目前勾選的那筆）也會被這裡
    處理掉，不是只處理 hidden 的——不管刪除當下這個 item 是可見還是隱藏，
    只要它已經不在 current_groups 裡，都用同一套清理邏輯，不分兩套；
    _try_incremental_diff_update() 的 old_keys/new_keys diff 本來就會把
    它算進 removed_keys（現在可見清單裡沒有它，這件事本身不受這裡影響），
    只是它的 widget 已經被這裡提前清掉了，diff 的 hide 步驟看到後跳過
    即可，不需要重複 destroy。"""
    current_item_keys = {api.compute_item_key(group) for group in groups}
    stale_item_keys = [key for key in _row_widgets_by_item_key if key not in current_item_keys]
    if not stale_item_keys:
        return

    for key in stale_item_keys:
        row_widget = _row_widgets_by_item_key.pop(key)
        row_widget.destroy()
        checkbox_widgets_by_item_key.pop(key, None)
        pending_thumbnail_labels.pop(key, None)
        _row_signature_by_item_key.pop(key, None)
        checked_item_keys.discard(key)
        requested_item_keys.discard(key)

    print(f'[Gallery Perf] prune_stale_row_widgets(): 清理 {len(stale_item_keys)} 筆已刪除項目的 row widget（含目前隱藏中的）')


def on_refresh_error(error, refresh_token):
    """2026-08-19 修正：原本 messagebox.showerror() 寫在 token 檢查之外，
    不管過不過期都會跳出來——DVR 斷線時 cancel_pending_work_for_disconnect()
    會 bump current_refresh_token 讓這次 refresh 過期，就是為了不要在使用者
    已經被導去 Recording 看 Disconnected Preview 之後，還跳出一個多餘的
    Gallery refresh 失敗訊息，這裡把它一起移進 token 檢查裡面。"""
    global is_refreshing
    if refresh_token == current_refresh_token:
        is_refreshing = False
        set_controls_busy(False)
        # 2026-08-24 新增：這次失敗如果是在 show_loading_state() 顯示過
        # Loading 佔位標籤之後發生的，過去完全沒有任何程式碼清掉它——
        # clear_list() 不會被呼叫（沒有成功拿到資料可以 render），變成
        # 孤兒 widget 永久卡在畫面上。跟 on_recordings_loaded() 一樣，這裡
        # 已經先確認過 refresh_token 是目前最新的才會執行到這裡，不會誤清
        # 掉「較新那次 refresh」自己的 Loading。
        _hide_loading_state()
        status_label.config(text='Failed to refresh Gallery.')
        messagebox.showerror('Connection Error', f'Failed to refresh Gallery.\n{error}')


def reset_batch_action_controls():
    """Refresh 完成後：清除所有 Checkbox 勾選、退出 Selection Mode、
    右上角恢復 Filter icon。"""
    global is_batch_mode
    checked_item_keys.clear()
    is_batch_mode = False
    update_top_bar_mode()


def cancel_pending_work_for_disconnect():
    """DVR 斷線時呼叫（不論目前是不是在 Gallery/Favorite 畫面）：取消
    Selection Mode（不執行任何待處理的 Favorite/Unfavorite/Delete）、停止
    Thumbnail Lazy Loading 的可視範圍檢查、讓還在背景跑的 refresh worker
    失效（bump 既有的 current_refresh_token，不另外發明機制）。跟正常切換
    畫面時的 on_gallery_screen_hidden() 不一樣的地方是這裡會強制退出
    Selection Mode——正常切換畫面不會這樣做，斷線這種例外情況才需要，
    不能讓使用者斷線後回來時還卡在一個待處理的批次操作狀態。"""
    global current_refresh_token, is_refreshing
    reset_batch_action_controls()
    on_gallery_screen_hidden()  # 沿用既有：停止可視範圍檢查迴圈
    current_refresh_token += 1  # 讓任何還在背景跑的舊 load_recordings()/on_recordings_loaded() 結果直接失效
    is_refreshing = False
    set_controls_busy(False)


def notify_favorite_state_changed():
    """每次 refresh 完都呼叫，回報目前 Favorite 總數（跨全部 group，不受
    目前 Filter/View Mode 影響），讓 DA005ControlPanel.pyw 決定 Favorite
    Tab 要不要顯示。"""
    if on_favorite_state_changed_callback:
        favorite_count = sum(1 for group in current_groups if api.get_group_representative(group).is_favorite)
        on_favorite_state_changed_callback(favorite_count)


# ============================================================
# 畫面繪製：每一列 = [Checkbox] + 縮圖 + 文字（Icon/類型/收藏星號/時間/長度）
# ============================================================

def clear_list():
    diag_tracking_a_delete = GALLERY_DELETE_REFRESH_DIAGNOSTIC_LOGGING and _diag_last_deleted_fileids is not None
    if diag_tracking_a_delete:
        print('[Gallery Refresh After Delete] clear_list_called=True')
        print(f'  row_widget_count_before={len(list_frame.winfo_children())}')

    # 2026-08-20 使用者要求確認：第二次進 Gallery（destroy 上一輪已經有
    # 真的 PhotoImage 的 rows，不是純文字佔位）是不是造成 Tk geometry/
    # layout/update 成本異常的原因之一。這裡把 destroy 迴圈本身跟
    # image_refs.clear() 分開計時，並記錄 destroy 之前 image_refs 裡還有
    # 多少張真正的縮圖（= 上一輪停留期間 Lazy Loading 已經下載完成、
    # 原地更新過的縮圖數量）——如果這個數字（existing_image_ref_count）
    # 在第二次明顯 > 0，而 destroy_duration 也明顯拉長，就能對應到「destroy
    # 帶圖片的 widget 比較貴」這個假設；如果 destroy_duration 跟第一次
    # 差不多、但後面 add_list_row() 的 cache-hit row 才是真正拉長的地方，
    # 就能排除 destroy 本身是主因。
    row_count_before_destroy = len(list_frame.winfo_children())
    existing_image_ref_count = len(image_refs)
    t_destroy_start = perf_counter()
    _gallery_render_diag_log('clear_list', f'destroy_children_count={row_count_before_destroy}')
    for widget in list_frame.winfo_children():
        widget.destroy()
    destroy_duration = perf_counter() - t_destroy_start

    t_image_refs_clear_start = perf_counter()
    image_refs.clear()
    image_refs_clear_duration = perf_counter() - t_image_refs_clear_start

    checkbox_widgets_by_item_key.clear()
    # 2026-08-14 Thumbnail Lazy Loading：舊的 row 全部被 destroy 了，殘留的
    # pending label 參照也要一起清掉（不清的話，等舊的下載完成回呼觸發
    # on_thumbnail_downloaded() 時會去更新一個已經不存在的 widget）。
    pending_thumbnail_labels.clear()
    # 2026-08-20 使用者要求 Gallery 增量更新：_row_widgets_by_item_key 是
    # item_key -> row_container 對照表，只有完整 rebuild（這裡）跟增量
    # 更新（_try_incremental_diff_update()）兩個地方會維護，完整 rebuild
    # 一定要在這裡清掉重建，不然殘留的參照會跟新一輪的 row widget 對不上
    # ——包含目前隱藏中（Filter 造成，pack_forget 但沒 destroy）的那些也
    # 一起清掉，完整 rebuild 是「保證正確」的 fallback，不用保留 hidden
    # row cache（list_frame.winfo_children() 本來就包含還沒 pack_forget
    # 過的隱藏 widget，上面的 destroy 迴圈已經連它們一起清掉了）。
    _row_widgets_by_item_key.clear()
    _row_signature_by_item_key.clear()
    _row_widgets_by_item_key_source.clear()  # 2026-08-20 duplicate fileid 診斷字典，同步清空

    # 2026-08-20 使用者要求把 Empty State 獨立管理之後：上面的 destroy
    # 迴圈如果剛好有一個 Empty State Label 存在（list_frame 的 child，
    # 一樣會被 winfo_children() 抓到、一起 destroy 掉），這裡的模組全域
    # 參照就變成指向一個已經被 destroy 的 widget——完整 rebuild 是
    # 「保證正確」的 fallback，這裡明確重設成 None/False，下次真的需要
    # 顯示 Empty State 時，_ensure_empty_state_label_exists() 才會正確
    # 重新建立一個新的，不會誤以為舊的還能用。
    global _empty_state_label_widget, _empty_state_currently_shown
    _empty_state_label_widget = None
    _empty_state_currently_shown = False

    # 2026-08-20 使用者要求追查 Favorite Page Unfavorite 效能問題：跟
    # _try_incremental_diff_update() 一樣，把這次算好的數字存起來給呼叫端
    # 組 [Favorite Perf] Summary 用，純粹讀取既有數字，不影響任何行為。
    global _last_clear_list_metrics
    _last_clear_list_metrics = {
        'destroyed_rows': row_count_before_destroy,
        'destroy_duration': destroy_duration,
    }

    print(f'[Gallery Perf] clear_list(): destroyed_rows={row_count_before_destroy}, '
          f'existing_image_ref_count_before_clear={existing_image_ref_count}, '
          f'destroy_duration={destroy_duration:.4f}s, image_refs_clear_duration={image_refs_clear_duration:.4f}s')

    if diag_tracking_a_delete:
        print(f'  row_widget_count_after={len(list_frame.winfo_children())}')
        active_swipe_row_exists = (_diag_last_swiped_row_container is not None
                                    and _diag_last_swiped_row_container.winfo_exists())
        print(f'  active_swipe_row_exists={active_swipe_row_exists}')


def get_visible_groups():
    """依目前的 View Mode（Gallery 全部 / Favorite 只留收藏）跟 Filter
    （All/Scenic/Event/Photo）算出目前應該顯示的 group，純畫面端運算，
    不會呼叫任何 DVR API。"""
    groups = current_groups
    if current_view_mode == VIEW_MODE_FAVORITE:
        groups = [group for group in groups if api.get_group_representative(group).is_favorite]
    if current_filter != FILTER_ALL:
        wanted_category = FILTER_TO_CATEGORY[current_filter]
        groups = [group for group in groups if api.get_category(api.get_group_representative(group)) == wanted_category]
    return groups


def get_empty_state_text():
    if current_view_mode == VIEW_MODE_FAVORITE:
        return 'No favorites yet.'
    if current_filter != FILTER_ALL:
        return 'No items match this filter.'
    return 'No data available.'


def _ensure_empty_state_label_exists():
    """只在第一次需要顯示 Empty State 時建立一次，之後（不管顯示/隱藏
    切換幾次）都重複使用同一個 widget，不會每次都新建一個新的 Label。
    跟 row 的 widget（_row_widgets_by_item_key）完全分開管理，這裡沒有
    item_key 的概念，_try_incremental_diff_update() 的 old/new key diff
    不會碰到它。"""
    global _empty_state_label_widget
    if _empty_state_label_widget is not None:
        return
    _empty_state_label_widget = tk.Label(list_frame, bg=BACKGROUND_COLOR, fg=TEXT_SECONDARY_COLOR)


def _show_empty_state_label():
    """顯示 Empty State：文字一律重新用 get_empty_state_text() 現算
    ——這支既有函式已經正確依 current_view_mode/current_filter 判斷該
    顯示哪一種文字（Gallery 真的 0 筆／Favorite 0 筆／其他 Filter 結果
    0 筆，三種文字不一樣），這裡不用另外判斷是哪一種原因造成的 0 筆，
    也不會把三者搞混。widget 只建立一次、之後重複使用；已經顯示中的話
    不重複呼叫 pack()（重複呼叫本身雖然安全，但沒必要）。"""
    global _empty_state_currently_shown
    _ensure_empty_state_label_exists()
    _empty_state_label_widget.config(text=get_empty_state_text())
    if not _empty_state_currently_shown:
        _empty_state_label_widget.pack(pady=20)
        _empty_state_currently_shown = True


def _hide_empty_state_label():
    """隱藏 Empty State：只 pack_forget()，不 destroy，之後再需要顯示時
    直接 reuse 同一個 widget。還沒建立過（_empty_state_label_widget 是
    None）或本來就沒顯示中，呼叫這裡是安全的 no-op。"""
    global _empty_state_currently_shown
    if _empty_state_label_widget is not None and _empty_state_currently_shown:
        _empty_state_label_widget.pack_forget()
    _empty_state_currently_shown = False


def _log_gallery_repack_diagnostics(stage, old_order, new_order, old_survivors, added_keys, removed_keys):
    """2026-09-17 使用者實機回報 Root Cause 排查用：只印資訊，不改任何
    行為/狀態。只在 _try_incremental_diff_update() 判斷 old_survivors 是
    空的（Scenic<->Event 這種完全沒有 overlap 的切換）時才會被呼叫，
    目的是分辨白色空 row 到底是下面哪一種情況：
    1. row 已經 mapped，但 child 沒 mapped
    2. row/child 都 mapped，只是 macOS 沒 repaint
    3. row geometry/height 已建立，但 Canvas window/scrollregion 沒同步
    4. thumbnail callback 更新 row 的時機造成 race

    stage 分三次呼叫（呼叫端見 _try_incremental_diff_update()）：
    - 'before_repack'：repack 迴圈執行前，記錄這次 diff 的輸入。
    - 'immediate_after_repack'：repack 迴圈剛跑完，還在同一個呼叫堆疊裡，
      沒有插入任何額外事件處理，跟目前的實際行為完全一致。
    - 'after_idle'：用 list_frame.after_idle() 排到下一個 Tk idle cycle
      才印——跟 'immediate_after_repack' 比對，才能看出 Canvas
      scrollregion／widget mapped 狀態是「一直都是這樣」還是「下一個
      idle cycle 就自己同步好了」。這裡完全沒有呼叫 update()/
      update_idletasks()，只是排一個之後才執行、單純讀狀態的 callback，
      不會提早消化 event queue，跟強制吃掉整個 pending event queue 的
      update() 是不同的兩件事。"""
    prefix = f'[Gallery Repack Diag][{stage}]'
    print(f'{prefix} old_order={old_order}')
    print(f'{prefix} new_order={new_order}')
    print(f'{prefix} old_survivors={old_survivors}')
    print(f'{prefix} added_keys={sorted(added_keys)}')
    print(f'{prefix} removed_keys={sorted(removed_keys)}')

    for item_key in sorted(added_keys):
        row_widget = _row_widgets_by_item_key.get(item_key)
        if row_widget is None:
            print(f'{prefix} added_key={item_key}: 沒有對應的 row widget（不應該發生）')
            continue
        if not row_widget.winfo_exists():
            print(f'{prefix} added_key={item_key}: winfo_exists()=False（widget 已經被 destroy）')
            continue

        is_mapped = row_widget.winfo_ismapped()
        try:
            pack_info = row_widget.pack_info()
        except tk.TclError as error:
            pack_info = f'<pack_info() TclError: {error}>'
        row_container_bg = row_widget.cget('bg')
        row_children = row_widget.winfo_children()
        print(f'{prefix} added_key={item_key}: winfo_ismapped={is_mapped}, pack_info={pack_info}, '
              f'row_container_bg={row_container_bg}, row_container_child_count={len(row_children)}')

        row_content = getattr(row_widget, 'gallery_row_content', None)
        if row_content is None or not row_content.winfo_exists():
            print(f'{prefix} added_key={item_key}: row_content 不存在或已 destroy')
            continue
        row_content_bg = row_content.cget('bg')
        content_children = row_content.winfo_children()
        print(f'{prefix} added_key={item_key}: row_content_bg={row_content_bg}, '
              f'row_content_child_count={len(content_children)}')
        for child in content_children:
            try:
                child_mapped = child.winfo_ismapped()
                child_class = child.winfo_class()
            except tk.TclError as error:
                child_mapped = f'<TclError: {error}>'
                child_class = '<unknown>'
            print(f'{prefix}   added_key={item_key} row_content child class={child_class} '
                  f'winfo_ismapped={child_mapped}')

    if list_frame is not None and list_frame.winfo_exists():
        pack_slaves_order = [getattr(widget, 'gallery_row_item_key', '<no item_key attr>')
                              for widget in list_frame.pack_slaves()]
        print(f'{prefix} list_frame.pack_slaves() 順序={pack_slaves_order}')
        print(f'{prefix} list_frame.winfo_reqwidth()={list_frame.winfo_reqwidth()}, '
              f'winfo_reqheight()={list_frame.winfo_reqheight()}')
    else:
        print(f'{prefix} list_frame 不存在或已 destroy')

    if list_canvas is not None and list_canvas.winfo_exists():
        print(f'{prefix} list_canvas.cget("scrollregion")={list_canvas.cget("scrollregion")}')
        for canvas_item_id in list_canvas.find_withtag('all'):
            if list_canvas.type(canvas_item_id) == 'window':
                item_width = list_canvas.itemcget(canvas_item_id, 'width')
                item_height = list_canvas.itemcget(canvas_item_id, 'height')
                print(f'{prefix} list_canvas window item id={canvas_item_id}: '
                      f'width={item_width}, height={item_height}')
    else:
        print(f'{prefix} list_canvas 不存在或已 destroy')


def _describe_widget_geometry(widget):
    """回傳 (manager, info)：manager 是 winfo_manager() 的結果（'pack'/
    'place'/'grid'/空字串），info 依實際 manager 呼叫對應的 pack_info()/
    place_info()/grid_info()——2026-09-17 使用者實機回報排查用：row_content
    是用 place() 排版的（見 add_list_row()），不是 pack()，如果不先用
    winfo_manager() 判斷、直接呼叫 pack_info()，對 row_content 一律會拿到
    TclError，看不出它實際是哪種 manager、還是已經完全沒有被任何
    geometry manager 管理（manager 是空字串）。"""
    try:
        manager = widget.winfo_manager()
    except tk.TclError as error:
        return '<TclError>', f'<winfo_manager() TclError: {error}>'
    if manager == 'pack':
        try:
            info = widget.pack_info()
        except tk.TclError as error:
            info = f'<pack_info() TclError: {error}>'
    elif manager == 'place':
        try:
            info = widget.place_info()
        except tk.TclError as error:
            info = f'<place_info() TclError: {error}>'
    elif manager == 'grid':
        try:
            info = widget.grid_info()
        except tk.TclError as error:
            info = f'<grid_info() TclError: {error}>'
    else:
        info = '<not managed by any geometry manager>'
    return manager, info


def _log_row_geometry_snapshot(stage, item_key, row_widget, origin=''):
    """2026-09-17 使用者實機回報 Root Cause 排查用（進階版，只印資訊、
    不改任何行為/狀態）：針對單一 row，把 row_container 本身跟
    row_content 及其每個主要 child 的 winfo_ismapped()/實際 geometry
    manager/對應的 pack_info()/place_info()/grid_info() 都印出來，用來
    確認「隱藏 row 時，除了最外層 row_container 的 pack_forget() 之外，
    row_content 或其 children 是不是也在某個環節被 pack_forget()/
    place_forget()/grid_remove() 過，而重新顯示時只把最外層 row_container
    pack 回來」。

    stage 標示這是生命週期的哪個時間點（呼叫端見
    _try_incremental_diff_update()）：before_hide/after_hide（第 1 步隱藏
    前後）、after_show_before_repack/after_create_before_repack（第 2 步
    show/建立之後，還沒進第 3 步 repack 之前）、after_repack_immediate
    （第 3 步 pack()/pack(before=...) 剛執行完）、after_idle（排到下一個
    Tk idle cycle 才印，跟 immediate 對照）。origin 標示這個 row 是
    'brand_new'/'destroyed_and_recreated'/'reused_unchanged' 三者之一
    （只有 added_keys 才有意義，其餘傳空字串），方便比較「剛建立的」跟
    「從隱藏池重新顯示的」兩種 row 的差異。"""
    prefix = f'[Gallery Repack Geometry Diag][{stage}][item_key={item_key}][origin={origin}]'
    if not row_widget.winfo_exists():
        print(f'{prefix} row_container: winfo_exists()=False（已被 destroy）')
        return

    manager, info = _describe_widget_geometry(row_widget)
    print(f'{prefix} row_container: winfo_ismapped={row_widget.winfo_ismapped()}, '
          f'winfo_manager={manager}, geometry_info={info}, bg={row_widget.cget("bg")}')

    row_content = getattr(row_widget, 'gallery_row_content', None)
    if row_content is None or not row_content.winfo_exists():
        print(f'{prefix} row_content: 不存在或已 destroy')
        return
    content_manager, content_info = _describe_widget_geometry(row_content)
    print(f'{prefix} row_content: winfo_ismapped={row_content.winfo_ismapped()}, '
          f'winfo_manager={content_manager}, geometry_info={content_info}, bg={row_content.cget("bg")}')

    for child in row_content.winfo_children():
        child_manager, child_info = _describe_widget_geometry(child)
        try:
            child_mapped = child.winfo_ismapped()
            child_class = child.winfo_class()
        except tk.TclError as error:
            child_mapped = f'<TclError: {error}>'
            child_class = '<unknown>'
        print(f'{prefix}   row_content child class={child_class} winfo_ismapped={child_mapped} '
              f'winfo_manager={child_manager} geometry_info={child_info}')


# 2026-09-17 使用者實機補充：白 row 不是永久卡住，大約 1~2 秒後有時會
# 自己恢復——用這幾個固定時間點（毫秒）各印一次精簡欄位，確認是不是
# 「macOS Tk remap 延遲 1~2 秒」這個假設。只用 list_frame.after(ms, ...)
# （Tk 標準計時器機制，跟 after_idle() 一樣單純排到之後才執行的
# callback），不呼叫 update()/update_idletasks()，也不重新 place()，
# 純粹讀狀態。
_MAPPING_TIMELINE_DELAYS_MS = (50, 100, 250, 500, 1000, 2000)


def _log_row_mapping_timeline_point(stage, item_key, row_widget):
    """時間序列版，只印使用者這次指定的精簡欄位（row_container/
    row_content 的 winfo_ismapped、row_content 的 winfo_manager、每個
    child 的 winfo_ismapped、canvas scrollregion），不是完整 geometry
    dump（那份已經有 _log_row_geometry_snapshot()）。只讀狀態，不改任何
    UI。"""
    prefix = f'[Gallery Repack Mapping Timeline][{stage}][item_key={item_key}]'
    if not row_widget.winfo_exists():
        print(f'{prefix} row_container: winfo_exists()=False（已被 destroy）')
        return
    print(f'{prefix} row_container.winfo_ismapped={row_widget.winfo_ismapped()}')

    row_content = getattr(row_widget, 'gallery_row_content', None)
    if row_content is None or not row_content.winfo_exists():
        print(f'{prefix} row_content: 不存在或已 destroy')
    else:
        content_manager, _content_info = _describe_widget_geometry(row_content)
        print(f'{prefix} row_content.winfo_ismapped={row_content.winfo_ismapped()}, '
              f'row_content.winfo_manager={content_manager}')
        for child in row_content.winfo_children():
            try:
                child_mapped = child.winfo_ismapped()
                child_class = child.winfo_class()
            except tk.TclError as error:
                child_mapped = f'<TclError: {error}>'
                child_class = '<unknown>'
            print(f'{prefix}   child class={child_class} winfo_ismapped={child_mapped}')

    if list_canvas is not None and list_canvas.winfo_exists():
        print(f'{prefix} list_canvas.scrollregion={list_canvas.cget("scrollregion")}')
    else:
        print(f'{prefix} list_canvas 不存在或已 destroy')


def _schedule_row_mapping_timeline_logging(added_keys):
    """對這次每個 added_key，各排 _MAPPING_TIMELINE_DELAYS_MS 裡的每一個
    延遲時間各一次 list_frame.after(ms, ...) callback，執行時才去查
    _row_widgets_by_item_key 拿目前的 widget（不是提早綁定，避免萬一這段
    期間 widget 被其他流程 destroy/replace 時拿到過期參照）。"""
    for delay_ms in _MAPPING_TIMELINE_DELAYS_MS:
        for item_key in added_keys:
            def _log_at_delay(item_key=item_key, delay_ms=delay_ms):
                row_widget = _row_widgets_by_item_key.get(item_key)
                if row_widget is not None:
                    _log_row_mapping_timeline_point(f'after_{delay_ms}ms', item_key, row_widget)
            list_frame.after(delay_ms, _log_at_delay)


def _try_incremental_diff_update(new_visible_groups):
    """2026-08-20 這一版兩個修正（Filter 不再 destroy 暫時不可見的 row、
    Batch Delete 離開 Selection Mode 不再 full rebuild survivor row）都
    是同一個核心觀念的延伸：舊版把「目前不在可見清單裡」直接當成「要
    destroy」，但這其實混淆了兩種完全不同的情況——

    1. 這個 item 在 current_groups 裡已經真的不存在了（真的被刪除）
       -> 這裡才需要 destroy，而且這一步已經搬到 prune_stale_row_
       widgets()（在 current_groups 真的變動時就先處理掉，比這支函式
       更早呼叫），這裡看到的 removed_keys 保證只會是下面第 2 種情況。
    2. 這個 item 還在 current_groups 裡，只是目前 Filter/View Mode 不顯示
       -> 只需要 pack_forget()「隱藏」，widget 保留活著，之後 Filter 切
       回來能直接 reuse，不用重新 add_list_row()。

    同樣地，「is_batch_mode 改變」也不再是「整批 fallback 完整 rebuild」
    的理由——Checkbox 有無只是 row_content 裡的一個 child，
    _show_checkbox_on_existing_row()/_hide_checkbox_on_existing_row() 已
    經證實可以在不 destroy 整個 row_container 的情況下動態加/拿掉，所以
    這裡改成：套用完 hide/show/create 之後，對這次「目前可見」的每一列
    各自檢查一次 Checkbox 有無跟目前 is_batch_mode 是否一致，不一致才
    動那一列的 Checkbox，其餘完全不碰。

    整體流程（old ∩ new / old - new / new - old 三種情況，跟上一版一樣
    不設變動筆數 threshold）：

    - old ∩ new（兩邊都可見）→ reuse，widget 完全不碰。是否需要重新
      pack() 調整位置，見下面「Repack survivors」的說明——純移除的情況
      現在完全不會動到 survivor。
    - old - new（原本可見，這次不可見）→ 一律當作「Filter 造成的暫時
      隱藏」（真的刪除已經被 prune_stale_row_widgets() 處理掉，不會出現
      在這裡）：只 pack_forget()，widget/Checkbox/pending thumbnail 狀態
      原封不動保留。
    - new - old（原本不可見，這次要顯示）→ 先看 _row_widgets_by_item_key
      有沒有現成的 hidden widget：
        - 有，而且隱藏期間內容沒有變（比對 _row_signature_by_item_key）
          → 直接 reuse，只需要之後統一 repack。
        - 有，但隱藏期間內容悄悄變了（例如 is_favorite）→ 只有這一筆
          單獨 destroy + 用 add_list_row() 重建，其餘 reuse/hidden row
          完全不受影響——不冒險顯示過期內容，但也不因為這一筆而拖累
          其他所有 row。
        - 完全沒有現成 widget（第一次看到這個 item）→ 直接
          add_list_row(place_immediately=False) 新建。

    Repack survivors（2026-08-20 使用者要求量測 Favorite Page Unfavorite
    卡頓後確認 Root Cause 並修正）：pack_forget()/pack() 呼叫本身很快，
    但這兩個呼叫會讓 Tk 排入一次幾何管理器重算，這段重算是延遲執行的，
    成本跟「這次被要求重新 pack 的 widget 數量」成正比，不是跟「真的
    變動了幾筆」成正比（實測：29 筆 survivor 全部 repack，pack_forget()/
    pack() 呼叫本身只要 0.08s，但接下來 Tk 幾何重算要 0.52s，是前者的
    6 倍以上，且完全不在 repack_duration 這個數字裡）。純移除（added_
    keys 是空的、removed_keys 不是空的）的情況現在完全不 repack 任何
    survivor——Tk 的 pack 管理器在一個 packed widget 被 pack_forget() 之
    後，會自動讓其餘 sibling 維持原本相對順序補上這個空隙，不需要對它們
    逐一重新呼叫 pack_forget()/pack()。只要 added_keys 非空（這次有任何
    新增/從 hidden pool 重新顯示的項目），還是維持原來對 new_order 全部
    repack 一次——新建的 widget 一定會被 add_list_row(place_immediately=
    False) append 到目前 pack 順序的最後面，如果它不該排在最後就必須
    repack 才能落到正確位置，這一輪不處理「只移動新增 row」這個更精細
    的優化（純新增情境留給下一輪評估 pack(before=...) 的安全性）。

    刻意不處理、直接回傳 False 讓呼叫端照舊走完整 rebuild 當 fallback
    的情況：
    - 還沒有完整 render 過一次（_rendered_signature is None）。
    - 除了新增/隱藏/顯示之外，其餘 survivor 項目的相對順序不一樣了
      （真正的 reorder，例如 current_groups 本身排序變了）。
    - 除了新增/隱藏/顯示之外，任何一個 survivor 自己的內容也剛好在同一
      次變了（同上一版的安全檢查，理由不變）。

    Empty <-> Non-empty 轉場（2026-08-20 使用者回報 Root Cause 並要求
    修正）：舊版「old_order 或 new_order 其中一個是空的就直接 fallback」
    這個限制已經拿掉了。舊版會這樣寫，是因為當初 Empty State 是一個沒有
    item_key、跟 row 完全不同結構的 widget（純 tk.Label），最初設計時
    圖方便直接排除、丟給完整 rebuild 處理——後來 hidden row pool 出現後，
    這個「方便」的代價變成無法接受：Favorite 從 1 筆變 0 筆時，
    new_order 是空的，一旦 fallback 就會 clear_list() 把所有隱藏中、
    跟這次變化完全無關的 Gallery row 一起 destroy 掉（真機實測 146 筆
    destroy_duration 高達 7 秒）。

    仔細檢查後發現：old_order/new_order 其中一個（或兩個）是空的情況，
    其實完全是上面 old ∩ new / old - new / new - old 這套邏輯的自然退化
    情況，不需要另外寫分支——empty list 的集合運算/迴圈天生就會產生
    正確結果（old_survivors/new_survivors 都會變成 []，兩者相等，
    reorder 檢查自然通過；removed_keys/added_keys 會正確涵蓋「全部隱藏」
    或「全部新增」；repack 迴圈對空的 new_order 自然是 0 次）。真正
    需要額外處理的只有一件事：Empty State Label 這個獨立於 row 之外的
    widget，要在 new_order 變空/變非空時對應顯示/隱藏——見
    _show_empty_state_label()/_hide_empty_state_label()，這裡呼叫這兩個
    函式，不會因為「可見清單變空」就誤判成要 destroy 任何 row。

    Scroll Position 分析同上一版不變：這裡完全沒有呼叫 yview_moveto()，
    見 _gallery_incremental_update_expanded_scope_check.py 的 Case 6。"""
    global _current_row_order, _visibility_check_last_yview, _row_cache_hit_perf_samples
    global _rendered_view_mode, _rendered_filter, _rendered_signature, _rendered_is_batch_mode
    global _current_groups_changed_without_render

    if _rendered_signature is None:
        return False

    old_order = _current_row_order
    new_order = [api.compute_item_key(group_items) for group_items in new_visible_groups]

    old_keys = set(old_order)
    new_keys = set(new_order)
    added_keys = new_keys - old_keys
    removed_keys = old_keys - new_keys

    # 除了新增/隱藏/顯示之外，其餘 survivor 項目的相對順序要完全一樣，
    # 才代表這次真的只是「Filter 造成的可見子集合改變」，沒有牽涉到真正
    # 的排序調整。純粹的 Filter/Favorite 篩選（從同一份已排序的
    # current_groups 挑子集合）本來就會自動滿足這個條件，不需要額外的
    # 筆數 threshold 才能安全套用。
    old_survivors = [key for key in old_order if key not in removed_keys]
    new_survivors = [key for key in new_order if key not in added_keys]
    if old_survivors != new_survivors:
        return False

    # 2026-09-17 使用者實機回報：Scenic<->Event 這種完全沒有 overlap
    # （old_survivors 剛好是空的）的切換，偶發白色空 row，且是
    # intermittent。往下每一步（隱藏/顯示/建立/repack/after_idle）遇到
    # is_diag_target 才會印診斷 log，避免洗版到不相關的一般 Filter 切換
    # （有 survivor 的，例如 All -> Scenic）。提早到這裡算好，因為下面
    # 第 0/1/2 步都要用到，不是只有原本第 3 步 repack 才需要。
    is_diag_target = not old_survivors

    # 除了新增/隱藏/顯示之外，任何一個 survivor 自己的內容（is_favorite
    # 等）有沒有同時悄悄變了——只比對 item_key 集合/順序抓不到這種情況，
    # 寧可 fallback 完整 rebuild，不冒險只更新一部分內容導致其他 row
    # 顯示過期資訊。
    new_signature_list = _compute_gallery_display_signature(new_visible_groups)
    new_signature_by_key = dict(zip(new_order, new_signature_list))
    old_signature_by_key = dict(zip(old_order, _rendered_signature))
    for survivor_key in old_survivors:
        if old_signature_by_key[survivor_key] != new_signature_by_key[survivor_key]:
            return False

    t_total_start = perf_counter()
    _row_cache_hit_perf_samples = []  # 跟完整 rebuild 一樣，避免累積到上一輪的數字

    # 0. Empty State 顯示/隱藏——跟 row 完全獨立管理，這裡只依這次最終
    # 是不是有任何一筆可見（new_order 是不是空的）決定，不管是哪種原因
    # 造成的空（Gallery 真的 0 筆／Favorite 0 筆／其他 Filter 結果 0 筆，
    # 文字由 get_empty_state_text() 內部自己分辨），跟下面的 row hide/
    # show/repack 完全不衝突（不同 widget）。放在最前面：0 -> N 的情況，
    # 要先把 Empty State 讓開，等一下才會 pack() 真正的 row 進來；順序
    # 對這裡的正確性其實不影響（整段函式執行完 Tk 才會真的重繪），這樣
    # 寫只是讀起來更直覺。
    t_empty_state_start = perf_counter()
    if new_order:
        _hide_empty_state_label()
    else:
        _show_empty_state_label()
    empty_state_duration = perf_counter() - t_empty_state_start

    # 1. 隱藏 old - new：只 pack_forget()，widget/Checkbox/pending
    # thumbnail 狀態都保留，不 destroy、不清任何 dict——之後 Filter 切
    # 回來能直接 reuse。removed_keys 裡大多數是「Filter 造成的暫時隱藏」
    # （widget 還在），但也可能包含「這個 item 剛好真的被刪除，已經被
    # prune_stale_row_widgets() 提前清掉 widget」的情況——prune 那支函式
    # 刻意不去同步修改 _current_row_order（見它自己的說明，避免搞亂
    # _can_skip_rebuild_for_current_view() 的判斷），所以這裡看到的
    # old_order 可能還留著已經被 prune 清掉 widget 的 key，這裡用
    # .get() 防禦性處理，widget 已經不在就跳過，不是漏處理，而是它已經
    # 在更早的階段被正確清乾淨了。
    t_hide_start = perf_counter()
    for hidden_key in removed_keys:
        hidden_widget = _row_widgets_by_item_key.get(hidden_key)
        if hidden_widget is not None:
            if is_diag_target:
                _log_row_geometry_snapshot('before_hide', hidden_key, hidden_widget)
            hidden_widget.pack_forget()
            if is_diag_target:
                _log_row_geometry_snapshot('after_hide', hidden_key, hidden_widget)
    hide_duration = perf_counter() - t_hide_start

    # 2. 顯示/新建 new - old：優先 reuse 現成的 hidden widget（隱藏期間
    # 內容沒變的話），沒有現成的、或內容悄悄變了才需要 add_list_row()。
    new_group_items_by_key = {api.compute_item_key(group_items): group_items
                               for group_items in new_visible_groups}
    shown_count = 0
    created_count = 0
    destroyed_stale_count = 0
    # 2026-09-17 使用者實機回報排查用：標記每個 added_key 這次是
    # 'reused_unchanged'（從隱藏池直接沿用，懷疑對象）、
    # 'destroyed_and_recreated'、還是 'brand_new'（剛建立），下面 repack/
    # after_idle 的診斷 log 會一起印出來，方便比較「剛建立的」跟「從隱藏池
    # 重新顯示的」兩種 row 的差異。只是本地追蹤用的 dict，不影響任何既有
    # 行為/資料結構。
    row_origin_by_item_key = {}
    t_show_start = perf_counter()
    for shown_key in added_keys:
        existing_widget = _row_widgets_by_item_key.get(shown_key)
        if existing_widget is not None and _row_signature_by_item_key.get(shown_key) == new_signature_by_key[shown_key]:
            # 現成的 hidden widget，隱藏期間內容沒變，直接 reuse。
            shown_count += 1
            row_origin_by_item_key[shown_key] = 'reused_unchanged'
            if is_diag_target:
                _log_row_geometry_snapshot('after_show_before_repack', shown_key, existing_widget,
                                            origin='reused_unchanged')
            continue
        if existing_widget is not None:
            # 現成的 hidden widget，但隱藏期間內容悄悄變了——只單獨
            # destroy 這一筆重建，不影響其他任何 row。
            existing_widget.destroy()
            checkbox_widgets_by_item_key.pop(shown_key, None)
            pending_thumbnail_labels.pop(shown_key, None)
            destroyed_stale_count += 1
            row_origin_by_item_key[shown_key] = 'destroyed_and_recreated'
        else:
            row_origin_by_item_key[shown_key] = 'brand_new'
        new_row_container = add_list_row(new_group_items_by_key[shown_key], place_immediately=False)
        _row_widgets_by_item_key[shown_key] = new_row_container
        created_count += 1
        if is_diag_target:
            _log_row_geometry_snapshot('after_create_before_repack', shown_key, new_row_container,
                                        origin=row_origin_by_item_key[shown_key])
    show_duration = perf_counter() - t_show_start

    # 3. Repack survivors——2026-08-20 使用者要求量測後確認 Root Cause：
    # pack_forget()/pack() 呼叫本身很快，但這兩個呼叫會讓 Tk 排入一次
    # 幾何管理器重算，這段重算是延遲（lazy）執行的，成本跟「這次被要求
    # 重新 pack 的 widget 數量」成正比，不是跟「真的變動了幾筆」成正比
    # ——之前無條件對 new_order 全部 repack 一次，對純移除（沒有任何
    # 新增）的情況完全是多做的：Tk 的 pack 管理器在一個 packed widget
    # 被 pack_forget() 之後，會自動讓其餘 sibling 維持原本相對順序補上
    # 這個空隙，不需要對它們逐一重新呼叫 pack_forget()/pack()（見
    # _debug_favorite_page_unfavorite_perf_repro.py 的實測：29 筆
    # survivor 全部 repack，Tk 幾何重算耗時 0.52s，遠高於 pack_forget()/
    # pack() 呼叫本身量到的 0.08s）。
    #
    # 純新增（added_keys 非空）的情況這一輪不處理：新建的 widget 一定會
    # 被 add_list_row(place_immediately=False) append 到目前 pack 順序的
    # 最後面，如果它不該排在最後（大部分情況都不會排在最後，因為新項目
    # 通常是最新的一筆、該排在最前面），就必須靠這裡的 repack 依
    # new_order 重新排一次才能落到正確位置——要不要改成用
    # pack(before=...) 只移動新增 row，留給下一輪評估。
    is_pure_removal = not added_keys and bool(removed_keys)

    # is_diag_target 已經在上面（old_survivors 剛算完那裡）算好，第 0/1/2
    # 步也會用到，這裡不用重算，只補這次 repack 前的整體快照。
    if is_diag_target:
        _log_gallery_repack_diagnostics('before_repack', old_order, new_order, old_survivors,
                                         added_keys, removed_keys)

    if is_pure_removal:
        survivor_repack_count = 0
        repack_duration = 0.0
    else:
        # 2026-09-17 macOS 實機回報：這裡原本對 new_order 全部 row 無條件
        # pack_forget() 再 pack() 一次，在 macOS 的 Tk 上會讓 Gallery List
        # 短暫大片空白（跟 Top Navigation 按鈕消失同一類成因：大量 widget
        # 同時 pack_forget()/pack()，macOS 沒有被強迫立刻重繪）。原本考慮
        # 補一次 window.update()/update_idletasks() 強制重繪，但
        # reset_gallery_scroll_to_top() 的說明已經記錄過：Thumbnail Lazy
        # Loading 的背景 worker thread 會呼叫 list_frame.after(0, ...)，
        # Tcl/Tk 不是 thread-safe，這裡疊加一次 update_idletasks()/update()
        # 已經真機測出會讓整個 Tk mainloop 卡死——不能重踩這個地雷（見
        # _try_incremental_diff_update() 上面的完整討論）。
        #
        # 改成只對真的需要「新增或從隱藏池重新顯示」的 row（added_keys）
        # 呼叫一次 pack(before=...) 插入到正確位置，survivor（已經在上面
        # 驗證過 old_survivors == new_survivors，相對順序保證沒變）完全
        # 不呼叫 pack_forget()/pack()，維持原本的 widget/pack 狀態不動。
        #
        # 反向走訪 new_order，用「這一筆後面緊接著的那一筆」當 before=
        # 的錨點：不管那個錨點是 survivor（本來就在正確位置，這次沒被
        # 動過）還是這一輪剛插入的 row，從最後一筆往前遞迴保證每一筆插入
        # 後的最終順序都正確；new_order 最後一筆如果剛好也要插入（代表
        # 它後面沒有任何東西），直接 pack() 附加到目前已管理 widget 清單
        # 最後面即可，不需要 before=。
        t_repack_start = perf_counter()
        pack_kwargs = dict(fill='none', anchor='w',
                            padx=(GALLERY_ROW_HORIZONTAL_MARGIN, GALLERY_ROW_HORIZONTAL_MARGIN), pady=4)
        next_widget = None
        moved_count = 0
        for item_key in reversed(new_order):
            row_widget = _row_widgets_by_item_key[item_key]
            if item_key in added_keys:
                if next_widget is not None:
                    row_widget.pack(before=next_widget, **pack_kwargs)
                else:
                    row_widget.pack(**pack_kwargs)
                moved_count += 1
                if is_diag_target:
                    _log_row_geometry_snapshot('after_repack_immediate', item_key, row_widget,
                                                origin=row_origin_by_item_key.get(item_key, ''))
            next_widget = row_widget
        repack_duration = perf_counter() - t_repack_start
        survivor_repack_count = moved_count

        # 2026-09-17 使用者實機驗證：方案 A（重新 place_configure() 同一組
        # place_info() 參數）實測沒有效果，白 row 依然會持續 1~2 秒——
        # 代表 Place 幾何管理器重新排程本身沒有連帶觸發 macOS 的重繪。
        # 換成方案 B：對 row_content 重新套用同一個 bg（走 widget option
        # reconfigure，不是 geometry manager，跟方案 A 是完全不同的內部
        # 路徑）。只針對「從隱藏池直接 reuse、沒有重建」的 row（origin ==
        # 'reused_unchanged'——brand_new/destroyed_and_recreated 都是剛
        # 建立的全新 widget，不會有這個問題，不處理），在 repack 完之後
        # 排一個 50ms 後才執行的一次性局部操作。同一時間只測一個方案，
        # 不跟方案 A 一起上，才能知道到底是哪個真的有效。完全沒有呼叫
        # update()/update_idletasks()，不消化事件佇列，也不動 row_content
        # 的 children；只在 macOS 上執行，Windows（sys.platform != 'darwin'）
        # 完全不會進到這段程式碼，跟目前的 pack(before=...) 排序邏輯完全
        # 獨立、互不影響。
        if sys.platform == 'darwin':
            for item_key in added_keys:
                if row_origin_by_item_key.get(item_key) != 'reused_unchanged':
                    continue

                def _nudge_reused_row_content_repaint(item_key=item_key):
                    row_widget = _row_widgets_by_item_key.get(item_key)
                    if row_widget is None or not row_widget.winfo_exists():
                        return
                    row_content = getattr(row_widget, 'gallery_row_content', None)
                    if row_content is None or not row_content.winfo_exists():
                        return
                    row_content.config(bg=row_content.cget('bg'))

                list_frame.after(50, _nudge_reused_row_content_repaint)

        if is_diag_target:
            _log_gallery_repack_diagnostics('immediate_after_repack', old_order, new_order, old_survivors,
                                             added_keys, removed_keys)

            def _log_after_idle_snapshot():
                # 這裡故意不呼叫 update()/update_idletasks()，只是排一個
                # 「之後才執行、單純讀狀態」的 callback，跟目前這批 pack()/
                # pack_forget() 完全不搶事件佇列——用來跟上面
                # 'after_repack_immediate'/'immediate_after_repack' 那幾筆
                # 比對，才能看出 Canvas scrollregion／widget mapped 狀態是
                # 「一直都是這樣」還是「下一個 idle cycle 就自己同步好了」。
                _log_gallery_repack_diagnostics('after_idle', old_order, new_order, old_survivors,
                                                 added_keys, removed_keys)
                for item_key in added_keys:
                    row_widget = _row_widgets_by_item_key.get(item_key)
                    if row_widget is not None:
                        _log_row_geometry_snapshot('after_idle', item_key, row_widget,
                                                    origin=row_origin_by_item_key.get(item_key, ''))

            list_frame.after_idle(_log_after_idle_snapshot)

            # 2026-09-17 使用者實機補充：白 row 有時候大約 1~2 秒後會自己
            # 恢復——額外排一段固定時間點的時間序列 log（50ms~2000ms），
            # 確認是不是「macOS Tk remap 延遲 1~2 秒」。純讀狀態，不呼叫
            # update()/update_idletasks()，不重新 place()。
            _schedule_row_mapping_timeline_logging(added_keys)

    # 4. is_batch_mode 一致性檢查：對這次「目前可見」的每一列，確保
    # Checkbox 有無跟目前 is_batch_mode 一致——不管這一列是原本就可見的
    # survivor、剛從 hidden 重新顯示、還是剛新建的，統一用同一個判斷；
    # 剛新建的 row 在 add_list_row() 當下就已經依 is_batch_mode 建好，
    # 這裡對它是 no-op。不用「is_batch_mode 這次是否真的改變」當閘門，
    # 因為 hidden row 可能是在 is_batch_mode 還是另一個值的時候被隱藏的，
    # 重新顯示時才是真正要修正的時機，不能只看「這次 render 跟上次比」。
    t_checkbox_start = perf_counter()
    for item_key in new_order:
        row_widget = _row_widgets_by_item_key[item_key]
        if is_batch_mode:
            _show_checkbox_on_existing_row(row_widget)
        else:
            _hide_checkbox_on_existing_row(row_widget)
    checkbox_sync_duration = perf_counter() - t_checkbox_start

    total_duration = perf_counter() - t_total_start

    # 5. 更新「目前還存在 widget 的每一筆」的 signature cache——不只是
    # new_order（目前可見的），removed_keys（這次隱藏的）本來就沒動內容，
    # 沿用它們原本的 signature 即可，不需要重算/覆蓋。
    for item_key in new_order:
        _row_signature_by_item_key[item_key] = new_signature_by_key[item_key]

    _current_row_order = new_order
    # 跟完整 rebuild 一樣：row 的 index<->畫面位置對應關係變了，舊的 yview
    # 快取不能再信，強迫下一次可視範圍檢查重新掃描一次。
    _visibility_check_last_yview = None
    _rendered_view_mode = current_view_mode
    _rendered_filter = current_filter
    _rendered_signature = new_signature_list  # 沿用上面已經算好的，不用再重算一次
    _rendered_is_batch_mode = is_batch_mode
    _current_groups_changed_without_render = False  # 這次增量更新已經讓畫面追上 current_groups，旗標消費掉
    update_selection_status_text()

    # 2026-08-20 使用者要求追查 Favorite Page Unfavorite 效能問題：把這次
    # 已經算好的數字存進 _last_incremental_diff_metrics，給呼叫端（目前是
    # apply_star_visual_update()）組自己的 [Favorite Perf] Summary 用，
    # 純粹讀取，不影響這裡任何既有行為/既有 print。
    global _last_incremental_diff_metrics
    _last_incremental_diff_metrics = {
        'reused': len(old_survivors), 'hidden': len(removed_keys), 'shown': shown_count,
        'created': created_count, 'destroyed': destroyed_stale_count,
        'hide_duration': hide_duration, 'show_duration': show_duration,
        'repack_duration': repack_duration, 'checkbox_sync_duration': checkbox_sync_duration,
        'total_duration': total_duration,
        'is_pure_removal': is_pure_removal, 'survivor_repack_count': survivor_repack_count,
        'empty_state_duration': empty_state_duration, 'is_now_empty': not bool(new_order),
    }

    print(f'[Gallery Perf] Incremental update: reused={len(old_survivors)}, hidden={len(removed_keys)}, '
          f'shown={shown_count}, created={created_count}, destroyed={destroyed_stale_count}, '
          f'hide_duration={hide_duration:.4f}s, show_duration={show_duration:.4f}s, '
          f'repack_duration={repack_duration:.4f}s (survivor_repack_count={survivor_repack_count}, '
          f'is_pure_removal={is_pure_removal}), '
          f'checkbox_sync_duration={checkbox_sync_duration:.4f}s, empty_state_duration={empty_state_duration:.4f}s '
          f'(is_now_empty={not bool(new_order)}), total_duration={total_duration:.4f}s')

    # 2026-08-20 使用者回報真機 Trigger Time DESC 排序 regression（Filter/
    # Favorite 之後、離開 Gallery 期間新增 recording，回到 Gallery 舊資料
    # 卡在最上面），要求先只加診斷 log 不要改邏輯。這裡印出這次 diff 算出
    # 來的 new_order，跟 Tk 兩種不同意義的「目前 widget 順序」：
    # - pack_slaves()：Tk 目前真正的視覺 pack 順序（geometry manager 的
    #   stacking 順序，pack_forget()+pack() 之後會改變）——這才是「畫面上
    #   實際看到的順序」的正確判斷依據。
    # - winfo_children()：Tk 這個 parent 底下所有 child 的「建立順序」
    #   （不受 pack_forget()/pack() 影響，新建的 widget 一律排在最後），
    #   不是視覺順序，只是為了對照才印出來，避免有人誤用它當作「畫面順序」
    #   來判斷（這是這幾輪已經在其他測試腳本確認過的既有 Tk 行為）。
    # 只要 new_order 本身正確，這裡的目的是確認 pack_slaves() 是不是真的
    # 跟 new_order 同步；如果哪一天兩者對不上，這行 log 會直接指出來，不用
    # 再靠真機肉眼看畫面猜。
    actual_pack_order = [getattr(widget, 'gallery_row_item_key', None) for widget in list_frame.pack_slaves()]
    actual_pack_order = [key for key in actual_pack_order if key is not None]
    actual_creation_order = [getattr(widget, 'gallery_row_item_key', None) for widget in list_frame.winfo_children()]
    actual_creation_order = [key for key in actual_creation_order if key is not None]
    order_matches = actual_pack_order == new_order
    print(f'[Gallery Order Diag] new_order={new_order}')
    print(f'[Gallery Order Diag] actual_pack_order (pack_slaves，真正視覺順序)={actual_pack_order} '
          f'matches_new_order={order_matches}')
    if actual_creation_order != new_order:
        print(f'[Gallery Order Diag] actual_creation_order (winfo_children，只是建立順序，'
              f'不是視覺順序，僅供對照)={actual_creation_order}')
    if not order_matches:
        print(f'[Gallery Order Diag] ⚠ new_order 跟 pack_slaves() 實際視覺順序不一致！'
              f'new_order={new_order} vs actual_pack_order={actual_pack_order}')

    # Row 建立完（如果這次有新增/顯示）立刻檢查一次可視範圍，跟完整
    # rebuild 收尾動作一致，不用等 200ms 週期檢查的第一次才觸發。
    list_canvas.after_idle(queue_visible_thumbnail_downloads)
    return True


def render_visible_list(perf_total_start=None, perf_stage_durations=None, cache_render_start=None):
    """依 get_visible_groups() 重新畫整個清單。Refresh 完、切 Filter、
    切批次選擇模式（Checkbox 顯示/隱藏）、切 Gallery/Favorite 都會呼叫
    這個。2026-08-14 改成 Thumbnail Lazy Loading 之後：已經在
    current_thumbnails_by_item_key cache 裡的直接顯示，不會重新下載；
    還沒下載過的先顯示佔位，交給 add_list_row() 註冊進
    pending_thumbnail_labels，實際下載由 queue_visible_thumbnail_downloads()
    依可視範圍觸發。

    這裡有兩種互斥的 Performance Profiling 模式，呼叫端只會給其中一種：

    - perf_total_start/perf_stage_durations（mode=fresh_refresh）：完整
      refresh 流程（load_recordings() -> on_recordings_loaded()）才會給
      值，perf_total_start 是這次 GET recordings 開始的時間點。
    - cache_render_start（mode=cache_render）：2026-08-20 使用者回報，
      真機最常見的操作路徑（開 Tool -> 點 Gallery）其實是
      show_gallery_view()/show_favorite_view() 直接用現成的 current_groups
      cache render 一次，完全沒有經過 load_recordings() 這條 GET 路徑
      ——current_groups 通常是 Recording 頁面的
      report_gallery_item_count_from_recordings() 側漏填好的，這裡沒有
      GET/分組/排序可以算，cache_render_start 是 show_gallery_view()/
      show_favorite_view() 呼叫這裡之前記錄的時間點。

    不管哪種模式，都用來印出 Favorite/Filter 處理、UI row 建立這兩個
    階段的耗時，以及到第一批 row 顯示為止的總耗時（= 使用者已經可以看到
    並操作 Gallery List 的時間點，也就是 First Usable Gallery Time；不含
    縮圖，縮圖是 Lazy Loading，耗時另外印在
    queue_visible_thumbnail_downloads()/on_thumbnail_downloaded()）。這次
    順便把這幾個階段的耗時（＋fresh_refresh 模式下 perf_stage_durations
    帶的 GET/分組/排序/完成度過濾耗時）存進 _gallery_load_perf_context，
    等這次連帶觸發的第一批 Thumbnail 下載完成後，由
    _maybe_print_gallery_load_summary() 統一印出完整的 Gallery Load
    Summary（明確標示 mode 是 cache_render 還是 fresh_refresh，兩種不會
    混在一起算）。量測完後這幾行 Log 要移除。"""
    global _gallery_render_call_count, _current_row_order, _visibility_check_last_yview
    global _row_cache_hit_perf_samples
    global _rendered_view_mode, _rendered_filter, _rendered_signature, _rendered_is_batch_mode
    global _current_groups_changed_without_render
    global _last_incremental_diff_metrics, _last_clear_list_metrics
    # 2026-08-20 使用者要求追查 Favorite Page Unfavorite 效能問題：這兩個
    # 是「最近一次呼叫結果」的快照，每次 render_visible_list() 一開始就
    # 重設成 None，讓呼叫端（例如 apply_star_visual_update()）在這次呼叫
    # 結束後讀到的一定是這次的結果，不會誤讀到上一次殘留的舊數字。
    _last_incremental_diff_metrics = None
    _last_clear_list_metrics = None
    _gallery_render_call_count += 1
    _gallery_render_diag_log('render_visible_list',
                              f'render_call_count={_gallery_render_call_count} perf_total_start_given={perf_total_start is not None} '
                              f'cache_render_start_given={cache_render_start is not None}')
    cached_items_count = len(current_groups)
    t0 = perf_counter()
    visible_groups = get_visible_groups()
    t1 = perf_counter()

    # 2026-08-20 使用者要求擴大「Gallery 增量更新」涵蓋範圍（含 Filter 切換
    # 這種可能一次變動多筆的情況）：完整 clear_list()/rebuild 之前，先讓
    # _try_incremental_diff_update() 判斷這次 old/new 可見清單能不能安全
    # 算出交集直接套用——可以的話直接在裡面套用完就 return，完全不會執行
    # 下面的 clear_list()/rebuild；不行的話回傳 False，完全不影響、不觸碰
    # 下面既有的完整 rebuild 邏輯，原始行為 100% 保留當作 fallback（沒有
    # 另外發明第二套「要不要重畫」的判斷，這裡只是在既有流程最前面多一層
    # 機會）。
    if _try_incremental_diff_update(visible_groups):
        return

    clear_list()
    cache_hit_count = 0
    cache_miss_count = 0
    _row_cache_hit_perf_samples = []  # 2026-08-20 新增：這次重畫的 cache-hit row 細部耗時，重畫開始前清空，避免累積到上一次重畫的數字
    if not visible_groups:
        # 2026-08-20 使用者要求把 Empty State 獨立管理之後：完整 rebuild
        # 這裡也改用同一組 show/hide 函式，不要另外再寫一次建立邏輯
        # ——clear_list() 剛剛已經把 _empty_state_label_widget 重設成
        # None，這裡呼叫會正確建立一個新的，不是嘗試 reuse 被 destroy
        # 的舊 widget。
        _show_empty_state_label()
    else:
        for group_items in visible_groups:
            item_key = api.compute_item_key(group_items)
            # 2026-08-20 使用者回報真機同一次 render 中，同一個 fileid
            # 出現兩次 'Row created' 要求追查——Layer 3 診斷：這個迴圈上面
            # 剛被 clear_list() 清空過 _row_widgets_by_item_key，所以這裡
            # 如果同一個 item_key 在這次 render 的迴圈裡出現第二次，代表
            # 這次要顯示的 visible_groups 本身就有兩個不同的 group 算出
            # 同一個 item_key（純印 log，不影響任何行為，不會因此跳過
            # add_list_row()，仍然照舊建立第二個 row）。改用
            # api.compute_item_key() 之後這裡理論上不應該再重複出現，
            # 保留這個診斷是為了在真的又發生 collision 時能立刻發現
            # （例如 groupid/srcUri 本身也不唯一的情況），不是預期會觸發。
            if item_key in _row_widgets_by_item_key:
                previous_group_items = _row_widgets_by_item_key_source.get(item_key)
                print(f'[Gallery Duplicate Diag] Layer 3 (Rendered rows): item_key={item_key} '
                      f'在這次 render 裡重複出現！')
                if previous_group_items is not None:
                    print(f'[Gallery Duplicate Diag]   先前那筆代表項目: '
                          f'{_format_recording_item_identity(api.get_group_representative(previous_group_items))}, '
                          f'該 group 內共 {len(previous_group_items)} 段')
                print(f'[Gallery Duplicate Diag]   這次這筆代表項目: '
                      f'{_format_recording_item_identity(api.get_group_representative(group_items))}, '
                      f'該 group 內共 {len(group_items)} 段')
            _row_widgets_by_item_key_source[item_key] = group_items
            if item_key in current_thumbnails_by_item_key:
                cache_hit_count += 1
            else:
                cache_miss_count += 1
            # 2026-08-20 使用者要求 Gallery 增量更新：完整 rebuild 這裡也要
            # 維護 _row_widgets_by_item_key（item_key -> row_container 對
            # 照表）跟 _row_signature_by_item_key（這一筆目前的內容
            # signature），之後才能在下一次 render_visible_list() 呼叫時，
            # 讓 _try_incremental_diff_update() 有正確的基準可以 diff/判斷
            # 隱藏期間內容有沒有變，不是只有增量路徑自己維護一份。
            _row_widgets_by_item_key[item_key] = add_list_row(group_items)
            _row_signature_by_item_key[item_key] = _compute_single_item_signature(group_items)
    t2 = perf_counter()
    update_selection_status_text()

    # 2026-08-20 使用者回報第二次進 Gallery 異常慢，要求拆開 add_list_row()
    # 的 cache-hit branch 細部計時（見該函式內的說明）。這裡把這次重畫
    # 收集到的每一筆 cache-hit row 耗時做彙總，才能一眼看出哪個步驟佔比
    # 最高，不用自己手動加總每一行 log。cache_miss/沒有任何 cache-hit
    # 的重畫（例如第一次進 Gallery）_row_cache_hit_perf_samples 會是空的，
    # 不印這段彙總，避免洗版。
    if _row_cache_hit_perf_samples:
        step_labels = ['cache_lookup', 'Image.open', 'resize', 'ImageTk.PhotoImage', 'label_create', 'pack', 'row_total']
        sample_count = len(_row_cache_hit_perf_samples)
        print(f'[Gallery Perf] Cache-hit row 細部耗時彙總（{sample_count} 筆 cache-hit rows）:')
        for step_label in step_labels:
            step_values = [sample[step_label] for sample in _row_cache_hit_perf_samples]
            step_total = sum(step_values)
            step_avg = step_total / sample_count
            step_max = max(step_values)
            print(f'[Gallery Perf]   {step_label}: total={step_total:.4f}s, avg={step_avg:.4f}s, max={step_max:.4f}s')

    # 2026-08-20 使用者要求優化 queue_visible_thumbnail_downloads()：改成
    # 用「畫面順序的 item_key 列表」+ canvas.yview() 概算 viewport index
    # range，取代原本逐一呼叫 winfo_rooty() 掃描全部 pending_thumbnail_
    # labels（每 200ms 一次，4000 筆時會很貴）。_current_row_order 在這裡
    # 重新整份重建（跟 visible_groups 同一份順序，不用另外呼叫
    # list_frame.winfo_children() 這種一樣要問 Tk、一樣是 O(N) 的方式取得
    # 順序），任何會重畫 List 的路徑（Refresh/Filter/Gallery<->Favorite/
    # Delete/Format/Favorite 狀態改變等，全部都經過這支函式）都會連帶更新
    # 這份順序，不用個別在每個呼叫端補。
    #
    # _visibility_check_last_yview 同時重置成 None：不管這次 render 完
    # 之後 canvas.yview() 剛好是不是跟重畫前一樣，都要強迫下一次
    # queue_visible_thumbnail_downloads() 真的重新掃一次（不能因為 yview
    # 數值剛好相同就誤判成「畫面沒變化」而跳過這次 render 之後的第一次
    # Thumbnail 排隊）——這是 Gallery 首次進入、Filter 切換、Gallery/
    # Favorite 切換、各種造成 List 重建的操作，都能确保不受舊 cache 影響
    # 的關鍵一步。
    _current_row_order = [api.compute_item_key(group_items) for group_items in visible_groups]
    _visibility_check_last_yview = None

    # 2026-08-20 使用者要求：這裡是唯一真的執行完整重畫（clear_list() +
    # 重建全部 row）的地方，重畫完成後把「這次實際顯示的內容」記錄下來
    # （view_mode/filter/signature），show_gallery_view()/show_favorite_
    # view() 之後再次進入時，靠 _can_skip_rebuild_for_current_view() 比對
    # 這幾個值決定要不要跳過整個 clear_list()/render_visible_list()——
    # 沿用 render_visible_list() 本身當作唯一的「重畫完成」時間點來更新，
    # 不管是被 Refresh/Filter/Gallery<->Favorite/Delete/Selection Mode
    # 切換等哪一種呼叫端觸發都會更新到，不用在每個呼叫端各自補一份。
    _rendered_view_mode = current_view_mode
    _rendered_filter = current_filter
    _rendered_signature = _compute_gallery_display_signature(visible_groups)
    _rendered_is_batch_mode = is_batch_mode
    _current_groups_changed_without_render = False  # 這次完整 rebuild 已經讓畫面追上 current_groups，旗標消費掉

    # 2026-08-14 使用者要求量測：這次重畫裡有幾筆縮圖是直接吃 cache（沒有
    # 重新下載），幾筆是還沒下載過的——第二次 Refresh 應該幾乎全部是
    # cache_hit，miss 應該只剩 DVR 端真的新增的項目。
    print(f'[Gallery Perf] Thumbnail cache 命中狀況: cache_hit={cache_hit_count}, '
          f'cache_miss(還沒下載過)={cache_miss_count}, total_rows={len(visible_groups)}')

    # Row 建立完立刻檢查一次可視範圍，載入目前捲動位置看得到的縮圖，不用
    # 等 GALLERY_VISIBILITY_CHECK_INTERVAL_MS 週期檢查的第一次才觸發。
    list_canvas.after_idle(queue_visible_thumbnail_downloads)

    # perf_total_start（fresh_refresh）跟 cache_render_start（cache_render）
    # 兩個呼叫端不會同時給值，這裡統一算出這次要用哪一個當計時起點、
    # 屬於哪個 mode，兩條路徑共用底下這幾行 print/context 組裝邏輯，
    # 不用各自寫一份。
    reference_start = perf_total_start if perf_total_start is not None else cache_render_start
    mode = 'fresh_refresh' if perf_total_start is not None else ('cache_render' if cache_render_start is not None else None)

    if reference_start is not None:
        visible_filter_duration = t1 - t0
        ui_rows_duration = t2 - t1
        first_usable_duration = t2 - reference_start
        print(f'[Gallery Perf] 4. Favorite/filter processing (get_visible_groups) [mode={mode}]: '
              f'start=+{t0 - reference_start:.3f}s, end=+{t1 - reference_start:.3f}s, '
              f'duration={visible_filter_duration:.3f}s, final_gallery_item_count={len(visible_groups)}')
        print(f'[Gallery Perf] 6. UI row creation/render [mode={mode}]: start=+{t1 - reference_start:.3f}s, '
              f'end=+{t2 - reference_start:.3f}s, duration={ui_rows_duration:.3f}s, rows_created={len(visible_groups)}')
        print(f'[Gallery Perf] 7. First Usable Gallery Time [mode={mode}]（->使用者已經可以看到並操作 '
              f'Gallery List，不含縮圖）: {first_usable_duration:.3f}s')

        global _gallery_load_perf_context
        if mode == 'fresh_refresh' and perf_stage_durations is not None:
            _gallery_load_perf_context = {
                'mode': 'fresh_refresh',
                'total_start': perf_total_start,
                'recordings_count': perf_stage_durations['recordings_count'],
                'get_duration': perf_stage_durations['get_duration'],
                'group_duration': perf_stage_durations['group_duration'],
                'sort_duration': perf_stage_durations['sort_duration'],
                'completion_filter_duration': perf_stage_durations['completion_filter_duration'],
                'visible_filter_duration': visible_filter_duration,
                'ui_rows_duration': ui_rows_duration,
                'row_count': len(visible_groups),
                'first_usable_duration': first_usable_duration,
            }
        elif mode == 'cache_render':
            # 2026-08-20 使用者明確要求：這條路徑沒有新的 GET recordings，
            # Summary 裡不能硬算 GET/分組/排序——這裡只存 cached_items
            # （render 開始當下 current_groups 的筆數，不是篩選後的數字）
            # 跟 Favorite/Filter、UI row 建立、First Usable 這幾個真的有
            # 量到的階段。
            _gallery_load_perf_context = {
                'mode': 'cache_render',
                'total_start': cache_render_start,
                'cached_items': cached_items_count,
                'visible_filter_duration': visible_filter_duration,
                'ui_rows_duration': ui_rows_duration,
                'row_count': len(visible_groups),
                'first_usable_duration': first_usable_duration,
            }


def reset_gallery_scroll_to_top():
    """2026-08-20 使用者真機回報：Gallery 捲動到下方後套用 Filter，畫面
    整片黑/第一筆上方殘留黑色空間/在第一筆時滾輪仍可繼續往上捲。

    Root Cause：list_canvas 的 scrollregion 只靠 inner_frame（=list_frame）
    的 <Configure> binding 被動更新（見 create_scrollable_list_area()）：
    `inner_frame.bind('<Configure>', lambda event: canvas.configure(
    scrollregion=canvas.bbox('all')))`。這個事件要等 Tk 下一輪 idle 才會
    真的觸發；而且不管什麼時候觸發，Tk 的 canvas 在 scrollregion 縮小時
    不會自動把「目前捲動位置」拉回有效範圍——原本 render_visible_list()
    整段重畫流程完全沒有呼叫任何 yview_moveto()/重新設定 scrollregion，
    所以 Filter 前如果捲到了下面（例如舊的、比較高的 scrollregion 的
    70% 位置），Filter 後 list_frame 變矮了，view 還是停在舊的絕對像素
    位置，落在新內容範圍之外——這就是整片黑（viewport 在新內容上方/
    下方之外）、以及殘留空間、滾輪還能繼續往上捲（scrollregion 那時候
    還是舊的、比較高的值，捲動當然還沒到底）的成因。不是 widget layout
    本身錯位，是 viewport/scrollregion 跟新內容沒有同步。

    修法：Filter 屬於使用者主動改變資料集合，不需要保留 Filter 前的
    scroll position，所以直接呼叫 yview_moveto(0.0) 重設回最上方。

    2026-08-20 實測發現：這裡原本多加了 list_canvas.update_idletasks()
    + 手動 canvas.configure(scrollregion=...)，想「強迫 Tk 立刻算完這批
    新 row 的版面配置」，結果反而造成真的會卡住的 race——update_idletasks()
    會整批處理 Tk 目前所有 pending 的事件/idle callback，這個時間點
    Thumbnail Lazy Loading 的背景 worker thread 可能正好也在呼叫
    list_frame.after(0, ...) 把縮圖下載結果排回主執行緒；Tcl/Tk 本身不是
    thread-safe 的，這裡再疊加一次跨執行緒的事件佇列操作，量測到會直接
    卡住整個 Tk mainloop（背景 thread 還在跑，但主執行緒完全沒有再前進）。
    改成不需要 update_idletasks()、也不需要手動設定 scrollregion：
    yview_moveto(fraction) 是「把 view 移到 scrollregion 的 fraction
    位置」，fraction=0.0 對應的絕對座標永遠是 scrollregion 的 y 起點（這裡
    固定是 0，因為 inner_frame 一定是從 canvas 座標 (0,0) 開始排版），跟
    scrollregion 目前的高度是舊的、中間值、還是已經更新完全無關——不會有
    使用者擔心的『yview_moveto(0) 執行得太早，之後 <Configure> 又用新
    geometry 更新 scrollregion，最終 viewport 又跑掉』這個問題，因為
    無論 scrollregion 之後怎麼變，0 永遠還是 0，不需要等版面計算完成才能
    呼叫。scrollregion 本身繼續交給既有的 inner_frame <Configure> binding
    被動更新（不用管它什麼時候真的觸發），呼叫端接下來（render_visible_
    list() 內已經排定的 list_canvas.after_idle(queue_visible_thumbnail_
    downloads)）執行時，Tk 的 idle 佇列本來就會先處理版面/幾何相關的
    idle task、才輪到一般的 after_idle callback，所以那時候 canvas 位置
    跟 scrollregion 都已經是正確、同步好的狀態，可視範圍判斷（is_row_
    visible() 用的 winfo_rooty()）不會拿到中間值。

    呼叫端：
    - on_filter_selected()：Filter 是使用者主動改變資料集合，語意上
      本來就該重設回頂端。
    - 2026-08-20 使用者新規則：show_gallery_view()/show_favorite_view()
      在「從 Recording 點 Gallery/Favorite Tab 進來」這個情境下，也要
      無條件呼叫這裡（見這兩支函式的 reset_scroll_to_top 參數）——不管
      這次進來是完全跳過重畫（內容沒變、直接沿用既有 widget tree）、
      走增量 diff、還是完整 rebuild，都要重設回頂端，「Scroll reset 不
      代表 rebuild」：這裡呼叫跟要不要重畫是兩件互相獨立的事，這支函式
      本身不會觸發任何 render_visible_list()/clear_list()。至於「Player/
      Photo Viewer 返回 Gallery」則刻意不傳這個參數、維持捲動位置——
      兩者呼叫的是同一個 show_gallery_view()/show_favorite_view()，差別
      只在呼叫端傳不傳 reset_scroll_to_top=True，不是另外寫兩套函式。
    - 其他呼叫 render_visible_list() 的地方（Gallery 內資料背景 Refresh、
      Selection Mode 進出、Favorite 分頁裡 Unfavorite 造成的重畫、單筆
      Delete/Favorite 原地操作）都不呼叫這裡，維持使用者原本的捲動
      位置——這幾個情境使用者沒有主動要求『重新從第一筆看』，跟 Filter/
      從 Recording 進 Gallery 的語意不一樣。

    沒有另外建立第二套 Scroll Position 系統：全程只用 Tk canvas 原生的
    scrollregion/yview，跟既有機制共用同一份狀態。"""
    global _visibility_check_last_yview
    list_canvas.yview_moveto(0.0)
    # 2026-08-20 使用者明確要求：重設回頂端後，Thumbnail Lazy Loading 的
    # 可視範圍檢查一定要重新掃描一次目前 viewport，不能沿用重設前的舊
    # yview 快取值誤判成「跟上次一樣、不用掃」而跳過（見
    # queue_visible_thumbnail_downloads() 的 dirty check）。yview_moveto()
    # 之後 list_canvas.yview() 實際上通常也已經會回傳不同的 fraction、
    # 讓既有的 dirty check 自然偵測到差異，但這裡明確重設成 None，不依賴
    # 「剛好會不一樣」這個間接效果，語意上更直接、也不怕未來 dirty check
    # 邏輯調整後失效。
    _visibility_check_last_yview = None


def get_card_width():
    """2026-08-17 第四次調整：不再用比例夾寬度，Row 直接延伸到接近右側
    Scrollbar，左右各留 GALLERY_ROW_HORIZONTAL_MARGIN 的固定 margin（跟
    status_label 的左邊距一致，兩者才會對齊，見 add_list_row() 的
    row_container.pack()）。CARD_MIN_WIDTH 只在視窗窄到 margin 都放不下時
    當一個下限，避免算出負值。"""
    available_width = list_canvas.winfo_width() if list_canvas else 0
    if available_width <= 1:
        available_width = 900  # 畫面還沒真的算出寬度前（剛開啟）的保守預設值
    return max(CARD_MIN_WIDTH, available_width - 2 * GALLERY_ROW_HORIZONTAL_MARGIN)


def draw_rounded_rect(canvas, x1, y1, x2, y2, radius, fill, outline='', width=0, tags=None):
    """在 Canvas 上畫一個圓角矩形。原本是用兩個矩形（十字形拼接）＋四個
    quarter-circle 扇形硬拼出來，Canvas 原生 arc 在小尺寸下邊緣本來就會
    有鋸齒。2026-08-17 改成呼叫 ui_shapes（PIL 高解析度畫完再 LANCZOS
    縮小＋cache），畫出來的圖用 canvas.create_image() 貼上去，簽名/呼叫
    方式對呼叫端（screen_recording 的左側面板卡片）完全不變。"""
    radius = min(radius, (x2 - x1) / 2, (y2 - y1) / 2)
    width_px = round(x2 - x1)
    height_px = round(y2 - y1)
    outline_color = outline if outline else None
    photo_image = ui_shapes.make_rounded_rect_photo_image(
        width_px, height_px, radius, fill=fill, outline=outline_color, outline_width=width)
    kwargs = {'anchor': 'nw'}
    if tags:
        kwargs['tags'] = tags
    item_id = canvas.create_image(x1, y1, image=photo_image, **kwargs)
    # PhotoImage 沒有其他 Python 參照指著會被 GC 回收，畫面上的圖就會消失
    # ——用 canvas 本身的一個屬性存住這個參照即可（cache 只負責「不用重畫」，
    # 不負責參照保留，兩件事分開）。用 dict 存多個 item 的參照，因為同一個
    # Canvas 上可能會畫超過一個圓角矩形（不會只保留最後一張蓋掉前面的）。
    if not hasattr(canvas, 'rounded_rect_image_refs'):
        canvas.rounded_rect_image_refs = {}
    canvas.rounded_rect_image_refs[item_id] = photo_image


FILTER_ICON_SIZE = 24   # 2026-08-17 第二次調整：右上角單獨一個圖示，比小小一顆
                        # 類型 Icon（ICON_SIZE=18）稍微放大一點，提高辨識度


def _new_filter_icon_image():
    """畫右上角的漏斗 Filter icon：白色細線條描邊、透明背景，跟其他既有
    Icon（screen_player.py 的 Overlay 按鈕、Recording 頁面圖示）用同一套
    ui_shapes.render_supersampled() 技巧——先在 4 倍尺寸畫完，再用
    LANCZOS 縮小一次，邊緣才會平滑，不會又出現鋸齒。"""
    def draw_callback(draw, big_width, big_height, scale):
        line_width = max(1, round(1.4 * scale))
        # 漏斗上寬下窄的梯形（跟 Type Icon 的線寬風格一致）
        draw.polygon([(big_width * 2 / 18, big_height * 3 / 18), (big_width * 16 / 18, big_height * 3 / 18),
                      (big_width * 11 / 18, big_height * 9 / 18), (big_width * 7 / 18, big_height * 9 / 18)],
                     outline=TEXT_PRIMARY_COLOR, width=line_width)
        # 漏斗下方的短柄
        draw.line([(big_width * 9 / 18, big_height * 9 / 18), (big_width * 9 / 18, big_height * 15 / 18)],
                  fill=TEXT_PRIMARY_COLOR, width=line_width)

    image = ui_shapes.render_supersampled(FILTER_ICON_SIZE, FILTER_ICON_SIZE, draw_callback)
    # 注意：這裡不放進 image_refs——那個 list 是給清單列的縮圖用，每次
    # render_visible_list() 都會被 clear_list() 清空；Filter icon 是常駐
    # 在 Top Bar 上的，靠模組層級的 filter_icon_image 這個全域變數保留
    # 參照就夠了（build_screen() 裡已經有 filter_icon_image = 這個函式
    # 的回傳值），兩種參照的生命週期不一樣，不能混在同一個 list 裡。
    return ImageTk.PhotoImage(image)


FILTER_ACTIVE_DOT_SIZE = 8   # 2026-08-19 使用者要求：漏斗右上角的 Active 提示點，不要太大


def _new_filter_active_dot_image():
    """Filter 漏斗右上角的小圓點，表示目前套用了非 All 的 Filter——不用
    打開 Menu 也能知道。跟其他圖示一樣用 render_supersampled() 做
    anti-alias，不用 Canvas 原生 create_oval() 在最終小尺寸上直接畫。"""
    def draw_callback(draw, big_width, big_height, scale):
        cx, cy = big_width / 2, big_height / 2
        radius = big_width * 0.42
        draw.ellipse((cx - radius, cy - radius, cx + radius, cy + radius), fill=ACCENT_COLOR)

    image = ui_shapes.render_supersampled(FILTER_ACTIVE_DOT_SIZE, FILTER_ACTIVE_DOT_SIZE, draw_callback)
    return ImageTk.PhotoImage(image)


FILTER_RADIO_ICON_SIZE = 14   # Filter Menu 選項前面的單選圓點，跟選單文字字級搭配的大小


def _new_radio_icon_image(selected):
    """Filter Menu 裡每個選項前面的單選圓點：選中畫實心圓，沒選中畫空心
    細線圓框。跟專案其他圖示一樣用 render_supersampled() 做 anti-alias，
    不用 Canvas 原生 create_oval() 在最終小尺寸上直接畫（鋸齒會很明顯）。"""
    def draw_callback(draw, big_width, big_height, scale):
        cx, cy = big_width / 2, big_height / 2
        radius = big_width * 0.38
        if selected:
            draw.ellipse((cx - radius, cy - radius, cx + radius, cy + radius), fill=TEXT_PRIMARY_COLOR)
        else:
            line_width = max(1, round(1.4 * scale))
            draw.ellipse((cx - radius, cy - radius, cx + radius, cy + radius),
                         outline=TEXT_PRIMARY_COLOR, width=line_width)

    image = ui_shapes.render_supersampled(FILTER_RADIO_ICON_SIZE, FILTER_RADIO_ICON_SIZE, draw_callback)
    return ImageTk.PhotoImage(image)


def create_type_icon(parent, category):
    """畫一個跟類型文字同一列的 Icon（Scenic/Event/Photo），用 Canvas 畫
    簡單的線條圖形，不直接用 Emoji——Emoji 在不同 Windows 字型/版本下
    粗細、對齊、有沒有彩色都不一致，畫面風格會跑掉；自己畫圖形才能保證
    跟其他 Dark theme 元件一樣的視覺風格，顏色也能跟現有的 Accent/警示色
    (screen_recording.ACTIVE_RED_COLOR) 對應一致。"""
    canvas = tk.Canvas(parent, width=ICON_SIZE, height=ICON_SIZE, bg=ROW_COLOR,
                       highlightthickness=0, cursor='hand2')
    if category == 'event':
        draw_event_icon(canvas)
    elif category == 'photo':
        draw_photo_icon(canvas)
    else:
        draw_scenic_icon(canvas)
    return canvas


ICON_LINE_WIDTH = 1.4  # 細線條輪廓的線寬，跟真機截圖比對過（2026-08-14）：
                        # HCP3 這幾個圖示都是描邊、不是實心填色


def draw_scenic_icon(canvas):
    """Scenic（一般錄影）：攝影機側面剪影，方形機身＋梯形鏡頭，細線條描邊
    （不是實心填色——2026-08-14 對照 HCP3 實機截圖調整，原本是實心，
    真機是細線輪廓）。"""
    canvas.create_rectangle(2, 5, 10, 13, outline=SCENIC_ICON_COLOR, width=ICON_LINE_WIDTH)
    canvas.create_polygon(10, 7, 15, 4, 15, 14, 10, 11,
                           outline=SCENIC_ICON_COLOR, fill='', width=ICON_LINE_WIDTH, joinstyle='round')


def draw_event_icon(canvas):
    """Event（事件觸發）：警示三角形外框＋驚嘆號（一條短線＋一個小圓點），
    細線條描邊。顏色借用 screen_recording 的 ACTIVE_RED_COLOR，這裡才讀
    （呼叫時兩邊模組都已經載入完成），不要在模組頂層讀（見上面
    SCENIC_ICON_COLOR 旁的說明，循環 import 會炸）。"""
    event_icon_color = screen_recording.ACTIVE_RED_COLOR
    canvas.create_polygon(9, 2, 16, 15, 2, 15,
                           outline=event_icon_color, fill='', width=ICON_LINE_WIDTH, joinstyle='round')
    canvas.create_line(9, 6.5, 9, 10.3, fill=event_icon_color, width=ICON_LINE_WIDTH)
    canvas.create_oval(8.4, 11.4, 9.6, 12.6, outline=event_icon_color, width=ICON_LINE_WIDTH)


def draw_photo_icon(canvas):
    """Photo（照片）：相機剪影，機身＋觀景窗凸起＋環狀鏡頭，細線條描邊。"""
    canvas.create_rectangle(2, 6, 16, 15, outline=PHOTO_ICON_COLOR, width=ICON_LINE_WIDTH)
    canvas.create_rectangle(6, 3, 11, 6, outline=PHOTO_ICON_COLOR, width=ICON_LINE_WIDTH)
    canvas.create_oval(6.5, 8.5, 11.5, 13.5, outline=PHOTO_ICON_COLOR, width=ICON_LINE_WIDTH)


# Swipe Delete 紅色區域裡的白色垃圾桶圖示，跟其他圖示一樣用
# ui_shapes.render_supersampled() 做 anti-alias（4x 畫完再 LANCZOS 縮小），
# 不用 Canvas 原生 create_oval()/create_rectangle() 在最終小尺寸上直接畫。
# 固定比例的圖，跟 filter_icon_image 一樣只需要畫一次，存成模組全域變數
# 重複使用，不用每次 Swipe 都重新畫。
trash_icon_image_normal = None
trash_icon_image_ready = None


def _new_trash_icon_image(size):
    def draw_callback(draw, big_width, big_height, scale):
        line_width = max(1, round(1.6 * scale))
        # 桶身
        draw.rectangle((big_width * 5 / 18, big_height * 6 / 18, big_width * 13 / 18, big_height * 16 / 18),
                       outline='white', width=line_width)
        # 桶蓋
        draw.line([(big_width * 3 / 18, big_height * 6 / 18), (big_width * 15 / 18, big_height * 6 / 18)],
                  fill='white', width=line_width)
        # 把手
        draw.line([(big_width * 7 / 18, big_height * 6 / 18), (big_width * 7 / 18, big_height * 4 / 18),
                   (big_width * 11 / 18, big_height * 4 / 18), (big_width * 11 / 18, big_height * 6 / 18)],
                  fill='white', width=line_width)
        # 桶身內的直條紋
        draw.line([(big_width * 7.5 / 18, big_height * 8 / 18), (big_width * 7.5 / 18, big_height * 14 / 18)],
                  fill='white', width=line_width)
        draw.line([(big_width * 9 / 18, big_height * 8 / 18), (big_width * 9 / 18, big_height * 14 / 18)],
                  fill='white', width=line_width)
        draw.line([(big_width * 10.5 / 18, big_height * 8 / 18), (big_width * 10.5 / 18, big_height * 14 / 18)],
                  fill='white', width=line_width)

    image = ui_shapes.render_supersampled(size, size, draw_callback)
    return ImageTk.PhotoImage(image)


def ensure_trash_icon_images_built():
    """只在第一次需要時畫一次（跟 ensure_thumbnail_workers_started() 同一種
    「只做一次」寫法），build_screen() 呼叫。"""
    global trash_icon_image_normal, trash_icon_image_ready
    if trash_icon_image_normal is not None:
        return
    trash_icon_image_normal = _new_trash_icon_image(TRASH_ICON_SIZE)
    trash_icon_image_ready = _new_trash_icon_image(TRASH_ICON_READY_SIZE)


def _build_checkbox_label_widget(row_content, item_key):
    """建立 Checkbox 本體（BooleanVar + 外觀 + click handler），不負責
    pack()——呼叫端決定要怎麼 pack（add_list_row() 第一次建立 row 時，
    Checkbox 是第一個 pack 進 row_content 的 child，直接 pack(side='left')
    就好；_show_checkbox_on_existing_row() 是動態加到「已經有其他 child
    在排」的既有 row_content，需要用 pack(before=thumbnail_label) 插入
    到正確位置，兩種情境的 pack 方式不一樣，所以建立本體這段抽出來共用，
    不是整支 add_list_row() 都重構）。

    2026-08-20 使用者要求 Batch Delete 離開 Selection Mode 不要 full
    rebuild：新增這支函式，把原本寫在 add_list_row() 裡的建立邏輯原封不動
    搬出來，方便之後 Incremental Diff 也能對「既有 row」動態加 Checkbox，
    不用整個 row_container 重建才能加/拿掉 Checkbox。"""
    checkbox_var = tk.BooleanVar(value=(item_key in checked_item_keys))
    checkbox = tk.Label(row_content, font=('Calibri', 14), bg=ROW_COLOR,
                         disabledforeground=TEXT_SECONDARY_COLOR, cursor='hand2')

    def refresh_checkbox_appearance():
        if checkbox_var.get():
            checkbox.config(text='☑', fg=ACCENT_COLOR)
        else:
            checkbox.config(text='☐', fg=TEXT_SECONDARY_COLOR)

    def handle_checkbox_click(event, key=item_key, var=checkbox_var):
        if is_gallery_busy:
            return
        var.set(not var.get())
        on_checkbox_toggled(key, var)
        refresh_checkbox_appearance()

    refresh_checkbox_appearance()
    checkbox.bind('<Button-1>', handle_checkbox_click)
    return checkbox


def _show_checkbox_on_existing_row(row_container):
    """對一個已經存在（不管目前可見還是隱藏）的 row，動態加上 Checkbox，
    不 destroy/重建整個 row_container。用在 Incremental Diff 偵測到
    is_batch_mode 從 False 變 True（進入 Selection Mode）時，對目前可見
    的每一列套用；也用在「原本隱藏的 row 被 Filter 切回來重新顯示」時，
    確保它的 Checkbox 狀態跟目前 is_batch_mode 一致（隱藏期間 is_batch_
    mode 可能已經變過，不能假設沿用隱藏當下的狀態）。

    Checkbox 一律插入到縮圖（gallery_row_thumbnail_label）前面，用
    pack(before=...)，不需要動到縮圖/文字/星號等其他既有 child 的 pack
    順序（Tk 的 pack before= 只影響新插入的這個 widget 自己的排列位置）。
    已經有 Checkbox 的 row 呼叫這裡是安全的 no-op（不會重複建立）。"""
    item_key = row_container.gallery_row_item_key
    if item_key in checkbox_widgets_by_item_key:
        return
    row_content = row_container.gallery_row_content
    thumbnail_label = row_container.gallery_row_thumbnail_label
    checkbox = _build_checkbox_label_widget(row_content, item_key)
    checkbox.pack(side='left', padx=(12, 4), before=thumbnail_label)
    checkbox_widgets_by_item_key[item_key] = checkbox


def _hide_checkbox_on_existing_row(row_container):
    """對一個已經存在的 row，把 Checkbox 拿掉（destroy），不 destroy/重建
    整個 row_container，其餘 child（縮圖/文字/星號）完全不受影響——Tk
    的 pack 管理器在其中一個 packed child 被 destroy 之後，會自動讓其餘
    仍在 pack 狀態的 sibling 維持原本的相對順序重新排版，不需要额外對
    其他 child 呼叫 pack()。用在 Incremental Diff 偵測到 is_batch_mode
    從 True 變 False（離開 Selection Mode）時，對目前可見的每一列套用。
    沒有 Checkbox 的 row 呼叫這裡是安全的 no-op。"""
    item_key = row_container.gallery_row_item_key
    checkbox = checkbox_widgets_by_item_key.pop(item_key, None)
    if checkbox is not None:
        checkbox.destroy()
    checked_item_keys.discard(item_key)


def add_list_row(group_items, place_immediately=True):
    """畫一整列：[Checkbox] + 縮圖 + 文字資訊，單欄直向排列。

    2026-08-20 使用者要求 Prototype「Gallery 增量更新」：place_immediately
    （預設 True，既有所有呼叫端都是這樣，行為完全不變）決定 row_container
    這個最外層 widget 建立完後要不要立刻 pack() 進 list_frame。增量更新
    需要先建立好這個 row widget、但不馬上決定它排在哪裡（因為最終順序要
    等 _try_incremental_diff_update() 把新增/刪除都處理完，統一
    對所有 row 依照最終順序重新 pack() 一次才會定案），所以傳
    place_immediately=False 時只建立、不 pack()，呼叫端自己決定何時/如何
    place。row_container 內部所有子 widget（Checkbox/縮圖/Icon/文字/
    Swipe 相關的 delete_area/trash_label）跟排版邏輯完全不受影響——這裡
    只是把「最外層 row_container 要不要現在就 pack 進 list_frame」這一個
    步驟抽出來給呼叫端決定，不是重構整支函式。

    回傳 row_container，方便呼叫端（增量更新）之後能直接操作/記錄這個
    widget 參照——既有呼叫端（render_visible_list() 的完整 rebuild 迴圈）
    忽略回傳值即可，不受影響。
    Card 固定寬度（不撐滿整個視窗，見 get_card_width()），靠左對齊。
    Checkbox 只有在批次選擇模式（is_batch_mode）才會建立/顯示；不是批次
    模式的話完全不畫這個 widget，不是畫出來但隱藏——這樣預設進入 Gallery
    才會真的「沒有 Checkbox」，不是留一個看不見的空位。
    Checkbox 是獨立的 widget、有自己的 command callback，跟縮圖/文字綁的
    <Button-1>（用來播放）是分開的兩件事，不會互相觸發；Favorite 星號
    也是獨立 widget、獨立 callback（單筆 Favorite/Unfavorite），一樣不會
    跟播放或 Checkbox 互相觸發。

    2026-08-19 使用者要求新增 Normal Mode 的 Swipe-to-Delete：Row 的實際
    內容（Checkbox/縮圖/文字，以下都稱 row_content）現在是疊在
    row_container 上面的一層，用 place() 定位（不是 pack()），這樣才能在
    使用者向左拖曳時整個一起水平移動，不用重新排版；row_container 底層
    還有一個一直存在、跟 row_container 同寬同高的紅色 delete_area（先建立，
    z-order 最底層）+ Trash icon Label（次底層，初始隱藏），row_content
    向左移開多少，紅色區域自然就露出多少——不需要另外計算/重畫紅色區域
    本身的寬度。row_container 仍然照原本方式用 pack() 排進 list_frame，
    垂直捲動清單的排版邏輯完全沒變。"""
    representative_item = api.get_group_representative(group_items)
    # 2026-08-14 使用者確認規則：Checkbox/縮圖快取這種本地 UI 追蹤用途一律
    # 用 item_key，不能只用 groupid——理由見上面 checked_item_keys 等變數
    # 宣告處的說明。2026-08-20 改用 api.compute_item_key()（不再單純等於
    # fileid，理由同樣見上面的說明）。groupid 本身（Video Grouping/Player/
    # Delete/Favorite 等既有功能的識別碼）完全沒有被拿掉，只是 2026-08-19
    # 使用者要求 Row UI 不再顯示「Group: xxx」這種 internal debug
    # identifier，這裡不用再另外存一份區域變數。
    item_key = api.compute_item_key(group_items)

    card_width = get_card_width()
    card_height = THUMBNAIL_SIZE[1] + 8  # 縮圖高度＋上下 pady，卡片高度跟著縮圖走

    row_container = tk.Frame(list_frame, bg=ROW_COLOR, width=card_width, height=card_height)
    row_container.pack_propagate(False)  # 固定寬高，不要被裡面的內容撐大/縮小
    # 2026-08-17 第四次調整：改用固定 margin（GALLERY_ROW_HORIZONTAL_MARGIN）
    # 靠左對齊，不再用「置中」湊出左右對稱留白——Row 寬度已經是
    # 「available_width - 2*margin」，anchor='w' + 左邊 padx=margin 會讓
    # Row 左緣跟 status_label 的左邊距對齊（兩者用同一個 margin 數字），
    # 右緣自然落在「視窗右緣 - margin」，不用另外算置中座標。
    if place_immediately:
        row_container.pack(fill='none', anchor='w', padx=(GALLERY_ROW_HORIZONTAL_MARGIN, GALLERY_ROW_HORIZONTAL_MARGIN), pady=4)

    # ---- 底層：紅色 Delete Area（先建立，z-order 最底層，永遠不用重畫，
    # 只是被上面的 row_content 蓋住/露出多少而已）+ Trash icon（次底層，
    # 初始隱藏，寬度不夠時不顯示，見 update_row_swipe_visual()）----
    delete_area = tk.Frame(row_container, bg=DELETE_AREA_COLOR)
    delete_area.place(x=0, y=0, relwidth=1, relheight=1)
    trash_label = tk.Label(row_container, bg=DELETE_AREA_COLOR, bd=0, highlightthickness=0)
    # 初始不 place()，維持隱藏，等真的開始拖曳且露出寬度夠了才顯示。

    # ---- 前景：row_content，實際內容全部畫在這裡面，整個用 place() 疊在
    # row_container 上面（跟 row_container 同寬同高，x=0 是靜止狀態）----
    row_content = tk.Frame(row_container, bg=ROW_COLOR, width=card_width, height=card_height)
    row_content.place(x=0, y=0, relwidth=1, relheight=1)

    # 用簡單的 widget 屬性記住這幾個關聯物件（item_key/row_content/
    # trash_label），純粹方便之後用 item_key 反查對應的 widget（例如
    # 回歸測試要模擬 Swipe 手勢），不影響任何顯示/互動邏輯本身。
    row_container.gallery_row_item_key = item_key
    row_container.gallery_row_content = row_content
    row_container.gallery_row_trash_label = trash_label

    # ---- Checkbox（只有批次選擇模式才會建立）：跟整列垂直置中，只改變
    # 選取狀態，不會觸發播放、不會切頁。只有勾選時才顯示黃色 Accent，
    # 跟 Favorite 星號是兩件完全獨立的事：★ 只看
    # representative_item.is_favorite（DVR 回傳），Checkbox 只看
    # checked_item_keys（目前 Batch Action 的選取狀態），互不影響。
    # 2026-08-20 使用者要求 Batch Delete 離開 Selection Mode 不要 full
    # rebuild：實際建立 Checkbox 的邏輯抽到 _build_checkbox_label_widget()，
    # 這裡只負責第一次建立 row 時要不要建、pack 在哪個位置（第一個 pack
    # 進 row_content 的 child，不需要 before=）；_show_checkbox_on_existing_
    # row()/_hide_checkbox_on_existing_row() 共用同一份建立邏輯，用在
    # Incremental Diff 對「既有 row」動態顯示/隱藏 Checkbox，不重建整個
    # row_container。----
    if is_batch_mode:
        checkbox = _build_checkbox_label_widget(row_content, item_key)
        checkbox.pack(side='left', padx=(12, 4))
        checkbox_widgets_by_item_key[item_key] = checkbox

    # ---- 縮圖 + 文字：點這些才會播放 ----
    # 2026-08-14 使用者確認規則：Thumbnail 改成 Lazy Loading。
    # current_thumbnails_by_item_key 有這個 item_key 才代表下載過（值可能
    # 是 bytes，也可能是 None——None 代表「試過但沒有縮圖/下載失敗」，一樣
    # 不用再下載）；沒有這個 key 才是真的還沒下載過，先顯示 Loading 佔位，
    # 註冊進 pending_thumbnail_labels，讓可視範圍檢查排進下載佇列，下載
    # 完成後由 on_thumbnail_downloaded() 原地更新這個 Label，不整個重畫。
    thumbnail_width, thumbnail_height = THUMBNAIL_SIZE
    is_cache_hit_row = item_key in current_thumbnails_by_item_key
    if is_cache_hit_row:
        # 2026-08-20 使用者回報第二次進 Gallery 的 cache-hit rows 異常慢
        # （平均約 0.61s/row），要求拆開量：cache lookup -> Image.open/
        # decode -> resize -> ImageTk.PhotoImage -> Label create/config ->
        # pack，逐一計時、逐筆印出，不直接假設是 PIL decode 本身。這裡只
        # 加量測，前後的行為（哪個分支、顯示什麼）完全不變。
        t_row_thumbnail_start = perf_counter()

        t_cache_lookup_start = perf_counter()
        cached_thumbnail_bytes = current_thumbnails_by_item_key[item_key]
        cache_lookup_duration = perf_counter() - t_cache_lookup_start

        step_timings = []
        thumbnail_image = build_thumbnail_image(cached_thumbnail_bytes, step_timings=step_timings)
        step_durations = {label: duration for label, duration in step_timings}

        t_label_create_start = perf_counter()
        if thumbnail_image is not None:
            thumbnail_label = tk.Label(row_content, image=thumbnail_image, bg=ROW_COLOR, cursor='hand2')
        else:
            thumbnail_label = tk.Label(row_content, text='No Thumbnail',
                                        width=thumbnail_width // 8, height=thumbnail_height // 16,
                                        bg=CONTROL_COLOR, fg=TEXT_PRIMARY_COLOR, cursor='hand2')
        label_create_duration = perf_counter() - t_label_create_start
    else:
        thumbnail_label = tk.Label(row_content, text='Loading...',
                                    width=thumbnail_width // 8, height=thumbnail_height // 16,
                                    bg=CONTROL_COLOR, fg=TEXT_SECONDARY_COLOR, cursor='hand2')
        pending_thumbnail_labels[item_key] = (thumbnail_label, representative_item)
        _thumbnail_diag_log(item_key, 'Row created, 尚未下載過，顯示 Loading，已註冊進 pending_thumbnail_labels')

    t_pack_start = perf_counter()
    thumbnail_label.pack(side='left', padx=(0, 14), pady=4)
    pack_duration = perf_counter() - t_pack_start

    # 2026-08-20 使用者要求 Batch Delete 離開 Selection Mode 不要 full
    # rebuild：之後要動態在既有 row 上加/移除 Checkbox 時，需要用
    # pack(before=thumbnail_label) 插入到縮圖前面（Checkbox 一律排在縮圖
    # 左邊），不需要動到縮圖/文字等其他既有 child 的 pack 順序，這裡先
    # 存好參照，見 _show_checkbox_on_existing_row()。
    row_container.gallery_row_thumbnail_label = thumbnail_label

    if is_cache_hit_row:
        row_thumbnail_total_duration = perf_counter() - t_row_thumbnail_start
        open_duration = step_durations.get('Image.open', 0.0)
        resize_duration = step_durations.get('resize', 0.0)
        photoimage_duration = step_durations.get('ImageTk.PhotoImage', 0.0)
        print(f'[Gallery Perf Row] item_key={item_key}: cache_lookup={cache_lookup_duration:.4f}s, '
              f'Image.open={open_duration:.4f}s, resize={resize_duration:.4f}s, '
              f'ImageTk.PhotoImage={photoimage_duration:.4f}s, label_create={label_create_duration:.4f}s, '
              f'pack={pack_duration:.4f}s, row_total={row_thumbnail_total_duration:.4f}s')
        _row_cache_hit_perf_samples.append({
            'item_key': item_key,
            'cache_lookup': cache_lookup_duration,
            'Image.open': open_duration,
            'resize': resize_duration,
            'ImageTk.PhotoImage': photoimage_duration,
            'label_create': label_create_duration,
            'pack': pack_duration,
            'row_total': row_thumbnail_total_duration,
        })

    info_frame = tk.Frame(row_content, bg=ROW_COLOR, cursor='hand2')
    info_frame.pack(side='left', fill='both', expand=True, pady=4)

    clickable_widgets = [thumbnail_label, info_frame]

    # 2026-08-19 使用者要求移除 File/Group 這種 internal debug identifier
    # 之後，Photo Row 只剩兩行文字（比 Video 少一行 Duration），如果照舊
    # 讓這幾行文字直接從 info_frame 最上方開始排，行數變少後底下會明顯
    # 留白，跟旁邊固定高度的縮圖比起來視覺不平衡。這裡改成先包進一個
    # 獨立的 info_text_stack，用 pack(fill='x', expand=True)（刻意不
    # fill='y'/'both'）——Tk 的 pack 在 expand=True 又沒有把某個方向填滿
    # 時，會把這個 widget 在那個方向的可用空間內置中，Video（3 行）／
    # Photo（2 行）兩種行數都適用，不用個別特殊處理。
    info_text_stack = tk.Frame(info_frame, bg=ROW_COLOR, cursor='hand2')
    info_text_stack.pack(fill='x', expand=True)
    clickable_widgets.append(info_text_stack)

    # ---- 第一行：類型 Icon + 類型文字（左）、Favorite ☆/★（固定最右側） ----
    type_row = tk.Frame(info_text_stack, bg=ROW_COLOR, cursor='hand2')
    type_row.pack(fill='x', anchor='w')
    clickable_widgets.append(type_row)

    # Favorite 星號固定在最右側，是獨立的收藏切換按鈕，不算播放用的
    # clickable_widgets，也不受批次選擇模式影響——單筆操作跟批次操作是
    # 兩種互不相關的路徑。
    is_favorite = representative_item.is_favorite
    star_label = tk.Label(type_row, text=('★' if is_favorite else '☆'), font=('Calibri', 20, 'bold'),
                           bg=ROW_COLOR, fg=(FAVORITE_STAR_COLOR if is_favorite else TEXT_SECONDARY_COLOR),
                           cursor='hand2')
    # 2026-08-17 第四次調整：Row 拉寬到接近視窗右緣之後，Star 跟著調整內縮
    # 距離（12px -> 20px），視覺上才不會貼著新的 Row 右邊界。
    star_label.pack(side='right', padx=(0, 20))
    star_label.bind('<Button-1>', lambda event, group=group_items, label=star_label: on_star_clicked(group, label))

    type_icon = create_type_icon(type_row, api.get_category(representative_item))
    type_icon.pack(side='left', padx=(0, 6))
    clickable_widgets.append(type_icon)

    type_label = tk.Label(type_row, text=api.get_display_type_text(representative_item), anchor='w',
                           font=('Calibri', 13, 'bold'), bg=ROW_COLOR, fg=TEXT_PRIMARY_COLOR)
    type_label.pack(side='left')
    clickable_widgets.append(type_label)

    # ---- 次要資訊：Trigger Time 自己一行；Video 多一行 Duration ----
    # 2026-08-19 使用者要求：Row 不再顯示 File: xxx / Group: xxx 這種
    # internal debug identifier，只保留使用者看得懂的資訊——Video 留
    # Duration，Photo 不需要為了排版硬補第三行文字，直接少一行。字體大小
    # 維持不變（Calibri 9），只把顏色從 TEXT_SECONDARY_COLOR 換成稍亮的
    # GALLERY_ROW_META_TEXT_COLOR，提高可讀性，但仍明顯比類型標題暗，
    # 不搶 Scenic/Event/Photo 的視覺焦點。fileid/groupid 本身完全沒有被
    # 拿掉，Delete/Favorite/Selection key 仍然用既有的 item_key（fileid）/
    # groupid，這裡只是不再把它們印在畫面上。
    date_label = tk.Label(info_text_stack, text=api.get_time_text(representative_item), anchor='w',
                           font=('Calibri', 9), bg=ROW_COLOR, fg=GALLERY_ROW_META_TEXT_COLOR, cursor='hand2')
    date_label.pack(fill='x', anchor='w')
    clickable_widgets.append(date_label)

    if not api.is_photo_recording(representative_item):
        duration_text = f'Duration: {api.get_group_duration_text(group_items)}'
        duration_label = tk.Label(info_text_stack, text=duration_text, anchor='w',
                                   font=('Calibri', 9), bg=ROW_COLOR, fg=GALLERY_ROW_META_TEXT_COLOR,
                                   cursor='hand2')
        duration_label.pack(fill='x', anchor='w')
        clickable_widgets.append(duration_label)

    # 2026-08-17 HCP3 操作流程改版：短按維持既有行為（Normal Mode 播放/
    # 開照片，Selection Mode 改成 toggle 這一列的 Checkbox）；長按（按住
    # 超過 LONG_PRESS_THRESHOLD_MS 沒放開）才進入 Selection Mode。用
    # ButtonPress/ButtonRelease + after() 計時器判斷，不是改寫原本的
    # on_row_click()，見 on_row_press()/on_row_release() 的說明。
    #
    # 2026-08-19 新增 <B1-Motion>：判斷水平向左拖曳是不是 Swipe Delete，
    # 見 on_row_motion() 的說明。三個 handler 共用同一組 clickable_widgets，
    # 不用另外綁一套。
    for widget in clickable_widgets:
        widget.bind('<ButtonPress-1>', lambda event, key=item_key, group=group_items, rc=row_content,
                    tl=trash_label, cw=card_width: on_row_press(event, key, group, rc, tl, cw))
        widget.bind('<B1-Motion>', lambda event, key=item_key: on_row_motion(event, key))
        widget.bind('<ButtonRelease-1>', lambda event, key=item_key, group=group_items: on_row_release(event, key, group))

    return row_container


def build_thumbnail_image(thumbnail_bytes, step_timings=None):
    """download_thumbnail_safely() 只確保下載本身不會讓整個清單掛掉，但沒
    保證下載回來的 bytes 一定是合法圖片——DVR 偶爾會回傳損毀/不完整的資料。
    這裡如果解碼失敗一樣要接住，不然會在 render_visible_list() 畫到一半
    中斷，讓 is_refreshing/is_gallery_busy 卡在 busy 狀態，整個畫面卡死
    進不去。失敗一律當作沒有縮圖處理（畫面顯示 No Thumbnail）。

    2026-08-20 使用者回報：第二次進 Gallery（cache_hit=11）比第一次
    （cache_hit=0）多花約 6.7 秒，懷疑跟這裡的 PIL 處理有關，但
    on_thumbnail_downloaded() 那條路徑量到的 decode 耗時只有 2~4ms，
    差距太大，要求拆開每個步驟細看，不要直接假設就是 PIL decode 本身。
    step_timings（可選）：呼叫端傳一個 list 進來，這裡會依序 append
    ('Image.open', duration) / ('resize', duration) /
    ('ImageTk.PhotoImage', duration) 三筆——只用於這次的細部 Profiling，
    不傳的話（None，既有所有呼叫端，包括 on_thumbnail_downloaded()，都是
    這樣呼叫）完全不影響行為/回傳值。

    注意：PIL 的 Image.open() 是 lazy 的，只讀檔頭、不會真的解碼像素資料
    ——真正的解碼動作是在第一次需要存取像素資料時才觸發（這裡是
    image.resize() 這一行），所以下面 step_timings 記錄的 'Image.open'
    耗時預期會接近 0，'resize' 那筆才是真正的「decode + resize」耗時，
    兩者不能直接照字面意思各自看待，這裡先如實記錄、不做任何合併/美化，
    避免藏起真正的耗時分佈。"""
    if thumbnail_bytes is None:
        return None

    def record_step(label, step_start_time):
        if step_timings is not None:
            step_timings.append((label, perf_counter() - step_start_time))

    try:
        t_open_start = perf_counter()
        image = Image.open(BytesIO(thumbnail_bytes))
        record_step('Image.open', t_open_start)

        t_resize_start = perf_counter()
        image = image.resize(THUMBNAIL_SIZE)
        record_step('resize', t_resize_start)

        t_photoimage_start = perf_counter()
        photo_image = ImageTk.PhotoImage(image)
        record_step('ImageTk.PhotoImage', t_photoimage_start)
    except Exception as error:
        print(f'[Gallery] Thumbnail decode failed: {error}')
        return None
    image_refs.append(photo_image)  # 保留參照，避免圖片被回收
    return photo_image


# ============================================================
# Thumbnail Lazy Loading：只下載目前 Canvas 可視範圍看得到的縮圖
# ============================================================
# 2026-08-14 使用者確認規則後實作，真機量測過 Thumbnail download 佔了
# Gallery Refresh 94.9% 的時間，改成：
#   1. Row 建立時不等縮圖，還沒 cache 過的先顯示 Loading 佔位
#   2. 週期性檢查 Canvas 可視範圍（GALLERY_VISIBILITY_CHECK_INTERVAL_MS，
#      只做「判斷可見 + 排進佇列」，不下載、不 GET recordings、不整個重畫）
#   3. 固定 THUMBNAIL_WORKER_COUNT 個背景 worker 從佇列拿出來下載，
#      避免一次對 DVR 開大量 request，也不是每個 row 各開一個 thread
#   4. 下載完成只更新那一個 row 的 Label，不整個重畫清單

def is_row_visible(widget):
    """判斷這個 widget 目前是不是在 list_canvas 的可視範圍內。直接比較
    螢幕座標（winfo_rooty()）最簡單可靠，不用管這個 widget 巢狀包在
    row_frame/list_frame 裡面第幾層，也不用換算 Canvas 內部座標系。"""
    if not widget.winfo_exists():
        return False
    canvas_top = list_canvas.winfo_rooty()
    canvas_bottom = canvas_top + list_canvas.winfo_height()
    widget_top = widget.winfo_rooty()
    widget_bottom = widget_top + widget.winfo_height()
    return widget_bottom >= canvas_top and widget_top <= canvas_bottom


def queue_visible_thumbnail_downloads():
    """檢查目前哪些「還沒下載過」的 row 進入可視範圍，排進下載佇列。
    只做判斷 + 排隊這兩件事，不在這裡下載、不 GET recordings、不整個
    重畫 UI——這個函式會被週期性呼叫（GALLERY_VISIBILITY_CHECK_INTERVAL_MS），
    要維持很輕量。

    2026-08-20 使用者要求優化（真機量測：118 筆時 pending_count 尖峰約
    90，未來 4000 筆時可能長期停在數千筆）：原本不管畫面有沒有變化，
    每 200ms 都逐一對 pending_thumbnail_labels 裡「所有」還沒下載過的
    row 呼叫 is_row_visible()（實際上是問 Tk 要 winfo_rooty()），是
    O(pending_count)，且即使使用者完全沒動也照樣全掃一次。改成兩層
    過濾，不改變 tick 頻率本身（仍然是 GALLERY_VISIBILITY_CHECK_INTERVAL_MS
    週期性呼叫，不是 event-driven）：

    1. 先比對這次的 canvas.yview() 跟上次檢查時的值——完全一樣就直接
       return，不碰任何 widget、不呼叫任何 winfo_rooty()，也不掃描
       pending_thumbnail_labels（見 _visibility_check_last_yview 的
       說明）。
    2. yview 真的變了（或是剛重畫過、cache 被 render_visible_list()
       重置成 None，保證第一次一定會掃），才用 _current_row_order
       （跟畫面顯示順序一致的 item_key 清單）配合 yview 的 fraction
       概算目前大概第幾列到第幾列在畫面上——每一列高度是固定常數
       （card_height），這個概算法本身就準；GALLERY_VISIBILITY_
       SCAN_BUFFER_ROWS 再往外多抓幾列當緩衝，避免快速捲動時剛好卡在
       邊界的縮圖來不及排入。只對這個範圍內的 item_key 做「是不是還在
       pending、有沒有已經排隊」的字典查詢（O(1)），範圍以外還有幾千筆
       在 pending_thumbnail_labels 裡完全不會被摸到，也不呼叫任何
       winfo_rooty()。

    2026-08-20 實測發現（_thumbnail_retry_regression_check.py 這支既有
    回歸測試抓到）：一開始只用「yview 有沒有變」當唯一的跳過條件，會讓
    「下載失敗、還在重試額度內、但使用者完全沒有捲動」這種情況永遠卡住
    ——重試需要靠這個函式被重新呼叫才會繼續，yview 沒變就整個 return
    的話，重試會停在半途，要等使用者剛好捲一下才會繼續。已經修正成：
    只要 thumbnail_retry_counts 非空（有任何項目正在重試中），就不套用
    「yview 沒變就跳過」這條早退邏輯，強制繼續往下做 viewport+buffer
    範圍掃描（不會回退成掃全部 pending_thumbnail_labels，重試期間的
    每次檢查成本一樣是 O(viewport+buffer)，不隨總筆數增加）。"""
    global _visible_batch_start_time, _visible_batch_pending_count
    global _thumbnail_perf_samples, _thumbnail_perf_count, _thumbnail_perf_total_time
    global _thumbnail_perf_max_time, _thumbnail_perf_batch_start
    global _visibility_check_last_yview

    if not pending_thumbnail_labels:
        return  # 沒有任何 row 在等縮圖，不用做事——這個分支不印 log，這個函式是週期性呼叫（每 200ms），沒事做的情況會很頻繁，全部印出來只會洗版

    # 2026-08-20 實測發現（_thumbnail_retry_regression_check.py 回歸測試
    # 抓到）：如果有項目下載失敗、還在 THUMBNAIL_MAX_RETRY_COUNT 重試額度
    # 內（thumbnail_retry_counts 非空），不能因為 yview 沒變就跳過——這種
    # 情況下使用者可能完全沒有捲動，但重試還是要能自動繼續進行，不能變成
    # 要使用者自己捲一下才會重試。這裡只是不套用「yview 沒變就跳過」這個
    # 早退邏輯，範圍掃描本身還是一樣只掃 viewport+buffer（不是回退成掃全部
    # pending_thumbnail_labels），重試期間的成本還是有界、不會隨總筆數增加。
    has_pending_retries = bool(thumbnail_retry_counts)

    current_yview = list_canvas.yview()
    if not has_pending_retries and _visibility_check_last_yview is not None and current_yview == _visibility_check_last_yview:
        return  # 跟上次檢查時完全一樣（沒有捲動、畫面也沒重建），也沒有正在重試中的項目，不需要重新掃描
    _visibility_check_last_yview = current_yview

    total_rows = len(_current_row_order)
    if total_rows == 0:
        return  # 理論上不會發生（有 pending 代表應該有 row），防禦性寫法

    _gallery_render_diag_log('queue_visible_thumbnail_downloads', f'pending_count={len(pending_thumbnail_labels)}')

    first_fraction, last_fraction = current_yview
    start_index = max(0, math.floor(first_fraction * total_rows) - GALLERY_VISIBILITY_SCAN_BUFFER_ROWS)
    end_index = min(total_rows - 1, math.ceil(last_fraction * total_rows) + GALLERY_VISIBILITY_SCAN_BUFFER_ROWS)

    newly_queued_count = 0
    for index in range(start_index, end_index + 1):
        item_key = _current_row_order[index]
        pending_entry = pending_thumbnail_labels.get(item_key)
        if pending_entry is None:
            continue  # 這個 index 對應的項目已經下載過/不是還在等待中的那種
        if item_key in requested_item_keys:
            continue  # 已經排隊/下載中，不要重複排（防重複下載）
        thumbnail_label, representative_item = pending_entry
        requested_item_keys.add(item_key)
        # 2026-08-19 使用者要求 UI 狀態要區分「尚未排入 Lazy Loader」
        # （Loading，還在等可視範圍檢查排到它）跟「已經排隊/下載中」
        # （Pending）——原本兩者文字一樣都是 Loading，這裡排入佇列的當下
        # 立刻改字，不用等下載完成才更新畫面。
        if thumbnail_label.winfo_exists():
            thumbnail_label.config(text='Pending...')
        media_type = 'photo' if api.is_photo_recording(representative_item) else 'video'
        attempt_number = thumbnail_retry_counts.get(item_key, 0) + 1
        _thumbnail_diag_log(item_key, f'進入可視範圍，已排入下載佇列，顯示 Pending '
                                       f'(media_type={media_type}, attempt={attempt_number}/{THUMBNAIL_MAX_RETRY_COUNT})')
        _thumbnail_download_queue.put((item_key, representative_item))
        newly_queued_count += 1

    if newly_queued_count > 0:
        if _visible_batch_pending_count == 0:
            _visible_batch_start_time = time()
            # 2026-08-20 新增：這是新的一批（上一批已經全部清空），重置
            # Thumbnail 前 N 筆的個別 timing 樣本＋累計統計，這樣「thumbnail
            # queue 開始時間」/「前 20 張耗時」量的才是這一批，不會被上一批
            # 殘留的數字污染。
            _thumbnail_perf_samples = []
            _thumbnail_perf_count = 0
            _thumbnail_perf_total_time = 0.0
            _thumbnail_perf_max_time = 0.0
            _thumbnail_perf_batch_start = perf_counter()
            if _gallery_load_perf_context is not None:
                batch_start_offset = _thumbnail_perf_batch_start - _gallery_load_perf_context['total_start']
                print(f'[Gallery Perf] 5. Thumbnail queue 開始（新的一批可視縮圖）: start=+{batch_start_offset:.3f}s')
            else:
                print('[Gallery Perf] 5. Thumbnail queue 開始（新的一批可視縮圖，非完整 Load 觸發，例如切 Filter/Scroll）')
        _visible_batch_pending_count += newly_queued_count
        print(f'[Gallery Perf] Lazy Load 排入新一批可視縮圖下載: {newly_queued_count} 筆 '
              f'(這一批目前還在等待中的張數={_visible_batch_pending_count})')


def ensure_thumbnail_workers_started():
    """固定開 THUMBNAIL_WORKER_COUNT 個背景 worker，只在第一次需要時啟動
    一次，之後重複呼叫不會再開新的——不是每個 row 各開一個 thread，也不會
    一次對 DVR 開大量 request。"""
    global _thumbnail_workers_started
    if _thumbnail_workers_started:
        return
    _thumbnail_workers_started = True
    for _ in range(THUMBNAIL_WORKER_COUNT):
        thread = threading.Thread(target=_thumbnail_download_worker, daemon=True)
        thread.start()


def _thumbnail_download_worker():
    """背景執行緒（固定只有 THUMBNAIL_WORKER_COUNT 個）：從
    _thumbnail_download_queue 拿 item_key 出來下載縮圖，完成後排回主執行緒
    更新畫面。佇列是空的時候 queue.get() 會阻塞等待，不會忙等。"""
    while True:
        item_key, representative_item = _thumbnail_download_queue.get()
        _thumbnail_diag_log(item_key, 'download started（背景 worker 從佇列取出，即將呼叫 download_thumbnail_safely()）')
        download_start = perf_counter()
        thumbnail_bytes = download_thumbnail_safely(representative_item, item_key)
        download_duration = perf_counter() - download_start
        list_frame.after(0, lambda key=item_key, data=thumbnail_bytes, dl=download_duration:
                          on_thumbnail_downloaded(key, data, dl))
        _thumbnail_download_queue.task_done()


def on_thumbnail_downloaded(item_key, thumbnail_bytes, download_duration=0.0):
    """主執行緒：縮圖下載完成，寫入 cache，如果這一列還存在畫面上就原地
    更新這一個 Label 的圖片，不整個重畫清單（重畫會讓捲動位置跳掉）。

    2026-08-20 使用者要求量測每張縮圖的 download/decode/apply 耗時：
    download_duration 是呼叫端（_thumbnail_download_worker()，背景執行緒）
    量到的下載耗時，這裡量的是 decode（build_thumbnail_image() 的
    Image.open()+resize()+ImageTk.PhotoImage()，PIL 操作）+ apply（
    thumbnail_label.config() 套用到畫面）這兩段，兩段都在主執行緒執行。
    只有真的走到最後套用 UI 那幾行才會記錄一筆樣本——重試中（下載失敗但
    還沒到 THUMBNAIL_MAX_RETRY_COUNT）、或這一列已經被篩選掉/銷毀重畫過
    這幾種提早 return 的情況，decode/apply 根本沒有執行，不算一筆完整的
    樣本，避免統計失真。

    2026-08-19 使用者回報 Regression 修正：下載失敗（thumbnail_bytes 是
    None）不再直接永久判定 No Thumbnail——失敗次數還沒到
    THUMBNAIL_MAX_RETRY_COUNT 之前，維持 pending 狀態（不寫進
    current_thumbnails_by_item_key，也不動 pending_thumbnail_labels），
    只從 requested_item_keys 移除，讓它能在下一次可視範圍檢查
    （queue_visible_thumbnail_downloads()）被重新排隊，等於自動重試。
    連續失敗次數真的達到上限才寫進 cache、正式顯示 No Thumbnail。

    2026-08-20 使用者真機回報「偶發 Thumbnail 下載失敗」排查：這裡加了
    decode_result/cache_result/item_still_visible/UI callback applied-
    ignored/final_failure_reason 這幾個診斷欄位，純記錄，不改行為。
    排查過程中發現一個目前程式碼可以確認的潛在缺口（要真機 log 才能
    證實是不是這次回報的實際成因）：retry 計數只看 download_thumbnail_
    safely() 有沒有回傳 None（HTTP/下載層失敗），如果下載「成功」拿到
    bytes、但 build_thumbnail_image() 解碼那些 bytes 失敗（例如 DVR
    回傳不完整/損毀的圖片資料），目前會直接落到下面 else 分支當作
    「成功」寫入 current_thumbnails_by_item_key（永久 cache），完全不會
    被 THUMBNAIL_MAX_RETRY_COUNT 這套重試機制重試——這種情況下畫面上一樣
    會顯示 No Thumbnail，但重試次數其實是 0 次，不是 3 次。這一輪先只加
    log 標記出這個情況（見下面 decode 失敗但 download 成功那個分支），
    不改重試邏輯。"""
    global _visible_batch_pending_count, _visible_batch_start_time

    final_failure_reason = None
    if thumbnail_bytes is None:
        retry_count = thumbnail_retry_counts.get(item_key, 0) + 1
        if retry_count <= THUMBNAIL_MAX_RETRY_COUNT:
            thumbnail_retry_counts[item_key] = retry_count
            requested_item_keys.discard(item_key)
            _thumbnail_diag_log(item_key, f'下載失敗，第 {retry_count}/{THUMBNAIL_MAX_RETRY_COUNT} 次，'
                                           f'維持 pending，等下一次可視範圍檢查重新排隊')
            return
        thumbnail_retry_counts.pop(item_key, None)
        final_failure_reason = 'download_failed_after_max_retry'
        _thumbnail_diag_log(item_key, f'重試 {THUMBNAIL_MAX_RETRY_COUNT} 次仍失敗，'
                                       f'final_failure_reason={final_failure_reason}，判定為正式 No Thumbnail')
    else:
        thumbnail_retry_counts.pop(item_key, None)
        _thumbnail_diag_log(item_key, f'download 成功（{len(thumbnail_bytes)} bytes），cache_result=pending_decode')

    current_thumbnails_by_item_key[item_key] = thumbnail_bytes
    requested_item_keys.discard(item_key)
    _thumbnail_diag_log(item_key, 'cache_result=written（已寫入 current_thumbnails_by_item_key，之後不會再重新下載）')

    # 2026-08-20 使用者要求量測每張縮圖耗時：這裡只先算「這一批是不是在
    # 這次呼叫用完」，先不要在這裡就印統計/Summary——這個當下這一筆
    # （通常是這一批最後一筆）自己的 decode/apply 都還沒跑、還沒呼叫
    # _record_thumbnail_perf_sample()，太早印會少算這一筆（實測抓到：印出
    # 的 count 會比實際張數少 1，因為批次完成判斷發生在 decode/apply 之
    # 前）。改成先記下「這次是不是批次完成」，等這一筆真的跑完 decode/
    # apply/record_sample 之後（不管是正常路徑，還是提早 return 的那幾種
    # 情況），再統一印統計/Summary，見下面的 _finish_batch_if_needed()。
    batch_just_completed = False
    if _visible_batch_pending_count > 0:
        _visible_batch_pending_count -= 1
        if _visible_batch_pending_count == 0 and _visible_batch_start_time is not None:
            elapsed = time() - _visible_batch_start_time
            print(f'[Gallery Perf] 這一批可視縮圖全部載入完成: {elapsed:.3f}s')
            _visible_batch_start_time = None
            batch_just_completed = True

    def _finish_batch_if_needed():
        if batch_just_completed:
            _print_thumbnail_perf_aggregate('整批')
            _maybe_print_gallery_load_summary()

    pending_entry = pending_thumbnail_labels.pop(item_key, None)
    if pending_entry is None:
        _thumbnail_diag_log(item_key, 'UI callback=ignored（這一列的 pending 註冊已經不在，'
                                       '可能已經被重新整理/篩選掉了，只更新 cache，不用動任何 widget）')
        _finish_batch_if_needed()
        return  # 這一列可能已經被重新整理/篩選掉了，只更新 cache，不用動任何 widget
    thumbnail_label, _representative_item = pending_entry
    if not thumbnail_label.winfo_exists():
        _thumbnail_diag_log(item_key, 'UI callback=ignored（Row widget 已經被銷毀重畫過了）')
        _finish_batch_if_needed()
        return  # Row 已經被銷毀重畫過了

    item_still_visible = is_row_visible(thumbnail_label)
    decode_start = perf_counter()
    thumbnail_image = build_thumbnail_image(thumbnail_bytes)
    decode_duration = perf_counter() - decode_start
    if thumbnail_bytes is not None and thumbnail_image is None:
        # 見函式開頭的說明：download 成功但 decode 失敗，目前不會被計入
        # 重試次數，這裡明確記錄下來，方便真機重現時對照。
        final_failure_reason = 'decode_failed_after_download_success'
        _thumbnail_diag_log(item_key, f'⚠ decode_result=failed，但 download 是成功的（{len(thumbnail_bytes)} bytes）'
                                       f'——這個情況目前不會被 THUMBNAIL_MAX_RETRY_COUNT 重試，'
                                       f'final_failure_reason={final_failure_reason}')
    decode_result = 'success' if thumbnail_image is not None else ('n/a(no_bytes)' if thumbnail_bytes is None else 'failed')
    _thumbnail_diag_log(item_key, f'decode_result={decode_result} item_still_visible={item_still_visible} '
                                   f'UI callback=applied final_failure_reason={final_failure_reason}')
    apply_start = perf_counter()
    if thumbnail_image is not None:
        # 2026-08-14 修正：這個 Label 原本是「Loading...」佔位，建立時給了
        # width/height（文字模式下單位是字元數）。改成顯示圖片時 Tk 會把
        # 同一組 width/height 數字改成「像素」解讀，不歸零的話縮圖會被硬塞
        # 進一個只有幾個像素的框框，畫面上幾乎完全看不到——這正是使用者
        # 回報「完全看不到縮圖」的原因。跟 add_list_row() 裡「一開始就命中
        # cache」那個分支一樣，改成圖片模式時 width/height 都要清成 0，
        # 讓 Label 照圖片本身大小顯示。
        thumbnail_label.config(image=thumbnail_image, text='', width=0, height=0)
        thumbnail_label.image = thumbnail_image  # 額外保留一份參照在 widget 自己身上，跟 image_refs 分開，避免被下一次 clear_list() 的 image_refs.clear() 波及
    else:
        thumbnail_label.config(text='No Thumbnail', image='')
    apply_duration = perf_counter() - apply_start

    # 2026-08-20 使用者要求量測每張縮圖的 download/decode/apply 耗時：
    # 這裡是真的完整跑完 decode+套用 UI 的終點，才算一筆完整樣本，見函式
    # 開頭的說明。一定要先記錄完這一筆，才能呼叫 _finish_batch_if_needed()
    # ——不然這一批最後一筆自己的樣本會來不及算進統計/Summary（見上面的
    # 說明）。
    _record_thumbnail_perf_sample(item_key, download_duration, decode_duration, apply_duration)
    _finish_batch_if_needed()


def _record_thumbnail_perf_sample(item_key, download_duration, decode_duration, apply_duration):
    """記錄這一張縮圖的 download/decode/apply 耗時。前 THUMBNAIL_PERF_
    SAMPLE_LIMIT（20）張個別印出來，方便確認個別行為；之後的張數只累計
    count/total/max，不個別印，避免 log 洗版——count/total/max 是這一批
    全部張數的累加，不限於前 20 張。"""
    global _thumbnail_perf_count, _thumbnail_perf_total_time, _thumbnail_perf_max_time
    total_duration = download_duration + decode_duration + apply_duration
    _thumbnail_perf_count += 1
    _thumbnail_perf_total_time += total_duration
    _thumbnail_perf_max_time = max(_thumbnail_perf_max_time, total_duration)
    if len(_thumbnail_perf_samples) < THUMBNAIL_PERF_SAMPLE_LIMIT:
        _thumbnail_perf_samples.append((item_key, download_duration, decode_duration, apply_duration, total_duration))
        print(f'[Gallery Perf] Thumbnail #{_thumbnail_perf_count} (item_key={item_key}): '
              f'download={download_duration:.3f}s, decode={decode_duration:.3f}s, '
              f'apply={apply_duration:.3f}s, total={total_duration:.3f}s')
    if _thumbnail_perf_count == THUMBNAIL_PERF_SAMPLE_LIMIT:
        _print_thumbnail_perf_aggregate(f'前 {THUMBNAIL_PERF_SAMPLE_LIMIT} 張')
        _maybe_print_gallery_load_summary()


def _print_thumbnail_perf_aggregate(label):
    """印出目前這一批縮圖累積的 count/total/average/max，label 只是用來
    區分是「前 20 張」觸發的，還是「整批下載完成」觸發的，數字本身兩邊
    共用同一份累加狀態，不是兩套統計。"""
    average_duration = _thumbnail_perf_total_time / _thumbnail_perf_count if _thumbnail_perf_count else 0.0
    print(f'[Gallery Perf] Thumbnail 統計（{label}）: count={_thumbnail_perf_count}, '
          f'total={_thumbnail_perf_total_time:.3f}s, avg={average_duration:.3f}s, max={_thumbnail_perf_max_time:.3f}s')


def _maybe_print_gallery_load_summary():
    """_gallery_load_perf_context 有值時（代表這一批縮圖是由一次完整
    Gallery Load 觸發，不是切 Filter/批次模式那種局部重畫），統一印出
    完整的 Gallery Load Summary；印完立刻把 context 清成 None，避免之後
    不相關的縮圖批次（例如使用者接著切 Filter）再次觸發時重複印出同一份
    Summary。這個函式在「前 20 張下載完成」跟「整批下載完成」兩個地方都
    會被呼叫，兩個都用同一個 None 檢查擋住，不會印兩次：批次張數
    >= 20 的話，「前 20 張」那次會先印、清空 context，之後「整批完成」
    那次呼叫看到 context 已經是 None 就直接跳過；批次張數 < 20 的話，
    「前 20 張」那個門檻永遠不會到達，改由「整批完成」那次印。

    2026-08-20 使用者要求：context['mode'] 是 'fresh_refresh'（
    load_recordings() 那條完整 GET 路徑）還是 'cache_render'（
    show_gallery_view()/show_favorite_view() 直接用現成 cache render，
    真機最常見的「開 Tool -> 點 Gallery」路徑）決定要印哪幾行——
    cache_render 沒有經過任何新的 GET，不能硬湊 GET/grouping/sorting
    這幾個數字，只印真的有量到的 cached_items/favorite-filter/UI rows/
    first usable；fresh_refresh 維持原本完整的 7 個階段都印。第一行一律
    先印 mode，兩種 Summary 一眼就能分清楚是哪一條路徑。"""
    global _gallery_load_perf_context
    context = _gallery_load_perf_context
    if context is None:
        return
    _gallery_load_perf_context = None

    thumbnail_batch_elapsed = (perf_counter() - _thumbnail_perf_batch_start
                                if _thumbnail_perf_batch_start is not None else 0.0)
    sample_count_label = min(_thumbnail_perf_count, THUMBNAIL_PERF_SAMPLE_LIMIT)
    average_duration = _thumbnail_perf_total_time / _thumbnail_perf_count if _thumbnail_perf_count else 0.0
    total_elapsed = perf_counter() - context['total_start']

    print('[Gallery Perf] ===== Gallery Load Summary =====')
    print(f"[Gallery Perf] mode = {context['mode']}")
    if context['mode'] == 'fresh_refresh':
        print(f"[Gallery Perf] recordings_count = {context['recordings_count']}")
        print(f"[Gallery Perf] GET recordings      = {context['get_duration']:.3f} s")
        print(f"[Gallery Perf] grouping            = {context['group_duration']:.3f} s")
        print(f"[Gallery Perf] completion filter   = {context['completion_filter_duration']:.3f} s")
        print(f"[Gallery Perf] favorite/filter     = {context['visible_filter_duration']:.3f} s")
        print(f"[Gallery Perf] sorting             = {context['sort_duration']:.3f} s")
    else:  # cache_render：沒有新的 GET，不硬算 GET/grouping/sorting
        print(f"[Gallery Perf] cached_items = {context['cached_items']}")
        print(f"[Gallery Perf] favorite/filter     = {context['visible_filter_duration']:.3f} s")
    print(f"[Gallery Perf] create UI rows      = {context['ui_rows_duration']:.3f} s ({context['row_count']} rows)")
    print(f"[Gallery Perf] first usable        = {context['first_usable_duration']:.3f} s")
    print(f"[Gallery Perf] thumbnail first {sample_count_label} = {thumbnail_batch_elapsed:.3f} s "
          f"(count={_thumbnail_perf_count}, total={_thumbnail_perf_total_time:.3f}s, "
          f"avg={average_duration:.3f}s, max={_thumbnail_perf_max_time:.3f}s)")
    print(f'[Gallery Perf] total               = {total_elapsed:.3f} s')
    print('[Gallery Perf] ================================')


def start_visibility_check_loop():
    """Gallery/Favorite 分頁變成使用中時呼叫（show_gallery_view()/
    show_favorite_view()），開始週期性可視範圍檢查。重複呼叫不會疊加多個
    迴圈（用 _visibility_check_after_id 判斷是不是已經在跑）。"""
    global is_gallery_screen_active
    is_gallery_screen_active = True
    if _visibility_check_after_id is not None:
        return  # 已經在跑，不要疊加第二個迴圈
    _schedule_next_visibility_check()


def _schedule_next_visibility_check():
    global _visibility_check_after_id
    _visibility_check_after_id = list_canvas.after(GALLERY_VISIBILITY_CHECK_INTERVAL_MS, _visibility_check_tick)


def _visibility_check_tick():
    """2026-08-20 使用者回報真機兩筆縮圖永遠卡在 'Loading...'、重啟也一樣，
    排查後確認 Root Cause 機制：這支函式原本沒有任何例外保護，
    queue_visible_thumbnail_downloads() 只要拋出任何未預期例外，下面
    「排下一次 .after()」那一行就永遠不會執行到——Tkinter 對 .after()
    callback 裡沒接住的例外，預設只是印一次 traceback 就繼續 mainloop，
    不會讓程式當掉，但也不會自動重新排下一次，代表整個週期性可視範圍
    檢查從那一刻起，這個 Session 剩下的時間永久停止。凡是那個當下還沒被
    排進下載佇列的縮圖，之後不管怎麼捲動畫面都不會再被檢查到，永遠卡在
    初始的 'Loading...'（還沒排隊過，跟已經排隊、下載失敗的 'Pending...'/
    'No Thumbnail' 是不同狀態）。

    這裡只加 try/except 保護、印出完整 traceback，讓迴圈不會因為任何一次
    例外就永久停止——這輪使用者明確要求先不要動
    queue_visible_thumbnail_downloads() 內部邏輯/retry/worker/decode 流程，
    只先確保這個外層迴圈本身有韌性、不會整個死掉。真正是哪一筆資料的
    哪個欄位觸發例外，要等真機再次重現、對照這裡印出的完整 traceback
    才能確認，這裡不假設/不宣稱已經修好真機那兩筆縮圖的問題。"""
    global _visibility_check_after_id
    try:
        queue_visible_thumbnail_downloads()
    except Exception:
        print('[Thumbnail/Visibility] queue_visible_thumbnail_downloads() 拋出未預期例外，'
              '已攔截，不會讓可視範圍檢查迴圈永久停止（下一次 tick 仍會照常執行）：')
        traceback.print_exc()
    if is_gallery_screen_active:
        _schedule_next_visibility_check()
    else:
        _visibility_check_after_id = None


def on_gallery_screen_hidden():
    """離開 Gallery/Favorite 分頁時呼叫（DA005ControlPanel.pyw 的
    show_screen()），停止週期性可視範圍檢查，避免背景無意義一直跑。
    已經排進佇列/下載中的縮圖不會被取消，繼續完成——反正結果會進 cache，
    不會浪費，下次回到 Gallery/Favorite 也能直接用。"""
    global is_gallery_screen_active, _visibility_check_after_id
    is_gallery_screen_active = False
    if _visibility_check_after_id is not None:
        list_canvas.after_cancel(_visibility_check_after_id)
        _visibility_check_after_id = None


def on_checkbox_toggled(item_key, checkbox_var):
    """只改變選取狀態，不可以觸發播放、不可以切換頁面"""
    if checkbox_var.get():
        checked_item_keys.add(item_key)
    else:
        checked_item_keys.discard(item_key)
    update_selection_status_text()  # 「N selected」即時跟著勾選數量變化


# ============================================================
# Filter / 批次選擇模式：純畫面端狀態切換
# ============================================================

def on_filter_selected(selected_value):
    """Filter 只改變目前顯示內容，不修改 DVR 資料，也不用重新 GET，直接
    用 current_thumbnails_by_item_key 快取重新畫清單。
    切 Filter 一定要清掉目前的勾選：Apply 是拿 checked_item_keys 對整個
    current_groups（不受 Filter 限制）找目標，如果切 Filter 之後隱藏掉
    的項目還留著勾選狀態，使用者會在看不到那些項目的情況下對它們送出
    Favorite/Unfavorite/Delete——Delete 這樣做有誤刪風險，所以切 Filter
    比照選回 Select Action 一樣，直接清掉勾選。"""
    global current_filter
    current_filter = selected_value
    checked_item_keys.clear()
    render_visible_list()
    # 2026-08-20 使用者真機回報 Filter 後 Scroll State 異常（viewport 停在
    # 舊的捲動位置，落在新內容範圍之外）：Filter 是使用者主動改變資料
    # 集合，不需要保留 Filter 前的捲動位置，見 reset_gallery_scroll_to_top()
    # 的完整說明。
    reset_gallery_scroll_to_top()
    # 2026-08-19 使用者要求：漏斗右上角的 Active 提示點要跟著 Filter 切換
    # 立即更新（不打開 Menu 也看得出來）。這裡不需要重新套用 is_batch_mode
    # 本身（切 Filter 時一定是 Normal Mode，不然使用者也點不到 Filter
    # icon），但沿用同一個函式比自己另外寫一行 itemconfigure() 更不容易
    # 漏掉以後的規則變化。
    _apply_right_controls_item_states()


def show_filter_menu():
    """點右上角的 Filter 漏斗 icon：彈出既有的 Filter 選項（All/Scenic/
    Event/Photo），選了直接呼叫既有的 on_filter_selected()——只是把入口
    從 tk.OptionMenu 換成 tk.Menu 彈出選單，篩選邏輯完全沒有重寫。

    2026-08-19 使用者要求：每個選項前面加一個單選圓點，目前選中的那個
    畫實心圓，其他畫空心圓框，一次只會有一個是選中狀態——用既有的
    current_filter 判斷，不用另外保存一份選單自己的狀態。"""
    if is_gallery_busy:
        return
    menu = build_dark_popup_menu(right_controls_canvas)
    for option in FILTER_OPTIONS:
        radio_icon = filter_radio_selected_icon_image if option == current_filter else filter_radio_unselected_icon_image
        menu.add_command(label=option, image=radio_icon, compound='left',
                          command=lambda value=option: on_filter_selected(value))
    popup_menu_below_canvas_item(menu, right_controls_canvas, 'filter_icon')


def show_selection_action_menu():
    """點右上角的 ... ：彈出 Favorite/Unfavorite/Delete，選了直接執行
    （不用再按 Apply），見 trigger_selection_action()。"""
    if is_gallery_busy:
        return
    menu = build_dark_popup_menu(right_controls_canvas)
    for action_name in SELECTION_ACTIONS:
        menu.add_command(label=action_name, command=lambda name=action_name: trigger_selection_action(name))
    popup_menu_below_canvas_item(menu, right_controls_canvas, 'selection_menu')


def trigger_selection_action(action_name):
    """使用者從 ... 選單直接選 Favorite/Unfavorite/Delete：沿用既有的
    start_batch_action()/run_batch_action()/on_batch_action_done() 完全
    不變，只是觸發入口從『下拉選單 + Apply 按鈕』改成『長按進 Selection
    Mode + 勾選 + 點 ... 選單直接執行』，不需要 Apply 這個額外步驟。
    Delete 的確認對話框是既有機制，這裡照樣保留，不因為拿掉 Apply 就跳過。"""
    if not checked_item_keys:
        messagebox.showinfo('Notice', 'Please select at least one item.')
        return
    if action_name == 'Delete':
        confirmed = messagebox.askyesno('Confirm Delete', 'Are you sure you want to delete the selected item(s)?')
        if not confirmed:
            return  # No：不送任何 Delete API，維持原本 Gallery
    start_batch_action(action_name)


def enter_selection_mode(item_key):
    """長按某一列觸發：進入 Selection Mode，並自動勾選被長按的那一筆
    （其他項目預設不勾），使用者之後可以繼續勾選/取消其他項目。"""
    global is_batch_mode
    if is_gallery_busy:
        return
    is_batch_mode = True
    checked_item_keys.clear()
    checked_item_keys.add(item_key)
    render_visible_list()
    update_top_bar_mode()


def exit_selection_mode():
    """點右上角的 X：離開 Selection Mode、清除所有勾選、Checkbox 全部消失、
    右上角恢復 Filter icon。純粹是 UI 模式切換，不會重新 GET recordings、
    不會重新下載縮圖（render_visible_list() 本來就只用既有的 current_
    groups/current_thumbnails_by_item_key 重畫，沒有呼叫任何 DVR API）。"""
    global is_batch_mode
    is_batch_mode = False
    checked_item_keys.clear()
    render_visible_list()
    update_top_bar_mode()


def update_top_bar_mode():
    """依 is_batch_mode 切換右上角要顯示 Filter icon 還是 X/... 兩個
    Canvas item，並同步「N items」/「N selected」文字。三個 item 都畫在
    同一個 right_controls_canvas 上、位置永遠不變，只用 itemconfigure()
    切換要顯示哪一組（跟 screen_player.py 的 Overlay Controls 顯示/隱藏
    是同一種做法），Canvas 本身完全不會跳動。"""
    _apply_right_controls_item_states()
    update_selection_status_text()


def _apply_right_controls_item_states():
    """right_controls_canvas 上 3 個 item 的最終狀態要同時考慮兩件事：
    is_batch_mode 決定「這個 item 現在該不該顯示」，is_gallery_busy 決定
    「有顯示的話，能不能點」——Tk Canvas item 的 state 只有一個值
    （normal/hidden/disabled），沒辦法同時獨立表達這兩個條件，所以這裡
    統一算出最終結果，不要在兩個地方各自直接呼叫 itemconfigure()。

    2026-08-17 第三次調整：這個 Canvas 現在是由外部呼叫
    create_nav_filter_controls() 才會建立（正式流程是 DA005ControlPanel.pyw
    在 Top Navigation Bar 建立），不是 build_screen() 自動建立——只呼叫
    build_screen() 而還沒呼叫 create_nav_filter_controls() 的情境下（例如
    只想測試 Thumbnail/Filter/Refresh 邏輯本身、不關心 Nav Bar 圖示的
    測試腳本），right_controls_canvas 會是 None，這裡直接跳過，不要讓
    set_controls_busy()/update_top_bar_mode() 這些核心流程因為一個純外觀
    的 Nav Bar 圖示還沒建立就整個崩潰。"""
    if right_controls_canvas is None:
        return

    def resolve(should_show):
        if not should_show:
            return 'hidden'
        return 'disabled' if is_gallery_busy else 'normal'

    right_controls_canvas.itemconfigure('filter_icon', state=resolve(not is_batch_mode))
    right_controls_canvas.itemconfigure('selection_close', state=resolve(is_batch_mode))
    right_controls_canvas.itemconfigure('selection_menu', state=resolve(is_batch_mode))
    # 2026-08-19 使用者要求：漏斗右上角的 Active 提示點——Selection Mode
    # 期間漏斗本身就隱藏了，點也要跟著隱藏；Normal Mode 下只有 Filter
    # 不是 All 才顯示。
    right_controls_canvas.itemconfigure(
        'filter_active_dot', state=resolve(not is_batch_mode and current_filter != FILTER_ALL))


def update_selection_status_text():
    """Normal Mode 顯示「N items」，Selection Mode 顯示「N selected」。"""
    if is_batch_mode:
        status_label.config(text=f'{len(checked_item_keys)} selected')
    else:
        status_label.config(text=f'{len(get_visible_groups())} items')


# ============================================================
# 長按進入 Selection Mode／短按維持既有行為／向左 Swipe 進單筆 Delete
# ============================================================
# 用 ButtonPress-1 時排一個 after() 計時器、ButtonRelease-1 時看計時器
# 有沒有先觸發，判斷這次是長按還是短按——專案目前沒有現成的 long-press
# helper，這裡是第一次加，只給 Gallery Row 用，不是共用元件。
#
# 2026-08-19 新增 Swipe-to-Delete：<B1-Motion> 判斷「明顯向左的水平拖曳」
# 才確認進入 Swipe，一旦確認：立即取消 Long Press 計時器、放開不會再觸發
# 短按/長按的任何後續行為。三種手勢（Click/Long Press/Swipe）共用同一組
# press/release 事件，靠 _swipe_state 這個狀態機互斥，不需要三套分開的
# 事件綁定。

def on_row_press(event, item_key, group_items, row_content, trash_label, card_width):
    """按下的當下：
    1. 照舊排一個 Long Press 計時器（時間到了使用者還沒放開，且中途沒有
       確認成 Swipe，就算長按，自動進入 Selection Mode）。
    2. 額外記錄這次按下的座標/這一列的 row_content 等 Swipe 相關資訊——
       Selection Mode 下完全不記錄（is_batch_mode 時 Swipe 完全停用），
       <B1-Motion>/<ButtonRelease-1> 才不會誤判成 Swipe。
    3. card_width（Row 實際寬度）在這裡存一次，之後 motion/release 全部
       用這個存好的值算 threshold，不會在拖曳中途或放開之後重新查詢
       widget 的即時寬度（真機回報的卡住 Bug，使用者要求要避免這種
       「Row 已經移動後才取寬度」的寫法，這裡本來就是先存好、不是每次
       都重查，維持這個做法）。"""
    global _long_press_after_id, _long_press_triggered
    global _swipe_press_x, _swipe_press_y, _swipe_active_item_key, _swipe_state
    global _swipe_row_content, _swipe_trash_label, _swipe_card_width, _swipe_group_items, _swipe_dx

    if is_gallery_busy:
        return

    _long_press_triggered = False
    _long_press_after_id = list_frame.after(
        LONG_PRESS_THRESHOLD_MS, lambda: on_long_press_triggered(item_key))

    if is_batch_mode:
        _swipe_active_item_key = None  # Selection Mode 完全禁止 Swipe，不記錄拖曳起點
        _swipe_state = 'idle'
        return

    _swipe_press_x = event.x_root
    _swipe_press_y = event.y_root
    _swipe_active_item_key = item_key
    _swipe_state = 'idle'
    _swipe_row_content = row_content
    _swipe_trash_label = trash_label
    _swipe_card_width = card_width
    _swipe_group_items = group_items
    _swipe_dx = 0
    _swipe_diag_log(f'press_x={event.x_root}')
    _swipe_diag_log(f'original_row_width={card_width}')
    _swipe_diag_log(f'threshold={card_width * FULL_SWIPE_DELETE_THRESHOLD_RATIO:.1f}')


def on_long_press_triggered(item_key):
    global _long_press_after_id, _long_press_triggered
    _long_press_after_id = None
    _long_press_triggered = True
    enter_selection_mode(item_key)


def on_row_motion(event, item_key):
    """拖曳過程中判斷/更新 Swipe 狀態：只做純本地 UI 更新（移動這一列的
    row_content、更新 Trash icon），不 GET recordings、不 render_visible_
    list()、不下載縮圖——這個函式在拖曳期間會被大量呼叫，必須維持輕量。

    判斷是不是 Swipe：先要求位移超過 SWIPE_MOVEMENT_THRESHOLD_PX（避免
    點擊時手抖 2~3px 就被誤判），再要求「往左」且「水平位移明顯大於垂直
    位移」（SWIPE_HORIZONTAL_DOMINANCE_RATIO），兩者都成立才確認為 Swipe。
    一旦確認：立即取消 Long Press 計時器，之後 Release 不會再觸發短按/
    長按。

    2026-08-19 Bug 修正：dx 的下限刻意留
    SWIPE_FULL_DRAG_SAFETY_MARGIN_PX 這麼多 px 的餘裕，不讓 dx 真的等於
    -card_width——row_content 永遠跟 row_container 保留一點點重疊，避免
    完全脫離可視範圍導致抓住滑鼠的 widget 收不到之後的 <ButtonRelease-1>
    （真機回報「拉到底放開卡住」的懷疑root cause，見這幾個常數的說明）。"""
    global _swipe_state, _swipe_dx, _long_press_after_id

    if is_batch_mode or is_gallery_busy:
        return  # Selection Mode 完全禁止 Swipe
    if _swipe_active_item_key != item_key or _swipe_row_content is None:
        return  # 理論上不會發生（Tk 在按下時就鎖定後續事件都送到同一個 widget），防禦性檢查
    if _swipe_state == 'deleting':
        return  # 已經鎖定進入 Delete 流程，之後任何殘留的 motion 事件一律忽略

    dx = event.x_root - _swipe_press_x
    dy = event.y_root - _swipe_press_y

    if _swipe_state == 'idle':
        if abs(dx) < SWIPE_MOVEMENT_THRESHOLD_PX and abs(dy) < SWIPE_MOVEMENT_THRESHOLD_PX:
            return  # 移動量太小，可能只是手抖，先不判定任何 Gesture
        is_horizontal_left_swipe = dx < 0 and abs(dx) > abs(dy) * SWIPE_HORIZONTAL_DOMINANCE_RATIO
        if not is_horizontal_left_swipe:
            return  # 不是明顯的向左水平拖曳（可能是垂直捲動或長按時手滑），維持既有 Long Press 邏輯繼續等待
        _swipe_state = 'dragging'
        if _long_press_after_id is not None:
            list_frame.after_cancel(_long_press_after_id)
            _long_press_after_id = None

    min_dx = -(_swipe_card_width - SWIPE_FULL_DRAG_SAFETY_MARGIN_PX)
    dx = max(min_dx, min(0, dx))  # 限制在 [min_dx, 0]，永遠留一點點跟 row_container 的重疊
    _swipe_dx = dx
    _swipe_diag_log(f'current_x={event.x_root}')
    _swipe_diag_log(f'drag_distance={dx}')
    update_row_swipe_visual(_swipe_row_content, _swipe_trash_label, dx, _swipe_card_width)


def update_row_swipe_visual(row_content, trash_label, dx, card_width):
    """純本地 UI 更新：同時更新 row_content 位置跟 Trash icon 顯示/大小。
    拖曳過程跟放開後的回彈動畫共用同一份邏輯，不寫兩次。dx 是 row_content
    相對 row_container 的水平位移（<=0），紅色 Delete Area 本身完全不用
    重畫/重新計算寬度——它一直都是滿版鋪在 row_container 最底層，
    row_content 往左移開多少，紅色區域自然就露出多少。"""
    if not row_content.winfo_exists():
        return
    row_content.place(x=dx, y=0, relwidth=1, relheight=1)

    if not trash_label.winfo_exists():
        return
    visible_width = abs(dx)
    if visible_width >= DELETE_AREA_MIN_WIDTH_FOR_TRASH_ICON:
        is_ready = visible_width >= card_width * FULL_SWIPE_DELETE_THRESHOLD_RATIO
        trash_label.config(image=(trash_icon_image_ready if is_ready else trash_icon_image_normal))
        # Trash icon 保持在目前可見紅色區域的視覺中央：可見紅色區域是
        # [card_width+dx, card_width]，中心點 = card_width + dx/2。
        trash_center_x = card_width + dx / 2
        trash_label.place(x=trash_center_x, rely=0.5, anchor='center')
    else:
        trash_label.place_forget()  # 紅色區域太窄，先不顯示 Trash icon


def on_row_release(event, item_key, group_items):
    """放開的當下：
    1. 已經確認是 Swipe 的話，這次放開不會再觸發短按/長按/開 Viewer/進
       Selection Mode——依「按下時存好的原始 Row 寬度 × 85~90%」門檻，
       用整個拖曳過程中累積、跟著每次 <B1-Motion> 更新的 _swipe_dx（不是
       從 row_content 目前的 Canvas/place x 座標反推回去），直接執行
       Delete（不跳確認視窗）或回彈，跟原本的 Click/Long Press 判斷完全
       分開、互斥。
    2. 一旦判定 Full Swipe，立刻把 _swipe_state 鎖定成 'deleting'——就算
       之後又收到重複/殘留的 press/motion/release 事件，也一定會被最前面
       的 'deleting' 檢查擋掉，不會讓這次放開又落到下面的 click/長按/
       回彈任何一個分支重新處理一次。
    3. 否則維持既有邏輯：長按已經觸發過的話不用再做事；否則取消還沒到
       時間的計時器，當作一次短按（Normal Mode 開 Viewer，Selection Mode
       toggle Checkbox）。"""
    global _long_press_after_id, _swipe_state, _swipe_active_item_key

    if _long_press_after_id is not None:
        list_frame.after_cancel(_long_press_after_id)
        _long_press_after_id = None

    if _swipe_active_item_key == item_key and _swipe_state in ('dragging', 'deleting'):
        if _swipe_state == 'deleting':
            return  # 已經在處理這次的 Full Swipe Delete 了，忽略重複的 release

        row_content = _swipe_row_content
        trash_label = _swipe_trash_label
        original_row_width = _swipe_card_width   # 按下當下存好的原始寬度，不是現在才查
        group_items_captured = _swipe_group_items
        total_drag_x = _swipe_dx                 # 最後一次 <B1-Motion> 更新的累積位移

        full_swipe_threshold = original_row_width * FULL_SWIPE_DELETE_THRESHOLD_RATIO
        is_full_swipe = abs(total_drag_x) >= full_swipe_threshold

        _swipe_diag_log(f'release=({event.x_root},{event.y_root})')
        _swipe_diag_log(f'drag_distance={total_drag_x}')
        _swipe_diag_log(f'original_row_width={original_row_width}')
        _swipe_diag_log(f'threshold={full_swipe_threshold:.1f}')
        _swipe_diag_log(f'full_swipe={is_full_swipe}')

        _swipe_active_item_key = None
        if is_full_swipe:
            _swipe_state = 'deleting'  # 先鎖定，execute_full_swipe_delete() 內任何 callback 都不會被當成新手勢處理
            execute_full_swipe_delete(item_key, group_items_captured, row_content, trash_label, original_row_width)
        else:
            _swipe_state = 'idle'
            animate_row_snap_back(row_content, trash_label, original_row_width)
        return

    _swipe_active_item_key = None

    if _long_press_triggered:
        return  # 長按已經處理過了

    if is_gallery_busy:
        return

    if is_batch_mode:
        toggle_checkbox_for_item(item_key)
    else:
        on_row_click(group_items)


def execute_full_swipe_delete(item_key, group_items, row_content, trash_label, original_row_width):
    """2026-08-19 使用者更新規則：Full Swipe（>= 85~90% 門檻，視覺上等同
    拉到底）放開後直接刪除，不再跳出確認視窗——跟 Selection Mode「...」
    選單的 Delete（trigger_selection_action('Delete')/start_batch_action()）
    是兩條不同的路徑，那一條仍然保留原本的確認對話框，這裡刻意不呼叫
    messagebox.askyesno()。沒有達到 Full Swipe 門檻的放開一律回彈、不執行
    刪除，見 on_row_release() 的判斷。

    實際刪除還是直接沿用既有的 run_batch_action()/on_batch_action_done()
    單筆刪除流程，不新增第二套 Delete API。target_groups 只有這一個
    group，不經過 checked_item_keys（Swipe Delete 是 Normal Mode 單筆
    操作，不牽涉 Selection Mode 的勾選狀態，也不會互相影響）。

    不論成功或失敗，最後都會把 Swipe 狀態收回 'idle'，保證 UI 不會永久
    停留在拖到底的紅色畫面。

    2026-08-19 使用者補充規則：刪除的如果剛好是 Gallery 最後一筆，
    Delete success 後不能先讓畫面切回 Recording、再期待 Gallery 之後
    某次 render 自己把這一列的紅色 Swipe 區清掉——on_batch_action_done()
    觸發的 refresh 完成後會呼叫 notify_gallery_item_count_changed()，
    這是外部（DA005ControlPanel.pyw）決定要不要自動切去 Recording 畫面
    的依據；所以這裡要在呼叫 on_batch_action_done() 之前，就先把這一列
    的 Swipe 視覺跟全域狀態復原乾淨（cleanup_swipe_ui_state()），不管
    最後是不是真的會切頁、也不管刪的是不是最後一筆，都先確保復原完成、
    不留下任何殘留的紅色畫面，才繼續往下走既有流程。"""
    _swipe_diag_log(f'delete item_key={item_key}')

    representative_item = api.get_group_representative(group_items)
    is_video_group = not api.is_photo_recording(representative_item)
    if GALLERY_DELETE_REFRESH_DIAGNOSTIC_LOGGING:
        global _diag_last_deleted_fileids, _diag_last_deleted_groupid, _diag_last_swiped_row_container
        _diag_last_deleted_fileids = [item.fileid for item in group_items]
        _diag_last_deleted_groupid = representative_item.groupid
        _diag_last_swiped_row_container = row_content.master
        if is_video_group:
            print(f'[Swipe Video Delete] deleted_groupid={representative_item.groupid}')

    set_controls_busy(True)
    status_label.config(text='Delete...')

    def on_swipe_delete_done(action_name, succeeded_item_keys, failed_item_keys, commit_error,
                              refresh_already_applied=False):
        global _swipe_state
        delete_succeeded = not (failed_item_keys or commit_error)
        _swipe_diag_log(f'delete result={"success" if delete_succeeded else "failed"}')
        if GALLERY_DELETE_REFRESH_DIAGNOSTIC_LOGGING and is_video_group:
            print(f'[Swipe Video Delete] delete_api_success={delete_succeeded}')

        if delete_succeeded:
            # 成功：立刻、直接把這一列復位跟全域狀態清乾淨（不用回彈
            # 動畫——動畫需要好幾個 after() 步驟才會收斂到 0，這裡刻意
            # 選擇立刻歸位，確保 on_batch_action_done() 之後不論是不是
            # 剛好觸發自動切回 Recording 畫面，都不會有任何一瞬間的殘影）。
            cleanup_swipe_ui_state(row_content, trash_label)
        else:
            # 失敗：資料沒變，不會有真的重畫也不會有切頁，維持原本的
            # 回彈動畫 UX；一樣要先把 _swipe_state 收回 idle。
            _swipe_state = 'idle'
            animate_row_snap_back(row_content, trash_label, original_row_width)

        # 2026-08-20 新增：refresh_already_applied 原樣轉呼叫給
        # on_batch_action_done()——Delete 成功時 run_batch_action() 已經
        # 呼叫過 verify_delete_completion_and_apply() 確認 target 消失並
        # 套用過資料，這裡不用再讓它呼叫一次 start_refresh()。
        on_batch_action_done(action_name, succeeded_item_keys, failed_item_keys, commit_error,
                              refresh_already_applied)

    thread = threading.Thread(target=run_batch_action, args=('Delete', [group_items], on_swipe_delete_done),
                              daemon=True)
    thread.start()


def cleanup_swipe_ui_state(row_content, trash_label):
    """Full Swipe Delete 成功時，在呼叫 on_batch_action_done()（可能觸發
    重新整理 Gallery、甚至因為刪到最後一筆而自動切回 Recording 畫面）之前，
    先把這一列的 Swipe 視覺跟全域手勢狀態立刻復原乾淨：
    - _swipe_state 收回 'idle'
    - _swipe_dx 歸零
    - _swipe_active_item_key 清空
    - row_content 移回 x=0（紅色 Delete Area 本來就是滿版鋪在底層，
      row_content 蓋回去就等於視覺上把紅色區域完全遮住/隱藏）
    - trash_label 隱藏

    row_content/trash_label 這時候可能已經被既有的 clear_list() 銷毀了
    （剛好比這裡先跑完那一次重新整理），也可能還活著，兩種情況都要能
    安全處理、不報錯。Tk 的滑鼠 grab 是按下時自動建立、放開時自動釋放
    的隱含機制，這裡沒有呼叫過 grab_set()，不需要（也沒有）額外的
    grab_release()。"""
    global _swipe_state, _swipe_dx, _swipe_active_item_key

    _swipe_state = 'idle'
    _swipe_dx = 0
    _swipe_active_item_key = None

    if row_content.winfo_exists():
        row_content.place(x=0, y=0, relwidth=1, relheight=1)
    if trash_label.winfo_exists():
        trash_label.place_forget()


def animate_row_snap_back(row_content, trash_label, card_width):
    """Swipe 沒有達到 Full Swipe 門檻（85~90%）就放開、或 Delete 失敗：
    把這一列滑回原始位置。用 Tkinter after() 做幾段小步驟動畫（不是
    sleep()/額外 thread，不會阻塞 Tk mainloop）；如果 widget 已經被銷毀
    （例如剛好碰上其他操作重畫了清單）直接跳過，不報錯。

    起始位移直接讀 row_content 目前實際的 place x 座標，不依賴模組層級
    的 _swipe_dx——這個函式有兩個呼叫來源（Release 時未達 Full Swipe 門檻、
    Delete 失敗；Full Swipe Delete 沒有確認對話框，不會有「取消」這個
    來源），呼叫的當下 row_content 的實際位置永遠是最新、最可靠的依據，
    不用另外傳一份可能過期的位移值。"""
    if not row_content.winfo_exists():
        return
    try:
        current_x = int(float(row_content.place_info().get('x', 0) or 0))
    except (ValueError, TypeError):
        current_x = 0
    if current_x == 0:
        return  # 已經在原始位置，不需要動畫
    _animate_snap_back_step(row_content, trash_label, card_width, current_x)


def _animate_snap_back_step(row_content, trash_label, card_width, current_x):
    if not row_content.winfo_exists():
        return
    next_x = round(current_x * SNAP_BACK_STEP_COUNT_RATIO)
    if abs(next_x) <= 1:
        next_x = 0
    update_row_swipe_visual(row_content, trash_label, next_x, card_width)
    if next_x != 0:
        list_frame.after(SNAP_BACK_STEP_INTERVAL_MS,
                          lambda: _animate_snap_back_step(row_content, trash_label, card_width, next_x))


def toggle_checkbox_for_item(item_key):
    """短按某一列（Selection Mode 下）：跟直接點 Checkbox 本身效果一樣，
    沿用同一個 on_checkbox_toggled()，並同步這一列 Checkbox 的勾選外觀。"""
    is_checked_now = item_key not in checked_item_keys
    checkbox_var = tk.BooleanVar(value=is_checked_now)
    on_checkbox_toggled(item_key, checkbox_var)
    checkbox_widget = checkbox_widgets_by_item_key.get(item_key)
    if checkbox_widget is not None and checkbox_widget.winfo_exists():
        if is_checked_now:
            checkbox_widget.config(text='☑', fg=ACCENT_COLOR)
        else:
            checkbox_widget.config(text='☐', fg=TEXT_SECONDARY_COLOR)


# ============================================================
# 點縮圖/文字播放：Photo 不進 Player；影片要先確認 DVR 錄影狀態
# ============================================================

def on_row_click(group_items):
    """點縮圖/文字：Photo 跟 Video 在這裡就要分流，不能共用同一條路徑。
    Photo 走 on_select_photo_callback（2026-08-19 起指到
    screen_player.set_current_photo_item()，跟 Video 共用同一個 Unified
    Media Viewer，但只下載原始 JPG 用 PIL 顯示，完全不會碰到
    on_select_video_callback／start_pre_play_check()／screen_player.py
    那一整套 FFmpeg Video Decoder——Photo 的 srcUri 是一張圖片，不是可
    解碼的影片內容，之前誤把 Photo 導去 Video 那條路徑（或完全沒接任何
    路徑）是造成「Photo 點擊後沒有反應」這個 regression 的原因。"""
    if is_gallery_busy:
        return  # Refresh / Batch Action / 播放前確認錄影狀態 進行中，不允許點列表播放

    representative_item = api.get_group_representative(group_items)
    if api.is_photo_recording(representative_item):
        if on_select_photo_callback:
            on_select_photo_callback(representative_item)
        return

    if not on_select_video_callback:
        return

    start_pre_play_check(group_items)


def start_pre_play_check(group_items):
    """點播放前先確認 DVR 是不是字面上的 STANDBY：不管目前是一般
    Recording、Event Recording，還是其他任何非 STANDBY 的狀態，都先送
    stop_recording() 切過去、GET 確認真的切成 STANDBY 了，才播放；如果
    已經是 STANDBY 就直接播放。這裡用 screen_recording.is_standby_mode()/
    confirm_standby_mode() 直接檢查字面上的 dvr_mode，不是用
    derive_dvr_state() 那套只分 Standby/Scenic/Event 三種的分類——那套
    分類是給 Recording 頁面按鈕顯示用的，其他原始狀態（例如
    RECORD_WITHOUT_RINGBUFFER）會被歸類成 standby 顯示，拿來判斷「能不能
    安全播放」會判斷錯，所以播放前的檢查改成直接看 dvr_mode 本身。
    這裡不會去動 Recording 頁面自己的按鈕/縮圖狀態（那是它自己畫面的事，
    這次沒有要動它——已知的限制是如果之後切回 Recording 頁面，它的按鈕
    顏色可能還沒反映這次在 Gallery 這裡順手停掉的錄影，要等使用者在
    Recording 頁面自己操作一次才會更新）。"""
    set_controls_busy(True)
    status_label.config(text='Checking recording status...')
    thread = threading.Thread(target=pre_play_check_worker, args=(group_items,), daemon=True)
    thread.start()


def pre_play_check_worker(group_items):
    try:
        mode = api.get_dvr_mode()
    except Exception as error:
        list_frame.after(0, lambda captured_error=error: on_pre_play_check_error(captured_error))
        return

    if not screen_recording.is_standby_mode(mode):
        try:
            api.stop_recording()
        except Exception as error:
            list_frame.after(0, lambda captured_error=error: on_pre_play_check_error(captured_error))
            return

        if not screen_recording.confirm_standby_mode():
            list_frame.after(0, lambda: on_pre_play_check_error(
                Exception('Failed to confirm DVR switched to STANDBY before playback.')))
            return

    list_frame.after(0, lambda: on_pre_play_check_success(group_items))


def on_pre_play_check_success(group_items):
    """2026-08-19 Ghosting 全面 Audit 發現：這裡原本完全沒有檢查使用者是不是
    還在 Gallery/Favorite——點了縮圖之後、DVR STANDBY 確認完成之前，如果
    使用者已經切去別的分頁，這裡還是會硬把畫面切去 Player，等於背景把
    使用者「劫持」到另一個畫面。用既有的 is_gallery_screen_active 擋掉，
    不用另外建立新的旗標。"""
    if not is_gallery_screen_active:
        return
    set_controls_busy(False)
    status_label.config(text=f'{len(get_visible_groups())} items')
    if on_select_video_callback:
        on_select_video_callback(group_items)


def on_pre_play_check_error(error):
    if not is_gallery_screen_active:
        return
    set_controls_busy(False)
    status_label.config(text='Failed to check recording status.')
    messagebox.showerror('Error', f'Failed to check recording status.\n{error}')


# ============================================================
# Favorite：單筆（右側 ☆/★）與批次（Action + Checkbox + Apply）
# 兩種操作方式共用同一個 run_batch_action()，protocol 完全一致
# ============================================================

# 用來擋掉「同一筆資料的星星還在等 API 回應時，又被連點一次」。
# 這不是 is_gallery_busy 那種全域鎖（會連 Refresh/Filter/其他列都一起
# disable）——單筆 Favorite/Unfavorite 就是要盡量不影響其他操作，所以
# 只用一個很小範圍的集合，只防同一筆資料被重複觸發兩次 API。
# 2026-08-14 使用者確認規則後改用 fileid（item_key）而不是 groupid：
# 好幾張照片可能共用/重複 groupid，用 groupid 當 key 會讓點擊某一張照片
# 的星星，誤把其他共用 groupid 的照片也一起擋掉。
_pending_star_toggle_item_keys = set()


def on_star_clicked(group_items, star_label):
    """點右側 ☆/★：只對這一個 group 送 Favorite/Unfavorite（標記＋依現有
    規則補送一次 groupid=0 commit），不會觸發整個 Gallery 重新整理、
    不會重新呼叫 GET recordings、也不會重新下載縮圖——這是刻意跟批次
    Favorite/Unfavorite（Action + Checkbox + Apply）分開處理的路徑：
    批次操作結束後全部重新整理，但單筆點星星只是想快速切換一筆，重新整理
    整個清單太重、體感也慢。

    採用『先樂觀更新畫面，失敗才復原』：點下去當下就直接改本地資料
    (representative_item.is_favorite) 跟星星圖示，畫面立刻反應；真的送出
    API 失敗才把這筆的本地狀態跟星星復原、跳錯誤訊息。Favorite Tab 的
    顯示數量（是否要顯示/隱藏、要不要自動切回 Gallery）也是看這裡的
    本地狀態算，跟著同步更新，不需要重新整理才能反映。

    完整 Gallery Refresh 只保留在：進入 Gallery/Favorite 分頁、使用者按
    Refresh、Delete 完成、或其他需要重新跟 DVR 同步狀態的情況，不包含
    這裡的單筆 Favorite/Unfavorite。

    2026-08-20 使用者回報嚴重 regression、確認 Root Cause 後修正：這裡的
    group_items 參數是 Star widget 在 add_list_row() 建立/上次被
    Incremental Diff reuse 當下捕捉的閉包——這個 row 如果後來又經過真實
    Refresh（on_recordings_loaded()/report_gallery_item_count_from_
    recordings() 都會用全新反序列化物件整批替換 current_groups，但 Filter
    Hide/Show Reuse、is_batch_mode Checkbox 局部切換都讓 row widget 本身
    繼續沿用同一個，不會重新綁定 <Button-1> handler），這個閉包就會變成
    脫鉤的舊物件——對它 mutate 完全不會反映到真正的 current_groups 上，
    但 Star 顏色是直接用點擊當下算出的布林值 patch，看起來像是成功了
    （見 _debug_favorite_stale_closure_repro.py 的實測）。

    修法：這裡只把參數本身的 item_key（api.compute_item_key()，不變的
    識別碼，即使物件被整批替換也不會變）當作「使用者點的是哪一筆」的識別
    依據，實際要 mutate 的 group_items/representative_item 一律重新從
    目前 current_groups 反查最新版本，不直接信任閉包捕捉到的參數本身——
    不管這個 row widget 被 reuse 多久、current_groups 被真實 Refresh
    换過幾輪，Star click 永遠操作當下最新 current_groups 裡的那一筆。

    2026-08-20 真機證實 fileid 本身不保證唯一（三層診斷確認，見
    api.compute_item_key() 的說明）之後改用 compute_item_key()：原本這裡
    直接比對 .fileid == item_key，如果 current_groups 裡剛好有兩個 group
    共用同一個 fileid，next() 會抓到 iteration 順序中先出現的那個，可能
    不是使用者實際點的那一筆——等於誤刪/誤 mutate 到錯誤的 recording。
    compute_item_key() 用 Video 用 groupid、Photo 用 srcUri，兩者都不是
    這次真機證實會重複的 fileid，重新反查時比對整個 compute_item_key()
    而不是單一個 .fileid 欄位。"""
    if is_gallery_busy:
        return  # Refresh/批次操作/播放前確認錄影狀態 進行中，避免跟正在重畫的清單打架

    item_key = api.compute_item_key(group_items)
    latest_group_items = next(
        (group for group in current_groups if api.compute_item_key(group) == item_key), None)
    if latest_group_items is None:
        # 理論上不會發生（能點到 Star 代表這個 item 目前一定還在
        # current_groups 裡），除非剛好跟一次 Delete 完成撞在一起——不冒險
        # 對一個已經不存在的 item 送 Favorite/Unfavorite，直接放棄這次點擊。
        print(f'[Favorite Diag] click item_key={item_key} 目前已經不在 current_groups 裡（可能剛好被刪除），略過')
        return
    group_items = latest_group_items  # 之後全部用這份最新的，不要再用參數本身（閉包）
    representative_item = api.get_group_representative(group_items)

    if item_key in _pending_star_toggle_item_keys:
        return  # 這一筆資料上一次點擊的 API 還沒回應，先忽略這次點擊
    _pending_star_toggle_item_keys.add(item_key)

    original_is_favorite = representative_item.is_favorite
    new_is_favorite = not original_is_favorite

    # 2026-08-20 使用者回報 Favorite regression，保留這個最小診斷 log 追蹤
    # 這條 flow（只追這裡，不加大量 Gallery 全域 profiling），方便真機
    # 確認這次修正後 current_groups_updated 穩定是 True。
    print(f'[Favorite Diag] click item_key={item_key} source_view={current_view_mode} '
          f'old_is_favorite={original_is_favorite} using_latest_current_groups_entry=True')

    # 2026-08-20 使用者回報 Favorite Page Unfavorite UI 卡約 5 秒，要求追查
    # Root Cause——這裡計時「本地資料 mutation」跟「apply_star_visual_
    # update() 整段（含它裡面會呼叫的 render_visible_list()）」，因為這整段
    # 目前是同步執行、發 API 請求之前就先做完了（樂觀更新），使用者感受到
    # 的畫面卡頓極可能就發生在這個同步區塊內，不是在等 API 回應。
    t_click_start = perf_counter()

    # 樂觀更新：先直接改本地資料 + 畫面，讓使用者立刻看到變化
    t_local_update_start = perf_counter()
    representative_item.is_favorite = new_is_favorite
    local_update_duration = perf_counter() - t_local_update_start
    print(f'[Favorite Diag] click item_key={item_key} new_is_favorite={new_is_favorite} '
          f'current_groups_updated={representative_item.is_favorite == new_is_favorite}')

    t_apply_visual_start = perf_counter()
    apply_star_visual_update(star_label, new_is_favorite, group_items)
    apply_visual_duration = perf_counter() - t_apply_visual_start

    t_notify_start = perf_counter()
    notify_favorite_state_changed()
    notify_duration = perf_counter() - t_notify_start

    total_click_duration = perf_counter() - t_click_start
    print(f'[Favorite Perf] local update={local_update_duration:.4f}s')
    print(f'[Favorite Perf] apply_star_visual_update() total={apply_visual_duration:.4f}s '
          f'(含 render_visible_list()，見上面幾行細部拆解——Gallery 分頁這裡通常接近 0，'
          f'因為只 patch Star widget，不會呼叫 render)')
    print(f'[Favorite Perf] notify_favorite_state_changed()={notify_duration:.4f}s')
    print(f'[Favorite Perf] total（點擊當下同步區塊，不含後續 API 回應）={total_click_duration:.4f}s')

    action_name = 'Favorite' if new_is_favorite else 'Unfavorite'
    thread = threading.Thread(
        target=run_single_favorite_toggle,
        args=(representative_item, item_key, action_name, original_is_favorite, star_label, group_items),
        daemon=True)
    thread.start()


def apply_star_visual_update(star_label, is_favorite, group_items):
    """套用一筆資料 Favorite 狀態變化後，畫面該怎麼反應——依目前是
    Gallery 還是 Favorite 分頁，需要的處理完全不一樣：
    - Gallery 分頁：這個 group 不管 favorite 與否都還是會顯示，只要改
      這一個 Star widget 的文字/顏色就好，不用整個清單重畫。
    - Favorite 分頁：Favorite 分頁本來就是 current_groups 篩選 is_favorite
      的結果（見 get_visible_groups()），一筆資料的 is_favorite 變成
      False，代表它不再符合這個分頁該顯示的條件，只改 Star 圖示不夠，
      這一整列必須從清單上消失（或者失敗復原時要讓它重新出現），只能
      呼叫 render_visible_list() 依目前的 current_groups 重新篩選、重畫
      ——一樣是全部用本地資料/縮圖快取重畫，沒有呼叫任何 DVR API。

    2026-08-20 使用者回報 Issue 3、實測用 _debug_favorite_signature_
    staleness_repro.py 追蹤確認：Gallery 分頁這個分支只 patch Star widget
    本身，完全不會呼叫 render_visible_list()，這是刻意的設計（單筆
    Favorite/Unfavorite 不需要整個清單重畫）。但 is_favorite 剛好是
    _compute_single_item_signature() 的其中一個欄位，如果 Gallery 分頁
    這樣局部更新完全不同步 _row_signature_by_item_key/_rendered_signature，
    這個 item 的 signature 快取就會停留在「點擊前」的舊值，跟
    representative_item.is_favorite 的實際新值不一致。這個不一致本身
    不會馬上有影響（這個 item 當下沒有被 destroy/重建），但下一次任何
    其他原因觸發的 render_visible_list()（切 Filter、新增其他媒體、進出
    Selection Mode……）如果這個 item 剛好還是 survivor，
    _try_incremental_diff_update() 的 survivor content 安全檢查會比對到
    「這個 item 的內容悄悄變了」而 fallback 完整 rebuild——即使這個 item
    的畫面其實早就在點擊當下就已經正確反映新狀態，仍然會拖累整份清單
    重新 destroy/建立。這裡呼叫 _sync_row_signature_cache_for_item()
    同步修正快取，避免這個已知、可以直接局部處理的 is_favorite 變化被
    誤判成「未知的 survivor content 變化」。Favorite 分頁那個分支本來就
    會呼叫 render_visible_list()，走正常 render/diff 流程本身就會更新
    這兩份快取，不需要在這裡另外處理。"""
    item_key = api.compute_item_key(group_items)
    row_widget_exists = item_key in _row_widgets_by_item_key
    row_currently_visible = item_key in _current_row_order
    print(f'[Favorite Diag] click item_key={item_key} row_widget_exists={row_widget_exists} '
          f'row_currently_visible={row_currently_visible}')

    if current_view_mode == VIEW_MODE_FAVORITE:
        # 2026-08-20 使用者回報 Favorite Page Unfavorite UI 卡約 5 秒，要求
        # 只加最小 Performance Profiling、先不要改邏輯——這裡計時
        # render_visible_list() 本身的總耗時，並讀取
        # _last_incremental_diff_metrics/_last_clear_list_metrics（這兩個
        # 已經是 render_visible_list() 內部既有算好的數字，這裡只是讀出來
        # 組成 [Favorite Perf] Summary，不影響任何既有行為）。
        t_render_start = perf_counter()
        render_visible_list()
        render_duration = perf_counter() - t_render_start

        diff_metrics = _last_incremental_diff_metrics
        clear_metrics = _last_clear_list_metrics
        if diff_metrics is not None:
            fallback_full_rebuild = False
            incremental_diff_duration = diff_metrics['total_duration']
            hidden_or_removed_duration = diff_metrics['hide_duration']
            repack_duration = diff_metrics['repack_duration']
            survivor_repack_count = diff_metrics['survivor_repack_count']
            is_pure_removal = diff_metrics['is_pure_removal']
            destroy_duration = 0.0  # diff 路徑的 hide 只 pack_forget，不 destroy
            destroyed_count = diff_metrics['destroyed']  # 只有「hidden pool 內容悄悄變了」才會單獨 destroy 這幾筆
            reused_count = diff_metrics['reused']
            hidden_count = diff_metrics['hidden']
        elif clear_metrics is not None:
            fallback_full_rebuild = True
            incremental_diff_duration = 0.0
            hidden_or_removed_duration = 0.0
            repack_duration = 0.0
            survivor_repack_count = 0
            is_pure_removal = False
            destroy_duration = clear_metrics['destroy_duration']
            destroyed_count = clear_metrics['destroyed_rows']
            reused_count = 0
            hidden_count = 0
        else:
            # 理論上不會發生（render_visible_list() 一定會走其中一條），
            # 防禦性寫法，避免這裡的 Profiling 本身噴例外。
            fallback_full_rebuild = None
            incremental_diff_duration = hidden_or_removed_duration = repack_duration = destroy_duration = 0.0
            destroyed_count = reused_count = hidden_count = survivor_repack_count = 0
            is_pure_removal = False

        print(f'[Favorite Perf] item_key={item_key} fallback_full_rebuild={fallback_full_rebuild} '
              f'is_pure_removal={is_pure_removal} reused={reused_count} hidden={hidden_count} '
              f'destroyed={destroyed_count}')
        print(f'[Favorite Perf] incremental diff={incremental_diff_duration:.4f}s')
        print(f'[Favorite Perf] hidden/removed row 處理={hidden_or_removed_duration:.4f}s')
        print(f'[Favorite Perf] repack={repack_duration:.4f}s (survivor_repack_count={survivor_repack_count}, '
              f'預期 pure removal 時應該是 0)')
        print(f'[Favorite Perf] destroy={destroy_duration:.4f}s '
              f'({"clear_list() 完整 rebuild" if fallback_full_rebuild else "diff 路徑，理論上應該是 0"})')
        print(f'[Favorite Perf] render_visible_list() total={render_duration:.4f}s')
    else:
        update_star_widget_appearance(star_label, is_favorite)
        _sync_row_signature_cache_for_item(group_items)
        rendered_signature_updated = (item_key in _row_signature_by_item_key
                                       and _row_signature_by_item_key[item_key][3] == is_favorite)
        print(f'[Favorite Diag] click item_key={item_key} view=gallery -> 只 patch Star widget，'
              f'rendered_signature_updated={rendered_signature_updated}')


def _sync_row_signature_cache_for_item(group_items):
    """只局部同步 1 個 item 的 signature 快取（_row_signature_by_item_key
    跟 _rendered_signature 裡對應的那個 tuple），不觸發任何 render/diff。
    給 apply_star_visual_update() 的 Gallery 分頁分支用——那裡刻意只
    patch Star widget 本身，不呼叫 render_visible_list()，這裡把「已知
    且可以直接局部處理的 is_favorite 變化」同步進快取，避免之後其他
    render 把這個已經正確反映的變化誤判成「未知的 survivor content
    變化」而 fallback 完整 rebuild（見 apply_star_visual_update() 的
    說明、_debug_favorite_signature_staleness_repro.py 的實測結果）。

    這裡刻意只更新快取，不去補呼叫 render_visible_list()：Favorite 分頁
    以外的畫面本來就不需要因為這 1 筆 is_favorite 變化重畫任何東西，快取
    只是要跟真實資料保持一致，不是要多做一次重畫。"""
    global _rendered_signature
    item_key = api.compute_item_key(group_items)
    if item_key not in _row_widgets_by_item_key:
        return  # 理論上不會發生（能點星星代表這個 item 目前一定有 widget），防禦性寫法
    new_signature = _compute_single_item_signature(group_items)
    _row_signature_by_item_key[item_key] = new_signature
    if _rendered_signature is not None and item_key in _current_row_order:
        index = _current_row_order.index(item_key)
        if index < len(_rendered_signature):
            _rendered_signature[index] = new_signature


def update_star_widget_appearance(star_label, is_favorite):
    """只改這一個 Star widget 的文字/顏色，不影響畫面其他任何地方。
    如果在等 API 回應的這段時間，使用者剛好觸發了一次完整 Refresh（例如
    手動按 Refresh），render_visible_list() 會整個 destroy 重畫，這個
    widget 可能已經不存在了——對已經銷毀的 widget 呼叫 .config() 會丟
    TclError，所以更新前一定要先確認 widget 還在。"""
    if not star_label.winfo_exists():
        return
    star_label.config(text=('★' if is_favorite else '☆'),
                       fg=(FAVORITE_STAR_COLOR if is_favorite else TEXT_SECONDARY_COLOR))


def run_single_favorite_toggle(representative_item, item_key, action_name, original_is_favorite, star_label,
                                group_items):
    """背景執行緒：對單一筆資料送 Favorite/Unfavorite 標記，再補送一次
    groupid=0 commit——跟批次操作（run_batch_action()）用的是同一組
    api.py 函式，protocol 完全一致，差別只在這裡固定只處理一筆資料，
    而且完成後不會觸發任何 Gallery 重新整理。"""
    t_api_start = perf_counter()
    try:
        api.set_recording_item_favorite(representative_item, action_name == 'Favorite')
        if action_name == 'Favorite':
            api.commit_favorite_batch(representative_item.type_of_recording)
        else:
            api.commit_unfavorite_batch(representative_item.type_of_recording)
        error = None
    except Exception as caught_error:
        error = caught_error
    api_duration = perf_counter() - t_api_start

    print(f'[Favorite Diag] click item_key={item_key} API result={"success" if error is None else f"failed: {error}"}')
    print(f'[Favorite Perf] API={api_duration:.4f}s')
    list_frame.after(0, lambda: on_single_favorite_toggle_done(
        representative_item, item_key, action_name, original_is_favorite, star_label, group_items, error))


def on_single_favorite_toggle_done(representative_item, item_key, action_name, original_is_favorite,
                                    star_label, group_items, error):
    t_callback_start = perf_counter()
    _pending_star_toggle_item_keys.discard(item_key)

    if error is None:
        callback_duration = perf_counter() - t_callback_start
        print(f'[Favorite Diag] click item_key={item_key} callback_finished=True（成功，樂觀更新已經是最終狀態）')
        print(f'[Favorite Perf] on_single_favorite_toggle_done()={callback_duration:.4f}s（成功路徑，理論上應該接近 0）')
        return  # 成功：畫面在點擊當下（樂觀更新）就已經是對的了，不用再做事

    # 失敗：復原本地資料 + 畫面，回到點擊前的樣子（在 Favorite 分頁的話，
    # 剛剛被移除的那一列要重新出現），並提示錯誤。
    #
    # 2026-08-20 使用者要求：跟點擊當下同樣的理由，這裡也要重新從目前
    # current_groups 反查最新的 group_items/representative_item 再復原
    # ——等 API 回應的這段時間（背景執行緒），current_groups 可能已經又
    # 被別的 Refresh 換過一輪新物件，不能沿用呼叫鏈一路傳進來、可能早就
    # 脫鉤的舊參照，不然「失敗復原」本身也會復原到一個已經沒人看得到的
    # 物件上。
    latest_group_items = next(
        (group for group in current_groups if api.compute_item_key(group) == item_key), None)
    if latest_group_items is not None:
        group_items = latest_group_items
        representative_item = api.get_group_representative(group_items)
        representative_item.is_favorite = original_is_favorite
    else:
        print(f'[Favorite Diag] click item_key={item_key} 復原時發現已經不在 current_groups 裡（可能剛好被刪除），'
              f'略過本地資料復原')
    apply_star_visual_update(star_label, original_is_favorite, group_items)
    notify_favorite_state_changed()
    callback_duration = perf_counter() - t_callback_start
    print(f'[Gallery] {action_name} item_key={item_key} failed: {error}')
    print(f'[Favorite Diag] click item_key={item_key} callback_finished=True（失敗，已復原本地狀態）')
    print(f'[Favorite Perf] on_single_favorite_toggle_done()={callback_duration:.4f}s（失敗復原路徑，'
          f'含 apply_star_visual_update()，若在 Favorite 分頁會再包含一次 render_visible_list()）')
    messagebox.showerror('Error', f'Failed to update favorite status.\n{error}')


# ============================================================
# Batch Action：Favorite / Unfavorite / Delete
# ============================================================

def start_batch_action(action_name):
    set_controls_busy(True)
    status_label.config(text=f'{action_name}...')

    # 用目前畫面上的 current_groups 對應勾選的 item_key，這份清單跟使用者
    # 勾選時看到的是同一份——Refresh/Batch Action 進行中 Confirm 本身就是
    # disabled，不會有「勾選完，清單卻在中途被換掉」的情況。2026-08-14
    # 使用者確認規則後改用 fileid 比對：如果改用單純 groupid 比對，好幾張
    # 共用/重複 groupid 的照片會被誤判成「都被勾選」，勾一張 Photo 卻整批
    # 處理到其他沒被勾選的 Photo。2026-08-20 真機證實 fileid 本身也不保證
    # 唯一，改用 api.compute_item_key()——這裡一定要跟 checkbox 勾選當下
    # 存進 checked_item_keys 的 key 是同一套算法，兩邊型別/算法對不上的話
    # 這裡會一個都比對不到，變成勾了但整批動作套用到 0 筆。
    target_groups = [
        group for group in current_groups
        if api.compute_item_key(group) in checked_item_keys
    ]

    thread = threading.Thread(target=run_batch_action, args=(action_name, target_groups), daemon=True)
    thread.start()


# 2026-08-20 使用者真機回報：Delete commit success 後，被刪除的 Row 有時
# 仍暫時顯示在 Gallery，切換分頁/手動 Refresh 才會消失——確認 Root Cause
# 是 DVR 的 eventual consistency：commit 回應成功不代表 /dashcam/recordings
# 立刻反映最新狀態（多段落的 Video Group 尤其明顯），commit 完馬上 GET
# 一次的話，GET 回來的清單仍然包含被刪除的 item/group，Gallery 的
# cache-first signature 比對判定 data_changed=False 就跳過重畫，使用者
# 因此看到「Delete 明明成功、Row 卻還在」，要等下一次不相關的 refresh
# （切頁/手動 Refresh）才會被套用。DELETE_VERIFY_RETRY_INTERVAL_SECONDS/
# DELETE_VERIFY_TIMEOUT_SECONDS 是使用者建議的 300~500ms／3~5 秒，各自
# 取中間值。
DELETE_VERIFY_RETRY_INTERVAL_SECONDS = 0.4
DELETE_VERIFY_TIMEOUT_SECONDS = 4


def _get_delete_target_identity(deleted_groups):
    """算出這次 Delete 實際要確認消失的識別碼：完全比照既有 Delete
    identity 判斷（api.is_photo_recording()，不是另外發明一套規則）——
    Video 用 groupid（一個 groupid 底下可能好幾段，只要 groupid 還在，
    就代表還沒真的刪除完）、Photo 用 fileid。回傳 (video_groupids,
    photo_fileids) 兩個 set。"""
    video_groupids = set()
    photo_fileids = set()
    for group in deleted_groups:
        representative_item = api.get_group_representative(group)
        if api.is_photo_recording(representative_item):
            photo_fileids.add(representative_item.fileid)
        else:
            video_groupids.add(representative_item.groupid)
    return video_groupids, photo_fileids


def _delete_targets_still_present(recordings, video_groupids, photo_fileids):
    """檢查這次要刪除的 target 是不是還存在於這次 GET 到的 recordings
    （raw，還沒分組）裡：Photo 看 fileid 還在不在，Video 看 groupid 還有
    沒有任何段落存在。"""
    for item in recordings:
        if api.is_photo_recording(item):
            if item.fileid in photo_fileids:
                return True
        elif item.groupid in video_groupids:
            return True
    return False


def _favorite_targets_still_stale(recordings, video_groupids, photo_fileids, expected_is_favorite):
    """檢查這次要更新 Favorite/Unfavorite 狀態的 target，在這次 GET 到的
    recordings（raw，還沒分組）裡，is_favorite 是不是還沒真的同步成預期
    的新值——用法跟 _delete_targets_still_present() 對應，identity 判斷
    完全共用 _get_delete_target_identity()。"""
    for item in recordings:
        if api.is_photo_recording(item):
            if item.fileid in photo_fileids and item.is_favorite != expected_is_favorite:
                return True
        elif item.groupid in video_groupids and item.is_favorite != expected_is_favorite:
            return True
    return False


def verify_favorite_completion_and_apply(favorited_groups, expected_is_favorite):
    """Favorite/Unfavorite commit 成功後才會呼叫（見 run_batch_action()）：
    2026-08-26 真機回報 Root Cause——批次 Favorite/Unfavorite 跟 Delete
    共用同一套「標記 + commit（groupid=0）」機制，DVR 端同樣存在 eventual
    consistency：commit 成功不代表 /dashcam/recordings 立刻反映新的
    is_favorite。過去的假設（見 run_batch_action() 舊註解）是 Favorite/
    Unfavorite 沒有這個問題、commit 完直接 start_refresh() 打一次 GET
    就好——實測證實這個假設是錯的：commit 完馬上 GET 到的清單，
    is_favorite 有時還是舊值，Gallery 的 cache-first signature 比對判定
    data_changed=False，整個 render_visible_list() 被跳過，使用者看到
    「勾選 Favorite/Unfavorite 後畫面沒反應」，要等下一次不相關的
    refresh（切換分頁）才會被套用。修法完全比照已經驗證有效的
    verify_delete_completion_and_apply()：commit 成功後不要立刻假設完成，
    在目前所在的背景執行緒反覆 GET，直到這次要更新的 target 全部確認
    已經變成預期的新 is_favorite 值，或超過 DELETE_VERIFY_TIMEOUT_SECONDS
    逾時，才把這次驗證用的最後一次 GET 結果套用到 Gallery——沿用同一組
    逾時/重試常數，不是另外發明一套。"""
    global is_refreshing, current_refresh_token

    video_groupids, photo_fileids = _get_delete_target_identity(favorited_groups)
    current_refresh_token += 1
    refresh_token = current_refresh_token
    is_refreshing = True

    deadline = time() + DELETE_VERIFY_TIMEOUT_SECONDS
    attempt = 0
    recordings = []
    confirmed = False
    while True:
        attempt += 1
        try:
            recordings = api.get_recordings()
        except Exception as error:
            print(f'[Gallery Favorite Verify] 第 {attempt} 次確認 GET recordings 失敗: {error}')
            recordings = []
        else:
            still_stale = _favorite_targets_still_stale(recordings, video_groupids, photo_fileids, expected_is_favorite)
            print(f'[Gallery Favorite Verify] 第 {attempt} 次確認：video_groupids={video_groupids} '
                  f'photo_fileids={photo_fileids} expected_is_favorite={expected_is_favorite} still_stale={still_stale}')
            if not still_stale:
                confirmed = True
                break
        if time() >= deadline:
            print(f'[Gallery Favorite Verify] 逾時（{DELETE_VERIFY_TIMEOUT_SECONDS}s），target 仍未確認同步成新的 '
                  f'is_favorite，改用最後一次 GET 結果套用，不再繼續等待')
            break
        sleep(DELETE_VERIFY_RETRY_INTERVAL_SECONDS)

    print(f'[Gallery Favorite Verify] 結束，總共嘗試 {attempt} 次，confirmed={confirmed}')
    groups = api.group_recordings_for_gallery(recordings)
    finished_groups = [group_items for group_items in groups if api.is_group_finished(group_items)]
    list_frame.after(0, lambda: on_recordings_loaded(finished_groups, refresh_token))


def verify_delete_completion_and_apply(deleted_groups):
    """Delete commit 成功後才會呼叫（見 run_batch_action()）：不能 commit
    success 就立刻假設 /dashcam/recordings 已經同步完成，這裡在目前所在
    的背景執行緒（呼叫端本來就在背景執行緒，這裡不用另外開執行緒）反覆
    GET recordings，直到這次真的要刪除的 target（Photo 用 fileid、Video
    用 groupid）都已經從清單消失，或超過 DELETE_VERIFY_TIMEOUT_SECONDS
    逾時，才真的把這次驗證用的最後一次 GET 結果分組/排序/套用到 Gallery
    ——不會為了套用資料再多 GET 一次。

    Batch Delete（Selection Mode 的 .../Delete）跟 Swipe Delete 共用這支
    函式，兩者都是透過 run_batch_action() 呼叫，不需要各自寫一套。

    逾時仍未確認消失的話，一樣照最後一次 GET 到的結果套用（維持既有
    「一律以 DVR 真實狀態為準，不自己假設」的原則），並記錄 diagnostic
    log；套用之後 on_recordings_loaded() 自己會把 is_refreshing/
    set_controls_busy 復原成正常（不忙碌）狀態，不會讓 UI 卡在 Delete
    中，Row 有沒有真的消失完全依 DVR 這次 GET 到的實際資料決定，不會
    假裝已經刪除成功。"""
    global is_refreshing, current_refresh_token

    video_groupids, photo_fileids = _get_delete_target_identity(deleted_groups)
    current_refresh_token += 1
    refresh_token = current_refresh_token
    is_refreshing = True

    deadline = time() + DELETE_VERIFY_TIMEOUT_SECONDS
    attempt = 0
    recordings = []
    confirmed = False
    while True:
        attempt += 1
        try:
            recordings = api.get_recordings()
        except Exception as error:
            print(f'[Gallery Delete Verify] 第 {attempt} 次確認 GET recordings 失敗: {error}')
            recordings = []
        else:
            still_present = _delete_targets_still_present(recordings, video_groupids, photo_fileids)
            print(f'[Gallery Delete Verify] 第 {attempt} 次確認：video_groupids={video_groupids} '
                  f'photo_fileids={photo_fileids} still_present={still_present}')
            if not still_present:
                confirmed = True
                break
        if time() >= deadline:
            print(f'[Gallery Delete Verify] 逾時（{DELETE_VERIFY_TIMEOUT_SECONDS}s），target 仍未從 '
                  f'DVR recordings 清單確認消失，改用最後一次 GET 結果套用，不再繼續等待')
            break
        sleep(DELETE_VERIFY_RETRY_INTERVAL_SECONDS)

    print(f'[Gallery Delete Verify] 結束，總共嘗試 {attempt} 次，confirmed={confirmed}')
    groups = api.group_recordings_for_gallery(recordings)
    finished_groups = [group_items for group_items in groups if api.is_group_finished(group_items)]
    list_frame.after(0, lambda: on_recordings_loaded(finished_groups, refresh_token))


def run_batch_action(action_name, target_groups, on_done=None):
    """依序處理每一個目標 group：先逐一標記，全部標記完才補送一次
    commit（groupid=0）。任何一個 group 失敗都不會中斷其他 group 的處理，
    最後統一記錄哪些成功、哪些失敗；只要有任何一筆標記成功，就照協議送出
    最後那次 commit，以 DVR 真實狀態為準，不會自己假設全部都成功。如果
    全部都標記失敗（一筆都沒送成功），就不送這次 commit——DVR 上可能還
    留著上一次沒 commit 完的殘留標記，這次全部失敗卻還送 commit，等於用
    不相關、動作類型也對不上的 commit 去套用那些殘留標記，比完全不送更
    危險。這支函式同時被批次 Apply（target_groups 可能好幾個）、右側
    ☆/★ 單筆操作、Swipe Delete（target_groups 都只有一個）共用，是唯一的
    Delete/Favorite/Unfavorite 實作，不會另外重寫一套。

    on_done（2026-08-19 新增，預設 None）：完成時要呼叫的回呼，預設是
    既有的 on_batch_action_done()（Selection Mode Apply/... 選單用）；
    Swipe Delete 這種單筆操作會傳一個包一層的版本，額外處理「刪除失敗/
    取消時要把這一列復位」，不影響/不重寫這支函式本身的 PUT/GET/commit
    邏輯。

    2026-08-20 使用者要求：Delete commit 成功時，不能直接呼叫
    on_done() -> start_refresh() 就當作完成——DVR 存在 eventual
    consistency，commit 成功不代表 recordings 已經同步完成，見
    verify_delete_completion_and_apply() 的說明。這裡改成 Delete commit
    成功時，先在這個背景執行緒內完成「確認 target 消失 + 套用資料」，
    確認完（或逾時）才呼叫 on_done()，並且告訴它這次已經套用過資料了
    （refresh_already_applied=True），不會再多呼叫一次 start_refresh()
    造成多餘的第二次 GET。

    2026-08-26 使用者真機回報 Batch Favorite/Unfavorite 完成後畫面沒有
    立即更新，要切回 Recording 再切回 Gallery 才看到正確結果——追查後
    發現上面這段（原本寫著「Favorite/Unfavorite 不受影響，沒有這個
    eventual consistency 問題」）的假設是錯的：Favorite/Unfavorite 跟
    Delete 用的是同一套「標記 + commit（groupid=0）」機制，DVR 端同樣有
    eventual consistency，commit 完直接 start_refresh() 打的那次 GET，
    is_favorite 常常還是舊值，導致 on_recordings_loaded() 的 signature
    比對判定 data_changed=False，整個 render_visible_list() 被跳過。
    改成跟 Delete 一樣，commit 成功時先呼叫 verify_favorite_completion_
    and_apply() 確認 target 真的同步成新的 is_favorite（或逾時）才套用
    資料，見該函式的完整說明。"""
    global _exit_selection_mode_after_next_refresh
    if on_done is None:
        on_done = on_batch_action_done
    # 2026-08-14 使用者確認規則後改用 item_key 追蹤成功/失敗，不能用單純
    # groupid——好幾張照片可能共用/重複 groupid，用 groupid 找「第一筆成功
    # 的那個 group」會找錯。2026-08-20 真機證實 fileid 本身也不保證每一筆
    # 都不一樣（三層診斷確認，見 api.compute_item_key() 的說明），原本
    # 「fileid 保證唯一」這個理由已經不成立，改用 api.compute_item_key()
    # （Video 用 groupid、Photo 用 srcUri）才能真的避免追蹤到錯的那一筆。
    succeeded_item_keys = []
    failed_item_keys = []
    succeeded_groups = []

    for group in target_groups:
        representative_item = api.get_group_representative(group)
        item_key = api.compute_item_key(group)
        try:
            if action_name == 'Favorite':
                api.set_recording_item_favorite(representative_item, True)
            elif action_name == 'Unfavorite':
                api.set_recording_item_favorite(representative_item, False)
            elif action_name == 'Delete':
                api.delete_recording_item(representative_item)
            succeeded_item_keys.append(item_key)
            succeeded_groups.append(group)
        except Exception as error:
            print(f'[Gallery] {action_name} item_key={item_key} failed: {error}')
            failed_item_keys.append(item_key)

    commit_error = None
    if succeeded_item_keys:
        commit_type_of_recording = api.get_group_representative(succeeded_groups[0]).type_of_recording
        try:
            if action_name == 'Favorite':
                api.commit_favorite_batch(commit_type_of_recording)
            elif action_name == 'Unfavorite':
                api.commit_unfavorite_batch(commit_type_of_recording)
            elif action_name == 'Delete':
                api.commit_delete_batch(commit_type_of_recording)
        except Exception as error:
            commit_error = error
            print(f'[Gallery] commit (groupid=0) failed: {error}')

    if action_name == 'Delete' and succeeded_groups and commit_error is None:
        _exit_selection_mode_after_next_refresh = True  # on_recordings_loaded() 會用這個旗標判斷要不要離開 Selection Mode
        verify_delete_completion_and_apply(succeeded_groups)
        list_frame.after(0, lambda: on_done(action_name, succeeded_item_keys, failed_item_keys, commit_error, True))
    elif action_name in ('Favorite', 'Unfavorite') and succeeded_groups and commit_error is None:
        _exit_selection_mode_after_next_refresh = True  # on_recordings_loaded() 會用這個旗標判斷要不要離開 Selection Mode
        verify_favorite_completion_and_apply(succeeded_groups, expected_is_favorite=(action_name == 'Favorite'))
        list_frame.after(0, lambda: on_done(action_name, succeeded_item_keys, failed_item_keys, commit_error, True))
    else:
        list_frame.after(0, lambda: on_done(action_name, succeeded_item_keys, failed_item_keys, commit_error))


def on_batch_action_done(action_name, succeeded_item_keys, failed_item_keys, commit_error,
                          refresh_already_applied=False):
    global _exit_selection_mode_after_next_refresh
    if failed_item_keys or commit_error:
        error_message_map = {
            'Favorite': 'Failed to update favorite status.',
            'Unfavorite': 'Failed to update favorite status.',
            'Delete': 'Failed to delete selected item(s).',
        }
        detail_lines = [error_message_map.get(action_name, 'Batch action failed.')]
        if succeeded_item_keys:
            detail_lines.append(f'Succeeded: {succeeded_item_keys}')
        if failed_item_keys:
            detail_lines.append(f'Failed: {failed_item_keys}')
        if commit_error:
            detail_lines.append('Failed to finalize the batch operation.')
        messagebox.showerror('Batch Action Error', '\n'.join(detail_lines))

    if refresh_already_applied:
        # 2026-08-20 新增：Delete commit 成功那條路徑已經在 run_batch_
        # action() 裡呼叫 verify_delete_completion_and_apply() 確認 target
        # 消失並套用過資料了，這裡不用再呼叫一次 start_refresh()，避免
        # 多餘的第二次 GET。
        return

    # 不管全部成功還是部分失敗，都重新 GET，用 DVR 真實狀態同步畫面
    # （包含 Favorite 數量，Favorite Tab 顯示/隱藏靠這次 refresh 完的
    # notify_favorite_state_changed() 更新）。既有規則：Batch Action 完成
    # 後（不論成不成功）都要離開 Selection Mode，這裡設旗標讓
    # on_recordings_loaded() 知道這次 refresh 是這個原因觸發的。
    _exit_selection_mode_after_next_refresh = True
    start_refresh()


def set_controls_busy(is_busy):
    """Refresh / Favorite / Unfavorite / Delete / 播放前確認錄影狀態
    進行期間，暫時 disable Filter / X / ... / Checkbox / 點列表播放，
    避免使用者同時重複操作造成 API command sequence 錯亂，也避免 busy
    期間切 Filter/長按進 Selection Mode 讓畫面顯示舊資料、狀態看起來
    不一致。"""
    global is_gallery_busy
    is_gallery_busy = is_busy

    _apply_right_controls_item_states()

    state = 'disabled' if is_busy else 'normal'
    for checkbox_widget in checkbox_widgets_by_item_key.values():
        checkbox_widget.config(state=state)
