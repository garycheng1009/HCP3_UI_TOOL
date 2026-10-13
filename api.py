"""
api.py — DA005 DVR 通訊層

這支檔案集中放所有跟 DVR 溝通的程式碼：
- REST API (HTTP GET/PUT + Protobuf 序列化/反序列化)
- 縮圖 / 影片檔案下載
- KeepAlive (背景持續送，保持連線不斷)
- RTSP Live View (ffmpeg 子行程)

規則：GUI (tab_xxx.py) 只能呼叫這支檔案裡提供的函式，
不要在 GUI 程式裡直接 import requests 或自己組 protobuf 訊息。

參考來源：tools/sample/DA005ProtobufSender_v028.pyw
"""

import platform
import subprocess
import threading
import tempfile
import queue
from datetime import datetime
from time import sleep, time, perf_counter
from io import BytesIO
import requests
from PIL import Image

import audi_pb2
from video_decoder import find_ffmpeg_executable

# Windows 下啟動 ffmpeg 子行程不要跳出 cmd/Terminal 視窗——這是 GUI 工具，
# 背景解碼/串流不應該讓使用者看到一個黑底文字視窗。CREATE_NO_WINDOW 只有
# Windows 的 subprocess 模組才有這個常數，
# 用 getattr 給預設值 0（等於不加這個 flag），這樣萬一未來在別的平台跑
# 也不會直接炸掉。跟 video_decoder.py 用的是同一個常數、同一個理由，這裡
# 沒有共用同一份定義，是因為兩支檔案本來就各自獨立、不互相 import，加一個
# import 只為了共用一個常數不划算。
_CREATE_NO_WINDOW = getattr(subprocess, 'CREATE_NO_WINDOW', 0)


# ============ 連線設定 ============
# DVR 本機透過 USB-NCM 連線時的 IPv6 位址，Windows 跟 Linux 測試機接的是不同網段的
# DVR（2026-08-11 確認）：Windows 這台接的是拔掉 VLAN 的新機台，位址在 803 網段；
# 249/155 這兩台 Linux 測試機接的還是原來那台，位址在 802 網段。用 platform.system()
# 判斷，同一份 api.py 不用因為部署在哪一邊而手動改常數。
# 如果要換連線目標（例如 Settings 畫面），呼叫 set_device_address() 即可。
if platform.system() == 'Windows':
    _device_host = 'FD53:7CB8:383:803::215'
else:
    _device_host = 'FD53:7CB8:383:803::215'
_device_port = 80


def set_device_address(host, port=80):
    """更改要連線的 DVR 位址（Settings 分頁會用到）"""
    global _device_host, _device_port
    _device_host = host
    _device_port = port


def get_device_address():
    """回傳目前的 DVR 位址，給 Settings 分頁顯示用"""
    return _device_host, _device_port


def get_liveview_rtsp_url():
    """依目前的 DVR 位址組出 Live View 用的 RTSP URL。2026-10-13 新增：
    DA005ControlPanel.pyw 建立共用 RTSPVideoSource 時用這個，不再用
    video_source.py 寫死的 DEFAULT_RTSP_URL；下面舊的 start_liveview() 也
    改用同一個函式，兩邊組法一致。"""
    return f'rtsp://[{_device_host}]:554/h264'


def _get_base_url():
    # IPv6 位址(例如 DVR 的 FD53:...)網址裡要用中括號包起來，一般 IPv4/主機名稱不用
    if ':' in _device_host:
        return f'http://[{_device_host}]:{_device_port}'
    else:
        return f'http://{_device_host}:{_device_port}'


# ============ 底層 HTTP 傳輸 ============

def send_get(path, timeout=10):
    """對 DVR 送出 GET 請求，回傳原始 bytes"""
    url = _get_base_url() + path
    response = requests.get(url, timeout=timeout)
    response.raise_for_status()
    return response.content


def send_put(path, message_bytes, timeout=10):
    """對 DVR 送出 PUT 請求，body 放序列化後的 protobuf bytes"""
    url = _get_base_url() + path
    headers = {
        'Content-Type': 'application/x-protobuf',
        'Content-Length': str(len(message_bytes)),
    }
    response = requests.put(url, data=message_bytes, headers=headers, timeout=timeout)
    response.raise_for_status()
    return response.content


def download_file(url, timeout=30):
    """下載任意檔案（縮圖、影片都會用到），回傳原始 bytes"""
    response = requests.get(url, timeout=timeout)
    response.raise_for_status()
    return response.content


# ============ DvrMode：錄影狀態控制 ============

def get_dvr_mode():
    """GET /dashcam/dvr_mode，回傳 DvrMode 物件"""
    data = send_get('/dashcam/dvr_mode')
    message = audi_pb2.DvrMode()
    message.ParseFromString(data)
    return message


def start_recording_with_ringbuffer(event_recording_setting=None):
    """切換成錄影模式（含 ring buffer，行車紀錄器一般模式）"""
    message = audi_pb2.DvrMode()
    message.dvr_mode = audi_pb2.DvrMode.DvrSystemState.DVR_SYSTEM_STATE_RECORD_WITH_RINGBUFFER
    if event_recording_setting is not None:
        message.event_recording_setting = event_recording_setting
    else:
        message.event_recording_setting = audi_pb2.DvrMode.DvrEvent.DVREVENT_SCENIC
    send_put('/dashcam/dvr_mode', message.SerializeToString())


def start_recording_without_ringbuffer(event_recording_setting):
    """切換成錄影模式（不含 ring buffer，測試案例常用來錄固定長度影片）"""
    message = audi_pb2.DvrMode()
    message.dvr_mode = audi_pb2.DvrMode.DvrSystemState.DVR_SYSTEM_STATE_RECORD_WITHOUT_RINGBUFFER
    message.event_recording_setting = event_recording_setting
    send_put('/dashcam/dvr_mode', message.SerializeToString())


def stop_recording():
    """切換成 STANDBY（停止錄影）"""
    message = audi_pb2.DvrMode()
    message.dvr_mode = audi_pb2.DvrMode.DvrSystemState.DVR_SYSTEM_STATE_STANDBY
    send_put('/dashcam/dvr_mode', message.SerializeToString())


def set_play_mode():
    """切換成 PLAY 模式（要瀏覽/播放影片前，DVR 需要先切到這個模式）"""
    message = audi_pb2.DvrMode()
    message.dvr_mode = audi_pb2.DvrMode.DvrSystemState.DVR_SYSTEM_STATE_PLAY
    send_put('/dashcam/dvr_mode', message.SerializeToString())


# ============ Desired / Actual DVR Mode：統一管理 ============
# Settings 畫面是 DVR「正常狀態」唯一的設定來源：desired_dvr_mode 代表使用者
# 在 Settings 用 ON/OFF Switch 設定的、DVR 平常應該維持的狀態，只會是
# WITHOUT_RINGBUFFER（Switch ON）或 STANDBY（Switch OFF）兩種。actual 狀態
# 一律即時呼叫 get_dvr_mode() 重新問 DVR，這裡不快取「現在是什麼」，只快取
# 「應該是什麼」。
#
# Recording / Gallery / Favorite / Player / Format 都可能讓 actual 暫時偏離
# desired（例如 Player 播放前暫時切 STANDBY），但都不能呼叫
# set_desired_dvr_mode() 去永久改變 desired；只有 Settings 的 Switch 可以。
# 操作結束後要呼叫 restore_desired_dvr_mode()，讓 actual 回到 desired。
_desired_dvr_mode = audi_pb2.DvrMode.DvrSystemState.DVR_SYSTEM_STATE_RECORD_WITHOUT_RINGBUFFER

DVR_MODE_CONFIRM_TIMEOUT_SECONDS = 6
DVR_MODE_CONFIRM_POLL_INTERVAL_SECONDS = 0.3


def get_desired_dvr_mode():
    """回傳目前 Settings 設定的 desired_dvr_mode（DvrSystemState enum 值，
    只會是 WITHOUT_RINGBUFFER 或 STANDBY）"""
    return _desired_dvr_mode


def set_desired_dvr_mode(dvr_system_state):
    """只有 Settings 的 ON/OFF Switch 可以呼叫這個——這是整個工具裡唯一
    允許永久改變 desired_dvr_mode 的地方。其他頁面（Recording/Gallery/
    Favorite/Player/Format）只能暫時改變 actual，不能呼叫這個函式。"""
    global _desired_dvr_mode
    _desired_dvr_mode = dvr_system_state


def confirm_dvr_system_state(expected_dvr_system_state, timeout=DVR_MODE_CONFIRM_TIMEOUT_SECONDS):
    """送出控制指令後，確認 actual dvr_mode 是不是真的變成 expected 這個
    字面上的原始狀態（直接比對 dvr_mode 欄位本身，不透過 derive_dvr_state()
    這種分類）。這是這次新增的共用確認邏輯，desired_dvr_mode 相關的操作都
    共用這一個，不要各自重寫一份 polling 迴圈。"""
    start_time = time()
    while True:
        try:
            mode = get_dvr_mode()
            if mode.dvr_mode == expected_dvr_system_state:
                return True
        except Exception as error:
            print(f'[api] confirm_dvr_system_state 讀取狀態失敗: {error}')
        if time() - start_time >= timeout:
            return False
        sleep(DVR_MODE_CONFIRM_POLL_INTERVAL_SECONDS)


