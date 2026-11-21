# -*- coding: utf-8 -*-
"""
car_mqtt_device.py — 车端以 IoT 设备身份接入（课程模块④/⑤：本地模拟 与 华为云 IoTDA 真平台）

两种模式：
  local（默认）：连本地 mqtt_broker.py 模拟的 IoTDA（教学鉴权模板）。
  real        ：直连华为云 IoTDA 真平台，鉴权严格按官方规范：
                  clientId = {deviceId}_0_{sig}_{ts}    ts = UTC 时间，格式 YYYYMMDDHH
                  username = {deviceId}
                  password = HMAC-SHA256(key=ts, message=secret) 的十六进制摘要
                  sig = 0 不校验时间戳（默认，时钟偏差不影响） / 1 校验时间戳
                端口：1883(MQTT 明文) 或 8883(MQTTS, 加 --tls)
                命令响应：向 $oc/devices/{id}/sys/commands/response/request_id={rid} 回执。

用法：
  python car_mqtt_device.py
  python car_mqtt_device.py --platform real --host <接入域名> --port 1883 \
      --device-id <设备ID> --secret <设备密钥>
  python car_mqtt_device.py --platform real --host <接入域名> --port 8883 --tls \
      --device-id <设备ID> --secret <设备密钥> [--ca huawei_ca.pem]
"""

import argparse
import hashlib
import hmac
import json
import os
import ssl
import time
from datetime import datetime, timezone

import mqtt_client as mqtt
import mqtt_protocol as proto
from car_model import CarState, STATE_INTERVAL

# 固定场景可把参数写入同目录 iotda_config.json，命令行即可全部省略：
#   {"platform":"real","host":"...","port":8883,"tls":true,"device_id":"...","secret":"..."}
CONFIG_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "iotda_config.json")


