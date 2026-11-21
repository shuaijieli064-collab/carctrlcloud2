import { socket } from '@kit.NetworkKit';
import { util } from '@kit.ArkTS';
import { CarConfig } from '../common/CarConfig';

/**
 * 连接状态（与 TcpClient 保持一致，便于 ControlPage 复用）
 */
export type MqttConnState = 'disconnected' | 'connecting' | 'connected' | 'reconnecting';

/**
 * MQTT 下行回调：topic + payload 文本（来自平台下发的命令 / 属性）
 */
export interface MqttMessage {
  topic: string;
  payload: string;
}

/** 订阅队列元素（命名接口，ArkTS 严格模式禁止行内对象字面量类型） */
interface SubscribeItem {
  topic: string;
  qos: number;
}

/**
 * MqttClient — 鸿蒙侧 MQTT 3.1.1 客户端（课程模块④：APP 接入华为云 IoTDA）
 *
 * 设计要点：
 * - 基于 @ohos.net.socket 手写 MQTT 协议（CONNECT/CONNACK/SUBSCRIBE/SUBACK/
 *   PUBLISH(PUBACK)/PINGREQ/PINGRESP/DISCONNECT），零第三方依赖；
 * - 两种传输层：
 *     · connect()      → 明文 TCPSocket（本地 broker / 局域网模拟）；
 *     · connectTls()   → TLSSocket（真华为云 IoTDA，端口 8883，加载 CA 证书）；
 *   两者复用同一套 MQTT 编解码与心跳/看门狗/重连逻辑，仅传输层不同；
 * - 与 TcpClient.ets 保持同样的健壮性：connect 前先 bind、心跳、下行看门狗、
 *   指数退避重连、连接竞态守卫；
 * - 暴露 onStateChange / onMessage 两个回调，ControlPage 可直接替换 TcpClient 使用；
 *
 * 真平台对接：调用 connectTls(host, 8883, clientId, deviceId, hmacPassword, caPem)，
 * 其中 host 为 IoTDA 实例 MQTT 域名、hmacPassword 由 IotBridge.buildAuthPassword 生成、
 * caPem 为平台 CA 证书 PEM 文本（由调用方从沙箱资源读取后传入）。
 */
export class MqttClient {
  /** 状态变化回调 */
  onStateChange?: (state: MqttConnState, info: string) => void;
  /** 下行报文回调（已分帧、已解码） */
  onMessage?: (msg: MqttMessage) => void;

  private tcpSocket: socket.TCPSocket | null = null;
  private tlsSocket: socket.TLSSocket | null = null;
  /** 当前传输层是否为 TLS（connectTls 置 true，明文 connect 置 false） */
  private useTls: boolean = false;
  /** TLS 模式下的 CA 证书 PEM 文本（由 connectTls 传入；空字符串=跳过校验，仅本地联调） */
  private caContent: string = '';
  private state: MqttConnState = 'disconnected';

  // broker 配置
  private brokerHost: string = '';
  private brokerPort: number = 1883;
  private clientId: string = 'harmony_app';
  private username: string = '';
  private password: string = '';
  private keepAlive: number = 30;

  // 接收缓冲（字节流需缓存不完整包）
  private recvBuf: Uint8Array = new Uint8Array(0);
  private decoder: util.TextDecoder = util.TextDecoder.create('utf-8');

  // 定时器
  private heartbeatTimer: number = -1;
  private watchdogTimer: number = -1;
  private reconnectTimer: number = -1;
  private lastDownlinkTime: number = 0;
  private reconnectDelay: number = CarConfig.RECONNECT_MIN_MS;
  private subscribeQueue: Array<SubscribeItem> = [];

  getState(): MqttConnState {
    return this.state;
  }

