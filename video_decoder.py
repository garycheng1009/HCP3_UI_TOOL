"""
video_decoder.py — 影片解碼 Decoder Abstraction

目的：把「怎麼把 DVR 影片網址變成一張一張可以顯示的畫面」這件事，從
screen_player.py 的 UI / 播放控制邏輯裡獨立出來。screen_player.py 只透過
這幾個方法跟 decoder 溝通，完全不知道背後是 OpenCV 還是 FFmpeg QSV/SW：

    decoder = create_decoder_with_fallback(url, seek_seconds=None)
    decoder.is_open                       # bool
    decoder.get_fps()                     # float
    has_frame, frame_rgb, media_seconds = decoder.read()
    decoder.seek(seconds)                 # 同一段串流內重新定位
    decoder.is_healthy()                  # False 代表中途出錯（串流中斷），不是正常播完
    decoder.release()                     # 一定要呼叫，確保 FFmpeg subprocess 不會變成 orphan process

三種 backend，依序 fallback（QSV 只是加速選項，不是工具的必要條件；
FFmpeg 找不到的話直接兩個都跳過，退到 OpenCV）：

    FFmpeg available?
        │
        ├─ 否 → OpenCVDecoder（永遠可用，不依賴任何外部程式）
        │
        └─ 是 → QSV probe（實際嘗試開啟）
                 ├─ 成功 → FFmpegQSVDecoder
                 └─ 失敗 → FFmpegSoftwareDecoder
                            ├─ 成功 → 用它
                            └─ 失敗 → OpenCVDecoder

實測結果（2026-08-12，同一支 42 秒/1238 frame 的 4K HEVC 真機影片）：
    OpenCV VideoCapture      ~19~20 FPS   CPU ~310%
    FFmpeg 純軟體解碼        ~34~36 FPS   CPU ~450%
    FFmpeg QSV 硬體解碼      ~87~258 FPS  CPU ~30~80%（依網路/本機檔案而定）

所有 decoder 的 read() 都回傳「已經縮小到 DISPLAY_SIZE 的 RGB ndarray」，
不是原始 4K 畫面：
    - FFmpeg 路線是在 GPU（QSV：vpp_qsv filter）或 FFmpeg 內部
      （純軟體：scale filter）就先縮小，再用 rawvideo pipe 傳出來，
      Python 這端收到的已經是 800x450，不會有完整 4K frame 搬進 Python
      記憶體再 resize 這件事。
    - OpenCVDecoder 沿用目前正式環境已經驗證有效的做法：cv2.resize()
      （比 PIL resize 快很多，2026-08-11 實測過）先縮小，再
      cv2.cvtColor() 轉成 RGB。
Player（screen_player.py 的 draw_frame()）拿到的畫面格式完全一致，不需要
再自己做任何 resize/色彩轉換，也不需要知道背後是哪一種 backend。

media_seconds 一律用「解碼順序 frame 序號 / fps」算，不是用 PTS/時間戳：
這台 DVR 的 timestamp 已經確認不可靠（這個 session 更早之前調查過 Event
影片播放對不上的問題就是這個原因）。FFmpeg 路線這裡固定加
-fps_mode passthrough，不依賴 PTS 做任何補幀/丟幀判斷——一開始沒加這個
選項時實測過，42 秒 1238 張的影片會被灌出 3237 張（FFmpeg 誤判 PTS 算出
的影片長度是 107.9 秒，比真正的 42 秒長很多，"-vsync cfr" 之類的補幀策略
會照這個錯誤的時間軸去補），加上 -fps_mode passthrough 之後張數才完全正確。
跟 OpenCVDecoder 原本用 CAP_PROP_POS_FRAMES（解碼順序索引，不是時間戳）
算 media_seconds 是同一個道理。

Decode Pacing 不是這支檔案的責任：QSV 實測比 30fps 快很多（87~258 FPS），
如果不節流會在極短時間內把 screen_player.py 的小 Queue 灌爆（PoC 階段
實測過，不節流時 Displayed Frames 只剩 42%）。這裡刻意把 read() 設計成
跟 cv2.VideoCapture.read() 一樣「單純、同步、呼叫一次吐一張畫面」的介面，
節流交給 screen_player.py 既有的 _decode_loop 節流機制處理（那邊已經有
「queue 補到一定張數就依照目標速度睡一下」的邏輯，這裡不重複做一份）。
"""

