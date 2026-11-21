# -*- coding: utf-8 -*-
"""
car_model.py — 小车运动学模型（与 car_server.py 内的 CarState 逻辑保持一致）

本模块被以下角色复用，避免重复实现：
  - car_mqtt_device.py  （课程模块④：车端以 IoT 设备身份接入）
  - （car_server.py 仍保留自己的内联副本，因 live 直连服务不可重启；二者逻辑需保持一致）

模型约定（与 APP 屏幕坐标系一致）：
  - lx ∈ [-1, 1]：左右转向（正=右转）
  - ly ∈ [-1, 1]：前后（负=前进，与屏幕“向上拖=前进”一致）
  - heading：0=北(屏幕上方)，顺时针增大；x=东向位移，y=北向位移
"""

import math
import threading

# ---------------- 小车模拟参数 ----------------
MAX_SPEED = 1.0        # 最大线速度 m/s（ly=-1 时达到）
MAX_TURN = 90.0        # 最大角速度 °/s（lx=±1 时达到）
SPEED_RAMP = 3.0       # 加速度：速度逼近目标的速率（单位/秒）
BATTERY_DRAIN = 0.01   # 每 tick 电量消耗（%/tick，100ms 一个 tick）
STATE_INTERVAL = 0.1   # 状态推进周期（秒）
STEER_DEADZONE = 0.05  # 转向死区：|lx| 小于该值视为回正，抑制残余自转


class CarState:
    """小车模拟状态（全局共享，加锁访问）"""

    def __init__(self):
        self.lock = threading.Lock()
        self.speed = 0.0          # 当前线速度 m/s（正值前进）
        self.target_speed = 0.0   # 目标线速度
        self.turn_rate = 0.0      # 当前角速度 °/s
        self.heading = 0.0        # 朝向（0=北，顺时针增大）
        self.x = 0.0              # 位置 x（米，东向）
        self.y = 0.0              # 位置 y（米，北向）
        self.battery = 100.0      # 电量 %

    def apply_move(self, lx, ly):
        """lx∈[-1,1] 左右转向；ly∈[-1,1] 前后（ly 负值=前进）"""
        if abs(lx) < STEER_DEADZONE:
            lx = 0.0
        with self.lock:
            self.target_speed = -ly * MAX_SPEED
            self.turn_rate = lx * MAX_TURN

    def apply_stop(self):
        with self.lock:
            self.target_speed = 0.0
            self.turn_rate = 0.0

    def tick(self, dt):
        """状态推进：速度逼近目标 → 朝向转动 → 位置积分 → 电量缓降"""
        with self.lock:
            diff = self.target_speed - self.speed
            step = SPEED_RAMP * dt
            if abs(diff) <= step:
                self.speed = self.target_speed
            else:
                self.speed += step if diff > 0 else -step
            rad = math.radians(self.heading)
            self.heading = (self.heading + self.turn_rate * dt) % 360.0
            self.x += math.sin(rad) * self.speed * dt
            self.y += math.cos(rad) * self.speed * dt
            drain = BATTERY_DRAIN * (1.0 + abs(self.speed))
            self.battery = max(0.0, self.battery - drain)

    def snapshot(self):
        with self.lock:
            return {
                "type": "state",
                "speed": round(self.speed, 3) + 0.0,
                "battery": round(self.battery, 1) + 0.0,
                "heading": round(self.heading, 1) + 0.0,
                "x": round(self.x, 3) + 0.0,
                "y": round(self.y, 3) + 0.0,
            }
