# Office 内嵌媒体测试夹具（按需生成，二进制不入库）

`app/tests/test_office_media.py` 默认把夹具生成到临时目录；需要人工查看/复用时设置：

```powershell
$env:YINZHAN_WRITE_FIXTURES='1'
python -X utf8 app\tests\test_office_media.py
```

生成后会落在本目录，包含：

| 文件 | 内容 | 用途 |
|---|---|---|
| `qr.png` / `normal.png` | cv2 合成二维码（`mediascan` 可稳定检出）/ 无敏感目标的普通插图 | 被嵌入到 DOCX/XLSX |
| `docx_hidden.docx` | 正文含虚构手机号；内嵌 QR 图 + 普通图；注入 chart 部件、OLE embedding（带 rels，保存后保留）、伪造 EMF 不可解码图片 | 解析计数、默认自动脱敏、隐藏对象确认、strict 剥离 |
| `docx_clean.docx` | 只有一张普通插图 | 无敏感目标图片不破坏、不误报 |
| `docx_textbox.docx` | `word/document.xml` 注入 `w:txbxContent` + 孤儿 drawing 部件 | 文本框计入隐藏对象、strict 移除 |
| `xlsx_hidden.xlsx` | 单元格虚构手机号；内嵌 QR 图 + 普通图；openpyxl 真图表 + 注入 OLE | xlsx 解析/导出/strict |
| `xlsx_clean.xlsx` | 只有一张普通插图 | 负样本 |

> 所有 PII / marker 均为虚构值（`13800138000`、`OLE-FAKE-BYTES-SECRET`、
> `TEXTBOX-SECRET-MARKER` 等），不要替换成真实数据；这些文件由测试脚本生成，
> 不提交二进制。
