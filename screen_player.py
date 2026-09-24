"""
screen_player.py — Player 畫面

流程：外部（Recording / Gallery 畫面）呼叫 set_current_recording_group() 傳入
一個「group」（同一個 groupid 底下的所有段落，已依 sequenceid 排序的 list）
-> 背景執行緒用 video_decoder.create_decoder_with_fallback() 打開第一段的
HTTP 網址（FFmpeg QSV -> FFmpeg 軟體解碼 -> OpenCV 依序 fallback） -> 內嵌
播放；一段播完自動接著開下一段，直到整個 group 播完。

不開外部播放器、不用 os.startfile / xdg-open / VLC。

為什麼要處理「多段」：HCP3 的規則是同一個 groupid 算同一部影片，但韌體的
ring buffer 換檔會把它切成好幾段（不同 fileid/sequenceid）。例如三段
1分/1分/30秒，使用者感覺到的是一部 2:30 的影片，所以這裡要自動接續播放，
進度條也要顯示整個 group 的累計時間，不是只看目前這一段。

邊載邊播的原理（給以後維護的人看）：
- 不用「先整支下載到暫存檔，下載完才播放」的做法，改成直接把 DVR 影片的網址
  交給 decoder 打開（不管是 FFmpeg 還是 OpenCV，底層都支援直接讀 HTTP 網址）。
- 實測過（接真機）：DVR 的 HTTP server 支援 Range request，開啟網址只要
  0.1 秒左右，不需要等整支影片下載完，seek 也正常。

Progress Bar / Seek 原理（多段版）：
- set_current_recording_group() 一開始就用每一段的 duration 欄位（protobuf
  metadata，不用真的打開影片）算出「每段開始時，前面累積的秒數」跟「整個
  group 的總長度」，進度條的總長度就是這個 group 總長度。
- 播放中，目前時間 = 目前這段的累積起點 + 目前這段播放到第幾秒。
- 拖曳進度條放開時，先算出目標時間落在第幾段、段內第幾秒，如果是同一段就
  直接 seek，如果是別的段就重新開那一段的串流再 seek 到指定位置。

Previous / Next 原理：
- 呼叫 api.get_recordings() -> api.group_recordings_by_groupid() 分組，
  找出目前這個 group 的前一組/後一組，一樣呼叫 set_current_recording_group()。

2026-08-12 新增播放效能量測（start_performance_recording() /
print_performance_report()）：純粹記錄 Source FPS / Decode FPS / Actual
Render FPS / Displayed Frames / Dropped Frames / Queue Drops / Frame
Interval 統計 / Decode Time / Render Time 等數字，不會影響任何播放排程、
佇列、UI 邏輯本身——不呼叫 start_performance_recording() 的話，完全沒有
任何行為差異。給診斷腳本用，不會有任何畫面顯示這些數字（沒有動 UI）。

2026-08-12 第三階段：依上面量測到的結果（decode 一旦落後，playback clock
永遠錨定真實時間，造成幾百張畫面連鎖被判定 stale 丟棄）調整播放同步策略，
不換播放器框架、不動 UI、不導入 Qt/GStreamer/硬解：
- Frame Queue 從 2 張放大到 FRAME_QUEUE_MAX_SIZE（8）張，decode thread 補到
  FRAME_QUEUE_MIN_BUFFER（5）張以上就可以稍微休息，不用無限制超前狂讀。
- 開始播放、或播放中佇列被讀空，都會先進入 Buffering（begin_buffering()）：
  playback clock 暫停，不會繼續往真實時間跑，等佇列補到 FRAME_QUEUE_MIN_
  BUFFER 張（或逾時/EOF）才 resync clock、恢復播放（end_buffering()）。
- show_next_frame() 的丟幀邏輯改成分級：落後在 FRAME_LAG_NORMAL_MS
  （50ms）以內正常播放；50~150ms 之間允許丟，但同一輪最多連續丟
  MAX_CONSECUTIVE_LAG_DROPS（5）張；超過 FRAME_LAG_RESYNC_THRESHOLD_MS
  （150ms）或連續丟太多張，不再丟，直接顯示手上這張並 resync_playback_
  clock()，避免「網路卡一下，後面幾十秒都在丟」的連鎖反應。
- advance_to_next_segment() 會比對 decode 實際讀到的媒體時間跟這段的
  duration metadata，落差太大（低於 STREAM_COMPLETE_RATIO_THRESHOLD）就
  記一次 Stream Error，量測時不能把「串流中斷」當成「正常播完」。
- 效能報告新增 Buffering Count / Total Buffering Time / Max Buffering
  Time / Clock Resync Count / Stream Error Count，drop 相關欄位改成
  「Frames Dropped by Lag」跟「Frames Dropped by Queue」兩個分開算。

2026-08-12 第四階段：影片解碼改用 video_decoder.py 的 Decoder abstraction
（正式整合 QSV 硬體解碼 PoC）：
- 這支檔案不再直接 import cv2、不再自己組任何 QSV/FFmpeg command，全部
  搬進 video_decoder.py。跟 decoder 只透過統一介面溝通：get_fps() /
  read() -> (has_frame, frame_rgb, media_seconds) / seek(seconds) /
  release()，三種 backend（FFmpeg QSV / FFmpeg 軟體 / OpenCV）對這支
  檔案來說完全一樣，這裡不需要知道背後是哪一種。
- Buffer / Clock Resync / Small Queue / Playback controls（play/pause/
  seek/上一組/下一組）完全沿用第三階段驗證有效的邏輯，沒有重寫。
- _decode_loop() 的節流邏輯升級成「目標播放速度」的 pacing（不是固定
  小睡一下）：QSV 實測 87~258 FPS，遠超過 30fps，決定要不要睡是看
  「decode 領先目標時間軸多少」，最多領先 FRAME_QUEUE_MIN_BUFFER 張，
  不是靠 Queue 滿了自然回壓（那樣會製造大量 Queue Drop）。decode 本身
  比 fps 慢的情況（OpenCV backend）目標時間點永遠已經過去，完全不受
  影響，跟之前行為一致。
- media_seconds 改成由 decoder.read() 直接回傳（decoder 內部統一用
  「解碼順序 frame 序號 / fps」算，不是 PTS/時間戳），這支檔案不用再
  自己呼叫任何底層 API 去查目前播到第幾秒。
- draw_frame() 不再自己做 cv2.resize()/cv2.cvtColor()，三種 backend 的
  decoder.read() 都直接回傳「已經縮小、已經是 RGB」的畫面
  （FFmpeg 路線在 GPU/FFmpeg 端處理；OpenCV 路線搬進 OpenCVDecoder 裡，
  邏輯跟之前完全一樣，只是換了位置）。
- 現有 OpenCV 播放路徑完全保留，當 FFmpeg 或 QSV 都不可用時的最終
  fallback，沒有被刪除。
"""

import threading
import tkinter as tk
import tkinter.font as tkfont
from tkinter import messagebox
from io import BytesIO
from time import time, sleep
from collections import deque
from statistics import mean, pstdev
from PIL import Image, ImageDraw, ImageTk

import api
import video_decoder
import ui_shapes

# 2026-08-12 第四階段：影片解碼改用 video_decoder.py 的 Decoder abstraction
# （FFmpeg QSV -> FFmpeg 軟體解碼 -> OpenCV 依序 fallback），這支檔案不再
# 直接 import cv2、不再自己組 QSV/FFmpeg command——那些都搬進
# video_decoder.py 了。這裡拿到的 decoder 物件不管背後是哪一種 backend，
# 都用同一組介面：get_fps() / read() -> (has_frame, frame_rgb, media_seconds)
# / seek(seconds) / release()，Buffer/Clock Resync/Small Queue/Playback
# controls 完全沿用之前驗證有效的邏輯，不重寫。
VIDEO_DISPLAY_SIZE = video_decoder.DISPLAY_SIZE

# ============ 背景 decode 執行緒 + 小型 frame queue（2026-08-11 第二階段優化，
# 2026-08-12 第三階段調整 Buffer/Resync 策略） ============
# 第一階段只是「排程對齊時間軸」，但 cap.read() 還是在 UI 執行緒（Tkinter 主執行緒）
# 上直接呼叫，網路/解碼慢的時候還是會卡住整個畫面。第二階段改成：
# - 另開一條背景執行緒專門負責 read()，UI 執行緒完全不會呼叫 read()。
# - 用 collections.deque(maxlen=FRAME_QUEUE_MAX_SIZE) 當佇列：滿了自動丟掉
#   最舊的那一張，永遠保留最新的畫面，不會累積 backlog（deque 的 append()
#   本身是 GIL 保護下的單一操作，在 CPython 上可以安全地跨執行緒用，不用另外加鎖）。
#
# 第三階段（實測 4 次 42 秒影片後發現的問題）：playback clock 原本永遠錨定在
# 「這一段開始播的那一刻」，decode 一旦因為網路/串流卡頓落後，expected_media
# _seconds 還是照真實時間一直往前跑，decode 補上來的畫面永遠比這個時間點舊，
# 結果幾乎整支影片的畫面都被判定 stale 丟棄（實測 800~1236 張，Actual Render
# FPS 只有 0~18.76）。這裡改成：
# - 佇列放大到 FRAME_QUEUE_MAX_SIZE 張（原本只有 2），decode thread 補到
#   FRAME_QUEUE_MIN_BUFFER 張以上就可以先休息一下，不用一直全速衝。
# - 開始播放/queue 讀到空的時候，先進入 Buffering：playback clock 暫停
#   （不要讓 expected_media_seconds 繼續往前跑），等佇列補到
#   FRAME_QUEUE_MIN_BUFFER 張才 resync clock、恢復播放。
# - 丟幀改成分級：落後在 FRAME_LAG_NORMAL_MS 以內正常播放；落後在
#   FRAME_LAG_NORMAL_MS ~ FRAME_LAG_RESYNC_THRESHOLD_MS 之間，允許丟，但同一輪
#   最多丟 MAX_CONSECUTIVE_LAG_DROPS 張；超過 FRAME_LAG_RESYNC_THRESHOLD_MS 或
#   連續丟太多張，不要再丟，直接 resync clock 到目前這張的實際時間，避免
#   「網路卡一下，後面幾十秒都在丟」的連鎖反應。
FRAME_QUEUE_MAX_SIZE = 8
FRAME_QUEUE_MIN_BUFFER = 5      # Buffering 要補到至少這麼多張才恢復播放；decode thread 也用這個當 pacing 的「小幅 ahead buffer」上限
QUEUE_EMPTY_RETRY_MS = 10       # decode 還沒補上新畫面/Buffering 檢查佇列，多久後再檢查一次
BUFFERING_TIMEOUT_MS = 2000     # Buffering 最多等這麼久（例如影片剩下的長度不夠 5 張），逾時就別再等了
DECODE_PACING_MAX_SLEEP_SECONDS = 0.5  # decode pacing 單次最多睡這麼久，避免 stop_event 設定後要等太久才反應

FRAME_LAG_NORMAL_MS = 50            # 落後在這以內，正常播放，不啟動丟幀邏輯
FRAME_LAG_RESYNC_THRESHOLD_MS = 150  # 落後超過這個門檻，不要再丟，直接 resync clock
MAX_CONSECUTIVE_LAG_DROPS = 5        # 即使單張落後量不到 resync 門檻，同一輪最多連續丟這麼多張就強制 resync
STREAM_COMPLETE_RATIO_THRESHOLD = 0.9  # decode 到的媒體時間要達到這段 duration 的這個比例，才算「正常播完」

# 2026-08-14 除錯播放卡住問題用：改成 True，之後每次開始播一段
# （start_segment()）都會自動打開暫時診斷 log（同時打開效能量測，
# Rendered Frames 才有數字），在接真機測試「播放幾秒後畫面卡住」時
# 直接看 console 輸出，不用另外寫測試腳本呼叫 start_diagnostic_logging()。
# 這是暫時開關，確認問題解決後應該改回 False（或整段 diagnostic log
# 拿掉），不是正式功能。
AUTO_ENABLE_DIAGNOSTIC_LOGGING = False

current_group = []             # 目前播放的 group：同一個 groupid 底下所有段落，依 sequenceid 排序
current_segment_index = 0      # 目前播放到第幾段（0-based）
segment_start_offsets = []     # 每一段開始時，前面已經播過的累計秒數（依 duration 欄位算出來）
group_total_duration_seconds = 0

current_decoder = None
current_after_id = None
is_playing = False
current_recordings_list = None   # 完整清單（未分組），for 上一組/下一組

# 2026-08-19 使用者要求 Photo Viewer 併入這裡，變成同一個 Unified Media
# Viewer——current_media_type 決定目前要顯示哪些 Controls（'video' 有
# Play/Pause/Replay/Seek Bar/Time，'photo' 沒有這些，只有 Back/Title/
# Previous/Next）。Photo 完全不會建立 current_decoder／不會呼叫
# start_decode_thread()，跟 Video 播放流程徹底分開，這裡只是共用同一顆
# Canvas 跟 Back/Title/Previous/Next。
current_media_type = 'video'
current_photo_original_image = None   # 目前顯示中的照片原始 PIL Image（給 letterbox 用）

# 2026-08-17 修正「播完後無法再次播放」問題新增的狀態：playback_finished
# 代表「整個 group 已經自然播完」（不是使用者暫停，是真的沒有 decoder 在
# 管理任何播放 session 了）。單靠 current_decoder is None 沒辦法安全判斷
# 「已播完」——open_segment() 打開新串流那段短暫的非同步等待期間
# current_decoder 也會是 None，但那是「還在連線中」不是「播完了」，兩者
# 不能混為一談，所以另外開一個明確的旗標。
#
# 2026-08-17 第二次調整：使用者要求「Finished 狀態下 Seek＝自動從 Seek
# 位置開始播放」，不需要使用者再按一次 Play——所以 Finished 分支的
# on_seek_end() 直接觸發播放，不再需要另外記錄「有沒有 Seek 過」等 Play
# 按下才處理（原本的 seeked_after_finish 旗標拿掉，Case A/B 的差異現在
# 只剩「on_play_pause_click() 在 Finished 狀態下被按」這一種情況，一定是
# 使用者沒有 Seek 過，才會走到「從 00:00 Replay」，Seek 過的話 Seek 當下
# 就已經自動播放、清掉 playback_finished 了，不會留到 Play 按下才處理）。
playback_finished = False

fps = 30
user_is_seeking = False
was_playing_before_seek = False

# 播放時鐘（timeline / playback clock）：記錄「這一段從第幾秒開始播」跟
# 「開始播的那一刻，真實時間是幾點」，用這兩個數字就能隨時算出「現在應該
# 播到第幾秒」，不受單張畫面處理耗時影響。每次開始播一段新的串流、從暫停
# 恢復播放、或 seek 完恢復播放，都要重新設定這兩個值。
playback_clock_start_wall_time = 0
playback_clock_start_media_seconds = 0
last_shown_media_seconds = 0  # 最後一次實際畫出來那張畫面的媒體時間（暫停/seek 對齊時鐘會用到）
last_decoded_media_seconds = 0  # 最後一次 decode thread 真的 read() 到的媒體時間（不管有沒有被顯示），
                                 # 用來判斷這段是不是「正常播完」還是「中途斷流」

# Buffering 狀態：queue 補到 FRAME_QUEUE_MIN_BUFFER 之前，playback clock 暫停
# 不前進，不會判斷任何 stale/落後（見 begin_buffering()/end_buffering()）
is_buffering = False
buffering_started_wall_time = 0

