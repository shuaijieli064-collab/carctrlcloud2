/**
 * 全局配置常量：小车连接参数与通信节律
 *
 * 寻址约定（关键）：
 *  - DevEco 模拟器：10.0.2.2 = 宿主机（PC）本机；
 *  - 真机（dayu / OpenHarmony 7.0）：10.0.2.2 无效，必须填电脑局域网 IP
 *    （如 10.234.215.153），且设备与电脑处于同一 Wi-Fi。
 *
 * 架构：默认「云端（IoTDA 风格）」为主模式；TCP 直连仅作为隐藏 DEBUG 选项保留。
 *       live 直连服务（8888/8889）保持运行不动。
 */
export class CarConfig {
  /** 默认接入模式：'iot' 云端（主）/ 'tcp' 直连（隐藏 DEBUG） */
  static readonly DEFAULT_MODE: 'tcp' | 'iot' = 'iot';

  // ============ 模块③ TCP 直连（保留为隐藏 DEBUG 选项，live 服务 8888/8889 不动） ============
  /** 模拟小车服务端默认地址（DevEco 模拟器访问宿主机用 10.0.2.2） */
  static readonly DEFAULT_IP: string = '10.0.2.2';
  /** 模拟小车 TCP 服务端端口（live 直连服务） */
  static readonly DEFAULT_PORT: number = 8888;
  /** 是否启用隐藏的 TCP 直连 DEBUG 开关（真机联调时临时开启以验证直连模式） */
  static readonly ENABLE_TCP_DEBUG: boolean = true;

  // ============ 模块④：云端（华为云 IoTDA 风格，本地 broker 模拟） ============
  /** 云端 broker（模拟 IoTDA 平台）地址；真机填电脑局域网 IP */
  static readonly IOT_BROKER_HOST: string = '10.234.215.153';
  /** 云端 broker MQTT 端口；真平台用 8883(TLS) */
  static readonly IOT_BROKER_PORT: number = 1883;
  /** 设备（车端）标识，命令与属性 topic 以此为准 */
  static readonly IOT_DEVICE_ID: string = 'car_001';
  /** 应用（APP）标识 + 密钥（本地模拟；真平台用应用凭证 / access token） */
  static readonly IOT_APP_ID: string = 'app_controller';
  static readonly IOT_APP_SECRET: string = 'secret_app';

  // ============ 真平台接入（拓扑 A：车端接真 IoTDA，APP 走本地 broker） ============
  /** 是否对车端启用 TLS 直连真实 IoTDA（默认 false，走本地模拟 broker） */
  static readonly CLOUD_USE_TLS: boolean = false;
  /** 真实 IoTDA 实例 MQTT 地址（占位，接真平台时填写，如 xxxx.iot-mqtt.cn-north-4.myhuaweicloud.com） */
  static readonly CLOUD_REAL_HOST: string = '';
  /** 真实 IoTDA MQTT 端口（通常为 8883） */
  static readonly CLOUD_REAL_PORT: number = 8883;
  /** 真实 IoTDA CA 证书路径（占位，接真平台时填写） */
  static readonly CLOUD_CA_CERT_PATH: string = '';
  /** 真实设备密钥（拓扑 A 下供车端 car_mqtt_device.py 使用，占位） */
  static readonly CLOUD_REAL_DEVICE_SECRET: string = '';

  // ============ 模块⑤：华为云 IoTDA 直连（应用侧 HTTPS API） ============
  /** IAM 统一认证端点（换取 X-Subject-Token，24h 有效） */
  static readonly CLOUD_IAM_URL: string = 'https://iam.myhuaweicloud.com/v3/auth/tokens';
  /** 区域（token scope 与 API 路径均以此为准） */
  static readonly CLOUD_REGION: string = 'cn-north-4';
  /** IoTDA 应用接入端点（控制台 总览→接入信息→应用接入 HTTPS 443） */
  static readonly CLOUD_APP_ENDPOINT: string = '1dc089e08c.st1.iotda-app.cn-north-4.myhuaweicloud.com';
  /** 默认云端设备 ID（产品 carCtrl 下注册的 car_001） */
  /** 云端设备 ID（IoTDA 注册设备，形如 {产品ID}_car_001） */
  static readonly CLOUD_DEVICE_ID: string = '6ae6e66a7f2e6c302fa8ffd1_car_001';
  /** 区域项目 ID（我的凭证 → 项目列表 cn-north-4；AK/SK 签名必填） */
  static readonly CLOUD_PROJECT_ID: string = '01a195e54c1572cb94866775d0be8c18';
  /** 设备影子轮询周期（APP 取实时属性的方式：HTTPS 轮询替代 MQTT 订阅） */
  static readonly CLOUD_POLL_INTERVAL_MS: number = 1000;
  /** 云端模式摇杆指令节流（HTTPS 开销高于 MQTT，放宽到 200ms） */
  static readonly CLOUD_SEND_INTERVAL_MS: number = 200;

  // ============ 通信节律（两种模式共用） ============
  /** 心跳周期：每 5 秒发送一次 ping */
  static readonly HEARTBEAT_INTERVAL_MS: number = 5000;
  /** 下行超时判定：15 秒未收到任何下行数据视为断线 */
  static readonly DOWNLINK_TIMEOUT_MS: number = 15000;
  /** 摇杆指令节流：约 100ms 一帧 */
  static readonly SEND_INTERVAL_MS: number = 100;
  /** 重连最小间隔（指数退避起点） */
  static readonly RECONNECT_MIN_MS: number = 1000;
  /** 重连最大间隔（指数退避上限） */
  static readonly RECONNECT_MAX_MS: number = 30000;
  /** TCP connect 超时 */
  static readonly CONNECT_TIMEOUT_MS: number = 5000;
}
