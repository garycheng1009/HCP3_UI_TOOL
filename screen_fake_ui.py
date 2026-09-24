"""
screen_fake_ui.py — HCP3 外觀比對用的裝飾性假 UI（Left Rail / Bottom Bar）

2026-08-19 使用者提供主管給的 HCP3 實機截圖（桌面.jpg），希望 DA005 Control
Panel 外觀更接近 HCP3。這支檔案只負責畫「殼」，全部都是裝飾用：
- 不呼叫任何 api，不綁任何 command，不新增 navigation，不影響 Recording/
  Gallery/Favorite/Settings/Player 現有的任何行為。
- icon 全部先用 Canvas 手畫的簡單幾何圖形佔位（assets/ 目前沒有 Home/
  導航/Connectivity/手機/圓圈/四宮格/鈴鐺/大頭貼/風扇/送風模式這幾個
  圖示），先求版面/比例正確，之後如果需要更像真機，再個別換成從 HCP3
  素材圖裁出來的圖片，不需要重寫這支檔案的呼叫方式。
- Left Rail 拆成兩欄（對照原圖修正）：
    - Outer Status Column（外欄，較窄，沒有膠囊背景）：上方鈴鐺(+通知
      小藍點)，下方大頭貼，彼此獨立，跟下面的 Navigation icons 不是
      同一欄。
    - Inner Navigation Rail（內欄，獨立的黑色圓角膠囊容器）：由上而下
      Home、方向三角形、Connectivity、Phone、Circle、Grid 六個 icon，
      膠囊背景刻意用比外圍更深的顏色（NAV_RAIL_CAPSULE_COLOR），要能
      明顯跟外圍區分，不是隨便一個同色 Frame。
- 2026-08-19 使用者第三次對照原圖修正：原圖右側完全沒有對應的 icon rail
  結構，之前為了「左右對稱」自行補的 Right Rail（build_right_rail()）
  已經整個移除，不要猜測/仿造原圖沒有的區域。
- 2026-08-19 使用者最後一輪視覺調整：Top Bar／Left Rail 外圍／Main
  Content 三者背景要統一成同一個深灰色（跟 screen_gallery.BACKGROUND_
  COLOR 一致），讓整體看起來像同一個大 Frame，只靠 button/text 本身區分
  層級；Inner Navigation Rail 的黑色圓角膠囊（NAV_RAIL_CAPSULE_COLOR）
  維持不變；Bottom Control Bar 維持原本更深的黑色（獨立的
  BOTTOM_BAR_BG_COLOR，不能被上面的統一連動到）。

2026-08-20 使用者要求試做「淺紫灰漸層殼 + Pillow icon + Top Bar 新配色」，
實際看過截圖後不接受，要求整個退回這一版（本檔案現在的狀態），只保留
兩個新增項目：Outer Status Column 最上方的 PC 本地時間、Bottom Bar 右側
「－」「＋」再往右移一點（RIGHT_PLUS_MINUS_EXTRA_SHIFT）。漸層背景／
Pillow icon／Top Bar 新配色全部撤回，不要在那個版本上繼續微調——之後如果
要重新調整外觀，一次只改一個項目（例如先只做 icon 品質），不要同時改
背景/icon/layout/Top Bar。

DA005ControlPanel.pyw 只需要呼叫 build_left_rail()/build_bottom_bar()，
把回傳的 Frame pack 到正確位置即可。
"""
import os
import tkinter as tk
from datetime import datetime

from PIL import Image, ImageTk

import screen_gallery
import ui_shapes
from resource_paths import resource_path

# 2026-08-19 使用者要求：Top Bar／Left Rail 外圍／Main Content 三者背景
# 統一，直接沿用 screen_gallery.BACKGROUND_COLOR（Main Content 的畫面
# 本來就是這個顏色），不是另外調一個新色碼，確保三處視覺上真的是同一個
# 顏色、不會因為各自維護一份十六進位色碼而慢慢跑掉。
FAKE_UI_BG_COLOR = screen_gallery.BACKGROUND_COLOR

# Bottom Control Bar 維持「目前最深」的黑色（就是這幾輪 FAKE_UI_BG_COLOR
# 原本用的值），使用者明確要求「不跟上方統一」，所以獨立成自己的常數，
# 不會被上面 FAKE_UI_BG_COLOR 的變動連動到。
BOTTOM_BAR_BG_COLOR = '#1a1a1a'

FAKE_UI_ICON_COLOR = '#c9c9d1'    # 淺灰白線條，模擬真機的白色描邊 icon
FAKE_UI_ACCENT_COLOR = '#6a6ad8'  # 大頭貼圓底用的紫藍色調
FAKE_UI_BADGE_COLOR = '#4a90ff'   # 鈴鐺通知小藍點
NAV_RAIL_CAPSULE_COLOR = '#0d0d10'  # Inner Navigation Rail 的膠囊背景，使用者明確要求維持不變，不跟著 FAKE_UI_BG_COLOR 變動

