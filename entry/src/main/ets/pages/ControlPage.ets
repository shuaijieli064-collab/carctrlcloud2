import { CarConfig } from '../common/CarConfig';
import { TcpClient, ConnState } from '../net/TcpClient';
import { MqttClient, MqttConnState, MqttMessage } from '../net/MqttClient';
import { IotBridge } from '../net/IotBridge';
import { HuaweiCloudClient, CloudState } from '../net/HuaweiCloudClient';
import { CarDownlink, CommandProtocol } from '../model/CommandProtocol';
import { Joystick } from '../view/Joystick';

/**
 * 操控页（课程模块①②③④⑤ 集成）
 * 组成：连接状态栏 + 模式切换 + 连接配置 + 小车状态面板 + 摇杆 + 日志区
 *
 * 模块④：云端（IoTDA 风格）—— APP 以"应用"身份接入本地 MQTT broker（模拟华为云 IoTDA）。
 * 模块⑤：华为云直连 —— APP 以真实华为云账号经 IAM 登录，走应用侧 HTTPS API：
 *             设备影子轮询（实时属性）+ 命令下发（move 异步 / stop·ping 同步），
 *             直连真实 IoTDA 实例，无需电脑桥接。
 */
@Entry
@Component
struct ControlPage {
  /** 接入模式：tcp 直连 / iot 本地平台 / cloud 华为云直连（默认云端，见 CarConfig.DEFAULT_MODE） */
  @State mode: 'tcp' | 'iot' | 'cloud' = CarConfig.DEFAULT_MODE;

  /** 连接配置（tcp/iot 模式） */
  @State ipText: string = CarConfig.IOT_BROKER_HOST;
  @State portText: string = `${CarConfig.IOT_BROKER_PORT}`;
  /** 华为云登录配置（cloud 模式） */
  @State userText: string = '';   // cloud 模式：Access Key（AK）
  @State pwdText: string = '';    // cloud 模式：Secret Access Key（SK）
  @State projText: string = CarConfig.CLOUD_PROJECT_ID;
  @State deviceIdText: string = CarConfig.CLOUD_DEVICE_ID;
  /** 连接状态（三种模式的语义一致，统一表示） */
  @State connState: 'disconnected' | 'connecting' | 'connected' | 'reconnecting' = 'disconnected';
  @State connInfo: string = '未连接';
  /** 小车状态（由状态流驱动） */
  @State speed: number = 0;
  @State battery: number = 100;
  @State heading: number = 0;
  @State carX: number = 0;
  @State carY: number = 0;
  /** 最近一次指令回执 / 下发命令 */
  @State lastAck: string = '无';
  /** 日志 */
  @State logText: string = '';

  private tcpClient: TcpClient = new TcpClient();
  private mqttClient: MqttClient = new MqttClient();
  private huaweiClient: HuaweiCloudClient = new HuaweiCloudClient();
  private lastSendTime: number = 0;
  /** 摇杆输出平滑状态（一阶低通，抑制抖动） */
  private smoothX: number = 0;
  private smoothY: number = 0;

  aboutToAppear(): void {
    // TCP 直连客户端回调
    this.tcpClient.onStateChange = (state: ConnState, info: string) => {
      this.connState = state;
      this.connInfo = info;
      this.appendLog(info);
    };
    this.tcpClient.onDownlink = (msg: CarDownlink) => {
      this.handleDownlink(msg);
    };
    // IoT 平台客户端回调
    this.mqttClient.onStateChange = (state: MqttConnState, info: string) => {
      this.connState = state;
      this.connInfo = info;
      this.appendLog(info);
    };
    this.mqttClient.onMessage = (msg: MqttMessage) => {
      this.handleIotMessage(msg);
    };
    // 华为云直连客户端回调
    this.huaweiClient.onStateChange = (state: CloudState, info: string) => {
      this.connState = state === 'error' ? 'disconnected' : state;
      this.connInfo = info;
      this.appendLog(info);
    };
    this.huaweiClient.onSnapshot = (snap) => {
      this.speed = snap.speed;
      this.battery = snap.battery;
      this.heading = snap.heading;
      this.carX = snap.x;
      this.carY = snap.y;
    };
    this.huaweiClient.onAck = (info: string) => {
      this.lastAck = info;
    };
  }

