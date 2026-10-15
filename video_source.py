"""
video_source.py — 可替換的 Live View 影像來源（VideoSource abstraction）

2026-09-23 新增。目的：讓 IQ Adjustment 這種需要即時看畫面的頁面，可以
呼叫一個共同介面（VideoSource）取得 Live View 畫面，不用知道背後實際上是
RTSP 還是 UVC。目前有兩個實作：
    RTSPVideoSource       -- 透過網路 RTSP 取得畫面（保留作 debug / fallback）
    FFmpegUVCVideoSource  -- 2026-10-12 新增，透過 USB UVC 取得畫面（Windows
                             DirectShow / 2026-10-13 起 macOS AVFoundation），
                             首頁與 IQ Adjustment 預設使用
要用哪一個由 DA005ControlPanel.pyw 決定，呼叫端（live_view_controller.py／
screen_iq_adjustment.py）完全不用改。

介面（VideoSource）：
    start()          -- 開始連線/開始讀取畫面，非阻塞（很快回傳，實際
                         連線成功與否要靠 read_frame()/get_error() 判斷）
    read_frame()      -- 非阻塞，回傳目前最新一張畫面
                         (width, height, rgb24_bytes)，還沒有畫面就回傳 None
    stop()            -- 停止並釋放資源，可以安全重複呼叫
    is_running()      -- 目前是不是還在嘗試連線／已連線
    get_error()       -- 目前的錯誤訊息（str），沒有錯誤就回傳 None

這支檔案完全不 import tkinter、不知道呼叫端是哪個 GUI 頁面——純粹是
「拿到畫面」這件事的抽象層，跟 UI 完全分開。

RTSPVideoSource 的實作方式：沿用這個專案已經在用、且已經在 Recording
首頁 Live View 驗證過可以動的做法——用專案自帶的 ffmpeg（
video_decoder.find_ffmpeg_executable()）開一個 subprocess，RTSP 轉成
rawvideo RGB24 從 stdout pipe 吐出來，背景 thread 持續讀、只保留最新一張
畫面（latest-frame-only，不排隊）。

2026-09-23 分析：這個專案的 requirements.txt 已經有 opencv-python，理論上
可以用 cv2.VideoCapture(rtsp_url) 更省事，但這裡刻意不用——cv2 的 RTSP
支援吃的是 OpenCV 編譯時內建的 ffmpeg（版本/編譯選項不受這個專案控制，
也沒辦法像這裡一樣明確指定 -rtsp_transport udp 這種參數），可靠度不如
直接呼叫這個工具自己打包、自己控制版本的 ffmpeg.exe。api.py 的 Recording
Live View 已經用同一套做法驗證過可以動，這裡延用同一個經過驗證的方案，
不是重新引入新的相依套件，也不是隨意新增大型 dependency。
"""

import subprocess
import sys
import threading
import time

from video_decoder import find_ffmpeg_executable

# 目前 IQ Adjustment 頁面用的 RTSP 位址——集中放在這裡（RTSPVideoSource
# 的預設值），不要寫死在 screen_iq_adjustment.py 或 DA005ControlPanel.pyw
# 裡。之後如果要換成別的 Dashcam 位址，只要改這裡一個地方。
DEFAULT_RTSP_URL = 'rtsp://[fd53:7cb8:383:803::215]:554/h264'

# 2026-09-23 使用者實測回報：位址/網路不通時，畫面會永遠停在
# 「Connecting...」，不會跳出任何錯誤——根本原因是 ffmpeg 對一個「連得到
# host 但 RTSP handshake 卡住」或「封包被防火牆默默丟掉」的情況，可能既
# 不會印出錯誤也不會結束行程，只是一直等，這裡原本完全沒有計時，
# read_frame()/get_error() 就會一直回傳「還在等」。加一個連線逾時：
# start() 之後這麼多秒還沒收到過第一張畫面，就算連線失敗，跟
# video_decoder.py 的 OPEN_PROBE_TIMEOUT_SECONDS 是同一種理由。
CONNECT_TIMEOUT_SECONDS = 8

# Windows 下啟動 ffmpeg subprocess 不要跳出 cmd/Terminal 視窗——這是 GUI
# 工具背景解碼的既有規則，跟 api.py/video_decoder.py 同樣的理由，這裡
# 獨立維護一份常數（三支檔案本來就各自獨立、不互相 import，不划算為了
# 共用一個常數建立額外相依）。
_CREATE_NO_WINDOW = getattr(subprocess, 'CREATE_NO_WINDOW', 0)


