# 条码 / 二维码测试 fixtures

本目录由 `app/tests/test_barcodes.py` 自动生成（`_generate_fixtures()`），用于
`yinzhan/barcodes.py` 的离线自测。**全部为合成图**，不含任何真实票据/个人信息。

| 文件 | 内容 | 期望 |
|---|---|---|
| `qr_invoice.png` | 发票样式 payload 的 QR | `qrcode` ×1 |
| `datamatrix_invoice.png` | DataMatrix | `qrcode` ×1 |
| `pdf417_invoice.png` | PDF417 | `qrcode` ×1 |
| `aztec_invoice.png` | Aztec | `qrcode` ×1 |
| `microqr.png` | Micro QR | `qrcode` ×1 |
| `ean13.png` / `ean8.png` | EAN-13 / EAN-8 | `barcode` ×1 |
| `code128.png` | Code 128（payload 里带哨兵串，用于检查内容不泄露） | `barcode` ×1 |
| `code39.png` / `itf.png` | Code 39 / ITF | `barcode` ×1 |
| `qr_rot90.png` / `qr_inverted.png` / `qr_lowcontrast.png` | 旋转 90° / 反色 / 低对比 QR | `qrcode` ×1 |
| `qr_occluded_detect_only.png` | 中心遮挡 36% 的 QR | 仍返回命中（`/detect`，禁止依赖解码成功） |
| `multi_code_page_300dpi.png` | A4/300dpi，6 个不同码制同页 | 6 个 ROI 全部命中 |
| `invoice_style_300dpi.png` | A4/300dpi，QR + EAN-13 + 文字条（发票排版近似） | 2 个 ROI 全部命中 |
| `tiny_qr_page_300dpi.png` | A4/300dpi，3/4/6 px per module 的小 QR | 3 个 ROI 全部命中 |

## 重建

```powershell
# 删除本目录后重跑即可；需要 zxing-cpp
python -X utf8 app/tests/test_barcodes.py
```

`manifest.json` 记录每个 fixture 的 `kind`、期望命中数、ROI 与 sha256；测试用它
做 ROI 覆盖断言。**不要手工改 fixtures**：如 payload/排版需要调整，改
`test_barcodes.py::_generate_fixtures` 后重建。