import os
import queue
import shutil
import subprocess
import sys
import threading
from collections import deque

import cv2
import numpy as np

DISPLAY_WIDTH = 800
DISPLAY_HEIGHT = 450
DISPLAY_SIZE = (DISPLAY_WIDTH, DISPLAY_HEIGHT)
_FRAME_BYTES = DISPLAY_WIDTH * DISPLAY_HEIGHT * 3  # rgb24，每個像素 3 bytes

# 這台 DVR 目前所有影片都是 30fps，FFmpeg 路線的 media_seconds 計算需要
# 一個 fps 基準值，這裡先固定用這個值（跟 OpenCVDecoder 遇到抓不到真實
# fps 時的預設值一致），不是每次都另外呼叫 ffprobe 探測，避免多花時間。
DEFAULT_SOURCE_FPS = 30

# Windows 下啟動 ffmpeg subprocess 不要跳出 cmd/Terminal 視窗——這是 GUI
# 工具，背景解碼不應該讓使用者看到一個黑底文字視窗。CREATE_NO_WINDOW 只有
# Windows 的 subprocess 模組才有這個常數，用 getattr 給預設值 0（等於不加
# 這個 flag），這樣萬一未來在別的平台跑也不會直接炸掉，不影響其他行為。
_CREATE_NO_WINDOW = getattr(subprocess, 'CREATE_NO_WINDOW', 0)

# QSV 是不是可用，只在程式這次執行期間探測一次（第一次真的嘗試開一個
# FFmpegQSVDecoder 才知道結果），成功/失敗都快取起來，避免每次開新影片
# 都重新花時間嘗試一次已經知道不可用的 backend。None 代表還沒探測過。
_qsv_available_cache = None


def _set_qsv_available_cache(value):
    global _qsv_available_cache
    _qsv_available_cache = value


def get_qsv_available_cache():
    """給診斷/測試用：目前快取的 QSV 可用狀態（None/True/False）"""
    return _qsv_available_cache


def reset_qsv_probe_cache():
    """給測試用：清掉快取，下次 create_decoder_with_fallback() 會重新真的
    嘗試一次 QSV。正式流程不會呼叫這個。"""
    _set_qsv_available_cache(None)


def force_qsv_unavailable_for_testing():
    """給測試用：強制讓後續的 create_decoder_with_fallback() 都跳過 QSV，
    直接走 FFmpeg 軟體解碼 fallback，不用真的把 QSV 環境弄壞才能測。
    正式流程不會呼叫這個。"""
    _set_qsv_available_cache(False)


