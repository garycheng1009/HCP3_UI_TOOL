# DVR版本需使用disable VLAN 版

https://jetoptotw.sharepoint.com/:f:/t/DA005AudiDashcam/IgAUbpn6jWe9SYf9PuDvNfrAAZOlhk_UKk8ICB5sno9ysHQ?e=xUhWW0
如要更換為有VLAN(Linux)，需將IP更改為802重新打包

# DA005 DVR Control Panel

DA005 行車紀錄器的桌面控制工具（Tkinter GUI），透過 REST API / Protobuf 跟真機溝通，
提供 Recording／Gallery／Player／Settings 幾個畫面，可以錄影、瀏覽影片與照片、
Favorite／Delete、Format SD Card、調整 Dashcam 設定等操作。目前版本：v2.0。

## 執行

**開發環境（有裝 Python）**
- 雙擊 `執行DA005ControlPanel.bat` 直接啟動
- 或 `執行DA005ControlPanel_除錯用.bat`：會保留 console 視窗，方便看錯誤訊息

**已打包版本（不需要裝 Python）**
- `dist/Dashcam_Control_Panel_v2.0/Dashcam_Control_Panel_v2.0.exe`
- 需要整個 `Dashcam_Control_Panel_v2.0` 資料夾一起帶走，不能只拷貝 exe
- 2026-09-16 使用者要求：打包產出名稱從 DA005_DVR_Control_Panel 改成
  Dashcam_Control_Panel（.spec 檔案本身檔名不變，只改 EXE()/COLLECT()
  裡的 name= 設定，見 `Control_Panel_windows.spec` / `Control_Panel_mac.spec` 開頭說明）

只有 onedir 版本，onefile（單一 exe）版本因為每次啟動都要重新解壓縮、速度太慢，
已經停用。

## 開發環境設定

```
pip install -r requirements.txt
```

Python 需另外確認安裝時有勾選 tcl/tk（官方安裝包預設會裝，tkinter 本身是標準庫，
不需要另外 pip install）。

## 重新打包

在這個資料夾底下執行：

```
pyinstaller Control_Panel_windows.spec
```
（macOS 用 `Control_Panel_mac.spec`）

2026-09-21 改用 PyInstaller 預設輸出路徑（`dist/`、`build/`，`.gitignore`
本來就有排除這兩個資料夾），不再另外指定 `--distpath`/`--workpath`——
之前指定 `release/onedir` 這件事很容易被忘記加，忘了加就會一次打包進
`release/`、一次打包進 `dist/`，兩邊都留下不同步的舊產物；統一用預設路徑
之後，打包出來的東西固定都在 `dist/` 底下，不會再有兩邊對不上的問題。

打包設定（要包哪些檔案、icon、console 開關等）都寫在 `.spec` 檔案裡，不要在命令列
另外疊參數。

## 主要檔案

| 檔案 | 說明 |
|---|---|
| `DA005ControlPanel.pyw` | 程式進入點，建立主視窗、畫面切換邏輯 |
| `api.py` | 所有 DVR REST API / Protobuf 溝通 |
| `screen_recording.py` | Recording 首頁 |
| `screen_gallery.py` | Gallery／Favorite 清單、縮圖、批次操作 |
| `screen_player.py` | 影片／照片播放（Unified Media Viewer） |
| `screen_settings.py` | Settings 頁面 |
| `screen_fake_ui.py` | 左側 Navigation Rail／底部 Bar 等裝飾殼 UI |
| `video_decoder.py` | FFmpeg／OpenCV 影片解碼 |
| `single_instance.py` | 單一視窗執行限制 |
| `resource_paths.py` | 開發環境／打包後共用的資源路徑判斷 |
| `audi_pb2.py`、`nanopb_pb2.py` | Protobuf 定義（產生檔，不要手動修改） |
| `generate_app_icon.py`、`generate_left_rail_icons.py`、`generate_bottom_bar_icons.py` | 一次性 icon 產生工具，要改 icon 造型才需要重新執行 |
| `assets/` | 執行期需要的圖片／icon 資源 |
| `runtime/ffmpeg/ffmpeg.exe` | 打包時一起發行的 FFmpeg，不依賴使用者自己裝 |

## 注意事項

- Onedir 打包後的 `contents_directory='.'` 是刻意設定（讓檔案攤平在同一層，
  不是 PyInstaller 6.x 預設的 `_internal/` 子資料夾），因為 `video_decoder.py`
  是用「exe 自己所在的資料夾」找 `runtime/ffmpeg/ffmpeg.exe`，不能改動這個設定，
  不然會找不到 FFmpeg。
