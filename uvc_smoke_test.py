"""
uvc_smoke_test.py — Dashcam UVC 的獨立 smoke test（不屬於正式工具）

目的：確認 Windows + OpenCV 能不能正常取得 Dashcam（裝置管理員「相機」裡的
「USB2 NCM」）的畫面，結果確認後再決定怎麼接進正式的 IQ Adjustment / Live View。
這支檔案不 import、也不修改任何正式程式（LiveViewController、RTSPVideoSource 等）。

使用方式：
    python uvc_smoke_test.py scan                  掃描 index 0~10（預設 CAP_DSHOW），存下每個 index 的第一個 frame
    python uvc_smoke_test.py scan --backend msmf   用其他 backend 掃描（dshow / msmf / any）
    python uvc_smoke_test.py test --index 1        對指定 index 做完整測試（open / read / Live View /
                                                   release / reopen / 1920x1080@30），預設 CAP_DSHOW
    python uvc_smoke_test.py compare --index 1     以 CAP_DSHOW 的 index 1 當作 Dashcam，比較
                                                   CAP_DSHOW / CAP_MSMF / 不指定 backend 哪個最穩定
    python uvc_smoke_test.py all                   依序執行 scan、test、compare（自動找出 Dashcam）

選項：
    --seconds N     Live View / FPS 量測的秒數（預設 8）
    --no-window     不開 Live View 視窗（只讀 frame、量 FPS）

輸出：每次執行會在 uvc_smoke_output/<時間>/ 存下 frame 圖片，方便人工確認哪個是 Dashcam。

重要觀察（ffmpeg -list_options 查到的 Dashcam 支援格式）：
- MJPEG：3840x2160 / 1920x1080 / 1600x1200 @30，1280x720 以下可到 60
- H.264：最高 3840x2160
- NV12（未壓縮）：只有 640x480 @30
所以要拿到 1920x1080，必須先把 FOURCC 設成 MJPG，再設寬高；只設寬高可能會被降成 640x480。
"""

import argparse
import os
import shutil
import subprocess
import sys
import time
from datetime import datetime

# OpenCV 掃描不存在的 index 時會印很多警告，只顯示錯誤
os.environ.setdefault("OPENCV_LOG_LEVEL", "ERROR")

import cv2


DASHCAM_DEVICE_NAME = "USB2 NCM"     # 裝置管理員 / DirectShow 裡 Dashcam 的名稱
MAX_SCAN_INDEX = 10
FIRST_FRAME_MAX_TRIES = 30           # 剛開啟時前幾個 frame 可能讀不到，最多試這麼多次
TARGET_WIDTH = 1920
TARGET_HEIGHT = 1080
TARGET_FPS = 30
MATCH_MAX_DIFFERENCE = 30            # 畫面差異（0~255 平均）小於這個值才算「同一台相機」
BLACK_FRAME_MAX_BRIGHTNESS = 5       # 參考畫面最大亮度低於這個值 = 全黑，無法拿來比對

BACKENDS = {
    "dshow": cv2.CAP_DSHOW,
    "msmf": cv2.CAP_MSMF,
    "any": cv2.CAP_ANY,              # 不指定 backend，等同 cv2.VideoCapture(index)
}

OUTPUT_ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "uvc_smoke_output")


# ==========================================
# 小工具
# ==========================================

def make_output_folder():
    folder = os.path.join(OUTPUT_ROOT, datetime.now().strftime("%Y%m%d_%H%M%S"))
    os.makedirs(folder, exist_ok=True)
    return folder


def fourcc_to_text(fourcc_value):
    """cap.get(CAP_PROP_FOURCC) 回傳的數字 -> 'MJPG' 這種文字"""
    value = int(fourcc_value)
    if value <= 0:
        return "-"
    text = ""
    for shift in (0, 8, 16, 24):
        text += chr((value >> shift) & 0xFF)
    return text.strip() or "-"


def open_capture(index, backend_name):
    """開啟相機，回傳 (cap, 開啟花了幾秒)"""
    start = time.monotonic()
    if backend_name == "any":
        cap = cv2.VideoCapture(index)
    else:
        cap = cv2.VideoCapture(index, BACKENDS[backend_name])
    return cap, time.monotonic() - start