# decode 背景執行緒相關（跟目前這一段串流綁在一起，換段落/seek/停止都要整組重建）
current_frame_queue = None          # collections.deque，decode thread 塞畫面進來，UI thread 從這裡拿
current_decode_thread = None
current_decode_stop_event = None    # 設定後，decode thread 讀到下一輪就會結束
current_decode_run_event = None     # 清除時 decode thread 會卡在原地等（暫停用），設定才會繼續讀
current_decode_eof_event = None     # decode thread 讀到影片結尾時會設定這個
decode_start_wall_time = 0          # decode thread 開始跑的真實時間，用來算平均 decode fps

# 統計數字（每次 start_decode_thread() 都會重設，方便量測用）
decode_frames_read_count = 0
decode_frames_dropped_by_queue_count = 0   # 佇列滿了，被自動丟掉的舊畫面數量
ui_frames_skipped_stale_count = 0          # UI 這端因為畫面已經落後時間軸而丟掉的數量
max_queue_backlog_observed = 0             # 佇列曾經累積到的最大長度

# 每次呼叫 set_current_recording_group() 就會 +1，用來判斷背景執行緒回來的結果
# 是不是舊的、使用者已經切去播別的 group 了，這種過期結果就不要理它
current_request_token = 0

# 2026-08-20 修 Player 快速連點 Next/Previous 造成 DVR 503 的第一階段：
# 目前 current_request_token 對應的內容切換（video segment 開啟或 photo
# 下載）是否已經有結果（成功或失敗都算「有結果」）。Previous/Next 按鈕要
# 等這個是 True，才可以重新 enable——之前的版本只等 on_recordings_list_
# loaded()（另一條背景執行緒，通常很快就回來）就重新 enable，使用者可以在
# 上一個 item 的串流都還沒開完/照片都還沒下載完的時候，就點下一次，導致
# 同時有好幾個 decoder open/下載在跑，見 find_adjacent_groups() 附近的
# 說明。set_current_recording_group()/set_current_photo_item() 開始新的
# request 時重設為 False，start_segment()/show_stream_error()/
# _on_photo_downloaded()/_on_photo_download_error() 對到目前 token 時才
# 設回 True。
current_request_content_ready = False

# ============ 播放效能統計（2026-08-12 新增，純量測，不影響播放邏輯本身） ============
# 跟上面 decode_frames_read_count 等既有計數器不一樣：那些是每段（每次
# start_decode_thread()）就重設一次，這裡是整個 group（可能跨多段）累積
# 一次，只在 set_current_recording_group() 重設，這樣量測 42 秒影片（如果
# 中間跨了幾段）才會是完整、連續的數字，不會被段落切換打斷。
# 這裡只負責「記錄」，完全不影響播放排程/佇列/UI 邏輯本身的任何判斷分支。
perf_recording_enabled = False
perf_source_fps = 0
perf_decode_time_samples = []      # 每次 decoder.read() 實際花了多少秒
perf_decode_timestamps = []        # 每次 read() 成功完成的那一刻（time()），算 Decode FPS 用
perf_render_time_samples = []      # 每次 draw_frame() 實際花了多少秒
perf_frame_display_wall_times = []  # 每次真的畫出一張新畫面的那一刻（time()），算 Render FPS/frame interval 用
perf_displayed_frame_count = 0
perf_dropped_by_lag_count = 0      # UI 這端因為落後時間軸、分級丟幀策略主動丟掉的張數
perf_queue_drop_count = 0          # decode thread 佇列滿了、被覆蓋掉的張數
perf_buffering_count = 0           # 進入 Buffering 狀態的次數
perf_total_buffering_seconds = 0.0  # 所有 Buffering 累計花掉的真實時間
perf_max_buffering_seconds = 0.0    # 單次 Buffering 最長花的真實時間
perf_resync_count = 0              # playback clock 被 resync 的次數
perf_stream_error_count = 0        # 段落沒播到 duration 的 90% 就結束，疑似串流中斷（不是正常播完）


def start_performance_recording():
    """開始（或重新開始）記錄這次播放的效能數字。要在呼叫
    set_current_recording_group() 之前呼叫，才能從第一張畫面就開始算。
    純粹是量測用的開關，不會改變任何播放邏輯——關掉的話（預設值）完全
    不會有任何行為差異，只是不記錄數字。"""
    global perf_recording_enabled
    global perf_source_fps, perf_decode_time_samples, perf_decode_timestamps
    global perf_render_time_samples, perf_frame_display_wall_times
    global perf_displayed_frame_count, perf_dropped_by_lag_count, perf_queue_drop_count
    global perf_buffering_count, perf_total_buffering_seconds, perf_max_buffering_seconds
    global perf_resync_count, perf_stream_error_count

    perf_recording_enabled = True
    perf_source_fps = 0
    perf_decode_time_samples = []
    perf_decode_timestamps = []
    perf_render_time_samples = []
    perf_frame_display_wall_times = []
    perf_displayed_frame_count = 0
    perf_dropped_by_lag_count = 0
    perf_queue_drop_count = 0
    perf_buffering_count = 0
    perf_total_buffering_seconds = 0.0
    perf_max_buffering_seconds = 0.0
    perf_resync_count = 0
    perf_stream_error_count = 0


def stop_performance_recording():
    global perf_recording_enabled
    perf_recording_enabled = False


def _ms_stats(samples_seconds):
    """把一堆「秒」的樣本轉成 (avg_ms, max_ms) 給報告用；沒有樣本回傳 (0, 0)"""
    if not samples_seconds:
        return 0.0, 0.0
    samples_ms = [s * 1000 for s in samples_seconds]
    return mean(samples_ms), max(samples_ms)


def build_performance_report():
    """算出目前累積到的效能報告（dict），通常在整支影片播完之後呼叫。
    純粹讀取上面那些 perf_* 數字做統計計算，不會動到任何播放狀態。"""
    frame_intervals_ms = []
    for i in range(1, len(perf_frame_display_wall_times)):
        frame_intervals_ms.append((perf_frame_display_wall_times[i] - perf_frame_display_wall_times[i - 1]) * 1000)

    decode_avg_ms, decode_max_ms = _ms_stats(perf_decode_time_samples)
    render_avg_ms, render_max_ms = _ms_stats(perf_render_time_samples)

    if len(perf_decode_timestamps) >= 2:
        decode_span_seconds = perf_decode_timestamps[-1] - perf_decode_timestamps[0]
        decode_fps = (len(perf_decode_timestamps) - 1) / decode_span_seconds if decode_span_seconds > 0 else 0.0
    else:
        decode_fps = 0.0

    if len(perf_frame_display_wall_times) >= 2:
        display_span_seconds = perf_frame_display_wall_times[-1] - perf_frame_display_wall_times[0]
        actual_render_fps = (len(perf_frame_display_wall_times) - 1) / display_span_seconds if display_span_seconds > 0 else 0.0
    else:
        display_span_seconds = 0.0
        actual_render_fps = 0.0

    return {
        'source_fps': perf_source_fps,
        'decode_fps': decode_fps,
        'actual_render_fps': actual_render_fps,
        'displayed_frames': perf_displayed_frame_count,
        'dropped_frames_by_lag': perf_dropped_by_lag_count,
        'queue_drops': perf_queue_drop_count,
        'buffering_count': perf_buffering_count,
        'total_buffering_seconds': perf_total_buffering_seconds,
        'max_buffering_seconds': perf_max_buffering_seconds,
        'resync_count': perf_resync_count,
        'stream_error_count': perf_stream_error_count,
        'average_frame_interval_ms': mean(frame_intervals_ms) if frame_intervals_ms else 0.0,
        'max_frame_interval_ms': max(frame_intervals_ms) if frame_intervals_ms else 0.0,
        'frame_interval_stddev_ms': pstdev(frame_intervals_ms) if len(frame_intervals_ms) >= 2 else 0.0,
        'frame_intervals_over_50ms': sum(1 for interval in frame_intervals_ms if interval > 50),
        'frame_intervals_over_66ms': sum(1 for interval in frame_intervals_ms if interval > 66),
        'frame_intervals_over_100ms': sum(1 for interval in frame_intervals_ms if interval > 100),
        'average_decode_time_ms': decode_avg_ms,
        'max_decode_time_ms': decode_max_ms,
        'average_render_time_ms': render_avg_ms,
        'max_render_time_ms': render_max_ms,
        'display_span_seconds': display_span_seconds,
        'decode_span_seconds': decode_span_seconds if len(perf_decode_timestamps) >= 2 else 0.0,
        'decode_frame_count': len(perf_decode_timestamps),
        'frame_intervals_ms': frame_intervals_ms,  # 原始資料，畫面外分析用（例如畫分布圖）
    }


def print_performance_report():
    """把 build_performance_report() 的結果印成人看得懂的報告，方便診斷腳本直接呼叫。"""
    report = build_performance_report()
    print('======= Player 播放效能報告 =======')
    print(f"Source FPS: {report['source_fps']:.2f}")
    print(f"Decode FPS: {report['decode_fps']:.2f}")
    print(f"Actual Render FPS: {report['actual_render_fps']:.2f}")
    print(f"Displayed Frames: {report['displayed_frames']}")
    print(f"Frames Dropped by Lag (UI 端分級丟幀): {report['dropped_frames_by_lag']}")
    print(f"Frames Dropped by Queue (decode 端佇列覆蓋): {report['queue_drops']}")
    print(f"Buffering Count: {report['buffering_count']}")
    print(f"Total Buffering Time: {report['total_buffering_seconds']:.2f}s")
    print(f"Max Buffering Time: {report['max_buffering_seconds']:.2f}s")
    print(f"Clock Resync Count: {report['resync_count']}")
    print(f"Stream Error Count（段落沒播到 90% duration 就結束）: {report['stream_error_count']}")
    print(f"Average Frame Interval: {report['average_frame_interval_ms']:.2f} ms")
    print(f"Maximum Frame Interval: {report['max_frame_interval_ms']:.2f} ms")
    print(f"Frame Interval Jitter (stddev): {report['frame_interval_stddev_ms']:.2f} ms")
    print(f"Frame Intervals > 50ms: {report['frame_intervals_over_50ms']}")
    print(f"Frame Intervals > 66ms: {report['frame_intervals_over_66ms']}")
    print(f"Frame Intervals > 100ms: {report['frame_intervals_over_100ms']}")
    print(f"Average Decode Time: {report['average_decode_time_ms']:.2f} ms")
    print(f"Maximum Decode Time: {report['max_decode_time_ms']:.2f} ms")
    print(f"Average Render Time: {report['average_render_time_ms']:.2f} ms")
    print(f"Maximum Render Time: {report['max_render_time_ms']:.2f} ms")
    print(f"Decode Frame Count: {report['decode_frame_count']}")
    print(f"Decode Span (第一張到最後一張 read() 的真實時間差): {report['decode_span_seconds']:.2f}s")
    print(f"(畫面顯示總時長: {report['display_span_seconds']:.2f}s)")
    print('====================================')
    return report


# ============ 暫時診斷 Log（2026-08-14 除錯播放卡住問題，用完要拿掉） ============
# 目的是先分清楚卡住當下到底是哪一種：
#   A. FFmpeg 不再輸出（FFmpeg Alive 變 False，或 Decoded Frames 沒有再增加
#      但 process 還活著——後者代表 FFmpeg 卡住沒吐資料，不是真的死掉）
#   B. stdout reader 卡住（FFmpeg Alive=True，Decode Thread Alive=True，
#      但 Decoded Frames 沒有再增加——decode thread 卡在 read() 裡面）
#   C. Queue producer 停止（Decode Thread Alive 變 False，Decoded Frames
#      不再增加，但 FFmpeg 可能還活著——decode thread 自己跳出迴圈了）
#   D. UI consumer 停止（Decoded Frames 持續增加、Queue Size 持續變大，
#      但 Rendered Frames 沒有跟著增加——show_next_frame() 沒有被排程到）
#   E. Playback Clock / Buffering 沒有恢復（Buffering 一直是 True 不變回
#      False，但 Queue Size 其實已經有東西）
# 只是印 log，不影響任何播放排程/佇列/UI 邏輯本身的判斷分支，拿掉這一段
# 跟呼叫端的 start_diagnostic_logging()/stop_diagnostic_logging() 之後，
# 播放行為完全不變。
diagnostic_logging_enabled = False
_diagnostic_after_id = None
last_frame_rendered_wall_time = 0   # 最後一次真的 draw_frame() 畫出新畫面的時間，算 Last Frame Age 用

# 2026-08-17 新增（問題 3：Seek 偶發卡住的 Root Cause 診斷用）：每次真的
# 執行到 on_seek_end() 的「實際處理 seek」邏輯（不是被開頭的 guard 擋掉）
# 就 +1，用來確認「使用者只點一次 Progress Bar，Python 這邊到底進入了
# 幾次 on_seek_end() 的實際處理分支」——純粹是印 log 用的計數器，不影響
# 任何播放/seek 邏輯本身。
seek_request_counter = 0


def start_diagnostic_logging():
    """Rendered Frames 這個診斷數字是靠 perf_displayed_frame_count 算的，
    只有 perf_recording_enabled 是 True 才會真的累計，所以這裡自己保證
    一定會開，不依賴呼叫端另外記得呼叫 start_performance_recording()——
    不然單獨呼叫這支函式時 Rendered Frames 會一直顯示 0，log 反而誤導。
    如果呼叫之前 perf 已經是開的（呼叫端自己手動開的），這裡不會重設它
    （不呼叫 start_performance_recording()），避免蓋掉呼叫端自己在量測的
    數字。"""
    global diagnostic_logging_enabled, last_frame_rendered_wall_time
    if not perf_recording_enabled:
        start_performance_recording()
    diagnostic_logging_enabled = True
    last_frame_rendered_wall_time = time()
    _diagnostic_log_tick()


def stop_diagnostic_logging():
    """跟 start_diagnostic_logging() 對稱：這裡也一起關掉效能量測，不要
    讓 perf_recording_enabled 停在 True 卻沒人記得關（stop_video() 只會
    呼叫這支函式，不會單獨管 perf_recording_enabled，兩者要綁在一起才不
    會有一邊忘記關的情況）。"""
    global diagnostic_logging_enabled, _diagnostic_after_id
    diagnostic_logging_enabled = False
    stop_performance_recording()
    if _diagnostic_after_id is not None:
        video_label.after_cancel(_diagnostic_after_id)
        _diagnostic_after_id = None


def _diagnostic_log_tick():
    global _diagnostic_after_id
    if not diagnostic_logging_enabled:
        return

    # 直接伸手進 decoder 內部的 _process（正常不會這樣用，video_decoder.py
    # 的介面本來就不打算讓呼叫端知道背後是不是 subprocess）：這裡是暫時
    # 診斷用，只讀不動，用完整段拿掉就恢復原本的介面邊界。
    ffmpeg_alive = 'N/A (no subprocess / decoder not open)'
    process = getattr(current_decoder, '_process', None)
    if process is not None:
        ffmpeg_alive = (process.poll() is None)

    decode_thread_alive = current_decode_thread.is_alive() if current_decode_thread is not None else False
    queue_size = len(current_frame_queue) if current_frame_queue is not None else 0
    last_frame_age_seconds = time() - last_frame_rendered_wall_time

    print(f'[Diag] FFmpeg Alive: {ffmpeg_alive} | Decode Thread Alive: {decode_thread_alive} | '
          f'Decoded Frames: {decode_frames_read_count} | Rendered Frames: {perf_displayed_frame_count} | '
          f'Queue Size: {queue_size} | Playback State: {"Playing" if is_playing else "Paused/Stopped"} | '
          f'Buffering: {is_buffering} | Last Frame Age: {last_frame_age_seconds:.2f}s')

    _diagnostic_after_id = video_label.after(1000, _diagnostic_log_tick)


