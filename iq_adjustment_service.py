"""
iq_adjustment_service.py — IQ Adjustment 頁面的 protobuf / RESTful 邏輯

screen_iq_adjustment.py 只負責 UI，不 import audi_pb2、不直接呼叫
api.send_get()/send_put()、也不 hardcode Dashcam URL——這些全部集中在這支
檔案，共用 api.py 現有的 send_get()/send_put()（Content-Type/
Content-Length header、IPv6 URL 組合、raise_for_status() 判斷成功/失敗
都已經在這個專案裡驗證過，不重新發明一套 HTTP 傳輸層）。

Endpoint／欄位對照（2026-09-23 確認，來源：
audi_dashcam_ncm_protocol_v0.28.proto 的 CameraConfiguration message、
舊工具 DA005ProtobufSender_v027_2.pyw 的 GET/PUT /dashcam/cameraConfiguration
用法、以及使用者提供的真機 GET 回應驗證過 range；Contrast/Saturation 對應
isp_dynamic_filter1/isp_dynamic_filter2 是使用者跟 RD 額外確認過的結果，
不是這裡自己猜的）：

    UI payload key      CameraConfiguration 欄位     Field #
    ------------------  --------------------------  -------
    noise_reduction  -> isp_noise_reduction          18
    edge_enhancement -> isp_redge_enhancement        23   (拼字是 redge，不是 edge)
    colour_red       -> isp_color_red                25
    colour_blue      -> isp_color_blue               27
    brightness       -> isp_brightness               28
    contrast         -> isp_dynamic_filter1          29   (RD 確認)
    saturation       -> isp_dynamic_filter2          30   (RD 確認)

CameraConfiguration 除了上面 7 個 IQ 相關欄位，還有 camera_position/
local_host/dashcam_ip/dashcam_port/adapter/enconding_res/local_codec/
resolution_setting/framerate/bitrate/image_processing_device/
fov_cropoff_*/isp_hot_pixel_correction/isp_demosaic/isp_shading_correction/
isp_geometric_correction/isp_color_correction/isp_tone_curve_adjustement/
isp_automatic_white_balance/isp_color_green/isp_dynamic_filter3~10 這些
完全不相關的既有設定。這些欄位全部是 proto3 optional（有 field
presence）：apply_iq_settings() 一律先 GET 目前完整的 CameraConfiguration、
只改上面 7 個 IQ 欄位、再把整個 message 序列化送回去——不管韌體端收到
PUT 之後是做 partial merge 還是整包覆寫持久化設定，這樣送都不會動到其他
既有欄位。GET 失敗時直接讓例外往外丟，不會送出任何 PUT。

2026-09-23 第二輪：實測發現 bitrate 這個欄位 GET 回應回傳的狀態韌體自己
PUT 不回去（見 apply_iq_settings() 上方的詳細說明），PUT 前會明確
ClearField('bitrate')，讓它在送出的 message 裡變成完全沒設定（field
absent），不是塞一個猜出來的替代值，也不是把 GET 給的 0 原封不動送回去。
"""

import audi_pb2
import api

CAMERA_CONFIGURATION_PATH = '/dashcam/cameraConfiguration'

# UI payload 的 key -> CameraConfiguration protobuf 屬性名稱
IQ_FIELD_TO_PROTO_ATTR = {
    'noise_reduction': 'isp_noise_reduction',
    'edge_enhancement': 'isp_redge_enhancement',
    'colour_red': 'isp_color_red',
    'colour_blue': 'isp_color_blue',
    'brightness': 'isp_brightness',
    'contrast': 'isp_dynamic_filter1',
    'saturation': 'isp_dynamic_filter2',
}


class IQAdjustmentValidationError(Exception):
    """payload 欄位型別/範圍不對，或組出來的值不符合 protobuf 欄位型別
    （例如 uint32 塞負數會被 protobuf 拒絕）時丟出。訊息是給使用者看的
    清楚說明，不是原始的 protobuf/Python 例外文字。"""


def get_current_iq_settings():
    """GET 目前的 CameraConfiguration，回傳 7 個 IQ 欄位的 plain dict
    （key 對照見檔案開頭），給 UI 進頁面時載入真實值用。GET 失敗直接讓
    例外往外丟，呼叫端自己決定要顯示什麼、要不要保留上一次載入的值——
    這裡不吞掉錯誤、不回傳猜測值。"""
    message = audi_pb2.CameraConfiguration()
    data = api.send_get(CAMERA_CONFIGURATION_PATH)
    message.ParseFromString(data)
    return {
        payload_key: getattr(message, proto_attr)
        for payload_key, proto_attr in IQ_FIELD_TO_PROTO_ATTR.items()
    }


# 2026-09-23 使用者實測回報：GET→modify→PUT 送回去時，韌體回報
# `[E][SetCameraConfig()@3641]: Invalid value of CameraConfig_t.bitrate`，
# 但用舊工具送一個完全空白（所有欄位都不設定）的 CameraConfiguration
# 卻能成功（HTTP 200）。實測驗證（見這支檔案的單元測試）：
#   - bitrate 完全沒被設定過（field absent，HasField()==False）：韌體接受
#   - bitrate 被明確設定成 0（field present, value=0）：韌體拒絕
# 目前這台裝置的 GET 回應對 bitrate 回報的就是「present, value=0」這種
# 韌體自己不接受的狀態——這是 GET/PUT 兩邊對 bitrate 不對稱的裝置端行為，
# 不是這支程式碼的邏輯問題（GET→modify→PUT 本身完全正確保留了 GET 給
# 的值，問題出在 GET 給的那個值本身送不回去）。
#
# 因應方式：Apply IQ Settings 這支函式本來就完全不打算對 bitrate 表達
# 任何意見，一律 ClearField('bitrate')，讓它在送出的 message 裡變成
# 「完全沒有設定」（field absent），複製舊工具那次成功案例的狀態——
# 不是塞一個猜出來的替代數字，也不是照抄 GET 給的 0（那個值本身就是
# 韌體會拒絕的狀態），是乾脆不對這個欄位表達任何意見，讓韌體自己維持
# 它原本的 bitrate。
def apply_iq_settings(payload):
    """payload 是 screen_iq_adjustment.py 收集的 7 個 IQ 欄位 plain dict
    （key 對照見檔案開頭）。GET 目前完整 CameraConfiguration -> 只改這
    7 個欄位 -> ClearField('bitrate')（見上面說明）-> 整個 PUT 回去。
    GET 這一行失敗會直接拋出例外，不會執行到後面的 PUT。"""
    message = audi_pb2.CameraConfiguration()
    data = api.send_get(CAMERA_CONFIGURATION_PATH)  # 失敗就在這裡拋出，不會往下執行
    message.ParseFromString(data)

    try:
        for payload_key, proto_attr in IQ_FIELD_TO_PROTO_ATTR.items():
            value = payload.get(payload_key)
            if value is None:
                continue  # 使用者沒填這個欄位（理論上 UI 一定會給完整 7 個值，這裡是保險）
            setattr(message, proto_attr, int(value))
    except (TypeError, ValueError, OverflowError) as error:
        # int() 轉型失敗，或轉出來的值超出 protobuf 欄位本身的型別限制
        # （isp_* 都是 uint32，不能是負數），統一包成清楚的錯誤訊息。
        raise IQAdjustmentValidationError(f'Invalid IQ setting value: {error}') from error

    message.ClearField('bitrate')

    api.send_put(CAMERA_CONFIGURATION_PATH, message.SerializeToString())
