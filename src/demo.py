"""Demo usage of AndroidTVRemote."""

import argparse
import asyncio
import logging
from typing import cast

from pynput import keyboard
from zeroconf import ServiceStateChange, Zeroconf
from zeroconf.asyncio import AsyncServiceBrowser, AsyncServiceInfo, AsyncZeroconf

from androidtvremote2 import (
    AndroidTVRemote,
    CannotConnect,
    ConnectionClosed,
    InvalidAuth,
)

_LOGGER = logging.getLogger(__name__)


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
        "\n- 't': send text 'Hello world' to Android TV\n\n"
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

    def transmit_keys() -> asyncio.Queue:
        queue: asyncio.Queue = asyncio.Queue()
        loop = asyncio.get_event_loop()

        def on_press(key: keyboard.Key | keyboard.KeyCode | None) -> None:
            loop.call_soon_threadsafe(queue.put_nowait, key)

        keyboard.Listener(on_press=on_press).start()
        return queue

    key_queue = transmit_keys()
    while True:
        key = await key_queue.get()
        if key in key_mappings:
            remote.send_key_command(key_mappings[key])
        if hasattr(key, "char"):
            if key.char == "q":
                remote.disconnect()
                return
            elif key.char == "m":
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

    async def async_display_service_info(
        zeroconf: Zeroconf, service_type: str, name: str
    ) -> None:
        info = AsyncServiceInfo(service_type, name)
        await info.async_request(zeroconf, 3000)
        if info:
            addresses = [
                f"{addr}:{cast(int, info.port)}"
                for addr in info.parsed_scoped_addresses()
            ]
            print(f"  Name: {name}")
            print(f"  Addresses: {", ".join(addresses)}")
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
    print(
        f"\nBrowsing {services} service(s) for {timeout} seconds, press Ctrl-C to exit...\n"
    )
    browser = AsyncServiceBrowser(
        zc.zeroconf, services, handlers=[_async_on_service_state_change]
    )
    await asyncio.sleep(timeout)

    await browser.async_cancel()
    await zc.async_close()

    return input("Enter IP address of Android TV to connect to: ").split(":")[0]


async def _pair(remote: AndroidTVRemote) -> None:
    name, mac = await remote.async_get_name_and_mac()
    if (
        input(
            f"Do you want to pair with {remote.host} {name} {mac}"
            " (this will turn on the Android TV)? y/n: "
        )
        != "y"
    ):
        exit()
    await remote.async_start_pairing()
    while True:
        pairing_code = input("Enter pairing code: ")
        try:
            return await remote.async_finish_pairing(pairing_code)
        except InvalidAuth as exc:
            _LOGGER.error("Invalid pairing code. Error: %s", exc)
            continue
        except ConnectionClosed as exc:
            _LOGGER.error("Initialize pair again. Error: %s", exc)
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
    parser.add_argument(
        "-v", "--verbose", help="enable verbose logging", action="store_true"
    )
    args = parser.parse_args()

    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.INFO)

    host = args.host or await _host_from_zeroconf(args.scan_timeout)

    remote = AndroidTVRemote(args.client_name, args.certfile, args.keyfile, host)

    if await remote.async_generate_cert_if_missing():
        _LOGGER.info("Generated new certificate")
        await _pair(remote)

    while True:
        try:
            await remote.async_connect()
            break
        except InvalidAuth as exc:
            _LOGGER.error("Need to pair again. Error: %s", exc)
            await _pair(remote)
        except (CannotConnect, ConnectionClosed) as exc:
            _LOGGER.error("Cannot connect, exiting. Error: %s", exc)
            return

    remote.keep_reconnecting()

    _LOGGER.info("device_info: %s", remote.device_info)
    _LOGGER.info("is_on: %s", remote.is_on)
    _LOGGER.info("current_app: %s", remote.current_app)
    _LOGGER.info("volume_info: %s", remote.volume_info)

    def is_on_updated(is_on: bool) -> None:
        _LOGGER.info("Notified that is_on: %s", is_on)

    def current_app_updated(current_app: str) -> None:
        _LOGGER.info("Notified that current_app: %s", current_app)

    def volume_info_updated(volume_info: dict[str, str | bool]) -> None:
        _LOGGER.info("Notified that volume_info: %s", volume_info)

    def is_available_updated(is_available: bool) -> None:
        _LOGGER.info("Notified that is_available: %s", is_available)

    remote.add_is_on_updated_callback(is_on_updated)
    remote.add_current_app_updated_callback(current_app_updated)
    remote.add_volume_info_updated_callback(volume_info_updated)
    remote.add_is_available_updated_callback(is_available_updated)

    await _bind_keyboard(remote)


asyncio.run(_main(), debug=True)
