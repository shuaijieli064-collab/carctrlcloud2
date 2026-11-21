# -*- coding: utf-8 -*-
"""
iotda_monitor.py — 华为云 IoTDA 影子监控桥（轨迹视图端在真平台拓扑下的数据源）

作用：把"华为云 IoTDA 设备影子"桥接到本地 WebSocket 俯视图（复用 webview.html）。
链路：APP/车端 → IoTDA 平台(影子) --HTTPS V11签名轮询--> 本脚本 --WS 广播--> 浏览器俯视图

用法：
  python iotda_monitor.py                       # 配置默认读 iotda_app_config.json
  python iotda_monitor.py --ws-port 8899        # 指定俯视图端口
  浏览器打开 http://127.0.0.1:8899 即为实时俯视图（小车真实轨迹，500ms 刷新）

签名：与 entry/src/main/ets/net/HuaweiCloudClient.ets 完全同构（以实测可用实现为准）：
  - CanonicalRequest: METHOD \\n URI(尾斜杠) \\n 空行 \\n Headers(每行 k:v\\n) \\n \\n SignedHeaders \\n SHA256(body)
  - HKDF：PRK=HMAC(key=AK,msg=SK)；DK=hex(HMAC(PRK, info+0x01))；info={YYYYMMDD}/{region}/iotdm
  - Signature=HMAC(key=utf8(DKhex), msg=StringToSign)；Authorization 头带 X-Project-Id / X-Sdk-Date
"""

import argparse
import hashlib
import hmac
import json
import os
import ssl
import sys
import threading
import time
import urllib.request
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
# 复用本地 broker 的最小 WebSocket 网关（HTTP 服务 webview.html + WS 升级 + 广播）
from mqtt_broker import ws_bridge_worker, ws_broadcast_state

# ---------------- 配置 ----------------
CFG_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "iotda_app_config.json")
DEFAULT_CFG = {
    "endpoint": "1dc089e08c.st1.iotda-app.cn-north-4.myhuaweicloud.com",
    "region": "cn-north-4",
    "project_id": "01a195e54c1572cb94866775d0be8c18",
    "device_id": "6ae6e66a7f2e6c302fa8ffd1_car_001",
    "ak": "",          # IAM 子用户 carapp 的 Access Key Id
    "sk": "",          # 对应 Secret Access Key（敏感，勿提交仓库）
    "poll_ms": 500,
    "ws_port": 8899,
}


def load_cfg() -> dict:
    cfg = dict(DEFAULT_CFG)
    if os.path.exists(CFG_PATH):
        try:
            with open(CFG_PATH, encoding="utf-8") as f:
                cfg.update(json.load(f))
        except Exception as e:
            print(f"[WARN] 配置文件解析失败：{e}，使用默认值")
    return cfg


# ---------------- V11 签名（与 ArkTS 实现逐行同构） ----------------

