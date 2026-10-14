"""
mac_uvc_check.py — macOS 上 Dashcam UVC（AVFoundation）的獨立驗證腳本（不屬於正式工具）

目的：在改正式程式（video_source.py 的 FFmpegUVCVideoSource）之前，先確認
macOS + 專案內附的 runtime/ffmpeg/ffmpeg 能不能透過 AVFoundation 拿到 Dashcam
的 UVC 畫面。這支檔案不修改任何正式程式。

使用方式（在專案根目錄、Terminal 執行）：
    python3 mac_uvc_check.py                      自動找 Dashcam（排除 FaceTime / 螢幕擷取等內建裝置）
    python3 mac_uvc_check.py --device 1           指定 AVFoundation video device index
    python3 mac_uvc_check.py --open-by name       用裝置名稱開啟（"USB2 NCM:none"），預設用 index
    python3 mac_uvc_check.py --gap 1.0            reopen 測試每次 stop 後等幾秒（預設 1.0）
    python3 mac_uvc_check.py --cycles 10          reopen 次數（預設 10）
    python3 mac_uvc_check.py --stop-mode drain-term  主要 reopen 測試用的停止方式（預設 drain-term）
    python3 mac_uvc_check.py --compare-cycles 3   其他停止方式各跑幾次做比較（預設 3，0 = 不比較）

2026-10-13 第三版：第二版發現每次 stop 都要等 terminate 2 秒後 kill。原因是收完
畫面後腳本就不再讀 stdout，ffmpeg 卡在 write()（一張 rgb24 畫面 2.7MB，pipe
緩衝只有約 64KB），收到 SIGTERM 只會設「要結束」旗標，卻一直回不到主迴圈去
檢查。Windows 的 terminate() 是 TerminateProcess（直接強制結束）所以沒有這問題。
這版停止時一邊把 stdout 讀完（drain），一邊送 graceful 停止訊號，並比較
SIGTERM / SIGINT / 'q' 三種方式；PNG 測試改成看檔案有沒有寫出來，stderr 一律印出。

2026-10-13 第二版：第一次測試「探測 PASS 後下一次 open 立刻 Input/output error，
之後全部失敗」，這版每一次 ffmpeg 都改成自己用 Popen 管理生命週期，並記錄：
    - open -> 第一張畫面 -> stop -> process 真的退出 各花多少時間
    - 退出後 exit code、stdout/stderr 是否已 close
    - 系統上是否還有殘留的 ffmpeg process（ps）
    - 每次 open 前 AVFoundation 裝置列表裡 Dashcam 還在不在、index 有沒有變
    - 失敗時 ffmpeg 完整的 stderr（loglevel info）
    - KeepAlive 有沒有中斷（判斷是不是 DVR 本身掉線）
另外在探測之後，用逐步拉長的等待時間（0.5 / 1 / 2 / 5 / 10 / 20 秒）重試，
找出 AVFoundation 釋放相機後需要等多久才能重新開啟。

輸出：mac_uvc_output/<時間>/ 底下的 log.txt、frame.png。
"""

import argparse
import os
import platform
import re
import signal
import subprocess
import sys
import threading
import time
from datetime import datetime

PROJECT_DIR = os.path.dirname(os.path.abspath(__file__))
FFMPEG_PATH = os.path.join(PROJECT_DIR, 'runtime', 'ffmpeg', 'ffmpeg')

INPUT_WIDTH = 1920
INPUT_HEIGHT = 1080
INPUT_FPS = 30
OUTPUT_WIDTH = 1280
OUTPUT_HEIGHT = 720
OUTPUT_FPS = 15

PROBE_SECONDS = 8                         # 探測要收幾秒畫面
RECOVERY_WAIT_STEPS = (0.5, 1, 2, 5, 10, 20)  # 探測後 PNG 失敗時，逐步拉長的等待秒數
FIRST_FRAME_TIMEOUT = 10                  # open 後幾秒內沒有第一張畫面就算失敗
STOP_WAIT_SECONDS = 2                     # 每一步停止方式送出後，等 process 結束的秒數
STOP_MODES = ('drain-term', 'terminate', 'drain-int', 'drain-q')  # 見 stop_camera_ffmpeg() 說明

# 內建、不是 Dashcam 的裝置名稱關鍵字（自動挑選時排除）
BUILTIN_DEVICE_KEYWORDS = ('facetime', 'capture screen', 'desk view', 'iphone', 'ipad', 'obs virtual')