def load_config() -> dict:
    if os.path.exists(CONFIG_FILE):
        try:
            with open(CONFIG_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except (ValueError, OSError):
            pass
    return {}


# ---------- 华为云 IoTDA 官方鉴权模板 ----------
def real_timestamp() -> str:
    """UTC 时间戳，格式 YYYYMMDDHH"""
    return datetime.now(timezone.utc).strftime("%Y%m%d%H")


def real_client_id(device_id: str, ts: str, sig: int = 0) -> str:
    """{deviceId}_0_{sig}_{ts}：身份标识类型固定 0；sig=0 不校验时间戳 / 1 校验"""
    return f"{device_id}_0_{sig}_{ts}"


def real_password(secret: str, ts: str) -> str:
    """以 secret 为内容、时间戳为密钥的 HMAC-SHA256 十六进制摘要（官方规范）"""
    return hmac.new(ts.encode("utf-8"), secret.encode("utf-8"), hashlib.sha256).hexdigest()


def main():
    cfg = load_config()
    parser = argparse.ArgumentParser(
        description="车端 IoT 设备（本地模拟 / 华为云 IoTDA 真平台）；"
                    "命令行省略的参数会自动从同目录 iotda_config.json 读取")
    parser.add_argument("--host", default=cfg.get("host", "127.0.0.1"),
                        help="broker/平台地址（real 填 IoTDA 接入域名）")
    parser.add_argument("--port", type=int, default=cfg.get("port", 1883),
                        help="端口：本地 1883；real 用 1883 或 8883")
    parser.add_argument("--device-id", default=cfg.get("device_id", proto.DEFAULT_DEVICE_ID))
    parser.add_argument("--platform", choices=["local", "real"],
                        default=cfg.get("platform", "local"),
                        help="local=本地模拟(默认)；real=华为云 IoTDA 真平台")
    parser.add_argument("--secret", default=cfg.get("secret"),
                        help="real 模式：注册设备时平台返回的密钥")
    parser.add_argument("--sig-verify", type=int, choices=[0, 1],
                        default=cfg.get("sig_verify", 0),
                        help="real 模式：密码签名类型 0=不校验时间戳(默认) 1=校验")
    parser.add_argument("--tls", action="store_true", default=cfg.get("tls", False),
                        help="real 模式：启用 TLS（端口通常 8883）")
    parser.add_argument("--no-tls", dest="tls", action="store_false",
                        help="强制关闭 TLS（local 模式连本地明文 broker 时使用，"
                             "覆盖 iotda_config.json 中的 tls 配置）")
    parser.add_argument("--ca", default=cfg.get("ca"),
                        help="real 模式：CA 证书 PEM 路径（可选）")
    args = parser.parse_args()

    if args.platform == "real" and not args.secret:
        parser.error("real 模式必须提供 --secret（注册设备时平台返回的密钥）")

    # ---------- 鉴权参数（local/real 两套模板） ----------
    if args.platform == "local":
        secret = proto.DEVICE_REGISTRY.get(args.device_id)
        if secret is None:
            print(f"[ERR ] 未知设备 {args.device_id}，请在 mqtt_protocol.DEVICE_REGISTRY 注册")
            return
        ts = int(time.time())
        client_id = proto.make_client_id(args.device_id, ts)
        username = args.device_id
        password = proto.make_iot_password(args.device_id, secret, ts)
    else:
        ts = real_timestamp()
        client_id = real_client_id(args.device_id, ts, args.sig_verify)
        username = args.device_id
        password = real_password(args.secret, ts)

    car = CarState()
    client = mqtt.MiniMqttClient(
        host=args.host,
        port=args.port,
        client_id=client_id,
        username=username,
        password=password,
        keepalive=30,
        tls=args.tls,
        ca_file=args.ca,
    )

    def on_command(topic, payload):
        cmd = proto.parse_command(topic, payload)
        if cmd is None:
            print(f"[WARN] 无法解析命令 topic={topic} payload={payload[:80]}")
            return
        log = proto.apply_iot_command(car, cmd)
        print(f"[CMD ] {log}  (rid={cmd.get('request_id')})")
        # real 模式按 IoTDA 规范回执命令响应（平台界面可见"成功"）
        if args.platform == "real":
            rid = cmd.get("request_id")
            if rid:
                resp_topic = f"$oc/devices/{args.device_id}/sys/commands/response/request_id={rid}"
                client.publish(resp_topic,
                               json.dumps({"result_code": 0, "result_desc": "success"}), qos=0)

    client.on_message = on_command

    mode_txt = "华为云 IoTDA" if args.platform == "real" else "本地模拟 broker"
    tls_txt = "(TLS)" if args.tls else ""
    print(f"[BOOT] 车端设备 {args.device_id} 接入 {mode_txt} {args.host}:{args.port}{tls_txt} ...")
    print(f"[AUTH] clientId={client_id}")
    if not client.connect():
        print("[ERR ] 连接失败。real 模式排障：")
        print("       1) 域名/端口是否为控制台\"总览-接入信息\"中的值")
        print("       2) --device-id / --secret 是否为平台注册设备的值（复制完整，勿含空格）")
        print("       3) CONNACK code=4 表示用户名或密码错误（密码模板见 real_password）")
        print("       4) 网络：本机需能访问公网 1883/8883（防火墙/代理放行）")
        print("       5) 同一设备ID只允许一条连接：确认没有其他窗口/后台进程已在跑同一个脚本")
        return
    client.subscribe(proto.command_sub_topic(args.device_id), qos=1)
    print(f"[READY] 已订阅命令，开始上报属性（每 {int(STATE_INTERVAL*1000)}ms）；"
          f"可在 IoTDA 控制台\"设备-实时状态/在线调试\"查看")

    report_topic = proto.property_report_topic(args.device_id)

    def reconnect():
        """断线自动重连：关闭死连接 → 退避重试 → 重新订阅。"""
        print(f"\n[WARN] 连接断开，{RECONNECT_DELAY}s 后自动重连...")
        try:
            client.disconnect()
        except Exception:
            pass
        while True:
            time.sleep(RECONNECT_DELAY)
            try:
                if client.connect():
                    client.subscribe(proto.command_sub_topic(args.device_id), qos=1)
                    print("[READY] 重连成功，恢复属性上报")
                    return
            except Exception as e:
                print(f"[WARN] 重连失败（{type(e).__name__}: {e}），{RECONNECT_DELAY}s 后再试")

    RECONNECT_DELAY = 5
    try:
        while True:
            if not client.is_alive():
                # 读包线程已死（命令无人读取，平台回执超时）：走重连
                reconnect()
            try:
                car.tick(STATE_INTERVAL)
                snap = car.snapshot()
                client.publish(report_topic, proto.encode_property_report(snap), qos=0)
            except (OSError, ssl.SSLError) as e:
                # 断线不退出：自动重连（真实寻检车的基本健壮性要求）
                reconnect()
            time.sleep(STATE_INTERVAL)
    except KeyboardInterrupt:
        print("\n[EXIT] 车端设备退出")
    finally:
        try:
            client.disconnect()
        except Exception:
            pass


if __name__ == "__main__":
    main()