class VideoSource:
    """所有 Live View 影像來源共用的介面，見檔案開頭說明。子類別要覆寫
    這五個方法；這裡的預設實作全部丟 NotImplementedError，避免不小心
    漏寫某個方法卻沒發現。"""

    def start(self):
        raise NotImplementedError

    def read_frame(self):
        raise NotImplementedError

    def stop(self):
        raise NotImplementedError

    def is_running(self):
        raise NotImplementedError

    def get_error(self):
        raise NotImplementedError


class RTSPVideoSource(VideoSource):
    """RTSP -> ffmpeg subprocess -> rawvideo RGB24 pipe -> 背景 thread ->
    latest frame only。跟 api.py 的 Recording Live View 是同一套已驗證
    過的做法，這裡是給 IQ Adjustment 用的獨立一份（不共用 api.py 的全域
    狀態——api.py 那份是 Recording 頁面專用的單例，兩個頁面各自需要
    獨立的畫面來源，不應該共用同一個 ffmpeg process）。"""

    def __init__(self, rtsp_url=DEFAULT_RTSP_URL, frame_width=640, frame_height=360, fps=15):
        self._rtsp_url = rtsp_url
        self._frame_width = frame_width
        self._frame_height = frame_height
        self._fps = fps

        self._process = None
        self._reader_thread = None
        self._stop_flag = None  # threading.Event()
        self._frame_lock = threading.Lock()
        self._latest_frame_bytes = None
        self._error_message = None
        self._start_time = None  # start() 呼叫當下的時間，給連線逾時判斷用

    def get_frame_size(self):
        """回傳 (width, height)，呼叫端要用這個尺寸還原 read_frame() 的 bytes。"""
        return self._frame_width, self._frame_height

    def start(self):
        if self._process is not None:
            return  # 已經在跑，不重複啟動第二條 thread/subprocess

        self._error_message = None
        self._start_time = time.time()
        with self._frame_lock:
            self._latest_frame_bytes = None

        ffmpeg_path = find_ffmpeg_executable()
        if ffmpeg_path is None:
            self._error_message = '找不到可用的 ffmpeg'
            return

        command = [
            ffmpeg_path,
            '-rtsp_transport', 'udp',
            '-i', self._rtsp_url,
            '-vf', f'scale={self._frame_width}:{self._frame_height},fps={self._fps}',
            '-f', 'rawvideo',
            '-pix_fmt', 'rgb24',
            'pipe:1',
        ]

        self._stop_flag = threading.Event()
        try:
            self._process = subprocess.Popen(
                command,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                creationflags=_CREATE_NO_WINDOW,
            )
        except OSError as error:
            self._error_message = f'啟動 ffmpeg 失敗: {error}'
            self._process = None
            return

        process = self._process
        stop_flag = self._stop_flag

        def watch_stderr():
            for line in iter(process.stderr.readline, b''):
                decoded_line = line.decode('utf-8', errors='ignore')
                print('[video_source][rtsp]', decoded_line.strip())

        threading.Thread(target=watch_stderr, daemon=True).start()

        self._reader_thread = threading.Thread(
            target=self._reader_loop, args=(process, stop_flag), daemon=True,
        )
        self._reader_thread.start()

    def _reader_loop(self, process, stop_flag):
        frame_byte_size = self._frame_width * self._frame_height * 3  # RGB24：每個像素 3 bytes
        try:
            while not stop_flag.is_set():
                frame_bytes = _read_exact_from_pipe(process.stdout, frame_byte_size)
                if frame_bytes is None:
                    break
                with self._frame_lock:
                    self._latest_frame_bytes = frame_bytes
        except (OSError, ValueError) as error:
            if not stop_flag.is_set():
                self._error_message = f'讀取畫面時發生例外: {error}'

        if not stop_flag.is_set():
            # 不是使用者主動停止，卻讀不到畫面了：視為連線中斷。
            self._error_message = self._error_message or 'RTSP 連線中斷或無法讀取畫面'

    def read_frame(self):
        with self._frame_lock:
            frame_bytes = self._latest_frame_bytes
        if frame_bytes is None:
            return None
        return self._frame_width, self._frame_height, frame_bytes

    def stop(self):
        if self._process is None:
            return  # 沒有在跑，安全地什麼都不做

        if self._stop_flag is not None:
            self._stop_flag.set()

        process = self._process
        self._process = None
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

        if self._reader_thread is not None:
            self._reader_thread.join(timeout=1)
        self._reader_thread = None

        with self._frame_lock:
            self._latest_frame_bytes = None

    def is_running(self):
        return self._process is not None

    def get_error(self):
        if self._error_message:
            return self._error_message

        # 連線逾時判斷：process 還活著、但這麼多秒了連一張畫面都沒收到過
        # （不是「讀到一半斷線」那種——那種已經在 _reader_loop() 裡設過
        # _error_message 了，會被上面那個 if 擋掉，不會走到這裡）。
        if self._process is not None and self._start_time is not None:
            with self._frame_lock:
                has_frame = self._latest_frame_bytes is not None
            if not has_frame and (time.time() - self._start_time) > CONNECT_TIMEOUT_SECONDS:
                return f'連線逾時（{CONNECT_TIMEOUT_SECONDS} 秒內沒有收到畫面，請確認 RTSP 位址/網路）'

        return None


