# PDF 内嵌图片测试夹具（P1-B / P1.5）

本目录由 `app/tests/test_pdf_embedded.py` 使用；PDF 由
[`generate_pdfs.py`](generate_pdfs.py) 用 PyMuPDF 生成，图片为 PIL/zxing-cpp
合成。**全部文本/单位/证件均为虚构**，不含真实人像/个人信息，可提交。

| 文件 | 用途 |
|---|---|
| `sensitive.pdf` | 文本层 + 内嵌二维码（≈6.5% 页）+ 虚构证件图（≈8.5%）+ 正常风景图（≈12%）：应产 2 条 `embedded_image`（视觉 `n=`） |
| `text_sensitive.pdf` | 文本层 + 内嵌纯文字图（≈16.3% 页，ASCII 手机号/邮箱/合法校验位身份证号）：视觉无命中 → P1.5 helper 产 1 条 `text=N` |
| `normal_only.pdf` | 文本层 + 正常风景配图：0 条（负样本） |
| `tiny_qr_only.pdf` | 文本层 + 小二维码（≈1.4% 页）：低于 5% 门槛，0 条 |
| `text_only.pdf` | 纯文本层无图片：0 条，性能基线 |
| `scanned_qr.pdf` | 无文本层、整页二维码（扫描页）：不走内嵌图片分支，0 条 |
| `qr.png` / `landscape.png` / `text_img.png` | 合成素材（二维码由 zxing-cpp 生成，其余 PIL 绘制） |
| `manifest.json` | 页面尺寸与各内嵌图片矩形（PDF 页坐标）；`text_sensitive` 含 `expected_text_min` |

## 重新生成

```powershell
python -X utf8 app/tests/fixtures/pdf_embedded/generate_pdfs.py
```

图片内容确定；PDF 保存会带生成时间/文件 ID，**重复生成可能产生二进制差异**
（内容与测试断言不受影响）。改版式/新增用例后请重跑并提交新的 PDF + manifest。
测试若发现 fixtures 缺失会自动调用生成器补齐。
