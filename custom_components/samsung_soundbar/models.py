from dataclasses import dataclass, field

from pysmartthings import SmartThings

from .api_extension.SoundbarDevice import SoundbarDevice


@dataclass
class DeviceConfig:
    config: dict
    device: SoundbarDevice


@dataclass
class SoundbarConfig:
    client: SmartThings
    devices: dict = field(default_factory=dict)
