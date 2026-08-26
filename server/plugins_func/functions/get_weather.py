import asyncio
import httpx
from config.logger import setup_logging
from plugins_func.register import register_function, ToolType, ActionResponse, Action
from core.utils.util import get_ip_info
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from core.connection import ConnectionHandler

TAG = __name__
logger = setup_logging()

GET_WEATHER_FUNCTION_DESC = {
    "type": "function",
    "function": {
        "name": "get_weather",
        "description": (
            "获取某个地点的天气，用户应提供一个位置，比如用户说杭州天气，参数为：杭州。"
            "如果用户说的是省份，默认用省会城市。如果用户说的不是省份或城市而是一个地名，默认用该地所在省份的省会城市。"
            "重要：本地未来7天天气已在上下文中提供，用户未指明其他城市时绝对不要调用此工具。"
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "location": {
                    "type": "string",
                    "description": "地点名，例如杭州。可选参数，如果不提供则不传",
                },
                "lang": {
                    "type": "string",
                    "description": "返回用户使用的语言code，例如zh_CN/zh_HK/en_US/ja_JP等，默认zh_CN",
                },
            },
            "required": ["lang"],
        },
    },
}

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/92.0.4515.107 Safari/537.36"
    )
}

# 天气代码 https://dev.qweather.com/docs/resource/icons/#weather-icons
WEATHER_CODE_MAP = {
    "100": "晴",
    "101": "多云",
    "102": "少云",
    "103": "晴间多云",
    "104": "阴",
    "150": "晴",
    "151": "多云",
    "152": "少云",
    "153": "晴间多云",
    "300": "阵雨",
    "301": "强阵雨",
    "302": "雷阵雨",
    "303": "强雷阵雨",
    "304": "雷阵雨伴有冰雹",
    "305": "小雨",
    "306": "中雨",
    "307": "大雨",
    "308": "极端降雨",
    "309": "毛毛雨/细雨",
    "310": "暴雨",
    "311": "大暴雨",
    "312": "特大暴雨",
    "313": "冻雨",
    "314": "小到中雨",
    "315": "中到大雨",
    "316": "大到暴雨",
    "317": "暴雨到大暴雨",
    "318": "大暴雨到特大暴雨",
    "350": "阵雨",
    "351": "强阵雨",
    "399": "雨",
    "400": "小雪",
    "401": "中雪",
    "402": "大雪",
    "403": "暴雪",
    "404": "雨夹雪",
    "405": "雨雪天气",
    "406": "阵雨夹雪",
    "407": "阵雪",
    "408": "小到中雪",
    "409": "中到大雪",
    "410": "大到暴雪",
    "456": "阵雨夹雪",
    "457": "阵雪",
    "499": "雪",
    "500": "薄雾",
    "501": "雾",
    "502": "霾",
    "503": "扬沙",
    "504": "浮尘",
    "507": "沙尘暴",
    "508": "强沙尘暴",
    "509": "浓雾",
    "510": "强浓雾",
    "511": "中度霾",
    "512": "重度霾",
    "513": "严重霾",
    "514": "大雾",
    "515": "特强浓雾",
    "900": "热",
    "901": "冷",
    "999": "未知",
}


async def fetch_city_info(location, api_key, api_host):
    """通过和风 geo API 查询城市（X-QW-Api-Key 认证，适用于专属 API Host）"""
    headers = {"X-QW-Api-Key": api_key}
    async with httpx.AsyncClient(timeout=httpx.Timeout(5.0, connect=3.0)) as client:
        response = await client.get(
            f"https://{api_host}/geo/v2/city/lookup",
            params={"location": location, "lang": "zh"},
            headers=headers,
        )
    if response.status_code != 200:
        logger.bind(tag=TAG).error(f"和风城市查询失败: HTTP {response.status_code}")
        return None
    data = response.json()
    if data.get("code") != "200":
        logger.bind(tag=TAG).error(f"和风城市查询失败: code={data.get('code')}")
        return None
    locations = data.get("location") or []
    return locations[0] if locations else None


