# 远程设备

从设备中心打开远控页面，先观看画面，需要操作时申请控制权。测试执行占用期间禁止人工输入。结束操作后释放控制权，设备才可重新分配给测试。

页面支持点击、滑动及停留、文字输入和系统按键。右侧终端在底部输入命令，保留已执行命令及结果；刷新画面不应清空终端记录。命令以当前设备和控制会话为边界，历史由控制面保存。

## 节点准备

配置 `NODE_ID`、`NODE_STATE_DIR`、`API_URL`、`ARTEMIS_URL`、`ARTEMIS_ROOT` 和 `ARTEMIS_PYTHON`。外部引擎源码与 Python 路径由部署者指定，例如在平台仓库根目录启动节点时：

```sh
export ARTEMIS_ROOT=../artemis
export ARTEMIS_PYTHON=../artemis/.venv/bin/python
export NODE_STATE_DIR=.runtime/device-node
```

平台与执行引擎保持独立 Python 环境。真机入口及节点身份约束见[节点运行说明](node-runtime.md)。

## 画面与输入

默认视频后端使用 scrcpy，节点发送 H.264 数据到控制面，浏览器用 WebCodecs 解码。`SCRCPY_SERVER_PATH` 指向兼容版本的服务端文件，也可使用 `scripts/fetch_video_server.py` 下载脚本指定版本。输入支持 scrcpy 的触摸和剪贴板能力。

兼容后端可显式设置 `VIDEO_BACKEND=screenrecord`，由系统编码、PyAV 分包。分辨率、码率及持续时长应按目标设备和系统版本验证，不能假定所有系统支持无限时长。采集失败或未出首帧时页面展示等待或错误状态，也可手动使用截图模式。

实时视频转发与执行录像留存是不同链路。验证时应分别检查画面延迟、手势、文本输入、控制权释放、节点重连与实际报告证据；单设备正常不能证明多设备并发容量。