output_dir = os.path.join(PROJECT_DIR, 'mac_uvc_output', datetime.now().strftime('%Y%m%d_%H%M%S'))
os.makedirs(output_dir, exist_ok=True)
log_file = open(os.path.join(output_dir, 'log.txt'), 'w', encoding='utf-8')

keepalive_stats = {'ok': 0, 'fail': 0, 'last_fail_time': None}


def log(text):
    """印出帶時間戳（含毫秒）的 log，同時寫進 log.txt"""
    line = f'[{datetime.now().strftime("%H:%M:%S.%f")[:-3]}] {text}'
    print(line, flush=True)
    log_file.write(line + '\n')
    log_file.flush()


def run_command(command, timeout=30):
    """執行一般指令（不是攝影機的 ffmpeg），回傳 (exit code, stdout, stderr)"""
    try:
        result = subprocess.run(command, capture_output=True, timeout=timeout, stdin=subprocess.DEVNULL)
    except subprocess.TimeoutExpired:
        return None, '', f'逾時（{timeout} 秒）'
    except OSError as error:
        return None, '', f'無法執行: {error}'
    return (result.returncode,
            result.stdout.decode('utf-8', errors='ignore'),
            result.stderr.decode('utf-8', errors='ignore'))


# ==========================================
# 系統狀態：殘留 ffmpeg / 裝置列表 / KeepAlive
# ==========================================

def list_ffmpeg_processes():
    """回傳系統上所有 ffmpeg process 的 [(pid, 指令)]"""
    _code, out, _err = run_command(['ps', '-A', '-o', 'pid=,command='], timeout=10)
    processes = []
    for line in out.splitlines():
        line = line.strip()
        if not line:
            continue
        pid_text, _space, command = line.partition(' ')
        executable = command.split(' ')[0]
        if os.path.basename(executable) == 'ffmpeg':
            processes.append((pid_text, command[:150]))
    return processes


def list_video_devices():
    """回傳 [(index, name)]，只有 video devices（這個 ffmpeg 不碰攝影機，只是列表）"""
    _code, _out, err = run_command([FFMPEG_PATH, '-hide_banner', '-f', 'avfoundation',
                                    '-list_devices', 'true', '-i', ''], timeout=20)
    devices = []
    in_video_section = False
    for line in err.splitlines():
        if 'AVFoundation video devices' in line:
            in_video_section = True
            continue
        if 'AVFoundation audio devices' in line:
            in_video_section = False
            continue
        match = re.search(r'\[(\d+)\] (.+)$', line)
        if in_video_section and match:
            devices.append((int(match.group(1)), match.group(2).strip()))
    return devices


def describe_system_state(device_name):
    """一行文字描述：Dashcam 在裝置列表的 index、殘留 ffmpeg 數量、KeepAlive 狀態"""
    devices = list_video_devices()
    dashcam_index = None
    for index, name in devices:
        if name == device_name:
            dashcam_index = index
    leftover = list_ffmpeg_processes()
    if dashcam_index is None:
        device_text = f'Dashcam 不在裝置列表！（目前列表 {devices}）'
    else:
        device_text = f'Dashcam index={dashcam_index}'
    return f'{device_text}，殘留 ffmpeg={len(leftover)} {leftover if leftover else ""}，KeepAlive ok={keepalive_stats["ok"]} fail={keepalive_stats["fail"]}'


# ==========================================
# 1. ffmpeg 檔案
# ==========================================

