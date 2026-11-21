/**
 * IotBridge — 课程模块④：华为云 IoTDA 风格的协议映射（ArkTS 侧）
 *
 * 把"命令下发 / 属性上报"翻译为本项目约定的 topic + JSON 信封，使 ControlPage
 * 能以"应用"身份接入本地 broker（模拟 IoTDA 平台）。
 *
 * 设计要点（与 simulator/mqtt_protocol.py 严格对齐）：
 * - topic 模板：$oc/devices/{device_id}/sys/commands/#（订阅）
 *               $oc/devices/{device_id}/sys/commands/request_id=.../command_name=...（发布）
 *               $oc/devices/{device_id}/sys/properties/report（属性上报）
 * - 命令信封：{"command_name":"move","paras":{"lx":..,"ly":..}}
 * - 属性信封：{"services":[{"service_id":"Car","properties":{"speed":..,..}}]}
 * - 鉴权：clientId=appId_时间戳，password=Base64(HMAC-SHA256("HMAC-SHA256 appId ts", secret))
 *
 * 零第三方依赖：HMAC-SHA256 / Base64 / UTF-8 均手写实现（与项目"标准库自研"一致）。
 * 真平台对接时：把 brokerHost 换成 IoTDA 实例、端口 8883(TLS)，密码改用平台的
 * 设备密钥/应用凭证（见 simulator/README.md）。
 */
/** 命令信封（对应华为云 IoTDA 的 command 请求体） */
interface CommandEnvelope {
  command_name: string;
  paras: Record<string, number>;
}

/** 物模型服务项 */
interface ServiceItem {
  service_id: string;
  properties: Record<string, number>;
}

export class IotBridge {
  /** 物模型服务 ID */
  static readonly SERVICE_ID: string = 'Car';
  /** 命令字 */
  static readonly COMMAND_MOVE: string = 'move';
  static readonly COMMAND_STOP: string = 'stop';
  static readonly COMMAND_PING: string = 'ping';

  /** IoTDA 风格 topic 模板 */
  static commandSubTopic(deviceId: string): string {
    return `$oc/devices/${deviceId}/sys/commands/#`;
  }

  static commandPubTopic(deviceId: string, commandName: string, requestId?: string): string {
    const rid: string = requestId ?? `app_${Math.floor(Date.now() / 1000)}_${Date.now() % 1000}`;
    return `$oc/devices/${deviceId}/sys/commands/request_id=${rid}/command_name=${commandName}`;
  }

  static propertyReportTopic(deviceId: string): string {
    return `$oc/devices/${deviceId}/sys/properties/report`;
  }

  /** 鉴权：clientId 内嵌时间戳，密码为 IoTDA 风格 HMAC-SHA256 + Base64 */
  static makeClientId(principalId: string, timestamp: number): string {
    return `${principalId}_${timestamp}`;
  }

  /**
   * 计算 IoTDA 风格接入密码（与 simulator/mqtt_protocol.make_iot_password 完全一致）
   * @param principalId 设备/应用标识（本地 broker = deviceId/appId）
   * @param secret      共享密钥
   * @param timestamp   与 clientId 中的时间戳保持一致
   */
  static makeIotPassword(principalId: string, secret: string, timestamp: number): string {
    const content: string = `HMAC-SHA256 ${principalId} ${timestamp}`;
    const mac: Uint8Array = hmacSha256(utf8Bytes(secret), utf8Bytes(content));
    return base64Encode(mac);
  }

  /** 命令信封（APP → 平台 → 车端） */
  static encodeCommand(commandName: string, paras: Record<string, number>): string {
    const env: CommandEnvelope = { command_name: commandName, paras: paras };
    return JSON.stringify(env);
  }

  static buildMovePayload(lx: number, ly: number): string {
    const p: Record<string, number> = {};
    p['lx'] = Math.round(lx * 100) / 100;
    p['ly'] = Math.round(ly * 100) / 100;
    return IotBridge.encodeCommand(IotBridge.COMMAND_MOVE, p);
  }

  static buildStopPayload(): string {
    return IotBridge.encodeCommand(IotBridge.COMMAND_STOP, {} as Record<string, number>);
  }

  /** 属性上报解析（车端 → 平台 → APP）：提取 services[0].properties */
  static decodePropertyReport(payload: string): CarStateSnapshot | null {
    try {
      const root = JSON.parse(payload) as ServiceReport;
      if (!root || !root.services || root.services.length === 0) {
        return null;
      }
      const props: Record<string, number> = root.services[0].properties;
      if (!props) {
        return null;
      }
      const snap: CarStateSnapshot = {
        speed: props['speed'] ?? 0,
        battery: props['battery'] ?? 0,
        heading: props['heading'] ?? 0,
        x: props['x'] ?? 0,
        y: props['y'] ?? 0
      };
      return snap;
    } catch (e) {
      return null;
    }
  }
}