def utc_stamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def sha256_hex(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _hmac_hex(key: bytes, msg: bytes) -> str:
    return hmac.new(key, msg, hashlib.sha256).hexdigest()


def v11_signed_headers(method: str, path: str, ak: str, sk: str, project_id: str,
                       region: str, body: str = "") -> dict:
    """返回带 Authorization 的完整请求头（GET/POST 通用）。"""
    t = utc_stamp()
    headers = {
        "host": HOST[0],
        "X-Sdk-Date": t,
        "X-Project-Id": project_id,
    }
    if body:
        headers["Content-Type"] = "application/json"

    signed = sorted(k.lower() for k in headers)
    uri = path if path.endswith("/") else path + "/"
    body_hash = sha256_hex(body.encode("utf-8"))
    canonical_headers = "".join(f"{k}:{str(headers[[h for h in headers if h.lower() == k][0]]).strip()}\n"
                                for k in signed)
    canonical_request = f"{method}\n{uri}\n\n{canonical_headers}\n{';'.join(signed)}\n{body_hash}"

    info = f"{t[:8]}/{region}/iotdm"
    string_to_sign = f"{SIGN_PREFIX}\n{t}\n{info}\n{sha256_hex(canonical_request.encode('utf-8'))}"

    # HKDF：PRK=HMAC(key=AK, msg=SK) → DK=hex(HMAC(PRK, info+0x01))
    prk = hmac.new(ak.encode("utf-8"), sk.encode("utf-8"), hashlib.sha256).digest()
    dk_hex = _hmac_hex(prk, info.encode("utf-8") + b"\x01")
    signature = _hmac_hex(dk_hex.encode("utf-8"), string_to_sign.encode("utf-8"))

    headers["Authorization"] = (f"{SIGN_PREFIX} Credential={ak}/{info}, "
                                f"SignedHeaders={';'.join(signed)}, Signature={signature}")
    return headers


SIGN_PREFIX = ""   # 由命令行/配置决定（见 main），默认与 ArkTS 端保持一致
HOST = [""]


# ---------------- 影子轮询与广播 ----------------

def fetch_shadow(cfg: dict) -> dict | None:
    """V11 签名 GET 设备影子，返回 properties dict 或 None。"""
    path = f"/v5/iot/{cfg['project_id']}/devices/{cfg['device_id']}/shadow"
    url = f"https://{cfg['endpoint']}{path}"
    headers = v11_signed_headers("GET", path, cfg["ak"], cfg["sk"],
                                 cfg["project_id"], cfg["region"])
    req = urllib.request.Request(url, headers=headers, method="GET")
    ctx = ssl.create_default_context()
    with urllib.request.urlopen(req, timeout=10, context=ctx) as resp:
        data = json.loads(resp.read().decode("utf-8"))
    for svc in data.get("shadow", []):
        if svc.get("service_id") == "Car":
            return svc.get("reported", {}).get("properties", {})
    return None


def poll_loop(cfg: dict):
    interval = cfg.get("poll_ms", 500) / 1000.0
    last = None
    last_printed = None
    while True:
        try:
            props = fetch_shadow(cfg)
            if props is None:
                print("[WARN] 影子中无 Car 服务数据")
            else:
                last = props
                state = {
                    "type": "state",
                    "speed": props.get("speed", 0.0),
                    "battery": props.get("battery", 0),
                    "heading": props.get("heading", 0),
                    "x": props.get("x", 0.0),
                    "y": props.get("y", 0.0),
                }
                ws_broadcast_state(json.dumps(state))
                # 状态变化才打印（避免 500ms 一行刷屏，长跑时日志膨胀上百万字符）
                if state != last_printed:
                    print(f"[上报] x={state['x']:.1f} y={state['y']:.1f} heading={state['heading']:.0f} "
                          f"speed={state['speed']:.2f} battery={state['battery']:.1f}")
                    last_printed = dict(state)
        except urllib.error.HTTPError as e:
            body = ""
            try:
                body = e.read().decode("utf-8")[:160]
            except Exception:
                pass
            print(f"[ERR ] 影子查询 HTTP {e.code} {body}")
        except Exception as e:
            print(f"[ERR ] 影子查询失败：{e}")
        time.sleep(interval)


def main():
    global SIGN_PREFIX, HOST
    cfg = load_cfg()
    parser = argparse.ArgumentParser(description="IoTDA 影子 → WS 俯视图桥")
    parser.add_argument("--ws-port", type=int, default=cfg.get("ws_port", 8899))
    parser.add_argument("--poll-ms", type=int, default=cfg.get("poll_ms", 500))
    parser.add_argument("--ak", default=cfg.get("ak"))
    parser.add_argument("--sk", default=cfg.get("sk"))
    parser.add_argument("--device-id", default=cfg.get("device_id"))
    parser.add_argument("--prefix", default=None,
                        help="签名方案前缀；缺省时自动从 HuaweiCloudClient.ets 提取（单一真源）")
    args = parser.parse_args()

    if not args.ak or not args.sk:
        print("[ERR ] 缺少 AK/SK：请填入 iotda_app_config.json 或用 --ak/--sk 传入")
        return

    # 签名前缀：自动从 APP 端源码提取，保证与已实测可用的实现逐字节一致
    if args.prefix:
        SIGN_PREFIX = args.prefix
    else:
        import re
        ets = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                           "..", "entry", "src", "main", "ets", "net", "HuaweiCloudClient.ets")
        m = re.search(r"(V11-HMAC-\S+?) Credential=", open(ets, encoding="utf-8").read())
        if not m:
            print("[ERR ] 未能从 HuaweiCloudClient.ets 提取签名前缀，请用 --prefix 手工指定")
            return
        SIGN_PREFIX = m.group(1)
        print(f"[AUTH] 签名前缀（自 ArkTS 源码提取）= {SIGN_PREFIX}")
    HOST = [cfg["endpoint"]]
    cfg["ak"], cfg["sk"], cfg["device_id"] = args.ak, args.sk, args.device_id
    cfg["ws_port"], cfg["poll_ms"] = args.ws_port, args.poll_ms

    print(f"[BOOT] IoTDA 影子监控桥：{cfg['device_id']} @ {cfg['endpoint']}")
    print(f"[BOOT] 签名方案={SIGN_PREFIX} 轮询={cfg['poll_ms']}ms 俯视图=http://127.0.0.1:{cfg['ws_port']}")

    t = threading.Thread(target=ws_bridge_worker, args=("0.0.0.0", cfg["ws_port"]), daemon=True)
    t.start()
    print(f"[WS  ] 俯视图网关就绪 :{cfg['ws_port']}（浏览器打开 http://127.0.0.1:{cfg['ws_port']}）")

    poll_loop(cfg)


if __name__ == "__main__":
    main()
