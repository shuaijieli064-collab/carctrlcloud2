# -*- coding: utf-8 -*-
"""生成《全场景智能寻检车APP开发实战》开发计划文档的5张流程图 PNG"""
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.patches import FancyBboxPatch, FancyArrowPatch
import os

plt.rcParams["font.sans-serif"] = ["Microsoft YaHei", "SimHei"]
plt.rcParams["axes.unicode_minus"] = False

OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "figs")
os.makedirs(OUT, exist_ok=True)

# 配色（浅色主题）
C_BLUE   = "#DBEAFE"; C_BLUE_E   = "#2563EB"
C_ORANGE = "#FFEDD5"; C_ORANGE_E = "#EA580C"
C_GREEN  = "#DCFCE7"; C_GREEN_E  = "#16A34A"
C_GRAY   = "#F3F4F6"; C_GRAY_E   = "#6B7280"
C_RED    = "#FEE2E2"; C_RED_E    = "#DC2626"
C_PURPLE = "#EDE9FE"; C_PURPLE_E = "#7C3AED"
C_NOTE   = "#FEF3C7"; C_NOTE_E   = "#D97706"


def box(ax, x, y, w, h, text, fc, ec, fs=11, weight="normal", lw=1.8, align="center"):
    ax.add_patch(FancyBboxPatch((x, y), w, h, boxstyle="round,pad=0.6,rounding_size=1.6",
                                fc=fc, ec=ec, lw=lw, zorder=3))
    ha = {"center": "center", "left": "left"}[align]
    tx = x + w / 2 if align == "center" else x + 1.5
    ax.text(tx, y + h / 2, text, ha=ha, va="center", fontsize=fs,
            fontweight=weight, zorder=4, linespacing=1.5)


def arrow(ax, x1, y1, x2, y2, color="#374151", style="-", lw=1.8, ls=None,
          conn="arc3,rad=0", mut=16):
    ax.add_patch(FancyArrowPatch((x1, y1), (x2, y2), arrowstyle="-|>",
                                 mutation_scale=mut, color=color, lw=lw,
                                 linestyle=ls if ls else style,
                                 connectionstyle=conn, zorder=2))


def label(ax, x, y, text, fs=9.5, color="#111827", ha="center", weight="normal", bg=None):
    kw = {}
    if bg:
        kw["bbox"] = dict(boxstyle="round,pad=0.25", fc=bg, ec="none")
    ax.text(x, y, text, ha=ha, va="center", fontsize=fs, color=color,
            fontweight=weight, zorder=5, linespacing=1.4, **kw)


def new_ax(w, h, title=None):
    fig, ax = plt.subplots(figsize=(w, h), dpi=120)
    ax.set_xlim(0, 100); ax.set_ylim(0, 100)
    ax.axis("off")
    if title:
        ax.text(50, 97.5, title, ha="center", va="top", fontsize=15, fontweight="bold")
    return fig, ax


def save(fig, name):
    path = os.path.join(OUT, name)
    fig.savefig(path, dpi=120, bbox_inches="tight", facecolor="white")
    plt.close(fig)
    print("saved:", path)