def _read_exact_from_pipe(stream, num_bytes):
    """從 pipe 讀滿 num_bytes 才回傳；讀到 EOF（read 回傳空 bytes）回傳
    None。這是這支檔案自己維護的小工具，故意不跟 api.py／video_decoder.py
    共用同一份——三邊各自獨立、互不影響，改一邊不會不小心動到另一邊已經
    驗證過的行為。"""
    buffer = bytearray()
    while len(buffer) < num_bytes:
        chunk = stream.read(num_bytes - len(buffer))
        if not chunk:
            return None
        buffer.extend(chunk)
    return bytes(buffer)


# ==========================================
# UVC（USB 影像）來源
# ==========================================
# 2026-10-12 新增。取代原本用 camera index + cv2 的 UVCVideoSource 佔位類別。
#
# 為什麼不用 cv2.VideoCapture：
#   1. camera index 會隨裝置插拔順序改變，這裡改用 DirectShow 的「裝置名稱」
#      指定，不會抓錯相機。
#   2. 跟 RTSPVideoSource 用同一個專案自帶的 ffmpeg、同一套 pipe 讀取方式，
#      兩種來源的行為一致、比較好維護。
#
# 實測（uvc_smoke_test.py，2026-10-12）：Dashcam 的 MJPEG 1920x1080@30 可以
# 穩定 30fps；輸出給 Python 的畫面由 ffmpeg 先降成 1280x720@15——UI 本身每
# 67ms（約 15fps）才更新一次，送完整 1920x1080@30 只會白白增加 pipe 流量。
#
# 2026-10-12 輸出尺寸比較（UI 繪製時間 / 畫質 PSNR）後，從 960x540 改成 1280x720：
#   960x540   繪製約 20ms，視窗放大後明顯變糊
#   1280x720  繪製約 24ms，畫質明顯較好，CPU 全機只多約 2%（目前使用）
#   1920x1080 繪製約 33ms（吃掉 67ms tick 的一半），UI 有變卡風險
#
# 注意：這台測試 PC 上，用 python / pythonw 啟動時 Dashcam 可能完全不送畫面
# （python -X utf8 正常），懷疑是 Kaspersky 網路攝影機保護攔截。這種情況
# ffmpeg 會自己結束，這裡會回報錯誤，畫面顯示 Live View unavailable。
#
# 2026-10-13 新增 macOS（AVFoundation）支援，實測（mac_uvc_check.py）：
#   - AVFoundation 裝置列表裡 Dashcam 的名稱一樣是 "USB2 NCM"，但 index 會變
#     （同一台 Mac 兩次測試分別是 [1]、[0]），所以一律用名稱開啟，不用 index。
#   - AVFoundation 不能指定「拿 MJPEG」，macOS 會先解碼好再給 ffmpeg，
#     uyvy422 1920x1080 實測約 28.5fps；輸出一樣降成 1280x720@15 rgb24。
#   - 第一張畫面約 0.35~0.39 秒，連續 open / stop / reopen 正常。
#   - stop 的注意事項見 stop() 跟 _reader_loop() 的說明（停止時要繼續讀 stdout）。
#   - 需要 macOS 相機權限：開發時是 Terminal（或 VS Code 等啟動 python 的 App），
#     打包後是 .app 本身（Control_Panel_mac.spec 的 NSCameraUsageDescription）。

