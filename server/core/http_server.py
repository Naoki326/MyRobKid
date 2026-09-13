import asyncio
from aiohttp import web
from config.logger import setup_logging
from core.api.ota_handler import OTAHandler
from core.api.vision_handler import VisionHandler
from core.api.config_handler import ConfigHandler
from core.api.camera_handler import CameraHandler
from config.config_loader import get_project_dir

TAG = __name__


class SimpleHttpServer:
    def __init__(self, config: dict):
        self.config = config
        self.logger = setup_logging()
        self.ota_handler = OTAHandler(config)
        self.vision_handler = VisionHandler(config)
        self.config_handler = ConfigHandler(config, get_project_dir())
        self.camera_handler = CameraHandler(config)

    def _get_websocket_url(self, local_ip: str, port: int) -> str:
        """获取websocket地址

        Args:
            local_ip: 本地IP地址
            port: 端口号

        Returns:
            str: websocket地址
        """
        server_config = self.config["server"]
        websocket_config = server_config.get("websocket")

        if websocket_config and "你" not in websocket_config:
            return websocket_config
        else:
            return f"ws://{local_ip}:{port}/xiaozhi/v1/"

    async def start(self):
        try:
            server_config = self.config["server"]
            read_config_from_api = self.config.get("read_config_from_api", False)
            host = server_config.get("ip", "0.0.0.0")
            port = int(server_config.get("http_port", 8003))

            if port:
                app = web.Application()

                if not read_config_from_api:
                    # 如果没有开启智控台，只是单模块运行，就需要再添加简单OTA接口，用于下发websocket接口
                    app.add_routes(
                        [
                            web.get("/xiaozhi/ota/", self.ota_handler.handle_get),
                            web.post("/xiaozhi/ota/", self.ota_handler.handle_post),
                            web.options(
                                "/xiaozhi/ota/", self.ota_handler.handle_options
                            ),
                            # 下载接口，仅提供 data/bin/*.bin 下载
                            web.get(
                                "/xiaozhi/ota/download/{filename}",
                                self.ota_handler.handle_download,
                            ),
                            web.options(
                                "/xiaozhi/ota/download/{filename}",
                                self.ota_handler.handle_options,
                            ),
                            # 远程重启设备（重启后自动检查OTA）：curl http://<ip>:8003/xiaozhi/ota/reboot?device_id=xx
                            web.get("/xiaozhi/ota/reboot", self.ota_handler.handle_reboot),
                            web.post("/xiaozhi/ota/reboot", self.ota_handler.handle_reboot),
                            web.options("/xiaozhi/ota/reboot", self.ota_handler.handle_options),
                        ]
                    )
                # 添加路由（带 /xiaozhi 前缀，匹配 nginx 反代路径；设备经 8080 访问）
                app.add_routes(
                    [
                        web.get(
                            "/xiaozhi/mcp/vision/explain",
                            self.vision_handler.handle_get,
                        ),
                        web.post(
                            "/xiaozhi/mcp/vision/explain",
                            self.vision_handler.handle_post,
                        ),
                        web.options(
                            "/xiaozhi/mcp/vision/explain",
                            self.vision_handler.handle_options,
                        ),
                    ]
                )

                # 轻量配置页路由（页面 + 读写 API）
                app.add_routes(
                    [
                        # 旧入口兑底：``/xiaozhi/config/`` **302** 到首域
                        # （§4.1）。旧八组页面已在 #31 收线时删除；
                        # 这里是 302 而不是 301——它不是规范形（§8.1 的域页
                        # URL 才是），只是旧链接/书签不 404 的兑底。
                        web.get(
                            "/xiaozhi/config/",
                            self.config_handler.handle_config_root_default,
                        ),
                        # 页面拓扑：五域 + 逃生口（§8.1 的 slug 是用户契约）。
                        # 无尾斜杠 → 尾斜杠的 301 由**应用层**发（§8.2），
                        # 不依赖仓库外的 nginx 配置。
                        web.get(
                            "/xiaozhi/config",
                            self.config_handler.handle_config_root_redirect,
                        ),
                        web.get(
                            "/xiaozhi/config/{slug}",
                            self.config_handler.handle_domain_redirect,
                        ),
                        web.get(
                            "/xiaozhi/config/{slug}/",
                            self.config_handler.handle_domain_page,
                        ),
                        # 页面状态模型（无 DOM 依赖的 ES 模块，配置页 <script type="module"> 引入）
                        web.get(
                            "/xiaozhi/config/config_state_model.js",
                            self.config_handler.handle_state_model,
                        ),
                        # 域页的编辑脚本（五个域页共用同一份）
                        web.get(
                            "/xiaozhi/config/config_domain_page.js",
                            self.config_handler.handle_domain_page_script,
                        ),
                        # 危险分级模型（父 spec §6）：分级判定与后果文案的
                        # 唯一实现，全部域页共用
                        web.get(
                            "/xiaozhi/config/config_danger_model.js",
                            self.config_handler.handle_danger_model,
                        ),
                        web.post(
                            "/xiaozhi/config/api/auth",
                            self.config_handler.handle_auth,
                        ),
                        web.get(
                            "/xiaozhi/config/api/full",
                            self.config_handler.handle_full,
                        ),
                        web.get(
                            "/xiaozhi/config/api/meta",
                            self.config_handler.handle_meta,
                        ),
                        web.post(
                            "/xiaozhi/config/api/save",
                            self.config_handler.handle_save,
                        ),
                        web.post(
                            "/xiaozhi/config/api/test-llm",
                            self.config_handler.handle_test_llm,
                        ),
                        web.post(
                            "/xiaozhi/config/api/restart",
                            self.config_handler.handle_restart,
                        ),
                        # SmartConfig 设备配网
                        web.get(
                            "/xiaozhi/config/api/local-wifi",
                            self.config_handler.handle_local_wifi,
                        ),
                        web.post(
                            "/xiaozhi/config/api/smartconfig",
                            self.config_handler.handle_smartconfig,
                        ),
                        # 设备与固件管理（远程重启设备 / OTA 固件上传）
                        web.get(
                            "/xiaozhi/config/api/devices",
                            self.config_handler.handle_devices,
                        ),
                        web.get(
                            "/xiaozhi/config/api/firmware",
                            self.config_handler.handle_firmware_list,
                        ),
                        web.post(
                            "/xiaozhi/config/api/firmware/upload",
                            self.config_handler.handle_firmware_upload,
                        ),
                        web.post(
                            "/xiaozhi/config/api/firmware/delete",
                            self.config_handler.handle_firmware_delete,
                        ),
                        web.options(
                            "/xiaozhi/config/api/auth",
                            self.config_handler.handle_options,
                        ),
                        web.options(
                            "/xiaozhi/config/api/full",
                            self.config_handler.handle_options,
                        ),
                        web.options(
                            "/xiaozhi/config/api/save",
                            self.config_handler.handle_options,
                        ),
                        web.options(
                            "/xiaozhi/config/api/restart",
                            self.config_handler.handle_options,
                        ),
                        # 摄像头监控页 + 快照代理（§4.4 / §8.2）
                        # 页面**不再双注册**：无斜杠 301 到规范形，由应用层发；
                        # api/* 子路由是 API 不是页面，不跟着 301。
                        web.get(
                            "/xiaozhi/camera",
                            self.config_handler.handle_camera_redirect,
                        ),
                        web.get(
                            "/xiaozhi/camera/",
                            self.config_handler.handle_camera_page,
                        ),
                        web.get(
                            "/xiaozhi/camera/api/snapshot",
                            self.camera_handler.handle_snapshot,
                        ),
                        web.get(
                            "/xiaozhi/camera/api/device-info",
                            self.camera_handler.handle_device_info,
                        ),
                    ]
                )

                # 运行服务
                runner = web.AppRunner(app)
                await runner.setup()
                site = web.TCPSite(runner, host, port)
                await site.start()

                # 保持服务运行
                while True:
                    await asyncio.sleep(3600)  # 每隔 1 小时检查一次
        except Exception as e:
            self.logger.bind(tag=TAG).error(f"HTTP服务器启动失败: {e}")
            import traceback

            self.logger.bind(tag=TAG).error(f"错误堆栈: {traceback.format_exc()}")
            raise