/** 小车状态快照（属性上报解析结果） */
export interface CarStateSnapshot {
  speed: number;
  battery: number;
  heading: number;
  x: number;
  y: number;
}

interface ServiceReport {
  services: Array<ServiceItem>;
}

// ===================== 自研加密工具（零依赖） =====================

/** UTF-8 字符串 → 字节（含多字节，避免依赖 util.TextEncoder 行为差异） */
function utf8Bytes(s: string): Uint8Array {
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

function ror32(x: number, n: number): number {
  return ((x >>> n) | (x << (32 - n))) >>> 0;
}

function sha256(msg: Uint8Array): Uint8Array {
  const K: Uint32Array = new Uint32Array([
    0x428a2f98, 0x71374491, 0xb5c0fbcf, 0xe9b5dba5, 0x3956c25b, 0x59f111f1, 0x923f82a4, 0xab1c5ed5,
    0xd807aa98, 0x12835b01, 0x243185be, 0x550c7dc3, 0x72be5d74, 0x80deb1fe, 0x9bdc06a7, 0xc19bf174,
    0xe49b69c1, 0xefbe4786, 0x0fc19dc6, 0x240ca1cc, 0x2de92c6f, 0x4a7484aa, 0x5cb0a9dc, 0x76f988da,
    0x983e5152, 0xa831c66d, 0xb00327c8, 0xbf597fc7, 0xc6e00bf3, 0xd5a79147, 0x06ca6351, 0x14292967,
    0x27b70a85, 0x2e1b2138, 0x4d2c6dfc, 0x53380d13, 0x650a7354, 0x766a0abb, 0x81c2c92e, 0x92722c85,
    0xa2bfe8a1, 0xa81a664b, 0xc24b8b70, 0xc76c51a3, 0xd192e819, 0xd6990624, 0xf40e3585, 0x106aa070,
    0x19a4c116, 0x1e376c08, 0x2748774c, 0x34b0bcb5, 0x391c0cb3, 0x4ed8aa4a, 0x5b9cca4f, 0x682e6ff3,
    0x748f82ee, 0x78a5636f, 0x84c87814, 0x8cc70208, 0x90befffa, 0xa4506ceb, 0xbef9a3f7, 0xc67178f2
  ]);
  let h0: number = 0x6a09e667, h1: number = 0xbb67ae85, h2: number = 0x3c6ef372, h3: number = 0xa54ff53a;
  let h4: number = 0x510e527f, h5: number = 0x9b05688c, h6: number = 0x1f83d9ab, h7: number = 0x5be0cd19;

  const l: number = msg.length;
  const withOne: number = l + 1;
  const blocks: number = Math.ceil((withOne + 8) / 64);
  const total: number = blocks * 64;
  const buf: Uint8Array = new Uint8Array(total);
  buf.set(msg, 0);
  buf[l] = 0x80;
  const bitLenLo: number = (l * 8) >>> 0;
  const bitLenHi: number = Math.floor((l * 8) / 0x100000000);
  buf[total - 8] = (bitLenHi >>> 24) & 0xff;
  buf[total - 7] = (bitLenHi >>> 16) & 0xff;
  buf[total - 6] = (bitLenHi >>> 8) & 0xff;
  buf[total - 5] = bitLenHi & 0xff;
  buf[total - 4] = (bitLenLo >>> 24) & 0xff;
  buf[total - 3] = (bitLenLo >>> 16) & 0xff;
  buf[total - 2] = (bitLenLo >>> 8) & 0xff;
  buf[total - 1] = bitLenLo & 0xff;

  const w: Uint32Array = new Uint32Array(64);
  for (let off = 0; off < total; off += 64) {
    for (let i = 0; i < 16; i++) {
      const j: number = off + i * 4;
      w[i] = ((buf[j] << 24) | (buf[j + 1] << 16) | (buf[j + 2] << 8) | buf[j + 3]) >>> 0;
    }
    for (let i = 16; i < 64; i++) {
      const s0: number = ror32(w[i - 15], 7) ^ ror32(w[i - 15], 18) ^ (w[i - 15] >>> 3);
      const s1: number = ror32(w[i - 2], 17) ^ ror32(w[i - 2], 19) ^ (w[i - 2] >>> 10);
      w[i] = (w[i - 16] + s0 + w[i - 7] + s1) >>> 0;
    }
    let a: number = h0, b: number = h1, c: number = h2, d: number = h3;
    let e: number = h4, f: number = h5, g: number = h6, h: number = h7;
    for (let i = 0; i < 64; i++) {
      const S1: number = ror32(e, 6) ^ ror32(e, 11) ^ ror32(e, 25);
      const ch: number = (e & f) ^ (~e & g);
      const t1: number = (h + S1 + ch + K[i] + w[i]) >>> 0;
      const S0: number = ror32(a, 2) ^ ror32(a, 13) ^ ror32(a, 22);
      const maj: number = (a & b) ^ (a & c) ^ (b & c);
      const t2: number = (S0 + maj) >>> 0;
      h = g; g = f; f = e; e = (d + t1) >>> 0; d = c; c = b; b = a; a = (t1 + t2) >>> 0;
    }
    h0 = (h0 + a) >>> 0; h1 = (h1 + b) >>> 0; h2 = (h2 + c) >>> 0; h3 = (h3 + d) >>> 0;
    h4 = (h4 + e) >>> 0; h5 = (h5 + f) >>> 0; h6 = (h6 + g) >>> 0; h7 = (h7 + h) >>> 0;
  }

  const out: Uint8Array = new Uint8Array(32);
  const hs: number[] = [h0, h1, h2, h3, h4, h5, h6, h7];
  for (let i = 0; i < 8; i++) {
    out[i * 4] = (hs[i] >>> 24) & 0xff;
    out[i * 4 + 1] = (hs[i] >>> 16) & 0xff;
    out[i * 4 + 2] = (hs[i] >>> 8) & 0xff;
    out[i * 4 + 3] = hs[i] & 0xff;
  }
  return out;
}

function hmacSha256(key: Uint8Array, data: Uint8Array): Uint8Array {
  const block: number = 64;
  let k: Uint8Array = key;
  if (k.length > block) {
    k = sha256(k);
  }
  const kpad: Uint8Array = new Uint8Array(block);
  kpad.set(k, 0);
  const oKey: Uint8Array = new Uint8Array(block);
  const iKey: Uint8Array = new Uint8Array(block);
  for (let i = 0; i < block; i++) {
    oKey[i] = kpad[i] ^ 0x5c;
    iKey[i] = kpad[i] ^ 0x36;
  }
  const inner: Uint8Array = new Uint8Array(block + data.length);
  inner.set(iKey, 0);
  inner.set(data, block);
  const innerHash: Uint8Array = sha256(inner);
  const outer: Uint8Array = new Uint8Array(block + 32);
  outer.set(oKey, 0);
  outer.set(innerHash, block);
  return sha256(outer);
}

// ==================== 跨模块复用的自研加密原语 ====================
// 华为云应用侧 V11-HMAC-SHA256 请求签名（HuaweiCloudClient）与 MQTT 鉴权共用同一套实现。

/** SHA-256 摘要（自研实现，已过 NIST 标准向量校验） */
export function cryptoSha256(msg: Uint8Array): Uint8Array {
  return sha256(msg);
}

/** HMAC-SHA256（自研实现） */
export function cryptoHmacSha256(key: Uint8Array, data: Uint8Array): Uint8Array {
  return hmacSha256(key, data);
}

/** UTF-8 编码 */
export function cryptoUtf8(s: string): Uint8Array {
  return utf8Bytes(s);
}

/** 字节转小写十六进制串 */
export function bytesToHexStr(bytes: Uint8Array): string {
  const hexChars: string = '0123456789abcdef';
  let out: string = '';
  for (let i = 0; i < bytes.length; i++) {
    out += hexChars.charAt((bytes[i] >>> 4) & 0x0f);
    out += hexChars.charAt(bytes[i] & 0x0f);
  }
  return out;
}

/** 拼接两个字节缓冲 */
export function concatBytes(a: Uint8Array, b: Uint8Array): Uint8Array {
  const out: Uint8Array = new Uint8Array(a.length + b.length);
  out.set(a, 0);
  out.set(b, a.length);
  return out;
}

function base64Encode(bytes: Uint8Array): string {
  const chars: string = 'ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/';
  let out: string = '';
  const len: number = bytes.length;
  for (let i = 0; i < len; i += 3) {
    const b0: number = bytes[i];
    const b1: number = i + 1 < len ? bytes[i + 1] : 0;
    const b2: number = i + 2 < len ? bytes[i + 2] : 0;
    const n: number = (b0 << 16) | (b1 << 8) | b2;
    out += chars[(n >> 18) & 0x3f];
    out += chars[(n >> 12) & 0x3f];
    out += i + 1 < len ? chars[(n >> 6) & 0x3f] : '=';
    out += i + 2 < len ? chars[n & 0x3f] : '=';
  }
  return out;
}
