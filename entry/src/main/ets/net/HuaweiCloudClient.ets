/**
 * HuaweiCloudClient — 课程模块⑤：APP 直连华为云 IoTDA（应用侧 HTTPS API）
 *
 * 背景：IoTDA 只允许"设备"身份走 MQTT；"应用"身份的官方通道是 HTTPS API。
 *       鉴权采用华为云标准/企业版实例的「衍生 AK/SK 签名」（与官方 SDK 的
 *       DerivationAKSKSigner 等价，已由 PC 侧参考实现对照实测通过）：
 *
 *   V11-HMAC-SHA256 签名链：
 *     1. HKDF-Extract : PRK = HMAC-SHA256(key=AK, msg=SK)
 *     2. HKDF-Expand  : DK  = hex(HMAC-SHA256(PRK, info + 0x01))，info = {YYYYMMDD}/{region}/iotdm
 *     3. CanonicalRequest = METHOD/URI/QS/Headers/SignedHeaders/SHA256(BODY)
 *     4. StringToSign = V11-HMAC-SHA256 \n {UTC时间} \n info \n SHA256(CanonicalRequest)
 *     5. Signature    = HMAC-SHA256(key=utf8(DK), msg=StringToSign)
 *
 *   API 三件套：
 *   1. 设备影子轮询 GET /v5/iot/{project_id}/devices/{device_id}/shadow（替代 MQTT 订阅）
 *   2. 异步命令     POST /v5/iot/{project_id}/devices/{device_id}/async-commands（move，高频）
 *   3. 同步命令     POST /v5/iot/{project_id}/devices/{device_id}/commands（stop/ping，带回执）
 *
 * 零第三方依赖：HTTPS 用系统 @kit.NetworkKit；SHA256/HMAC 复用自研 IotBridge 加密原语。
 */
import { http } from '@kit.NetworkKit';
import { BusinessError } from '@kit.BasicServicesKit';
import { CarConfig } from '../common/CarConfig';
import { IotBridge, CarStateSnapshot,
  cryptoSha256, cryptoHmacSha256, cryptoUtf8, bytesToHexStr, concatBytes } from './IotBridge';

/** 统一连接状态（与 TcpClient/MqttClient 的状态语义对齐） */
export type CloudState = 'disconnected' | 'connecting' | 'connected' | 'error';

/** 简化 HTTP 结果 */
interface HttpResult {
  code: number;
  body: string;
}

/** 设备影子响应体 */
interface ShadowReported {
  properties?: Record<string, number>;
}

interface ShadowItem {
  service_id?: string;
  reported?: ShadowReported;
}

interface ShadowBody {
  shadow?: Array<ShadowItem>;
}

/** 同步命令响应体（设备回执） */
interface SyncCmdResponse {
  result_code?: number;
  result_desc?: string;
}

interface SyncCmdBody {
  command_id?: string;
  response?: SyncCmdResponse;
}

/** UTC 时间戳 YYYYMMDDTHHMMSSZ（V11 签名要求 UTC） */
function utcStamp(d: Date): string {
  const p2 = (n: number): string => (n < 10 ? '0' + n : '' + n);
  return `${d.getUTCFullYear()}${p2(d.getUTCMonth() + 1)}${p2(d.getUTCDate())}` +
    `T${p2(d.getUTCHours())}${p2(d.getUTCMinutes())}${p2(d.getUTCSeconds())}Z`;
}

/** HKDF 派生密钥（官方 SDK 同款：extract 用 AK 作盐、SK 作输入；expand 单轮，输出取 hex） */
function hkdfDerivedKey(ak: string, sk: string, info: string): string {
  const prk: Uint8Array = cryptoHmacSha256(cryptoUtf8(ak), cryptoUtf8(sk));
  const expandInput: Uint8Array = concatBytes(cryptoUtf8(info), new Uint8Array([0x01]));
  const dk: Uint8Array = cryptoHmacSha256(prk, expandInput);
  return bytesToHexStr(dk);
}