STATUS_COLUMN_WIDTH = 90   # Outer Status Column（時間／鈴鐺／大頭貼）
# 2026-08-20 使用者比對 HCP3 實機比例，要求這一欄縮窄 20~25%（原本
# 118px 是為了配合上一輪放大到 20pt 的時間字體），這輪時間字體同時縮小到
# 16pt（見 TIME_FONT），實測 Calibri 16pt bold 的 "23:59"（最寬的 HH:mm
# 組合、DPI-aware 情況下最壞的量測值）需要 73px，90px（73 + 左邊距 8 +
# 右側緩衝 9）還是足夠不裁切文字，同時比原本 118px 縮窄約 24%，落在
# 使用者要求的 20~25% 範圍內。黑色 Navigation Rail 本身寬度
# （NAV_RAIL_WIDTH）沒有變，會自動跟著 status_column 變窄往左靠。
NAV_RAIL_WIDTH = 60        # Inner Navigation Rail（黑色圓角膠囊），維持不變
LEFT_RAIL_WIDTH = STATUS_COLUMN_WIDTH + NAV_RAIL_WIDTH  # 兩欄相加

BOTTOM_BAR_HEIGHT = 68

RAIL_ICON_SIZE = 32     # Navigation Rail 六個 icon 統一尺寸，維持不變
BELL_ICON_SIZE = 38     # 2026-08-20 使用者要求鈴鐺「尺寸比現在稍大」，比 RAIL_ICON_SIZE 大一點
BOTTOM_ICON_SIZE = 26   # Bottom Bar 中間三個 icon 用的正方形 Canvas 邊長，這輪不動

# 2026-08-20 使用者比對左側圖.jpg 實機近照，指出「找一個意思差不多的
# Lucide icon」這個方向跟實機造型差太多，要求直接照片重製、不用堅持哪個
# icon library。Navigation Rail 6 個 icon 目前 Antenna/Phone/Dotted Circle/
# Grid 4 個維持用 Pillow 在高解析度畫布上即時畫線（沿用
# ui_shapes.render_supersampled() 的 supersampling 機制消除鋸齒）；
# Bell/Home/Navigation 這 3 個這輪進一步改成「獨立高解析度透明 PNG
# asset」（assets/icons/bell.png、home.png、navigation.png，見 scratchpad
# 的 _generate_icon_assets_v1.py 產生方式），不是執行當下即時算線條座標
# ——確認這個做法/線條風格 OK 之後，其餘 4 個 icon 會用同一套方式轉成
# asset。User（大頭貼）這輪維持不動，還是用 _load_avatar_photo()/
# user.svg（使用者說「大致可以，最後再調」，這輪不動它）。
# 2026-08-20 改用 resource_path()（見 resource_paths.py），PyInstaller
# 打包成 exe 之後 __file__ 所在資料夾不是資源檔案真正的存放位置。
ICONS_DIR = resource_path('assets', 'icons')

_icon_photo_cache = {}      # Navigation Rail 6 個 icon + 鈴鐺共用（PNG asset 版跟 Pillow 即時畫版都放這裡）
_avatar_photo_cache = {}    # User 大頭貼（still SVG-based，這輪不動）獨立一份，key 格式跟上面不同


def _get_icon_photo(cache_key, draw_callback, size):
    """跟 draw_callback（PIL ImageDraw 的高解析度畫法）+ size 一起當
    cache key，同樣參數不會每次呼叫都重新畫一次。render_supersampled()
    回傳的圖本身是透明背景（RGBA），疊在黑色膠囊/Outer Status Column
    背景上不需要另外處理底色或 alpha 合成。"""
    full_key = (cache_key, size)
    cached = _icon_photo_cache.get(full_key)
    if cached is not None:
        return cached
    pil_image = ui_shapes.render_supersampled(size, size, draw_callback)
    photo_image = ImageTk.PhotoImage(pil_image)
    _icon_photo_cache[full_key] = photo_image
    return photo_image


# 2026-08-20 使用者要求：Bell/Home/Navigation 這三顆 icon 不要再由程式在
# 執行當下即時畫，改成獨立的高解析度透明 PNG asset（assets/icons/
# bell.png、home.png、navigation.png，見 scratchpad 的
# _generate_icon_assets_v1.py 產生方式：512px、內部 4x supersample 畫完再
# LANCZOS 縮小），App 執行時只負責「載入 + 依實際顯示尺寸 LANCZOS 縮小」，
# 不會每次都重新計算線條座標。Active 狀態需要的白色變體不用另外存一張
# 白色版 PNG——用同一張圖的 alpha channel（形狀）套上目標顏色的純色圖層，
# 换色不用重新畫線。
def _load_png_asset_photo(filename, size, color=None):
    cache_key = ('png_asset', filename, size, color)
    cached = _icon_photo_cache.get(cache_key)
    if cached is not None:
        return cached
    asset_path = os.path.join(ICONS_DIR, filename)
    pil_image = Image.open(asset_path).convert('RGBA')
    pil_image = pil_image.resize((size, size), Image.LANCZOS)
    if color is not None:
        alpha = pil_image.split()[3]
        rgb = tuple(int(color.lstrip('#')[i:i + 2], 16) for i in (0, 2, 4))
        pil_image = Image.new('RGBA', pil_image.size, rgb + (255,))
        pil_image.putalpha(alpha)
    photo_image = ImageTk.PhotoImage(pil_image)
    _icon_photo_cache[cache_key] = photo_image
    return photo_image