async def fetch_weather(location_id, api_key, api_host):
    """调用和风 v7 API 获取实时天气与 7 天预报"""
    headers = {"X-QW-Api-Key": api_key}
    async with httpx.AsyncClient(timeout=httpx.Timeout(8.0, connect=3.0)) as client:
        now_resp, daily_resp = await asyncio.gather(
            client.get(
                f"https://{api_host}/v7/weather/now",
                params={"location": location_id},
                headers=headers,
            ),
            client.get(
                f"https://{api_host}/v7/weather/7d",
                params={"location": location_id},
                headers=headers,
            ),
        )
    now = now_resp.json() if now_resp.status_code == 200 else {}
    daily = daily_resp.json() if daily_resp.status_code == 200 else {}
    if now.get("code") != "200":
        logger.bind(tag=TAG).error(f"和风实时天气失败: {now.get('code')}")
        return None, None
    return now, daily if daily.get("code") == "200" else None


def build_weather_report(city_name, now, daily):
    """将和风 API 数据拼装为给 LLM 的文本报告"""
    n = now.get("now", {})
    wind = f"{n.get('windDir', '未知')} {n.get('windScale', '?')}级"
    report = (
        f"您查询的位置是：{city_name}\n\n"
        f"当前天气: {n.get('text', '未知')}，气温 {n.get('temp', '?')}°C"
        f"（体感 {n.get('feelsLike', '?')}°C），湿度 {n.get('humidity', '?')}%，{wind}，降水 {n.get('precip', '0')}mm\n"
    )
    if daily:
        report += "\n未来7天预报：\n"
        for day in daily.get("daily", [])[:7]:
            weather = WEATHER_CODE_MAP.get(day.get("iconDay", "999"), day.get("textDay", "未知"))
            report += (
                f"{day.get('fxDate', '?')}: {weather}，"
                f"气温 {day.get('tempMin', '?')}~{day.get('tempMax', '?')}°C，"
                f"白天 {day.get('textDay', '')}"
                f"{'，夜间 ' + day.get('textNight', '') if day.get('textNight') and day.get('textNight') != day.get('textDay') else ''}\n"
            )
    report += "\n（数据来自和风天气，如需某一天的具体天气，请告诉我日期）"
    return report


@register_function("get_weather", GET_WEATHER_FUNCTION_DESC, ToolType.SYSTEM_CTL)
async def get_weather(conn: "ConnectionHandler", location: str = None, lang: str = "zh_CN"):
    from core.utils.cache.manager import cache_manager, CacheType

    weather_config = conn.config.get("plugins", {}).get("get_weather", {})
    api_host = weather_config.get("api_host", "mj7p3y7naa.re.qweatherapi.com")
    api_key = weather_config.get("api_key", "a861d0d5e7bf4ee1a83d9a9e4f96d4da")
    # 配置的城市（default_location）：非空则优先使用；留空则按客户端 IP 自动定位
    default_location = (weather_config.get("default_location") or "").strip()
    client_ip = conn.client_ip

    # 优先使用用户提供的location参数
    if not location:
        if default_location:
            # 配置了默认城市，直接使用
            location = default_location
        elif client_ip:
            # 未配置城市，通过客户端IP解析（局域网 IP 会自动改查服务器公网出口）
            cached_ip_info = cache_manager.get(CacheType.IP_INFO, client_ip)
            if cached_ip_info:
                location = cached_ip_info.get("city")
            else:
                ip_info = get_ip_info(client_ip, logger)
                if ip_info:
                    cache_manager.set(CacheType.IP_INFO, client_ip, ip_info)
                    location = ip_info.get("city")

        if not location:
            # 既未配置城市、IP 定位也失败：请用户明确指定
            return ActionResponse(
                Action.REQLLM,
                "无法确定您所在的位置（未配置默认城市且 IP 定位失败），请告诉我想查询哪个城市的天气，例如“上海天气”",
                None,
            )
    # 尝试从缓存获取完整天气报告
    weather_cache_key = f"full_weather_{location}_{lang}"
    cached_weather_report = cache_manager.get(CacheType.WEATHER, weather_cache_key)
    if cached_weather_report:
        return ActionResponse(Action.REQLLM, cached_weather_report, None)

    # 缓存未命中，获取实时天气数据（和风 v7 API）
    city_info = await fetch_city_info(location, api_key, api_host)
    if not city_info:
        return ActionResponse(
            Action.REQLLM, f"未找到相关的城市: {location}，请确认地点是否正确", None
        )
    now, daily = await fetch_weather(city_info["id"], api_key, api_host)
    if not now:
        return ActionResponse(Action.REQLLM, None, "请求和风天气 API 失败")
    city_name = city_info.get("name", location)
    weather_report = build_weather_report(city_name, now, daily)

    # 缓存完整的天气报告
    cache_manager.set(CacheType.WEATHER, weather_cache_key, weather_report)

    return ActionResponse(Action.REQLLM, weather_report, None)
