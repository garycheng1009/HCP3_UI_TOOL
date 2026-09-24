"""產生 Bottom Bar 中央 3 個 HVAC icon 的高解析度透明 PNG asset
（assets/icons/menu_fan.png、max_defrost.png、rear_defrost.png）。

跟 generate_left_rail_icons.py 同一套原則、同一個「教訓」：ASSET_SIZE
必須跟實際顯示尺寸（screen_fake_ui.BOTTOM_ICON_SIZE=26）維持在合理倍數
內（這裡用 208 = 26 的 8 倍），而且 stroke 倍率 X 要用「目標最終顯示
粗細 × (ASSET_SIZE / 實際顯示尺寸)」反推，不能直接套一個看起來合理的
小數字，不然縮到 26px 之後線條會變成次像素寬度、看起來偏暗/鋸齒。

造型依照使用者提供的 HCP3 實機照片（下方圖.jpg）重製：
- Menu：4 片彎曲花瓣繞中心排列（不是簡單的橢圓扇葉）+ 中心小圓點。
- MAX：外框是頂端微凸的弧形擋風玻璃（不是矩形），內部 3 條由下往上的
  除霧線（直線+小箭頭）。
- 第三顆（後擋風玻璃除霧）：矩形窗框（不是弧形），內部一樣 3 條除霧線，
  右下角多一個小梯形（照後鏡）造型。
"""
import math

from PIL import Image, ImageDraw

ASSET_SIZE = 208             # BOTTOM_ICON_SIZE(26) 的 8 倍，跟 Left Rail 同一套倍率原則
SUPERSAMPLE = 4
ICON_COLOR = (201, 201, 209, 255)   # 跟 FAKE_UI_ICON_COLOR 一致

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


def _fan_blade_points(cx, cy, length, width, rotation_deg, curve_amount, num_points=24):
    """算出一片「風扇葉片」的實心多邊形頂點：頭尾都收尖於局部座標
    (0,0)／(length,0)（寬度用 sin 曲線從 0 脹到 width 再收回 0，跟舊版
    花瓣一樣），但中心線額外加上 curve_amount*sin(pi*t) 的側向偏移，讓
    整片葉片本身帶弧度（像香蕉/逗點形狀），4 片同方向偏移排列起來才會
    有「旋轉方向感」，不是對稱的直柳葉。

    2026-08-20 使用者回報「像四葉窗格，不像風扇」：舊版是用 draw.line()
    描邊畫花瓣外框，中空的花瓣在 26px 縮小後只剩邊框像素，看起來像方格。
    改成 draw.polygon() 實心填滿 + 帶弧度，在小尺寸下才會讀出「風扇葉片」
    而不是「窗格」。"""
    rot = math.radians(rotation_deg)
    cos_r, sin_r = math.cos(rot), math.sin(rot)
    top_edge = []
    bottom_edge = []
    for i in range(num_points + 1):
        t = i / num_points
        local_x = length * t
        centerline_y = curve_amount * math.sin(math.pi * t)
        half_width = width * math.sin(math.pi * t) * 0.5
        top_edge.append((local_x, centerline_y - half_width))
        bottom_edge.append((local_x, centerline_y + half_width))
    local_points = top_edge + list(reversed(bottom_edge))
    return [(cx + lx * cos_r - ly * sin_r, cy + lx * sin_r + ly * cos_r) for lx, ly in local_points]


def draw_menu_fan(draw, w, h, scale):
    """四葉風扇：4 片實心、帶弧度的扇葉（尖端對齊中心，向外延伸，同方向
    彎曲呈現旋轉感）+ 中心小圓點，對照 HCP3 實機圖的風扇造型。"""
    cx, cy = w * 0.5, h * 0.5
    blade_length, blade_width = w * 0.36, w * 0.22
    curve_amount = w * 0.12
    for angle_deg in (45, 135, 225, 315):
        points = _fan_blade_points(cx, cy, blade_length, blade_width, angle_deg, curve_amount)
        draw.polygon(points, fill=ICON_COLOR)
    center_r = w * 0.05
    draw.ellipse([cx - center_r, cy - center_r, cx + center_r, cy + center_r], fill=ICON_COLOR)


def _draw_up_arrow(draw, x, y_bottom, y_top, stroke, head_size):
    draw.line([(x, y_bottom), (x, y_top)], fill=ICON_COLOR, width=stroke)
    draw.line([(x - head_size, y_top + head_size), (x, y_top), (x + head_size, y_top + head_size)],
              fill=ICON_COLOR, width=stroke, joint='curve')


