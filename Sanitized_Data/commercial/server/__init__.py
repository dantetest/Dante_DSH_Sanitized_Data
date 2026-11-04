# -*- coding: utf-8 -*-
"""数据隐站 L3 商业服务（FastAPI + SQLite）。

模块划分：
- ``config``   环境变量配置（属性名与 env 同名，T2/T3/运维按名读取）
- ``catalog``  SKU/价格（服务端唯一事实来源）
- ``db``       SQLite（orders/payments/licenses/events，幂等约束）
- ``signer``   Ed25519 license 签发（复用产品 yinzhan.licensing.issue）
- ``qr``       二维码生成（vendored Nayuki 纯 Python 库 + SVG 输出）
- ``payments`` 支付提供者（mock 内置；alipay 由 T2 延迟导入）
- ``app``      FastAPI 路由/中间件/CORS/限流
"""

__version__ = "1.0.0"