def _draw_antenna_icon(draw, w, h, scale, color=FAKE_UI_ICON_COLOR):
    """2026-08-20 使用者回報上一版「橫線+上下鏡像弧線」縮小後看起來像
    雷達/靶心，明確要求改回「中央直立天線 + 左右對稱訊號波」造型（不是
    一般 Wi-Fi 那種單側扇形圖案）：中央一條垂直線（天線），線上一個小
    圓點標記，左右各 3 道對稱的弧線像聲波/訊號一樣往外擴散。

    2026-08-20 第一次改完左右各半圈（180°）弧線，兩邊合起來剛好湊成一個
    完整的圓，縮小後一樣看起來像同心圓/靶心（沒有任何缺口）。改成每道
    弧線只取 100° 的一小段（不是半圓），上下各留約 80° 的明顯缺口，
    才會像「聲波」而不是「圓環」。"""
    stroke = max(2, round(1.8 * scale))
    cx, cy = w * 0.5, h * 0.5
    draw.line([(cx, h * 0.22), (cx, h * 0.78)], fill=color, width=stroke)
    marker_r = w * 0.055
    draw.ellipse([cx - marker_r, cy - marker_r, cx + marker_r, cy + marker_r], fill=color)
    for radius_ratio in (0.16, 0.28, 0.40):
        r = w * radius_ratio
        bbox = [cx - r, cy - r, cx + r, cy + r]
        draw.arc(bbox, start=130, end=230, fill=color, width=stroke)   # 左側一小段，開口朝右
        draw.arc(bbox, start=-50, end=50, fill=color, width=stroke)    # 右側一小段，開口朝左（鏡像）


def _draw_dotted_ring_icon(draw, w, h, scale, color=FAKE_UI_ICON_COLOR):
    """依左側圖.jpg 重製：由一圈小圓點組成的空心圓環，中央沒有實心點
    ——不是 Circle+center dot。"""
    import math
    cx, cy = w * 0.5, h * 0.5
    r = w * 0.32
    dot_r = max(1.2 * scale, 2)
    dot_count = 16
    for index in range(dot_count):
        angle = 2 * math.pi * index / dot_count
        x = cx + r * math.cos(angle)
        y = cy + r * math.sin(angle)
        draw.ellipse([x - dot_r, y - dot_r, x + dot_r, y + dot_r], fill=color)


def _draw_grid_boxes_icon(draw, w, h, scale, color=FAKE_UI_ICON_COLOR):
    """依左側圖.jpg 重製：2x2 四個獨立方框，大小/間距一致。"""
    stroke = max(2, round(2.0 * scale))
    box = w * 0.32
    gap = w * 0.1
    start_x = (w - (2 * box + gap)) / 2
    start_y = (h - (2 * box + gap)) / 2
    for col in (0, 1):
        for row in (0, 1):
            x = start_x + col * (box + gap)
            y = start_y + row * (box + gap)
            draw.rectangle([x, y, x + box, y + box], outline=color, width=stroke)


def _load_avatar_photo(size, icon_color, circle_fill_color):
    """大頭貼：直接載入 user.png，不再於 runtime 轉換 SVG。"""
    cache_key = ('avatar', size, icon_color, circle_fill_color)
    cached = _avatar_photo_cache.get(cache_key)
    if cached is not None:
        return cached

    inner_size = round(size * 0.62)

    # 直接讀取已經轉好的 PNG
    png_path = os.path.join(ICONS_DIR, 'user.png')
    inner_image = Image.open(png_path).convert('RGBA')
    inner_image = inner_image.resize((inner_size, inner_size), Image.LANCZOS)

    # 用 PNG 的 alpha 當遮罩，把人形重新套成指定 icon_color
    alpha = inner_image.getchannel('A')
    rgb = tuple(
        int(icon_color.lstrip('#')[i:i + 2], 16)
        for i in (0, 2, 4)
    )
    colored_icon = Image.new('RGBA', inner_image.size, rgb + (255,))
    colored_icon.putalpha(alpha)

    # 建立原本的圓形底色
    square_image = Image.new('RGBA', (size, size), circle_fill_color)

    paste_xy = (
        (size - inner_size) // 2,
        (size - inner_size) // 2
    )
    square_image.alpha_composite(colored_icon, paste_xy)

    # 保留原本的圓形遮罩
    circular_mask = ui_shapes.make_rounded_mask(size, size, size / 2)
    square_image.putalpha(circular_mask)

    photo_image = ImageTk.PhotoImage(square_image)
    _avatar_photo_cache[cache_key] = photo_image
    return photo_image


