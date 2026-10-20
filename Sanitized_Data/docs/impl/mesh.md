# 三维模型（PLY/OBJ/STL/GLB）脱敏说明（P0 修复：PLY 二进制损坏）

> 状态：已修复（task-40） · 2026-10-20 · 负责人：researcher-geo
> 代码：`app/server/yinzhan/parsers/mesh_parser.py`
> 测试：`app/tests/test_mesh.py`（7 项）+ `app/tests/e2e.py`（5/5）+ `app/tests/regression.py`（240 项全绿）
> 背景：11 号调研报告实测发现——PLY header comment 命中敏感词后，旧导出把替换值 `█`（UTF-8 3 字节）写进 header，二进制 PLY 数据段整体错位/损坏，而 `pipeline.verify` 仍返回 `verified=True`。

---

## 1. 缺陷与根因

| 项 | 旧实现（缺陷） | 说明 |
|---|---|---|
| header 重写 | `_ply_build_header()` 对所有 comment/obj_info 命中行做**变长文本替换**（`█`） | ASCII PLY 行式数据无影响；二进制 PLY header 变长后数据段偏移改变 |
| 二进制重定位写回 | `output = bytearray(raw_data); output[:old_header_len] = new_header` | **bytearray 切片赋值会改变长度**，把数据段整体后移；随后仍按 `old_header_len + start` 写顶点 → 顶点/面全部错位（严重时文件损坏） |
| 非重定位路径 | `write(new_header); seek(old_data_offset); copy` | 路径本身可用，但 header 变长后若下游按旧偏移读也会错；且损坏文件无自检 |
| verify | `pipeline.verify` 重解析 mesh 只读 header 文本块 | 几何损坏看不出来 → `verified=True` 误导 |

---

## 2. 修复内容（`mesh_parser.py`）

### 2.1 二进制 PLY：header 命中行直接删除

`_ply_build_header(header, rules, *, binary=False)`：

- **ASCII**：保持原策略（替换成假值/黑块文本，行式数据安全）；
- **二进制**：命中 comment/obj_info 行**整行删除**（任务允许“删注释或等长替换”），
  header 变短、不再写任何变长 UTF-8 内容；`data_offset` 以**新 header 长度**重新计算；
- warning 明确：“已删除对应 comment/obj_info 行（二进制 PLY 不改写变长内容，保证数据段偏移/长度不变）”。

### 2.2 二进制写回：header + 数据段副本，按新偏移写顶点

```python
source_data = raw_data[old_data_offset:]          # 只取数据段
out = bytearray(source_data)
out[start : start + count*itemsize] = vertex_array.tobytes()   # start 是数据段内偏移
write(header_bytes); write(out)                   # 新 header + 等长数据段
```

- 数据段**字节长度与源完全一致**（断言见测试）；
- 顶点数组按包围盒中心平移（原“重定位保持”）；
- 顶点前/后的数据（面、其它 element）**逐字节不变**；
- 源文件本身截断（数据段小于 header 声明）→ 直接 `ExportError`，不再“降级复制源文件”。

### 2.3 导出后结构自检（parser 侧）

`_ply_verify_output(source, out_path, header, header_bytes, byteorder, center, warnings)`：

1. 重读成品 header，校验 format/element 名称/数量/property 布局与源一致；
2. 二进制：成品数据段长度 == 源数据段长度（硬约束）；解析顶点数组比对
   `成品顶点 ≈ 源顶点 − center`；**顶点前/后数据段逐字节相等**（面数据一致）；
3. ASCII：逐 element 读行数；顶点数值比对 `源顶点 − center`；非顶点 element
   行内容**逐字节相等**（面数据一致）；
4. 任一不一致 → 删除成品并 `ExportError("PLY 成品复检失败：…")`。

### 2.4 失败不再“原样复制”

`export()` 旧逻辑 catch 所有异常后 `_copy_fallback`（把**带敏感注释的源文件**复制成成品，
verify 仍 True）。现改为：

- `ExportError` **直接向上抛**；
- `.ply` 其它异常 → 包装成 `ExportError("PLY 导出失败（拒绝原样复制，避免交付损坏/未脱敏模型）")`；
- 只有 OBJ/STL/GLB 的未知/兼容错误保留原降级策略（PLY 结构强约束不放行）。

---

## 3. 实测数字（2026-10-20，本机 Windows + Python 3.12.9）

夹具（`app/tests/test_mesh.py`，PII 全虚构）：8 顶点立方体（大坐标 10000+）+
12 三角形面 + comment 含虚构手机号 `13800138000`；三种子格式各一份。

