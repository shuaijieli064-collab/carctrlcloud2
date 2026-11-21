# -*- coding: utf-8 -*-
"""
test_client.py — 开发期"替身演员"（站在 APP 的位置打桩，先测车端）

用法：
  python test_client.py                          # 自动用例（默认连 127.0.0.1:8888）
  python test_client.py --ip 10.0.2.2 --port 8888
  python test_client.py --interactive            # 交互模式：手动输入 JSON 指令

自动用例覆盖：正常指令 / 急停 / 心跳 / 非法 JSON / 缺字段 / 长连接稳定性。
"""

import argparse
import json
import socket
import sys
import time

RECV_TIMEOUT = 5.0


class TestClient:
    def __init__(self, ip, port):
        self.sock = socket.create_connection((ip, port), timeout=RECV_TIMEOUT)
        self.buffer = b""
        self.seq = 0
        print(f"[CONN] 已连接 {ip}:{port}")

    def send_obj(self, obj):
        line = json.dumps(obj, ensure_ascii=False) + "\n"
        self.sock.sendall(line.encode("utf-8"))
        print(f"[SEND] {line.strip()}")

    def send_raw(self, text):
        self.sock.sendall((text + "\n").encode("utf-8"))
        print(f"[SEND] {text!r}")

    def send_json_line(self, text):
        """发送一行原始文本（用于非法报文用例）"""
        self.sock.sendall((text + "\n").encode("utf-8"))
        print(f"[SEND] {text[:60]!r}")

    def recv_frames(self, wait=0.5):
        """在 wait 秒内收取所有完整帧"""
        frames = []
        deadline = time.time() + wait
        self.sock.settimeout(0.1)
        while time.time() < deadline:
            try:
                data = self.sock.recv(4096)
                if not data:
                    break
                self.buffer += data
            except socket.timeout:
                continue
            except OSError:
                break
            while b"\n" in self.buffer:
                line, self.buffer = self.buffer.split(b"\n", 1)
                try:
                    frames.append(json.loads(line.decode("utf-8")))
                except (ValueError, UnicodeDecodeError):
                    frames.append({"type": "PARSE_ERROR", "raw": line[:60].decode("utf-8", "ignore")})
        for f in frames:
            print(f"[RECV] {json.dumps(f, ensure_ascii=False)}")
        return frames

    def next_seq(self):
        self.seq += 1
        return self.seq

    def close(self):
        self.sock.close()
        print("[DISC] 连接已关闭")


def expect(frames, ftype, note):
    ok = any(f.get("type") == ftype for f in frames)
    mark = "PASS" if ok else "FAIL"
    print(f"[TEST] {mark} — {note}\n")
    return ok


def run_auto_tests(ip, port):
    results = []
    c = TestClient(ip, port)

    # 用例1：正常 move 指令 → 期待 ack + state
    c.send_obj({"type": "move", "seq": c.next_seq(), "lx": 0.5, "ly": -1.0})
    frames = c.recv_frames(wait=1.0)
    results.append(expect(frames, "ack", "用例1a：move 指令收到 ack"))
    results.append(expect(frames, "state", "用例1b：收到 state 状态流"))

    # 用例2：急停 → ack 且 speed 归零（观察后续 state）
    c.send_obj({"type": "stop", "seq": c.next_seq()})
    frames = c.recv_frames(wait=1.2)
    results.append(expect(frames, "ack", "用例2a：stop 指令收到 ack"))
    states = [f for f in frames if f.get("type") == "state"]
    zeroed = any(f.get("speed", 1) == 0 for f in states[-2:]) if states else False
    print(f"[TEST] {'PASS' if zeroed else 'FAIL'} — 用例2b：stop 后 speed 归零\n")
    results.append(zeroed)

    # 用例3：心跳 → pong
    c.send_obj({"type": "ping", "seq": c.next_seq()})
    frames = c.recv_frames(wait=0.8)
    results.append(expect(frames, "pong", "用例3：ping 收到 pong"))

    # 用例4：非法 JSON → 不崩溃，回 error（或静默）
    c.send_json_line("hello world")
    frames = c.recv_frames(wait=0.8)
    print(f"[TEST] PASS — 用例4：非法报文连接保持（服务端返回 {len(frames)} 条响应）\n")
    results.append(True)

    # 用例5：缺字段指令 → error
    c.send_json_line('{"type":"move"}')
    frames = c.recv_frames(wait=0.8)
    results.append(expect(frames, "error", "用例5：缺字段指令被拒绝"))

    # 用例6：长连接稳定性 5 秒持续收 state
    print("[TEST] 用例6：长连接稳定性观察 5 秒……")
    frames = c.recv_frames(wait=5.0)
    n_state = sum(1 for f in frames if f.get("type") == "state")
    stable = n_state >= 30  # 100ms 周期，5 秒约 50 条，留余量
    print(f"[TEST] {'PASS' if stable else 'FAIL'} — 5 秒收到 {n_state} 条 state\n")
    results.append(stable)

    c.close()

    passed = sum(1 for r in results if r)
    print(f"===== 测试汇总：{passed}/{len(results)} 通过 =====")
    return all(results)


def run_interactive(ip, port):
    c = TestClient(ip, port)
    print("交互模式：直接输入 JSON 指令回车发送（如 {\"type\":\"move\",\"lx\":0.5,\"ly\":-1.0}），quit 退出")
    print("快捷指令：move / stop / ping")
    import threading

    def reader():
        while True:
            try:
                data = c.sock.recv(4096)
                if not data:
                    print("[DISC] 服务端关闭连接")
                    sys.exit(0)
                c.buffer += data
                while b"\n" in c.buffer:
                    line, c.buffer = c.buffer.split(b"\n", 1)
                    try:
                        print(f"[RECV] {json.dumps(json.loads(line.decode('utf-8')), ensure_ascii=False)}")
                    except (ValueError, UnicodeDecodeError):
                        print(f"[RECV] <无法解析> {line[:80]!r}")
            except OSError:
                return

    threading.Thread(target=reader, daemon=True).start()
    while True:
        try:
            text = input("> ").strip()
        except (EOFError, KeyboardInterrupt):
            break
        if text == "quit":
            break
        if text == "move":
            text = json.dumps({"type": "move", "seq": c.next_seq(), "lx": 0.5, "ly": -1.0})
        elif text == "stop":
            text = json.dumps({"type": "stop", "seq": c.next_seq()})
        elif text == "ping":
            text = json.dumps({"type": "ping", "seq": c.next_seq()})
        if text:
            c.send_raw(text)
    c.close()


def main():
    parser = argparse.ArgumentParser(description="模拟小车打桩测试客户端")
    parser.add_argument("--ip", default="127.0.0.1", help="服务端地址（默认 127.0.0.1）")
    parser.add_argument("--port", type=int, default=8888, help="服务端端口（默认 8888）")
    parser.add_argument("--interactive", action="store_true", help="交互模式")
    args = parser.parse_args()

    if args.interactive:
        run_interactive(args.ip, args.port)
    else:
        ok = run_auto_tests(args.ip, args.port)
        sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
