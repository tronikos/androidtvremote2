# ruff: noqa: T201, PLR0912, PLR0915
"""Demo usage of AndroidTVRemote."""

import argparse
import asyncio
import logging
import os
import sys
import time
import wave
from typing import cast

import pyaudio
from pynput import keyboard
from zeroconf import ServiceStateChange, Zeroconf
from zeroconf.asyncio import AsyncServiceBrowser, AsyncServiceInfo, AsyncZeroconf

from androidtvremote2 import (
    AndroidTVRemote,
    CannotConnect,
    ConnectionClosed,
    InvalidAuth,
    VolumeInfo,
)

_LOGGER = logging.getLogger(__name__)

VOICE_ENABLED = True
VOICE_RECORD_SECONDS = 5
VOICE_STREAM_SECONDS = 10
VOICE_FORMAT = pyaudio.paInt16
VOICE_CHANNELS = 1
VOICE_RATE = 8000
VOICE_FILE = "voice_command.wav"


async def _bind_keyboard(remote: AndroidTVRemote) -> None:
    print(
        "\n\nYou can control the connected Android TV with:"
        "\n- arrow keys: move selected item"
        "\n- enter: run selected item"
        "\n- space: play/pause"
        "\n- home: go to the home screen"
        "\n- backspace or esc: go back"
        "\n- delete: power off/on"
        "\n- +/-: volume up/down"
        "\n- 'm': mute"
        "\n- 'y': YouTube"
        "\n- 'n': Netflix"
        "\n- 'd': Disney+"
        "\n- 'a': Amazon Prime Video"
        "\n- 'k': Kodi"
        "\n- 'q': quit"
        "\n- 't': send text 'Hello world' to Android TV"
        "\n- 'v': stream a voice command from the default audio input. Press v again to stop streaming."
        "\n- 'r': record a " + str(VOICE_RECORD_SECONDS) + "s voice command"
        "\n- 'p': play back pre-recorded voice command"
        "\n- 'w': send pre-recorded voice command in " + VOICE_FILE + " to Android TV\n\n"
    )
    key_mappings = {
        keyboard.Key.up: "DPAD_UP",
        keyboard.Key.down: "DPAD_DOWN",
        keyboard.Key.left: "DPAD_LEFT",
        keyboard.Key.right: "DPAD_RIGHT",
        keyboard.Key.enter: "DPAD_CENTER",
        keyboard.Key.space: "MEDIA_PLAY_PAUSE",
        keyboard.Key.home: "HOME",
        keyboard.Key.backspace: "BACK",
        keyboard.Key.esc: "BACK",
        keyboard.Key.delete: "POWER",
    }

    def transmit_keys() -> asyncio.Queue[keyboard.Key | keyboard.KeyCode | None]:
        queue: asyncio.Queue[keyboard.Key | keyboard.KeyCode | None] = asyncio.Queue()
        loop = asyncio.get_event_loop()

        def on_press(key: keyboard.Key | keyboard.KeyCode | None) -> None:
            loop.call_soon_threadsafe(queue.put_nowait, key)

        keyboard.Listener(on_press=on_press).start()
        return queue

    key_queue = transmit_keys()
    voice_task: asyncio.Task[None] | None = None
    voice_stop_event = asyncio.Event()
    while True:
        key = await key_queue.get()
        if key is None:
            continue
        if isinstance(key, keyboard.Key) and key in key_mappings:
            remote.send_key_command(key_mappings[key])
        if not isinstance(key, keyboard.KeyCode):
            continue
        if key.char == "q":
            remote.disconnect()
            return
        if key.char == "m":
            remote.send_key_command("MUTE")
        elif key.char == "+":
            remote.send_key_command("VOLUME_UP")
        elif key.char == "-":
            remote.send_key_command("VOLUME_DOWN")
        elif key.char == "y":
            remote.send_launch_app_command("https://www.youtube.com")
        elif key.char == "n":
            remote.send_launch_app_command("com.netflix.ninja")
        elif key.char == "d":
            remote.send_launch_app_command("com.disney.disneyplus")
        elif key.char == "a":
            remote.send_launch_app_command("com.amazon.amazonvideo.livingroom")
        elif key.char == "k":
            remote.send_launch_app_command("org.xbmc.kodi")
        elif key.char == "t":
            remote.send_text("Hello World!")
        if key.char == "r":
            _record_voice_command(VOICE_FILE)
        elif key.char == "p":
            _play_voice_command(VOICE_FILE)
        elif key.char == "v":
            if voice_task is not None and not voice_task.done():
                print("Stopping voice recording")
                voice_stop_event.set()
                continue
            print("Starting voice recording. Press v again to stop. Auto stop after " + str(VOICE_STREAM_SECONDS) + "s")
            voice_stop_event.clear()
            voice_task = asyncio.get_event_loop().create_task(_stream_voice(remote, voice_stop_event))
        elif key.char == "w":
            await _send_voice(VOICE_FILE, remote)


