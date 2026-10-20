# PDF 内嵌图片测试夹具（P1-B）

本目录由 `app/tests/test_pdf_embedded.py` 使用；PDF 由
[`generate_pdfs.py`](generate_pdfs.py) 用 PyMuPDF 生成，图片为 PIL/zxing-cpp
合成。**全部文本/单位/证件均为虚构**，不含真实人像/个人信息，可提交。

| 文件 | 用途 |
|---|---|
| `sensitive.pdf` | 文本层 + 内嵌二维码（≈6.5% 页）+ 虚构证件图（≈8.5%）+ 正常风景图（≈12%）：应产 2 条 `embedded_image` |
| `normal_only.pdf` | 文本层 + 正常风景配图：0 条（负样本） |
| `tiny_qr_only.pdf` | 文本层 + 小二维码（≈1.4% 页）：低于 5% 门槛，0 条 |
| `text_only.pdf` | 纯文本层无图片：0 条，性能基线 |
| `scanned_qr.pdf` | 无文本层、整页二维码（扫描页）：不走内嵌图片分支，0 条 |
| `qr.png` / `landscape.png` | 合成素材（二维码由 zxing-cpp 生成，风景由 PIL 绘制） |
| `manifest.json` | 页面尺寸与各内嵌图片矩形（PDF 页坐标） |

## 重新生成

```powershell
python -X utf8 app/tests/fixtures/pdf_embedded/generate_pdfs.py
```

生成是确定性的（相同字体/库版本下一致）；改版式后请重跑并提交新 PDF + manifest。
测试若发现 fixtures 缺失会自动调用生成器补齐。
