# 项目记忆（curated）

## 项目：全场景智能寻检车 APP（carctrlcloud2）
HarmonyOS NEXT 模板工程，API 22 / 6.0.2，Stage 模型，包名 `com.Nox7th.carctrlcloud`，仅 phone。
**部署目标：真机 dayu 设备 / OpenHarmony 7.0**（用户环境已装 OpenHarmony SDK）；模拟器仅作开发联调。

## 课程模块进度
- 模块① UI、模块② 摇杆、模块③ TCP — 已完成（APP + 模拟器 + 俯视图）。
- 模块④ 华为云 IoTDA 集成 — **已接入完成**（本地 broker 模拟，9/9 端到端通过）。

## 后续路线约定（2026-11-15 用户明确）
- **后续工作按课程实验手册推进**，不再以自定义扩展为主线。自由移动版 / PWM 调速 / Web 摇杆 / 鸿蒙手机直连 / 安卓热点网页等成果**保留为参考实现，不删不回滚**。
- 手册共 16 个实验：`Hispark设备开发资料/Hispark设备开发资料/鸿蒙智慧车机开发实战（配套实验资源）/鸿蒙智慧车机开发实战-实验手册/`。1~8 设备端(hi3861)，9~16 应用侧(DevEco / 车机中控)。
- 盘点结论：**已闭环** 2/5/6/7/8（8 含自由移动版、PWM 版）；**部分完成** 1(缺 DAYU200 烧录)、3/4(未单独实机验收)、9(缺 HelloWorld 留证)、12/13(能力覆盖但缺手册 UI 与外设控制按钮)；**未开始** 10 中控主页面、11 仪表盘、14 设置页、15 媒体播放器、16 导航。
- **形态冲突（待决）**：手册 10~16 是「车机中控」(菜单栏 / 仪表盘 / 媒体 / 导航 / 设置，多页面跳转)；现有 `carctrlcloud2` 是「寻检车遥控」(仅 `Index` + `ControlPage` 两页，无 Preferences / Video / Web 组件)。补做前须先定「改造现有工程」还是「新建中控工程」，未定前不动代码。
- 课程模块①~⑤ 与手册实验编号**不是一一对应**，勿混用口径。

## 接入架构（端口独立，勿互扰）
- TCP 直连：`8888`(控制) / `8889`(俯视图 WS)。由 `simulator/car_server.py` 提供；**2026-11-01 会话中用户手动停掉了全部后台服务**（broker ucZYe4 / 车端 kS8fg2 / car_server OpL7YN），当前均未运行，未被授权前不要重启。
- IoT 平台：`1883`(MQTT) / `8899`(俯视图 WS)。本地 broker 模拟 IoTDA，可独立重启。
- **默认主模式=云端(iot)**；TCP 直连降级为隐藏 DEBUG（`CarConfig.ENABLE_TCP_DEBUG=false` 时 ControlPage 不显示该按钮）。

## 寻址约定（关键）
- 模拟器：`10.0.2.2` = 宿主机（PC）本机。
- **真机(dayu/OpenHarmony)**：`10.0.2.2` 无效，必须用电脑局域网 IP（当前 `10.234.215.153`），设备与电脑同一 Wi-Fi。
- `CarConfig.IOT_BROKER_HOST` 已设为局域网 IP；真机联调时若 IP 变化需更新。

## 关键设计约定
- **零第三方依赖 / 标准库自研**：Python 模拟器与鸿蒙侧加密(MQTT/HMAC-SHA256/Base64/WS)均为手写，不用外部库。
- 云端鉴权：`password = Base64(HMAC-SHA256("HMAC-SHA256 {id} {ts}", secret))`，`clientId = {id}_{时间戳}`，±10min 时间窗；设备/应用分别用 `DEVICE_REGISTRY` / `APP_REGISTRY`。
- topic：`$oc/devices/{device_id}/sys/commands/#`（订阅）、`.../commands/request_id=.../command_name=...`（发布）、`.../properties/report`（属性）。
- 俯视图 WS 帧必须含 `type:"state"`，字段 speed/battery/heading/x/y。
- 小车运动学：`car_model.py` 为单一真源，转向死区 STEER_DEADZONE=0.05；APP 侧摇杆另有 0.08 死区 + 0.55 低通。

