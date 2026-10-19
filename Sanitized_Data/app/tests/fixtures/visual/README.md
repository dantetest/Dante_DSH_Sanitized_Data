# visual 测试夹具（按需生成，不入库二进制）

本目录用于人工查看 `app/tests/test_visual.py` 生成的合成夹具。

- 默认测试**不往仓库写文件**：夹具生成到 `tempfile.mkdtemp(prefix="yinzhan_visual_")`
  （qa 约定：不往仓库塞二进制）。
- 想落地到本目录人工检查时：

  ```powershell
  $env:YINZHAN_WRITE_FIXTURES='1'; python -X utf8 app/tests/test_visual.py
  ```

  会生成：`plate_blue.png`、`plate_green.png`、`roadsign.png`、`idcard.png`、
  `idcard_whole.png`、`passport.png`、`business_license.png`、`contract_page.png`、
  `business_mention.png`、`blank_page.png`。

- 人脸正样本不在夹具里：仓库没有可再分发的真人照片，face 几何逻辑用
  `_FACE_CASCADES_OVERRIDE` 注入假级联验证；真实照片测试集待用户提供。
- 这些 PNG 是**测试产物**，不要提交进仓库；需要长期样例请找 qa 讨论放 `samples/`。