def find_ffmpeg_executable():
    """正式打包後應該用工具自己資料夾裡的 runtime/ffmpeg/ffmpeg.exe，
    不依賴使用者自己裝的 FFmpeg 或系統 PATH（使用者不應該需要自己裝
    FFmpeg 或設定 PATH）。開發環境（還沒打包、還沒放 runtime/ffmpeg/）
    先 fallback 到系統 PATH 找得到的 ffmpeg，方便開發測試；找不到就回傳
    None，呼叫端會整個 FFmpeg 路線都跳過，直接用 OpenCV。

    這支函式沒有加底線前綴，是因為 api.py 的 Live View（另一個獨立的
    ffmpeg 用途，開的是 RTSP 串流視窗，不是這支檔案的解碼 pipe）也需要
    用一樣的邏輯找 ffmpeg.exe，兩邊各寫一份很容易之後改一邊忘記改另一邊，
    所以讓 api.py 直接 import 這支函式共用。"""
    if getattr(sys, 'frozen', False):
        app_dir = os.path.dirname(sys.executable)
    else:
        app_dir = os.path.dirname(os.path.abspath(__file__))

    # Windows 打包檔名是 ffmpeg.exe；macOS/Linux 是沒有 .exe 的 ffmpeg。
    ffmpeg_name = 'ffmpeg.exe' if sys.platform == 'win32' else 'ffmpeg'

    bundled_candidates = [
        os.path.join(app_dir, 'runtime', 'ffmpeg', ffmpeg_name),
    ]

    # PyInstaller macOS .app 常把可執行檔放在 Contents/MacOS，資源則可能放在
    # Contents/Resources；兩種位置都支援，開發環境不受影響。
    if sys.platform == 'darwin' and getattr(sys, 'frozen', False):
        contents_dir = os.path.dirname(app_dir)
        bundled_candidates.append(
            os.path.join(contents_dir, 'Resources', 'runtime', 'ffmpeg', ffmpeg_name)
        )

    for bundled_path in bundled_candidates:
        if os.path.isfile(bundled_path) and os.access(bundled_path, os.X_OK):
            return bundled_path

    system_ffmpeg = shutil.which('ffmpeg')
    if system_ffmpeg:
        expected = bundled_candidates[0]
        print(f'[video_decoder] 還沒有打包版 ffmpeg（預期路徑：{expected}），'
              f'開發環境暫時用系統 PATH 找到的：{system_ffmpeg}')
        return system_ffmpeg

    return None


RECENT_STDERR_LINES_MAX = 20  # 只保留最近這麼多行，足夠診斷用，不會無限累積佔記憶體


def _drain_stream(stream, recent_lines, recent_lines_lock):
    """背景執行緒：持續讀 ffmpeg 的 stderr。一定要有人讀，不然 pipe buffer
    滿了會讓 ffmpeg 卡住（stderr 訊息量不小，尤其是進度輸出）——但「持續讀」
    不等於「全部丟掉」，讀到的每一行都存進 recent_lines（bounded deque，
    滿了自動丟最舊的一行），這樣真的發生 Stream Error/串流中斷時，還能
    拿最近幾行 ffmpeg 自己回報的錯誤原因來看，不是只知道「壞了」但不知道
    為什麼。加鎖是因為這個 deque 同時有這支背景執行緒在寫、呼叫端的執行緒
    在讀（get_recent_stderr_text() 的 join()）——deque 單一 append/pop 雖然
    是原子操作，但讀取端在迭代整個 deque 的當下如果被另一個執行緒
    append() 打斷，理論上可能撞見 RuntimeError: deque mutated during
    iteration，用鎖把「寫一行」跟「讀出全部」都變成互斥，才不會有這個風險。"""
    try:
        while True:
            line = stream.readline()
            if not line:
                break
            decoded_line = line.decode('utf-8', errors='ignore').rstrip()
            with recent_lines_lock:
                recent_lines.append(decoded_line)
    except (OSError, ValueError):
        pass


def _read_exact(stream, num_bytes):
    """從 pipe 讀滿指定 bytes 數才回傳；Windows 的 pipe read() 常常一次
    讀不滿一張畫面，要自己 loop 累積。讀到 EOF（read 回傳空 bytes）就
    回傳 None。"""
    buffer = bytearray()
    while len(buffer) < num_bytes:
        chunk = stream.read(num_bytes - len(buffer))
        if not chunk:
            return None
        buffer.extend(chunk)
    return bytes(buffer)


OPEN_PROBE_TIMEOUT_SECONDS = 8  # 開啟時讀第一張畫面探測用，網路/解碼掛掉時最多等這麼久就放棄，改試下一種 backend


