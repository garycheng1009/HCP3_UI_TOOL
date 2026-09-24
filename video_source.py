"""
video_source.py — 可替換的 Live View 影像來源（VideoSource abstraction）

2026-09-23 新增。目的：讓 IQ Adjustment 這種需要即時看畫面的頁面，可以
呼叫一個共同介面（VideoSource）取得 Live View 畫面，不用知道背後實際上是
RTSP 還是 UVC。目前只有 RTSPVideoSource 是真的可以用的實作，
UVCVideoSource 先佔位（見下面的類別說明），之後 RD 提供 UVC 裝置存取方式
後，只要把 UVCVideoSource 的方法填好，呼叫端（live_view_controller.py／
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


class UVCVideoSource(VideoSource):
    """佔位用：之後 RD 提供 UVC 裝置存取方式後，把 start()/read_frame()/
    stop()/is_running()/get_error() 填成真正呼叫 UVC 裝置（例如
    cv2.VideoCapture(device_index) 或廠商 SDK）的邏輯即可——因為介面
    (VideoSource) 完全一樣，live_view_controller.py 跟
    screen_iq_adjustment.py 都不需要修改，只要把建立 VideoSource 的那一行
    從 RTSPVideoSource(...) 換成 UVCVideoSource(device_index) 就好。

    目前故意不實作內容（NotImplementedError），避免有人誤用一個看起來能動
    但其實什麼都沒做的假 UVC 來源。"""

    def __init__(self, device_index=0):
        self._device_index = device_index

    def start(self):
        raise NotImplementedError('UVCVideoSource 尚未實作，等 RD 提供 UVC 裝置存取方式後再補上')

    def read_frame(self):
        return None

    def stop(self):
        pass

    def is_running(self):
        return False

    def get_error(self):
        return 'UVC not implemented yet'
