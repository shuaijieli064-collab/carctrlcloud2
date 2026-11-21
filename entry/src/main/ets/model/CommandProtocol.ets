/**
 * 指令协议定义与编解码（JSON 文本帧 + 换行符分帧）
 *
 * APP → 车端（指令）：
 *   {"type":"move","seq":12,"lx":0.50,"ly":-1.00,"ts":1760000000000}
 *   {"type":"stop","seq":13}
 *   {"type":"ping","seq":14}
 * 车端 → APP（回执/状态）：
 *   {"type":"ack","seq":12,"ok":true}
 *   {"type":"pong","seq":14}
 *   {"type":"state","speed":0.60,"battery":87,"heading":90,"x":1.2,"y":3.4}
 *   {"type":"error","msg":"..."}
 *
 * 协议约束：字段全小写；seq 单调递增用于回执匹配；ly 取负值为前进。
 */

/** APP 下发指令结构 */
export interface CarCommand {
  type: string;
  seq?: number;
  lx?: number;
  ly?: number;
  ts?: number;
}

/** 车端下行报文结构（ack / pong / state / error / hello） */
export interface CarDownlink {
  type: string;
  seq?: number;
  ok?: boolean;
  speed?: number;
  battery?: number;
  heading?: number;
  x?: number;
  y?: number;
  msg?: string;
}

export class CommandProtocol {
  private static seqCounter: number = 0;

  /** 获取下一个单调递增的指令序号 */
  static nextSeq(): number {
    CommandProtocol.seqCounter += 1;
    return CommandProtocol.seqCounter;
  }

  /** 构造 move 指令：lx/ly 为摇杆归一化偏移 [-1,1]，ly 负值为前进 */
  static buildMove(lx: number, ly: number): string {
    const cmd: CarCommand = {
      type: 'move',
      seq: CommandProtocol.nextSeq(),
      lx: lx,
      ly: ly,
      ts: new Date().getTime()
    };
    return CommandProtocol.encode(cmd);
  }

  /** 构造急停指令 */
  static buildStop(): string {
    const cmd: CarCommand = { type: 'stop', seq: CommandProtocol.nextSeq() };
    return CommandProtocol.encode(cmd);
  }

  /** 构造心跳指令 */
  static buildPing(): string {
    const cmd: CarCommand = { type: 'ping', seq: CommandProtocol.nextSeq() };
    return CommandProtocol.encode(cmd);
  }

  /** 指令对象 → JSON 文本帧（含换行分帧符） */
  static encode(cmd: CarCommand): string {
    return JSON.stringify(cmd) + '\n';
  }

  /**
   * 把 TCP 收到的字节流文本按换行符切分成帧。
   * 返回 [完整帧数组, 剩余的不完整尾部]。
   */
  static splitFrames(raw: string): string[] {
    const frames: string[] = [];
    const parts: string[] = raw.split('\n');
    for (let i = 0; i < parts.length; i++) {
      const f = parts[i].trim();
      if (f.length > 0) {
        frames.push(f);
      }
    }
    return frames;
  }

  /** JSON 帧 → 下行报文对象；非法帧返回 null（容错不崩溃） */
  static parseDownlink(frame: string): CarDownlink | null {
    try {
      const obj = JSON.parse(frame) as CarDownlink;
      if (typeof obj.type !== 'string' || obj.type.length === 0) {
        return null;
      }
      return obj;
    } catch (e) {
      return null;
    }
  }
}
