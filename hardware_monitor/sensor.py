import aiohttp
import asyncio
import logging
import json
from datetime import datetime
from homeassistant.util import dt as dt_util
from typing import Optional, List, Dict, Any
from dataclasses import dataclass

from homeassistant.helpers.entity import Entity
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.typing import ConfigType, HomeAssistantType

_LOGGER = logging.getLogger(__name__)

# ==================== 数据结构 ====================
@dataclass
class HardwareSensor:
    id: str
    hw_type: str
    sensor_category: str
    value: Optional[float]
    peak: Optional[float]

# ==================== 数据解析器 ====================
class HardwareDataParser:
    def __init__(self):
        self.hardware_data = {}

    def parse_data(self, node: dict):
        self.hardware_data = {
            "timestamp": datetime.now().isoformat(),
            "sensors": []
        }

        def _scan(n: dict):
            sensor_id = n.get("SensorId", "")
            value = self._extract_value(n.get("Value"))
            max_val = self._extract_value(n.get("Max"))

            if sensor_id and value is not None:
                parts = sensor_id.strip("/").split("/")
                if len(parts) < 2:
                    return

                hw_type = parts[0].lower()
                sensor_type = parts[-1].lower()

                # 识别传感器类型
                if "temperature" in sensor_type:
                    sensor_category = "temperature"
                elif "power" in sensor_type:
                    sensor_category = "power"
                elif "load" in sensor_type or "usage" in sensor_type:
                    sensor_category = "usage"
                elif "clock" in sensor_type and not sensor_type.startswith("clock/0"):
                    sensor_category = "frequency"
                elif "data" in sensor_type:
                    sensor_category = "memory"
                elif "throughput" in sensor_type:
                    sensor_category = "network"
                else:
                    return  # 忽略不支持的传感器

                self.hardware_data["sensors"].append(HardwareSensor(
                    id=sensor_id,
                    hw_type=hw_type,
                    sensor_category=sensor_category,
                    value=value,
                    peak=max_val
                ))

            if "Children" in n:
                for child in n.get("Children", []):
                    _scan(child)

        _scan(node)

        return self.hardware_data

    @staticmethod
    def _extract_value(value: Optional[str]) -> Optional[float]:
        if value is None:
            return None
        try:
            return float(str(value).split()[0])
        except (ValueError, IndexError, AttributeError):
            return None

# ==================== 实体创建 ====================
async def async_setup_entry(
    hass: HomeAssistantType,
    config_entry: ConfigType,
    async_add_entities: AddEntitiesCallback
):
    url = config_entry.data["url"]

    async def async_update_data():
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(url, timeout=3) as response:
                    if response.status != 200:
                        raise UpdateFailed(f"HTTP状态码异常: {response.status}")
                    data = await response.json()
                    parser = HardwareDataParser()
                    return parser.parse_data(data)
        except Exception as e:
            raise UpdateFailed(f"获取或解析数据失败: {e}")

    coordinator = DataUpdateCoordinator(
        hass,
        _LOGGER,
        name="hardware_monitor",
        update_method=async_update_data,
        update_interval=dt_util.parse_duration("00:00:03"),  # 手动构造时间间隔
    )
    await coordinator.async_config_entry_first_refresh()

    sensors = []
    for sensor in coordinator.data.get("sensors", []):
        sensors.append(HardwareMonitorSensor(coordinator, sensor))

    async_add_entities(sensors)

class HardwareMonitorSensor(Entity):
    def __init__(self, coordinator, sensor_data: HardwareSensor):
        self.coordinator = coordinator
        self._sensor = sensor_data
        self._key = f"{self._sensor.hw_type}_{self._sensor.sensor_category}_{self._sensor.id.split('/')[-1]}"
        self._attr_unique_id = f"hardware_monitor_{self._key}"
        self._attr_name = f"{self._sensor.hw_type.title()} {self._sensor.sensor_category.title()}"

    @property
    def available(self):
        return self.coordinator.last_update_success

    @property
    def should_poll(self):
        return False

    @property
    def state(self):
        return self._sensor.value

    @property
    def unit_of_measurement(self):
        category = self._sensor.sensor_category
        if category == "temperature":
            return "°C"
        elif category == "usage":
            return "%"
        elif category == "power":
            return "W"
        elif category == "memory":
            return "GB"
        elif category == "frequency":
            return "GHz"
        elif category == "network":
            return "MB/s"
        return None

    @property
    def extra_state_attributes(self):
        return {
            "硬件类型": self._sensor.hw_type,
            "传感器类型": self._sensor.sensor_category,
            "峰值": self._sensor.peak,
            "更新时间": self.coordinator.data.get("timestamp")
        }

    async def async_added_to_hass(self):
        self.async_on_remove(
            self.coordinator.async_add_listener(self.async_write_ha_state)
        )