# 2026-08-20 使用者比對 HCP3 實機圖（下方圖.jpg）要求 Bottom Bar 中央
# 這 3 個 HVAC icon 改成獨立的高解析度透明 PNG asset（跟 Left Rail 那幾個
# icon 同一套 _load_png_asset_photo() 載入方式），不要再用 Tkinter Canvas
# 手畫幾何圖形（沒有 anti-aliasing，會鋸齒）。造型/產生方式見
# generate_bottom_bar_icons.py：
#   - Menu -> menu_fan.png（4 片紡錘形花瓣繞中心排列 + 中心小圓點）
#   - MAX -> max_defrost.png（頂端微凸的弧形擋風玻璃 + 3 條除霧線）
#   - 第三顆 -> rear_defrost.png（矩形窗框 + 3 條除霧線 + 右下角照後鏡）


# 2026-08-20 使用者比對左側圖.jpg／HCP3 需求圖重新確認每個位置的正確
# 造型（Lucide 對應版本造型差太多，這輪全部換成 Pillow 手畫版本，意義/
# 順序維持不變）：
#   1. Home           -> home.png             （PNG asset，房屋輪廓，尖屋頂＋門/內框）
#   2. Navigation     -> navigation.png        （PNG asset，朝上的細長導航箭頭，底部向內收縮）
#   3. Connectivity   -> _draw_antenna_icon    （中央 antenna mast + 左右對稱訊號弧線，這輪還沒轉成 asset）
#   4. Phone/Device   -> phone.png             （PNG asset，加寬後的直立長方形，上下各一小段橫線）
#   5. Dotted Circle  -> _draw_dotted_ring_icon （一圈小圓點組成的空心圓環，無中心點，這輪還沒轉成 asset）
#   6. Grid           -> _draw_grid_boxes_icon  （2x2 四個獨立方框，這輪還沒轉成 asset）
#
# list 裡每個項目是 (icon_key, source)：source 是字串就代表 PNG asset
# 檔名（走 _load_png_asset_photo()），是函式就代表還在用 Pillow 即時畫
# （走 _get_icon_photo()）——等使用者確認完剩下的造型，其餘會比照改成
# 同樣的字串（PNG 檔名）形式。
LEFT_RAIL_ICON_DRAWERS = [
    ('home', 'home.png'),
    ('navigation', 'navigation.png'),
    ('antenna', _draw_antenna_icon),
    ('device', 'phone.png'),
    ('dotted_ring', _draw_dotted_ring_icon),
    ('grid', _draw_grid_boxes_icon),
]

# 2026-08-25 使用者要求 optical size 一致性調整：量過每個 icon 素材/畫法
# 實際的「墨色範圍」（扣掉透明留白後圖案本身真正佔用的像素）發現差異很大
# （例如 Phone 只佔畫布寬度的 50%，Antenna 卻頂到 100%），同樣用
# RAIL_ICON_SIZE=32 畫布，肉眼看起來大小落差很大——不能直接統一放大
# RAIL_ICON_SIZE（留白比例不變，落差不會消失），改成每個 icon 各自查表
# 用不同的 render size 去補償留白差異，讓 6 個 icon 的墨色主要方向（寬/高
# 取較大值）收斂到接近的視覺大小。RAIL_ICON_SIZE 本身維持 32 不變，繼續
# 只給 NAV_RAIL_HEIGHT/icon_gap/黑色膠囊尺寸計算用，跟這裡的 render size
# 完全是兩件事——每個 icon 是用 canvas.create_image() 在同一個中心點畫
# 上去的，換一顆不同 size 生成的 PhotoImage 不會影響中心點/間距，水平
# 垂直置中自動維持。找不到對應 key 的話 fallback 回 RAIL_ICON_SIZE。
LEFT_RAIL_ICON_RENDER_SIZES = {
    'home': 36,
    'navigation': 37,
    'antenna': 30,  # 2026-08-25 使用者真機回報 optical size 偏小，28→30 小幅放大
    'device': 38,
    'dotted_ring': 34,  # 2026-08-25 使用者真機回報 optical size 偏小，32→34 小幅放大
    'grid': 30,
}

# 2026-08-20 使用者確認 HCP3 實機參考圖裡 Home 左側「沒有」藍色直條，
# 撤回上一輪自行加上的 Active 狀態示範（白色變亮＋左側藍色側邊
# marker）——這個 Navigation Rail 本來就是純裝飾、不追蹤真的「目前在
# 哪一頁」，不需要無中生有做一個 Active 樣式，全部 6 個 icon 統一用同一個
# 顏色（FAKE_UI_ICON_COLOR），不分 Active/Inactive。

# Inner Navigation Rail 膠囊尺寸：垂直方向要能剛好裝下 LEFT_RAIL_ICON_DRAWERS
# 六個 icon，上下各留 NAV_RAIL_CAP_PADDING 讓圓角端點（半圓形）有地方畫，
# 不會把 icon 切到膠囊的圓弧邊緣。
NAV_RAIL_ICON_GAP = 14
NAV_RAIL_CAP_PADDING = 26
NAV_RAIL_HEIGHT = (len(LEFT_RAIL_ICON_DRAWERS) * RAIL_ICON_SIZE
                   + (len(LEFT_RAIL_ICON_DRAWERS) - 1) * NAV_RAIL_ICON_GAP
                   + NAV_RAIL_CAP_PADDING * 2)