def read_first_frame(cap):
    """讀到第一個有效 frame 為止。回傳 (frame 或 None, 嘗試次數, 花了幾秒)"""
    start = time.monotonic()
    for attempt in range(1, FIRST_FRAME_MAX_TRIES + 1):
        is_ok, frame = cap.read()
        if is_ok and frame is not None and frame.size > 0:
            return frame, attempt, time.monotonic() - start
    return None, FIRST_FRAME_MAX_TRIES, time.monotonic() - start


def describe_capture(cap, frame):
    """cap.get() 回報的設定 + 實際 frame 的大小"""
    info = {
        "get_width": int(cap.get(cv2.CAP_PROP_FRAME_WIDTH)),
        "get_height": int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT)),
        "get_fps": round(cap.get(cv2.CAP_PROP_FPS), 2),
        "fourcc": fourcc_to_text(cap.get(cv2.CAP_PROP_FOURCC)),
        "backend": cap.getBackendName() if cap.isOpened() else "-",
    }
    if frame is not None:
        info["frame_width"] = frame.shape[1]
        info["frame_height"] = frame.shape[0]
        info["brightness"] = round(float(frame.mean()), 1)   # 接近 0 代表全黑畫面
    return info


def apply_target_format(cap, use_mjpg):
    """設定 1920x1080 @30。use_mjpg=True 時先設定 FOURCC=MJPG（順序很重要：先格式、再寬高）"""
    results = {}
    if use_mjpg:
        results["set_fourcc"] = cap.set(cv2.CAP_PROP_FOURCC, cv2.VideoWriter_fourcc(*"MJPG"))
    results["set_width"] = cap.set(cv2.CAP_PROP_FRAME_WIDTH, TARGET_WIDTH)
    results["set_height"] = cap.set(cv2.CAP_PROP_FRAME_HEIGHT, TARGET_HEIGHT)
    results["set_fps"] = cap.set(cv2.CAP_PROP_FPS, TARGET_FPS)
    return results


def measure_frames(cap, seconds, show_window, window_title):
    """連續讀 frame seconds 秒，回傳實際量到的 FPS、成功 / 失敗次數、最後一個 frame。
    show_window=True 時同時顯示 Live View（按 q 可提早結束）"""
    success_count = 0
    fail_count = 0
    last_frame = None
    start = time.monotonic()
    while time.monotonic() - start < seconds:
        is_ok, frame = cap.read()
        if not is_ok or frame is None:
            fail_count += 1
            continue
        success_count += 1
        last_frame = frame
        if show_window:
            elapsed = time.monotonic() - start
            preview = frame.copy()
            text = f"{frame.shape[1]}x{frame.shape[0]}  frames={success_count}  fps={success_count / max(elapsed, 0.001):.1f}"
            cv2.putText(preview, text, (20, 40), cv2.FONT_HERSHEY_SIMPLEX, 1.0, (0, 255, 0), 2)
            # 大畫面縮小顯示，避免視窗超出螢幕
            if preview.shape[1] > 1280:
                scale = 1280 / preview.shape[1]
                preview = cv2.resize(preview, (1280, int(preview.shape[0] * scale)))
            cv2.imshow(window_title, preview)
            if cv2.waitKey(1) & 0xFF == ord("q"):
                break
    elapsed = time.monotonic() - start
    if show_window:
        cv2.destroyWindow(window_title)
    return {
        "measured_fps": round(success_count / elapsed, 2) if elapsed > 0 else 0,
        "frames": success_count,
        "failed_reads": fail_count,
        "seconds": round(elapsed, 1),
        "last_frame": last_frame,
    }


def list_dshow_device_names():
    """用 ffmpeg 列出 DirectShow 視訊裝置的名稱（順序通常就是 CAP_DSHOW 的 index）。
    找不到 ffmpeg 就回傳空清單"""
    ffmpeg_path = shutil.which("ffmpeg")
    bundled = os.path.join(os.path.dirname(os.path.abspath(__file__)), "runtime", "ffmpeg", "ffmpeg.exe")
    if os.path.exists(bundled):
        ffmpeg_path = bundled
    if not ffmpeg_path:
        return []
    result = subprocess.run([ffmpeg_path, "-hide_banner", "-list_devices", "true", "-f", "dshow", "-i", "dummy"],
                            capture_output=True, text=True, encoding="utf-8", errors="replace")
    names = []
    for line in result.stderr.splitlines():
        if line.rstrip().endswith("(video)") and '"' in line:
            names.append(line.split('"')[1])
    return names


