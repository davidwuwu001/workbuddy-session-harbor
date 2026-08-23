"""平台适配器注册表：新平台在此登记即可插拔。

每个适配器需实现（千问等未完成平台可部分缺省）：
  PLATFORM_ID / PLATFORM_NAME
  status() -> dict          平台状态聚合（账号列表、App 当前登录、能力矩阵）
  capture(app_key) -> dict  从本机登录态提取账号入库（Trae 的"授权"方式）
  switch(account_id, app_key) -> dict  切换账号
"""

from platforms import trae, qwen

REGISTRY = {
    trae.PLATFORM_ID: trae,
    qwen.PLATFORM_ID: qwen,
}


def get_platform(platform_id):
    adapter = REGISTRY.get(platform_id)
    if not adapter:
        raise RuntimeError(f"未知平台: {platform_id}（可用: {', '.join(REGISTRY)}）")
    return adapter


def list_platforms():
    return [
        {"id": adapter.PLATFORM_ID, "name": adapter.PLATFORM_NAME, **adapter.status()}
        for adapter in REGISTRY.values()
    ]