  aboutToDisappear(): void {
    this.tcpClient.destroy();
    this.mqttClient.destroy();
    this.huaweiClient.destroy();
  }

  private appendLog(line: string): void {
    const time = new Date().toLocaleTimeString();
    this.logText = `[${time}] ${line}\n${this.logText}`;
    const lines = this.logText.split('\n');
    if (lines.length > 30) {
      this.logText = lines.slice(0, 30).join('\n');
    }
  }

  /** TCP 直连：解析服务端 ack / state */
  private handleDownlink(msg: CarDownlink): void {
    if (msg.type === 'ack') {
      this.lastAck = `seq=${msg.seq ?? '-'} ok=${msg.ok === true}`;
    } else if (msg.type === 'state') {
      this.speed = msg.speed ?? 0;
      this.battery = msg.battery ?? 0;
      this.heading = msg.heading ?? 0;
      this.carX = msg.x ?? 0;
      this.carY = msg.y ?? 0;
    }
  }

  /** IoT 平台：解析设备属性上报（仅处理属性 topic） */
  private handleIotMessage(msg: MqttMessage): void {
    if (msg.topic !== IotBridge.propertyReportTopic(CarConfig.IOT_DEVICE_ID)) {
      return;
    }
    const snap = IotBridge.decodePropertyReport(msg.payload);
    if (snap) {
      this.speed = snap.speed;
      this.battery = snap.battery;
      this.heading = snap.heading;
      this.carX = snap.x;
      this.carY = snap.y;
    }
  }

  private stateColor(): string {
    if (this.connState === 'connected') {
      return '#16A34A';
    } else if (this.connState === 'connecting' || this.connState === 'reconnecting') {
      return '#D97706';
    }
    return '#DC2626';
  }

  private stateLabel(): string {
    if (this.connState === 'connected') {
      return '已连接';
    } else if (this.connState === 'connecting') {
      return '连接中';
    } else if (this.connState === 'reconnecting') {
      return '重连中';
    }
    return '未连接';
  }

  /** 切换接入模式；切换前若已连接则先断开 */
  private switchMode(m: 'tcp' | 'iot' | 'cloud'): void {
    if (this.connState !== 'disconnected') {
      this.disconnectFromCar();
    }
    this.mode = m;
    if (m === 'tcp') {
      this.ipText = CarConfig.DEFAULT_IP;
      this.portText = `${CarConfig.DEFAULT_PORT}`;
    } else if (m === 'iot') {
      this.ipText = CarConfig.IOT_BROKER_HOST;
      this.portText = `${CarConfig.IOT_BROKER_PORT}`;
    }
    this.appendLog(`已切换至 ${m === 'tcp' ? 'TCP 直连' : (m === 'iot' ? 'IoT 平台(本地)' : '华为云直连')} 模式`);
  }

  private connectToCar(): void {
    if (this.connState === 'connected' || this.connState === 'connecting' || this.connState === 'reconnecting') {
      this.appendLog('已在连接流程中，忽略重复连接');
      return;
    }
    if (this.mode === 'cloud') {
      this.connectCloud();
      return;
    }
    const port = Number(this.portText);
    if (this.ipText.length === 0 || Number.isNaN(port) || port <= 0 || port > 65535) {
      this.appendLog('地址或端口格式不正确');
      return;
    }
    if (this.mode === 'tcp') {
      this.tcpClient.connect(this.ipText, port);
    } else {
      this.connectIot();
    }
  }

