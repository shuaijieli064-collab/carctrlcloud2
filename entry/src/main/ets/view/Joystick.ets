/**
 * 自研摇杆组件（课程模块②）
 *
 * 交互：底座圆盘 + 摇头，触摸拖动跟随、松手自动回中。
 * 输出：归一化偏移 (x, y) ∈ [-1, 1]，屏幕坐标系（y 向下为正），
 *       即"向上拖 = y 为负 = 小车前进"，与服务端协议约定一致。
 */
@Component
export struct Joystick {
  /** 摇杆移动回调：x/y 归一化偏移 [-1, 1] */
  onMove?: (x: number, y: number) => void;
  /** 松手回中回调 */
  onRelease?: () => void;

  /** 底座直径（vp） */
  private baseSize: number = 220;
  /** 摇头直径（vp） */
  private knobSize: number = 84;

  @State private knobX: number = 0;
  @State private knobY: number = 0;

  /** 摇头中心允许偏离底座中心的最大距离 */
  private maxOffset: number = 0;
  private maxReady: boolean = false;

  build() {
    Stack() {
      // 底座圆盘
      Circle()
        .width(this.baseSize)
        .height(this.baseSize)
        .fill('#F2F6FC')
        .fillOpacity(0.9)
        .stroke('#1A5CB0')
        .strokeWidth(2)

      // 十字准星
      Row() {
        Divider().vertical(true).height(this.baseSize * 0.7).color('#C9D4E4')
      }.width(this.baseSize)
      Row() {
        Divider().width(this.baseSize * 0.7).color('#C9D4E4')
      }.height(this.baseSize)

      // 摇头（跟随手指）
      Circle()
        .width(this.knobSize)
        .height(this.knobSize)
        .fill('#1A5CB0')
        .fillOpacity(0.85)
        .stroke('#12325E')
        .strokeWidth(2)
        .shadow({ radius: 8, color: 'rgba(0,0,0,0.25)', offsetY: 3 })
        .translate({ x: this.knobX, y: this.knobY })
    }
    .width(this.baseSize)
    .height(this.baseSize)
    .onAreaChange((_oldArea: Area, newArea: Area) => {
      this.maxOffset = (Number(newArea.width) - this.knobSize) / 2;
      this.maxReady = this.maxOffset > 0;
    })
    .gesture(
      PanGesture({ fingers: 1, distance: 5 })
        .onActionUpdate((event: GestureEvent) => {
          this.handleDrag(event.offsetX, event.offsetY);
        })
        .onActionEnd(() => {
          this.resetKnob();
        })
        .onActionCancel(() => {
          this.resetKnob();
        })
    )
  }

  private handleDrag(offsetX: number, offsetY: number): void {
    if (!this.maxReady) {
      return;
    }
    // 限制摇头不脱出底座：超出最大半径则按方向截断
    const mag = Math.sqrt(offsetX * offsetX + offsetY * offsetY);
    let nx = 0;
    let ny = 0;
    if (mag <= this.maxOffset) {
      nx = offsetX / this.maxOffset;
      ny = offsetY / this.maxOffset;
      this.knobX = offsetX;
      this.knobY = offsetY;
    } else {
      nx = offsetX / mag;
      ny = offsetY / mag;
      this.knobX = nx * this.maxOffset;
      this.knobY = ny * this.maxOffset;
    }
    if (this.onMove) {
      this.onMove(nx, ny);
    }
  }

  private resetKnob(): void {
    this.knobX = 0;
    this.knobY = 0;
    if (this.onRelease) {
      this.onRelease();
    }
  }
}
