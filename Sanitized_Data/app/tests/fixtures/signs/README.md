# 招牌类测试 fixtures（门牌 / 路牌 / 店招 / 单位铭牌）

本目录由 `app/tests/test_sign_targets.py` 使用；图片由
[`generate_signs.py`](generate_signs.py) 用 PIL 生成，**全部为虚构文本**，
不含真实单位/地址/个人信息，图上带「虚构样例·仅供数据隐站测试」小字水印，
可提交到仓库（纯合成数据）。

| 文件 | 类型 | 期望 kind | 说明 |
|---|---|---|---|
| `sign_door.png` | 正样本 | `door_address` | 蓝底门牌「示例路 88 号」 |
| `sign_road.png` | 正样本 | `road_sign` | 绿底路牌「示例大道 / 东行 2 km」 |
| `sign_shop.png` | 正样本 | `shop_sign` | 店招灯箱「示例便利店 / 24小时营业」 |
| `sign_org.png` | 正样本 | `org_plaque` | 金属单位铭牌「北京示例科技有限公司」 |
| `neg_scenery.png` | 负样本 | — | 合成风景（无文字） |
| `neg_document.png` | 负样本 | — | 合同正文样式（含地址/公司名小字，不应命中） |
| `neg_card.png` | 负样本 | — | 名片样式（公司/地址/电话小字，不应命中） |
| `manifest.json` | 元数据 | — | 每个正样本的期望 kind 与 ROI（像素） |

## 重新生成

```powershell
python -X utf8 app/tests/fixtures/signs/generate_signs.py
```

生成是确定性的（相同字体/库版本下一致）；改虚构文本/版式后请重跑并提交新图 +
`manifest.json`。测试用**真实 OCR**读取这些图（不手填文本框），再经
`mediascan.analyze` 断言 kind/ROI/note。

## 说明

- 负样本覆盖「名片 / 发票 / 合同正文 / 风景」四类误报风险；发票负样本使用
  `app/tests/fixtures/pipl_public/synthetic_invoice.png`，名片另有真实样例
  `samples/名片.png` 参与负样本测试。
- 图片仅为功能夹具，不参与产品打包。