  /** 华为云直连：AK/SK 凭证校验 → 成功后开始影子轮询（状态流自动驱动 UI） */
  private connectCloud(): void {
    if (this.userText.length === 0 || this.pwdText.length === 0) {
      this.appendLog('请填写 Access Key 与 Secret Key（credentials.csv）');
      this.connInfo = '未连接（缺少 AK/SK）';
      return;
    }
    if (this.deviceIdText.length === 0) {
      this.appendLog('请填写云端设备 ID');
      return;
    }
    this.connState = 'connecting';
    this.connInfo = '校验 AK/SK 中...';
    this.huaweiClient.deviceId = this.deviceIdText;
    this.huaweiClient.login(this.userText, this.pwdText, this.projText)
      .then((ok: boolean) => {
        if (ok) {
          this.connState = 'connected';
          this.connInfo = `华为云已连接（设备 ${this.deviceIdText.substring(this.deviceIdText.length - 7)}）`;
          this.appendLog('华为云直连成功，开始影子轮询（1s）');
          this.huaweiClient.startPolling();
        }
      })
      .catch((e: Error) => {
        this.connState = 'disconnected';
        this.connInfo = '未连接';
        this.appendLog(`华为云连接失败：${e.message}`);
      });
  }

  /** IoT 平台接入：以"应用"身份鉴权、订阅属性、连接 broker */
  private connectIot(): void {
    const ts: number = Math.floor(Date.now() / 1000);
    const clientId: string = IotBridge.makeClientId(CarConfig.IOT_APP_ID, ts);
    const pwd: string = IotBridge.makeIotPassword(CarConfig.IOT_APP_ID, CarConfig.IOT_APP_SECRET, ts);
    // 预先排队订阅属性上报，CONNACK 后由客户端自动补订阅
    this.mqttClient.subscribe(IotBridge.propertyReportTopic(CarConfig.IOT_DEVICE_ID), 0);
    this.mqttClient.connect(this.ipText, Number(this.portText), clientId, CarConfig.IOT_APP_ID, pwd, 30);
  }

  private disconnectFromCar(): void {
    if (this.mode === 'tcp') {
      this.tcpClient.disconnect();
    } else if (this.mode === 'iot') {
      this.mqttClient.disconnect();
    } else {
      this.huaweiClient.stopPolling();
      this.connState = 'disconnected';
      this.connInfo = '未连接';
      this.appendLog('已断开华为云连接');
    }
  }

  /** 摇杆移动：节流 + 死区 + 低通平滑后下发 move 指令（云端模式节流放宽至 200ms） */
  private onJoystickMove(x: number, y: number): void {
    const throttle = this.mode === 'cloud' ? CarConfig.CLOUD_SEND_INTERVAL_MS : CarConfig.SEND_INTERVAL_MS;
    const now = new Date().getTime();
    if (now - this.lastSendTime < throttle) {
      return;
    }
    this.lastSendTime = now;
    if (this.connState !== 'connected') {
      return; // 断线期间指令静默丢弃（服务端保持急停语义）
    }
    // 死区：中心附近微小抖动归零，保证直行稳定
    const dz = 0.08;
    const ax = Math.abs(x) < dz ? 0 : x;
    const ay = Math.abs(y) < dz ? 0 : y;
    // 一阶低通平滑：抑制手部抖动与手势突变导致的转向跳变
    this.smoothX = this.smoothX * 0.45 + ax * 0.55;
    this.smoothY = this.smoothY * 0.45 + ay * 0.55;
    const lx = Math.round(this.smoothX * 100) / 100;
    const ly = Math.round(this.smoothY * 100) / 100;
    if (this.mode === 'tcp') {
      this.tcpClient.send(CommandProtocol.buildMove(lx, ly));
    } else if (this.mode === 'iot') {
      const topic = IotBridge.commandPubTopic(CarConfig.IOT_DEVICE_ID, IotBridge.COMMAND_MOVE);
      this.mqttClient.publish(topic, IotBridge.buildMovePayload(lx, ly), 0);
      this.lastAck = `下发 move(${lx.toFixed(2)},${ly.toFixed(2)})`;
    } else {
      const paras: Record<string, number> = {};
      paras['lx'] = lx;
      paras['ly'] = ly;
      // MQTT 设备不支持 async-commands 接口（IOTDA.014114），move 也走同步命令接口
      this.huaweiClient.sendCommandSync(IotBridge.COMMAND_MOVE, paras);
      this.lastAck = `云端 move(${lx.toFixed(2)},${ly.toFixed(2)})`;
    }
  }