async def _host_from_zeroconf(timeout: float) -> str:
    def _async_on_service_state_change(
        zeroconf: Zeroconf,
        service_type: str,
        name: str,
        state_change: ServiceStateChange,
    ) -> None:
        if state_change is not ServiceStateChange.Added:
            return
        _ = asyncio.ensure_future(  # noqa: RUF006
            async_display_service_info(zeroconf, service_type, name)
        )

    async def async_display_service_info(zeroconf: Zeroconf, service_type: str, name: str) -> None:
        info = AsyncServiceInfo(service_type, name)
        await info.async_request(zeroconf, 3000)
        if info:
            addresses = [f"{addr}:{cast('int', info.port)}" for addr in info.parsed_scoped_addresses()]
            print(f"  Name: {name}")
            print(f"  Addresses: {', '.join(addresses)}")
            if info.properties:
                print("  Properties:")
                for key, value in info.properties.items():
                    print(f"    {key!r}: {value!r}")
            else:
                print("  No properties")
        else:
            print("  No info")
        print()

    zc = AsyncZeroconf()
    services = ["_androidtvremote2._tcp.local."]
    print(f"\nBrowsing {services} service(s) for {timeout} seconds, press Ctrl-C to exit...\n")
    browser = AsyncServiceBrowser(zc.zeroconf, services, handlers=[_async_on_service_state_change])
    await asyncio.sleep(timeout)

    await browser.async_cancel()
    await zc.async_close()

    return input("Enter IP address of Android TV to connect to: ").split(":")[0]


async def _pair(remote: AndroidTVRemote) -> None:
    name, mac = await remote.async_get_name_and_mac()
    if input(f"Do you want to pair with {remote.host} {name} {mac} (this will turn on the Android TV)? y/n: ") != "y":
        sys.exit()
    await remote.async_start_pairing()
    while True:
        pairing_code = input("Enter pairing code: ")
        try:
            return await remote.async_finish_pairing(pairing_code)
        except InvalidAuth:
            _LOGGER.exception("Invalid pairing code")
            continue
        except ConnectionClosed:
            _LOGGER.exception("Initialize pair again")
            return await _pair(remote)


async def _main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--host", help="IP address of Android TV to connect to")
    parser.add_argument(
        "--certfile",
        help="filename that contains the client certificate in PEM format",
        default="cert.pem",
    )
    parser.add_argument(
        "--keyfile",
        help="filename that contains the public key in PEM format",
        default="key.pem",
    )
    parser.add_argument(
        "--client_name",
        help="shown on the Android TV during pairing",
        default="Android TV Remote demo",
    )
    parser.add_argument(
        "--scan_timeout",
        type=float,
        help="zeroconf scan timeout in seconds",
        default=3,
    )
    parser.add_argument("-v", "--verbose", help="enable verbose logging", action="store_true")
    args = parser.parse_args()

    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.INFO)

    host = args.host or await _host_from_zeroconf(args.scan_timeout)

    remote = AndroidTVRemote(args.client_name, args.certfile, args.keyfile, host, enable_voice=VOICE_ENABLED)

    if await remote.async_generate_cert_if_missing():
        _LOGGER.info("Generated new certificate")
        await _pair(remote)

    while True:
        try:
            await remote.async_connect()
            break
        except InvalidAuth:
            _LOGGER.exception("Need to pair again")
            await _pair(remote)
        except (CannotConnect, ConnectionClosed):
            _LOGGER.exception("Cannot connect, exiting")
            return

    remote.keep_reconnecting()

    _LOGGER.info("device_info: %s", remote.device_info)
    _LOGGER.info("is_on: %s", remote.is_on)
    _LOGGER.info("current_app: %s", remote.current_app)
    _LOGGER.info("volume_info: %s", remote.volume_info)
    _LOGGER.info("voice enabled: %s", remote.is_voice_enabled)

    def is_on_updated(is_on: bool) -> None:
        _LOGGER.info("Notified that is_on: %s", is_on)

    def current_app_updated(current_app: str) -> None:
        _LOGGER.info("Notified that current_app: %s", current_app)

    def volume_info_updated(volume_info: VolumeInfo) -> None:
        _LOGGER.info("Notified that volume_info: %s", volume_info)

    def is_available_updated(is_available: bool) -> None:
        _LOGGER.info("Notified that is_available: %s", is_available)

    remote.add_is_on_updated_callback(is_on_updated)
    remote.add_current_app_updated_callback(current_app_updated)
    remote.add_volume_info_updated_callback(volume_info_updated)
    remote.add_is_available_updated_callback(is_available_updated)

    await _bind_keyboard(remote)


