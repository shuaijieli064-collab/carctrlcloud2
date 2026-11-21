@Entry
@Component
struct Index {
  build() {
    Column({ space: 24 }) {
      Blank().height(80)
      Text('全场景智能寻检车')
        .fontSize(32)
        .fontWeight(FontWeight.Bold)
        .fontColor('#12325E')
      Text('APP 开发实战 · TCP 直连模拟')
        .fontSize(16)
        .fontColor('#1A5CB0')
      Divider()
        .width('40%')
        .color('#C9D4E4')
      Text('通过 TCP 长连接控制模拟寻检车：\n摇杆操控 · 指令回执 · 状态回显 · 断线重连')
        .fontSize(14)
        .fontColor('#666666')
        .textAlign(TextAlign.Center)
        .lineHeight(24)

      Blank()
      Button('进入操控页', { type: ButtonType.Capsule })
        .width('60%')
        .height(48)
        .fontSize(18)
        .backgroundColor('#1A5CB0')
        .onClick(() => {
          this.getUIContext().getRouter().pushUrl({ url: 'pages/ControlPage' });
        })
      Blank().height(60)
    }
    .width('100%')
    .height('100%')
    .backgroundColor('#F2F6FC')
  }
}