  // ===================== 对外 API =====================
  /**
   * 连接 MQTT broker（明文 TCP）
   * @param host   broker 地址；真平台用 IoTDA 实例域名（配合 connectTls）
   * @param port   端口；本地 1883
   * @param clientId  客户端标识（建议含时间戳，与平台鉴权一致）
   * @param username  设备/应用标识（本地 broker = deviceId/appId）
   * @param password  接入密码（IoTDA 风格 HMAC，或本地密钥）
   * @param keepAlive 心跳周期（秒）
   */
  connect(host: string, port: number, clientId: string, username: string, password: string,
          keepAlive: number = 30): void {
    this.useTls = false;
    this.caContent = '';
    this.brokerHost = host;
    this.brokerPort = port;
    this.clientId = clientId;
    this.username = username;
    this.password = password;
    this.keepAlive = keepAlive;
    this.stopTimers();
    this.doConnect();
  }

  /**
   * 以 TLS 直连真实华为云 IoTDA（或任何启用 TLS 的 MQTT broker）。
   * 与 connect() 的唯一区别在传输层：改用 TLSSocket 并加载 CA 证书；
   * MQTT 协议与全部编解码逻辑完全复用，无需改动。
   *
   * @param host      IoTDA 实例 MQTT 域名，如 xxxx.iot-mqtt.cn-north-4.myhuaweicloud.com
   * @param port      通常为 8883
   * @param clientId  客户端标识（建议 `${deviceId}_${timestamp}`）
   * @param username  平台设备ID（鉴权用户名）
   * @param password  平台 HMAC 签名密码（见 IotBridge.buildAuthPassword）
   * @param caContent 可选：CA 证书 PEM 文本（字符串）。传入则开启证书校验；留空则跳过
   *                  校验（仅本地联调，真平台必须传入，由调用方从沙箱资源读取后填此处）
   * @param keepAlive 心跳周期（秒）
   */
  connectTls(host: string, port: number, clientId: string, username: string, password: string,
             caContent?: string, keepAlive: number = 30): void {
    this.useTls = true;
    this.caContent = caContent ?? '';
    this.brokerHost = host;
    this.brokerPort = port;
    this.clientId = clientId;
    this.username = username;
    this.password = password;
    this.keepAlive = keepAlive;
    this.stopTimers();
    this.doConnect();
  }

  /** 订阅主题（连接建立后由调用方触发，失败重连会自动补订阅） */
  subscribe(topic: string, qos: number = 0): void {
    const item: SubscribeItem = { topic: topic, qos: qos };
    this.subscribeQueue.push(item);
    if (this.state === 'connected' && this.isSocketAlive()) {
      this.sendSubscribe(topic, qos);
    }
  }

  /** 发布消息 */
  publish(topic: string, payload: string, qos: number = 0): boolean {
    if (this.state !== 'connected' || !this.isSocketAlive()) {
      return false;
    }
    this.sendPublish(topic, payload, qos);
    return true;
  }

  /** 主动断开（不再重连） */
  disconnect(): void {
    this.stopTimers();
    this.closeSocket();
    this.setState('disconnected', '已断开连接');
  }

  /** 页面销毁时调用 */
  destroy(): void {
    this.disconnect();
  }