| 用例 | 结果 |
|---|---|
| ASCII PLY 导出 | 敏感注释被替换/缩短；顶点重定位正确；12 个面数据逐行一致；成品可重解析 |
| binary_little_endian | 敏感 comment 行删除后 **数据段字节数不变**（源/成品数据段等长）；8 顶点按中心平移；12 面逐字节一致；header 无手机号；`PLY 成品复检通过` |
| binary_big_endian | 同上（大端结构完整） |
| 旧缺陷回归 | `src_header_len != out_header_len` 且数据段等长 → 证明“header 变长不再影响数据偏移”；面/顶点独立重读一致 |
| 成品复检 | 成品重解析顶点数=8；导出 warning 含“复检通过/ASCII 重写” |
| 截断源文件 | 导出抛 `ExportError`（含“数据段/损坏”字样），不留成品、不复制源文件 |
| 面数据篡改 | 对成品尾部翻一个字节后调用 `_ply_verify_output` → `ExportError("PLY 成品复检失败…")` 且删除损坏成品 |
| 门禁 | `test_mesh.py` **7/7 通过（1.1s）**；`test_tiff.py` 36/36；e2e 5/5；regression 240/240 全绿 |

### 测试命令

```powershell
# 新建的 PLY 完整性回归（7 项）
python -X utf8 app\tests\test_mesh.py

# 保留夹具人工查看
$env:YINZHAN_WRITE_FIXTURES='1'; python -X utf8 app\tests\test_mesh.py

# 红线
cd app\server; $env:PYTHONIOENCODING='utf-8'; python -X utf8 ..\tests\e2e.py
python -X utf8 app\tests\regression.py
```

夹具说明见 `app/tests/fixtures/mesh/README.md`（虚构 PII，二进制不入库）。

---

## 4. 行为矩阵

| 场景 | 行为 |
|---|---|
| ASCII PLY + header 敏感文本 | 文本替换（行式安全）；顶点重定位；面行原样；成品复检 |
| 二进制 PLY（LE/BE）+ header 敏感文本 | **删除命中注释行**；数据段等长；顶点重定位；面逐字节一致；成品复检 |
| 二进制 PLY 无命中 | 数据段原样透传；成品复检（字节一致） |
| 源 PLY 数据段截断/损坏 | `ExportError`（不交付、不复制源文件） |
| 成品复检发现偏移/篡改 | 删除成品 + `ExportError` |
| 文件过大 / 属性含 list / 缺 numpy | 跳过重定位、仅清理 header；仍做数据段等长复检（warning 说明） |
| OBJ/STL/GLB | 原逻辑不变（本任务未改）；失败降级策略仍保留 |

---

## 5. 需要 Lead 接的点（verify / 契约）

1. **`pipeline.verify` 对 mesh 仍只重读 header 文本**：几何/数据段完整性目前由 parser 导出后自检保证；
   若希望 verify 也兜底，可在 `pipeline.verify` 对 `.ply` 调用
   `mesh_parser._ply_verify_output(...)`/或重读顶点面数据（不动 pipeline 的话以 parser 自检为准）。
2. **PLY 失败不再降级复制**：jobs 会把 `ExportError` 当导出失败展示（预期行为变更：以前会“成功”交付损坏/未脱敏文件）。
3. **红线文档**：可在 CONTRACT 媒体/三维条目补充“二进制 PLY header 不得写变长内容；导出后必须重读顶点/面”。
4. **UI 文案**：导出结果建议展示“PLY 结构复检通过（顶点重定位/面数据一致）”。

---

## 6. 剩余风险与边界（诚实声明）

1. **只做“破坏性/中性化”处理**：删除 comment 行会丢失注释信息；不承诺保留全部 header 元信息。
2. **自检范围**：校验 element 布局、数据段长度、顶点数值、面字节；不校验语义（法线/UV 合理性、拓扑流形）。
3. **文件过大（>256MB）**：跳过顶点重定位（仅清理 header + 数据段等长复检），坐标未重定位需 warning 人工复核。
4. **属性含 list/未知类型**：无法 numpy 重定位，走“原样数据段 + 复检”；若源本身损坏，复检会拦截。
5. **OBJ/STL/GLB** 的同类“变长 header 破坏偏移”风险：STL 为定长 80 字节 header（旧实现已按定长处理）；OBJ 行式安全；GLB JSON chunk 有长度字段（本任务未改，后续可补同类自检）。
6. **pipeline.verify 缺口**：见 §5；当前以 parser 自检为准。
7. **不 push / 不提交二进制夹具**：夹具按需生成。

---

## 7. 变更记录

| 日期 | 变更 | 负责人 |
|---|---|---|
| 2026-10-20 | **task-40 修复二进制 PLY 几何损坏**：二进制 header 删除命中注释行、写回按新偏移重算、数据段等长；新增导出后结构自检（顶点/面逐一比对）；PLY 失败不再原样复制；新增 `test_mesh.py` 7 项；e2e 5/5、regression 240/240 全绿 | researcher-geo |

