"""Read-only sensors for the supported Gizwits v4 meter models."""

from dataclasses import dataclass
from decimal import Decimal
from .profile_data import MODEL_DATA


@dataclass(frozen=True)
class Sensor:
    key: str
    name: str
    device_class: str
    unit: str
    state_class: str = "measurement"


SINGLE = {
    "PF_value_P1": Sensor("power_factor", "功率因数", "power_factor", ""),
    "f_value": Sensor("frequency", "频率", "frequency", "Hz"),
    "U_value": Sensor("voltage", "电压", "voltage", "V"),
    "I_value_P1": Sensor("current", "电流", "current", "A"),
    "P_value_P1": Sensor("power", "有功功率", "power", "W"),
    "Q_value_P1": Sensor("reactive_power", "无功功率", "reactive_power", "var"),
    "E_value": Sensor("energy", "累计电量", "energy", "kWh", "total_increasing"),
}
THREE = {}
for phase, prefix in (("a", "A 相"), ("b", "B 相"), ("c", "C 相"), ("t", "合相")):
    suffix = "" if phase == "t" else f"_{phase}"
    THREE[f"PF{phase}_value"] = Sensor("power_factor" + suffix, f"{prefix}功率因数", "power_factor", "")
    THREE[f"P{phase}_value"] = Sensor("power" + suffix, f"{prefix}有功功率", "power", "W")
    THREE[f"Q{phase}_value"] = Sensor("reactive_power" + suffix, f"{prefix}无功功率", "reactive_power", "var")
    THREE[f"E{phase}_value"] = Sensor("energy" + suffix, f"{prefix}累计电量", "energy", "kWh", "total_increasing")
    if phase != "t":
        THREE[f"U{phase}_value"] = Sensor("voltage" + suffix, f"{prefix}电压", "voltage", "V")
        THREE[f"I{phase}_value"] = Sensor("current" + suffix, f"{prefix}电流", "current", "A")


class Profile:
    def __init__(self, kind):
        model = MODEL_DATA[kind]
        self.model = model["name"]
        self.sensors = {"single": SINGLE, "three": THREE}[kind]
        self.fields = model["fields"]
        self.mask_size = (len(self.fields) + 7) // 8

    def decode(self, payload):
        if len(payload) < self.mask_size:
            raise ValueError("Missing datapoint mask")
        mask = int.from_bytes(payload[:self.mask_size], "big")
        if mask >> len(self.fields):
            raise ValueError("Unrecognized datapoint ID")

        offset = self.mask_size
        readings = {}
        for index, (size, *spec) in enumerate(self.fields):
            if not mask & (1 << index):
                continue
            data = payload[offset:offset + size]
            if len(data) != size:
                raise ValueError("Truncated datapoint")
            offset += size
            if not spec:
                continue
            name, ratio, addition, minimum, maximum = spec
            sensor = self.sensors.get(name)
            raw = int.from_bytes(data, "big")
            if sensor is not None and minimum <= raw <= maximum:
                value = raw * Decimal(str(ratio)) + Decimal(str(addition))
                readings[sensor.key] = float(value)
        if offset != len(payload):
            raise ValueError("Unexpected datapoint payload length")
        return readings


PROFILES = {kind: Profile(kind) for kind in MODEL_DATA}
PRODUCT_FINGERPRINTS = {
    "8d12e112e68023444ce6ce3c39b9e5c49ae834ea919a8ac376e715d99292c1ff": "single",
    "624b1c4e46ffad12b102b23faa5f546547087fcbbd287b2b16c46e9503a175ed": "three",
}