def draw_max_defrost(draw, w, h, scale):
    """前擋風玻璃除霧：外框是頂端微凸的弧形擋風玻璃輪廓（不是矩形/
    梯形直角），內部 3 條由下往上的除霧線（直線+箭頭）。

    2026-08-20 使用者要求「造型保留，整體視覺尺寸放大約 10%」：造型
    完全不變，只是把所有比例座標以圖示中心 (0.5, 0.5) 為基準等比放大
    1.1 倍（MAX_SCALE），stroke 粗細不變，維持原本的線條視覺重量。"""
    MAX_SCALE = 1.10

    def scaled(ratio, center=0.5):
        return center + (ratio - center) * MAX_SCALE

    stroke = round(16.0 * scale)
    cx = w * 0.5
    top_y, bottom_y = h * scaled(0.16), h * scaled(0.62)
    half_w_top, half_w_bottom = w * 0.34 * MAX_SCALE, w * 0.22 * MAX_SCALE
    bulge = h * 0.08 * MAX_SCALE
    num_curve_points = 24
    curve_points = []
    for i in range(num_curve_points + 1):
        t = i / num_curve_points
        x = cx - half_w_top + 2 * half_w_top * t
        # 中間（t=0.5）往上凸出 bulge（y 變小），兩端（t=0/1）回到
        # top_y，形成頂端微凸的弧形（不是直線頂邊）。
        y = top_y - bulge + bulge * (2 * t - 1) ** 2
        curve_points.append((x, y))
    outline = curve_points + [
        (cx + half_w_bottom, bottom_y),
        (cx - half_w_bottom, bottom_y),
        curve_points[0],
    ]
    draw.line(outline, fill=ICON_COLOR, width=stroke, joint='curve')

    arrow_top = top_y + h * 0.14 * MAX_SCALE
    arrow_bottom = bottom_y - h * 0.10 * MAX_SCALE
    head_size = w * 0.045 * MAX_SCALE
    arrow_stroke = round(12.0 * scale)
    for x_ratio in (scaled(0.5 - 0.14), scaled(0.5), scaled(0.5 + 0.14)):
        _draw_up_arrow(draw, w * x_ratio, arrow_bottom, arrow_top, arrow_stroke, head_size)


def draw_rear_defrost(draw, w, h, scale):
    """後擋風玻璃除霧：矩形窗框（不是弧形）+ 內部 3 條除霧線 + 右下角
    小三角形（照後鏡）造型，不加文字。

    2026-08-20 使用者要求「造型方向保留，整體放大約 15~20%，並簡化細節
    避免縮小後糊成一團」：
    - 座標以窗框中心 (0.44, 0.42) 為基準等比放大 1.175 倍（REAR_SCALE，
      落在使用者要求的 15~20% 中間值）。
    - 簡化：照後鏡從「4 個端點的空心折線」簡化成「單一實心小三角形」
      （少一半的邊、實心比空心在小尺寸下更清楚），減少縮圖後糊成一團
      的細節量。"""
    REAR_SCALE = 1.175
    center_x_ratio, center_y_ratio = 0.44, 0.42

    def scaled_x(ratio):
        return center_x_ratio + (ratio - center_x_ratio) * REAR_SCALE

    def scaled_y(ratio):
        return center_y_ratio + (ratio - center_y_ratio) * REAR_SCALE

    stroke = round(16.0 * scale)
    rect_left, rect_right = w * scaled_x(0.16), w * scaled_x(0.72)
    rect_top, rect_bottom = h * scaled_y(0.20), h * scaled_y(0.64)
    radius = w * 0.03 * REAR_SCALE
    draw.rounded_rectangle([rect_left, rect_top, rect_right, rect_bottom], radius=radius,
                            outline=ICON_COLOR, width=stroke)

    arrow_top = rect_top + h * 0.10 * REAR_SCALE
    arrow_bottom = rect_bottom - h * 0.10 * REAR_SCALE
    head_size = w * 0.045 * REAR_SCALE
    arrow_stroke = round(12.0 * scale)
    cx = (rect_left + rect_right) / 2
    span = (rect_right - rect_left) * 0.28
    for x in (cx - span, cx, cx + span):
        _draw_up_arrow(draw, x, arrow_bottom, arrow_top, arrow_stroke, head_size)

    # 右下角小三角形（照後鏡），從窗框右下角往外延伸；簡化成單一實心
    # 三角形（原本是 4 點空心折線），細節更少、在小尺寸下更清楚。
    mirror_points = [
        (rect_right, rect_bottom - h * 0.10 * REAR_SCALE),
        (w * scaled_x(0.92), h * scaled_y(0.70)),
        (rect_right - w * 0.02 * REAR_SCALE, rect_bottom + h * 0.02 * REAR_SCALE),
    ]
    draw.polygon(mirror_points, fill=ICON_COLOR)


if __name__ == '__main__':
    render(draw_menu_fan, 'menu_fan.png')
    render(draw_max_defrost, 'max_defrost.png')
    render(draw_rear_defrost, 'rear_defrost.png')