def ensure_safe_ringbuffer_transition(target_is_with_ringbuffer):
    """WITH_RINGBUFFER 跟 WITHOUT_RINGBUFFER 不能直接互相切換（AutoMate
    既有案例驗證過的規則），中間一定要先過渡到 STANDBY。這個函式只在「目前
    真的處於會被這條規則擋到的相反狀態」時，才先送一次 stop_recording() 過渡
    到 STANDBY；其他情況（目前已經是 STANDBY、PLAY 等）什麼都不用做，直接
    讓呼叫端送真正的目標即可。

    回傳 True 表示安全可以繼續（不需要過渡，或過渡後已經確認真的到了
    STANDBY）；回傳 False 表示真的需要過渡但沒有確認到 STANDBY——呼叫端
    這時候不能再送真正的目標，不然還是會踩到 WITH_RINGBUFFER/
    WITHOUT_RINGBUFFER 不能直接互轉的限制（Codex review 2026-08-13
    發現：原本這個函式的確認結果被忽略，呼叫端一定會繼續送目標）。讀取
    現在狀態本身失敗（GET 有問題）時沒有依據判斷要不要過渡，維持原本
    「讓呼叫端自己送 PUT，交給那次呼叫的例外處理」的行為，回傳 True。"""
    DvrSystemState = audi_pb2.DvrMode.DvrSystemState
    blocking_state = (DvrSystemState.DVR_SYSTEM_STATE_RECORD_WITH_RINGBUFFER if not target_is_with_ringbuffer
                       else DvrSystemState.DVR_SYSTEM_STATE_RECORD_WITHOUT_RINGBUFFER)
    try:
        current_mode = get_dvr_mode()
    except Exception as error:
        print(f'[api] ensure_safe_ringbuffer_transition 讀取狀態失敗: {error}')
        return True
    if current_mode.dvr_mode == blocking_state:
        stop_recording()
        return confirm_dvr_system_state(DvrSystemState.DVR_SYSTEM_STATE_STANDBY)
    return True


def _put_dvr_system_state(target_dvr_system_state):
    """依 target 送出對應的 PUT，desired_dvr_mode 相關的操作（Settings
    Switch、restore_desired_dvr_mode()）都共用這個，不用各自判斷要呼叫
    哪一個 start_recording_xxx()。如果需要先過渡到 STANDBY 卻沒有確認
    成功，直接中止、不送真正的目標（見 ensure_safe_ringbuffer_transition()
    的說明）。"""
    DvrSystemState = audi_pb2.DvrMode.DvrSystemState
    if target_dvr_system_state == DvrSystemState.DVR_SYSTEM_STATE_RECORD_WITHOUT_RINGBUFFER:
        if not ensure_safe_ringbuffer_transition(target_is_with_ringbuffer=False):
            raise Exception('Failed to confirm STANDBY before switching to WITHOUT_RINGBUFFER.')
        start_recording_without_ringbuffer(audi_pb2.DvrMode.DvrEvent.DVREVENT_NO_EVENT)
    else:
        stop_recording()


def apply_and_set_desired_dvr_mode(target_dvr_system_state):
    """Settings 專用：PUT target -> GET 確認 -> 成功才把這個值存成新的
    desired_dvr_mode。失敗完全不會呼叫 set_desired_dvr_mode()，desired 維持
    原來的值不變（符合「PUT 或 GET verification 失敗時不修改 desired」的
    規則）。回傳 True/False。"""
    try:
        _put_dvr_system_state(target_dvr_system_state)
    except Exception as error:
        print(f'[api] apply_and_set_desired_dvr_mode PUT 失敗: {error}')
        return False

    confirmed = confirm_dvr_system_state(target_dvr_system_state)
    if confirmed:
        set_desired_dvr_mode(target_dvr_system_state)
    return confirmed


def restore_desired_dvr_mode():
    """讀 desired_dvr_mode -> GET actual -> 不一樣才 PUT -> GET 確認。
    Recording 停止 Scenic、Player 離開、Format 完成都呼叫這一個，不要各自
    兜一份。回傳 True 表示確認成功（或本來就已經是 desired，不用做任何
    事）；False 表示 PUT 過後還是沒有變成 desired（呼叫端自己決定要不要跳
    錯誤訊息，這裡不負責 UI）。"""
    desired = get_desired_dvr_mode()
    try:
        current_mode = get_dvr_mode()
    except Exception as error:
        print(f'[api] restore_desired_dvr_mode 讀取目前狀態失敗: {error}')
        return False

    if current_mode.dvr_mode == desired:
        return True

    try:
        _put_dvr_system_state(desired)
    except Exception as error:
        print(f'[api] restore_desired_dvr_mode 送出 PUT 失敗: {error}')
        return False

    return confirm_dvr_system_state(desired)


# ============ Memory：SD 卡格式化 ============
# 協議是跟 AutoMate 既有案例對照確認過的，不是自己猜的：conftest.py 的
# prepare_dashcam fixture、test_DA005_dvr_010.py 等全部案例都是同一個寫法
# ——PUT /dashcam/memory，Memory.sd_do_format = DO_FORMAT_TYPE_AUTO，沒有
# 案例用過 DO_FORMAT_TYPE_FAT32 / DO_FORMAT_TYPE_EXFAT。

def format_sd_card():
    """格式化 SD 卡。呼叫端要自己先確認 DVR 目前是 Standby 才呼叫這個——
    AutoMate 全部案例都是先確認切到 STANDBY 才送 Format，沒有任何案例測過
    「錄影中直接 Format」，這裡不做任何狀態檢查或自動停止錄影，那是呼叫端
    （畫面邏輯）的責任。"""
    message = audi_pb2.Memory()
    message.sd_do_format = audi_pb2.DoFormatType.DO_FORMAT_TYPE_AUTO
    send_put('/dashcam/memory', message.SerializeToString())


def get_memory():
    """GET /dashcam/memory，回傳 Memory 物件。"""
    data = send_get('/dashcam/memory')
    message = audi_pb2.Memory()
    message.ParseFromString(data)
    return message


# ============ SD Card Presence（2026-08-20 新增）============
# 2026-08-20 使用者真機驗證過的 GET /dashcam/memory 結果：
#   有 SD Card：sd_inserted=True,  sd_encrypted=True,  sd_card.file_system=FILESYSTEM_EXFAT,
#               sd_card.readable/writable=true, sd_card.size=59328, size_available=59125
#   沒有 SD Card：sd_inserted=False, sd_encrypted=False, sd_card.file_system=FILESYSTEM_NO_FILESYSTEM,
#               sd_card.size=9999999, size_available=9999999
# 使用者明確要求：正式判斷 SD Card 是否存在唯一主要依據
# memory.sd_inserted，不要用 size==9999999 當主要判斷條件（9999999 只能
# 當 log/diagnostic 資訊）。
#
# 跟 is_dvr_connected() 同一種設計：這裡只保存「最近一次 GET Memory 讀到
# 的 sd_inserted」，是一個純粹的記憶體快取，呼叫 is_sd_card_present() 本身
# 不會觸發任何新的網路請求；真正的 GET 只在兩個時機發生（App Startup、
# KeepAlive 通知 Memory 有變化），見 _memory_changed_event 附近的說明，
# 呼叫端（screen_recording.py 的全域監測）負責決定「什麼時候該呼叫
# refresh_sd_card_presence()」，這支檔案不會自己起一個定時 GET。
_sd_card_inserted = True   # 樂觀預設：多數情況下 SD 卡本來就已經插著，跟 is_dvr_connected() 的預設哲學一致
_sd_card_state_lock = threading.Lock()


def is_sd_card_present():
    """回傳目前記憶體快取的 SD Card 是否存在，不會觸發任何新的網路請求。"""
    with _sd_card_state_lock:
        return _sd_card_inserted


def _set_sd_card_inserted(is_inserted):
    global _sd_card_inserted
    with _sd_card_state_lock:
        _sd_card_inserted = is_inserted


def refresh_sd_card_presence():
    """實際 GET 一次 Memory，更新全域 SD Card Presence 快取，回傳這次讀到
    的 sd_inserted。GET 失敗時維持原本快取值不變（不猜、不假設），並把
    例外往上拋給呼叫端決定要怎麼處理（呼叫端通常是背景執行緒/週期性
    tick，自己會 try/except 並印出失敗訊息，這裡不吞掉例外，維持跟其他
    GET 函式一致的錯誤處理方式）。

    size/size_available 這兩個欄位只印出來當診斷用，不是判斷條件——真機
    驗證過沒有 SD 卡時這兩個欄位固定回傳 9999999，但正式判斷一律只看
    sd_inserted。"""
    memory = get_memory()
    print(f'[api] Memory 診斷: sd_inserted={memory.sd_inserted} sd_encrypted={memory.sd_encrypted} '
          f'file_system={memory.sd_card.file_system} size={memory.sd_card.size} '
          f'size_available={memory.sd_card.size_available}（size/size_available 僅供診斷，不是判斷依據）')
    _set_sd_card_inserted(memory.sd_inserted)
    return memory.sd_inserted


def take_photo():
    """觸發拍照"""
    message = audi_pb2.DvrMode()
    message.photo_state = audi_pb2.DvrMode.DvrPhoto.DVR_PHOTO_TAKE_PHOTO
    send_put('/dashcam/dvr_mode', message.SerializeToString())


# ============ Recordings：影片/照片清單、縮圖、下載 ============

def get_recordings():
    """GET /dashcam/recordings，回傳 Recording 物件的清單"""
    data = send_get('/dashcam/recordings')
    message = audi_pb2.Recordings()
    message.ParseFromString(data)
    return list(message.recordings)


# Thumbnail 解析度：Recording Page 縮圖是畫面上唯一一張、佔比很大的大縮圖，
# 要用高解析度；Gallery List 一次要顯示一整串 Item，同一時間可能要下載
# 很多張，用小解析度才不會浪費下載量、拖慢 List 的 Loading 時間。
# 呼叫端（screen_recording.py / screen_gallery.py）依自己的用途指定要哪一種，
# 不要讓 Gallery 也去下載 Recording 用的高解析度。
THUMBNAIL_RESOLUTION_GALLERY = '640x360'
THUMBNAIL_RESOLUTION_RECORDING = '2560x1440'


def build_thumbnail_url(recording_item, resolution=THUMBNAIL_RESOLUTION_GALLERY):
    """縮圖網址：thumbnailUri 後面加上 <resolution>.JPG"""
    uri = recording_item.thumbnailUri.rstrip('/') + f'/{resolution}.JPG'
    return _get_base_url() + uri


def build_video_url(recording_item):
    """影片網址：srcUri 本身已經包含資料夾與檔名，直接接在 base url 後面即可"""
    return _get_base_url() + recording_item.srcUri