# ============ 图1 整体四角色架构图 ============
fig, ax = new_ax(11, 7.2, "图1  TCP 直连：整体四角色架构（本期范围）")
box(ax, 4, 62, 34, 18, "鸿蒙 APP\nTCP 客户端 · DevEco 模拟器", C_BLUE, C_BLUE_E, fs=12, weight="bold")
box(ax, 62, 62, 34, 18, "模拟小车 car_server.py\nTCP 服务端 · 监听 :8888（PC 运行，代替真车）", C_ORANGE, C_ORANGE_E, fs=11.5, weight="bold")
box(ax, 4, 22, 34, 16, "test_client.py\n开发期替身 · 先测车端（站在 APP 位置打桩）", C_GRAY, C_GRAY_E, fs=11)
box(ax, 62, 22, 34, 16, "浏览器俯视图\nWebSocket :8889 · 只看不控（后期扩展）", C_GREEN, C_GREEN_E, fs=11)
# APP -> car_server
arrow(ax, 39.5, 74.5, 60.5, 74.5, color=C_BLUE_E)
label(ax, 50, 78.5, "① connect(小车IP, 8888)\n连接永远由 APP 主动发起", fs=10, weight="bold")
# car_server -> APP (dashed back)
arrow(ax, 60.5, 66.5, 39.5, 66.5, color=C_ORANGE_E, ls=(0, (5, 4)))
label(ax, 50, 62.0, "② 指令回执 / 状态流持续回送（长连接双向）", fs=9.5)
# test_client -> car_server
arrow(ax, 39.5, 30, 60.5, 30, color=C_GRAY_E, ls=(0, (5, 4)))
label(ax, 50, 33.5, "同协议打桩联调（先用替身验证车端）", fs=9.5)
# car_server -> webview
arrow(ax, 79, 60.5, 79, 40.5, color=C_GREEN_E, ls=(0, (5, 4)))
label(ax, 81.5, 50.5, "状态广播", fs=9.5, ha="left")
# key note bar
box(ax, 4, 4, 92, 9,
    "关键约定：模拟器里 10.0.2.2 = 宿主机（PC）本机 —— APP 连的是它，不是 127.0.0.1\n"
    "端口约定：TCP :8888 控制通道（APP—车端）    WebSocket :8889 俯视图（只看不控）",
    C_NOTE, C_NOTE_E, fs=10.5)
save(fig, "fig1_arch.png")

# ============ 图2 TCP 连接与通信时序图 ============
fig, ax = new_ax(10.5, 9.5, "图2  TCP 连接与通信时序（长连接生命周期）")
xs = {"app": 18, "tcp": 50, "srv": 82}
box(ax, 6, 87.5, 24, 7, "鸿蒙 APP\n(操控页/摇杆)", C_BLUE, C_BLUE_E, fs=11, weight="bold")
box(ax, 38, 87.5, 24, 7, "TcpClient.ets\n(网络封装层)", C_PURPLE, C_PURPLE_E, fs=11, weight="bold")
box(ax, 70, 87.5, 24, 7, "car_server.py\n(TCP :8888)", C_ORANGE, C_ORANGE_E, fs=11, weight="bold")
for k, x in xs.items():
    ax.plot([x, x], [13, 87.5], color="#9CA3AF", lw=1.2, ls=(0, (4, 4)), zorder=1)

steps = [
    ("app", "srv", "connect(10.0.2.2, 8888)  主动连接", C_BLUE_E, "-"),
    ("srv", "app", "TCP 连接建立（长连接）", C_ORANGE_E, "-"),
    ("app", "srv", "心跳 ping（每 5s）", C_BLUE_E, "-"),
    ("srv", "app", "pong 应答", C_ORANGE_E, "-"),
    ("app", "srv", '{"type":"move","lx":0.5,"ly":-1.0}', C_BLUE_E, "-"),
    ("srv", "app", '{"type":"ack","seq":12,"ok":true}  指令回执', C_ORANGE_E, "-"),
    ("srv", "app", '{"type":"state",...}  状态流（每 100ms 持续回送）', C_ORANGE_E, "-"),
    (None, None, "—— 断线：15s 未收到下行判定超时 ——", C_RED_E, None),
    ("app", "srv", "指数退避重连 1s→2s→4s→…→上限 30s", C_RED_E, (0, (5, 4))),
]
y = 81.5
for a, b, txt, color, ls in steps:
    if a is None:
        label(ax, 50, y, txt, fs=10, color=color, weight="bold", bg="#FFFFFF")
        y -= 6.5
        continue
    x1, x2 = xs[a], xs[b]
    arrow(ax, x1, y, x2, y, color=color, ls=ls if ls else "-", lw=1.6, mut=13)
    off = (x1 + x2) / 2
    label(ax, off, y + 2.2, txt, fs=9.5)
    y -= 8.2
box(ax, 6, 2.5, 88, 6.5,
    "保活约定：ping/pong 心跳 · 15s 无下行判超时 · 断线期间指令静默丢弃并保持急停态 · 重连成功自动恢复状态流",
    C_NOTE, C_NOTE_E, fs=10)
save(fig, "fig2_seq.png")