on_back_callback = None

video_label = None      # 現在是 tk.Canvas（不是 tk.Label），沿用同一個變數名稱，
                        # 這樣既有一大堆 video_label.after()/after_cancel() 排程
                        # 呼叫都不用改——Canvas 一樣繼承自 Tkinter 的 Misc，
                        # after()/after_cancel() 這兩個方法都有。
video_container = None
progress_scale = None
current_time_label = None
total_duration_label = None

# 2026-08-17 使用者回報 Overlay 按鈕外圍還是看得到黑色方形，改成把
# Back/Title/Previous/Next/Play-Pause 全部直接畫在 video_label（現在是
# Canvas）上面，用 canvas.create_image()/create_text()，不再用
# tk.Label/tk.Button 包一層。Canvas 的 item 不是 Python widget 物件，
# 不能像以前那樣直接在 widget 上附加 .enabled 屬性，所以「這顆按鈕現在
# 能不能點」改成用下面這幾個獨立的布林變數紀錄。
back_button_enabled = True
play_pause_button_enabled = False
previous_button_enabled = False
next_button_enabled = False

# ============================================================
# 2026-08-17 使用者要求：Controls 自動隱藏/顯示（播放中一段時間沒操作就
# 隱藏，點影片/滑鼠移動/操作任一 Control 都要重新顯示＋重設計時器）。
# 只用單一計時器（controls_auto_hide_after_id），不要每個按鈕各自維護一套，
# 所有「顯示/隱藏/重設倒數」都集中在 show_player_controls()/
# hide_player_controls()/reset_controls_auto_hide_timer() 這三個函式，
# 純粹是「Controls 看不看得到」的 UI 狀態，不影響任何播放邏輯本身。
# ============================================================
CONTROLS_AUTO_HIDE_DELAY_MS = 2500   # 無操作幾秒後自動隱藏（使用者建議 2~3 秒）

overlay_control_widgets = []       # [(widget, place_kwargs), ...]，show/hide 用
controls_visible = True
controls_auto_hide_after_id = None

# ============================================================
# 2026-08-14 使用者要求：Player 畫面改成接近 HCP3 實機的配置——影片畫面
# 盡量放大，Back/Play-Pause/Previous/Next 改成 Overlay 在影片上的圓形
# 半透明按鈕，Progress Bar/時間也搬到影片底部 Overlay，不再是影片下方
# 一整排 PC-style control bar。這一段只負責「畫出圓形按鈕圖示」，純粹是
# 外觀，不含任何播放邏輯——按鈕實際的 command 還是接回原本就有的
# on_back_button_click()/on_play_pause_click()/on_previous_button_click()/
# on_next_button_click()，沒有重寫。
#
# 2026-08-17 使用者回報：改用 tk.Label 疊放圖片的做法，Label 本身還是一個
# 有實色背景的矩形 widget，圓形圖示以外的方形範圍會露出 Label 的 bg
# （原本用純黑去模擬融合，但只要影片內容不是純黑就會看到明顯的方塊）。
# 改成：Back/Title/Previous/Next/Play-Pause 全部直接畫在 video_label這個
# Canvas 上面（canvas.create_image()/create_text()），圖示本身用真的
# RGBA alpha transparency（PIL Image.new('RGBA', ..., (0,0,0,0)) 當底，
# 圓形用帶 alpha 的顏色畫），Canvas 疊圖片本來就支援 alpha 合成（跟
# tk.Label 不同，Canvas 上同一張 Canvas 裡的多個 item 是真的疊在一起合成
# 顯示，不是各自獨立的不透明矩形 widget），這樣圓形以外的部分才會真的透明、
# 看得到底下的影片畫面，不再是黑色方塊。
# ============================================================
OVERLAY_CIRCLE_FILL = (122, 118, 168, 170)          # 圓形按鈕填色（淺紫，約 67% 不透明，參考 HCP3 截圖的半透明感）
OVERLAY_CIRCLE_DISABLED_FILL = (58, 56, 64, 140)    # Disabled 時較暗、更透明
OVERLAY_ICON_COLOR = 'white'
OVERLAY_ICON_DISABLED_COLOR = '#9a97b0'

BACK_BUTTON_DIAMETER = 44
PLAY_PAUSE_BUTTON_DIAMETER = 76
NAV_BUTTON_DIAMETER = 56
TITLE_TEXT_FONT = ('Calibri', 12)   # 左上角 Filename 文字字型；Icon 尺寸也依這個字型的實際高度換算，兩邊要用同一份設定
TITLE_ICON_TO_TEXT_HEIGHT_RATIO = 1.2   # 2026-08-19 使用者要求 Icon 高度約為文字高度的 1.1~1.3 倍，取中間值
TITLE_ICON_TEXT_GAP = 10   # 2026-08-19 使用者要求 Icon 與 Filename 間距 8~12px，取中間值

# 左上角標題前面的類型 icon 大小（正方形，2026-08-17 改成依 Scenic/Event/
# Photo 切換之後，三種圖形共用同一個正方形畫布，跟 screen_gallery 的
# ICON_SIZE 設計一致，比例才不會跑掉）。2026-08-19 使用者回報「Icon 大約
# 只有文字高度的一半」，改成不再寫死一個固定 px 值，而是在 build_screen()
# 裡用 _compute_title_icon_size() 依 TITLE_TEXT_FONT 的實際字型高度算出來
# （見該函式），這裡的 16 只是模組載入當下（還沒有 Tk root，量不到字型）
# 的暫時預設值，實際顯示前一定會被覆蓋。
TITLE_ICON_SIZE = 16


def _compute_title_icon_size():
    """依 TITLE_TEXT_FONT 的實際 linespace 高度算 Icon 邊長，不是憑感覺猜一個
    px 值——這樣以後如果調整字型大小，Icon 會自動等比例跟著變。必須在 Tk
    root 建立之後才能呼叫（tkinter.font.Font 需要預設的 Tk interpreter），
    所以放在 build_overlay_icon_images() 裡呼叫，不是模組載入時。"""
    font = tkfont.Font(family=TITLE_TEXT_FONT[0], size=TITLE_TEXT_FONT[1])
    text_height = font.metrics('linespace')
    return round(text_height * TITLE_ICON_TO_TEXT_HEIGHT_RATIO)

overlay_icon_image_refs = []   # PhotoImage 都要保留參照，不然會被 GC 回收

play_icon_enabled_image = None
pause_icon_enabled_image = None
play_pause_icon_disabled_image = None
replay_icon_enabled_image = None   # 2026-08-18 新增：整個 group 播完後，中央按鈕改顯示這個，取代一般 Play
previous_icon_enabled_image = None
previous_icon_disabled_image = None
next_icon_enabled_image = None
next_icon_disabled_image = None
back_icon_image = None
# 2026-08-17 使用者回報：左上角標題 icon 固定顯示 Scenic 的攝影機圖示，
# 不會隨目前播放的 Recording 類型切換。改成依 api.get_category() 判斷
# （'recording'=Scenic／'event'=Event／'photo'=Photo），三種各自一張圖，
# update_title_for_current_segment() 依目前 segment 的類型切換 image。
# 圖形比例沿用 screen_gallery.draw_scenic_icon()/draw_event_icon()/
# draw_photo_icon() 的座標設計（同一套視覺風格，只是那邊是直接畫在
# Gallery 卡片背景色的 Canvas 上、顏色是深色線條；這裡疊在影片畫面上，
# 顏色改成白色，其餘比例/形狀完全比照），不是另外設計一套不同風格。
title_scenic_icon_image = None
title_event_icon_image = None
title_photo_icon_image = None


def _new_overlay_circle_canvas_image(diameter, draw_icon_fn, disabled=False):
    """畫一張「真的透明底 + 圓形（半透明填色）+ icon 線條」的圖，回傳
    PhotoImage。整張圖用 RGBA、底色 alpha=0（完全透明），只有圓形本身
    跟圖示線條是不透明/半透明的實色——貼到 Canvas 上時，圓形以外的地方
    會直接透出底下的影片畫面，不會有方形色塊。

    2026-08-17：原本直接在最終顯示的 diameter（44/56/76px）上畫，圓形
    邊緣跟圖示線條都會有鋸齒。改成呼叫 ui_shapes.render_supersampled()
    ——在 4 倍尺寸的畫布上把「圓形＋圖示」一起畫完，才用 LANCZOS 縮小
    一次，圓形邊緣跟圖示線條的鋸齒會一起被平滑掉（如果分開處理，只把
    圓形做平滑、圖示還是貼在最終尺寸上畫，圖示線條一樣還是會有鋸齒）。
    draw_icon_fn 本身完全不用改——各個 icon 函式的座標/線寬都是用傳進來
    的 diameter 依比例計算，這裡直接傳「放大後的 diameter」進去，圖示
    自然也會跟著放大畫、跟圓形一起被 supersample。純粹畫圖用的小工具，
    不含任何播放邏輯。"""
    circle_fill = OVERLAY_CIRCLE_DISABLED_FILL if disabled else OVERLAY_CIRCLE_FILL
    icon_color = OVERLAY_ICON_DISABLED_COLOR if disabled else OVERLAY_ICON_COLOR

    def draw_callback(draw, big_diameter, _big_height, _scale):
        draw.ellipse((0, 0, big_diameter - 1, big_diameter - 1), fill=circle_fill)
        draw_icon_fn(draw, big_diameter, icon_color)

    image = ui_shapes.render_supersampled(diameter, diameter, draw_callback)
    photo_image = ImageTk.PhotoImage(image)
    overlay_icon_image_refs.append(photo_image)
    return photo_image


def _draw_play_triangle(draw, diameter, color):
    cx, cy = diameter / 2, diameter / 2
    half = diameter * 0.16
    draw.polygon([(cx - half * 0.6, cy - half), (cx - half * 0.6, cy + half), (cx + half * 1.1, cy)], fill=color)


def _draw_pause_bars(draw, diameter, color):
    cx, cy = diameter / 2, diameter / 2
    bar_width = diameter * 0.09
    bar_height = diameter * 0.22
    gap = diameter * 0.07
    draw.rectangle((cx - gap - bar_width, cy - bar_height, cx - gap, cy + bar_height), fill=color)
    draw.rectangle((cx + gap, cy - bar_height, cx + gap + bar_width, cy + bar_height), fill=color)


def _draw_chevron(draw, diameter, color, direction):
    cx, cy = diameter / 2, diameter / 2
    size = diameter * 0.16
    width = max(2, int(diameter * 0.09))
    if direction == 'left':
        points = [(cx + size * 0.6, cy - size), (cx - size * 0.6, cy), (cx + size * 0.6, cy + size)]
    else:
        points = [(cx - size * 0.6, cy - size), (cx + size * 0.6, cy), (cx - size * 0.6, cy + size)]
    draw.line(points, fill=color, width=width, joint='curve')


def _draw_replay_icon(draw, diameter, color):
    """Replay（整個 group 播完後，中央按鈕改顯示這個，取代一般 Play）：
    常見的「重播」圖示畫法——一個約 270 度缺口的圓弧＋箭頭，箭頭指向圓弧
    前進方向（順時針），視覺上類似瀏覽器/影片播放器常見的重新整理/
    重播箭頭。跟其他 icon 一樣是純線條/實心色塊，沒有另外的方形底
    （方形透明背景由 _new_overlay_circle_canvas_image() 統一處理）。"""
    cx, cy = diameter / 2, diameter / 2
    radius = diameter * 0.24
    stroke_width = max(2, int(diameter * 0.08))
    bbox = (cx - radius, cy - radius, cx + radius, cy + radius)
    # PIL 的 arc() 角度是「以圖片座標系（y 往下）為準，0 度在 3 點鐘方向，
    # 角度增加＝順時針」，這裡畫 0~270 度（3 點鐘沿順時針轉到 12 點鐘），
    # 留下 12~3 點鐘這一段當缺口。
    draw.arc(bbox, start=0, end=270, fill=color, width=stroke_width)

    # 箭頭畫在圓弧結束端（12 點鐘方向），指向圓弧前進方向（順時針，往右），
    # 是一個朝右的簡單三角形，尖端對齊圓弧結束的那個點。
    tip_x, tip_y = cx, cy - radius
    arrow_length = diameter * 0.16
    arrow_half_width = diameter * 0.11
    base_x = tip_x - arrow_length
    draw.polygon([
        (tip_x, tip_y),
        (base_x, tip_y - arrow_half_width),
        (base_x, tip_y + arrow_half_width),
    ], fill=color)


# screen_gallery.draw_scenic_icon()/draw_event_icon()/draw_photo_icon() 的
# 座標都是設計在一個 18x18 的正方形畫布上（screen_gallery.ICON_SIZE=18），
# 線寬用 screen_gallery.ICON_LINE_WIDTH=1.4——這裡換算成比例（除以 18），
# 畫在 TITLE_ICON_SIZE 的正方形上，形狀/比例完全比照，只是換算成任意
# 尺寸都適用的比例座標，不是照抄一份寫死 18px 的座標。
_GALLERY_ICON_LINE_WIDTH_RATIO = 1.4 / 18


def _new_title_scenic_icon_image():
    """Scenic：攝影機側面剪影（方形機身＋梯形鏡頭），沿用
    screen_gallery.draw_scenic_icon() 的圖形比例，白色線條、透明背景。"""
    def draw_callback(draw, big_width, big_height, scale):
        line_width = max(1, round(_GALLERY_ICON_LINE_WIDTH_RATIO * big_width))
        draw.rectangle((big_width * 2 / 18, big_height * 5 / 18, big_width * 10 / 18, big_height * 13 / 18),
                        outline='white', width=line_width)
        draw.polygon([(big_width * 10 / 18, big_height * 7 / 18), (big_width * 15 / 18, big_height * 4 / 18),
                      (big_width * 15 / 18, big_height * 14 / 18), (big_width * 10 / 18, big_height * 11 / 18)],
                     outline='white', width=line_width)

    image = ui_shapes.render_supersampled(TITLE_ICON_SIZE, TITLE_ICON_SIZE, draw_callback)
    photo_image = ImageTk.PhotoImage(image)
    overlay_icon_image_refs.append(photo_image)
    return photo_image


def _new_title_event_icon_image():
    """Event：警示三角形外框＋驚嘆號，沿用 screen_gallery.draw_event_icon()
    的圖形比例，白色線條、透明背景。"""
    def draw_callback(draw, big_width, big_height, scale):
        line_width = max(1, round(_GALLERY_ICON_LINE_WIDTH_RATIO * big_width))
        draw.polygon([(big_width * 9 / 18, big_height * 2 / 18), (big_width * 16 / 18, big_height * 15 / 18),
                      (big_width * 2 / 18, big_height * 15 / 18)],
                     outline='white', width=line_width)
        draw.line([(big_width * 9 / 18, big_height * 6.5 / 18), (big_width * 9 / 18, big_height * 10.3 / 18)],
                  fill='white', width=line_width)
        draw.ellipse((big_width * 8.4 / 18, big_height * 11.4 / 18, big_width * 9.6 / 18, big_height * 12.6 / 18),
                     outline='white', width=line_width)

    image = ui_shapes.render_supersampled(TITLE_ICON_SIZE, TITLE_ICON_SIZE, draw_callback)
    photo_image = ImageTk.PhotoImage(image)
    overlay_icon_image_refs.append(photo_image)
    return photo_image