def download_photo_original(recording_item):
    """下載照片的原始 JPG（不縮小），Photo Viewer 顯示用。srcUri 的組法
    跟 build_video_url() 完全一樣（base_url + srcUri，對影片跟照片都是
    同一種規則），這裡另外寫一個函式只是語意上『下載照片』跟『開影片
    串流』是兩件不一樣的事，不要共用一個名字容易讓人誤會。"""
    url = _get_base_url() + recording_item.srcUri
    return download_file(url, timeout=15)


def download_thumbnail(recording_item, resolution=THUMBNAIL_RESOLUTION_GALLERY):
    """下載縮圖，回傳原始圖片 bytes"""
    return download_file(build_thumbnail_url(recording_item, resolution), timeout=5)


def parse_resolution_to_max_size(resolution):
    """把 '2560x1440' 這種字串換成 PIL resize 要用的 (width, height) tuple"""
    width_text, height_text = resolution.split('x')
    return int(width_text), int(height_text)


def download_preview_image(recording_item, resolution=THUMBNAIL_RESOLUTION_GALLERY):
    """取得可以當縮圖用的圖片 bytes，GUI 只要呼叫這一個函式就好，不用自己判斷
    這筆資料是影片還是照片、也不用自己組網址。resolution 由呼叫端指定
    （Recording Page 用 THUMBNAIL_RESOLUTION_RECORDING，Gallery 用
    THUMBNAIL_RESOLUTION_GALLERY），不要讓 Gallery List 每個 Item 都下載
    Recording Page 用的高解析度。

    實測結果（2026-08-04，接真機）：
    - 影片：thumbnailUri 是正常路徑（例如 '/scenic/thumbnail/videoid_1'），
      沿用已驗證成功的規則：thumbnailUri + '/<resolution>.JPG'
    - 照片：thumbnailUri 韌體目前回傳的是一個空白字元 ' '（不是真的路徑），
      這種情況改成下載 srcUri 的原始 JPG（例如 2.7MB 的全尺寸照片），
      再用 PIL 縮小成 resolution 對應的大小

    任何一種方式失敗都會用 log_preview_image_error() 印出 fileid / URL /
    HTTP Status / Content-Type / Exception，不會只默默回傳 None，
    方便之後排查是不是網址規則又不一樣了。全部方式都失敗才回傳 None。
    """
    if is_photo_recording(recording_item):
        return download_photo_preview_image(recording_item, resolution)
    else:
        return download_video_thumbnail(recording_item, resolution)


def download_video_thumbnail(recording_item, resolution):
    """影片縮圖：已驗證成功的規則，thumbnailUri + /<resolution>.JPG"""
    url = build_thumbnail_url(recording_item, resolution)
    try:
        return download_file(url, timeout=5)
    except Exception as error:
        log_preview_image_error(recording_item, url, error)
        return None


def download_photo_preview_image(recording_item, resolution):
    """照片縮圖：先試韌體給的 thumbnailUri（如果看起來是真的路徑），
    沒有可用的 thumbnailUri 或下載失敗，改抓 srcUri 的原始 JPG 再縮小成
    resolution 對應的大小"""
    thumbnail_uri = recording_item.thumbnailUri.strip()

    if thumbnail_uri:
        url = build_thumbnail_url(recording_item, resolution)
        try:
            return download_file(url, timeout=5)
        except Exception as error:
            log_preview_image_error(recording_item, url, error)
            # thumbnailUri 這條路失敗，不要直接放棄，繼續往下試 srcUri

    src_url = _get_base_url() + recording_item.srcUri
    try:
        original_image_bytes = download_file(src_url, timeout=15)
        return resize_image_bytes(original_image_bytes, parse_resolution_to_max_size(resolution))
    except Exception as error:
        log_preview_image_error(recording_item, src_url, error)
        return None


def resize_image_bytes(image_bytes, max_size):
    """把原始照片縮小成縮圖大小，回傳縮小後的 JPEG bytes"""
    image = Image.open(BytesIO(image_bytes))
    image.thumbnail(max_size)
    output = BytesIO()
    image.convert('RGB').save(output, format='JPEG')
    return output.getvalue()


def log_preview_image_error(recording_item, url, error):
    """縮圖/預覽圖下載失敗時，把排查需要的資訊完整印出來，不要只顯示「無縮圖」了事"""
    response = getattr(error, 'response', None)
    status_code = response.status_code if response is not None else None
    content_type = response.headers.get('Content-Type') if response is not None else None

    print('[縮圖下載失敗]')
    print(f'  fileid: {recording_item.fileid}')
    print(f'  URL: {url}')
    print(f'  HTTP Status: {status_code}')
    print(f'  Content-Type: {content_type}')
    print(f'  Exception: {error}')


def download_recording_to_temp_file(recording_item, on_progress=None):
    """用串流下載影片或照片本體到暫存檔，回傳存檔路徑（照片存 .jpg，影片存 .mp4）。
    一律走 open_video_stream() + save_stream_to_file()，不會整支檔案一次讀進記憶體。
    on_progress(downloaded_size, total_size) 可以拿來更新畫面上的下載進度。"""
    response = open_video_stream(recording_item)
    response.raise_for_status()

    file_suffix = '.jpg' if is_photo_recording(recording_item) else '.mp4'
    temp_file = tempfile.NamedTemporaryFile(suffix=file_suffix, delete=False)
    temp_file.close()

    save_stream_to_file(response, temp_file.name, on_progress=on_progress)
    return temp_file.name


def open_video_stream(recording_item, timeout=30):
    """打開一個到影片檔案的 HTTP 串流連線（stream=True，不會整支影片一次讀進記憶體）。
    回傳 requests 的 response 物件，呼叫端要自己檢查 response.status_code 是不是 200，
    確認沒問題才呼叫 save_stream_to_file() 把內容寫進檔案。"""
    url = build_video_url(recording_item)
    return requests.get(url, stream=True, timeout=timeout)


def save_stream_to_file(response, save_path, chunk_size=64 * 1024, on_progress=None):
    """把 open_video_stream() 回傳的串流內容邊收邊寫進檔案，一次只處理一小塊 (chunk_size)，
    不會整支影片一次讀進記憶體。on_progress(downloaded_size, total_size) 每寫一個 chunk 就會被呼叫一次，
    total_size 如果伺服器沒回 Content-Length 就會是 0。回傳實際下載到的 bytes 數。"""
    total_size = int(response.headers.get('Content-Length', 0))
    downloaded_size = 0

    with open(save_path, 'wb') as file:
        for chunk in response.iter_content(chunk_size=chunk_size):
            if not chunk:
                continue
            file.write(chunk)
            downloaded_size += len(chunk)
            if on_progress:
                on_progress(downloaded_size, total_size)

    return downloaded_size


def is_photo_recording(recording_item):
    """判斷這筆資料是不是照片（Photo 分頁會用到）"""
    type_name = audi_pb2.TypeOfRecording.Name(recording_item.type_of_recording)
    is_jpeg_format = recording_item.media_format == audi_pb2.Recording.MediaFormat.MEDIA_FORMAT_jpeg
    return 'PHOTO' in type_name or is_jpeg_format


def is_event_recording(recording_item):
    """判斷這筆資料是不是事件觸發影片（Event 分頁會用到）
    註：目前韌體只有 STREETVIDEO_USER_EVENT_TRIGGERED 這個類型的名稱含有 EVENT，
    用關鍵字比對是為了以後韌體多加事件類型時不用改這裡的程式碼。"""
    type_name = audi_pb2.TypeOfRecording.Name(recording_item.type_of_recording)
    return 'EVENT' in type_name


def get_category(recording_item):
    """把一筆資料分成 'photo' / 'event' / 'recording' 三種分類，畫面端排列用"""
    if is_photo_recording(recording_item):
        return 'photo'
    if is_event_recording(recording_item):
        return 'event'
    return 'recording'


# ============ Group：同一個 groupid 算同一部影片 ============
# HCP3 的規則：同一個 groupid 底下可能有好幾筆 Recording（不同 fileid/sequenceid，
# 通常是 ring buffer 換檔造成的分段），這幾段其實是「同一部影片」，播放時要接續播完，
# 使用者感覺到的長度是這幾段加總（例如三段 1分/1分/30秒，播起來是 2:30，不是只算一段）。
# 縮圖也只有一張，同一個 group 裡每一段的 thumbnailUri 都一樣，拿任何一段的都可以。
# 所以 Gallery / Recording 首頁一律要先分組再顯示，不能直接用 fileid 一筆一筆列。
#
# 2026-08-21 真機證實 groupid 不是「一支影片」的可靠識別碼：同一份
# /dashcam/recordings 裡，同時觀察到 14 個 groupid 底下各混了兩支完全
# 不相關的影片（通常是 RTC 還沒校時、trigger_time 停在 2023-01-01 的
# 舊殘留檔案，跟今天新錄的影片剛好被 DVR 重複用到同一個 groupid 數字）。
# 純粹依 groupid 分組會把這些不相關的影片誤合併成一組（縮圖、代表項目、
# 播放清單全部跟著錯）。
#
# 用真機這 14 組碰撞資料比對後發現一個可靠訊號：真正同一支影片的
# sequenceid，依 trigger_time 排序後一定嚴格遞增（可以跳號，例如
# 1,2,3,4,6,8...，但絕不會重複或變小）；重複/變小只在確認碰撞的案例
# 才出現。所以分組規則改成兩段式：先依 groupid 聚合候選項目，再依
# trigger_time 排序、檢查 sequenceid 是否嚴格遞增，一旦沒有遞增（重複
# 或倒退）就切成新的一組——即使 groupid 相同也視為不同影片。
def group_recordings_by_groupid(recordings):
    """把清單依照 groupid 分組，再對每組做 sequenceid 遞增檢查拆分（見上方
    2026-08-21 說明），確保同一組回傳的都是真正同一支影片的段落。
    每一組內部依 sequenceid 由小到大排序（代表段落的播放順序）。
    回傳一個 list，每個元素是一組（已排序的 Recording list），依
    (groupid, 該組代表項目的 trigger_time) 由大到小排列（最新的一組排最前面）。"""
    groups_by_id = {}
    for item in recordings:
        groups_by_id.setdefault(item.groupid, []).append(item)

    groups = []
    for groupid in groups_by_id:
        groups.extend(_split_groupid_bucket_by_sequenceid(groups_by_id[groupid]))

    groups.sort(
        key=lambda group_items: (group_items[0].groupid, group_items[0].interval.start_time.ToDatetime()),
        reverse=True)
    return groups


