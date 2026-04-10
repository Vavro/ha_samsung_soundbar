import asyncio
import datetime
import json
import logging
from urllib.parse import quote

from pysmartthings import SmartThings

from .const import SpeakerIdentifier, RearSpeakerMode
from ..const import DOMAIN

_LOGGER = logging.getLogger(__name__)

API_BASE = "https://api.smartthings.com/v1"


class SoundbarDevice:
    def __init__(
            self,
            client: SmartThings,
            device_id: str,
            session,
            api_token: str,
            max_volume: int,
            device_name: str,
            manufacturer: str = "Samsung",
            model: str = "Soundbar",
            firmware_version: str = "",
            enable_eq: bool = False,
            enable_soundmode: bool = False,
            enable_advanced_audio: bool = False,
            enable_woofer: bool = False,
    ):
        self._client = client
        self._device_id = device_id
        self._api_token = api_token
        self._session = session
        self.__device_name = device_name
        self._status = {}

        self._manufacturer = manufacturer
        self._model = model
        self._firmware_version = firmware_version

        self.__enable_soundmode = enable_soundmode
        # Fallback values for when Samsung's execute readback returns null
        self.__supported_soundmodes = ["standard", "adaptive", "surround", "gamepro"]
        self.__active_soundmode = None

        self.__enable_woofer = enable_woofer
        self.__woofer_level = 0
        self.__woofer_connection = ""

        self.__enable_eq = enable_eq
        self.__active_eq_preset = None
        self.__supported_eq_presets = ["standard", "pop", "jazz", "classical", "bass boost"]
        self.__eq_action = ""
        self.__eq_bands = []

        self.__enable_advanced_audio = enable_advanced_audio
        self.__voice_amplifier = None
        self.__night_mode = None
        self.__bass_mode = None

        self.__execute_readback_available = True

        self.__media_title = ""
        self.__media_artist = ""
        self.__media_cover_url = ""
        self.__media_cover_url_update_time: datetime.datetime | None = None
        self.__old_media_title = ""

        self.__max_volume = max_volume

    def _get_status_value(self, capability: str, attribute: str, default=None):
        """Safely extract a value from the raw status dict."""
        try:
            return (
                self._status
                .get("components", {})
                .get("main", {})
                .get(capability, {})
                .get(attribute, {})
                .get("value", default)
            )
        except (AttributeError, TypeError):
            return default

    def _find_attribute_value(self, attribute_name: str, default=None):
        """Search all capabilities in the main component for an attribute."""
        main = self._status.get("components", {}).get("main", {})
        for cap_data in main.values():
            if isinstance(cap_data, dict) and attribute_name in cap_data:
                attr = cap_data[attribute_name]
                if isinstance(attr, dict):
                    return attr.get("value", default)
        return default

    async def _refresh_status(self):
        """Fetch device status via pysmartthings client."""
        try:
            self._status = await self._client.get_raw_device_status(self._device_id)
        except Exception as err:
            _LOGGER.error("[%s] Error refreshing device status: %s", DOMAIN, err)

    async def _send_command(self, capability, command, args=None, component="main"):
        """Send a command via pysmartthings client."""
        try:
            await self._client.execute_device_command(
                self._device_id,
                capability,
                command,
                component=component,
                argument=args,
            )
        except Exception as err:
            _LOGGER.error(
                "[%s] Error sending command %s/%s: %s",
                DOMAIN, capability, command, err,
            )

    async def update(self):
        await self._refresh_status()
        if not self._status:
            return

        await self._update_media()

        if self.__execute_readback_available:
            if self.__enable_soundmode:
                await self._update_soundmode()
            if self.__enable_advanced_audio:
                await self._update_advanced_audio()
            if self.__enable_soundmode:
                await self._update_woofer()
            if self.__enable_eq:
                await self._update_equalizer()

    async def _update_media(self):
        track_data = self._get_status_value("audioTrackData", "audioTrackData")
        if track_data and isinstance(track_data, dict):
            self.__media_artist = track_data.get("artist", "")
            self.__media_title = track_data.get("title", "")
            if self.__media_title != self.__old_media_title:
                self.__old_media_title = self.__media_title
                self.__media_cover_url_update_time = datetime.datetime.now()
                self.__media_cover_url = await self.get_song_title_artwork(
                    self.__media_artist, self.__media_title
                )

    async def _update_soundmode(self):
        await self.update_execution_data(["/sec/networkaudio/soundmode"])
        await asyncio.sleep(1)
        payload = await self.get_execute_status()
        retry = 0
        while (
                "x.com.samsung.networkaudio.supportedSoundmode" not in payload
                and retry < 3
        ):
            await asyncio.sleep(1)
            payload = await self.get_execute_status()
            retry += 1
        if retry >= 3:
            _LOGGER.debug("[%s] Execute readback unavailable for soundmode", DOMAIN)
            self.__execute_readback_available = False
            return

        self.__supported_soundmodes = payload[
            "x.com.samsung.networkaudio.supportedSoundmode"
        ]
        self.__active_soundmode = payload["x.com.samsung.networkaudio.soundmode"]

    async def _update_woofer(self):
        await self.update_execution_data(["/sec/networkaudio/woofer"])
        await asyncio.sleep(0.1)
        payload = await self.get_execute_status()
        retry = 0
        while "x.com.samsung.networkaudio.woofer" not in payload and retry < 3:
            await asyncio.sleep(0.2)
            payload = await self.get_execute_status()
            retry += 1
        if retry >= 3:
            _LOGGER.debug("[%s] Execute readback unavailable for woofer", DOMAIN)
            self.__execute_readback_available = False
            return
        self.__woofer_level = payload["x.com.samsung.networkaudio.woofer"]
        self.__woofer_connection = payload["x.com.samsung.networkaudio.connection"]

    async def _update_equalizer(self):
        await self.update_execution_data(["/sec/networkaudio/eq"])
        await asyncio.sleep(0.1)
        payload = await self.get_execute_status()
        retry = 0
        while "x.com.samsung.networkaudio.EQname" not in payload and retry < 3:
            await asyncio.sleep(0.2)
            payload = await self.get_execute_status()
            retry += 1
        if retry >= 3:
            _LOGGER.debug("[%s] Execute readback unavailable for equalizer", DOMAIN)
            self.__execute_readback_available = False
            return
        self.__active_eq_preset = payload["x.com.samsung.networkaudio.EQname"]
        self.__supported_eq_presets = payload[
            "x.com.samsung.networkaudio.supportedList"
        ]
        self.__eq_action = payload["x.com.samsung.networkaudio.action"]
        self.__eq_bands = payload["x.com.samsung.networkaudio.EQband"]

    async def _update_advanced_audio(self):
        await self.update_execution_data(["/sec/networkaudio/advancedaudio"])
        await asyncio.sleep(0.1)

        payload = await self.get_execute_status()
        retry = 0
        while "x.com.samsung.networkaudio.nightmode" not in payload and retry < 3:
            await asyncio.sleep(0.2)
            payload = await self.get_execute_status()
            retry += 1
        if retry >= 3:
            _LOGGER.debug("[%s] Execute readback unavailable for advanced audio", DOMAIN)
            self.__execute_readback_available = False
            return

        self.__night_mode = payload["x.com.samsung.networkaudio.nightmode"]
        self.__bass_mode = payload["x.com.samsung.networkaudio.bassboost"]
        self.__voice_amplifier = payload["x.com.samsung.networkaudio.voiceamplifier"]

    # ------------ DEVICE INFORMATION ----------

    @property
    def manufacturer(self):
        return self._manufacturer

    @property
    def model(self):
        return self._model

    @property
    def firmware_version(self):
        return self._firmware_version

    @property
    def device_id(self):
        return self._device_id

    @property
    def device_name(self):
        return self.__device_name

    # ------------ ON / OFF ------------

    @property
    def state(self) -> str:
        switch_val = self._get_status_value("switch", "switch")
        if switch_val == "on":
            playback = self._get_status_value("mediaPlayback", "playbackStatus")
            if playback == "playing":
                return "playing"
            if playback == "paused":
                return "paused"
            return "on"
        return "off"

    async def switch_off(self):
        await self._send_command("switch", "off")

    async def switch_on(self):
        await self._send_command("switch", "on")

    # ------------ VOLUME --------------

    @property
    def volume_level(self) -> float:
        vol = self._get_status_value("audioVolume", "volume", 0)
        try:
            vol = int(vol)
        except (TypeError, ValueError):
            return 0.0
        if vol > self.__max_volume:
            return 1.0
        return vol / self.__max_volume

    @property
    def raw_volume(self) -> int:
        """Return the raw volume value (0-100) without max_volume scaling."""
        vol = self._get_status_value("audioVolume", "volume", 0)
        try:
            return int(vol)
        except (TypeError, ValueError):
            return 0

    @property
    def volume_muted(self) -> bool:
        return self._get_status_value("audioMute", "mute") != "unmuted"

    async def set_volume(self, volume: float):
        await self._send_command(
            "audioVolume", "setVolume", [int(volume * self.__max_volume)]
        )

    async def mute_volume(self, mute: bool):
        if mute:
            await self._send_command("audioMute", "mute")
        else:
            await self._send_command("audioMute", "unmute")

    async def volume_up(self):
        await self._send_command("audioVolume", "volumeUp")

    async def volume_down(self):
        await self._send_command("audioVolume", "volumeDown")

    # ------------ WOOFER LEVEL -------------

    @property
    def woofer_level(self) -> int:
        return self.__woofer_level

    @property
    def woofer_connection(self) -> str:
        return self.__woofer_connection

    async def set_woofer(self, level: int):
        await self.set_custom_execution_data(
            href="/sec/networkaudio/woofer",
            property="x.com.samsung.networkaudio.woofer",
            value=level,
        )
        self.__woofer_level = level

    # ------------ INPUT SOURCE -------------

    @property
    def input_source(self):
        if self.media_app_name in ("AirPlay", "Spotify"):
            return "wifi"
        return self._get_status_value("samsungvd.audioInputSource", "inputSource")

    @property
    def supported_input_sources(self):
        return self._get_status_value("samsungvd.audioInputSource", "supportedInputSources", [])

    async def select_source(self, source: str):
        await self._send_command("samsungvd.audioInputSource", "setInputSource", [source])

    # ------------- SOUND MODE --------------
    @property
    def sound_mode(self):
        return self.__active_soundmode

    @property
    def supported_soundmodes(self):
        return self.__supported_soundmodes

    async def select_sound_mode(self, sound_mode: str):
        await self.set_custom_execution_data(
            href="/sec/networkaudio/soundmode",
            property="x.com.samsung.networkaudio.soundmode",
            value=sound_mode,
        )

    # ------------- ADVANCED AUDIO ---------------

    @property
    def night_mode(self) -> bool | None:
        if self.__night_mode is None:
            return None
        return self.__night_mode == 1

    async def set_night_mode(self, value: bool):
        await self.set_custom_execution_data(
            href="/sec/networkaudio/advancedaudio",
            property="x.com.samsung.networkaudio.nightmode",
            value=1 if value else 0,
        )
        self.__night_mode = 1 if value else 0

    @property
    def bass_mode(self) -> bool | None:
        if self.__bass_mode is None:
            return None
        return self.__bass_mode == 1

    async def set_bass_mode(self, value: bool):
        await self.set_custom_execution_data(
            href="/sec/networkaudio/advancedaudio",
            property="x.com.samsung.networkaudio.bassboost",
            value=1 if value else 0,
        )
        self.__bass_mode = 1 if value else 0

    @property
    def voice_amplifier(self) -> bool | None:
        if self.__voice_amplifier is None:
            return None
        return self.__voice_amplifier == 1

    async def set_voice_amplifier(self, value: bool):
        await self.set_custom_execution_data(
            href="/sec/networkaudio/advancedaudio",
            property="x.com.samsung.networkaudio.voiceamplifier",
            value=1 if value else 0,
        )
        self.__voice_amplifier = 1 if value else 0

    # ------------ EQUALIZER --------------

    @property
    def active_equalizer_preset(self):
        return self.__active_eq_preset

    @property
    def supported_equalizer_presets(self):
        return self.__supported_eq_presets

    @property
    def equalizer_action(self):
        return self.__eq_action

    @property
    def equalizer_bands(self):
        return self.__eq_bands

    async def set_equalizer_preset(self, preset: str):
        await self.set_custom_execution_data(
            href="/sec/networkaudio/eq",
            property="x.com.samsung.networkaudio.EQname",
            value=preset,
        )

    # ------------- MEDIA ----------------
    @property
    def media_title(self):
        return self.__media_title

    @property
    def media_artist(self):
        return self.__media_artist

    @property
    def media_coverart_url(self):
        return self.__media_cover_url

    @property
    def media_duration(self) -> int | None:
        return self._find_attribute_value("totalTime")

    @property
    def media_position(self) -> int | None:
        return self._find_attribute_value("elapsedTime")

    async def media_play(self):
        await self._send_command("mediaPlayback", "play")

    async def media_pause(self):
        await self._send_command("mediaPlayback", "pause")

    async def media_stop(self):
        await self._send_command("mediaPlayback", "stop")

    async def media_next_track(self):
        await self._send_command("mediaPlayback", "fastForward")

    async def media_previous_track(self):
        await self._send_command("mediaPlayback", "rewind")

    @property
    def media_app_name(self):
        return self._get_status_value("samsungvd.soundFrom", "detailName")

    @property
    def media_coverart_updated(self) -> datetime.datetime:
        return self.__media_cover_url_update_time

    # ------------ Speaker Level ----------------

    async def set_speaker_level(self, speaker: SpeakerIdentifier, level: int):
        await self.set_custom_execution_data(
            href="/sec/networkaudio/channelVolume",
            property="x.com.samsung.networkaudio.channelVolume",
            value=[{"name": speaker.value, "value": level}],
        )

    async def set_rear_speaker_mode(self, mode: RearSpeakerMode):
        await self.set_custom_execution_data(
            href="/sec/networkaudio/surroundspeaker",
            property="x.com.samsung.networkaudio.currentRearPosition",
            value=mode.value,
        )

    # ------------ OTHER FUNCTIONS ------------

    async def set_active_voice_amplifier(self, enabled: bool):
        await self.set_custom_execution_data(
            href="/sec/networkaudio/activeVoiceAmplifier",
            property="x.com.samsung.networkaudio.activeVoiceAmplifier",
            value=1 if enabled else 0
        )

    async def set_space_fit_sound(self, enabled: bool):
        await self.set_custom_execution_data(
            href="/sec/networkaudio/spacefitSound",
            property="x.com.samsung.networkaudio.spacefitSound",
            value=1 if enabled else 0
        )

    # ------------ SUPPORT FUNCTIONS ------------

    async def update_execution_data(self, argument):
        await self._send_command("execute", "execute", argument)

    async def set_custom_execution_data(self, href: str, property: str, value):
        argument = [href, {property: value}]
        await self._send_command("execute", "execute", argument)

    async def get_execute_status(self):
        url = f"{API_BASE}/devices/{self._device_id}/components/main/capabilities/execute/status"
        request_headers = {"Authorization": "Bearer " + self._api_token}
        try:
            resp = await self._session.get(url, headers=request_headers)
            dict_stuff = await resp.json()
            value = dict_stuff.get("data", {}).get("value")
            if value is None:
                return {}
            return value.get("payload", {})
        except Exception as err:
            _LOGGER.error("[%s] Error fetching execute status: %s", DOMAIN, err)
            return {}

    async def get_song_title_artwork(self, artist: str, title: str) -> str:
        """
        This function loads a Music Art Cover from iTunes based on
        the title and the artist
        :param artist: string
        :param title: string
        :return: url as string
        """
        query_term = f"{artist} {title}"
        url = "https://itunes.apple.com/search?term=%s&media=music&entity=%s" % (
            quote(query_term),
            "musicTrack",
        )
        resp = await self._session.get(url)
        resp_dict = json.loads(await resp.text())
        if len(resp_dict["results"]) != 0:
            return resp_dict["results"][0]["artworkUrl100"]

    @property
    def retrieve_data(self):
        return {
            "status": self.state,
            "device_information": {
                "model": self.model,
                "manufacture": self.manufacturer,
                "firmware_version": self.firmware_version,
                "device_id": self.device_id,
            },
            "volume": {"level": self.volume_level, "muted": self.volume_muted},
            "woofer": {
                "level": self.woofer_level,
                "connection": self.woofer_connection,
            },
            "source": {
                "active_source": self.input_source,
                "supported_sources": self.supported_input_sources,
            },
            "sound_mode": {
                "active_sound_mode": self.sound_mode,
                "supported_sound_modes": self.supported_soundmodes,
            },
            "advanced_audio": {
                "night_mode": self.night_mode,
                "bass_mode": self.bass_mode,
                "voice_amplifier": self.voice_amplifier,
            },
            "equalizer": {
                "active_preset": self.active_equalizer_preset,
                "supported_presets": self.supported_equalizer_presets,
                "action": self.equalizer_action,
                "bands": self.equalizer_bands,
            },
            "media": {
                "media_title": self.media_title,
                "media_artist": self.media_artist,
                "media_cover_url": self.media_coverart_url,
                "media_duration": self.media_duration,
                "media_position": self.media_position,
            },
        }