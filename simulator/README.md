# 模拟器与华为云 IoTDA 联调套件

本目录是《全场景智能寻检车 APP 开发实战》课程的**服务端 / 平台侧模拟**代码，全部使用 Python 标准库实现，**零第三方依赖**。

## 两套接入架构

课程演示了两种控制链路，互不干扰，可独立运行：

| 架构 | 端口 | 角色 | 用途 |
|------|------|------|------|
| TCP 直连 | 8888(控制) / 8889(俯视图 WS) | APP ↔ 模拟小车服务 | 模块①②③：UI / 摇杆 / TCP 私有协议 |
| IoT 平台 | 1883(MQTT) / 8899(俯视图 WS) | APP ↔ 本地 broker(模拟 IoTDA) ↔ 车端设备 | 模块④：华为云 IoTDA 风格接入 |

> 关键约束：两套链路端口独立。修改 IoT 模块**不会**影响正在运行的 TCP 直连服务（8888/8889）。

## 模块④：华为云 IoTDA 风格模拟

`mqtt_broker.py` 用标准库手写了一个最小 MQTT 3.1.1 broker，**模拟华为云 IoTDA 平台**：

- 设备/应用鉴权：`username = deviceId/appId`，`password = Base64(HMAC-SHA256("HMAC-SHA256 {id} {ts}", secret))`，`clientId = {id}_{时间戳}`，并带 ±10 分钟时间窗校验；
- 发布/订阅路由：支持 `+` 单级、`#` 多级通配符；
- 命令下发（APP→平台→设备）与属性上报（设备→平台）；
- WebSocket 桥（:8899）：把属性上报翻译成 `{...,"type":"state"}` 推给浏览器俯视图。

### 文件一览

| 文件 | 职责 |
|------|------|
| `mqtt_protocol.py` | IoTDA 风格协议映射：设备/应用注册表、HMAC 鉴权、topic 模板、命令/属性信封编解码 |
| `mqtt_broker.py` | 本地 MQTT broker（模拟 IoTDA 平台）+ 俯视图 WS 桥 |
| `mqtt_client.py` | 极简 MQTT 3.1.1 客户端（车端设备与 APP 替身复用） |
| `car_model.py` | 小车运动学模型（与 TCP 车端共享同一套物理） |
| `car_mqtt_device.py` | 车端以 IoT 设备身份接入 broker，订阅命令、上报属性 |
| `app_mqtt_client.py` | APP 替身：9 项自动化用例 + 交互模式（端到端验证） |
| `car_server.py` | TCP 直连小车服务（模块①②③，端口 8888/8889） |
| `webview.html` | 浏览器俯视图（只看不控），`location.host` 自动适配端口 |

### 快速启动（推荐：一键）

**双击 `simulator\run_iot.bat`** —— 自动停旧本地服务 → 起 broker(1883/8899) + 本地车端设备 → 打开俯视图。窗口保持开着，按回车即全部停止。

然后**另开一个终端**跑验证：

```bash
cd simulator
python app_mqtt_client.py        # 应输出「结果：9/9 通过」
```

命令行等价用法（不想留窗口时）：

```bash
python run_iot.py start --hidden   # 后台常驻，日志在 logs/broker.log、logs/car.log
python run_iot.py stop             # 停止（只停本地套件，不动真平台车端）
```

> 一键启动器已把车端**钉死**为 `--platform local --no-tls --device-id car_001`，不要省略。原因见下方「两个必须记住的坑」。
> 注意：.bat 只保留 ASCII 内容——cmd 按 ANSI(GBK) 解析批处理，批处理里写中文会乱码/闪退，逻辑一律放 Python。

### 手工分步启动（调试时用）

```bash
# 终端 1
python mqtt_broker.py --port 1883 --ws-port 8899

# 终端 2 —— --platform local / --no-tls / --device-id 三个都不能少
python car_mqtt_device.py --platform local --no-tls --device-id car_001 --host 127.0.0.1 --port 1883

# 终端 3
python app_mqtt_client.py
# 浏览器俯视图：http://localhost:8899
```

### 两个必须记住的坑

1. **不带 `--platform local` 就会连错平台**：`car_mqtt_device.py` 无参数时会读同目录 `iotda_config.json`，而这份配置现在是真平台的（`platform=real` / 8883 / TLS）。省略参数 → 车端连去华为云 IoTDA，本地 1883 上没有任何设备会话 → `app_mqtt_client.py` 只能得到 **2/9**（仅鉴权用例 1、9 能过）。日志里的 `speed=1.0` 是测试脚本收不到属性帧时的**兜底默认值**，不是车端真值。
2. **1883 / 8899 归本地套件，真平台俯视图走 8900**：`iotda_app_config.json` 的 `ws_port` 已设为 **8900**，`iotda_monitor.py` 默认就在 8900。曾经两套都绑 8899 时，Windows 的 `SO_REUSEADDR` 允许两个进程同时 LISTENING，浏览器连接被真平台 monitor 静默劫持，拿到的是 ~1.5Hz 的静态影子数据（实测 684ms/帧），表现就是「小车跑得很卡」；分离端口后本地俯视图恢复 **10Hz（100ms/帧）**。`mqtt_broker.py` 的 `ws_bridge_worker` 在 Windows 已改用 `SO_EXCLUSIVEADDRUSE`，端口被占会直接报错，不再静默抢连接。`run_iot.py stop` 只清 1883/8899，不影响 8900。

