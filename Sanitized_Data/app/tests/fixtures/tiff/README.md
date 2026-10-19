# TIF 测试夹具（按需生成，二进制不入库）

`app/tests/test_tiff.py` 默认把夹具生成到临时目录；需要人工查看/复用时设置：

```powershell
$env:YINZHAN_WRITE_FIXTURES='1'
python -X utf8 app\tests\test_tiff.py
```

生成后会落在本目录，包含：

| 文件 | 尺寸 | 用途 |
|---|---|---|
| `u16_3band.tif` | 300×200，uint16×3 | 位深/波段保留、GeoTIFF 中性化 |
| `rgba_4band.tif` | 200×150，uint8×4 | RGBN/4 波段保留 |
| `multiframe_2page.tif` | 400×300×2 帧 | 多帧路径（Pillow 回退 + 仅剥离 warning） |
| `icc_geotiff.tif` | 200×120 | ICC(34675) 必须被剥离 |
| `meta_leak.tif` | 200×120 | EXIF/XMP/描述/设备标签必须被剥离 |
| `overviews_1024.tif` | 1024×768 + 内部金字塔 | 内部 overview IFD 不被误判为多帧 |
| `plain_no_geo.tif` | 120×80 | 无地理参考的普通 TIFF（只剥离不中性化） |
| `big_20000x20000.tif` | 20000×20000，4 亿像素 | rasterio 大图路线（写入约 0.5MB，生成约 2~3 秒） |
| `corrupt.tif` | — | 结构扫描容错 |

> 这些文件由测试脚本生成，不提交二进制；`big_20000x20000.tif` 体积小但像素极大，
> 用 Pillow 打开会触发 `DecompressionBombError`，属预期。