def _read_exact_with_timeout(stream, num_bytes, timeout_seconds):
    """跟 _read_exact() 一樣，但最多等 timeout_seconds 秒，逾時就放棄回傳
    None（不代表 EOF，只是「這次沒等到」）。實測過（2026-08-12）：網路
    卡住時 ffmpeg 的 rawvideo pipe 可能完全沒有資料進來，_read_exact()
    會永遠卡住，這裡開啟/探測階段還沒有任何地方持有這個 decoder 的參照，
    卡住就是真的卡死整個程式，沒有後續補救機會，一定要有逾時才能讓
    create_decoder_with_fallback() 的 fallback 鏈繼續往下試。播放中的
    read()（_decode_loop 那邊）不用這個逾時機制：那已經在專屬的 decode
    thread 上跑，不會卡住 UI 主執行緒，使用者離開畫面時 release() 會
    terminate 底層 process，逼 read() 結束，不會真的無法回收。"""
    result_queue = queue.Queue(maxsize=1)

    def read_worker():
        try:
            result_queue.put(_read_exact(stream, num_bytes))
        except (OSError, ValueError):
            result_queue.put(None)

    worker_thread = threading.Thread(target=read_worker, daemon=True)
    worker_thread.start()
    try:
        return result_queue.get(timeout=timeout_seconds)
    except queue.Empty:
        return None


def _open_video_capture_with_timeout(url, timeout_seconds):
    """cv2.VideoCapture(url) 加上逾時保護，理由跟 _read_exact_with_timeout()
    一樣：這是 fallback 鏈的最後一站，如果也卡住就真的沒有下一步了。逾時
    回傳 None；如果逾時後 worker 執行緒才真的把 VideoCapture 開出來，這裡
    會直接呼叫 release() 把它關掉，不留下沒人管的物件。"""
    result_queue = queue.Queue(maxsize=1)

    def open_worker():
        try:
            result_queue.put(cv2.VideoCapture(url))
        except Exception:
            result_queue.put(None)

    worker_thread = threading.Thread(target=open_worker, daemon=True)
    worker_thread.start()
    try:
        return result_queue.get(timeout=timeout_seconds)
    except queue.Empty:
        # 逾時放棄了，但 worker 執行緒可能晚一點還是會真的把 VideoCapture
        # 開出來，交給 _release_late_capture() 在背景等它、關掉它，不要
        # 留下沒人管、卻還佔著網路連線的物件。
        threading.Thread(target=_release_late_capture, args=(result_queue,), daemon=True).start()
        return None


def _release_late_capture(result_queue):
    try:
        late_capture = result_queue.get(timeout=120)
        if late_capture is not None:
            late_capture.release()
    except queue.Empty:
        pass


class OpenCVDecoder:
    """最終 fallback，永遠可用（不依賴 FFmpeg 有沒有裝、QSV 有沒有支援）。
    這是目前正式環境原本唯一的路線，邏輯完全照搬過來，只是搬進這個
    class 裡讓 Player 可以用統一介面呼叫。"""

    backend_name = 'OpenCV'

    def __init__(self):
        self.is_open = False
        self._capture = None
        self._fps = DEFAULT_SOURCE_FPS
        self._frame_index = 0
        self._url = None

    def open(self, url, seek_seconds=None):
        """cv2.VideoCapture(url) 本身是會卡住的呼叫，網路斷線/DVR 沒回應時
        實測過可能卡住很久甚至不會回來（跟 FFmpeg 探測第一張畫面會卡住是
        同一種風險）。跟 _FFmpegPipeDecoderBase 的探測讀取一樣，這裡也包一層
        逾時保護，逾時就當開啟失敗回傳 False，不要讓整個 fallback 鏈卡死。"""
        self._url = url
        self._capture = _open_video_capture_with_timeout(url, OPEN_PROBE_TIMEOUT_SECONDS)
        if self._capture is None or not self._capture.isOpened():
            self._capture = None
            self.is_open = False
            return False

        fps = self._capture.get(cv2.CAP_PROP_FPS)
        self._fps = fps if fps and fps > 0 else DEFAULT_SOURCE_FPS
        self._frame_index = 0

        if seek_seconds is not None:
            self.seek(seek_seconds)

        self.is_open = True
        return True

    def get_fps(self):
        return self._fps

    def read(self):
        has_frame, frame_bgr = self._capture.read()
        if not has_frame:
            return False, None, 0.0

        self._frame_index += 1
        # 實測過（2026-08-11）：cv2.resize() 先把 4K 縮小，比先轉 RGB 再用
        # PIL resize 快很多（PIL 要處理整張 4K 資料，平均一張 150ms 以上）。
        resized = cv2.resize(frame_bgr, DISPLAY_SIZE)
        frame_rgb = cv2.cvtColor(resized, cv2.COLOR_BGR2RGB)
        media_seconds = self._frame_index / self._fps
        return True, frame_rgb, media_seconds

    def seek(self, seconds):
        target_frame_index = int(seconds * self._fps)
        self._capture.set(cv2.CAP_PROP_POS_FRAMES, target_frame_index)
        self._frame_index = target_frame_index

    def release(self):
        if self._capture is not None:
            self._capture.release()
            self._capture = None
        self.is_open = False

    def is_healthy(self):
        return True  # 沒有 subprocess 可以檢查存活，跟原本行為一致，永遠當作正常