  // ===================== 连接流程 =====================
  private doConnect(): void {
    this.closeSocket();
    this.setState('connecting', `正在连接 ${this.brokerHost}:${this.brokerPort} ...`);

    if (this.useTls) {
      const client = socket.constructTLSSocketInstance();
      this.tlsSocket = client;
      this.tcpSocket = null;

      const bindAddr: socket.NetAddress = { address: '0.0.0.0', port: 0, family: 1 };
      client.bind(bindAddr).then(() => {
        // secureOptions 为必填：有 CA 时显式指定，否则用空对象走系统默认信任库
        // （连受信正规 CA 如华为云 IoTDA 可直接通过；本地自签 CA 需经 caContent 传入）。
        const connOpts: socket.TLSConnectOptions = {
          address: { address: this.brokerHost, port: this.brokerPort, family: 1 },
          secureOptions: this.caContent.length > 0 ? { ca: [this.caContent] } : {}
        };
        return client.connect(connOpts);
      }).then(() => {
        if (this.tlsSocket !== client || this.state !== 'connecting') {
          client.close().catch(() => {});
          return;
        }
        this.afterSocketUp();
      }).catch((err: Error) => {
        if (this.state !== 'connecting') {
          return;
        }
        this.scheduleReconnect(`TLS 连接失败：${err.message}`);
      });

      client.on('message', (value: socket.SocketMessageInfo) => {
        this.lastDownlinkTime = new Date().getTime();
        const chunk = new Uint8Array(value.message);
        this.recvBuf = this.appendBytes(this.recvBuf, chunk);
        this.consumePackets();
      });
      client.on('close', () => {
        this.scheduleReconnect('TLS 连接被服务端关闭');
      });
      client.on('error', (err: Error) => {
        this.scheduleReconnect(`TLS 连接错误：${err.message}`);
      });
    } else {
      const client = socket.constructTCPSocketInstance();
      this.tcpSocket = client;
      this.tlsSocket = null;

      const bindAddr: socket.NetAddress = { address: '0.0.0.0', port: 0, family: 1 };
      client.bind(bindAddr).then(() => {
        const connOpts: socket.TCPConnectOptions = {
          address: { address: this.brokerHost, port: this.brokerPort, family: 1 },
          timeout: CarConfig.CONNECT_TIMEOUT_MS
        };
        return client.connect(connOpts);
      }).then(() => {
        if (this.tcpSocket !== client || this.state !== 'connecting') {
          client.close().catch(() => {});
          return;
        }
        this.afterSocketUp();
      }).catch((err: Error) => {
        if (this.state !== 'connecting') {
          return;
        }
        this.scheduleReconnect(`连接失败：${err.message}`);
      });

      client.on('message', (value: socket.SocketMessageInfo) => {
        this.lastDownlinkTime = new Date().getTime();
        const chunk = new Uint8Array(value.message);
        this.recvBuf = this.appendBytes(this.recvBuf, chunk);
        this.consumePackets();
      });
      client.on('close', () => {
        this.scheduleReconnect('连接被服务端关闭');
      });
      client.on('error', (err: Error) => {
        this.scheduleReconnect(`连接错误：${err.message}`);
      });
    }
  }

  /** 连接建立后公共收尾：重置缓冲、启动心跳、发送 CONNECT */
  private afterSocketUp(): void {
    this.recvBuf = new Uint8Array(0);
    this.lastDownlinkTime = new Date().getTime();
    this.reconnectDelay = CarConfig.RECONNECT_MIN_MS;
    // 发送 CONNECT
    this.sendConnect();
    this.startTimers();
  }

  // ===================== MQTT 编解码 =====================
  private sendConnect(): void {
    const body: number[] = [];
    // 可变头：协议名 "MQTT"，级别 4，连接标志，keepalive
    const cflags = 0x02 | (this.username ? 0x80 : 0) | (this.password ? 0x40 : 0);
    this.pushString(body, 'MQTT');
    body.push(4, cflags, (this.keepAlive >> 8) & 0xFF, this.keepAlive & 0xFF);
    // 负载：clientId / username / password
    this.pushString(body, this.clientId);
    if (this.username) {
      this.pushString(body, this.username);
    }
    if (this.password) {
      this.pushString(body, this.password);
    }
    this.sendPacket(0x01, 0, body);
  }

  private sendSubscribe(topic: string, qos: number): void {
    const pid = this.nextPacketId();
    const body: number[] = [(pid >> 8) & 0xFF, pid & 0xFF];
    this.pushString(body, topic);
    body.push(qos & 0x02);
    this.sendPacket(0x08, 0x02, body);   // SUBSCRIBE 固定头 flags=0x02
  }

  private sendPublish(topic: string, payload: string, qos: number): void {
    const body: number[] = [];
    this.pushString(body, topic);
    if (qos > 0) {
      const pid = this.nextPacketId();
      body.push((pid >> 8) & 0xFF, pid & 0xFF);
    }
    const pld = Array.from(this.utf8Bytes(payload));
    for (const b of pld) {
      body.push(b);
    }
    this.sendPacket(0x03, (qos << 1) & 0x06, body);
  }

  private sendPing(): void {
    this.sendPacket(0x0C, 0, []);
  }

  private sendDisconnectPacket(): void {
    this.sendPacket(0x0E, 0, []);
  }

  private sendPacket(type: number, flags: number, body: number[]): void {
    const full = [((type << 4) | flags) & 0xFF, ...this.encodeRemainingLength(body.length), ...body];
    this.sendRaw(new Uint8Array(full));
  }