def _new_title_photo_icon_image():
    """Photo：相機剪影（機身＋觀景窗凸起＋環狀鏡頭），沿用
    screen_gallery.draw_photo_icon() 的圖形比例，白色線條、透明背景。"""
    def draw_callback(draw, big_width, big_height, scale):
        line_width = max(1, round(_GALLERY_ICON_LINE_WIDTH_RATIO * big_width))
        draw.rectangle((big_width * 2 / 18, big_height * 6 / 18, big_width * 16 / 18, big_height * 15 / 18),
                        outline='white', width=line_width)
        draw.rectangle((big_width * 6 / 18, big_height * 3 / 18, big_width * 11 / 18, big_height * 6 / 18),
                        outline='white', width=line_width)
        draw.ellipse((big_width * 6.5 / 18, big_height * 8.5 / 18, big_width * 11.5 / 18, big_height * 13.5 / 18),
                     outline='white', width=line_width)

    image = ui_shapes.render_supersampled(TITLE_ICON_SIZE, TITLE_ICON_SIZE, draw_callback)
    photo_image = ImageTk.PhotoImage(image)
    overlay_icon_image_refs.append(photo_image)
    return photo_image


def build_overlay_icon_images():
    """在 build_screen() 裡呼叫一次，把所有需要的圓形按鈕圖示都先畫好存起來，
    之後只需要 .config(image=...) 切換，不用每次都重畫。"""
    global play_icon_enabled_image, pause_icon_enabled_image, play_pause_icon_disabled_image
    global replay_icon_enabled_image
    global previous_icon_enabled_image, previous_icon_disabled_image
    global next_icon_enabled_image, next_icon_disabled_image, back_icon_image
    global title_scenic_icon_image, title_event_icon_image, title_photo_icon_image
    global TITLE_ICON_SIZE

    TITLE_ICON_SIZE = _compute_title_icon_size()

    play_icon_enabled_image = _new_overlay_circle_canvas_image(
        PLAY_PAUSE_BUTTON_DIAMETER, _draw_play_triangle)
    pause_icon_enabled_image = _new_overlay_circle_canvas_image(
        PLAY_PAUSE_BUTTON_DIAMETER, _draw_pause_bars)
    play_pause_icon_disabled_image = _new_overlay_circle_canvas_image(
        PLAY_PAUSE_BUTTON_DIAMETER, _draw_play_triangle, disabled=True)
    replay_icon_enabled_image = _new_overlay_circle_canvas_image(
        PLAY_PAUSE_BUTTON_DIAMETER, _draw_replay_icon)

    previous_icon_enabled_image = _new_overlay_circle_canvas_image(
        NAV_BUTTON_DIAMETER, lambda draw, d, color: _draw_chevron(draw, d, color, 'left'))
    previous_icon_disabled_image = _new_overlay_circle_canvas_image(
        NAV_BUTTON_DIAMETER, lambda draw, d, color: _draw_chevron(draw, d, color, 'left'), disabled=True)
    next_icon_enabled_image = _new_overlay_circle_canvas_image(
        NAV_BUTTON_DIAMETER, lambda draw, d, color: _draw_chevron(draw, d, color, 'right'))
    next_icon_disabled_image = _new_overlay_circle_canvas_image(
        NAV_BUTTON_DIAMETER, lambda draw, d, color: _draw_chevron(draw, d, color, 'right'), disabled=True)

    back_icon_image = _new_overlay_circle_canvas_image(
        BACK_BUTTON_DIAMETER, lambda draw, d, color: _draw_chevron(draw, d, color, 'left'))

    title_scenic_icon_image = _new_title_scenic_icon_image()
    title_event_icon_image = _new_title_event_icon_image()
    title_photo_icon_image = _new_title_photo_icon_image()


def make_canvas_button_click_handler(is_enabled_fn, callback):
    """Canvas 上的圓形按鈕統一用這個包一層：只有 is_enabled_fn() 回傳 True
    才會真的呼叫 callback（callback 是既有的 on_xxx_button_click()，完全
    不動）。Canvas item 不是 Python widget 物件，不能像以前 tk.Label 那樣
    直接在 widget 上附加 .enabled 屬性，所以改成傳一個「怎麼查目前是否
    enabled」的函式進來，各顆按鈕對應各自的模組層級布林變數
    （back_button_enabled/previous_button_enabled/...）。

    2026-08-17 使用者要求：點任何一個 Control（Back/Previous/Next/
    Play-Pause）都要重新顯示 Controls＋重設 auto-hide 倒數，這裡統一在
    這一層處理，四顆按鈕共用同一套，不用各自重寫。注意要在 callback()
    執行「之後」才 reset 計時器——例如 Play/Pause 的 is_playing 是在
    callback() 裡面才真的切換，如果先 reset 再呼叫 callback()，
    should_keep_controls_visible() 判斷用的會是切換前的舊狀態，暫停動作
    反而可能排出一個「稍後把 Controls 藏起來」的錯誤計時器。"""
    def handler(event):
        if is_enabled_fn():
            callback()
            reset_controls_auto_hide_timer()
    return handler


# 2026-08-17：Back/Title/Previous/Next/Play-Pause 現在都是畫在 video_label
# 這個 Canvas 上的 item，用這些固定的 tag 名稱操作顯示/隱藏跟切換圖示，
# 不用另外维护一份 (widget, place_kwargs) 清單。
CANVAS_OVERLAY_CONTROL_TAGS = ('back_button', 'title_icon', 'title_text', 'previous_button', 'next_button', 'play_pause_button')

overlay_control_widgets = []       # 剩下 bottom_overlay 這種還是用 tk widget + .place() 的元件


def show_player_controls():
    """把 Back/Title/Previous/Next/Play-Pause（Canvas item）跟底部 Overlay
    （Progress Bar + 時間 + Trigger Time，還是 tk widget）全部顯示出來。
    狀態文字（'progress_label_item' 這個 Canvas item，例如 Stream Failed/
    Restoring DVR mode...）不受影響，一直保持可見。

    2026-08-19 Photo 併入 Unified Media Viewer 之後：Photo 不需要
    Play/Pause/Replay/Seek Bar/Time（見 CLAUDE.md 需求），這裡依
    current_media_type 把 play_pause_button（Canvas item）跟 bottom_overlay
    （tk widget，Seek Bar + 時間）排除在外，其餘 Back/Title/Previous/Next
    兩種模式都要顯示。"""
    global controls_visible
    controls_visible = True
    is_photo_mode = current_media_type == 'photo'
    for tag in CANVAS_OVERLAY_CONTROL_TAGS:
        if tag == 'play_pause_button' and is_photo_mode:
            video_label.itemconfigure(tag, state='hidden')
            continue
        video_label.itemconfigure(tag, state='normal')
    for widget, place_kwargs in overlay_control_widgets:
        if is_photo_mode:
            widget.place_forget()
        else:
            widget.place(**place_kwargs)


def hide_player_controls():
    global controls_visible
    controls_visible = False
    for tag in CANVAS_OVERLAY_CONTROL_TAGS:
        video_label.itemconfigure(tag, state='hidden')
    for widget, _place_kwargs in overlay_control_widgets:
        widget.place_forget()


def _place_overlay_widget(widget, **place_kwargs):
    """跟 widget.place(**place_kwargs) 效果一樣，但額外記住這次用的參數，
    讓 show_player_controls()/hide_player_controls() 可以之後用同一組參數
    重新 place()/place_forget()，不用另外维护一份座標表。2026-08-17 之後
    只剩 bottom_overlay（Progress Bar + 時間）還在用這個——Back/Title/
    Previous/Next/Play-Pause 都改成畫在 Canvas 上，見
    CANVAS_OVERLAY_CONTROL_TAGS。"""
    widget.place(**place_kwargs)
    overlay_control_widgets.append((widget, place_kwargs))


def should_keep_controls_visible():
    """暫停中、或整個 group 播完了，都不應該自動隱藏——只有「正在播放」
    才會啟動自動隱藏倒數。對照使用者需求：暫停時 Play 按鈕（含其他
    Controls）要保持顯示；播完後 Controls 自動顯示且不再 auto-hide。

    Photo 模式沒有「播放」這個概念，Back/Title/Previous/Next 應該一直
    顯示，不能被 auto-hide 倒數藏起來。"""
    if current_media_type == 'photo':
        return True
    return not is_playing


def reset_controls_auto_hide_timer():
    """單一控制入口：顯示 Controls，取消舊的 auto-hide 計時器（如果有），
    只有「目前正在播放」才重新排一個新的計時器。所有會讓 Controls 該顯示
    出來的操作（點影片、滑鼠移動、點任何 Control、開始新的 segment/group）
    都呼叫這一個函式，不要各自寫一套計時邏輯。"""
    global controls_auto_hide_after_id

    if controls_auto_hide_after_id is not None:
        video_label.after_cancel(controls_auto_hide_after_id)
        controls_auto_hide_after_id = None

    show_player_controls()

    if not should_keep_controls_visible():
        controls_auto_hide_after_id = video_label.after(CONTROLS_AUTO_HIDE_DELAY_MS, _on_controls_auto_hide_timeout)


def _on_controls_auto_hide_timeout():
    global controls_auto_hide_after_id
    controls_auto_hide_after_id = None
    if should_keep_controls_visible():
        return  # 這段期間可能已經暫停/播完了，不要隱藏
    hide_player_controls()


def on_video_clicked(event):
    """點影片畫面本身：只負責顯示 Controls + 重設倒數，不能連動 Play/Pause
    （跟點 Play/Pause Overlay 按鈕是分開的兩件事，那個有自己的
    on_play_pause_click()）。"""
    reset_controls_auto_hide_timer()


def on_video_mouse_moved(event):
    """滑鼠在影片上移動也視為一種操作，一樣重新顯示 Controls + 重設倒數。"""
    reset_controls_auto_hide_timer()


def set_previous_button_enabled(enabled):
    global previous_button_enabled
    previous_button_enabled = enabled
    video_label.itemconfig('previous_button', image=previous_icon_enabled_image if enabled else previous_icon_disabled_image)


def set_next_button_enabled(enabled):
    global next_button_enabled
    next_button_enabled = enabled
    video_label.itemconfig('next_button', image=next_icon_enabled_image if enabled else next_icon_disabled_image)


def set_back_button_enabled(enabled):
    global back_button_enabled
    back_button_enabled = enabled
    # Back 圖示本身沒有 disabled 變體（HCP3 截圖上 Back 一直是同一個樣子），
    # 這裡只擋點擊，不用另外切換圖示。


def update_play_pause_icon():
    """依目前狀態切換中央按鈕圖示，只在按鈕本身是 enabled 狀態才需要正確
    反映（disabled 時固定顯示 play_pause_icon_disabled_image）。取代原本
    的 play_pause_button.config(text='Pause'/'Play')。

    2026-08-18 新增 Replay 狀態：整個 group 播完後（playback_finished=
    True）不能再顯示一般的 Play icon——這裡優先判斷 playback_finished，
    顯示 Replay icon；沒有播完的話才依 is_playing 判斷 Pause/Play，跟
    之前的邏輯一致。播完後 is_playing 一定是 False，所以這個判斷順序
    （先看 playback_finished，才看 is_playing）不會跟原本的 Pause/Play
    邏輯衝突。"""
    if not play_pause_button_enabled:
        return
    if playback_finished:
        video_label.itemconfig('play_pause_button', image=replay_icon_enabled_image)
    else:
        video_label.itemconfig('play_pause_button', image=pause_icon_enabled_image if is_playing else play_icon_enabled_image)


def set_play_pause_enabled(enabled):
    global play_pause_button_enabled
    play_pause_button_enabled = enabled
    if enabled:
        update_play_pause_icon()
    else:
        video_label.itemconfig('play_pause_button', image=play_pause_icon_disabled_image)