def print_info(title, info):
    print(f"  {title}")
    for key, value in info.items():
        if key != "last_frame":
            print(f"    {key:<14}: {value}")


# ==========================================
# 1. 掃描 camera index
# ==========================================

def scan_cameras(backend_name, output_folder, max_index=MAX_SCAN_INDEX):
    """掃描 index 0 ~ max_index：能不能 open、能不能 read、frame 大小、FPS，並存下第一個 frame"""
    print(f"\n===== 掃描 camera index 0~{max_index}（backend={backend_name}）=====")
    device_names = list_dshow_device_names() if backend_name == "dshow" else []
    if device_names:
        print("DirectShow 視訊裝置（順序通常等於 CAP_DSHOW index）：")
        for position, name in enumerate(device_names):
            print(f"  {position}: {name}")

    results = []
    for index in range(max_index + 1):
        cap, open_seconds = open_capture(index, backend_name)
        if not cap.isOpened():
            cap.release()
            continue
        frame, attempts, read_seconds = read_first_frame(cap)
        info = describe_capture(cap, frame)
        info["index"] = index
        info["name"] = device_names[index] if index < len(device_names) else "?"
        info["read_ok"] = frame is not None
        info["open_seconds"] = round(open_seconds, 2)
        info["first_frame_seconds"] = round(read_seconds, 2)
        info["first_frame_tries"] = attempts
        if frame is not None:
            image_path = os.path.join(output_folder, f"scan_{backend_name}_index{index}.png")
            cv2.imwrite(image_path, frame)
            info["saved"] = os.path.basename(image_path)
        cap.release()
        results.append(info)

    print(f"{'index':<6}{'name':<20}{'read':<6}{'frame':<12}{'get_fps':<9}{'fourcc':<8}{'open(s)':<9}{'1st frame(s)':<14}saved")
    for info in results:
        frame_text = f"{info.get('frame_width', '-')}x{info.get('frame_height', '-')}"
        print(f"{info['index']:<6}{info['name'][:19]:<20}{str(info['read_ok']):<6}{frame_text:<12}"
              f"{info['get_fps']:<9}{info['fourcc']:<8}{info['open_seconds']:<9}{info['first_frame_seconds']:<14}"
              f"{info.get('saved', '-')}")
    if not results:
        print("沒有任何 index 可以開啟")
    return results


def find_dashcam_index(scan_results):
    """依 DirectShow 裝置名稱找 Dashcam 的 index（只有 dshow 掃描有名稱）"""
    for info in scan_results:
        if info["name"] == DASHCAM_DEVICE_NAME and info["read_ok"]:
            return info["index"]
    return None


# ==========================================
# 2~4. 完整測試：open / read / Live View / release / reopen / 1920x1080@30
# ==========================================

