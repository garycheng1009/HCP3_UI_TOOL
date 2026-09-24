"""共用的圓角矩形／圓形／膠囊(pill) 形狀繪圖工具（2026-08-17 新增）。

背景：專案裡原本有三種各自獨立的「畫圓角」土法煉鋼方式——
  1. Canvas 矩形＋四個扇形(pieslice)拼接（screen_gallery.draw_rounded_rect）
  2. Canvas 兩個圓＋一個矩形拼成膠囊（screen_settings 的 Toggle、
     DA005ControlPanel.pyw 的 Tab 外框）
  3. PIL ImageDraw 直接在「最終顯示大小」畫圓角矩形/橢圓
     （screen_recording.py 的 Preview 面板、screen_player.py 的圓形按鈕）
全部都是直接用最終像素大小畫，Tkinter Canvas 原生 arc/oval 在小尺寸下
本來就會有鋸齒，PIL 的 ImageDraw 預設也不做 anti-aliasing，所以邊緣
看起來都是「毛毛的」。

這裡統一用「先放大 SUPERSAMPLE_FACTOR 倍畫，再用 LANCZOS 縮小回實際
大小」的 supersampling 方式產生真正平滑的邊緣，轉成 tkinter 看得懂的
PhotoImage，並且做 cache（同樣參數不會每次 repaint 都重新畫一次）。

之後要調 radius / 顏色 / border / active-idle 只要改呼叫這幾個函式的
參數，不用每個畫面各自維護一套畫法。
"""
from PIL import Image, ImageDraw, ImageTk

SUPERSAMPLE_FACTOR = 4   # 放大倍率：越高邊緣越平滑，但畫一次的成本也越高，
                         # 4 倍在這幾個元件的尺寸（十幾到幾百 px）下已經
                         # 看不出鋸齒，也不會拖慢畫面

# 兩層 cache：
# _pil_cache 存「原始、從未被外部修改過」的 PIL Image／遮罩，給還需要
#   在上面貼圖／畫文字的呼叫端（例如 screen_recording 的 Preview 背景）；
#   每次要回傳給外部使用時一律回傳 .copy()，不會弄髒 cache。
# _photo_cache 存轉換好的 ImageTk.PhotoImage，給直接要放到 Canvas 上、
#   不會再被修改的呼叫端（Toggle／Tab 外框／Player 圓形按鈕／
#   Recording 左側面板背景）。
_pil_cache = {}
_photo_cache = {}


def _get_or_build_pil(cache_key, builder_fn):
    cached = _pil_cache.get(cache_key)
    if cached is None:
        cached = builder_fn()
        _pil_cache[cache_key] = cached
    return cached


def _build_rounded_rect(width, height, radius, fill, outline, outline_width):
    scale = SUPERSAMPLE_FACTOR
    big_width, big_height = width * scale, height * scale
    big_radius = radius * scale
    big_outline_width = outline_width * scale
    image = Image.new('RGBA', (big_width, big_height), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)
    draw.rounded_rectangle(
        (0, 0, big_width - 1, big_height - 1), radius=big_radius,
        fill=fill, outline=outline, width=big_outline_width if outline else 0)
    return image.resize((width, height), Image.LANCZOS)


def _build_circle(diameter, fill, outline, outline_width):
    scale = SUPERSAMPLE_FACTOR
    big_diameter = diameter * scale
    big_outline_width = outline_width * scale
    image = Image.new('RGBA', (big_diameter, big_diameter), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)
    draw.ellipse(
        (0, 0, big_diameter - 1, big_diameter - 1),
        fill=fill, outline=outline, width=big_outline_width if outline else 0)
    return image.resize((diameter, diameter), Image.LANCZOS)


def _build_rounded_mask(width, height, radius):
    scale = SUPERSAMPLE_FACTOR
    big_width, big_height = width * scale, height * scale
    mask = Image.new('L', (big_width, big_height), 0)
    draw = ImageDraw.Draw(mask)
    draw.rounded_rectangle((0, 0, big_width - 1, big_height - 1), radius=radius * scale, fill=255)
    return mask.resize((width, height), Image.LANCZOS)