# ============ 图3 摇杆指令数据流图 ============
fig, ax = new_ax(11.5, 6.8, "图3  摇杆指令数据流（发出 → 执行 → 回显）")
top = [
    (2,  "用户触摸\n拖动摇杆", C_GRAY, C_GRAY_E),
    (21, "Joystick 组件\n归一化偏移 (x,y)∈[-1,1]", C_BLUE, C_BLUE_E),
    (40, "节流器\n约 100ms/帧", C_PURPLE, C_PURPLE_E),
    (59, "CommandProtocol\nJSON 封装 + \\n 分帧", C_PURPLE, C_PURPLE_E),
    (78, "TcpClient.send()\n→ 10.0.2.2:8888", C_BLUE, C_BLUE_E),
]
bot = [
    (78, "car_server.py\n解析执行（模拟运动）", C_ORANGE, C_ORANGE_E),
    (59, "ack 回执 / state 状态流\n(TCP 回送)", C_ORANGE, C_ORANGE_E),
    (40, "on('message') 收包\nTextDecoder 解码 + 分帧", C_PURPLE, C_PURPLE_E),
    (21, "@State 更新\n状态面板刷新 UI", C_GREEN, C_GREEN_E),
    (2,  "用户看到\n速度/电量/朝向", C_GRAY, C_GRAY_E),
]
bw, bh, ytop, ybot = 18, 15, 58, 22
for x, t, fc, ec in top:
    box(ax, x, ytop, bw, bh, t, fc, ec, fs=10)
for i in range(len(top) - 1):
    arrow(ax, top[i][0] + bw, ytop + bh / 2, top[i + 1][0], ytop + bh / 2, lw=1.8)
# down connector
arrow(ax, 87, ytop, 87, ybot + bh, color="#374151", lw=1.8)
label(ax, 90.5, (ytop + ybot + bh) / 2, "TCP 长连接", fs=9, ha="left")
for x, t, fc, ec in bot:
    box(ax, x, ybot, bw, bh, t, fc, ec, fs=10)
for i in range(len(bot) - 1):
    arrow(ax, bot[i][0], ybot + bh / 2, bot[i + 1][0] + bw, ybot + bh / 2, lw=1.8)
label(ax, 50, 8, "闭环：指令 100ms 级节流下发，回执与状态流实时回显 —— 断线时暂停下发并置急停，重连后自动恢复",
      fs=10.5, weight="bold", bg=C_NOTE)
save(fig, "fig3_dataflow.png")

# ============ 图4 分阶段开发流程图 ============
fig, ax = new_ax(10.5, 11, "图4  分阶段开发流程（阶段0 → 阶段7）")
stages = [
    ("阶段 0  工程配置", "module.json5 加 INTERNET 权限 · 目录骨架", C_GRAY, C_GRAY_E),
    ("阶段 1  操控页 UI（课程模块①）", "ControlPage.ets · 路由注册 · Index 改入口", C_BLUE, C_BLUE_E),
    ("阶段 2  摇杆组件（课程模块②）", "view/Joystick.ets · 拖拽/回中 · onMove(x,y)", C_BLUE, C_BLUE_E),
    ("阶段 3  TCP 客户端与协议（模块③·APP侧）", "net/TcpClient.ets · model/CommandProtocol.ets · CarConfig", C_PURPLE, C_PURPLE_E),
    ("阶段 4  模拟车端（模块③·服务端侧）", "simulator/car_server.py · 监听0.0.0.0:8888 · 回执+状态流", C_ORANGE, C_ORANGE_E),
    ("阶段 5  打桩联调（替身→真APP闭环）", "simulator/test_client.py 全用例 → APP 连 10.0.2.2:8888 联调", C_ORANGE, C_ORANGE_E),
    ("阶段 6  浏览器俯视图（可选扩展）", "simulator/webview.html · WebSocket:8889 只看不控", C_GREEN, C_GREEN_E),
    ("阶段 7  华为云 IoT 集成（后期计划）", "IoTDA 建模 · MQTT 接入 · 协议映射 · 真车替换", C_RED, C_RED_E),
]
y0, bh, gap = 86, 7.4, 3.8
for i, (title, sub, fc, ec) in enumerate(stages):
    y = y0 - i * (bh + gap)
    box(ax, 8, y, 62, bh, title + "\n" + sub, fc, ec, fs=10.5)
    # 验收门
    box(ax, 76, y + 1.2, 18, bh - 2.4, "验收门\n编译/用例通过", "#FFFFFF", C_GRAY_E, fs=8.5, lw=1.2)
    arrow(ax, 70.5, y + bh / 2, 75, y + bh / 2, lw=1.2, mut=10, color=C_GRAY_E)
    if i < len(stages) - 1:
        arrow(ax, 39, y, 39, y - gap + 0.3, lw=2, mut=14)
