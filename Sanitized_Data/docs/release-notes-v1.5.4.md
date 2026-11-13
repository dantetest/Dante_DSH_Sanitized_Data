# 数据隐站 v1.5.4 · 支付地址修复

发布日期：2026-11-13
类型：patch
重点：修复桌面版购买页仍指向 buysd.blctek.com:8443 的问题。

## 更新内容

- 桌面版默认 commercial.json 改为 https://buysd.blctek.com
- Windows 安装包、绿色版内置同样的 443 地址
- buysd buy.html / order.html 的“返回主站”改为 https://sd.blctek.com/
- 配置示例、README、Alipay 文档同步清理 8443
- 版本号统一到 1.5.4

## Windows 产物

- Sanitized_Data_Setup_1.5.4_x64.exe
  173,773,010 bytes
  SHA256: CE8B31A7770A58971DA81848494B4BBCFC3A3908728AC2BEFBBED95F3DBA394F

- Sanitized_Data-1.5.4-win64-portable.zip
  224,745,341 bytes
  SHA256: 9166057657101E34B4FA0863911FBB49DE8A3361E96B9590D1A50D2EB041DB55

## macOS

GitHub Actions 双架构构建已完成：

- Sanitized_Data-1.5.4-arm64.dmg
  267,769,551 bytes
  SHA256: 01D0F55112BC9C1F0F660A031D8D2B7B0C7C12AEC29B2FD31BEBE22040541B62

- Sanitized_Data-1.5.4-x64.dmg
  211,707,035 bytes
  SHA256: FCA0129255725341B1B6A326CDA987824100DD52A614E31E1770928003423996

另有给已安装 1.5.3 的 Mac 用户的热修复包：

- Sanitized_Data-1.5.4-macos-hotfix.zip
  1,430 bytes
  SHA256: AC4DD683FF0A167E86666D612E4442578BF6B1F7618BBF57EA953396B844F8B9

macOS 更新仍是手动：检查更新后下载 DMG，拖入「应用程序」覆盖；不会静默自动安装。

## 验证

- Windows 安装包 ProductVersion=1.5.4
- dist/Sanitized_Data/commercial.json = https://buysd.blctek.com
- 绿色版 zip 内两份 commercial.json 均为 443
- 构建自检 /api/health version=1.5.4
- 已用未支付测试订单验证 Alipay 收银台：
  - notify_url = https://buysd.blctek.com/notify/alipay
  - return_url = https://buysd.blctek.com/order.html?...
  - 无 8443
  - qr_svg 生成成功

## 已知限制

- 安装包未做代码签名，Windows SmartScreen 可能提示。
- 真实支付未执行；需在生产环境做一笔小额支付并确认 notify 自动发码。
- macOS 1.5.4 DMG 尚未生成，需 macOS 构建机。
- 旧客户端需要安装本版本，或手动替换 commercial.json，或设置 YINZHAN_COMMERCIAL_URL。
