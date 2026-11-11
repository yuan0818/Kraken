# 硬件验收入口

当前没有自动真机测试。先执行 scripts/linux-preflight.sh 只读检查，再按 docs/runbooks/linux.md 的环境和任务边界开展 ENG/FLA 验收。M0 测试不得选择真实设备；注册端点只接受 SIM- 前缀。模拟完成、容器测试通过、CLI 退出 0 均不能作为真机断言通过证据。