def _split_groupid_bucket_by_sequenceid(items_with_same_groupid):
    """同一個 groupid 底下的原始清單，依 trigger_time 排序後檢查 sequenceid
    是否嚴格遞增，只要下一筆的 sequenceid 沒有比前一筆大（重複或變小），
    就代表這個 groupid 被另一支不相關的影片重複使用，從這一筆切出新的
    子群組。sequenceid 允許跳號，不要求連續的 1,2,3。

    回傳一個 list，每個元素是一個子群組（已依 sequenceid 排序的 Recording
    list）。"""
    items_sorted_by_trigger_time = sorted(
        items_with_same_groupid, key=lambda item: item.interval.start_time.ToDatetime())

    sub_groups = []
    current_sub_group = []
    previous_sequenceid = None
    for item in items_sorted_by_trigger_time:
        is_new_video = current_sub_group and item.sequenceid <= previous_sequenceid
        if is_new_video:
            sub_groups.append(current_sub_group)
            current_sub_group = []
        current_sub_group.append(item)
        previous_sequenceid = item.sequenceid
    if current_sub_group:
        sub_groups.append(current_sub_group)

    return [sorted(sub_group, key=lambda item: item.sequenceid) for sub_group in sub_groups]


def get_group_representative(group_items):
    """一組裡面挑一筆代表：sequenceid 最小（最早）的那一段，縮圖/類型跟其他段都一樣"""
    return group_items[0]


# 2026-08-20 使用者回報真機 /dashcam/recordings 原始資料本身就有重複 fileid
# （包含不同 groupid/type/srcUri/trigger_time 的兩筆資料共用同一個 fileid，
# 甚至 Photo/Video 共用同一個 fileid 的情況），確認 .proto 規格裡 fileid 只是
# 「1…99999」的有界數字，本來就沒有全域唯一的保證。Gallery/Player 過去直接拿
# representative_item.fileid 當畫面端識別碼（item_key），在這種情況下會造成
# dict key collision（後建立的那筆覆蓋前一筆的 row widget/thumbnail 快取），
# 導致某些 row 永遠停在 Loading，或 Favorite/Player 導航對到錯誤的那一筆。
#
# 這裡改成依「這是 Video 還是 Photo」給不同的識別方式：
# - Video group：用 groupid。DVR 的刪除/收藏協議（delete_recording_group()、
#   set_recording_group_favorite() 附近）本來就直接拿 groupid 當同一支影片
#   的識別碼，這裡只是把既有的信任延伸到畫面端的 key，沒有新增假設。
# - Photo：用 srcUri（實際的檔案路徑，download_photo_preview_image() 下載
#   原圖也是靠這個欄位）。真機資料證實兩筆共用 fileid 的紀錄，srcUri 是不同
#   的，比 fileid 更貼近「實際指向哪一個檔案」。srcUri 是空字串的極端情況
#   （理論上不該發生）才 fallback 回 fileid + trigger time 三元組。
#
# 前面一律加 'video'/'photo' 分類 tag：Photo 的 groupid 早就已知不保證唯一
# （見 screen_player.py find_adjacent_groups() 附近的說明），如果不加 tag，
# Photo 的 fallback key 理論上可能跟 Video 的 groupid 值域跨型別撞在一起。
def compute_item_key(group_items):
    """算出畫面端（Gallery row widget、thumbnail cache、Favorite、Checkbox、
    Player 導航……）用來識別「這是哪一個 Gallery group/item」的 key，
    取代過去直接使用 representative_item.fileid 的舊做法。"""
    representative_item = get_group_representative(group_items)
    if is_photo_recording(representative_item):
        if representative_item.srcUri:
            return ('photo', representative_item.srcUri)
        return ('photo', representative_item.fileid, representative_item.interval.start_time.ToDatetime())
    return ('video', representative_item.groupid)


# ============ Gallery 專用：依 Trigger Time 分組＋排序 ============
# 2026-08-14 使用者確認規則：Gallery/Favorite 的排序一律用「這筆資料最初被
# 觸發/開始產生的時間」，不能用 finalized 時間、檔案修改時間、下載時間、
# thumbnail 取得時間、Gallery refresh 時間，也不能用 groupid 大小代替。
#
# 欄位確認（不是用欄位名稱像 timestamp/start_time 就直接假設，是對照
# dashcam_interface_v0.23.proto 的 Recording message 確認過）：Scenic /
# Event / Photo 全部都是同一種 audi_pb2.Recording 訊息（只靠
# type_of_recording/media_format 分類，沒有各自專屬的時間欄位），這個
# 訊息裡唯一的時間欄位是 interval（google.type.Interval，標準 Google
# 型別，語意明確：start_time 就是這個區間「開始」的時間點），所以三種
# 類型的 Trigger Time 統一都是 recording_item.interval.start_time——
# 這一段錄影/這張照片真正開始被寫入的時間，不是檔案完成/修改/下載的
# 時間。get_time_text() 顯示用的也是同一個欄位，這裡沿用同一套依據。
#
# 多重 sequence 的影片：get_group_representative() 本來就是挑
# sequenceid 最小（最早）的那一段，所以直接拿代表項目的 interval.start_time
# 就是這整個 Group 最初被觸發的時間，之後不管切出多少新 segment（新
# segment 的 sequenceid 一定更大，不會變成代表項目）都不會改變這個值，
# Gallery 排序位置也就不會被新 segment 影響。

def get_group_trigger_time(group_items):
    """這個 group（一支多 sequence 的影片，或一張照片）的排序用時間。"""
    return get_group_representative(group_items).interval.start_time.ToDatetime()


# 2026-08-20 新增：見 group_recordings_for_gallery() 內的說明，只是把
# 分組/排序這兩段耗時另外暴露出來給 screen_gallery.py 組 Performance
# Summary 用，不是給任何邏輯判斷用的狀態。
_last_gallery_grouping_perf = {'group_duration': 0.0, 'sort_duration': 0.0}


def group_recordings_for_gallery(recordings):
    """Gallery/Favorite 專用的分組＋排序，group_recordings_by_groupid() 本身
    不變（Video Grouping/Player/Delete/Favorite 等既有功能繼續用它）。

    照片不能跟影片一樣用 groupid 分組：影片的 groupid 是這台 DVR 真的拿來
    識別「同一支多段影片」的欄位，但照片本來就沒有分段的概念，這支專案
    既有的刪除/收藏協議（send_recording_edit() 附近的註解）也早就是
    「影片用 groupid、照片用 fileid（不是 groupid）」——代表照片的
    groupid 本來就不保證每張都不一樣（可能相同、可能是 0）。如果照片也
    直接用 group_recordings_by_groupid() 分組，會把使用者連續拍的好幾張
    照片誤合併成同一筆 Gallery 項目，這正是「拍很多張 Photo 卻只顯示
    一張」的原因——是分組/去重邏輯的問題，不是資料解析錯誤（每一張照片
    自己的 Recording 資料本身是正確、各自獨立的，只是被分組邏輯合併了）。

    所以這裡把照片跟影片分開處理：照片每一筆都自己獨立成一個「group」
    （list 裡只有它自己一項），影片才用既有的 group_recordings_by_groupid()
    分組。最後把兩邊的 group 合併，一起依 get_group_trigger_time() 由新到
    舊排序（Scenic/Event/Photo 混在同一個 List，不分類型）。

    2026-08-14 使用者回報 Gallery Refresh 變慢，要求先量測、不要先重構。
    這裡暫時加了分組／排序兩個階段各自的耗時 Log，量測完後要移除。

    2026-08-20 使用者再次要求針對 Gallery 載入速度做完整 Performance
    Profiling（84 筆約 6 秒，未來 SD Card 可能到數百~4000 筆）：計時改用
    time.perf_counter()（單調時鐘，量測耗時比 time() 準，不受系統時間
    調整影響）。這支函式本身沒有 caller 可以直接接住回傳值多帶一份計時
    資訊——group_recordings_for_gallery() 有好幾個呼叫端（screen_gallery.py
    的 load_recordings()/compute_finished_gallery_item_count()/
    report_gallery_item_count_from_recordings()，screen_player.py 的
    Trigger Time 排序）,改成回傳 tuple 要一次改全部呼叫端，超出這次「只
    量測、不重構」的範圍。改成用 _last_gallery_grouping_perf 這個模組層級
    dict 把這兩段耗時「順便」暴露出來，只有真的需要組出完整 Gallery Load
    Summary 的呼叫端（screen_gallery.py 的 load_recordings()）會讀它，
    其他呼叫端完全不受影響、也不用知道這個 dict 存在。"""
    t_group_start = perf_counter()
    photo_items = [item for item in recordings if is_photo_recording(item)]
    video_items = [item for item in recordings if not is_photo_recording(item)]

    video_groups = group_recordings_by_groupid(video_items)
    photo_groups = [[item] for item in photo_items]

    all_groups = video_groups + photo_groups
    t_group_end = perf_counter()

    all_groups.sort(key=get_group_trigger_time, reverse=True)
    t_sort_end = perf_counter()

    _last_gallery_grouping_perf['group_duration'] = t_group_end - t_group_start
    _last_gallery_grouping_perf['sort_duration'] = t_sort_end - t_group_end

    print(f'[Gallery Perf] 2. group_recordings_for_gallery() 分組: {t_group_end - t_group_start:.3f}s, '
          f'video_group_count={len(video_groups)}, photo_item_count={len(photo_groups)}')
    print(f'[Gallery Perf] 3. Trigger Time sorting: {t_sort_end - t_group_end:.3f}s, '
          f'total_group_count={len(all_groups)}')

    return all_groups


# ============ Finished/Completed 判斷：還在錄的資料不算完成 ============
# /dashcam/recordings 可能會回傳目前還在錄製中、還沒真的存檔完的 item
# （state 還是 INIT/STARTED/PREVIEW_AVAIABLE/ONGOING，或是 ERROR），這種
# 資料不完整，不該顯示在 Gallery，也不該下載縮圖/允許 Favorite/Delete/
# Play。判斷邏輯集中放在這裡，GUI 不要自己直接解析 state 這個 protobuf
# enum。

