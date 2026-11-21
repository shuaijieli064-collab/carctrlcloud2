# -*- coding: utf-8 -*-
"""
app_mqtt_client.py — APP 替身（打桩），站在 APP 位置验证 IoT 平台链路（课程模块④：集成测试）

以"平台应用"身份接入 broker，向车端设备下发命令并订阅其属性上报，跑通 8 项用例：
  1) 应用鉴权接入  2) 前进指令生效  3) 急停归零  4) 转向生效
  5) ping 不中断上报  6) 非法报文容错  7) 缺字段拒绝  8) 错误密钥被平台拒绝

用法：
  python app_mqtt_client.py                 # 跑 8 项自动用例
  python app_mqtt_client.py --interactive   # 交互模式：手动发命令
"""

import argparse
import json
import time

import mqtt_client as mqtt
import mqtt_protocol as proto


class TestHarness:
    def __init__(self, host, port, device_id, app_id):
        self.host = host
        self.port = port
        self.device_id = device_id
        self.app_id = app_id
        self.props = []          # 收到的属性快照列表
        self.lock = __import__("threading").Lock()
        self.client = None

    def on_property(self, topic, payload):
        props = proto.decode_property_report(payload)
        if props:
            with self.lock:
                self.props.append(props)

    def latest(self):
        with self.lock:
            return self.props[-1] if self.props else None

    def wait_property(self, timeout=2.0):
        t0 = time.time()
        n0 = len(self.props)
        while time.time() - t0 < timeout:
            with self.lock:
                if len(self.props) > n0:
                    return self.props[-1]
            time.sleep(0.02)
        return None

    def connect(self):
        secret = proto.APP_REGISTRY[self.app_id]
        ts = int(time.time())
        c = mqtt.MiniMqttClient(
            host=self.host, port=self.port,
            client_id=proto.make_client_id(self.app_id, ts),
            username=self.app_id,
            password=proto.make_iot_password(self.app_id, secret, ts),
            keepalive=30,
        )
        c.on_message = self.on_property
        if not c.connect():
            return None
        c.subscribe(proto.property_report_topic(self.device_id), qos=0)
        self.client = c
        return c

    def send_command(self, name, paras=None):
        topic = proto.command_pub_topic(self.device_id, name)
        self.client.publish(topic, proto.encode_command(name, paras), qos=1)