def make_rounded_rect_image(width, height, radius, fill=None, outline=None, outline_width=0):
    """回傳一張 PIL Image（RGBA，圓角以外透明），呼叫端還要在上面貼圖／
    畫文字的話直接用這張就好——每次呼叫都是獨立副本，不會共用到 cache
    裡的原始圖片，不怕外部把 cache 弄髒。"""
    cache_key = ('rect', width, height, radius, fill, outline, outline_width)
    original = _get_or_build_pil(cache_key, lambda: _build_rounded_rect(width, height, radius, fill, outline, outline_width))
    return original.copy()


def make_rounded_rect_photo_image(width, height, radius, fill=None, outline=None, outline_width=0):
    """回傳可以直接 canvas.create_image(..., image=...) 使用的 PhotoImage，
    同樣參數會直接回傳 cache 裡已經轉好的那一份，不會每次 repaint 都重新
    畫一次。呼叫端要自己找地方保留這個回傳值的參照（例如存在 widget 的
    屬性上），PhotoImage 沒有其他 Python 參照指著就會被 GC 回收，畫面上
    的圖就會消失——這是 Tkinter 本身的限制，這支 cache 只負責「不用重畫」，
    不能取代呼叫端該做的參照保留。"""
    cache_key = ('rect', width, height, radius, fill, outline, outline_width)
    cached_photo = _photo_cache.get(cache_key)
    if cached_photo is not None:
        return cached_photo
    original = _get_or_build_pil(cache_key, lambda: _build_rounded_rect(width, height, radius, fill, outline, outline_width))
    photo_image = ImageTk.PhotoImage(original)
    _photo_cache[cache_key] = photo_image
    return photo_image


def make_pill_image(width, height, fill=None, outline=None, outline_width=0):
    """膠囊/藥丸(stadium)形狀：圓角矩形的特例，半徑固定等於 height/2。"""
    return make_rounded_rect_image(width, height, height / 2, fill=fill, outline=outline, outline_width=outline_width)


def make_pill_photo_image(width, height, fill=None, outline=None, outline_width=0):
    return make_rounded_rect_photo_image(width, height, height / 2, fill=fill, outline=outline, outline_width=outline_width)


def make_circle_image(diameter, fill=None, outline=None, outline_width=0):
    cache_key = ('circle', diameter, fill, outline, outline_width)
    original = _get_or_build_pil(cache_key, lambda: _build_circle(diameter, fill, outline, outline_width))
    return original.copy()


def make_circle_photo_image(diameter, fill=None, outline=None, outline_width=0):
    cache_key = ('circle', diameter, fill, outline, outline_width)
    cached_photo = _photo_cache.get(cache_key)
    if cached_photo is not None:
        return cached_photo
    original = _get_or_build_pil(cache_key, lambda: _build_circle(diameter, fill, outline, outline_width))
    photo_image = ImageTk.PhotoImage(original)
    _photo_cache[cache_key] = photo_image
    return photo_image


def make_rounded_mask(width, height, radius):
    """回傳一張灰階('L')遮罩，用來對現有照片（Preview／縮圖）套用圓角
    （image.putalpha(mask)），一樣先高解析度畫再縮小，邊緣才會平滑，
    不是直接在最終尺寸上畫一次。遮罩本身不會被呼叫端拿去畫東西/修改，
    直接回傳 cache 裡的物件即可，不用像 make_rounded_rect_image 那樣
    每次都複製一份。"""
    cache_key = ('mask', width, height, radius)
    return _get_or_build_pil(cache_key, lambda: _build_rounded_mask(width, height, radius))


def render_supersampled(width, height, draw_callback, supersample=SUPERSAMPLE_FACTOR):
    """通用版本：把「形狀＋圖示要一起做 anti-alias」的情境交給呼叫端自己
    畫（draw_callback(draw, big_width, big_height, scale) 在高解析度畫布
    上把整張圖畫完），畫完才用 LANCZOS 縮小一次。適合像 Player 圓形按鈕
    這種圓形跟圖示線條要一起平滑處理的情況——如果分開做（先做完平滑的
    圓形，再貼上一個用最終尺寸畫的圖示），圖示線條邊緣還是會是鋸齒的。
    回傳最終尺寸的 PIL Image（RGBA，底色透明），不做 cache——呼叫端
    (screen_player.py) 已經在 build_overlay_icon_images() 只呼叫一次、
    自行把結果存到固定的模組變數，不是每次 repaint 都重畫。"""
    scale = supersample
    big_width, big_height = width * scale, height * scale
    image = Image.new('RGBA', (big_width, big_height), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)
    draw_callback(draw, big_width, big_height, scale)
    return image.resize((width, height), Image.LANCZOS)