class _FFmpegPipeDecoderBase:
    """FFmpeg subprocess + rawvideo pipe 共用的邏輯，QSV 跟純軟體解碼只是
    _build_command() 組出來的 command line 不同，開啟/讀取/seek/釋放完全
    共用同一套實作，不重複寫兩次。"""

    backend_name = 'FFmpeg (base)'

    def __init__(self, ffmpeg_path):
        self.is_open = False
        self._ffmpeg_path = ffmpeg_path
        self._process = None
        self._stderr_thread = None
        self._recent_stderr_lines = deque(maxlen=RECENT_STDERR_LINES_MAX)
        self._recent_stderr_lock = threading.Lock()
        self._fps = DEFAULT_SOURCE_FPS
        self._frame_index = 0
        self._url = None
        self._pending_frame = None
        self._had_error = False

    def get_recent_stderr_text(self):
        """給 Stream Error / 開啟失敗診斷用：最近幾行 ffmpeg 自己印出來的
        stderr（通常是實際的錯誤原因，例如網路中斷、找不到 codec）。
        加鎖理由見 _drain_stream() 的說明。"""
        with self._recent_stderr_lock:
            lines_snapshot = list(self._recent_stderr_lines)
        return '\n'.join(lines_snapshot) if lines_snapshot else '(no stderr output captured)'

    def _build_command(self, url, seek_seconds):
        raise NotImplementedError

    def open(self, url, seek_seconds=None):
        self._url = url
        self._fps = DEFAULT_SOURCE_FPS
        command = self._build_command(url, seek_seconds)

        try:
            self._process = subprocess.Popen(
                command, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                stdin=subprocess.DEVNULL, creationflags=_CREATE_NO_WINDOW)
        except OSError as error:
            print(f'[{self.backend_name}] 啟動 ffmpeg 失敗: {error}')
            self._process = None
            self.is_open = False
            return False

        self._stderr_thread = threading.Thread(
            target=_drain_stream,
            args=(self._process.stderr, self._recent_stderr_lines, self._recent_stderr_lock),
            daemon=True)
        self._stderr_thread.start()

        self._frame_index = int(seek_seconds * self._fps) if seek_seconds else 0
        self._had_error = False

        # 實際讀一張畫面才能確認這個 backend 真的能解碼這支影片（例如這台
        # 機器不支援 QSV 的話，這裡就會失敗），讀不到就當開啟失敗，讓
        # create_decoder_with_fallback() 改試下一種 backend。有逾時保護
        # （見 _read_exact_with_timeout() 的說明），網路卡住時不會無限期卡死。
        probe_frame = _read_exact_with_timeout(self._process.stdout, _FRAME_BYTES, OPEN_PROBE_TIMEOUT_SECONDS)
        if probe_frame is None:
            self._mark_process_error_if_any()
            print(f'[{self.backend_name}] 開場探測讀不到畫面，ffmpeg 最近的 stderr:\n{self.get_recent_stderr_text()}')
            self.release()
            return False

        self._pending_frame = probe_frame
        self.is_open = True
        return True

    def get_fps(self):
        return self._fps

    def read(self):
        if self._process is None:
            return False, None, 0.0

        if self._pending_frame is not None:
            frame_bytes = self._pending_frame
            self._pending_frame = None
        else:
            frame_bytes = _read_exact(self._process.stdout, _FRAME_BYTES)

        if frame_bytes is None:
            self._mark_process_error_if_any()
            return False, None, 0.0

        self._frame_index += 1
        frame_rgb = np.frombuffer(frame_bytes, dtype=np.uint8).reshape(
            (DISPLAY_HEIGHT, DISPLAY_WIDTH, 3))
        media_seconds = self._frame_index / self._fps
        return True, frame_rgb, media_seconds

    def _mark_process_error_if_any(self):
        """讀到 EOF 時檢查 ffmpeg 的 exit code：正常播完（讀到影片真的
        結尾）returncode 應該是 0；串流中斷/檔案有問題通常會是非 0。
        這裡只負責記錄，是不是「正常播完」還是「串流中斷」的完整判斷
        （例如比對 decode 到的時間跟 metadata duration）留給
        screen_player.py 自己的邏輯做，跟目前 OpenCV 那邊的做法一致。
        非 0 exit code 的話，把最近的 stderr 印出來——不是只知道「壞了」，
        要看得到 ffmpeg 自己回報的實際原因，才有辦法診斷 Stream Error。"""
        if self._process is None:
            return
        self._process.poll()
        if self._process.returncode is not None and self._process.returncode != 0:
            self._had_error = True
            print(f'[{self.backend_name}] ffmpeg 非正常結束（exit code={self._process.returncode}），'
                  f'最近的 stderr:\n{self.get_recent_stderr_text()}')

    def seek(self, seconds):
        """FFmpeg subprocess 沒有「在原地跳到某個時間點」這種操作，只能
        關掉目前這個 process、用 -ss 重新開一個新的——跟 OpenCVDecoder的
        cap.set() 比起來重一點，但正確性沒問題，seek 本身不是這次要優化
        的東西。"""
        self.release()
        self.open(self._url, seek_seconds=seconds)

    def release(self):
        if self._process is not None:
            self._process.terminate()
            try:
                self._process.wait(timeout=2)
            except subprocess.TimeoutExpired:
                self._process.kill()
                self._process.wait(timeout=2)
            self._process = None
        self._pending_frame = None
        self.is_open = False

    def is_healthy(self):
        return not self._had_error


