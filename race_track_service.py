"""
race_track_service.py — Race Track Simulation 頁面的 protobuf / RESTful 邏輯

screen_race_track.py 只負責 UI，不 import audi_pb2、不直接呼叫
api.send_get()/send_put()、也不 hardcode Dashcam URL——這些全部集中在這支
檔案，共用 api.py 現有的 send_get()/send_put()（Content-Type/
Content-Length header、IPv6 URL 組合、raise_for_status() 判斷成功/失敗
都已經在這個專案裡驗證過，不重新發明一套 HTTP 傳輸層）。

TrackInformation/SystemConfiguration 的欄位名稱、型別、PUT endpoint 完全
比照舊工具 DA005ProtobufSender_v027_2.pyw 已經驗證過 HTTP 200 成功的組包
方式（sequenceID/id/name/isTotalLap/isFinished 直接賦值；sectorStartTime/
sectorDuration/totalLapTime 用 seconds/nanos 賦值），不重新猜 schema。

SystemConfiguration.race_track 是 proto3 optional 欄位（有 field
presence）：set_race_track_enabled() 一律先 GET 目前完整設定、只改
race_track 這一個欄位、再把整個 message 序列化送回去——不管韌體端收到
PUT 之後是做 partial merge 還是整包覆寫持久化設定，這樣送都不會動到其他
既有欄位（sales_region/regulation_type/encrypt_sd/encyption_key/
gsensor_sensitivity）。GET 失敗時直接讓例外往外丟，不會送出任何 PUT。
"""

import audi_pb2
import api

SYSTEM_CONFIGURATION_PATH = '/dashcam/systemConfiguration'
TRACK_INFORMATION_PATH = '/dashcam/trackInformation'


class RaceTrackValidationError(Exception):
    """payload 欄位型別轉換失敗，或組出來的值不符合 protobuf 欄位型別
    （例如 sequenceID 是 uint32，塞負數會被 protobuf 拒絕）時丟出。訊息是
    給使用者看的清楚說明，不是原始的 protobuf/Python 例外文字，呼叫端
    直接把 str(error) 顯示在 Status 就好。"""


def get_race_track_enabled():
    """GET 目前的 SystemConfiguration，回傳 race_track 欄位目前的值。

    - 裝置回傳的 SystemConfiguration 從來沒設定過 race_track（proto3
      optional 沒有值）：回傳 None，呼叫端要顯示 Unknown，不能用猜的
      當作 Disabled。
    - GET 本身失敗（連線/HTTP 錯誤）：例外直接往外丟，呼叫端負責決定
      要顯示什麼狀態，這裡不吞掉錯誤。"""
    message = audi_pb2.SystemConfiguration()
    data = api.send_get(SYSTEM_CONFIGURATION_PATH)
    message.ParseFromString(data)
    if not message.HasField('race_track'):
        return None
    return message.race_track


def set_race_track_enabled(enabled):
    """先 GET 完整的 SystemConfiguration，只改 race_track，再把整個
    message 送回去。GET 這一行失敗會直接拋出例外，不會執行到後面的 PUT
    ——不可能發生「GET 失敗但還是送出一個只有 race_track 的殘缺 PUT」
    這種情況。"""
    message = audi_pb2.SystemConfiguration()
    data = api.send_get(SYSTEM_CONFIGURATION_PATH)  # 失敗就在這裡拋出，不會往下執行
    message.ParseFromString(data)
    message.race_track = bool(enabled)
    api.send_put(SYSTEM_CONFIGURATION_PATH, message.SerializeToString())


def _to_optional_int(value, field_label):
    """把 payload 裡的值安全轉成 int：
    - None 直接放行，回傳 None（代表使用者沒填這個欄位，維持 proto3
      optional「沒設定」的語意，呼叫端不會對這個欄位賦值）。
    - 轉不出來（空字串、非數字文字）丟 RaceTrackValidationError，訊息
      清楚指出是哪個欄位、原始值是什麼。"""
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        raise RaceTrackValidationError(f'{field_label} is not a valid number: {value!r}')


def send_track_information(payload):
    """payload 是 screen_race_track.py 收集的 plain dict（key 對照見
    screen_race_track.py 開頭的欄位對照表）。組 SectorInfo/TrackInformation、
    serialize、PUT 到 /dashcam/trackInformation。

    數值欄位先用 _to_optional_int() 安全轉型；轉型失敗，或轉出來的值不符合
    protobuf 欄位本身的型別限制（例如 sequenceID 是 uint32，不能是負數），
    都會在這裡被攔下來、統一包成 RaceTrackValidationError，不會讓使用者
    看到原始、難懂的 protobuf/Python 例外。"""
    try:
        sequence_id = _to_optional_int(payload.get('sequence_id'), 'Sequence ID')
        sector_duration_seconds = _to_optional_int(payload.get('sector_duration_seconds'), 'Sector Duration')
        total_lap_time_seconds = _to_optional_int(payload.get('total_lap_time_seconds'), 'Total Lap Time')
        sector_start_time_seconds = _to_optional_int(
            payload.get('sector_start_time_seconds'), 'Sector Start Time (seconds)')
        sector_start_time_nanos = _to_optional_int(
            payload.get('sector_start_time_nanos'), 'Sector Start Time (nanos)')

        sector_info = audi_pb2.SectorInfo()
        if sequence_id is not None:
            sector_info.sequenceID = sequence_id
        if payload.get('sector_id'):
            sector_info.id = str(payload['sector_id'])
        if payload.get('sector_name'):
            sector_info.name = str(payload['sector_name'])

        sector_info.isTotalLap = bool(payload.get('is_total_lap'))
        sector_info.isFinished = bool(payload.get('is_finished'))

        if sector_start_time_seconds is not None:
            sector_info.sectorStartTime.seconds = sector_start_time_seconds
            sector_info.sectorStartTime.nanos = sector_start_time_nanos or 0

        if sector_duration_seconds is not None:
            sector_info.sectorDuration.seconds = sector_duration_seconds

        message = audi_pb2.TrackInformation()
        message.sectorInfo.CopyFrom(sector_info)

        if total_lap_time_seconds is not None:
            message.totalLapTime.seconds = total_lap_time_seconds
    except RaceTrackValidationError:
        raise
    except (TypeError, ValueError, OverflowError) as error:
        # 保底：轉型本身過了，但 protobuf 賦值時因為型別/範圍限制丟出的
        # 例外（例如 uint32 欄位塞負數），一樣包成清楚的 RaceTrackValidationError。
        raise RaceTrackValidationError(f'Invalid Race Track data: {error}') from error

    api.send_put(TRACK_INFORMATION_PATH, message.SerializeToString())
