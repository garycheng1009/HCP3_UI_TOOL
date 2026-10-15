"""
live_view_controller.py — Live View 的 UI-無關 orchestration 層

2026-09-23 新增。介於 UI（screen_iq_adjustment.py）跟 VideoSource
（video_source.py）之間：UI 只呼叫 start_live_view()/stop_live_view()/
retry_live_view()，然後用自己的 Tk after() 迴圈呼叫 get_status()/
get_latest_frame() 拿最新狀態/畫面——這支檔案完全不 import tkinter，
也不知道呼叫端是哪個畫面，純粹管理「一個 VideoSource 的生命週期＋把它的
原始狀態換算成 UI 看得懂的 idle/connecting/connected/error」。

哪一種 VideoSource（RTSP 還是 UVC）是建構時從外面注入進來的（dependency
injection），這支檔案完全不 import video_source.py 裡任何具體實作類別
（RTSPVideoSource/FFmpegUVCVideoSource），只依賴 video_source.VideoSource 那五個
方法的介面——之後要換成 UVC，只要呼叫端建構 LiveViewController 時傳一個
FFmpegUVCVideoSource 進來，這支檔案不用改一行。

2026-10-13 改成「整個 App 共用一個 LiveViewController」：首頁（Recording）
跟 IQ Adjustment 都用同一個實例、同一個 VideoSource，同一台 UVC Camera
物理上只會有一個 ffmpeg 在開。為了讓兩個頁面不會互相干擾，加了 owner：

    start_live_view(owner)  -- owner 是呼叫端自己取的名字（'home' /
                               'iq_adjustment'）。別的 owner 正在用的話，
                               先完整停掉舊的 stream，再幫新 owner 啟動。
                               同一個 owner 重複呼叫不會重複建立 stream。
    stop_live_view(owner)   -- 只有「目前的 owner」才停得掉，避免首頁離開時
                               把 IQ 正在用的 stream 關掉。
    force_stop()            -- App 關閉時用，不管 owner 是誰都停掉。

另外 VideoSource.start()/stop() 不在 UI thread 執行：FFmpegUVCVideoSource
的 stop() 正常約 0.1~0.2 秒，但 ffmpeg 卡住時要等 terminate/kill 的
timeout（最多好幾秒）。所以這裡開一條背景 worker thread 專門做
start/stop，UI 只是「登記想要的狀態」（_wanted_owner）然後馬上返回，
worker 再照順序把實際狀態調整成想要的狀態。使用者快速來回切頁時，
中間沒來得及做的 start/stop 會直接合併掉，不會一個一個排隊執行。

2026-10-14 新增 suspend()/resume()（快速拔插 Dashcam 後 Live View 卡住）：
Dashcam 斷線（DISCONNECTED）跟恢復中（RECOVERING）由全域連線 watcher
呼叫 suspend()——停掉 stream、清掉 owner、之後的 start/retry 一律忽略，
get_status() 回傳 STATUS_UNAVAILABLE。回到 CONNECTED 才 resume()，resume
不會自動重開 Live View，使用者要自己再開一次。Home／IQ 不用各自處理
reconnect，只要看 get_status() 就好。"""

import threading

STATUS_IDLE = 'idle'
STATUS_CONNECTING = 'connecting'
STATUS_CONNECTED = 'connected'
STATUS_ERROR = 'error'
STATUS_UNAVAILABLE = 'unavailable'  # Dashcam 斷線／恢復中，controller 被 suspend

FORCE_STOP_WAIT_SECONDS = 10  # App 關閉時最多等 worker 手上正在做的 start/stop 這麼久