  /** 按当前传输层（明文 / TLS）发送字节流 */
  private sendRaw(bytes: Uint8Array): void {
    if (this.useTls) {
      if (this.tlsSocket === null) {
        return;
      }
      this.tlsSocket.send(bytes.buffer).catch((err: Error) => {
        console.error(`[MqttClient] send failed: ${err.message}`);
      });
    } else {
      if (this.tcpSocket === null) {
        return;
      }
      const data: socket.TCPSendOptions = { data: bytes.buffer };
      this.tcpSocket.send(data).catch((err: Error) => {
        console.error(`[MqttClient] send failed: ${err.message}`);
      });
    }
  }

  private nextPacketId(): number {
    // 简单自增；单连接内足够
    this.packetId = (this.packetId + 1) % 65535;
    if (this.packetId === 0) {
      this.packetId = 1;
    }
    return this.packetId;
  }
  private packetId: number = 1;

  // 变长剩余长度编码
  private encodeRemainingLength(length: number): number[] {
    const out: number[] = [];
    let len = length;
    do {
      let digit = len % 128;
      len = Math.floor(len / 128);
      if (len > 0) {
        digit |= 0x80;
      }
      out.push(digit);
    } while (len > 0);
    return out;
  }

  // 写 UTF-8 字符串字段（2 字节长度 + 内容）
  private pushString(body: number[], s: string): void {
    const bytes = Array.from(this.utf8Bytes(s));
    body.push((bytes.length >> 8) & 0xFF, bytes.length & 0xFF);
    for (const b of bytes) {
      body.push(b);
    }
  }

  // 从字节流解析所有完整 MQTT 包
  private consumePackets(): void {
    while (true) {
      if (this.recvBuf.length < 2) {
        return;
      }
      const type = (this.recvBuf[0] >> 4) & 0x0F;
      const flags = this.recvBuf[0] & 0x0F;
      const rl = this.decodeRemainingLength(this.recvBuf, 1);
      const length = rl[0];
      const pos = rl[1];
      if (length < 0 || this.recvBuf.length < pos + length) {
        return; // 不完整，等待更多数据
      }
      const pktBody = this.recvBuf.slice(pos, pos + length);
      this.recvBuf = this.recvBuf.slice(pos + length);
      this.dispatch(type, flags, pktBody);
    }
  }

  private decodeRemainingLength(buf: Uint8Array, pos: number): [number, number] {
    let multiplier = 1;
    let value = 0;
    let p = pos;
    while (p < buf.length) {
      const b = buf[p];
      p++;
      value += (b & 0x7F) * multiplier;
      if ((b & 0x80) === 0) {
        break;
      }
      multiplier *= 128;
    }
    return [value, p];
  }

  private dispatch(type: number, flags: number, body: Uint8Array): void {
    if (type === 0x02) {
      // CONNACK：第 3 字节为返回码，0=接受
      const code = body.length >= 3 ? body[2] : 1;
      if (code === 0) {
        if (this.isSocketAlive() && this.state === 'connecting') {
          this.setState('connected', `已连接 ${this.brokerHost}:${this.brokerPort}`);
          // 连接成功后补订阅
          for (const sub of this.subscribeQueue) {
            this.sendSubscribe(sub.topic, sub.qos);
          }
        }
      } else {
        this.scheduleReconnect(`CONNACK 拒绝 code=${code}`);
      }
    } else if (type === 0x03) {
      // PUBLISH
      let p = 0;
      const rs = this.readString(body, p);
      const topic = rs[0];
      const np = rs[1];
      p = np;
      const qos = (flags >> 1) & 0x03;
      let pid = 0;
      if (qos > 0) {
        pid = (body[p] << 8) | body[p + 1]; // 正确取出 packet id
        p += 2;
      }
      const payload = this.decoder.decodeToString(body.slice(p));
      if (qos === 1) {
        this.sendPubAck(pid); // QoS1 必须回 PUBACK（平台/设备据此确认送达）
      }
      if (this.onMessage) {
        this.onMessage({ topic, payload });
      }
    } else if (type === 0x0D) {
      // PINGRESP：正常，无需处理
    }
    // SUBACK / PUBACK 本地无需特别处理
  }

