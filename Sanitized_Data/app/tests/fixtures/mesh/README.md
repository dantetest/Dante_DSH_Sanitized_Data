# 三维（PLY）测试夹具（按需生成，二进制不入库）

`app/tests/test_mesh.py` 默认把夹具生成到临时目录；需要人工查看/复用时设置：

```powershell
$env:YINZHAN_WRITE_FIXTURES='1'
python -X utf8 app\tests\test_mesh.py
```

生成后会落在本目录：

| 文件 | 内容 | 用途 |
|---|---|---|
| `secret_ascii.ply` | 8 顶点立方体（大坐标 10000+）+ 12 三角形面；comment 含虚构手机号 `13800138000` | ASCII PLY 文本替换 + 顶点重定位 |
| `secret_le.ply` | 同上，`binary_little_endian` | 二进制 PLY 数据段等长/顶点/面复检（旧缺陷回归） |
| `secret_be.ply` | 同上，`binary_big_endian` | 大端结构复检 |
| `truncated_le.ply` | header 声明 8 顶点、数据只给 2 顶点 | parser 自检必须拦截（`ExportError`），不得复制源文件 |

> 所有 PII 均为虚构值（`13800138000`、`张伟`），不要替换成真实数据；
> 这些文件由测试脚本生成，不提交二进制。
