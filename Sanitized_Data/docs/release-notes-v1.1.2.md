# 数据隐站 v1.1.2 · 预览与成品一致性修复

发布日期：2026-10-21 · 上一版：[v1.1.1](release-notes-v1.1.1.md)

## 本次修复

用户反馈：同一份扫描件（`民 24593-包罗.pdf` 第 2 页），**在线预览**把整段条款涂黑，
但**下载的涂黑版**只涂了人名/公司名 —— 预览与成品不一致。

根因：两条渲染路径各算各的几何。

| 路径 | 旧实现 | 问题 |
|---|---|---|
| 在线预览（涂黑形态） | 按实体 span → 行块 bbox 估算 | OCR 行块没有字符级坐标，同一行多个实体时整行/整段被画黑 |
| 下载成品（human 导出） | 解析器 `_collect_ops` + `_merge_ops` 精确矩形 | 只涂实体本身，且重叠框 union 会放大黑块 |

v1.1.2 把二者收敛为**同一套矩形**：

1. **预览=成品（单一口径）**：新增解析器可选钩子 `export_black_rects()`，预览
   「涂黑」形态直接使用与 human 导出完全相同的矩形（`black_rects`，
   `black_source="export-parity"`），不再按实体框估算。预览页返回的每个黑框
   都是成品上真实会涂的框。
2. **OCR 行块按字符比例插值**：预览/导出统一按字符位置在行框内插值（此前预览
   回退整行 bbox）。
3. **黑框合并不再膨胀**：交叠黑框只在「包含/近重合」时合并，其余分别绘制，
   修复「成品比预览更大更黑、压掉上一行条款」。
4. **待确认实体预览不伪装**：`pending` 且当前不会处理的实体，预览不再画实心黑。

## 验证

- 新增 `app/tests/test_preview_export_parity.py`：渲染 human 成品，逐像素对比
  「预览矩形」与「成品新增黑色区域」——预期外新增 0 px，预览黑区 0 px 未落地；
  实测 `民 24593-包罗.pdf` 第 2/5 页：`preview→成品 缺失 0 px、预期外新增 0 px`。
- `app/tests/test_preview_boxes.py`：OCR 行块插值 / 黑框合并 / 版本回退口径。
- WebUI 实测（127.0.0.1:8760）：「涂黑」形态第 2 页只涂人名与公司名，条款正文可见；
  与下载成品逐页一致。
- `regression.py` 回归门禁通过（红线未破）；`run_all.py` 新增 `previewparity` 域测试。

## 部署自检

部署后打开 `GET /api/health`，应为：

```json
{"version": "1.1.2", "build": "20261021-preview-parity",
 "engine": {"preview_parity": "export-parity/v1", ...}}
```

若看到的仍是 `version: "1.1.1"` 或没有 `preview_parity` 字段，说明部署的还是旧包
（旧包的预览会整行黑、下载只涂实体）。

## 附件与校验值

见 Release 页 `SHA256SUMS.txt`：

| 文件 | 大小 | SHA-256 |
|---|---|---|
| `Sanitized_Data-1.1.2-win64.zip`（源码包） | 2,538,460 B（2.42 MiB） | `3471772268A2C9C7E3CDA234FCB95A3BF8257FB001FABE474C96B76D64DB43B5` |
| `Sanitized_Data-1.1.2-linux64.tar.gz`（源码包） | 2,381,494 B（2.27 MiB） | `4A698213913DA2223F978B2E52090173E405799F6BB4F531660D627C5D2A84C3` |
| `Sanitized_Data-1.1.2-arm64.dmg`（macOS Apple Silicon） | 204,563,156 B（195.1 MiB） | `1AC30A229668A454CEA399293571AD7B3BEF323A0987270F939E3A8DC013F634` |
| `Sanitized_Data-1.1.2-x64.dmg`（macOS Intel） | 197,998,406 B（188.8 MiB） | `820FB0FB3EAD40A27FB75D95A0E6312C988004AC711492EC4B050B75D2CB93B6` |
| `Sanitized_Data_Setup_1.1.2_x64.exe`（安装包） | 168,704,289 B（160.9 MiB） | `CC6DF2636903A77A99F8F9F36D0619193B14FFA9CB22ED50EED0111922246AA6` |
| `Sanitized_Data-1.1.2-win64-portable.zip`（绿色版） | 191,717,563 B（182.8 MiB） | `0CF30971E23E49FD7F48A4F6E305A73B8A190DAB00AF62652130DEE43773AFDD` |

> 部署后请用 `GET /api/health` 确认：`version=1.1.2`、`build=20261021-preview-parity`、
> `engine.preview_parity=export-parity/v1`。旧版（1.1.1 及以前）会出现「预览整行黑、
> 下载只涂实体」的不一致。