  private sendPubAck(pid: number): void {
    // 回 PUBACK 并正确回填对端发来的 packet id，满足 QoS1 握手
    this.sendPacket(0x04, 0, [(pid >> 8) & 0xFF, pid & 0xFF]);
  }

  private readString(buf: Uint8Array, pos: number): [string, number] {
    if (pos + 2 > buf.length) {
      return ['', pos];
    }
    const len = (buf[pos] << 8) | buf[pos + 1];
    pos += 2;
    const slice = buf.slice(pos, pos + len);
    return [this.decoder.decodeToString(slice), pos + len];
  }

  private appendBytes(a: Uint8Array, b: Uint8Array): Uint8Array {
    const out = new Uint8Array(a.length + b.length);
    out.set(a, 0);
    out.set(b, a.length);
    return out;
  }

  // UTF-8 字符串 → 字节（手写，避免依赖 TextEncoder 的废弃 API）
  private utf8Bytes(s: string): Uint8Array {
    const out: number[] = [];
    for (let i = 0; i < s.length; i++) {
      let c: number = s.charCodeAt(i);
      if (c < 0x80) {
        out.push(c);
      } else if (c < 0x800) {
        out.push(0xC0 | (c >> 6), 0x80 | (c & 0x3F));
      } else {
        out.push(0xE0 | (c >> 12), 0x80 | ((c >> 6) & 0x3F), 0x80 | (c & 0x3F));
      }
    }
    return new Uint8Array(out);
  }

  // ===================== 心跳 / 看门狗 / 重连 =====================
  private startTimers(): void {
    this.heartbeatTimer = setInterval(() => {
      this.sendPing();
    }, CarConfig.HEARTBEAT_INTERVAL_MS);

    this.watchdogTimer = setInterval(() => {
      if (this.state === 'connected') {
        const silent = new Date().getTime() - this.lastDownlinkTime;
        if (silent > CarConfig.DOWNLINK_TIMEOUT_MS) {
          this.scheduleReconnect(`${Math.round(silent / 1000)}s 未收到下行，判定断线`);
        }
      }
    }, 3000);
  }

  private scheduleReconnect(reason: string): void {
    if (this.state === 'reconnecting' || this.state === 'disconnected') {
      return;
    }
    this.stopTimers();
    this.closeSocket();
    const delay = this.reconnectDelay;
    this.reconnectDelay = Math.min(this.reconnectDelay * 2, CarConfig.RECONNECT_MAX_MS);
    this.setState('reconnecting', `${reason}，${delay / 1000}s 后重连`);
    this.reconnectTimer = setInterval(() => {
      clearInterval(this.reconnectTimer);
      this.reconnectTimer = -1;
      if (this.state === 'reconnecting') {
        this.doConnect();
      }
    }, delay);
  }

  private stopTimers(): void {
    if (this.heartbeatTimer !== -1) {
      clearInterval(this.heartbeatTimer);
      this.heartbeatTimer = -1;
    }
    if (this.watchdogTimer !== -1) {
      clearInterval(this.watchdogTimer);
      this.watchdogTimer = -1;
    }
    if (this.reconnectTimer !== -1) {
      clearInterval(this.reconnectTimer);
      this.reconnectTimer = -1;
    }
  }

  private closeSocket(): void {
    if (this.tcpSocket !== null) {
      const client = this.tcpSocket;
      this.tcpSocket = null;
      client.off('message');
      client.off('close');
      client.off('error');
      client.close().catch(() => {});
    }
    if (this.tlsSocket !== null) {
      const client = this.tlsSocket;
      this.tlsSocket = null;
      client.off('message');
      client.off('close');
      client.off('error');
      client.close().catch(() => {});
    }
    this.recvBuf = new Uint8Array(0);
  }

  /** 当前是否存在任一存活的底层 socket（明文或 TLS） */
  private isSocketAlive(): boolean {
    return this.tcpSocket !== null || this.tlsSocket !== null;
  }

  private setState(state: MqttConnState, info: string): void {
    this.state = state;
    if (this.onStateChange) {
      this.onStateChange(state, info);
    }
  }
}