# 2026-08-20 使用者要求新增：顯示 PC 本地時間（HH:MM，每分鐘更新），
# 純本地端，不呼叫任何 DVR API、不跟 Current Time Sync 功能綁在一起。
#
# 2026-08-20 使用者比對 HCP3 實機圖，要求時間要進入 Top Bar 高度範圍
# （跟 Recording/Gallery Tab 同一個垂直高度區域），不能再放在 middle_row/
# Left Rail 裡面被 Recording 面板的 top padding 限制——改由
# build_top_bar_time_label() 建立，DA005ControlPanel.pyw 呼叫時傳入
# top_bar_left_spacer（Top Bar 那一列原本空著的佔位區域）當 parent。
# build_left_rail() 的 Outer Status Column 不再放時間，只留鈴鐺／User
# icon，鈴鐺的垂直位置維持不變（見 _TIME_BLOCK_SPACER_HEIGHT 的說明）。
TIME_LABEL_UPDATE_INTERVAL_MS = 60_000  # 每分鐘更新一次，不需要每秒刷新

# 2026-08-20 使用者比對 HCP3 實機圖，覺得上一輪放大到 20pt 太大，要求
# 縮小約 20~25%、保留 Bold，改成 16pt（20pt * 0.8 = 16pt，剛好 20%）；
# 後來再要求「字體再縮小約 15%」，16pt * 0.85 = 13.6，取最接近的整數
# 14pt，這輪「先不要再改字體大小」，維持 14pt。
TIME_FONT = ('Calibri', 14, 'bold')

# 2026-08-20 時間搬到 Top Bar 之後，Outer Status Column 最上方原本時間
# 佔用的空間（top margin + 文字本身 + 下方 pady）用一個等高的空白 Frame
# 補回來，鈴鐺才能維持原本的垂直位置不變、不會因為時間搬走而跟著往上移
# ——這個高度是照上一輪時間還在這裡時的實際版面量出來的。
_TIME_BLOCK_SPACER_HEIGHT = 69


def _update_time_text(label):
    """用 .after() 排下一次更新；label 被銷毀之後（切換畫面重建 Left
    Rail 的極端情況）.config() 對已刪除 widget 會丟 TclError，這裡用
    try/except 靜默吞掉，避免造成假的 Tk callback traceback（純裝飾用途，
    不影響任何實際功能，沒必要為了這個額外維護一個取消計時器的機制）。"""
    try:
        label.config(text=datetime.now().strftime('%H:%M'))
        label.after(TIME_LABEL_UPDATE_INTERVAL_MS, lambda: _update_time_text(label))
    except tk.TclError:
        pass


def build_top_bar_time_label(parent):
    """把時間放進 Top Bar 高度範圍（跟 Recording/Gallery Tab 同一個垂直
    高度區域），不再受 middle_row/Recording 面板的 top padding 影響。

    X 位置：用一個寬度剛好等於 STATUS_COLUMN_WIDTH 的容器置中——跟
    build_left_rail() 的 Outer Status Column 用同一個寬度常數置中，兩處
    的水平中心線因此保證會對齊（不是分別在兩處各自猜一個座標），時間仍然
    落在左側第一欄的水平範圍內，不會跑到 Recording Tab 下方。

    parent 是 DA005ControlPanel.pyw 的 top_bar_left_spacer（Top Bar 那
    一列原本空著、寬度等於 LEFT_RAIL_WIDTH 的佔位 Frame），純視覺，
    不綁任何 command/api。"""
    time_area = tk.Frame(parent, width=STATUS_COLUMN_WIDTH, bg=FAKE_UI_BG_COLOR)
    time_area.pack(side='left', fill='y')
    time_area.pack_propagate(False)

    time_label = tk.Label(time_area, text='--:--', bg=FAKE_UI_BG_COLOR, fg=FAKE_UI_ICON_COLOR,
                           font=TIME_FONT)
    time_label.place(relx=0.5, rely=0.5, anchor='center')
    _update_time_text(time_label)
    return time_area