DEFAULT_UVC_DEVICE_NAME = 'USB2 NCM'   # Dashcam 的 UVC 裝置名稱（Windows DirectShow / macOS AVFoundation 都一樣）

# Dashcam 端的輸入格式（實測過正常的組合）
UVC_INPUT_WIDTH = 1920
UVC_INPUT_HEIGHT = 1080
UVC_INPUT_FPS = 30
UVC_MAC_PIXEL_FORMAT = 'uyvy422'       # macOS AVFoundation 實測可用的像素格式

UVC_FIRST_FRAME_TIMEOUT_SECONDS = 10   # start() 後這麼多秒還沒收到第一張畫面，就算失敗
# 2026-10-14 新增：收到第一張畫面之後，這麼多秒完全沒有新畫面就判定 stream
# stalled。快速拔插 Dashcam 時 FW 會中途 stop UVC，但 ffmpeg 不一定會結束，
# 原本只有「第一張畫面逾時」，畫面就一直停在最後一張、不會顯示 unavailable。
UVC_STALE_FRAME_TIMEOUT_SECONDS = 2
UVC_STDERR_KEEP_LINES = 20             # ffmpeg 錯誤訊息只保留最近這麼多行，不會無限累積
UVC_STOP_WAIT_SECONDS = 2              # stop 時等 ffmpeg 結束、等 thread 結束的秒數


def _uvc_log(message):
    """印出帶時間戳的 log，方便跟 ffmpeg / 系統 log 對時間。"""
    timestamp = time.strftime('%Y-%m-%d %H:%M:%S')
    print(f'[{timestamp}][video_source][uvc] {message}')