def build_screen(parent, on_back):
    """建立 Player 畫面，on_back 是按下「返回」時要呼叫的函式。

    2026-08-14 使用者要求改成接近 HCP3 的 Layout。2026-08-17 第二次調整：
    video_container 改成固定 = VIDEO_DISPLAY_SIZE 的大小、置中，讓 Overlay
    控制項的座標真的貼在影片內容上（詳見 video_container 建立那行旁邊的
    說明）。

    2026-08-17 第三次調整：使用者回報 Back/Previous/Play-Pause/Next 外圍
    還是看得到黑色方形——根因是這幾顆之前用 tk.Label 疊圖片，Label 本身
    是一個有實色背景的矩形 widget，圓形以外的範圍會露出 Label 的 bg
    （不同 widget 之間沒有跨 widget 的 alpha 合成，只能盡量選一個接近的
    顏色去掩蓋，但影片內容不是那個顏色時就會看到方塊）。改成：Back/
    Title/Previous/Next/Play-Pause 全部直接畫在 video_label 這個 Canvas
    上面（canvas.create_image()/create_text()），圖示本身用真的 RGBA
    alpha transparency（圓形以外的範圍 alpha=0，完全透明）——Canvas 上
    同一個 Canvas 裡的多個 item 是真的疊在一起合成顯示的，不是各自獨立
    的不透明矩形 widget，這樣才能讓圓形以外的地方真的透出底下的影片畫面。

    所有控制元件都 Overlay 在 video_label 上面，command/callback 全部
    沿用原本就有的函式，沒有新增或修改任何播放邏輯本身。"""
    global on_back_callback, video_label, video_container
    global progress_scale, current_time_label, total_duration_label
    global overlay_control_widgets

    on_back_callback = on_back

    build_overlay_icon_images()

    frame = tk.Frame(parent, bg='black')

    # ---- 影片畫面：固定跟解碼輸出一樣大小、置中，所有控制元件都 Overlay 在它上面 ----
    video_container = tk.Frame(frame, bg='black', width=VIDEO_DISPLAY_SIZE[0], height=VIDEO_DISPLAY_SIZE[1])
    video_container.place(relx=0.5, rely=0.5, anchor='center')

    # video_label 現在是 tk.Canvas（沿用同一個變數名稱，見模組層級的說明），
    # 影格畫面本身跟所有 Overlay 控制項都是這個 Canvas 上的 item。
    video_label = tk.Canvas(video_container, width=VIDEO_DISPLAY_SIZE[0], height=VIDEO_DISPLAY_SIZE[1],
                             bg='black', highlightthickness=0)
    video_label.place(x=0, y=0, relwidth=1, relheight=1)
    video_label.create_image(0, 0, anchor='nw', tags='video_frame_item')
    # 狀態文字（Connecting.../Stream Failed 等，原本是 video_label.config(text=...)），
    # 改成畫面正中央的 Canvas 文字 item，之後用 itemconfig('status_text_item', text=...) 更新。
    video_label.create_text(VIDEO_DISPLAY_SIZE[0] / 2, VIDEO_DISPLAY_SIZE[1] / 2,
                             text='', fill='white', font=('Calibri', 14), tags='status_text_item')
    # 點影片畫面本身只負責顯示 Controls + 重設 auto-hide 計時，不能連動
    # Play/Pause——跟點 Overlay 按鈕（各自有自己的 command）是分開的兩件事。
    # 只綁在 Canvas 空白處，不會被下面各個按鈕的 tag_bind 蓋掉（Tk 事件會
    # 先看點擊位置有沒有落在某個 item 上，命中哪個 item 的 tag_bind 就先用
    # 那個，不會再往下傳給 Canvas 整體的 <Button-1>）。
    video_label.bind('<Button-1>', on_video_clicked)
    video_label.bind('<Motion>', on_video_mouse_moved)

    overlay_control_widgets = []  # 現在只剩 bottom_overlay 這種還用 (widget, place_kwargs)

    # ---- 左上角：Back（沿用 on_back_button_click()，只換外觀跟位置） ----
    back_x, back_y = 16 + BACK_BUTTON_DIAMETER // 2, 16 + BACK_BUTTON_DIAMETER // 2
    video_label.create_image(back_x, back_y, image=back_icon_image, tags='back_button')
    video_label.tag_bind('back_button', '<Button-1>',
                          make_canvas_button_click_handler(lambda: back_button_enabled, on_back_button_click))

    # 2026-08-17 使用者要求：左上角改成「攝影機圖示 + 目前正在播放的
    # segment 檔名」，不再顯示 Group ID/Recording Type/segment 數量——
    # 圖示先畫，文字接在圖示右邊，兩者垂直對齊同一個 y。實際文字內容由
    # update_title_for_current_segment() 更新（set_current_recording_group()/
    # start_segment() 都會呼叫，segment 切換時同步更新檔名）。
    title_icon_x, title_y = 16 + BACK_BUTTON_DIAMETER + 10, 16 + BACK_BUTTON_DIAMETER // 2
    video_label.create_image(title_icon_x, title_y, image=title_scenic_icon_image,
                              anchor='w', tags='title_icon')
    title_text_x = title_icon_x + TITLE_ICON_SIZE + TITLE_ICON_TEXT_GAP
    video_label.create_text(title_text_x, title_y, text='', fill='white', font=TITLE_TEXT_FONT,
                             anchor='w', tags='title_text')

    # 狀態文字（Connecting.../Restoring DVR mode... 等）：不屬於「Controls」，
    # 不跟著 auto-hide 隱藏，一直保持在畫面上，才不會把 Stream Failed 這種
    # 重要錯誤訊息也一起藏起來——沿用同一個 progress_label，但現在是畫在
    # Canvas 上面靠上置中，不是另一個 tk.Label。
    video_label.create_text(VIDEO_DISPLAY_SIZE[0] / 2, 16, text='', fill='white', font=('Calibri', 11),
                             anchor='n', tags='progress_label_item')

    # ---- 影片畫面中央：Play/Pause（沿用 on_play_pause_click()） ----
    center_x, center_y = VIDEO_DISPLAY_SIZE[0] / 2, VIDEO_DISPLAY_SIZE[1] / 2
    video_label.create_image(center_x, center_y, image=play_pause_icon_disabled_image, tags='play_pause_button')
    video_label.tag_bind('play_pause_button', '<Button-1>',
                          make_canvas_button_click_handler(lambda: play_pause_button_enabled, on_play_pause_click))

    # ---- 左右兩側：Previous / Next（沿用 on_previous_button_click()/on_next_button_click()） ----
    previous_x = VIDEO_DISPLAY_SIZE[0] * 0.06
    video_label.create_image(previous_x, center_y, image=previous_icon_disabled_image, tags='previous_button')
    video_label.tag_bind('previous_button', '<Button-1>',
                          make_canvas_button_click_handler(lambda: previous_button_enabled, on_previous_button_click))

    next_x = VIDEO_DISPLAY_SIZE[0] * 0.94
    video_label.create_image(next_x, center_y, image=next_icon_disabled_image, tags='next_button')
    video_label.tag_bind('next_button', '<Button-1>',
                          make_canvas_button_click_handler(lambda: next_button_enabled, on_next_button_click))

    # ---- 底部 Overlay：Trigger Time / 目前時間｜總長度 / 進度條 ----
    # 這幾個目前使用者沒有反應黑底問題，維持原本 tk widget + .place() 的
    # 做法（Progress Bar 是互動元件，改到 Canvas 上會牽動 Seek 的事件綁定，
    # 這次范圍只處理 Back/Title/Previous/Next/Play-Pause 的透明背景）。
    # 進度條（progress_scale）跟 Seek 相關的 bind 完全沿用原本的
    # on_progress_scale_moved()/on_seek_start()/on_seek_end()，只是換了
    # 容器跟位置，不是重新實作 Seek。
    bottom_overlay = tk.Frame(video_container, bg='black')
    _place_overlay_widget(bottom_overlay, relx=0, rely=1.0, relwidth=1.0, anchor='sw')

    time_info_frame = tk.Frame(bottom_overlay, bg='black')
    time_info_frame.pack(fill='x', padx=16, pady=(4, 0))

    # 2026-08-17 使用者要求移除右下角的日期時間（Trigger Time）那一行，
    # 只保留「目前時間 / 總長度」，靠右對齊。
    time_row = tk.Frame(time_info_frame, bg='black')
    time_row.pack(side='top', anchor='e')

    current_time_label = tk.Label(time_row, text='00:00', bg='black', fg='white', font=('Calibri', 11))
    current_time_label.pack(side='left')

    tk.Label(time_row, text=' / ', bg='black', fg='white', font=('Calibri', 11)).pack(side='left')

    total_duration_label = tk.Label(time_row, text='00:00', bg='black', fg='white', font=('Calibri', 11))
    total_duration_label.pack(side='left')

    progress_scale = tk.Scale(bottom_overlay, from_=0, to=100, orient='horizontal', showvalue=False,
                               bg='black', fg='white', troughcolor='#444444', highlightthickness=0,
                               command=on_progress_scale_moved)
    progress_scale.pack(fill='x', padx=16, pady=(0, 12))
    progress_scale.bind('<ButtonPress-1>', on_seek_start)
    progress_scale.bind('<ButtonRelease-1>', on_seek_end)

    return frame


def update_title_for_current_segment():
    """左上角標題顯示目前正在播放的 segment 檔名（從 srcUri 取出，不含
    資料夾路徑），不是 Group ID/Recording Type/segment 數量。set_current_
    recording_group()（一開始顯示第一段）跟 start_segment()（每次真的
    切到新的一段）都會呼叫這裡，確保切換 segment 時檔名同步更新。

    2026-08-17 使用者回報：前面的 icon 固定顯示 Scenic 的攝影機圖示，
    沒有依照目前這段的類型切換。改成用既有的 api.get_category()（跟
    Gallery 判斷分類同一個函式，不是自己憑檔名猜測）依 Scenic/Event/
    Photo 切換對應的 icon——同一個 group 底下的每一段理論上都是同一種
    類型，這裡仍然逐段判斷目前這段的類型，segment 切換時 icon 會跟著
    filename 一起同步更新。"""
    if not current_group or not (0 <= current_segment_index < len(current_group)):
        return
    current_segment_item = current_group[current_segment_index]
    filename = api.get_filename_text(current_segment_item)
    video_label.itemconfig('title_text', text=filename)

    category = api.get_category(current_segment_item)
    if category == 'event':
        icon_image = title_event_icon_image
    elif category == 'photo':
        icon_image = title_photo_icon_image
    else:
        icon_image = title_scenic_icon_image
    video_label.itemconfig('title_icon', image=icon_image)


def set_current_recording_group(recording_group):
    """外部（Recording / Gallery 畫面）呼叫這個函式，開始串流播放一整個 group
    （group 是同一個 groupid 底下所有段落的 list，已依 sequenceid 排序）"""
    global current_group, current_segment_index, segment_start_offsets
    global group_total_duration_seconds, current_request_token
    global playback_finished, current_media_type
    global current_request_content_ready, current_recordings_list

    stop_video()
    current_media_type = 'video'
    current_group = recording_group
    current_segment_index = 0
    segment_start_offsets, group_total_duration_seconds = compute_segment_offsets(recording_group)

    # 這個 request 還沒有任何結果，Previous/Next 要等 start_segment()/
    # show_stream_error() 對到新 token 才能重新 enable；current_recordings_list
    # 也先清掉，避免舊清單被拿去算新 item 的鄰居（見 update_previous_next_
    # button_state() 的說明）。
    current_request_content_ready = False
    current_recordings_list = None

    # 2026-08-17：新 group 開始載入，馬上把「播完」旗標清掉（不用等非同步
    # 的 start_segment() 才清）——避免載入新 group、串流還在 Connecting...
    # 的極短暫空檔內，如果使用者剛好操作 Seek，on_seek_end() 誤用上一個
    # group 播完時留下的舊狀態。
    playback_finished = False

    current_request_token += 1
    request_token = current_request_token

    set_play_pause_enabled(False)
    set_previous_button_enabled(False)
    set_next_button_enabled(False)

    # 2026-08-17 使用者要求：左上角改成目前正在播放的 segment 檔名，不是
    # Group ID/Recording Type/segment 數量；這裡先顯示第一段（segment 0）
    # 的檔名，之後每次 start_segment() 真的開始播放新的一段時會再更新一次。
    update_title_for_current_segment()
    video_label.itemconfig('video_frame_item', image='')
    video_label.itemconfig('status_text_item', text='Connecting...', fill='white')
    video_label.itemconfig('progress_label_item', text='')
    reset_seek_bar()
    total_duration_label.config(text=format_time(group_total_duration_seconds))
    progress_scale.config(to=group_total_duration_seconds if group_total_duration_seconds > 0 else 100)

    open_segment(current_segment_index, request_token)
    threading.Thread(target=load_recordings_list_for_navigation, args=(request_token,), daemon=True).start()


def set_current_photo_item(recording_item):
    """外部（Gallery）呼叫這個，開始下載並顯示一張照片——2026-08-19 使用者
    要求 Photo 併入這個 Unified Media Viewer，跟 set_current_recording_group()
    共用同一顆 Canvas/Back/Title/Previous/Next，取代舊的 screen_photo_viewer.py。
    Photo 完全不會碰 FFmpeg/OpenCV Video Decoder、不會呼叫 open_segment()/
    start_decode_thread()——只是把 current_group 設成只有這一筆的 list，
    這樣 update_title_for_current_segment()（依 api.get_category() 判斷
    icon/顯示檔名）跟 find_adjacent_groups()（依 fileid 找鄰居）完全不用
    改就能直接沿用。"""
    global current_group, current_segment_index, segment_start_offsets
    global group_total_duration_seconds, current_request_token
    global playback_finished, current_media_type, current_photo_original_image
    global current_request_content_ready, current_recordings_list

    stop_video()  # 沿用既有的清理流程：停掉還在跑的 Video decode/decoder，並讓舊的非同步下載失效（bump current_request_token）
    current_media_type = 'photo'
    current_group = [recording_item]
    current_segment_index = 0
    segment_start_offsets, group_total_duration_seconds = [0], 0

    # 同 set_current_recording_group()：這個 request 還沒有結果，Previous/
    # Next 要等 _on_photo_downloaded()/_on_photo_download_error() 對到新
    # token 才能重新 enable；current_recordings_list 也先清掉。
    current_request_content_ready = False
    current_recordings_list = None
    playback_finished = False
    current_photo_original_image = None

    current_request_token += 1
    request_token = current_request_token

    set_play_pause_enabled(False)
    set_previous_button_enabled(False)
    set_next_button_enabled(False)

    update_title_for_current_segment()
    video_label.itemconfig('video_frame_item', image='')
    video_label.itemconfig('status_text_item', text='Loading...', fill='white')
    video_label.itemconfig('progress_label_item', text='')
    reset_controls_auto_hide_timer()  # Photo 模式下 should_keep_controls_visible() 回傳 True，這裡會讓 Play/Pause/Seek Bar 正確隱藏、不會排 auto-hide 計時器

    threading.Thread(target=_download_photo_worker, args=(recording_item, request_token), daemon=True).start()
    threading.Thread(target=load_recordings_list_for_navigation, args=(request_token,), daemon=True).start()


def _build_letterboxed_photo_image(original_pil_image):
    """把原始照片等比例縮放到符合 VIDEO_DISPLAY_SIZE，其餘範圍用黑色填滿
    （跟 Video 畫面同樣大小的顯示區域），這樣可以直接沿用 'video_frame_item'
    這個 Canvas item（本來就是整張畫滿 Canvas、錨點固定在 (0,0)），不用
    為了 Photo 的長寬比另外改動它的座標邏輯。"""
    canvas_width, canvas_height = VIDEO_DISPLAY_SIZE
    fitted_image = original_pil_image.copy()
    fitted_image.thumbnail((canvas_width, canvas_height), Image.LANCZOS)
    letterboxed_image = Image.new('RGB', (canvas_width, canvas_height), (0, 0, 0))
    paste_x = (canvas_width - fitted_image.width) // 2
    paste_y = (canvas_height - fitted_image.height) // 2
    letterboxed_image.paste(fitted_image, (paste_x, paste_y))
    return letterboxed_image


def _download_photo_worker(recording_item, request_token):
    try:
        image_bytes = api.download_photo_original(recording_item)
    except Exception as error:
        video_label.after(0, lambda captured_error=error: _on_photo_download_error(recording_item, captured_error, request_token))
        return
    video_label.after(0, lambda: _on_photo_downloaded(image_bytes, request_token))


def _on_photo_downloaded(image_bytes, request_token):
    global current_photo_original_image
    if request_token != current_request_token:
        return  # 使用者可能已經離開 Player，或已經換到下一張/上一張，過期的下載結果不要套用
    try:
        original_image = Image.open(BytesIO(image_bytes)).convert('RGB')
    except Exception as error:
        _on_photo_download_error(None, error, request_token)
        return
    current_photo_original_image = original_image
    photo_image = ImageTk.PhotoImage(_build_letterboxed_photo_image(original_image))
    video_label.itemconfig('video_frame_item', image=photo_image)
    video_label.image = photo_image  # 保留參照避免被 GC 回收，跟 draw_frame() 的做法一致
    video_label.itemconfig('status_text_item', text='')
    _mark_content_ready_and_maybe_enable_buttons(request_token)


def _on_photo_download_error(recording_item, error, request_token):
    if request_token != current_request_token:
        return
    fileid = recording_item.fileid if recording_item is not None else (current_group[0].fileid if current_group else '?')
    print(f'[Player] 下載照片失敗 (fileid={fileid}): {error}')
    video_label.itemconfig('video_frame_item', image='')
    video_label.itemconfig('status_text_item', text='Failed to load photo.', fill='red')
    _mark_content_ready_and_maybe_enable_buttons(request_token)


def compute_segment_offsets(recording_group):
    """依照每段的 duration 欄位（protobuf metadata），算出「每段開始時前面累計的
    秒數」跟「整個 group 的總長度」。這裡完全不用打開影片，純粹算 metadata。"""
    offsets = []
    running_total = 0
    for item in recording_group:
        offsets.append(running_total)
        running_total += item.duration.seconds
    return offsets, running_total


def reset_seek_bar():
    current_time_label.config(text='00:00')
    total_duration_label.config(text='00:00')
    progress_scale.config(to=100)
    progress_scale.set(0)


# ============ 開啟某一段的串流（背景執行緒） ============

def open_segment(segment_index, request_token, seek_to_seconds=None):
    """在背景執行緒打開指定段落的影片網址；seek_to_seconds 有給值的話，
    打開後會先跳到那個時間點再開始播（用在跨段落 seek 的情況）"""
    thread = threading.Thread(
        target=_open_segment_stream, args=(segment_index, request_token, seek_to_seconds), daemon=True)
    thread.start()


