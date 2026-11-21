# 华为云 IoTDA 接入操作指南（寻检车车端）

> 目标：把 `simulator/car_mqtt_device.py`（模拟真车车端）从"本地模拟 broker"切换到  
> **华为云 IoTDA 真平台**，完成设备注册、属性上报、命令下发全链路。  
> APP 侧本阶段仍走本地 broker（拓扑 A，见文末说明）。

---

## 0. 开通 IoTDA（从零开始，零成本）

1. **实名认证**：华为云控制台右上角账号 → 账号中心 → 实名认证（个人认证即可；  
   未实名无法使用设备接入）。
2. **开通免费实例单元**：控制台搜索「设备接入 IoTDA」→ 进入 IoTDA 控制台 →  
   左侧导航 **「IoTDA 实例」** → 单击 **「开通免费单元」** →  
   区域选 **华北-北京四（cn-north-4）**，其余默认 → 确定开通。
   - 免费单元为标准版规格，足够教学使用（设备数、消息数都有富余）；
   - 开通后左侧「总览」即显示实例的接入信息。
3. **记录接入信息**：总览页 → 「接入信息」卡片 → 复制 **MQTT (1883)** 的域名。

1dc089e08c.st1.iotda-app.cn-north-4.myhuaweicloud.com

---

## 1. 获取接入信息（三选一记录）

进入 **IoTDA 控制台 → 总览 → 接入信息**，记录：

| 项目             | 说明                 | 示例                                             |
| -------------- | ------------------ | ---------------------------------------------- |
| **MQTT 接入域名**  | 非TLS，免费单元即在总览接入信息中 | `{实例ID}.iot-mqtt.cn-north-4.myhuaweicloud.com` |
| **MQTTS 接入域名** | TLS                | 同上，端口 8883                                     |
| **端口**         | 明文 1883 / TLS 8883 | 起步建议 1883，免证书                                  |

> 若页面显示的是 IP（部分实例），也可直接填 IP。

---

## 2. 创建产品

控制台 → **产品** → 创建产品：

- **产品名称**：`CarCtrl`（任意）
- **协议**：`MQTT`
- **数据格式**：`JSON`

创建后进入产品详情，记下 **产品ID（product_id）**。

carCtrl

<span style="color:rgb(133, 133, 133)">ID: 6ae6e66a7f2e6c302fa8ffd1</span>

---

## 3. 定义物模型（两种方式任选）

### 方式 A：上传模型文件（推荐，本仓库已备好）

产品详情 → 基本信息页 → **上传模型文件** → 选择本仓库打包好的  
`simulator/CarCtrl_Nox7th.zip`（桌面亦有副本）。

> 注意：控制台只认**离线产品模型格式**的 zip（`profile/devicetype-capability.json`
>
> - `service/Car/profile/servicetype-capability.json` 两级结构，zip 名为  
>   `{deviceType}_{manufacturerId}.zip`）；单个裸 JSON 会报"未包含服务"。  
>   源文件在 `simulator/iotda_profile/` 下，改动后需重新打包。

模型内容（与车端实现逐字段对齐）：

- 服务：`Car`
- 属性（均为只读 R）：

| 属性      | 类型      | 范围           | 单位  |
| ------- | ------- | ------------ | --- |
| speed   | decimal | -1 ~ 1       | m/s |
| battery | decimal | 0 ~ 100      | %   |
| heading | decimal | 0 ~ 360      | °   |
| x       | decimal | -1000 ~ 1000 | m   |
| y       | decimal | -1000 ~ 1000 | m   |

- 命令：

| 命令   | 参数                           | 说明    |
| ---- | ---------------------------- | ----- |
| move | lx(必填, -1~~1), ly(必填, -1~~1) | 双轴摇杆  |
| stop | 无                            | 急停归零  |
| ping | 无                            | 连通性测试 |

### 方式 B：手动添加

在模型定义里按上表逐个添加服务/属性/命令（字段名必须完全一致，区分大小写）。

---

## 4. 注册设备（拿到三个关键值）

控制台 → **设备 → 所有设备 → 注册设备**：

- **产品**：选 `CarCtrl`
- **设备标识码（node_id）**：`car_001`（建议，与本地保持一致）
- **设备名称**：任意（如 寻检车01）
- **密钥**：可自定义或留空自动生成

注册成功弹窗中**立即保存**（关掉后需重置才能再看密钥）：

1. **设备ID（device_id）**——形如 `{product_id}_car_001`
2. **设备密钥（secret）**
3. 2cbcafa13499a5a94026c8c65b82a782

---

## 5. 运行车端脚本

```bash
cd D:\hong_meng\carctrlcloud2\simulator

# 明文 1883（起步推荐）
D:\xl\python.exe car_mqtt_device.py --platform real ^
    --host <MQTT接入域名> ^
    --device-id <设备ID> ^
    --secret <设备密钥>

# TLS 8883（稳定后切换，无需 CA 时走系统信任库）
D:\xl\python.exe car_mqtt_device.py --platform real --tls ^
    --host <MQTTS接入域名> --port 8883 ^
    --device-id <设备ID> --secret <设备密钥>
```

鉴权由脚本自动按官方规范生成（无需手工算）：

- `clientId = {deviceId}_0_0_{UTC时间戳YYYYMMDDHH}`（签名类型 0 = 不校验时间戳）
- `username = {deviceId}`
- `password = HMAC-SHA256(key=时间戳, message=secret)` 十六进制摘要

---

## 6. 验证

1. **设备在线**：控制台 → 设备列表，`car_001` 状态变为 **在线**（脚本连上即在线）。
2. **属性上报**：设备详情 → 最新数据/历史数据，可见 speed/battery/heading/x/y  
   每秒刷新（电量缓慢下降属正常模拟）。
3. **命令下发**：
   - 控制台 → 设备 → **在线调试** 或 命令下发，选命令 `move`，参数  
     `lx=0, ly=1`（前进）、`lx=1, ly=0`（右转）、`stop`（急停）；
   - 脚本窗口打印 `[CMD] ...`，且向平台回执 `result_code=0`（界面显示成功）。

---

## 7. APP 侧架构说明（拓扑 A）

本阶段**车端连真平台、APP 仍连本地 broker**：

```
[APP 鸿蒙] --MQTT--> [本地 broker 1883] <--MQTT-- [车端脚本] --MQTTS--> [华为云 IoTDA 真平台]
```

原因：IoTDA 的应用侧官方通道是 **HTTPS API（IAM token 下发命令）+ AMQP（订阅数据）**，  
不提供应用 MQTT 接入；APP 直连真平台属下一阶段（需华为云 AK/SK 或 IAM 配置）。

本地 broker 停止不影响车端上云，两者互不依赖。

---

## 8. 常见问题

| 现象               | 原因/处理                                                      |
| ---------------- | ---------------------------------------------------------- |
| CONNACK code=4   | 用户名或密码错：核对 device_id/secret 是否完整复制；密码模板见 `real_password()` |
| 连接超时             | 域名填错（注意是 `iot-mqtt` 不是 `iot-daemon`）；防火墙/代理拦截 1883/8883    |
| 在线但收不到命令         | 物模型命令名大小写须为 `move/stop/ping`；参数名 `lx/ly`                   |
| 属性上报失败（平台无数据）    | 属性名/服务ID与物模型不一致；检查 `service_id=Car`                        |
| 时间戳校验失败（sig=1 时） | 本机时钟偏差大；默认 sig=0 不校验，无需处理                                  |
| 8883 TLS 握手失败    | 加 `--ca <华为云CA证书.pem>`（控制台"证书"页可下载）；或先用 1883               |