async def _send_voice(wav_file: str, remote: AndroidTVRemote) -> None:
    """Send a WAV file as a voice command."""
    if not os.path.isfile(wav_file):
        _LOGGER.error("WAV file not found: %s", wav_file)
        return
    if not remote.is_voice_enabled:
        _LOGGER.warning("Voice feature is not enabled in the client or not supported on the device")
        return

    try:
        with wave.open(wav_file, "rb") as wf:
            if wf.getnchannels() != 1:
                _LOGGER.error("Only mono WAV files are supported")
                return
            if wf.getsampwidth() != 2:
                _LOGGER.error("Only 16-bit WAV files are supported")
                return
            if wf.getframerate() != 8000:
                _LOGGER.error("Only 8 kHz WAV files are supported")
                return
            nframes = wf.getnframes()
            pcm_data = wf.readframes(nframes)

        _LOGGER.debug("Loaded WAV file '%s': frames=%d, bytes=%d", wav_file, nframes, len(pcm_data))

        async with await remote.start_voice() as session:
            session.send_chunk(pcm_data)
    except FileNotFoundError:
        _LOGGER.exception("WAV file not found")
    except wave.Error:
        _LOGGER.exception("Invalid/unsupported WAV file %s", wav_file)
    except asyncio.TimeoutError:
        _LOGGER.warning("Timeout: could not start voice session")
    except Exception:
        _LOGGER.exception("Unexpected error in send_voice")


def _record_voice_command(wav_file: str) -> None:
    """Record a WAV file from the default audio input."""
    with wave.open(wav_file, "wb") as wf:
        p = pyaudio.PyAudio()
        wf.setnchannels(VOICE_CHANNELS)
        wf.setsampwidth(p.get_sample_size(VOICE_FORMAT))
        wf.setframerate(VOICE_RATE)

        def callback(in_data: bytes, frame_count: object, time_info: object, status: object) -> tuple[bytes | None, int]:
            wf.writeframes(in_data)
            return None, pyaudio.paContinue

        stream = p.open(format=VOICE_FORMAT, channels=VOICE_CHANNELS, rate=VOICE_RATE, input=True, stream_callback=callback)

        print("Recording " + str(VOICE_RECORD_SECONDS) + "s voice command to:", wav_file)
        start = time.time()
        while stream.is_active() and (time.time() - start) < VOICE_RECORD_SECONDS:
            time.sleep(0.1)
        print("Recording stopped")

        stream.close()
        p.terminate()


def _play_voice_command(wav_file: str) -> None:
    """Play a WAV file on the default audio output."""
    if not os.path.isfile(wav_file):
        _LOGGER.error("WAV file not found: %s", wav_file)
        return
    print("Playing back recorded voice command:", wav_file)
    with wave.open(wav_file, "rb") as wf:
        p = pyaudio.PyAudio()
        stream = p.open(
            format=p.get_format_from_width(wf.getsampwidth()), channels=wf.getnchannels(), rate=wf.getframerate(), output=True
        )

        chunk_size = 1024
        while len(data := wf.readframes(chunk_size)):
            stream.write(data)

        stream.close()
        p.terminate()


async def _stream_voice(remote: AndroidTVRemote, stop_event: asyncio.Event) -> None:
    """Record from the default audio input and stream as a voice command."""
    chunk_size = 8 * 1024

    if not remote.is_voice_enabled:
        _LOGGER.warning("Voice feature is not enabled in the client or not supported on the device")
        return

    # Start a streaming voice session
    # Context manager calls session.end() automatically
    try:
        async with await remote.start_voice() as session:
            if not remote.is_voice_enabled:
                _LOGGER.warning("Voice feature is not enabled in the client or not supported on the device")
                return

            def callback(in_data: bytes, frame_count: object, time_info: object, status: object) -> tuple[bytes | None, int]:
                _LOGGER.debug("MIC callback: frame_count=%d, time_info=%s, status=%s", frame_count, time_info, status)
                session.send_chunk(in_data)
                return None, pyaudio.paContinue

            _LOGGER.info("Voice session established, opening microphone...")
            p = pyaudio.PyAudio()
            stream = p.open(
                format=VOICE_FORMAT,
                channels=VOICE_CHANNELS,
                rate=VOICE_RATE,
                input=True,
                frames_per_buffer=chunk_size,
                stream_callback=callback,
            )

            _LOGGER.info("Recording started, sending data to Android TV...")
            start = time.time()

            # Use run_in_executor to check stream status without blocking
            loop = asyncio.get_event_loop()
            # Wait until timeout, stop_event is set, or stream becomes inactive
            while (time.time() - start) < VOICE_RECORD_SECONDS:
                if stop_event.is_set():
                    _LOGGER.debug("Recording stopped by external event")
                    break

                if not await loop.run_in_executor(None, stream.is_active):
                    _LOGGER.debug("Recording stopped: stream became inactive")
                    break

                try:
                    await asyncio.wait_for(stop_event.wait(), timeout=0.25)
                    break
                except asyncio.TimeoutError:
                    pass

        print("Voice data sent, closing microphone")

        stream.close()
        p.terminate()
    except asyncio.TimeoutError as e:
        print("Timeout: could not start voice session.", e)


asyncio.run(_main(), debug=True)
