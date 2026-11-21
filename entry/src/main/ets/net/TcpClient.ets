import { socket } from '@kit.NetworkKit';
import { util } from '@kit.ArkTS';
import { CarConfig } from '../common/CarConfig';
import { CarDownlink, CommandProtocol } from '../model/CommandProtocol';

/** 连接状态 */
export type ConnState = 'disconnected' | 'connecting' | 'connected' | 'reconnecting';

/**
 * TCP 客户端封装：连接 / 断开 / 发送 / 接收 / 心跳 / 指数退避重连
 *
 * 使用要点（对应课程阶段3）：
 * - connect 前必须先 bind()，否则 API 22 部分版本报 2301025 错误；
 * - on('message') 回调数据为 ArrayBuffer，用 TextDecoder 解码后按 '\n' 分帧；
 * - 统一监听 on('close') / on('error')，进入重连状态机；
 * - 心跳 5s，15s 无下行判定断线，重连退避 1s→2s→4s→…→30s。
 */
export class TcpClient {
  /** 连接状态变化回调（供 UI 层刷新 @State） */
  onStateChange?: (state: ConnState, info: string) => void;
  /** 下行报文回调（已分帧、已解析） */
  onDownlink?: (msg: CarDownlink) => void;

  private tcpSocket: socket.TCPSocket | null = null;
  private state: ConnState = 'disconnected';
  private serverIp: string = '';
  private serverPort: number = 0;

  /** 接收缓冲区：TCP 是字节流，缓存不完整帧 */
  private recvBuffer: string = '';
  private decoder: util.TextDecoder = util.TextDecoder.create('utf-8');

  /** 最近一次收到下行数据的时间戳（watchdog 判据） */
  private lastDownlinkTime: number = 0;
  private heartbeatTimer: number = -1;
  private watchdogTimer: number = -1;
  private reconnectTimer: number = -1;
  private reconnectDelay: number = CarConfig.RECONNECT_MIN_MS;

  getState(): ConnState {
    return this.state;
  }

  private setState(state: ConnState, info: string): void {
    this.state = state;
    if (this.onStateChange) {
      this.onStateChange(state, info);
    }
  }

  /**
   * 发起连接（连接永远由 APP 主动发起）
   * @param ip 服务端地址（模拟器环境使用 10.0.2.2）
   * @param port 服务端端口（默认 8888）
   */
  connect(ip: string, port: number): void {
    this.serverIp = ip;
    this.serverPort = port;
    this.stopTimers();
    this.doConnect();
  }

  private doConnect(): void {
    this.closeSocket();
    this.setState('connecting', `正在连接 ${this.serverIp}:${this.serverPort} ...`);

    const client = socket.constructTCPSocketInstance();
    this.tcpSocket = client;

    // 关键：connect 前必须先 bind，否则部分 API 版本报 2301025
    const bindAddr: socket.NetAddress = { address: '0.0.0.0', port: 0, family: 1 };
    client.bind(bindAddr).then(() => {
      // API 22 的 TCPConnectOptions 仅支持 address / timeout
      const connOpts: socket.TCPConnectOptions = {
        address: { address: this.serverIp, port: this.serverPort, family: 1 },
        timeout: CarConfig.CONNECT_TIMEOUT_MS
      };
      return client.connect(connOpts);
    }).then(() => {
      // 竞态守卫：连接期间用户已点"断开"则不再进入 connected 态
      if (this.tcpSocket !== client || this.state !== 'connecting') {
        client.close().catch(() => {});
        return;
      }
      this.recvBuffer = '';
      this.lastDownlinkTime = new Date().getTime();
      this.reconnectDelay = CarConfig.RECONNECT_MIN_MS;
      this.setState('connected', `已连接 ${this.serverIp}:${this.serverPort}`);
      this.startTimers();
    }).catch((err: Error) => {
      if (this.state !== 'connecting') {
        return; // 用户已主动断开，不再进入重连流程
      }
      this.scheduleReconnect(`连接失败：${err.message}`);
    });

    // 收包：解码 → 缓存 → 分帧 → 解析 → 回调
    client.on('message', (value: socket.SocketMessageInfo) => {
      this.lastDownlinkTime = new Date().getTime();
      const text = this.decoder.decodeToString(new Uint8Array(value.message));
      this.recvBuffer += text;
      this.consumeFrames();
    });
    client.on('close', () => {
      this.scheduleReconnect('连接被服务端关闭');
    });
    client.on('error', (err: Error) => {
      this.scheduleReconnect(`连接错误：${err.message}`);
    });
  }

  /** 把缓冲区中所有完整帧解析并回调 */
  private consumeFrames(): void {
    const idx = this.recvBuffer.lastIndexOf('\n');
    if (idx < 0) {
      return;
    }
    const ready = this.recvBuffer.substring(0, idx);
    this.recvBuffer = this.recvBuffer.substring(idx + 1);
    const frames = CommandProtocol.splitFrames(ready);
    for (let i = 0; i < frames.length; i++) {
      const msg = CommandProtocol.parseDownlink(frames[i]);
      if (msg !== null && this.onDownlink) {
        this.onDownlink(msg);
      }
    }
  }

  /** 发送一帧文本（已含分帧符） */
  send(frame: string): boolean {
    if (this.state !== 'connected' || this.tcpSocket === null) {
      return false;
    }
    const data: socket.TCPSendOptions = { data: new Uint8Array(this.utf8Bytes(frame)).buffer };
    this.tcpSocket.send(data).catch((err: Error) => {
      console.error(`[TcpClient] send failed: ${err.message}`);
    });
    return true;
  }

  /** 主动断开（不再自动重连） */
  disconnect(): void {
    this.stopTimers();
    this.closeSocket();
    this.setState('disconnected', '已断开连接');
  }

  /** 页面销毁时调用：彻底释放资源 */
  destroy(): void {
    this.disconnect();
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

  private closeSocket(): void {
    if (this.tcpSocket !== null) {
      const client = this.tcpSocket;
      this.tcpSocket = null;
      client.off('message');
      client.off('close');
      client.off('error');
      client.close().catch(() => {});
    }
    this.recvBuffer = '';
  }

  /** 启动心跳与下行看门狗 */
  private startTimers(): void {
    this.heartbeatTimer = setInterval(() => {
      this.send(CommandProtocol.buildPing());
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

  /** 进入重连状态机：指数退避 1s→2s→4s→…→30s */
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
}
