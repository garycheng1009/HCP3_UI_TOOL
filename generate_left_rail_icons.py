"""產生 Left Rail 用的高解析度透明 PNG icon asset（assets/icons/）。

這是一次性的產生工具，不是 App 執行時會用到的模組——App（screen_fake_ui.py
的 _load_png_asset_photo()）只負責載入這裡產生好的 PNG 檔案 + 依實際顯示
尺寸用 LANCZOS 縮小，不會即時重畫。修改 icon 造型/線條粗細時，改這支
檔案裡對應的 draw_xxx() 函式，重新執行一次（python generate_left_rail_
icons.py）就會覆蓋 assets/icons/ 底下的 PNG，不需要重開 App 的其他程式碼。

2026-08-20 重要教訓（icon 顏色/亮度不一致問題的根本原因）：
ASSET_SIZE 必須跟實際顯示尺寸（screen_fake_ui.RAIL_ICON_SIZE=32、
BELL_ICON_SIZE=38）保持在「4x～8x」的合理倍數內。最早這幾個 icon 用
ASSET_SIZE=512 產生，實際顯示只有 32px，等於一次縮小 16 倍——畫的時候看起來
線條夠粗，但縮小 16 倍之後線寬變成不到 1px，反鋸齒讓這種次像素寬度的線條
呈現半透明/偏灰色，在黑色背景上就像「顏色太暗、快融入背景」，其實顏色數值
從頭到尾沒有錯，純粹是縮放倍率太誇張造成的視覺假象。現在固定用
ASSET_SIZE=256（對 32px 是 8 倍、對 38px 的 Bell 約 6.7 倍），之後新增 icon
也要維持在這個倍率範圍內，不要為了「看起來更精緻」隨便調大 ASSET_SIZE。
"""
import math

from PIL import Image, ImageDraw

ASSET_SIZE = 256            # 對應實際顯示尺寸（32~38px）的 4x~8x，不要隨便調大
SUPERSAMPLE = 4              # 內部再放大幾倍畫，畫完縮小到 ASSET_SIZE，消除鋸齒
ICON_COLOR = (201, 201, 209, 255)    # 跟 screen_fake_ui.FAKE_UI_ICON_COLOR 一致
WARNING_COLOR = (245, 197, 24, 255)  # 跟原本鈴鐺警示三角形的黃色一致

OUTPUT_DIR = 'assets/icons'