# 交叉旁路标注（置于阶段7下方，避免与阶段框重叠）
label(ax, 50, 4.2,
      "※ 阶段 3 / 4 / 5 允许交叉：可先做阶段 4+5 验证协议，再回补阶段 3（呼应“先解决联调对象”的问题导向）",
      fs=10, weight="bold", bg=C_NOTE)
save(fig, "fig4_phases.png")

# ============ 图5 华为云 IoT 演进图 ============
fig, ax = new_ax(11.5, 6.5, "图5  架构演进：TCP 直连 → 华为云 IoTDA 平台中转（后期计划）")
# 现状
box(ax, 3, 84, 30, 6, "现状 · 本期（TCP 直连）", "#FFFFFF", C_GRAY_E, fs=11, weight="bold")
box(ax, 5, 58, 26, 16, "鸿蒙 APP\nTCP 客户端", C_BLUE, C_BLUE_E, fs=11, weight="bold")
box(ax, 5, 26, 26, 16, "car_server.py\n模拟小车（PC）", C_ORANGE, C_ORANGE_E, fs=11, weight="bold")
arrow(ax, 18, 57, 18, 43, color=C_BLUE_E, lw=2)
label(ax, 20, 50, "TCP :8888\nJSON 指令/状态", fs=9.5, ha="left")
# 演进箭头
arrow(ax, 34.5, 48, 42.5, 48, color="#111827", lw=2.6, mut=22)
label(ax, 38.5, 54, "演进", fs=11, weight="bold")
# 目标（右侧重新布局：平行双向箭头，标签错开避免重叠）
box(ax, 64, 84, 33, 6, "目标 · 后期（云平台中转）", "#FFFFFF", C_GRAY_E, fs=11, weight="bold")
box(ax, 44, 62, 18, 14, "鸿蒙 APP", C_BLUE, C_BLUE_E, fs=11, weight="bold")
box(ax, 74, 50, 23, 24, "华为云 IoTDA\n设备接入 &\n管理平台", C_PURPLE, C_PURPLE_E, fs=11, weight="bold")
box(ax, 44, 20, 18, 14, "真车车端\n(MCU/网关)", C_GREEN, C_GREEN_E, fs=11, weight="bold")
# APP 与 IoTDA 之间：两条平行横向箭头
arrow(ax, 62.5, 71, 73.5, 71, color=C_PURPLE_E, lw=2)
label(ax, 67.5, 75.5, "MQTT(TLS)\n指令下发", fs=9)
arrow(ax, 73.5, 64, 62.5, 64, color=C_PURPLE_E, lw=2)
label(ax, 67.5, 58.5, "状态/属性\n上报", fs=9)
# 真车 与 IoTDA 之间：一条双向箭头
ax.add_patch(FancyArrowPatch((60.5, 35), (75, 49), arrowstyle="<|-|>",
                             mutation_scale=18, color=C_GREEN_E, lw=2,
                             connectionstyle="arc3,rad=0.12", zorder=2))
label(ax, 61, 43.5, "命令下发 /\n状态上报", fs=9, ha="left")
box(ax, 3, 4, 94, 9,
    "后期里程碑：① IoTDA 产品/设备建模（deviceId/密钥）  ② APP 侧 MQTT 接入与协议映射（TCP 指令 → IoT 命令）\n"
    "③ 车端接入平台与设备影子  ④ 真车替换模拟车端  ⑤ 平台集成测试。本期文档仅列路线，不排工时。",
    C_NOTE, C_NOTE_E, fs=10)
save(fig, "fig5_iot.png")

print("ALL DONE")