def check_ffmpeg_file():
    log('===== 1. ffmpeg 檔案 =====')
    log(f'macOS {platform.mac_ver()[0]}，CPU={platform.machine()}，Python {platform.python_version()}')
    log(f'路徑：{FFMPEG_PATH}')
    if not os.path.isfile(FFMPEG_PATH):
        log('FAIL 找不到 runtime/ffmpeg/ffmpeg（這個資料夾被 .gitignore 排除，要手動複製）')
        return False

    is_executable = os.access(FFMPEG_PATH, os.X_OK)
    log(f'可執行權限：{"有" if is_executable else "沒有 -> 請執行 chmod +x runtime/ffmpeg/ffmpeg"}')

    _code, quarantine_out, _err = run_command(['xattr', '-p', 'com.apple.quarantine', FFMPEG_PATH])
    if quarantine_out.strip():
        log(f'quarantine 屬性：有（{quarantine_out.strip()}）-> 請執行 xattr -d com.apple.quarantine runtime/ffmpeg/ffmpeg')
    else:
        log('quarantine 屬性：沒有')

    _code, file_out, _err = run_command(['file', FFMPEG_PATH])
    log(f'檔案格式：{file_out.strip()}')

    if not is_executable:
        return False
    code, version_out, version_err = run_command([FFMPEG_PATH, '-hide_banner', '-version'], timeout=20)
    if code != 0:
        log(f'FAIL ffmpeg -version 執行失敗（exit={code}）：{version_err.strip()[-300:]}')
        return False
    log(f'PASS {version_out.splitlines()[0] if version_out else "ffmpeg 可以執行"}')

    leftover = list_ffmpeg_processes()
    if leftover:
        log(f'注意：測試開始前就有 ffmpeg 在跑（可能是 DA005ControlPanel 或上一次測試沒關乾淨）：{leftover}')
    return True


def choose_device(devices, device_argument):
    """--device 有指定就用指定的；沒指定就排除內建裝置，剩一個就用它"""
    log('===== 2. AVFoundation video devices =====')
    for index, name in devices:
        log(f'  [{index}] {name}')

    if device_argument is not None:
        for index, name in devices:
            if device_argument == str(index) or device_argument == name:
                return index, name
        log(f'FAIL 找不到 --device {device_argument}')
        return None

    candidates = []
    for index, name in devices:
        is_builtin = False
        for keyword in BUILTIN_DEVICE_KEYWORDS:
            if keyword in name.lower():
                is_builtin = True
        if not is_builtin:
            candidates.append((index, name))

    if len(candidates) == 1:
        log(f'自動選擇 Dashcam：[{candidates[0][0]}] {candidates[0][1]}')
        return candidates[0]
    log(f'無法自動判斷 Dashcam（候選 {len(candidates)} 個：{candidates}），請用 --device 指定 index')
    return None


# ==========================================
# 3. KeepAlive
# ==========================================

def start_keepalive():
    log('===== 3. KeepAlive =====')
    sys.path.insert(0, PROJECT_DIR)
    import api

    original_success = api._mark_keepalive_success
    original_failure = api._mark_keepalive_failure

    def count_success():
        keepalive_stats['ok'] += 1
        original_success()

    def count_failure():
        keepalive_stats['fail'] += 1
        keepalive_stats['last_fail_time'] = datetime.now().strftime('%H:%M:%S.%f')[:-3]
        original_failure()

    # 只是包一層計數，原本的行為不變
    api._mark_keepalive_success = count_success
    api._mark_keepalive_failure = count_failure
    api.start_keepalive()

    wait_begin = time.time()
    while keepalive_stats['ok'] < 10 and time.time() - wait_begin < 15:
        time.sleep(0.2)
    if keepalive_stats['ok'] >= 10:
        log(f'PASS KeepAlive 開始成功（ok={keepalive_stats["ok"]}，DVR={api.get_device_address()[0]}）')
        return True
    log(f'FAIL 15 秒內 KeepAlive 成功={keepalive_stats["ok"]}、失敗={keepalive_stats["fail"]}，'
        f'請先確認 NCM 網路（ping6 fd53:7cb8:383:803::215）')
    return False


# ==========================================
# 攝影機 ffmpeg 的完整生命週期（所有攝影機測試都走這裡）
# ==========================================

def build_input_args(device_spec, pixel_format):
    """AVFoundation 輸入參數。device_spec 是 index 或名稱；':none' = 只要影像、不要聲音"""
    args = ['-f', 'avfoundation',
            '-framerate', str(INPUT_FPS),
            '-video_size', f'{INPUT_WIDTH}x{INPUT_HEIGHT}']
    if pixel_format:
        args += ['-pixel_format', pixel_format]
    args += ['-i', f'{device_spec}:none']
    return args


def read_exact(stream, byte_count):
    buffer = bytearray()
    while len(buffer) < byte_count:
        chunk = stream.read(byte_count - len(buffer))
        if not chunk:
            return None
        buffer.extend(chunk)
    return bytes(buffer)


def drain_pipe(stream):
    """一直讀到 EOF、內容丟掉。停止 ffmpeg 時要有人繼續讀 stdout，
    不然 ffmpeg 卡在 write()，收到 SIGTERM/SIGINT 也沒機會正常結束。"""
    try:
        while stream.read(65536):
            pass
    except (OSError, ValueError):
        pass


