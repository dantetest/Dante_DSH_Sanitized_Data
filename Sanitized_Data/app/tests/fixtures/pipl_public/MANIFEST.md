# 公开测试素材清单（pipl_public）

> **本目录只提交：** `MANIFEST.md`、`README.md`、`generate_synthetic.py` 与合成夹具（`synthetic_*.png/xlsx/csv`）。
> **公开素材（真实照片/文档扫描件）一律不提交**：下载到 `%TEMP%\pipl_public_testset\`，只在本机评测；产品运行时不联网。

## 下载策略

- 来源优先 GitHub raw（Wikimedia 有 429，未使用）；下载脚本见任务记录（`%TEMP%` 下临时脚本，不入库）。
- 真实人像只使用公开测试图/公众人物示例图（OpenCV/face_recognition/tesseract/zxing 仓库样例），仅本地功能测试，不打包、不提交、不用于任何对外展示。
- 图片版权以原出处为准；仓库许可证只覆盖代码/测试数据打包方式，不代表图片本身可再分发。

## 许可与内部测试边界（重要）

> 结论：**当前下载的公开素材全部只可用于本机内部功能评测**，不得对外分发、不得用于产品演示/官网素材、
> 不得用于模型训练或微调。原因是「代码仓库许可证」不等于「样图版权/肖像权」；其中公众人物照片、
> OpenCV 知名测试图（含 lena）与 opencv_extra 人脸图均无法仅凭仓库许可证确认可再分发。

| 分级 | 素材（数量） | 仓库许可 | 内部测试 | 对外/分发/训练 |
|---|---|---|---|---|
| A 仅内部测试 | face_recognition 公众人物 4 张（obama/biden/two_people/obama2） | 仓库 MIT；**照片版权/肖像权未授予** | ✅ 可用 | ❌ 禁止（含演示、官网、对外材料、训练/微调） |
| A 仅内部测试 | OpenCV samples 13 张（lena/messi/graf/board/building/home/sudoku 等） | 仓库 Apache-2.0；样图出处/版权未单独声明（lena 等知名度高） | ✅ 可用 | ❌ 禁止 |
| A 仅内部测试 | opencv_extra testdata 人脸 3 张、QR 2 张 | 仓库 Apache-2.0；部分人脸图可能源自第三方数据集 | ✅ 可用 | ❌ 禁止 |
| A 仅内部测试 | tesseract-ocr/test 文档扫描 6 张、zxing/zxing-cpp 黑白盒样本 8 张 | 仓库 Apache-2.0（测试数据） | ✅ 可用 | ⚠️ 不建议（具体样图出处未逐一确认） |
| B 已排除 | openalpr `appicon.png`（车牌类） | 仓库 **AGPL-3.0** | ❌ 已删除，不参与评测 | ❌ 禁止 |
| C 未使用 | Wikimedia 图片（429）、真实车牌照片（未找到合适 raw 样例） | — | — | — |

**处置**：发现 AGPL 源的 `plate_appicon.png` 后已从 `%TEMP%\pipl_public_testset\` 删除，
实际参与评测的公开素材为 31 个；车牌场景由合成夹具 `synthetic_plate.png` 覆盖。
本清单**不构成合规结论**；若将来需要对外展示测试素材，必须替换为 CC0/公有领域或自制素材。

## 已下载（32 个；其中 1 个 AGPL 源已移除，实际用于评测 31 个；**是否提交：否**）

### 二维码/条码样本

| 文件名（本地） | 来源 URL | 许可证/状态说明 | 用途 | 大小 | SHA-256（前16位） |
|---|---|---|---|---|---|
| `zxing_7.png` | https://raw.githubusercontent.com/zxing-cpp/zxing-cpp/master/test/samples/aztec-1/7.png | zxing-cpp 仓库测试图（仓库 Apache-2.0），仅本地测试 | 二维码/条码样本 | 192 B | `683e8f556e69766e` |
| `zxing_BinarizerFallback.png` | https://raw.githubusercontent.com/zxing-cpp/zxing-cpp/master/test/samples/aztec-1/BinarizerFallback.png | zxing-cpp 仓库测试图（仓库 Apache-2.0），仅本地测试 | 二维码/条码样本 | 456 B | `7d2fba27360fdb34` |
| `zxing_Historico.png` | https://raw.githubusercontent.com/zxing-cpp/zxing-cpp/master/test/samples/aztec-1/Historico.png | zxing-cpp 仓库测试图（仓库 Apache-2.0），仅本地测试 | 二维码/条码样本 | 196 B | `efbb1677ce32adf4` |
| `zxing_HistoricoLong.png` | https://raw.githubusercontent.com/zxing-cpp/zxing-cpp/master/test/samples/aztec-1/HistoricoLong.png | zxing-cpp 仓库测试图（仓库 Apache-2.0），仅本地测试 | 二维码/条码样本 | 527 B | `f5862fe874019c92` |
| `zxingbb_1_qrcode-1.png` | https://raw.githubusercontent.com/zxing/zxing/master/core/src/test/resources/blackbox/qrcode-1/1.png | zxing 仓库测试图（仓库 Apache-2.0），仅本地测试 | 二维码/一维码黑盒样本 | 236542 B | `6908e133d3ce9bdb` |
| `zxingbb_2_qrcode-1.png` | https://raw.githubusercontent.com/zxing/zxing/master/core/src/test/resources/blackbox/qrcode-1/2.png | zxing 仓库测试图（仓库 Apache-2.0），仅本地测试 | 二维码/一维码黑盒样本 | 216523 B | `30f7c2ea285650b5` |
| `zxingbb_1_qrcode-2.png` | https://raw.githubusercontent.com/zxing/zxing/master/core/src/test/resources/blackbox/qrcode-2/1.png | zxing 仓库测试图（仓库 Apache-2.0），仅本地测试 | 二维码/一维码黑盒样本 | 1451 B | `68e418d6cb318eb1` |
| `zxingbb_1_code128-1.png` | https://raw.githubusercontent.com/zxing/zxing/master/core/src/test/resources/blackbox/code128-1/1.png | zxing 仓库测试图（仓库 Apache-2.0），仅本地测试 | 二维码/一维码黑盒样本 | 1975 B | `ebe2f2b91bd1fd4c` |
| `ocvextra_qrcode_err_correct_1M.png` | https://raw.githubusercontent.com/opencv/opencv_extra/4.x/testdata/cv/qrcode/err_correct_1M.png | opencv_extra 测试数据（OpenCV 仓库 Apache-2.0），仅本地测试 | opencv_extra qrcode 测试图 | 183 B | `4e478988e4168f26` |
| `ocvextra_qrcode_err_correct_2L.png` | https://raw.githubusercontent.com/opencv/opencv_extra/4.x/testdata/cv/qrcode/err_correct_2L.png | opencv_extra 测试数据（OpenCV 仓库 Apache-2.0），仅本地测试 | opencv_extra qrcode 测试图 | 229 B | `1e84ec5512d195fc` |
| `ocvextra_qrcode_issue_21287.png` | https://raw.githubusercontent.com/opencv/opencv_extra/4.x/testdata/cv/qrcode/issue_21287.png | opencv_extra 测试数据（OpenCV 仓库 Apache-2.0），仅本地测试 | opencv_extra qrcode 测试图 | 43716 B | `a73ffaea771e7b9a` |

### 人脸照片（正脸/合影/侧脸）

| 文件名（本地） | 来源 URL | 许可证/状态说明 | 用途 | 大小 | SHA-256（前16位） |
|---|---|---|---|---|---|
| `opencv_lena.jpg` | https://raw.githubusercontent.com/opencv/opencv/4.x/samples/data/lena.jpg | OpenCV 仓库示例数据（仓库 Apache-2.0；示例图片版权以原出处为准），仅本地测试 | 单人正脸（经典测试图） | 91814 B | `7de7ed51a1594fff` |
| `opencv_messi5.jpg` | https://raw.githubusercontent.com/opencv/opencv/4.x/samples/data/messi5.jpg | OpenCV 仓库示例数据（仓库 Apache-2.0；示例图片版权以原出处为准），仅本地测试 | 单人/运动场景人脸 | 72937 B | `1d570e49654e84c7` |
| `face_rec_obama.jpg` | https://raw.githubusercontent.com/ageitgey/face_recognition/master/examples/obama.jpg | face_recognition 仓库示例图（仓库 MIT；人物照片版权/肖像权归原权利人），仅本地测试 | 单人正脸（公众人物） | 279922 B | `0930e3aa8cae5920` |
| `face_rec_biden.jpg` | https://raw.githubusercontent.com/ageitgey/face_recognition/master/examples/biden.jpg | face_recognition 仓库示例图（仓库 MIT；人物照片版权/肖像权归原权利人），仅本地测试 | 单人正脸（公众人物） | 353723 B | `3c17508bb91554c6` |
| `face_rec_two_people.jpg` | https://raw.githubusercontent.com/ageitgey/face_recognition/master/examples/two_people.jpg | face_recognition 仓库示例图（仓库 MIT；人物照片版权/肖像权归原权利人），仅本地测试 | 双人合影 | 487722 B | `536b29cd513fb618` |
| `face_rec_obama2.jpg` | https://raw.githubusercontent.com/ageitgey/face_recognition/master/examples/obama2.jpg | face_recognition 仓库示例图（仓库 MIT；人物照片版权/肖像权归原权利人），仅本地测试 | 侧脸/半边脸 | 184455 B | `a7efcc9073752747` |
| `ocvextra_face_100032540_1.jpg` | https://raw.githubusercontent.com/opencv/opencv_extra/4.x/testdata/cv/face/100032540_1.jpg | opencv_extra 测试数据（OpenCV 仓库 Apache-2.0），仅本地测试 | opencv_extra face 测试图 | 189025 B | `bfe7d1d9bef715bc` |
| `ocvextra_face_100040721_1.jpg` | https://raw.githubusercontent.com/opencv/opencv_extra/4.x/testdata/cv/face/100040721_1.jpg | opencv_extra 测试数据（OpenCV 仓库 Apache-2.0），仅本地测试 | opencv_extra face 测试图 | 47493 B | `2400a116a262a719` |
| `ocvextra_face_david1.jpg` | https://raw.githubusercontent.com/opencv/opencv_extra/4.x/testdata/cv/face/david1.jpg | opencv_extra 测试数据（OpenCV 仓库 Apache-2.0），仅本地测试 | opencv_extra face 测试图 | 19981 B | `378293e79d680203` |

### 文档/表格/票据扫描图

| 文件名（本地） | 来源 URL | 许可证/状态说明 | 用途 | 大小 | SHA-256（前16位） |
|---|---|---|---|---|---|
| `tess_eurotext.tif` | https://raw.githubusercontent.com/tesseract-ocr/test/main/testing/eurotext.tif | tesseract-ocr/test 仓库测试图（仓库 Apache-2.0），仅本地测试 | 文档/表格/票据扫描图 | 102598 B | `7b9bd14aba7d5e30` |
| `tess_phototest-rotated-180.png` | https://raw.githubusercontent.com/tesseract-ocr/test/main/testing/phototest-rotated-180.png | tesseract-ocr/test 仓库测试图（仓库 Apache-2.0），仅本地测试 | 文档/表格/票据扫描图 | 5276 B | `9a2ceaab3685a36a` |
| `tess_phototest-rotated-L.png` | https://raw.githubusercontent.com/tesseract-ocr/test/main/testing/phototest-rotated-L.png | tesseract-ocr/test 仓库测试图（仓库 Apache-2.0），仅本地测试 | 文档/表格/票据扫描图 | 6793 B | `f9c80afb481abc07` |
| `tess_phototest-rotated-R.png` | https://raw.githubusercontent.com/tesseract-ocr/test/main/testing/phototest-rotated-R.png | tesseract-ocr/test 仓库测试图（仓库 Apache-2.0），仅本地测试 | 文档/表格/票据扫描图 | 6862 B | `8b78f03821e57eec` |
| `tess_phototest.tif` | https://raw.githubusercontent.com/tesseract-ocr/test/main/testing/phototest.tif | tesseract-ocr/test 仓库测试图（仓库 Apache-2.0），仅本地测试 | 文档/表格/票据扫描图 | 38668 B | `d2241a1eb6d6cd2e` |
| `tess_phototest_2.tif` | https://raw.githubusercontent.com/tesseract-ocr/test/main/testing/phototest_2.tif | tesseract-ocr/test 仓库测试图（仓库 Apache-2.0），仅本地测试 | 文档/表格/票据扫描图 | 1614184 B | `84293add818d61ba` |
| `opencv_home.jpg` | https://raw.githubusercontent.com/opencv/opencv/4.x/samples/data/home.jpg | OpenCV 仓库示例数据（仓库 Apache-2.0；图片版权以原出处为准），仅本地测试 | 街道/标牌类照片（负样本） | 32197 B | `23b8cf46a1965d0e` |
| `opencv_sudoku.png` | https://raw.githubusercontent.com/opencv/opencv/4.x/samples/data/sudoku.png | OpenCV 仓库示例数据（仓库 Apache-2.0；图片版权以原出处为准），仅本地测试 | 街道/标牌类照片（负样本） | 250720 B | `3cd9ab173f7178c0` |

### 负样本（无目标/干扰图）

| 文件名（本地） | 来源 URL | 许可证/状态说明 | 用途 | 大小 | SHA-256（前16位） |
|---|---|---|---|---|---|
| `opencv_graf1.png` | https://raw.githubusercontent.com/opencv/opencv/4.x/samples/data/graf1.png | OpenCV 仓库示例数据（仓库 Apache-2.0；示例图片版权以原出处为准），仅本地测试 | 纹理/文档底图（负样本） | 951440 B | `1504b769303c7bde` |
| `opencv_board.jpg` | https://raw.githubusercontent.com/opencv/opencv/4.x/samples/data/board.jpg | OpenCV 仓库示例数据（仓库 Apache-2.0；示例图片版权以原出处为准），仅本地测试 | 杂乱背景（负样本） | 121597 B | `4c9a24ba67138ad5` |
| `opencv_building.jpg` | https://raw.githubusercontent.com/opencv/opencv/4.x/samples/data/building.jpg | OpenCV 仓库示例数据（仓库 Apache-2.0；示例图片版权以原出处为准），仅本地测试 | 建筑/门牌类场景（负样本） | 79718 B | `742a1baad62ac82e` |

### 车牌照片

无可用公开车牌照片（真实车牌 raw 样例未找到、openalpr 源为 AGPL 已排除）；
车牌覆盖用合成夹具 `synthetic_plate.png`（见本目录 README），不依赖公开素材。

## 未获取（已记录，不阻塞）

| 目标 | 结果 | 处理 |
|---|---|---|
| Wikimedia 图片 | HTTP 429 限流 | 改用 GitHub raw，未下载 Wikimedia |
| 真实车牌照片 | GitHub 公开仓库未找到可直接 raw 下载的合适样例（API 404/限流） | 用 `synthetic_plate.png` 合成车牌覆盖；评测结论中注明“公开车牌照片缺样” |
| `faces.jpg` | 不在 opencv/samples/data 列表里 | 跳过，不影响其它素材 |
| `group.jpg` | 不在 opencv/samples/data 列表里 | 跳过，不影响其它素材 |
| `qr.png` | 不在 opencv/samples/data 列表里 | 跳过，不影响其它素材 |
| `qrcode.png` | 不在 opencv/samples/data 列表里 | 跳过，不影响其它素材 |
| `barcode.png` | 不在 opencv/samples/data 列表里 | 跳过，不影响其它素材 |
| `receipt.png` | 不在 opencv/samples/data 列表里 | 跳过，不影响其它素材 |
| `tesseract_listing` | HTTPError: HTTP Error 404: Not Found | 跳过，不影响其它素材 |
| `zxing_#128.webp` | HTTPError: HTTP Error 404: Not Found | 跳过，不影响其它素材 |
| `zxing_#217.png` | HTTPError: HTTP Error 404: Not Found | 跳过，不影响其它素材 |
| `zxingbb_1_ean13-1.png` | TimeoutError: The read operation timed out | 跳过，不影响其它素材 |
| `opencv_extra_text` | 目录不存在 | 跳过，不影响其它素材 |
| `opencv_extra_ocr` | 目录不存在 | 跳过，不影响其它素材 |

## 使用方式

```powershell
# 评测时把 %TEMP% 目录作为额外输入（脚本不联网、不复制素材）
python -X utf8 app/tests/eval_real_media.py ^
    --dir "C:\Users\Dante\Desktop\测试集" ^
    --dir "%TEMP%\pipl_public_testset" ^
    --dir app/tests/fixtures/pipl_public
```

## 判据提醒

- 公开素材是「补充覆盖」用途，不代表用户真实业务数据分布；
- 本清单不构成合规结论；真实人像/证件只做功能评测，不进入仓库、不对外分发；
- 公开素材**仅限本机内部功能评测**，不得对外展示/演示/分发/训练；
- 若需对外展示，请用本目录合成夹具（全虚构、可提交）或明确 CC0/公有领域素材替换。