def render(draw_callback, filename):
    big_size = ASSET_SIZE * SUPERSAMPLE
    image = Image.new('RGBA', (big_size, big_size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)
    draw_callback(draw, big_size, big_size, SUPERSAMPLE)
    final = image.resize((ASSET_SIZE, ASSET_SIZE), Image.LANCZOS)
    path = f'{OUTPUT_DIR}/{filename}'
    final.save(path)
    print(f'saved {path}')
    return final


def draw_home(draw, w, h, scale):
    """房屋輪廓：尖屋頂 + 屋身矩形 + 中間門/內框（開口朝下的凹字形）。

    2026-08-20 使用者回報「改完 ASSET_SIZE 之後看起來還是太暗太細」，
    實際驗算發現前一版的算法是錯的：supersample 畫完再縮小回 ASSET_SIZE
    這一步，會把 stroke=round(X*scale) 除回 X（scale 在乘跟除兩邊剛好
    抵銷），不是「改小 ASSET_SIZE 就自動變粗」。真正決定最終顯示粗細的是
    X 本身乘上「ASSET_SIZE / 實際顯示尺寸」這個比例——原本 X=5.0，
    ASSET_SIZE=256、實際顯示 32px，算出來最終只有 0.625px，難怪反鋸齒
    之後看起來偏灰。這裡把 X 提高到 18（= 目標最終顯示粗細 2.2px ×
    256/32 的縮小比例），跟 Antenna/Grid 那幾個「看起來沒問題」的 icon
    （實測最終顯示粗細約 1.75~2.0px）同一個等級。

    2026-08-25 第二輪：screen_fake_ui.py 把 Home 的實際顯示尺寸從 32
    調到 36（optical size 一致性調整）之後，同一個 stroke=18 換算出來的
    最終顯示粗細從 2.25px 變成 2.53px（display_size 變大、stroke 沒跟著
    調小，等於變相加粗），使用者回報「線條偏粗」。這裡降到 15
    （= 目標最終顯示粗細 2.11px × 256/36），收斂回接近 Antenna/Grid 的
    1.75~2.0px 基準，只是小幅調整，不是要跟它們完全一樣細（Home 線條
    比較複雜，太細會糊）。"""
    stroke = round(15.0 * scale)
    body_top = h * 0.5
    body_bottom = h * 0.82
    left_x, right_x = w * 0.16, w * 0.84
    draw.line([(left_x, body_top), (w * 0.5, h * 0.15)], fill=ICON_COLOR, width=stroke, joint='curve')
    draw.line([(w * 0.5, h * 0.15), (right_x, body_top)], fill=ICON_COLOR, width=stroke, joint='curve')
    draw.line([(left_x, body_top), (left_x, body_bottom)], fill=ICON_COLOR, width=stroke, joint='curve')
    draw.line([(right_x, body_top), (right_x, body_bottom)], fill=ICON_COLOR, width=stroke, joint='curve')
    draw.line([(left_x, body_bottom), (right_x, body_bottom)], fill=ICON_COLOR, width=stroke, joint='curve')
    door_left, door_right = w * 0.40, w * 0.60
    door_top = h * 0.58
    draw.line([(door_left, body_bottom), (door_left, door_top)], fill=ICON_COLOR, width=stroke, joint='curve')
    draw.line([(door_left, door_top), (door_right, door_top)], fill=ICON_COLOR, width=stroke, joint='curve')
    draw.line([(door_right, door_top), (door_right, body_bottom)], fill=ICON_COLOR, width=stroke, joint='curve')


def draw_navigation(draw, w, h, scale):
    """朝上的細長導航箭頭：頂端尖、兩側肩膀最寬、底部中央向內收縮
    （不是一般朝右的空心三角形/play 鍵）。

    2026-08-20 stroke 倍率算法修正（見 draw_home() 開頭的說明），目標
    最終顯示粗細比 Home 粗一點（使用者要求「線條加粗」）。使用者回報
    24（→3.0px）「稍微細一點點」就好，這裡降到 20（= 目標最終顯示粗細
    2.5px × 256/32），這輪維持這個 stroke 不變。

    2026-08-20 第二輪：使用者要求「維持目前造型與 stroke，整體稍微拉高、
    縮窄，底部 V 型內縮再明顯一點」——只調整下面幾個座標點的比例，
    stroke 完全不動：垂直範圍從 0.08~0.72（span 0.64）拉高到 0.05~0.76
    （span 0.71）；水平範圍從 0.16~0.84（span 0.68）縮窄到 0.22~0.78
    （span 0.56）；底部凹口從 0.46（跟肩膀 0.72 差 0.26）內縮到 0.40
    （跟新肩膀 0.76 差 0.36），V 型更明顯。

    2026-08-25 第三輪：screen_fake_ui.py 把 Navigation 的實際顯示尺寸從
    32 調到 37（optical size 一致性調整）之後，同一個 stroke=20 換算出來
    的最終顯示粗細從 2.5px 變成 2.89px，使用者回報「線條偏粗」。這裡降到
    16（= 目標最終顯示粗細 2.31px × 256/37），理由跟 draw_home() 這輪的
    調整一致。"""
    stroke = round(16.0 * scale)
    top = (w * 0.5, h * 0.05)
    right_shoulder = (w * 0.78, h * 0.76)
    bottom_notch = (w * 0.5, h * 0.40)
    left_shoulder = (w * 0.22, h * 0.76)
    points = [top, right_shoulder, bottom_notch, left_shoulder]
    for i in range(len(points)):
        draw.line([points[i], points[(i + 1) % len(points)]], fill=ICON_COLOR, width=stroke,
                  joint='curve')


def draw_phone(draw, w, h, scale):
    """直立圓角矩形（偏寬，不要細長），上下各一小段短橫線。

    2026-08-20 stroke 倍率算法修正（見 draw_home() 開頭的說明），使用者
    要求「線條明顯加粗」。回報 26（→3.25px）「稍微細一點點」就好，
    降到 22（= 目標最終顯示粗細 2.75px × 256/32，仍是這幾個 icon 裡最
    粗的一個），這輪維持這個 stroke 不變。

    2026-08-20 第二輪：使用者要求「整體尺寸放大約 10~15%，寬度也稍微
    增加，但不要增加 stroke 粗細」——只放大矩形本身的座標範圍，stroke
    完全不動：垂直範圍從 0.15~0.85（span 0.70）放大到 0.11~0.89
    （span 0.78，+11%，在 10~15% 範圍內）；水平範圍從 0.30~0.70
    （span 0.40）放大到 0.26~0.74（span 0.48，+20%，比垂直方向多放大
    一點，對應「寬度也稍微增加」）。上下短橫線位置跟著新的矩形範圍等比
    調整，維持在「離矩形上/下邊緣同樣距離」的相對位置。

    2026-08-25 第三輪：使用者這次把 Phone 在 screen_fake_ui.py 裡的實際
    顯示尺寸從 32 調到 38（optical size 一致性調整），加上要求「線條再
    稍微細一點」。stroke 參數（相對於 ASSET_SIZE=256）跟最終顯示粗細的
    換算關係是 final_px = stroke / (256/display_size)，同一個 stroke 值，
    display_size 從 32 變 38 反而會讓線條變粗（22/(256/32)=2.75px ->
    22/(256/38)=3.27px），跟使用者要的方向相反，所以這裡不是維持 22，
    是要降更多才能同時做到「顯示變大」+「線條變細」兩件事。目標最終顯示
    粗細抓 2.3px（比原本 32px 顯示時的 2.75px 再細一點），對應
    stroke = 2.3 * (256/38) ≈ 15.5，取整數 16。

    2026-08-25 第四輪：使用者真機看過後回報「外框線條稍微還是偏粗」，
    這裡再降到 13（= 目標最終顯示粗細 1.93px × 256/38），收斂到接近
    Antenna/Grid 的 1.75~2.0px 基準。"""
    stroke = round(13.0 * scale)
    left_x, right_x = w * 0.26, w * 0.74
    top_y, bottom_y = h * 0.11, h * 0.89
    radius = w * 0.05
    draw.rounded_rectangle([left_x, top_y, right_x, bottom_y], radius=radius,
                            outline=ICON_COLOR, width=stroke)
    draw.line([(w * 0.40, h * 0.21), (w * 0.60, h * 0.21)], fill=ICON_COLOR, width=stroke)
    draw.line([(w * 0.40, h * 0.79), (w * 0.60, h * 0.79)], fill=ICON_COLOR, width=stroke)


def draw_bell(draw, w, h, scale):
    """細長流線型鐘身（上窄下寬，肩部＋鐘口）+ 底部 clapper + 右上角黃色
    警示三角形（內含驚嘆號）。

    2026-08-20 stroke 倍率算法修正（見 draw_home() 開頭的說明）。Bell
    顯示尺寸是 38px（BELL_ICON_SIZE，不是 32px 的 RAIL_ICON_SIZE），
    縮小比例是 256/38≈6.74 倍，不是 8 倍——X 提高到 14（= 目標最終顯示
    粗細 2.1px × 6.74），比 Phone/Navigation 細一點，維持「保持細長
    流線型」的要求，但不能再像原本 3.2 那樣變成次像素寬度。"""
    stroke = round(14.0 * scale)
    cx = w * 0.42
    dome_cy = h * 0.30
    rx = w * 0.17
    ry = h * 0.20
    num_segments = 48
    points = []
    for i in range(num_segments + 1):
        theta = math.pi + (math.pi * i / num_segments)
        x = cx + rx * math.cos(theta)
        y = dome_cy + ry * math.sin(theta)
        points.append((x, y))
    draw.line(points, fill=ICON_COLOR, width=stroke, joint='curve')

    left_shoulder = points[0]
    right_shoulder = points[-1]
    left_base = (cx - w * 0.24, h * 0.62)
    right_base = (cx + w * 0.24, h * 0.62)
    draw.line([left_shoulder, left_base], fill=ICON_COLOR, width=stroke, joint='curve')
    draw.line([right_shoulder, right_base], fill=ICON_COLOR, width=stroke, joint='curve')
    draw.line([left_base, right_base], fill=ICON_COLOR, width=stroke)
    clapper_r = w * 0.028
    clapper_cy = h * 0.72
    draw.ellipse([cx - clapper_r, clapper_cy - clapper_r, cx + clapper_r, clapper_cy + clapper_r],
                 fill=ICON_COLOR)

    tri_cx, tri_top_y = w * 0.78, h * 0.04
    tri_half_w, tri_h = w * 0.15, h * 0.20
    tri_bottom_y = tri_top_y + tri_h
    draw.polygon([(tri_cx, tri_top_y), (tri_cx - tri_half_w, tri_bottom_y),
                  (tri_cx + tri_half_w, tri_bottom_y)], fill=WARNING_COLOR)
    # 驚嘆號同樣要套用修正後的粗細算法（X=10 => 最終顯示粗細約 1.5px），
    # 不然會跟修正前的鐘身一樣變成次像素寬度、幾乎看不見。
    mark_stroke = round(10.0 * scale)
    mark_top = tri_top_y + tri_h * 0.32
    mark_bottom = tri_top_y + tri_h * 0.68
    mark_color = (46, 42, 20, 255)
    draw.line([(tri_cx, mark_top), (tri_cx, mark_bottom)], fill=mark_color, width=mark_stroke,
              joint='curve')
    dot_r = mark_stroke * 0.6
    dot_cy = tri_top_y + tri_h * 0.82
    draw.ellipse([tri_cx - dot_r, dot_cy - dot_r, tri_cx + dot_r, dot_cy + dot_r], fill=mark_color)


if __name__ == '__main__':
    render(draw_home, 'home.png')
    render(draw_navigation, 'navigation.png')
    render(draw_phone, 'phone.png')
    render(draw_bell, 'bell.png')
