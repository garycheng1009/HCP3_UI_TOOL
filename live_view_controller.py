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
（RTSPVideoSource/UVCVideoSource），只依賴 video_source.VideoSource 那五個
方法的介面——之後要換成 UVC，只要呼叫端建構 LiveViewController 時傳一個
UVCVideoSource 進來，這支檔案不用改一行。

實際的背景 thread／pipe 讀取都在 VideoSource 實作內部（例如
RTSPVideoSource），這裡的 start_live_view()/stop_live_view() 本身很快就
會回傳（VideoSource.start() 只是啟動 subprocess/thread，不會阻塞等連線
真正建立），呼叫端可以放心直接在 Tk 的 callback/command 裡呼叫，不需要
自己再包一層 thread。"""

STATUS_IDLE = 'idle'
STATUS_CONNECTING = 'connecting'
STATUS_CONNECTED = 'connected'
STATUS_ERROR = 'error'


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
        # UVCVideoSource 還沒實作、直接 raise NotImplementedError 的情況，
        # 或未來任何 VideoSource 實作忘記自己處理例外，都不能讓呼叫端
        # （Tk 的 button command）整個炸掉，只能反映成 error 狀態。
        self._start_exception_message = None

    def start_live_view(self):
        """如果已經在跑，直接 return，不會重複啟動第二條 stream/thread。"""
        if self._video_source.is_running():
            return
        self._has_received_frame = False
        self._start_exception_message = None
        try:
            self._video_source.start()
        except Exception as error:
            self._start_exception_message = str(error)

    def stop_live_view(self):
        """可以安全重複呼叫（VideoSource.stop() 本身也是安全重複呼叫的）。"""
        try:
            self._video_source.stop()
        except Exception as error:
            print(f'[live_view_controller] stop_live_view() 發生例外: {error}')
        self._has_received_frame = False

    def retry_live_view(self):
        """使用者按「Retry Live View」時呼叫：先完整停止、再重新開始，
        不是只是清掉錯誤訊息假裝重連——真的重新建立一次連線。"""
        self.stop_live_view()
        self.start_live_view()

    def get_latest_frame(self):
        """回傳 (width, height, rgb24_bytes) 或 None（還沒有畫面）。"""
        return self._video_source.read_frame()

    def get_status(self):
        """回傳 (status, error_message, source_label)：
        - status 是 STATUS_IDLE/CONNECTING/CONNECTED/ERROR 其中一個
        - error_message 只有 status=='error' 時才有內容，其他情況是 None
        - source_label 是建構時傳入的來源名稱（給 UI 顯示 "Connected -
          RTSP" 這種文字用）

        判斷順序：先看 VideoSource 有沒有回報錯誤（有錯誤就算 error，
        不管 is_running() 是 True 還是 False——錯誤是終局狀態，值得比
        「行程還活著」更優先顯示）；沒有錯誤的話，只要曾經成功讀到過一張
        畫面就算 connected（就算這一瞬間 read_frame() 剛好還沒有更新新
        畫面，也不會在已經連上的情況下又跳回 connecting，畫面閃爍）；
        還沒收到過畫面但 VideoSource 確實在跑就是 connecting；都不是
        就是 idle（還沒呼叫過 start_live_view()，或已經 stop 過）。

        start_live_view() 呼叫 VideoSource.start() 時如果直接丟例外
        （_start_exception_message 有值），視為最優先的錯誤來源。"""
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

        if self._video_source.is_running():
            return STATUS_CONNECTING, None, self._source_label

        return STATUS_IDLE, None, self._source_label

    def is_running(self):
        return self._video_source.is_running()