### 真平台链路（华为云 IoTDA）怎么起

要连真平台时**不要动本地套件**，另起两个进程即可：

```bash
python car_mqtt_device.py                 # 读 iotda_config.json → real + 8883 TLS
python iotda_monitor.py                   # 影子 → 俯视图，默认 ws_port=8900
# 浏览器：http://127.0.0.1:8900（影子轮询 500ms，约 1.5Hz，本来就比本地链路慢）
```

`run_iot.bat` / `run_iot.py stop` 的清理逻辑只匹配带 `--platform local` 的车端，**不会误杀真平台车端**。

### 端到端验证结果（最近一次）

```
[PASS] 1. 应用以正确密钥接入平台      (CONNACK 接受)
[PASS] 2. 能收到车端属性上报          (speed=0.0)
[PASS] 3. 前进指令使位置变化          (Δy=+1.780m)
[PASS] 4. 急停后速度归零             (speed=0.0)
[PASS] 5. 转向指令使朝向变化          (Δheading=+108.0°)
[PASS] 6. ping 命令不中断属性上报     (新增6帧)
[PASS] 7. 非法报文后属性仍持续        (新增6帧)
[PASS] 8. 缺字段指令被拒绝            (speed=0.0)
[PASS] 9. 错误密钥被平台拒绝接入      (CONNACK 拒绝)
结果：9/9 通过
```

## 鸿蒙侧（APP）接入

APP 侧在 `entry/src/main/ets/`：

- `net/MqttClient.ets`：手写 MQTT 3.1.1 客户端（与 `TcpClient.ets` 同款健壮性：bind/心跳/下行看门狗/指数退避重连/连接竞态守卫）。
- `net/IotBridge.ets`：IoTDA 风格协议映射 + **纯手写 HMAC-SHA256/Base64/UTF-8（零依赖，已用 NIST 测试向量校验）**。
- `common/CarConfig.ets`：`IOT_BROKER_HOST/PORT`、`IOT_DEVICE_ID`、`IOT_APP_ID/SECRET`。
- `pages/ControlPage.ets`：顶部「TCP 直连 / IoT 平台」模式切换，两种链路共用同一状态面板与摇杆。

**运行 APP 时**：模拟器里 `10.0.2.2` = 宿主机（PC）。IoT 模式下填 `10.0.2.2:1883`，点「连接小车」即可；切到「IoT 平台」会自动预填 broker 地址。

## APP 模拟器实测步骤（DevEco）

完成模块④后，在真机/模拟器上跑通端到端：

1. **构建**：在 DevEco Studio 重新编译 `entry`（模块④ 改动的 ArkTS 已修复严格模式编译错误）。
2. **启动平台**：双击 `simulator/run_iot.bat`（或手动起 broker 1883 + 车端设备）。确认终端出现 `IoT 套件已启动`。
3. **起模拟器**：运行 entry HAP 到手机模拟器（或真机，需同一局域网）。
4. **切模式**：操控页顶部切到「**IoT 平台**」——此时 broker 地址自动预填为 `10.0.2.2:1883`。
5. **连接**：点「连接小车」，状态应变为「已连接」（鉴权走 HMAC，与本地 broker 一致）。
6. **看效果**：
   - 拖摇杆 → APP 状态面板实时显示 speed/heading/battery/x/y；
   - 浏览器打开 `http://localhost:8899` → 俯视图小车随指令移动、转向、留轨迹。
7. **容错**：断开 broker 进程 → APP 显示断线并自动重连；重连后继续可控。

> 若连接失败，先确认 PC 防火墙放行 1883 入站，且模拟器用的是 `10.0.2.2`（不是 `127.0.0.1`）。

## 迁移到真实华为云 IoTDA

本地 broker 仅用于无账号时的端到端验证。切换到真实平台时：

1. **端点**：把 broker 地址换成 IoTDA 实例的 MQTT 接入域名，端口 **8883（强制 TLS）**；
   - `MqttClient.ets` 已预留 `connectTls` 扩展点，改用 `TLSSocket` 并加载平台 CA 即可。
2. **设备鉴权**：车端用平台下发的 `deviceId + secret`（HMAC 密码算法一致，无需改协议层）。
3. **应用侧**：真实应用中 APP 通常走 IoTDA **应用侧 API（HTTPS + access token）** 下发命令、用 AMQP 订阅设备数据，而非直连 MQTT；本课程的 MQTT 应用身份是「平台中转」的简化教学模型。
4. **topic**：`$oc/devices/{device_id}/sys/...` 模板与真实 IoTDA 完全一致，命令/属性信封结构可直接复用。

> 注意：浏览器俯视图的 WS 桥（8899）是本地模拟器特性；真平台需改用 IoTDA 数据转发 + 自建可视化服务。