class FFmpegQSVDecoder(_FFmpegPipeDecoderBase):
    """Intel Quick Sync Video 硬體解碼。實測（2026-08-12）87~258 FPS，
    遠超過這台 DVR 影片的 30fps 需求，CPU 使用率也遠低於純軟體/OpenCV
    路線。只是加速選項，不可用時 create_decoder_with_fallback() 會自動
    跳過，改用 FFmpegSoftwareDecoder。"""

    backend_name = 'FFmpeg QSV (Intel Quick Sync 硬體解碼)'

    def _build_command(self, url, seek_seconds):
        command = [self._ffmpeg_path, '-hide_banner', '-loglevel', 'error']
        if seek_seconds:
            command += ['-ss', str(seek_seconds)]
        command += [
            '-hwaccel', 'qsv', '-hwaccel_output_format', 'qsv',
            '-c:v', 'hevc_qsv', '-i', url,
            # 在 GPU 端直接把 4K 縮小成顯示尺寸再搬回系統記憶體，不要先把
            # 完整 4K frame 搬回 CPU 才 resize（PoC 階段驗證過，vpp_qsv 這條
            # filter 才吃得動 QSV 硬體解碼出來的 GPU frame，plain scale
            # filter 不行）。
            '-vf', f'vpp_qsv=w={DISPLAY_WIDTH}:h={DISPLAY_HEIGHT},hwdownload,format=nv12',
            '-pix_fmt', 'rgb24',
            # 這台 DVR 的 timestamp 不可靠，不要用任何依賴 PTS 補幀/丟幀的
            # 策略（例如 -vsync cfr），一律照解碼順序原樣輸出。
            '-fps_mode', 'passthrough',
            '-f', 'rawvideo', '-'
        ]
        return command


