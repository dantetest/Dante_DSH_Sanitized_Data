# 离线依赖包（部署 / 打包用）

本目录说明如何在没有外网的目标机上安装 P0 新增的可选依赖。**不把二进制 wheel 提交进仓库**
（rasterio/pyproj 解压后约 90MB，会显著撑大仓库）；随离线安装包分发时，把下列 wheel
复制到本目录（或安装包同级目录），用 `pip install --no-index --find-links` 安装。

> 产品运行时**不联网**；这些依赖只需在构建/部署机上安装一次。

## 1. 需要准备的 wheel（Windows x64 / CPython 3.12）

| 包 | 版本 | wheel | 大小 | sha256 | 许可证 |
|---|---|---|---|---|---|
| zxing-cpp | 3.1.1 | `zxing_cpp-3.1.1-cp312-abi3-win_amd64.whl` | 1,003,677 B | `29f98a91148171460b47a942d137ecc90c4b8097636f23cca65263a56bb025d3` | Apache-2.0 |
| rasterio | 1.4.3 | `rasterio-1.4.3-cp312-cp312-win_amd64.whl`（内含 GDAL 3.9.3） | 25,430,154 B | `a962ad4c29feaf38b1d7a94389313127de3646a5b9b734fbf9a04e16051a27ff` | BSD-3-Clause |
| pyproj | 3.7.2 | `pyproj-3.7.2-cp312-cp312-win_amd64.whl`（内含 PROJ 9.5.1） | 6,312,176 B | `11614a054cd86a2ed968a657d00987a86eeb91fdcbd9ad3310478685dc14a128` | MIT |

rasterio/pyproj 还有若干传递依赖（numpy 等已在本机，另有 certifi/click/affine/attrs/
cligj/pyparsing/snuggs 等）；生成完整 wheel 目录用：

```powershell
# 构建机（可联网一次）：
python -m pip download --no-deps --only-binary :all: -d deploy\wheels `
  "zxing-cpp==3.1.1" "rasterio==1.4.3" "pyproj==3.7.2"
# 传递依赖一并下载（如目标机是干净环境）：
python -m pip download --only-binary :all: -d deploy\wheels -r app\server\requirements.txt
```

```powershell
# 目标机（离线）：
python -m pip install --no-index --find-links deploy\wheels -r app\server\requirements.txt
```

> 注意：本机 pip 默认可能走 `HTTP(S)_PROXY`（127.0.0.1:4780）代理导致挂起；下载前清空
> `HTTP_PROXY` / `HTTPS_PROXY` 环境变量，或显式指定可用的镜像。

## 2. 打包（PyInstaller / 安装包）

- `desktop/app.spec` 已对 `zxingcpp`、`rasterio`、`pyproj` 调用 `collect_all()`（未安装时自动跳过并打印）。
- `desktop/build_win.ps1` 第 1 步检查 `zxingcpp` 是否可导入；第 5 步自检会读取
  `/api/health` 的 `engine.scanners`，确认 `barcode` 扫描器真的进了包。
- 安装包体积预计 +60~100MB（rasterio 15.5MB + rasterio.libs 51.1MB + pyproj 17.5MB + pyproj.libs 6.7MB
  + proj.db 8.8MB，解压口径；实际以构建实测为准）。构建后请在目标机复测：
  `/api/health` → `scanners=["barcode","visual_targets"]`、`scan_enabled=true`。
- 许可证义务：按 Apache-2.0 / BSD-3-Clause / MIT 在安装包或「关于」中保留相应 LICENSE/NOTICE。

## 3. 降级行为（未装上述依赖时）

- 未装 `zxing-cpp`：条码/二维码扫描器不加载（`/api/health` 的 scanners 里没有 barcode），
  其余功能正常；这是"缺能力"而不是"静默失败"，回归门禁会记录依赖软项。
- 未装 `rasterio`/`pyproj`：TIF 走 Pillow 降级（>1.79 亿像素打不开、16bit 降 8bit），
  并写明确 warning；坐标中性化不可用。
