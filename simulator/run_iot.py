# -*- coding: utf-8 -*-
"""
run_iot.py — IoT 套件一键启动器（零第三方依赖，标准库实现）

职责：
  1. 停掉旧的「本地 IoT 套件」进程（mqtt_broker.py + 带 --platform local 的
     car_mqtt_device.py，并兜底清理 1883/8899 端口监听者）；
     注意：直连华为云 IoTDA 的真平台车端不带 --platform local，必须保留，
     否则一键启动会顺手杀掉正在上报的 8883 TLS 会话；
  2. 启动 IoT broker（MQTT:1883 + 俯视图 WS:8899）；
  3. 启动车端设备（deviceId=car_001）；
  4. 自动打开浏览器俯视图 http://localhost:8899。

用法：
  python run_iot.py            交互模式：启动 → 回车后全部停止（双击 .bat 走这条）
  python run_iot.py start      启动后直接退出，服务保持后台运行
  python run_iot.py stop       仅停止
  python run_iot.py start --hidden   启动但不弹出服务窗口（日志写入 logs/）

实现说明：
  - 隐藏模式（CREATE_NO_WINDOW）下子进程没有控制台，直接 print 会因句柄无效
    崩溃，因此隐藏模式一律把 stdout/stderr 重定向到 logs/*.log；
  - 启动后对 1883 端口做就绪探活，失败时提示查看日志。
"""
import os
import socket
import subprocess
import sys
import time
import webbrowser

SIM_DIR = os.path.dirname(os.path.abspath(__file__))
LOG_DIR = os.path.join(SIM_DIR, 'logs')
PY = sys.executable  # 用当前 Python 运行子服务，无需再找解释器路径

BROKER_ARGS = [PY, 'mqtt_broker.py', '--port', '1883', '--ws-port', '8899']
# 必须显式钉死 local 模式：car_mqtt_device.py 会把 iotda_config.json 当默认值读，
# 而该配置是「真平台 real + TLS:8883」。若这里不覆盖，一键启动器会把车端连去
# 华为云 IoTDA（甚至对本地明文 broker 发 TLS），导致本地 9/9 集成测试全挂。
CAR_ARGS = [PY, '-u', 'car_mqtt_device.py',
            '--platform', 'local', '--no-tls',
            '--device-id', 'car_001',
            '--host', '127.0.0.1', '--port', '1883']

# 进程匹配：只清「本地套件」。
# 真平台车端通常无参数启动（读 iotda_config.json 的 real+TLS:8883），
# 命令行里没有 --platform local，必须排除；否则 start/stop 会误杀它。
KILL_PS = (
    "Get-CimInstance Win32_Process | "
    "Where-Object { $_.CommandLine -match 'mqtt_broker\\.py' -or "
    "($_.CommandLine -match 'car_mqtt_device\\.py' -and "
    "$_.CommandLine -match '--platform local') } | "
    "ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }; "
    "Get-NetTCPConnection -LocalPort 1883,8899 -State Listen -ErrorAction SilentlyContinue | "
    "Select-Object -ExpandProperty OwningProcess -Unique | "
    "ForEach-Object { Stop-Process -Id $_ -Force -ErrorAction SilentlyContinue }"
)


def kill_old() -> None:
    """停止旧 IoT 进程（broker + 车端设备 + 端口占用者）。"""
    try:
        subprocess.run(
            ['powershell', '-NoProfile', '-Command', KILL_PS],
            capture_output=True, timeout=20,
            creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0),
        )
    except Exception as exc:  # 清理失败不阻断启动
        print('  （清理旧进程时出现问题，继续启动：%s）' % exc)
    time.sleep(1.0)


def spawn(args: list, name: str, hidden: bool) -> subprocess.Popen:
    """启动子服务。

    - 可见模式：CREATE_NEW_CONSOLE，每服务一个窗口直接看日志；
    - 隐藏模式：CREATE_NO_WINDOW，日志重定向到 logs/<name>.log
      （子进程无控制台时不重定向会因 stdout 句柄无效而崩溃）。
    """
    creationflags = getattr(subprocess, 'CREATE_NO_WINDOW', 0) if hidden \
        else getattr(subprocess, 'CREATE_NEW_CONSOLE', 0)
    if hidden:
        os.makedirs(LOG_DIR, exist_ok=True)
        logf = open(os.path.join(LOG_DIR, name + '.log'), 'ab')
        return subprocess.Popen(
            args, cwd=SIM_DIR, creationflags=creationflags,
            stdin=subprocess.DEVNULL, stdout=logf, stderr=subprocess.STDOUT,
        )
    return subprocess.Popen(args, cwd=SIM_DIR, creationflags=creationflags)


def wait_port(port: int, timeout: float = 8.0) -> bool:
    """就绪探活：等待 127.0.0.1:port 可连接。"""
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            s = socket.create_connection(('127.0.0.1', port), timeout=0.5)
            s.close()
            return True
        except OSError:
            time.sleep(0.3)
    return False


def start(hidden: bool = False) -> None:
    print('[1/3] 停止旧 IoT 进程 ...')
    kill_old()
    print('[2/3] 启动 IoT broker（MQTT 1883 + 俯视图 WS 8899）...')
    spawn(BROKER_ARGS, 'broker', hidden)
    if not wait_port(1883):
        print('  [!] broker 未能在 8 秒内就绪，请查看日志：logs/broker.log')
    else:
        print('  broker 就绪（1883 端口已监听）')
    print('[3/3] 启动车端设备（deviceId=car_001）...')
    spawn(CAR_ARGS, 'car', hidden)
    time.sleep(1.5)
    if hidden:
        print('  隐藏模式：日志文件在 simulator/logs/（broker.log / car.log）')
    print()
    print('=' * 52)
    print(' IoT 套件已启动')
    print('   MQTT broker : 127.0.0.1:1883')
    print('   俯视图      : http://localhost:8899')
    print('   APP 连接    : IoT 平台模式，host=10.0.2.2:1883')
    print('=' * 52)
    try:
        webbrowser.open('http://localhost:8899')
    except Exception:
        pass  # 浏览器打开失败不影响服务


def stop() -> None:
    print('正在停止 IoT 服务 ...')
    kill_old()
    print('已全部停止。')


def main() -> None:
    argv = sys.argv[1:]
    cmd = argv[0] if argv else ''
    hidden = '--hidden' in argv

    if cmd == 'stop':
        stop()
        return
    if cmd == 'start':
        start(hidden)
        return  # 保持后台运行，直接退出

    # 交互模式（双击 .bat 的默认路径）
    start(hidden)
    print()
    try:
        input('按回车键停止全部 IoT 服务 ...')
    except (EOFError, KeyboardInterrupt):
        print()
    stop()


if __name__ == '__main__':
    main()