def test_device(index, backend_name, seconds, show_window, output_folder):
    print(f"\n===== 完整測試 index={index}（backend={backend_name}）=====")
    summary = {}

    # A. 用預設設定開啟：看 OpenCV 預設選了什麼格式
    cap, open_seconds = open_capture(index, backend_name)
    if not cap.isOpened():
        print("  A. 預設開啟：失敗")
        return {"default_open": False}
    frame, attempts, read_seconds = read_first_frame(cap)
    default_info = describe_capture(cap, frame)
    default_info.update(open_seconds=round(open_seconds, 2), first_frame_seconds=round(read_seconds, 2))
    print_info("A. 預設開啟（不做任何 set）", default_info)
    cap.release()
    summary["default"] = default_info

    # B. 只設寬高 / FPS、不指定 MJPG：確認會不會被降成 640x480
    cap, _ = open_capture(index, backend_name)
    set_results = apply_target_format(cap, use_mjpg=False)
    frame, _, _ = read_first_frame(cap)
    no_mjpg_info = describe_capture(cap, frame)
    no_mjpg_info["set() 回傳"] = set_results
    print_info("B. 設定 1920x1080@30（沒有指定 MJPG）", no_mjpg_info)
    cap.release()
    summary["no_mjpg"] = no_mjpg_info

    # C. 先設 MJPG 再設 1920x1080@30，持續 Live View + 量 FPS
    cap, open_seconds = open_capture(index, backend_name)
    set_results = apply_target_format(cap, use_mjpg=True)
    frame, attempts, read_seconds = read_first_frame(cap)
    mjpg_info = describe_capture(cap, frame)
    mjpg_info["set() 回傳"] = set_results
    mjpg_info.update(open_seconds=round(open_seconds, 2), first_frame_seconds=round(read_seconds, 2))
    print_info("C. 設定 MJPG + 1920x1080@30（cap.get 與實際 frame）", mjpg_info)
    first_measure = measure_frames(cap, seconds, show_window, f"UVC Live View - index {index} ({backend_name}) #1")
    print_info(f"   Live View {seconds} 秒（實際量測）", first_measure)
    if first_measure["last_frame"] is not None:
        cv2.imwrite(os.path.join(output_folder, f"test_{backend_name}_index{index}_round1.png"),
                    first_measure["last_frame"])
    cap.release()
    print("   release() 完成")
    summary["mjpg_round1"] = mjpg_info
    summary["measure_round1"] = first_measure

    # D. 再次開啟（同樣設定），確認第二次還能正常取得畫面
    cap, open_seconds = open_capture(index, backend_name)
    reopened = cap.isOpened()
    apply_target_format(cap, use_mjpg=True)
    frame, attempts, read_seconds = read_first_frame(cap)
    reopen_info = describe_capture(cap, frame)
    reopen_info.update(reopened=reopened, open_seconds=round(open_seconds, 2),
                       first_frame_seconds=round(read_seconds, 2))
    print_info("D. release 後再次開啟", reopen_info)
    second_measure = measure_frames(cap, seconds, show_window, f"UVC Live View - index {index} ({backend_name}) #2")
    print_info(f"   第二次 Live View {seconds} 秒（實際量測）", second_measure)
    if second_measure["last_frame"] is not None:
        cv2.imwrite(os.path.join(output_folder, f"test_{backend_name}_index{index}_round2.png"),
                    second_measure["last_frame"])
    cap.release()
    summary["reopen"] = reopen_info
    summary["measure_round2"] = second_measure
    return summary


# ==========================================
# 5. 比較 backend
# ==========================================

def frame_difference(frame_a, frame_b):
    """兩張畫面縮小成灰階後的平均亮度差（0 = 完全一樣），用來找出「同一台相機」"""
    size = (160, 90)
    small_a = cv2.cvtColor(cv2.resize(frame_a, size), cv2.COLOR_BGR2GRAY)
    small_b = cv2.cvtColor(cv2.resize(frame_b, size), cv2.COLOR_BGR2GRAY)
    return float(cv2.absdiff(small_a, small_b).mean())


def quick_backend_check(index, backend_name, seconds):
    """開啟 -> MJPG + 1920x1080@30 -> 量 FPS -> release，再重開一次，回傳比較用的數字"""
    rounds = []
    for round_number in (1, 2):
        cap, open_seconds = open_capture(index, backend_name)
        if not cap.isOpened():
            rounds.append({"round": round_number, "opened": False})
            cap.release()
            continue
        apply_target_format(cap, use_mjpg=True)
        frame, attempts, read_seconds = read_first_frame(cap)
        info = describe_capture(cap, frame)
        measure = measure_frames(cap, seconds, False, "")
        cap.release()
        rounds.append({
            "round": round_number, "opened": True, "read_ok": frame is not None,
            "frame": f"{info.get('frame_width', '-')}x{info.get('frame_height', '-')}",
            "get": f"{info['get_width']}x{info['get_height']}@{info['get_fps']}",
            "fourcc": info["fourcc"], "open_s": round(open_seconds, 2), "first_frame_s": round(read_seconds, 2),
            "measured_fps": measure["measured_fps"], "failed_reads": measure["failed_reads"],
        })
    return rounds