def build_left_rail(parent, nav_rail_height=None, nav_rail_top_offset=0):
    """建立左側假 UI，拆成兩個獨立欄位（對照原圖修正，不能把 Bell/Profile
    跟 Navigation icons 混在同一欄）：

    - Outer Status Column（外欄，較窄，沒有膠囊背景）：中上鈴鐺(+通知
      小藍點)，下方大頭貼（SVG 人形線條疊在圓形色底上）。PC 時間已經
      移到 Top Bar 高度範圍（見 build_top_bar_time_label()），不在這裡。
    - Inner Navigation Rail（內欄，獨立的黑色圓角膠囊容器，顏色比外圍
      更深）：由上而下 Home/Event-Warning/Wi-Fi/Phone/Record/Grid 六個
      Lucide SVG icon，畫在同一個 Canvas 上（膠囊背景 + icon 共用座標系，
      不用像以前那樣每個 icon 各自一個小 Canvas）。

    2026-08-19 使用者第四次對照原圖要求：Inner Navigation Rail 的
    top/bottom 要對齊 Recording/Event/Photo Panel 的 top/bottom，不是
    在整個 Left Rail 高度裡置中。nav_rail_height/nav_rail_top_offset
    由 DA005ControlPanel.pyw 用 screen_recording 已公開的
    PREVIEW_SIZE[1]（= Panel 高度）/VERTICAL_PADY[0]（= Panel 頂端距離
    content_container 頂端的距離）算好傳進來，這裡不重複計算/猜測。
    沒有傳的話（standalone 測試、或未來還沒接上實際 Panel 尺寸時）退回
    原本「依 icon 數量自動算高度、在 Left Rail 裡置中」的行為，不會壞掉。

    純視覺，不綁任何 command/api。"""
    rail = tk.Frame(parent, width=LEFT_RAIL_WIDTH, bg=FAKE_UI_BG_COLOR)
    rail.pack_propagate(False)

    # ---- Outer Status Column ----
    status_column = tk.Frame(rail, width=STATUS_COLUMN_WIDTH, bg=FAKE_UI_BG_COLOR)
    status_column.pack(side='left', fill='y')
    status_column.pack_propagate(False)

    # 2026-08-20 使用者要求時間移到 Top Bar 高度範圍（跟 Recording/Gallery
    # Tab 同一個垂直高度區域），不再放在這個 status_column/middle_row
    # 裡面——改由 build_top_bar_time_label() 建立，DA005ControlPanel.pyw
    # 呼叫時傳入 top_bar_left_spacer 當 parent。這裡原本時間佔用的空間用
    # 一個等高的空白 Frame（_TIME_BLOCK_SPACER_HEIGHT）補回來，鈴鐺才能
    # 維持原本的垂直位置不變，不會因為時間搬走而跟著往上移。
    top_spacer = tk.Frame(status_column, height=_TIME_BLOCK_SPACER_HEIGHT, bg=FAKE_UI_BG_COLOR)
    top_spacer.pack()

    # 2026-08-20 使用者確認目前的鈴鐺造型不能接受（鐘身輪廓錯誤＋警示
    # 三角形疊到鐘身把右側線條蓋住，見這次修改前的分析），改成獨立的高
    # 解析度透明 PNG asset（assets/icons/bell.png，鐘身本體＋鐘舌 clapper＋
    # 右上角黃色警示三角形都畫在同一張圖裡、彼此不重疊，見 scratchpad 的
    # _generate_icon_assets_v1.py）。畫布高度多留一截給下方的藍色通知
    # 圓點（使用者要求藍點改到鈴鐺下方，不是右上角，藍點跟圖裡的 clapper
    # 是兩個獨立東西，不會疊在一起）。
    bell_canvas_height = round(BELL_ICON_SIZE * 1.25)
    bell_canvas = tk.Canvas(status_column, width=BELL_ICON_SIZE, height=bell_canvas_height,
                             bg=FAKE_UI_BG_COLOR, highlightthickness=0)
    bell_photo = _load_png_asset_photo('bell.png', BELL_ICON_SIZE)
    bell_canvas.bell_photo_ref = bell_photo  # 保留參照，避免被 GC 回收
    bell_canvas.create_image(BELL_ICON_SIZE / 2, BELL_ICON_SIZE / 2, image=bell_photo, anchor='center')
    # 藍色通知小圓點：使用者要求改到鈴鐺下方（不是右上角），直接在 Canvas
    # 上疊一個小實心圓，尺寸很小不需要另外做成圖檔。
    dot_x, dot_y = BELL_ICON_SIZE / 2, BELL_ICON_SIZE * 1.1
    dot_r = 4
    bell_canvas.create_oval(dot_x - dot_r, dot_y - dot_r, dot_x + dot_r, dot_y + dot_r,
                             fill=FAKE_UI_BADGE_COLOR, outline='')
    # 2026-08-20 使用者確認鈴鐺水平位置不對，要求改回跟時間/User icon
    # 同一條中心線（上一輪貼右邊界的做法撤回）：拿掉 anchor='e'，一樣用
    # pack() 預設置中，垂直 pady 維持不變（Y 位置已經確認大致可以，
    # 藍點下移的空間是在 canvas 本身高度加的，不動 pady 才不會連帶把
    # bell canvas 的起始位置也跟著移動）。
    bell_canvas.pack(pady=(6, 16))

    avatar_size = 44
    avatar_canvas = tk.Canvas(status_column, width=avatar_size, height=avatar_size,
                               bg=FAKE_UI_BG_COLOR, highlightthickness=0)
    avatar_photo = _load_avatar_photo(avatar_size, FAKE_UI_ICON_COLOR, FAKE_UI_ACCENT_COLOR)
    avatar_canvas.avatar_photo_ref = avatar_photo  # 保留參照，避免被 GC 回收
    avatar_canvas.create_image(avatar_size / 2, avatar_size / 2, image=avatar_photo)
    avatar_canvas.pack(side='bottom', pady=(16, 20))

    # ---- Inner Navigation Rail：獨立的黑色圓角膠囊 ----
    nav_column = tk.Frame(rail, width=NAV_RAIL_WIDTH, bg=FAKE_UI_BG_COLOR)
    nav_column.pack(side='left', fill='y')
    nav_column.pack_propagate(False)

    icon_count = len(LEFT_RAIL_ICON_DRAWERS)
    if nav_rail_height is None:
        actual_nav_rail_height = NAV_RAIL_HEIGHT
        icon_gap = NAV_RAIL_ICON_GAP
    else:
        actual_nav_rail_height = nav_rail_height
        available_for_icons = actual_nav_rail_height - NAV_RAIL_CAP_PADDING * 2
        icon_gap = (available_for_icons - icon_count * RAIL_ICON_SIZE) / (icon_count - 1)

    nav_rail_canvas = tk.Canvas(nav_column, width=NAV_RAIL_WIDTH, height=actual_nav_rail_height,
                                 bg=FAKE_UI_BG_COLOR, highlightthickness=0)
    if nav_rail_height is None:
        nav_rail_canvas.pack(expand=True)
    else:
        nav_rail_canvas.pack(pady=(nav_rail_top_offset, 0))

    # 膠囊背景：radius 用寬度的一半，兩端才會是完整半圓形（藥丸形狀），
    # 顏色刻意比 FAKE_UI_BG_COLOR 更深，跟外圍區分開來。
    screen_gallery.draw_rounded_rect(nav_rail_canvas, 0, 0, NAV_RAIL_WIDTH, actual_nav_rail_height,
                                      NAV_RAIL_WIDTH / 2, fill=NAV_RAIL_CAPSULE_COLOR)

    icon_photo_refs = []
    for index, (icon_key, source) in enumerate(LEFT_RAIL_ICON_DRAWERS):
        # 2026-08-25 optical size 一致性調整：實際「畫多大」查
        # LEFT_RAIL_ICON_RENDER_SIZES（每個 icon 各自的值，補償留白差異），
        # 但排版用的 slot 高度/間距（y_center 算式）繼續固定用
        # RAIL_ICON_SIZE，兩者刻意分開，換不同 size 的 icon 不會影響
        # 版面間距——create_image() 用中心點定位，置中自動維持。
        render_size = LEFT_RAIL_ICON_RENDER_SIZES.get(icon_key, RAIL_ICON_SIZE)
        if isinstance(source, str):
            # PNG asset（見 LEFT_RAIL_ICON_DRAWERS 上面的說明）：直接載入
            # 檔案 + LANCZOS 縮到 render_size。
            icon_photo = _load_png_asset_photo(source, render_size)
        else:
            # 還在用 Pillow 即時畫的 icon：source 是函式，吃
            # (draw, w, h, scale, color=...)，交給 render_supersampled()。
            icon_photo = _get_icon_photo(icon_key, source, render_size)
        icon_photo_refs.append(icon_photo)
        y_center = NAV_RAIL_CAP_PADDING + RAIL_ICON_SIZE / 2 + index * (RAIL_ICON_SIZE + icon_gap)
        nav_rail_canvas.create_image(NAV_RAIL_WIDTH / 2, y_center, image=icon_photo)
    nav_rail_canvas.icon_photo_refs = icon_photo_refs  # 保留參照，避免被 GC 回收

    return rail