def stop_camera_ffmpeg(process, label, stop_mode, is_pipe_output):
    """依 stop_mode 停止 ffmpeg，等不到才升級成下一種方式。
    stop_mode：
        terminate  -- 第一版的做法：不讀 stdout，直接 SIGTERM
        drain-term -- 一邊把 stdout 讀完，一邊送 SIGTERM（ffmpeg 視同 Ctrl+C，會正常收尾）
        drain-int  -- 一邊把 stdout 讀完，一邊送 SIGINT
        drain-q    -- 一邊把 stdout 讀完，一邊從 stdin 送 'q'（ffmpeg 的鍵盤結束指令）
    回傳 (停止耗時秒數, 最後是哪一步讓它結束的)"""
    drain_thread = None
    if is_pipe_output and stop_mode != 'terminate':
        drain_thread = threading.Thread(target=drain_pipe, args=(process.stdout,), daemon=True)
        drain_thread.start()

    stop_begin = time.time()
    # 第一步：graceful
    if stop_mode == 'drain-q':
        first_step = 'q'
        try:
            process.stdin.write(b'q')
            process.stdin.flush()
        except (OSError, ValueError):
            pass
    elif stop_mode == 'drain-int':
        first_step = 'SIGINT'
        process.send_signal(signal.SIGINT)
    else:
        first_step = 'SIGTERM'
        process.terminate()

    ended_by = first_step
    try:
        process.wait(timeout=STOP_WAIT_SECONDS)
    except subprocess.TimeoutExpired:
        # 第二步：q / SIGINT 沒用的話再送 SIGTERM
        if first_step != 'SIGTERM':
            log(f'  [{label}] {first_step} 後 {STOP_WAIT_SECONDS}s 沒結束，改送 SIGTERM')
            process.terminate()
            ended_by = 'SIGTERM'
            try:
                process.wait(timeout=1)
            except subprocess.TimeoutExpired:
                pass
        # 第三步：最後才 kill
        if process.poll() is None:
            log(f'  [{label}] {ended_by} 後仍沒結束，改用 kill')
            process.kill()
            process.wait(timeout=STOP_WAIT_SECONDS)
            ended_by = 'kill'

    stop_seconds = time.time() - stop_begin
    if drain_thread is not None:
        drain_thread.join(timeout=1)
    return stop_seconds, ended_by