## 工具约定
- 跑 Python 脚本用 `D:/xl/python.exe`（3.12）。
- **沙箱构建须先设** `export DEVECO_SDK_HOME="/d/xl/hm/DevEco Studio/sdk"`，否则报 `Invalid value of DEVECO_SDK_HOME`。
- ArkTS 改动可在沙箱经 `hvigorw`（DevEco 安装目录 `tools/hvigor/bin/hvigorw.js`，需 `DEVECO_SDK_HOME` + `cd` 工程根）构建验证，本会话已多次 BUILD SUCCESSFUL。

## 真平台迁移（拓扑 A：车端接真 IoTDA，APP 走本地 broker）
- **用户已开通 IoTDA 实例（2026-11-01）**：设备接入域名 `1dc089e08c.st1.iotda-device.cn-north-4.myhuaweicloud.com`（解析→117.78.5.125，TCP 8883 已验证可达）；该实例 **MQTT 1883 灰置，仅开放 MQTTS 8883（TLS）**，车端必须 `--tls`；产品 `carCtrl` 已建（数据格式 json，产品ID尾号 e66a7f2e6c302fa8ffd1，资源空间 DefaultApp_6ae73jp3），物模型尚未导入、设备尚未注册。
- 端点换 IoTDA 实例域名 + 端口 8883(TLS)；`MqttClient.ets` **已实现 `connectTls(host,port,clientId,username,pwd,caContent?)`**（BUILD SUCCESSFUL）。
- **TLS API 形态（本 SDK / API 22 实测）**：`socket.constructTLSSocketInstance()` 创建；`TLSConnectOptions.secureOptions` **必填**，其类型 `TLSSecureOptions` **无 `rejectUnauthorized` 字段**（不可关闭校验，真平台预期行为）；有 CA 传 `{ca:[pem]}`，无 CA 传 `{}`（走系统默认信任库）。`TLSSocket.send()` 直接收 `string|ArrayBuffer`（**非** `TCPSendOptions` 包装），用 `tlsSocket.send(bytes.buffer)`。`socket` **未导出 `TLSSendOptions`**。
- `car_mqtt_device.py` **已支持真平台直连（#25 完成）**：`--platform real --host <接入域名> [--port 1883|8883 --tls --ca] --device-id --secret --sig-verify`；real 模式自动按官方规范回执命令响应（`.../commands/response/request_id={rid}`，`{"result_code":0}`）。
- **官方鉴权模板已核实并对齐（#26 完成）**：`clientId={deviceId}_0_{sig}_{ts}`（ts=UTC `YYYYMMDDHH`；sig=0 不校验时间戳/1 校验），`username={deviceId}`，`password=HMAC-SHA256(key=ts, message=secret).hexdigest()`（hex，非 base64）。**与本地教学模板（Base64, msg="HMAC-SHA256 {id} {ts}"）不同**，local/real 双模式并存于脚本内。
- `mqtt_client.py` 已加 TLS：`MiniMqttClient(..., tls=True, ca_file=...)`，`ssl.create_default_context(cafile)` + `wrap_socket(server_hostname)`。
- `simulator/iotda_car_model.json`：产品模型（服务 Car，属性 speed[-1,1]/battery[0,100]/heading[0,360]/x/y，命令 move(lx,ly)/stop/ping），可直接在 IoTDA 控制台导入。
- 接入指南：`docs/华为云IoTDA接入指南.md`（创建产品→导入模型→注册设备→运行脚本→验证）。
- **2026-11-01 15:47 真平台端到端全通**：在线✅ 属性上报✅ 命令下发+回执✅（平台显示成功，车端日志带 rid 的 `[CMD]`）。踩坑三个：①真平台命令 topic 的 request_id/command_name 分两段，`parse_command` 已改逐段扫描；②自研 MiniMqttClient 原本无心跳无 SUBACK 日志，已加自动 PINGREQ（keepalive/2）与 SUBACK 打印；③重启会话切换空窗期平台可能把命令路由给旧死会话（IOTDA.014111 超时），重发即可。车端持续运行中（后台任务）。