def is_recording_finished(item):
    """判斷單一筆 Recording（影片的某一段，或照片）是不是已經完成存檔。
    只有 RECORDING_STATE_FINISHED 才算完成；INIT/STARTED/
    PREVIEW_AVAIABLE/ONGOING 都還在進行中，ERROR 是失敗，這些狀態的資料
    都還不完整。"""
    return item.state == audi_pb2.Recording.RecordingState.RECORDING_STATE_FINISHED


def is_group_finished(group_items):
    """一整個 group（同一個 groupid 底下所有段落）要全部段落都 FINISHED
    才算完成，不能只看代表項目（sequenceid 最小的那一段）。多段影片常見
    「前面幾段已經存檔完成，最後一段還在錄」的情況，只看代表項目會誤判
    整組已經完成，使用者點進去播放會少最後一段，變成不完整的影片；只要
    還有任何一段沒 FINISHED，整組都先當作沒完成，不顯示。"""
    return all(is_recording_finished(item) for item in group_items)


def get_group_total_duration_seconds(group_items):
    """把一組裡面每一段的長度加總，這才是使用者感覺到的「這部影片」總長度"""
    return sum(item.duration.seconds for item in group_items)


def get_latest_group(recordings, category=None):
    """從一份已經取得的清單裡，找出「最後一組」符合分類的資料（groupid 最大的那一組）；
    找不到回傳 None。category 傳 None 代表不篩選，全部都算。"""
    groups = group_recordings_by_groupid(recordings)
    matched_groups = [
        group for group in groups
        if category is None or get_category(get_group_representative(group)) == category
    ]
    if not matched_groups:
        return None
    return matched_groups[0]  # groups 已經是 groupid 由大到小排序，第一個就是最新的


def is_dvr_recording_now():
    """查詢 DVR 目前是不是正在錄影中（粗略判斷，只看 dvr_mode，不分 Scenic/Event）。
    注意：Recording 首頁畫面判斷 Scenic/Event/Standby 三種狀態時不能用這個函式，
    要用 screen_recording.py 裡的 derive_dvr_state()，那邊會同時看
    dvr_mode 跟 event_recording_setting 兩個欄位才能分辨清楚。"""
    mode = get_dvr_mode()
    recording_states = (
        audi_pb2.DvrMode.DvrSystemState.DVR_SYSTEM_STATE_RECORD_WITH_RINGBUFFER,
        audi_pb2.DvrMode.DvrSystemState.DVR_SYSTEM_STATE_RECORD_WITHOUT_RINGBUFFER,
    )
    return mode.dvr_mode in recording_states


def get_latest_video_group(recordings):
    """找出「最新一部影片」的 group，排除照片（Recording 首頁大縮圖只顯示影片，
    照片只留在 Gallery 的 Photo 分類）。找不到影片回傳 None。

    2026-08-21 提醒：這裡是用 groupid 數值大小決定「最新」（groups 已經是
    groupid 由大到小排列），不是用 trigger_time。groupid 是 DVR 端有界、
    非全域唯一的計數器，換 SD 卡或同一張卡上都不保證「數字比較大＝時間
    比較新」——Recording 首頁 Preview／新錄影偵測這兩處已經改用下面的
    get_latest_video_group_by_trigger_time()，不再呼叫這支函式。這支函式
    保留給尚未遇到這個問題、暫時還沒改的其他呼叫端用，不要在需要正確反映
    時間先後的地方使用它。"""
    groups = group_recordings_by_groupid(recordings)
    video_groups = [group for group in groups if not is_photo_recording(get_group_representative(group))]
    if not video_groups:
        return None
    return video_groups[0]  # groups 已經是 groupid 由大到小排序，第一個就是最新的


def get_latest_video_item_by_trigger_time(recordings):
    """在所有非 Photo 的原始 recording items 裡（不先分組），直接找
    trigger_time（interval.start_time）最大的那一筆，回傳這筆 item 本身。
    找不到就回傳 None。

    這是「用時間決定最新影片」的核心邏輯，故意不透過
    group_recordings_by_groupid() 分組後再挑代表項目：分組後的代表項目
    固定取 sequenceid 最小的那一段（get_group_representative()），如果
    groupid 恰好被兩支不相關的影片共用而被誤合併成一組，代表項目可能
    還是舊的那一段，會讓比較 trigger_time 時漏掉真正最新的那一筆。直接
    在所有原始 item 裡比較，就不會被分組結果影響。"""
    video_items = [item for item in recordings if not is_photo_recording(item)]
    if not video_items:
        return None
    return max(video_items, key=lambda item: item.interval.start_time.ToDatetime())


def get_latest_video_group_by_trigger_time(recordings):
    """跟 get_latest_video_item_by_trigger_time() 一樣先找出 trigger_time
    最新的那一筆，再回傳它所屬的完整 group（其他段落一起帶出來，依
    sequenceid 排序，保留「多段落算同一支影片」的既有顯示規則），給需要
    完整 group（例如要下載縮圖、算總長度）的呼叫端用。找不到影片回傳
    None。

    2026-08-21 新增，取代 Recording 首頁 Preview／新錄影偵測（screen_
    recording.py）原本間接依賴 get_latest_video_group()（groupid 排序）的
    做法。

    2026-08-21 修正：原本這裡直接用 item.groupid == latest_item.groupid
    在原始清單裡抓同 groupid 的其他項目，繞過了 group_recordings_by_
    groupid()——真機證實 groupid 會被不相關的影片重複使用時，這樣抓出來
    的「group」其實是混了舊影片的錯誤結果（例如 groupid=77 底下混進一支
    trigger_time 停在 2023-01-01 的舊影片）。改成呼叫已修正的
    group_recordings_by_groupid()（見上方 2026-08-21 分組規則說明），
    找出 latest_item 真正所屬的那個子群組，不再自己重複一份分組邏輯。"""
    latest_item = get_latest_video_item_by_trigger_time(recordings)
    if latest_item is None:
        return None
    groups = group_recordings_by_groupid(recordings)
    # 用 is（物件身分）比對，不用 in（結構相等）：protobuf message 的 ==
    # 是比較欄位內容，如果剛好有兩筆不同的原始 item 欄位值完全相同（理論
    # 上可能發生），in 可能比對到錯的那一筆，is 才能保證比對到的就是
    # get_latest_video_item_by_trigger_time() 實際選出來的那個物件。
    return next((group for group in groups if any(item is latest_item for item in group)), None)


# ============ 顯示文字格式化 ============
# 這些函式把 protobuf 的欄位轉成 GUI 要顯示的文字。
# 放在 api.py 是因為只有這裡會 import audi_pb2，GUI 端不需要知道 protobuf 的細節。

def get_event_name_text(recording_item):
    """把 type_of_recording 這個 enum 轉成比較好讀的文字"""
    full_name = audi_pb2.TypeOfRecording.Name(recording_item.type_of_recording)
    return full_name.replace('TYPE_OF_RECORDING_', '')


# type_of_recording 真正的 enum 名稱 -> Gallery 要顯示的文字。
# 名稱清單是直接從目前這份 audi_pb2.py 讀出來的（不是憑空猜的），對照如下：
#   TYPE_OF_RECORDING_STREETVIDEO_HOST_TRIGGERED       -> Street Video
#   TYPE_OF_RECORDING_STREETVIDEO_USER_EVENT_TRIGGERED -> Event   （接真機驗證過的類型之一）
#   TYPE_OF_RECORDING_STREETVIDEO_USER_SCENIC_TRIGGERED-> Scenic  （接真機驗證過，實際錄影的類型）
#   TYPE_OF_RECORDING_STREETVIDEO_PARK_TRIGGERED       -> Parking
#   TYPE_OF_RECORDING_STREETPHOTO_*_TRIGGERED          -> Photo   （其中 USER_TRIGGERED 接真機驗證過）
#   TYPE_OF_RECORDING_RACETRACKVIDEO_*_TRIGGERED       -> Race Track
#   TYPE_OF_RECORDING_REMASTEREDVIDEO / REMASTEREDPHOTO-> Remastered Video / Remastered Photo
TYPE_OF_RECORDING_DISPLAY_NAMES = {
    'TYPE_OF_RECORDING_STREETVIDEO_HOST_TRIGGERED': 'Street Video',
    'TYPE_OF_RECORDING_STREETVIDEO_USER_EVENT_TRIGGERED': 'Event',
    'TYPE_OF_RECORDING_STREETVIDEO_USER_SCENIC_TRIGGERED': 'Scenic',
    'TYPE_OF_RECORDING_STREETVIDEO_PARK_TRIGGERED': 'Parking',
    'TYPE_OF_RECORDING_STREETPHOTO_USER_TRIGGERED': 'Photo',
    'TYPE_OF_RECORDING_STREETPHOTO_HOST_TRIGGERED': 'Photo',
    'TYPE_OF_RECORDING_STREETPHOTO_DASHCAM_TRIGGERED': 'Photo',
    'TYPE_OF_RECORDING_RACETRACKVIDEO_USER_TRIGGERED': 'Race Track',
    'TYPE_OF_RECORDING_RACETRACKVIDEO_HOST_TRIGGERED': 'Race Track',
    'TYPE_OF_RECORDING_RACETRACKVIDEO_DASHCAM_TRIGGERED': 'Race Track',
    'TYPE_OF_RECORDING_REMASTEREDVIDEO': 'Remastered Video',
    'TYPE_OF_RECORDING_REMASTEREDPHOTO': 'Remastered Photo',
    'TYPE_OF_RECORDING_REGULATIONS_UNSPECIFIED': 'Unspecified',
}


