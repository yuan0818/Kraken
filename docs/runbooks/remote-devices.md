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

默认视频后端使用 scrcpy，节点发送 H.264 数据到控制面，浏览器默认优先用 WebCodecs 解码；WebCodecs 不可用或解码失败时自动改用 MSE（适用于内网 HTTP 访问）。也可在画面上方手动选择播放方式。MSE 使用 [JMuxer](https://github.com/webstream-labs/jmuxer) 将 H.264 封装成 fragmented MP4，交给浏览器播放，不在服务端转码。浏览器仍须支持 MSE 和当前 H.264 编码规格；均不支持时使用截图兼容模式。`SCRCPY_SERVER_PATH` 指向兼容版本的服务端文件，也可使用 `scripts/fetch_video_server.py` 下载脚本指定版本。输入支持 scrcpy 的触摸和剪贴板能力。

兼容后端可显式设置 `VIDEO_BACKEND=screenrecord`，由系统编码、PyAV 分包。分辨率、码率及持续时长应按目标设备和系统版本验证，不能假定所有系统支持无限时长。采集失败或未出首帧时页面展示等待或错误状态，也可手动使用截图模式。

MSE 从参数集和关键帧开始播放；视频重连、采集重启及横竖屏切换时重新初始化。播放器清理已播放缓冲并追赶直播位置，以限制长时间观看后的积压；它的延迟通常高于 WebCodecs。静止画面不要求持续出帧，通道心跳和持续收帧却未播放分别检查。切换播放方式不会变更设备控制权，视频输入仍使用实际视频尺寸换算。

实时视频转发与执行录像留存是不同链路。验证时应分别检查画面延迟、手势、文本输入、控制权释放、节点重连与实际报告证据；单设备正常不能证明多设备并发容量。

## 播放兼容性检查

`pnpm --filter @kraken/web test` 检查视频包、关键帧起播、参数集切换和时间戳换算。浏览器验证应分别覆盖 localhost/HTTPS（WebCodecs）与内网 HTTP（MSE），观察实际播放帧数增长；再检查手动切换、横竖屏、节点断线恢复和退出后连接释放。仅收到视频数据不等于画面已播放。MSE 必须等待 `sourceopen` 后再送入视频数据，避免初始化片段被丢弃。
