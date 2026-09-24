"""產生 DA005 DVR Control Panel 的 App/Title Bar Icon（.ico）。

跟 generate_left_rail_icons.py／generate_bottom_bar_icons.py 同一種一次性
產生工具的定位：這支檔案不會被正式程式 import，只在需要重新產生/調整
Icon 造型時手動執行（python generate_app_icon.py）。

配色沿用現有 UI 已經在用的兩個顏色，不新增任何顏色：
- 背景：screen_fake_ui.NAV_RAIL_CAPSULE_COLOR（Navigation Rail 黑色膠囊背景）
- 線條：screen_fake_ui.FAKE_UI_ICON_COLOR（淺灰白，其餘 UI icon 共用的線條色）
背景用實心方塊（不透明），不管 Windows 標題列/工作列當下是什麼主題色，
Icon 本身自帶深色底，線條永遠維持足夠對比，不會被淺色主題背景吃掉。

2026-08-25 第二輪：使用者真機看過第一版（機身+鏡頭+固定支架三個元素）
在 16px 縮到最小時線條糊在一起（機身太扁、支架細線擠在一起），決定改成
「兩套造型」：
- Full（32px 以上）：機身+鏡頭+固定支架，三個元素，看得出是吸附在
  擋風玻璃上的行車紀錄器。
- Simple（16/24px）：拿掉支架，只留機身+鏡頭這兩個最關鍵的元素，省下的
  垂直空間拿來把機身/鏡頭放大、線條加粗一點，在最小尺寸下維持清楚。
.ico 用 Pillow 的 append_images 手動組合兩套不同來源的畫面到同一個檔案，
不是單純把同一張圖 resize 成多種尺寸（那樣小尺寸還是會糊）。
"""
from PIL import Image, ImageDraw

ASSET_SIZE = 256
SUPERSAMPLE = 4
BG_COLOR = (13, 13, 16, 255)      # NAV_RAIL_CAPSULE_COLOR (#0d0d10)
ICON_COLOR = (201, 201, 209, 255)  # FAKE_UI_ICON_COLOR (#c9c9d1)

OUTPUT_PNG_FULL = 'assets/app_icon_preview_full.png'
OUTPUT_PNG_SIMPLE = 'assets/app_icon_preview_simple.png'
OUTPUT_ICO = 'assets/app_icon.ico'

SIMPLE_SIZES = [16, 24]        # 這兩個尺寸太小，支架容易糊在一起，改用簡化版
FULL_SIZES = [32, 48, 64, 128, 256]


def draw_dashcam_full(draw, w, h, scale):
    """機身：偏寬的圓角矩形 + 鏡頭圓圈 + 固定支架/吸盤底座。支架這個元素
    是「看得出是行車紀錄器、不是普通相機」的關鍵線索（暗示吸附在擋風
    玻璃上），32px 以上才用這個完整版。"""
    stroke = round(9.0 * scale)

    body_left, body_right = w * 0.16, w * 0.84
    body_top, body_bottom = w * 0.42, w * 0.70
    radius = w * 0.04
    draw.rounded_rectangle([body_left, body_top, body_right, body_bottom],
                            radius=radius, outline=ICON_COLOR, width=stroke)

    lens_cx, lens_cy = w * 0.40, (body_top + body_bottom) / 2
    lens_r = w * 0.12
    draw.ellipse([lens_cx - lens_r, lens_cy - lens_r, lens_cx + lens_r, lens_cy + lens_r],
                 outline=ICON_COLOR, width=stroke)

    mount_cx = (body_left + body_right) / 2
    arm_top_y = w * 0.22
    draw.line([(mount_cx, body_top), (mount_cx, arm_top_y)], fill=ICON_COLOR, width=stroke,
              joint='curve')
    mount_r = w * 0.07
    draw.ellipse([mount_cx - mount_r, arm_top_y - mount_r * 0.6,
                  mount_cx + mount_r, arm_top_y + mount_r * 0.6],
                 outline=ICON_COLOR, width=stroke)


def draw_dashcam_simple(draw, w, h, scale):
    """16/24px 專用簡化版：拿掉固定支架，只留機身+鏡頭。省下支架原本
    佔用的上方空間，機身拉高、鏡頭放大，線條也加粗一點，避免小尺寸縮小
    後外框上下兩條線糊在一起。"""
    stroke = round(13.0 * scale)

    body_left, body_right = w * 0.10, w * 0.90
    body_top, body_bottom = w * 0.28, w * 0.76
    radius = w * 0.06
    draw.rounded_rectangle([body_left, body_top, body_right, body_bottom],
                            radius=radius, outline=ICON_COLOR, width=stroke)

    lens_cx, lens_cy = w * 0.38, (body_top + body_bottom) / 2
    lens_r = w * 0.15
    draw.ellipse([lens_cx - lens_r, lens_cy - lens_r, lens_cx + lens_r, lens_cy + lens_r],
                 outline=ICON_COLOR, width=stroke)


def render_master(draw_callback):
    big_size = ASSET_SIZE * SUPERSAMPLE
    image = Image.new('RGBA', (big_size, big_size), BG_COLOR)
    draw = ImageDraw.Draw(image)
    draw_callback(draw, big_size, big_size, SUPERSAMPLE)
    return image.resize((ASSET_SIZE, ASSET_SIZE), Image.LANCZOS)


def render():
    full_master = render_master(draw_dashcam_full)
    simple_master = render_master(draw_dashcam_simple)
    full_master.save(OUTPUT_PNG_FULL)
    simple_master.save(OUTPUT_PNG_SIMPLE)
    print(f'saved {OUTPUT_PNG_FULL}')
    print(f'saved {OUTPUT_PNG_SIMPLE}')

    frames = [simple_master.resize((size, size), Image.LANCZOS) for size in SIMPLE_SIZES]
    frames += [full_master.resize((size, size), Image.LANCZOS) for size in FULL_SIZES]
    # PIL 的 ICO writer 用「當作 self 呼叫 .save() 的那張圖」的尺寸當上限，
    # 比它大的 size 會被直接跳過（見 IcoImagePlugin._save() 的
    # `if size[0] > width or size[1] > height: continue`）——一定要用
    # 最大張的當 base image 呼叫 save()，其餘（含小尺寸的簡化版）才會
    # 全部用 append_images 原封不動收進去，不是被拿去做放大版的 resize。
    frames.sort(key=lambda im: im.size[0], reverse=True)

    frames[0].save(OUTPUT_ICO, format='ICO', sizes=[im.size for im in frames],
                   append_images=frames[1:])
    print(f'saved {OUTPUT_ICO} (sizes={sorted(im.size for im in frames)})')


if __name__ == '__main__':
    render()