def get_display_type_text(recording_item):
    """Gallery 顯示用的類型文字，一律依照真正的 type_of_recording 判斷，
    不要只看 get_category() 的粗分類（那個只分 recording/event/photo 三種，
    會把 Scenic、Parking 這些不同類型都混在一起顯示成同一個名字）。
    對照不到的話，直接回傳去掉前綴的 enum 名稱，不會顯示空白。"""
    type_name = audi_pb2.TypeOfRecording.Name(recording_item.type_of_recording)
    return TYPE_OF_RECORDING_DISPLAY_NAMES.get(type_name, type_name.replace('TYPE_OF_RECORDING_', ''))


def format_duration_seconds(total_seconds):
    """把秒數轉成 mm:ss 格式"""
    total_seconds = int(total_seconds)
    minutes = total_seconds // 60
    seconds = total_seconds % 60
    return f'{minutes:02d}:{seconds:02d}'


def get_duration_text(recording_item):
    """單一筆資料的長度，mm:ss 格式"""
    return format_duration_seconds(recording_item.duration.seconds)


def get_group_duration_text(group_items):
    """一整組（同一個 groupid，可能好幾段）加總起來的長度，mm:ss 格式——
    這才是使用者感覺到的「這部影片」總長度，不是只看其中一段"""
    return format_duration_seconds(get_group_total_duration_seconds(group_items))


def get_time_text(recording_item):
    """錄影時間，格式 yyyy/MM/dd HH:mm:ss；拿不到時間的話回傳 'N/A'"""
    try:
        return recording_item.interval.start_time.ToDatetime().strftime('%Y/%m/%d %H:%M:%S')
    except Exception:
        return 'N/A'


def get_filename_text(recording_item):
    """從 srcUri 取出檔名（不含資料夾路徑）。srcUri 本身就是「資料夾+檔名」
    （build_video_url() 直接拿去接 base_url 當播放網址），例如
    '/scenic/video/20230102_002813.mp4' 取最後一段就是
    '20230102_002813.mp4'。srcUri 是 URL 路徑一律用 '/' 分隔，不用
    os.path（Windows 上的 os.path 邏輯是給本機檔案路徑用的，這裡的
    輸入來源是 DVR 回傳的網址路徑，兩者概念不一樣）。拿不到/是空字串
    就回傳 'N/A'，跟 get_time_text() 的錯誤處理風格一致。"""
    try:
        filename = recording_item.srcUri.rstrip('/').split('/')[-1]
        return filename if filename else 'N/A'
    except Exception:
        return 'N/A'


def get_recording_label_text(recording_item):
    """縮圖下方要顯示的文字：檔案編號、事件類型、影片長度"""
    return f'#{recording_item.fileid}  {get_event_name_text(recording_item)}\n{get_duration_text(recording_item)}'


# ============ RecordingEdit：刪除 / 收藏 ============
# 協議是跟 AutoMate 既有案例（test_DA005_edit_001/003/007/009/012/013.py）
# 對照確認過的，不是自己猜的：
# - 每一筆要編輯的項目，用一次 PUT /dashcam/recordingEdit 個別「標記」
#   （stage）要做的動作：影片用 groupid，照片用 fileid（不是 groupid）。
# - type_of_recording 一定要填「這個項目本身真正的類型」，不能省略。
#   實測過（2026-08-11）：沒填或填錯，DVR 完全不會報錯，但也完全不會生效
#   （靜默失敗），這是舊版這兩支函式一直沒生效的原因，這次順便修掉。
# - 全部要編輯的項目都標記完之後，最後再送一次 groupid=0 當作「commit」，
#   DVR 才會真正把前面那些標記套用下去。實測過（2026-08-11，用控制組驗證）：
#   groupid=0 只會 commit 剛剛那幾筆真的送過的項目，不會影響其他沒送過的
#   recordings（不是「套用到全部影片」的萬用指令），也不受 commit 那筆
#   本身帶的 type_of_recording 影響（混合 Scenic+Event+Photo 一次 commit
#   全部有效，親自測過）。
# - 但 commit 那筆的 action 一定要跟這批標記的動作一致（Favorite 批次用
#   COMMAND_SET_FAVOURITE 收尾、Unfavorite 用 COMMAND_UNSET_FAVOURITE、
#   Delete 用 COMMAND_DELETE），對照 AutoMate edit_005/007/013.py 都是
#   這樣寫。實測過（2026-08-11）：Delete 批次如果用 COMMAND_SET_FAVOURITE
#   當 commit action，DVR 完全不會刪除，之前以為「commit 的 action 不影響
#   結果」是只驗證過 Favorite 這一種動作，不能套用到 Delete。

def send_recording_edit(action, groupid=None, fileid=None, type_of_recording=None):
    """對 /dashcam/recordingEdit 送一筆編輯指令。groupid/fileid 兩個至少要
    給一個：影片給 groupid，照片給 fileid；type_of_recording 一定要給，
    而且要是這個項目真正的類型，不然 DVR 不會生效。"""
    message = audi_pb2.RecordingEdit()
    message.action = action
    if groupid is not None:
        message.groupid = groupid
    if fileid is not None:
        message.fileid = fileid
    if type_of_recording is not None:
        message.type_of_recording = type_of_recording
    send_put('/dashcam/recordingEdit', message.SerializeToString())


def commit_recording_edit_batch(action, type_of_recording):
    """幫前面已經標記好的那些項目做「commit」，DVR 才會真的套用。
    action 一定要跟前面標記的動作一致（例如整批都是 Delete，這筆 commit
    也要是 COMMAND_DELETE），對照 AutoMate edit_005/007/013.py 都是這樣
    寫。type_of_recording 不影響套用結果（混合 Scenic+Event+Photo 一次
    commit 全部有效，親自測過），但還是照 AutoMate 案例的寫法帶著。"""
    send_recording_edit(action, groupid=0, type_of_recording=type_of_recording)


def commit_favorite_batch(type_of_recording):
    """整批 Favorite 標記完後的 commit。"""
    commit_recording_edit_batch(audi_pb2.RecordingEdit.Command.COMMAND_SET_FAVOURITE, type_of_recording)


def commit_unfavorite_batch(type_of_recording):
    """整批 Unfavorite 標記完後的 commit。"""
    commit_recording_edit_batch(audi_pb2.RecordingEdit.Command.COMMAND_UNSET_FAVOURITE, type_of_recording)


def commit_delete_batch(type_of_recording):
    """整批 Delete 標記完後的 commit。"""
    commit_recording_edit_batch(audi_pb2.RecordingEdit.Command.COMMAND_DELETE, type_of_recording)


def delete_recording(fileid, type_of_recording):
    """刪除指定 fileid 的影片/照片（用在單一 fileid，例如照片）。
    只是「標記」要刪除，還要呼叫 commit_recording_edit_batch() 才會真的刪掉。"""
    send_recording_edit(audi_pb2.RecordingEdit.Command.COMMAND_DELETE,
                         fileid=fileid, type_of_recording=type_of_recording)


def delete_recording_group(groupid, type_of_recording):
    """刪除指定 groupid 的整組影片（多段影片會一起刪）。
    只是「標記」要刪除，還要呼叫 commit_recording_edit_batch() 才會真的刪掉。"""
    send_recording_edit(audi_pb2.RecordingEdit.Command.COMMAND_DELETE,
                         groupid=groupid, type_of_recording=type_of_recording)


def set_recording_favorite(fileid, is_favorite, type_of_recording):
    """設定/取消收藏（用在單一 fileid，例如照片）。
    只是「標記」，還要呼叫 commit_recording_edit_batch() 才會真的生效。"""
    action = (audi_pb2.RecordingEdit.Command.COMMAND_SET_FAVOURITE if is_favorite
              else audi_pb2.RecordingEdit.Command.COMMAND_UNSET_FAVOURITE)
    send_recording_edit(action, fileid=fileid, type_of_recording=type_of_recording)


def set_recording_group_favorite(groupid, is_favorite, type_of_recording):
    """設定/取消收藏（用在整組 groupid，多段影片）。
    只是「標記」，還要呼叫 commit_recording_edit_batch() 才會真的生效。"""
    action = (audi_pb2.RecordingEdit.Command.COMMAND_SET_FAVOURITE if is_favorite
              else audi_pb2.RecordingEdit.Command.COMMAND_UNSET_FAVOURITE)
    send_recording_edit(action, groupid=groupid, type_of_recording=type_of_recording)


def delete_recording_item(representative_item):
    """刪除一整個 group（用代表項目判斷是影片還是照片，各用正確的欄位定址）"""
    if is_photo_recording(representative_item):
        delete_recording(representative_item.fileid, representative_item.type_of_recording)
    else:
        delete_recording_group(representative_item.groupid, representative_item.type_of_recording)


def set_recording_item_favorite(representative_item, is_favorite):
    """設定/取消收藏一整個 group（用代表項目判斷是影片還是照片，各用正確的欄位定址）"""
    if is_photo_recording(representative_item):
        set_recording_favorite(representative_item.fileid, is_favorite, representative_item.type_of_recording)
    else:
        set_recording_group_favorite(representative_item.groupid, is_favorite, representative_item.type_of_recording)


# ============ CurrentTime：時間校正 ============

def set_current_time(adjusted_datetime_utc, gmt_offset_minutes):
    """PUT /dashcam/currentTime"""
    message = audi_pb2.CurrentTime()
    message.timestamp.FromDatetime(adjusted_datetime_utc)
    message.gmt_offset = gmt_offset_minutes
    send_put('/dashcam/currentTime', message.SerializeToString())