  /** 摇杆松手：清空平滑状态并下发急停 */
  private onJoystickRelease(): void {
    this.smoothX = 0;
    this.smoothY = 0;
    if (this.connState === 'connected') {
      if (this.mode === 'tcp') {
        this.tcpClient.send(CommandProtocol.buildStop());
      } else if (this.mode === 'iot') {
        const topic = IotBridge.commandPubTopic(CarConfig.IOT_DEVICE_ID, IotBridge.COMMAND_STOP);
        this.mqttClient.publish(topic, IotBridge.buildStopPayload(), 0);
        this.lastAck = '下发 stop';
      } else {
        const empty: Record<string, number> = {};
        this.huaweiClient.sendCommandSync(IotBridge.COMMAND_STOP, empty);
        this.lastAck = '云端 stop 已提交';
      }
    }
  }

  build() {
    Column({ space: 10 }) {
      // ===== 标题与连接状态栏 =====
      Row() {
        Text('寻检车操控')
          .fontSize(20)
          .fontWeight(FontWeight.Bold)
          .fontColor('#12325E')
          .layoutWeight(1)
        Circle().width(10).height(10).fill(this.stateColor())
        Text(this.stateLabel())
          .fontSize(14)
          .fontColor(this.stateColor())
          .margin({ left: 6 })
      }
      .width('100%')
      Text(this.connInfo)
        .fontSize(11)
        .fontColor('#666666')
        .width('100%')

      // ===== 模式切换（云端为主；TCP 直连仅当 ENABLE_TCP_DEBUG=true 时显示） =====
      Row({ space: 8 }) {
        Text('接入模式')
          .fontSize(13)
          .fontColor('#374151')
          .layoutWeight(1)
        if (CarConfig.ENABLE_TCP_DEBUG) {
          Button('TCP 直连', { type: ButtonType.Capsule })
            .height(34)
            .fontSize(13)
            .backgroundColor(this.mode === 'tcp' ? '#1A5CB0' : '#E5E7EB')
            .fontColor(this.mode === 'tcp' ? '#FFFFFF' : '#374151')
            .onClick(() => {
              this.switchMode('tcp');
            })
        }
        Button('IoT 平台', { type: ButtonType.Capsule })
          .height(34)
          .fontSize(13)
          .backgroundColor(this.mode === 'iot' ? '#1A5CB0' : '#E5E7EB')
          .fontColor(this.mode === 'iot' ? '#FFFFFF' : '#374151')
          .onClick(() => {
            this.switchMode('iot');
          })
        Button('华为云', { type: ButtonType.Capsule })
          .height(34)
          .fontSize(13)
          .backgroundColor(this.mode === 'cloud' ? '#C72921' : '#E5E7EB')
          .fontColor(this.mode === 'cloud' ? '#FFFFFF' : '#374151')
          .onClick(() => {
            this.switchMode('cloud');
          })
      }
      .width('100%')

      // ===== 连接配置区（cloud 模式为华为云账号；其他模式为 IP/端口） =====
      if (this.mode === 'cloud') {
        TextInput({ text: this.userText, placeholder: 'Access Key（AK，credentials.csv 第2列）' })
          .height(40)
          .fontSize(13)
          .width('100%')
          .onChange((value: string) => {
            this.userText = value;
          })
        TextInput({ text: this.pwdText, placeholder: 'Secret Access Key（SK，credentials.csv 第3列）' })
          .height(40)
          .fontSize(13)
          .width('100%')
          .type(InputType.Password)
          .onChange((value: string) => {
            this.pwdText = value;
          })
        TextInput({ text: this.projText, placeholder: '项目 ID（cn-north-4）' })
          .height(40)
          .fontSize(13)
          .width('100%')
          .onChange((value: string) => {
            this.projText = value;
          })
        TextInput({ text: this.deviceIdText, placeholder: '云端设备 ID' })
          .height(40)
          .fontSize(13)
          .width('100%')
          .onChange((value: string) => {
            this.deviceIdText = value;
          })
      } else {
        Row({ space: 8 }) {
          TextInput({ text: this.ipText, placeholder: this.mode === 'tcp' ? '小车IP（模拟器用 10.0.2.2）' : 'Broker（真机填电脑局域网IP，如 10.234.215.153）' })
            .height(40)
            .fontSize(13)
            .layoutWeight(3)
            .onChange((value: string) => {
              this.ipText = value;
            })
          TextInput({ text: this.portText, placeholder: '端口' })
            .height(40)
            .fontSize(13)
            .layoutWeight(1)
            .type(InputType.Number)
            .onChange((value: string) => {
              this.portText = value;
            })
        }
        .width('100%')
      }

      Row({ space: 12 }) {
        Button('连接小车', { type: ButtonType.Capsule })
          .height(40)
          .fontSize(15)
          .layoutWeight(1)
          .backgroundColor(this.connState === 'connected' ? '#9CA3AF' : '#16A34A')
          .onClick(() => {
            this.connectToCar();
          })
        Button('断开', { type: ButtonType.Capsule })
          .height(40)
          .fontSize(15)
          .layoutWeight(1)
          .backgroundColor('#DC2626')
          .onClick(() => {
            this.disconnectFromCar();
          })
      }
      .width('100%')

      // ===== 小车状态面板 =====
      Row({ space: 8 }) {
        Column({ space: 4 }) {
          Text('速度').fontSize(11).fontColor('#666666')
          Text(this.speed.toFixed(2)).fontSize(18).fontWeight(FontWeight.Bold).fontColor('#12325E')
        }
        .layoutWeight(1)
        .padding(8)
        .borderRadius(8)
        .backgroundColor('#F2F6FC')

        Column({ space: 4 }) {
          Text('电量').fontSize(11).fontColor('#666666')
          Text(`${this.battery.toFixed(0)}%`).fontSize(18).fontWeight(FontWeight.Bold).fontColor('#16A34A')
        }
        .layoutWeight(1)
        .padding(8)
        .borderRadius(8)
        .backgroundColor('#F2F6FC')

        Column({ space: 4 }) {
          Text('朝向').fontSize(11).fontColor('#666666')
          Text(`${this.heading.toFixed(0)}°`).fontSize(18).fontWeight(FontWeight.Bold).fontColor('#1A5CB0')
        }
        .layoutWeight(1)
        .padding(8)
        .borderRadius(8)
        .backgroundColor('#F2F6FC')

        Column({ space: 4 }) {
          Text('位置').fontSize(11).fontColor('#666666')
          Text(`(${this.carX.toFixed(1)},${this.carY.toFixed(1)})`)
            .fontSize(14).fontWeight(FontWeight.Bold).fontColor('#EA580C')
        }
        .layoutWeight(1.4)
        .padding(8)
        .borderRadius(8)
        .backgroundColor('#F2F6FC')
      }
      .width('100%')

      Text(`最近回执：${this.lastAck}`)
        .fontSize(11)
        .fontColor('#666666')
        .width('100%')

      // ===== 摇杆 =====
      Column({ space: 6 }) {
        Joystick({
          onMove: (x: number, y: number): void => {
            this.onJoystickMove(x, y);
          },
          onRelease: (): void => {
            this.onJoystickRelease();
          }
        })
        Text(this.connState === 'connected'
          ? '拖动摇杆控制小车 · 松手即急停'
          : (this.mode === 'tcp' ? '未连接：请先连接小车（10.0.2.2:8888）'
            : (this.mode === 'iot' ? `未连接：请先连接 IoT 平台（${CarConfig.IOT_BROKER_HOST}:${CarConfig.IOT_BROKER_PORT}）`
              : '未连接：请填写 AK/SK 凭证后连接')))
          .fontSize(12)
          .fontColor(this.connState === 'connected' ? '#12325E' : '#DC2626')
      }
      .layoutWeight(1)
      .justifyContent(FlexAlign.Center)
      .width('100%')

      // ===== 日志区 =====
      Scroll() {
        Text(this.logText.length > 0 ? this.logText : '日志区：连接与通信事件将显示在这里')
          .fontSize(10)
          .fontColor('#666666')
          .width('100%')
      }
      .height(90)
      .width('100%')
      .padding(6)
      .borderRadius(6)
      .backgroundColor('#F5F7FA')
      .align(Alignment.TopStart)
    }
    .padding(14)
    .width('100%')
    .height('100%')
    .backgroundColor('#FFFFFF')
  }
}