# 2026-08-20 使用者比對 HCP3 實機圖要求：右側「－」「＋」再往右移，比較
# 接近實機比例——這是這一輪唯二保留下來的新增項目之一（另一個是 Outer
# Status Column 的 PC 時間）。其餘（高度／左右延伸／黑色／中間 Menu/MAX/
# icon 位置／左側「－」「＋」）明確不動，只調這一個位移量。
RIGHT_PLUS_MINUS_EXTRA_SHIFT = 46


# 2026-08-19 使用者第四次對照原圖要求：Bottom Bar 兩端的「－」「＋」放大
# 約 1.5~2 倍。
#
# 2026-08-20 曾經試過改成 Canvas 畫線段（把長度跟粗細分開控制），但
# 使用者回報「現在太醜」，改回文字字型版本，不再嘗試 Canvas 畫法。
PLUS_MINUS_SIZE_SCALE = 1.75


def _add_plus_minus_group(bar, side, center_x=None):
    """Bottom Bar 一組「－」「＋」文字（純 Label，不用圖片），顏色比照
    參考圖：「－」偏藍、「＋」偏紅，放大 PLUS_MINUS_SIZE_SCALE 倍。

    2026-08-19 使用者要求：左側這組要對齊 Recording/Event/Photo Panel
    水平中心，右側對齊 Preview Area 水平中心，不是貼齊整個 Bottom Bar
    左右邊界——center_x 有給的話用 place() 定位在那個絕對 x 座標（由
    DA005ControlPanel.pyw 用 screen_recording 已公開的 PANEL_WIDTH/
    LEFT_NAV_PADX/RIGHT_AREA_PADX/PREVIEW_SIZE 常數＋LEFT_RAIL_WIDTH
    算好傳進來），沒給的話（standalone 測試）退回貼齊 bar 左右邊界的
    舊行為。

    2026-08-20 使用者要求：右側這組再往右移 RIGHT_PLUS_MINUS_EXTRA_SHIFT
    像素，比較接近 HCP3 實機比例——只有 side='right' 才加這個偏移，左側
    對齊 Panel 水平中心的位置維持不變。"""
    group = tk.Frame(bar, bg=BOTTOM_BAR_BG_COLOR)
    font_size = round(14 * PLUS_MINUS_SIZE_SCALE)
    label_padx = round(10 * PLUS_MINUS_SIZE_SCALE)
    # 2026-08-20 使用者要求「減號拉長一點，跟＋的橫槓一樣長」：實測同一個
    # font size 下，全形「－」(U+FF0D) 渲染寬度只有 14px，「＋」(U+FF0B)
    # 是 24px。換成數學符號「−」MINUS SIGN (U+2212) 同樣 font size 量出來
    # 是 22px，非常接近＋的橫槓長度，而且高度（筆畫粗細）跟原本的「－」
    # 完全一樣（都是 5px），不會變粗，純粹只是換一個字元、不是改字型大小。
    tk.Label(group, text='−', bg=BOTTOM_BAR_BG_COLOR, fg='#5b8dff',
              font=('Calibri', font_size, 'bold')).pack(side='left', padx=label_padx)
    tk.Label(group, text='＋', bg=BOTTOM_BAR_BG_COLOR, fg='#ff5b5b',
              font=('Calibri', font_size, 'bold')).pack(side='left', padx=label_padx)
    if center_x is None:
        group.pack(side=side, padx=28)
    else:
        actual_center_x = center_x + RIGHT_PLUS_MINUS_EXTRA_SHIFT if side == 'right' else center_x
        group.place(x=actual_center_x, rely=0.5, anchor='center')