def _open_segment_stream(segment_index, request_token, seek_to_seconds):
    """依序嘗試 FFmpeg QSV -> FFmpeg 軟體解碼 -> OpenCV（video_decoder.py
    負責決定用哪一種），seek_to_seconds 有給值的話直接在開啟時帶進去，
    不用開好之後再多一次 seek（FFmpeg backend 的 seek 是關掉重開一次
    subprocess，能省則省）。

    2026-08-20 傳入 is_stale：一旦使用者已經切去別的 item（request_token
    對不上），create_decoder_with_fallback() 會在還沒嘗試的 backend 前
    提早放棄，不會繼續對 DVR 開更多連線（見該函式的說明）。"""
    recording_item = current_group[segment_index]
    url = api.build_video_url(recording_item)
    decoder = video_decoder.create_decoder_with_fallback(
        url, seek_seconds=seek_to_seconds,
        is_stale=lambda: request_token != current_request_token)

    if not decoder.is_open:
        video_label.after(0, lambda: show_stream_error(
            url, Exception('Failed to open video stream (all decoder backends failed)'), request_token))
        return

    video_label.after(0, lambda: start_segment(decoder, segment_index, request_token, seek_to_seconds))


def show_stream_error(url, error, request_token):
    if request_token != current_request_token:
        return
    print(f'[Player Seek/Segment] decoder open FAILED url={url} error={error}')
    video_label.itemconfig('video_frame_item', image='')
    video_label.itemconfig('status_text_item', text='Stream Failed', fill='red')
    video_label.itemconfig('progress_label_item', text='')
    _mark_content_ready_and_maybe_enable_buttons(request_token)
    detail = f'URL: {url}\nException: {error}'
    messagebox.showerror('Playback Failed', detail)


# ============ 播放（某一段的串流打開後） ============

def start_segment(decoder, segment_index, request_token, seek_to_seconds):
    global current_decoder, current_segment_index, is_playing, fps
    global playback_clock_start_wall_time, playback_clock_start_media_seconds, last_shown_media_seconds
    global perf_source_fps, playback_finished

    if request_token != current_request_token:
        # 使用者已經切去播別的 group 了，這個串流過期了，釋放掉就好
        decoder.release()
        return

    _mark_content_ready_and_maybe_enable_buttons(request_token)

    if AUTO_ENABLE_DIAGNOSTIC_LOGGING and not diagnostic_logging_enabled:
        start_diagnostic_logging()  # 自己會一併開啟效能量測，見該函式的說明

    current_decoder = decoder
    current_segment_index = segment_index
    # 2026-08-17 修正「播完後無法再次播放」問題：只要真的有一個新 decoder
    # session 建立起來（不管是一開始播放、中間換段、或播完後 Replay/Seek
    # 重新開啟），就代表已經不是「Finished」狀態了。
    playback_finished = False
    update_title_for_current_segment()  # 切到新的一段，左上角檔名同步更新
    video_label.itemconfig('progress_label_item', text='')
    print(f'[Player Segment Started] segment={segment_index} seek_to_seconds={seek_to_seconds} '
          f'decoder_alive=True request_token={request_token}')

    fps = decoder.get_fps()

    if perf_recording_enabled:
        perf_source_fps = fps

    # decoder 開啟時（create_decoder_with_fallback）已經處理過 seek_to_seconds，
    # 這裡不用再 set 一次
    start_media_seconds = seek_to_seconds if seek_to_seconds is not None else 0

    # 這一段從 start_media_seconds 開始播，此刻的真實時間就是播放時鐘的起點
    playback_clock_start_wall_time = time()
    playback_clock_start_media_seconds = start_media_seconds
    last_shown_media_seconds = start_media_seconds

    is_playing = True
    set_play_pause_enabled(True)  # 這裡 is_playing 已經是 True，圖示會正確顯示 Pause icon
    reset_controls_auto_hide_timer()  # 新 segment/group 開始播放，Controls 顯示一次、重新起算 auto-hide 倒數
    start_decode_thread(decoder, should_run=True)
    show_next_frame()


# ============ decode 背景執行緒：專門負責 read()，UI 執行緒不直接碰 VideoCapture ============

def start_decode_thread(decoder, should_run):
    """建立一組新的佇列/事件，啟動一條新的 decode 背景執行緒讀取 decoder。
    should_run=True 代表建立後立刻開始讀（正常播放、或播放中 seek）；
    should_run=False 代表先建立好但卡在等待，用在「暫停狀態下 seek」——那種
    情況 UI 會自己讀一張畫出來，decode thread 要先卡住，避免兩邊同時讀同一個
    decoder 物件。呼叫這個函式前，如果舊的 decode thread 還在跑同一個
    decoder，要先呼叫 stop_decode_thread() 確認它真的停了。
    decoder 不管是 FFmpeg QSV/SW 還是 OpenCV，都用同一組介面（read()/get_fps()/
    seek()/release()），這裡完全不需要知道背後是哪一種 backend。"""
    global current_frame_queue, current_decode_stop_event, current_decode_run_event
    global current_decode_thread, current_decode_eof_event, decode_start_wall_time
    global decode_frames_read_count, decode_frames_dropped_by_queue_count, max_queue_backlog_observed

    decode_frames_read_count = 0
    decode_frames_dropped_by_queue_count = 0
    max_queue_backlog_observed = 0
    decode_start_wall_time = time()

    current_frame_queue = deque(maxlen=FRAME_QUEUE_MAX_SIZE)
    current_decode_stop_event = threading.Event()
    current_decode_run_event = threading.Event()
    current_decode_eof_event = threading.Event()
    if should_run:
        current_decode_run_event.set()

    current_decode_thread = threading.Thread(
        target=_decode_loop,
        args=(decoder, current_decode_stop_event, current_decode_run_event,
              current_frame_queue, current_decode_eof_event),
        daemon=True)
    current_decode_thread.start()


def stop_decode_thread():
    """通知目前的 decode thread 停下來，並等它真的結束才回傳。要對同一個
    VideoCapture 做任何 seek/release 之前，一定要先呼叫這個，確認 decode
    thread 不會再去碰它，避免兩個執行緒同時存取同一個 decoder 物件。
    正常情況下它最多還在跑一次 read()，等一下就會結束，設 timeout 只是
    保底避免真的卡死時整個程式跟著卡住。"""
    global current_decode_thread
    if current_decode_stop_event is None:
        return
    current_decode_stop_event.set()
    current_decode_run_event.set()  # 如果剛好卡在暫停的 wait()，先喚醒讓它看到 stop_event
    if current_decode_thread is not None:
        current_decode_thread.join(timeout=2)
        current_decode_thread = None


def _decode_loop(decoder, stop_event, run_event, frame_queue, eof_event):
    """背景執行緒：不斷呼叫 decoder.read() 拿畫面，連同這張畫面對應的媒體
    時間一起塞進佇列。佇列滿的時候 deque(maxlen=...) 會自動丟掉最舊的那
    一張，永遠保留最新的，不會累積 backlog。暫停時（run_event 沒被設定）
    會卡在 wait()，不會繼續讀，這樣暫停期間才不會偷偷把影片往前讀走。

    Decode Pacing（2026-08-12 第四階段，換成 FFmpeg QSV 之後才需要）：
    QSV 實測 87~258 FPS，遠超過影片本身的 30fps，如果不節流，會在極短
    時間內把這裡的小 Queue 灌爆好幾輪，畫面在還沒被 UI 看到之前就被覆蓋
    掉（PoC 階段實測過，不節流時 Displayed Frames 只剩 42%）。這裡用
    「目標播放速度」算出下一張理論上該在什麼時間點讀到，最多讓 decode
    領先 FRAME_QUEUE_MIN_BUFFER 張（小幅 ahead buffer），超前太多就先睡，
    不是靠 Queue 滿了自然回壓（那樣等於放任大量 Queue Drop 發生）。
    decode 本身比 fps 慢的情況（目前 OpenCV backend，19~20 FPS）目標時間
    點永遠已經過去，完全不會睡，跟原本行為一致，繼續全速讀、優先追上——
    這個節流只在 decoder 真的比 fps 快時才會生效。"""
    global decode_frames_read_count, decode_frames_dropped_by_queue_count, max_queue_backlog_observed
    global perf_queue_drop_count, last_decoded_media_seconds

    decode_loop_start_wall_time = time()

    while True:
        run_event.wait()
        if stop_event.is_set():
            break

        target_wall_time = decode_loop_start_wall_time + (decode_frames_read_count - FRAME_QUEUE_MIN_BUFFER + 1) / fps
        lead_seconds = target_wall_time - time()
        if lead_seconds > 0:
            sleep(min(lead_seconds, DECODE_PACING_MAX_SLEEP_SECONDS))
            if stop_event.is_set():
                break

        decode_call_start = time()
        has_frame, frame, media_seconds = decoder.read()
        decode_call_elapsed = time() - decode_call_start
        if not has_frame:
            eof_event.set()
            break

        decode_frames_read_count += 1
        last_decoded_media_seconds = media_seconds

        if perf_recording_enabled:
            perf_decode_time_samples.append(decode_call_elapsed)
            perf_decode_timestamps.append(time())

        was_full = len(frame_queue) >= frame_queue.maxlen
        frame_queue.append((frame, media_seconds))
        if was_full:
            decode_frames_dropped_by_queue_count += 1
            if perf_recording_enabled:
                perf_queue_drop_count += 1

        if len(frame_queue) > max_queue_backlog_observed:
            max_queue_backlog_observed = len(frame_queue)


def begin_buffering():
    """暫停 playback clock（不設定新的起點，讓 expected_media_seconds 停在
    原地，不會繼續往前跑），等佇列補到 FRAME_QUEUE_MIN_BUFFER 張才恢復。
    不管是「這一段剛開始播」還是「播放中網路卡頓、佇列被讀空」，都是同一套
    機制——這正是這次要解決的核心問題：decode 落後時，不要讓 playback clock
    照真實時間繼續跑，而是先停下來等 buffer，恢復時再 resync。"""
    global is_buffering, buffering_started_wall_time, current_after_id
    global perf_buffering_count

    if not is_playing:
        return
    if is_buffering:
        return  # 已經在 Buffering 了，不要重複進入、重複計數

    is_buffering = True
    buffering_started_wall_time = time()
    if perf_recording_enabled:
        perf_buffering_count += 1

    current_after_id = video_label.after(QUEUE_EMPTY_RETRY_MS, check_buffering_progress)


def check_buffering_progress():
    """Buffering 期間定期檢查：佇列補到 FRAME_QUEUE_MIN_BUFFER 張、或已經讀到
    影片結尾、或等太久（BUFFERING_TIMEOUT_MS，例如剩下的影片不夠 5 張）
    三種情況都結束 Buffering，恢復播放；否則繼續等。"""
    global current_after_id

    if not is_playing:
        return

    buffer_ready = current_frame_queue is not None and len(current_frame_queue) >= FRAME_QUEUE_MIN_BUFFER
    eof_reached = current_decode_eof_event is not None and current_decode_eof_event.is_set()
    timed_out = (time() - buffering_started_wall_time) * 1000 >= BUFFERING_TIMEOUT_MS

    if buffer_ready or eof_reached or timed_out:
        end_buffering()
    else:
        current_after_id = video_label.after(QUEUE_EMPTY_RETRY_MS, check_buffering_progress)


def end_buffering():
    """結束 Buffering：記錄這次花了多久，然後把 playback clock resync 到
    佇列裡最舊那一張的實際媒體時間（不是繼續沿用 buffering 之前的舊時鐘），
    這樣恢復播放後不會覺得「buffering 那段時間也算落後」而馬上又觸發丟幀。
    如果等到逾時佇列還是空的、也還沒到 EOF，維持原本時鐘不變，直接讓
    show_next_frame() 走它自己的空佇列處理（再等一輪，不會誤判成播完）。"""
    global is_buffering
    global perf_total_buffering_seconds, perf_max_buffering_seconds

    buffering_elapsed = time() - buffering_started_wall_time
    if perf_recording_enabled:
        perf_total_buffering_seconds += buffering_elapsed
        if buffering_elapsed > perf_max_buffering_seconds:
            perf_max_buffering_seconds = buffering_elapsed

    is_buffering = False

    if current_frame_queue:
        _, resume_media_seconds = current_frame_queue[0]
        resync_playback_clock(resume_media_seconds)

    show_next_frame()


def resync_playback_clock(media_seconds):
    """把 playback clock 重新對齊到「現在真的播到這裡」，不要再沿用舊的
    起點繼續往前跑。這是這次要解決的核心問題：decode 落後太多時，不是
    繼續無限丟 stale frame，是把時鐘拉回來跟 decode 的實際進度對齊，讓
    後面重新開始正常配速——跟 on_seek_end()/on_play_pause_click() 對齊時鐘
    的做法是同一個概念，只是這裡是「因為落後太多」觸發，不是使用者操作。"""
    global playback_clock_start_wall_time, playback_clock_start_media_seconds
    global perf_resync_count

    playback_clock_start_wall_time = time()
    playback_clock_start_media_seconds = media_seconds
    if perf_recording_enabled:
        perf_resync_count += 1


def show_next_frame():
    """UI 執行緒（Tkinter 主執行緒）完全不呼叫 read()，只從 decode thread
    塞好的佇列裡拿畫面：
    - 目標：現在真實時間對應到播放時鐘上「應該播到第幾秒」
    - 落後分級處理（2026-08-12 調整，取代原本「落後超過一幀就無限丟」的
      做法）：
        - 落後在 FRAME_LAG_NORMAL_MS 以內：正常播放，不丟。
        - 落後在 FRAME_LAG_NORMAL_MS ~ FRAME_LAG_RESYNC_THRESHOLD_MS 之間：
          允許丟掉這張追時間軸，但同一輪最多連續丟 MAX_CONSECUTIVE_LAG_DROPS
          張，不會無限丟。
        - 落後超過 FRAME_LAG_RESYNC_THRESHOLD_MS，或已經連續丟了
          MAX_CONSECUTIVE_LAG_DROPS 張：不要再丟，直接顯示手上這張，並把
          playback clock resync 到它的實際時間——避免「網路卡一下，後面
          幾十秒都在丟 stale frame」的連鎖反應。
    - 佇列目前是空的（decode 還沒補上新畫面）不代表播完了，進入 Buffering
      等佇列補到 FRAME_QUEUE_MIN_BUFFER 張再恢復，不會誤判成播完；真的讀到
      影片結尾是 decode thread 那邊會設定 eof_event。
    - 排程下一次呼叫的時間，用「下一幀應該出現的真實時間」跟「現在」的差，
      不是每次都固定等 1000/fps，這樣才不會因為這一輪處理花了多久而讓
      整體播放速度變慢。"""
    global current_after_id, ui_frames_skipped_stale_count, last_shown_media_seconds
    global perf_dropped_by_lag_count, perf_displayed_frame_count, last_frame_rendered_wall_time

    if not is_playing:
        return

    frame_to_draw = None
    frame_media_seconds = None
    consecutive_lag_drops = 0

    while current_frame_queue:
        frame, media_seconds = current_frame_queue.popleft()
        expected_media_seconds = playback_clock_start_media_seconds + (time() - playback_clock_start_wall_time)
        lag_ms = (expected_media_seconds - media_seconds) * 1000

        if lag_ms <= FRAME_LAG_NORMAL_MS:
            # 沒有明顯落後（或甚至還沒到這張的時間），直接播放
            frame_to_draw = frame
            frame_media_seconds = media_seconds
            break

        if lag_ms <= FRAME_LAG_RESYNC_THRESHOLD_MS and consecutive_lag_drops < MAX_CONSECUTIVE_LAG_DROPS:
            # 落後不算太離譜，而且這輪還沒連續丟太多張，丟這張追時間軸
            ui_frames_skipped_stale_count += 1
            consecutive_lag_drops += 1
            if perf_recording_enabled:
                perf_dropped_by_lag_count += 1
            continue

        # 落後太多，或已經連續丟了太多張：不要再丟，顯示手上這張，
        # 並把 playback clock resync 到它的實際時間
        frame_to_draw = frame
        frame_media_seconds = media_seconds
        resync_playback_clock(media_seconds)
        break

    if frame_to_draw is None:
        if current_decode_eof_event is not None and current_decode_eof_event.is_set():
            advance_to_next_segment()
        else:
            # decode 還沒補上新畫面（佇列暫時是空的），進入 Buffering 等補足，
            # 不要讓 playback clock 繼續往前跑、也不要誤判成播完
            begin_buffering()
        return

    render_call_start = time()
    draw_frame(frame_to_draw)
    render_call_elapsed = time() - render_call_start
    if perf_recording_enabled:
        perf_render_time_samples.append(render_call_elapsed)
        perf_frame_display_wall_times.append(time())
        perf_displayed_frame_count += 1
    if diagnostic_logging_enabled:
        last_frame_rendered_wall_time = time()

    last_shown_media_seconds = frame_media_seconds
    update_seek_position(frame_media_seconds)

    next_frame_media_seconds = frame_media_seconds + (1 / fps)
    next_frame_wall_time = playback_clock_start_wall_time + (next_frame_media_seconds - playback_clock_start_media_seconds)
    delay_ms = max(0, int((next_frame_wall_time - time()) * 1000))
    current_after_id = video_label.after(delay_ms, show_next_frame)


