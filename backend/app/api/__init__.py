"""API 层（P4）：只读查询接口。

- 无鉴权层（DESIGN §0）；鉴权全在 Nginx（Basic Auth + HTTPS）。
- 路由只依赖 repository 读函数与序列化器，不 import 任何 engine/策略模块（三层架构）。
"""