def _add_bottom_center_item(center_group, label_text, icon_filename):
    """Bottom Bar 中間一組「icon + 文字」（Menu / MAX / 後擋風玻璃除霧），
    文字為空字串時只顯示 icon（後擋參考圖沒有文字標籤）。icon_filename
    是 assets/icons/ 底下的 PNG 檔名，用跟 Left Rail 同一套
    _load_png_asset_photo() 載入。"""
    item = tk.Frame(center_group, bg=BOTTOM_BAR_BG_COLOR)
    item.pack(side='left', padx=18)
    icon_canvas = tk.Canvas(item, width=BOTTOM_ICON_SIZE, height=BOTTOM_ICON_SIZE,
                             bg=BOTTOM_BAR_BG_COLOR, highlightthickness=0)
    icon_photo = _load_png_asset_photo(icon_filename, BOTTOM_ICON_SIZE)
    icon_canvas.icon_photo_ref = icon_photo  # 保留參照，避免被 GC 回收
    icon_canvas.create_image(BOTTOM_ICON_SIZE / 2, BOTTOM_ICON_SIZE / 2, image=icon_photo)
    icon_canvas.pack()
    # 2026-08-20 使用者回報「上下需要對齊，MAX圖稍微高了一點」：原因是
    # 第三顆（無文字）沒有建立 Label，item 高度只有 icon 高度，pack 置中
    # 之後三個 item 的 icon 位置會跟有文字的 Menu/MAX 對不齊。改成永遠
    # 建立 Label（文字空字串時視覺上看不到，但保留同樣的行高），三個
    # item 高度一致，icon 才會對齊在同一個高度。
    tk.Label(item, text=label_text, bg=BOTTOM_BAR_BG_COLOR, fg=FAKE_UI_ICON_COLOR,
              font=('Calibri', 9)).pack()


def build_bottom_bar(parent, left_group_center_x=None, right_group_center_x=None):
    """建立下方假控制列：中間 Menu / MAX / 送風模式維持在 bar 正中央
    （使用者確認這部分不用改），左右兩端各一組「－」「＋」，純視覺，
    不綁任何 command/api。真機同一條 bar 在 Recording 跟 Player 畫面都有
    拍到（參考圖4.jpg），所以統一在所有畫面顯示，不分畫面別。

    2026-08-19 使用者第四次對照原圖要求：左側「－」「＋」對齊 Recording/
    Event/Photo Panel 水平中心，右側對齊 Preview Area 水平中心（不是
    貼齊整個 Bottom Bar 左右邊界），並放大約 1.75 倍——
    left_group_center_x/right_group_center_x 由 DA005ControlPanel.pyw
    算好傳進來，沒傳的話（standalone 測試）退回貼齊 bar 邊界的舊行為。

    2026-08-19 使用者最後一輪視覺調整：這條 bar 維持獨立的
    BOTTOM_BAR_BG_COLOR（目前最深的黑色），明確不跟 Top Bar/Left Rail/
    Main Content 統一的淺灰背景（FAKE_UI_BG_COLOR）連動。"""
    bar = tk.Frame(parent, height=BOTTOM_BAR_HEIGHT, bg=BOTTOM_BAR_BG_COLOR)
    bar.pack_propagate(False)

    _add_plus_minus_group(bar, 'left', center_x=left_group_center_x)
    _add_plus_minus_group(bar, 'right', center_x=right_group_center_x)

    center_group = tk.Frame(bar, bg=BOTTOM_BAR_BG_COLOR)
    center_group.place(relx=0.5, rely=0.5, anchor='center')
    _add_bottom_center_item(center_group, 'Menu', 'menu_fan.png')
    _add_bottom_center_item(center_group, 'MAX', 'max_defrost.png')
    _add_bottom_center_item(center_group, '', 'rear_defrost.png')

    return bar