export class HuaweiCloudClient {
  private ak: string = '';
  private sk: string = '';
  private projectId: string = CarConfig.CLOUD_PROJECT_ID;
  /** 云端设备 ID（连接前由 ControlPage 设置） */
  deviceId: string = CarConfig.CLOUD_DEVICE_ID;
  private polling: boolean = false;

  /** 状态回调（连接/断开/错误） */
  onStateChange: ((state: CloudState, info: string) => void) | null = null;
  /** 属性快照回调（影子轮询结果） */
  onSnapshot: ((snap: CarStateSnapshot) => void) | null = null;
  /** 命令回执回调 */
  onAck: ((info: string) => void) | null = null;

  /** 是否已配置凭证 */
  isLoggedIn(): boolean {
    return this.ak.length > 0 && this.sk.length > 0;
  }

  /**
   * 配置 AK/SK 凭证并实测校验（向平台查一次设备影子，200 即凭证有效）。
   * 与旧版 IAM 登录不同：AK/SK 无需换 token，签名随每个请求携带。
   */
  async login(ak: string, sk: string, projectId: string): Promise<boolean> {
    if (this.onStateChange !== null) {
      this.onStateChange('connecting', '校验 AK/SK 凭证...');
    }
    this.ak = ak.trim();
    this.sk = sk.trim();
    this.projectId = projectId.trim().length > 0 ? projectId.trim() : CarConfig.CLOUD_PROJECT_ID;
    const code: number = await this.probeShadow();
    if (code === 200) {
      if (this.onStateChange !== null) {
        this.onStateChange('connected', `凭证有效（项目 ${this.projectId.substring(0, 8)}...）`);
      }
      return true;
    }
    // 失败：清空凭证并报错
    this.ak = '';
    this.sk = '';
    throw new Error(`AK/SK 校验失败 code=${code}`);
  }

  // ==================== 设备影子（实时属性） ====================

  /** 影子接口探测：返回 HTTP 码（凭证校验用） */
  private async probeShadow(): Promise<number> {
    const url: string = `https://${CarConfig.CLOUD_APP_ENDPOINT}/v5/iot/${this.projectId}` +
      `/devices/${this.deviceId}/shadow`;
    const r: HttpResult = await this.doRequest('GET', url, '');
    return r.code;
  }

  /** 查询设备影子，提取 Car 服务最新上报属性 */
  async queryShadow(): Promise<CarStateSnapshot | null> {
    const url: string = `https://${CarConfig.CLOUD_APP_ENDPOINT}/v5/iot/${this.projectId}` +
      `/devices/${this.deviceId}/shadow`;
    const r: HttpResult = await this.doRequest('GET', url, '');
    if (r.code === 401) {
      this.stopPolling();
      if (this.onStateChange !== null) {
        this.onStateChange('error', '签名被拒(401)：检查 AK/SK/项目ID 或系统时间');
      }
      return null;
    }
    if (r.code !== 200) {
      return null;
    }
    try {
      const parsed = JSON.parse(r.body) as ShadowBody;
      if (!parsed.shadow) {
        return null;
      }
      for (const item of parsed.shadow) {
        if (item.service_id === IotBridge.SERVICE_ID && item.reported && item.reported.properties) {
          const p: Record<string, number> = item.reported.properties;
          const snap: CarStateSnapshot = {
            speed: p['speed'] ?? 0,
            battery: p['battery'] ?? 0,
            heading: p['heading'] ?? 0,
            x: p['x'] ?? 0,
            y: p['y'] ?? 0
          };
          return snap;
        }
      }
    } catch (e) {
      // JSON 异常按无数据处理
    }
    return null;
  }

  /** 启动影子轮询（1s 周期，模拟实时属性流） */
  startPolling(): void {
    if (this.polling) {
      return;
    }
    this.polling = true;
    this.pollLoop();
  }

