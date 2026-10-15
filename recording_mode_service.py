"""
recording_mode_service.py — Settings 頁 Recording Mode（錄影解析度）的 protobuf / RESTful 邏輯

2026-10-14 新增。screen_settings.py 只負責 UI，不直接組 CameraConfiguration、
不直接呼叫 api.send_get()/send_put()——這些集中在這支檔案，寫法跟
iq_adjustment_service.py 一樣，共用 api.py 既有的 send_get()/send_put()。

Endpoint／欄位（2026-10-14 RD 確認）：
    GET/PUT /dashcam/cameraConfiguration
    CameraConfiguration.resolution_setting（Field #8，enum CameraConfiguration.Resolution）

    UI 顯示文字        resolution_setting
    ----------------  -------------------------------
    4K / 30FPS     -> RESOLUTION_3860X2160 = 9   （proto 原文就是 3860，不是 3840）
    1080P / 30FPS  -> RESOLUTION_1920X1080 = 6

PUT 只送 resolution_setting 一個欄位（RD 確認）：
- 不送 bitrate：唯讀欄位，送了韌體會拒絕（見 iq_adjustment_service.py 的說明、
  AutoMate test_DA005_confiM_004）
- 不送 framerate：兩個選項都是 30FPS，不用改
- 不送其他 CameraConfiguration 欄位：所有欄位都是 proto3 optional（有 field
  presence），沒設定的欄位不會被序列化，韌體只更新有送的欄位（AutoMate
  test_DA005_confiM_001~009 都是這種「全新 message 只設一個欄位」的寫法）。
  所以這裡不需要先 GET 完整設定再 PUT 回去，也不會把舊的 IQ 值又送一次。
"""

import audi_pb2
import api

CAMERA_CONFIGURATION_PATH = '/dashcam/cameraConfiguration'

# UI 顯示文字（screen_settings.py 直接用這兩個常數，不要自己再寫一份）
RECORDING_MODE_4K = '4K / 30FPS'
RECORDING_MODE_1080P = '1080P / 30FPS'

Resolution = audi_pb2.CameraConfiguration.Resolution

# UI 顯示文字 -> resolution_setting enum 值
RECORDING_MODE_TO_RESOLUTION = {
    RECORDING_MODE_4K: Resolution.RESOLUTION_3860X2160,    # 9
    RECORDING_MODE_1080P: Resolution.RESOLUTION_1920X1080,  # 6
}


def get_recording_mode():
    """GET 目前的 CameraConfiguration，回傳 RECORDING_MODE_4K / RECORDING_MODE_1080P。
    resolution_setting 沒設定、或是其他解析度（例如 2560X1440）回傳 None，
    UI 就不高亮任何一個選項。GET 失敗直接讓例外往外丟，呼叫端自己決定怎麼顯示。"""
    data = api.send_get(CAMERA_CONFIGURATION_PATH)
    message = audi_pb2.CameraConfiguration()
    message.ParseFromString(data)

    if not message.HasField('resolution_setting'):
        print('[Recording Mode] GET：resolution_setting 沒有設定')
        return None

    resolution_value = message.resolution_setting
    resolution_name = Resolution.Name(resolution_value) if resolution_value in Resolution.values() else '未知'
    print(f'[Recording Mode] GET：resolution_setting={resolution_value}（{resolution_name}）')

    for mode, mode_resolution in RECORDING_MODE_TO_RESOLUTION.items():
        if resolution_value == mode_resolution:
            return mode
    return None  # 不是 4K 也不是 1080P


def build_recording_mode_message(mode):
    """組出要 PUT 的 CameraConfiguration：全新的 message，只設定 resolution_setting。
    mode 不是 4K / 1080P 其中一個會丟 ValueError（不會送出任何東西）。"""
    if mode not in RECORDING_MODE_TO_RESOLUTION:
        raise ValueError(f'不支援的 Recording Mode：{mode}')
    message = audi_pb2.CameraConfiguration()
    message.resolution_setting = RECORDING_MODE_TO_RESOLUTION[mode]
    return message


def apply_recording_mode(mode):
    """PUT /dashcam/cameraConfiguration，只送 resolution_setting。
    HTTP 失敗（例如韌體回 500、斷線）直接讓例外往外丟，呼叫端負責恢復 UI。

    註：這裡沒有 PUT 之後再 GET 確認——RD 還沒確認設定是不是立即生效（之後
    實機測），如果需要重開才生效，GET 馬上確認反而會誤判失敗。"""
    message = build_recording_mode_message(mode)
    print(f'[Recording Mode] PUT：mode={mode}，resolution_setting={message.resolution_setting}，'
          f'body={message.SerializeToString().hex()}')
    api.send_put(CAMERA_CONFIGURATION_PATH, message.SerializeToString())
