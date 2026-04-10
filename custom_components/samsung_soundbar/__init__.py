import logging

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from pysmartthings import SmartThings

from .api_extension.SoundbarDevice import SoundbarDevice
from .const import (
    CONF_ENTRY_API_KEY,
    CONF_ENTRY_DEVICE_ID,
    CONF_ENTRY_DEVICE_NAME,
    CONF_ENTRY_MAX_VOLUME,
    CONF_ENTRY_SETTINGS_ADVANCED_AUDIO_SWITCHES,
    CONF_ENTRY_SETTINGS_EQ_SELECTOR,
    CONF_ENTRY_SETTINGS_SOUNDMODE_SELECTOR,
    CONF_ENTRY_SETTINGS_WOOFER_NUMBER,
    CONF_DEFAULT_BASS_MODE,
    CONF_DEFAULT_NIGHT_MODE,
    CONF_DEFAULT_VOICE_AMPLIFIER,
    DOMAIN,
)
from .models import DeviceConfig, SoundbarConfig

_LOGGER = logging.getLogger(__name__)

PLATFORMS = ["media_player", "switch", "image", "number", "select", "sensor"]


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up component from a config entry."""
    _LOGGER.info("[%s] Starting to setup a ConfigEntry", DOMAIN)

    token = entry.data.get(CONF_ENTRY_API_KEY)
    session = async_get_clientsession(hass)

    if DOMAIN not in hass.data:
        client = SmartThings(_token=token, session=session)
        hass.data[DOMAIN] = SoundbarConfig(client=client, devices={})

    domain_config: SoundbarConfig = hass.data[DOMAIN]
    client = domain_config.client

    device_id = entry.data.get(CONF_ENTRY_DEVICE_ID)
    if device_id not in domain_config.devices:
        _LOGGER.info("[%s] Setting up new Soundbar device: %s", DOMAIN, device_id)

        # Fetch device metadata via pysmartthings
        manufacturer = "Samsung"
        model = "Soundbar"
        firmware_version = ""
        try:
            device_info = await client.get_raw_device(device_id)
            manufacturer = device_info.get("deviceManufacturerCode", "Samsung Electronics")
            ocf = device_info.get("ocf", {}) or {}
            model = ocf.get("modelNumber", "") or device_info.get("name", "Soundbar")
            firmware_version = ocf.get("firmwareVersion", "")
        except Exception as err:
            _LOGGER.warning("[%s] Could not fetch device metadata: %s", DOMAIN, err)

        soundbar_device = SoundbarDevice(
            client=client,
            device_id=device_id,
            session=session,
            api_token=token,
            max_volume=entry.data.get(CONF_ENTRY_MAX_VOLUME),
            device_name=entry.data.get(CONF_ENTRY_DEVICE_NAME),
            manufacturer=manufacturer,
            model=model,
            firmware_version=firmware_version,
            enable_eq=entry.data.get(CONF_ENTRY_SETTINGS_EQ_SELECTOR),
            enable_advanced_audio=entry.data.get(CONF_ENTRY_SETTINGS_ADVANCED_AUDIO_SWITCHES),
            enable_soundmode=entry.data.get(CONF_ENTRY_SETTINGS_SOUNDMODE_SELECTOR),
            enable_woofer=entry.data.get(CONF_ENTRY_SETTINGS_WOOFER_NUMBER),
        )
        await soundbar_device.update()
        domain_config.devices[device_id] = DeviceConfig(entry.data, soundbar_device)

        # Sync initial state for advanced audio (Samsung API can't read current state)
        if entry.data.get(CONF_ENTRY_SETTINGS_ADVANCED_AUDIO_SWITCHES):
            try:
                default_bass = entry.data.get(CONF_DEFAULT_BASS_MODE, True)
                default_night = entry.data.get(CONF_DEFAULT_NIGHT_MODE, False)
                default_voice = entry.data.get(CONF_DEFAULT_VOICE_AMPLIFIER, False)
                await soundbar_device.set_bass_mode(default_bass)
                await soundbar_device.set_night_mode(default_night)
                await soundbar_device.set_voice_amplifier(default_voice)
                _LOGGER.info(
                    "[%s] Applied startup defaults: bass=%s night=%s voice=%s",
                    DOMAIN, default_bass, default_night, default_voice,
                )
            except Exception as err:
                _LOGGER.warning("[%s] Could not apply startup defaults: %s", DOMAIN, err)

        _LOGGER.info("[%s] Successfully initialized Soundbar device", DOMAIN)

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry):
    """Unload a config entry."""
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    domain_data = hass.data[DOMAIN]
    if unload_ok:
        del domain_data.devices[entry.data.get(CONF_ENTRY_DEVICE_ID)]
        if len(domain_data.devices) == 0:
            del hass.data[DOMAIN]
    return unload_ok