def compare_backends(dshow_index, seconds, output_folder):
    print(f"\n===== 比較 backend（Dashcam = CAP_DSHOW index {dshow_index}）=====")
    # 先拿 CAP_DSHOW 的 Dashcam 畫面當參考，用畫面比對找出其他 backend 裡哪個 index 是 Dashcam
    cap, _ = open_capture(dshow_index, "dshow")
    apply_target_format(cap, use_mjpg=True)
    reference, _, _ = read_first_frame(cap)
    cap.release()
    if reference is None:
        print("無法從 CAP_DSHOW 取得參考畫面")
        return {}
    # 參考畫面全黑時，跟任何畫面比對都沒有意義，避免把別台相機誤判成 Dashcam
    reference_is_black = int(reference.max()) < BLACK_FRAME_MAX_BRIGHTNESS
    if reference_is_black:
        print("CAP_DSHOW 參考畫面是全黑的，無法用畫面比對找 Dashcam，其他 backend 一律判定為「找不到」")

    matched_index = {"dshow": dshow_index}
    for backend_name in ("msmf", "any"):
        scan_results = scan_cameras(backend_name, output_folder)
        best_index = None
        best_difference = None
        for info in scan_results:
            if not info["read_ok"]:
                continue
            image = cv2.imread(os.path.join(output_folder, info["saved"]))
            difference = frame_difference(reference, image)
            print(f"  {backend_name} index {info['index']} 與 Dashcam 參考畫面的差異 = {difference:.1f}")
            if best_difference is None or difference < best_difference:
                best_index = info["index"]
                best_difference = difference
        # 差異太大代表不是同一台相機（例如 GoPro 虛擬相機），不能硬選最接近的
        if reference_is_black or best_difference is None or best_difference > MATCH_MAX_DIFFERENCE:
            best_index = None
        matched_index[backend_name] = best_index
        print(f"  => {backend_name} 的 Dashcam 判定為 index {best_index}")

    comparison = {}
    for backend_name, index in matched_index.items():
        if index is None:
            comparison[backend_name] = "找不到 Dashcam"
            continue
        comparison[backend_name] = {"index": index, "rounds": quick_backend_check(index, backend_name, seconds)}

    print("\n----- backend 比較（MJPG + 1920x1080@30，各開兩次）-----")
    print(f"{'backend':<8}{'index':<7}{'round':<7}{'read':<6}{'frame':<11}{'cap.get':<17}{'fourcc':<8}"
          f"{'open(s)':<9}{'1st(s)':<8}{'fps':<8}fail")
    for backend_name, result in comparison.items():
        if isinstance(result, str):
            print(f"{backend_name:<8}{result}")
            continue
        for item in result["rounds"]:
            if not item["opened"]:
                print(f"{backend_name:<8}{result['index']:<7}{item['round']:<7}open 失敗")
                continue
            print(f"{backend_name:<8}{result['index']:<7}{item['round']:<7}{str(item['read_ok']):<6}{item['frame']:<11}"
                  f"{item['get']:<17}{item['fourcc']:<8}{item['open_s']:<9}{item['first_frame_s']:<8}"
                  f"{item['measured_fps']:<8}{item['failed_reads']}")
    return comparison


# ==========================================
# 主程式
# ==========================================

def main():
    parser = argparse.ArgumentParser(description="Dashcam UVC smoke test")
    parser.add_argument("mode", choices=["scan", "test", "compare", "all"])
    parser.add_argument("--index", type=int, help="test / compare 用的 camera index")
    parser.add_argument("--backend", choices=list(BACKENDS), default="dshow")
    parser.add_argument("--seconds", type=float, default=8)
    parser.add_argument("--no-window", action="store_true", help="不開 Live View 視窗")
    args = parser.parse_args()

    output_folder = make_output_folder()
    print(f"OpenCV {cv2.__version__}｜Python {sys.version.split()[0]}｜輸出資料夾：{output_folder}")
    show_window = not args.no_window

    if args.mode == "scan":
        scan_cameras(args.backend, output_folder)
    elif args.mode == "test":
        if args.index is None:
            parser.error("test 需要 --index")
        test_device(args.index, args.backend, args.seconds, show_window, output_folder)
    elif args.mode == "compare":
        if args.index is None:
            parser.error("compare 需要 --index（CAP_DSHOW 的 Dashcam index）")
        compare_backends(args.index, args.seconds, output_folder)
    else:
        scan_results = scan_cameras("dshow", output_folder)
        dashcam_index = find_dashcam_index(scan_results)
        if dashcam_index is None:
            print(f"\n在 CAP_DSHOW 找不到名稱為「{DASHCAM_DEVICE_NAME}」且能讀到畫面的裝置，請看輸出資料夾的圖片手動判斷")
            return
        print(f"\n依 DirectShow 名稱判定 Dashcam = CAP_DSHOW index {dashcam_index}（{DASHCAM_DEVICE_NAME}）")
        test_device(dashcam_index, "dshow", args.seconds, show_window, output_folder)
        compare_backends(dashcam_index, min(args.seconds, 5), output_folder)


if __name__ == "__main__":
    main()
