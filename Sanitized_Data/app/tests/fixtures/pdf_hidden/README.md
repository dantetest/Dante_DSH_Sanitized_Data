# PDF 隐藏数据测试夹具（按需生成，二进制不入库）

`app/tests/test_pdf_hidden.py` 默认把夹具生成到临时目录；需要人工查看/复用时设置：

```powershell
$env:YINZHAN_WRITE_FIXTURES='1'
python -X utf8 app\tests\test_pdf_hidden.py
```

生成后会落在本目录，包含：

| 文件 | 内容 | 用途 |
|---|---|---|
| `pdf_hidden_full.pdf` | 附件 `secret.txt`、Text + FreeText 批注、1 个表单控件、OpenAction JavaScript、可见正文手机号；附件/批注/表单/JS 各带唯一 marker 与**虚构**手机号 | 解析 meta 计数、ai/human 默认剥离、成品 marker/PII 扫描、verify |
| `pdf_hidden_clean.pdf` | 同上结构，但隐藏数据**不含** PII（附件/批注/表单为普通文字） | 验证 `keep_pdf_hidden=True` 的保留路径可被 verify 放行 |
| `pdf_annot_over_text.pdf` | 批注正好盖在正文手机号上 | 验证“删批注 + redaction”顺序与结果 |
| `pdf_hidden_thumb.pdf` | 页面 `/Thumb` 缩略图（含唯一 marker） | 验证页面缩略图被一并剥离 |

> 所有 PII / marker 均为虚构值（`13800138000`、`ATTACHSECRET-*` 等），
> 不要替换成真实数据；这些文件由测试脚本生成，不提交二进制。