# 2026-08-19 使用者要求：每個新的 DVR Connected Session（App Startup 連上、
# 或斷線後 Reconnect）自動送一次 PC Current Time 給 DVR，不要 KeepAlive
# 每次都送、不要固定週期 polling、不要 RESOURCE_DVR_MODE 一變化就送。
#
# 2026-08-19 使用者修正：gmt_offset_minutes 不可以寫死常數——需求本來就是
# 「同步 PC 當下時區」，要從 PC 系統當下的時區設定即時算出來（包含 DST，
# 如果 PC 所在時區有日光節約時間），台灣 PC 算出來剛好會是 480，但那是
# 算出來的結果，不是 hard-code 的假設。timestamp 欄位/protobuf 寫法本身
# 沒有變，一樣沿用既有的 set_current_time(adjusted_datetime_utc,
# gmt_offset_minutes)。
def _get_local_gmt_offset_minutes():
    """回傳 PC 當下本機時區跟 UTC 的偏移分鐘數（正負號、含 DST 都會反映
    當下實際狀態）。datetime.now().astimezone()（不帶參數）會自動套用
    作業系統當下的本機時區規則，.utcoffset() 拿到的就是「這一刻」的
    真正偏移量——同一台 PC 在有 DST 的時區，夏令/冬令時間呼叫這支函式
    會給不同結果，這是故意的，不是 bug。"""
    local_now = datetime.now().astimezone()
    return int(local_now.utcoffset().total_seconds() // 60)


def sync_current_time_once(context_label):
    """自動同步一次 PC Current Time 給 DVR（給「新的 Connected Session」
    這種一次性時機呼叫，例如 Startup Sync / Reconnect baseline recovery
    worker，不要在 KeepAlive 或週期性 polling 裡呼叫）。

    context_label 只是用來讓 log 分得出是哪個時機觸發的（例如
    'startup connection'、'reconnect'），不影響實際送出的內容。

    刻意在這裡就把 try/except 包起來、只印 log 不 raise：呼叫端
    （Startup Sync / Reconnect worker）本身既有的 baseline recovery
    流程不應該被這裡的失敗擋住，維持既有的『不阻擋後續流程』設計。"""
    print(f'[CurrentTime] {context_label}: sending PC current time ...')
    try:
        gmt_offset_minutes = _get_local_gmt_offset_minutes()
        set_current_time(datetime.utcnow(), gmt_offset_minutes)
    except Exception as error:
        print(f'[CurrentTime] {context_label}: result=failed: {error}')
    else:
        print(f'[CurrentTime] {context_label}: result=success (gmt_offset_minutes={gmt_offset_minutes})')


# ============ KeepAlive：背景持續送 keepalive 保持連線 ============
# 邏輯照抄 AutoMate 的 apis/da005Related/keepalive.py，差別是網址改成呼叫
# _get_base_url()，這樣如果之後在 Settings 換了連線位址，keepalive 會自動
# 跟著換，不會像原本那樣把 DVR 位址另外寫死一份。

_keepalive_queue = queue.Queue()
_keepalive_event = threading.Event()
_keepalive_thread = None

# KeepAlive 的回應裡有 updated_resources 這個欄位，韌體會用它告訴我們「從上次問到
# 現在，哪些資源有變化」（例如 RESOURCE_DVR_MODE 代表錄影狀態變了）。實機驗證過
# （2026-08-10）：開始錄影/觸發 Event/Event 自動結束/停止錄影，每一次 KeepAlive
# 都在 50~250ms 內就回報 RESOURCE_DVR_MODE，比固定間隔去 GET DvrMode 更快也更省。
# 所以 Recording 頁面監控 Event 有沒有結束，改成等這個旗標，不要再固定 sleep 輪詢。
_dvr_mode_changed_event = threading.Event()

# RESOURCE_RECORDINGS 代表 /dashcam/recordings 清單有變化（例如新錄影項目登記進去了），
# 實機也驗證過會跟 RESOURCE_DVR_MODE 差不多時間回報，一樣可以拿來取代固定間隔輪詢
# /dashcam/recordings（screen_recording.py 等新錄影項目出現、抓縮圖時會用到）。
_recordings_changed_event = threading.Event()

# 2026-08-20 使用者要求：SD Card Presence 不能新增週期性 GET Memory
# polling，只能在「App Startup」跟「KeepAlive 通知 Memory 有變化」這兩個
# 時機才 GET 一次。確認過 audi_dashcam_ncm_protocol_v0.28.proto 的
# KeepAlive.Resource enum，代表「/dashcam/memory 有變化」（拔插 SD 卡）
# 的是 RESOURCE_MEMORY（數值 21）——跟 RESOURCE_DVR_MODE/RESOURCE_
# RECORDINGS 同一種機制，不是用猜的。
_memory_changed_event = threading.Event()

# 2026-08-20 使用者要求：DVR System Restart 後的 Runtime Baseline
# Recovery，一樣「不要新增固定頻率 GET DvrMode polling」，只能在 KeepAlive
# 通知 RESOURCE_DVR_MODE 有變化時才 GET 一次來判斷。這裡刻意跟上面既有的
# _dvr_mode_changed_event 分開、另外開一個獨立的 Event，兩個消費者各自
# clear 各自的旗標，不會互相偷走對方的通知（_dvr_mode_changed_event 是
# wait_for_dvr_mode_change_notification() 的阻塞式消費者，目前給 Event
# Monitoring 用；這個新的是非阻塞消費者，給全域連線監測 tick 用，跟
# _memory_changed_event/consume_memory_change_notification() 同一種
# 阻塞版跟非阻塞版分開的既有模式）。
_dvr_mode_baseline_check_event = threading.Event()


def start_keepalive():
    """開始送 keepalive；背景執行緒只會真的建立一次，重複呼叫這個函式沒關係"""
    global _keepalive_thread
    if _keepalive_thread is None:
        _keepalive_thread = threading.Thread(target=_run_keepalive_loop, daemon=True)
        _keepalive_thread.start()
    _keepalive_event.set()


def stop_keepalive():
    """暫停送 keepalive（背景執行緒不會結束，只是先卡在等待，之後還能 start_keepalive() 恢復）"""
    _keepalive_event.clear()


def is_keepalive_running():
    return _keepalive_event.is_set()


def wait_for_dvr_mode_change_notification(timeout):
    """等待 KeepAlive 通知「DvrMode 有變化」（回應的 updated_resources 出現
    RESOURCE_DVR_MODE），取代固定間隔去 GET DvrMode 猜狀態有沒有變。
    回傳 True 表示等到了通知，False 表示等了 timeout 秒都沒等到（呼叫端應該
    自己再檢查一次，不要因為沒收到通知就以為狀態一定沒變）。
    等到之後會自動清掉旗標，下一次呼叫才不會誤判成「舊的通知」。"""
    got_notified = _dvr_mode_changed_event.wait(timeout)
    if got_notified:
        _dvr_mode_changed_event.clear()
    return got_notified


def clear_dvr_mode_change_notification():
    """開始一段新的監控前先清掉舊旗標，避免把「之前某個不相關的變化」誤判成
    這次要等的那個變化（旗標本身不記錄變了什麼，只記錄「有沒有變」）。"""
    _dvr_mode_changed_event.clear()


def wait_for_recordings_change_notification(timeout):
    """等待 KeepAlive 通知「/dashcam/recordings 清單有變化」（回應的
    updated_resources 出現 RESOURCE_RECORDINGS），取代固定間隔去 GET
    recordings 猜清單有沒有變。用法跟 wait_for_dvr_mode_change_notification()
    一樣：回傳 True 表示等到了通知，False 表示等了 timeout 秒都沒等到。"""
    got_notified = _recordings_changed_event.wait(timeout)
    if got_notified:
        _recordings_changed_event.clear()
    return got_notified


def consume_memory_change_notification():
    """非阻塞版本：檢查 KeepAlive 有沒有通知過「/dashcam/memory 有變化」
    （拔插 SD 卡），有的話立刻清掉旗標並回傳 True，沒有就立刻回傳 False，
    不會等待。SD Card Presence 的全域監測用 Tk after() 週期性 tick 檢查
    （跟 screen_recording.py 既有的 DVR 連線監測同一種寫法），不是背景
    執行緒裡的阻塞 wait，所以需要一個立刻回傳結果的版本，跟上面兩個
    wait_for_..._change_notification()（會阻塞）分開，呼叫語意不同。"""
    if _memory_changed_event.is_set():
        _memory_changed_event.clear()
        return True
    return False


def consume_dvr_mode_baseline_check_notification():
    """非阻塞版本：檢查 KeepAlive 有沒有通知過「DvrMode 有變化」
    （RESOURCE_DVR_MODE），有的話立刻清掉旗標並回傳 True，沒有就立刻回傳
    False，不會等待。專門給 Runtime DVR Baseline Recovery 的全域連線監測
    tick 用（跟 consume_memory_change_notification() 同一種寫法）——刻意
    用獨立的 _dvr_mode_baseline_check_event，不去消費既有的
    _dvr_mode_changed_event，避免跟 Event Monitoring 現有的阻塞式
    wait_for_dvr_mode_change_notification() 互相偷走對方的通知。"""
    if _dvr_mode_baseline_check_event.is_set():
        _dvr_mode_baseline_check_event.clear()
        return True
    return False


KEEPALIVE_CYCLE_SECONDS = 0.05  # 送快一點，留更多緩衝空間給連線本身的延遲（URL 的 cycle=100 沒改，只是我們送更快）

# ============ DVR 連線狀態（2026-08-18 新增）============
# 專案原本完全沒有任何「DVR 現在是不是連得上」的訊號——KeepAlive 失敗時
# 只是把 id 佇列歸零、印一行 log，不會留下任何可以查詢的狀態。這裡直接
# 搭這班既有的 KeepAlive（本來就每 ~100ms 送一次）順風車，記錄「最近
# 這幾次有沒有成功」，不是另外開一個新的高頻率 GET polling。
#
# 連續失敗達到門檻才判定成「斷線」（DVR_DISCONNECT_FAILURE_THRESHOLD 次，
# 每次週期約 100ms，約 0.5 秒），避免單一次網路小抖動就誤判；恢復連線
# 則是只要有一次成功就馬上算數（斷線要穩定確認、恢復要盡快讓使用者知道，
# 是常見的不對稱判斷方式）。
_dvr_connected = None                    # None＝還沒有任何一次 KeepAlive 結果（工具剛啟動）
_consecutive_keepalive_failures = 0
_dvr_connection_lock = threading.Lock()  # 保護 _dvr_connected/_consecutive_keepalive_failures：KeepAlive 背景執行緒寫、UI 執行緒讀
DVR_DISCONNECT_FAILURE_THRESHOLD = 5


def is_dvr_connected():
    """回傳目前是否連得上 DVR，資料來源是既有 KeepAlive 背景執行緒持續
    更新的旗標，呼叫這支函式本身不會觸發任何新的網路請求。還沒有任何一次
    KeepAlive 結果時（None）視同已連線（樂觀預設，避免工具剛啟動、
    KeepAlive 還沒送出第一次就先顯示斷線畫面）。"""
    with _dvr_connection_lock:
        return _dvr_connected if _dvr_connected is not None else True


def _mark_keepalive_success():
    global _dvr_connected, _consecutive_keepalive_failures
    with _dvr_connection_lock:
        _dvr_connected = True
        _consecutive_keepalive_failures = 0


def _mark_keepalive_failure():
    global _dvr_connected, _consecutive_keepalive_failures
    with _dvr_connection_lock:
        _consecutive_keepalive_failures += 1
        if _consecutive_keepalive_failures >= DVR_DISCONNECT_FAILURE_THRESHOLD:
            _dvr_connected = False


def _run_keepalive_loop():
    received_message = audi_pb2.KeepAlive()
    _keepalive_queue.put(0)
    session = requests.Session()

    while True:
        _keepalive_event.wait()
        cycle_start_time = time()
        try:
            current_id = _keepalive_queue.get() if not _keepalive_queue.empty() else 0
            url = _get_base_url() + f'/dashcam/keepAlive?id={current_id}&cycle=100&reset=FALSE'
            response = session.get(url, timeout=1)
            if response.status_code == 200:
                received_message.ParseFromString(response.content)
                if audi_pb2.KeepAlive.Resource.RESOURCE_DVR_MODE in received_message.updated_resources:
                    _dvr_mode_changed_event.set()
                    _dvr_mode_baseline_check_event.set()
                if audi_pb2.KeepAlive.Resource.RESOURCE_RECORDINGS in received_message.updated_resources:
                    _recordings_changed_event.set()
                if audi_pb2.KeepAlive.Resource.RESOURCE_MEMORY in received_message.updated_resources:
                    _memory_changed_event.set()
                # id 一直往上加，加到 1000 就歸零重新開始（跟韌體端的規則一致）
                next_id = current_id + 1 if current_id < 1000 else 0
                _keepalive_queue.put(next_id)
                _mark_keepalive_success()
            else:
                _keepalive_queue.put(0)  # 失敗就歸零重新開始
                _mark_keepalive_failure()
        except requests.RequestException:
            _keepalive_queue.put(0)
            _mark_keepalive_failure()
        except Exception as error:
            print(f'[keepalive] 未預期的錯誤: {error}')
            _mark_keepalive_failure()

        # 實測發現：不管這一輪 HTTP 請求花了多久都固定再 sleep(0.1)，會讓整個
        # 週期變成「請求時間 + 100ms」，超過韌體要求的 100ms 週期（例如請求花
        # 40ms，週期就變成 140ms），韌體可能因此判定 ID 序號異常、跟其他連線
        # 打架。改成量測這一輪實際花了多少時間，只補齊剩下的部分，讓整個週期
        # 盡量貼齊 100ms；如果這一輪本身就已經超過 100ms（連線很慢的情況），
        # 就不用再等，馬上送下一輪。
        elapsed_seconds = time() - cycle_start_time
        remaining_seconds = KEEPALIVE_CYCLE_SECONDS - elapsed_seconds
        if remaining_seconds > 0:
            sleep(remaining_seconds)


# ============ Live View：RTSP 即時影像 (ffmpeg 子行程) ============
# 這裡不是 REST/Protobuf API，而是額外開一個 ffmpeg 子行程把 RTSP 串流轉成
# rawvideo RGB24 從 stdout pipe 吐出來，一樣把它包在 api.py 裡，GUI 只需要
# 呼叫 start_liveview() / stop_liveview() / get_latest_liveview_frame()。
#
# 2026-09-21 從原本的 `-f sdl`（另開一個 SDL 視窗）改成 pipe 給 GUI 自己畫在
# Recording 首頁的縮圖區域——不再跳出額外視窗。
#
# 「latest frame only」設計：背景 thread 只維護一個 bytes 變數，每次讀到新
# 畫面就整包換掉，不是 append 進 queue。GUI 端（screen_recording.py）用
# after() 定期主動來拿一次，拿到什麼就是什麼，UI 跟不上時自然就是「漏看」
# 某幾張畫面，不會累積、不會造成延遲越來越嚴重。
#
# pipe read 邏輯特意不共用 video_decoder.py 的 _read_exact()：Live View 是
# 連續串流、沒有 seek/EOF 語意，跟 Player 用的 decoder pipeline 目的不同，
# 這裡自己維護一份一樣簡單的「讀滿 N bytes」小工具，不會為了 Live View 去動
# 已經驗證過穩定的 Playback 解碼路徑。

_liveview_process = None
_liveview_reader_thread = None
_liveview_stop_flag = None  # threading.Event()，reader thread 用來分辨「使用者主動停止」跟「連線真的斷了」
_liveview_frame_lock = threading.Lock()
_liveview_latest_frame_bytes = None
_LIVEVIEW_FRAME_SIZE = (640, 360)  # (width, height)，要跟下面 ffmpeg -vf scale 參數一致
_LIVEVIEW_FPS = 15


def is_liveview_running():
    return _liveview_process is not None


def get_liveview_frame_size():
    """回傳 rawvideo 畫面的 (width, height)，GUI 端要用這個尺寸還原 bytes。"""
    return _LIVEVIEW_FRAME_SIZE


def get_latest_liveview_frame():
    """回傳目前最新一張畫面的 RGB24 raw bytes；還沒收到過畫面或已停止就回傳 None。"""
    with _liveview_frame_lock:
        return _liveview_latest_frame_bytes


def _read_exact_from_liveview_pipe(stream, num_bytes):
    """從 pipe 讀滿 num_bytes 才回傳；讀到 EOF（read 回傳空 bytes）回傳 None。
    Live View 專用的獨立小工具，見上方模組說明——故意不跟 video_decoder.py
    共用。"""
    buffer = bytearray()
    while len(buffer) < num_bytes:
        chunk = stream.read(num_bytes - len(buffer))
        if not chunk:
            return None
        buffer.extend(chunk)
    return bytes(buffer)


def _liveview_reader_loop(process, stop_flag, on_error):
    """背景 daemon thread：持續從 ffmpeg stdout 讀一張張完整畫面，每讀到一張
    就整包換掉 _liveview_latest_frame_bytes（不排隊）。讀到 EOF 而且不是
    使用者主動停止的話，視為連線中斷，回報 on_error。"""
    global _liveview_latest_frame_bytes
    width, height = _LIVEVIEW_FRAME_SIZE
    frame_byte_size = width * height * 3  # RGB24：每個像素 3 bytes
    try:
        while not stop_flag.is_set():
            frame_bytes = _read_exact_from_liveview_pipe(process.stdout, frame_byte_size)
            if frame_bytes is None:
                break
            with _liveview_frame_lock:
                _liveview_latest_frame_bytes = frame_bytes
    except (OSError, ValueError) as error:
        print(f'[liveview] reader thread 發生例外: {error}')

    if not stop_flag.is_set() and on_error:
        on_error('RTSP 連線中斷或畫面讀取失敗')


def start_liveview(on_error=None):
    """啟動 RTSP Live View；on_error(message) 會在連線失敗/中斷時被呼叫
    （可能從背景 thread 呼叫，GUI 端要自己排回 main thread 才能動 Tkinter）。
    如果已經啟動，直接 return，不會重複建立 ffmpeg 子行程。"""
    global _liveview_process, _liveview_reader_thread, _liveview_stop_flag, _liveview_latest_frame_bytes
    if _liveview_process is not None:
        return

    ffmpeg_path = find_ffmpeg_executable()
    if ffmpeg_path is None:
        if on_error:
            on_error('找不到可用的 ffmpeg，無法啟動 Live View')
        return

    width, height = _LIVEVIEW_FRAME_SIZE
    rtsp_url = get_liveview_rtsp_url()
    command = [
        ffmpeg_path,
        '-rtsp_transport', 'udp',
        '-i', rtsp_url,
        '-vf', f'scale={width}:{height},fps={_LIVEVIEW_FPS}',
        '-f', 'rawvideo',
        '-pix_fmt', 'rgb24',
        'pipe:1',
    ]

    with _liveview_frame_lock:
        _liveview_latest_frame_bytes = None
    _liveview_stop_flag = threading.Event()
    _liveview_process = subprocess.Popen(
        command,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        creationflags=_CREATE_NO_WINDOW,
    )
    process = _liveview_process
    stop_flag = _liveview_stop_flag

    def watch_for_errors():
        for line in iter(process.stderr.readline, b''):
            decoded_line = line.decode('utf-8', errors='ignore')
            print('[ffmpeg][liveview]', decoded_line.strip())
            if 'Network is unreachable' in decoded_line or 'Conversion failed' in decoded_line:
                if on_error and not stop_flag.is_set():
                    on_error('RTSP 連線失敗：Network is unreachable')
                break

    threading.Thread(target=watch_for_errors, daemon=True).start()

    _liveview_reader_thread = threading.Thread(
        target=_liveview_reader_loop, args=(process, stop_flag, on_error), daemon=True,
    )
    _liveview_reader_thread.start()


def stop_liveview():
    """停止 Live View：通知 reader thread 停止 -> terminate ffmpeg（逾時就
    kill）-> 關閉 pipe -> 等 reader thread 正常結束 -> 清除 latest frame。
    可以安全重複呼叫（沒有在跑就直接 return）。"""
    global _liveview_process, _liveview_reader_thread, _liveview_stop_flag, _liveview_latest_frame_bytes
    if _liveview_process is None:
        return

    if _liveview_stop_flag is not None:
        _liveview_stop_flag.set()

    process = _liveview_process
    _liveview_process = None
    try:
        process.terminate()
        process.wait(timeout=1)
    except subprocess.TimeoutExpired:
        process.kill()
    except OSError:
        pass

    if process.stdout is not None:
        process.stdout.close()
    if process.stderr is not None:
        process.stderr.close()

    if _liveview_reader_thread is not None:
        _liveview_reader_thread.join(timeout=1)
    _liveview_reader_thread = None

    with _liveview_frame_lock:
        _liveview_latest_frame_bytes = None