def advance_to_next_segment():
    """這一段播完了，接著播下一段（如果有的話），讓使用者感覺是接續播放同一部影片；
    如果已經是最後一段，才算整個 group 播完。
    decode thread 讀到 has_frame=False 不代表一定是「正常播完」，也可能是
    串流中途斷掉——這裡比對 decode 實際讀到的媒體時間跟這一段 metadata
    記錄的 duration，差太多（低於 STREAM_COMPLETE_RATIO_THRESHOLD）就記一次
    Stream Error，量測時不能把這種情況當成正常播放完成。"""
    global current_decoder
    global perf_stream_error_count

    if perf_recording_enabled and current_group and 0 <= current_segment_index < len(current_group):
        expected_duration_seconds = current_group[current_segment_index].duration.seconds
        if expected_duration_seconds > 0:
            completion_ratio = last_decoded_media_seconds / expected_duration_seconds
            if completion_ratio < STREAM_COMPLETE_RATIO_THRESHOLD:
                perf_stream_error_count += 1
                print(f'[Player Perf] Stream Error / Invalid Test：這段只播到 '
                      f'{last_decoded_media_seconds:.1f}s / {expected_duration_seconds}s'
                      f'（{completion_ratio * 100:.0f}%），疑似串流中斷，不是正常播完')

    stop_decode_thread()  # 確認 decode thread 真的停了，才能安全 release() 這個 decoder
    if current_decoder is not None:
        current_decoder.release()
        current_decoder = None

    next_index = current_segment_index + 1
    if next_index < len(current_group):
        open_segment(next_index, current_request_token)
    else:
        stop_playback_at_end()


def draw_frame(frame_rgb):
    """decoder.read() 回傳的畫面已經是縮小到 VIDEO_DISPLAY_SIZE、色彩是
    RGB 的 ndarray（FFmpeg 路線在 GPU/FFmpeg 端就處理好；OpenCV 路線在
    OpenCVDecoder.read() 裡處理，沿用 2026-08-11 實測過的 cv2.resize()
    先縮小再轉色彩的做法，只是搬進 video_decoder.py），這裡不用再自己
    resize/轉色彩，三種 backend 出來的畫面格式完全一致。"""
    image = Image.fromarray(frame_rgb)
    photo_image = ImageTk.PhotoImage(image)

    video_label.itemconfig('video_frame_item', image=photo_image)
    video_label.itemconfig('status_text_item', text='')  # 真的開始畫影格了，清掉 Connecting... 這類狀態文字
    video_label.image = photo_image  # 保留參照，避免圖片被 Tkinter 回收（Canvas 一樣可以附加任意屬性）


def update_seek_position(segment_elapsed_seconds):
    """播放中，把「目前這段的累積起點 + 這段播到第幾秒」同步到進度條跟時間文字上，
    這個相加後的數字才是整個 group 的累計時間（跟使用者感覺到的長度一致）。
    segment_elapsed_seconds 現在是從佇列裡的畫面資料直接拿到的（decode thread
    讀取 decoder.read() 時一起拿到的媒體時間），不能再讓 UI 執行緒自己去
    呼叫 decoder 查目前播到第幾秒——read() 現在都在背景 decode thread 裡
    做，UI 執行緒不該再直接碰這個 decoder 物件（兩個執行緒同時存取同一個
    decoder 不安全，不管背後是 OpenCV 還是 FFmpeg subprocess）。"""
    if user_is_seeking:
        return  # 使用者正在拖曳進度條，不要去跟他打架

    global_time_seconds = segment_start_offsets[current_segment_index] + segment_elapsed_seconds
    progress_scale.set(global_time_seconds)
    current_time_label.config(text=format_time(global_time_seconds))


def format_time(total_seconds):
    total_seconds = int(total_seconds)
    minutes = total_seconds // 60
    seconds = total_seconds % 60
    return f'{minutes:02d}:{seconds:02d}'


def stop_playback_at_end():
    """整個 group 都播完了（不是使用者按暫停），停在最後一張畫面就好。
    2026-08-17 使用者要求：播完後 Controls 要顯示出來、Play 圖示顯示、
    不再 auto-hide，讓使用者可以 Replay/Previous/Next/Back——
    is_playing 設成 False 之後呼叫 reset_controls_auto_hide_timer()，
    should_keep_controls_visible() 會判斷目前不是在播放，只顯示不排新的
    隱藏計時器，效果就是這裡要的。

    2026-08-17 修正「播完後無法再次播放」問題：這裡是唯一「整個 group
    真的自然播完」的地方（中間 segment 自然切換到下一段是
    advance_to_next_segment() 直接呼叫 open_segment()，不會走到這裡，
    不會被誤判成 Finished）。設定 playback_finished=True，讓
    on_play_pause_click()/on_seek_end() 知道現在 current_decoder 是 None
    是因為「真的播完了」，可以重新建立 decoder，不是還在連線中。

    2026-08-18 使用者要求：播完時 current time 應該跟 total duration
    顯示一致（例如 00:09 / 00:09），Progress Bar 停在最右側。最後一張
    畫面實際的媒體時間（frame_index / fps 算出來的）天生會比宣告的整數秒
    duration 略短（例如 60 張 30fps 的畫面涵蓋 0~59/30=1.967 秒，不是剛好
    2 秒），如果只是照實顯示最後一張畫面的時間，current time 顯示會比
    total duration 少 1 秒、兩者對不齊。這裡播完的當下直接把顯示對齊到
    group 總長度，不是另外發明一套時間換算。"""
    global is_playing, playback_finished
    is_playing = False
    playback_finished = True
    current_time_label.config(text=format_time(group_total_duration_seconds))
    progress_scale.set(group_total_duration_seconds)
    update_play_pause_icon()  # 沿用既有的圖示切換，播完顯示 Replay icon
    reset_controls_auto_hide_timer()

    print(f'[Player End] playback_finished={playback_finished} is_playing={is_playing} '
          f'current_time={last_shown_media_seconds:.2f} current_segment={current_segment_index} '
          f'decoder_alive={current_decoder is not None}')


# ============ Seek：拖曳進度條（可能跨段落） ============

def on_seek_start(event):
    """2026-08-17 修正「播完後 Seek 回前面仍無法再次播放」問題的一部分：
    原本 current_decoder is None 就直接 return，但整個 group 播完
    （Finished）之後 current_decoder 正常就是 None，導致使用者在
    Progress Bar 上按下去的當下就被這裡擋掉——user_is_seeking 永遠不會
    變成 True，後面 on_seek_end() 也會被同一種 guard 擋掉，Seek 完全沒有
    作用（進度條滑塊看起來滑得動，是 tk.Scale 元件自己原生的視覺效果，
    跟這裡的邏輯無關，實際上什麼都沒發生）。改成：只有「真的還沒有任何
    decoder、也不是播完狀態」（例如串流還在 Connecting...）才擋掉；
    Finished 狀態（playback_finished=True）允許使用者開始 Seek。"""
    global user_is_seeking, was_playing_before_seek
    if current_decoder is None and not playback_finished:
        return
    user_is_seeking = True
    was_playing_before_seek = is_playing
    reset_controls_auto_hide_timer()  # 2026-08-17 使用者要求：操作 Seek 也要顯示 Controls + 重設倒數


def on_progress_scale_moved(value):
    """拖曳中即時更新時間文字，但還不去真的 seek 影片（放開滑鼠才做，比較不卡）"""
    if not user_is_seeking:
        return
    current_time_label.config(text=format_time(float(value)))


def on_seek_end(event):
    global user_is_seeking, is_playing, playback_clock_start_wall_time, playback_clock_start_media_seconds
    global last_shown_media_seconds, current_decoder, current_segment_index
    global playback_finished, seek_request_counter

    if not current_group:
        user_is_seeking = False
        return

    if current_decoder is None and not playback_finished:
        # 還沒有任何 decoder，而且不是「已經播完」的狀態（例如整個 group
        # 才剛觸發 set_current_recording_group()，串流還在 Connecting...），
        # 這種情況跟原本行為一致：先不處理，等連線完成再讓使用者操作。
        user_is_seeking = False
        return

    target_global_seconds = float(progress_scale.get())
    target_segment_index, target_local_seconds = find_segment_for_time(target_global_seconds)

    seek_request_counter += 1
    print(f'[Player Seek] request_id={seek_request_counter} target_time={target_global_seconds:.2f} '
          f'target_segment={target_segment_index} target_offset={target_local_seconds:.2f} '
          f'caller=progress_bar_release timestamp={time():.3f} '
          f'decoder_alive_before={current_decoder is not None}')

    user_is_seeking = False

    if current_decoder is None:
        # 2026-08-17 第二次調整：使用者要求「Finished 狀態下 Seek＝自動從
        # Seek 位置開始播放」，不需要使用者再按一次 Play——這是 Finished
        # 狀態下 Seek 的唯一特例規則（一般播放中/暫停中 Seek 完維持原本
        # is_playing 狀態不變，這條不影響那兩種，見下面 else 分支）。
        #
        # 沒有任何 decoder 在跑，不能真的在原地 seek 一個不存在的串流，
        # 一定要重新開一個 decoder session——這裡跟現有「播放中跨 segment
        # Seek」用同一個慣例：is_playing/icon/auto-hide 先樂觀更新成
        # 「播放中」，實際 decoder 是非同步建立的（open_segment() 打開
        # 網址需要時間），跟目前程式碼一致的做法，不是另外發明一套。
        # playback_finished 在這裡就同步清成 False（不用等 start_segment()
        # 才清）：避免在這段短暫的 Connecting... 空檔內，如果使用者又點了
        # 一次中央 Play，on_play_pause_click() 誤判成「還是 Finished 狀態，
        # 要從 00:00 Replay」，變成跟這裡的 open_segment() 同時搶著開兩個
        # decoder。
        current_segment_index = target_segment_index
        last_shown_media_seconds = target_local_seconds
        playback_finished = False
        is_playing = True
        update_play_pause_icon()
        reset_controls_auto_hide_timer()
        update_title_for_current_segment()  # 可能跳到別的 segment，icon/檔名要同步更新
        update_seek_position(target_local_seconds)
        print(f'[Player Seek After End] target_time={target_global_seconds:.2f} '
              f'target_segment={target_segment_index} target_offset={target_local_seconds:.2f} '
              f'auto_play=True decoder_alive_before=False')
        video_label.itemconfig('status_text_item', text='Connecting...', fill='white')
        open_segment(target_segment_index, current_request_token, seek_to_seconds=target_local_seconds)
        return

    is_playing = was_playing_before_seek
    update_play_pause_icon()  # is_playing 可能因為 Seek 前後不同，圖示要跟著同步
    reset_controls_auto_hide_timer()  # 2026-08-17 使用者要求：Seek 完成也要顯示 Controls + 重設倒數

    if target_segment_index == current_segment_index:
        # 還在同一段裡面，直接在目前這個 decoder 上跳就好，不用重開網址。
        # 先確認舊的 decode thread 真的停了，才能安全去動 decoder 的
        # 播放位置——不然 UI 執行緒在這裡 seek() 的同時，decode thread
        # 可能還在另一個時間點呼叫 read()，兩邊會打架。
        # 2026-08-17（問題 3 診斷）：先留一份舊 thread 物件的參照，
        # stop_decode_thread() 執行完之後不管 join() 有沒有真的等到它結束，
        # 都會把 current_decode_thread 設回 None——這裡用留存的參照檢查
        # is_alive()，才能知道 join(timeout=2) 逾時後，舊執行緒是不是還
        # 真的活著（還卡在某次 decoder.read() 裡）。純粹診斷用，不改變
        # stop_decode_thread() 本身的行為。
        old_thread_ref = current_decode_thread
        stop_decode_thread()
        old_thread_actually_stopped = old_thread_ref is None or not old_thread_ref.is_alive()
        print(f'[Player Seek] same-segment: old_decode_thread_actually_stopped={old_thread_actually_stopped}')

        current_decoder.seek(target_local_seconds)
        print(f'[Player Seek] same-segment: decoder.seek() returned, decoder.is_open={current_decoder.is_open}')
        last_shown_media_seconds = target_local_seconds

        if is_playing:
            # seek 完位置變了，播放時鐘也要重新對齊到新的位置，不然會沿用
            # seek 之前的舊起點去算「應該播到第幾秒」，變成誤判落後/超前。
            playback_clock_start_wall_time = time()
            playback_clock_start_media_seconds = target_local_seconds
            start_decode_thread(current_decoder, should_run=True)
            show_next_frame()
        else:
            # 暫停狀態下 seek：decode thread 先建好但卡在等待（should_run=False），
            # UI 這裡自己讀一張畫出來就好，跟原本邏輯一致
            start_decode_thread(current_decoder, should_run=False)
            has_frame, frame, media_seconds = current_decoder.read()
            print(f'[Player Seek] same-segment (paused): first frame after seek received={has_frame}')
            if has_frame:
                draw_frame(frame)
                last_shown_media_seconds = media_seconds
                update_seek_position(media_seconds)
    else:
        # 跳到別的段落，要重新開那一段的串流，開好之後再 seek 到指定位置。
        # 先停掉舊段落的 decode thread 並釋放舊的 decoder，不然背景
        # 執行緒會一直在讀一個已經沒人要看的舊串流，資源永遠不會釋放
        # （FFmpeg backend 的話也不會留下 orphan process）。
        old_thread_ref = current_decode_thread
        stop_decode_thread()
        old_thread_actually_stopped = old_thread_ref is None or not old_thread_ref.is_alive()
        print(f'[Player Seek] cross-segment: old_decode_thread_actually_stopped={old_thread_actually_stopped}')
        if current_decoder is not None:
            current_decoder.release()
            current_decoder = None
        open_segment(target_segment_index, current_request_token, seek_to_seconds=target_local_seconds)