  stopPolling(): void {
    this.polling = false;
  }

  private async pollLoop(): Promise<void> {
    while (this.polling) {
      try {
        const snap = await this.queryShadow();
        if (snap !== null && this.onSnapshot !== null) {
          this.onSnapshot(snap);
        }
      } catch (e) {
        // 单次轮询失败不中断（弱网容忍），连续失败由 token 失效分支处理
      }
      await this.sleep(CarConfig.CLOUD_POLL_INTERVAL_MS);
    }
  }

  private sleep(ms: number): Promise<void> {
    return new Promise<void>((resolve: (v: void) => void) => {
      setTimeout(() => {
        resolve();
      }, ms);
    });
  }

  // ==================== 命令下发 ====================

  /** 异步命令（不等待设备回执）：move 等高频指令用 */
  async sendCommandAsync(name: string, paras: Record<string, number>): Promise<boolean> {
    const parasJson: string = JSON.stringify(paras);
    // send_strategy 必填，取值 immediately(立即下发)|delay(缓存，设备上报时再发)
    // 小车控制实时性要求高，用 immediately（IOTDA.000006 校验项）
    const body: string = `{"service_id":"${IotBridge.SERVICE_ID}","command_name":"${name}",` +
      `"paras":${parasJson},"send_strategy":"immediately"}`;
    const url: string = `https://${CarConfig.CLOUD_APP_ENDPOINT}/v5/iot/${this.projectId}` +
      `/devices/${this.deviceId}/async-commands`;
    const r: HttpResult = await this.doRequest('POST', url, body);
    const ok: boolean = r.code === 201 || r.code === 200;
    if (this.onAck !== null) {
      const detail: string = (!ok && r.body.length > 0) ? ` ${r.body.slice(0, 140)}` : '';
      this.onAck(ok ? `已提交 ${name}(${r.code})` : `${name} 提交失败 code=${r.code}${detail}`);
    }
    return ok;
  }

  /** 同步命令（等待设备回执）：stop/ping 等单发指令用 */
  async sendCommandSync(name: string, paras: Record<string, number>): Promise<boolean> {
    const parasJson: string = JSON.stringify(paras);
    const body: string = `{"service_id":"${IotBridge.SERVICE_ID}","command_name":"${name}","paras":${parasJson}}`;
    const url: string = `https://${CarConfig.CLOUD_APP_ENDPOINT}/v5/iot/${this.projectId}` +
      `/devices/${this.deviceId}/commands`;
    const r: HttpResult = await this.doRequest('POST', url, body);
    // 平台对同步命令超时返回 HTTP 200 + 错误体（如 IOTDA.014111），须检查 error_code
    const platErr: string | null = r.body.includes('"error_code"') ? r.body.slice(0, 140) : null;
    if ((r.code !== 201 && r.code !== 200) || platErr !== null) {
      if (this.onAck !== null) {
        const detail: string = platErr !== null ? ` ${platErr}` :
          (r.body.length > 0 ? ` ${r.body.slice(0, 140)}` : '');
        this.onAck(`${name} 下发失败 code=${r.code}${detail}`);
      }
      return false;
    }
    let ack: string = `${name} 已执行`;
    try {
      const parsed = JSON.parse(r.body) as SyncCmdBody;
      if (parsed.response && parsed.response.result_code !== undefined) {
        const okCmd: boolean = parsed.response.result_code === 0;
        ack = okCmd ? `${name} 执行成功` : `${name} 执行失败(${parsed.response.result_code})`;
      }
    } catch (e) {
      // 保留默认 ack
    }
    if (this.onAck !== null) {
      this.onAck(ack);
    }
    return true;
  }

  // ==================== 生命周期 ====================

  destroy(): void {
    this.stopPolling();
    this.ak = '';
    this.sk = '';
  }

  // ==================== V11-HMAC-SHA256 签名 HTTPS ====================