class LiveViewController:
    def __init__(self, video_source, source_label='RTSP'):
        """video_source：符合 video_source.VideoSource 介面的實例（例如
        RTSPVideoSource(...)），由呼叫端決定要用哪一種來源。
        source_label：純粹給 UI 顯示用的文字（例如 "Connected - RTSP"
        裡的 "RTSP"），不影響任何邏輯。"""
        self._video_source = video_source
        self._source_label = source_label
        self._has_received_frame = False
        # VideoSource.start()/stop() 理論上不該丟例外（RTSPVideoSource 自己
        # 內部已經 try/except 過），但這裡還是包一層安全網——例如
        # 某個 VideoSource 實作的 start() 直接 raise 例外的情況，
        # 或未來任何 VideoSource 實作忘記自己處理例外，都不能讓 worker
        # thread 整個死掉，只能反映成 error 狀態。
        self._start_exception_message = None

        # ---- UI thread 寫、worker thread 讀：「想要」的狀態 ----
        self._state_lock = threading.Lock()   # 保護下面三個變數
        self._wanted_owner = None             # 現在「應該」是誰在用 Live View，None = 應該停止
        self._restart_requested = False       # True = 就算同一個 owner 也要先 stop 再 start（重新開始/Retry）
        self._worker_busy = False             # worker 正在做 start/stop 中

        # ---- 只有 worker thread（或 force_stop()）會改：「實際」的狀態 ----
        self._running_owner = None            # VideoSource 目前實際是為誰開的，None = 沒有在跑

        # worker 做 start/stop 時要拿這把鎖；force_stop() 也要拿，確保兩邊不會同時操作 VideoSource
        self._operation_lock = threading.Lock()
        self._closed = False                  # force_stop() 之後就不再接受 start（永久，App 關閉用）
        self._suspended = False               # suspend() 之後暫時不接受 start，resume() 解除（斷線用）

        self._wake_event = threading.Event()  # UI 改了想要的狀態後 set()，叫醒 worker
        self._worker_thread = threading.Thread(target=self._worker_loop, daemon=True)
        self._worker_thread.start()

    # ==========================================
    # UI thread 呼叫：登記想要的狀態，馬上返回
    # ==========================================

    def start_live_view(self, owner):
        """登記 owner 要開始 Live View，不會卡住 UI。
        - 同一個 owner 已經在用：什麼都不做（不重複建立 stream）
        - 原本沒人用，或是別的 owner 在用：worker 會先停掉舊的，再重新啟動"""
        with self._state_lock:
            if self._closed:
                return
            if self._suspended:
                print(f'[live_view_controller] start 忽略：owner={owner}，Dashcam 斷線或恢復中（suspended）')
                return
            if self._wanted_owner == owner:
                return
            print(f'[live_view_controller] start 要求：owner={owner}（原本={self._wanted_owner}）')
            self._wanted_owner = owner
            # 每次「新的開始」都要拿到一條全新的 stream——就算 worker 還沒來得及
            # 處理上一個 owner 的 stop，也不能沿用舊 stream（可能已經 error）
            self._restart_requested = True
            self._has_received_frame = False
            self._start_exception_message = None
        self._wake_event.set()

    def stop_live_view(self, owner):
        """只有目前的 owner 才能停止；其他 owner 呼叫會被忽略。可以安全重複呼叫。"""
        with self._state_lock:
            if self._wanted_owner != owner:
                return
            print(f'[live_view_controller] stop 要求：owner={owner}')
            self._wanted_owner = None
            self._has_received_frame = False
        self._wake_event.set()

    def retry_live_view(self, owner):
        """使用者按「Retry Live View」時呼叫：真的完整停止、再重新開始，
        不是只是清掉錯誤訊息假裝重連——真的重新建立一次連線。"""
        with self._state_lock:
            if self._closed:
                return
            if self._suspended:
                print(f'[live_view_controller] retry 忽略：owner={owner}，Dashcam 斷線或恢復中（suspended）')
                return
            print(f'[live_view_controller] retry 要求：owner={owner}')
            self._wanted_owner = owner
            self._restart_requested = True
            self._has_received_frame = False
            self._start_exception_message = None
        self._wake_event.set()

    def suspend(self):
        """Dashcam 斷線時呼叫（全域連線 watcher）：不管 owner 是誰都停掉
        stream、清掉 owner，之後 start/retry 一律忽略，直到 resume()。
        stop 本身交給 worker 在背景做，不會卡 UI。可以安全重複呼叫。"""
        with self._state_lock:
            if self._closed:
                return
            print(f'[live_view_controller] suspend：停止 Live View（原 owner={self._wanted_owner}），'
                  f'恢復前不接受 start')
            self._suspended = True
            self._wanted_owner = None
            self._restart_requested = False
            self._has_received_frame = False
            self._start_exception_message = None
        self._wake_event.set()

    def resume(self):
        """Dashcam 回到 CONNECTED 時呼叫：只是允許之後的 start，不會自動
        重開 Live View（使用者要自己再開一次）。"""
        with self._state_lock:
            if not self._suspended:
                return
            print('[live_view_controller] resume：允許重新開啟 Live View（不自動重開）')
            self._suspended = False

    def is_suspended(self):
        with self._state_lock:
            return self._suspended

    def force_stop(self):
        """App 關閉時呼叫：不管 owner 是誰都停掉，而且會等到真的停完才返回
        （這時候視窗已經 withdraw，卡住一下沒關係，重點是 ffmpeg 一定要結束）。"""
        with self._state_lock:
            self._closed = True
            self._wanted_owner = None
        self._wake_event.set()

        # 等 worker 手上正在做的 start/stop 做完，然後由這裡直接 stop
        got_lock = self._operation_lock.acquire(timeout=FORCE_STOP_WAIT_SECONDS)
        if not got_lock:
            print('[live_view_controller] force_stop：等不到 worker 完成，直接 stop VideoSource')
        try:
            self._safe_stop_source()
            self._running_owner = None
        finally:
            if got_lock:
                self._operation_lock.release()

    # ==========================================
    # UI thread 呼叫：查詢狀態 / 畫面（非阻塞）
    # ==========================================

    def is_requested_by(self, owner):
        """owner 目前是不是「想要」Live View 的那一個（不管實際連上了沒）。"""
        with self._state_lock:
            return self._wanted_owner == owner

    def get_latest_frame(self, owner):
        """回傳 (width, height, rgb24_bytes) 或 None（還沒有畫面）。
        stream 不是為這個 owner 開的（例如交接到一半），一律回傳 None，
        不會讓 A 頁面畫出 B 頁面的畫面。"""
        if not self._is_ready_for(owner):
            return None
        return self._video_source.read_frame()

    def get_status(self, owner):
        """回傳 (status, error_message, source_label)：
        - status 是 STATUS_IDLE/CONNECTING/CONNECTED/ERROR 其中一個
        - error_message 只有 status=='error' 時才有內容，其他情況是 None
        - source_label 是建構時傳入的來源名稱（給 UI 顯示 "Connected -
          RTSP" 這種文字用）

        判斷順序：
        0. 被 suspend（Dashcam 斷線／恢復中）-> unavailable
        1. Live View 不是這個 owner 要的 -> idle
        2. worker 還在處理（停舊的 / 啟動新的）-> connecting
        3. VideoSource.start() 直接丟例外，或 VideoSource 回報錯誤 -> error
           （錯誤是終局狀態，值得比「行程還活著」更優先顯示）
        4. 曾經成功讀到過一張畫面 -> connected（就算這一瞬間 read_frame()
           剛好還沒有新畫面，也不會跳回 connecting，畫面不會閃爍）
        5. 其他 -> connecting"""
        with self._state_lock:
            if self._suspended:
                return STATUS_UNAVAILABLE, None, self._source_label
            if self._wanted_owner != owner:
                return STATUS_IDLE, None, self._source_label

        if not self._is_ready_for(owner):
            return STATUS_CONNECTING, None, self._source_label

        if self._start_exception_message:
            return STATUS_ERROR, self._start_exception_message, self._source_label

        error_message = self._video_source.get_error()
        if error_message:
            return STATUS_ERROR, error_message, self._source_label

        frame = self._video_source.read_frame()
        if frame is not None:
            self._has_received_frame = True

        if self._has_received_frame:
            return STATUS_CONNECTED, None, self._source_label

        return STATUS_CONNECTING, None, self._source_label

    def is_running(self):
        return self._video_source.is_running()

    def _is_ready_for(self, owner):
        """stream 已經真的是為這個 owner 開好、worker 沒有待處理的工作。"""
        with self._state_lock:
            if self._wanted_owner != owner:
                return False
            if self._worker_busy or self._restart_requested:
                return False
            return self._running_owner == owner

    # ==========================================
    # worker thread：把實際狀態調整成想要的狀態
    # ==========================================

    def _worker_loop(self):
        while True:
            self._wake_event.wait()
            self._wake_event.clear()
            # 這次處理的期間 UI 如果又改了想要的狀態，_wake_event 會再被 set，
            # 下一圈就會再處理一次，不會漏掉
            with self._operation_lock:
                self._apply_wanted_state()

    def _apply_wanted_state(self):
        with self._state_lock:
            wanted_owner = self._wanted_owner
            restart = self._restart_requested
            self._restart_requested = False
            self._worker_busy = True

        try:
            # 第一步：需要的話先停掉目前的 stream（owner 換人、要求重新開始、或已經不需要了）
            need_stop = self._running_owner is not None and (restart or self._running_owner != wanted_owner)
            if need_stop:
                print(f'[live_view_controller] worker：停止 stream（原 owner={self._running_owner}）')
                self._safe_stop_source()
                self._running_owner = None

            # 第二步：有人要用、而且目前沒在跑，就啟動
            if wanted_owner is not None and self._running_owner is None:
                print(f'[live_view_controller] worker：啟動 stream（owner={wanted_owner}，來源={self._source_label}）')
                self._safe_start_source()
                self._running_owner = wanted_owner
        finally:
            with self._state_lock:
                self._worker_busy = False

    def _safe_start_source(self):
        try:
            self._video_source.start()
        except Exception as error:
            print(f'[live_view_controller] VideoSource.start() 發生例外: {error}')
            self._start_exception_message = str(error)

    def _safe_stop_source(self):
        try:
            self._video_source.stop()
        except Exception as error:
            print(f'[live_view_controller] VideoSource.stop() 發生例外: {error}')