class FFmpegUVCVideoSource(VideoSource):
    """UVC -> DirectShow（Windows）/ AVFoundation（macOS）-> ffmpeg subprocess
    -> rawvideo RGB24 pipe -> 背景 thread -> latest frame only。

    對外介面跟 RTSPVideoSource 一樣（start/read_frame/stop/is_running/
    get_error/get_frame_size），LiveViewController 不需要知道差別。
    兩個平台共用同一個類別，只有 ffmpeg 的輸入參數不同（見 _build_input_args()）。
    其他平台（例如 Linux）不支援，start() 會直接回報錯誤。"""

    def __init__(self, device_name=DEFAULT_UVC_DEVICE_NAME, frame_width=1280, frame_height=720, fps=15):
        self._device_name = device_name
        self._frame_width = frame_width     # 輸出給 UI 的畫面寬
        self._frame_height = frame_height   # 輸出給 UI 的畫面高
        self._fps = fps                     # 輸出給 UI 的 fps

        self._process = None
        self._reader_thread = None
        self._stderr_thread = None
        self._stop_flag = None  # threading.Event()，每次 start() 都建立新的一個

        self._frame_lock = threading.Lock()
        self._latest_frame_bytes = None

        self._stderr_lock = threading.Lock()
        self._recent_stderr_lines = []

        self._error_message = None
        self._start_time = None
        self._first_frame_seconds = None   # 從 start() 到收到第一張畫面花了幾秒
        self._timeout_logged = False       # 第一張畫面逾時的 log 只印一次
        self._last_frame_time = None       # 最近一次收到新畫面的時間（time.time()），stalled 判斷用
        self._stall_logged = False         # stalled 的 log 只印一次

    def get_frame_size(self):
        """回傳 (width, height)，呼叫端要用這個尺寸還原 read_frame() 的 bytes。"""
        return self._frame_width, self._frame_height

    def get_first_frame_seconds(self):
        """回傳 start() 到第一張有效畫面的秒數，還沒收到畫面回傳 None（驗證/除錯用）。"""
        return self._first_frame_seconds

    # ---------- 啟動 ----------

    def _build_input_args(self):
        """依平台組出 ffmpeg 的輸入參數（兩個平台都用裝置名稱，不用 index）。
        回傳 None 表示目前平台不支援 UVC。"""
        if sys.platform == 'win32':
            # Windows DirectShow：-vcodec mjpeg 是選 Dashcam 的 MJPEG 輸出格式
            return [
                '-f', 'dshow',
                '-rtbufsize', '64M',
                '-vcodec', 'mjpeg',
                '-video_size', f'{UVC_INPUT_WIDTH}x{UVC_INPUT_HEIGHT}',
                '-framerate', str(UVC_INPUT_FPS),
                '-i', f'video={self._device_name}',
            ]
        if sys.platform == 'darwin':
            # macOS AVFoundation：-i "名稱:none" = 只要影像、不要聲音（不需要麥克風權限）
            return [
                '-f', 'avfoundation',
                '-framerate', str(UVC_INPUT_FPS),
                '-video_size', f'{UVC_INPUT_WIDTH}x{UVC_INPUT_HEIGHT}',
                '-pixel_format', UVC_MAC_PIXEL_FORMAT,
                '-i', f'{self._device_name}:none',
            ]
        return None

    def _build_command(self, ffmpeg_path, input_args):
        """組出 ffmpeg 指令。輸入：Dashcam 1920x1080@30（參數見 _build_input_args()）；
        輸出：先降 fps 再縮小（先降 fps 可以少縮放一半的畫面），rawvideo RGB24。"""
        video_filter = f'fps={self._fps},scale={self._frame_width}:{self._frame_height}:flags=fast_bilinear'
        command = [
            ffmpeg_path,
            '-hide_banner',
            '-nostats',                  # 不要每秒印進度，減少 stderr 輸出量
            '-loglevel', 'error',        # 只印錯誤（MJPEG 每次都會有一行無害的 pixel format 警告，不印）
        ]
        command += input_args
        command += [
            '-vf', video_filter,
            '-f', 'rawvideo',
            '-pix_fmt', 'rgb24',
            'pipe:1',
        ]
        return command

    def start(self):
        if self._process is not None:
            return  # 已經在跑，不重複啟動第二個 ffmpeg

        # 清掉上一輪的狀態
        self._error_message = None
        self._start_time = time.time()
        self._first_frame_seconds = None
        self._timeout_logged = False
        self._last_frame_time = None
        self._stall_logged = False
        with self._frame_lock:
            self._latest_frame_bytes = None
        with self._stderr_lock:
            self._recent_stderr_lines = []

        ffmpeg_path = find_ffmpeg_executable()
        if ffmpeg_path is None:
            self._error_message = '找不到可用的 ffmpeg'
            _uvc_log(self._error_message)
            return

        input_args = self._build_input_args()
        if input_args is None:
            self._error_message = f'這個平台（{sys.platform}）不支援 UVC，請改用 DA005_LIVE_VIEW_SOURCE=rtsp'
            _uvc_log(self._error_message)
            return

        command = self._build_command(ffmpeg_path, input_args)
        input_format = input_args[1]   # 'dshow' 或 'avfoundation'
        _uvc_log(f'啟動 ffmpeg（{input_format}）：裝置="{self._device_name}"，輸入 {UVC_INPUT_WIDTH}x{UVC_INPUT_HEIGHT}@{UVC_INPUT_FPS}，'
                 f'輸出 {self._frame_width}x{self._frame_height}@{self._fps} RGB24，ffmpeg={ffmpeg_path}')

        self._stop_flag = threading.Event()
        try:
            self._process = subprocess.Popen(
                command,
                stdin=subprocess.DEVNULL,     # ffmpeg 不需要輸入，避免它等鍵盤輸入
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                creationflags=_CREATE_NO_WINDOW,
            )
        except OSError as error:
            self._process = None
            self._error_message = f'啟動 ffmpeg 失敗: {error}'
            _uvc_log(self._error_message)
            return

        _uvc_log(f'ffmpeg 已啟動，pid={self._process.pid}')

        # 把這一輪的 process / stop_flag 傳給 thread，
        # 這樣舊的 thread 只會看自己那一輪的東西，不會影響 reopen 後的新一輪
        process = self._process
        stop_flag = self._stop_flag

        # stderr 一定要有人一直讀，不然 pipe 滿了 ffmpeg 會卡住
        self._stderr_thread = threading.Thread(target=self._stderr_loop, args=(process,), daemon=True)
        self._stderr_thread.start()

        self._reader_thread = threading.Thread(
            target=self._reader_loop, args=(process, stop_flag, self._stderr_thread), daemon=True,
        )
        self._reader_thread.start()

    # ---------- 背景 thread ----------

    def _stderr_loop(self, process):
        """持續讀 ffmpeg 的 stderr：印到 log，並只保留最近幾行給錯誤訊息用。"""
        try:
            for line in iter(process.stderr.readline, b''):
                text = line.decode('utf-8', errors='ignore').strip()
                if not text:
                    continue
                _uvc_log(f'ffmpeg: {text}')
                with self._stderr_lock:
                    self._recent_stderr_lines.append(text)
                    if len(self._recent_stderr_lines) > UVC_STDERR_KEEP_LINES:
                        self._recent_stderr_lines.pop(0)
        except (OSError, ValueError):
            pass  # stop() 關掉 pipe 時會走到這裡，屬於正常結束

    def _reader_loop(self, process, stop_flag, stderr_thread):
        """持續從 stdout 讀完整的一張畫面，只保留最新一張。

        2026-10-13：一定要讀到 EOF 才結束，stop 之後也繼續讀（只是畫面丟掉）。
        macOS 上 terminate() 是送 SIGTERM，ffmpeg 收到後只是設一個「要結束」的
        旗標，要回到主迴圈才會真的結束；如果這時候沒人讀 stdout，ffmpeg 會卡在
        寫 pipe（一張 rgb24 畫面約 2.7MB，pipe 緩衝只有約 64KB），永遠回不到主
        迴圈，只能等 timeout 後被 kill。實測一邊讀一邊送 SIGTERM，約 0.07 秒就
        正常結束。Windows 的 terminate() 是直接強制結束，讀到 EOF 的行為一樣。"""
        frame_byte_size = self._frame_width * self._frame_height * 3  # RGB24：每個像素 3 bytes
        try:
            while True:
                frame_bytes = _read_exact_from_pipe(process.stdout, frame_byte_size)
                if frame_bytes is None:
                    break  # EOF：ffmpeg 結束了（不完整的最後一張直接丟掉）
                if stop_flag.is_set():
                    continue  # 正在停止：繼續讀 stdout 讓 ffmpeg 能正常結束，畫面丟掉
                with self._frame_lock:
                    self._latest_frame_bytes = frame_bytes
                self._last_frame_time = time.time()
                if self._first_frame_seconds is None:
                    self._first_frame_seconds = time.time() - self._start_time
                    _uvc_log(f'收到第一張畫面，耗時 {self._first_frame_seconds:.2f} 秒')
        except (OSError, ValueError) as error:
            if not stop_flag.is_set():
                self._error_message = f'讀取 UVC 畫面時發生例外: {error}'

        if stop_flag.is_set():
            return  # 使用者主動 stop，不算錯誤

        # 不是使用者停止，卻讀不到畫面：ffmpeg 異常結束（找不到裝置、USB 拔除、被 kill...）
        # 等 ffmpeg 真的結束、stderr 最後幾行讀完，錯誤訊息才會完整
        exit_code = None
        try:
            exit_code = process.wait(timeout=UVC_STOP_WAIT_SECONDS)
        except subprocess.TimeoutExpired:
            pass
        stderr_thread.join(timeout=1)

        # 上面等待的這段時間，使用者可能已經按了 Retry（stop 再 start），
        # 這時候不能把舊一輪的錯誤寫到新一輪
        if stop_flag.is_set():
            return
        if not self._error_message:
            last_line = self._get_last_stderr_line()
            self._error_message = f'UVC 影像中斷（ffmpeg 結束，exit code={exit_code}）：{last_line}'
            if sys.platform == 'darwin' and self._is_camera_permission_error():
                self._error_message += '（macOS 相機權限被拒：系統設定 > 隱私權與安全性 > 相機，允許這個 App / Terminal 後重新啟動）'
        _uvc_log(self._error_message)

    def _is_camera_permission_error(self):
        """ffmpeg 最近的 stderr 看起來像不像 macOS 相機權限被拒"""
        with self._stderr_lock:
            recent_text = ' '.join(self._recent_stderr_lines).lower()
        for keyword in ('not authorized', 'denied', 'permission'):
            if keyword in recent_text:
                return True
        return False

    def _get_last_stderr_line(self):
        with self._stderr_lock:
            if self._recent_stderr_lines:
                return self._recent_stderr_lines[-1]
        return '沒有錯誤訊息'

    # ---------- 對外介面 ----------

    def read_frame(self):
        with self._frame_lock:
            frame_bytes = self._latest_frame_bytes
        if frame_bytes is None:
            return None
        return self._frame_width, self._frame_height, frame_bytes

    def stop(self):
        """停止順序：設 stop flag -> terminate（macOS 是 SIGTERM，讓 ffmpeg 正常
        收尾；這段期間 reader thread 會繼續把 stdout 讀完，見 _reader_loop()）
        -> 等結束 -> 逾時才 kill -> 等結束 -> join thread -> 關 pipe -> 清畫面。
        一定要等 ffmpeg 真的結束（攝影機才會釋放），之後 reopen 才不會開不起來。
        可以安全重複呼叫。"""
        if self._process is None:
            return

        if self._stop_flag is not None:
            self._stop_flag.set()

        process = self._process
        self._process = None
        stop_begin = time.time()

        _stop_process(process)

        if self._reader_thread is not None:
            self._reader_thread.join(timeout=UVC_STOP_WAIT_SECONDS)
        if self._stderr_thread is not None:
            self._stderr_thread.join(timeout=UVC_STOP_WAIT_SECONDS)
        self._reader_thread = None
        self._stderr_thread = None

        _close_pipe(process.stdout)
        _close_pipe(process.stderr)

        with self._frame_lock:
            self._latest_frame_bytes = None

        _uvc_log(f'已停止，pid={process.pid}，exit code={process.poll()}，耗時 {time.time() - stop_begin:.2f} 秒')

    def is_running(self):
        return self._process is not None

    def get_error(self):
        if self._error_message:
            return self._error_message

        # 第一張畫面逾時：ffmpeg 還活著，但一直沒有送出畫面
        if self._process is not None and self._first_frame_seconds is None and self._start_time is not None:
            if time.time() - self._start_time > UVC_FIRST_FRAME_TIMEOUT_SECONDS:
                message = f'{UVC_FIRST_FRAME_TIMEOUT_SECONDS} 秒內沒有收到 UVC 畫面（裝置="{self._device_name}"）'
                if not self._timeout_logged:
                    _uvc_log(f'{message}，ffmpeg 最後訊息：{self._get_last_stderr_line()}')
                    self._timeout_logged = True
                return message

        # 2026-10-14：收過畫面之後停止更新（stream stalled）：ffmpeg 還活著，
        # 但超過 UVC_STALE_FRAME_TIMEOUT_SECONDS 秒沒有新畫面
        if self._process is not None and self._last_frame_time is not None:
            seconds_since_last_frame = time.time() - self._last_frame_time
            if seconds_since_last_frame > UVC_STALE_FRAME_TIMEOUT_SECONDS:
                message = f'UVC 畫面停止更新（{UVC_STALE_FRAME_TIMEOUT_SECONDS} 秒沒有新畫面，裝置="{self._device_name}"）'
                if not self._stall_logged:
                    _uvc_log(f'{message}，距離上一張 {seconds_since_last_frame:.2f} 秒，'
                             f'ffmpeg 最後訊息：{self._get_last_stderr_line()}')
                    self._stall_logged = True
                return message

        return None


def _stop_process(process):
    """terminate -> wait(2s) -> 逾時就 kill -> wait。確認 process 真的結束。
    terminate() 在 Windows 是直接強制結束；macOS 是 SIGTERM（ffmpeg 會正常收尾，
    前提是 reader thread 還在讀 stdout），kill 只是逾時的最後手段。"""
    try:
        process.terminate()
        process.wait(timeout=UVC_STOP_WAIT_SECONDS)
        return
    except subprocess.TimeoutExpired:
        _uvc_log(f'ffmpeg 在 {UVC_STOP_WAIT_SECONDS} 秒內沒有結束，改用 kill，pid={process.pid}')
    except OSError:
        pass  # process 已經自己結束了

    try:
        process.kill()
        process.wait(timeout=UVC_STOP_WAIT_SECONDS)
    except subprocess.TimeoutExpired:
        _uvc_log(f'警告：kill 之後 ffmpeg 仍未結束，pid={process.pid}，攝影機可能還沒釋放')
    except OSError:
        pass


def _close_pipe(pipe):
    if pipe is None:
        return
    try:
        pipe.close()
    except OSError:
        pass