  /**
   * 签名并发起 HTTPS 请求。
   * 参与签名的头：host / x-sdk-date / x-project-id /（有 body 时）content-type，全部小写排序。
   * 注意：本机系统时间偏差过大会导致 401（服务端校验 X-Sdk-Date 时间窗）。
   */
  private async doRequest(method: string, url: string, body: string): Promise<HttpResult> {
    const result: HttpResult = { code: -1, body: '' };
    // ---- 1. 组装请求头（除 Authorization） ----
    const headers: Record<string, string> = {};
    headers['host'] = CarConfig.CLOUD_APP_ENDPOINT;
    headers['X-Sdk-Date'] = utcStamp(new Date());
    headers['X-Project-Id'] = this.projectId;
    if (body.length > 0) {
      headers['Content-Type'] = 'application/json';
    }
    // ---- 2. Canonical Request ----
    const signed: Array<string> = Object.keys(headers)
      .map((k: string): string => k.toLowerCase())
      .sort();
    // URI：逐段百分号编码 + 尾斜杠（官方 SDK 行为；本工程路径仅含字母数字_-，无需额外转义）
    let uri: string = this.urlPathOf(url);
    uri = uri.endsWith('/') ? uri : uri + '/';
    const bodyHash: string = bytesToHexStr(cryptoSha256(cryptoUtf8(body)));
    let canonicalHeaders: string = '';
    for (const k of signed) {
      canonicalHeaders += `${k}:${this.lookupHeader(headers, k)}\n`;
    }
    const canonicalRequest: string = `${method}\n${uri}\n\n${canonicalHeaders}\n` +
      `${signed.join(';')}\n${bodyHash}`;
    // ---- 3. StringToSign 与派生密钥 ----
    const t: string = headers['X-Sdk-Date'];
    const info: string = `${t.substring(0, 8)}/${CarConfig.CLOUD_REGION}/iotdm`;
    const stringToSign: string = `V11-HMAC-SHA256\n${t}\n${info}\n` +
      bytesToHexStr(cryptoSha256(cryptoUtf8(canonicalRequest)));
    const derKey: string = hkdfDerivedKey(this.ak, this.sk, info);
    // ---- 4. 签名 ----
    const signature: string = bytesToHexStr(cryptoHmacSha256(cryptoUtf8(derKey), cryptoUtf8(stringToSign)));
    headers['Authorization'] = `V11-HMAC-SHA256 Credential=${this.ak}/${info}, ` +
      `SignedHeaders=${signed.join(';')}, Signature=${signature}`;
    // ---- 5. 发送 ----
    const req: http.HttpRequest = http.createHttp();
    try {
      const opt: http.HttpRequestOptions = {
        method: method === 'POST' ? http.RequestMethod.POST : http.RequestMethod.GET,
        header: headers,
        extraData: body.length > 0 ? body : undefined,
        connectTimeout: 10000,
        readTimeout: 15000,
        expectDataType: http.HttpDataType.STRING
      };
      const resp = await req.request(url, opt);
      result.code = resp.responseCode;
      if (typeof resp.result === 'string') {
        result.body = resp.result;
      }
    } catch (e) {
      const err = e as BusinessError;
      result.code = -1;
      result.body = `网络错误(${err.code}): ${err.message}`;
    } finally {
      req.destroy();
    }
    return result;
  }

  /** 从 URL 提取路径部分 */
  private urlPathOf(url: string): string {
    const schemeEnd: number = url.indexOf('//');
    const pathStart: number = url.indexOf('/', schemeEnd + 2);
    return pathStart < 0 ? '/' : url.substring(pathStart);
  }

  /** 按小写键取头值（signed 数组已小写，headers 键可能大小写混合） */
  private lookupHeader(headers: Record<string, string>, lowerKey: string): string {
    const keys: Array<string> = Object.keys(headers);
    for (const k of keys) {
      if (k.toLowerCase() === lowerKey) {
        return headers[k].trim();
      }
    }
    return '';
  }
}