class FFmpegSoftwareDecoder(_FFmpegPipeDecoderBase):
    """FFmpeg 純軟體解碼（沒有 QSV 時的 fallback）。實測 34~36 FPS，
    比 OpenCV 內建的舊版 FFmpeg（19~20 FPS）快，但比 QSV 慢很多。"""

    backend_name = 'FFmpeg Software Decode'

    def _build_command(self, url, seek_seconds):
        command = [self._ffmpeg_path, '-hide_banner', '-loglevel', 'error']
        if seek_seconds:
            command += ['-ss', str(seek_seconds)]
        command += [
            '-c:v', 'hevc', '-i', url,
            '-vf', f'scale={DISPLAY_WIDTH}:{DISPLAY_HEIGHT}',
            '-pix_fmt', 'rgb24',
            '-fps_mode', 'passthrough',
            '-f', 'rawvideo', '-'
        ]
        return command


def _try_open(decoder, url, seek_seconds, label):
    try:
        opened = decoder.open(url, seek_seconds)
    except Exception as error:
        print(f'[video_decoder] {label} 開啟時發生例外: {error}')
        opened = False
    if not opened:
        decoder.release()
        print(f'[video_decoder] {label} 無法開啟這支影片，嘗試下一種 backend')
    return opened


def create_decoder_with_fallback(url, seek_seconds=None, is_stale=None):
    """依序嘗試 FFmpeg QSV -> FFmpeg 軟體解碼 -> OpenCV，回傳第一個真的
    能成功開啟這個 url 的 decoder。QSV 只是加速選項，不是必要條件；
    OpenCV 永遠可用，是最終保底，三種都失敗基本不會發生（那代表這個
    url 本身就打不開，回傳的 OpenCV decoder 的 is_open 會是 False，
    呼叫端跟原本一樣檢查這個旗標即可）。

    is_stale：呼叫端可傳入一個「目前這次要求是否已經過期」的檢查函式
    （screen_player.py 傳的是 lambda: request_token != current_request_
    token）。2026-08-20 修 Player 快速連點 Next/Previous 造成 DVR 503：
    每個 backend 嘗試「之前」都會先檢查一次，一旦過期就立刻不再嘗試下一個
    backend，回傳一個沒開啟的 OpenCVDecoder（呼叫端既有的 request_token
    比對邏輯本來就會正確丟棄，這裡只是不要再浪費時間對 DVR 開更多連線）。
    這一輪只加「提早放棄、不再嘗試下一個 backend」，不會主動中止已經送出去
    的 subprocess/連線（那些仍會照原本的方式跑完/逾時），也完全不影響
    「沒有過期」時的正常流程——is_stale 沒給或回傳 False，行為跟改之前
    完全一樣。"""
    def request_is_stale():
        return is_stale is not None and is_stale()

    if request_is_stale():
        return OpenCVDecoder()

    ffmpeg_path = find_ffmpeg_executable()

    if ffmpeg_path is not None and get_qsv_available_cache() is not False:
        qsv_decoder = FFmpegQSVDecoder(ffmpeg_path)
        if _try_open(qsv_decoder, url, seek_seconds, qsv_decoder.backend_name):
            _set_qsv_available_cache(True)
            return qsv_decoder
        _set_qsv_available_cache(False)

    if request_is_stale():
        return OpenCVDecoder()

    if ffmpeg_path is not None:
        sw_decoder = FFmpegSoftwareDecoder(ffmpeg_path)
        if _try_open(sw_decoder, url, seek_seconds, sw_decoder.backend_name):
            return sw_decoder
    else:
        print('[video_decoder] 找不到可用的 ffmpeg，直接使用 OpenCV')

    if request_is_stale():
        return OpenCVDecoder()

    opencv_decoder = OpenCVDecoder()
    _try_open(opencv_decoder, url, seek_seconds, opencv_decoder.backend_name)
    return opencv_decoder