def run_tests(host, port, device_id, app_id):
    h = TestHarness(host, port, device_id, app_id)
    results = []

    def check(name, ok, detail=""):
        results.append((name, ok, detail))
        print(f"[{'PASS' if ok else 'FAIL'}] {name}" + (f"  ({detail})" if detail else ""))

    # 用例1：应用鉴权接入
    c = h.connect()
    check("1. 应用以正确密钥接入平台", c is not None, "CONNACK 接受" if c else "连接失败")
    if c is None:
        _report(results)
        return

    time.sleep(0.5)
    check("2. 能收到车端属性上报", h.latest() is not None,
          f"speed={h.latest().get('speed')}" if h.latest() else "无数据")

    # 用例2：前进指令（发令后等待足时长，再读最新属性，避免只量到起步瞬间）
    # 位移按欧氏距离计算，不依赖车头初始朝向（车端状态跨轮次持久，heading 可能非 0）
    base = h.latest() or {"x": 0, "y": 0}
    x0 = base.get("x", 0.0)
    y0 = base.get("y", 0.0)
    h.send_command(proto.COMMAND_MOVE, {"lx": 0.0, "ly": -1.0})
    time.sleep(2.0)
    p = h.latest()
    x1 = p.get("x", x0) if p else x0
    y1 = p.get("y", y0) if p else y0
    dist = ((x1 - x0) ** 2 + (y1 - y0) ** 2) ** 0.5
    moved = dist > 0.5
    check("3. 前进指令(move ly=-1)使位置变化", moved,
          f"位移={dist:.3f}m (Δx={x1 - x0:+.3f}, Δy={y1 - y0:+.3f})")

    # 用例3：急停归零
    h.send_command(proto.COMMAND_STOP)
    time.sleep(1.0)
    p = h.latest()
    sp = p.get("speed", 1.0) if p else 1.0
    check("4. 急停(stop)后速度归零", abs(sp) < 0.05, f"speed={sp}")

    # 用例4：转向
    h0 = (h.latest() or {}).get("heading", 0.0)
    h.send_command(proto.COMMAND_MOVE, {"lx": 0.6, "ly": 0.0})
    time.sleep(2.0)
    p = h.latest()
    h1 = p.get("heading", h0) if p else h0
    turned = abs(((h1 - h0 + 180) % 360) - 180) > 10
    check("5. 转向指令(lx=0.6)使朝向变化", turned, f"Δheading={((h1-h0+180)%360)-180:+.1f}°")
    h.send_command(proto.COMMAND_STOP)
    time.sleep(0.5)

    # 用例5：ping 不中断上报
    n0 = len(h.props)
    h.send_command(proto.COMMAND_PING)
    time.sleep(0.6)
    check("6. ping 命令不中断属性上报", len(h.props) > n0 + 2, f"新增{len(h.props)-n0}帧")

    # 用例6：非法报文容错（非 JSON）
    n0 = len(h.props)
    h.client.publish(proto.command_pub_topic(device_id, "move"),
                     "this-is-not-json", qos=1)
    time.sleep(0.6)
    check("7. 非法报文后属性仍持续（设备未崩）", len(h.props) > n0 + 2, f"新增{len(h.props)-n0}帧")

    # 用例7：缺字段拒绝（move 仅 lx）
    h.send_command(proto.COMMAND_MOVE, {"lx": 0.9})   # 缺 ly
    time.sleep(0.6)
    h.send_command(proto.COMMAND_STOP)
    time.sleep(1.0)
    p = h.latest()
    sp = p.get("speed", 1.0) if p else 1.0
    check("8. 缺字段指令被拒绝（发 stop 后仍归零）", abs(sp) < 0.05, f"speed={sp}")

    # 用例8：错误密钥被平台拒绝
    bad = mqtt.MiniMqttClient(host=host, port=port, client_id="app_controller_x",
                              username="app_controller", password="wrong",
                              keepalive=30)
    ok_bad = bad.connect()
    check("9. 错误密钥被平台拒绝接入", ok_bad is False, "CONNACK 拒绝" if not ok_bad else "意外接受")
    if ok_bad:
        bad.disconnect()

    h.client.disconnect()
    _report(results)


def _report(results):
    passed = sum(1 for _, ok, _ in results if ok)
    print("\n" + "=" * 48)
    print(f"结果：{passed}/{len(results)} 通过")
    print("=" * 48)


def interactive(host, port, device_id, app_id):
    h = TestHarness(host, port, device_id, app_id)
    c = h.connect()
    if c is None:
        print("[ERR ] 接入失败")
        return
    c.subscribe(proto.command_sub_topic(device_id), qos=0)
    print("交互模式：输入 'move lx ly' / 'stop' / 'ping' / 'quit'")
    while True:
        try:
            line = input("> ").strip()
        except EOFError:
            break
        if not line or line == "quit":
            break
        if line == "stop":
            h.send_command(proto.COMMAND_STOP)
        elif line == "ping":
            h.send_command(proto.COMMAND_PING)
        elif line.startswith("move"):
            parts = line.split()
            lx = float(parts[1]) if len(parts) > 1 else 0.0
            ly = float(parts[2]) if len(parts) > 2 else -1.0
            h.send_command(proto.COMMAND_MOVE, {"lx": lx, "ly": ly})
        p = h.latest()
        if p:
            print(f"    当前属性: speed={p.get('speed')} heading={p.get('heading')} "
                  f"x={p.get('x')} y={p.get('y')} battery={p.get('battery')}")


def main():
    parser = argparse.ArgumentParser(description="APP 替身：IoT 平台链路打桩测试")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=1883)
    parser.add_argument("--device-id", default=proto.DEFAULT_DEVICE_ID)
    parser.add_argument("--app-id", default=proto.DEFAULT_APP_ID)
    parser.add_argument("--interactive", action="store_true")
    args = parser.parse_args()
    if args.interactive:
        interactive(args.host, args.port, args.device_id, args.app_id)
    else:
        run_tests(args.host, args.port, args.device_id, args.app_id)


if __name__ == "__main__":
    main()