def find_segment_for_time(target_global_seconds):
    """算出某個「整個 group 累計」的時間點，落在第幾段、段內第幾秒"""
    for index in range(len(current_group) - 1, -1, -1):
        if target_global_seconds >= segment_start_offsets[index]:
            local_seconds = target_global_seconds - segment_start_offsets[index]
            return index, local_seconds
    return 0, 0


# ============ 播放/暫停 ============

def on_play_pause_click():
    """2026-08-17 第二次調整：Finished 狀態下如果使用者是「先 Seek 再按
    Play」，Seek 當下（on_seek_end() 的 Finished 分支）就已經自動觸發
    播放、清掉 playback_finished 了，不會留到這裡才處理——這裡的
    current_decoder is None 分支現在只剩一種情境：使用者播完後完全沒有
    Seek，直接按 Play，從整個 group 00:00 重新開始（Replay）。"""
    global is_playing, playback_clock_start_wall_time, playback_clock_start_media_seconds
    global current_segment_index

    action = 'toggle'
    if current_decoder is None:
        action = 'resume_from_finish_replay' if playback_finished else 'no_op_not_ready'

    print(f'[Player Play Click] playback_finished={playback_finished} is_playing={is_playing} '
          f'current_time={last_shown_media_seconds:.2f} current_segment={current_segment_index} '
          f'decoder_alive={current_decoder is not None} action={action}')

    if current_decoder is None:
        if not playback_finished:
            return  # 還沒有任何 decoder 可以操作（例如還在 Connecting...），維持原本行為

        # 2026-08-17 修正「播完後無法再次播放」問題的核心：整個 group 已經
        # 播完，沒有任何 decoder 在跑了，原本「is_playing = not is_playing」
        # 加上「叫 decode thread 繼續讀」這套 resume 邏輯是對著一個已經不
        # 存在的 decoder 下指令，完全無效（decode thread/decoder 都已經
        # release 掉了）。改成用既有的 open_segment()（跟開新 group/跨段落
        # seek 同一條路徑）重新建立一個新的 decoder session，從整個 group
        # 00:00 開始（Replay）。is_playing 會在新的 start_segment() 真的
        # 開始播放時設回 True（跟目前開新 group/切段落的既有行為一致，
        # 這裡不用提前設定）。
        #
        # 2026-08-18 修正「Replay 後 Total Duration 變成 00:09/00:00」的
        # bug：這裡原本呼叫 reset_seek_bar()，但那支函式連
        # total_duration_label/progress_scale 的 to=（總長度上限）都會
        # 一起重設成 00:00/100——Replay 播的是同一個 group，總長度
        # （group_total_duration_seconds）根本沒有變，不應該被清掉，而且
        # 清掉之後也沒有任何後續動作會把它復原（start_segment() 不會重設
        # 這兩個），所以畫面才會一直卡在錯誤的 00:00 總長度。這裡只重設
        # 「目前播放位置」（current_time_label/progress_scale 的目前值)，
        # 完全不動總長度顯示跟 Scale 的範圍上限。
        current_segment_index = 0
        update_title_for_current_segment()
        current_time_label.config(text='00:00')
        progress_scale.set(0)
        video_label.itemconfig('status_text_item', text='Connecting...', fill='white')
        open_segment(0, current_request_token, seek_to_seconds=None)
        return

    is_playing = not is_playing
    update_play_pause_icon()

    if is_playing:
        # 從暫停恢復播放，播放時鐘要用「現在真實時間」跟「目前實際停在第幾秒」
        # 重新對齊，不然時鐘會以為時間軸還停在暫停前的舊起點，恢復播放後
        # 反而會誤判成「落後很多」而一路丟幀。用 last_shown_media_seconds
        # （最後一次實際畫出來的畫面時間），不能再叫 UI 執行緒自己去讀
        # current_decoder 的位置——那個物件現在是 decode thread 在管的。
        playback_clock_start_wall_time = time()
        playback_clock_start_media_seconds = last_shown_media_seconds
        if current_decode_run_event is not None:
            current_decode_run_event.set()  # 讓 decode thread 繼續讀
        show_next_frame()
    else:
        if current_decode_run_event is not None:
            current_decode_run_event.clear()  # 暫停：decode thread 停在原地等，不會偷偷往前讀


# ============ 上一組 / 下一組 ============

def load_recordings_list_for_navigation(request_token):
    """背景執行緒：抓一次完整清單，用來判斷上一組/下一組是誰"""
    try:
        recordings = api.get_recordings()
    except Exception as error:
        print(f'取得清單失敗，上一組/下一組按鈕先停用: {error}')
        recordings = None

    video_label.after(0, lambda: on_recordings_list_loaded(recordings, request_token))


def on_recordings_list_loaded(recordings, request_token):
    global current_recordings_list
    if request_token != current_request_token:
        return
    current_recordings_list = recordings
    update_previous_next_button_state()


def _mark_content_ready_and_maybe_enable_buttons(request_token):
    """2026-08-20 修 Player 快速連點 Next/Previous 造成 DVR 503 的第一階段：
    start_segment()/show_stream_error()（Video）跟 _on_photo_downloaded()/
    _on_photo_download_error()（Photo）對到目前 request_token 有結果時都會
    呼叫這裡，標記「目前這個 item 的內容切換已經處理完成」，讓 Previous/
    Next 有機會重新 enable。stale request 的 callback（token 不符）一律
    直接 return，不會動到目前 request 的按鈕狀態——不能因為一個舊 request
    終於跑完，就誤把使用者當下正在等待的新 request 的按鈕打開或關掉。"""
    global current_request_content_ready
    if request_token != current_request_token:
        return
    current_request_content_ready = True
    update_previous_next_button_state()


def update_previous_next_button_state():
    """Previous/Next 只有在「目前 request 的內容切換已完成」（video 開好/
    開失敗，或 photo 下載成功/失敗）跟「導覽清單已載入」兩個條件都成立時
    才會真的打開；任一邊還沒到，就維持目前的 disabled 狀態，不會提早開放
    使用者再點下一次。這是這支函式的兩個呼叫端（on_recordings_list_
    loaded() 清單載入完成、_mark_content_ready_and_maybe_enable_buttons()
    內容切換完成）共用的同一個判斷，不是各自決定一半。"""
    if not current_request_content_ready:
        return
    previous_group, next_group = find_adjacent_groups()
    set_previous_button_enabled(previous_group is not None)
    set_next_button_enabled(next_group is not None)


def find_adjacent_groups():
    """回傳 (前一項, 後一項)，找不到就回傳 (None, None)。

    2026-08-19 使用者要求 Photo 併入 Unified Media Viewer 之後，
    Previous/Next 不能再只找「其他影片」——要用跟 Gallery 完全一樣的排序
    依據：api.group_recordings_for_gallery()（Trigger Time DESC，Video
    用既有的 group_recordings_by_groupid() 分段規則，Photo 每筆各自獨立
    成一個單項的「group」），不能用 groupid 排序（Photo 的 groupid 不保證
    唯一，會找錯鄰居）。原本用 fileid 當識別身分比對。

    2026-08-20 真機三層診斷證實 /dashcam/recordings 原始資料本身就有重複
    fileid（含 Photo/Video 共用同一個 fileid 的情況，見 api.compute_
    item_key() 附近的說明），直接用 fileid 比對 representative_fileids.
    index(current_fileid) 在這種情況下可能找到錯誤的鄰居（同一個 fileid
    對到兩個不同的 group，index() 只會回傳先出現的那個），改用
    api.compute_item_key()（Video 用 groupid、Photo 用 srcUri）取代單純
    比對 fileid，Gallery 那邊的 item_key 設計同步套用在這裡，兩處對「同一
    個 group 該用什麼識別碼」保持一致。"""
    if current_recordings_list is None or not current_group:
        return None, None

    groups_sorted = api.group_recordings_for_gallery(current_recordings_list)
    representative_item_keys = [api.compute_item_key(group) for group in groups_sorted]

    current_item_key = api.compute_item_key(current_group)
    try:
        index = representative_item_keys.index(current_item_key)
    except ValueError:
        return None, None

    previous_group = groups_sorted[index - 1] if index > 0 else None
    next_group = groups_sorted[index + 1] if index < len(groups_sorted) - 1 else None
    return previous_group, next_group


def _navigate_to_group(group_items):
    """依目標項目的類型決定要進 Video 模式還是 Photo 模式——同一個 Unified
    Media Viewer，只是根據 api.get_category()／api.is_photo_recording()
    判斷的類型切換要顯示哪些 Controls（見 set_current_recording_group()/
    set_current_photo_item()），不是切換到不同的畫面。"""
    representative_item = api.get_group_representative(group_items)
    if api.is_photo_recording(representative_item):
        set_current_photo_item(representative_item)
    else:
        set_current_recording_group(group_items)


def on_previous_button_click():
    previous_group, _ = find_adjacent_groups()
    if previous_group is not None:
        _navigate_to_group(previous_group)


def on_next_button_click():
    _, next_group = find_adjacent_groups()
    if next_group is not None:
        _navigate_to_group(next_group)


# ============ 停止 / 清理 ============

def stop_video():
    """停止播放並釋放影片串流資源；切換到別的畫面或選別的 group 前都要先呼叫這個。
    decoder.release() 對 FFmpeg backend 來說會 terminate/kill 底層的
    ffmpeg subprocess，不會留下 orphan process。

    2026-08-18 修正「切換畫面看到殘影」問題的一部分：開啟一段影片串流是
    非同步的（_open_segment_stream() 在背景執行緒跑，可能因為網路卡頓
    等到 OPEN_PROBE_TIMEOUT_SECONDS=8 秒逾時前都還沒回來），如果使用者
    在等待期間就按 Back 離開 Player（或很快切到別的 group），原本只有
    set_current_recording_group() 會遞增 current_request_token，
    stop_video()（leave_player() 最終會呼叫到這裡）本身不會——這代表
    「使用者已經離開/停止播放」跟「還在背景嘗試打開串流的舊 request」
    之間沒有明確切開，那個很晚才回來的 _open_segment_stream() callback
    (start_segment()) 檢查 request_token 時仍然會相符，因而真的把
    current_decoder/current_frame_queue 等模組全域狀態設成它自己的，
    蓋掉使用者離開後可能已經開始的下一個 session，也會在畫面已經切走、
    看不到 Player 的情況下才畫一張新影格到 Canvas 上。這裡讓
    current_request_token 一併遞增，離開/停止的當下就讓任何還在背景跑的
    舊 open 動作直接失效（沿用既有的 token 比對機制，不是另外發明一套）。
    同時把 Canvas 上的影格圖片清空，避免下次很快回到 Player 時，在新的
    畫面真的顯示出來之前，短暫看到上一次播放停在的最後一張畫面。"""
    global current_decoder, current_after_id, is_playing, is_buffering
    global controls_auto_hide_after_id, current_request_token

    is_playing = False
    is_buffering = False  # 避免殘留的 Buffering 狀態影響下一次播放（begin_buffering() 靠這個旗標擋重複進入）
    current_request_token += 1  # 讓任何還在背景嘗試打開串流的舊 request 直接失效
    if current_after_id is not None:
        video_label.after_cancel(current_after_id)
        current_after_id = None
    # 2026-08-17 使用者要求的 Controls auto-hide 計時器也要一併取消，不然
    # 離開/切換播放內容後，這個舊的 after() 還是會在背景跑，時間到了去
    # 隱藏一個已經不是使用者在看的畫面（雖然無害，但屬於沒清乾淨的殘留）。
    # 順便讓 Controls 回到顯示狀態，下次開始播放才是乾淨的起始畫面。
    if controls_auto_hide_after_id is not None:
        video_label.after_cancel(controls_auto_hide_after_id)
        controls_auto_hide_after_id = None
    show_player_controls()
    stop_decode_thread()  # 確認 decode thread 真的停了，才能安全 release() 這個 decoder
    if current_decoder is not None:
        current_decoder.release()
        current_decoder = None
    video_label.itemconfig('video_frame_item', image='')


leaving_player_busy = False  # 避免同時有兩個離開 Player 的 cleanup 流程並行


def cancel_pending_work_for_disconnect():
    """DVR 斷線時呼叫（不論目前是不是在 Player/Photo 畫面，兩者共用同一套
    處理，見模組開頭的說明）：只做本地清理（停止播放/下載、釋放 decoder、
    讓所有還在背景跑的舊 request 失效，都是 stop_video() 已經做的事），
    不像 leave_player() 那樣呼叫 api.restore_desired_dvr_mode()——那是一個
    網路請求，斷線時呼叫只會卡住/失敗且沒有意義，重新連線後
    refresh_recording_page() 本來就會重新 GET 真實狀態，不需要靠這裡
    「恢復」。跟正常的 leave_player() 是兩條不同的離開路徑，這條專門給
    斷線用，呼叫端（DA005ControlPanel.pyw）會在這之後直接切回 Recording。"""
    global leaving_player_busy
    stop_video()
    leaving_player_busy = False  # 以防斷線發生在正常 leave_player() 流程進行中，避免這個旗標卡死之後的正常操作


def leave_player(on_leave_complete):
    """真的離開 Player 畫面（不是 set_current_recording_group() 內部拿來
    重設狀態用的那次 stop_video() 呼叫）時呼叫這個——在 Player 內部
    Play/Pause/Resume/Previous/Next/切換不同 Group 全程都維持目前的
    actual dvr_mode（通常是暫時的 STANDBY），不需要每支影片之間反覆
    Restore；只有真的離開時才要把 desired_dvr_mode 恢復回來一次。

    Back 按鈕（on_back_button_click()）跟 DA005ControlPanel.pyw 的 Top
    Navigation（Recording/Gallery/Favorite/Settings，見那邊的
    leave_player_and_then()）離開 Player 都要共用這一個，不要各自兜一份
    cleanup 邏輯——2026-08-14 發現 Top Navigation 原本繞過這個流程直接
    切畫面，導致 DVR 還停在暫時的 STANDBY、desired_dvr_mode 沒有被恢復。

    恢復流程（api.restore_desired_dvr_mode()）本身是網路操作，用
    leaving_player_busy 擋掉重複呼叫（例如使用者連點 Back，或者按了 Back
    同時又點了 Nav Tab）；流程跑完（不管成功或失敗）才真正呼叫
    stop_video() + on_leave_complete() 離開畫面——失敗也要讓使用者離開
    得掉，不能卡在 Player 畫面裡，只是要跳出明確的錯誤訊息。"""
    global leaving_player_busy
    if leaving_player_busy:
        return
    leaving_player_busy = True
    set_back_button_enabled(False)
    video_label.itemconfig('progress_label_item', text='Restoring DVR mode...')
    thread = threading.Thread(target=_leave_player_worker, args=(on_leave_complete,), daemon=True)
    thread.start()


def _leave_player_worker(on_leave_complete):
    try:
        restored = api.restore_desired_dvr_mode()
    except Exception as error:
        restored = False
        print(f'[Player] 離開時恢復 DVR mode 發生例外: {error}')
    video_label.after(0, lambda: _finish_leaving_player(restored, on_leave_complete))


def _finish_leaving_player(restored, on_leave_complete):
    global leaving_player_busy
    if diagnostic_logging_enabled:
        stop_diagnostic_logging()
    stop_video()
    set_back_button_enabled(True)
    leaving_player_busy = False
    if not restored:
        messagebox.showerror('Error', 'Failed to restore DVR mode after leaving the player.')
    if on_leave_complete:
        on_leave_complete()


def on_back_button_click():
    leave_player(on_back_callback)