def run_camera_ffmpeg(label, input_args, output_args, frame_size=None, capture_seconds=3.0,
                      stop_mode='drain-term', output_file=None):
    """啟動一個讀攝影機的 ffmpeg，收畫面 capture_seconds 秒後停止，並確認完整退出。

    frame_size：輸出是 rawvideo pipe 時，每張畫面的 bytes 數（用來算第一張畫面時間）；
                None 表示輸出不是 pipe（例如存檔 / -f null），只等 process 自己結束。
    output_file：輸出是檔案時（PNG），成功與否看檔案有沒有真的寫出來，不只看 exit code。
    回傳 dict：success / first_frame / frames / stop / ended_by / exit_wait / exit_code / stderr"""
    command = [FFMPEG_PATH, '-hide_banner', '-loglevel', 'info'] + input_args + output_args
    result = {'success': False, 'first_frame': None, 'frames': 0, 'stop': 0.0, 'ended_by': '自己結束',
              'exit_wait': None, 'exit_code': None, 'stderr': [], 'stopped_by_us': False}

    if output_file is not None and os.path.isfile(output_file):
        os.remove(output_file)  # 先刪掉舊檔，才知道這次有沒有寫出來

    # drain-q 要從 stdin 送 'q'，其他模式 stdin 用 DEVNULL
    if stop_mode == 'drain-q':
        stdin_setting = subprocess.PIPE
    else:
        stdin_setting = subprocess.DEVNULL

    open_begin = time.time()
    process = subprocess.Popen(command, stdin=stdin_setting, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    spawn_seconds = time.time() - open_begin

    # stderr 一定要有人一直讀，不然 pipe 滿了 ffmpeg 會卡住
    def read_stderr():
        try:
            for line in iter(process.stderr.readline, b''):
                result['stderr'].append(line.decode('utf-8', errors='ignore').rstrip())
        except (OSError, ValueError):
            pass

    stderr_thread = threading.Thread(target=read_stderr, daemon=True)
    stderr_thread.start()

    if frame_size is not None:
        # 逾時保護：FIRST_FRAME_TIMEOUT 秒內沒有第一張畫面就 terminate，
        # 不然 read_exact() 會一直卡在讀 pipe
        def stop_if_no_frame():
            if result['first_frame'] is None and process.poll() is None:
                log(f'  [{label}] {FIRST_FRAME_TIMEOUT}s 內沒有第一張畫面，terminate')
                process.terminate()

        first_frame_timer = threading.Timer(FIRST_FRAME_TIMEOUT, stop_if_no_frame)
        first_frame_timer.start()

        # rawvideo pipe：一直讀，收到第一張畫面後再讀 capture_seconds 秒
        while True:
            frame = read_exact(process.stdout, frame_size)
            now = time.time()
            if frame is None:
                break  # ffmpeg 結束了（開啟失敗或被逾時保護停掉）
            result['frames'] += 1
            if result['first_frame'] is None:
                result['first_frame'] = now - open_begin
            if now - open_begin - result['first_frame'] >= capture_seconds:
                break
        first_frame_timer.cancel()
    else:
        # 輸出到檔案 / null：等它自己結束（指令本身有 -t），最多等 capture_seconds + 20 秒
        try:
            process.wait(timeout=capture_seconds + 20)
        except subprocess.TimeoutExpired:
            pass

    # ---- 停止：graceful -> 逾時 SIGTERM -> 最後才 kill（見 stop_camera_ffmpeg()）----
    if process.poll() is None:
        result['stopped_by_us'] = True
        result['stop'], result['ended_by'] = stop_camera_ffmpeg(process, label, stop_mode, frame_size is not None)
    result['exit_code'] = process.returncode

    # ---- 確認 pipe 都 close、stderr thread 讀完 ----
    stderr_thread.join(timeout=2)
    process.stdout.close()
    process.stderr.close()
    if process.stdin is not None:
        process.stdin.close()
    pipes_closed = process.stdout.closed and process.stderr.closed

    # ---- 確認 process 真的不在了（用 ps，不只看 returncode）----
    exit_check_begin = time.time()
    pid_alive = True
    while time.time() - exit_check_begin < 3:
        alive_pids = [pid for pid, _cmd in list_ffmpeg_processes() if pid == str(process.pid)]
        if not alive_pids:
            pid_alive = False
            break
        time.sleep(0.05)
    result['exit_wait'] = time.time() - exit_check_begin

    if frame_size is not None:
        result['success'] = result['first_frame'] is not None
    elif output_file is not None:
        # PNG：檔案有寫出來就算成功；exit code 另外記錄，不混在一起判斷
        result['success'] = os.path.isfile(output_file) and os.path.getsize(output_file) > 0
    else:
        result['success'] = process.returncode == 0

    first_text = f'{result["first_frame"]:.2f}s' if result['first_frame'] is not None else '-'
    log(f'  [{label}] {"OK" if result["success"] else "FAIL"}  pid={process.pid} spawn={spawn_seconds * 1000:.0f}ms '
        f'第一張={first_text} 收到={result["frames"]}張 '
        f'stop={result["stop"]:.2f}s（結束方式：{result["ended_by"]}） '
        f'exit_code={result["exit_code"]} pipes_closed={pipes_closed} '
        f'ps 確認已消失={not pid_alive}（{result["exit_wait"] * 1000:.0f}ms）')
    # 失敗、或 PNG（輸出到檔案）一律印出 stderr，方便看 PNG 這一步實際發生什麼事
    if not result['success'] or output_file is not None:
        log(f'  [{label}] ffmpeg stderr（最後 25 行）：')
        for line in result['stderr'][-25:]:
            log('       ' + line)
    return result


# ==========================================
# 4. 探測
# ==========================================

def probe_input_mode(device_spec):
    """收 PROBE_SECONDS 秒、輸出丟掉，從 stderr 看輸入格式和收到幾張。回傳 pixel_format 或 False"""
    log(f'===== 4. {INPUT_WIDTH}x{INPUT_HEIGHT}@{INPUT_FPS} 探測（{PROBE_SECONDS} 秒） =====')
    for pixel_format in (None, 'uyvy422', 'nv12', 'yuyv422', '0rgb'):
        label = f'probe pixel_format={pixel_format or "預設"}'
        # 探測要 -stats 才會印 frame= 數量；其他測試用 -nostats 減少輸出
        result = run_camera_ffmpeg(label, build_input_args(device_spec, pixel_format),
                                   ['-stats', '-t', str(PROBE_SECONDS), '-f', 'null', '-'],
                                   capture_seconds=PROBE_SECONDS)
        all_stderr = '\n'.join(result['stderr'])
        frame_matches = re.findall(r'frame=\s*(\d+)', all_stderr)
        frame_count = int(frame_matches[-1]) if frame_matches else 0
        stream_lines = [line.strip() for line in result['stderr'] if 'Stream #0:0' in line and 'Video' in line]
        if result['success'] and frame_count > 0:
            log(f'PASS {stream_lines[0] if stream_lines else ""}')
            log(f'     {PROBE_SECONDS} 秒收到 {frame_count} 張，約 {frame_count / PROBE_SECONDS:.1f} fps')
            return pixel_format
        lowered = all_stderr.lower()
        if 'not authorized' in lowered or 'denied' in lowered or 'permission' in lowered:
            log('看起來是攝影機權限被拒：系統設定 > 隱私權與安全性 > 相機，允許目前用的 Terminal 後重開 Terminal')
            return False
    return False


# ==========================================
# 5. 找出需要等多久才能重新開啟 + 存 PNG
# ==========================================

def save_png_with_recovery(device_spec, pixel_format, device_name):
    """探測結束後，依 RECOVERY_WAIT_STEPS 逐步拉長等待時間嘗試存 PNG。
    回傳成功時「累計等了幾秒」，全部失敗回傳 None"""
    log('===== 5. 探測結束後重新開啟（逐步拉長等待時間）+ 存 PNG =====')
    log(f'  探測結束當下：{describe_system_state(device_name)}')
    png_path = os.path.join(output_dir, 'frame.png')
    total_waited = 0.0
    for wait_seconds in RECOVERY_WAIT_STEPS:
        time.sleep(wait_seconds)
        total_waited += wait_seconds
        log(f'  累計等待 {total_waited:.1f}s 後嘗試：{describe_system_state(device_name)}')
        # 開 3 秒、一直覆寫同一張（-update 1），留下的是第 3 秒的畫面，避開剛開啟時可能的黑畫面
        result = run_camera_ffmpeg(f'png 累計等待 {total_waited:.1f}s', build_input_args(device_spec, pixel_format),
                                   ['-nostats', '-t', '3', '-update', '1', '-y', png_path], capture_seconds=3,
                                   output_file=png_path)
        if result['success']:
            log(f'PASS 探測結束後累計等待 {total_waited:.1f}s 可以重新開啟，已存：{png_path}（請打開確認是 Dashcam 畫面）')
            return total_waited
    log(f'FAIL 等了 {total_waited:.1f}s 仍無法重新開啟')
    return None


# ==========================================
# 6. 正式 pipeline 的 open / stop / reopen
# ==========================================

def reopen_test(device_spec, pixel_format, device_name, cycles, gap_seconds, stop_mode):
    """用正式 pipeline 連續 open / stop / reopen，回傳每次 stop 的 (秒數, 結束方式) 清單"""
    log(f'===== 6. 正式 pipeline（{OUTPUT_WIDTH}x{OUTPUT_HEIGHT}@{OUTPUT_FPS} rgb24）'
        f'open / stop / reopen {cycles} 次，stop 方式={stop_mode}，每次 stop 後等 {gap_seconds}s =====')
    video_filter = f'fps={OUTPUT_FPS},scale={OUTPUT_WIDTH}:{OUTPUT_HEIGHT}:flags=fast_bilinear'
    output_args = ['-nostats', '-vf', video_filter, '-f', 'rawvideo', '-pix_fmt', 'rgb24', 'pipe:1']
    frame_size = OUTPUT_WIDTH * OUTPUT_HEIGHT * 3

    fail_count = 0
    stop_records = []
    for cycle in range(1, cycles + 1):
        log(f'  [{stop_mode}] cycle {cycle} open 前：{describe_system_state(device_name)}')
        result = run_camera_ffmpeg(f'{stop_mode} cycle {cycle}', build_input_args(device_spec, pixel_format), output_args,
                                   frame_size=frame_size, capture_seconds=3, stop_mode=stop_mode)
        if not result['success']:
            fail_count += 1
        stop_records.append((result['stop'], result['ended_by']))
        time.sleep(gap_seconds)

    if fail_count == 0:
        log(f'PASS [{stop_mode}] {cycles} 次全部成功')
    else:
        log(f'FAIL [{stop_mode}] {cycles} 次中失敗 {fail_count} 次')
    return stop_records


def summarize_stop_records(stop_mode, stop_records):
    """一行摘要：平均 / 最長 stop 秒數、各種結束方式的次數"""
    seconds_list = [seconds for seconds, _ended_by in stop_records]
    ended_by_counts = {}
    for _seconds, ended_by in stop_records:
        ended_by_counts[ended_by] = ended_by_counts.get(ended_by, 0) + 1
    log(f'  {stop_mode:<11} stop 平均 {sum(seconds_list) / len(seconds_list):.2f}s，最長 {max(seconds_list):.2f}s，'
        f'結束方式 {ended_by_counts}')


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--device', help='AVFoundation video device index 或名稱')
    parser.add_argument('--open-by', choices=('index', 'name'), default='index',
                        help='ffmpeg -i 用 index（"1:none"）還是名稱（"USB2 NCM:none"）')
    parser.add_argument('--gap', type=float, default=1.0, help='reopen 測試每次 stop 後等幾秒')
    parser.add_argument('--cycles', type=int, default=10)
    parser.add_argument('--stop-mode', choices=STOP_MODES, default='drain-term',
                        help='主要 reopen 測試用的停止方式（預設 drain-term）')
    parser.add_argument('--compare-cycles', type=int, default=3,
                        help='其他停止方式各跑幾次做比較（0 = 不比較）')
    args = parser.parse_args()

    if sys.platform != 'darwin':
        log('這支腳本只給 macOS 用')
        return
    log(f'輸出資料夾：{output_dir}')
    log(f'參數：open-by={args.open_by} gap={args.gap}s cycles={args.cycles} stop-mode={args.stop_mode} '
        f'compare-cycles={args.compare_cycles}')

    if not check_ffmpeg_file():
        return
    device = choose_device(list_video_devices(), args.device)
    if device is None:
        return
    device_index, device_name = device
    if args.open_by == 'name':
        device_spec = device_name
    else:
        device_spec = str(device_index)

    start_keepalive()
    keepalive_ok_before = keepalive_stats['ok']
    keepalive_fail_before = keepalive_stats['fail']
    test_begin = time.time()

    pixel_format = probe_input_mode(device_spec)
    if pixel_format is False:
        return
    save_png_with_recovery(device_spec, pixel_format, device_name)

    # 主要測試：指定的 stop 方式跑 --cycles 次
    all_stop_records = {}
    all_stop_records[args.stop_mode] = reopen_test(device_spec, pixel_format, device_name,
                                                   args.cycles, args.gap, args.stop_mode)
    # 比較：其他 stop 方式各跑 --compare-cycles 次
    if args.compare_cycles > 0:
        for stop_mode in STOP_MODES:
            if stop_mode == args.stop_mode:
                continue
            all_stop_records[stop_mode] = reopen_test(device_spec, pixel_format, device_name,
                                                      args.compare_cycles, args.gap, stop_mode)
    log('===== 6b. 各種 stop 方式比較 =====')
    for stop_mode, stop_records in all_stop_records.items():
        summarize_stop_records(stop_mode, stop_records)

    log('===== 7. KeepAlive / 殘留 process =====')
    elapsed = time.time() - test_begin
    ok_count = keepalive_stats['ok'] - keepalive_ok_before
    fail_count = keepalive_stats['fail'] - keepalive_fail_before
    log(f'{"PASS" if fail_count == 0 and ok_count > 0 else "FAIL"} 測試期間 {elapsed:.0f}s：KeepAlive 成功 {ok_count} 次、'
        f'失敗 {fail_count} 次（最後一次失敗時間：{keepalive_stats["last_fail_time"]}）')
    leftover = list_ffmpeg_processes()
    log(f'{"PASS" if not leftover else "FAIL"} 測試結束後殘留 ffmpeg：{leftover if leftover else "無"}')
    log(f'===== 結論：Dashcam = [{device_index}] {device_name}，open-by={args.open_by}，pixel_format = {pixel_format or "預設"} =====')


if __name__ == '__main__':
    main()
